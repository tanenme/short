"""Fuzzy word-to-scene alignment, dynamic highlight extraction, and CTA detection."""

from __future__ import annotations

import difflib
import re
from typing import Dict, List, Optional, Set, Tuple

AUDIO_TAG_RE = re.compile(r"\[([^\[\]]+)\]")
NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def strip_audio_tags(text: str) -> str:
    return re.sub(r"\s+", " ", AUDIO_TAG_RE.sub(" ", text or "")).strip()


def normalize_token(word: str) -> str:
    return NON_WORD_RE.sub("", (word or "").lower()).strip()


def tokenize_phrase(text: str) -> List[str]:
    """Tokenize Indonesian/English text, splitting hyphens cleanly so 'ek-ek' aligns reliably."""
    clean = strip_audio_tags(text)
    raw_parts = re.split(r"[\s\-—–/]+", clean)
    tokens = [normalize_token(p) for p in raw_parts]
    return [t for t in tokens if t]


def extract_dynamic_highlights(
    scenes: List[dict],
    naskah_text: str = "",
) -> Set[str]:
    """Extract emphasis words dynamically from CAPS words in script/brief + universal CTA cues."""
    highlights: Set[str] = {"SUBSCRIBE"}
    skip = {"WIB", "CTA", "SFX", "BGM", "CCTV", "VO", "ID", "YPP", "FYP", "APV"}

    blobs = [naskah_text] + [s.get("vo_raw", "") or s.get("vo", "") for s in scenes]
    for blob in blobs:
        clean = strip_audio_tags(blob)
        for w in re.findall(r"\b[A-Z]{3,}(?:-[A-Z]+)?\b", clean):
            if w not in skip:
                highlights.add(w)
                for sub in w.split("-"):
                    if len(sub) >= 3 and sub not in skip:
                        highlights.add(sub)
    return highlights


def _expand_asr_words(words: List[dict]) -> List[Tuple[str, int]]:
    """Map expanded normalized sub-tokens back to their original ASR word index."""
    expanded: List[Tuple[str, int]] = []
    for idx, w in enumerate(words):
        subs = tokenize_phrase(w.get("word", ""))
        if not subs:
            continue
        for sub in subs:
            expanded.append((sub, idx))
    return expanded


