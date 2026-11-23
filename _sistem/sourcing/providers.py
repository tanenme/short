"""Unified multi-provider clip search and direct provenance-tracked downloader."""

from __future__ import annotations

import json
import re
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config
from .licensing import classify_license, generate_credits_markdown, sha256_file
from .qc_video import _ffprobe_video

UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"


class SourcedCandidate(BaseModel):
    title: str
    url: str
    page_url: str = ""
    platform: str
    media_type: Literal["video", "photo"] = "video"
    creator: str = ""
    license: str = "UNKNOWN"
    license_url: str = ""
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"] = "HIGH"
    monetization_safe: bool = False
    thumbnail: Optional[str] = None
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    snippet: str = ""
    score: float = 0.0


def _http_get_json(url: str, headers: Optional[Dict[str, str]] = None, timeout: int = 18) -> dict:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": UA_WEB})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def _http_post_json(url: str, payload: dict, headers: Dict[str, str], timeout: int = 20) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", errors="ignore"))


def search_wikimedia(query: str, limit: int = 8, include_photos: bool = True) -> List[SourcedCandidate]:
    """Search Wikimedia Commons for CC/Public-Domain videos and optional photos."""
    titles: List[str] = []
    filters = ["filetype:video"] + (["filetype:bitmap"] if include_photos else [])
    for filt in filters:
        q = f'"{query}" {filt}'
        u = (
            "https://commons.wikimedia.org/w/api.php?action=query&format=json"
            f"&list=search&srsearch={urllib.parse.quote(q)}&srnamespace=6&srlimit={limit}"
        )
        try:
            d = _http_get_json(u)
            for s in d.get("query", {}).get("search", []):
                ttl = s.get("title", "")
                if ttl and ttl not in titles:
                    titles.append(ttl)
        except Exception:
            continue

    if not titles:
        return []

    out: List[SourcedCandidate] = []
    for i in range(0, min(len(titles), limit * 2), 20):
        chunk = titles[i : i + 20]
        u = (
            "https://commons.wikimedia.org/w/api.php?action=query&format=json"
            "&prop=imageinfo&iiprop=url|size|mime|extmetadata"
            f"&titles={urllib.parse.quote('|'.join(chunk))}"
        )
        try:
            d = _http_get_json(u)
        except Exception:
            continue

        for pg in (d.get("query", {}).get("pages", {}) or {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            file_url = (ii.get("url") or "").split("?")[0]
            if not file_url:
                continue
            mime = (ii.get("mime") or "").lower()
            is_vid = mime.startswith("video/") or file_url.lower().endswith((".webm", ".ogv", ".mp4", ".mov"))
            is_img = mime.startswith("image/") or file_url.lower().endswith((".jpg", ".jpeg", ".png"))
            if not (is_vid or is_img):
                continue

            em = ii.get("extmetadata", {}) or {}
            clean_html = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
            lic_short = clean_html((em.get("LicenseShortName") or {}).get("value", "")) or "NA"
            lic_url = clean_html((em.get("LicenseUrl") or {}).get("value", ""))
            artist = clean_html((em.get("Artist") or {}).get("value", "")) or "Wikimedia Contributor"
            desc = clean_html((em.get("ImageDescription") or {}).get("value", ""))
            verdict = classify_license(lic_short, "wikimedia")
            if verdict.risk_level == "BLOCKED":
                continue

            w = int(ii.get("width") or 0)
            h = int(ii.get("height") or 0)
            if is_img and (w < 700 or h < 700):
                continue

            title = pg.get("title", "").replace("File:", "")
            page = f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(pg.get('title', ''))}"
            base_score = 78.0 if is_vid else 64.0
            if verdict.monetization_safe:
                base_score += 15.0
            if h > w and w > 0:
                base_score += 5.0

            out.append(
                SourcedCandidate(
                    title=title,
                    url=file_url,
                    page_url=page,
                    platform="wikimedia",
                    media_type="video" if is_vid else "photo",
                    creator=artist[:80],
                    license=lic_short,
                    license_url=lic_url,
                    risk_level=verdict.risk_level,
                    monetization_safe=verdict.monetization_safe,
                    thumbnail=file_url if is_img else None,
                    width=w,
                    height=h,
                    duration_s=float(ii.get("duration") or 0.0),
                    snippet=desc[:220],
                    score=min(100.0, base_score),
                )
            )
    return out[:limit]


def search_inaturalist(query: str, limit: int = 6) -> List[SourcedCandidate]:
    """Search research-grade CC-licensed wildlife observations on iNaturalist."""
    u = "https://api.inaturalist.org/v1/observations?" + urllib.parse.urlencode(
        {"q": query, "photos": "true", "quality_grade": "research", "per_page": min(limit * 2, 20), "order_by": "votes", "locale": "en"}
    )
    try:
        d = _http_get_json(u)
    except Exception:
        return []

    out: List[SourcedCandidate] = []
    for obs in d.get("results", []):
        photos = obs.get("photos") or []
        if not photos:
            continue
        ph = photos[0]
        lic = (ph.get("license_code") or "").upper()
        verdict = classify_license(lic, "inaturalist")
        if verdict.risk_level == "BLOCKED" or not verdict.monetization_safe:
            continue
        iurl = (ph.get("url") or "").replace("/square.", "/original.")
        if not iurl:
            continue
        tax = obs.get("taxon") or {}
        title = tax.get("preferred_common_name") or tax.get("name") or query
        creator = ph.get("attribution") or (obs.get("user") or {}).get("login") or "iNaturalist"
        out.append(
            SourcedCandidate(
                title=f"{title} ({tax.get('name', '')})".strip(" ()"),
                url=iurl,
                page_url=f"https://www.inaturalist.org/observations/{obs.get('id')}",
                platform="inaturalist",
                media_type="photo",
                creator=str(creator)[:80],
                license=f"CC {lic}" if not lic.startswith("CC") else lic,
                risk_level=verdict.risk_level,
                monetization_safe=verdict.monetization_safe,
                thumbnail=(ph.get("url") or "").replace("/square.", "/medium."),
                width=int((ph.get("original_dimensions") or {}).get("width") or 0),
                height=int((ph.get("original_dimensions") or {}).get("height") or 0),
                snippet=(obs.get("place_guess") or "")[:160],
                score=75.0,
            )
        )
        if len(out) >= limit:
            break
    return out


def search_reddit_keenable(query: str, limit: int = 8) -> List[SourcedCandidate]:
    """Search raw Reddit video posts via Keenable public endpoint."""
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query).strip()
    payload = {"query": f"site:reddit.com/r/ {clean_q} video", "max_results": min(max(1, limit), 20)}
    try:
        data = _http_post_json(
            "https://api.keenable.ai/v1/search/public",
            payload,
            {"Content-Type": "application/json", "X-Keenable-Title": "shorts-studio"},
            timeout=14,
        )
    except Exception:
        return []

    out: List[SourcedCandidate] = []
    for r in data.get("results") or []:
        u = r.get("url") or ""
        if "reddit.com" not in u:
            continue
        m = re.search(r"reddit\.com/r/([^/]+)", u)
        sub = f"r/{m.group(1)}" if m else "r/Reddit"
        verdict = classify_license("", "reddit")
        out.append(
            SourcedCandidate(
                title=r.get("title") or "Reddit Video",
                url=u,
                page_url=u,
                platform="reddit",
                media_type="video",
                creator=sub,
                license=verdict.raw_license,
                risk_level=verdict.risk_level,
                monetization_safe=verdict.monetization_safe,
                snippet=(r.get("snippet") or r.get("description") or "")[:200],
                score=68.0,
            )
        )
    return out


