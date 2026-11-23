"""MyInstants Soundboard Search & Downloader (https://www.myinstants.com/en/index/id/)."""

from __future__ import annotations

import html
import json
import math
import re
import subprocess
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

from core.config import StudioConfig, get_config

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


def search_myinstants(query: str = "", limit: int = 10) -> List[dict]:
    """Search Indonesian/global viral SFX on MyInstants and return direct MP3 URLs."""
    if query.strip():
        url = f"https://www.myinstants.com/en/search/?name={urllib.parse.quote(query.strip())}"
    else:
        url = "https://www.myinstants.com/en/index/id/"

    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="ignore")

    # Extract play('/media/sounds/xxx.mp3', ...) paired with button title
    pattern = re.compile(
        r"play\('(/media/sounds/[^']+\.mp3)'[^)]*\)[^>]*title=\"Play ([^\"]+) sound\"",
        re.IGNORECASE,
    )
    results: List[dict] = []
    seen = set()
    for rel_mp3, title in pattern.findall(body):
        full_url = "https://www.myinstants.com" + rel_mp3
        if full_url in seen:
            continue
        seen.add(full_url)
        results.append(
            {
                "title": html.unescape(title).strip(),
                "mp3_url": full_url,
                "filename": Path(rel_mp3).name,
            }
        )
        if len(results) >= limit:
            break
    return results


