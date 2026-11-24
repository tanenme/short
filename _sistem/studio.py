#!/usr/bin/env python3
"""Enterprise YouTube Shorts Content Studio CLI.

Usage:
  python _sistem/studio.py doctor
  python _sistem/studio.py channels
  python _sistem/studio.py audit C1-fakta
  python _sistem/studio.py audit C1-fakta --episode 009-kucing-ek-cicak --write-manifest
  python _sistem/studio.py init-episode C1-fakta 014-bebek-berbaris --title "Kenapa Anak Bebek Berbaris?" --subject bebek
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Ensure _sistem is importable regardless of working directory
SISTEM_DIR = Path(__file__).resolve().parent
if str(SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SISTEM_DIR))

from core.config import get_config
from core.models import EpisodeManifest, EpisodeStatus
from core.validator import audit_episode_folder


def _mask(val: str) -> str:
    return "TERSET" if val else "BELUM DISET"


def cmd_doctor(_args: argparse.Namespace) -> int:
    cfg = get_config()
    print("=== STUDIO DOCTOR (Enterprise Environment Check) ===")
    print(f"Root Workspace : {cfg.root_dir}")
    print(f"Python         : {sys.version.split()[0]} ({sys.executable})")
    print(f"Env Sources    : {', '.join(cfg.env_sources) if cfg.env_sources else '(tidak ada .env)'}")
    print()
    print("[Binaries]")
    print(f"  ffmpeg       : {cfg.ffmpeg_bin or 'TIDAK DITEMUKAN'}")
    print(f"  ffprobe      : {cfg.ffprobe_bin or 'TIDAK DITEMUKAN'}")
    print(f"  yt-dlp       : {cfg.ytdlp_bin or 'TIDAK DITEMUKAN'}")
    capcut_ok = bool(cfg.capcut_drafts_dir and cfg.capcut_drafts_dir.exists())
    print(f"  CapCut Dir   : {cfg.capcut_drafts_dir} ({'ADA' if capcut_ok else 'TIDAK ADA'})")
    print()
    print("[Credentials & AI]")
    print(f"  EXA_API_KEY        : {_mask(cfg.exa_api_key)}")
    print(f"  PEXELS_API_KEY     : {_mask(cfg.pexels_api_key)}")
    print(f"  PIXABAY_API_KEY    : {_mask(cfg.pixabay_api_key)}")
    print(f"  AI_BASE_URL        : {cfg.ai_base_url}")
    print(f"  AI_API_KEY         : {_mask(cfg.ai_api_key)}")
    print(f"  ELEVENLABS_API_KEY : {_mask(cfg.elevenlabs_api_key)}")
    print()
    print(f"[Channels Loaded: {len(cfg.channels)}]")
    for cid, prof in cfg.channels.items():
        ch_dir = cfg.channel_dir(cid)
        bgm_dir = cfg.channel_bgm_dir(cid)
        sfx_dir = cfg.channel_sfx_dir(cid)
        print(
            f"  - {cid:<10} [{prof.status:<10}] dir={'OK' if ch_dir.exists() else 'MISSING'} "
            f"| bgm={bgm_dir.name}({'OK' if bgm_dir.exists() else '-'}) "
            f"| sfx={sfx_dir.name}({'OK' if sfx_dir.exists() else '-'})"
        )
    missing_core = [name for name, p in [("ffmpeg", cfg.ffmpeg_bin), ("ffprobe", cfg.ffprobe_bin)] if p is None]
    return 1 if missing_core else 0


def cmd_channels(_args: argparse.Namespace) -> int:
    cfg = get_config()
    if not cfg.channels:
        print("Belum ada channel profile di _sistem/channels/*.toml")
        return 1
    print(f"{'ID':<12} {'STATUS':<12} {'PACE (KATA)':<14} {'DURASI':<12} {'NAMA'}")
    print("-" * 80)
    for cid, p in cfg.channels.items():
        pace = f"{p.pacing.min_words}-{p.pacing.max_words}"
        dur = f"{p.pacing.min_duration_s:.0f}-{p.pacing.max_duration_s:.0f}s"
        print(f"{cid:<12} {p.status:<12} {pace:<14} {dur:<12} {p.display_name}")
    return 0


def _list_episodes(channel_dir: Path) -> list[str]:
    if not channel_dir.exists():
        return []
    eps = []
    for d in sorted(channel_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        # Skip audio bank folders
        if d.name.lower() in ("bgm", "sfx", "background sound", "backgorud sound", "effek sound", "effek sounf"):
            continue
        eps.append(d.name)
    return eps


def cmd_audit(args: argparse.Namespace) -> int:
    cfg = get_config()
    prof = cfg.get_channel(args.channel)
    ch_dir = cfg.channel_dir(prof.channel_id)

    targets = [args.episode] if args.episode else _list_episodes(ch_dir)
    if not targets:
        print(f"Tidak ada folder episode di {ch_dir}")
        return 0

    print(f"=== AUDIT CHANNEL: {prof.channel_id} ({len(targets)} episode) ===")
    print(f"{'EPISODE':<28} {'STATUS':<11} {'KATA':>5} {'KLIP':>5} {'SKOR':>5} {'GATE':<6} CATATAN UTAMA")
    print("-" * 108)

    all_passed = True
    for ep in targets:
        manifest, report = audit_episode_folder(ep, prof.channel_id, cfg)
        if not report.passed:
            all_passed = False
        issues = [c for c in report.checks if c.severity in ("FAIL", "WARN")]
        note = "; ".join(f"[{c.severity}] {c.code}" for c in issues[:3]) or "OK"
        gate = "PASS" if report.passed else "FAIL"
        print(
            f"{ep:<28} {manifest.status.value:<11} {manifest.script.word_count:>5} "
            f"{len(manifest.clips):>5} {report.score:>5} {gate:<6} {note}"
        )

        if args.episode:
            print()
            for c in report.checks:
                print(f"  [{c.severity:<4}] ({c.category:<9}) {c.code}: {c.message}")

        if args.write_manifest:
            out_path = cfg.episode_dir(ep, prof.channel_id) / "episode.json"
            out_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
            print(f"  -> manifest disimpan: {out_path}")

    return 0 if all_passed else 0


def cmd_init_episode(args: argparse.Namespace) -> int:
    cfg = get_config()
    prof = cfg.get_channel(args.channel)
    ep_dir = cfg.episode_dir(args.slug, prof.channel_id)
    if ep_dir.exists() and not args.force:
        print(f"Episode sudah ada: {ep_dir} (gunakan --force untuk menimpa manifest saja)")
        return 1

    (ep_dir / "assets").mkdir(parents=True, exist_ok=True)
    (ep_dir / "output").mkdir(parents=True, exist_ok=True)

    manifest = EpisodeManifest(
        episode_id=args.slug,
        channel_id=prof.channel_id,
        title=args.title,
        subject=args.subject or "",
        status=EpisodeStatus.IDEA,
    )
    (ep_dir / "episode.json").write_text(manifest.model_dump_json(indent=2), encoding="utf-8")

    cta_line = prof.cta.pattern.format(subject=args.subject or "[subjek]")
    naskah_p = ep_dir / "naskah.md"
    if not naskah_p.exists():
        naskah_p.write_text(
            f"# Naskah — {args.slug} {args.title}\n\n"
            f"Channel: {prof.channel_id} | Engine VO: {prof.voice.engine} | Target: {prof.pacing.min_words}-{prof.pacing.max_words} kata\n\n"
            f"## VO final Eleven v4\n\n"
            f"[curious, fast-paced] {args.title}\n\n"
            f"[skeptical, then firm] Banyak orang kira ..., padahal BUKAN!\n\n"
            f"[warm, conversational] {cta_line}\n\n"
            f"[intrigued, hushed revelation] ... [excited] ...\n\n"
            f"[punchy, amused conclusion] Makanya ...\n",
            encoding="utf-8",
        )

    brief_p = ep_dir / "brief-edit.md"
    if not brief_p.exists():
        brief_p.write_text(
            f"# Brief Edit — {args.slug} {args.title}\n\n"
            f"| # | Detik | Visual (klip + transformasi) | VO / Naskah | Teks layar | Musik / SFX |\n"
            f"|---|---|---|---|---|---|\n"
            f"| 1 | 0.0–2.0 | Hook visual | \"{args.title}\" | HOOK | whoosh |\n",
            encoding="utf-8",
        )

    sources_p = ep_dir / "sources.md"
    if not sources_p.exists():
        sources_p.write_text(
            f"# Sources — {args.slug}\n\n"
            f"## Sumber fakta tertulis (minimal {prof.ypp.min_tier1_fact_sources} sumber tier-1)\n"
            f"1. \n2. \n\n"
            f"## Daftar Klip Sumber\n"
            f"| Slot | Beat | Link sumber | Kreator | Timestamp | Lisensi | Transform-note |\n"
            f"|---|---|---|---|---|---|---|\n",
            encoding="utf-8",
        )

    upload_p = ep_dir / "paket-upload.txt"
    if not upload_p.exists():
        upload_p.write_text(
            f"PAKET UPLOAD — {args.slug}\n"
            f"JUDUL:\n1. {args.title} #shorts\n2. \n3. \n\n"
            f"DESKRIPSI:\n\n#shorts\n\nTAG:\n\nTHUMBNAIL / COVER FRAME:\n",
            encoding="utf-8",
        )

    print(f"Episode baru siap: {ep_dir}")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    engine_dir = SISTEM_DIR / "editor-engine"
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import builder

    if args.mode in ("capcut", "both"):
        draft = builder.build_capcut_project(args.episode, channel_id=args.channel)
        print(f"[+] Draft CapCut siap: {draft}")
    if args.mode in ("render", "both"):
        mp4 = builder.render_ffmpeg_direct(
            args.episode,
            channel_id=args.channel,
            enable_transitions=not args.no_transitions,
            enable_cta_overlay=not args.no_cta_overlay,
            make_qc_sheet=True,
            enforce_preflight=args.strict_gate,
            cta_style_override=getattr(args, "cta_style", None),
        )
        print(f"[+] Render MP4 v3.8 selesai: {mp4}")
    return 0


def cmd_preflight(args: argparse.Namespace) -> int:
    from sourcing.preflight import run_preflight_gate

    cfg = get_config()
    prof = cfg.get_channel(args.channel)
    ch_dir = cfg.channel_dir(prof.channel_id)
    targets = [args.episode] if args.episode else _list_episodes(ch_dir)

    print(f"=== YPP PRE-FLIGHT GATE: {prof.channel_id} ({len(targets)} episode) ===")
    print(f"{'EPISODE':<28} {'VERDICT':<17} {'SKOR':>5} {'CREATORS':>8}  RINGKASAN")
    print("-" * 108)

    for ep in targets:
        rep = run_preflight_gate(ep, prof.channel_id, run_autoqc=args.autoqc, config=cfg, write_report=args.write_report)
        issues = [c for c in rep.checks if c.severity in ("FAIL", "WARN")]
        summary = "; ".join(f"[{c.severity}] {c.code}" for c in issues[:3]) or "ALL CLEAR"
        u_cre = rep.creator_rotation.get("unique_creators", 0)
        print(f"{ep:<28} {rep.stage_verdict:<17} {rep.score:>5} {u_cre:>8}  {summary}")
        if args.episode:
            print()
            for c in rep.checks:
                print(f"  [{c.severity:<4}] ({c.category:<9}) {c.code}: {c.message}")
    return 0


def cmd_autoqc(args: argparse.Namespace) -> int:
    from sourcing.qc_video import run_episode_autoqc

    cfg = get_config()
    res = run_episode_autoqc(args.episode, args.channel, cfg, write_report=True)
    print(f"=== TECHNICAL AUTOQC: {res.channel_id}/{res.episode_id} ({res.total_clips} klip) ===")
    print(f"{'FILE':<44} {'DUR':>5} {'RESO':<11} {'BLUR':>5} {'YAVG':>6} {'YDIF':>6} {'VERDICT':<7} CATATAN")
    print("-" * 116)
    for it in res.items:
        notes = ", ".join(it.flags + it.warnings) or "OK"
        b_str = f"{it.blur_mean:.1f}" if it.blur_mean is not None else "-"
        print(
            f"{it.filename[:43]:<44} {it.duration_s:>4.1f}s {f'{it.width}x{it.height}':<11} "
            f"{b_str:>5} {it.yavg:>6.1f} {it.ydif:>6.2f} {it.verdict:<7} {notes}"
        )
    print(f"\nRingkasan: {res.ok_count} OK | {res.warn_count} WARN | {res.fail_count} FAIL")
    return 0


def cmd_credits(args: argparse.Namespace) -> int:
    from sourcing.licensing import generate_credits_markdown

    cfg = get_config()
    _, path = generate_credits_markdown(args.episode, args.channel, cfg, write_file=True)
    print(f"[+] CREDITS.md diperbarui: {path}")
    return 0


def cmd_source_search(args: argparse.Namespace) -> int:
    from sourcing.providers import search_unified

    cfg = get_config()
    provs = [p.strip() for p in args.providers.split(",") if p.strip()] if args.providers else None
    hits = search_unified(args.query, providers=provs, limit=args.limit, cc_only=args.cc_only, config=cfg)
    print(f"=== UNIFIED SOURCE SEARCH: '{args.query}' ({len(hits)} hasil) ===")
    print(f"{'#':>2} {'SKOR':>5} {'PLAT':<12} {'RISIKO':<8} {'LISENSI':<20} JUDUL")
    print("-" * 105)
    for i, c in enumerate(hits, 1):
        print(f"{i:>2} {c.score:>5.0f} {c.platform:<12} {c.risk_level:<8} {c.license[:19]:<20} {c.title[:48]}")
        print(f"   {c.url}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    from core.db import StudioDB

    cfg = get_config()
    db = StudioDB(config=cfg)
    targets = [args.channel] if args.channel != "all" else list(cfg.channels.keys())
    for cid in targets:
        eps = db.sync_channel(cid)
        pub = sum(1 for e in eps if e["preflight_verdict"] == "READY_TO_PUBLISH")
        ren = sum(1 for e in eps if e["preflight_verdict"] == "READY_TO_RENDER")
        blk = sum(1 for e in eps if e["preflight_verdict"] == "BLOCKED")
        print(f"[+] Synced {cid:<10} -> {len(eps)} episode (PUBLISH={pub}, RENDER={ren}, BLOCKED={blk}) -> {db.db_path}")
    return 0


def cmd_jobs(args: argparse.Namespace) -> int:
    from core.db import StudioDB

    db = StudioDB(config=get_config())
    jobs = db.list_jobs(limit=args.limit)
    if not jobs:
        print("Belum ada riwayat job di studio.db.")
        return 0
    print(f"{'JOB ID':<16} {'TYPE':<8} {'STATUS':<10} {'PROGRES':>7}  {'CHANNEL/EPISODE':<32} CATATAN")
    print("-" * 112)
    for j in jobs:
        target = f"{j['channel_id']}/{j['episode_id']}"
        note = j.get("error") or j.get("progress_msg") or ""
        pct = j.get("progress_pct", 100 if j["status"] == "succeeded" else 0)
        print(f"{j['job_id']:<16} {j['job_type']:<8} {j['status']:<10} {pct:>6}%  {target[:31]:<32} {note[:42]}")
    return 0


def cmd_tts(args: argparse.Namespace) -> int:
    from core.tts import generate_episode_voiceover, list_elevenlabs_voices

    cfg = get_config()
    if args.list_voices:
        voices = list_elevenlabs_voices(config=cfg)
        for v in voices:
            print(f"{v['voice_id']}  |  {v['name']} ({v.get('category', '-')})")
        return 0
    if not args.episode:
        print("Error: --episode wajib diisi kecuali menggunakan --list-voices.")
        return 1
    res = generate_episode_voiceover(args.episode, args.channel, voice_id_override=args.voice_id, config=cfg)
    print(f"[+] VO ElevenLabs siap: {res['mp3_path']} ({res['words_count']} kata ter-align)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    web_dir = SISTEM_DIR / "doksli-web"
    if str(web_dir) not in sys.path:
        sys.path.insert(0, str(web_dir))
    import uvicorn

    print(f"Menjalankan Enterprise Shorts Studio v3 di http://{args.host}:{args.port}")
    uvicorn.run(
        "server:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(SISTEM_DIR)] if args.reload else None,
        app_dir=str(web_dir),
    )
    return 0


def cmd_metrics(args: argparse.Namespace) -> int:
    from analytics.engine import compute_channel_analytics, generate_weekly_report, import_youtube_csv
    from core.db import StudioDB

    cfg = get_config()
    db = StudioDB(config=cfg)
    cid = cfg.get_channel(args.channel).channel_id

    if args.action == "log":
        if not args.episode:
            print("Error: --episode wajib diisi untuk metrics log.")
            return 1
        db.record_metrics(
            cid,
            args.episode,
            views=args.views,
            likes=args.likes,
            comments=args.comments,
            subs_gained=args.subs,
            avg_view_pct=args.apv,
            swipe_away_pct=args.swipe,
        )
        print(f"[+] Metrik {cid}/{args.episode} tersimpan (views={args.views}, subs=+{args.subs}, apv={args.apv}%, swipe={args.swipe}%).")
        return 0

    if args.action == "import-csv":
        if not args.csv:
            print("Error: --csv wajib diisi untuk metrics import-csv.")
            return 1
        res = import_youtube_csv(Path(args.csv), cid, db, cfg)
        print(f"[+] Imported {res['imported']} episode dari CSV (unmatched: {len(res['unmatched'])}).")
        return 0

    summary = compute_channel_analytics(cid, db, cfg)
    print(f"=== ANALYTICS FEEDBACK: {cid} [{summary.validation_verdict}] ===")
    print(
        f"Tracked: {summary.tracked_videos} | Views: {summary.total_views:,} | Subs: {summary.total_subs:,}/1000 ({summary.subs_progress_pct}%) "
        f"| Avg APV: {summary.avg_apv}% | Stay: {summary.avg_stay_to_watch_pct}%"
    )
    for d in summary.creative_directives:
        print(f"  -> {d}")
    if args.write:
        _, p = generate_weekly_report(cid, db, cfg, write_file=True)
        print(f"[+] Rapor mingguan disimpan: {p}")
    return 0


def cmd_clone_channel(args: argparse.Namespace) -> int:
    from analytics.scaler import provision_channel

    cfg = get_config()
    res = provision_channel(
        channel_id=args.channel,
        display_name=args.name or None,
        niche=args.niche or None,
        source_channel=args.source,
        copy_audio_bank=not args.no_audio,
        copy_overlays=not args.no_overlays,
        activate=args.activate,
        config=cfg,
    )
    print(
        f"[+] Channel {res['channel_id']} siap di {res['channel_dir']} "
        f"(aset bersama disalin: {res['copied_shared_assets']} file)."
    )
    return 0


def cmd_teardown_video(args: argparse.Namespace) -> int:
    from teardown.engine import teardown_video

    cfg = get_config()
    vtt = Path(args.vtt) if args.vtt else None
    rep = teardown_video(args.target, vtt_path=vtt, run_vision=not args.no_vision, config=cfg)
    print(f"=== FORENSIC VIDEO TEARDOWN: {rep.video_id} ===")
    print(f"Durasi: {rep.duration_s}s | Reso: {rep.width}x{rep.height} @{rep.fps}fps")
    print(
        f"Total Cut Editorial: {rep.total_cuts} (+ {rep.flash_transitions_count} flash/glitch) "
        f"| Avg Cut: {rep.avg_cut_duration_s}s | Median: {rep.median_cut_duration_s}s"
    )
    print(f"Pacing per Zona: Hook={rep.hook_cut_avg_s}s | CTA={rep.cta_cut_avg_s}s | Body={rep.body_cut_avg_s}s")
    print(
        f"Cadence VO: {rep.cadence.total_words} kata | Cold-Open={rep.cadence.cold_open_latency_s}s | "
        f"WPM={rep.cadence.overall_wpm} (Hook {rep.cadence.hook_wpm} WPM) | CTA={rep.cadence.cta_mechanic_type}"
    )
    if rep.cadence.verbal_signatures:
        print(f"Verbal Signatures: {', '.join(rep.cadence.verbal_signatures)}")
    print(
        f"Audio Forensics: {rep.audio.integrated_lufs} LUFS | LRA {rep.audio.loudness_range_lu} LU | "
        f"Peak {rep.audio.true_peak_dbfs} dBFS | Silence pockets: {rep.audio.silence_pockets_count}"
    )
    if rep.vision:
        print(f"Vision Analysis: mode={rep.vision.subtitle_mode} | y={rep.vision.subtitle_y_zone} | framing={rep.vision.framing_strategy}")
        print(f"CTA Visual Pattern: {rep.vision.cta_visual_pattern}")
        for ex in rep.vision.visual_anchor_examples[:3]:
            print(f"  * Visual Anchor: {ex}")
    print(f"Storyboard: {rep.storyboard_image}")
    print("\n--- Recommended ChannelProfile TOML Override ---")
    print(rep.recommended_toml_override)
    return 0


def cmd_teardown_channel(args: argparse.Namespace) -> int:
    from teardown.engine import teardown_channel

    cfg = get_config()
    rep = teardown_channel(args.target, limit=args.limit, config=cfg)
    print(f"=== FORENSIC CHANNEL OUTLIER TEARDOWN: @{rep.channel_handle} ({rep.videos_sampled} Shorts) ===")
    print(f"Median Views: {rep.median_views:,} | Mean Views: {rep.mean_views:,} | Max Views: {rep.max_views:,}")
    print(f"Winning Hook Patterns: {', '.join(rep.winning_patterns)}")
    print(f"\n{'MULT':>7} {'VIEWS':>11} {'PATTERN':<26} JUDUL")
    print("-" * 105)
    for o in rep.outliers[:10]:
        tag = "🔥" if o.is_viral_outlier else "  "
        print(f"{tag}{o.outlier_multiplier:>5.1f}x {o.views:>11,} {o.hook_pattern:<26} {o.title[:52]}")
    return 0


def cmd_sfx_download(args: argparse.Namespace) -> int:
    from sourcing.soundboard import build_soundboard_catalog, download_myinstants_sfx, search_myinstants

    cfg = get_config()
    if args.catalog:
        cat = build_soundboard_catalog(args.channel, cfg, write_file=True)
        auto_n = sum(1 for v in cat.values() if v["auto_use"])
        print(f"[+] Katalog soundboard diperbarui: {len(cat)} file ({auto_n} auto-aktif di engine) -> {cfg.channel_sfx_dir(args.channel) / '_soundboard_catalog.json'}")
        return 0
    if args.search_only:
        hits = search_myinstants(args.query, limit=args.limit)
        for i, h in enumerate(hits, 1):
            print(f"{i:>2}. {h['title']} -> {h['mp3_url']}")
        return 0
    res = download_myinstants_sfx(args.query, channel_id=args.channel, config=cfg)
    build_soundboard_catalog(args.channel, cfg, write_file=True)
    print(f"[+] Soundboard diunduh & dikalibrasi: {res['title']} -> {res['path']} ({res['bytes']} bytes)")
    return 0


def cmd_golden_moments(args: argparse.Namespace) -> int:
    engine_dir = SISTEM_DIR / "editor-engine"
    if str(engine_dir) not in sys.path:
        sys.path.insert(0, str(engine_dir))
    import builder
    from moment_finder import compute_scene_golden_moments

    cfg = get_config()
    ctx = builder._load_episode_context(args.episode, args.channel)
    moments = compute_scene_golden_moments(
        ctx["assets_dir"],
        ctx["scenes"],
        ctx["clip_mapping"],
        cfg=cfg,
        force_rescan=args.force,
        use_vision=args.vision,
    )
    print(f"\n=== GOLDEN MOMENTS v2.4 ({args.channel}/{args.episode}) [vision={args.vision}] ===")
    print(f"{'SC':>2} {'START':>6} {'SHIFT':>6} {'CROPX':>5} {'MODE':<21} {'VIS':>5} {'CLIP':<24} ALASAN")
    print("-" * 122)
    for idx in sorted(moments):
        m = moments[idx]
        vis = m.get("vision") or {}
        ref = m.get("refinement") or {}
        v_sc = f"{vis.get('semantic_score', '-')}/10" if vis else "-"
        sh = float(ref.get("shift_s", 0.0))
        sh_s = f"{sh:+.2f}s" if abs(sh) >= 0.01 else " 0.00s"
        cx_s = f"{float(m.get('crop_x_norm', 0.50)):.2f}"
        reason = vis.get("reason", "") if vis else f"shots={m.get('signals', {}).get('shot_count', 1)}"
        if m.get("rescued_from"):
            reason = f"[RESCUED] {reason}"
        print(
            f"{idx:>2} {m['start_s']:>5.2f}s {sh_s:>6} {cx_s:>5} {str(m.get('mode', ''))[:20]:<21} "
            f"{v_sc:>5} {str(m.get('filename', ''))[:23]:<24} {reason[:42]}"
        )
    print(f"\n[+] Disimpan ke: {ctx['assets_dir'] / 'golden_moments.json'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Enterprise YouTube Shorts Content Studio CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_doc = sub.add_parser("doctor", help="Cek kesehatan environment, binary, API key, dan channel")
    p_doc.set_defaults(func=cmd_doctor)

    p_ch = sub.add_parser("channels", help="Daftar semua channel profile yang terdaftar")
    p_ch.set_defaults(func=cmd_channels)

    p_aud = sub.add_parser("audit", help="Audit kepatuhan kreatif & YPP untuk episode di channel")
    p_aud.add_argument("channel", nargs="?", default="C1-fakta", help="ID channel (default: C1-fakta)")
    p_aud.add_argument("--episode", default="", help="Audit 1 episode spesifik dengan detail lengkap")
    p_aud.add_argument("--write-manifest", action="store_true", help="Tulis episode.json hasil audit ke folder episode")
    p_aud.set_defaults(func=cmd_audit)

    p_init = sub.add_parser("init-episode", help="Buat scaffold folder episode enterprise baru")
    p_init.add_argument("channel", help="ID channel (mis. C1-fakta)")
    p_init.add_argument("slug", help="Slug folder episode (mis. 014-bebek-berbaris)")
    p_init.add_argument("--title", required=True, help="Judul / pertanyaan hook utama")
    p_init.add_argument("--subject", default="", help="Subjek/hewan utama untuk template CTA")
    p_init.add_argument("--force", action="store_true", help="Timpa episode.json jika folder sudah ada")
    p_init.set_defaults(func=cmd_init_episode)

    p_ren = sub.add_parser("render", help="Render video Shorts v3 (FFmpeg / CapCut Draft) + QC contact sheet")
    p_ren.add_argument("channel", help="ID channel (mis. C1-fakta)")
    p_ren.add_argument("episode", help="Slug episode (mis. 009-kucing-ek-cicak)")
    p_ren.add_argument("--mode", default="render", choices=["render", "capcut", "both"])
    p_ren.add_argument("--no-transitions", action="store_true", help="Matikan xfade antar klip")
    p_ren.add_argument("--no-cta-overlay", action="store_true", help="Matikan CTA overlay")
    p_ren.add_argument("--cta-style", choices=["heart_pill", "gamified_321", "reward_pop"], default=None, help="Override gaya CTA (heart_pill, gamified_321, atau reward_pop untuk A/B test)")
    p_ren.add_argument("--strict-gate", action="store_true", help="Blokir render jika gagal YPP Pre-Flight Gate")
    p_ren.set_defaults(func=cmd_render)

    p_gm = sub.add_parser("golden-moments", help="Pindai Golden Moment v2 (4-signal FFmpeg + optional Multimodal Vision Reranker)")
    p_gm.add_argument("channel", help="ID channel (mis. C1-fakta)")
    p_gm.add_argument("episode", help="Slug episode (mis. 008-anjing-tendang-tanah)")
    p_gm.add_argument("--vision", action="store_true", help="Aktifkan Stage-2 Multimodal Vision Filmstrip Reranker")
    p_gm.add_argument("--force", action="store_true", help="Pindai ulang meski golden_moments.json sudah ada")
    p_gm.set_defaults(func=cmd_golden_moments)

    p_pf = sub.add_parser("preflight", help="Jalankan YPP Pre-Flight Safety Gate (script + lisensi + rotasi + render)")
    p_pf.add_argument("channel", nargs="?", default="C1-fakta")
    p_pf.add_argument("--episode", default="", help="Cek 1 episode spesifik dengan detail")
    p_pf.add_argument("--autoqc", action="store_true", help="Sekalian jalankan FFmpeg AutoQC pada klip")
    p_pf.add_argument("--write-report", action="store_true", help="Simpan preflight_report.json ke folder episode")
    p_pf.set_defaults(func=cmd_preflight)

    p_aq = sub.add_parser("autoqc", help="Jalankan FFmpeg Technical AutoQC (blur, gelap, freeze, resolusi)")
    p_aq.add_argument("channel", help="ID channel (mis. C1-fakta)")
    p_aq.add_argument("episode", help="Slug episode (mis. 009-kucing-ek-cicak)")
    p_aq.set_defaults(func=cmd_autoqc)

    p_cr = sub.add_parser("credits", help="Generate/perbarui CREDITS.md atribusi YouTube dari metadata aset")
    p_cr.add_argument("channel", help="ID channel")
    p_cr.add_argument("episode", help="Slug episode")
    p_cr.set_defaults(func=cmd_credits)

    p_ss = sub.add_parser("source-search", help="Cari klip terpadu (Wikimedia CC, iNaturalist, Pexels, YT, TikTok, Reddit)")
    p_ss.add_argument("query", help="Kata kunci pencarian (disarankan Inggris)")
    p_ss.add_argument("--providers", default="", help="Daftar provider dipisah koma")
    p_ss.add_argument("--limit", type=int, default=10)
    p_ss.add_argument("--cc-only", action="store_true", help="Hanya tampilkan materi CC/monetization-safe")
    p_ss.set_defaults(func=cmd_source_search)

    p_sy = sub.add_parser("sync", help="Sinkronkan seluruh episode disk ke database SQLite (_sistem/state/studio.db)")
    p_sy.add_argument("channel", nargs="?", default="all", help="ID channel atau 'all' (default: all)")
    p_sy.set_defaults(func=cmd_sync)

    p_jb = sub.add_parser("jobs", help="Lihat riwayat & status antrean background job di studio.db")
    p_jb.add_argument("--limit", type=int, default=20)
    p_jb.set_defaults(func=cmd_jobs)

    p_tts = sub.add_parser("tts", help="Generate VO ElevenLabs v4 + words_alignment.json otomatis")
    p_tts.add_argument("channel", nargs="?", default="C1-fakta")
    p_tts.add_argument("--episode", default="", help="Slug episode target")
    p_tts.add_argument("--voice-id", default="", help="Override Voice ID ElevenLabs")
    p_tts.add_argument("--list-voices", action="store_true", help="Tampilkan daftar voice akun ElevenLabs")
    p_tts.set_defaults(func=cmd_tts)

    p_sv = sub.add_parser("serve", help="Jalankan Enterprise Production Studio Dashboard (FastAPI + Web UI)")
    p_sv.add_argument("--host", default="127.0.0.1")
    p_sv.add_argument("--port", type=int, default=8000)
    p_sv.add_argument("--reload", action="store_true")
    p_sv.set_defaults(func=cmd_serve)

    p_mt = sub.add_parser("metrics", help="Catat metrik YouTube, impor CSV, atau buat rapor mingguan + Kill/Scale")
    p_mt.add_argument("action", choices=["report", "log", "import-csv"])
    p_mt.add_argument("channel", nargs="?", default="C1-fakta")
    p_mt.add_argument("--episode", default="")
    p_mt.add_argument("--views", type=int, default=0)
    p_mt.add_argument("--likes", type=int, default=0)
    p_mt.add_argument("--comments", type=int, default=0)
    p_mt.add_argument("--subs", type=int, default=0)
    p_mt.add_argument("--apv", type=float, default=0.0, help="Average Percentage Viewed (%%)")
    p_mt.add_argument("--swipe", type=float, default=0.0, help="Swiped Away (%%)")
    p_mt.add_argument("--csv", default="", help="Path file CSV ekspor YouTube Studio")
    p_mt.add_argument("--write", action="store_true", help="Tulis _rapor/RAPOR-YYYY-MM-DD.md")
    p_mt.set_defaults(func=cmd_metrics)

    p_cl = sub.add_parser("clone-channel", help="Siapkan / kloning folder & aset bersama untuk channel baru (C2/C3/...)")
    p_cl.add_argument("channel", help="ID channel target (mis. C2-momen)")
    p_cl.add_argument("--from", dest="source", default="C1-fakta", help="Channel sumber aset BGM/SFX/Overlay")
    p_cl.add_argument("--name", default="", help="Nama tampilan channel (jika channel baru)")
    p_cl.add_argument("--niche", default="", help="Slug niche (jika channel baru)")
    p_cl.add_argument("--no-audio", action="store_true")
    p_cl.add_argument("--no-overlays", action="store_true")
    p_cl.add_argument("--activate", action="store_true", help="Ubah status planned -> validation")
    p_cl.set_defaults(func=cmd_clone_channel)

    p_tv = sub.add_parser("teardown-video", help="Bedah forensik 8-dimensi video Shorts (lokal atau URL YouTube)")
    p_tv.add_argument("target", help="Path file video lokal atau URL YouTube Shorts")
    p_tv.add_argument("--vtt", default="", help="Path file .vtt opsional")
    p_tv.add_argument("--no-vision", action="store_true", help="Lewati panggilan Gemini Vision")
    p_tv.set_defaults(func=cmd_teardown_video)

    p_tc = sub.add_parser("teardown-channel", help="Bedah outlier & pola viral channel YouTube (@handle, URL, atau file .list)")
    p_tc.add_argument("target", help="@handle, URL channel YouTube, atau file .list")
    p_tc.add_argument("--limit", type=int, default=20)
    p_tc.set_defaults(func=cmd_teardown_channel)

    p_sfx = sub.add_parser("sfx-download", help="Cari, unduh, atau kalibrasi katalog soundboard SFX channel")
    p_sfx.add_argument("query", nargs="?", default="", help="Nama soundboard atau URL MP3 MyInstants (kosong = trending ID)")
    p_sfx.add_argument("--channel", default="C1-fakta")
    p_sfx.add_argument("--search-only", action="store_true")
    p_sfx.add_argument("--catalog", action="store_true", help="Scan ulang & kalibrasi _soundboard_catalog.json")
    p_sfx.add_argument("--limit", type=int, default=10)
    p_sfx.set_defaults(func=cmd_sfx_download)

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
