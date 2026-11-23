"""
Autonomous Video Editor Engine for YouTube Shorts / TikTok (v2.0)
Full Semantic Synchronization:
1. Audio Word Alignment via Local Gemini Audio Input (words_alignment.json)
2. Semantic Scene-to-Clip Matching via Local Gemini Vision (scene_clip_mapping.json)
3. Precision Cut Timing (Clips synced to sentence speech boundaries)
4. Word-by-Word Kinetic Subtitles (Pop-up zoom animation & emphasis colors)
5. Multi-track CapCut Draft & Direct FFmpeg Headless Rendering
"""

import os
import sys
import re
import json
import uuid
import time
import shutil
import base64
import subprocess
from pathlib import Path

SISTEM_DIR = Path(__file__).resolve().parent.parent
if str(SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SISTEM_DIR))

from core.config import get_config

CFG = get_config()
BASE_CONTENT_DIR = CFG.channel_dir()
BGM_DIR = CFG.channel_bgm_dir()
SFX_DIR = CFG.channel_sfx_dir()
CAPCUT_DRAFTS_DIR = CFG.capcut_drafts_dir or Path("C:/Users/ronyt/AppData/Local/CapCut/User Data/Projects/com.lveditor.draft")
CAPCUT_CACHE_DIR = CAPCUT_DRAFTS_DIR.parent.parent / "Cache/effect"

def get_local_ai_client():
    import openai
    return openai.OpenAI(base_url=CFG.ai_base_url, api_key=CFG.ai_api_key or "local")

TRANSITIONS_MAP = {
    "flash": {
        "name": "Flash Transition",
        "effect_id": "7613634637847481605",
        "path": "C:/Users/ronyt/AppData/Local/CapCut/User Data/Cache/effect/7613634637847481605/a0201eb05f769628d1ff26d051e4043b",
        "duration": 500000
    },
    "pull_in": {
        "name": "Pull in",
        "effect_id": "6724226861666144779",
        "path": "C:/Users/ronyt/AppData/Local/CapCut/User Data/Cache/effect/6724226861666144779/4d5a316f2eae582e7d0604b47feb8c32",
        "duration": 466666
    }
}

TEXT_TEMPLATE_POPUP = {
    "name": "逐词出现-白",
    "effect_id": "7331663443952438534",
    "third_resource_id": "7330906542717276673",
    "path": "C:/Users/ronyt/AppData/Local/CapCut/User Data/Cache/effect/7331663443952438534/7ef11f12e8084e7c5293aba0a661e012"
}

def get_media_duration(file_path: Path | str) -> float:
    try:
        cmd = [
            CFG.ffprobe_cmd(), "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(file_path)
        ]
        out = subprocess.check_output(cmd).decode().strip()
        return float(out)
    except Exception as e:
        print(f"[WARN] Failed to probe duration for {file_path}: {e}")
        return 0.0

def parse_brief(brief_path: Path) -> list:
    content = brief_path.read_text(encoding="utf-8")
    rows = []
    for line in content.splitlines():
        line = line.strip()
        if not line.startswith('|'):
            continue
        parts = [p.strip() for p in line.split('|')[1:-1]]
        if not parts or not parts[0].isdigit():
            continue
        
        sec_str = parts[1].replace(',', '.')
        m = re.findall(r'(\d+(?:\.\d+)?)', sec_str)
        if len(m) >= 2:
            start_s, end_s = float(m[0]), float(m[1])
        else:
            continue
            
        vo_text = parts[3] if len(parts) > 3 else ''
        clean_vo = re.sub(r'\[.*?\]', '', vo_text).replace('"', '').strip()
        
        rows.append({
            'index': int(parts[0]),
            'brief_start_s': start_s,
            'brief_end_s': end_s,
            'visual_desc': parts[2] if len(parts) > 2 else '',
            'vo': clean_vo,
            'screen_text': parts[4] if len(parts) > 4 else '',
            'sfx': parts[5] if len(parts) > 5 else ''
        })
    return rows

