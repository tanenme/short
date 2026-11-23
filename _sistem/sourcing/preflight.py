"""Enterprise YPP Pre-Flight Safety Gate (Script + Fact-Check + Licensing + Cleanliness + Rotation + Transform)."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config
from core.models import QcCheckItem
from core.validator import audit_episode_folder
from .licensing import check_creator_rotation, classify_license
from .qc_video import run_episode_autoqc


class PreflightReport(BaseModel):
    channel_id: str
    episode_id: str
    checked_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    stage_verdict: Literal["READY_TO_PUBLISH", "READY_TO_RENDER", "BLOCKED"]
    can_render: bool
    can_publish: bool
    score: int = Field(ge=0, le=100)
    checks: List[QcCheckItem] = Field(default_factory=list)
    creator_rotation: dict = Field(default_factory=dict)
    license_summary: dict = Field(default_factory=dict)


def run_preflight_gate(
    episode_id: str,
    channel_id: Optional[str] = None,
    run_autoqc: bool = False,
    config: Optional[StudioConfig] = None,
    write_report: bool = True,
) -> PreflightReport:
    """Execute the full YPP & quality Pre-Flight Gate before render or publish."""
    cfg = config or get_config()
    prof = cfg.get_channel(channel_id)
    ep_dir = cfg.episode_dir(episode_id, prof.channel_id)
    assets_dir = cfg.episode_assets_dir(episode_id, prof.channel_id)

    # 1. Run core creative DNA & structural audit
    manifest, core_report = audit_episode_folder(episode_id, prof.channel_id, cfg)
    checks: List[QcCheckItem] = list(core_report.checks)

    # 2. License & provenance compliance check
    meta_file = assets_dir / "_metadata.json"
    meta = {}
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    lic_counts = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "BLOCKED": 0}
    blocked_files: List[str] = []
    for clip in manifest.clips:
        m = meta.get(clip.filename, {})
        verdict = classify_license(str(m.get("license") or clip.license), str(m.get("source") or clip.source_platform))
        lic_counts[verdict.risk_level] += 1
        if verdict.risk_level == "BLOCKED":
            blocked_files.append(f"{clip.filename} ({verdict.normalized})")

    if blocked_files and prof.ypp.forbid_nc_nd_licenses:
        checks.append(
            QcCheckItem(
                code="LICENSE_BLOCKED_NCND",
                category="ypp",
                severity="FAIL",
                message=f"Ditemukan klip lisensi terlarang (NC/ND): {', '.join(blocked_files[:3])}.",
            )
        )
    elif manifest.clips:
        checks.append(
            QcCheckItem(
                code="LICENSE_AUDIT",
                category="ypp",
                severity="PASS" if lic_counts["HIGH"] == 0 else "WARN",
                message=(
                    f"Lisensi klip: LOW={lic_counts['LOW']}, MEDIUM={lic_counts['MEDIUM']}, "
                    f"HIGH(ToS/transform)={lic_counts['HIGH']}, BLOCKED={lic_counts['BLOCKED']}."
                ),
            )
        )

    # 3. Clean usable clip availability (after excluding dirty clips)
    usable_clips = [
        c
        for c in manifest.clips
        if c.cleanliness_status not in ("BANYAK_OVERLAY", "KOTOR")
        and (c.cleanliness_score is None or c.cleanliness_score >= prof.ypp.min_cleanliness_score)
    ]
    if len(usable_clips) < 3:
        checks.append(
            QcCheckItem(
                code="USABLE_CLIPS_LOW",
                category="assets",
                severity="FAIL",
                message=f"Hanya {len(usable_clips)} klip bersih siap pakai (minimal 3 klip untuk render aman YPP).",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="USABLE_CLIPS_READY",
                category="assets",
                severity="PASS",
                message=f"{len(usable_clips)} klip bersih lulus filter kebersihan (>={prof.ypp.min_cleanliness_score}).",
            )
        )

    # 4. Cross-episode Creator Rotation check (7-day window)
    rotation = check_creator_rotation(episode_id, prof.channel_id, window_days=7, config=cfg)
    overlaps = rotation.get("cross_episode_overlaps_7d") or {}
    intra = rotation.get("intra_episode_repeats") or {}
    if overlaps:
        sample = [f"{cre} (juga di {', '.join(eps)})" for cre, eps in list(overlaps.items())[:3]]
        checks.append(
            QcCheckItem(
                code="CREATOR_ROTATION_7D",
                category="ypp",
                severity="WARN",
                message=f"Rotasi kreator 7 hari terlanggar: {'; '.join(sample)}.",
            )
        )
    elif intra:
        sample = [f"{cre} ({n}x)" for cre, n in list(intra.items())[:3]]
        checks.append(
            QcCheckItem(
                code="CREATOR_INTRA_REPEAT",
                category="ypp",
                severity="WARN",
                message=f"Kreator dipakai >1x di episode yang sama: {', '.join(sample)}.",
            )
        )
    elif rotation.get("unique_creators", 0) > 0:
        checks.append(
            QcCheckItem(
                code="CREATOR_ROTATION_OK",
                category="ypp",
                severity="PASS",
                message=f"Rotasi kreator bersih ({rotation['unique_creators']} kreator unik, 0 overlap 7 hari).",
            )
        )

    # 5. Technical AutoQC check (cached or live)
    autoqc_file = assets_dir / "autoqc_report.json"
    if run_autoqc and manifest.clips:
        autoqc_res = run_episode_autoqc(episode_id, prof.channel_id, cfg, write_report=True)
    elif autoqc_file.exists():
        try:
            from .qc_video import AutoQcResult

            autoqc_res = AutoQcResult.model_validate_json(autoqc_file.read_text(encoding="utf-8"))
        except Exception:
            autoqc_res = None
    else:
        autoqc_res = None

    if autoqc_res is not None:
        if autoqc_res.fail_count > 0:
            bad_names = [i.filename for i in autoqc_res.items if i.verdict == "FAIL"][:3]
            checks.append(
                QcCheckItem(
                    code="AUTOQC_VIDEO_FAIL",
                    category="assets",
                    severity="WARN",
                    message=f"AutoQC FFmpeg: {autoqc_res.fail_count}/{autoqc_res.total_clips} klip bermasalah ({', '.join(bad_names)}).",
                )
            )
        else:
            checks.append(
                QcCheckItem(
                    code="AUTOQC_VIDEO_OK",
                    category="assets",
                    severity="PASS",
                    message=f"AutoQC FFmpeg lulus ({autoqc_res.ok_count} OK, {autoqc_res.warn_count} WARN dari {autoqc_res.total_clips} klip).",
                )
            )

    # 6. Render output & transform verification (for READY_TO_PUBLISH)
    render_rep_path = ep_dir / "output" / f"render_report_{episode_id}.json"
    final_mp4 = ep_dir / "output" / f"final_{episode_id}.mp4"
    render_verified = False
    if final_mp4.exists() and render_rep_path.exists():
        try:
            rr = json.loads(render_rep_path.read_text(encoding="utf-8"))
            scenes_rr = rr.get("scenes") or []
            min_tf = prof.ypp.min_transformations_per_clip
            weak_scenes = [s["scene_index"] for s in scenes_rr if len(s.get("transforms") or []) < min_tf]
            if weak_scenes:
                checks.append(
                    QcCheckItem(
                        code="RENDER_TRANSFORMS_WEAK",
                        category="render",
                        severity="FAIL",
                        message=f"Scene {weak_scenes} memiliki <{min_tf} transformasi anti-reused.",
                    )
                )
            else:
                render_verified = True
                checks.append(
                    QcCheckItem(
                        code="RENDER_VERIFIED",
                        category="render",
                        severity="PASS",
                        message=f"Render v{rr.get('engine_version', '?')} terverifikasi ({rr.get('duration_s')}s, {len(scenes_rr)} adegan, >={min_tf} transform/klip).",
                    )
                )
        except Exception:
            pass
    else:
        checks.append(
            QcCheckItem(
                code="RENDER_PENDING",
                category="render",
                severity="WARN",
                message="Video final belum di-render oleh Editor Engine v3.",
            )
        )

    # 7. Check VO presence for render readiness
    has_vo = manifest.vo_file is not None and (assets_dir / manifest.vo_file).exists()
    if not has_vo:
        checks.append(
            QcCheckItem(
                code="VO_REQUIRED_FOR_RENDER",
                category="assets",
                severity="FAIL",
                message="File VO .mp3 belum ada di `assets/` — tidak bisa render.",
            )
        )

    fail_n = sum(1 for c in checks if c.severity == "FAIL")
    warn_n = sum(1 for c in checks if c.severity == "WARN")
    score = max(0, min(100, 100 - (fail_n * 22) - (warn_n * 6)))

    can_render = (fail_n == 0)
    can_publish = can_render and render_verified
    stage: Literal["READY_TO_PUBLISH", "READY_TO_RENDER", "BLOCKED"] = (
        "READY_TO_PUBLISH" if can_publish else ("READY_TO_RENDER" if can_render else "BLOCKED")
    )

    report = PreflightReport(
        channel_id=prof.channel_id,
        episode_id=episode_id,
        stage_verdict=stage,
        can_render=can_render,
        can_publish=can_publish,
        score=score,
        checks=checks,
        creator_rotation=rotation,
        license_summary=lic_counts,
    )

    if write_report and ep_dir.exists():
        (ep_dir / "preflight_report.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report