def synchronize_scenes_fuzzy(
    scenes: List[dict],
    words: List[dict],
    total_duration: float,
    min_scene_dur: float = 0.8,
    cut_on_word_lock: bool = False,
    word_onset_lead_s: float = 0.033,
) -> List[dict]:
    """Align scene VO lines to ASR word timestamps via sequence matching (drift-resistant).

    When `cut_on_word_lock=True` (v3.7 Forensic Mode), inter-unit boundaries snap 1 frame (33ms)
    before the first spoken word onset of the incoming unit instead of drifting into pause midpoints.
    Guarantees a continuous, monotonic timeline covering [0.0, total_duration].
    """
    if not scenes:
        return []

    if not words:
        # Fallback cleanly to brief timings scaled to total_duration
        brief_end = max((s.get("brief_end_s", 0.0) for s in scenes), default=total_duration) or total_duration
        scale = total_duration / brief_end if brief_end > 0 else 1.0
        for i, sc in enumerate(scenes):
            sc["words"] = []
            sc["start_s"] = 0.0 if i == 0 else scenes[i - 1]["end_s"]
            sc["end_s"] = total_duration if i == len(scenes) - 1 else round(sc.get("brief_end_s", 0.0) * scale, 3)
            sc["duration_s"] = max(min_scene_dur, sc["end_s"] - sc["start_s"])
        return scenes

    # Build concatenated script tokens with scene ownership
    script_tokens: List[str] = []
    token_scene_idx: List[int] = []
    for si, sc in enumerate(scenes):
        vo_line = (sc.get("vo") or "").strip()
        if not vo_line or vo_line.startswith("("):
            continue
        toks = tokenize_phrase(vo_line)
        for t in toks:
            script_tokens.append(t)
            token_scene_idx.append(si)

    expanded_asr = _expand_asr_words(words)
    asr_tokens = [tok for tok, _ in expanded_asr]

    # SequenceMatcher aligns script_tokens <-> asr_tokens robustly even with insertions/deletions
    sm = difflib.SequenceMatcher(a=script_tokens, b=asr_tokens, autojunk=False)
    scene_word_indices: Dict[int, Set[int]] = {i: set() for i in range(len(scenes))}

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            for offset in range(i2 - i1):
                si = token_scene_idx[i1 + offset]
                _, orig_w_idx = expanded_asr[j1 + offset]
                scene_word_indices[si].add(orig_w_idx)
        elif tag == "replace":
            # Proportionally assign replaced ASR tokens to corresponding script scenes
            s_len = max(1, i2 - i1)
            a_len = j2 - j1
            for a_off in range(a_len):
                mapped_s_off = min(s_len - 1, int(a_off * s_len / max(1, a_len)))
                si = token_scene_idx[i1 + mapped_s_off]
                _, orig_w_idx = expanded_asr[j1 + a_off]
                scene_word_indices[si].add(orig_w_idx)

    # Ensure every ASR word belongs to at most one scene (prefer earliest owning scene)
    claimed: Set[int] = set()
    for si, sc in enumerate(scenes):
        idxs = sorted(w_i for w_i in scene_word_indices[si] if w_i not in claimed)
        claimed.update(idxs)
        sc["words"] = [words[w_i] for w_i in idxs]

    # Compute continuous start_s / end_s across all scenes (including silent payoff/outro beats)
    brief_total = max((s.get("brief_end_s", 0.0) for s in scenes), default=total_duration) or total_duration
    for i, sc in enumerate(scenes):
        if i == 0:
            sc["start_s"] = 0.0
        else:
            sc["start_s"] = scenes[i - 1]["end_s"]

        if i == len(scenes) - 1:
            sc["end_s"] = total_duration
        else:
            next_sc = scenes[i + 1]
            if sc["words"] and next_sc.get("words"):
                prev_end = float(sc["words"][-1]["end"])
                next_start = float(next_sc["words"][0]["start"])
                if cut_on_word_lock and next_start >= prev_end:
                    target_cut = max(prev_end, next_start - max(0.0, float(word_onset_lead_s)))
                else:
                    target_cut = (prev_end + next_start) / 2.0
                sc["end_s"] = max(sc["start_s"] + min_scene_dur, min(total_duration - min_scene_dur, target_cut))
            elif sc["words"]:
                # Current has words, next is silent payoff/outro: reserve proportional tail
                rem_brief = max(0.8, next_sc.get("brief_end_s", 0.0) - next_sc.get("brief_start_s", 0.0))
                proposed = min(sc["words"][-1]["end"] + 0.15, total_duration - min(rem_brief, 1.4))
                sc["end_s"] = max(sc["start_s"] + min_scene_dur, proposed)
            else:
                # Current has no words (e.g. "(jeda elipsis...)" beat): use proportional brief duration,
                # but NEVER delay the next spoken unit. v3.8 fix: 008's silent beat was granted 1.699s
                # and pushed every following scene +1.67s off the VO timeline.
                b_dur = max(min_scene_dur, (sc.get("brief_end_s", 0.0) - sc.get("brief_start_s", 0.0)) * (total_duration / brief_total))
                if next_sc.get("words"):
                    next_onset = float(next_sc["words"][0]["start"])
                    # Never delay the next spoken unit: the beat may run shorter than min_scene_dur
                    # (a 0.8s floor here would start the next visual 0.6s after its word).
                    b_dur = min(b_dur, max(0.10, next_onset - float(sc["start_s"])))
                sc["end_s"] = min(total_duration - min_scene_dur, sc["start_s"] + b_dur)

        sc["start_s"] = round(sc["start_s"], 3)
        sc["end_s"] = round(sc["end_s"], 3)
        sc["duration_s"] = round(max(0.05, sc["end_s"] - sc["start_s"]), 3)

    # Final pass: guarantee exact end alignment with total_duration
    scenes[-1]["end_s"] = round(total_duration, 3)
    scenes[-1]["duration_s"] = round(max(0.05, scenes[-1]["end_s"] - scenes[-1]["start_s"]), 3)

    # v3.8 Payoff Floor: the final beat must breathe >= 1.20s. 008 shipped a 0.38s payoff.
    # v3.8.1: pull the time from as many preceding scenes as needed (each keeps min_scene_dur and
    # its own duration), instead of only the neighbour -- which was usually already at the floor.
    min_payoff_s = 1.20
    if len(scenes) >= 2:
        last_sc = scenes[-1]
        needed = round(min_payoff_s - (float(last_sc["end_s"]) - float(last_sc["start_s"])), 3)
        k = len(scenes) - 2
        while needed > 0.001 and k >= 0:
            s_k = scenes[k]
            slack = max(0.0, float(s_k["end_s"]) - float(s_k["start_s"]) - min_scene_dur)
            take = min(needed, round(slack, 3))
            if take > 0.001:
                s_k["end_s"] = round(float(s_k["end_s"]) - take, 3)
                s_k["duration_s"] = round(s_k["end_s"] - s_k["start_s"], 3)
                for j in range(k + 1, len(scenes)):
                    scenes[j]["start_s"] = round(float(scenes[j]["start_s"]) - take, 3)
                    if j < len(scenes) - 1:
                        scenes[j]["end_s"] = round(float(scenes[j]["end_s"]) - take, 3)
                    scenes[j]["duration_s"] = round(
                        float(scenes[j]["end_s"]) - float(scenes[j]["start_s"]), 3
                    )
                needed = round(needed - take, 3)
            k -= 1
    return scenes


