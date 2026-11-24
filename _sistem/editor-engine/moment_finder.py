"""Golden Moment Engine v2.4 — Shot-Aware Semantic Indexing, Synonym/LLM Matcher, Subject Spatial Focus (crop_x),
Seam & Caption-Zone Gate (HyperFrames-inspired), Peak-to-Word Onset Lock, and Script-Aware Sub-Clip #2 Retrieval."""

from __future__ import annotations

import base64
import difflib
import json
import re
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import StudioConfig, get_config, resolve_ai_model

# Bilingual (ID + EN) animal behavior & anatomy concept clusters for synonym-aware semantic matching
CONCEPT_SYNONYM_CLUSTERS: List[Set[str]] = [
    {"mata", "pupil", "melebar", "membesar", "fokus", "tatap", "menatap", "melotot", "eye", "eyes", "pupil", "stare", "gaze", "blink", "kedip", "melek"},
    {"mulut", "rahang", "gigi", "lidah", "ek", "chatter", "chattering", "mengoceh", "bunyi", "suara", "mengeong", "meow", "jaw", "mouth", "teeth", "bite", "gigit"},
    {"kaki", "telapak", "bantalan", "cakar", "jari", "paw", "paws", "foot", "feet", "claw", "claws", "pad", "pads"},
    {"tendang", "nendang", "menendang", "garuk", "menggaruk", "cakar", "gali", "kick", "kicking", "scratch", "scratching", "dig", "tanah", "rumput", "dirt", "grass", "soil"},
    {"dorong", "mendorong", "jatuh", "menjatuhkan", "senggol", "tepis", "gelas", "cangkir", "botol", "barang", "meja", "pinggir", "push", "knock", "drop", "glass", "cup", "table", "edge"},
    {"endus", "mengendus", "cium", "mencium", "hidung", "bau", "aroma", "feromon", "kelenjar", "sniff", "sniffing", "smell", "nose", "scent"},
    {"lari", "berlari", "kabur", "cepat", "sprint", "kejar", "mengejar", "lompat", "melompat", "menerkam", "sergap", "run", "running", "chase", "jump", "pounce", "dash"},
    {"cicak", "tokek", "kadal", "mangsa", "serangga", "burung", "gecko", "lizard", "prey", "bird", "bug"},
    {"kucing", "anak", "kitten", "cat", "cats", "feline"},
    {"anjing", "anak", "puppy", "dog", "dogs", "canine"},
    {"bebek", "itik", "angsa", "berbaris", "baris", "koloni", "duck", "duckling", "geese", "row", "line"},
    {"ayam", "unggas", "kepala", "leher", "stabil", "goyang", "chicken", "rooster", "head", "neck", "stable"},
    {"tidur", "istirahat", "rebah", "santai", "tenang", "diam", "sleep", "sleeping", "nap", "rest", "calm"},
    {"lucu", "imut", "gemas", "senyum", "tersenyum", "cute", "adorable", "smile", "funny"},
]


def split_clip_into_shots(
    total_dur: float,
    cuts: List[float],
    min_shot_dur: float = 0.65,
) -> List[Dict[str, float]]:
    """Partition a raw video clip into atomic shots using detected internal cut timestamps."""
    if total_dur <= 0.0:
        return []
    bounds = [0.0] + [float(c) for c in sorted(cuts) if 0.25 < float(c) < (total_dur - 0.25)] + [float(total_dur)]
    shots: List[Dict[str, float]] = []
    for i in range(len(bounds) - 1):
        st = round(bounds[i], 3)
        en = round(bounds[i + 1], 3)
        dur = round(en - st, 3)
        if dur >= min_shot_dur or len(bounds) == 2:
            shots.append(
                {
                    "shot_id": len(shots) + 1,
                    "start_s": st,
                    "end_s": en,
                    "duration_s": dur,
                    "mid_s": round((st + en) / 2.0, 3),
                }
            )
    if not shots:
        shots.append({"shot_id": 1, "start_s": 0.0, "end_s": round(total_dur, 3), "duration_s": round(total_dur, 3), "mid_s": round(total_dur / 2.0, 3)})
    return shots


def expand_semantic_concepts(tokens: Set[str]) -> Set[str]:
    """Expand raw word tokens with bilingual animal/action concept synonyms and stems."""
    expanded = set(tokens)
    # Strip common Indonesian prefixes/suffixes (me-, meng-, ber-, -nya, -kan, -an)
    stems: Set[str] = set()
    for t in tokens:
        stems.add(t)
        for suf in ("nya", "kan", "an", "in"):
            if len(t) > len(suf) + 3 and t.endswith(suf):
                stems.add(t[: -len(suf)])
        for pre in ("meng", "meny", "men", "mem", "me", "ber", "ter", "di"):
            if len(t) > len(pre) + 3 and t.startswith(pre):
                stems.add(t[len(pre) :])
    expanded.update(stems)

    for cluster in CONCEPT_SYNONYM_CLUSTERS:
        if expanded & cluster:
            expanded.update(cluster)
    return expanded


