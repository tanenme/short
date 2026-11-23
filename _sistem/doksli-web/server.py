"""Enterprise YouTube Shorts Production Studio — FastAPI Backend (v3.0)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import traceback
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Dict, List, Literal, Optional

import openai
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel

SISTEM_DIR = Path(__file__).resolve().parent.parent
EDITOR_ENGINE_DIR = SISTEM_DIR / "editor-engine"
for p in (str(SISTEM_DIR), str(EDITOR_ENGINE_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import get_config, load_config
from core.db import StudioDB
from core.models import EpisodeManifest, EpisodeStatus
from core.tts import generate_episode_voiceover, list_elevenlabs_voices
from core.validator import audit_episode_folder
from analytics.engine import compute_channel_analytics, generate_weekly_report
from analytics.scaler import provision_channel
from sourcing.licensing import generate_credits_markdown
from sourcing.preflight import run_preflight_gate
from sourcing.providers import download_to_episode, search_unified
from sourcing.qc_video import run_episode_autoqc

try:
    import builder
except Exception as e:
    print(f"[WARN] Failed to import builder: {e}")
    builder = None

CFG = get_config()
DB = StudioDB(config=CFG)
APP_DIR = Path(__file__).resolve().parent
BASE_DIR = CFG.channel_dir()
EXA_KEY_DEFAULT = CFG.exa_api_key

EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="studio-job")

app = FastAPI(title="Enterprise Shorts Studio", version="3.0.0")


def get_local_vision_client():
    return openai.OpenAI(base_url=CFG.ai_base_url, api_key=CFG.ai_api_key or "local")


def _submit_background_job(
    channel_id: str,
    episode_id: str,
    job_type: str,
    fn: Callable[[], dict],
) -> str:
    job_id = DB.create_job(channel_id, episode_id, job_type, progress_msg=f"Menjalankan {job_type}...")

    def _runner():
        DB.update_job(job_id, status="running", progress_msg=f"Sedang memproses {job_type} untuk {episode_id}...")
        try:
            res = fn()
            DB.update_job(job_id, status="succeeded", progress_msg="Selesai", result=res)
            try:
                DB.sync_channel(channel_id)
            except Exception:
                pass
        except Exception as exc:
            DB.update_job(job_id, status="failed", progress_msg="Gagal", error=str(exc))

    EXECUTOR.submit(_runner)
    return job_id


# ---------------- Keyword Dictionaries (Legacy Doksli Helper) ----------------
ANIMAL_EN = {
    "kucing": ["cat", "cats", "kitten", "feline"],
    "anjing": ["dog", "dogs", "puppy", "canine"],
    "bebek": ["duck", "ducks", "duckling", "mallard"],
    "ayam": ["chicken", "rooster", "hen"],
    "sapi": ["cow", "cows", "cattle", "calf"],
    "cicak": ["gecko", "house gecko", "lizard"],
    "burung": ["bird", "birds"],
    "kuda laut": ["seahorse", "seahorses"],
    "kukang": ["slow loris", "loris"],
}

BEHAVIOR_EN = {
    "ek-ek": ["chattering", "chirping", "ekekek", "chittering"],
    "dorong": ["knocking off", "pushing off table", "batting"],
    "gelas": ["glass", "cup", "bottle"],
    "jatuh": ["falling", "dropping"],
    "nendang": ["kicking grass", "scratching ground", "back legs"],
    "buang air": ["after poop", "after pee", "potty"],
    "pup": ["pooping", "poop"],
    "melek": ["one eye open", "unihemispheric sleep"],
    "tidur": ["sleeping", "nap"],
    "goyang": ["shaking body", "steadycam", "stabilization"],
    "stabil": ["head stabilization", "steady head"],
    "gimbal": ["gimbal", "steady cam"],
    "mengunyah": ["chewing cud", "rumination", "chewing"],
    "rumput": ["grass", "pasture"],
    "bersin": ["sneezing while playing", "play sneeze"],
    "gulat": ["play fighting", "wrestling"],
    "berbaris": ["swimming in row", "formation swimming", "drafting"],
    "berkokok": ["rooster crowing dawn", "crowing morning"],
    "subuh": ["dawn", "early morning"],
    "mata menyala": ["glowing eyes", "tapetum lucidum", "eyes shine"],
    "tembok": ["wall", "blank wall"],
    "hantu": ["staring at nothing", "greebles"],
    "meong": ["meowing at owner", "talking cat"],
}


# ---------------- Request Schemas ----------------
class ExtractRequest(BaseModel):
    naskah: str
    pkg_id: Optional[str] = None


class SearchRequest(BaseModel):
    query: str
    platforms: List[str]
    num_results: int = 10
    raw_mode: bool = False
    preset: Optional[str] = None
    api_key: Optional[str] = None


class CleanlinessRequest(BaseModel):
    url: str
    platform: str
    api_key: Optional[str] = None


class VisionProEvalRequest(BaseModel):
    url: str
    platform: str
    scenario: str
    check_overlay: bool = True
    check_vertical: bool = True
    check_scenario: bool = True
    min_overlay_score: int = 60
    min_match_score: int = 50
    api_key: Optional[str] = None


class EditorActionRequest(BaseModel):
    package_name: str
    channel_id: Optional[str] = None
    strict_gate: bool = False


class UnifiedSearchRequest(BaseModel):
    query: str
    providers: Optional[List[str]] = None
    limit: int = 12
    cc_only: bool = False
    raw_mode: bool = True
    api_key: Optional[str] = None


class DownloadClipRequest(BaseModel):
    url: str
    package_name: str
    channel_id: Optional[str] = None
    title: str = ""
    platform: str = ""
    creator: str = ""
    license: str = ""
    license_url: str = ""
    page_url: str = ""


class CreateEpisodeRequest(BaseModel):
    channel_id: str = "C1-fakta"
    slug: str
    title: str
    subject: str = ""


class EnqueueJobRequest(BaseModel):
    channel_id: str = "C1-fakta"
    package_name: str
    job_type: Literal["render", "capcut", "autoqc", "vo_tts"]
    strict_gate: bool = False
    voice_id: Optional[str] = None


class TtsGenerateRequest(BaseModel):
    channel_id: str = "C1-fakta"
    package_name: str
    voice_id: Optional[str] = None


class RecordMetricsRequest(BaseModel):
    channel_id: str = "C1-fakta"
    episode_id: str
    views: int
    likes: int = 0
    comments: int = 0
    subs_gained: int = 0
    avg_view_pct: float = 0.0
    swipe_away_pct: float = 0.0


class ProvisionChannelRequest(BaseModel):
    channel_id: str
    display_name: Optional[str] = None
    niche: Optional[str] = None
    source_channel: str = "C1-fakta"
    copy_audio_bank: bool = True
    copy_overlays: bool = True
    activate: bool = True


# ---------------- Studio Control Plane (Multi-Channel + SQLite) ----------------
@app.get("/api/studio/health")
def api_studio_health():
    return {
        "status": "ok",
        "version": app.version,
        "ffmpeg_ready": CFG.ffmpeg_bin is not None,
        "ffprobe_ready": CFG.ffprobe_bin is not None,
        "ytdlp_ready": CFG.ytdlp_bin is not None,
        "exa_configured": bool(CFG.exa_api_key),
        "ai_configured": bool(CFG.ai_api_key),
        "elevenlabs_configured": bool(CFG.elevenlabs_api_key),
        "channels_count": len(CFG.channels),
    }


@app.get("/api/channels")
def list_channels():
    return {
        "default_channel": CFG.default_channel_id,
        "channels": [p.model_dump() for p in CFG.channels.values()],
    }


@app.get("/api/studio/board")
def api_studio_board(channel_id: Optional[str] = None, refresh: bool = True):
    cid = CFG.get_channel(channel_id).channel_id
    episodes = DB.sync_channel(cid) if refresh else DB.list_episodes(cid)
    summary = {
        "total": len(episodes),
        "ready_to_publish": sum(1 for e in episodes if e["preflight_verdict"] == "READY_TO_PUBLISH"),
        "ready_to_render": sum(1 for e in episodes if e["preflight_verdict"] == "READY_TO_RENDER"),
        "blocked": sum(1 for e in episodes if e["preflight_verdict"] == "BLOCKED"),
        "rendered": sum(1 for e in episodes if e["has_output"]),
    }
    return {
        "success": True,
        "channel_id": cid,
        "channel": CFG.get_channel(cid).model_dump(),
        "summary": summary,
        "episodes": episodes,
        "recent_jobs": DB.list_jobs(limit=15),
    }


@app.post("/api/studio/sync")
def api_studio_sync(channel_id: Optional[str] = None):
    cid = CFG.get_channel(channel_id).channel_id
    eps = DB.sync_channel(cid)
    return {"success": True, "channel_id": cid, "synced_count": len(eps)}


@app.post("/api/episodes/create")
def api_create_episode(req: CreateEpisodeRequest):
    prof = CFG.get_channel(req.channel_id)
    slug = re.sub(r"[^a-z0-9\-]+", "-", req.slug.strip().lower()).strip("-")
    if not slug:
        raise HTTPException(status_code=400, detail="Slug episode tidak valid.")
    ep_dir = CFG.episode_dir(slug, prof.channel_id)
    if ep_dir.exists():
        raise HTTPException(status_code=409, detail=f"Episode '{slug}' sudah ada di {prof.channel_id}.")

    (ep_dir / "assets").mkdir(parents=True, exist_ok=True)
    (ep_dir / "output").mkdir(parents=True, exist_ok=True)

    manifest = EpisodeManifest(
        episode_id=slug,
        channel_id=prof.channel_id,
        title=req.title.strip(),
        subject=req.subject.strip(),
        status=EpisodeStatus.IDEA,
    )
    (ep_dir / "episode.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    cta_line = prof.cta.pattern.format(subject=req.subject.strip() or "[subjek]")
    (ep_dir / "naskah.md").write_text(
        f"# Naskah — {slug} {req.title.strip()}\n\n"
        f"Channel: {prof.channel_id} | Engine VO: {prof.voice.engine} | Target: {prof.pacing.min_words}-{prof.pacing.max_words} kata\n\n"
        f"## VO final Eleven v4\n\n"
        f"[curious, fast-paced] {req.title.strip()}\n\n"
        f"[skeptical, then firm] Banyak orang kira ..., padahal BUKAN!\n\n"
        f"[warm, conversational] {cta_line}\n\n"
        f"[intrigued, hushed revelation] ... [excited] ...\n\n"
        f"[punchy, amused conclusion] Makanya ...\n",
        encoding="utf-8",
    )
    (ep_dir / "brief-edit.md").write_text(
        f"# Brief Edit — {slug} {req.title.strip()}\n\n"
        f"| # | Detik | Visual (klip + transformasi) | VO / Naskah | Teks layar | Musik / SFX |\n"
        f"|---|---|---|---|---|---|\n"
        f"| 1 | 0.0–2.0 | Hook visual | \"{req.title.strip()}\" | HOOK | whoosh |\n",
        encoding="utf-8",
    )
    (ep_dir / "sources.md").write_text(
        f"# Sources — {slug}\n\n"
        f"## Sumber fakta tertulis (minimal {prof.ypp.min_tier1_fact_sources} sumber tier-1)\n"
        f"1. \n2. \n",
        encoding="utf-8",
    )
    (ep_dir / "paket-upload.txt").write_text(
        f"PAKET UPLOAD — {slug}\nJUDUL:\n1. {req.title.strip()} #shorts\n\nDESKRIPSI:\n\n#shorts\n",
        encoding="utf-8",
    )

    DB.sync_channel(prof.channel_id)
    return {"success": True, "channel_id": prof.channel_id, "episode_id": slug, "path": str(ep_dir)}


# ---------------- Background Job Queue ----------------
@app.get("/api/jobs")
def api_list_jobs(limit: int = 20):
    return {"success": True, "jobs": DB.list_jobs(limit=limit)}


@app.get("/api/jobs/{job_id}")
def api_get_job(job_id: str):
    job = DB.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job tidak ditemukan.")
    return {"success": True, "job": job}


@app.post("/api/jobs/enqueue")
def api_enqueue_job(req: EnqueueJobRequest):
    cid = CFG.get_channel(req.channel_id).channel_id

    if req.job_type == "render":
        if not builder:
            raise HTTPException(status_code=500, detail="Editor engine tidak tersedia.")
        job_id = _submit_background_job(
            cid,
            req.package_name,
            "render",
            lambda: {
                "output_path": builder.render_ffmpeg_direct(
                    req.package_name,
                    channel_id=cid,
                    enforce_preflight=req.strict_gate,
                )
            },
        )
    elif req.job_type == "capcut":
        if not builder:
            raise HTTPException(status_code=500, detail="Editor engine tidak tersedia.")
        job_id = _submit_background_job(
            cid,
            req.package_name,
            "capcut",
            lambda: {"draft_path": builder.build_capcut_project(req.package_name, channel_id=cid)},
        )
    elif req.job_type == "autoqc":
        job_id = _submit_background_job(
            cid,
            req.package_name,
            "autoqc",
            lambda: run_episode_autoqc(req.package_name, cid, CFG, write_report=True).model_dump(),
        )
    elif req.job_type == "vo_tts":
        job_id = _submit_background_job(
            cid,
            req.package_name,
            "vo_tts",
            lambda: generate_episode_voiceover(req.package_name, cid, voice_id_override=req.voice_id, config=CFG),
        )
    else:
        raise HTTPException(status_code=400, detail=f"Tipe job tidak dikenal: {req.job_type}")

    return {"success": True, "job_id": job_id, "status": "queued"}


# ---------------- Packages & Script Extraction ----------------
@app.get("/api/packages")
def list_packages(channel_id: Optional[str] = None):
    ch_dir = CFG.channel_dir(channel_id)
    pkgs = []
    if ch_dir.exists():
        for d in sorted(ch_dir.iterdir()):
            if not d.is_dir() or d.name.startswith("_"):
                continue
            if d.name.lower() in ("bgm", "sfx", "backgorud sound", "effek sounf"):
                continue
            n_file = d / "naskah.md"
            title = d.name
            content = ""
            if n_file.exists():
                content = n_file.read_text(encoding="utf-8", errors="ignore")
                first_line = content.splitlines()[0] if content.splitlines() else d.name
                title = first_line.replace("# Naskah —", "").replace("#", "").strip()
            pkgs.append({"id": d.name, "title": title, "has_naskah": n_file.exists(), "content": content})
    return {"channel_id": CFG.get_channel(channel_id).channel_id, "packages": pkgs}


@app.post("/api/extract-keywords")
def extract_keywords(req: ExtractRequest):
    text = req.naskah.lower()
    detected_animals = [(id_a, en_list[0]) for id_a, en_list in ANIMAL_EN.items() if id_a in text]
    detected_behaviors = [(id_b, en_list[0]) for id_b, en_list in BEHAVIOR_EN.items() if id_b in text]

    queries = []
    clean_lines = [
        l.strip()
        for l in req.naskah.splitlines()
        if l.strip() and not l.startswith(("#", "-", "*", "Status", "Spesies", "Engine", "Batch", "Keputusan"))
    ]
    hook_line = ""
    for l in clean_lines:
        if any(h in l.lower() for h in ["kenapa", "pernah", "stop", "jangan", "[hook"]):
            hook_line = re.sub(r"\[.*?\]", "", l).strip()
            break

    if detected_animals and detected_behaviors:
        prim_a = detected_animals[0]
        b_en = " ".join(b[1] for b in detected_behaviors[:3])
        b_id = " ".join(b[0] for b in detected_behaviors[:3])
        queries.extend(
            [
                f"{prim_a[1]} {b_en}",
                f"raw footage {prim_a[1]} {b_en}",
                f"cute {prim_a[1]} {b_en} close up",
                f"pet camera {prim_a[1]} {b_en}",
                f"{prim_a[0]} {b_id}",
            ]
        )
        if hook_line:
            queries.append(re.sub(r"[?!.,]", "", hook_line))
    elif hook_line:
        queries.append(re.sub(r"[?!.,]", "", hook_line))
    else:
        words = [
            w
            for w in re.findall(r"\b[a-z]{4,}\b", text)
            if w not in ("banyak", "orang", "pikir", "kalau", "kamu", "suka", "subscribe", "makanya", "ternyata")
        ]
        queries.append(" ".join(words[:4]))

    return {
        "detected_animals": detected_animals,
        "detected_behaviors": detected_behaviors,
        "recommended_queries": list(dict.fromkeys(queries))[:6],
    }


# ---------------- Sourcing & Vision Pro ----------------
def search_reddit_keenable(query: str, limit: int = 10) -> List[dict]:
    from sourcing.providers import search_reddit_keenable as _keenable

    return [
        {
            "title": c.title,
            "url": c.url,
            "platform": c.platform,
            "thumbnail": c.thumbnail,
            "author": c.creator,
            "published_date": "",
            "highlights": [c.snippet],
            "snippet": c.snippet,
            "license": c.license,
            "risk_level": c.risk_level,
        }
        for c in _keenable(query, limit=limit)
    ]


@app.post("/api/search")
def search_exa(req: SearchRequest):
    key = req.api_key or CFG.exa_api_key
    reddit_requested = (req.preset == "reddit_raw") or any("reddit" in p.lower() for p in req.platforms)
    non_reddit_platforms = [p for p in req.platforms if "reddit" not in p.lower()]

    if not key and non_reddit_platforms and req.preset != "reddit_raw":
        raise HTTPException(status_code=400, detail="Exa API Key belum diset di .env maupun input.")

    from sourcing.providers import search_exa as _search_exa

    results = []
    if non_reddit_platforms and req.preset != "reddit_raw":
        hits = _search_exa(
            req.query,
            non_reddit_platforms,
            limit=req.num_results,
            raw_mode=req.raw_mode,
            preset=req.preset,
            api_key=key,
            config=CFG,
        )
        for h in hits:
            results.append(
                {
                    "title": h.title,
                    "url": h.url,
                    "platform": h.platform,
                    "thumbnail": h.thumbnail,
                    "author": h.creator,
                    "published_date": "",
                    "highlights": [h.snippet],
                    "snippet": h.snippet,
                    "license": h.license,
                    "risk_level": h.risk_level,
                }
            )

    if reddit_requested:
        results.extend(search_reddit_keenable(req.query, limit=min(req.num_results, 10)))

    return {"success": True, "count": len(results), "query_used": req.query, "results": results}


def resolve_video_thumbnail(u: str) -> Optional[str]:
    if "youtube.com" in u or "youtu.be" in u:
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
        if m:
            return f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
    elif "tiktok.com" in u:
        try:
            res = subprocess.run([CFG.ytdlp_cmd(), "--print", "thumbnail", "--no-warnings", u], capture_output=True, text=True, timeout=8)
            t = res.stdout.strip()
            if t.startswith("http"):
                return t
        except Exception:
            pass
    if any(ext in u.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
        return u
    return None


@app.post("/api/vision-pro-evaluate")
async def vision_pro_evaluate(req: VisionProEvalRequest):
    u = req.url
    thumb_url = resolve_video_thumbnail(u)
    if not thumb_url:
        return {"success": False, "error": "Thumbnail video tidak dapat diambil otomatis untuk URL ini."}

    is_short_url = "shorts/" in u or "tiktok.com" in u or "reels/" in u
    scenario = req.scenario.strip() or "video klip hewan menarik"
    prompt = f"""Kamu adalah asisten kurasi footage video YouTube Shorts. Evaluasi gambar thumbnail/frame ini berdasarkan skenario: "{scenario}".

