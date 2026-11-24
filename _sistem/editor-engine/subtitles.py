"""Broadcast-quality kinetic ASS subtitle & callout banner generator for YouTube Shorts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Optional, Set, Tuple

from core.models import VisualStyle


def hex_to_ass_bgr(hex_color: str, alpha: str = "00") -> str:
    """Convert #RRGGBB to ASS &HAABBGGRR& format."""
    c = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(c) != 6:
        c = "FFFFFF"
    r, g, b = c[0:2].upper(), c[2:4].upper(), c[4:6].upper()
    return f"&H{alpha.upper()}{b}{g}{r}&"


def fmt_ass_time(sec: float) -> str:
    sec = max(0.0, float(sec))
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = sec % 60
    return f"{h:d}:{m:02d}:{s:05.2f}"


def _escape_ass(text: str) -> str:
    return (text or "").replace("\\", "").replace("{", "(").replace("}", ")").strip()


def _is_highlight(word: str, highlights: Set[str]) -> bool:
    clean = re.sub(r"[^\w\-]", "", (word or "").upper())
    if not clean:
        return False
    if clean in highlights:
        return True
    return any(h in clean for h in highlights if len(h) >= 4)


def _group_words_for_readability(words: List[dict], highlights: Set[str]) -> List[dict]:
    """Group ultra-short connector words with their neighbor while keeping impact words solo."""
    connectors = {"DI", "KE", "DARI", "YANG", "DAN", "INI", "ITU", "PAS", "YA", "GAK", "TAPI"}
    grouped: List[dict] = []
    i = 0
    while i < len(words):
        curr = words[i]
        raw = re.sub(r"[^\w\?\!\-]", "", (curr.get("word") or "").strip().upper())
        if not raw:
            i += 1
            continue
        start = float(curr["start"])
        end = float(curr["end"])
        is_hl = _is_highlight(raw, highlights)

        # Pair a very short connector (<4 chars, non-highlight) with the next non-highlight short word
        if (
            not is_hl
            and raw in connectors
            and i + 1 < len(words)
        ):
            nxt = words[i + 1]
            nxt_raw = re.sub(r"[^\w\?\!\-]", "", (nxt.get("word") or "").strip().upper())
            gap = float(nxt["start"]) - end
            if nxt_raw and gap < 0.25 and len(raw) + len(nxt_raw) <= 13:
                nxt_hl = _is_highlight(nxt_raw, highlights)
                grouped.append(
                    {
                        "text": f"{raw} {nxt_raw}",
                        "start": start,
                        "end": max(float(nxt["end"]), start + 0.22),
                        "highlight": nxt_hl,
                        "parts": [(raw, False), (nxt_raw, nxt_hl)],
                    }
                )
                i += 2
                continue

        grouped.append(
            {
                "text": raw,
                "start": start,
                "end": max(end, start + 0.18),
                "highlight": is_hl,
                "parts": [(raw, is_hl)],
            }
        )
        i += 1

    # Prevent any subtitle overlap between consecutive events
    for idx in range(len(grouped) - 1):
        if grouped[idx]["end"] > grouped[idx + 1]["start"]:
            grouped[idx]["end"] = max(grouped[idx]["start"] + 0.10, grouped[idx + 1]["start"] - 0.01)
    return grouped


def _build_rsvp_single_words(words: List[dict], highlights: Set[str]) -> List[dict]:
    """Strict 1-word-at-a-time RSVP captions (competitor style: @edukasikuy / @BrightsBrain)."""
    items: List[dict] = []
    for curr in words:
        raw = re.sub(r"[^\w\?\!\-]", "", (curr.get("word") or "").strip().upper())
        if not raw:
            continue
        start = float(curr["start"])
        end = max(float(curr["end"]), start + 0.16)
        is_hl = _is_highlight(raw, highlights)
        items.append(
            {
                "text": raw,
                "start": start,
                "end": end,
                "highlight": is_hl,
                "parts": [(raw, is_hl)],
            }
        )
    # Zero-gap hypnotic RSVP continuity: bridge small pauses (<0.28s) so no blank frames flash between words
    for idx in range(len(items) - 1):
        gap = items[idx + 1]["start"] - items[idx]["end"]
        if gap < 0.0:
            items[idx]["end"] = max(items[idx]["start"] + 0.08, items[idx + 1]["start"] - 0.01)
        elif gap <= 0.28:
            items[idx]["end"] = max(items[idx]["start"] + 0.08, items[idx + 1]["start"] - 0.01)
    return items