def detect_cta_window(scenes: List[dict], words: List[dict]) -> Optional[Tuple[float, float]]:
    """Find the exact (start_s, end_s) window for the Subscribe CTA, strictly clamped to its scene."""
    # Find the owning CTA scene first so blur/badges NEVER bleed into the next scene
    cta_scene = None
    for sc in scenes:
        blob = f"{sc.get('vo', '')} {sc.get('screen_text', '')} {sc.get('visual_desc', '')}".lower()
        if "subscribe" in blob:
            cta_scene = sc
            break

    for w in words:
        if "subscribe" in normalize_token(w.get("word", "")):
            start = max(0.0, float(w["start"]) - 0.55)
            end = float(w["end"]) + 0.70
            if cta_scene is not None:
                start = max(float(cta_scene["start_s"]) + 0.10, start)
                end = min(float(cta_scene["end_s"]) - 0.08, end)
            if end <= start + 0.6:
                end = start + 1.2
            return (round(start, 3), round(end, 3))

    if cta_scene is not None:
        return (round(float(cta_scene["start_s"]) + 0.15, 3), round(float(cta_scene["end_s"]) - 0.10, 3))
    return None


def apply_adaptive_proof_pacing(
    scenes: List[dict],
    golden_moments: dict,
    clip_mapping: dict,
    max_proof_s: float = 2.80,
    require_motion_proof: bool = False,
) -> List[dict]:
    """v3.8: hold a physical proof shot only up to 2.80s AND only when the source actually moves.

    Merging adjacent body/mechanism scenes that share the same clip gives proof shots room to breathe,
    but v3.7 merged up to 4.4s and produced 3.2-4.1s frozen frames (007 00:07.05-00:11.11,
    009 00:08.60-00:12.23). `require_motion_proof` blocks the merge unless the golden-moment vision
    pass verified motion, so a held shot is always visibly alive (fail-closed: a missing vision
    pass means no merge).
    """
    if len(scenes) < 5:
        return scenes

    def _clip_name_for(sc: dict) -> str:
        gm = golden_moments.get(sc["index"])
        if gm and gm.get("filename"):
            return str(gm["filename"])
        cp = clip_mapping.get(sc["index"])
        return cp.name if cp is not None else ""

    merged: List[dict] = []
    i = 0
    while i < len(scenes):
        cur = dict(scenes[i])
        cur_name = _clip_name_for(cur)
        # Only merge inside body/mechanism act (after CTA scene #3 and before final payoff scene)
        if (
            cur.get("index", 0) >= 4
            and i + 1 < len(scenes) - 1
            and cur_name
            and _clip_name_for(scenes[i + 1]) == cur_name
        ):
            nxt = scenes[i + 1]
            comb_dur = round(float(nxt["end_s"]) - float(cur["start_s"]), 3)
            motion_ok = True
            if require_motion_proof:
                # Fail closed: only a vision pass that actually saw motion may hold a shot this long.
                # `metric_score` is a sharpness/exposure score (a frozen clip still scores 74-87), so it
                # cannot substitute for motion evidence.
                for idx in (cur.get("index"), nxt.get("index")):
                    vis = (golden_moments.get(idx) or {}).get("vision") or {}
                    if not bool(vis.get("motion_verified", False)):
                        motion_ok = False
                        break
            if comb_dur <= max_proof_s and motion_ok:
                cur["end_s"] = nxt["end_s"]
                cur["duration_s"] = comb_dur
                cur["vo"] = (cur.get("vo", "") + " " + nxt.get("vo", "")).strip()
                cur["words"] = list(cur.get("words", [])) + list(nxt.get("words", []))
                cur["sfx"] = (cur.get("sfx", "") + " / " + nxt.get("sfx", "")).strip(" /")
                cur["is_proof_shot"] = True
                merged.append(cur)
                i += 2
                continue
        merged.append(cur)
        i += 1
    return merged


