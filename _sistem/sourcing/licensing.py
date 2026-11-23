"""License risk classifier, attribution/CREDITS generator, and cross-episode creator rotation ledger."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config


class LicenseVerdict(BaseModel):
    raw_license: str
    normalized: str
    risk_level: Literal["LOW", "MEDIUM", "HIGH", "BLOCKED"]
    monetization_safe: bool
    attribution_required: bool
    share_alike_required: bool = False
    note: str


def classify_license(raw_license: str, source_platform: str = "") -> LicenseVerdict:
    """Classify media license for monetized YouTube Shorts YPP compliance."""
    raw = (raw_license or "").strip()
    low = raw.lower()
    plat = (source_platform or "").lower()

    # Pexels / Pixabay commercial-free stock
    if any(k in plat for k in ("pexels", "pixabay")) or any(k in low for k in ("pexels", "pixabay")):
        return LicenseVerdict(
            raw_license=raw or "Pexels/Pixabay Free",
            normalized="STOCK_FREE",
            risk_level="LOW",
            monetization_safe=True,
            attribution_required=False,
            note="Stock bebas komersial (tetap butuh >=2 transformasi anti-reused).",
        )

    # Public Domain / CC0
    if any(k in low for k in ("cc0", "public domain", "publicdomain")) or low == "pd":
        return LicenseVerdict(
            raw_license=raw,
            normalized="CC0 / Public Domain",
            risk_level="LOW",
            monetization_safe=True,
            attribution_required=False,
            note="Domain publik / CC0 — aman penuh untuk monetisasi.",
        )

    # Creative Commons NC / ND -> BLOCKED for monetized Shorts derivatives
    if ("by-nc" in low) or ("noncommercial" in low) or ("non-commercial" in low):
        return LicenseVerdict(
            raw_license=raw,
            normalized="CC-NC (FORBIDDEN)",
            risk_level="BLOCKED",
            monetization_safe=False,
            attribution_required=True,
            note="DILARANG untuk channel monetisasi (Non-Commercial).",
        )
    if ("by-nd" in low) or ("noderiv" in low) or ("no-derivatives" in low):
        return LicenseVerdict(
            raw_license=raw,
            normalized="CC-ND (FORBIDDEN)",
            risk_level="BLOCKED",
            monetization_safe=False,
            attribution_required=True,
            note="DILARANG untuk Shorts crop/edit (No-Derivatives).",
        )

    # CC BY-SA
    if "by-sa" in low:
        return LicenseVerdict(
            raw_license=raw,
            normalized="CC BY-SA",
            risk_level="MEDIUM",
            monetization_safe=True,
            attribution_required=True,
            share_alike_required=True,
            note="Boleh komersial, WAJIB atribusi & share-alike.",
        )

    # CC BY / Creative Commons Attribution
    if "cc by" in low or "cc-by" in low or "creative commons" in low or re.search(r"\bby\b", low):
        return LicenseVerdict(
            raw_license=raw,
            normalized="CC BY",
            risk_level="LOW",
            monetization_safe=True,
            attribution_required=True,
            note="Boleh komersial, WAJIB cantumkan kredit kreator di deskripsi.",
        )

    # Social / YouTube Standard ToS (fair-use transformative only)
    if any(k in low for k in ("standard youtube", "youtube tos", "all rights reserved")) or any(
        k in plat for k in ("tiktok", "youtube", "reddit", "instagram")
    ):
        return LicenseVerdict(
            raw_license=raw or f"{source_platform or 'Social'} ToS",
            normalized="SOCIAL_TRANSFORMATIVE",
            risk_level="HIGH",
            monetization_safe=False,
            attribution_required=True,
            note="All rights reserved: wajib transformasi berat (<=5s, >=2 efek, VO >=70%) + rotasi kreator.",
        )

    return LicenseVerdict(
        raw_license=raw or "UNKNOWN",
        normalized="UNKNOWN",
        risk_level="HIGH",
        monetization_safe=False,
        attribution_required=True,
        note="Lisensi belum tercatat — verifikasi sumber sebelum publish.",
    )


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def _infer_creator_from_filename(filename: str) -> str:
    """Extract @handle from TikTok/social filenames like `@cat.craft.stories_764077..._hd.mp4`."""
    m = re.match(r"^(@[A-Za-z0-9._-]+)_\d+", filename)
    if m:
        return m.group(1).lower()
    return ""


def collect_episode_creators(episode_dir: Path, assets_dir: Path) -> Dict[str, int]:
    """Return {creator_key: clip_count} from _metadata.json and filename handles."""
    counts: Dict[str, int] = {}
    meta_file = assets_dir / "_metadata.json"
    meta: Dict[str, dict] = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    if not assets_dir.exists():
        return counts

    video_files = [
        p
        for p in sorted(assets_dir.iterdir())
        if p.is_file() and p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
        and not p.name.startswith(("thumb_", "_temp_"))
    ]
    for vf in video_files:
        m = meta.get(vf.name, {})
        cre = str(m.get("creator") or "").strip().lower()
        if not cre or cre in ("na", "unknown", "tidak diketahui"):
            cre = _infer_creator_from_filename(vf.name)
        if cre:
            counts[cre] = counts.get(cre, 0) + 1
    return counts


def check_creator_rotation(
    episode_id: str,
    channel_id: Optional[str] = None,
    window_days: int = 7,
    config: Optional[StudioConfig] = None,
) -> dict:
    """Check both intra-episode creator concentration and cross-episode 7-day creator reuse."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    ch_dir = cfg.channel_dir(prof.channel_id)
    ep_dir = cfg.episode_dir(episode_id, prof.channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)

    current_creators = collect_episode_creators(ep_dir, assets_dir)
    intra_duplicates = {c: n for c, n in current_creators.items() if n > 1}

    # Check sibling episodes modified within `window_days`
    cutoff = datetime.now() - timedelta(days=window_days)
    cross_overlaps: Dict[str, List[str]] = {}
    if ch_dir.exists() and current_creators:
        for other in sorted(ch_dir.iterdir()):
            if not other.is_dir() or other.name == episode_id or other.name.startswith("_"):
                continue
            if other.name.lower() in ("bgm", "sfx", "backgorud sound", "effek sounf"):
                continue
            mtime = datetime.fromtimestamp(other.stat().st_mtime)
            if mtime < cutoff:
                continue
            other_assets = cfg.episode_assets_dir(other.name, prof.channel_id)
            other_creators = collect_episode_creators(other, other_assets)
            for cre in current_creators:
                if cre in other_creators:
                    cross_overlaps.setdefault(cre, []).append(other.name)

    return {
        "episode_id": episode_id,
        "unique_creators": len(current_creators),
        "creators": current_creators,
        "intra_episode_repeats": intra_duplicates,
        "cross_episode_overlaps_7d": cross_overlaps,
    }