def generate_ass_subtitles(
    words: List[dict],
    scenes: List[dict],
    highlights: Iterable[str],
    visual: VisualStyle,
    output_path: Path,
    include_callouts: Optional[bool] = None,
    cta_window: tuple[float, float] | None = None,
    subject_label: str = "",
    suppress_rsvp_windows: Optional[List[Tuple[float, float]]] = None,
) -> Path:
    """Build a multi-layer ASS file supporting Squooshify 3D pure-white RSVP, blanking windows, & Reward Pop CTA."""
    hl_set = {h.upper() for h in highlights if h}
    mode = getattr(visual, "subtitle_mode", "squooshify_white_3d")
    is_squooshify = mode == "squooshify_white_3d"
    is_rsvp = is_squooshify or (mode == "rsvp_1word")
    cta_mode = getattr(visual, "cta_style", "heart_pill")

    if include_callouts is None:
        include_callouts = bool(getattr(visual, "include_top_callouts", not is_squooshify))

    primary = hex_to_ass_bgr("#FFFFFF" if is_squooshify else visual.primary_color_hex)
    highlight_bgr = hex_to_ass_bgr("#FFFFFF" if is_squooshify else visual.highlight_color_hex)
    outline = hex_to_ass_bgr(visual.outline_color_hex)
    shadow_bg = "&HA8000000&" if is_squooshify else "&H90000000&"
    callout_bg = "&H55101018&"
    yt_red_bg = "&H081818E6&"
    sticker_bg = "&H08B83868&"  # High-contrast deep purple/magenta pill for white bold text
    reward_bg = "&H08007FE6&"   # Warm vibrant amber/gold reward pill (#10 @Squooshify reward pop)

    kinetic_size = max(visual.font_size, 104 if is_rsvp else 92)
    margin_v = getattr(visual, "subtitle_margin_v", 440 if is_squooshify else (620 if is_rsvp else 450))
    outline_w = 6 if is_squooshify else 10
    shadow_d = 10 if is_squooshify else 5

    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {visual.width}",
        f"PlayResY: {visual.height}",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
        "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, "
        "Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Kinetic,{visual.font_name},{kinetic_size},{primary},&H000000FF&,{outline},{shadow_bg},"
        f"-1,0,0,0,100,100,1,0,1,{outline_w},{shadow_d},2,70,70,{margin_v},1",
        f"Style: Callout,{visual.font_name},50,{ hex_to_ass_bgr(visual.highlight_color_hex) },&H000000FF&,{callout_bg},{callout_bg},"
        f"-1,0,0,0,100,100,1,0,3,18,0,8,70,70,230,1",
        f"Style: CtaBadge,{visual.font_name},52,&H00FFFFFF&,&H000000FF&,{yt_red_bg},{yt_red_bg},"
        f"-1,0,0,0,100,100,2,0,3,22,0,5,70,70,0,1",
        f"Style: CtaSticker,{visual.font_name},50,&H00FFFFFF&,&H000000FF&,{sticker_bg},{sticker_bg},"
        f"-1,0,0,0,100,100,2,0,3,22,0,5,70,70,0,1",
        f"Style: CtaReward,{visual.font_name},54,&H00FFFFFF&,&H000000FF&,{reward_bg},{reward_bg},"
        f"-1,0,0,0,100,100,2,0,3,24,0,5,70,70,0,1",
        f"Style: CtaHeartWhite,Arial,40,&H00FFFFFF&,&H000000FF&,&H00000000&,&H90000000&,"
        f"0,0,0,0,100,100,0,0,1,8,6,5,0,0,0,1",
        f"Style: CtaHeartRed,Arial,40,&H00381EFF&,&H000000FF&,&H00FFFFFF&,&H90000000&,"
        f"0,0,0,0,100,100,0,0,1,8,6,5,0,0,0,1",
        f"Style: CtaCountRed,{visual.font_name},230,&H00261CE8&,&H000000FF&,&H00101010&,&HA0000000&,"
        f"-1,0,0,0,100,100,0,0,1,14,10,5,0,0,0,1",
        f"Style: CtaCountWhite,{visual.font_name},230,&H00FFFFFF&,&H000000FF&,&H00B03018&,&HA0000000&,"
        f"-1,0,0,0,100,100,0,0,1,14,10,5,0,0,0,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # v3.7 Blank Windows: suppress Layer 2 RSVP during 3-2-1 countdown & explicit showcase/payoff windows
    blank_wins: List[Tuple[float, float]] = list(suppress_rsvp_windows or [])
    if cta_window is not None and cta_mode == "gamified_321" and bool(getattr(visual, "suppress_rsvp_on_overlay", True)):
        c_s, c_e = cta_window
        c_dur = max(0.9, c_e - c_s)
        c_p1 = c_s + c_dur * 0.36
        blank_wins.append((round(c_p1 - 0.03, 3), round(c_e + 0.03, 3)))

    # Layer 2: Kinetic captions (with perceptual visual lead so caption snaps dead-on with spoken voice!)
    lead_s = max(0.0, float(getattr(visual, "caption_lead_s", 0.08)))
    raw_items = _build_rsvp_single_words(words, hl_set) if is_rsvp else _group_words_for_readability(words, hl_set)
    grouped = []
    for it in raw_items:
        it_copy = dict(it)
        it_copy["start"] = max(0.0, round(float(it["start"]) - lead_s, 3))
        it_copy["end"] = max(it_copy["start"] + 0.08, round(float(it["end"]) - lead_s, 3))
        grouped.append(it_copy)
    for idx in range(len(grouped) - 1):
        if grouped[idx]["end"] > grouped[idx + 1]["start"]:
            grouped[idx]["end"] = max(grouped[idx]["start"] + 0.06, round(grouped[idx + 1]["start"] - 0.005, 3))

    if blank_wins:
        filtered_grouped = []
        for it in grouped:
            st, en = float(it["start"]), float(it["end"])
            drop_item = False
            for bs, be in blank_wins:
                if st >= bs and en <= be:
                    drop_item = True
                    break
                if st < bs < en <= be:
                    en = round(bs - 0.01, 3)
                elif bs <= st < be < en:
                    st = round(be + 0.01, 3)
                elif st < bs and en > be:
                    en = round(bs - 0.01, 3)
            if not drop_item and (en - st) >= 0.07:
                it_clean = dict(it)
                it_clean["start"] = st
                it_clean["end"] = en
                filtered_grouped.append(it_clean)
        grouped = filtered_grouped

    for item in grouped:
        t_start = fmt_ass_time(item["start"])
        t_end = fmt_ass_time(item["end"])
        if is_squooshify:
            pop = (
                "{\\blur0.7\\fscx88\\fscy88\\t(0,30,\\fscx110\\fscy110)\\t(30,70,\\fscx103\\fscy103)}"
                if item["highlight"]
                else "{\\blur0.7\\fscx90\\fscy90\\t(0,28,\\fscx106\\fscy106)\\t(28,65,\\fscx100\\fscy100)}"
            )
        else:
            pop = (
                "{\\fscx124\\fscy124\\t(0,80,\\fscx104\\fscy104)}"
                if item["highlight"]
                else "{\\fscx114\\fscy114\\t(0,65,\\fscx100\\fscy100)}"
            )

        rendered_parts = []
        for p_text, p_hl in item["parts"]:
            safe = _escape_ass(p_text)
            if p_hl and not is_squooshify:
                rendered_parts.append(f"{{\\c{highlight_bgr}}}{safe}{{\\c{primary}}}")
            else:
                rendered_parts.append(safe)
        text_body = " ".join(rendered_parts)
        lines.append(f"Dialogue: 2,{t_start},{t_end},Kinetic,,0,0,0,,{pop}{text_body}")

    # Layer 1: Upper-third callout banners (disabled by default in Squooshify mode for clean top 80%)
    if include_callouts and scenes:
        callout_indices = {scenes[0]["index"], scenes[-1]["index"]}
        if len(scenes) >= 2:
            callout_indices.add(scenes[1]["index"])
        for sc in scenes:
            stext = _escape_ass(sc.get("screen_text", ""))
            if not stext or len(stext) > 36:
                continue
            if sc["index"] not in callout_indices and "!" not in stext and "?" not in stext:
                continue
            if "subscribe" in stext.lower():
                continue
            t_start = fmt_ass_time(sc["start_s"] + 0.05)
            t_end = fmt_ass_time(max(sc["start_s"] + 0.6, sc["end_s"] - 0.08))
            anim = "{\\fad(120,120)\\fscx92\\fscy92\\t(0,110,\\fscx100\\fscy100)}"
            lines.append(f"Dialogue: 1,{t_start},{t_end},Callout,,0,0,0,,{anim}{stext.upper()}")

    # Layer 3: Interactive Center-Screen CTA ("heart_pill", "gamified_321", OR v3.7 "reward_pop")
    if cta_window is not None:
        cta_s, cta_e = cta_window
        dur = max(0.9, cta_e - cta_s)
        cx = visual.width // 2
        cy_pill = int(visual.height * 0.56)
        cy_heart = int(visual.height * 0.39)
        heart_path = "m 0 32 b 0 0 -55 -20 -80 15 b -105 50 -75 95 0 155 b 75 95 105 50 80 15 b 55 -20 0 0 0 32"

        if cta_mode == "gamified_321":
            # Squooshify 3/5 Viral DNA: Like+Subscribe Pill -> Giant 3D Countdown 3 -> 2 -> 1
            p1_end = cta_s + dur * 0.36
            step_d = max(0.22, (cta_e - p1_end) / 3.0)
            bounce_p = f"{{\\pos({cx},{cy_pill})\\fad(50,60)\\fscx80\\fscy80\\t(0,65,\\fscx110\\fscy110)\\t(65,130,\\fscx100\\fscy100)}}"
            lines.append(
                f"Dialogue: 3,{fmt_ass_time(cta_s)},{fmt_ass_time(p1_end)},CtaBadge,,0,0,0,,{bounce_p}LIKE  +  SUBSCRIBE"
            )
            h_anim = f"{{\\an5\\pos({cx},{cy_heart})\\fad(50,50)\\fscx72\\fscy72\\t(0,80,\\fscx110\\fscy110)\\t(80,150,\\fscx100\\fscy100)\\p1}}{heart_path}{{\\p0}}"
            lines.append(
                f"Dialogue: 4,{fmt_ass_time(cta_s)},{fmt_ass_time(p1_end)},CtaHeartWhite,,0,0,0,,{h_anim}"
            )
            cy_num = int(visual.height * 0.46)
            for idx_n, (num_txt, sty) in enumerate([("3", "CtaCountRed"), ("2", "CtaCountWhite"), ("1", "CtaCountRed")]):
                ns = p1_end + idx_n * step_d
                ne = p1_end + (idx_n + 1) * step_d
                num_anim = (
                    f"{{\\an5\\pos({cx},{cy_num})\\fad(25,35)"
                    f"\\fscx58\\fscy58\\t(0,65,\\fscx124\\fscy124)\\t(65,140,\\fscx100\\fscy100)}}"
                )
                lines.append(
                    f"Dialogue: 4,{fmt_ass_time(ns)},{fmt_ass_time(ne)},{sty},,0,0,0,,{num_anim}{num_txt}"
                )
        elif cta_mode == "reward_pop":
            # Squooshify #10 (4nRVBdyV5pU) 3-Beat Reward Contract CTA:
            # Beat 1 (Subscribe + White Heart) -> Beat 2 (Red Heart Click) -> Beat 3 (Delayed Subject Reward Pop!)
            t1 = cta_s + dur * 0.38
            t2 = cta_s + dur * 0.66
            end_t = cta_e
            subj_up = _escape_ass(subject_label or "HEWAN").upper()
            b1 = f"{{\\pos({cx},{cy_pill})\\fad(50,60)\\fscx80\\fscy80\\t(0,70,\\fscx110\\fscy110)\\t(70,135,\\fscx100\\fscy100)}}"
            b2 = f"{{\\pos({cx},{cy_pill})\\fad(40,60)\\fscx84\\fscy84\\t(0,65,\\fscx112\\fscy112)\\t(65,130,\\fscx100\\fscy100)}}"
            b3 = f"{{\\pos({cx},{int(visual.height * 0.45)})\\fad(35,95)\\fscx62\\fscy62\\t(0,70,\\fscx120\\fscy120)\\t(70,150,\\fscx102\\fscy102)}}"
            lines.append(
                f"Dialogue: 3,{fmt_ass_time(cta_s)},{fmt_ass_time(t1)},CtaBadge,,0,0,0,,{b1}SUBSCRIBE  +  LIKE"
            )
            lines.append(
                f"Dialogue: 3,{fmt_ass_time(t1)},{fmt_ass_time(t2)},CtaSticker,,0,0,0,,{b2}+1 {subj_up} LIKE!"
            )
            lines.append(
                f"Dialogue: 4,{fmt_ass_time(t2)},{fmt_ass_time(end_t)},CtaReward,,0,0,0,,{b3}BONUS {subj_up} IMUT!"
            )
            if getattr(visual, "vector_heart_cta", True):
                h_anim1 = f"{{\\an5\\pos({cx},{cy_heart})\\fad(50,35)\\fscx72\\fscy72\\t(0,85,\\fscx108\\fscy108)\\t(85,160,\\fscx100\\fscy100)\\p1}}{heart_path}{{\\p0}}"
                h_anim2 = f"{{\\an5\\pos({cx},{cy_heart})\\fad(35,70)\\fscx92\\fscy92\\t(0,70,\\fscx130\\fscy130)\\t(70,150,\\fscx108\\fscy108)\\p1}}{heart_path}{{\\p0}}"
                lines.append(
                    f"Dialogue: 4,{fmt_ass_time(cta_s)},{fmt_ass_time(t1)},CtaHeartWhite,,0,0,0,,{h_anim1}"
                )
                lines.append(
                    f"Dialogue: 4,{fmt_ass_time(t1)},{fmt_ass_time(t2)},CtaHeartRed,,0,0,0,,{h_anim2}"
                )
        else:
            mid_t = cta_s + dur * 0.52
            end_t = cta_e
            bounce1 = f"{{\\pos({cx},{cy_pill})\\fad(60,80)\\fscx80\\fscy80\\t(0,75,\\fscx110\\fscy110)\\t(75,140,\\fscx100\\fscy100)}}"
            bounce2 = f"{{\\pos({cx},{cy_pill})\\fad(60,100)\\fscx80\\fscy80\\t(0,75,\\fscx110\\fscy110)\\t(75,140,\\fscx100\\fscy100)}}"
            lines.append(
                f"Dialogue: 3,{fmt_ass_time(cta_s)},{fmt_ass_time(mid_t)},CtaBadge,,0,0,0,,{bounce1}SUBSCRIBE  +  LIKE"
            )
            subj_up = _escape_ass(subject_label or "IMUT").upper()
            lines.append(
                f"Dialogue: 3,{fmt_ass_time(mid_t)},{fmt_ass_time(end_t)},CtaSticker,,0,0,0,,{bounce2}+1 {subj_up} LIKE!"
            )
            if getattr(visual, "vector_heart_cta", True):
                h_anim1 = f"{{\\an5\\pos({cx},{cy_heart})\\fad(60,40)\\fscx72\\fscy72\\t(0,90,\\fscx108\\fscy108)\\t(90,170,\\fscx100\\fscy100)\\p1}}{heart_path}{{\\p0}}"
                h_anim2 = f"{{\\an5\\pos({cx},{cy_heart})\\fad(40,120)\\fscx92\\fscy92\\t(0,75,\\fscx128\\fscy128)\\t(75,160,\\fscx108\\fscy108)\\p1}}{heart_path}{{\\p0}}"
                lines.append(
                    f"Dialogue: 4,{fmt_ass_time(cta_s)},{fmt_ass_time(mid_t)},CtaHeartWhite,,0,0,0,,{h_anim1}"
                )
                lines.append(
                    f"Dialogue: 4,{fmt_ass_time(mid_t)},{fmt_ass_time(end_t)},CtaHeartRed,,0,0,0,,{h_anim2}"
                )

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
