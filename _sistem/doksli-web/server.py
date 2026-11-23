import os, re, sys, glob, json, traceback, urllib.request, subprocess
from pathlib import Path
from typing import List, Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import uvicorn

# Centralized Studio Configuration
SISTEM_DIR = Path(__file__).resolve().parent.parent
if str(SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SISTEM_DIR))

from core.config import get_config
from core.validator import audit_episode_folder

CFG = get_config()

# Standalone Vision Client using standard openai
import openai

def get_local_vision_client():
    return openai.OpenAI(base_url=CFG.ai_base_url, api_key=CFG.ai_api_key or "local")

app = FastAPI(title="Doksli Finder Local", version="2.1.0")

BASE_DIR = CFG.channel_dir()
APP_DIR = Path(__file__).resolve().parent
EXA_KEY_DEFAULT = CFG.exa_api_key

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

@app.get("/api/packages")
def list_packages():
    pkgs = []
    if BASE_DIR.exists():
        for d in sorted(BASE_DIR.iterdir()):
            if d.is_dir() and not d.name.startswith("_"):
                n_file = d / "naskah.md"
                title = d.name
                content = ""
                if n_file.exists():
                    content = n_file.read_text(encoding="utf-8")
                    first_line = content.splitlines()[0] if content.splitlines() else d.name
                    title = first_line.replace("# Naskah —", "").replace("#", "").strip()
                pkgs.append({
                    "id": d.name,
                    "title": title,
                    "has_naskah": n_file.exists(),
                    "content": content
                })
    return {"packages": pkgs}

@app.post("/api/extract-keywords")
def extract_keywords(req: ExtractRequest):
    text = req.naskah.lower()
    
    detected_animals = []
    for id_a, en_list in ANIMAL_EN.items():
        if id_a in text:
            detected_animals.append((id_a, en_list[0]))
            
    detected_behaviors = []
    for id_b, en_list in BEHAVIOR_EN.items():
        if id_b in text:
            detected_behaviors.append((id_b, en_list[0]))

    queries = []
    
    clean_lines = [l.strip() for l in req.naskah.splitlines() if l.strip() and not l.startswith(("#", "-", "*", "Status", "Spesies", "Engine", "Batch", "Keputusan"))]
    hook_line = ""
    for l in clean_lines:
        if any(h in l.lower() for h in ["kenapa", "pernah", "stop", "jangan", "[hook"]):
            hook_line = re.sub(r"\[.*?\]", "", l).strip()
            break

    if detected_animals and detected_behaviors:
        prim_a = detected_animals[0]
        b_en = " ".join([b[1] for b in detected_behaviors[:3]])
        b_id = " ".join([b[0] for b in detected_behaviors[:3]])
        
        # English queries (rich video results)
        queries.append(f"{prim_a[1]} {b_en}")
        queries.append(f"raw footage {prim_a[1]} {b_en}")
        queries.append(f"cute {prim_a[1]} {b_en} close up")
        queries.append(f"pet camera {prim_a[1]} {b_en}")
        
        # Indonesian queries
        queries.append(f"{prim_a[0]} {b_id}")
        if hook_line:
            clean_q = re.sub(r"[?!.,]", "", hook_line)
            queries.append(clean_q)
    elif hook_line:
        queries.append(re.sub(r"[?!.,]", "", hook_line))
    else:
        words = [w for w in re.findall(r"\b[a-z]{4,}\b", text) if w not in ("banyak", "orang", "pikir", "kalau", "kamu", "suka", "subscribe", "makanya", "ternyata")]
        queries.append(" ".join(words[:4]))

    return {
        "detected_animals": detected_animals,
        "detected_behaviors": detected_behaviors,
        "recommended_queries": list(dict.fromkeys(queries))[:6]
    }

