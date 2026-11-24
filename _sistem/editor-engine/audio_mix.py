"""Broadcast audio mixer: sidechain ducking, 0.5s hook audio, dynamic SFX, and -14 LUFS mastering."""

from __future__ import annotations

import json
import math
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from core.config import StudioConfig
from core.models import ChannelProfile


def load_soundboard_catalog(sfx_dir: Path) -> Dict[str, dict]:
    """Load auto-measured _soundboard_catalog.json (exact dB gains & semantic roles)."""
    cat_path = sfx_dir / "_soundboard_catalog.json"
    if cat_path.exists():
        try:
            return json.loads(cat_path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


# Fallback specs if a file isn't in _soundboard_catalog.json yet
CALIBRATED_SFX_SPECS = {
    "signature-bell.mp3": (0.75, 0.85, ""),
    "transition-whoosh.mp3": (0.18, 0.25, "lowpass=f=2200,"),
    "clean-whoosh.mp3": (0.025, 0.24, "lowpass=f=2000,"),
    "clean-pop.mp3": (0.65, 0.22, ""),
    "pop_7e9Is8L.mp3": (0.65, 0.24, ""),
    "bubble-pop.mp3": (0.45, 0.24, ""),
    "bubble_ApR55VW.mp3": (0.24, 0.22, ""),
    "clean-ding.mp3": (0.80, 0.40, ""),
    "ui-click.mp3": (0.55, 0.26, ""),
    "correct.mp3": (0.35, 0.45, ""),
    "cartoon-boing.mp3": (0.35, 0.40, ""),
    "boing_lmke36X.mp3": (0.45, 0.40, ""),
    "vinyl-stop.mp3": (0.45, 0.42, ""),
    "record-scratch-2.mp3": (0.35, 0.42, ""),
    "wrong_5.mp3": (0.38, 0.50, ""),
    "magic-sparkle.mp3": (0.55, 0.55, ""),
    "meow-1.mp3": (0.42, 0.50, ""),
    "cute-meow.mp3": (0.40, 0.50, ""),
    "mac-quack.mp3": (0.42, 0.50, ""),
    "cartoon_bite_sound_effect.mp3": (0.45, 0.45, ""),
    "goofy-ahh-runnin.mp3": (0.24, 0.50, ""),
    "cartoon-slip.mp3": (0.26, 0.40, ""),
    "cartoonslip.mp3": (0.24, 0.22, ""),
    "vine-boom.mp3": (0.14, 0.55, ""),
    "sudden-suspense-sound-effect.mp3": (0.24, 0.55, ""),
    "huh_37bAoRo.mp3": (0.24, 0.50, ""),
    "question-7tv.mp3": (0.75, 0.65, ""),
    "bruh-sound-effect_WstdzdM.mp3": (0.34, 0.65, ""),
    "shocked-sound-effect.mp3": (0.18, 0.55, ""),
    "anime-wow-sound-effect.mp3": (0.35, 0.65, ""),
}

# Semantic role -> ordered preferred filenames (avoids harsh clean-whoosh unless only option)
ROLE_PREFERRED_FILES = {
    "opening_bell": ["signature-bell.mp3", "clean-ding.mp3"],
    "transition_swish": ["transition-whoosh.mp3", "piuw.mp3", "camera-flash-sound-effect.mp3", "clean-whoosh.mp3"],
    "pop_accent": ["pop_7e9Is8L.mp3", "bubble_ApR55VW.mp3", "bubble-pop.mp3", "comedy_pop_finger_in_mouth_001.mp3", "clean-pop.mp3"],
    "ui_click": ["ui-click.mp3", "correct.mp3", "cash-register-sound-fx.mp3"],
    "ding_chime": ["clean-ding.mp3", "ding-sound-effect_1.mp3"],
    "twist_scratch": ["vinyl-stop.mp3", "record-scratch-2.mp3"],
    "wrong_buzzer": ["wrong_5.mp3", "spongebob-boowomp.mp3"],
    "comedy_bruh": ["bruh.mp3", "bruh-sound-effect_WstdzdM.mp3"],
    "question_cue": ["huh_37bAoRo.mp3", "ceeday-huh-sound-effect.mp3", "question-7tv.mp3"],
    "comedy_boing": ["boing_lmke36X.mp3", "cartoon-boing.mp3", "squeak_Q72c7Tg.mp3", "maro-jump-sound-effect_1.mp3"],
    "action_run": ["cartoon-slip.mp3", "cartoonslip.mp3", "goofy-ahh-runnin.mp3", "cartoon-slide.mp3"],
    "action_bite": ["cartoon_bite_sound_effect.mp3", "nom-nom-nom_gPJiWn4.mp3"],
    "impact_boom": ["vine-boom.mp3", "sudden-suspense-sound-effect.mp3", "punch-gaming-sound-effect-hd_RzlG1GE.mp3", "shocked-sound-effect.mp3"],
    "riser_sparkle": ["magic-sparkle.mp3", "popular-riser-metallic-sound-effect.mp3"],
    "animal_cat": ["meow-1.mp3", "cute-meow.mp3"],
    "animal_duck": ["mac-quack.mp3"],
    "animal_dog": ["shivering-dog-meme.mp3"],
    "wow_reaction": ["anime-wow-sound-effect.mp3", "kid-iyaa.mp3"],
}

SFX_KEYWORD_MAP = {
    "question": "huh_37bAoRo.mp3",
    "tanya": "huh_37bAoRo.mp3",
    "whoosh": "transition-whoosh.mp3",
    "zip": "cartoon-slip.mp3",
    "kabur": "goofy-ahh-runnin.mp3",
    "lari": "goofy-ahh-runnin.mp3",
    "riser": "magic-sparkle.mp3",
    "sniff": "bubble_ApR55VW.mp3",
    "pop": "pop_7e9Is8L.mp3",
    "ding": "clean-ding.mp3",
    "subscribe": "ui-click.mp3",
    "like": "correct.mp3",
    "sting": "magic-sparkle.mp3",
    "buzzer": "wrong_5.mp3",
    "scratch": "vinyl-stop.mp3",
    "bruh": "bruh.mp3",
    "bukan": "wrong_5.mp3",
    "salah": "wrong_5.mp3",
    "negatif": "vinyl-stop.mp3",
    "boing": "boing_lmke36X.mp3",
    "kucing": "meow-1.mp3",
    "bebek": "mac-quack.mp3",
    "gigit": "cartoon_bite_sound_effect.mp3",
    "menerkam": "cartoon_bite_sound_effect.mp3",
    "shock": "vine-boom.mp3",
    "kaget": "vine-boom.mp3",
    "tension": "sudden-suspense-sound-effect.mp3",
    "adrenalin": "vine-boom.mp3",
    "wow": "anime-wow-sound-effect.mp3",
    "ajaib": "magic-sparkle.mp3",
    "tutup": "pop_7e9Is8L.mp3",
    "berhenti": "pop_7e9Is8L.mp3",
}


def db_to_linear(db: float) -> float:
    return round(math.pow(10.0, db / 20.0), 4)


def resolve_sfx_path(sfx_hint: str, sfx_dir: Path) -> Optional[Path]:
    if not sfx_hint or not sfx_dir.exists():
        return None
    low = sfx_hint.lower()
    for kw, fname in SFX_KEYWORD_MAP.items():
        if kw in low:
            p = sfx_dir / fname
            if p.exists():
                return p
    return None


def pick_bgm_file(bgm_dir: Path) -> Optional[Path]:
    if not bgm_dir.exists():
        return None
    cands = sorted(bgm_dir.glob("*.mp3"))
    if not cands:
        return None
    # Prefer lively, clearly audible quirky tracks over quiet elevator ambience
    for pref in ("sneaky-snitch", "monkeys-spinning-monkeys", "wii-music", "elevator"):
        for b in cands:
            if pref in b.name.lower():
                return b
    return cands[0]


def wants_original_hook_audio(scene1: dict) -> bool:
    sfx = (scene1.get("sfx") or "").lower()
    vis = (scene1.get("visual_desc") or "").lower()
    return any(k in (sfx + " " + vis) for k in ("audio asli", "chattering", "audio ", "suara asli", "bunyi asli"))


def compute_sfx_entries(
    scenes: List[dict],
    sfx_dir: Path,
    highlights: Set[str],
    cta_window: Optional[Tuple[float, float]] = None,
    cta_style: str = "heart_pill",
    subject_label: str = "",
    bgm_vacuum_windows: Optional[List[Tuple[float, float]]] = None,
    max_events: int = 6,
    min_spacing_s: float = 1.10,
) -> List[Tuple[float, Path, float]]:
    """Compute sparse, high-signal micro-SFX + CTA choreography + mid-shot foley re-anchors.

    v3.8: cap at 6 events with 1.10s refractory spacing (v3.7 packed 9-12 generic cues and buried
    the diegetic foley -- Squooshify uses only 5 semantic hits per video).
    """
    catalog = load_soundboard_catalog(sfx_dir)
    candidates: List[Tuple[float, Path, float, int]] = []  # (time, path, gain, priority)
    cue_words = {"subscribe", "bukan", "salah"} | {h.lower() for h in highlights if len(h) >= 4}

    def _gain_for(p: Path) -> float:
        if p.name in catalog:
            return float(catalog[p.name]["calibrated_gain"])
        return CALIBRATED_SFX_SPECS.get(p.name, (0.40, 0.45, ""))[0]

    def _pick_role(role: str, used_counts: Dict[str, int]) -> Optional[Path]:
        if not sfx_dir.exists():
            return None
        prefs = ROLE_PREFERRED_FILES.get(role, [])
        existing = [sfx_dir / fn for fn in prefs if (sfx_dir / fn).exists()]
        if not existing:
            return None
        # Rotate least-used file in this role so soundboard feels rich and varied!
        existing.sort(key=lambda p: used_counts.get(p.name, 0))
        return existing[0]

    used_counts: Dict[str, int] = {}

    # 0. Mandatory Iconic Channel Signature Opening Bell at t=0.00s
    selected: List[Tuple[float, Path, float]] = []
    op = _pick_role("opening_bell", used_counts)
    if op:
        selected.append((0.0, op, _gain_for(op)))
        used_counts[op.name] = 1

    # 0b. CTA Audio Choreography: Gamified 3-2-1, v3.7 Reward Pop (#10 Squooshify), OR Heart+Subscribe Bell
    if sfx_dir.exists() and cta_style == "gamified_321" and cta_window is not None:
        cta_s, cta_e = float(cta_window[0]), float(cta_window[1])
        dur = max(0.9, cta_e - cta_s)
        p1_end = cta_s + dur * 0.36
        step_d = max(0.22, (cta_e - p1_end) / 3.0)
        cue_plan = [
            (cta_s + 0.04, "ui_click"),
            (p1_end, "pop_accent"),
            (p1_end + step_d, "pop_accent"),
            (p1_end + 2 * step_d, "ding_chime"),
            (cta_e + 0.02, "impact_boom"),
        ]
        for ct, role in cue_plan:
            sp = _pick_role(role, used_counts)
            if sp is not None:
                selected.append((round(ct, 3), sp, _gain_for(sp)))
                used_counts[sp.name] = used_counts.get(sp.name, 0) + 1
    elif sfx_dir.exists() and cta_style == "reward_pop" and cta_window is not None:
        cta_s, cta_e = float(cta_window[0]), float(cta_window[1])
        dur = max(0.9, cta_e - cta_s)
        subj_low = (subject_label or "").lower()
        reward_role = (
            "animal_cat" if "kucing" in subj_low
            else ("animal_duck" if "bebek" in subj_low else ("animal_dog" if "anjing" in subj_low else "riser_sparkle"))
        )
        t_sub = round(cta_s + 0.05, 3)
        t_click = round(cta_s + dur * 0.38, 3)
        t_reward = round(cta_s + dur * 0.66, 3)
        for ct, role in [(t_sub, "ding_chime"), (t_click, "ui_click"), (t_reward, reward_role)]:
            sp = _pick_role(role, used_counts) or _pick_role("riser_sparkle", used_counts)
            if sp is not None and all(abs(ct - ex_t) >= 0.32 for ex_t, _, _ in selected):
                selected.append((ct, sp, _gain_for(sp)))
                used_counts[sp.name] = used_counts.get(sp.name, 0) + 1
    elif sfx_dir.exists():
        sub_bell = _pick_role("ding_chime", used_counts)
        sub_t = None
        for sc in scenes:
            for w in sc.get("words", []):
                tok = "".join(ch for ch in (w.get("word") or "").lower() if ch.isalnum())
                if tok == "subscribe":
                    sub_t = round(float(w["start"]), 3)
                    break
            if sub_t is not None:
                break
        if sub_t is None and cta_window is not None:
            sub_t = round(float(cta_window[0]) + 0.08, 3)
        if sub_t is not None and sub_bell is not None:
            selected.append((sub_t, sub_bell, _gain_for(sub_bell)))
            used_counts[sub_bell.name] = used_counts.get(sub_bell.name, 0) + 1
            if cta_window is not None:
                heart_click_t = round(float(cta_window[0]) + max(0.55, (float(cta_window[1]) - float(cta_window[0])) * 0.52), 3)
                ui_p = _pick_role("ui_click", used_counts)
                if ui_p is not None and abs(heart_click_t - sub_t) >= 0.45:
                    selected.append((heart_click_t, ui_p, _gain_for(ui_p)))
                    used_counts[ui_p.name] = used_counts.get(ui_p.name, 0) + 1

    # 1. Context-literal word-level soundboard triggers (maps spoken Indonesian words -> exact role!)
    word_role_map = {
        "kenapa": ("question_cue", 8),
        "bukan": ("wrong_buzzer", 10),
        "salah": ("wrong_buzzer", 10),
        "ngobrol": ("twist_scratch", 8),
        "subscribe": ("ding_chime", 10),
        "like": ("ding_chime", 9),
        "ternyata": ("riser_sparkle", 9),
        "adrenalin": ("impact_boom", 10),
        "terhalang": ("comedy_boing", 8),
        "menerkam": ("action_bite", 10),
        "rahangnya": ("action_bite", 9),
        "menghirup": ("pop_accent", 8),
        "kabur": ("action_run", 10),
        "lari": ("action_run", 10),
        "dorong": ("comedy_boing", 9),
        "jatuh": ("action_run", 9),
        "nendang": ("impact_boom", 9),
        "berhenti": ("pop_accent", 9),
        "kucing": ("animal_cat", 7),
        "bebek": ("animal_duck", 8),
        "makanya": ("riser_sparkle", 8),
        "komen": ("ui_click", 8),
    }
    if sfx_dir.exists():
        for sc in scenes:
            for w in sc.get("words", []):
                tok = "".join(ch for ch in (w.get("word") or "").lower() if ch.isalnum())
                if tok in word_role_map:
                    role, prio = word_role_map[tok]
                    sp = _pick_role(role, used_counts)
                    if sp:
                        candidates.append((round(float(w["start"]), 3), sp, _gain_for(sp), prio))

    # 1b. v3.7 Pre-Vacuum Vinyl Brake & Mid-Shot Foley Re-Anchor on Unsplit 1.0x Holds (>= 2.85s)
    if sfx_dir.exists():
        for v_idx, (vs, _ve) in enumerate(bgm_vacuum_windows or []):
            vac_role = "twist_scratch" if v_idx == 0 else "riser_sparkle"
            vp = _pick_role(vac_role, used_counts)
            if vp is not None:
                candidates.append((round(max(0.0, float(vs)), 3), vp, _gain_for(vp), 9))

        for sc in scenes:
            sc_role = str(sc.get("scene_role", "")).lower()
            sc_dur = float(sc.get("duration_s", float(sc.get("end_s", 0.0)) - float(sc.get("start_s", 0.0))))
            if sc_role in ("proof", "payoff") and not sc.get("is_intra_unit_cut") and sc_dur >= 2.85:
                mid_foley_t = round(float(sc.get("start_s", 0.0)) + sc_dur * 0.52, 3)
                fp = _pick_role("pop_accent" if sc_role == "proof" else "comedy_boing", used_counts)
                if fp is not None:
                    candidates.append((mid_foley_t, fp, _gain_for(fp), 9))

    # 2. Scene cut transitions (fill remaining cuts with varied pops/sparkles/silky swishes)
    fallback_roles = ["pop_accent", "transition_swish", "riser_sparkle", "comedy_boing"]
    for i, sc in enumerate(scenes):
        if i == 0:
            continue
        sfx_hint = sc.get("sfx", "")
        sfx_path = resolve_sfx_path(sfx_hint, sfx_dir) or _pick_role(fallback_roles[i % len(fallback_roles)], used_counts)
        if not sfx_path:
            continue
        trigger_s = float(sc["start_s"])
        for w in sc.get("words", []):
            tok = "".join(ch for ch in (w.get("word") or "").lower() if ch.isalnum())
            if tok in cue_words:
                trigger_s = float(w["start"])
                break
        prio = 6 if sfx_path.name != "clean-whoosh.mp3" else 3
        candidates.append((round(max(0.0, trigger_s), 3), sfx_path, _gain_for(sfx_path), prio))

    # Select up to `max_events` high-priority micro-SFX (>= min_spacing_s, NEVER same file twice in a row!)
    candidates.sort(key=lambda x: (-x[3], x[0]))
    for t_s, sp, g, _ in candidates:
        if len(selected) >= max(1, int(max_events)):
            break
        if any(abs(t_s - ex_t) < float(min_spacing_s) for ex_t, _, _ in selected):
            continue
        # Ensure variety: don't use the same SFX filename more than 2 times in 1 video
        if used_counts.get(sp.name, 0) >= 2:
            continue
        selected.append((t_s, sp, g))
        used_counts[sp.name] = used_counts.get(sp.name, 0) + 1

    selected.sort(key=lambda x: x[0])
    # Final anti-consecutive-repeat pass
    dedup: List[Tuple[float, Path, float]] = []
    for item in selected:
        if dedup and dedup[-1][1].name == item[1].name:
            continue
        dedup.append(item)
    return dedup


def build_audio_filtergraph(
    vo_input_idx: int,
    vo_duration: float,
    profile: ChannelProfile,
    bgm_input_idx: Optional[int],
    hook_audio_input_idx: Optional[int],
    sfx_inputs: List[Tuple[int, float, float]],
    hook_audio_start_s: float = 0.0,
    bgm_drop_window: Optional[Tuple[float, float]] = None,
    bgm_start_s: float = 0.0,
    bgm_mute_window: Optional[Tuple[float, float]] = None,
    bgm_vacuum_windows: Optional[List[Tuple[float, float]]] = None,
) -> str:
    """Build broadcast-clean v3.7 FFmpeg audio graph: Presence EQ + post-hook BGM + pre-pivot vacuum resets + SFX -3dB."""
    parts: List[str] = []

    # 1. Clean Crispy Foreground VO
    vo_out = "[vo_clean]" if bgm_input_idx is not None else "[vo_mix]"
    use_presence = getattr(profile.visual, "vo_presence_eq", True)
    eq_chain = "equalizer=f=3600:t=q:w=1.2:g=3.0,treble=g=2.0:f=10000," if use_presence else ""
    # v3.8 Hook Loudness Ramp: +2.7 dB at frame 0 fading to 0 by 1.5s so the first second
    # matches the program level (5/5 v3.7 hooks sat 2.6-7.0 dB below the body).
    hook_ramp = "volume=volume='if(lt(t,1.5),1.0+0.35*(1.5-t)/1.5,1.0)':eval=frame,"
    parts.append(
        f"[{vo_input_idx}:a]highpass=f=85,{eq_chain}"
        f"acompressor=threshold=-22dB:ratio=3.5:attack=6:release=70:makeup=2.2,"
        f"volume=1.55,{hook_ramp}apad=pad_dur=0.6,atrim=0:{vo_duration:.3f},asetpts=PTS-STARTPTS{vo_out}"
    )
    if bgm_input_idx is not None:
        parts.append("[vo_clean]asplit=2[vo_mix][vo_sc]")

    mix_labels = ["[vo_mix]"]

    # 2. Balanced audible BGM starting AFTER the opening hook scene (+ v3.7 pre-pivot -28dB vacuum windows)
    if bgm_input_idx is not None:
        bgm_st = max(0.0, float(bgm_start_s))
        bgm_play_dur = max(0.5, vo_duration - bgm_st)
        fade_out_st = max(bgm_st + 0.5, vo_duration - 0.25)
        drop_op = ""
        if bgm_drop_window is not None:
            ds, de = bgm_drop_window
            if de > ds + 0.15:
                drop_op += f",volume=enable='between(t,{ds:.3f},{de:.3f})':volume=0.35"
        for vs, ve in (bgm_vacuum_windows or []):
            if ve > vs + 0.12:
                # v3.8: duck to 12% instead of 4% -- the old near-silence made momentary LUFS crash to -16.4.
                drop_op += f",volume=enable='between(t,{vs:.3f},{ve:.3f})':volume=0.12"
        if bgm_mute_window is not None:
            ms, me = bgm_mute_window
            if me > ms + 0.25:
                drop_op += f",volume=enable='between(t,{ms:.3f},{me:.3f})':volume=0.10"

        delay_ms = int(round(bgm_st * 1000))
        delay_chain = f"adelay={delay_ms}|{delay_ms}," if delay_ms > 0 else ""

        # BGM level controlled by profile.visual.bgm_volume_db (-8.0 dB default -> 1.059x)
        bgm_db = float(getattr(profile.visual, "bgm_volume_db", -8.0))
        bgm_gain = round(max(0.15, min(1.35, math.pow(10.0, (bgm_db + 8.5) / 20.0))), 3)

        parts.append(
            f"[{bgm_input_idx}:a]atrim=0:{bgm_play_dur:.3f},"
            f"asetpts=PTS-STARTPTS,{delay_chain}highpass=f=150,volume={bgm_gain:.3f}{drop_op},"
            f"afade=t=in:st={bgm_st:.3f}:d=0.25,afade=t=out:st={fade_out_st:.3f}:d=0.25,"
            f"apad=whole_dur={vo_duration:.3f}[bgm_base]"
        )
        parts.append(
            "[bgm_base][vo_sc]sidechaincompress=threshold=0.08:ratio=2.4:attack=20:release=250:makeup=1[bgm_ducked]"
        )
        mix_labels.append("[bgm_ducked]")

    # 3. Verified authentic animal hook audio (only when explicitly enabled in golden_moments)
    if hook_audio_input_idx is not None:
        h_st = max(0.0, float(hook_audio_start_s))
        h_en = h_st + 1.2
        parts.append(
            f"[{hook_audio_input_idx}:a]atrim={h_st:.3f}:{h_en:.3f},asetpts=PTS-STARTPTS,"
            f"highpass=f=180,lowpass=f=4500,volume=0.45,afade=t=in:st=0:d=0.05,afade=t=out:st=0.80:d=0.40,"
            f"apad=whole_dur={vo_duration:.3f}[hook_snd]"
        )
        mix_labels.append("[hook_snd]")

    # 4. Catalog-calibrated micro-SFX (-3 dB bus ceiling): HPF 150Hz + gentle 3kHz carve + tight tail fade
    sfx_bus_db = float(getattr(profile.visual, "sfx_volume_db", -3.0))
    sfx_bus_scale = math.pow(10.0, (sfx_bus_db + 3.0) / 20.0)
    catalog_cache: Dict[str, dict] = {}
    for idx, item in enumerate(sfx_inputs):
        in_idx, trigger_s, gain = item[0], item[1], item[2]
        sfx_name = item[3] if len(item) > 3 else ""
        sfx_dir_opt = item[4] if len(item) > 4 else None
        if sfx_dir_opt and not catalog_cache:
            catalog_cache = load_soundboard_catalog(Path(sfx_dir_opt))
        if sfx_name in catalog_cache:
            max_d = float(catalog_cache[sfx_name].get("trim_max_s", 0.45))
            extra_f = str(catalog_cache[sfx_name].get("extra_filter", ""))
        else:
            _, max_d, extra_f = CALIBRATED_SFX_SPECS.get(sfx_name, (gain, 0.45, ""))
        eff_gain = round(max(0.02, min(1.80, gain * sfx_bus_scale)), 3)
        delay_ms = int(round(trigger_s * 1000))
        fade_st = max(0.04, max_d - 0.12)
        lbl = f"sfx_{idx}"
        parts.append(
            f"[{in_idx}:a]atrim=0:{max_d:.2f},asetpts=PTS-STARTPTS,"
            f"highpass=f=150,{extra_f}equalizer=f=3000:t=q:w=1.1:g=-2.5,volume={eff_gain:.3f},"
            f"afade=t=out:st={fade_st:.2f}:d=0.12,adelay={delay_ms}|{delay_ms}[{lbl}]"
        )
        mix_labels.append(f"[{lbl}]")

    # 5. Master mix + commercial loudness mastering
    # v3.8: TP=-1.0 dBFS and LRA=2.5 (v3.7's TP=-0.5 still shipped +0.6 dBFS on 008).
    # Single-pass loudnorm is only the first stage; builder runs a measured two-pass master afterwards.
    target_i = profile.visual.target_lufs
    tp_ceil = float(getattr(profile.visual, "master_true_peak_db", -1.0))
    parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I={target_i:.1f}:TP={tp_ceil:.1f}:LRA=2.5[aout]"
    )
    return ";".join(parts)


