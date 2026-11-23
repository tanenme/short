"""Multi-Channel Provisioning & Scaling Engine (C2-momen, C3-reddit, or brand-new channels)."""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import Optional

from core.config import StudioConfig, get_config, load_config
from core.db import StudioDB


def provision_channel(
    channel_id: str,
    display_name: Optional[str] = None,
    niche: Optional[str] = None,
    source_channel: str = "C1-fakta",
    copy_audio_bank: bool = True,
    copy_overlays: bool = True,
    activate: bool = False,
    config: Optional[StudioConfig] = None,
) -> dict:
    """Provision full enterprise folder tree and TOML profile for a channel (clone-ready in 1 command)."""
    cfg = config or get_config()
    toml_path = cfg.channels_config_dir / f"{channel_id}.toml"
    created_profile = False

    if not toml_path.exists():
        cfg.channels_config_dir.mkdir(parents=True, exist_ok=True)
        disp = display_name or f"{channel_id} — Shorts Channel"
        niche_slug = niche or channel_id.lower().replace("-", "_")
        status_val = "active" if activate else "validation"
        toml_body = f"""# Channel Profile — {disp}
channel_id = "{channel_id}"
display_name = "{disp}"
folder_name = "{channel_id}"
niche = "{niche_slug}"
status = "{status_val}"
focus_subjects = []
competitors = []

[voice]
engine = "eleven_v4"
voice_id = ""
voice_name = "TBD — Kunci 1 Voice ID tetap khusus {channel_id}"
language = "id"
stability = 0.40
similarity = 0.80
median_wpm = 131
require_audio_tags = true

[pacing]
min_words = 40
max_words = 55
hard_max_words = 60
min_duration_s = 19.0
max_duration_s = 25.0
max_words_per_sentence = 12
target_clip_count = 10
min_clip_duration_s = 1.3
max_clip_duration_s = 5.0

[visual]
width = 1080
height = 1920
fps = 30
font_name = "Arial Black"
font_size = 84
primary_color_hex = "#FFFFFF"
highlight_color_hex = "#FFDD00"
outline_color_hex = "#000000"
target_lufs = -14.0
bgm_volume_db = -22.0
zoom_punch_min = 1.06
zoom_punch_max = 1.12

[ypp]
min_original_vo_ratio = 0.70
max_source_clip_s = 5.0
min_transformations_per_clip = 2
min_tier1_fact_sources = 2
max_clips_per_creator_per_week = 1
forbid_nc_nd_licenses = true
forbid_second_cta_at_end = true
min_cleanliness_score = 70

[cta]
template_id = "template_b"
pattern = "Kalau kamu suka video kayak gini, Subscribe ya."
window_start_s = 2.0
window_end_s = 8.5

[schedule]
timezone = "Asia/Jakarta"
daily_slots = ["06:30", "12:30", "19:30"]
target_videos_per_day = 3
"""
        toml_path.write_text(toml_body, encoding="utf-8")
        created_profile = True
        cfg = load_config(root_dir=cfg.root_dir, force_reload=True)
    elif activate:
        raw = toml_path.read_text(encoding="utf-8")
        updated = raw.replace('status = "planned"', 'status = "validation"')
        if updated != raw:
            toml_path.write_text(updated, encoding="utf-8")
            cfg = load_config(root_dir=cfg.root_dir, force_reload=True)

    ch_dir = cfg.channel_dir(channel_id)
    subdirs = ["_ide-harian", "_tiket", "_rapor", "_overlays", "bgm", "sfx"]
    for sub in subdirs:
        (ch_dir / sub).mkdir(parents=True, exist_ok=True)

    copied_files = 0
    if source_channel and source_channel in cfg.channels and source_channel != channel_id:
        if copy_audio_bank:
            src_bgm = cfg.channel_bgm_dir(source_channel)
            if src_bgm.exists():
                for f in src_bgm.glob("*.mp3"):
                    dest = ch_dir / "bgm" / f.name
                    if not dest.exists():
                        shutil.copy2(f, dest)
                        copied_files += 1
            src_sfx = cfg.channel_sfx_dir(source_channel)
            if src_sfx.exists():
                for f in src_sfx.glob("*.mp3"):
                    dest = ch_dir / "sfx" / f.name
                    if not dest.exists():
                        shutil.copy2(f, dest)
                        copied_files += 1
        if copy_overlays:
            src_ov = cfg.channel_overlays_dir(source_channel)
            if src_ov.exists():
                for f in src_ov.glob("*.mp4"):
                    dest = ch_dir / "_overlays" / f.name
                    if not dest.exists():
                        shutil.copy2(f, dest)
                        copied_files += 1

    db = StudioDB(config=cfg)
    db.sync_channel(channel_id)

    return {
        "success": True,
        "channel_id": channel_id,
        "channel_dir": str(ch_dir),
        "profile_path": str(toml_path),
        "created_profile": created_profile,
        "subdirs_ready": subdirs,
        "copied_shared_assets": copied_files,
    }
