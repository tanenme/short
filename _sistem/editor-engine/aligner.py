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
