"""3-Frame Temporal Strip Vision Judge & Auto-Trim Golden Moment Bridge (Sourcing & Vision v4.0).

1. Stitches up to 3 preview frames (`Start • Mid • End`) into a single `960x240` strip via FFmpeg
   (zero full-video download required for Pexels / YouTube / Wikimedia).
2. Evaluates temporal action relevance, negative-action traps, burned-in text, 9:16 caption
   safe-zone headroom (`margin_v=440`), and `crop_x_norm` in a single VLM call.
3. Bridges `download_to_episode` directly into `moment_finder.py` v2.4 to auto-trim bloated
   downloads down to the 3.5-4.6s peak action window (`<= 4.8s` YPP-safe) and lock `golden_moments.json`.
"""

from __future__ import annotations

import base64
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import openai

from core.config import StudioConfig, get_config, resolve_ai_model
from .providers import SourcedCandidate, UA_WEB, _collect_recent_creators_7d, compute_ready_score

EDITOR_DIR = Path(__file__).resolve().parent.parent / "editor-engine"
if str(EDITOR_DIR) not in sys.path:
    sys.path.insert(0, str(EDITOR_DIR))


def resolve_candidate_frame_urls(cand: SourcedCandidate, cfg: Optional[StudioConfig] = None) -> List[str]:
    """Resolve up to 3 chronological preview image URLs for a candidate without downloading full video."""
    if cand.preview_frames:
        valid = [u for u in cand.preview_frames if u and u.startswith("http")]
        if valid:
            return valid[:3]

    u = cand.url or ""
    if "youtube.com" in u or "youtu.be" in u:
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
        if m:
            vid = m.group(1)
            return [
                f"https://i.ytimg.com/vi/{vid}/1.jpg",
                f"https://i.ytimg.com/vi/{vid}/2.jpg",
                f"https://i.ytimg.com/vi/{vid}/3.jpg",
            ]

    if cand.thumbnail and cand.thumbnail.startswith("http"):
        return [cand.thumbnail]
    return []


def build_three_frame_strip_data_url(
    frame_urls: Sequence[str],
    local_video_path: Optional[Path] = None,
    cfg: Optional[StudioConfig] = None,
) -> Optional[str]:
    """Build a single 960x270 JPEG data-URL horizontal filmstrip from 1-3 frames using FFmpeg."""
    cfg = cfg or get_config()
    with tempfile.TemporaryDirectory(prefix="sv4_strip_") as tmp_str:
        tmp = Path(tmp_str)
        img_paths: List[Path] = []

        if local_video_path and local_video_path.exists():
            # Extract 3 evenly spaced frames from local Vault clip
            for idx, pct in enumerate((0.20, 0.50, 0.80)):
                out_jpg = tmp / f"f_{idx}.jpg"
                subprocess.run(
                    [
                        cfg.ffmpeg_cmd(),
                        "-y",
                        "-v",
                        "error",
                        "-ss",
                        f"{max(0.1, pct * 4.0):.2f}",
                        "-i",
                        str(local_video_path),
                        "-frames:v",
                        "1",
                        "-vf",
                        "scale=320:270:force_original_aspect_ratio=decrease,pad=320:270:(ow-iw)/2:(oh-ih)/2",
                        str(out_jpg),
                    ],
                    capture_output=True,
                    timeout=10,
                )
                if out_jpg.exists() and out_jpg.stat().st_size > 200:
                    img_paths.append(out_jpg)
        else:
            for idx, furl in enumerate(list(frame_urls)[:3]):
                try:
                    req = urllib.request.Request(furl, headers={"User-Agent": UA_WEB})
                    with urllib.request.urlopen(req, timeout=8) as resp:
                        raw = resp.read()
                    if len(raw) < 200 or raw[:80].lstrip().lower().startswith((b"<!doctype", b"<html")):
                        continue
                    fp = tmp / f"dl_{idx}.jpg"
                    fp.write_bytes(raw)
                    img_paths.append(fp)
                except Exception:
                    continue

        if not img_paths:
            return None

        if len(img_paths) == 1:
            b64 = base64.b64encode(img_paths[0].read_bytes()).decode("ascii")
            return f"data:image/jpeg;base64,{b64}"

        strip_out = tmp / "strip.jpg"
        in_args: List[str] = []
        norm_chains: List[str] = []
        tags: List[str] = []
        for idx, p in enumerate(img_paths):
            in_args.extend(["-i", str(p)])
            norm_chains.append(
                f"[{idx}:v]scale=320:270:force_original_aspect_ratio=decrease,pad=320:270:(ow-iw)/2:(oh-ih)/2,setsar=1[s{idx}]"
            )
            tags.append(f"[s{idx}]")

        fg = ";".join(norm_chains) + f";{''.join(tags)}hstack=inputs={len(img_paths)}[vout]"
        subprocess.run(
            [cfg.ffmpeg_cmd(), "-y", "-v", "error", *in_args, "-filter_complex", fg, "-map", "[vout]", "-q:v", "4", str(strip_out)],
            capture_output=True,
            timeout=10,
        )
        target = strip_out if (strip_out.exists() and strip_out.stat().st_size > 200) else img_paths[0]
        b64 = base64.b64encode(target.read_bytes()).decode("ascii")
        return f"data:image/jpeg;base64,{b64}"