def classify_scene_role(scene: dict, pos_idx: int, total_scenes: int, cta_window: Optional[Tuple[float, float]] = None) -> str:
    """Classify each scene into a Squooshify pacing/speed role: hook, cta, proof, process, or payoff."""
    if pos_idx == 0 or int(scene.get("index", 0)) == 1:
        return "hook"
    if scene.get("is_proof_shot"):
        return "proof"
    blob = f"{scene.get('visual_desc', '')} {scene.get('vo', '')} {scene.get('screen_text', '')}".lower()
    if any(k in blob for k in ("subscribe", "like", "cta")):
        return "cta"
    if cta_window is not None:
        st, en = float(scene.get("start_s", 0.0)), float(scene.get("end_s", 0.0))
        ov = max(0.0, min(en, cta_window[1]) - max(st, cta_window[0]))
        if ov >= 0.4 * max(0.1, en - st):
            return "cta"
    vo_low = str(scene.get("vo", "")).strip().lower()
    if pos_idx >= total_scenes - 1 or (
        pos_idx >= max(2, total_scenes - 2) and vo_low.startswith(("makanya", "itulah", "jadinya"))
    ):
        return "payoff"
    if any(k in blob for k in ("kabur", "lari", "tendang", "garuk", "dorong", "menerkam", "cabut", "potong", "geser", "bergetar", "bergerak")):
        return "process"
    return "balanced"


def detect_bgm_vacuum_windows(
    words: List[dict],
    scenes: List[dict],
    hook_end_s: float = 0.0,
    cta_window: Optional[Tuple[float, float]] = None,
    max_events: int = 2,
) -> List[Tuple[float, float]]:
    """v3.7 Acoustic Vacuum Reset: detect up to `max_events` pre-pivot BGM mute windows (~0.38s @ -28dB).

    Window 1 targets the early counter-myth pivot ("bukan", "salah", "ternyata", "padahal").
    Window 2 targets the final payoff pivot ("makanya", "itulah", "jadinya") separated by >= 3.2s.
    """
    if max_events <= 0 or not words:
        return []

    twist_tokens = {"bukan", "salah", "ternyata", "padahal", "impossible", "anehnya"}
    payoff_tokens = {"makanya", "itulah", "jadinya", "faktanya", "akhirnya"}

    def _overlaps_cta(ws: float, we: float) -> bool:
        if cta_window is None:
            return False
        return min(we, float(cta_window[1])) > max(ws, float(cta_window[0]))

    windows: List[Tuple[float, float]] = []

    # 1. Early counter-myth / twist pivot vacuum
    for w in words:
        tok = normalize_token(w.get("word", ""))
        wst = float(w.get("start", 0.0))
        if wst < max(1.0, hook_end_s - 0.15):
            continue
        if tok in twist_tokens:
            vs = round(max(hook_end_s, wst - 0.28), 3)
            ve = round(max(vs + 0.36, wst + 0.14), 3)
            if not _overlaps_cta(vs, ve):
                windows.append((vs, ve))
                break

    if len(windows) >= max_events:
        return windows

    # 2. Late pre-payoff vacuum ("Makanya...")
    min_second_st = max(8.0, (windows[0][1] + 3.2) if windows else (hook_end_s + 4.5))
    payoff_cand: Optional[Tuple[float, float]] = None
    for w in words:
        tok = normalize_token(w.get("word", ""))
        wst = float(w.get("start", 0.0))
        if wst < min_second_st:
            continue
        if tok in payoff_tokens:
            vs = round(max(min_second_st - 0.3, wst - 0.28), 3)
            ve = round(max(vs + 0.36, wst + 0.12), 3)
            if not _overlaps_cta(vs, ve):
                payoff_cand = (vs, ve)
                break

    if payoff_cand is None and scenes:
        last_sc = scenes[-1]
        lst = float(last_sc.get("start_s", 0.0))
        if lst >= min_second_st:
            vs = round(max(0.0, lst - 0.26), 3)
            ve = round(lst + 0.12, 3)
            if not _overlaps_cta(vs, ve):
                payoff_cand = (vs, ve)

    if payoff_cand is not None:
        windows.append(payoff_cand)

    return windows[:max_events]


