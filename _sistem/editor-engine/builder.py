"""
Enterprise Autonomous Video Editor Engine for YouTube Shorts (v3.0)

Upgrades over v2.0:
1. Multi-channel aware (reads ChannelProfile from _sistem/channels/*.toml)
2. Fuzzy sequence word-to-scene alignment (drift-resistant, hyphen-safe)
3. Dynamic highlight & SFX trigger extraction (zero hardcoded episode words)
4. Hard cleanliness filter (blocks dirty/UI-heavy clips from cleanliness_report.json)
5. Multi-frame Vision sampling (25%/50%/75%) when mapping uncached clips
6. Cinematic FFmpeg render: dynamic zoom-punch, alternating hflip, speed shift,
   cohesive color grade + vignette, xfade transitions, Ken Burns photo support,
   safe-zone kinetic ASS captions + callout banners, green-screen Subscribe CTA
   chromakey overlay, 0.5s authentic hook audio, sidechain BGM ducking, and
   -14 LUFS YouTube broadcast loudness mastering.
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional

ENGINE_DIR = Path(__file__).resolve().parent
SISTEM_DIR = ENGINE_DIR.parent
for p in (str(SISTEM_DIR), str(ENGINE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from aligner import (
    apply_adaptive_proof_pacing,
    detect_cta_window,
    extract_dynamic_highlights,
    strip_audio_tags,
    synchronize_scenes_fuzzy,
)
from audio_mix import (
    build_audio_filtergraph,
    compute_sfx_entries,
    pick_bgm_file,
    resolve_sfx_path,
    wants_original_hook_audio,
)
from core.config import get_config, load_config
from core.models import ChannelProfile
from core.validator import extract_vo_section
from moment_finder import compute_scene_golden_moments
from subtitles import generate_ass_subtitles
from vo_tightener import tighten_vo_and_alignment
from video_fx import (
    PHOTO_EXTS,
    VIDEO_EXTS,
    concat_with_transitions,
    filter_clean_clips,
    find_subscribe_overlay,
    generate_qc_contact_sheet,
    get_cleanliness_map,
    get_v33_transition_spec,
    probe_media_info,
    render_cinematic_segment,
)

CFG = get_config()
BASE_CONTENT_DIR = CFG.channel_dir()
BGM_DIR = CFG.channel_bgm_dir()
SFX_DIR = CFG.channel_sfx_dir()
CAPCUT_DRAFTS_DIR = CFG.capcut_drafts_dir or Path("C:/Users/ronyt/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft")

TRANSITIONS_MAP = {
    "flash": {
        "name": "Flash Transition",
        "effect_id": "7613634637847481605",
        "path": str((CAPCUT_DRAFTS_DIR.parent.parent / "Cache/effect/7613634637847481605/a0201eb05f769628d1ff26d051e4043b")).replace("\\", "/"),
        "duration": 500000,
    },
    "pull_in": {
        "name": "Pull in",
        "effect_id": "6724226861666144779",
        "path": str((CAPCUT_DRAFTS_DIR.parent.parent / "Cache/effect/6724226861666144779/4d5a316f2eae582e7d0604b47feb8c32")).replace("\\", "/"),
        "duration": 466666,
    },
}

TEXT_TEMPLATE_POPUP = {
    "name": "逐词出现-白",
    "effect_id": "7331663443952438534",
    "third_resource_id": "7330906542717276673",
    "path": str((CAPCUT_DRAFTS_DIR.parent.parent / "Cache/effect/7331663443952438534/7ef11f12e8084e7c5293aba0a661e012")).replace("\\", "/"),
}


def get_local_ai_client():
    import openai

    return openai.OpenAI(base_url=CFG.ai_base_url, api_key=CFG.ai_api_key or "local")


def get_media_duration(file_path: Path | str) -> float:
    return probe_media_info(Path(file_path), CFG)["duration"]


def parse_brief(brief_path: Path) -> List[dict]:
    content = brief_path.read_text(encoding="utf-8")
    rows: List[dict] = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if not parts or not parts[0].isdigit():
            continue

        sec_str = parts[1].replace(",", ".")
        m = re.findall(r"(\d+(?:\.\d+)?)", sec_str)
        if len(m) >= 2:
            start_s, end_s = float(m[0]), float(m[1])
        else:
            continue

        vo_raw = parts[3] if len(parts) > 3 else ""
        clean_vo = strip_audio_tags(vo_raw).replace('"', "").strip()

        rows.append(
            {
                "index": int(parts[0]),
                "brief_start_s": start_s,
                "brief_end_s": end_s,
                "visual_desc": parts[2] if len(parts) > 2 else "",
                "vo_raw": vo_raw,
                "vo": clean_vo,
                "screen_text": parts[4] if len(parts) > 4 else "",
                "sfx": parts[5] if len(parts) > 5 else "",
            }
        )
    return rows


def align_voiceover_words(assets_dir: Path, vo_file: Path) -> List[dict]:
    cache_file = assets_dir / "words_alignment.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    print("[*] Transcribing VO with word-level timestamps via local AI...")
    client = get_local_ai_client()
    audio_b64 = base64.b64encode(vo_file.read_bytes()).decode("utf-8")

    prompt = """Dengarkan audio voiceover Bahasa Indonesia ini dengan sangat teliti.