def evaluate_candidate_vision_strip(
    cand: SourcedCandidate,
    scenario: str,
    negative_terms: Optional[Sequence[str]] = None,
    episode_id: str = "",
    channel_id: Optional[str] = None,
    cfg: Optional[StudioConfig] = None,
) -> SourcedCandidate:
    """Evaluate a candidate with the 3-Frame Temporal Strip Vision Judge and recompute its ReadyScore."""
    cfg = cfg or get_config()
    recent_7d = _collect_recent_creators_7d(episode_id, channel_id, cfg)

    # Fast short-circuit if candidate already hit a deterministic negative keyword
    if cand.negative_hit:
        cand.vision_evaluated = True
        cand.vision_summary = f"Diblokir otomatis: terdeteksi kata aksi salah '{cand.negative_hit}'."
        return compute_ready_score(cand, scenario, negative_terms=negative_terms, recent_creators_7d=recent_7d)

    local_vp = Path(cand.local_path) if cand.local_path else None
    frame_urls = resolve_candidate_frame_urls(cand, cfg=cfg)
    image_data_url = build_three_frame_strip_data_url(frame_urls, local_video_path=local_vp, cfg=cfg)
    if not image_data_url:
        cand.vision_summary = "Preview frame belum tersedia; menggunakan skor metadata & lisensi."
        return compute_ready_score(cand, scenario, negative_terms=negative_terms, recent_creators_7d=recent_7d)

    neg_str = ", ".join(negative_terms or []) or "none"
    prompt = f"""Anda adalah Senior Video QC Judge untuk YouTube Shorts 9:16.
Gambar ini adalah filmstrip berurutan (Kiri=Awal, Tengah=Tengah, Kanan=Akhir) dari satu kandidat video.
Target Skenario Visual: "{scenario}"
Aksi Salah yang HARUS Ditolak (Negative Filter): [{neg_str}]

Evaluasi secara ketat:
1. action_match_score (0-100): Apakah subjek & gerakan fisik di gambar benar-benar cocok dengan "{scenario}"? Jika gambar justru menampilkan aksi negatif [{neg_str}], beri skor <= 15!
2. detected_negative_action: tulis kata aksi negatif jika terdeteksi (misal "eating" / "rolling"), atau "" jika aman.
3. clean_headroom_score (0-100): Apakah frame bebas subtitle tempelan/watermark besar DAN memiliki ruang aman di 22% bawah untuk teks caption putih besar?
4. motion_continuity_score (0-100): Apakah ke-3 frame tampak satu adegan kamera konsisten (bukan kolase meme / split-screen)?
5. crop_x_norm (0.15 - 0.85): posisi horizontal subjek utama (0.50 = tengah).
6. best_frame_slot (1, 2, atau 3): bagian mana (1=awal, 2=tengah, 3=akhir) yang paling kuat aksinya.
7. summary_id: penjelasan singkat 1 kalimat Bahasa Indonesia.

Jawab HANYA JSON valid:
{{
  "action_match_score": 88,
  "detected_negative_action": "",
  "clean_headroom_score": 92,
  "motion_continuity_score": 90,
  "crop_x_norm": 0.50,
  "best_frame_slot": 2,
  "summary_id": "Terlihat kaki belakang anjing menggaruk tanah berumput, area bawah bersih untuk caption."
}}"""

    try:
        vision_model = resolve_ai_model("vision", cfg)
        client = openai.OpenAI(base_url=cfg.ai_base_url, api_key=cfg.ai_api_key or "local", timeout=14.0, max_retries=0)
        resp = client.chat.completions.create(
            model=vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": image_data_url}},
                    ],
                }
            ],
            temperature=0.1,
            max_tokens=280,
        )
        raw = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            det_neg = str(parsed.get("detected_negative_action") or "").strip().lower()
            if det_neg in ("none", "tidak ada", "aman", "-"):
                det_neg = ""
            if det_neg:
                cand.negative_hit = det_neg
            cand.crop_x_norm = round(min(0.85, max(0.15, float(parsed.get("crop_x_norm") or 0.50))), 3)
            slot = int(parsed.get("best_frame_slot") or 2)
            dur = float(cand.duration_s or 0.0)
            if dur > 5.0:
                center_r = 0.22 if slot == 1 else (0.50 if slot == 2 else 0.76)
                st = max(0.0, round(dur * center_r - 1.8, 2))
                en = min(round(dur, 2), round(st + 3.8, 2))
                cand.suggested_trim = [st, en]
            cand.vision_evaluated = True
            cand.vision_summary = str(parsed.get("summary_id") or "").strip()[:200]
            return compute_ready_score(
                cand,
                scenario,
                negative_terms=negative_terms,
                recent_creators_7d=recent_7d,
                semantic_override=int(parsed.get("action_match_score", 70)),
                clean_override=int(parsed.get("clean_headroom_score", 80)),
                motion_override=int(parsed.get("motion_continuity_score", 82)),
            )
    except Exception as exc:
        cand.vision_summary = f"Fallback skor heuristik (Vision offline: {str(exc)[:60]})"

    return compute_ready_score(cand, scenario, negative_terms=negative_terms, recent_creators_7d=recent_7d)