def search_reddit_keenable(query: str, limit: int = 10) -> List[dict]:
    url = "https://api.keenable.ai/v1/search/public"
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', '', query).strip()
    payload = {
        "query": f"site:reddit.com/r/ {clean_q} video",
        "max_results": min(max(1, limit), 20)
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "X-Keenable-Title": "hermes-agent"
        }
    )
    results = []
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            data = json.loads(resp.read().decode())
            for idx, r in enumerate(data.get("results") or []):
                u = r.get("url") or ""
                if "reddit.com" in u:
                    sub_match = re.search(r"reddit\.com/r/([^/]+)", u)
                    sub_name = f"r/{sub_match.group(1)}" if sub_match else "r/Reddit"
                    
                    results.append({
                        "title": r.get("title") or "Reddit Video",
                        "url": u,
                        "platform": "reddit",
                        "thumbnail": None,
                        "author": sub_name,
                        "published_date": "",
                        "highlights": [r.get("snippet") or r.get("description") or ""],
                        "snippet": (r.get("snippet") or r.get("description") or "")[:200]
                    })
    except Exception as e:
        print("Reddit search error:", e)
    return results

@app.post("/api/search")
def search_exa(req: SearchRequest):
    key = req.api_key or EXA_KEY_DEFAULT
    if not key:
        raise HTTPException(status_code=400, detail="Exa API Key belum diisi.")
    
    # Check if reddit requested
    reddit_requested = (req.preset == "reddit_raw") or any("reddit" in p.lower() for p in req.platforms)
    non_reddit_platforms = [p for p in req.platforms if "reddit" not in p.lower()]
    
    final_query = req.query.strip()
    if req.preset == "stock_broll":
        non_reddit_platforms = ["pexels.com", "pixabay.com"]
    elif req.preset == "cctv_petcam":
        final_query = f"{final_query} pet camera OR cctv OR security cam"
    elif req.preset == "youtube_raw":
        non_reddit_platforms = ["youtube.com"]
        final_query = f"{final_query} raw footage OR uncut"

    if req.raw_mode and req.preset != "stock_broll":
        final_query = f'{final_query} (raw footage OR uncut OR "no text" OR original clip)'

    results = []

    # 1. Non-Reddit search via Exa
    if non_reddit_platforms and req.preset != "reddit_raw":
        domains = []
        for p in non_reddit_platforms:
            p_clean = p.lower().strip()
            if p_clean in ("youtube", "youtube.com"): domains.append("youtube.com")
            elif p_clean in ("tiktok", "tiktok.com"): domains.append("tiktok.com")
            elif p_clean in ("instagram", "instagram.com"): domains.append("instagram.com")
            elif p_clean in ("pexels", "pexels.com"): domains.append("pexels.com")
            elif p_clean: domains.append(p_clean)

        payload = {
            "query": final_query,
            "type": "auto",
            "numResults": min(req.num_results, 25),
            "contents": {
                "highlights": True,
                "text": True
            }
        }
        if domains:
            payload["includeDomains"] = domains

        url = "https://api.exa.ai/search"
        req_obj = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-api-key": key
            }
        )

        try:
            with urllib.request.urlopen(req_obj, timeout=25) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("results", []):
                    u = item.get("url", "")
                    plat = "web"
                    thumb = None
                    
                    if "youtube.com" in u or "youtu.be" in u:
                        plat = "youtube"
                        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
                        if m:
                            thumb = f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
                    elif "tiktok.com" in u:
                        plat = "tiktok"
                    elif "instagram.com" in u:
                        plat = "instagram"
                    elif "pexels.com" in u:
                        plat = "pexels"
                    
                    results.append({
                        "title": item.get("title") or "Video / Clip",
                        "url": u,
                        "platform": plat,
                        "thumbnail": thumb,
                        "author": item.get("author") or "",
                        "published_date": item.get("publishedDate") or "",
                        "highlights": item.get("highlights", []),
                        "snippet": (item.get("text") or "")[:200]
                    })
        except Exception as e:
            print("Exa search error:", e)

    # 2. Reddit search via Keenable
    if reddit_requested:
        reddit_hits = search_reddit_keenable(req.query, limit=min(req.num_results, 10))
        results.extend(reddit_hits)

    return {"success": True, "count": len(results), "query_used": final_query, "results": results}

