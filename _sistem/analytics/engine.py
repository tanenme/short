"""Growth Analytics Engine, Kill/Scale 30-Day Validation Logic, CSV Importer, and Weekly Report Generator."""

from __future__ import annotations

import csv
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config
from core.db import StudioDB


class EpisodePerfItem(BaseModel):
    channel_id: str
    episode_id: str
    title: str = ""
    subject: str = ""
    views: int = 0
    likes: int = 0
    comments: int = 0
    subs_gained: int = 0
    avg_view_pct: float = 0.0
    swipe_away_pct: float = 0.0
    stay_to_watch_pct: float = 0.0
    subs_per_1k_views: float = 0.0
    virality_score: float = 0.0
    recorded_at: str = ""


class SubjectStat(BaseModel):
    subject: str
    videos: int
    total_views: int
    total_subs: int
    avg_apv: float
    avg_stay_pct: float
    avg_virality: float
    action: Literal["SCALE", "KEEP", "KILL_OR_REWORK"]


class ChannelAnalyticsSummary(BaseModel):
    channel_id: str
    generated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    tracked_videos: int = 0
    target_videos_30d: int = 90
    target_subs_30d: int = 1000
    total_views: int = 0
    total_subs: int = 0
    max_views_single: int = 0
    avg_views: float = 0.0
    avg_subs_per_video: float = 0.0
    avg_apv: float = 0.0
    avg_stay_to_watch_pct: float = 0.0
    subs_progress_pct: float = 0.0
    validation_verdict: Literal["CLONE_READY", "SCALING", "IGNITION", "PIVOT_NEEDED", "NO_DATA"] = "NO_DATA"
    top_episodes: List[EpisodePerfItem] = Field(default_factory=list)
    bottom_episodes: List[EpisodePerfItem] = Field(default_factory=list)
    subject_breakdown: List[SubjectStat] = Field(default_factory=list)
    creative_directives: List[str] = Field(default_factory=list)


def _infer_subject(episode_id: str, title: str, focus_subjects: List[str]) -> str:
    blob = f"{episode_id} {title}".lower()
    for subj in focus_subjects:
        if subj.lower() in blob:
            return subj.lower()
    m = re.match(r"^\d+-([a-z]+)", episode_id.lower())
    return m.group(1) if m else "lainnya"


def compute_virality_score(views: int, subs_gained: int, avg_view_pct: float, swipe_away_pct: float) -> float:
    """Compute composite 0-100 Shorts virality score from retention, hook hold, sub conversion, and reach."""
    stay_pct = max(0.0, min(100.0, 100.0 - swipe_away_pct)) if swipe_away_pct > 0 else 70.0
    apv_score = min(100.0, (max(0.0, avg_view_pct) / 110.0) * 100.0)
    subs_per_1k = (subs_gained / views * 1000.0) if views > 0 else 0.0
    sub_score = min(100.0, (subs_per_1k / 5.0) * 100.0)
    reach_score = min(100.0, (views / 50000.0) * 100.0)
    score = (apv_score * 0.35) + (stay_pct * 0.30) + (sub_score * 0.20) + (reach_score * 0.15)
    return round(min(100.0, max(0.0, score)), 1)


