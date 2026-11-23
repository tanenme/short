"""Cinematic video segment builder, cleanliness filtering, xfade transitions, and CTA overlay."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import StudioConfig
from core.models import ChannelProfile

PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".mkv"}

# Snappy Shorts transition cycle (avoids repetitive single transition)
XFADE_CYCLE = ["fade", "wipeleft", "zoomin", "fadewhite", "wiperight", "smoothleft"]


def filter_clean_clips(
    assets_dir: Path,
    clips: List[Path],
    min_cleanliness_score: int = 70,
) -> Tuple[List[Path], List[str]]:
    """Exclude clips flagged dirty/heavy-overlay in cleanliness_report.json.

    Returns (clean_clips, excluded_reasons). Falls back to all clips if every clip is dirty.
    """
    report_path = assets_dir / "cleanliness_report.json"
    if not report_path.exists():
        return clips, []

    try:
        rows = json.loads(report_path.read_text(encoding="utf-8"))
        by_name = {r.get("filename", ""): r for r in rows}
    except Exception:
        return clips, []

    clean: List[Path] = []
    excluded: List[str] = []
    for c in clips:
        info = by_name.get(c.name)
        if not info:
            clean.append(c)
            continue
        status = str(info.get("status", "")).upper()
        score = info.get("score")
        if status in ("BANYAK_OVERLAY", "KOTOR") or (score is not None and int(score) < min_cleanliness_score):
            excluded.append(f"{c.name} ({status}, skor {score})")
        else:
            clean.append(c)

    if not clean:
        return clips, []
    return clean, excluded


def get_cleanliness_map(assets_dir: Path) -> Dict[str, dict]:
    report_path = assets_dir / "cleanliness_report.json"
    if not report_path.exists():
        return {}
    try:
        rows = json.loads(report_path.read_text(encoding="utf-8"))
        return {r.get("filename", ""): r for r in rows}
    except Exception:
        return {}


def probe_media_info(file_path: Path, cfg: StudioConfig) -> dict:
    try:
        cmd = [
            cfg.ffprobe_cmd(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(file_path),
        ]
        out = subprocess.check_output(cmd, text=True)
        d = json.loads(out)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
        return {
            "duration": float(d.get("format", {}).get("duration", 0.0) or 0.0),
            "width": int(v.get("width", 0) or 0),
            "height": int(v.get("height", 0) or 0),
            "has_audio": bool(a),
        }
    except Exception:
        return {"duration": 0.0, "width": 0, "height": 0, "has_audio": False}


def render_cinematic_segment(
    clip_path: Path,
    seg_out: Path,
    target_dur: float,
    scene_idx: int,
    profile: ChannelProfile,
    cfg: StudioConfig,
    allow_flip: bool = True,
    usage_offset: int = 0,
    forced_start_s: Optional[float] = None,
) -> dict:
    """Render one Shorts scene with anti-reused transforms + dynamic micro-motion.

    Returns transform audit metadata for sources/episode manifest.
    """
    vis = profile.visual
    W, H, FPS = vis.width, vis.height, vis.fps
    transforms: List[str] = ["crop_9_16"]

    is_photo = clip_path.suffix.lower() in PHOTO_EXTS
    z_min = vis.zoom_punch_min
    z_max = vis.zoom_punch_max
    zoom_in = (scene_idx % 2 == 1)

    if is_photo:
        frames = max(15, int(round(target_dur * FPS)))
        z0, z1 = (z_min, z_max + 0.04) if zoom_in else (z_max + 0.04, z_min)
        pan_dir = 0.025 if zoom_in else -0.025
        vf_parts = [
            f"scale={W * 2}:{H * 2}:force_original_aspect_ratio=increase",
            f"crop={W * 2}:{H * 2}",
            f"zoompan=z='{z0:.4f}+({z1 - z0:.4f})*on/{max(1, frames - 1)}':"
            f"d={frames}:x='iw/2-(iw/zoom)/2+({pan_dir}*iw*on/{max(1, frames - 1)})':"
            f"y='ih/2-(ih/zoom)/2':s={W}x{H}:fps={FPS}",
            "eq=contrast=1.06:saturation=1.08:brightness=0.01",
            "format=yuv420p",
        ]
        transforms.extend([f"ken_burns_{z0:.2f}_to_{z1:.2f}", "color_grade"])
        cmd = [
            cfg.ffmpeg_cmd(),
            "-y",
            "-loop",
            "1",
            "-i",
            str(clip_path),
            "-t",
            f"{target_dur:.3f}",
            "-vf",
            ",".join(vf_parts),
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            str(seg_out),
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"transforms": transforms, "start_trim": 0.0, "speed": 1.0, "is_photo": True}

    info = probe_media_info(clip_path, cfg)
    clip_dur = info["duration"]

    # Subtle speed boost (1.05x) when clip has headroom -> breaks source timing fingerprint
    speed = 1.05 if clip_dur >= (target_dur * 1.12 + 0.4) else 1.0
    needed_src_dur = target_dur * speed

    # Golden Moment trim window (uses exact forced_start_s when available, else smart inner window)
    if forced_start_s is not None and clip_dur > needed_src_dur:
        start_trim = max(0.0, min(clip_dur - needed_src_dur - 0.05, float(forced_start_s)))
    elif clip_dur > needed_src_dur + 0.6:
        usable = clip_dur - needed_src_dur
        anchor_ratio = [0.30, 0.55, 0.15, 0.72][usage_offset % 4]
        start_trim = max(0.2, min(usable - 0.1, usable * anchor_ratio))
    elif clip_dur > needed_src_dur:
        start_trim = (clip_dur - needed_src_dur) / 2.0
    else:
        start_trim = 0.0

    # Smooth animated zoom via per-frame scale (jitter-free compared to integer zoompan on video)
    z0, z1 = (z_min, z_max) if zoom_in else (z_max, z_min)
    dur_safe = max(0.5, target_dur)
    zoom_expr = f"{z0:.4f}+({z1 - z0:.4f})*min(t/{dur_safe:.3f}\\,1)"

    is_horizontal_or_square = info["width"] > 0 and info["height"] > 0 and info["width"] >= info["height"]
    use_blur_canvas = getattr(vis, "framing_mode", "auto_blur_canvas") == "auto_blur_canvas" and is_horizontal_or_square

    # Loop source if a rare clip is shorter than target_dur
    input_flags = ["-stream_loop", "-1"] if (0 < clip_dur < needed_src_dur) else []

    if use_blur_canvas:
        # Competitor-grade Blurred 9:16 Canvas Fill (preserves full 16:9 / 1:1 action without chopping subjects)
        transforms[0] = "blur_canvas_9_16"
        transforms.append(f"dynamic_zoom_{z0:.2f}_to_{z1:.2f}")
        flip_op = ",hflip" if (allow_flip and (scene_idx % 3 == 2)) else ""
        if flip_op:
            transforms.append("hflip")
        spd_op = f",setpts=PTS/{speed:.3f}" if speed != 1.0 else ""
        if spd_op:
            transforms.append(f"speed_{speed:.2f}x")
        transforms.append("color_grade")

        fg_h = 1140
        fc = (
            f"[0:v]split=2[bg_in][fg_in];"
            f"[bg_in]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:4,"
            f"eq=brightness=0.01:saturation=1.02[bg];"
            f"[fg_in]scale={W}:{fg_h}:force_original_aspect_ratio=increase,crop={W}:{fg_h},"
            f"scale=w='trunc(iw*({zoom_expr})/2)*2':h='trunc(ih*({zoom_expr})/2)*2':eval=frame,"
            f"crop={W}:{fg_h}:(iw-{W})/2:(ih-{fg_h})/2{flip_op},"
            f"eq=contrast=1.06:saturation=1.08:brightness=0.01[fg];"
            f"[bg][fg]overlay=0:(H-h)/2{spd_op},fps={FPS},setsar=1,format=yuv420p[v]"
        )
        cmd = [
            cfg.ffmpeg_cmd(),
            "-y",
            "-ss",
            f"{start_trim:.3f}",
            *input_flags,
            "-i",
            str(clip_path),
            "-t",
            f"{target_dur:.3f}",
            "-filter_complex",
            fc,
            "-map",
            "[v]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "19",
            "-pix_fmt",
            "yuv420p",
            str(seg_out),
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {
            "transforms": transforms,
            "start_trim": round(start_trim, 3),
            "speed": speed,
            "is_photo": False,
            "has_audio": info["has_audio"],
        }

    vf_parts = [
        f"scale={W}:{H}:force_original_aspect_ratio=increase",
        f"crop={W}:{H}",
        f"scale=w='trunc(iw*({zoom_expr})/2)*2':h='trunc(ih*({zoom_expr})/2)*2':eval=frame",
        f"crop={W}:{H}:(iw-{W})/2:(ih-{H})/2",
    ]
    transforms.append(f"dynamic_zoom_{z0:.2f}_to_{z1:.2f}")

    if allow_flip and (scene_idx % 3 == 2):
        vf_parts.append("hflip")
        transforms.append("hflip")

    if speed != 1.0:
        vf_parts.append(f"setpts=PTS/{speed:.3f}")
        transforms.append(f"speed_{speed:.2f}x")

    vf_parts.extend(
        [
            "eq=contrast=1.06:saturation=1.08:brightness=0.01",
            f"fps={FPS}",
            "setsar=1",
            "format=yuv420p",
        ]
    )
    transforms.append("color_grade")

    cmd = [
        cfg.ffmpeg_cmd(),
        "-y",
        "-ss",
        f"{start_trim:.3f}",
        *input_flags,
        "-i",
        str(clip_path),
        "-t",
        f"{target_dur:.3f}",
        "-vf",
        ",".join(vf_parts),
        "-an",
        "-c:v",
        "libx264",
        "-preset",
        "fast",
        "-crf",
        "19",
        "-pix_fmt",
        "yuv420p",
        str(seg_out),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {
        "transforms": transforms,
        "start_trim": round(start_trim, 3),
        "speed": speed,
        "is_photo": False,
        "has_audio": info["has_audio"],
    }


def get_v33_transition_spec(cut_index: int) -> Tuple[str, float]:
    """Forensic dVlZFNF8NU8 transition recipe @ 60fps (safe >=3-frame xfade minimum).

    - Cut #3 (Act change: Hook/CTA -> Mechanism): 5-frame Radial Zoom-In (0.085s)
    - Cuts #1, #5, #7, #8 (Entry & Climax/Payoff): 3-4 frame White Exposure Bloom (0.060s)
    - Intra-beat Match Cuts (#2, #4, #6, #9+): 3-frame snappy cut (0.050s)
    """
    if cut_index == 3:
        return ("zoomin", 0.085)
    if cut_index in (1, 5, 7, 8):
        return ("fadewhite", 0.060)
    return ("fade", 0.050)


def concat_with_transitions(
    segments: List[Path],
    segment_durations: List[float],
    out_video: Path,
    cfg: StudioConfig,
    trans_dur: float = 0.12,
    fps: int = 30,
    transition_style: str = "squooshify_v33",
) -> Path:
    """Join rendered video segments using v3.3 forensic transitions (hard cut + 5f zoom + 3f flash) or classic xfade."""
    if not segments:
        raise ValueError("No video segments to concatenate.")
    if len(segments) == 1:
        cmd = [cfg.ffmpeg_cmd(), "-y", "-i", str(segments[0]), "-c", "copy", str(out_video)]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return out_video

    cmd = [cfg.ffmpeg_cmd(), "-y"]
    for seg in segments:
        cmd.extend(["-i", str(seg)])

    # Normalize timebase across inputs before xfade
    filters = [f"[{i}:v]settb=AVTB,fps={fps}[v{i}]" for i in range(len(segments))]

    prev_label = "v0"
    running_dur = segment_durations[0]
    for i in range(1, len(segments)):
        if transition_style == "squooshify_v33":
            trans_name, raw_td = get_v33_transition_spec(i)
            eff_td = min(raw_td, max(0.017, segment_durations[i - 1] * 0.2), max(0.017, segment_durations[i] * 0.2))
        else:
            trans_name = XFADE_CYCLE[(i - 1) % len(XFADE_CYCLE)]
            eff_td = min(trans_dur, max(0.05, segment_durations[i - 1] * 0.25), max(0.05, segment_durations[i] * 0.25))

        offset = max(0.0, running_dur - eff_td)
        out_label = f"xf{i}" if i < len(segments) - 1 else "vout"
        filters.append(
            f"[{prev_label}][v{i}]xfade=transition={trans_name}:duration={eff_td:.3f}:offset={offset:.3f}[{out_label}]"
        )
        prev_label = out_label
        running_dur = offset + segment_durations[i]

    cmd.extend(
        [
            "-filter_complex",
            ";".join(filters),
            "-map",
            "[vout]",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(out_video),
        ]
    )
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return out_video


def find_subscribe_overlay(cfg: StudioConfig, channel_id: str) -> Optional[Path]:
    """Find a true green-screen overlay (avoids fake-checkerboard videos)."""
    ov_dir = cfg.channel_overlays_dir(channel_id)
    if not ov_dir.exists():
        return None
    cands = sorted(ov_dir.glob("*.mp4"))
    for c in cands:
        low = c.name.lower()
        if ("greenscreen" in low or "chroma" in low) and "subscribe" in low:
            return c
    for c in cands:
        low = c.name.lower()
        if "greenscreen" in low or "chroma" in low:
            return c
    return None


def generate_qc_contact_sheet(
    video_path: Path,
    out_jpg: Path,
    cfg: StudioConfig,
    cols: int = 3,
    rows: int = 3,
) -> Path:
    """Create a labeled NxM contact sheet of the rendered MP4 for visual Creative Director QC."""
    info = probe_media_info(video_path, cfg)
    dur = max(1.0, info["duration"])
    total = cols * rows
    # Sample evenly across the video from 0.4s to dur - 0.3s
    timestamps = [round(0.4 + i * max(0.1, (dur - 0.7)) / max(1, total - 1), 2) for i in range(total)]

    tmp_dir = out_jpg.parent / "_qc_sheet_tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    frame_files: List[Path] = []
    for idx, ts in enumerate(timestamps):
        fr = tmp_dir / f"f_{idx:02d}.jpg"
        cmd = [
            cfg.ffmpeg_cmd(),
            "-y",
            "-ss",
            f"{ts:.2f}",
            "-i",
            str(video_path),
            "-frames:v",
            "1",
            "-vf",
            f"scale=360:640,drawtext=text='t={ts:.1f}s':fontcolor=yellow:fontsize=28:x=10:y=10:box=1:boxcolor=black@0.7",
            "-update",
            "1",
            "-q:v",
            "3",
            str(fr),
        ]
        subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        if fr.exists():
            frame_files.append(fr)

    if not frame_files:
        raise RuntimeError(f"Gagal mengekstrak frame QC dari {video_path}")

    row_imgs: List[Path] = []
    for r in range(0, len(frame_files), cols):
        grp = frame_files[r : r + cols]
        if len(grp) < cols:
            break
        ro = tmp_dir / f"row_{r}.png"
        cmd = [cfg.ffmpeg_cmd(), "-y"]
        for g in grp:
            cmd.extend(["-i", str(g)])
        fc = "".join(f"[{j}:v]" for j in range(len(grp))) + f"hstack=inputs={len(grp)}[row]"
        cmd.extend(["-filter_complex", fc, "-map", "[row]", "-frames:v", "1", "-update", "1", str(ro)])
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        row_imgs.append(ro)

    cmd = [cfg.ffmpeg_cmd(), "-y"]
    for ro in row_imgs:
        cmd.extend(["-i", str(ro)])
    fc = "".join(f"[{j}:v]" for j in range(len(row_imgs))) + f"vstack=inputs={len(row_imgs)}[out]"
    cmd.extend(["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-update", "1", "-q:v", "3", str(out_jpg)])
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    import shutil
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_jpg
