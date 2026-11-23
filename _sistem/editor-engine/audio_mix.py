"""Broadcast audio mixer: sidechain ducking, 0.5s hook audio, dynamic SFX, and -14 LUFS mastering."""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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
) -> List[Tuple[float, Path, float]]:
    """Compute dense, rotating, auto-calibrated micro-SFX + crisp subscribe CTA bell."""
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

    # 0b. Mandatory CTA Subscribe Bell synced to spoken "subscribe" word (plus UI click on red-heart beat)
    if sfx_dir.exists():
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

    # Select up to 13 dense, varied micro-SFX (>= 0.90s spacing, NEVER same file twice in a row!)
    candidates.sort(key=lambda x: (-x[3], x[0]))
    for t_s, sp, g, _ in candidates:
        if len(selected) >= 13:
            break
        if any(abs(t_s - ex_t) < 0.90 for ex_t, _, _ in selected):
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
) -> str:
    """Build broadcast-clean v3.4 FFmpeg audio graph: Presence EQ + post-hook BGM + calibrated SFX + hot LUFS."""
    parts: List[str] = []

    # 1. Clean Crispy Foreground VO
    vo_out = "[vo_clean]" if bgm_input_idx is not None else "[vo_mix]"
    use_presence = getattr(profile.visual, "vo_presence_eq", True)
    eq_chain = "equalizer=f=3600:t=q:w=1.2:g=3.0,treble=g=2.0:f=10000," if use_presence else ""
    parts.append(
        f"[{vo_input_idx}:a]highpass=f=85,{eq_chain}"
        f"acompressor=threshold=-22dB:ratio=3.5:attack=6:release=70:makeup=2.2,"
        f"volume=1.55,apad=pad_dur=0.6,atrim=0:{vo_duration:.3f},asetpts=PTS-STARTPTS{vo_out}"
    )
    if bgm_input_idx is not None:
        parts.append("[vo_clean]asplit=2[vo_mix][vo_sc]")

    mix_labels = ["[vo_mix]"]

    # 2. Balanced audible BGM (~14 dB below VO) starting AFTER the opening hook scene
    if bgm_input_idx is not None:
        bgm_st = max(0.0, float(bgm_start_s))
        bgm_play_dur = max(0.5, vo_duration - bgm_st)
        fade_out_st = max(bgm_st + 0.5, vo_duration - 0.25)
        drop_op = ""
        if bgm_drop_window is not None:
            ds, de = bgm_drop_window
            if de > ds + 0.15:
                drop_op = f",volume=enable='between(t,{ds:.3f},{de:.3f})':volume=0.35"

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
    target_i = profile.visual.target_lufs
    tp_ceil = -0.5 if target_i >= -11.5 else -1.0
    parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:dropout_transition=0:normalize=0,"
        f"loudnorm=I={target_i:.1f}:TP={tp_ceil:.1f}:LRA=3.0[aout]"
    )
    return ";".join(parts)