def resolve_sfx_path(sfx_hint: str) -> Path | None:
    if not sfx_hint:
        return None
    sfx_hint = sfx_hint.lower()
    mapping = {
        "whoosh": "clean-whoosh.mp3",
        "zip": "clean-whoosh.mp3",
        "pop": "clean-pop.mp3",
        "ding": "clean-ding.mp3",
        "subscribe": "clean-ding.mp3",
        "sting": "clean-ding.mp3",
        "buzzer": "bruh-sound-effect_WstdzdM.mp3",
        "bruh": "bruh-sound-effect_WstdzdM.mp3",
        "bukan": "bruh-sound-effect_WstdzdM.mp3",
        "salah": "bruh-sound-effect_WstdzdM.mp3",
        "negatif": "bruh-sound-effect_WstdzdM.mp3",
        "question": "question-mark.mp3",
        "tanya": "question-mark.mp3",
        "feromon": "rizz-sound-effect.mp3",
        "tension": "rizz-sound-effect.mp3",
        "adrenalin": "rizz-sound-effect.mp3",
        "bass": "rizz-sound-effect.mp3",
        "sniff": "clean-whoosh.mp3"
    }
    for k, filename in mapping.items():
        if k in sfx_hint:
            p = SFX_DIR / filename
            if p.exists():
                return p
    return None

