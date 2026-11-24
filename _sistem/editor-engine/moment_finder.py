"""Golden Moment Engine v2.1 — Parallel Signal Cache + Action-Triplet (T0->T1->T2) Multimodal Vision Retrieval."""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from core.config import StudioConfig, get_config


def _probe_duration(clip_path: Path, cfg: StudioConfig) -> float:
    try:
        pr = subprocess.check_output(
            [
                cfg.ffprobe_cmd(),
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(clip_path),
            ],
            text=True,
            timeout=15,
        )
        return float(pr.strip() or 0.0)
    except Exception:
        return 0.0


def extract_multimodal_signals(
    clip_path: Path,
    cfg: StudioConfig,
    sample_fps: float = 1.5,
) -> Tuple[float, List[dict], List[float]]:
    """Fast single-pass FFmpeg extraction (180p) of YDIF, YAVG, SATAVG, blur, and internal scdet cuts."""
    total_dur = _probe_duration(clip_path, cfg)
    if total_dur <= 0.2:
        return 0.0, [], []

    r = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-v",
            "error",
            "-i",
            str(clip_path),
            "-vf",
            f"fps={sample_fps},scale=180:-2,scdet=t=12.0,signalstats,blurdetect,metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )

    frames: List[dict] = []
    cuts: List[float] = []
    cur: Optional[dict] = None

    for line in r.stdout.splitlines():
        if "pts_time:" in line:
            if cur is not None:
                frames.append(cur)
            try:
                t = float(line.split("pts_time:")[1].split()[0])
            except Exception:
                t = len(frames) / max(0.5, sample_fps)
            cur = {"t": round(t, 3), "ydif": 0.0, "yavg": 128.0, "satavg": 40.0, "blur": 2.0, "scdet": 0.0}
        elif cur is not None:
            if "lavfi.signalstats.YDIF=" in line:
                try:
                    cur["ydif"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif "lavfi.signalstats.YAVG=" in line:
                try:
                    cur["yavg"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif "lavfi.signalstats.SATAVG=" in line:
                try:
                    cur["satavg"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif "lavfi.blur=" in line:
                try:
                    cur["blur"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif "lavfi.scd.score=" in line:
                try:
                    sc = float(line.split("=", 1)[1].strip())
                    cur["scdet"] = sc
                    if sc >= 14.0 and cur["t"] > 0.35:
                        cuts.append(cur["t"])
                except Exception:
                    pass

    if cur is not None:
        frames.append(cur)

    clean_cuts: List[float] = []
    for ct in sorted(cuts):
        if not clean_cuts or ct - clean_cuts[-1] >= 0.40:
            clean_cuts.append(round(ct, 3))

    return round(total_dur, 3), frames, clean_cuts


def score_candidate_windows(
    total_dur: float,
    frames: List[dict],
    cuts: List[float],
    window_dur: float,
    intent: str = "action",
    top_k: int = 4,
    excluded_windows: Optional[List[Tuple[float, float]]] = None,
) -> List[dict]:
    """4-signal fusion + cut-straddle penalty + cross-scene anti-repeat exclusion + Temporal NMS (IoU < 0.20)."""
    if total_dur <= window_dur + 0.35 or not frames:
        return [
            {
                "start_s": 0.0,
                "end_s": round(min(total_dur, window_dur), 2),
                "score": 50.0,
                "motion": 0.0,
                "sharpness": 50.0,
                "exposure": 50.0,
                "crosses_cut": False,
            }
        ]

    excl = excluded_windows or []
    max_start = max(0.0, total_dur - window_dur - 0.08)
    raw_windows: List[dict] = []

    for fr in frames:
        st = float(fr["t"])
        if st > max_start:
            break
        en = st + window_dur
        win_f = [f for f in frames if st <= f["t"] <= en]
        if not win_f:
            continue

        # Cross-scene anti-repeat check: heavy penalty if overlapping an already-claimed window on the same clip
        overlaps_claimed = False
        for ex_st, ex_en in excl:
            inter = max(0.0, min(en, ex_en) - max(st, ex_st))
            union = max(0.01, (en - st) + (ex_en - ex_st) - inter)
            if (inter / union) >= 0.15:
                overlaps_claimed = True
                break

        internal_cuts = [ct for ct in cuts if (st + 0.25) < ct < (en - 0.25)]
        crosses_cut = len(internal_cuts) > 0

        ydifs = [f["ydif"] for f in win_f]
        avg_yd = sum(ydifs) / len(ydifs)
        max_yd = max(ydifs)
        if intent == "action":
            if avg_yd < 1.0:
                motion_sc = avg_yd * 25.0
            elif avg_yd <= 18.0:
                motion_sc = 35.0 + (avg_yd / 18.0) * 65.0
            else:
                motion_sc = max(20.0, 100.0 - (avg_yd - 18.0) * 3.0)
            if max_yd > 30.0:
                motion_sc -= 25.0
        elif intent == "calm":
            motion_sc = max(10.0, 100.0 - abs(avg_yd - 3.5) * 8.0)
        else:
            motion_sc = max(15.0, min(100.0, 45.0 + avg_yd * 3.5))
            if max_yd > 30.0:
                motion_sc -= 20.0
        motion_sc = max(0.0, min(100.0, motion_sc))

        avg_blur = sum(f["blur"] for f in win_f) / len(win_f)
        sharp_sc = max(0.0, min(100.0, 105.0 - avg_blur * 14.0))

        avg_y = sum(f["yavg"] for f in win_f) / len(win_f)
        avg_sat = sum(f["satavg"] for f in win_f) / len(win_f)
        if 70.0 <= avg_y <= 205.0:
            exp_sc = 92.0
        elif avg_y < 45.0 or avg_y > 235.0:
            exp_sc = 15.0
        else:
            dist = min(abs(avg_y - 70.0), abs(avg_y - 205.0))
            exp_sc = max(25.0, 92.0 - dist * 2.2)
        exp_sc = min(100.0, exp_sc + min(10.0, avg_sat / 6.0))

        if intent == "action":
            total_sc = 0.48 * motion_sc + 0.28 * sharp_sc + 0.24 * exp_sc
        elif intent == "calm":
            total_sc = 0.25 * motion_sc + 0.42 * sharp_sc + 0.33 * exp_sc
        else:
            total_sc = 0.38 * motion_sc + 0.32 * sharp_sc + 0.30 * exp_sc

        if crosses_cut:
            total_sc -= 30.0
        if overlaps_claimed:
            total_sc -= 55.0
        if st < 0.25 or en > total_dur - 0.35:
            total_sc -= 6.0

        raw_windows.append(
            {
                "start_s": round(st, 2),
                "end_s": round(en, 2),
                "score": round(max(0.0, total_sc), 2),
                "motion": round(motion_sc, 1),
                "sharpness": round(sharp_sc, 1),
                "exposure": round(exp_sc, 1),
                "crosses_cut": crosses_cut,
                "overlaps_claimed": overlaps_claimed,
            }
        )

    if not raw_windows:
        return [{"start_s": 0.0, "end_s": round(window_dur, 2), "score": 50.0, "motion": 50.0, "sharpness": 50.0, "exposure": 50.0, "crosses_cut": False}]

    raw_windows.sort(key=lambda w: w["score"], reverse=True)
    selected: List[dict] = []
    for cand in raw_windows:
        if len(selected) >= top_k:
            break
        overlaps = False
        for ex in selected:
            inter = max(0.0, min(cand["end_s"], ex["end_s"]) - max(cand["start_s"], ex["start_s"]))
            union = max(0.01, (cand["end_s"] - cand["start_s"]) + (ex["end_s"] - ex["start_s"]) - inter)
            if (inter / union) >= 0.20:
                overlaps = True
                break
        if not overlaps:
            selected.append(cand)

    return selected


def find_peak_motion_start(
    clip_path: Path,
    window_dur: float,
    cfg: StudioConfig,
    intent: str = "action",
) -> Tuple[float, float]:
    total_dur, frames, cuts = extract_multimodal_signals(clip_path, cfg)
    cands = score_candidate_windows(total_dur, frames, cuts, window_dur, intent=intent, top_k=1)
    best = cands[0]
    return float(best["start_s"]), float(best["score"])


def find_peak_audio_start(
    clip_path: Path,
    window_dur: float,
    cfg: StudioConfig,
) -> Tuple[float, float]:
    total_dur = _probe_duration(clip_path, cfg)
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


def _build_action_triplet_grid(
    clip_path: Path,
    candidate_starts: List[float],
    window_dur: float,
    total_dur: float,
    out_jpg: Path,
    cfg: StudioConfig,
) -> Path:
    """Build an Action-Triplet Grid: each column = 1 candidate (#1..#K), 3 rows = T0 (start) -> T1 (mid) -> T2 (end).

    Lets Vision SEE actual temporal motion (e.g., leg lifting -> kicking dirt -> grass flying), not just 1 still frame!
    """
    tmp_dir = out_jpg.parent / f"_gm_tri_{clip_path.stem[:14]}_{abs(hash(out_jpg.name)) % 9999}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    col_imgs: List[Path] = []
    for ci, st in enumerate(candidate_starts):
        tri_ts = [
            min(max(0.0, total_dur - 0.1), st + 0.15),
            min(max(0.0, total_dur - 0.1), st + window_dur * 0.50),
            min(max(0.0, total_dur - 0.1), st + window_dur * 0.85),
        ]
        row_fps: List[Path] = []
        for ri, ts in enumerate(tri_ts):
            fp = tmp_dir / f"c{ci}_r{ri}.jpg"
            lbl = f"#{ci + 1} T{ri} {ts:.1f}s"
            subprocess.check_call(
                [
                    cfg.ffmpeg_cmd(),
                    "-y",
                    "-ss",
                    f"{ts:.3f}",
                    "-i",
                    str(clip_path),
                    "-frames:v",
                    "1",
                    "-vf",
                    f"scale=220:320:force_original_aspect_ratio=decrease,pad=220:320:(ow-iw)/2:(oh-ih)/2:black,"
                    f"drawtext=text='{lbl}':fontcolor=yellow:fontsize=18:x=5:y=5:box=1:boxcolor=black@0.85",
                    "-update",
                    "1",
                    "-q:v",
                    "5",
                    str(fp),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=20,
            )
            row_fps.append(fp)

        col_p = tmp_dir / f"col_{ci}.jpg"
        cmd_v = [cfg.ffmpeg_cmd(), "-y"]
        for fp in row_fps:
            cmd_v += ["-i", str(fp)]
        cmd_v += ["-filter_complex", "[0:v][1:v][2:v]vstack=inputs=3[c]", "-map", "[c]", "-frames:v", "1", "-update", "1", "-q:v", "5", str(col_p)]
        subprocess.check_call(cmd_v, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
        col_imgs.append(col_p)

    cmd_h = [cfg.ffmpeg_cmd(), "-y"]
    for cp in col_imgs:
        cmd_h += ["-i", str(cp)]
    if len(col_imgs) == 1:
        shutil.copyfile(col_imgs[0], out_jpg)
    else:
        fc = "".join(f"[{j}:v]" for j in range(len(col_imgs))) + f"hstack=inputs={len(col_imgs)}[o]"
        cmd_h += ["-filter_complex", fc, "-map", "[o]", "-frames:v", "1", "-update", "1", "-q:v", "5", str(out_jpg)]
        subprocess.check_call(cmd_h, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=25)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_jpg


def rerank_candidates_with_vision(
    clip_path: Path,
    candidates: List[dict],
    total_dur: float,
    window_dur: float,
    scene: dict,
    cfg: StudioConfig,
) -> Optional[dict]:
    """Stage 2 (v2.1): Action-Triplet Vision Reranker — sees 3-step motion progression (T0->T1->T2) per candidate."""
    try:
        import openai
    except ImportError:
        return None

    starts: List[float] = [float(c["start_s"]) for c in candidates[:4]]
    if total_dur > window_dur + 3.0 and len(starts) < 5:
        for frac in (0.30, 0.70):
            st_u = round(max(0.0, min(total_dur - window_dur - 0.1, total_dur * frac)), 2)
            if all(abs(st_u - ex) >= max(1.0, window_dur * 0.5) for ex in starts):
                starts.append(st_u)
                if len(starts) >= 5:
                    break
    if not starts:
        return None

    grid_jpg = clip_path.parent / f"_tmp_tri_sc{scene.get('index', 0)}_{clip_path.stem[:12]}.jpg"
    try:
        _build_action_triplet_grid(clip_path, starts, window_dur, total_dur, grid_jpg, cfg)
        b64 = base64.b64encode(grid_jpg.read_bytes()).decode("utf-8")
    except Exception:
        return None
    finally:
        grid_jpg.unlink(missing_ok=True)

    cand_desc = ", ".join(f"Kolom #{i + 1} (start={st:.2f}s..{st + window_dur:.2f}s)" for i, st in enumerate(starts))
    prompt = (
        "Kamu adalah Sutradara Visual YouTube Shorts. Gambar grid ini menampilkan KANDIDAT MOMEN per KOLOM (#1..#N).\n"
        "Setiap kolom berisi 3 frame berurutan dari atas ke bawah (T0 awal -> T1 tengah -> T2 akhir) sehingga kamu bisa MELIHAT GERAKAN NYATA.\n"
        f"Kandidat: {cand_desc}\n"
        f"Kebutuhan adegan:\n"
        f"- Visual deskripsi: {scene.get('visual_desc', '')}\n"
        f"- Narasi VO: {scene.get('vo', '')}\n\n"
        "Pilih 1 KOLOM TERBAIK (#1..#N) yang: (1) progresi gerak T0->T1->T2 paling cocok dengan aksi di naskah, "
        "(2) subjek tajam & tidak terpotong, (3) paling BERSIH dari teks/stiker TikTok.\n"
        "Jawab HANYA JSON valid:\n"
        '{"best_candidate_index": 1, "semantic_score": 8, "motion_verified": true, "cleanliness_score": 9, '
        '"allow_flip": true, "has_burned_text": false, "reason": "alasan singkat 1 kalimat tentang aksi yang terlihat"}'
    )

    try:
        client = openai.OpenAI(base_url=cfg.ai_base_url, api_key=cfg.ai_api_key or "local")
        res = client.chat.completions.create(
            model=cfg.ai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            temperature=0.1,
            timeout=35,
        )
        raw_txt = (res.choices[0].message.content or "").strip()
        m = re.search(r"\{.*\}", raw_txt, re.S)
        if not m:
            return None
        parsed = json.loads(m.group(0))
        idx = max(0, min(len(starts) - 1, int(parsed.get("best_candidate_index", 1)) - 1))
        return {
            "start_s": starts[idx],
            "semantic_score": int(parsed.get("semantic_score", 7)),
            "motion_verified": bool(parsed.get("motion_verified", True)),
            "cleanliness_score": int(parsed.get("cleanliness_score", 8)),
            "allow_flip": bool(parsed.get("allow_flip", True)) and not bool(parsed.get("has_burned_text", False)),
            "has_burned_text": bool(parsed.get("has_burned_text", False)),
            "reason": str(parsed.get("reason", "")).strip(),
        }
    except Exception:
        return None


def _scene_intent(scene: dict) -> Tuple[str, bool]:
    idx = int(scene.get("index", 1))
    blob = f"{scene.get('visual_desc', '')} {scene.get('vo', '')} {scene.get('sfx', '')}".lower()
    is_audio_hook = (idx == 1) and any(
        k in blob for k in ("ek-ek", "chattering", "audio asli", "suara asli", "bunyi asli")
    )
    is_calm = any(k in blob for k in ("cta", "subscribe", "lucu", "imut", "tenang", "tidur", "berhenti"))
    is_fast_action = any(
        k in blob
        for k in ("kabur", "lari", "cepat", "merayap", "menerkam", "melompat", "nendang", "tendang", "garuk", "dorong", "jatuh", "bergetar")
    )
    intent = "action" if is_fast_action else ("calm" if is_calm else "balanced")
    return intent, is_audio_hook


def _build_clip_overview_strip(clip_path: Path, total_dur: float, out_jpg: Path, cfg: StudioConfig) -> Path:
    """Extract 4 evenly-spaced frames (20/42/64/85%) into 1 compact strip to semantically index a clip once."""
    tmp_dir = out_jpg.parent / f"_gm_idx_{clip_path.stem[:14]}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fps_list: List[Path] = []
    for i, frac in enumerate((0.20, 0.42, 0.64, 0.85)):
        ts = max(0.0, min(max(0.0, total_dur - 0.1), total_dur * frac))
        fp = tmp_dir / f"o_{i}.jpg"
        subprocess.check_call(
            [
                cfg.ffmpeg_cmd(), "-y", "-ss", f"{ts:.2f}", "-i", str(clip_path), "-frames:v", "1",
                "-vf", f"scale=200:320:force_original_aspect_ratio=decrease,pad=200:320:(ow-iw)/2:(oh-ih)/2:black,"
                       f"drawtext=text='{ts:.1f}s':fontcolor=yellow:fontsize=18:x=4:y=4:box=1:boxcolor=black@0.8",
                "-update", "1", "-q:v", "5", str(fp),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
        )
        fps_list.append(fp)
    cmd = [cfg.ffmpeg_cmd(), "-y"]
    for fp in fps_list:
        cmd += ["-i", str(fp)]
    cmd += ["-filter_complex", "[0:v][1:v][2:v][3:v]hstack=inputs=4[o]", "-map", "[o]", "-frames:v", "1", "-update", "1", "-q:v", "5", str(out_jpg)]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_jpg


def build_clip_visual_index(
    assets_dir: Path,
    cfg: StudioConfig,
    force: bool = False,
) -> Dict[str, dict]:
    """Iterasi 2 (v2.2): Semantic index of EVERY clean video clip in assets/ (cached in _clip_visual_index.json).

    Enables Cross-Clip Rescue when a scene's pre-assigned clip has burned-in text or low semantic match!
    """
    idx_path = assets_dir / "_clip_visual_index.json"
    if idx_path.exists() and not force:
        try:
            cached = json.loads(idx_path.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached:
                return cached
        except Exception:
            pass

    try:
        import openai
    except ImportError:
        return {}

    vid_files = sorted(
        p for p in assets_dir.iterdir()
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov"} and not p.name.startswith(("_tmp", "thumb_"))
    )
    if not vid_files:
        return {}

    client = openai.OpenAI(base_url=cfg.ai_base_url, api_key=cfg.ai_api_key or "local")

    def _index_one(vp: Path) -> Tuple[str, dict]:
        dur = _probe_duration(vp, cfg)
        strip_p = assets_dir / f"_tmp_idx_{vp.stem[:14]}.jpg"
        try:
            _build_clip_overview_strip(vp, dur, strip_p, cfg)
            b64 = base64.b64encode(strip_p.read_bytes()).decode("utf-8")
            prompt = (
                "Deskripsikan secara padat klip hewan dari 4 frame berurutan ini untuk katalog pencarian visual.\n"
                "Jawab HANYA JSON valid:\n"
                '{"tags": ["kata kunci aksi/subjek/bagian tubuh dalam bahasa Indonesia & Inggris"], '
                '"summary": "ringkasan 1 kalimat aksi utama yang terjadi", '
                '"emotion": "action|cute|funny|calm|dramatic", '
                '"has_burned_text": false, "cleanliness": 9}'
            )
            res = client.chat.completions.create(
                model=cfg.ai_vision_model,
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ]}],
                temperature=0.1,
                timeout=35,
            )
            raw = (res.choices[0].message.content or "").strip()
            m = re.search(r"\{.*\}", raw, re.S)
            if m:
                d = json.loads(m.group(0))
                d["duration_s"] = round(dur, 2)
                return vp.name, d
        except Exception:
            pass
        finally:
            strip_p.unlink(missing_ok=True)
        return vp.name, {"tags": [], "summary": "", "emotion": "balanced", "has_burned_text": False, "cleanliness": 7, "duration_s": round(dur, 2)}

    index: Dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for name, meta in pool.map(_index_one, vid_files):
            index[name] = meta

    idx_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    return index


def pick_best_cross_clip_rescue(
    scene: dict,
    current_clip_name: str,
    visual_index: Dict[str, dict],
    assets_dir: Path,
    used_clip_counts: Dict[str, int],
) -> Optional[Path]:
    """Find a cleaner, semantically stronger alternate clip from assets/ if the current clip fails (<7/10 or burned text)."""
    query_tokens = set(re.findall(r"[a-z0-9]{3,}", f"{scene.get('visual_desc', '')} {scene.get('vo', '')}".lower()))
    best_p: Optional[Path] = None
    best_sc = -1.0
    for fname, meta in visual_index.items():
        if fname == current_clip_name:
            continue
        p = assets_dir / fname
        if not p.exists():
            continue
        if meta.get("has_burned_text", False) or int(meta.get("cleanliness", 7)) < 7:
            continue
        blob = f"{meta.get('summary', '')} {' '.join(meta.get('tags', []))}".lower()
        clip_tokens = set(re.findall(r"[a-z0-9]{3,}", blob))
        overlap = len(query_tokens & clip_tokens)
        sc = overlap * 10.0 + int(meta.get("cleanliness", 7)) - used_clip_counts.get(fname, 0) * 6.0
        if sc > best_sc and overlap >= 1:
            best_sc = sc
            best_p = p
    return best_p


def refine_action_onset(
    clip_path: Path,
    coarse_start_s: float,
    window_dur: float,
    total_dur: float,
    cuts: List[float],
    intent: str,
    cfg: StudioConfig,
) -> Tuple[float, dict]:
    """Iterasi 3 (v2.3): Coarse-to-Fine 4 fps (0.25s) local zoom-in refinement.

    - For `action`: detects exact acceleration spike d(YDIF)/dt (when physical action IGNITES!) and snaps
      start_s = onset_t - 0.18s (180ms visual anticipation lead before the kick/push/sprint).
    - For `calm`: locks onto the sharpest (min blur) + steadiest quarter-second sub-window.
    - Strictly clamped inside the current shot boundaries so refinement NEVER introduces a cut!
    """
    if total_dur <= window_dur + 0.5:
        return round(coarse_start_s, 2), {"refined": False, "shift_s": 0.0, "method": "clip_too_short"}

    # Determine enclosing shot boundaries around coarse_start_s
    shot_left = 0.0
    shot_right = total_dur
    for ct in cuts:
        if ct <= coarse_start_s + 0.15:
            shot_left = max(shot_left, ct + 0.05)
        elif ct > coarse_start_s + 0.15:
            shot_right = min(shot_right, ct - 0.05)
            break

    scan_st = max(shot_left, coarse_start_s - 0.90)
    scan_en = min(shot_right, coarse_start_s + window_dur + 0.90)
    if scan_en - scan_st < window_dur + 0.25:
        return round(coarse_start_s, 2), {"refined": False, "shift_s": 0.0, "method": "shot_bound_tight"}

    r = subprocess.run(
        [
            cfg.ffmpeg_cmd(), "-v", "error",
            "-ss", f"{scan_st:.3f}", "-i", str(clip_path), "-t", f"{scan_en - scan_st:.3f}",
            "-vf", "fps=4,scale=180:-2,signalstats,blurdetect,metadata=print:file=-",
            "-an", "-f", "null", "-",
        ],
        capture_output=True, text=True, timeout=25,
    )
    pts: List[dict] = []
    cur: Optional[dict] = None
    for line in r.stdout.splitlines():
        if "pts_time:" in line:
            if cur is not None:
                pts.append(cur)
            try:
                rel_t = float(line.split("pts_time:")[1].split()[0])
            except Exception:
                rel_t = len(pts) * 0.25
            cur = {"t": round(scan_st + rel_t, 3), "ydif": 0.0, "blur": 2.0}
        elif cur is not None:
            if "lavfi.signalstats.YDIF=" in line:
                try:
                    cur["ydif"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
            elif "lavfi.blur=" in line:
                try:
                    cur["blur"] = float(line.split("=", 1)[1].strip())
                except Exception:
                    pass
    if cur is not None:
        pts.append(cur)

    if len(pts) < 5:
        return round(coarse_start_s, 2), {"refined": False, "shift_s": 0.0, "method": "too_few_samples"}

    max_valid_start = min(shot_right - window_dur - 0.05, total_dur - window_dur - 0.05)
    min_valid_start = max(shot_left, coarse_start_s - 0.85)
    if max_valid_start <= min_valid_start:
        return round(coarse_start_s, 2), {"refined": False, "shift_s": 0.0, "method": "no_headroom"}

    if intent == "action":
        # Find peak positive acceleration d(YDIF)/dt (exact moment motion IGNITES, ignoring spikes > 32)
        best_onset_t = coarse_start_s + 0.20
        best_accel = -999.0
        for i in range(1, len(pts)):
            y_prev = pts[i - 1]["ydif"]
            y_curr = pts[i]["ydif"]
            if y_curr > 32.0:
                continue  # skip flash/whip spike
            accel = (y_curr - y_prev) + 0.35 * min(20.0, y_curr) - 1.5 * pts[i]["blur"]
            cand_st = pts[i]["t"] - 0.18  # 180ms visual anticipation before kick/push/sprint!
            if min_valid_start <= cand_st <= min(max_valid_start, coarse_start_s + 0.85):
                if accel > best_accel:
                    best_accel = accel
                    best_onset_t = pts[i]["t"]
        refined_st = max(min_valid_start, min(max_valid_start, best_onset_t - 0.18))
        method = "action_onset_dYDIF_4fps"
    else:
        # Calm/balanced: lock onto sharpest + steadiest quarter-second window near Vision anchor
        best_st = coarse_start_s
        best_q = -999.0
        for p in pts:
            st_c = p["t"]
            if not (min_valid_start <= st_c <= min(max_valid_start, coarse_start_s + 0.75)):
                continue
            sub = [x for x in pts if st_c <= x["t"] <= st_c + window_dur]
            if not sub:
                continue
            avg_b = sum(x["blur"] for x in sub) / len(sub)
            avg_y = sum(x["ydif"] for x in sub) / len(sub)
            q = (100.0 - avg_b * 15.0) - abs(avg_y - 4.0) * 3.0
            if q > best_q:
                best_q = q
                best_st = st_c
        refined_st = best_st
        method = "sharpness_lock_4fps"

    refined_st = round(refined_st, 2)
    shift = round(refined_st - coarse_start_s, 2)
    return refined_st, {"refined": abs(shift) >= 0.05, "coarse_start_s": round(coarse_start_s, 2), "shift_s": shift, "method": method}


def analyze_clip_golden_moment(
    clip_path: Path,
    scene: dict,
    cfg: StudioConfig,
    use_vision: bool = False,
    precomputed_signals: Optional[Tuple[float, List[dict], List[float]]] = None,
    excluded_windows: Optional[List[Tuple[float, float]]] = None,
    refine_onset: bool = True,
) -> dict:
    dur = max(1.5, float(scene.get("duration_s", 2.2)))
    idx = int(scene.get("index", 1))
    intent, is_audio_hook = _scene_intent(scene)

    total_dur, frames, cuts = precomputed_signals if precomputed_signals is not None else extract_multimodal_signals(clip_path, cfg)
    cands = score_candidate_windows(
        total_dur, frames, cuts, dur, intent=intent, top_k=4, excluded_windows=excluded_windows
    )
    best = cands[0]

    start_s = float(best["start_s"])
    score = float(best["score"])
    mode = f"v23_signal_{intent}"
    use_audio = False
    allow_flip = True
    vision_meta: Optional[dict] = None

    if is_audio_hook:
        a_start, a_rms = find_peak_audio_start(clip_path, dur, cfg)
        if a_rms > -32.0:
            start_s = a_start
            score = round(max(0.0, 100.0 + a_rms), 1)
            mode = "v23_audio_hook"
            use_audio = True

    if use_vision and total_dur > dur + 0.5:
        v_res = rerank_candidates_with_vision(clip_path, cands, total_dur, dur, scene, cfg)
        if v_res is not None:
            start_s = float(v_res["start_s"])
            allow_flip = bool(v_res["allow_flip"])
            mode = f"v23_triplet_{intent}"
            vision_meta = v_res

    # Stage 3 (v2.3): Coarse-to-Fine 0.25s Action-Onset / Sharpness Refinement
    refinement_meta: Optional[dict] = None
    if refine_onset and not use_audio and total_dur > dur + 0.5:
        start_s, refinement_meta = refine_action_onset(
            clip_path, start_s, dur, total_dur, cuts, intent, cfg
        )

    entry = {
        "scene_index": idx,
        "filename": clip_path.name,
        "start_s": round(start_s, 2),
        "mode": mode,
        "metric_score": score,
        "use_clip_audio": use_audio,
        "allow_flip": allow_flip,
        "signals": {
            "clip_dur_s": total_dur,
            "internal_cuts": len(cuts),
            "top_candidates": cands[:3],
        },
    }
    if vision_meta:
        entry["vision"] = vision_meta
    if refinement_meta:
        entry["refinement"] = refinement_meta
    return entry


def compute_scene_golden_moments(
    assets_dir: Path,
    scenes: List[dict],
    clip_mapping: Dict[int, Path],
    cfg: Optional[StudioConfig] = None,
    force_rescan: bool = False,
    use_vision: bool = False,
) -> Dict[int, dict]:
    """Golden Moment Engine v2.3:
    Stage 1: Parallel 4-signal FFmpeg + shot-cut penalty + cross-scene anti-repeat exclusion
    Stage 2: Parallel Action-Triplet Vision Reranker + Cross-Clip Rescue via _clip_visual_index.json
    Stage 3: 4 fps (0.25s) Action-Onset Acceleration (dYDIF/dt) / Sharpness Micro-Refinement.
    """
    studio_cfg = cfg or get_config()
    cache_p = assets_dir / "golden_moments.json"
    existing_by_idx: Dict[int, dict] = {}
    if cache_p.exists():
        try:
            raw = json.loads(cache_p.read_text(encoding="utf-8"))
            if raw and all((assets_dir / m["filename"]).exists() for m in raw):
                existing_by_idx = {int(m["scene_index"]): m for m in raw}
                if not force_rescan:
                    return existing_by_idx
        except Exception:
            pass

    to_scan_scenes: List[Tuple[dict, Path]] = []
    preserved: Dict[int, dict] = {}
    unique_clips: Dict[str, Path] = {}

    for sc in scenes:
        idx = int(sc["index"])
        prev = existing_by_idx.get(idx)
        if prev and str(prev.get("mode", "")).startswith("visual_verified_") and not force_rescan:
            preserved[idx] = prev
            continue
        clip_p = clip_mapping.get(idx)
        if clip_p and clip_p.exists():
            to_scan_scenes.append((sc, clip_p))
            unique_clips[clip_p.name] = clip_p

    # Stage 1: Parallel deduplicated FFmpeg signal extraction
    signal_cache: Dict[str, Tuple[float, List[dict], List[float]]] = {}
    if unique_clips:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {name: pool.submit(extract_multimodal_signals, p, studio_cfg) for name, p in unique_clips.items()}
            for name, fut in futs.items():
                signal_cache[name] = fut.result()

    # Build Cross-Scene Anti-Repeat batches: scenes on DISTINCT clips run in parallel;
    # second occurrences of the same clip run in Wave 2 with Wave 1's claimed windows excluded!
    computed: Dict[int, dict] = dict(preserved)
    claimed_windows: Dict[str, List[Tuple[float, float]]] = {}
    for m in preserved.values():
        claimed_windows.setdefault(m["filename"], []).append((float(m["start_s"]), float(m["start_s"]) + 2.5))

    wave1: List[Tuple[dict, Path]] = []
    wave2: List[Tuple[dict, Path]] = []
    seen_in_wave1: set[str] = set()
    for sc, cp in to_scan_scenes:
        if cp.name not in seen_in_wave1:
            wave1.append((sc, cp))
            seen_in_wave1.add(cp.name)
        else:
            wave2.append((sc, cp))

    def _run_wave(wave_items: List[Tuple[dict, Path]]) -> None:
        if not wave_items:
            return
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {
                int(sc["index"]): (
                    sc,
                    pool.submit(
                        analyze_clip_golden_moment,
                        cp,
                        sc,
                        studio_cfg,
                        use_vision,
                        signal_cache.get(cp.name),
                        list(claimed_windows.get(cp.name, [])),
                        True,
                    ),
                )
                for sc, cp in wave_items
            }
            for idx, (sc, fut) in futs.items():
                res = fut.result()
                computed[idx] = res
                dur_w = max(1.8, float(sc.get("duration_s", 2.2)))
                claimed_windows.setdefault(res["filename"], []).append((res["start_s"], res["start_s"] + dur_w))

    _run_wave(wave1)
    _run_wave(wave2)

    # Iterasi 2 Cross-Clip Rescue: if any Vision-evaluated scene scored < 7/10 OR has burned-in text,
    # build/load _clip_visual_index.json and automatically rescue with a cleaner, higher-match clip!
    if use_vision:
        weak_scenes = [
            sc for sc, _ in to_scan_scenes
            if (
                int(sc["index"]) in computed
                and computed[int(sc["index"])].get("vision")
                and (
                    int(computed[int(sc["index"])]["vision"].get("semantic_score", 8)) < 7
                    or bool(computed[int(sc["index"])]["vision"].get("has_burned_text", False))
                )
            )
        ]
        if weak_scenes:
            v_index = build_clip_visual_index(assets_dir, studio_cfg, force=False)
            used_counts: Dict[str, int] = {}
            for m in computed.values():
                used_counts[m["filename"]] = used_counts.get(m["filename"], 0) + 1
            for sc in weak_scenes:
                idx = int(sc["index"])
                cur_m = computed[idx]
                alt_p = pick_best_cross_clip_rescue(sc, cur_m["filename"], v_index, assets_dir, used_counts)
                if alt_p is not None:
                    alt_sig = signal_cache.get(alt_p.name) or extract_multimodal_signals(alt_p, studio_cfg)
                    signal_cache[alt_p.name] = alt_sig
                    rescued = analyze_clip_golden_moment(
                        alt_p, sc, studio_cfg, use_vision=True,
                        precomputed_signals=alt_sig,
                        excluded_windows=list(claimed_windows.get(alt_p.name, [])),
                        refine_onset=True,
                    )
                    rescued_vis = rescued.get("vision") or {}
                    old_vis = cur_m.get("vision") or {}
                    if int(rescued_vis.get("semantic_score", 0)) >= int(old_vis.get("semantic_score", 0)) and not rescued_vis.get("has_burned_text", False):
                        rescued["mode"] = "v23_cross_clip_rescue"
                        rescued["rescued_from"] = cur_m["filename"]
                        computed[idx] = rescued
                        used_counts[alt_p.name] = used_counts.get(alt_p.name, 0) + 1

    moments_list = [computed[idx] for idx in sorted(computed)]
    cache_p.write_text(json.dumps(moments_list, indent=2, ensure_ascii=False), encoding="utf-8")
    return computed
