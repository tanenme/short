"""Automated Creative DNA & YPP Safety Gate Validator."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import List, Optional, Tuple

from .config import StudioConfig, get_config
from .models import (
    ChannelProfile,
    ClipAsset,
    EpisodeManifest,
    EpisodeStatus,
    FactSource,
    QcCheckItem,
    QcReport,
    SceneBeat,
    ScriptSpec,
    UploadPackage,
)

AUDIO_TAG_RE = re.compile(r"\[([^\[\]]+)\]")
URL_RE = re.compile(r"https?://[^\s\)\]>\"']+")


def extract_vo_section(naskah_md: str) -> str:
    """Extract the final VO block from a legacy naskah.md file."""
    m = re.search(
        r"##\s*(?:VO\s+final|Naskah\s+final)[^\n]*\n+([\s\S]*?)(?:\n##\s+|\Z)",
        naskah_md,
        re.IGNORECASE,
    )
    if m and m.group(1).strip():
        return m.group(1).strip()
    return naskah_md.strip()


def strip_audio_tags(text: str) -> str:
    return re.sub(r"\s+", " ", AUDIO_TAG_RE.sub(" ", text)).strip()


def extract_highlight_words(vo_text: str) -> List[str]:
    """Find intentional CAPS emphasis words (length >= 3) in the VO script."""
    clean = strip_audio_tags(vo_text)
    words = []
    for raw in re.findall(r"\b[A-Z]{3,}(?:-[A-Z]+)?\b", clean):
        if raw not in ("WIB", "CTA", "SFX", "BGM", "CCTV"):
            words.append(raw)
    return list(dict.fromkeys(words))


def analyze_script(vo_text: str, profile: ChannelProfile) -> Tuple[ScriptSpec, List[QcCheckItem]]:
    """Audit a VO script against the channel's creative DNA & ElevenLabs v4 rules."""
    checks: List[QcCheckItem] = []
    tags = [t.strip() for t in AUDIO_TAG_RE.findall(vo_text) if t.strip()]
    plain = strip_audio_tags(vo_text)
    words = [w for w in re.findall(r"[A-Za-z0-9À-ÿ]+(?:-[A-Za-z0-9À-ÿ]+)?", plain) if w]
    word_count = len(words)
    est_dur = round((word_count / max(1, profile.voice.median_wpm)) * 60.0, 2)
    highlights = extract_highlight_words(vo_text)

    sentences = [s.strip() for s in re.split(r"[.!?]+", plain) if s.strip()]
    hook_text = sentences[0] if sentences else ""

    spec = ScriptSpec(
        hook_text=hook_text,
        vo_full_text=vo_text,
        word_count=word_count,
        estimated_duration_s=est_dur,
        audio_tags=tags,
        highlight_words=highlights,
        cta_template=profile.cta.template_id,
    )

    # 1. Word count & pacing check
    if word_count == 0:
        checks.append(QcCheckItem(code="SCRIPT_EMPTY", category="script", severity="FAIL", message="Teks VO kosong."))
        return spec, checks

    p = profile.pacing
    if word_count > p.hard_max_words:
        checks.append(
            QcCheckItem(
                code="SCRIPT_TOO_LONG",
                category="script",
                severity="FAIL",
                message=f"Jumlah kata {word_count} melebihi batas keras {p.hard_max_words} kata (~{est_dur}s).",
            )
        )
    elif word_count < p.min_words or word_count > p.max_words:
        checks.append(
            QcCheckItem(
                code="SCRIPT_WORD_RANGE",
                category="script",
                severity="WARN",
                message=f"Jumlah kata {word_count} di luar sweet spot {p.min_words}-{p.max_words} kata (~{est_dur}s).",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="SCRIPT_WORD_RANGE",
                category="script",
                severity="PASS",
                message=f"Jumlah kata {word_count} kata (~{est_dur}s @ {profile.voice.median_wpm} kpm) masuk sweet spot.",
            )
        )

    # 2. ElevenLabs v4 audio tags
    if profile.voice.require_audio_tags:
        if len(tags) >= 3:
            checks.append(
                QcCheckItem(
                    code="AUDIO_TAGS",
                    category="script",
                    severity="PASS",
                    message=f"Audio tags Eleven v4 lengkap ({len(tags)} tag).",
                )
            )
        else:
            checks.append(
                QcCheckItem(
                    code="AUDIO_TAGS",
                    category="script",
                    severity="WARN",
                    message=f"Audio tags Eleven v4 minim ({len(tags)} tag; disarankan >=3 segmen emosi).",
                )
            )

    # 3. Digit check (ElevenLabs pronounces spelled-out numbers more reliably in ID)
    digits = re.findall(r"\b\d+\b", plain)
    if digits:
        checks.append(
            QcCheckItem(
                code="SCRIPT_DIGITS",
                category="script",
                severity="WARN",
                message=f"Ada angka digit {digits[:5]} di VO — eja jadi kata agar pelafalan ElevenLabs natural.",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="SCRIPT_DIGITS",
                category="script",
                severity="PASS",
                message="Tidak ada angka digit mentah di teks VO.",
            )
        )

    # 4. CTA placement & no second CTA at ending
    cta_hits = [i for i, s in enumerate(sentences) if "subscribe" in s.lower()]
    if not cta_hits:
        checks.append(
            QcCheckItem(
                code="CTA_MISSING",
                category="script",
                severity="WARN",
                message="Belum ada CTA Subscribe ringan di tengah naskah.",
            )
        )
    else:
        if profile.ypp.forbid_second_cta_at_end and len(cta_hits) > 1:
            checks.append(
                QcCheckItem(
                    code="CTA_DOUBLE",
                    category="script",
                    severity="FAIL",
                    message=f"Terdeteksi {len(cta_hits)} kali CTA Subscribe — dilarang ada CTA kedua.",
                )
            )
        elif profile.ypp.forbid_second_cta_at_end and cta_hits[-1] == len(sentences) - 1 and len(sentences) >= 3:
            checks.append(
                QcCheckItem(
                    code="CTA_AT_END",
                    category="script",
                    severity="FAIL",
                    message="CTA Subscribe berada di kalimat penutup; tutup video harus fakta/payoff murni.",
                )
            )
        else:
            checks.append(
                QcCheckItem(
                    code="CTA_PLACEMENT",
                    category="script",
                    severity="PASS",
                    message=f"CTA tunggal berada di posisi kalimat #{cta_hits[0] + 1}/{len(sentences)}.",
                )
            )

    # 5. Sentence breathability
    long_sents = [len(s.split()) for s in sentences if len(s.split()) > p.max_words_per_sentence]
    if long_sents:
        checks.append(
            QcCheckItem(
                code="SENTENCE_BREATH",
                category="script",
                severity="WARN",
                message=f"Ada {len(long_sents)} kalimat > {p.max_words_per_sentence} kata (maks {max(long_sents)} kata).",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="SENTENCE_BREATH",
                category="script",
                severity="PASS",
                message=f"Semua kalimat ringkas (<= {p.max_words_per_sentence} kata/kalimat).",
            )
        )

    return spec, checks


