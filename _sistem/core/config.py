"""Centralized configuration, secret loading, and local tool discovery."""

from __future__ import annotations

import os
import shutil
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .models import ChannelProfile


def _parse_dotenv(path: Path) -> Dict[str, str]:
    values: Dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k:
            values[k] = v
    return values


def _discover_binary(
    name: str,
    override: Optional[str] = None,
    extra_candidates: Optional[List[Path]] = None,
) -> Optional[Path]:
    if override:
        p = Path(override)
        if p.exists():
            return p

    found = shutil.which(name)
    if found:
        return Path(found)

    candidates: List[Path] = []
    if extra_candidates:
        candidates.extend(extra_candidates)

    exe_suffix = ".exe" if os.name == "nt" else ""
    py_scripts = Path(sys.executable).parent / "Scripts" / f"{name}{exe_suffix}"
    candidates.append(py_scripts)

    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    if name in ("ffmpeg", "ffprobe"):
        hermes_tools = local_appdata / "hermes" / "tools"
        if hermes_tools.exists():
            for ffdir in sorted(hermes_tools.glob("ffmpeg*"), reverse=True):
                candidates.append(ffdir / "bin" / f"{name}{exe_suffix}")
        candidates.append(Path("C:/ffmpeg/bin") / f"{name}{exe_suffix}")
        capcut_apps = local_appdata / "CapCut" / "Apps"
        if capcut_apps.exists():
            for ver_dir in sorted(capcut_apps.iterdir(), reverse=True):
                if ver_dir.is_dir():
                    candidates.append(ver_dir / f"{name}{exe_suffix}")
    elif name == "yt-dlp":
        hermes_installs = local_appdata / "hermes" / "installs"
        if hermes_installs.exists():
            for p in hermes_installs.glob("**/Scripts/yt-dlp.exe"):
                candidates.append(p)

    for c in candidates:
        if c.exists():
            return c
    return None


@dataclass
class StudioConfig:
    root_dir: Path
    system_dir: Path
    channels_config_dir: Path
    default_channel_id: str = "C1-fakta"

    # Secrets & AI endpoints (loaded from .env / os.environ / legacy fallback)
    exa_api_key: str = ""
    ai_base_url: str = "http://localhost:20128/v1"
    ai_api_key: str = ""
    ai_vision_model: str = "ag/gemini-3.8-flash-high"
    ai_audio_model: str = "ag/gemini-3.8-flash-high"
    ai_text_model: str = "ag/gemini-3.8-flash-high"
    elevenlabs_api_key: str = ""

    # Tool binaries & external paths
    ffmpeg_bin: Optional[Path] = None
    ffprobe_bin: Optional[Path] = None
    ytdlp_bin: Optional[Path] = None
    capcut_drafts_dir: Optional[Path] = None

    # Channel registry
    channels: Dict[str, ChannelProfile] = field(default_factory=dict)
    env_sources: List[str] = field(default_factory=list)

    def get_channel(self, channel_id: Optional[str] = None) -> ChannelProfile:
        cid = channel_id or self.default_channel_id
        if cid in self.channels:
            return self.channels[cid]
        # Allow case-insensitive match
        for k, prof in self.channels.items():
            if k.lower() == cid.lower():
                return prof
        raise KeyError(f"Channel '{cid}' tidak ditemukan. Tersedia: {list(self.channels.keys())}")

    def channel_dir(self, channel_id: Optional[str] = None) -> Path:
        prof = self.get_channel(channel_id)
        return self.root_dir / prof.folder_name

    def episode_dir(self, episode_id: str, channel_id: Optional[str] = None) -> Path:
        return self.channel_dir(channel_id) / episode_id

    def episode_assets_dir(self, episode_id: str, channel_id: Optional[str] = None) -> Path:
        """Resolve episode assets dir, supporting legacy `asset/` fallback."""
        ep_dir = self.episode_dir(episode_id, channel_id)
        canonical = ep_dir / "assets"
        if canonical.exists():
            return canonical
        legacy = ep_dir / "asset"
        if legacy.exists():
            return legacy
        return canonical

    def channel_bgm_dir(self, channel_id: Optional[str] = None) -> Path:
        """Resolve BGM dir, supporting standard `bgm/` and legacy `backgorud sound/`."""
        ch_dir = self.channel_dir(channel_id)
        for name in ("_audio/bgm", "bgm", "backgorud sound"):
            p = ch_dir / name
            if p.exists():
                return p
        return ch_dir / "bgm"

    def channel_sfx_dir(self, channel_id: Optional[str] = None) -> Path:
        """Resolve SFX dir, supporting standard `sfx/` and legacy `effek sounf/`."""
        ch_dir = self.channel_dir(channel_id)
        for name in ("_audio/sfx", "sfx", "effek sounf"):
            p = ch_dir / name
            if p.exists():
                return p
        return ch_dir / "sfx"

    def channel_overlays_dir(self, channel_id: Optional[str] = None) -> Path:
        """Resolve shared channel overlays dir (e.g. subscribe/like green-screen animations)."""
        ch_dir = self.channel_dir(channel_id)
        for name in ("_overlays", "overlays"):
            p = ch_dir / name
            if p.exists():
                return p
        return ch_dir / "_overlays"

    def ffmpeg_cmd(self) -> str:
        return str(self.ffmpeg_bin) if self.ffmpeg_bin else "ffmpeg"

    def ffprobe_cmd(self) -> str:
        return str(self.ffprobe_bin) if self.ffprobe_bin else "ffprobe"

    def ytdlp_cmd(self) -> str:
        return str(self.ytdlp_bin) if self.ytdlp_bin else "yt-dlp"


