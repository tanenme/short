"""Centralized configuration, secret loading, and local tool discovery."""

from __future__ import annotations

import json
import os
import shutil
import sys
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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

    # Secrets & AI endpoints (loaded from .env / os.environ / state/ai_provider.json)
    exa_api_key: str = ""
    pexels_api_key: str = ""
    pixabay_api_key: str = ""
    ai_provider_preset: str = "local_proxy"
    ai_provider_name: str = "Local Proxy"
    ai_base_url: str = "http://localhost:20128/v1"
    ai_api_key: str = ""
    ai_vision_model: str = "ag/gemini-3.8-flash-high"
    ai_audio_model: str = "ag/gemini-3.8-flash-high"
    ai_text_model: str = "ag/gemini-3.8-flash-high"
    ai_auto_fallback: bool = True
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
        """Resolve BGM dir, supporting standard `bgm/` and legacy `background sound/` / `backgorud sound/`."""
        ch_dir = self.channel_dir(channel_id)
        for name in ("_audio/bgm", "bgm", "background sound", "backgorud sound"):
            p = ch_dir / name
            if p.exists():
                return p
        return ch_dir / "bgm"

    def channel_sfx_dir(self, channel_id: Optional[str] = None) -> Path:
        """Resolve SFX dir, supporting standard `sfx/`, `effek sound/`, and legacy `effek sounf/`."""
        ch_dir = self.channel_dir(channel_id)
        for name in ("_audio/sfx", "sfx", "effek sound", "effek sounf"):
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
    ai_provider_preset = env_get("AI_PROVIDER_PRESET", "local_proxy")
    ai_provider_name = env_get("AI_PROVIDER_NAME", "Local Proxy")
    ai_vision_model = env_get("AI_VISION_MODEL", "ag/gemini-3.8-flash-high")
    ai_audio_model = env_get("AI_AUDIO_MODEL", "ag/gemini-3.8-flash-high")
    ai_text_model = env_get("AI_TEXT_MODEL", "ag/gemini-3.8-flash-high")
    ai_auto_fallback = env_get("AI_AUTO_FALLBACK", "true").lower() not in ("0", "false", "no")

    # Apply persisted runtime AI provider settings (gitignored under _sistem/state/ai_provider.json)
    state_ai_path = system_dir / "state" / "ai_provider.json"
    if state_ai_path.exists():
        try:
            saved_ai = json.loads(state_ai_path.read_text(encoding="utf-8"))
            if isinstance(saved_ai, dict):
                if saved_ai.get("provider_preset"):
                    ai_provider_preset = str(saved_ai["provider_preset"]).strip()
                if saved_ai.get("provider_name"):
                    ai_provider_name = str(saved_ai["provider_name"]).strip()
                if saved_ai.get("ai_base_url"):
                    ai_base_url = str(saved_ai["ai_base_url"]).strip()
                if "ai_api_key" in saved_ai and saved_ai["ai_api_key"] is not None:
                    saved_key = str(saved_ai["ai_api_key"]).strip()
                    if saved_key or saved_ai.get("clear_api_key"):
                        ai_key = saved_key
                if saved_ai.get("ai_text_model"):
                    ai_text_model = str(saved_ai["ai_text_model"]).strip()
                if saved_ai.get("ai_vision_model"):
                    ai_vision_model = str(saved_ai["ai_vision_model"]).strip()
                if saved_ai.get("ai_audio_model"):
                    ai_audio_model = str(saved_ai["ai_audio_model"]).strip()
                if "auto_fallback" in saved_ai:
                    ai_auto_fallback = bool(saved_ai["auto_fallback"])
                sources.append(f"state:{state_ai_path}")
        except Exception:
            pass

    local_appdata = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    capcut_override = env_get("CAPCUT_DRAFTS_DIR")
    default_capcut = local_appdata / "CapCut/User Data/Projects/com.lveditor.draft"
    capcut_dir = Path(capcut_override) if capcut_override else default_capcut

    cfg = StudioConfig(
        root_dir=root,
        system_dir=system_dir,
        channels_config_dir=channels_dir,
        exa_api_key=exa_key,
        pexels_api_key=env_get("PEXELS_API_KEY"),
        pixabay_api_key=env_get("PIXABAY_API_KEY"),
        ai_provider_preset=ai_provider_preset,
        ai_provider_name=ai_provider_name,
        ai_base_url=ai_base_url,
        ai_api_key=ai_key,
        ai_vision_model=ai_vision_model,
        ai_audio_model=ai_audio_model,
        ai_text_model=ai_text_model,
        ai_auto_fallback=ai_auto_fallback,
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


AI_PROVIDER_PRESETS: List[Dict[str, str]] = [
    {
        "id": "local_proxy",
        "name": "Local Proxy (localhost:20128)",
        "base_url": "http://localhost:20128/v1",
        "default_text": "claude",
        "default_vision": "mistral/deepseek-v4-1-flash-1017",
        "default_audio": "claude",
    },
    {
        "id": "openai",
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_text": "gpt-4o-mini",
        "default_vision": "gpt-4o-mini",
        "default_audio": "gpt-4o-audio-preview",
    },
    {
        "id": "openrouter",
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_text": "anthropic/claude-3.5-haiku",
        "default_vision": "google/gemini-2.5-flash",
        "default_audio": "google/gemini-2.5-flash",
    },
    {
        "id": "gemini",
        "name": "Google Gemini (OpenAI Compat)",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_text": "gemini-2.5-flash",
        "default_vision": "gemini-2.5-flash",
        "default_audio": "gemini-2.5-flash",
    },
    {
        "id": "groq",
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_text": "llama-3.3-70b-versatile",
        "default_vision": "meta-llama/llama-4-scout-17b-16e-instruct",
        "default_audio": "whisper-large-v3-turbo",
    },
    {
        "id": "deepseek",
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_text": "deepseek-chat",
        "default_vision": "deepseek-chat",
        "default_audio": "deepseek-chat",
    },
    {
        "id": "mistral",
        "name": "Mistral AI",
        "base_url": "https://api.mistral.ai/v1",
        "default_text": "mistral-small-latest",
        "default_vision": "pixtral-12b-2409",
        "default_audio": "mistral-small-latest",
    },
    {
        "id": "ollama",
        "name": "Ollama Local (11434)",
        "base_url": "http://localhost:11434/v1",
        "default_text": "qwen2.5:7b",
        "default_vision": "llava:latest",
        "default_audio": "qwen2.5:7b",
    },
    {
        "id": "custom",
        "name": "Custom OpenAI-Compatible",
        "base_url": "",
        "default_text": "",
        "default_vision": "",
        "default_audio": "",
    },
]


def _mask_secret(secret: str) -> str:
    s = (secret or "").strip()
    if not s:
        return ""
    if len(s) <= 8:
        return "*" * len(s)
    return f"{s[:4]}...{s[-4:]}"


def ai_settings_path(config: Optional[StudioConfig] = None) -> Path:
    cfg = config or get_config()
    return cfg.system_dir / "state" / "ai_provider.json"


def clear_live_models_cache() -> None:
    _LIVE_MODELS_CACHE.clear()


_LIVE_MODELS_CACHE: Dict[str, Tuple[float, List[str]]] = {}


def fetch_provider_models(
    base_url: str,
    api_key: str = "",
    timeout_s: float = 5.0,
) -> Dict[str, Any]:
    """Fetch live model list from any OpenAI-compatible `{base_url}/models` endpoint."""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        return {"ok": False, "models": [], "latency_ms": 0, "error": "Base URL kosong."}
    t0 = time.perf_counter()
    headers = {"Accept": "application/json", "User-Agent": "EnterpriseShortsStudio/3.0"}
    if api_key and api_key.strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    try:
        req = urllib.request.Request(f"{base}/models", headers=headers)
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            status_code = getattr(resp, "status", 200)
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        raw_list = payload.get("data") if isinstance(payload, dict) else payload
        if not isinstance(raw_list, list):
            raw_list = payload.get("models", []) if isinstance(payload, dict) else []
        models: List[str] = []
        for item in raw_list:
            if isinstance(item, dict):
                mid = item.get("id") or item.get("name")
                if mid:
                    models.append(str(mid))
            elif isinstance(item, str) and item.strip():
                models.append(item.strip())
        models = sorted(dict.fromkeys(models))
        latency_ms = round((time.perf_counter() - t0) * 1000)
        return {"ok": True, "status_code": status_code, "models": models, "latency_ms": latency_ms, "error": ""}
    except urllib.error.HTTPError as he:
        latency_ms = round((time.perf_counter() - t0) * 1000)
        body = ""
        try:
            body = he.read().decode("utf-8", errors="ignore")[:300]
        except Exception:
            pass
        return {
            "ok": False,
            "status_code": he.code,
            "models": [],
            "latency_ms": latency_ms,
            "error": f"HTTP {he.code}: {body or he.reason}",
        }
    except Exception as exc:
        latency_ms = round((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "status_code": 0,
            "models": [],
            "latency_ms": latency_ms,
            "error": str(exc),
        }


def test_ai_provider_connection(
    base_url: str,
    api_key: str = "",
    model: str = "",
    run_chat_ping: bool = True,
    timeout_s: float = 12.0,
) -> Dict[str, Any]:
    """Verify endpoint reachability, model listing, and optional live chat completion ping."""
    models_res = fetch_provider_models(base_url, api_key, timeout_s=min(6.0, timeout_s))
    discovered = models_res.get("models", [])
    test_model = (model or "").strip() or (discovered[0] if discovered else "")

    chat_res: Dict[str, Any] = {
        "tested": False,
        "ok": False,
        "model": test_model,
        "latency_ms": 0,
        "reply": "",
        "error": "",
    }
    if run_chat_ping and test_model and (base_url or "").strip():
        chat_res["tested"] = True
        t0 = time.perf_counter()
        try:
            import openai

            client = openai.OpenAI(
                base_url=base_url.strip().rstrip("/"),
                api_key=(api_key or "").strip() or "local",
                timeout=timeout_s,
                max_retries=0,
            )
            resp = client.chat.completions.create(
                model=test_model,
                messages=[{"role": "user", "content": "Reply with one word: OK"}],
                max_tokens=8,
                temperature=0.0,
            )
            content = resp.choices[0].message.content
            if isinstance(content, list):
                reply_text = " ".join(
                    str(b.get("text", "")) for b in content if isinstance(b, dict)
                ).strip()
            else:
                reply_text = str(content or "").strip()
            chat_res["ok"] = True
            chat_res["reply"] = reply_text[:80]
            chat_res["latency_ms"] = round((time.perf_counter() - t0) * 1000)
        except Exception as exc:
            chat_res["ok"] = False
            chat_res["error"] = str(exc)[:400]
            chat_res["latency_ms"] = round((time.perf_counter() - t0) * 1000)

    overall_ok = bool(chat_res["ok"]) if chat_res["tested"] else bool(models_res.get("ok"))
    return {
        "ok": overall_ok,
        "models_ok": bool(models_res.get("ok")),
        "models_count": len(discovered),
        "models": discovered,
        "models_latency_ms": models_res.get("latency_ms", 0),
        "models_error": models_res.get("error", ""),
        "chat": chat_res,
    }


def get_ai_provider_settings(config: Optional[StudioConfig] = None) -> Dict[str, Any]:
    """Return current AI provider config (API key masked) plus presets and live resolved models."""
    cfg = config or get_config()
    return {
        "provider_preset": cfg.ai_provider_preset,
        "provider_name": cfg.ai_provider_name,
        "ai_base_url": cfg.ai_base_url,
        "has_api_key": bool(cfg.ai_api_key),
        "api_key_masked": _mask_secret(cfg.ai_api_key),
        "ai_text_model": cfg.ai_text_model,
        "ai_vision_model": cfg.ai_vision_model,
        "ai_audio_model": cfg.ai_audio_model,
        "auto_fallback": cfg.ai_auto_fallback,
        "resolved_text_model": resolve_ai_model("text", cfg),
        "resolved_vision_model": resolve_ai_model("vision", cfg),
        "resolved_audio_model": resolve_ai_model("audio", cfg),
        "presets": AI_PROVIDER_PRESETS,
    }


def save_ai_provider_settings(
    payload: Dict[str, Any],
    config: Optional[StudioConfig] = None,
) -> Dict[str, Any]:
    """Persist custom AI provider config to gitignored `_sistem/state/ai_provider.json` & reload."""
    cfg = config or get_config()
    path = ai_settings_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: Dict[str, Any] = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            existing = {}

    preset = str(payload.get("provider_preset") or cfg.ai_provider_preset or "custom").strip()
    preset_meta = next((p for p in AI_PROVIDER_PRESETS if p["id"] == preset), None)
    provider_name = str(
        payload.get("provider_name")
        or (preset_meta["name"] if preset_meta else "")
        or cfg.ai_provider_name
        or "Custom Provider"
    ).strip()
    base_url = str(payload.get("ai_base_url") or cfg.ai_base_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("Base URL tidak boleh kosong.")

    clear_key = bool(payload.get("clear_api_key", False))
    incoming_key = payload.get("ai_api_key")
    if clear_key:
        final_key = ""
    elif incoming_key is not None and str(incoming_key).strip():
        final_key = str(incoming_key).strip()
    else:
        final_key = str(existing.get("ai_api_key") or cfg.ai_api_key or "").strip()

    text_model = str(payload.get("ai_text_model") or cfg.ai_text_model or "").strip()
    vision_model = str(payload.get("ai_vision_model") or cfg.ai_vision_model or text_model).strip()
    audio_model = str(payload.get("ai_audio_model") or cfg.ai_audio_model or text_model).strip()
    auto_fallback = bool(payload.get("auto_fallback", cfg.ai_auto_fallback))

    record = {
        "provider_preset": preset,
        "provider_name": provider_name,
        "ai_base_url": base_url,
        "ai_api_key": final_key,
        "clear_api_key": clear_key,
        "ai_text_model": text_model,
        "ai_vision_model": vision_model,
        "ai_audio_model": audio_model,
        "auto_fallback": auto_fallback,
    }
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding="utf-8")
    clear_live_models_cache()
    reloaded = load_config(force_reload=True)
    return get_ai_provider_settings(reloaded)


def discover_live_ai_models(config: Optional[StudioConfig] = None, ttl_s: float = 120.0) -> List[str]:
    """Query `{ai_base_url}/models` with short timeout & TTL cache so dead model IDs auto-fallback."""
    cfg = config or get_config()
    base = (cfg.ai_base_url or "").rstrip("/")
    if not base:
        return []
    cache_key = f"{base}|{bool(cfg.ai_api_key)}"
    now = time.monotonic()
    cached = _LIVE_MODELS_CACHE.get(cache_key)
    if cached and (now - cached[0]) < ttl_s:
        return cached[1]
    res = fetch_provider_models(base, cfg.ai_api_key, timeout_s=1.8)
    models = res.get("models", []) if res.get("ok") else []
    _LIVE_MODELS_CACHE[cache_key] = (now, models)
    return models


def resolve_ai_model(kind: str = "text", config: Optional[StudioConfig] = None) -> str:
    """Return configured AI model if active on provider, else pick best vetted live model."""
    cfg = config or get_config()
    if kind == "vision":
        preferred = cfg.ai_vision_model
    elif kind == "audio":
        preferred = cfg.ai_audio_model
    else:
        preferred = cfg.ai_text_model

    if not cfg.ai_auto_fallback:
        return preferred

    live = discover_live_ai_models(cfg)
    if not live:
        return preferred
    if preferred in live:
        return preferred

    if kind == "vision":
        priority = [
            "mistral/deepseek-v4-1-flash-1017",
            "gpt-4o-mini",
            "gpt-4o",
            "gemini-2.5-flash",
            "claude",
            "mistral/mistral-large-latest",
            "upstage/solar-pro4",
        ]
    elif kind == "audio":
        priority = [
            "gemini-2.5-flash",
            "gpt-4o-audio-preview",
            "mistral/deepseek-v4-1-flash-1017",
            "claude",
            "mistral/mistral-large-latest",
        ]
    else:
        priority = [
            "claude",
            "gpt-4o-mini",
            "gemini-2.5-flash",
            "mistral/mistral-small-2603",
            "mistral/deepseek-v4-1-flash-1017",
            "upstage/solar-mini4",
            "mistral/mistral-large-latest",
        ]
    for cand in priority:
        if cand in live:
            return cand
    return live[0]

