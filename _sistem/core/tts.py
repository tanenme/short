"""ElevenLabs v4 Voiceover Generator with native character-to-word alignment."""

from __future__ import annotations

import base64
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import StudioConfig, get_config
from .validator import extract_vo_section

ELEVENLABS_BASE = "https://api.elevenlabs.io/v1"


def alignment_chars_to_words(
    characters: List[str],
    char_starts: List[float],
    char_ends: List[float],
) -> List[Dict[str, object]]:
    """Convert ElevenLabs character-level alignment into clean word timestamps, skipping [audio tags]."""
    n = min(len(characters), len(char_starts), len(char_ends))
    words: List[Dict[str, object]] = []
    in_bracket = False
    buf_chars: List[str] = []
    w_start: Optional[float] = None
    w_end: float = 0.0

    def flush() -> None:
        nonlocal buf_chars, w_start, w_end
        raw = "".join(buf_chars).strip()
        cleaned = re.sub(r"^[^\w]+|[^\w]+$", "", raw, flags=re.UNICODE)
        if cleaned and w_start is not None:
            words.append(
                {
                    "word": cleaned,
                    "start": round(float(w_start), 3),
                    "end": round(max(float(w_start) + 0.05, float(w_end)), 3),
                }
            )
        buf_chars = []
        w_start = None
        w_end = 0.0

    for i in range(n):
        ch = characters[i]
        st = float(char_starts[i])
        en = float(char_ends[i])

        if ch == "[":
            flush()
            in_bracket = True
            continue
        if ch == "]":
            in_bracket = False
            continue
        if in_bracket:
            continue

        if ch.isspace():
            flush()
        else:
            if w_start is None:
                w_start = st
            w_end = en
            buf_chars.append(ch)

    flush()
    return words


def list_elevenlabs_voices(api_key: Optional[str] = None, config: Optional[StudioConfig] = None) -> List[dict]:
    cfg = config or get_config()
    key = (api_key or cfg.elevenlabs_api_key or "").strip()
    if not key:
        raise ValueError("ELEVENLABS_API_KEY belum diset di file .env.")
    req = urllib.request.Request(
        f"{ELEVENLABS_BASE}/voices",
        headers={"xi-api-key": key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {
            "voice_id": v.get("voice_id"),
            "name": v.get("name"),
            "category": v.get("category"),
            "labels": v.get("labels") or {},
        }
        for v in data.get("voices", [])
    ]


def generate_episode_voiceover(
    episode_id: str,
    channel_id: Optional[str] = None,
    voice_id_override: Optional[str] = None,
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> dict:
    """Generate ElevenLabs VO .mp3 + exact words_alignment.json directly from episode's naskah.md."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    key = (api_key or cfg.elevenlabs_api_key or "").strip()
    if not key:
        raise ValueError("ELEVENLABS_API_KEY belum diset di .env. Tambahkan key di E:\\YT\\short\\.env.")

    vid = (voice_id_override or prof.voice.voice_id or "").strip()
    if not vid:
        raise ValueError(
            f"voice_id untuk channel '{prof.channel_id}' belum diisi di _sistem/channels/{prof.channel_id}.toml "
            f"(atau kirim parameter --voice-id)."
        )

    ep_dir = cfg.episode_dir(episode_id, prof.channel_id)
    naskah_p = ep_dir / "naskah.md"
    if not naskah_p.exists():
        raise FileNotFoundError(f"naskah.md tidak ditemukan di {ep_dir}")

    vo_text = extract_vo_section(naskah_p.read_text(encoding="utf-8", errors="ignore"))
    if not vo_text:
        raise ValueError("Blok VO final di naskah.md kosong.")

    model_id = prof.voice.engine or "eleven_v4"
    # Map shorthand `eleven_v4` / `eleven_v3` cleanly if needed
    payload = {
        "text": vo_text,
        "model_id": model_id,
        "voice_settings": {
            "stability": prof.voice.stability,
            "similarity_boost": prof.voice.similarity,
        },
    }

    url = f"{ELEVENLABS_BASE}/text-to-speech/{urllib.parse.quote(vid)}/with-timestamps?output_format=mp3_44100_128"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": key,
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:300]
        raise RuntimeError(f"ElevenLabs API error ({e.code}): {detail}") from e

    audio_b64 = data.get("audio_base64")
    if not audio_b64:
        raise RuntimeError("ElevenLabs tidak mengembalikan audio_base64.")

    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)
    assets_dir.mkdir(parents=True, exist_ok=True)
    ts_str = time.strftime("%Y-%m-%dT%H_%M_%S")
    mp3_path = assets_dir / f"ElevenLabs_{ts_str}_{vid[:8]}_{model_id}.mp3"
    mp3_path.write_bytes(base64.b64decode(audio_b64))

    align_obj = data.get("alignment") or data.get("normalized_alignment") or {}
    words = alignment_chars_to_words(
        align_obj.get("characters") or [],
        align_obj.get("character_start_times_seconds") or [],
        align_obj.get("character_end_times_seconds") or [],
    )
    align_path = assets_dir / "words_alignment.json"
    if words:
        align_path.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")

    return {
        "success": True,
        "mp3_file": mp3_path.name,
        "mp3_path": str(mp3_path),
        "words_count": len(words),
        "alignment_file": str(align_path) if words else None,
        "model_id": model_id,
    }