def measure_integrated_loudness(media_path: Path, cfg: StudioConfig) -> Dict[str, float]:
    """Measure EBU R128 integrated loudness / true peak of a finished media file."""
    proc = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(media_path),
            "-map",
            "0:a:0",
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=180,
    )
    i_vals = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", proc.stderr)
    tp_vals = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", proc.stderr)
    out: Dict[str, float] = {}
    if i_vals:
        out["integrated_lufs"] = float(i_vals[-1])
    if tp_vals:
        out["true_peak_dbfs"] = float(tp_vals[-1])
    return out


def _measure_wav_loudness(wav_path: Path, cfg: StudioConfig) -> Dict[str, float]:
    """Fast ebur128 measurement of a raw WAV (no container decode overhead)."""
    proc = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-hide_banner",
            "-nostats",
            "-i",
            str(wav_path),
            "-af",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    i_vals = re.findall(r"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", proc.stderr)
    tp_vals = re.findall(r"Peak:\s*(-?\d+(?:\.\d+)?)\s*dBFS", proc.stderr)
    out: Dict[str, float] = {}
    if i_vals:
        out["integrated_lufs"] = float(i_vals[-1])
    if tp_vals:
        out["true_peak_dbfs"] = float(tp_vals[-1])
    return out


def apply_mastering_pass(
    video_path: Path,
    profile: ChannelProfile,
    cfg: StudioConfig,
    tolerance_db: float = 0.30,
    max_gain_db: float = 6.0,
    max_passes: int = 4,
) -> Dict[str, object]:
    """Measured loudness master on the finished MP4, iterated until it lands on target.

    v3.8.1: a single linear gain cannot reach -10 LUFS when the pre-master true peak is already hot --
    the alimiter takes back ~1.5-2 dB of the added gain. So the audio is extracted to WAV once, a
    *cumulative* gain + limiter is re-evaluated against the ORIGINAL wav each pass (no stacked limiting),
    and the winning candidate is encoded to AAC exactly once and muxed back with `-c:v copy`.
    """
    target = float(profile.visual.target_lufs)
    tp_limit = float(getattr(profile.visual, "master_true_peak_db", -1.0))
    stem = video_path.stem
    src_wav = video_path.with_name(stem + "_master_src.wav")
    cand_wav = video_path.with_name(stem + "_master_cand.wav")
    tmp_path = video_path.with_name(stem + "_master_tmp.mp4")

    try:
        before = measure_integrated_loudness(video_path, cfg)
    except Exception as exc:
        return {"applied": False, "status": f"measure_failed: {str(exc)[:120]}"}
    if "integrated_lufs" not in before:
        return {"applied": False, "status": "no_audio_or_unreadable"}
    current = float(before["integrated_lufs"])
    tp_before = before.get("true_peak_dbfs")
    if abs(target - current) <= tolerance_db and (
        tp_before is None or float(tp_before) <= tp_limit + 0.10
    ):
        return {"applied": False, "status": "within_tolerance", "gain_db": 0.0, **before}

    try:
        subprocess.check_call(
            [cfg.ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(video_path),
             "-vn", "-c:a", "pcm_s16le", "-ar", "48000", str(src_wav)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        return {"applied": False, "status": f"audio_extract_failed: {str(exc)[:120]}", **before}

    passes = 0
    # True-peak-safe limiting: run the limiter at 192 kHz so the inter-sample peaks it has to
    # control are visible to it. At 48 kHz a flat-topped limited signal reconstructs ~2-3 dB
    # above its sample ceiling (measured: ceiling -1.0 -> true peak +1.9), which is why the
    # v3.8 single-pass master could not gain loudness without breaking the -1 dBTP rule.
    def _master_af(gain_db: float, ceil_lin: float) -> str:
        return (
            f"volume={gain_db:.2f}dB,aresample=192000,"
            f"alimiter=limit={ceil_lin:.4f}:attack=1:release=40:level=false,aresample=48000"
        )

    ceiling_db = tp_limit - 0.5  # headroom for the final AAC encode
    best = {"gain_db": 0.0, "integrated_lufs": current,
            "true_peak_dbfs": float(before.get("true_peak_dbfs", 0.0))}
    after: Dict[str, float] = {}
    try:
        for _round in range(2):
            ceil_lin = min(0.999, math.pow(10.0, ceiling_db / 20.0))
            best = {"gain_db": 0.0, "integrated_lufs": current,
                    "true_peak_dbfs": float(before.get("true_peak_dbfs", 0.0))}
            cumulative = 0.0
            for _ in range(max(1, max_passes)):
                err = target - float(best["integrated_lufs"])
                if abs(err) <= tolerance_db:
                    break
                step = max(-max_gain_db, min(max_gain_db, err))
                cumulative = round(cumulative + step, 2)
                subprocess.check_call(
                    [cfg.ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(src_wav),
                     "-af", _master_af(cumulative, ceil_lin), "-c:a", "pcm_s16le", str(cand_wav)],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                passes += 1
                got = _measure_wav_loudness(cand_wav, cfg)
                if "integrated_lufs" not in got:
                    break
                prev = float(best["integrated_lufs"])
                best = {"gain_db": cumulative, **got}
                if abs(float(got["integrated_lufs"]) - prev) < 0.10:
                    break  # limiter wall: the last candidate on disk is the best we have
            if abs(float(best["gain_db"])) < 0.05:
                return {"applied": False, "status": "limiter_wall", "passes": passes,
                        "gain_db": 0.0, **before}
            subprocess.check_call(
                [cfg.ffmpeg_cmd(), "-y", "-hide_banner", "-i", str(video_path), "-i", str(cand_wav),
                 "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(tmp_path)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            tmp_path.replace(video_path)
            try:
                after = measure_integrated_loudness(video_path, cfg)
            except Exception:
                after = {}
            tp_got = after.get("true_peak_dbfs")
            # AAC rebuilds inter-sample peaks on a hard-limited waveform: if the delivered file
            # still exceeds the ceiling, re-cut from the untouched source wav with a lower ceiling.
            if tp_got is None or float(tp_got) <= tp_limit + 0.10:
                break
            ceiling_db -= float(tp_got) - tp_limit + 0.25
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return {"applied": False, "status": f"encode_failed: {str(exc)[:120]}",
                "passes": passes, "gain_db": best["gain_db"], **before}
    finally:
        src_wav.unlink(missing_ok=True)
        cand_wav.unlink(missing_ok=True)

    final_err = abs(target - float(after.get("integrated_lufs", target)))
    if final_err <= tolerance_db:
        status = "mastered"
    elif float(after.get("integrated_lufs", target)) < target:
        status = "clamped_short"
    else:
        status = "clamped_hot"
    if float(after.get("true_peak_dbfs", tp_limit)) > tp_limit + 0.10:
        status += "_peak_over"
    return {
        "applied": True,
        "status": status,
        "passes": passes,
        "gain_db": best["gain_db"],
        "before": before,
        "after": after,
    }
