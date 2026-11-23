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
        if d.name.lower() in ("bgm", "sfx", "backgorud sound", "effek sounf"):
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
        )
        print(f"[+] Render MP4 v3 selesai: {mp4}")
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
    p_ren.add_argument("--no-cta-overlay", action="store_true", help="Matikan green-screen CTA overlay")
    p_ren.add_argument("--strict-gate", action="store_true", help="Blokir render jika gagal YPP Pre-Flight Gate")
    p_ren.set_defaults(func=cmd_render)

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

    return ap


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