def _fetch_latest_metrics(db: StudioDB, channel_id: str) -> List[dict]:
    with db._conn() as conn:
        rows = conn.execute(
            """
            SELECT m.*
            FROM metrics m
            INNER JOIN (
                SELECT channel_id, episode_id, MAX(id) AS max_id
                FROM metrics
                WHERE channel_id = ?
                GROUP BY channel_id, episode_id
            ) latest ON m.id = latest.max_id
            ORDER BY m.views DESC
            """,
            (channel_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def compute_channel_analytics(
    channel_id: Optional[str] = None,
    db: Optional[StudioDB] = None,
    config: Optional[StudioConfig] = None,
) -> ChannelAnalyticsSummary:
    """Aggregate latest performance metrics per episode into Blueprint 30-day validation & Kill/Scale insights."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    store = db or StudioDB(config=cfg)

    ep_rows = {e["episode_id"]: e for e in store.list_episodes(prof.channel_id)}
    latest_metrics = _fetch_latest_metrics(store, prof.channel_id)

    if not latest_metrics:
        return ChannelAnalyticsSummary(
            channel_id=prof.channel_id,
            validation_verdict="NO_DATA",
            creative_directives=[
                "Belum ada data performa Shorts yang dicatat. Mulai fase Ignition: upload 3 episode terbaik (009, 008, 010) lalu catat views, APV, swipe-away, dan subs setelah 48 jam."
            ],
        )

    perf_items: List[EpisodePerfItem] = []
    for m in latest_metrics:
        ep_id = m["episode_id"]
        ep_meta = ep_rows.get(ep_id, {})
        title = ep_meta.get("title") or ep_id
        subj = ep_meta.get("subject") or _infer_subject(ep_id, title, prof.focus_subjects)
        views = int(m["views"])
        subs = int(m["subs_gained"])
        apv = float(m["avg_view_pct"])
        swipe = float(m["swipe_away_pct"])
        stay = round(max(0.0, min(100.0, 100.0 - swipe)), 1) if swipe > 0 else 0.0
        subs_1k = round((subs / views) * 1000.0, 2) if views > 0 else 0.0
        v_score = compute_virality_score(views, subs, apv, swipe)
        perf_items.append(
            EpisodePerfItem(
                channel_id=prof.channel_id,
                episode_id=ep_id,
                title=title,
                subject=subj,
                views=views,
                likes=int(m["likes"]),
                comments=int(m["comments"]),
                subs_gained=subs,
                avg_view_pct=round(apv, 1),
                swipe_away_pct=round(swipe, 1),
                stay_to_watch_pct=stay,
                subs_per_1k_views=subs_1k,
                virality_score=v_score,
                recorded_at=str(m["recorded_at"]),
            )
        )

    perf_items.sort(key=lambda x: (x.virality_score, x.views), reverse=True)
    n = len(perf_items)
    total_views = sum(i.views for i in perf_items)
    total_subs = sum(i.subs_gained for i in perf_items)
    max_views = max(i.views for i in perf_items)
    avg_views = round(total_views / n, 1)
    avg_subs = round(total_subs / n, 2)
    avg_apv = round(sum(i.avg_view_pct for i in perf_items) / n, 1)
    stay_vals = [i.stay_to_watch_pct for i in perf_items if i.stay_to_watch_pct > 0]
    avg_stay = round(sum(stay_vals) / len(stay_vals), 1) if stay_vals else 0.0
    subs_pct = round(min(100.0, (total_subs / 1000.0) * 100.0), 1)

    # Blueprint v2.0 H31 Clone Decision Rule:
    # Lolos clone jika total_subs >= 1000 ATAU (1 video > 500rb views + subs/video > 15)
    if total_subs >= 1000 or (max_views >= 500_000 and avg_subs >= 15.0):
        verdict: Literal["CLONE_READY", "SCALING", "IGNITION", "PIVOT_NEEDED", "NO_DATA"] = "CLONE_READY"
    elif n < 5:
        verdict = "IGNITION"
    elif avg_views >= 5000 or avg_subs >= 5.0 or avg_apv >= 95.0:
        verdict = "SCALING"
    else:
        verdict = "PIVOT_NEEDED"

    # Subject-level Kill / Scale breakdown
    by_subj: Dict[str, List[EpisodePerfItem]] = {}
    for item in perf_items:
        by_subj.setdefault(item.subject, []).append(item)

    subj_stats: List[SubjectStat] = []
    for subj, grp in by_subj.items():
        s_avg_v = round(sum(x.virality_score for x in grp) / len(grp), 1)
        s_avg_apv = round(sum(x.avg_view_pct for x in grp) / len(grp), 1)
        s_stay_list = [x.stay_to_watch_pct for x in grp if x.stay_to_watch_pct > 0]
        s_avg_stay = round(sum(s_stay_list) / len(s_stay_list), 1) if s_stay_list else 0.0
        action: Literal["SCALE", "KEEP", "KILL_OR_REWORK"] = (
            "SCALE" if s_avg_v >= 65.0 else ("KEEP" if s_avg_v >= 45.0 else "KILL_OR_REWORK")
        )
        subj_stats.append(
            SubjectStat(
                subject=subj,
                videos=len(grp),
                total_views=sum(x.views for x in grp),
                total_subs=sum(x.subs_gained for x in grp),
                avg_apv=s_avg_apv,
                avg_stay_pct=s_avg_stay,
                avg_virality=s_avg_v,
                action=action,
            )
        )
    subj_stats.sort(key=lambda s: (s.avg_virality, s.total_views), reverse=True)

    # Generate actionable AI Creative Director directives
    directives: List[str] = []
    if verdict == "CLONE_READY":
        directives.append("TARGET VALIDASI TERCAPAI! Channel siap di-clone ke C2-momen dan C3-reddit.")
    elif verdict == "IGNITION":
        directives.append(f"Fase Ignition ({n}/5 video awal): fokus selesaikan minimal 5 upload untuk data validasi hook.")
    elif verdict == "PIVOT_NEEDED":
        directives.append("Performa di bawah ambang: evaluasi ulang hook 0-2 detik & topik (uji sudut domestik hiper-lokal).")

    if avg_stay > 0 and avg_stay < 72.0:
        directives.append(f"Stay-to-watch rata-rata {avg_stay}% (<72%): perkuat hook 0-1 detik (onomatopoeia + audio asli hewan + visual close-up).")
    elif avg_stay >= 75.0:
        directives.append(f"Hook kuat (Stay-to-watch {avg_stay}%): pertahankan template pertanyaan domestik 0-2 detik.")

    if avg_apv > 0 and avg_apv < 95.0:
        directives.append(f"APV rata-rata {avg_apv}% (<95%): pangkas naskah ke 42-48 kata (19-21 dtk) agar loop rate tembus >100%.")
    elif avg_apv >= 100.0:
        directives.append(f"Retensi luar biasa (APV {avg_apv}%): pacing 19-22 detik terbukti memicu rewatch/loop.")

    scale_subjs = [s.subject for s in subj_stats if s.action == "SCALE"]
    kill_subjs = [s.subject for s in subj_stats if s.action == "KILL_OR_REWORK"]
    if scale_subjs:
        directives.append(f"SCALE (gandakan produksi): topik {', '.join(scale_subjs[:3])}.")
    if kill_subjs:
        directives.append(f"KILL / REWORK (kurangi atau ubah sudut): topik {', '.join(kill_subjs[:2])}.")

    return ChannelAnalyticsSummary(
        channel_id=prof.channel_id,
        tracked_videos=n,
        total_views=total_views,
        total_subs=total_subs,
        max_views_single=max_views,
        avg_views=avg_views,
        avg_subs_per_video=avg_subs,
        avg_apv=avg_apv,
        avg_stay_to_watch_pct=avg_stay,
        subs_progress_pct=subs_pct,
        validation_verdict=verdict,
        top_episodes=perf_items[:5],
        bottom_episodes=list(reversed(perf_items[-3:])) if n >= 3 else [],
        subject_breakdown=subj_stats,
        creative_directives=directives,
    )


def import_youtube_csv(
    csv_path: Path,
    channel_id: Optional[str] = None,
    db: Optional[StudioDB] = None,
    config: Optional[StudioConfig] = None,
) -> dict:
    """Import YouTube Studio CSV export (flexible column matching) into studio.db metrics."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    store = db or StudioDB(config=cfg)

    if not csv_path.exists():
        raise FileNotFoundError(f"File CSV tidak ditemukan: {csv_path}")

    episodes = store.list_episodes(prof.channel_id)
    imported = 0
    unmatched: List[str] = []

    def _to_int(val: str) -> int:
        s = re.sub(r"[^\d]", "", str(val or ""))
        return int(s) if s else 0

    def _to_float(val: str) -> float:
        s = str(val or "").replace("%", "").replace(",", ".").strip()
        try:
            return float(s)
        except ValueError:
            return 0.0

    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            norm = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
            ep_key = norm.get("episode_id") or norm.get("episode") or ""
            vid_title = norm.get("video title") or norm.get("title") or norm.get("judul") or ""

            matched_ep = ""
            if ep_key:
                matched_ep = ep_key
            elif vid_title:
                low_t = vid_title.lower()
                for ep in episodes:
                    if ep["episode_id"].lower() in low_t or (ep["title"] and ep["title"].lower()[:20] in low_t):
                        matched_ep = ep["episode_id"]
                        break

            if not matched_ep:
                if vid_title and vid_title.lower() != "total":
                    unmatched.append(vid_title[:60])
                continue

            views = _to_int(norm.get("views") or norm.get("penayangan") or "0")
            likes = _to_int(norm.get("likes") or norm.get("suka") or "0")
            comments = _to_int(norm.get("comments") or norm.get("komentar") or "0")
            subs = _to_int(norm.get("subscribers") or norm.get("subs_gained") or norm.get("subs") or "0")
            apv = _to_float(norm.get("average percentage viewed (%)") or norm.get("avg_view_pct") or norm.get("apv") or "0")
            swipe = _to_float(norm.get("swiped away (%)") or norm.get("swipe_away_pct") or norm.get("swipe") or "0")
            if swipe == 0.0:
                viewed_pct = _to_float(norm.get("viewed (%)") or norm.get("stayed to watch (%)") or "0")
                if viewed_pct > 0:
                    swipe = round(max(0.0, 100.0 - viewed_pct), 1)

            store.record_metrics(
                prof.channel_id,
                matched_ep,
                views=views,
                likes=likes,
                comments=comments,
                subs_gained=subs,
                avg_view_pct=apv,
                swipe_away_pct=swipe,
            )
            imported += 1

    return {"imported": imported, "unmatched": unmatched}


def generate_weekly_report(
    channel_id: Optional[str] = None,
    db: Optional[StudioDB] = None,
    config: Optional[StudioConfig] = None,
    write_file: bool = True,
) -> Tuple[str, Optional[Path]]:
    """Generate automated weekly executive report `<channel>/_rapor/RAPOR-YYYY-MM-DD.md`."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    store = db or StudioDB(config=cfg)
    summary = compute_channel_analytics(prof.channel_id, store, cfg)
    all_eps = store.list_episodes(prof.channel_id)

    today = datetime.now().strftime("%Y-%m-%d")
    pub_ready = sum(1 for e in all_eps if e["preflight_verdict"] == "READY_TO_PUBLISH")
    ren_ready = sum(1 for e in all_eps if e["preflight_verdict"] == "READY_TO_RENDER")
    blocked = sum(1 for e in all_eps if e["preflight_verdict"] == "BLOCKED")

    lines = [
        f"# RAPOR MINGGUAN {prof.channel_id} — {today} (Enterprise Analytics Engine)",
        "",
        f"Channel: **{prof.display_name}** | Status Validasi: **{summary.validation_verdict}**",
        "",
        "## 1. Progres Target Validasi 30 Hari (1.000 Subs / 90 Shorts)",
        "",
        "| Metrik | Nilai Aktual | Target |",
        "|---|---|---|",
        f"| Episode di Pipeline | {len(all_eps)} (Publish: {pub_ready}, Render: {ren_ready}, Blocked: {blocked}) | 90 video |",
        f"| Episode Terukur (Posted) | {summary.tracked_videos} | 90 video |",
        f"| Total Views | {summary.total_views:,} | >500.000 (1 viral hit) |",
        f"| Total Subscribers Gained | {summary.total_subs:,} ({summary.subs_progress_pct}%) | 1.000 subs |",
        f"| Rata-rata Subs / Video | {summary.avg_subs_per_video} | >= 15 |",
        f"| Rata-rata APV (Retensi) | {summary.avg_apv}% | >= 100% |",
        f"| Stay-to-Watch (Hook 0-3 dtk) | {summary.avg_stay_to_watch_pct}% | >= 75% |",
        "",
    ]

    if summary.top_episodes:
        lines.extend(
            [
                "## 2. Top Performers (Format Menang)",
                "",
                "| Episode | Subjek | Views | Subs | APV | Stay | Skor Viral |",
                "|---|---|---:|---:|---:|---:|---:|",
            ]
        )
        for ep in summary.top_episodes:
            lines.append(
                f"| `{ep.episode_id}` | {ep.subject} | {ep.views:,} | +{ep.subs_gained} | {ep.avg_view_pct}% | {ep.stay_to_watch_pct}% | **{ep.virality_score}** |"
            )
        lines.append("")

    if summary.subject_breakdown:
        lines.extend(
            [
                "## 3. Keputusan Kill / Scale per Subjek",
                "",
                "| Subjek | Video | Total Views | Total Subs | Avg APV | Avg Viral | Putusan |",
                "|---|---:|---:|---:|---:|---:|---|",
            ]
        )
        for s in summary.subject_breakdown:
            lines.append(
                f"| {s.subject} | {s.videos} | {s.total_views:,} | +{s.total_subs} | {s.avg_apv}% | {s.avg_virality} | **{s.action}** |"
            )
        lines.append("")

    lines.append("## 4. Arahan Kreatif Otomatis (Untuk Batch Berikutnya)")
    lines.append("")
    for idx, d in enumerate(summary.creative_directives, 1):
        lines.append(f"{idx}. {d}")
    lines.append("")

    md = "\n".join(lines)
    out_path: Optional[Path] = None
    if write_file:
        rapor_dir = cfg.channel_dir(prof.channel_id) / "_rapor"
        rapor_dir.mkdir(parents=True, exist_ok=True)
        out_path = rapor_dir / f"RAPOR-{today}.md"
        out_path.write_text(md, encoding="utf-8")
    return md, out_path