def parse_brief_scenes(brief_path: Path) -> List[SceneBeat]:
    """Parse shot-list table from brief-edit.md into structured SceneBeat list."""
    if not brief_path.exists():
        return []
    scenes: List[SceneBeat] = []
    for line in brief_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        parts = [p.strip() for p in line.split("|")[1:-1]]
        if not parts or not parts[0].isdigit():
            continue
        sec_str = parts[1].replace(",", ".")
        nums = re.findall(r"(\d+(?:\.\d+)?)", sec_str)
        if len(nums) < 2:
            continue
        vo_raw = parts[3] if len(parts) > 3 else ""
        vo_clean = strip_audio_tags(vo_raw).replace('"', "").strip()
        scenes.append(
            SceneBeat(
                index=int(parts[0]),
                brief_start_s=float(nums[0]),
                brief_end_s=float(nums[1]),
                visual_desc=parts[2] if len(parts) > 2 else "",
                vo_line=vo_clean,
                screen_text=parts[4] if len(parts) > 4 else "",
                sfx_cue=parts[5] if len(parts) > 5 else "",
                highlight_words=extract_highlight_words(vo_raw),
            )
        )
    return scenes


def extract_fact_sources(sources_md: Path, vonis_md: Path) -> List[FactSource]:
    urls: List[str] = []
    for p in (vonis_md, sources_md):
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore")
            for u in URL_RE.findall(txt):
                u_clean = u.rstrip(".,;)")
                if not any(
                    social in u_clean.lower()
                    for social in ("tiktok.com", "youtube.com", "youtu.be", "instagram.com", "pexels.com", "commons.wikimedia.org")
                ):
                    urls.append(u_clean)
    uniq = list(dict.fromkeys(urls))
    return [FactSource(title=f"Sumber #{i + 1}", url=u, tier="tier1") for i, u in enumerate(uniq)]


