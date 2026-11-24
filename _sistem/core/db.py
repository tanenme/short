"""SQLite Single-Source-of-Truth state store for episodes, background jobs, and analytics metrics."""

from __future__ import annotations

import json
import math
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .config import StudioConfig, get_config


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _sanitize_json_value(obj: Any) -> Any:
    """Recursively replace non-finite floats (NaN/Inf) with None so JSON responses never 500."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize_json_value(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize_json_value(v) for v in obj]
    return obj


class StudioDB:
    def __init__(self, db_path: Optional[Path] = None, config: Optional[StudioConfig] = None) -> None:
        self.cfg = config or get_config()
        self.db_path = db_path or (self.cfg.system_dir / "state" / "studio.db")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.db_path), timeout=15.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS episodes (
                    channel_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    stage TEXT NOT NULL DEFAULT 'idea',
                    preflight_verdict TEXT NOT NULL DEFAULT 'BLOCKED',
                    preflight_score INTEGER NOT NULL DEFAULT 0,
                    word_count INTEGER NOT NULL DEFAULT 0,
                    clips_count INTEGER NOT NULL DEFAULT 0,
                    has_vo INTEGER NOT NULL DEFAULT 0,
                    has_output INTEGER NOT NULL DEFAULT 0,
                    has_qc_sheet INTEGER NOT NULL DEFAULT 0,
                    issues_json TEXT NOT NULL DEFAULT '[]',
                    scheduled_slot TEXT NOT NULL DEFAULT '',
                    published_url TEXT NOT NULL DEFAULT '',
                    synced_at TEXT NOT NULL,
                    PRIMARY KEY (channel_id, episode_id)
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    job_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'queued',
                    progress_pct INTEGER NOT NULL DEFAULT 0,
                    progress_msg TEXT NOT NULL DEFAULT '',
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT
                );

                CREATE TABLE IF NOT EXISTS metrics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    channel_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    recorded_at TEXT NOT NULL,
                    views INTEGER NOT NULL DEFAULT 0,
                    likes INTEGER NOT NULL DEFAULT 0,
                    comments INTEGER NOT NULL DEFAULT 0,
                    subs_gained INTEGER NOT NULL DEFAULT 0,
                    avg_view_pct REAL NOT NULL DEFAULT 0.0,
                    swipe_away_pct REAL NOT NULL DEFAULT 0.0
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_created ON jobs(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_metrics_ep ON metrics(channel_id, episode_id, recorded_at DESC);

                CREATE TABLE IF NOT EXISTS broll_vault (
                    clip_id TEXT PRIMARY KEY,
                    channel_id TEXT NOT NULL,
                    episode_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    filepath TEXT NOT NULL,
                    creator TEXT NOT NULL DEFAULT '',
                    platform TEXT NOT NULL DEFAULT 'local',
                    license TEXT NOT NULL DEFAULT 'UNKNOWN',
                    risk_level TEXT NOT NULL DEFAULT 'HIGH',
                    monetization_safe INTEGER NOT NULL DEFAULT 0,
                    width INTEGER NOT NULL DEFAULT 0,
                    height INTEGER NOT NULL DEFAULT 0,
                    duration_s REAL NOT NULL DEFAULT 0.0,
                    cleanliness_score INTEGER NOT NULL DEFAULT 80,
                    caption_zone_clean INTEGER NOT NULL DEFAULT 1,
                    tags_text TEXT NOT NULL DEFAULT '',
                    summary_text TEXT NOT NULL DEFAULT '',
                    indexed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vault_ep ON broll_vault(channel_id, episode_id);
                """
            )
            # Safe migration for existing studio.db without progress_pct column
            cols = {r["name"] for r in conn.execute("PRAGMA table_info(jobs)").fetchall()}
            if "progress_pct" not in cols:
                conn.execute("ALTER TABLE jobs ADD COLUMN progress_pct INTEGER NOT NULL DEFAULT 0")

    def upsert_episode(self, row: Dict[str, Any]) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO episodes (
                    channel_id, episode_id, title, subject, stage,
                    preflight_verdict, preflight_score, word_count, clips_count,
                    has_vo, has_output, has_qc_sheet, issues_json, synced_at
                ) VALUES (
                    :channel_id, :episode_id, :title, :subject, :stage,
                    :preflight_verdict, :preflight_score, :word_count, :clips_count,
                    :has_vo, :has_output, :has_qc_sheet, :issues_json, :synced_at
                )
                ON CONFLICT(channel_id, episode_id) DO UPDATE SET
                    title=excluded.title,
                    subject=excluded.subject,
                    stage=excluded.stage,
                    preflight_verdict=excluded.preflight_verdict,
                    preflight_score=excluded.preflight_score,
                    word_count=excluded.word_count,
                    clips_count=excluded.clips_count,
                    has_vo=excluded.has_vo,
                    has_output=excluded.has_output,
                    has_qc_sheet=excluded.has_qc_sheet,
                    issues_json=excluded.issues_json,
                    synced_at=excluded.synced_at
                """,
                row,
            )

    def list_episodes(self, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if channel_id:
                rows = conn.execute(
                    "SELECT * FROM episodes WHERE channel_id = ? ORDER BY episode_id DESC",
                    (channel_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM episodes ORDER BY channel_id, episode_id DESC").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["has_vo"] = bool(d["has_vo"])
            d["has_output"] = bool(d["has_output"])
            d["has_qc_sheet"] = bool(d["has_qc_sheet"])
            try:
                d["issues"] = json.loads(d.pop("issues_json") or "[]")
            except Exception:
                d["issues"] = []
            out.append(d)
        return out

    def sync_channel(self, channel_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """Scan disk for a channel, run lightweight audit + YPP preflight, and persist into SQLite."""
        from sourcing.preflight import run_preflight_gate
        from .validator import audit_episode_folder

        prof = self.cfg.get_channel(channel_id)
        ch_dir = self.cfg.channel_dir(prof.channel_id)
        if not ch_dir.exists():
            return []

        ep_dirs = [
            d
            for d in sorted(ch_dir.iterdir())
            if d.is_dir()
            and not d.name.startswith("_")
            and d.name.lower() not in ("bgm", "sfx", "background sound", "backgorud sound", "effek sound", "effek sounf")
        ]

        now = _now_iso()
        seen_ids = set()
        for d in ep_dirs:
            ep_id = d.name
            seen_ids.add(ep_id)
            manifest, _ = audit_episode_folder(ep_id, prof.channel_id, self.cfg)
            pf = run_preflight_gate(ep_id, prof.channel_id, run_autoqc=False, config=self.cfg, write_report=False)
            out_dir = d / "output"
            has_output = (out_dir / f"final_{ep_id}.mp4").exists()
            has_qc = (out_dir / f"qc_sheet_{ep_id}.jpg").exists()
            assets_dir = self.cfg.episode_assets_dir(ep_id, prof.channel_id)
            has_vo = bool(manifest.vo_file and (assets_dir / manifest.vo_file).exists())
            issues = [
                {"code": c.code, "severity": c.severity, "message": c.message}
                for c in pf.checks
                if c.severity in ("FAIL", "WARN")
            ]
            self.upsert_episode(
                {
                    "channel_id": prof.channel_id,
                    "episode_id": ep_id,
                    "title": manifest.title,
                    "subject": manifest.subject,
                    "stage": manifest.status.value,
                    "preflight_verdict": pf.stage_verdict,
                    "preflight_score": pf.score,
                    "word_count": manifest.script.word_count,
                    "clips_count": len(manifest.clips),
                    "has_vo": int(has_vo),
                    "has_output": int(has_output),
                    "has_qc_sheet": int(has_qc),
                    "issues_json": json.dumps(issues, ensure_ascii=False),
                    "synced_at": now,
                }
            )

        # Prune deleted folders from DB
        with self._conn() as conn:
            existing = [
                r["episode_id"]
                for r in conn.execute("SELECT episode_id FROM episodes WHERE channel_id = ?", (prof.channel_id,)).fetchall()
            ]
            for stale_id in set(existing) - seen_ids:
                conn.execute(
                    "DELETE FROM episodes WHERE channel_id = ? AND episode_id = ?",
                    (prof.channel_id, stale_id),
                )

        return self.list_episodes(prof.channel_id)

    # --- Job queue persistence ---
    def create_job(self, channel_id: str, episode_id: str, job_type: str, progress_msg: str = "Queued") -> str:
        job_id = f"job_{uuid.uuid4().hex[:10]}"
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO jobs (job_id, channel_id, episode_id, job_type, status, progress_msg, created_at)
                VALUES (?, ?, ?, ?, 'queued', ?, ?)
                """,
                (job_id, channel_id, episode_id, job_type, progress_msg, _now_iso()),
            )
        return job_id

    def update_job(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress_pct: Optional[int] = None,
        progress_msg: Optional[str] = None,
        result: Optional[dict] = None,
        error: Optional[str] = None,
    ) -> None:
        fields = []
        params: List[Any] = []
        if status is not None:
            fields.append("status = ?")
            params.append(status)
            if status == "running":
                fields.append("started_at = ?")
                params.append(_now_iso())
            elif status in ("succeeded", "failed"):
                fields.append("finished_at = ?")
                params.append(_now_iso())
                if status == "succeeded" and progress_pct is None:
                    progress_pct = 100
        if progress_pct is not None:
            fields.append("progress_pct = ?")
            params.append(max(0, min(100, int(progress_pct))))
        if progress_msg is not None:
            fields.append("progress_msg = ?")
            params.append(progress_msg)
        if result is not None:
            fields.append("result_json = ?")
            params.append(json.dumps(_sanitize_json_value(result), ensure_ascii=False, allow_nan=False))
        if error is not None:
            fields.append("error = ?")
            params.append(error)
        if not fields:
            return
        params.append(job_id)
        with self._conn() as conn:
            conn.execute(f"UPDATE jobs SET {', '.join(fields)} WHERE job_id = ?", params)

    def get_job(self, job_id: str) -> Optional[Dict[str, Any]]:
        with self._conn() as conn:
            r = conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)).fetchone()
        if not r:
            return None
        d = dict(r)
        try:
            d["result"] = _sanitize_json_value(json.loads(d.pop("result_json") or "{}"))
        except Exception:
            d["result"] = {}
        return d

    def list_jobs(self, limit: int = 25) -> List[Dict[str, Any]]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["result"] = _sanitize_json_value(json.loads(d.pop("result_json") or "{}"))
            except Exception:
                d["result"] = {}
            out.append(d)
        return out

    # --- Analytics metrics (Phase 5 ready) ---
    def record_metrics(
        self,
        channel_id: str,
        episode_id: str,
        views: int,
        likes: int = 0,
        comments: int = 0,
        subs_gained: int = 0,
        avg_view_pct: float = 0.0,
        swipe_away_pct: float = 0.0,
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO metrics (channel_id, episode_id, recorded_at, views, likes, comments, subs_gained, avg_view_pct, swipe_away_pct)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (channel_id, episode_id, _now_iso(), views, likes, comments, subs_gained, avg_view_pct, swipe_away_pct),
            )

    # --- Local Semantic B-Roll Vault (Sourcing & Vision v4.0) ---
    def sync_broll_vault(self, channel_id: Optional[str] = None) -> int:
        """Index clean video clips across channel episodes into SQLite broll_vault for <10ms local reuse."""
        import re

        channels_to_scan = [self.cfg.get_channel(channel_id).channel_id] if channel_id else list(self.cfg.channels.keys())
        now = _now_iso()
        indexed = 0
        rows_to_upsert: List[tuple] = []

        for cid in channels_to_scan:
            ch_dir = self.cfg.channel_dir(cid)
            if not ch_dir.exists():
                continue
            for ep_dir in sorted(ch_dir.iterdir()):
                if not ep_dir.is_dir() or ep_dir.name.startswith("_"):
                    continue
                assets_dir = self.cfg.episode_assets_dir(ep_dir.name, cid)
                if not assets_dir.exists():
                    continue

                meta_map: Dict[str, dict] = {}
                v_index: Dict[str, dict] = {}
                clean_map: Dict[str, dict] = {}
                for fname, target in (
                    ("_metadata.json", meta_map),
                    ("_clip_visual_index.json", v_index),
                    ("cleanliness_report.json", clean_map),
                ):
                    fp = assets_dir / fname
                    if fp.exists():
                        try:
                            loaded = json.loads(fp.read_text(encoding="utf-8"))
                            if isinstance(loaded, dict):
                                if fname == "cleanliness_report.json" and "clips" in loaded and isinstance(loaded["clips"], dict):
                                    target.update(loaded["clips"])
                                else:
                                    target.update(loaded)
                        except Exception:
                            pass

                for mp4 in sorted(assets_dir.glob("*.mp4")):
                    if mp4.name.startswith("_"):
                        continue
                    c_info = clean_map.get(mp4.name, {})
                    vi_info = v_index.get(mp4.name, {})
                    m_info = meta_map.get(mp4.name, {})

                    clean_score = int(
                        c_info.get("score")
                        or (int(vi_info.get("cleanliness", 8)) * 10 if vi_info.get("cleanliness") is not None else 82)
                    )
                    if clean_score < 70 or bool(vi_info.get("has_burned_text", False)):
                        continue

                    creator = str(m_info.get("creator") or "")
                    if not creator and mp4.name.startswith("@"):
                        creator = mp4.name.split("_")[0]
                    tags_list = vi_info.get("tags") or []
                    shots_list = vi_info.get("shots") or []
                    shot_summaries = " ".join(str(s.get("summary", "")) for s in shots_list if isinstance(s, dict))
                    stem_words = re.sub(r"[@0-9_\-]+", " ", mp4.stem).strip()
                    tags_str = " ".join([*(str(t) for t in tags_list), ep_dir.name.replace("-", " "), stem_words]).strip()
                    summary_str = " ".join(
                        filter(None, [str(vi_info.get("summary") or ""), str(m_info.get("title") or ""), shot_summaries])
                    ).strip()

                    clip_id = f"{cid}:{ep_dir.name}:{mp4.name}"
                    rows_to_upsert.append(
                        (
                            clip_id,
                            cid,
                            ep_dir.name,
                            mp4.name,
                            str(mp4.resolve()),
                            creator[:80],
                            str(m_info.get("source") or "local_vault"),
                            str(m_info.get("license") or "SOCIAL_TRANSFORMATIVE"),
                            str(m_info.get("license_risk") or "HIGH"),
                            int(bool(m_info.get("publikasi_aman", False))),
                            int(m_info.get("width") or 0),
                            int(m_info.get("height") or 0),
                            float(m_info.get("duration") or 0.0),
                            clean_score,
                            int(bool(vi_info.get("caption_zone_clean", True))),
                            tags_str[:500],
                            summary_str[:600],
                            now,
                        )
                    )
                    indexed += 1

        if rows_to_upsert:
            with self._conn() as conn:
                conn.executemany(
                    """
                    INSERT INTO broll_vault (
                        clip_id, channel_id, episode_id, filename, filepath,
                        creator, platform, license, risk_level, monetization_safe,
                        width, height, duration_s, cleanliness_score, caption_zone_clean,
                        tags_text, summary_text, indexed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(clip_id) DO UPDATE SET
                        filepath=excluded.filepath,
                        creator=excluded.creator,
                        cleanliness_score=excluded.cleanliness_score,
                        caption_zone_clean=excluded.caption_zone_clean,
                        tags_text=excluded.tags_text,
                        summary_text=excluded.summary_text,
                        indexed_at=excluded.indexed_at
                    """,
                    rows_to_upsert,
                )
        return indexed

    def list_broll_vault(self, exclude_episode_id: str = "") -> List[Dict[str, Any]]:
        with self._conn() as conn:
            if exclude_episode_id:
                rows = conn.execute(
                    "SELECT * FROM broll_vault WHERE episode_id != ? ORDER BY cleanliness_score DESC",
                    (exclude_episode_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM broll_vault ORDER BY cleanliness_score DESC").fetchall()
        return [dict(r) for r in rows]