def evaluate_candidates_batch(
    candidates: Sequence[SourcedCandidate],
    scenario: str,
    negative_terms: Optional[Sequence[str]] = None,
    episode_id: str = "",
    channel_id: Optional[str] = None,
    max_eval: int = 5,
    cfg: Optional[StudioConfig] = None,
) -> Dict[str, List[SourcedCandidate]]:
    """Run parallel 3-Frame Strip Vision evaluation on Top-N candidates and partition accepted vs rejected."""
    cfg = cfg or get_config()
    items = [c.model_copy(deep=True) for c in candidates]
    to_eval = items[: max(1, min(max_eval, len(items)))]
    rest = items[len(to_eval) :]

    with ThreadPoolExecutor(max_workers=min(4, max(1, len(to_eval)))) as pool:
        futs = [
            pool.submit(
                evaluate_candidate_vision_strip,
                c,
                scenario,
                negative_terms,
                episode_id,
                channel_id,
                cfg,
            )
            for c in to_eval
        ]
        evaluated = [f.result() for f in futs]

    combined = evaluated + rest
    accepted = [c for c in combined if c.ready_status != "REJECTED"]
    rejected = [c for c in combined if c.ready_status == "REJECTED"]
    accepted.sort(key=lambda c: (c.vision_evaluated, c.score), reverse=True)
    rejected.sort(key=lambda c: c.score, reverse=True)
    return {"accepted": accepted, "rejected": rejected}