Periksa 3 aspek:
1. Bebas Overlay: Apakah frame ini bersih dari burned-in subtitle besar, banner teks menutupi tengah, teks meme, atau stiker?
2. Format 9:16: Apakah komposisi visual utamanya vertikal 9:16? (URL: {'Shorts/TikTok/Reels' if is_short_url else 'Video biasa'}).
3. Kesesuaian Skenario: Seberapa cocok adegan dalam gambar dengan skenario: "{scenario}"? (0-100).

Jawab HANYA JSON valid:
{{
  "overlay_status": "BERSIH" | "OVERLAY_RINGAN" | "KOTOR",
  "overlay_score": <int 0-100>,
  "aspect_ratio": "9:16" | "16:9",
  "is_vertical": true | false,
  "match_score": <int 0-100>,
  "details": "<penjelasan 1 kalimat>"
}}"""

    try:
        client = get_local_vision_client()
        resp = client.chat.completions.create(
            model=CFG.ai_vision_model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": thumb_url}}]}],
            max_tokens=250,
            temperature=0.1,
        )
        raw_ans = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw_ans, re.DOTALL)
        if not m:
            return {"success": False, "error": "Format respons AI tidak valid", "raw": raw_ans}

        parsed = json.loads(m.group(0))
        overlay_score = parsed.get("overlay_score", 50)
        is_vert = parsed.get("is_vertical", False)
        match_score = parsed.get("match_score", 50)

        reasons_failed = []
        if req.check_overlay and overlay_score < req.min_overlay_score:
            reasons_failed.append(f"Terdeteksi teks/overlay tebal ({parsed.get('overlay_status')}, skor {overlay_score})")
        if req.check_vertical and not is_vert:
            reasons_failed.append(f"Bukan dimensi 9:16 ({parsed.get('aspect_ratio')})")
        if req.check_scenario and match_score < req.min_match_score:
            reasons_failed.append(f"Kurang cocok dengan skenario (skor {match_score})")

        total_score = round((overlay_score * 0.35) + (match_score * 0.45) + ((100 if is_vert else 20) * 0.20))
        return {
            "success": True,
            "thumbnail": thumb_url,
            "evaluation": {
                "passed": len(reasons_failed) == 0,
                "total_score": total_score,
                "overlay_status": parsed.get("overlay_status"),
                "overlay_score": overlay_score,
                "aspect_ratio": parsed.get("aspect_ratio"),
                "is_vertical": is_vert,
                "match_score": match_score,
                "reasons_failed": reasons_failed,
                "details": parsed.get("details", ""),
            },
        }
    except Exception as e:
        return {"success": False, "error": f"Vision error: {str(e)}"}


@app.post("/api/scan-cleanliness")
async def scan_cleanliness(req: CleanlinessRequest):
    thumb_url = resolve_video_thumbnail(req.url)
    if not thumb_url:
        return {"success": False, "error": "Thumbnail tidak dapat diambil otomatis."}
    prompt = (
        "Analisis apakah gambar ini memiliki teks, subtitle, watermark, atau stiker. "
        'Jawab HANYA JSON: {"status": "BERSIH" | "OVERLAY_PINGGIR" | "KOTOR", "score": <0-100>, "details": "<1 kalimat>"}'
    )
    try:
        client = get_local_vision_client()
        resp = client.chat.completions.create(
            model=CFG.ai_vision_model,
            messages=[{"role": "user", "content": [{"type": "text", "text": prompt}, {"type": "image_url", "image_url": {"url": thumb_url}}]}],
            max_tokens=200,
        )
        raw_ans = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw_ans, re.DOTALL)
        parsed = json.loads(m.group(0)) if m else {"status": "OVERLAY_PINGGIR", "score": 75, "details": raw_ans[:120]}
        return {"success": True, "thumbnail": thumb_url, "cleanliness": parsed}
    except Exception as e:
        return {"success": False, "error": f"Vision error: {str(e)}", "trace": traceback.format_exc()}


@app.post("/api/sourcing/unified-search")
def api_unified_search(req: UnifiedSearchRequest):
    hits = search_unified(
        query=req.query,
        providers=req.providers,
        limit=req.limit,
        cc_only=req.cc_only,
        raw_mode=req.raw_mode,
        api_key=req.api_key,
        config=CFG,
    )
    return {"success": True, "count": len(hits), "results": [h.model_dump() for h in hits]}


@app.post("/api/sourcing/download")
def api_download_clip(req: DownloadClipRequest):
    try:
        res = download_to_episode(
            url=req.url,
            episode_id=req.package_name,
            channel_id=req.channel_id,
            title=req.title,
            platform=req.platform,
            creator=req.creator,
            license_str=req.license,
            license_url=req.license_url,
            page_url=req.page_url,
            config=CFG,
        )
        DB.sync_channel(req.channel_id)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------- Audit, Pre-Flight, AutoQC, Credits, TTS ----------------
@app.get("/api/audit/{package_name}")
def audit_package(package_name: str, channel_id: Optional[str] = None):
    try:
        manifest, report = audit_episode_folder(package_name, channel_id, CFG)
        return {
            "success": True,
            "report": report.model_dump(),
            "status": manifest.status.value,
            "word_count": manifest.script.word_count,
            "clips_count": len(manifest.clips),
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/preflight/{package_name}")
def api_preflight(package_name: str, channel_id: Optional[str] = None, run_autoqc: bool = False):
    try:
        rep = run_preflight_gate(package_name, channel_id, run_autoqc=run_autoqc, config=CFG, write_report=True)
        return {"success": True, "preflight": rep.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/autoqc/{package_name}")
def api_autoqc(package_name: str, channel_id: Optional[str] = None):
    try:
        res = run_episode_autoqc(package_name, channel_id, CFG, write_report=True)
        return {"success": True, "autoqc": res.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/credits/{package_name}")
def api_credits(package_name: str, channel_id: Optional[str] = None):
    try:
        md, out_path = generate_credits_markdown(package_name, channel_id, CFG, write_file=True)
        return {"success": True, "credits_file": str(out_path), "markdown": md}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/tts/voices")
def api_tts_voices():
    try:
        return {"success": True, "voices": list_elevenlabs_voices(config=CFG)}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/tts/generate")
def api_tts_generate(req: TtsGenerateRequest):
    try:
        res = generate_episode_voiceover(req.package_name, req.channel_id, voice_id_override=req.voice_id, config=CFG)
        DB.sync_channel(req.channel_id)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------- Phase 5: Analytics Feedback Loop & Multi-Channel Scale ----------------
@app.get("/api/analytics/summary")
def api_analytics_summary(channel_id: Optional[str] = None):
    try:
        summary = compute_channel_analytics(channel_id, DB, CFG)
        return {"success": True, "summary": summary.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/analytics/record")
def api_analytics_record(req: RecordMetricsRequest):
    try:
        cid = CFG.get_channel(req.channel_id).channel_id
        DB.record_metrics(
            cid,
            req.episode_id,
            views=req.views,
            likes=req.likes,
            comments=req.comments,
            subs_gained=req.subs_gained,
            avg_view_pct=req.avg_view_pct,
            swipe_away_pct=req.swipe_away_pct,
        )
        summary = compute_channel_analytics(cid, DB, CFG)
        return {"success": True, "summary": summary.model_dump()}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/analytics/report")
def api_analytics_report(channel_id: Optional[str] = None):
    try:
        md, out_path = generate_weekly_report(channel_id, DB, CFG, write_file=True)
        return {"success": True, "report_path": str(out_path), "markdown": md}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/channels/provision")
def api_provision_channel(req: ProvisionChannelRequest):
    global CFG
    try:
        res = provision_channel(
            channel_id=req.channel_id,
            display_name=req.display_name,
            niche=req.niche,
            source_channel=req.source_channel,
            copy_audio_bank=req.copy_audio_bank,
            copy_overlays=req.copy_overlays,
            activate=req.activate,
            config=CFG,
        )
        CFG = load_config(force_reload=True)
        return res
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------- Editor Engine Endpoints ----------------
@app.get("/api/editor/status/{package_name}")
def get_editor_status(package_name: str, channel_id: Optional[str] = None):
    if not builder:
        return {"success": False, "error": "Editor engine not initialized"}
    cid = CFG.get_channel(channel_id).channel_id
    pkg_dir = CFG.episode_dir(package_name, cid)
    if not pkg_dir.exists():
        return {"success": False, "error": "Paket tidak ditemukan"}

    brief_file = pkg_dir / "brief-edit.md"
    has_brief = brief_file.exists()
    scenes_count = len(builder.parse_brief(brief_file)) if has_brief else 0

    assets_dir = CFG.episode_assets_dir(package_name, cid)
    vo_files = list(assets_dir.glob("*.mp3")) if assets_dir.exists() else []
    has_vo = len(vo_files) > 0
    vo_file = max(vo_files, key=lambda f: f.stat().st_mtime) if has_vo else None
    vo_duration = builder.get_media_duration(vo_file) if vo_file else 0.0

    clips = (
        [p for p in assets_dir.iterdir() if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".jpg", ".jpeg", ".png"} and not p.name.startswith(("thumb_", "_temp_"))]
        if assets_dir.exists()
        else []
    )
    output_dir = pkg_dir / "output"
    output_file = output_dir / f"final_{package_name}.mp4"
    qc_sheet = output_dir / f"qc_sheet_{package_name}.jpg"

    return {
        "success": True,
        "channel_id": cid,
        "package_name": package_name,
        "has_brief": has_brief,
        "scenes_count": scenes_count,
        "has_vo": has_vo,
        "vo_name": vo_file.name if vo_file else "",
        "vo_duration": round(vo_duration, 2),
        "clips_count": len(clips),
        "has_output": output_file.exists(),
        "has_qc_sheet": qc_sheet.exists(),
        "output_file": str(output_file) if output_file.exists() else "",
    }


@app.post("/api/editor/build-capcut")
def api_build_capcut(req: EditorActionRequest):
    if not builder:
        raise HTTPException(status_code=500, detail="Editor engine not loaded")
    try:
        draft_path = builder.build_capcut_project(req.package_name, channel_id=req.channel_id)
        return {"success": True, "draft_path": draft_path, "message": f"Draft CapCut berhasil dibuat: {Path(draft_path).name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.post("/api/editor/render-direct")
def api_render_direct(req: EditorActionRequest):
    if not builder:
        raise HTTPException(status_code=500, detail="Editor engine not loaded")
    try:
        output_mp4 = builder.render_ffmpeg_direct(
            req.package_name,
            channel_id=req.channel_id,
            enforce_preflight=req.strict_gate,
        )
        DB.sync_channel(req.channel_id)
        return {"success": True, "output_path": output_mp4, "message": f"Render MP4 v3 selesai: {Path(output_mp4).name}"}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/api/editor/video/{package_name}")
def api_get_rendered_video(package_name: str, channel_id: Optional[str] = None):
    output_file = CFG.episode_dir(package_name, channel_id) / "output" / f"final_{package_name}.mp4"
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="File video belum di-render.")
    return FileResponse(str(output_file), media_type="video/mp4")


@app.get("/api/editor/qc-sheet/{package_name}")
def api_get_qc_sheet(package_name: str, channel_id: Optional[str] = None):
    qc_file = CFG.episode_dir(package_name, channel_id) / "output" / f"qc_sheet_{package_name}.jpg"
    if not qc_file.exists():
        raise HTTPException(status_code=404, detail="QC contact sheet belum tersedia.")
    return FileResponse(str(qc_file), media_type="image/jpeg")


@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = APP_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Enterprise Shorts Studio: index.html not found</h1>")


if __name__ == "__main__":
    print("Starting Enterprise Shorts Studio v3 at http://127.0.0.1:8000")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
