"""Broadcast audio mixer: sidechain ducking, 0.5s hook audio, dynamic SFX, and -14 LUFS mastering."""

from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Set, Tuple

from core.models import ChannelProfile


SFX_KEYWORD_MAP = {
    "whoosh": "clean-whoosh.mp3",
    "zip": "clean-whoosh.mp3",
    "riser": "clean-whoosh.mp3",
    "sniff": "clean-whoosh.mp3",
    "pop": "clean-pop.mp3",
    "ding": "clean-ding.mp3",
    "subscribe": "clean-ding.mp3",
    "sting": "clean-ding.mp3",
    "buzzer": "bruh-sound-effect_WstdzdM.mp3",
    "bruh": "bruh-sound-effect_WstdzdM.mp3",
    "bukan": "bruh-sound-effect_WstdzdM.mp3",
    "salah": "bruh-sound-effect_WstdzdM.mp3",
    "negatif": "bruh-sound-effect_WstdzdM.mp3",
    "question": "question-mark.mp3",
    "tanya": "question-mark.mp3",
    "feromon": "rizz-sound-effect.mp3",
    "tension": "rizz-sound-effect.mp3",
    "adrenalin": "rizz-sound-effect.mp3",
    "bass": "rizz-sound-effect.mp3",
    "wow": "anime-wow-sound-effect.mp3",
    "shock": "shocked-sound-effect.mp3",
}


def db_to_linear(db: float) -> float:
    return round(math.pow(10.0, db / 20.0), 4)


def resolve_sfx_path(sfx_hint: str, sfx_dir: Path) -> Optional[Path]:
    if not sfx_hint or not sfx_dir.exists():
        return None
    low = sfx_hint.lower()
    for kw, fname in SFX_KEYWORD_MAP.items():
        if kw in low:
            p = sfx_dir / fname
            if p.exists():
                return p
    return None


def pick_bgm_file(bgm_dir: Path) -> Optional[Path]:
    if not bgm_dir.exists():
        return None
    cands = sorted(bgm_dir.glob("*.mp3"))
    if not cands:
        return None
    for b in cands:
        if any(k in b.name.lower() for k in ("sneaky", "monkeys", "elevator")):
            return b
    return cands[0]


def wants_original_hook_audio(scene1: dict) -> bool:
    sfx = (scene1.get("sfx") or "").lower()
    vis = (scene1.get("visual_desc") or "").lower()
    return any(k in (sfx + " " + vis) for k in ("audio asli", "chattering", "audio ", "suara asli", "bunyi asli"))


def compute_sfx_entries(
    scenes: List[dict],
    sfx_dir: Path,
    highlights: Set[str],
) -> List[Tuple[float, Path, float]]:
    """Compute (trigger_s, sfx_path, gain) using dynamic emphasis words instead of hardcoded terms."""
    entries: List[Tuple[float, Path, float]] = []
    cue_words = {"subscribe", "bukan", "salah"} | {h.lower() for h in highlights if len(h) >= 4}

    for sc in scenes:
        sfx_hint = sc.get("sfx", "")
        sfx_path = resolve_sfx_path(sfx_hint, sfx_dir)
        if not sfx_path:
            continue

        trigger_s = float(sc["start_s"])
        # Align pop/ding/negatif SFX to the exact trigger word inside the scene if present
        for w in sc.get("words", []):
            tok = "".join(ch for ch in (w.get("word") or "").lower() if ch.isalnum())
            if tok in cue_words:
                trigger_s = float(w["start"])
                break

        gain = 0.42 if "whoosh" in sfx_path.name else 0.55
        entries.append((round(max(0.0, trigger_s), 3), sfx_path, gain))
    return entries


def build_audio_filtergraph(
    vo_input_idx: int,
    vo_duration: float,
    profile: ChannelProfile,
    bgm_input_idx: Optional[int],
    hook_audio_input_idx: Optional[int],
    sfx_inputs: List[Tuple[int, float, float]],
) -> str:
    """Build FFmpeg filter_complex audio graph with sidechain ducking and -14 LUFS mastering."""
    parts: List[str] = []

    # 1. Clean foreground VO (highpass rumble cut + subtle presence compression)
    vo_out = "[vo_clean]" if bgm_input_idx is not None else "[vo_mix]"
    parts.append(
        f"[{vo_input_idx}:a]highpass=f=85,acompressor=threshold=-20dB:ratio=3:attack=8:release=110:makeup=2,"
        f"volume=1.65,apad=pad_dur=0.6,atrim=0:{vo_duration:.3f},asetpts=PTS-STARTPTS{vo_out}"
    )
    if bgm_input_idx is not None:
        parts.append("[vo_clean]asplit=2[vo_mix][vo_sc]")

    mix_labels = ["[vo_mix]"]

    # 2. BGM with true sidechain auto-ducking driven by narrator voice (input uses -stream_loop -1)
    if bgm_input_idx is not None:
        base_bgm_lin = db_to_linear(max(-26.0, min(-14.0, profile.visual.bgm_volume_db + 4.0)))
        fade_out_start = max(0.0, vo_duration - 0.6)
        parts.append(
            f"[{bgm_input_idx}:a]atrim=0:{vo_duration:.3f},"
            f"asetpts=PTS-STARTPTS,volume={base_bgm_lin},"
            f"afade=t=in:st=0:d=0.35,afade=t=out:st={fade_out_start:.3f}:d=0.6[bgm_base]"
        )
        parts.append(
            "[bgm_base][vo_sc]sidechaincompress=threshold=0.03:ratio=4:attack=30:release=260:makeup=1[bgm_ducked]"
        )
        mix_labels.append("[bgm_ducked]")

    # 3. Optional 0.6s authentic animal hook audio snippet under second 0 (Pola Kucing Ek-Ek)
    if hook_audio_input_idx is not None:
        parts.append(
            f"[{hook_audio_input_idx}:a]atrim=0:0.65,asetpts=PTS-STARTPTS,"
            f"highpass=f=120,volume=0.32,afade=t=out:st=0.35:d=0.30,"
            f"apad=whole_dur={vo_duration:.3f}[hook_snd]"
        )
        mix_labels.append("[hook_snd]")

    # 4. Precision SFX cues
    for idx, (in_idx, trigger_s, gain) in enumerate(sfx_inputs):
        delay_ms = int(round(trigger_s * 1000))
        lbl = f"sfx_{idx}"
        parts.append(
            f"[{in_idx}:a]atrim=0:1.6,asetpts=PTS-STARTPTS,volume={gain:.2f},"
            f"adelay={delay_ms}|{delay_ms}[{lbl}]"
        )
        mix_labels.append(f"[{lbl}]")

    # 5. Master mix + YouTube Shorts broadcast loudness normalization (-14 LUFS)
    target_i = profile.visual.target_lufs
    parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I={target_i:.1f}:TP=-1.5:LRA=11[aout]"
    )
    return ";".join(parts)
