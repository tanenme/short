"""FFmpeg-based Technical AutoQC (blur, darkness, freeze, washed-out color, resolution)."""

from __future__ import annotations

import json
import math
import subprocess
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config

VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}


class ClipAutoQcItem(BaseModel):
    filename: str
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    is_vertical: bool = False
    blur_mean: Optional[float] = None
    yavg: float = 0.0
    ydif: float = 0.0
    satavg: float = 0.0
    verdict: Literal["OK", "WARN", "FAIL"] = "OK"
    flags: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)


class AutoQcResult(BaseModel):
    channel_id: str
    episode_id: str
    total_clips: int
    ok_count: int
    warn_count: int
    fail_count: int
    items: List[ClipAutoQcItem] = Field(default_factory=list)


def _ffprobe_video(path: Path, cfg: StudioConfig) -> dict:
    try:
        out = subprocess.check_output(
            [
                cfg.ffprobe_cmd(),
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            text=True,
            timeout=30,
        )
        d = json.loads(out)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        w = int(v.get("width", 0) or 0)
        h = int(v.get("height", 0) or 0)
        dur = round(float(d.get("format", {}).get("duration", 0.0) or 0.0), 2)
        return {"dur": dur, "w": w, "h": h, "vertical": bool(h > w and w > 0)}
    except Exception:
        return {"dur": 0.0, "w": 0, "h": 0, "vertical": False}


def inspect_clip_metrics(clip_path: Path, cfg: StudioConfig, sample_seconds: float = 4.0) -> ClipAutoQcItem:
    """Measure technical video quality on up to `sample_seconds` of clip (fast + FFmpeg 9 calibrated)."""
    probe = _ffprobe_video(clip_path, cfg)
    dur = probe["dur"]
    w, h = probe["w"], probe["h"]

    # Sample first few seconds (fast even for 60s source clips)
    ss_args = ["-ss", f"{min(1.0, max(0.0, dur * 0.2)):.2f}"] if dur > 2.5 else []
    t_args = ["-t", f"{sample_seconds:.1f}"]

    # 1. signalstats (brightness YAVG, frame motion YDIF, saturation SATAVG)
    r_sig = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-v",
            "error",
            *ss_args,
            *t_args,
            "-i",
            str(clip_path),
            "-vf",
            "fps=4,scale=360:-2,signalstats,metadata=print:file=-",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    vals: Dict[str, List[float]] = {}
    for line in r_sig.stdout.splitlines():
        if "=" in line and "pts" not in line:
            k, v = line.split("=", 1)
            short_k = k.split(".")[-1]
            if short_k in ("YAVG", "YDIF", "SATAVG"):
                try:
                    fv = float(v)
                    if math.isfinite(fv):
                        vals.setdefault(short_k, []).append(fv)
                except ValueError:
                    pass

    yavg = round(sum(vals.get("YAVG", [0.0])) / max(1, len(vals.get("YAVG", []))), 2)
    ydif = round(sum(vals.get("YDIF", [0.0])) / max(1, len(vals.get("YDIF", []))), 3)
    satavg = round(sum(vals.get("SATAVG", [0.0])) / max(1, len(vals.get("SATAVG", []))), 2)

    # 2. blurdetect (higher = blurrier; FFmpeg 9 calibrated: ~6 sharp, >12 soft, >18 blurry)
    r_blur = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            *ss_args,
            *t_args,
            "-i",
            str(clip_path),
            "-vf",
            "fps=3,blurdetect",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    blurs = []
    for line in r_blur.stderr.splitlines():
        if "blur mean:" in line:
            try:
                bv = float(line.split("blur mean:")[1].split()[0])
                if math.isfinite(bv):
                    blurs.append(bv)
            except Exception:
                pass
    blur_mean = round(sum(blurs) / len(blurs), 2) if blurs else None

    flags: List[str] = []
    warns: List[str] = []

    if dur < 1.5:
        flags.append(f"durasi terlalu pendek ({dur}s)")
    if h < 480 and w < 480:
        flags.append(f"resolusi terlalu rendah ({w}x{h})")
    elif h < 720 and w < 720:
        warns.append(f"resolusi sedang ({w}x{h})")

    if blur_mean is not None:
        if blur_mean > 18.0:
            flags.append(f"blur berat ({blur_mean})")
        elif blur_mean > 12.5:
            warns.append(f"agak blur ({blur_mean})")

    if yavg < 28.0:
        flags.append(f"terlalu gelap (YAVG={yavg})")
    elif yavg > 240.0:
        flags.append(f"overexposed (YAVG={yavg})")

    if ydif < 0.12 and dur > 1.5:
        flags.append(f"video beku/freeze (YDIF={ydif})")

    if satavg < 3.0:
        warns.append(f"warna pucat (SATAVG={satavg})")

    verdict: Literal["OK", "WARN", "FAIL"] = "FAIL" if flags else ("WARN" if warns else "OK")
    return ClipAutoQcItem(
        filename=clip_path.name,
        duration_s=dur,
        width=w,
        height=h,
        is_vertical=probe["vertical"],
        blur_mean=blur_mean,
        yavg=yavg,
        ydif=ydif,
        satavg=satavg,
        verdict=verdict,
        flags=flags,
        warnings=warns,
    )


def run_episode_autoqc(
    episode_id: str,
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
    write_report: bool = True,
) -> AutoQcResult:
    """Run technical FFmpeg AutoQC across all video clips in an episode's assets folder."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)

    video_files = (
        sorted(
            p
            for p in assets_dir.iterdir()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS and not p.name.startswith(("thumb_", "_temp_"))
        )
        if assets_dir.exists()
        else []
    )

    items = [inspect_clip_metrics(vf, cfg) for vf in video_files]
    result = AutoQcResult(
        channel_id=prof.channel_id,
        episode_id=episode_id,
        total_clips=len(items),
        ok_count=sum(1 for i in items if i.verdict == "OK"),
        warn_count=sum(1 for i in items if i.verdict == "WARN"),
        fail_count=sum(1 for i in items if i.verdict == "FAIL"),
        items=items,
    )

    if write_report and assets_dir.exists():
        (assets_dir / "autoqc_report.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result