def compute_semantic_match_score(query_text: str, candidate_text: str) -> float:
    """Compute a 0.0..100.0 semantic similarity score between Indonesian script text and clip/shot metadata.

    Combines exact token overlap, bilingual concept-cluster synonym expansion, and fuzzy stem matching
    so 'pupil melebar' matches 'mata kucing membesar' without requiring exact word identity.
    """
    q_raw = set(re.findall(r"[a-z0-9]{3,}", (query_text or "").lower()))
    c_raw = set(re.findall(r"[a-z0-9]{3,}", (candidate_text or "").lower()))
    stop = {"yang", "dan", "ini", "itu", "dari", "untuk", "pada", "dengan", "saat", "karena", "bisa", "lagi", "tidak", "bukan", "tapi", "atau", "the", "and", "with", "from"}
    q_toks = q_raw - stop
    c_toks = c_raw - stop
    if not q_toks or not c_toks:
        return 0.0

    # 1. Direct lexical overlap
    direct_hits = len(q_toks & c_toks)

    # 2. Concept cluster synonym overlap (counts every query/candidate token bridged by synonym expansion)
    q_exp = expand_semantic_concepts(q_toks)
    c_exp = expand_semantic_concepts(c_toks)
    bridged_q = len((q_toks - c_toks) & c_exp)
    bridged_c = len((c_toks - q_toks) & q_exp)
    concept_hits = bridged_q + bridged_c

    # 3. Fuzzy stem similarity for near-synonyms / morphological variants
    fuzzy_hits = 0.0
    for qt in q_toks - c_exp:
        best_r = max((difflib.SequenceMatcher(None, qt, ct).ratio() for ct in c_toks), default=0.0)
        if best_r >= 0.78:
            fuzzy_hits += best_r

    raw_pts = direct_hits * 16.0 + concept_hits * 11.0 + fuzzy_hits * 10.0
    return round(min(100.0, raw_pts), 2)


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
    shot_index: Optional[List[dict]] = None,
    scene_query_text: str = "",
) -> List[dict]:
    """v2.4: 4-signal fusion + Relative Sharpness + Shot-Semantic Bonus + HyperFrames Seam/Frame-0 Gate
    + Cross-Scene Anti-Repeat Exclusion + Temporal NMS (IoU < 0.20).
    """
    if total_dur <= window_dur + 0.35 or not frames:
        return [
            {
                "start_s": 0.0,
                "end_s": round(min(total_dur, window_dur), 2),
                "score": 50.0,
                "motion": 0.0,
                "sharpness": 50.0,
                "exposure": 50.0,
                "semantic_bonus": 0.0,
                "crosses_cut": False,
                "frame0_clean": True,
                "seam_clean": True,
            }
        ]

    excl = excluded_windows or []
    shots = shot_index or []
    max_start = max(0.0, total_dur - window_dur - 0.08)
    raw_windows: List[dict] = []

    # Clip-relative blur baseline so macro/soft clips still differentiate their sharpest window
    all_blurs = [float(f.get("blur", 2.0)) for f in frames]
    min_clip_blur = min(all_blurs) if all_blurs else 1.5
    max_clip_blur = max(min_clip_blur + 1.0, max(all_blurs) if all_blurs else 6.0)

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

        internal_cuts = [ct for ct in cuts if (st + 0.20) < ct < (en - 0.20)]
        crosses_cut = len(internal_cuts) > 0

        # HyperFrames-inspired Seam Audit (±0.20s around entry/exit cuts) & Frame-0 Empty/Dark Gate
        seam_near_cut = any(abs(ct - st) <= 0.20 or abs(ct - en) <= 0.20 for ct in cuts)
        seam_clean = (not crosses_cut) and (not seam_near_cut)
        f0 = win_f[0]
        f0_y = float(f0.get("yavg", 128.0))
        f0_b = float(f0.get("blur", 2.0))
        frame0_clean = (38.0 <= f0_y <= 238.0) and (f0_b <= max(6.8, min_clip_blur + 3.2))

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
        abs_sharp = max(0.0, min(100.0, 105.0 - avg_blur * 14.0))
        rel_sharp = max(0.0, min(100.0, 100.0 * (1.0 - (avg_blur - min_clip_blur) / max(1.0, max_clip_blur - min_clip_blur))))
        sharp_sc = round(0.65 * abs_sharp + 0.35 * rel_sharp, 1)

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

        # Per-Shot Semantic Bonus when shot_index and scene_query_text are provided
        sem_bonus = 0.0
        if shots and scene_query_text:
            win_mid = (st + en) / 2.0
            for sh in shots:
                sh_st = float(sh.get("start_s", 0.0))
                sh_en = float(sh.get("end_s", total_dur))
                if sh_st - 0.15 <= win_mid <= sh_en + 0.15:
                    sh_blob = f"{sh.get('summary', '')} {' '.join(sh.get('tags', []))}"
                    sem_sc = compute_semantic_match_score(scene_query_text, sh_blob)
                    sem_bonus = round(min(22.0, sem_sc * 0.28), 2)
                    break
        total_sc += sem_bonus

        if crosses_cut:
            total_sc -= 30.0
        elif seam_near_cut:
            total_sc -= 12.0
        if not frame0_clean:
            total_sc -= 16.0
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
                "semantic_bonus": sem_bonus,
                "crosses_cut": crosses_cut,
                "overlaps_claimed": overlaps_claimed,
                "frame0_clean": frame0_clean,
                "seam_clean": seam_clean,
            }
        )

    if not raw_windows:
        return [
            {
                "start_s": 0.0,
                "end_s": round(window_dur, 2),
                "score": 50.0,
                "motion": 50.0,
                "sharpness": 50.0,
                "exposure": 50.0,
                "semantic_bonus": 0.0,
                "crosses_cut": False,
                "frame0_clean": True,
                "seam_clean": True,
            }
        ]

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