_CACHED_CONFIG: Optional[StudioConfig] = None


def load_config(root_dir: Optional[Path] = None, force_reload: bool = False) -> StudioConfig:
    global _CACHED_CONFIG
    if _CACHED_CONFIG is not None and not force_reload and root_dir is None:
        return _CACHED_CONFIG

    if root_dir is None:
        # _sistem/core/config.py -> parent^3 is E:\YT\short
        inferred_root = Path(__file__).resolve().parents[2]
        env_root = os.environ.get("STUDIO_ROOT")
        root = Path(env_root) if env_root else inferred_root
    else:
        root = Path(root_dir).resolve()

    system_dir = root / "_sistem"
    channels_dir = system_dir / "channels"

    # Merge env priority: legacy hermes .env < root .env < process os.environ
    merged_env: Dict[str, str] = {}
    sources: List[str] = []

    root_dotenv = root / ".env"
    root_env_vals = _parse_dotenv(root_dotenv)

    legacy_path_str = (
        os.environ.get("LEGACY_HERMES_ENV")
        or root_env_vals.get("LEGACY_HERMES_ENV")
        or str(Path.home() / "AppData/Local/hermes/.env")
    )
    legacy_dotenv = Path(legacy_path_str)
    if legacy_dotenv.exists():
        merged_env.update(_parse_dotenv(legacy_dotenv))
        sources.append(f"legacy:{legacy_dotenv}")

    if root_dotenv.exists():
        merged_env.update(root_env_vals)
        sources.append(f"dotenv:{root_dotenv}")

    for k, v in os.environ.items():
        merged_env[k] = v

    def env_get(key: str, default: str = "") -> str:
        return merged_env.get(key, default).strip()

    exa_key = env_get("EXA_API_KEY")
    ai_key = env_get("AI_API_KEY") or env_get("HERMES_CUSTOM_LOCAL_LOCALHOST_20128_API_KEY")
    ai_base_url = env_get("AI_BASE_URL", "http://localhost:20128/v1")

    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    capcut_override = env_get("CAPCUT_DRAFTS_DIR")
    default_capcut = local_appdata / "CapCut/User Data/Projects/com.lveditor.draft"
    capcut_dir = Path(capcut_override) if capcut_override else default_capcut

    cfg = StudioConfig(
        root_dir=root,
        system_dir=system_dir,
        channels_config_dir=channels_dir,
        exa_api_key=exa_key,
        ai_base_url=ai_base_url,
        ai_api_key=ai_key,
        ai_vision_model=env_get("AI_VISION_MODEL", "ag/gemini-3.8-flash-high"),
        ai_audio_model=env_get("AI_AUDIO_MODEL", "ag/gemini-3.8-flash-high"),
        ai_text_model=env_get("AI_TEXT_MODEL", "ag/gemini-3.8-flash-high"),
        elevenlabs_api_key=env_get("ELEVENLABS_API_KEY"),
        ffmpeg_bin=_discover_binary("ffmpeg", env_get("FFMPEG_BIN") or None),
        ffprobe_bin=_discover_binary("ffprobe", env_get("FFPROBE_BIN") or None),
        ytdlp_bin=_discover_binary("yt-dlp", env_get("YTDLP_BIN") or None),
        capcut_drafts_dir=capcut_dir,
        env_sources=sources,
    )

    if channels_dir.exists():
        for toml_path in sorted(channels_dir.glob("*.toml")):
            with toml_path.open("rb") as fh:
                raw = tomllib.load(fh)
            profile = ChannelProfile.model_validate(raw)
            cfg.channels[profile.channel_id] = profile

    if root_dir is None:
        _CACHED_CONFIG = cfg
    return cfg


def get_config() -> StudioConfig:
    return load_config()