def apply_intra_unit_clip_splitting(
    scenes: List[dict],
    cta_window: Optional[Tuple[float, float]] = None,
    min_unit_split_s: float = 2.30,
    min_subclip_s: float = 0.85,
    cut_word_offset_s: float = -0.05,
    protect_payoff_hold: bool = False,
    golden_moments: Optional[dict] = None,
    max_payoff_hold_s: float = 4.80,
) -> List[dict]:
    """Squooshify v3.7 Intra-Unit Clip Splitter:
    Splits long non-proof, non-CTA narrative units (>= 2.30s) into 2 visual sub-clips snapped to
    VO keyword/clause boundaries (word_start - 0.05s), strictly enforcing the >= 0.85s (25f) cognitive floor.
    When `protect_payoff_hold=True`, protects dynamic `payoff` units (<= 4.80s, YPP-compliant) as an
    unsplit 1.0x multi-beat hold, falling back to splitting if source motion is weak/unverified.
    """
    clause_triggers = {
        "tapi", "bukan", "salah", "saat", "pas", "karena", "sambil", "lihat",
        "mulai", "langsung", "ternyata", "makanya", "sampai", "ketika", "padahal",
    }
    out: List[dict] = []
    total = len(scenes)

    for pos, raw_sc in enumerate(scenes):
        sc = dict(raw_sc)
        role = classify_scene_role(sc, pos, total, cta_window)
        unit_id = f"U{pos + 1}"
        sc["unit_id"] = unit_id
        sc["scene_role"] = role
        sc["is_intra_unit_cut"] = False

        dur = float(sc.get("duration_s", float(sc["end_s"]) - float(sc["start_s"])))
        sc_words = list(sc.get("words") or [])

        if role == "payoff" and protect_payoff_hold and dur <= max_payoff_hold_s:
            gm = (golden_moments or {}).get(sc.get("index"))
            payoff_dynamic = True
            if gm is not None:
                vis_ok = bool((gm.get("vision") or {}).get("motion_verified", False))
                score_ok = float(gm.get("metric_score", 0.0) or 0.0) >= 55.0
                payoff_dynamic = vis_ok or score_ok
            if payoff_dynamic:
                sc["is_payoff_hold"] = True
                out.append(sc)
                continue

        # Never split proof shots (held 3.0-4.4s @ 1.0x), CTA overlays, or short units (< min_unit_split_s)
        if role in ("proof", "cta") or dur < min_unit_split_s or len(sc_words) < 4:
            out.append(sc)
            continue

        st_u = float(sc["start_s"])
        en_u = float(sc["end_s"])
        mid_u = (st_u + en_u) / 2.0

        # Pick best split word whose onset keeps BOTH halves >= min_subclip_s (25f cognitive guardrail)
        best_wi = None
        best_score = -999.0
        for wi in range(1, len(sc_words) - 1):
            w_st = float(sc_words[wi]["start"])
            cut_t = round(w_st + cut_word_offset_s, 3)
            left_d = cut_t - st_u
            right_d = en_u - cut_t
            if left_d < min_subclip_s or right_d < min_subclip_s:
                continue
            tok = "".join(ch for ch in (sc_words[wi].get("word") or "").lower() if ch.isalnum())
            # Prefer clause/action keywords near the midpoint
            dist_pen = abs(cut_t - mid_u) * 2.0
            kw_bonus = 3.0 if tok in clause_triggers else (1.2 if len(tok) >= 6 else 0.0)
            score = kw_bonus - dist_pen
            if score > best_score:
                best_score = score
                best_wi = wi

        if best_wi is None:
            out.append(sc)
            continue

        cut_t = round(float(sc_words[best_wi]["start"]) + cut_word_offset_s, 3)
        left_sc = dict(sc)
        left_sc["end_s"] = cut_t
        left_sc["duration_s"] = round(cut_t - st_u, 3)
        left_sc["words"] = sc_words[:best_wi]
        left_sc["vo"] = " ".join(w.get("word", "") for w in left_sc["words"]).strip() or sc.get("vo", "")
        left_sc["sub_clip_part"] = 1

        right_sc = dict(sc)
        right_sc["start_s"] = cut_t
        right_sc["duration_s"] = round(en_u - cut_t, 3)
        right_sc["words"] = sc_words[best_wi:]
        right_sc["vo"] = " ".join(w.get("word", "") for w in right_sc["words"]).strip() or sc.get("vo", "")
        right_sc["is_intra_unit_cut"] = True
        right_sc["sub_clip_part"] = 2
        right_sc["split_trigger_word"] = sc_words[best_wi].get("word", "")

        out.append(left_sc)
        out.append(right_sc)

    return out