def auto_trim_and_lock_golden_moment(
    clip_path: Path,
    episode_id: str,
    channel_id: Optional[str] = None,
    beat_index: Optional[int] = None,
    scenario: str = "",
    crop_x_norm_hint: float = 0.50,
    target_trim_s: float = 4.0,
    trim_threshold_s: float = 8.5,
    cfg: Optional[StudioConfig] = None,
) -> dict:
    """Scan downloaded video via `moment_finder` v2.4, auto-trim bloated clips to <=4.8s peak action, and lock `golden_moments.json`."""
    from moment_finder import extract_multimodal_signals, score_candidate_windows

    cfg = cfg or get_config()
    total_dur, frames, cuts = extract_multimodal_signals(clip_path, cfg, sample_fps=2.0)
    if total_dur <= 0.4 or not frames:
        return {
            "trimmed": False,
            "trimmed_path": str(clip_path),
            "trim_window": [0.0, round(max(0.0, total_dur), 2)],
            "golden_moment_locked": False,
        }

    low_scen = (scenario or "").lower()
    intent = (
        "calm"
        if any(k in low_scen for k in ("calm", "sleep", "tidur", "melek", "cute", "subscribe"))
        else ("action" if any(k in low_scen for k in ("kick", "dig", "scratch", "nendang", "dorong", "chatter", "ek", "run")) else "balanced")
    )
    win_dur = min(max(2.4, target_trim_s), max(1.8, total_dur - 0.2), 4.5)
    cands = score_candidate_windows(total_dur, frames, cuts, window_dur=win_dur, intent=intent, top_k=2)
    best = cands[0] if cands else {"start_s": 0.0, "end_s": round(min(total_dur, 4.0), 3), "score": 72.0, "motion": 60.0}

    peak_st = float(best["start_s"])
    peak_en = float(best["end_s"])
    final_path = clip_path
    gm_start_in_file = peak_st
    gm_end_in_file = peak_en
    was_trimmed = False

    # Auto-trim bloated source videos (> 8.5s) down to a crisp ready-to-use clip (<= 4.80s + 0.20s handle)
    if total_dur > trim_threshold_s:
        slice_st = max(0.0, round(peak_st - 0.15, 3))
        slice_en = min(total_dur, round(slice_st + min(4.75, (peak_en - peak_st) + 0.35), 3))
        trimmed_p = clip_path.with_name(f"{clip_path.stem}_ready.mp4")
        rc = subprocess.run(
            [
                cfg.ffmpeg_cmd(),
                "-y",
                "-v",
                "error",
                "-ss",
                f"{slice_st:.3f}",
                "-to",
                f"{slice_en:.3f}",
                "-i",
                str(clip_path),
                "-vf",
                "scale='min(1920,iw)':'min(1920,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                str(trimmed_p),
            ],
            capture_output=True,
            timeout=90,
        )
        if rc.returncode == 0 and trimmed_p.exists() and trimmed_p.stat().st_size > 4096:
            try:
                clip_path.unlink(missing_ok=True)
            except Exception:
                pass
            final_path = trimmed_p
            gm_start_in_file = round(max(0.0, peak_st - slice_st), 3)
            gm_end_in_file = round(min(slice_en - slice_st, gm_start_in_file + (peak_en - peak_st)), 3)
            was_trimmed = True

    gm_locked = False
    if beat_index is not None and int(beat_index) >= 1:
        assets_dir = clip_path.parent
        gm_file = assets_dir / "golden_moments.json"
        gm_data: Dict[str, dict] = {}
        if gm_file.exists():
            try:
                gm_data = json.loads(gm_file.read_text(encoding="utf-8")) or {}
            except Exception:
                gm_data = {}

        gm_data[str(int(beat_index))] = {
            "scene_index": int(beat_index),
            "filename": final_path.name,
            "start_s": gm_start_in_file,
            "end_s": gm_end_in_file,
            "crop_x_norm": round(min(0.85, max(0.15, float(crop_x_norm_hint or 0.50))), 3),
            "allow_flip": True,
            "motion_verified": float(best.get("motion", 60.0)) >= 35.0,
            "metric_score": round(float(best.get("score", 75.0)), 1),
            "mode": "sourcing_v4_autotrim" if was_trimmed else "sourcing_v4_peak_lock",
            "signals": {"top_candidates": cands},
        }
        gm_file.write_text(json.dumps(gm_data, indent=2, ensure_ascii=False), encoding="utf-8")
        gm_locked = True

    return {
        "trimmed": was_trimmed,
        "trimmed_path": str(final_path),
        "original_duration_s": round(total_dur, 2),
        "trim_window": [round(peak_st, 2), round(peak_en, 2)],
        "in_file_window": [gm_start_in_file, gm_end_in_file],
        "metric_score": round(float(best.get("score", 75.0)), 1),
        "crop_x_norm": round(float(crop_x_norm_hint or 0.50), 3),
        "golden_moment_locked": gm_locked,
    }