def audit_episode_folder(
    episode_id: str,
    channel_id: Optional[str] = None,
    config: Optional[StudioConfig] = None,
) -> Tuple[EpisodeManifest, QcReport]:
    """Run full enterprise audit on an existing episode directory and synthesize EpisodeManifest."""
    cfg = config or get_config()
    profile = cfg.get_channel(channel_id)
    ep_dir = cfg.episode_dir(episode_id, profile.channel_id)
    if not ep_dir.exists():
        raise FileNotFoundError(f"Folder episode tidak ditemukan: {ep_dir}")

    checks: List[QcCheckItem] = []

    # --- Structural check ---
    naskah_p = ep_dir / "naskah.md"
    brief_p = ep_dir / "brief-edit.md"
    sources_p = ep_dir / "sources.md"
    vonis_p = ep_dir / "vonis-risiko.md"
    upload_p = ep_dir / "paket-upload.txt"
    assets_dir = cfg.episode_assets_dir(episode_id, profile.channel_id)

    if assets_dir.name == "asset":
        checks.append(
            QcCheckItem(
                code="LEGACY_ASSET_DIR",
                category="structure",
                severity="WARN",
                message="Folder aset bernama `asset/` (legacy); standarisasi ke `assets/`.",
            )
        )

    missing_docs = [name for name, p in [("naskah.md", naskah_p), ("brief-edit.md", brief_p), ("sources.md", sources_p), ("paket-upload.txt", upload_p)] if not p.exists()]
    if missing_docs:
        checks.append(
            QcCheckItem(
                code="DOCS_MISSING",
                category="structure",
                severity="FAIL",
                message=f"File wajib belum ada: {', '.join(missing_docs)}.",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="DOCS_COMPLETE",
                category="structure",
                severity="PASS",
                message="Dokumen inti lengkap (naskah, brief-edit, sources, paket-upload).",
            )
        )

    # --- Script audit ---
    title = episode_id
    script_spec = ScriptSpec()
    if naskah_p.exists():
        raw_naskah = naskah_p.read_text(encoding="utf-8", errors="ignore")
        first_line = raw_naskah.splitlines()[0] if raw_naskah.splitlines() else episode_id
        title = first_line.replace("# Naskah —", "").replace("#", "").strip() or episode_id
        vo_text = extract_vo_section(raw_naskah)
        script_spec, script_checks = analyze_script(vo_text, profile)
        checks.extend(script_checks)

    # --- Fact-check audit ---
    fact_sources = extract_fact_sources(sources_p, vonis_p)
    if len(fact_sources) >= profile.ypp.min_tier1_fact_sources:
        checks.append(
            QcCheckItem(
                code="FACT_SOURCES",
                category="factcheck",
                severity="PASS",
                message=f"Tercatat {len(fact_sources)} URL sumber fakta tertulis (min {profile.ypp.min_tier1_fact_sources}).",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="FACT_SOURCES",
                category="factcheck",
                severity="WARN",
                message=f"Sumber fakta tertulis baru {len(fact_sources)} (disarankan >= {profile.ypp.min_tier1_fact_sources}).",
            )
        )

    # --- Scenes & shot pacing audit ---
    scenes = parse_brief_scenes(brief_p)
    if scenes:
        long_cuts = [s.index for s in scenes if (s.brief_end_s - s.brief_start_s) > profile.ypp.max_source_clip_s]
        if long_cuts:
            checks.append(
                QcCheckItem(
                    code="SCENE_MAX_DURATION",
                    category="ypp",
                    severity="FAIL",
                    message=f"Scene {long_cuts} melebihi batas anti-reused {profile.ypp.max_source_clip_s}s/klip.",
                )
            )
        else:
            checks.append(
                QcCheckItem(
                    code="SCENE_PACING",
                    category="ypp",
                    severity="PASS",
                    message=f"{len(scenes)} adegan di brief-edit, semua <= {profile.ypp.max_source_clip_s}s.",
                )
            )
    else:
        checks.append(
            QcCheckItem(
                code="BRIEF_SCENES_EMPTY",
                category="structure",
                severity="WARN",
                message="Tabel shot-list di brief-edit.md belum terbaca/kosong.",
            )
        )

    # --- Assets, VO, cleanliness & render audit ---
    clips: List[ClipAsset] = []
    vo_file_name: Optional[str] = None
    vo_candidates = list(assets_dir.glob("*.mp3")) if assets_dir.exists() else []
    # Also detect misplaced VO in episode root (seen in 011, 012, 013)
    root_vo_candidates = list(ep_dir.glob("*.mp3"))
    if vo_candidates:
        vo_file_name = max(vo_candidates, key=lambda f: f.stat().st_mtime).name
        checks.append(
            QcCheckItem(code="VO_READY", category="assets", severity="PASS", message=f"VO tersedia di assets: {vo_file_name}.")
        )
    elif root_vo_candidates:
        vo_file_name = max(root_vo_candidates, key=lambda f: f.stat().st_mtime).name
        checks.append(
            QcCheckItem(
                code="VO_MISPLACED",
                category="structure",
                severity="WARN",
                message=f"File VO `{vo_file_name}` ada di root episode, pindahkan ke `assets/` agar terdeteksi editor.",
            )
        )
    else:
        checks.append(
            QcCheckItem(code="VO_MISSING", category="assets", severity="WARN", message="File VO (.mp3) belum tersedia.")
        )

    clean_map = {}
    clean_file = assets_dir / "cleanliness_report.json"
    if clean_file.exists():
        try:
            for row in json.loads(clean_file.read_text(encoding="utf-8")):
                clean_map[row.get("filename", "")] = row
        except Exception:
            pass

    meta_map = {}
    meta_file = assets_dir / "_metadata.json"
    if meta_file.exists():
        try:
            meta_map = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    video_files = (
        sorted(list(assets_dir.glob("*.mp4")) + list(assets_dir.glob("*.webm")))
        if assets_dir.exists()
        else []
    )
    dirty_clips = []
    for vf in video_files:
        cm = clean_map.get(vf.name, {})
        mm = meta_map.get(vf.name, {})
        c_status = cm.get("status", "UNSCANNED")
        if c_status not in ("BERSIH", "OVERLAY_RINGAN", "BANYAK_OVERLAY", "KOTOR", "UNSCANNED"):
            c_status = "UNSCANNED"
        c_score = cm.get("score")
        if c_score is not None and c_score < profile.ypp.min_cleanliness_score:
            dirty_clips.append(vf.name)
        clips.append(
            ClipAsset(
                filename=vf.name,
                source_platform=str(mm.get("source") or "local"),
                source_url=str(mm.get("url") or ""),
                creator=str(mm.get("creator") or ""),
                license=str(mm.get("license") or "UNKNOWN"),
                reusable_safe=bool(mm.get("publikasi_aman", False)),
                cleanliness_status=c_status,
                cleanliness_score=c_score,
                subject_action=str(cm.get("subject_action") or ""),
                duration_s=mm.get("duration"),
                width=mm.get("width"),
                height=mm.get("height"),
                sha256=str(mm.get("sha256") or ""),
            )
        )

    if len(video_files) >= 5:
        checks.append(
            QcCheckItem(
                code="CLIPS_COUNT",
                category="assets",
                severity="PASS",
                message=f"Tersedia {len(video_files)} klip video di folder aset.",
            )
        )
    else:
        checks.append(
            QcCheckItem(
                code="CLIPS_COUNT",
                category="assets",
                severity="WARN",
                message=f"Baru ada {len(video_files)} klip video (disarankan 5-10 klip beragam).",
            )
        )

    if dirty_clips:
        checks.append(
            QcCheckItem(
                code="DIRTY_CLIPS_PRESENT",
                category="ypp",
                severity="WARN",
                message=f"{len(dirty_clips)} klip skor kebersihan < {profile.ypp.min_cleanliness_score}: {', '.join(dirty_clips[:3])}.",
            )
        )

    output_mp4 = ep_dir / "output" / f"final_{episode_id}.mp4"
    has_output = output_mp4.exists()

    # Determine lifecycle status
    if has_output:
        status = EpisodeStatus.EDITING
    elif vo_file_name and len(video_files) >= 5:
        status = EpisodeStatus.VO_READY
    elif len(video_files) > 0:
        status = EpisodeStatus.SOURCED
    elif naskah_p.exists():
        status = EpisodeStatus.SCRIPTED
    else:
        status = EpisodeStatus.IDEA

    # Compute overall QC score
    fail_n = sum(1 for c in checks if c.severity == "FAIL")
    warn_n = sum(1 for c in checks if c.severity == "WARN")
    score = max(0, min(100, 100 - (fail_n * 25) - (warn_n * 8)))
    report = QcReport(
        channel_id=profile.channel_id,
        episode_id=episode_id,
        passed=(fail_n == 0),
        score=score,
        checks=checks,
    )

    manifest = EpisodeManifest(
        episode_id=episode_id,
        channel_id=profile.channel_id,
        title=title,
        status=status,
        script=script_spec,
        fact_sources=fact_sources,
        scenes=scenes,
        clips=clips,
        vo_file=vo_file_name,
        upload=UploadPackage(),
        qc_report=report,
    )
    return manifest, report