def search_exa(
    query: str,
    platforms: List[str],
    limit: int = 10,
    raw_mode: bool = True,
    preset: Optional[str] = None,
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Search video clips via Exa neural search."""
    cfg = config or get_config()
    key = (api_key or cfg.exa_api_key or "").strip()
    if not key:
        return []

    domains = []
    for p in platforms:
        p_clean = p.lower().strip()
        if "reddit" in p_clean:
            continue
        if p_clean in ("youtube", "youtube.com"):
            domains.append("youtube.com")
        elif p_clean in ("tiktok", "tiktok.com"):
            domains.append("tiktok.com")
        elif p_clean in ("instagram", "instagram.com"):
            domains.append("instagram.com")
        elif p_clean in ("pexels", "pexels.com"):
            domains.append("pexels.com")
        elif p_clean:
            domains.append(p_clean)

    final_q = query.strip()
    if preset == "stock_broll":
        domains = ["pexels.com", "pixabay.com"]
    elif preset == "cctv_petcam":
        final_q = f"{final_q} pet camera OR cctv OR security cam"
    elif preset == "youtube_raw":
        domains = ["youtube.com"]
        final_q = f"{final_q} raw footage OR uncut"

    if raw_mode and preset != "stock_broll":
        final_q = f'{final_q} (raw footage OR uncut OR "no text" OR original clip)'

    payload: dict = {"query": final_q, "type": "auto", "numResults": min(limit, 25), "contents": {"highlights": True, "text": True}}
    if domains:
        payload["includeDomains"] = domains

    try:
        data = _http_post_json(
            "https://api.exa.ai/search",
            payload,
            {"Content-Type": "application/json", "x-api-key": key},
            timeout=25,
        )
    except Exception:
        return []

    out: List[SourcedCandidate] = []
    for item in data.get("results", []):
        u = item.get("url") or ""
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

        verdict = classify_license("", plat)
        score = 82.0 if plat == "pexels" else (74.0 if ("shorts/" in u or plat == "tiktok") else 65.0)
        hl = item.get("highlights") or []
        out.append(
            SourcedCandidate(
                title=item.get("title") or "Video Clip",
                url=u,
                page_url=u,
                platform=plat,
                media_type="video",
                creator=item.get("author") or "",
                license=verdict.raw_license,
                risk_level=verdict.risk_level,
                monetization_safe=verdict.monetization_safe,
                thumbnail=thumb,
                snippet=(hl[0] if hl else (item.get("text") or ""))[:220],
                score=score,
            )
        )
    return out


def search_unified(
    query: str,
    providers: Optional[List[str]] = None,
    limit: int = 12,
    cc_only: bool = False,
    raw_mode: bool = True,
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Run unified search across CC repositories and social/stock engines, deduplicated and ranked."""
    cfg = config or get_config()
    selected = [p.lower() for p in (providers or ["wikimedia", "inaturalist", "pexels", "youtube", "tiktok", "reddit"])]

    results: List[SourcedCandidate] = []
    if "wikimedia" in selected:
        results.extend(search_wikimedia(query, limit=max(4, limit // 2)))
    if "inaturalist" in selected:
        results.extend(search_inaturalist(query, limit=max(3, limit // 3)))
    if not cc_only:
        if "reddit" in selected:
            results.extend(search_reddit_keenable(query, limit=max(4, limit // 2)))
        exa_plats = [p for p in selected if p in ("youtube", "tiktok", "instagram", "pexels")]
        if exa_plats:
            results.extend(search_exa(query, exa_plats, limit=limit, raw_mode=raw_mode, api_key=api_key, config=cfg))

    # Deduplicate by URL and optionally enforce CC/monetization-safe only
    seen = set()
    uniq: List[SourcedCandidate] = []
    for r in results:
        if r.url in seen:
            continue
        if cc_only and not r.monetization_safe:
            continue
        seen.add(r.url)
        uniq.append(r)

    uniq.sort(key=lambda c: (c.monetization_safe, c.score), reverse=True)
    return uniq[:limit]


def _slugify(text: str, max_len: int = 38) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "clip").lower())[:max_len].strip("-") or "clip"


def download_to_episode(
    url: str,
    episode_id: str,
    channel_id: Optional[str] = None,
    title: str = "",
    platform: str = "",
    creator: str = "",
    license_str: str = "",
    license_url: str = "",
    page_url: str = "",
    config: Optional[StudioConfig] = None,
) -> dict:
    """Download a clip into <channel>/<episode>/assets/ with full provenance in _metadata.json + CREDITS.md."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)
    assets_dir.mkdir(parents=True, exist_ok=True)

    verdict = classify_license(license_str, platform)
    if prof.ypp.forbid_nc_nd_licenses and verdict.risk_level == "BLOCKED":
        raise ValueError(f"Unduhan ditolak oleh YPP Gate: lisensi {verdict.normalized} dilarang untuk monetisasi.")

    low_u = url.lower()
    is_direct_media = any(
        ext in urllib.parse.urlparse(low_u).path for ext in (".mp4", ".webm", ".ogv", ".mov", ".jpg", ".jpeg", ".png", ".webp")
    ) or "upload.wikimedia.org" in low_u or "inaturalist" in low_u

    ts_tag = int(time.time()) % 100000
    base_name = f"{_slugify(creator or platform, 18)}_{_slugify(title or 'clip', 28)}_{ts_tag}"

    downloaded_path: Optional[Path] = None
    if is_direct_media:
        ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".mp4"
        dest = assets_dir / f"{base_name}{ext}"
        req = urllib.request.Request(url, headers={"User-Agent": UA_WEB})
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = resp.read()
        if data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
            raise RuntimeError("Server mengembalikan halaman HTML, bukan file media.")
        dest.write_bytes(data)
        downloaded_path = dest
    else:
        out_tpl = str(assets_dir / f"{base_name}.%(ext)s")
        cmd = [
            cfg.ytdlp_cmd(),
            "--no-warnings",
            "--no-playlist",
            "-f",
            "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
            "--merge-output-format",
            "mp4",
            "-o",
            out_tpl,
            url,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        produced = [
            p
            for p in assets_dir.glob(f"{base_name}.*")
            if p.suffix.lower() in {".mp4", ".webm", ".mkv", ".mov"}
        ]
        if not produced:
            raise RuntimeError(f"yt-dlp gagal mengunduh URL ini: {(res.stderr or res.stdout)[-300:]}")
        downloaded_path = max(produced, key=lambda p: p.stat().st_size)

    probe = _ffprobe_video(downloaded_path, cfg)
    file_hash = sha256_file(downloaded_path)

    # Update assets/_metadata.json provenance sidecar
    meta_file = assets_dir / "_metadata.json"
    meta: Dict[str, dict] = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    entry = {
        "source": platform or "web",
        "url": url,
        "page": page_url or url,
        "title": title or downloaded_path.stem,
        "creator": creator or "Tidak diketahui",
        "license": verdict.raw_license,
        "license_normalized": verdict.normalized,
        "license_risk": verdict.risk_level,
        "license_url": license_url,
        "attribution_required": verdict.attribution_required,
        "publikasi_aman": verdict.monetization_safe,
        "duration": probe["dur"],
        "width": probe["w"],
        "height": probe["h"],
        "is_vertical": probe["vertical"],
        "sha256": file_hash,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
    }
    meta[downloaded_path.name] = entry
    meta_file.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    _, credits_path = generate_credits_markdown(episode_id, prof.channel_id, cfg, write_file=True)

    return {
        "success": True,
        "filename": downloaded_path.name,
        "path": str(downloaded_path),
        "probe": probe,
        "license_verdict": verdict.model_dump(),
        "credits_file": str(credits_path) if credits_path else None,
    }
