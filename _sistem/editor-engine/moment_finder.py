"""Golden Moment Finder: locates peak motion windows, peak audio windows, and visual action moments."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import StudioConfig, get_config


def find_peak_motion_start(
    clip_path: Path,
    window_dur: float,
    cfg: StudioConfig,
) -> Tuple[float, float]:
    """Scan clip with FFmpeg signalstats YDIF to find the highest-action/movement window start_s."""
    try:
        pr = subprocess.check_output(
            [cfg.ffprobe_cmd(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
            text=True,
            timeout=15,
        )
        total_dur = float(pr.strip() or 0.0)
    except Exception:
        return 0.0, 0.0

    if total_dur <= window_dur + 0.5:
        return 0.0, 0.0

    r = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-v",
            "error",
            "-i",
            str(clip_path),
            "-vf",
            "fps=2,scale=240:-2,signalstats,metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    samples: List[Tuple[float, float]] = []
    curr_t = 0.0
    for line in r.stdout.splitlines():
        if "pts_time:" in line:
            try:
                curr_t = float(line.split("pts_time:")[1].split()[0])
            except Exception:
                pass
        elif "lavfi.signalstats.YDIF=" in line:
            try:
                yd = float(line.split("=", 1)[1].strip())
                samples.append((curr_t, yd))
            except Exception:
                pass

    if not samples:
        return round(max(0.0, (total_dur - window_dur) * 0.3), 2), 0.0

    # Sliding window sum of YDIF over `window_dur`
    best_start = 0.0
    best_score = -1.0
    max_start = max(0.0, total_dur - window_dur - 0.1)
    for t_s, _ in samples:
        if t_s > max_start:
            break
        win_vals = [yd for tt, yd in samples if t_s <= tt <= t_s + window_dur]
        if not win_vals:
            continue
        avg_m = sum(win_vals) / len(win_vals)
        if avg_m > best_score:
            best_score = avg_m
            best_start = t_s

    return round(best_start, 2), round(best_score, 2)


def find_peak_audio_start(
    clip_path: Path,
    window_dur: float,
    cfg: StudioConfig,
) -> Tuple[float, float]:
    """Scan clip audio energy (astats RMS per 0.5s) to locate authentic animal vocalizations (e.g. ek-ek-ek)."""
    try:
        pr = subprocess.check_output(
            [cfg.ffprobe_cmd(), "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(clip_path)],
            text=True,
            timeout=15,
        )
        total_dur = float(pr.strip() or 0.0)
    except Exception:
        return 0.0, -90.0

    if total_dur <= window_dur + 0.3:
        return 0.0, -20.0

    r = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-v",
            "error",
            "-i",
            str(clip_path),
            "-af",
            "asetnsamples=22050,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-",
            "-vn",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    samples: List[Tuple[float, float]] = []
    curr_t = 0.0
    for line in r.stdout.splitlines():
        if "pts_time:" in line:
            try:
                curr_t = float(line.split("pts_time:")[1].split()[0])
            except Exception:
                pass
        elif "RMS_level=" in line:
            val_s = line.split("=", 1)[1].strip()
            try:
                rms = float(val_s) if val_s != "-inf" else -90.0
                samples.append((curr_t, rms))
            except Exception:
                pass

    if not samples:
        return 0.0, -90.0

    best_start = 0.0
    best_rms = -99.0
    max_start = max(0.0, total_dur - window_dur - 0.1)
    for t_s, _ in samples:
        if t_s > max_start:
            break
        win_vals = [rms for tt, rms in samples if t_s <= tt <= t_s + window_dur]
        if not win_vals:
            continue
        avg_r = sum(win_vals) / len(win_vals)
        if avg_r > best_rms:
            best_rms = avg_r
            best_start = t_s

    return round(best_start, 2), round(best_rms, 2)


def compute_scene_golden_moments(
    assets_dir: Path,
    scenes: List[dict],
    clip_mapping: Dict[int, Path],
    cfg: Optional[StudioConfig] = None,
    force_rescan: bool = False,
) -> Dict[int, dict]:
    """Compute or load `golden_moments.json` with optimal start_s per scene (audio peak for hook, motion peak for action)."""
    studio_cfg = cfg or get_config()
    cache_p = assets_dir / "golden_moments.json"
    if cache_p.exists() and not force_rescan:
        try:
            raw = json.loads(cache_p.read_text(encoding="utf-8"))
            if raw and all((assets_dir / m["filename"]).exists() for m in raw):
                return {int(m["scene_index"]): m for m in raw}
        except Exception:
            pass

    moments_list: List[dict] = []
    for i, sc in enumerate(scenes):
        idx = int(sc["index"])
        clip_p = clip_mapping.get(idx)
        if not clip_p or not clip_p.exists():
            continue
        dur = float(sc.get("duration_s", 2.2))
        blob = f"{sc.get('visual_desc', '')} {sc.get('vo', '')} {sc.get('sfx', '')}".lower()

        is_audio_hook = (i == 0) and any(
            k in blob for k in ("ek-ek", "chattering", "audio", "suara", "bunyi", "bergetar", "mengeong")
        )
        is_fast_action = any(
            k in blob for k in ("kabur", "lari", "cepat", "merayap", "menerkam", "melompat", "nendang", "dorong", "jatuh", "bergetar")
        )

        if is_audio_hook:
            start_s, score = find_peak_audio_start(clip_p, max(1.8, dur), studio_cfg)
            mode = "audio_peak"
            use_audio = True
        elif is_fast_action:
            start_s, score = find_peak_motion_start(clip_p, max(1.8, dur), studio_cfg)
            mode = "motion_peak"
            use_audio = False
        else:
            start_s, score = find_peak_motion_start(clip_p, max(1.8, dur), studio_cfg)
            mode = "motion_balanced"
            use_audio = False

        moments_list.append(
            {
                "scene_index": idx,
                "filename": clip_p.name,
                "start_s": start_s,
                "mode": mode,
                "metric_score": score,
                "use_clip_audio": use_audio,
            }
        )

    cache_p.write_text(json.dumps(moments_list, indent=2, ensure_ascii=False), encoding="utf-8")
    return {int(m["scene_index"]): m for m in moments_list}