def enforce_hook_max_first_shot(
    scenes: List[dict],
    max_first_shot_s: float = 1.20,
    min_subclip_s: float = 0.55,
    cut_word_offset_s: float = -0.05,
) -> List[dict]:
    """v3.8 Hook Pattern-Interrupt: guarantee the first visual change lands <= 1.20s.

    Squooshify's five benchmarks cut their opening shot at 00:00.36-00:01.48, while v3.7 renders held
    the hook for 2.20-3.33s because `min_unit_split_s=2.30` never split the first unit. This pass runs
    after intra-unit splitting and splits ONLY the first scene, at the word onset closest to the target.
    """
    if not scenes:
        return scenes
    first = dict(scenes[0])
    st = float(first["start_s"])
    en = float(first["end_s"])
    dur = en - st
    if dur <= max_first_shot_s + 0.05:
        return scenes
    words = list(first.get("words") or [])
    target = st + max_first_shot_s
    best_wi = None
    best_d = 1e9
    for wi in range(1, len(words)):
        cut_t = round(float(words[wi]["start"]) + cut_word_offset_s, 3)
        if cut_t - st < min_subclip_s or en - cut_t < min_subclip_s:
            continue
        d = abs(cut_t - target)
        if d < best_d:
            best_d = d
            best_wi = wi
    if best_wi is None:
        return scenes

    cut_t = round(float(words[best_wi]["start"]) + cut_word_offset_s, 3)
    left = dict(first)
    left["end_s"] = cut_t
    left["duration_s"] = round(cut_t - st, 3)
    left["words"] = words[:best_wi]
    left["vo"] = " ".join(w.get("word", "") for w in left["words"]).strip() or first.get("vo", "")
    left["sub_clip_part"] = 1

    right = dict(first)
    right["start_s"] = cut_t
    right["duration_s"] = round(en - cut_t, 3)
    right["words"] = words[best_wi:]
    right["vo"] = " ".join(w.get("word", "") for w in right["words"]).strip() or first.get("vo", "")
    right["is_intra_unit_cut"] = True
    right["sub_clip_part"] = 2
    right["split_trigger_word"] = words[best_wi].get("word", "")
    right["unit_id"] = left.get("unit_id", "U1")
    right["scene_role"] = left.get("scene_role", "hook")
    return [left, right] + list(scenes[1:])