def generate_credits_markdown(
    episode_id: str,
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
    write_file: bool = True,
) -> Tuple[str, Optional[Path]]:
    """Build standardized CREDITS.md from assets/_metadata.json and filename provenance."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    ep_dir = cfg.episode_dir(episode_id, prof.channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)

    meta_file = assets_dir / "_metadata.json"
    meta: Dict[str, dict] = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    media_files = (
        sorted(
            p.name
            for p in assets_dir.iterdir()
            if p.is_file()
            and p.suffix.lower() in {".mp4", ".webm", ".mov", ".mkv", ".jpg", ".jpeg", ".png", ".webp"}
            and not p.name.startswith(("thumb_", "_temp_"))
        )
        if assets_dir.exists()
        else []
    )
    all_keys = list(dict.fromkeys(media_files + list(meta.keys())))

    lines = [
        f"# Kredit & Atribusi Materi — {episode_id}",
        "",
        "Tempel blok atribusi di bawah ke deskripsi YouTube (wajib untuk materi CC BY / CC BY-SA).",
        "",
        "| # | File | Sumber | Kreator | Lisensi | Risiko | Monetisasi | URL |",
        "|---|---|---|---|---|---|---|---|",
    ]
    credit_bullets: List[str] = []
    stats = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "BLOCKED": 0}

    for idx, fname in enumerate(all_keys, 1):
        m = meta.get(fname, {})
        src = str(m.get("source") or ("TikTok" if fname.startswith("@") else "local"))
        cre = str(m.get("creator") or _infer_creator_from_filename(fname) or "Tidak diketahui")
        raw_lic = str(m.get("license") or "")
        verdict = classify_license(raw_lic, src)
        stats[verdict.risk_level] += 1
        url = str(m.get("page") or m.get("url") or "-")
        title = str(m.get("title") or fname)[:55]
        safe_str = "AMAN" if verdict.monetization_safe else ("BLOKIR" if verdict.risk_level == "BLOCKED" else "TRANSFORM")
        lines.append(
            f"| {idx} | `{fname}` | {src} | {cre[:28]} | {verdict.normalized} | {verdict.risk_level} | {safe_str} | {url} |"
        )
        if verdict.attribution_required:
            lurl = f" ({m.get('license_url')})" if m.get("license_url") else ""
            credit_bullets.append(f"- `{fname}`: {title} — oleh {cre} — {verdict.normalized}{lurl} — {url}")

    lines.extend(["", "## Blok Kredit Siap Tempel (Deskripsi YouTube)", ""])
    if credit_bullets:
        lines.extend(credit_bullets)
    else:
        lines.append("- Tidak ada atribusi wajib (semua materi CC0/Stock bebas).")

    lines.extend(
        [
            "",
            "## Ringkasan Risiko Lisensi",
            f"- Total materi: {len(all_keys)}",
            f"- LOW (CC0/CC-BY/Stock): {stats['LOW']} | MEDIUM (CC-BY-SA): {stats['MEDIUM']} | "
            f"HIGH (ToS/Transformative): {stats['HIGH']} | BLOCKED (NC/ND): {stats['BLOCKED']}",
            "",
        ]
    )

    md_text = "\n".join(lines)
    out_path: Optional[Path] = None
    if write_file:
        out_path = ep_dir / "CREDITS.md"
        out_path.write_text(md_text, encoding="utf-8")
    return md_text, out_path
