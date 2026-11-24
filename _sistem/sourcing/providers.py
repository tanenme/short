"""Unified multi-provider clip search, Native Stock APIs, Local B-Roll Vault, ReadyScore (0-100), and Auto-Trim downloader."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Dict, List, Literal, Optional, Sequence, Set, Tuple

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config
from .licensing import check_creator_rotation, classify_license, generate_credits_markdown, sha256_file
from .qc_video import _ffprobe_video

UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# Bilingual concept bridge so Indonesian/English Vault & API metadata score accurately
CONCEPT_SYNONYMS: List[Set[str]] = [
    {"dog", "dogs", "puppy", "canine", "anjing"},
    {"cat", "cats", "kitten", "feline", "kucing"},
    {"duck", "ducks", "duckling", "mallard", "bebek"},
    {"chicken", "rooster", "hen", "ayam"},
    {"cow", "cows", "cattle", "calf", "sapi"},
    {"gecko", "lizard", "cicak"},
    {"kick", "kicking", "scratch", "scratching", "dig", "digging", "nendang", "tendang", "garuk"},
    {"dirt", "soil", "ground", "grass", "turf", "earth", "tanah", "rumput"},
    {"paw", "paws", "hind", "legs", "foot", "kaki", "telapak", "cakar"},
    {"chatter", "chattering", "chirping", "ekekek", "ek", "mulut"},
    {"push", "pushing", "knock", "knocking", "batting", "dorong", "jatuh"},
    {"glass", "cup", "mug", "table", "gelas", "meja"},
    {"sleep", "sleeping", "eye", "blink", "tidur", "melek", "mata"},
    {"head", "steady", "stabilization", "gimbal", "kepala", "stabil"},
]


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
    preview_video_url: Optional[str] = None
    preview_frames: List[str] = Field(default_factory=list)
    width: int = 0
    height: int = 0
    duration_s: float = 0.0
    snippet: str = ""
    score: float = 0.0
    ready_status: Literal["READY", "REVIEW", "REJECTED"] = "REVIEW"
    score_breakdown: Dict[str, int] = Field(default_factory=dict)
    query_tier: str = ""
    negative_hit: str = ""
    vision_evaluated: bool = False
    vision_summary: str = ""
    crop_x_norm: float = 0.50
    suggested_trim: Optional[List[float]] = None
    local_path: str = ""


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


def _expand_tokens(text: str) -> Set[str]:
    raw_toks = {t for t in re.findall(r"[a-z0-9]{3,}", (text or "").lower()) if t not in {"the", "and", "with", "for", "raw", "footage", "video", "clip"}}
    expanded = set(raw_toks)
    for grp in CONCEPT_SYNONYMS:
        if raw_toks & grp:
            expanded |= grp
    return expanded


def detect_negative_hit(text_blob: str, negative_terms: Optional[Sequence[str]]) -> str:
    """Return the first negative action/concept token matched in candidate metadata, or ''."""
    if not negative_terms or not text_blob:
        return ""
    low = text_blob.lower()
    for neg in negative_terms:
        n_clean = (neg or "").strip().lower()
        if len(n_clean) < 3:
            continue
        if re.search(rf"\b{re.escape(n_clean)}(?:ing|ed|s)?\b", low):
            return n_clean
    return ""


def compute_ready_score(
    cand: SourcedCandidate,
    query: str,
    negative_terms: Optional[Sequence[str]] = None,
    recent_creators_7d: Optional[Set[str]] = None,
    semantic_override: Optional[int] = None,
    clean_override: Optional[int] = None,
    motion_override: Optional[int] = None,
) -> SourcedCandidate:
    """Compute deterministic 0-100 ReadyScore across 5 pillars + Negative Action Hard Gate.

    ReadyScore = 0.35*Semantic + 0.25*CleanHeadroom + 0.20*FramingRes + 0.10*Motion + 0.10*YPPSafety
    """
    blob = f"{cand.title} {cand.snippet}".strip()
    neg_hit = cand.negative_hit or detect_negative_hit(blob, negative_terms)

    # 1. Semantic Action Relevance (35%)
    if semantic_override is not None:
        s_sem = max(0, min(100, int(semantic_override)))
    else:
        q_raw = {t for t in re.findall(r"[a-z0-9]{3,}", (query or "").lower()) if t not in {"the", "and", "with", "for", "raw", "footage", "video", "clip"}}
        b_expanded = _expand_tokens(blob)
        if not q_raw:
            s_sem = 72
        else:
            hits = sum(1 for qt in q_raw if qt in b_expanded or any(qt in bt or bt in qt for bt in b_expanded))
            ratio = hits / max(1, len(q_raw))
            s_sem = min(98, int(round(38 + ratio * 60)))
    if neg_hit:
        s_sem = min(s_sem, 14)

    # 2. Cleanliness & Caption Safe-Zone Headroom (25%)
    if clean_override is not None:
        s_clean = max(0, min(100, int(clean_override)))
    else:
        if cand.platform in ("pexels", "pixabay"):
            s_clean = 95
        elif cand.platform in ("wikimedia", "inaturalist"):
            s_clean = 90
        elif cand.platform == "vault":
            s_clean = 92
        elif any(bad in blob.lower() for bad in ("meme", "compilation", "top 10", "reaction", "lyrics")):
            s_clean = 35
        else:
            s_clean = 74

    # 3. Framing & Resolution Readiness (20%)
    w, h = int(cand.width or 0), int(cand.height or 0)
    if w > 0 and h > 0:
        is_vert = h > w
        if is_vert and w >= 1080 and h >= 1920:
            s_frame = 100
        elif is_vert and w >= 720 and h >= 1280:
            s_frame = 90
        elif (not is_vert) and w >= 1920 and h >= 1080:
            s_frame = 84  # Full-HD 16:9 crop-safe via crop_x_norm
        elif is_vert:
            s_frame = 72
        elif w >= 1280 and h >= 720:
            s_frame = 64
        else:
            s_frame = 38
    else:
        if "shorts/" in cand.url or cand.platform in ("tiktok", "instagram"):
            s_frame = 84
        else:
            s_frame = 66

    # 4. Motion & Shot Continuity (10%)
    if motion_override is not None:
        s_motion = max(0, min(100, int(motion_override)))
    else:
        dur = float(cand.duration_s or 0.0)
        if cand.media_type == "photo":
            s_motion = 52
        elif 2.5 <= dur <= 22.0:
            s_motion = 95
        elif 22.0 < dur <= 60.0:
            s_motion = 84
        elif dur > 60.0:
            s_motion = 58
        else:
            s_motion = 78

    # 5. YPP License & 7-Day Creator Rotation Safety (10%)
    creator_norm = (cand.creator or "").strip().lower()
    creator_repeated = bool(creator_norm and recent_creators_7d and creator_norm in recent_creators_7d)
    if cand.risk_level == "BLOCKED":
        s_ypp = 0
    elif creator_repeated:
        s_ypp = 20
    elif cand.monetization_safe and cand.risk_level == "LOW":
        s_ypp = 100
    elif cand.monetization_safe:
        s_ypp = 88
    else:
        s_ypp = 62

    total = round(
        0.35 * s_sem
        + 0.25 * s_clean
        + 0.20 * s_frame
        + 0.10 * s_motion
        + 0.10 * s_ypp,
        1,
    )
    if neg_hit or cand.risk_level == "BLOCKED":
        total = min(total, 39.0)
        status: Literal["READY", "REVIEW", "REJECTED"] = "REJECTED"
    elif total >= 78.0:
        status = "READY"
    elif total >= 58.0:
        status = "REVIEW"
    else:
        status = "REJECTED"

    cand.negative_hit = neg_hit
    cand.score = total
    cand.ready_status = status
    cand.score_breakdown = {
        "semantic": s_sem,
        "clean_headroom": s_clean,
        "framing_res": s_frame,
        "motion_continuity": s_motion,
        "ypp_safety": s_ypp,
    }
    return cand


def _pick_three_even(urls: List[str]) -> List[str]:
    clean = [u for u in urls if u and u.startswith("http")]
    if len(clean) <= 3:
        return clean
    n = len(clean)
    idxs = [max(0, int(n * 0.18)), int(n * 0.50), min(n - 1, int(n * 0.82))]
    return [clean[i] for i in idxs]


def search_pexels_videos(
    query: str,
    limit: int = 8,
    orientation: str = "",
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Search Pexels Native Videos REST API (returns direct MP4 links, dimensions, duration & multi-frame strip).

    Falls back cleanly to Exa on `pexels.com` WITHOUT `raw_mode` pollution if `PEXELS_API_KEY` is unset.
    """
    cfg = config or get_config()
    key = (api_key or cfg.pexels_api_key or "").strip()
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query or "").strip()
    if not clean_q:
        return []

    if not key:
        # Clean stock fallback (strictly raw_mode=False so "(raw footage OR uncut)" NEVER pollutes Pexels!)
        return search_exa(clean_q, ["pexels"], limit=limit, raw_mode=False, preset="stock_broll", config=cfg)

    params: Dict[str, str] = {"query": clean_q, "per_page": str(min(max(limit * 2, 8), 30))}
    if orientation in ("portrait", "landscape", "square"):
        params["orientation"] = orientation
    u = "https://api.pexels.com/videos/search?" + urllib.parse.urlencode(params)
    try:
        data = _http_get_json(u, headers={"Authorization": key, "User-Agent": UA_WEB}, timeout=16)
    except Exception:
        return []

    out: List[SourcedCandidate] = []
    verdict = classify_license("Pexels Free", "pexels")
    for v in data.get("videos") or []:
        files = [f for f in (v.get("video_files") or []) if (f.get("file_type") or "").endswith("mp4") and f.get("link")]
        if not files:
            continue
        # Pick best HD download file (<= 1920px max edge) & lightweight SD preview file for hover-scrubbing
        hd_files = [f for f in files if max(int(f.get("width") or 0), int(f.get("height") or 0)) <= 1920] or files
        best_dl = max(hd_files, key=lambda f: int(f.get("height") or 0) * int(f.get("width") or 0))
        sd_files = [f for f in files if 320 <= max(int(f.get("width") or 0), int(f.get("height") or 0)) <= 960] or [best_dl]
        preview_vid = min(sd_files, key=lambda f: int(f.get("width") or 9999))["link"]

        pics = [p.get("picture", "") for p in (v.get("video_pictures") or []) if p.get("picture")]
        frames_3 = _pick_three_even(pics)
        page_url = v.get("url") or ""
        slug_title = page_url.rstrip("/").split("/")[-1].replace("-", " ") if page_url else ""
        slug_title = re.sub(r"\s+\d+$", "", slug_title).strip().title() or f"Pexels {clean_q.title()}"
        user_obj = v.get("user") or {}
        creator = str(user_obj.get("name") or "Pexels Creator")[:80]
        dur = float(v.get("duration") or 0.0)
        sug_trim = [round(max(0.0, dur * 0.25), 2), round(min(dur, max(0.0, dur * 0.25) + 3.8), 2)] if dur > 5.0 else [0.0, round(dur, 2)]

        cand = SourcedCandidate(
            title=slug_title,
            url=best_dl["link"],
            page_url=page_url or best_dl["link"],
            platform="pexels",
            media_type="video",
            creator=creator,
            license=verdict.raw_license,
            license_url="https://www.pexels.com/license/",
            risk_level=verdict.risk_level,
            monetization_safe=verdict.monetization_safe,
            thumbnail=v.get("image") or (frames_3[1] if len(frames_3) > 1 else (frames_3[0] if frames_3 else None)),
            preview_video_url=preview_vid,
            preview_frames=frames_3,
            width=int(best_dl.get("width") or v.get("width") or 0),
            height=int(best_dl.get("height") or v.get("height") or 0),
            duration_s=dur,
            snippet=f"{slug_title} • {int(best_dl.get('width') or 0)}x{int(best_dl.get('height') or 0)} • {dur:.1f}s",
            suggested_trim=sug_trim if dur > 0 else None,
        )
        compute_ready_score(cand, clean_q)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def search_pixabay_videos(
    query: str,
    limit: int = 6,
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Search Pixabay Native Videos REST API when PIXABAY_API_KEY is configured."""
    cfg = config or get_config()
    key = (api_key or cfg.pixabay_api_key or "").strip()
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query or "").strip()
    if not key or not clean_q:
        return []

    u = "https://pixabay.com/api/videos/?" + urllib.parse.urlencode(
        {"key": key, "q": clean_q, "per_page": min(max(limit * 2, 6), 20), "safesearch": "true"}
    )
    try:
        data = _http_get_json(u, timeout=15)
    except Exception:
        return []

    out: List[SourcedCandidate] = []
    verdict = classify_license("Pixabay Content License", "pixabay")
    for h in data.get("hits") or []:
        vids = h.get("videos") or {}
        best = vids.get("medium") or vids.get("large") or vids.get("small") or {}
        tiny = vids.get("tiny") or vids.get("small") or best
        if not best.get("url"):
            continue
        pic_id = h.get("picture_id") or ""
        thumb = f"https://i.vimeocdn.com/video/{pic_id}_640x360.jpg" if pic_id else (best.get("thumbnail") or None)
        tags_str = str(h.get("tags") or clean_q)
        dur = float(h.get("duration") or 0.0)
        cand = SourcedCandidate(
            title=f"{tags_str.title()} ({h.get('user', 'Pixabay')})",
            url=best["url"],
            page_url=h.get("pageURL") or best["url"],
            platform="pixabay",
            media_type="video",
            creator=str(h.get("user") or "Pixabay")[:80],
            license=verdict.raw_license,
            license_url="https://pixabay.com/service/license-summary/",
            risk_level=verdict.risk_level,
            monetization_safe=verdict.monetization_safe,
            thumbnail=thumb,
            preview_video_url=tiny.get("url") or best["url"],
            preview_frames=[thumb] if thumb else [],
            width=int(best.get("width") or 0),
            height=int(best.get("height") or 0),
            duration_s=dur,
            snippet=f"Tags: {tags_str} • {dur:.1f}s",
            suggested_trim=[round(dur * 0.2, 2), round(min(dur, dur * 0.2 + 3.8), 2)] if dur > 5.0 else [0.0, round(dur, 2)],
        )
        compute_ready_score(cand, clean_q)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def search_local_vault(
    query: str,
    limit: int = 6,
    episode_id: str = "",
    channel_id: Optional[str] = None,
    negative_terms: Optional[Sequence[str]] = None,
    recent_creators_7d: Optional[Set[str]] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Search already-vetted local clean clips across studio episodes via SQLite B-Roll Vault."""
    from core.db import StudioDB

    cfg = config or get_config()
    db = StudioDB(config=cfg)
    rows = db.list_broll_vault(exclude_episode_id=episode_id)
    if not rows:
        db.sync_broll_vault()
        rows = db.list_broll_vault(exclude_episode_id=episode_id)

    q_toks = _expand_tokens(query)
    if not q_toks:
        return []

    scored: List[SourcedCandidate] = []
    for r in rows:
        fp = Path(r["filepath"])
        if not fp.exists():
            continue
        blob = f"{r['tags_text']} {r['summary_text']} {r['filename']}"
        row_toks = _expand_tokens(blob)
        overlap = len(q_toks & row_toks)
        if overlap == 0:
            continue

        cand = SourcedCandidate(
            title=f"[Vault {r['episode_id']}] {r['filename']}",
            url=f"vault://{r['channel_id']}/{r['episode_id']}/{r['filename']}",
            page_url="",
            platform="vault",
            media_type="video",
            creator=r["creator"],
            license=r["license"],
            risk_level=r["risk_level"],  # type: ignore[arg-type]
            monetization_safe=bool(r["monetization_safe"]),
            width=int(r["width"] or 0),
            height=int(r["height"] or 0),
            duration_s=float(r["duration_s"] or 0.0),
            snippet=(r["summary_text"] or r["tags_text"])[:220],
            query_tier="vault",
            local_path=str(fp),
        )
        compute_ready_score(
            cand,
            query,
            negative_terms=negative_terms,
            recent_creators_7d=recent_creators_7d,
            clean_override=int(r["cleanliness_score"] or 85),
        )
        if cand.ready_status != "REJECTED":
            scored.append(cand)

    scored.sort(key=lambda c: c.score, reverse=True)
    return scored[:limit]


def search_wikimedia(query: str, limit: int = 8, include_photos: bool = True) -> List[SourcedCandidate]:
    """Search Wikimedia Commons for CC/Public-Domain videos and optional photos."""
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query or "").strip()
    if not clean_q:
        return []
    titles: List[str] = []
    filters = ["filetype:video"] + (["filetype:bitmap"] if include_photos else [])
    for filt in filters:
        q = f'"{clean_q}" {filt}'
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
            "&prop=imageinfo&iiprop=url|size|mime|extmetadata&iiurlwidth=480"
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
            thumb_u = ii.get("thumburl") or (file_url if is_img else None)
            dur = float(ii.get("duration") or 0.0)
            cand = SourcedCandidate(
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
                thumbnail=thumb_u,
                preview_video_url=file_url if is_vid else None,
                preview_frames=[thumb_u] if thumb_u else [],
                width=w,
                height=h,
                duration_s=dur,
                snippet=desc[:220],
            )
            compute_ready_score(cand, clean_q)
            out.append(cand)
    return out[:limit]