def detect_horizontal_subject_focus(
    clip_path: Path,
    start_s: float,
    window_dur: float,
    cfg: StudioConfig,
) -> float:
    """Fast 3-zone (Left / Center / Right) horizontal motion saliency probe -> crop_x_norm in [0.22, 0.78].

    Returns 0.50 (dead center) when motion is balanced or clip is too short/unreadable.
    """
    probe_dur = max(0.8, min(2.2, window_dur))
    try:
        r = subprocess.run(
            [
                cfg.ffmpeg_cmd(),
                "-v",
                "error",
                "-ss",
                f"{max(0.0, start_s):.3f}",
                "-t",
                f"{probe_dur:.3f}",
                "-i",
                str(clip_path),
                "-vf",
                (
                    "fps=3,scale=180:180,split=3[L][C][R];"
                    "[L]crop=60:180:0:0,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-[Lo];"
                    "[C]crop=60:180:60:0,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-[Co];"
                    "[R]crop=60:180:120:0,signalstats,metadata=print:key=lavfi.signalstats.YDIF:file=-[Ro];"
                    "[Lo][Co][Ro]hstack=inputs=3"
                ),
                "-an",
                "-f",
                "null",
                "-",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        vals: List[float] = []
        for line in r.stdout.splitlines():
            if "YDIF=" in line:
                try:
                    vals.append(float(line.split("=", 1)[1].strip()))
                except Exception:
                    pass
        if len(vals) >= 3:
            l_vals = vals[0::3]
            c_vals = vals[1::3]
            r_vals = vals[2::3]
            w_l = sum(l_vals) / max(1, len(l_vals)) + 1.0
            w_c = (sum(c_vals) / max(1, len(c_vals)) + 1.5) * 1.15  # mild center prior
            w_r = sum(r_vals) / max(1, len(r_vals)) + 1.0
            tot = w_l + w_c + w_r
            if tot > 0:
                centroid = (0.20 * w_l + 0.50 * w_c + 0.80 * w_r) / tot
                return round(max(0.22, min(0.78, centroid)), 2)
    except Exception:
        pass
    return 0.50


def _extract_scene_emphasis_offset(scene: dict) -> Optional[float]:
    """Find relative timestamp offset (in seconds from scene start) of the primary emphasis/trigger word."""
    words = scene.get("words") or []
    if not words:
        return None
    sc_start = float(scene.get("start_s", 0.0))
    sc_dur = max(0.8, float(scene.get("duration_s", 2.0)))

    # Priority 1: ALL-CAPS highlight word in vo_raw/vo or explicit action verb
    raw_vo = str(scene.get("vo_raw", "") or scene.get("vo", ""))
    caps_tokens = {re.sub(r"[^\w]", "", w).lower() for w in re.findall(r"\b[A-Z]{3,}\b", raw_vo)}
    action_verbs = {"tendang", "nendang", "dorong", "jatuh", "lompat", "lari", "kabur", "ek", "melek", "stabil", "kaget"}

    for w in words:
        tok = re.sub(r"[^\w]", "", str(w.get("word", ""))).lower()
        rel_t = float(w.get("start", sc_start)) - sc_start
        if 0.12 <= rel_t <= sc_dur * 0.78 and (tok in caps_tokens or tok in action_verbs):
            return round(rel_t, 3)
    return None


def _call_vision_json(client: object, cfg: StudioConfig, prompt: str, b64: str) -> Optional[dict]:
    """Call multimodal Vision endpoint with automatic provider failover and list-content parsing."""
    live_cfg = get_config()
    resolved = resolve_ai_model("vision", live_cfg)
    models_to_try: List[str] = []
    for m in (resolved, live_cfg.ai_vision_model, "mistral/deepseek-v4-1-flash-1017", "claude", "mistral/mistral-large-latest"):
        if m and m not in models_to_try:
            models_to_try.append(m)

    for model_name in models_to_try:
        try:
            res = client.chat.completions.create(  # type: ignore[attr-defined]
                model=model_name,
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
            content = res.choices[0].message.content
            if isinstance(content, list):
                text_parts = [
                    str(blk.get("text", ""))
                    for blk in content
                    if isinstance(blk, dict) and blk.get("type") == "text"
                ]
                raw_txt = "\n".join(text_parts).strip()
            else:
                raw_txt = str(content or "").strip()

            m = re.search(r"\{.*\}", raw_txt, re.S)
            if m:
                return json.loads(m.group(0))
        except Exception:
            continue
    return None


def rerank_candidates_with_vision(
    clip_path: Path,
    candidates: List[dict],
    total_dur: float,
    window_dur: float,
    scene: dict,
    cfg: StudioConfig,
) -> Optional[dict]:
    """Stage 2 (v2.4): Action-Triplet Vision Reranker with Caption-Zone Gate, Subject Focus (crop_x), and Auto-Retry."""
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
        "(2) subjek tajam & tidak terpotong, (3) paling BERSIH dari teks/stiker TikTok terutama di ZONA CAPTION BAWAH (25% bawah layar).\n"
        "Tentukan juga posisi horizontal utama subjek `subject_x_norm` (0.25=kiri, 0.50=tengah, 0.75=kanan).\n"
        "Jawab HANYA JSON valid:\n"
        '{"best_candidate_index": 1, "semantic_score": 8, "motion_verified": true, "cleanliness_score": 9, '
        '"allow_flip": true, "has_burned_text": false, "caption_zone_clean": true, "subject_x_norm": 0.50, '
        '"reason": "alasan singkat 1 kalimat tentang aksi yang terlihat"}'
    )

    live_cfg = get_config()
    client = openai.OpenAI(base_url=live_cfg.ai_base_url, api_key=live_cfg.ai_api_key or "local")
    parsed = _call_vision_json(client, live_cfg, prompt, b64)
    if not parsed:
        return None
    idx = max(0, min(len(starts) - 1, int(parsed.get("best_candidate_index", 1)) - 1))
    has_burned = bool(parsed.get("has_burned_text", False))
    cap_clean = bool(parsed.get("caption_zone_clean", not has_burned))
    subj_x = float(parsed.get("subject_x_norm", 0.50) or 0.50)
    subj_x = round(max(0.20, min(0.80, subj_x)), 2)
    return {
        "start_s": starts[idx],
        "semantic_score": int(parsed.get("semantic_score", 7)),
        "motion_verified": bool(parsed.get("motion_verified", True)),
        "cleanliness_score": int(parsed.get("cleanliness_score", 8)),
        "allow_flip": bool(parsed.get("allow_flip", True)) and not has_burned,
        "has_burned_text": has_burned,
        "caption_zone_clean": cap_clean,
        "subject_x_norm": subj_x,
        "reason": str(parsed.get("reason", "")).strip(),
    }


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


def _build_clip_overview_strip(
    clip_path: Path,
    total_dur: float,
    out_jpg: Path,
    cfg: StudioConfig,
    shots: Optional[List[Dict[str, float]]] = None,
) -> Tuple[Path, List[Dict[str, float]]]:
    """Extract up to 4 shot-aware midpoint frames into 1 compact strip to semantically index a clip per-shot."""
    tmp_dir = out_jpg.parent / f"_gm_idx_{clip_path.stem[:14]}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    shot_list = shots or split_clip_into_shots(total_dur, [])
    if len(shot_list) >= 2:
        sampled_shots = shot_list[:4]
    else:
        # Single continuous shot: divide into 4 virtual sub-segments for fine-grained indexing
        seg_d = max(0.5, total_dur / 4.0)
        sampled_shots = [
            {
                "shot_id": i + 1,
                "start_s": round(i * seg_d, 2),
                "end_s": round(min(total_dur, (i + 1) * seg_d), 2),
                "duration_s": round(seg_d, 2),
                "mid_s": round(min(max(0.0, total_dur - 0.1), (i + 0.5) * seg_d), 2),
            }
            for i in range(4)
        ]

    fps_list: List[Path] = []
    for i, sh in enumerate(sampled_shots):
        ts = max(0.0, min(max(0.0, total_dur - 0.1), float(sh.get("mid_s", total_dur * 0.5))))
        fp = tmp_dir / f"o_{i}.jpg"
        lbl = f"S{i + 1} {ts:.1f}s"
        subprocess.check_call(
            [
                cfg.ffmpeg_cmd(), "-y", "-ss", f"{ts:.2f}", "-i", str(clip_path), "-frames:v", "1",
                "-vf", f"scale=200:320:force_original_aspect_ratio=decrease,pad=200:320:(ow-iw)/2:(oh-ih)/2:black,"
                       f"drawtext=text='{lbl}':fontcolor=yellow:fontsize=18:x=4:y=4:box=1:boxcolor=black@0.85",
                "-update", "1", "-q:v", "5", str(fp),
            ],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20,
        )
        fps_list.append(fp)
    cmd = [cfg.ffmpeg_cmd(), "-y"]
    for fp in fps_list:
        cmd += ["-i", str(fp)]
    if len(fps_list) == 1:
        shutil.copyfile(fps_list[0], out_jpg)
    else:
        fc = "".join(f"[{j}:v]" for j in range(len(fps_list))) + f"hstack=inputs={len(fps_list)}[o]"
        cmd += ["-filter_complex", fc, "-map", "[o]", "-frames:v", "1", "-update", "1", "-q:v", "5", str(out_jpg)]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_jpg, sampled_shots


def build_clip_visual_index(
    assets_dir: Path,
    cfg: StudioConfig,
    force: bool = False,
    signal_cache: Optional[Dict[str, Tuple[float, List[dict], List[float]]]] = None,
) -> Dict[str, dict]:
    """v2.4: Per-Shot Semantic Index of EVERY clean video clip in assets/ (cached in _clip_visual_index.json).

    Indexes both clip-level metadata and per-shot intervals (`shots`) + `caption_zone_clean` + `subject_x_norm`.
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

    live_cfg = get_config()
    client = openai.OpenAI(base_url=live_cfg.ai_base_url, api_key=live_cfg.ai_api_key or "local")
    sig_map = signal_cache or {}

    def _index_one(vp: Path) -> Tuple[str, dict]:
        if vp.name in sig_map:
            dur, _, cuts = sig_map[vp.name]
        else:
            dur = _probe_duration(vp, cfg)
            cuts = []
        raw_shots = split_clip_into_shots(dur, cuts)
        strip_p = assets_dir / f"_tmp_idx_{vp.stem[:14]}.jpg"
        try:
            _, sampled_shots = _build_clip_overview_strip(vp, dur, strip_p, cfg, shots=raw_shots)
            b64 = base64.b64encode(strip_p.read_bytes()).decode("utf-8")
            shot_labels = ", ".join(f"S{i + 1} ({s['start_s']:.1f}-{s['end_s']:.1f}s)" for i, s in enumerate(sampled_shots))
            prompt = (
                f"Deskripsikan secara padat klip hewan dari panel {shot_labels} ini untuk katalog pencarian visual per-shot.\n"
                "Jawab HANYA JSON valid:\n"
                '{"tags": ["kata kunci aksi/subjek/bagian tubuh dalam bahasa Indonesia & Inggris"], '
                '"summary": "ringkasan 1 kalimat aksi utama yang terjadi", '
                '"emotion": "action|cute|funny|calm|dramatic", '
                '"has_burned_text": false, "caption_zone_clean": true, "cleanliness": 9, "subject_x_norm": 0.50, '
                '"shots": [{"shot_id": 1, "summary": "aksi spesifik di panel S1", "tags": ["tag1", "tag2"]}]}'
            )
            d = _call_vision_json(client, cfg, prompt, b64)
            if d:
                d["duration_s"] = round(dur, 2)
                # Merge start_s / end_s into shots array
                parsed_shots = d.get("shots") if isinstance(d.get("shots"), list) else []
                merged_shots: List[dict] = []
                for idx_s, sh_base in enumerate(sampled_shots):
                    p_sh = parsed_shots[idx_s] if idx_s < len(parsed_shots) and isinstance(parsed_shots[idx_s], dict) else {}
                    merged_shots.append(
                        {
                            "shot_id": idx_s + 1,
                            "start_s": sh_base["start_s"],
                            "end_s": sh_base["end_s"],
                            "summary": str(p_sh.get("summary") or d.get("summary") or ""),
                            "tags": list(p_sh.get("tags") or d.get("tags") or []),
                        }
                    )
                d["shots"] = merged_shots
                return vp.name, d
        except Exception:
            pass
        finally:
            strip_p.unlink(missing_ok=True)
        return vp.name, {
            "tags": [],
            "summary": "",
            "emotion": "balanced",
            "has_burned_text": False,
            "caption_zone_clean": True,
            "cleanliness": 7,
            "subject_x_norm": 0.50,
            "duration_s": round(dur, 2),
            "shots": raw_shots,
        }

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
    """v2.4: Synonym-aware + Per-Shot semantic rescue when current clip has burned-in text or weak semantic match."""
    query_text = f"{scene.get('visual_desc', '')} {scene.get('vo', '')}".strip()
    best_p: Optional[Path] = None
    best_sc = -1.0
    for fname, meta in visual_index.items():
        if fname == current_clip_name:
            continue
        p = assets_dir / fname
        if not p.exists():
            continue
        if meta.get("has_burned_text", False) or not meta.get("caption_zone_clean", True) or int(meta.get("cleanliness", 7)) < 7:
            continue

        clip_blob = f"{meta.get('summary', '')} {' '.join(meta.get('tags', []))}"
        sem_score = compute_semantic_match_score(query_text, clip_blob)

        # Also check per-shot summaries/tags so a multi-shot clip with 1 matching shot is rescued accurately
        for sh in meta.get("shots") or []:
            if isinstance(sh, dict):
                sh_blob = f"{sh.get('summary', '')} {' '.join(sh.get('tags', []))}"
                sem_score = max(sem_score, compute_semantic_match_score(query_text, sh_blob))

        if sem_score <= 0.0:
            continue

        sc = sem_score + int(meta.get("cleanliness", 7)) * 1.2 - used_clip_counts.get(fname, 0) * 8.0
        if sc > best_sc:
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
    target_emphasis_offset_s: Optional[float] = None,
) -> Tuple[float, dict]:
    """Stage 3 (v2.4): Coarse-to-Fine 4 fps (0.25s) local zoom-in refinement + Peak-to-Word Onset Lock.

    - For `action`: detects exact acceleration spike d(YDIF)/dt (when physical action IGNITES!) and aligns it
      either to `target_emphasis_offset_s` (spoken keyword onset lock!) or `-0.18s` anticipation lead.
    - For `calm`/`balanced`: locks onto the sharpest (min blur) + steadiest quarter-second sub-window,
      avoiding dark/blurry Frame-0 seams.
    - Strictly clamped inside the current shot boundaries so refinement NEVER introduces a cut!
    """
    if total_dur <= window_dur + 0.5:
        return round(coarse_start_s, 2), {"refined": False, "shift_s": 0.0, "method": "clip_too_short"}

    # Determine enclosing shot boundaries around coarse_start_s (with 0.15s seam guard)
    shot_left = 0.0
    shot_right = total_dur
    for ct in cuts:
        if ct <= coarse_start_s + 0.15:
            shot_left = max(shot_left, ct + 0.12)
        elif ct > coarse_start_s + 0.15:
            shot_right = min(shot_right, ct - 0.12)
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
            cur = {"t": round(scan_st + rel_t, 3), "ydif": 0.0, "yavg": 128.0, "blur": 2.0}
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

    lead_s = 0.18
    method = "action_onset_dYDIF_4fps"
    if target_emphasis_offset_s is not None and 0.15 <= target_emphasis_offset_s <= min(1.25, window_dur * 0.65):
        lead_s = float(target_emphasis_offset_s)
        method = "peak_to_word_onset_4fps"

    if intent == "action":
        # Find peak positive acceleration d(YDIF)/dt (exact moment motion IGNITES, ignoring spikes > 32)
        best_onset_t = coarse_start_s + lead_s
        best_accel = -999.0
        for i in range(1, len(pts)):
            y_prev = pts[i - 1]["ydif"]
            y_curr = pts[i]["ydif"]
            if y_curr > 32.0:
                continue  # skip flash/whip spike
            accel = (y_curr - y_prev) + 0.35 * min(20.0, y_curr) - 1.5 * pts[i]["blur"]
            cand_st = pts[i]["t"] - lead_s
            if min_valid_start <= cand_st <= min(max_valid_start, coarse_start_s + 0.85):
                if accel > best_accel:
                    best_accel = accel
                    best_onset_t = pts[i]["t"]
        refined_st = max(min_valid_start, min(max_valid_start, best_onset_t - lead_s))
    else:
        # Calm/balanced: lock onto sharpest + steadiest quarter-second window near Vision anchor with clean Frame-0
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
            f0_ok = 38.0 <= float(sub[0].get("yavg", 128.0)) <= 238.0
            q = (100.0 - avg_b * 15.0) - abs(avg_y - 4.0) * 3.0 + (6.0 if f0_ok else -12.0)
            if q > best_q:
                best_q = q
                best_st = st_c
        refined_st = best_st
        method = "sharpness_lock_4fps"

    refined_st = round(refined_st, 2)
    shift = round(refined_st - coarse_start_s, 2)
    return refined_st, {
        "refined": abs(shift) >= 0.05,
        "coarse_start_s": round(coarse_start_s, 2),
        "shift_s": shift,
        "lead_s": round(lead_s, 2),
        "method": method,
    }


def select_subclip2_golden_moment(
    subclip2_scene: dict,
    parent_gm: Optional[dict],
    assets_dir: Path,
    default_clip_path: Path,
    clips: List[Path],
    visual_index: Optional[Dict[str, dict]] = None,
    used_counts: Optional[Dict[str, int]] = None,
) -> dict:
    """v2.4 Script-Aware Intra-Unit Sub-Clip #2 Selector:
    Instead of blindly shifting +2.2s, reads sub-clip #2's VO text (`subclip2_scene['vo']`) and intent,
    then picks the highest-matching non-overlapping candidate window (or alternate shot/clip).
    """
    parent_st = float(parent_gm.get("start_s", 0.0)) if parent_gm else 0.0
    base_crop_x = float(parent_gm.get("crop_x_norm", 0.50)) if parent_gm else 0.50
    sub_intent, _ = _scene_intent(subclip2_scene)
    sub_query = f"{subclip2_scene.get('vo', '')} {subclip2_scene.get('split_trigger_word', '')} {subclip2_scene.get('visual_desc', '')}".strip()
    v_idx = visual_index or {}
    u_cnt = used_counts or {}

    # 1. Check if parent clip has per-shot metadata or multiple non-overlapping candidates
    cands = (parent_gm.get("signals", {}).get("top_candidates") or []) if parent_gm else []
    clip_dur = float(parent_gm.get("signals", {}).get("clip_dur_s", 0.0)) if parent_gm else 0.0
    sub_dur = max(0.85, float(subclip2_scene.get("duration_s", 1.2)))
    clip_meta = v_idx.get(default_clip_path.name, {})
    shots = clip_meta.get("shots") if isinstance(clip_meta.get("shots"), list) else []

    best_st: Optional[float] = None
    best_sc = -999.0
    for cand in cands:
        c_st = float(cand.get("start_s", 0.0))
        if abs(c_st - parent_st) < 1.35:
            continue
        # Score candidate against sub-clip #2's specific intent (e.g. if clause after 'tapi' shifts to action!)
        if sub_intent == "action":
            c_sc = float(cand.get("motion", 50.0)) * 0.55 + float(cand.get("sharpness", 50.0)) * 0.25 + float(cand.get("score", 50.0)) * 0.20
        elif sub_intent == "calm":
            c_sc = float(cand.get("sharpness", 50.0)) * 0.55 + float(cand.get("exposure", 50.0)) * 0.25 + float(cand.get("score", 50.0)) * 0.20
        else:
            c_sc = float(cand.get("score", 50.0))

        # Per-shot semantic match bonus against sub-clip #2's specific spoken words
        for sh in shots:
            if isinstance(sh, dict) and float(sh.get("start_s", 0.0)) - 0.2 <= c_st <= float(sh.get("end_s", 99.0)):
                sh_blob = f"{sh.get('summary', '')} {' '.join(sh.get('tags', []))}"
                c_sc += compute_semantic_match_score(sub_query, sh_blob) * 0.35
                break

        if c_sc > best_sc:
            best_sc = c_sc
            best_st = round(c_st, 2)

    if best_st is not None:
        return {
            "clip_path": default_clip_path,
            "start_s": best_st,
            "crop_x_norm": base_crop_x,
            "mode": f"v24_subclip2_{sub_intent}",
        }

    # 2. If parent clip is long enough for a clean non-overlapping second window inside the same shot
    if clip_dur >= parent_st + sub_dur + 2.1:
        return {
            "clip_path": default_clip_path,
            "start_s": round(min(clip_dur - sub_dur - 0.1, parent_st + max(1.6, sub_dur + 0.5)), 2),
            "crop_x_norm": base_crop_x,
            "mode": f"v24_subclip2_offset_{sub_intent}",
        }

    # 3. Otherwise rescue with the best semantically matching alternate clip from visual_index or clean pool
    if v_idx:
        rescued_p = pick_best_cross_clip_rescue(subclip2_scene, default_clip_path.name, v_idx, assets_dir, u_cnt)
        if rescued_p is not None:
            r_meta = v_idx.get(rescued_p.name, {})
            r_shots = r_meta.get("shots") if isinstance(r_meta.get("shots"), list) else []
            r_st = float(r_shots[0].get("start_s", 0.3)) if r_shots and isinstance(r_shots[0], dict) else 0.3
            return {
                "clip_path": rescued_p,
                "start_s": round(r_st, 2),
                "crop_x_norm": float(r_meta.get("subject_x_norm", 0.50) or 0.50),
                "mode": "v24_subclip2_cross_clip",
            }

    alt_clips = [c for c in clips if c.name != default_clip_path.name]
    chosen_clip = alt_clips[int(subclip2_scene.get("index", 1)) % len(alt_clips)] if alt_clips else default_clip_path
    return {
        "clip_path": chosen_clip,
        "start_s": round(parent_st + 1.8, 2) if chosen_clip.name == default_clip_path.name else 0.4,
        "crop_x_norm": base_crop_x,
        "mode": "v24_subclip2_alt",
    }


def analyze_clip_golden_moment(
    clip_path: Path,
    scene: dict,
    cfg: StudioConfig,
    use_vision: bool = False,
    precomputed_signals: Optional[Tuple[float, List[dict], List[float]]] = None,
    excluded_windows: Optional[List[Tuple[float, float]]] = None,
    refine_onset: bool = True,
    clip_visual_meta: Optional[dict] = None,
) -> dict:
    dur = max(1.5, float(scene.get("duration_s", 2.2)))
    idx = int(scene.get("index", 1))
    intent, is_audio_hook = _scene_intent(scene)
    query_text = f"{scene.get('visual_desc', '')} {scene.get('vo', '')}".strip()
    shot_index = (clip_visual_meta.get("shots") if isinstance(clip_visual_meta, dict) else None) or []

    total_dur, frames, cuts = precomputed_signals if precomputed_signals is not None else extract_multimodal_signals(clip_path, cfg)
    shots = split_clip_into_shots(total_dur, cuts)
    cands = score_candidate_windows(
        total_dur,
        frames,
        cuts,
        dur,
        intent=intent,
        top_k=4,
        excluded_windows=excluded_windows,
        shot_index=shot_index if shot_index else shots,
        scene_query_text=query_text,
    )
    best = cands[0]

    start_s = float(best["start_s"])
    score = float(best["score"])
    mode = f"v24_signal_{intent}"
    use_audio = False
    allow_flip = True
    crop_x_norm = float((clip_visual_meta or {}).get("subject_x_norm", 0.50) or 0.50)
    vision_meta: Optional[dict] = None

    if is_audio_hook:
        a_start, a_rms = find_peak_audio_start(clip_path, dur, cfg)
        if a_rms > -32.0:
            start_s = a_start
            score = round(max(0.0, 100.0 + a_rms), 1)
            mode = "v24_audio_hook"
            use_audio = True

    if use_vision and total_dur > dur + 0.5:
        v_res = rerank_candidates_with_vision(clip_path, cands, total_dur, dur, scene, cfg)
        if v_res is not None:
            start_s = float(v_res["start_s"])
            allow_flip = bool(v_res["allow_flip"])
            crop_x_norm = float(v_res.get("subject_x_norm", crop_x_norm) or 0.50)
            mode = f"v24_triplet_{intent}"
            vision_meta = v_res

    # Stage 3 (v2.4): Coarse-to-Fine 0.25s Action-Onset / Peak-to-Word Lock / Sharpness Refinement
    refinement_meta: Optional[dict] = None
    emphasis_off = _extract_scene_emphasis_offset(scene)
    if refine_onset and not use_audio and total_dur > dur + 0.5:
        start_s, refinement_meta = refine_action_onset(
            clip_path, start_s, dur, total_dur, cuts, intent, cfg, target_emphasis_offset_s=emphasis_off
        )

    # Probe horizontal motion subject focus when Vision did not supply a non-center focus
    if abs(crop_x_norm - 0.50) < 0.02 and total_dur > 0.5:
        crop_x_norm = detect_horizontal_subject_focus(clip_path, start_s, dur, cfg)

    entry = {
        "scene_index": idx,
        "filename": clip_path.name,
        "start_s": round(start_s, 2),
        "crop_x_norm": round(crop_x_norm, 2),
        "mode": mode,
        "metric_score": score,
        "use_clip_audio": use_audio,
        "allow_flip": allow_flip,
        "signals": {
            "clip_dur_s": total_dur,
            "internal_cuts": len(cuts),
            "shot_count": len(shots),
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
    """Golden Moment Engine v2.4:
    Stage 1: Parallel 4-signal FFmpeg + Per-Shot Semantic Bonus + Seam/Frame-0 Gate + Anti-Repeat Exclusion
    Stage 2: Parallel Action-Triplet Vision Reranker (with auto-retry) + Synonym/Shot-Aware Cross-Clip Rescue
    Stage 3: 4 fps (0.25s) Action-Onset Acceleration (dYDIF/dt) + Peak-to-Word Lock + Subject Spatial Focus (crop_x).
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
                    needs_vision_fill = use_vision and any(
                        not m.get("vision") and not str(m.get("mode", "")).startswith("visual_verified_")
                        for m in existing_by_idx.values()
                    )
                    if not needs_vision_fill:
                        return existing_by_idx
        except Exception:
            pass

    to_scan_scenes: List[Tuple[dict, Path]] = []
    preserved: Dict[int, dict] = {}
    unique_clips: Dict[str, Path] = {}

    for sc in scenes:
        idx = int(sc["index"])
        prev = existing_by_idx.get(idx)
        if prev and not force_rescan:
            if str(prev.get("mode", "")).startswith("visual_verified_") or (use_vision and prev.get("vision")):
                if "crop_x_norm" not in prev:
                    cp_prev = assets_dir / prev["filename"]
                    prev["crop_x_norm"] = (
                        detect_horizontal_subject_focus(cp_prev, float(prev.get("start_s", 0.0)), float(sc.get("duration_s", 2.0)), studio_cfg)
                        if cp_prev.exists()
                        else 0.50
                    )
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

    # Load cached _clip_visual_index.json immediately if available (even when use_vision=False!)
    v_index: Dict[str, dict] = {}
    idx_file = assets_dir / "_clip_visual_index.json"
    if idx_file.exists():
        try:
            v_index = json.loads(idx_file.read_text(encoding="utf-8")) or {}
        except Exception:
            v_index = {}

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
                        v_index.get(cp.name),
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

    # Stage 2.5 (v2.4): Cross-Clip Rescue when Vision scored < 7/10, has burned-in text, caption zone cluttered,
    # OR when Vision timed out on a scene / cached visual index flags the pre-assigned clip as dirty!
    if use_vision or v_index:
        weak_scenes: List[dict] = []
        for sc, _ in to_scan_scenes:
            idx = int(sc["index"])
            if idx not in computed:
                continue
            m_cur = computed[idx]
            vis = m_cur.get("vision") or {}
            idx_meta = v_index.get(m_cur["filename"], {})
            is_weak = False
            if vis:
                if (
                    int(vis.get("semantic_score", 8)) < 7
                    or bool(vis.get("has_burned_text", False))
                    or not bool(vis.get("caption_zone_clean", True))
                ):
                    is_weak = True
            elif use_vision:
                # Vision timed out even after retry -> mark for index rescue
                is_weak = True
            elif idx_meta and (bool(idx_meta.get("has_burned_text", False)) or not bool(idx_meta.get("caption_zone_clean", True))):
                is_weak = True
            if is_weak:
                weak_scenes.append(sc)

        if weak_scenes:
            if not v_index and use_vision:
                v_index = build_clip_visual_index(assets_dir, studio_cfg, force=False, signal_cache=signal_cache)
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
                        alt_p,
                        sc,
                        studio_cfg,
                        use_vision=use_vision,
                        precomputed_signals=alt_sig,
                        excluded_windows=list(claimed_windows.get(alt_p.name, [])),
                        refine_onset=True,
                        clip_visual_meta=v_index.get(alt_p.name),
                    )
                    rescued_vis = rescued.get("vision") or {}
                    old_vis = cur_m.get("vision") or {}
                    if (
                        not old_vis
                        or (
                            int(rescued_vis.get("semantic_score", 7)) >= int(old_vis.get("semantic_score", 0))
                            and not rescued_vis.get("has_burned_text", False)
                            and rescued_vis.get("caption_zone_clean", True)
                        )
                    ):
                        rescued["mode"] = "v24_cross_clip_rescue"
                        rescued["rescued_from"] = cur_m["filename"]
                        computed[idx] = rescued
                        used_counts[alt_p.name] = used_counts.get(alt_p.name, 0) + 1

    moments_list = [computed[idx] for idx in sorted(computed)]
    cache_p.write_text(json.dumps(moments_list, indent=2, ensure_ascii=False), encoding="utf-8")
    return computed