Buat transkrip SETIAP KATA (word-by-word) dengan timestamp mulai dan selesai yang sangat presisi dalam detik (desimal).
Format output WAJIB HANYA JSON array valid tanpa teks markdown pembuka/penutup:
[
  {"word": "Kenapa", "start": 0.08, "end": 0.44}
]"""

    res = client.chat.completions.create(
        model=CFG.ai_audio_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}},
                ],
            }
        ],
    )

    raw = (res.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)

    words = json.loads(raw)
    cache_file.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Extracted {len(words)} words with precision timestamps.")
    return words


def _describe_clip_multiframe(clip_p: Path, assets_dir: Path, idx: int, client) -> str:
    """Inspect 3 frames (25%, 50%, 75%) of a video clip via Vision AI for accurate action + cleanliness."""
    if clip_p.suffix.lower() in PHOTO_EXTS:
        b64 = base64.b64encode(clip_p.read_bytes()).decode("utf-8")
        res = client.chat.completions.create(
            model=CFG.ai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "Sebutkan subjek, aksi, dan apakah ada teks/watermark di gambar ini dalam 1 kalimat pendek."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
        )
        return (res.choices[0].message.content or "Photo clip").strip()

    dur = get_media_duration(clip_p)
    ratios = (0.25, 0.50, 0.75) if dur >= 2.0 else (0.50,)
    img_blocks = []
    temp_thumbs = []
    for r_idx, ratio in enumerate(ratios):
        thumb_p = assets_dir / f"_temp_thumb_{idx:02d}_{r_idx}.jpg"
        subprocess.run(
            [CFG.ffmpeg_cmd(), "-y", "-ss", f"{dur * ratio:.2f}", "-i", str(clip_p), "-vframes", "1", "-vf", "scale=360:-2", str(thumb_p)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if thumb_p.exists():
            temp_thumbs.append(thumb_p)
            b64 = base64.b64encode(thumb_p.read_bytes()).decode("utf-8")
            img_blocks.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

    desc = "Video clip"
    if img_blocks:
        try:
            res = client.chat.completions.create(
                model=CFG.ai_vision_model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "Dari 3 frame video ini, sebutkan subjek, aksi utama, dan apakah bersih dari teks/UI sosmed dalam 1 kalimat pendek.",
                            },
                            *img_blocks,
                        ],
                    }
                ],
            )
            desc = (res.choices[0].message.content or desc).strip()
        finally:
            for tp in temp_thumbs:
                tp.unlink(missing_ok=True)
    return desc


def match_scenes_to_clips(
    assets_dir: Path,
    scenes: List[dict],
    clips: List[Path],
    profile: ChannelProfile,
) -> Dict[int, Path]:
    clean_clips, excluded = filter_clean_clips(assets_dir, clips, profile.ypp.min_cleanliness_score)
    if excluded:
        print(f"[!] Cleanliness filter excluded {len(excluded)} dirty clip(s): {', '.join(excluded)}")

    clean_names = {c.name for c in clean_clips}
    cache_file = assets_dir / "scene_clip_mapping.json"
    if cache_file.exists():
        try:
            mapping = json.loads(cache_file.read_text(encoding="utf-8"))
            if mapping and all((assets_dir / m["filename"]).exists() and m["filename"] in clean_names for m in mapping):
                return {int(m["scene_index"]): assets_dir / m["filename"] for m in mapping}
        except Exception:
            pass

    print("[*] Running Multi-Frame Vision AI to match scene briefs with clean clips...")
    client = get_local_ai_client()
    clean_meta = get_cleanliness_map(assets_dir)

    clip_descriptions = []
    for i, clip_p in enumerate(clean_clips):
        if clip_p.name in clean_meta and clean_meta[clip_p.name].get("subject_action"):
            desc = f"{clean_meta[clip_p.name]['subject_action']} (status: {clean_meta[clip_p.name].get('status', 'OK')})"
        else:
            desc = _describe_clip_multiframe(clip_p, assets_dir, i, client)
        clip_descriptions.append({"index": i, "filename": clip_p.name, "desc": desc})

    scene_prompts = [{"scene_index": sc["index"], "desc": f"{sc['visual_desc']} | VO: {sc['vo']}"} for sc in scenes]
    match_prompt = f"""Kamu adalah sutradara video editor YouTube Shorts kelas dunia.
Berikut daftar adegan yang dibutuhkan naskah:
{json.dumps(scene_prompts, indent=2, ensure_ascii=False)}

Berikut daftar klip video BERSIH yang tersedia di folder assets:
{json.dumps(clip_descriptions, indent=2, ensure_ascii=False)}