def search_inaturalist(query: str, limit: int = 6) -> List[SourcedCandidate]:
    """Search research-grade CC-licensed wildlife observations on iNaturalist."""
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query or "").strip()
    if not clean_q:
        return []
    u = "https://api.inaturalist.org/v1/observations?" + urllib.parse.urlencode(
        {"q": clean_q, "photos": "true", "quality_grade": "research", "per_page": min(limit * 2, 20), "order_by": "votes", "locale": "en"}
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
        title = tax.get("preferred_common_name") or tax.get("name") or clean_q
        creator = ph.get("attribution") or (obs.get("user") or {}).get("login") or "iNaturalist"
        med_thumb = (ph.get("url") or "").replace("/square.", "/medium.")
        cand = SourcedCandidate(
            title=f"{title} ({tax.get('name', '')})".strip(" ()"),
            url=iurl,
            page_url=f"https://www.inaturalist.org/observations/{obs.get('id')}",
            platform="inaturalist",
            media_type="photo",
            creator=str(creator)[:80],
            license=f"CC {lic}" if not lic.startswith("CC") else lic,
            risk_level=verdict.risk_level,
            monetization_safe=verdict.monetization_safe,
            thumbnail=med_thumb,
            preview_frames=[med_thumb] if med_thumb else [],
            width=int((ph.get("original_dimensions") or {}).get("width") or 0),
            height=int((ph.get("original_dimensions") or {}).get("height") or 0),
            snippet=(obs.get("place_guess") or "")[:160],
        )
        compute_ready_score(cand, clean_q)
        out.append(cand)
        if len(out) >= limit:
            break
    return out


def search_reddit_keenable(query: str, limit: int = 8) -> List[SourcedCandidate]:
    """Search raw Reddit video posts via Keenable public endpoint."""
    clean_q = re.sub(r'\(.*?\)|"no text"|uncut|raw footage', "", query).strip()
    if not clean_q:
        return []
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
        cand = SourcedCandidate(
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
        )
        compute_ready_score(cand, clean_q)
        out.append(cand)
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
    """Search video clips via Exa neural search (with stock-domain hygiene & YouTube 3-frame strip URLs)."""
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
        elif p_clean in ("pixabay", "pixabay.com"):
            domains.append("pixabay.com")
        elif p_clean:
            domains.append(p_clean)

    final_q = query.strip()
    is_stock_only = bool(domains) and all(d in ("pexels.com", "pixabay.com") for d in domains)
    if preset == "stock_broll":
        domains = ["pexels.com", "pixabay.com"]
        is_stock_only = True
    elif preset == "cctv_petcam":
        final_q = f"{final_q} pet camera OR cctv OR security cam"
    elif preset == "youtube_raw":
        domains = ["youtube.com"]
        final_q = f"{final_q} raw footage OR uncut"

    # NEVER inject "(raw footage OR uncut)" into stock sites like Pexels/Pixabay!
    if raw_mode and not is_stock_only:
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
        frames_3: List[str] = []
        if "youtube.com" in u or "youtu.be" in u:
            plat = "youtube"
            m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", u)
            if m:
                vid = m.group(1)
                thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
                frames_3 = [
                    f"https://i.ytimg.com/vi/{vid}/1.jpg",
                    f"https://i.ytimg.com/vi/{vid}/2.jpg",
                    f"https://i.ytimg.com/vi/{vid}/3.jpg",
                ]
        elif "tiktok.com" in u:
            plat = "tiktok"
        elif "instagram.com" in u:
            plat = "instagram"
        elif "pexels.com" in u:
            plat = "pexels"
        elif "pixabay.com" in u:
            plat = "pixabay"

        verdict = classify_license("", plat)
        hl = item.get("highlights") or []
        cand = SourcedCandidate(
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
            preview_frames=frames_3,
            snippet=(hl[0] if hl else (item.get("text") or ""))[:220],
        )
        compute_ready_score(cand, query)
        out.append(cand)
    return out


def _collect_recent_creators_7d(episode_id: str, channel_id: Optional[str], cfg: StudioConfig) -> Set[str]:
    if not episode_id:
        return set()
    try:
        rot = check_creator_rotation(episode_id, channel_id, window_days=7, config=cfg)
        return {k.lower() for k in (rot.get("recent_creators_pool") or [])}
    except Exception:
        return set()


def search_unified(
    query: str,
    providers: Optional[List[str]] = None,
    limit: int = 12,
    cc_only: bool = False,
    raw_mode: bool = True,
    api_key: Optional[str] = None,
    negative_terms: Optional[Sequence[str]] = None,
    episode_id: str = "",
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> List[SourcedCandidate]:
    """Run unified search across Vault, Native Stock APIs, CC repos, and social engines, scored 0-100."""
    cfg = config or get_config()
    selected = [p.lower() for p in (providers or ["vault", "pexels", "pixabay", "wikimedia", "inaturalist", "youtube", "tiktok", "reddit"])]
    recent_7d = _collect_recent_creators_7d(episode_id, channel_id, cfg)

    results: List[SourcedCandidate] = []
    if "vault" in selected and not cc_only:
        results.extend(
            search_local_vault(
                query,
                limit=max(3, limit // 3),
                episode_id=episode_id,
                channel_id=channel_id,
                negative_terms=negative_terms,
                recent_creators_7d=recent_7d,
                config=cfg,
            )
        )
    if "pexels" in selected:
        results.extend(search_pexels_videos(query, limit=max(5, limit // 2), config=cfg))
    if "pixabay" in selected:
        results.extend(search_pixabay_videos(query, limit=max(4, limit // 3), config=cfg))
    if "wikimedia" in selected:
        results.extend(search_wikimedia(query, limit=max(4, limit // 2)))
    if "inaturalist" in selected:
        results.extend(search_inaturalist(query, limit=max(3, limit // 3)))
    if not cc_only:
        if "reddit" in selected:
            results.extend(search_reddit_keenable(query, limit=max(4, limit // 2)))
        # Note: pexels & pixabay are handled above via native APIs / clean stock search!
        exa_plats = [p for p in selected if p in ("youtube", "tiktok", "instagram")]
        if exa_plats:
            results.extend(search_exa(query, exa_plats, limit=limit, raw_mode=raw_mode, api_key=api_key, config=cfg))

    seen = set()
    uniq: List[SourcedCandidate] = []
    for r in results:
        if r.url in seen:
            continue
        if cc_only and not r.monetization_safe:
            continue
        seen.add(r.url)
        compute_ready_score(r, query, negative_terms=negative_terms, recent_creators_7d=recent_7d)
        uniq.append(r)

    uniq.sort(key=lambda c: (c.ready_status != "REJECTED", c.score, c.monetization_safe), reverse=True)
    return uniq[:limit]


def search_smart_routed(
    *,
    conceptual_queries: Optional[List[str]] = None,
    literal_queries: Optional[List[str]] = None,
    metaphorical_queries: Optional[List[str]] = None,
    fallback_query: str = "",
    providers: Optional[List[str]] = None,
    negative_terms: Optional[List[str]] = None,
    cc_only: bool = False,
    raw_mode: bool = True,
    min_ready_score: float = 58.0,
    limit: int = 16,
    episode_id: str = "",
    channel_id: Optional[str] = None,
    api_key: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> Dict[str, List[SourcedCandidate]]:
    """Execute 3-Tier Visual Intent Routed Search and partition results into `accepted` vs `rejected`."""
    cfg = config or get_config()
    selected = {p.lower() for p in (providers or ["vault", "pexels", "pixabay", "wikimedia", "inaturalist", "youtube", "tiktok", "reddit"])}
    recent_7d = _collect_recent_creators_7d(episode_id, channel_id, cfg)

    c_qs = [q for q in (conceptual_queries or []) if q.strip()]
    l_qs = [q for q in (literal_queries or []) if q.strip()]
    m_qs = [q for q in (metaphorical_queries or []) if q.strip()]
    if not (c_qs or l_qs or m_qs) and fallback_query.strip():
        c_qs = [fallback_query.strip()]
        l_qs = [fallback_query.strip()]

    pool: List[Tuple[str, str, SourcedCandidate]] = []  # (tier, query_used, candidate)

    # Tier 0: Local B-Roll Vault
    if "vault" in selected and not cc_only:
        vq = (c_qs[0] if c_qs else (l_qs[0] if l_qs else fallback_query)).strip()
        if vq:
            for c in search_local_vault(vq, limit=4, episode_id=episode_id, channel_id=channel_id, negative_terms=negative_terms, recent_creators_7d=recent_7d, config=cfg):
                pool.append(("vault", vq, c))

    # Tier 1: Conceptual Kinematic Stock -> Pexels / Pixabay / Wikimedia
    for q in c_qs[:2]:
        if "pexels" in selected:
            for c in search_pexels_videos(q, limit=6, config=cfg):
                pool.append(("conceptual_stock", q, c))
        if "pixabay" in selected:
            for c in search_pixabay_videos(q, limit=4, config=cfg):
                pool.append(("conceptual_stock", q, c))
        if "wikimedia" in selected:
            for c in search_wikimedia(q, limit=4):
                pool.append(("conceptual_stock", q, c))

    # Tier 2: Literal UGC -> YouTube / TikTok / Reddit (only when not cc_only)
    if not cc_only:
        ugc_plats = [p for p in ("youtube", "tiktok", "instagram") if p in selected]
        for q in l_qs[:2]:
            if ugc_plats:
                for c in search_exa(q, ugc_plats, limit=6, raw_mode=raw_mode, api_key=api_key, config=cfg):
                    pool.append(("literal_ugc", q, c))
            if "reddit" in selected:
                for c in search_reddit_keenable(q, limit=4):
                    pool.append(("literal_ugc", q, c))

    # Tier 3: Metaphorical / Anatomy Cutaway B-Roll -> Pexels / iNaturalist / Wikimedia
    for q in m_qs[:1]:
        if "pexels" in selected:
            for c in search_pexels_videos(q, limit=3, config=cfg):
                pool.append(("metaphorical_broll", q, c))
        if "inaturalist" in selected:
            for c in search_inaturalist(q, limit=3):
                pool.append(("metaphorical_broll", q, c))

    seen_urls: Set[str] = set()
    accepted: List[SourcedCandidate] = []
    rejected: List[SourcedCandidate] = []
    for tier, q_used, cand in pool:
        if cand.url in seen_urls:
            continue
        if cc_only and not cand.monetization_safe:
            continue
        seen_urls.add(cand.url)
        cand.query_tier = tier
        compute_ready_score(cand, q_used, negative_terms=negative_terms, recent_creators_7d=recent_7d)
        if cand.ready_status == "REJECTED" or cand.score < min_ready_score:
            rejected.append(cand)
        else:
            accepted.append(cand)

    accepted.sort(key=lambda c: (c.score, c.monetization_safe), reverse=True)
    rejected.sort(key=lambda c: c.score, reverse=True)
    return {
        "accepted": accepted[:limit],
        "rejected": rejected[:12],
    }


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
    auto_trim: bool = False,
    beat_index: Optional[int] = None,
    scenario: str = "",
    crop_x_norm: float = 0.50,
    local_path: str = "",
    config: Optional[StudioConfig] = None,
) -> dict:
    """Download (or copy from Vault) a clip into `<channel>/<episode>/assets/` with optional Auto-Trim & Golden Moment lock."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)
    assets_dir.mkdir(parents=True, exist_ok=True)

    verdict = classify_license(license_str, platform)
    if prof.ypp.forbid_nc_nd_licenses and verdict.risk_level == "BLOCKED":
        raise ValueError(f"Unduhan ditolak oleh YPP Gate: lisensi {verdict.normalized} dilarang untuk monetisasi.")

    ts_tag = int(time.time()) % 100000
    base_name = f"{_slugify(creator or platform, 18)}_{_slugify(title or 'clip', 28)}_{ts_tag}"

    downloaded_path: Optional[Path] = None
    if url.startswith("vault://") or (local_path and Path(local_path).exists()):
        src_p = Path(local_path)
        if not src_p.exists() and url.startswith("vault://"):
            parts = url.replace("vault://", "").split("/", 2)
            if len(parts) == 3:
                src_p = cfg.episode_assets_dir(parts[1], parts[0]) / parts[2]
        if not src_p.exists():
            raise FileNotFoundError(f"File Vault lokal tidak ditemukan: {url}")
        dest = assets_dir / f"{base_name}{src_p.suffix.lower() or '.mp4'}"
        shutil.copy2(src_p, dest)
        downloaded_path = dest
    else:
        low_u = url.lower()
        parsed_u = urllib.parse.urlparse(low_u)
        is_direct_media = (
            any(parsed_u.path.endswith(ext) for ext in (".mp4", ".webm", ".ogv", ".mov", ".jpg", ".jpeg", ".png", ".webp"))
            or "upload.wikimedia.org" in low_u
            or "inaturalist" in low_u
            or "videos.pexels.com" in low_u
            or "cdn.pixabay.com" in low_u
        )

        if is_direct_media:
            ext = Path(urllib.parse.urlparse(url).path).suffix.lower() or ".mp4"
            dest = assets_dir / f"{base_name}{ext}"
            req = urllib.request.Request(url, headers={"User-Agent": UA_WEB})
            with urllib.request.urlopen(req, timeout=75) as resp:
                data = resp.read()
            if data[:200].lstrip().lower().startswith((b"<!doctype", b"<html")):
                raise RuntimeError("Server mengembalikan halaman HTML, bukan file media.")
            dest.write_bytes(data)
            downloaded_path = dest
        else:
            out_tpl = str(assets_dir / f"{base_name}.%(ext)s")
            ff_args = ["--ffmpeg-location", str(cfg.ffmpeg_bin)] if cfg.ffmpeg_bin else []
            cmd = [
                cfg.ytdlp_cmd(),
                "--no-warnings",
                "--no-playlist",
                "--js-runtimes",
                "node",
                *ff_args,
                "-f",
                "bv*[height<=1080][ext=mp4]+ba[ext=m4a]/b[height<=1080][ext=mp4]/b",
                "--merge-output-format",
                "mp4",
                "-o",
                out_tpl,
                "--",
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

    trim_info = None
    if auto_trim and downloaded_path.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv"}:
        from .vision_gate import auto_trim_and_lock_golden_moment

        trim_info = auto_trim_and_lock_golden_moment(
            clip_path=downloaded_path,
            episode_id=episode_id,
            channel_id=prof.channel_id,
            beat_index=beat_index,
            scenario=scenario or title,
            crop_x_norm_hint=crop_x_norm,
            cfg=cfg,
        )
        if trim_info.get("trimmed_path"):
            downloaded_path = Path(trim_info["trimmed_path"])

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
        "beat_index": beat_index,
        "trim_window": trim_info.get("trim_window") if trim_info else None,
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
        "trim_info": trim_info,
        "license_verdict": verdict.model_dump(),
        "credits_file": str(credits_path) if credits_path else None,
    }