def resolve_video_thumbnail(u: str) -> Optional[str]:
    if "youtube.com" in u or "youtu.be" in u:
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
        if m:
            return f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
    elif "tiktok.com" in u:
        try:
            cmd = [CFG.ytdlp_cmd(), "--print", "thumbnail", "--no-warnings", u]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
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
        return {
            "success": False,
            "error": "Thumbnail video tidak dapat diambil otomatis untuk URL ini."
        }

    is_short_url = "shorts/" in u or "tiktok.com" in u or "reels/" in u
    scenario = req.scenario.strip() or "video klip hewan menarik"

    prompt = f"""Kamu adalah asisten kurasi footage video YouTube Shorts. Evaluasi gambar thumbnail/frame ini berdasarkan skenario: "{scenario}".

Periksa 3 aspek:
1. Bebas Overlay: Apakah frame ini bersih dari burned-in subtitle besar, banner teks menutupi tengah, teks meme, atau stiker? (Watermark teks kecil di pojok/bawah masih tergolong OVERLAY_RINGAN skor 70-90. Subtitle tebal/meme skor <50).
2. Format 9:16: Apakah komposisi visual atau framing utamanya adalah vertikal 9:16 (cocok untuk Shorts/TikTok)? (Catatan format URL: {'Shorts/TikTok/Reels' if is_short_url else 'Video biasa'}).
3. Kesesuaian Skenario: Seberapa cocok adegan/hewan/gerakan dalam gambar dengan skenario: "{scenario}"? (Skor 0-100).

Jawab HANYA dalam format JSON valid persis:
{{
  "overlay_status": "BERSIH" | "OVERLAY_RINGAN" | "KOTOR",
  "overlay_score": <int 0-100>,
  "aspect_ratio": "9:16" | "16:9",
  "is_vertical": true | false,
  "match_score": <int 0-100>,
  "details": "<penjelasan 1 kalimat alasan evaluasi>"
}}"""

    try:
        client = get_local_vision_client()
        resp = client.chat.completions.create(
            model=CFG.ai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": thumb_url}}
                    ]
                }
            ],
            max_tokens=250,
            temperature=0.1
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

        passed = len(reasons_failed) == 0
        total_score = round(
            (overlay_score * 0.35) + 
            (match_score * 0.45) + 
            ((100 if is_vert else 20) * 0.20)
        )

        return {
            "success": True,
            "thumbnail": thumb_url,
            "evaluation": {
                "passed": passed,
                "total_score": total_score,
                "overlay_status": parsed.get("overlay_status"),
                "overlay_score": overlay_score,
                "aspect_ratio": parsed.get("aspect_ratio"),
                "is_vertical": is_vert,
                "match_score": match_score,
                "reasons_failed": reasons_failed,
                "details": parsed.get("details", "")
            }
        }
    except Exception as e:
        return {"success": False, "error": f"Vision error: {str(e)}"}

@app.post("/api/scan-cleanliness")
async def scan_cleanliness(req: CleanlinessRequest):
    u = req.url
    thumb_url = None

    if "youtube.com" in u or "youtu.be" in u:
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
        if m:
            thumb_url = f"https://i.ytimg.com/vi/{m.group(1)}/hqdefault.jpg"
    elif "tiktok.com" in u:
        try:
            cmd = ["yt-dlp", "--print", "thumbnail", "--no-warnings", u]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            t = res.stdout.strip()
            if t.startswith("http"):
                thumb_url = t
        except Exception:
            pass

    if not thumb_url and any(ext in u.lower() for ext in (".jpg", ".jpeg", ".png", ".webp")):
        thumb_url = u

    if not thumb_url:
        return {
            "success": False,
            "error": "Thumbnail tidak dapat diambil otomatis. Gunakan tombol Tonton untuk cek manual."
        }

    prompt = (
        "Analisis apakah gambar thumbnail/frame video ini memiliki teks, tulisan subtitle, caption, watermark, atau stiker. "
        "Jawab HANYA dalam JSON format persis: "
        '{"status": "BERSIH" | "OVERLAY_PINGGIR" | "KOTOR", "score": <0-100>, "details": "<penjelasan 1 kalimat>"}'
    )

    try:
        client = get_local_vision_client()
        resp = client.chat.completions.create(
            model=CFG.ai_vision_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": thumb_url}}
                    ]
                }
            ],
            max_tokens=200
        )
        raw_ans = resp.choices[0].message.content or ""
        m = re.search(r"\{.*\}", raw_ans, re.DOTALL)
        if m:
            parsed = json.loads(m.group(0))
            return {
                "success": True,
                "thumbnail": thumb_url,
                "cleanliness": parsed
            }
        else:
            return {
                "success": True,
                "thumbnail": thumb_url,
                "cleanliness": {
                    "status": "BERSIH" if "bersih" in raw_ans.lower() else "OVERLAY_PINGGIR",
                    "score": 80,
                    "details": raw_ans[:120]
                }
            }
    except Exception as e:
        return {"success": False, "error": f"Vision error: {str(e)}", "trace": traceback.format_exc()}