def align_voiceover_words(assets_dir: Path, vo_file: Path) -> list:
    cache_file = assets_dir / "words_alignment.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass
            
    print("[*] Transcribing VO with word-level timestamps via local AI...")
    client = get_local_ai_client()
    with open(vo_file, "rb") as f:
        audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        
    prompt = """Dengarkan audio voiceover Bahasa Indonesia ini dengan sangat teliti.
Buat transkrip SETIAP KATA (word-by-word) dengan timestamp mulai dan selesai yang sangat presisi dalam detik (desimal).
Format output WAJIB HANYA JSON array valid tanpa teks markdown pembuka/penutup:
[
  {"word": "Kenapa", "start": 0.08, "end": 0.44}
]"""

    res = client.chat.completions.create(
        model=CFG.ai_vision_model,
        messages=[
            {"role": "user", "content": [
                {"type": "text", "text": prompt},
                {"type": "input_audio", "input_audio": {"data": audio_b64, "format": "mp3"}}
            ]}
        ]
    )
    
    raw = res.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
        
    words = json.loads(raw)
    cache_file.write_text(json.dumps(words, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[+] Extracted {len(words)} words with precision timestamps.")
    return words

def match_scenes_to_clips(assets_dir: Path, scenes: list, clips: list) -> dict:
    cache_file = assets_dir / "scene_clip_mapping.json"
    if cache_file.exists():
        try:
            mapping = json.loads(cache_file.read_text(encoding="utf-8"))
            if all((assets_dir / m["filename"]).exists() for m in mapping):
                return {m["scene_index"]: assets_dir / m["filename"] for m in mapping}
        except Exception:
            pass
            
    print("[*] Running Vision AI to match scene briefs with raw video clips...")
    client = get_local_ai_client()
    
    clip_descriptions = []
    for i, clip_p in enumerate(clips):
        dur = get_media_duration(clip_p)
        thumb_p = assets_dir / f"_temp_thumb_{i:02d}.jpg"
        subprocess.run(
            [CFG.ffmpeg_cmd(), "-y", "-ss", f"{dur/2:.2f}", "-i", str(clip_p), "-vframes", "1", str(thumb_p)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        if thumb_p.exists():
            with open(thumb_p, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("utf-8")
            res = client.chat.completions.create(
                model=CFG.ai_vision_model,
                messages=[
                    {"role": "user", "content": [
                        {"type": "text", "text": "Sebutkan subjek dan aksi dalam gambar ini dalam 1 kalimat pendek."},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
                    ]}
                ]
            )
            desc = res.choices[0].message.content.strip()
            clip_descriptions.append({"index": i, "filename": clip_p.name, "desc": desc})
            try:
                thumb_p.unlink()
            except Exception:
                pass
        else:
            clip_descriptions.append({"index": i, "filename": clip_p.name, "desc": "Video clip"})
            
    scene_prompts = [{"scene_index": sc["index"], "desc": f"{sc['visual_desc']} | VO: {sc['vo']}"} for sc in scenes]
    match_prompt = f"""Kamu adalah sutradara video editor ahli.
Berikut adalah daftar adegan yang dibutuhkan naskah:
{json.dumps(scene_prompts, indent=2)}

Berikut adalah daftar klip video yang tersedia di folder assets:
{json.dumps(clip_descriptions, indent=2)}

SYARAT MUTLAK:
Pilihlah HANYA klip yang BERSIH dari gangguan antarmuka media sosial (UI TikTok/Instagram, tombol like, status bar), watermark besar, atau burned-in teks meme.
Tugaskan 1 klip video yang PALING TEPAT secara visual dan aksi untuk setiap adegan.
Format output WAJIB HANYA JSON array:
[
  {{"scene_index": 1, "filename": "nama_file.mp4"}}
]"""

    res = client.chat.completions.create(
        model=CFG.ai_vision_model,
        messages=[{"role": "user", "content": match_prompt}]
    )
    raw = res.choices[0].message.content.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n", "", raw)
        raw = re.sub(r"\n```$", "", raw)
        
    mapping_list = json.loads(raw)
    cache_file.write_text(json.dumps(mapping_list, indent=2), encoding="utf-8")
    
    result = {}
    for m in mapping_list:
        p = assets_dir / m["filename"]
        result[m["scene_index"]] = p if p.exists() else clips[0]
    return result

def synchronize_scenes(scenes: list, words: list, total_duration: float) -> list:
    word_idx = 0
    for sc in scenes:
        if not sc['vo'] or sc['vo'].startswith('('):
            sc['words'] = []
            continue
            
        target_tokens = [re.sub(r'[^\w]', '', w.lower()) for w in sc['vo'].split() if re.sub(r'[^\w]', '', w.lower())]
        matched = []
        while word_idx < len(words) and len(matched) < len(target_tokens):
            matched.append(words[word_idx])
            word_idx += 1
        sc['words'] = matched
        
    for i, sc in enumerate(scenes):
        if i == 0:
            sc['start_s'] = 0.0
        else:
            prev_sc = scenes[i - 1]
            if prev_sc['words']:
                sc['start_s'] = prev_sc['words'][-1]['end']
            else:
                sc['start_s'] = prev_sc['end_s']
                
        if i < len(scenes) - 1:
            next_sc = scenes[i + 1]
            if sc['words'] and next_sc.get('words'):
                gap_mid = (sc['words'][-1]['end'] + next_sc['words'][0]['start']) / 2.0
                sc['end_s'] = gap_mid
            elif sc['words']:
                sc['end_s'] = sc['words'][-1]['end'] + 0.3
            else:
                sc['end_s'] = sc['start_s'] + 2.0
        else:
            sc['end_s'] = total_duration
            
        sc['duration_s'] = max(0.5, sc['end_s'] - sc['start_s'])
        
    return scenes

def build_capcut_project(package_name: str, draft_id_name: str = None) -> str:
    pkg_dir = BASE_CONTENT_DIR / package_name
    brief_file = pkg_dir / "brief-edit.md"
    raw_scenes = parse_brief(brief_file)
    
    assets_dir = pkg_dir / "assets"
    vo_files = list(assets_dir.glob("*.mp3"))
    if not vo_files:
        raise FileNotFoundError(f"No voiceover mp3 found in {assets_dir}")
    vo_file = max(vo_files, key=lambda f: f.stat().st_mtime)
    vo_duration = get_media_duration(vo_file)
    
    clips = sorted(list(assets_dir.glob("*.mp4")) + list(assets_dir.glob("*.webm")))
    if not clips:
        raise FileNotFoundError(f"No video clips found in {assets_dir}")
        
    bgm_candidates = list(BGM_DIR.glob("*.mp3"))
    bgm_file = bgm_candidates[0] if bgm_candidates else None
    for b in bgm_candidates:
        if "monkeys" in b.name.lower() or "sneaky" in b.name.lower():
            bgm_file = b
            break

    words = align_voiceover_words(assets_dir, vo_file)
    clip_mapping = match_scenes_to_clips(assets_dir, raw_scenes, clips)
    scenes = synchronize_scenes(raw_scenes, words, vo_duration)
    
    if not draft_id_name:
        draft_id_name = f"auto_{package_name}_{int(time.time())}"
        
    draft_folder = CAPCUT_DRAFTS_DIR / draft_id_name
    draft_folder.mkdir(parents=True, exist_ok=True)
    
    total_us = int(vo_duration * 1_000_000)
    now_ts = int(time.time())
    
    draft_content = {
        "id": str(uuid.uuid4()).upper(),
        "version": 360000,
        "new_version": "12.0.0",
        "name": draft_id_name,
        "duration": total_us,
        "create_time": now_ts,
        "update_time": now_ts,
        "fps": 30.0,
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
            "zoom_info_params": None
        },
        "canvas_config": {
            "ratio": "9:16",
            "width": 1080,
            "height": 1920,
            "background": None
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
            "sound_channel_mappings": []
        }
    }
    
    # 1. Main Video Track
    track_video = {"id": str(uuid.uuid4()).upper(), "type": "video", "flag": 0, "segments": []}
    curr_target_start_us = 0
    
    for i, sc in enumerate(scenes):
        sc_dur_us = int(sc['duration_s'] * 1_000_000)
        clip_path = clip_mapping.get(sc['index'], clips[i % len(clips)])
        clip_dur_us = int(get_media_duration(clip_path) * 1_000_000)
        
        source_start_us = max(0, (clip_dur_us - sc_dur_us) // 2) if clip_dur_us > sc_dur_us else 0
        mat_video_id = str(uuid.uuid4()).upper()
        speed_id = str(uuid.uuid4()).upper()
        
        draft_content["materials"]["videos"].append({
            "id": mat_video_id,
            "type": "video",
            "material_name": clip_path.name,
            "path": str(clip_path).replace("\\", "/"),
            "duration": clip_dur_us,
            "width": 1080,
            "height": 1920,
            "extra_info": clip_path.name
        })
        
        draft_content["materials"]["speeds"].append({"id": speed_id, "speed": 1.0, "mode": 0})
        
        seg = {
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_video_id,
            "source_timerange": {"start": source_start_us, "duration": sc_dur_us},
            "target_timerange": {"start": curr_target_start_us, "duration": sc_dur_us},
            "speed_id": speed_id,
            "volume": 0.0
        }
        
        if i > 0:
            trans_spec = TRANSITIONS_MAP["flash"]
            trans_id = str(uuid.uuid4()).upper()
            draft_content["materials"]["transitions"].append({
                "id": trans_id,
                "type": "transition",
                "name": trans_spec["name"],
                "effect_id": trans_spec["effect_id"],
                "resource_id": trans_spec["effect_id"],
                "path": trans_spec["path"],
                "duration": trans_spec["duration"],
                "is_overlap": True,
                "platform": "all"
            })
            seg["extra_material_refs"] = [trans_id]
            
        track_video["segments"].append(seg)
        curr_target_start_us += sc_dur_us
        
    draft_content["tracks"].append(track_video)
    
    # 2. Kinetic Word-by-Word Subtitle Track
    track_text = {"id": str(uuid.uuid4()).upper(), "type": "text", "flag": 1, "segments": []}
    
    for w in words:
        w_text = w["word"].strip().upper()
        if not w_text:
            continue
        w_start_us = int(w["start"] * 1_000_000)
        w_dur_us = max(180000, int((w["end"] - w["start"]) * 1_000_000))
        
        mat_text_id = str(uuid.uuid4()).upper()
        tpl_id = str(uuid.uuid4()).upper()
        
        draft_content["materials"]["text_templates"].append({
            "id": tpl_id,
            "version": "1.0.0",
            "effect_id": TEXT_TEMPLATE_POPUP["effect_id"],
            "resource_id": TEXT_TEMPLATE_POPUP["effect_id"],
            "third_resource_id": TEXT_TEMPLATE_POPUP["third_resource_id"],
            "name": TEXT_TEMPLATE_POPUP["name"],
            "type": "text_template_subtitle",
            "path": TEXT_TEMPLATE_POPUP["path"]
        })
        
        is_highlight = any(k in w_text for k in ["CICAK", "BUKAN", "SUBSCRIBE", "ADRENALIN", "MENERKAM", "BERHENTI"])
        fill_color = [1.0, 0.85, 0.0] if is_highlight else [1.0, 1.0, 1.0]
        
        text_content_obj = {
            "text": w_text,
            "styles": [
                {
                    "fill": {"content": {"render_type": "solid", "solid": {"color": fill_color}}},
                    "strokes": [{"content": {"render_type": "solid", "solid": {"color": [0.0, 0.0, 0.0]}}, "width": 14.0}]
                }
            ]
        }
        
        draft_content["materials"]["texts"].append({
            "id": mat_text_id,
            "type": "text",
            "content": json.dumps(text_content_obj),
            "recognize_text": w_text
        })
        
        track_text["segments"].append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_text_id,
            "source_timerange": {"start": 0, "duration": w_dur_us},
            "target_timerange": {"start": w_start_us, "duration": w_dur_us},
            "extra_material_refs": [tpl_id]
        })
        
    draft_content["tracks"].append(track_text)
    
    # 3. VO Audio Track
    track_vo = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
    mat_vo_id = str(uuid.uuid4()).upper()
    draft_content["materials"]["audios"].append({
        "id": mat_vo_id,
        "type": "music",
        "name": vo_file.name,
        "path": str(vo_file).replace("\\", "/"),
        "duration": total_us,
        "extra_info": vo_file.name
    })
    track_vo["segments"].append({
        "id": str(uuid.uuid4()).upper(),
        "material_id": mat_vo_id,
        "source_timerange": {"start": 0, "duration": total_us},
        "target_timerange": {"start": 0, "duration": total_us},
        "volume": 1.0
    })
    draft_content["tracks"].append(track_vo)
    
    # 4. Ducked BGM Track
    if bgm_file and bgm_file.exists():
        track_bgm = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
        mat_bgm_id = str(uuid.uuid4()).upper()
        bgm_dur_us = int(get_media_duration(bgm_file) * 1_000_000)
        draft_content["materials"]["audios"].append({
            "id": mat_bgm_id,
            "type": "music",
            "name": bgm_file.name,
            "path": str(bgm_file).replace("\\", "/"),
            "duration": bgm_dur_us,
            "extra_info": bgm_file.name
        })
        track_bgm["segments"].append({
            "id": str(uuid.uuid4()).upper(),
            "material_id": mat_bgm_id,
            "source_timerange": {"start": 0, "duration": min(total_us, bgm_dur_us)},
            "target_timerange": {"start": 0, "duration": min(total_us, bgm_dur_us)},
            "volume": 0.06 # Soft background ambience
        })
        draft_content["tracks"].append(track_bgm)
        
    # 5. Precise SFX Track (synchronized to speech starts or trigger words)
    track_sfx = {"id": str(uuid.uuid4()).upper(), "type": "audio", "flag": 0, "segments": []}
    for sc in scenes:
        sfx_path = resolve_sfx_path(sc.get('sfx', ''))
        if sfx_path:
            sfx_dur_us = int(get_media_duration(sfx_path) * 1_000_000)
            mat_sfx_id = str(uuid.uuid4()).upper()
            draft_content["materials"]["audios"].append({
                "id": mat_sfx_id,
                "type": "sound_effect",
                "name": sfx_path.name,
                "path": str(sfx_path).replace("\\", "/"),
                "duration": sfx_dur_us,
                "extra_info": sfx_path.name
            })
            # Check if cue word exists in scene words (e.g. subscribe)
            trigger_s = sc['start_s']
            for w in sc.get('words', []):
                if any(k in w['word'].lower() for k in ["subscribe", "bukan", "adrenalin", "menerkam"]):
                    trigger_s = w['start']
                    break
            start_us = int(trigger_s * 1_000_000)
            play_dur = min(int(sc['duration_s'] * 1_000_000), sfx_dur_us)
            track_sfx["segments"].append({
                "id": str(uuid.uuid4()).upper(),
                "material_id": mat_sfx_id,
                "source_timerange": {"start": 0, "duration": play_dur},
                "target_timerange": {"start": start_us, "duration": play_dur},
                "volume": 0.7
            })
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
        "tm_draft_modified": now_ts * 1_000_000
    }
    (draft_folder / "draft_meta_info.json").write_text(json.dumps(meta_info, indent=2), encoding="utf-8")
    
    root_meta_file = CAPCUT_DRAFTS_DIR / "root_meta_info.json"
    if root_meta_file.exists():
        try:
            root_data = json.loads(root_meta_file.read_text(encoding="utf-8"))
            all_drafts = root_data.setdefault("all_draft_store", [])
            all_drafts = [d for d in all_drafts if d.get("draft_name") != draft_id_name]
            all_drafts.insert(0, {
                "draft_id": meta_info["draft_id"],
                "draft_name": draft_id_name,
                "draft_root_path": str(CAPCUT_DRAFTS_DIR).replace("\\", "/"),
                "draft_fold_path": str(draft_folder).replace("\\", "/")
            })
            root_data["all_draft_store"] = all_drafts
            root_meta_file.write_text(json.dumps(root_data, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[WARN] Failed to update root_meta_info: {e}")
            
    return str(draft_folder)

def render_ffmpeg_direct(package_name: str, output_path: str = None) -> str:
    pkg_dir = BASE_CONTENT_DIR / package_name
    brief_file = pkg_dir / "brief-edit.md"
    raw_scenes = parse_brief(brief_file)
    
    assets_dir = pkg_dir / "assets"
    vo_files = list(assets_dir.glob("*.mp3"))
    if not vo_files:
        raise FileNotFoundError(f"No VO mp3 found in {assets_dir}")
    vo_file = max(vo_files, key=lambda f: f.stat().st_mtime)
    vo_duration = get_media_duration(vo_file)
    
    clips = sorted(list(assets_dir.glob("*.mp4")) + list(assets_dir.glob("*.webm")))
    bgm_candidates = list(BGM_DIR.glob("*.mp3"))
    bgm_file = bgm_candidates[0] if bgm_candidates else None
    for b in bgm_candidates:
        if "monkeys" in b.name.lower() or "sneaky" in b.name.lower():
            bgm_file = b
            break
            
    words = align_voiceover_words(assets_dir, vo_file)
    clip_mapping = match_scenes_to_clips(assets_dir, raw_scenes, clips)
    scenes = synchronize_scenes(raw_scenes, words, vo_duration)
    
    out_dir = pkg_dir / "output"
    out_dir.mkdir(exist_ok=True)
    if not output_path:
        output_path = str(out_dir / f"final_{package_name}.mp4")
        
    temp_dir = out_dir / "_temp"
    temp_dir.mkdir(exist_ok=True)
    
    processed_segments = []
    for i, sc in enumerate(scenes):
        sc_dur = sc['duration_s']
        clip_path = clip_mapping.get(sc['index'], clips[i % len(clips)])
        clip_dur = get_media_duration(clip_path)
        start_trim = max(0.0, (clip_dur - sc_dur) / 2.0) if clip_dur > sc_dur else 0.0
        
        seg_out = temp_dir / f"seg_{i:02d}.mp4"
        vf_filter = (
            "scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,"
            "fps=30"
        )
        
        cmd = [
            CFG.ffmpeg_cmd(), "-y",
            "-ss", f"{start_trim:.3f}",
            "-t", f"{sc_dur:.3f}",
            "-i", str(clip_path),
            "-vf", vf_filter,
            "-an",
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "20",
            str(seg_out)
        ]
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        processed_segments.append(seg_out)
        
    concat_list_file = temp_dir / "concat_list.txt"
    with open(concat_list_file, "w", encoding="utf-8") as f:
        for seg in processed_segments:
            f.write(f"file '{seg.name}'\n")
            
    concat_video = temp_dir / "concat_video.mp4"
    cmd_concat = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat_list_file),
        "-c", "copy",
        str(concat_video)
    ]
    subprocess.check_call(cmd_concat, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    ass_file = temp_dir / "captions.ass"
    ass_lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        "PlayResX: 1080",
        "PlayResY: 1920",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        "Style: Kinetic,Arial Black,84,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,8,3,2,40,40,380,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text"
    ]
    
    def fmt_ts(sec):
        h = int(sec // 3600)
        m = int((sec % 3600) // 60)
        s = sec % 60
        return f"{h:d}:{m:02d}:{s:05.2f}"
        
    for w in words:
        raw_w = w["word"].strip().upper()
        clean_w = re.sub(r'[^\w\?\!\-]', '', raw_w)
        if not clean_w:
            continue
            
        t_start = fmt_ts(w["start"])
        t_end = fmt_ts(max(w["start"] + 0.18, w["end"]))
        
        is_highlight = any(k in clean_w for k in ["CICAK", "BUKAN", "SUBSCRIBE", "ADRENALIN", "MENERKAM", "BERHENTI"])
        color_tag = "{\\c&H0022FFFF&}" if is_highlight else ""
        pop_anim = "{\\fscx115\\fscy115\\t(0,70,\\fscx100\\fscy100)}"
        
        dialogue_text = f"{color_tag}{pop_anim}{clean_w}"
        ass_lines.append(f"Dialogue: 0,{t_start},{t_end},Kinetic,,0,0,0,,{dialogue_text}")
        
    ass_file.write_text("\n".join(ass_lines), encoding="utf-8")
    
    audio_inputs = ["-i", str(vo_file)]
    has_bgm = bool(bgm_file and bgm_file.exists())
    if has_bgm:
        audio_inputs.extend(["-i", str(bgm_file)])
        
    sfx_entries = []
    for sc in scenes:
        sfx_path = resolve_sfx_path(sc.get('sfx', ''))
        if sfx_path:
            trigger_s = sc['start_s']
            for w in sc.get('words', []):
                if any(k in w['word'].lower() for k in ["subscribe", "bukan", "adrenalin", "menerkam"]):
                    trigger_s = w['start']
                    break
            sfx_entries.append((trigger_s, sfx_path))
            
    for _, sfx_p in sfx_entries:
        audio_inputs.extend(["-i", str(sfx_p)])
        
    filter_parts = []
    # Boost VO volume to 1.75 for clear foreground voice
    filter_parts.append("[1:a]volume=1.75,apad=pad_dur=1[vo]")
    
    # Drop BGM to 0.06 (-24dB) so it sits cleanly behind VO
    if has_bgm:
        filter_parts.append("[2:a]volume=0.06,atrim=0:" + f"{vo_duration:.2f}" + "[bgm]")
        bgm_label = "[bgm]"
        sfx_start_idx = 3
    else:
        bgm_label = ""
        sfx_start_idx = 2
        
    sfx_labels = []
    for idx, (offset_s, sfx_p) in enumerate(sfx_entries):
        delay_ms = int(offset_s * 1000)
        input_idx = sfx_start_idx + idx
        lbl = f"sfx_{idx}"
        filter_parts.append(f"[{input_idx}:a]adelay={delay_ms}|{delay_ms},volume=0.7[{lbl}]")
        sfx_labels.append(f"[{lbl}]")
        
    mix_sources = "[vo]" + (bgm_label if bgm_label else "") + "".join(sfx_labels)
    total_sources = 1 + (1 if has_bgm else 0) + len(sfx_labels)
    filter_parts.append(f"{mix_sources}amix=inputs={total_sources}:duration=first:dropout_transition=2[aout]")
    
    audio_filtergraph = ";".join(filter_parts)
    
    cmd_final = [
        "ffmpeg", "-y",
        "-i", str(concat_video)
    ] + audio_inputs + [
        "-filter_complex",
        f"[0:v]subtitles=captions.ass[vout];{audio_filtergraph}",
        "-map", "[vout]",
        "-map", "[aout]",
        "-c:v", "libx264",
        "-preset", "fast",
        "-crf", "18",
        "-c:a", "aac",
        "-b:a", "192k",
        "-t", f"{vo_duration:.2f}",
        str(output_path)
    ]
    subprocess.check_call(cmd_final, cwd=str(temp_dir), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    try:
        shutil.rmtree(temp_dir)
    except Exception:
        pass
        
    return str(output_path)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python builder.py [package_slug] [--mode capcut|render|both]")
        sys.exit(1)
        
    pkg = sys.argv[1]
    mode = sys.argv[2] if len(sys.argv) > 2 else "--mode=both"
    
    print(f"[*] Processing package: {pkg}")
    if "capcut" in mode or "both" in mode:
        draft = build_capcut_project(pkg)
        print(f"[+] CapCut Draft created successfully: {draft}")
    if "render" in mode or "both" in mode:
        mp4 = render_ffmpeg_direct(pkg)
        print(f"[+] Direct MP4 rendered successfully: {mp4}")
