"""Broadcast-quality kinetic ASS subtitle & callout banner generator for YouTube Shorts."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, List, Set

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


def generate_ass_subtitles(
    words: List[dict],
    scenes: List[dict],
    highlights: Iterable[str],
    visual: VisualStyle,
    output_path: Path,
    include_callouts: bool = True,
    cta_window: tuple[float, float] | None = None,
) -> Path:
    """Build a multi-layer ASS file: Kinetic captions + Upper Callout Banners + CTA Subscribe Pill."""
    hl_set = {h.upper() for h in highlights if h}
    primary = hex_to_ass_bgr(visual.primary_color_hex)
    highlight_bgr = hex_to_ass_bgr(visual.highlight_color_hex)
    outline = hex_to_ass_bgr(visual.outline_color_hex)
    shadow_bg = "&H90000000&"
    callout_bg = "&H55101018&"  # Dark translucent pill backing
    yt_red_bg = "&H101818E6&"   # Crisp YouTube red pill backing (BGR: E6,18,18)

    kinetic_size = max(visual.font_size, 92)

    # Safe zone positioning for 1080x1920 YouTube Shorts (dead-center horizontally):
    # - Kinetic caption: Alignment=2 (bottom-center), MarginV=450
    # - CtaBadge: Alignment=2 (bottom-center), MarginV=595 (right above kinetic caption)
    # - Callout banner: Alignment=8 (top-center), MarginV=230 (below top Shorts UI)
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
        f"-1,0,0,0,100,100,1,0,1,10,4,2,70,70,450,1",
        f"Style: Callout,{visual.font_name},50,{highlight_bgr},&H000000FF&,{callout_bg},{callout_bg},"
        f"-1,0,0,0,100,100,1,0,3,18,0,8,70,70,230,1",
        f"Style: CtaBadge,{visual.font_name},48,&H00FFFFFF&,&H000000FF&,{yt_red_bg},{yt_red_bg},"
        f"-1,0,0,0,100,100,2,0,3,18,0,2,70,70,595,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    # Layer 2: Kinetic word-by-word captions
    grouped = _group_words_for_readability(words, hl_set)
    for item in grouped:
        t_start = fmt_ass_time(item["start"])
        t_end = fmt_ass_time(item["end"])
        pop = (
            "{\\fscx124\\fscy124\\t(0,80,\\fscx104\\fscy104)}"
            if item["highlight"]
            else "{\\fscx114\\fscy114\\t(0,65,\\fscx100\\fscy100)}"
        )

        rendered_parts = []
        for p_text, p_hl in item["parts"]:
            safe = _escape_ass(p_text)
            if p_hl:
                rendered_parts.append(f"{{\\c{highlight_bgr}}}{safe}{{\\c{primary}}}")
            else:
                rendered_parts.append(safe)
        text_body = " ".join(rendered_parts)
        lines.append(f"Dialogue: 2,{t_start},{t_end},Kinetic,,0,0,0,,{pop}{text_body}")

    # Layer 1: Upper-third callout banners on high-impact beats (Hook, Mitos, Payoff)
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

    # Layer 3: Crisp animated YouTube-style SUBSCRIBE pill during exact CTA window
    if cta_window is not None:
        cta_s, cta_e = cta_window
        t_start = fmt_ass_time(cta_s)
        t_end = fmt_ass_time(min(cta_s + 2.2, max(cta_s + 1.4, cta_e)))
        bounce = "{\\fad(100,180)\\fscx80\\fscy80\\t(0,110,\\fscx110\\fscy110)\\t(110,210,\\fscx100\\fscy100)}"
        lines.append(f"Dialogue: 3,{t_start},{t_end},CtaBadge,,0,0,0,,{bounce}SUBSCRIBE")

    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output_path