# --- AUTONOMOUS VIDEO EDITOR INTEGRATION ---
EDITOR_ENGINE_DIR = SISTEM_DIR / "editor-engine"
if str(EDITOR_ENGINE_DIR) not in sys.path:
    sys.path.append(str(EDITOR_ENGINE_DIR))
try:
    import builder
except Exception as e:
    print(f"[WARN] Failed to import builder: {e}")
    builder = None


@app.get("/api/channels")
def list_channels():
    return {
        "default_channel": CFG.default_channel_id,
        "channels": [p.model_dump() for p in CFG.channels.values()],
    }


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

class EditorActionRequest(BaseModel):
    package_name: str

@app.get("/api/editor/status/{package_name}")
def get_editor_status(package_name: str):
    if not builder:
        return {"success": False, "error": "Editor engine not initialized"}
    pkg_dir = BASE_DIR / package_name
    if not pkg_dir.exists():
        return {"success": False, "error": "Paket tidak ditemukan"}
    
    brief_file = pkg_dir / "brief-edit.md"
    has_brief = brief_file.exists()
    scenes_count = 0
    if has_brief:
        try:
            scenes_count = len(builder.parse_brief(brief_file))
        except Exception:
            pass
            
    assets_dir = pkg_dir / "assets"
    vo_files = list(assets_dir.glob("*.mp3")) if assets_dir.exists() else []
    has_vo = len(vo_files) > 0
    vo_name = vo_files[0].name if vo_files else ""
    vo_duration = builder.get_media_duration(vo_files[0]) if has_vo else 0.0
    
    clips = list(assets_dir.glob("*.mp4")) + list(assets_dir.glob("*.webm")) if assets_dir.exists() else []
    clips_count = len(clips)
    
    output_dir = pkg_dir / "output"
    output_file = output_dir / f"final_{package_name}.mp4"
    has_output = output_file.exists()
    
    return {
        "success": True,
        "package_name": package_name,
        "has_brief": has_brief,
        "scenes_count": scenes_count,
        "has_vo": has_vo,
        "vo_name": vo_name,
        "vo_duration": round(vo_duration, 2),
        "clips_count": clips_count,
        "has_output": has_output,
        "output_file": str(output_file) if has_output else ""
    }

@app.post("/api/editor/build-capcut")
def api_build_capcut(req: EditorActionRequest):
    if not builder:
        raise HTTPException(status_code=500, detail="Editor engine not loaded")
    try:
        draft_path = builder.build_capcut_project(req.package_name)
        return {
            "success": True,
            "draft_path": draft_path,
            "message": f"Draft CapCut berhasil dibuat: {Path(draft_path).name}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.post("/api/editor/render-direct")
def api_render_direct(req: EditorActionRequest):
    if not builder:
        raise HTTPException(status_code=500, detail="Editor engine not loaded")
    try:
        output_mp4 = builder.render_ffmpeg_direct(req.package_name)
        return {
            "success": True,
            "output_path": output_mp4,
            "message": f"Render MP4 selesai: {Path(output_mp4).name}"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/api/editor/video/{package_name}")
def api_get_rendered_video(package_name: str):
    pkg_dir = BASE_DIR / package_name
    output_file = pkg_dir / "output" / f"final_{package_name}.mp4"
    if not output_file.exists():
        raise HTTPException(status_code=404, detail="File video belum di-render.")
    return FileResponse(str(output_file), media_type="video/mp4")

@app.get("/", response_class=HTMLResponse)
def serve_index():
    index_file = APP_DIR / "index.html"
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Doksli Finder: index.html not found</h1>")

if __name__ == "__main__":
    print(f"Starting Doksli Finder v2 at http://localhost:8000")
    uvicorn.run("server:app", host="127.0.0.1", port=8000, reload=True)