def download_myinstants_sfx(
    query_or_url: str,
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> dict:
    """Download a viral SFX MP3 from MyInstants directly into channel's SFX folder."""
    cfg = config or get_config()
    sfx_dir = cfg.channel_sfx_dir(channel_id)
    sfx_dir.mkdir(parents=True, exist_ok=True)

    if query_or_url.startswith(("http://", "https://")) and query_or_url.lower().endswith(".mp3"):
        mp3_url = query_or_url
        fname = Path(urllib.parse.urlparse(mp3_url).path).name
        title = fname
    elif "myinstants.com" in query_or_url and "/instant/" in query_or_url:
        req_pg = urllib.request.Request(query_or_url, headers={"User-Agent": UA})
        pg_html = urllib.request.urlopen(req_pg, timeout=20).read().decode("utf-8", errors="ignore")
        m = re.search(r"/media/sounds/[^'\"\s]+\.mp3", pg_html)
        if not m:
            raise RuntimeError("MP3 URL tidak ditemukan di halaman instant MyInstants ini.")
        mp3_url = "https://www.myinstants.com" + m.group(0)
        fname = re.sub(r"[^a-zA-Z0-9._-]+", "_", Path(m.group(0)).name)
        title = fname
    else:
        hits = search_myinstants(query_or_url, limit=5)
        if not hits:
            raise RuntimeError(f"Tidak ditemukan soundboard '{query_or_url}' di MyInstants.")
        best = hits[0]
        mp3_url = best["mp3_url"]
        fname = re.sub(r"[^a-zA-Z0-9._-]+", "_", best["filename"])
        title = best["title"]

    dest = sfx_dir / fname
    req = urllib.request.Request(mp3_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    if len(data) < 512:
        raise RuntimeError("File MP3 dari MyInstants terlalu kecil/korup.")
    dest.write_bytes(data)
    return {"success": True, "title": title, "filename": dest.name, "path": str(dest), "bytes": len(data)}


# Role -> (target_rms_db_pre_mix, max_dur_s, extra_ffmpeg_filter, auto_use_in_engine)
# Calibrated for crisp -3 dB SFX ceiling so SFX cuts cleanly above -8 dB BGM without harsh whooshes
ROLE_TARGET_SPECS = {
    "opening_bell": (-16.0, 0.85, "", True),
    "transition_swish": (-26.0, 0.24, "lowpass=f=2200,", True),
    "pop_accent": (-17.0, 0.24, "", True),
    "ui_click": (-16.5, 0.28, "", True),
    "ding_chime": (-16.0, 0.45, "", True),
    "twist_scratch": (-17.5, 0.45, "", True),
    "wrong_buzzer": (-18.0, 0.55, "", True),
    "comedy_bruh": (-18.0, 0.65, "", True),
    "question_cue": (-18.0, 0.55, "", True),
    "comedy_boing": (-18.0, 0.42, "", True),
    "action_run": (-18.0, 0.50, "", True),
    "action_bite": (-17.5, 0.50, "", True),
    "impact_boom": (-18.5, 0.55, "", True),
    "riser_sparkle": (-18.5, 0.60, "", True),
    "animal_cat": (-18.0, 0.55, "", True),
    "animal_duck": (-18.0, 0.55, "", True),
    "animal_dog": (-19.0, 0.60, "", True),
    "wow_reaction": (-19.0, 0.65, "", True),
    "meme_long_manual": (-21.0, 1.00, "", False),
}


def classify_sfx_role(filename: str, dur_s: float) -> str:
    low = filename.lower()
    if "signature-bell" in low:
        return "opening_bell"
    if "transition-whoosh" in low or "clean-whoosh" in low or "camera-flash" in low or low == "piuw.mp3":
        return "transition_swish"
    if "ui-click" in low or low in ("correct.mp3", "cash-register-sound-fx.mp3", "kaching-sound-fx.mp3"):
        return "ui_click"
    if "vinyl" in low or "record-scratch" in low:
        return "twist_scratch"
    if low in ("wrong_5.mp3", "spongebob-boowomp.mp3", "spongebob-fail.mp3"):
        return "wrong_buzzer"
    if low in ("bruh.mp3", "bruh-sound-effect_wstdzdm.mp3"):
        return "comedy_bruh"
    if low in ("huh_37baoro.mp3", "ceeday-huh-sound-effect.mp3", "question-7tv.mp3"):
        return "question_cue"
    if "boing" in low or "maro-jump" in low or "squeak" in low:
        return "comedy_boing"
    if any(k in low for k in ("cartoon-slip", "cartoonslip", "cartoon-slide", "goofy-ahh-runnin", "crab-walking", "mr-krab-walking")):
        return "action_run"
    if "bite" in low or "nom-nom" in low:
        return "action_bite"
    if low in ("vine-boom.mp3", "punch-gaming-sound-effect-hd_rzlg1ge.mp3", "sudden-suspense-sound-effect.mp3", "shocked-sound-effect.mp3"):
        return "impact_boom"
    if "sparkle" in low or "riser-metallic" in low:
        return "riser_sparkle"
    if low in ("meow-1.mp3", "cute-meow.mp3"):
        return "animal_cat"
    if "quack" in low:
        return "animal_duck"
    if low in ("shivering-dog-meme.mp3", "laughing-dog-meme.mp3"):
        return "animal_dog"
    if low in ("clean-pop.mp3", "pop_7e9is8l.mp3", "bubble-pop.mp3", "bubble_apr55vw.mp3", "comedy_pop_finger_in_mouth_001.mp3"):
        return "pop_accent"
    if low in ("clean-ding.mp3", "ding-sound-effect_1.mp3"):
        return "ding_chime"
    if low in ("anime-wow-sound-effect.mp3", "kid-iyaa.mp3", "quick-kiss.mp3"):
        return "wow_reaction"
    return "meme_long_manual"


def build_soundboard_catalog(
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
    write_file: bool = True,
) -> Dict[str, dict]:
    """Probe every audio file in channel SFX folder, measure exact dB, and compute auto-calibrated linear gain."""
    cfg = config or get_config()
    sfx_dir = cfg.channel_sfx_dir(channel_id)
    if not sfx_dir.exists():
        return {}

    catalog: Dict[str, dict] = {}
    audio_files = sorted(p for p in sfx_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp3", ".wav", ".ogg", ".m4a"})

    for p in audio_files:
        try:
            dur_str = subprocess.check_output(
                [cfg.ffprobe_cmd(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                text=True,
                timeout=15,
            ).strip()
            dur_s = round(float(dur_str or 0.0), 2)
        except Exception:
            dur_s = 0.0

        r = subprocess.run(
            [cfg.ffmpeg_cmd(), "-i", str(p), "-af", "volumedetect", "-f", "null", "-"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        mean_db, max_db = -20.0, -3.0
        for line in r.stderr.splitlines():
            if "mean_volume:" in line:
                try:
                    mean_db = float(line.split("mean_volume:")[1].split("dB")[0].strip())
                except Exception:
                    pass
            elif "max_volume:" in line:
                try:
                    max_db = float(line.split("max_volume:")[1].split("dB")[0].strip())
                except Exception:
                    pass

        role = classify_sfx_role(p.name, dur_s)
        target_rms_db, max_dur_s, extra_f, auto_use = ROLE_TARGET_SPECS[role]

        # Exact mathematical gain: bring file's measured mean_db to role's target_rms_db, clamped safely
        gain_db = target_rms_db - mean_db
        # Protect peak ceiling at -3.0 dBFS so SFX stays punchy (-3 dB) without clipping
        if max_db + gain_db > -3.0:
            gain_db = -3.0 - max_db
        linear_gain = round(max(0.02, min(1.50, math.pow(10.0, gain_db / 20.0))), 4)

        catalog[p.name] = {
            "filename": p.name,
            "role": role,
            "auto_use": auto_use,
            "duration_s": dur_s,
            "mean_db": mean_db,
            "max_db": max_db,
            "target_rms_db": target_rms_db,
            "calibrated_gain": linear_gain,
            "trim_max_s": min(dur_s, max_dur_s) if dur_s > 0 else max_dur_s,
            "extra_filter": extra_f,
        }

    if write_file:
        (sfx_dir / "_soundboard_catalog.json").write_text(
            json.dumps(catalog, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    return catalog
