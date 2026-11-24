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
) -> List[dict]:
    """Align scene VO lines to ASR word timestamps via sequence matching (drift-resistant).

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
                mid = (sc["words"][-1]["end"] + next_sc["words"][0]["start"]) / 2.0
                sc["end_s"] = max(sc["start_s"] + min_scene_dur, min(total_duration - min_scene_dur, mid))
            elif sc["words"]:
                # Current has words, next is silent payoff/outro: reserve proportional tail
                rem_brief = max(0.8, next_sc.get("brief_end_s", 0.0) - next_sc.get("brief_start_s", 0.0))
                proposed = min(sc["words"][-1]["end"] + 0.15, total_duration - min(rem_brief, 1.4))
                sc["end_s"] = max(sc["start_s"] + min_scene_dur, proposed)
            else:
                # Current has no words: use proportional brief duration
                b_dur = max(min_scene_dur, (sc.get("brief_end_s", 0.0) - sc.get("brief_start_s", 0.0)) * (total_duration / brief_total))
                sc["end_s"] = min(total_duration - min_scene_dur, sc["start_s"] + b_dur)

        sc["start_s"] = round(sc["start_s"], 3)
        sc["end_s"] = round(sc["end_s"], 3)
        sc["duration_s"] = round(max(0.4, sc["end_s"] - sc["start_s"]), 3)

    # Final pass: guarantee exact end alignment with total_duration
    scenes[-1]["end_s"] = round(total_duration, 3)
    scenes[-1]["duration_s"] = round(max(0.4, scenes[-1]["end_s"] - scenes[-1]["start_s"]), 3)
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
    max_proof_s: float = 4.4,
) -> List[dict]:
    """Squooshify 5/5 DNA: keep Hook crisp & fast, hold physical proof shots uninterrupted (3.0-4.4s).

    Merges adjacent body/mechanism scenes that share the same clip filename (up to max_proof_s <= 4.4s,
    safely within YPP's 5.0s max source clip rule) so physical evidence breathes without a jump-cut.
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
            if comb_dur <= max_proof_s:
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
    if pos_idx >= total_scenes - 1:
        return "payoff"
    if any(k in blob for k in ("kabur", "lari", "tendang", "garuk", "dorong", "menerkam", "cabut", "potong", "geser", "bergetar", "bergerak")):
        return "process"
    return "balanced"


def apply_intra_unit_clip_splitting(
    scenes: List[dict],
    cta_window: Optional[Tuple[float, float]] = None,
    min_unit_split_s: float = 2.30,
    min_subclip_s: float = 0.85,
    cut_word_offset_s: float = -0.05,
) -> List[dict]:
    """Squooshify v3.6 Intra-Unit Clip Splitter:
    Splits long non-proof, non-CTA narrative units (>= 2.65s) into 2 visual sub-clips snapped to
    VO keyword/clause boundaries (word_start - 0.05s), strictly enforcing the >= 0.85s (25f) cognitive floor.
    Marks `unit_id`, `scene_role`, and `is_intra_unit_cut` for downstream transition & speed control.
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

        # Never split proof shots (held 3.0-4.4s @ 1.0x), CTA overlays, or short units (< 2.65s)
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