ATURAN:
1. Tugaskan 1 klip yang PALING COCOK secara visual dan aksi untuk setiap adegan.
2. Usahakan memaksimalkan variasi klip (hindari memakai klip yang sama berturut-turut).
Format output WAJIB HANYA JSON array:
[
  {{"scene_index": 1, "filename": "nama_file.mp4"}}
]"""

    res = client.chat.completions.create(
        model=CFG.ai_vision_model,
        messages=[{"role": "user", "content": match_prompt}],
    )
    raw = (res.choices[0].message.content or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)

    mapping_list = json.loads(raw)
    cache_file.write_text(json.dumps(mapping_list, indent=2, ensure_ascii=False), encoding="utf-8")

    result: Dict[int, Path] = {}
    for m in mapping_list:
        p = assets_dir / m["filename"]
        result[int(m["scene_index"])] = p if (p.exists() and p.name in clean_names) else clean_clips[0]
    return result


def _load_episode_context(package_name: str, channel_id: Optional[str] = None):
    global CFG
    CFG = load_config()
    profile = CFG.get_channel(channel_id)
    pkg_dir = CFG.episode_dir(package_name, profile.channel_id)
    brief_file = pkg_dir / "brief-edit.md"
    if not brief_file.exists():
        raise FileNotFoundError(f"brief-edit.md tidak ditemukan di {pkg_dir}")

    raw_scenes = parse_brief(brief_file)
    if not raw_scenes:
        raise ValueError(f"Tabel shot-list di {brief_file} kosong atau formatnya tidak terbaca.")

    assets_dir = CFG.episode_assets_dir(package_name, profile.channel_id)
    vo_files = list(assets_dir.glob("*.mp3")) if assets_dir.exists() else []
    if not vo_files:
        raise FileNotFoundError(f"No voiceover mp3 found in {assets_dir}")
    vo_file = max(vo_files, key=lambda f: f.stat().st_mtime)
    vo_duration = get_media_duration(vo_file)

    all_media = (
        sorted(p for p in assets_dir.iterdir() if p.is_file() and p.suffix.lower() in (VIDEO_EXTS | PHOTO_EXTS) and not p.name.startswith(("thumb_", "_temp_")))
        if assets_dir.exists()
        else []
    )
    if not all_media:
        raise FileNotFoundError(f"No video/photo clips found in {assets_dir}")

    naskah_file = pkg_dir / "naskah.md"
    naskah_raw = naskah_file.read_text(encoding="utf-8", errors="ignore") if naskah_file.exists() else ""
    naskah_vo_only = extract_vo_section(naskah_raw) if naskah_raw else ""

    clean_clips, _ = filter_clean_clips(assets_dir, all_media, profile.ypp.min_cleanliness_score)
    words = align_voiceover_words(assets_dir, vo_file)
    clip_mapping = match_scenes_to_clips(assets_dir, raw_scenes, all_media, profile)
    scenes = synchronize_scenes_fuzzy(raw_scenes, words, vo_duration)
    highlights = extract_dynamic_highlights(scenes, naskah_vo_only)
    cta_window = detect_cta_window(scenes, words)

    return {
        "profile": profile,
        "pkg_dir": pkg_dir,
        "assets_dir": assets_dir,
        "vo_file": vo_file,
        "vo_duration": vo_duration,
        "clips": clean_clips,
        "words": words,
        "clip_mapping": clip_mapping,
        "scenes": scenes,
        "highlights": highlights,
        "cta_window": cta_window,
    }


def _hex_to_rgb_floats(hex_color: str) -> List[float]:
    c = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(c) != 6:
        return [1.0, 1.0, 1.0]
    return [round(int(c[i : i + 2], 16) / 255.0, 3) for i in (0, 2, 4)]


def build_capcut_project(
    package_name: str,
    draft_id_name: Optional[str] = None,
    channel_id: Optional[str] = None,
) -> str:
    ctx = _load_episode_context(package_name, channel_id)
    profile: ChannelProfile = ctx["profile"]
    vo_file: Path = ctx["vo_file"]
    vo_duration: float = ctx["vo_duration"]
    clips: List[Path] = ctx["clips"]
    words: List[dict] = ctx["words"]
    clip_mapping: Dict[int, Path] = ctx["clip_mapping"]
    scenes: List[dict] = ctx["scenes"]
    highlights = ctx["highlights"]

    bgm_file = pick_bgm_file(CFG.channel_bgm_dir(profile.channel_id))
    sfx_dir = CFG.channel_sfx_dir(profile.channel_id)

    if not draft_id_name:
        draft_id_name = f"auto_{profile.channel_id}_{package_name}_{int(time.time())}"

    draft_folder = CAPCUT_DRAFTS_DIR / draft_id_name
    draft_folder.mkdir(parents=True, exist_ok=True)

    total_us = int(vo_duration * 1_000_000)
    now_ts = int(time.time())
    hl_rgb = _hex_to_rgb_floats(profile.visual.highlight_color_hex)
    prim_rgb = _hex_to_rgb_floats(profile.visual.primary_color_hex)

    draft_content = {
        "id": str(uuid.uuid4()).upper(),
        "version": 360000,
        "new_version": "12.0.0",
        "name": draft_id_name,
        "duration": total_us,
        "create_time": now_ts,
        "update_time": now_ts,
        "fps": float(profile.visual.fps),
        "is_drop_frame_timecode": False,
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "maintrack_adsorb": True,
            "material_save_mode": 0,
            "multi_language_current": "none",
            "multi_language_list": [],
            "multi_language_main": "none",
            "multi_language_mode": "none",
            "original_sound_last_index": 1,
            "record_audio_last_index": 1,
            "sticker_max_index": 1,
            "subtitle_keywords_config": None,
            "subtitle_recognition_id": "",
            "subtitle_sync": True,
            "subtitle_taskinfo": [],
            "system_font_list": [],
            "video_mute": False,
            "zoom_info_params": None,
        },
        "canvas_config": {
            "ratio": "9:16",
            "width": profile.visual.width,
            "height": profile.visual.height,
            "background": None,
        },
        "tracks": [],
        "materials": {
            "videos": [],
            "audios": [],
            "texts": [],
            "transitions": [],
            "text_templates": [],
            "speeds": [],
            "canvases": [],
            "material_colors": [],
            "audio_fades": [],
            "beats": [],
            "sound_channel_mappings": [],
        },
    }

    # 1. Main Video Track
    track_video = {"id": str(uuid.uuid4()).upper(), "type": "video", "flag": 0, "segments": []}
    curr_target_start_us = 0
    for i, sc in enumerate(scenes):
        sc_dur_us = int(sc["duration_s"] * 1_000_000)
        clip_path = clip_mapping.get(sc["index"], clips[i % len(clips)])
        clip_dur_us = int(get_media_duration(clip_path) * 1_000_000)
        source_start_us = max(0, (clip_dur_us - sc_dur_us) // 2) if clip_dur_us > sc_dur_us else 0

        mat_video_id = str(uuid.uuid4()).upper()
        speed_id = str(uuid.uuid4()).upper()
        draft_content["materials"]["videos"].append(
            {
                "id": mat_video_id,
                "type": "video",
                "material_name": clip_path.name,
                "path": str(clip_path).replace("\\", "/"),
                "duration": clip_dur_us,
                "width": profile.visual.width,
                "height": profile.visual.height,
                "extra_info": clip_path.name,
            }
        )
        draft_content["materials"]["speeds"].append({"id": speed_id, "speed": 1.05, "mode": 0})
        seg = {
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_video_id,
            "source_timerange": {"start": source_start_us, "duration": sc_dur_us},
            "target_timerange": {"start": curr_target_start_us, "duration": sc_dur_us},
            "speed_id": speed_id,
            "volume": 0.0,
        }
        if i > 0:
            trans_spec = TRANSITIONS_MAP["flash" if i % 2 == 1 else "pull_in"]
            trans_id = str(uuid.uuid4()).upper()
            draft_content["materials"]["transitions"].append(
                {
                    "id": trans_id,
                    "type": "transition",
                    "name": trans_spec["name"],
                    "effect_id": trans_spec["effect_id"],
                    "resource_id": trans_spec["effect_id"],
                    "path": trans_spec["path"],
                    "duration": trans_spec["duration"],
                    "is_overlap": True,
                    "platform": "all",
                }
            )
            seg["extra_material_refs"] = [trans_id]
        track_video["segments"].append(seg)
        curr_target_start_us += sc_dur_us
    draft_content["tracks"].append(track_video)

    # 2. Kinetic Subtitle Track (Dynamic Highlights)
    track_text = {"id": str(uuid.uuid4()).upper(), "type": "text", "flag": 1, "segments": []}
    for w in words:
        w_text = (w.get("word") or "").strip().upper()
        if not w_text:
            continue
        w_start_us = int(float(w["start"]) * 1_000_000)
        w_dur_us = max(180000, int((float(w["end"]) - float(w["start"])) * 1_000_000))
        mat_text_id = str(uuid.uuid4()).upper()
        tpl_id = str(uuid.uuid4()).upper()
        draft_content["materials"]["text_templates"].append(
            {
                "id": tpl_id,
                "version": "1.0.0",
                "effect_id": TEXT_TEMPLATE_POPUP["effect_id"],
                "resource_id": TEXT_TEMPLATE_POPUP["effect_id"],
                "third_resource_id": TEXT_TEMPLATE_POPUP["third_resource_id"],
                "name": TEXT_TEMPLATE_POPUP["name"],
                "type": "text_template_subtitle",
                "path": TEXT_TEMPLATE_POPUP["path"],
            }
        )
        clean_w = re.sub(r"[^\w\-]", "", w_text)
        is_hl = clean_w in highlights or any(h in clean_w for h in highlights if len(h) >= 4)
        fill_color = hl_rgb if is_hl else prim_rgb
        text_content_obj = {
            "text": w_text,
            "styles": [
                {
                    "fill": {"content": {"render_type": "solid", "solid": {"color": fill_color}}},
                    "strokes": [{"content": {"render_type": "solid", "solid": {"color": [0.0, 0.0, 0.0]}}, "width": 14.0}],
                }
            ],
        }
        draft_content["materials"]["texts"].append(
            {"id": mat_text_id, "type": "text", "content": json.dumps(text_content_obj), "recognize_text": w_text}
        )
        track_text["segments"].append(
            {
                "id": str(uuid.uuid4()).upper(),
                "material_id": mat_text_id,
                "source_timerange": {"start": 0, "duration": w_dur_us},
                "target_timerange": {"start": w_start_us, "duration": w_dur_us},
                "extra_material_refs": [tpl_id],
            }
        )
    draft_content["tracks"].append(track_text)

    # 3. VO Audio Track
    track_vo = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
    mat_vo_id = str(uuid.uuid4()).upper()
    draft_content["materials"]["audios"].append(
        {
            "id": mat_vo_id,
            "type": "music",
            "name": vo_file.name,
            "path": str(vo_file).replace("\\", "/"),
            "duration": total_us,
            "extra_info": vo_file.name,
        }
    )
    track_vo["segments"].append(
        {
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_vo_id,
            "source_timerange": {"start": 0, "duration": total_us},
            "target_timerange": {"start": 0, "duration": total_us},
            "volume": 1.0,
        }
    )
    draft_content["tracks"].append(track_vo)

    # 4. BGM Track
    if bgm_file and bgm_file.exists():
        track_bgm = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
        mat_bgm_id = str(uuid.uuid4()).upper()
        bgm_dur_us = int(get_media_duration(bgm_file) * 1_000_000)
        draft_content["materials"]["audios"].append(
            {
                "id": mat_bgm_id,
                "type": "music",
                "name": bgm_file.name,
                "path": str(bgm_file).replace("\\", "/"),
                "duration": bgm_dur_us,
                "extra_info": bgm_file.name,
            }
        )
        track_bgm["segments"].append(
            {
                "id": str(uuid.uuid4()).upper(),
                "material_id": mat_bgm_id,
                "source_timerange": {"start": 0, "duration": min(total_us, bgm_dur_us)},
                "target_timerange": {"start": 0, "duration": min(total_us, bgm_dur_us)},
                "volume": 0.08,
            }
        )
        draft_content["tracks"].append(track_bgm)

    # 5. Dynamic SFX Track
    track_sfx = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
    for trigger_s, sfx_path, gain in compute_sfx_entries(scenes, sfx_dir, highlights):
        sfx_dur_us = int(get_media_duration(sfx_path) * 1_000_000)
        mat_sfx_id = str(uuid.uuid4()).upper()
        draft_content["materials"]["audios"].append(
            {
                "id": mat_sfx_id,
                "type": "sound_effect",
                "name": sfx_path.name,
                "path": str(sfx_path).replace("\\", "/"),
                "duration": sfx_dur_us,
                "extra_info": sfx_path.name,
            }
        )
        start_us = int(trigger_s * 1_000_000)
        play_dur = min(1_600_000, sfx_dur_us)
        track_sfx["segments"].append(
            {
                "id": str(uuid.uuid4()).upper(),
                "material_id": mat_sfx_id,
                "source_timerange": {"start": 0, "duration": play_dur},
                "target_timerange": {"start": start_us, "duration": play_dur},
                "volume": gain,
            }
        )
    if track_sfx["segments"]:
        draft_content["tracks"].append(track_sfx)

    (draft_folder / "draft_content.json").write_text(json.dumps(draft_content, indent=2, ensure_ascii=False), encoding="utf-8")
    meta_info = {
        "draft_id": str(uuid.uuid4()).upper(),
        "draft_name": draft_id_name,
        "draft_fold_path": str(draft_folder).replace("\\", "/"),
        "draft_root_path": str(CAPCUT_DRAFTS_DIR).replace("\\", "/"),
        "tm_duration": total_us,
        "tm_draft_create": now_ts * 1_000_000,
        "tm_draft_modified": now_ts * 1_000_000,
    }
    (draft_folder / "draft_meta_info.json").write_text(json.dumps(meta_info, indent=2), encoding="utf-8")

    root_meta_file = CAPCUT_DRAFTS_DIR / "root_meta_info.json"
    if root_meta_file.exists():
        try:
            root_data = json.loads(root_meta_file.read_text(encoding="utf-8"))
            all_drafts = root_data.setdefault("all_draft_store", [])
            all_drafts = [d for d in all_drafts if d.get("draft_name") != draft_id_name]
            all_drafts.insert(
                0,
                {
                    "draft_id": meta_info["draft_id"],
                    "draft_name": draft_id_name,
                    "draft_root_path": str(CAPCUT_DRAFTS_DIR).replace("\\", "/"),
                    "draft_fold_path": str(draft_folder).replace("\\", "/"),
                },
            )
            root_data["all_draft_store"] = all_drafts
            root_meta_file.write_text(json.dumps(root_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] Failed to update root_meta_info: {e}")

    return str(draft_folder)


def render_ffmpeg_direct(
    package_name: str,
    output_path: Optional[str] = None,
    channel_id: Optional[str] = None,
    enable_transitions: bool = True,
    enable_cta_overlay: bool = True,
    make_qc_sheet: bool = True,
    enforce_preflight: bool = False,
    progress_cb=None,
    cta_style_override: Optional[str] = None,
) -> str:
    def _prog(pct: int, msg: str) -> None:
        if progress_cb:
            try:
                progress_cb(pct, msg)
            except Exception:
                pass

    _prog(5, "Menjalankan pemeriksaan YPP Pre-Flight Gate...")
    if enforce_preflight:
        from sourcing.preflight import run_preflight_gate

        pf = run_preflight_gate(package_name, channel_id, run_autoqc=False, config=CFG, write_report=True)
        if not pf.can_render:
            fails = [f"{c.code}: {c.message}" for c in pf.checks if c.severity == "FAIL"]
            raise RuntimeError("YPP Pre-Flight Gate BLOCKED render:\n- " + "\n- ".join(fails))

    _prog(14, "Memuat aset, alignment kata VO, dan pemetaan adegan...")
    ctx = _load_episode_context(package_name, channel_id)
    profile: ChannelProfile = ctx["profile"]
    pkg_dir: Path = ctx["pkg_dir"]
    assets_dir: Path = ctx["assets_dir"]
    vo_file: Path = ctx["vo_file"]
    vo_duration: float = ctx["vo_duration"]
    clips: List[Path] = ctx["clips"]
    words: List[dict] = ctx["words"]
    clip_mapping: Dict[int, Path] = ctx["clip_mapping"]
    scenes: List[dict] = ctx["scenes"]
    highlights = ctx["highlights"]
    cta_window = ctx["cta_window"]

    bgm_file = pick_bgm_file(CFG.channel_bgm_dir(profile.channel_id))
    sfx_dir = CFG.channel_sfx_dir(profile.channel_id)
    cat_file = sfx_dir / "_soundboard_catalog.json"
    if sfx_dir.exists():
        need_recat = not cat_file.exists()
        if not need_recat:
            try:
                _cat_tmp = json.loads(cat_file.read_text(encoding="utf-8"))
                _ob = _cat_tmp.get("signature-bell.mp3")
                if not _ob or float(_ob.get("target_rms_db", -99)) < -18.0:
                    need_recat = True
            except Exception:
                need_recat = True
        if need_recat:
            from sourcing.soundboard import build_soundboard_catalog

            build_soundboard_catalog(profile.channel_id, CFG, write_file=True)
    clean_meta = get_cleanliness_map(assets_dir)

    out_dir = pkg_dir / "output"
    out_dir.mkdir(exist_ok=True)
    if not output_path:
        output_path = str(out_dir / f"final_{package_name}.mp4")

    temp_dir = out_dir / "_temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)
    temp_dir.mkdir(exist_ok=True)

    # 0. Acoustic Zero-Gap VO Tightening (cuts real silence < vo_silence_db on raw VO & remaps word timings!)
    vo_silence_db = float(getattr(profile.visual, "vo_silence_db", -32.0))
    vo_min_silence_s = float(getattr(profile.visual, "vo_min_silence_s", 0.10))
    if getattr(profile.visual, "vo_zero_gap", True) and vo_file.exists():
        _prog(18, f"Memotong kantong hening VO (< {vo_silence_db:.1f} dB) & sinkronisasi WAV...")
        tight_vo_path = temp_dir / "_vo_tightened.wav"
        vo_file, words, vo_duration = tighten_vo_and_alignment(
            vo_file=vo_file,
            words=words,
            out_mp3=tight_vo_path,
            cfg=CFG,
            max_pause_s=vo_min_silence_s,
            tempo_boost=float(getattr(profile.visual, "vo_tempo_boost", 1.07)),
            silence_db=vo_silence_db,
        )
        if words:
            scenes = synchronize_scenes_fuzzy(scenes, words, vo_duration)
            cta_window = detect_cta_window(scenes, words)

    # 1. Locate Golden Moments + apply Squooshify Adaptive Proof Shot Pacing (holds uninterrupted proof shots 3.0-4.4s)
    _prog(22, "Memindai Golden Moments & menerapkan Adaptive Proof Shot Pacing...")
    golden_moments = compute_scene_golden_moments(assets_dir, scenes, clip_mapping, CFG)
    if getattr(profile.visual, "adaptive_proof_pacing", True):
        scenes = apply_adaptive_proof_pacing(scenes, golden_moments, clip_mapping, max_proof_s=4.4)
    if cta_style_override in ("heart_pill", "gamified_321"):
        profile = profile.model_copy(
            update={"visual": profile.visual.model_copy(update={"cta_style": cta_style_override})}
        )

    # Render each scene with anti-reused transforms & dynamic zoom-punch at its exact Golden Moment
    t_style = getattr(profile.visual, "transition_style", "squooshify_v33")
    processed_segments: List[Path] = []
    segment_durations: List[float] = []
    usage_counts: Dict[str, int] = {}
    scene_render_log = []
    hook_clip_path: Optional[Path] = None
    hook_clip_has_audio = False
    hook_audio_start_s = 0.0

    for i, sc in enumerate(scenes):
        gm = golden_moments.get(sc["index"])
        clip_path = (assets_dir / gm["filename"]) if (gm and (assets_dir / gm["filename"]).exists()) else clip_mapping.get(sc["index"], clips[i % len(clips)])
        forced_st = gm.get("start_s") if gm else None
        u_idx = usage_counts.get(clip_path.name, 0)
        usage_counts[clip_path.name] = u_idx + 1

        # Exact overlap compensation per cut so total duration stays millisecond-locked to VO
        if enable_transitions and i < len(scenes) - 1:
            if t_style == "squooshify_v33":
                _, cut_pad = get_v33_transition_spec(i + 1)
            elif t_style == "hard_cut":
                cut_pad = 0.0
            else:
                cut_pad = 0.12
        else:
            cut_pad = 0.0
        seg_dur = float(sc["duration_s"]) + cut_pad
        c_status = str(clean_meta.get(clip_path.name, {}).get("status", "BERSIH")).upper()
        allow_flip = (c_status == "BERSIH") and bool(gm.get("allow_flip", True) if gm else True)

        seg_out = temp_dir / f"seg_{i:02d}.mp4"
        _prog(26 + int((i / max(1, len(scenes))) * 42), f"Merender adegan {i + 1}/{len(scenes)} ({clip_path.name[:24]})...")
        seg_info = render_cinematic_segment(
            clip_path=clip_path,
            seg_out=seg_out,
            target_dur=seg_dur,
            scene_idx=sc["index"],
            profile=profile,
            cfg=CFG,
            allow_flip=allow_flip,
            usage_offset=u_idx,
            forced_start_s=forced_st,
        )
        processed_segments.append(seg_out)
        segment_durations.append(seg_dur)
        scene_render_log.append(
            {
                "scene_index": sc["index"],
                "start_s": sc["start_s"],
                "end_s": sc["end_s"],
                "clip": clip_path.name,
                "golden_moment_mode": gm.get("mode") if gm else "default",
                **seg_info,
            }
        )
        if i == 0:
            hook_clip_path = clip_path
            hook_clip_has_audio = bool(seg_info.get("has_audio", False))
            hook_audio_start_s = float(seg_info.get("start_trim", 0.0))

    # 2. Join segments with v3.3 forensic transitions (5f zoom-blur on act change + 3f exposure flash + hard cuts)
    _prog(74, "Menggabungkan transisi v3.3 (zoom-blur & exposure flash)...")
    joined_video = temp_dir / "joined_video.mp4"
    if enable_transitions and len(processed_segments) > 1 and t_style != "hard_cut":
        concat_with_transitions(
            processed_segments,
            segment_durations,
            joined_video,
            CFG,
            trans_dur=0.12,
            fps=profile.visual.fps,
            transition_style=t_style,
        )
    else:
        concat_list = temp_dir / "concat_list.txt"
        concat_list.write_text("\n".join(f"file '{s.name}'" for s in processed_segments) + "\n", encoding="utf-8")
        subprocess.check_call(
            [CFG.ffmpeg_cmd(), "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c", "copy", str(joined_video)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # 3. Generate multi-layer ASS kinetic subtitles + callout banners + Squooshify 2-Beat CTA Sticker
    ass_file = temp_dir / "captions.ass"
    subj_guess = next((s for s in profile.focus_subjects if s.lower() in package_name.lower()), "KUCING")
    generate_ass_subtitles(
        words,
        scenes,
        highlights,
        profile.visual,
        ass_file,
        include_callouts=getattr(profile.visual, "include_top_callouts", False),
        cta_window=cta_window if enable_cta_overlay else None,
        subject_label=subj_guess,
    )

    # 4. Prepare FFmpeg inputs for final composite & broadcast audio mastering
    cmd_inputs: List[str] = ["-i", str(joined_video)]
    next_idx = 1

    # Optional third-party green-screen overlay (skipped when crisp native 60fps vector_heart_cta is enabled)
    use_vector_heart = bool(getattr(profile.visual, "vector_heart_cta", True))
    cta_overlay_path = (
        find_subscribe_overlay(CFG, profile.channel_id)
        if (enable_cta_overlay and cta_window and not use_vector_heart)
        else None
    )
    cta_overlay_idx: Optional[int] = None
    if cta_overlay_path and cta_overlay_path.exists():
        cmd_inputs.extend(["-i", str(cta_overlay_path)])
        cta_overlay_idx = next_idx
        next_idx += 1

    # VO audio
    cmd_inputs.extend(["-i", str(vo_file)])
    vo_idx = next_idx
    next_idx += 1

    # BGM audio (looped cleanly at input level)
    bgm_idx: Optional[int] = None
    if bgm_file and bgm_file.exists():
        cmd_inputs.extend(["-stream_loop", "-1", "-i", str(bgm_file)])
        bgm_idx = next_idx
        next_idx += 1

    # Optional authentic animal hook audio snippet ONLY when explicitly verified in golden_moments
    hook_audio_idx: Optional[int] = None
    gm1 = golden_moments.get(scenes[0]["index"]) if scenes else None
    allow_hook_audio = bool(gm1.get("use_clip_audio", False)) if gm1 else False
    if hook_clip_path and hook_clip_has_audio and allow_hook_audio and wants_original_hook_audio(scenes[0]):
        cmd_inputs.extend(["-i", str(hook_clip_path)])
        hook_audio_idx = next_idx
        next_idx += 1

    # Precision SFX cues (pass filename + sfx_dir + cta_window + cta_style for subscribe bell or 3-2-1 countdown)
    active_cta_style = getattr(profile.visual, "cta_style", "heart_pill")
    sfx_entries = compute_sfx_entries(
        scenes,
        sfx_dir,
        highlights,
        cta_window=cta_window if enable_cta_overlay else None,
        cta_style=active_cta_style,
    )
    sfx_inputs = []
    for trigger_s, sfx_p, gain in sfx_entries:
        cmd_inputs.extend(["-i", str(sfx_p)])
        sfx_inputs.append((next_idx, trigger_s, gain, sfx_p.name, str(sfx_dir)))
        next_idx += 1

    # 5. Build combined video + audio filter_complex (CTA Bokeh Blur without dark vignette)
    video_filters: List[str] = []
    base_v_label = "[0:v]"

    if cta_window is not None and getattr(profile.visual, "cta_bg_blur", True):
        cta_s, cta_e = cta_window
        blur_stop = max(cta_s + 0.6, cta_e)
        video_filters.append(
            f"{base_v_label}split=2[v_sharp][v_to_blur];"
            f"[v_to_blur]boxblur=24:3,eq=saturation=1.08,format=yuva420p,"
            f"fade=t=in:st={cta_s:.3f}:d=0.18:alpha=1,"
            f"fade=t=out:st={max(cta_s + 0.25, blur_stop - 0.18):.3f}:d=0.18:alpha=1[v_bokeh];"
            f"[v_sharp][v_bokeh]overlay=0:0:enable='between(t,{cta_s:.3f},{blur_stop:.3f})'[v_base]"
        )
        base_v_label = "[v_base]"

    if cta_overlay_idx is not None and cta_window is not None:
        cta_start, cta_end = cta_window
        cta_dur = max(0.8, cta_end - cta_start)
        cta_stop = cta_end
        video_filters.append(
            f"[{cta_overlay_idx}:v]trim=0:{cta_dur:.3f},setpts=PTS-STARTPTS+{cta_start:.3f}/TB,"
            f"scale=540:-1,chromakey=0x00ff00:0.22:0.08,format=yuva420p,"
            f"fade=t=in:st={cta_start:.3f}:d=0.15:alpha=1,"
            f"fade=t=out:st={max(cta_start + 0.2, cta_stop - 0.18):.3f}:d=0.18:alpha=1[cta_ov]"
        )
        video_filters.append(
            f"{base_v_label}[cta_ov]overlay=x=(W-w)/2:y=240:eof_action=pass:enable='between(t,{cta_start:.3f},{cta_stop:.3f})'[v_cta]"
        )
        video_filters.append("[v_cta]subtitles=captions.ass[vout]")
    else:
        video_filters.append(f"{base_v_label}subtitles=captions.ass[vout]")

    # Detect dramatic myth-twist BGM micro-drop window (~0.45s at "bukan"/"salah")
    bgm_drop_win = None
    for w in words:
        tok = "".join(ch for ch in (w.get("word") or "").lower() if ch.isalnum())
        if tok in ("bukan", "salah", "impossible"):
            ws = max(0.0, float(w["start"]) - 0.10)
            bgm_drop_win = (ws, ws + 0.45)
            break

    # Start BGM cleanly AFTER the opening hook scene (+ mute BGM during 3-2-1 gamified countdown)
    hook_end_s = float(scenes[0]["end_s"]) if scenes else 0.0
    bgm_mute_win = cta_window if (enable_cta_overlay and active_cta_style == "gamified_321" and cta_window) else None

    audio_fg = build_audio_filtergraph(
        vo_input_idx=vo_idx,
        vo_duration=vo_duration,
        profile=profile,
        bgm_input_idx=bgm_idx,
        hook_audio_input_idx=hook_audio_idx,
        sfx_inputs=sfx_inputs,
        hook_audio_start_s=hook_audio_start_s,
        bgm_drop_window=bgm_drop_win,
        bgm_start_s=hook_end_s,
        bgm_mute_window=bgm_mute_win,
    )

    full_filter = ";".join(video_filters) + ";" + audio_fg
    cmd_final = [
        CFG.ffmpeg_cmd(),
        "-y",
        *cmd_inputs,
        "-filter_complex",
        full_filter,
        "-map",
        "[vout]",
        "-map",
        "[aout]",
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
        "192k",
        "-movflags",
        "+faststart",
        "-t",
        f"{vo_duration:.3f}",
        str(output_path),
    ]
    _prog(86, "Mastering final MP4 60fps, subtitle 3D, Vector Heart CTA & audio -10.8 LUFS...")
    res = subprocess.run(cmd_final, cwd=str(temp_dir), capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"FFmpeg final render gagal:\n{res.stderr[-1200:]}")

    shutil.rmtree(temp_dir, ignore_errors=True)

    # 6. Write render report + optional visual QC contact sheet
    _prog(95, "Membuat Visual QC Contact Sheet 3x3...")
    out_p = Path(output_path)
    qc_sheet_path: Optional[Path] = None
    if make_qc_sheet:
        try:
            qc_sheet_path = generate_qc_contact_sheet(out_p, out_dir / f"qc_sheet_{package_name}.jpg", CFG)
        except Exception as e:
            print(f"[WARN] QC contact sheet gagal dibuat: {e}")

    final_info = probe_media_info(out_p, CFG)
    report = {
        "engine_version": "3.5.0",
        "channel_id": profile.channel_id,
        "package_name": package_name,
        "output_file": str(out_p),
        "qc_sheet": str(qc_sheet_path) if qc_sheet_path else None,
        "duration_s": round(final_info["duration"], 3),
        "width": final_info["width"],
        "height": final_info["height"],
        "highlights": sorted(highlights),
        "cta_window": cta_window,
        "cta_style": active_cta_style,
        "opening_radial_blur": bool(getattr(profile.visual, "opening_radial_blur", True)),
        "adaptive_proof_pacing": bool(getattr(profile.visual, "adaptive_proof_pacing", True)),
        "cta_overlay_used": cta_overlay_path.name if cta_overlay_path else None,
        "hook_audio_used": hook_audio_idx is not None,
        "bgm_used": bgm_file.name if bgm_file else None,
        "sfx_count": len(sfx_entries),
        "target_lufs": profile.visual.target_lufs,
        "vo_silence_db": vo_silence_db,
        "scenes": scene_render_log,
    }
    (out_dir / f"render_report_{package_name}.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return str(out_p)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python builder.py [package_slug] [--mode capcut|render|both] [--channel C1-fakta]")
        sys.exit(1)

    pkg = sys.argv[1]
    mode = "both"
    ch = None
    for arg in sys.argv[2:]:
        if arg.startswith("--mode="):
            mode = arg.split("=", 1)[1]
        elif arg in ("capcut", "render", "both"):
            mode = arg
        elif arg.startswith("--channel="):
            ch = arg.split("=", 1)[1]

    print(f"[*] Processing package: {pkg} (channel={ch or CFG.default_channel_id}, mode={mode})")
    if "capcut" in mode or "both" in mode:
        draft = build_capcut_project(pkg, channel_id=ch)
        print(f"[+] CapCut Draft created successfully: {draft}")
    if "render" in mode or "both" in mode:
        mp4 = render_ffmpeg_direct(pkg, channel_id=ch)
        print(f"[+] Direct MP4 rendered successfully: {mp4}")
