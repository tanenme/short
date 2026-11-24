# Enterprise YouTube Shorts Content Studio

Workspace ini adalah sistem pabrik konten YouTube Shorts multi-channel (`C1-fakta`, `C2-momen`, `C3-reddit`) yang menggabungkan **AI Creative Direction** (riset, penulisan naskah viral, kurasi visual, QC) dengan **Mesin Produksi Deterministik** (sourcing, licensing, editing FFmpeg/CapCut, dan validasi YPP).

## Struktur Arsitektur

```text
E:\YT\short\
├── CLAUDE.md                     # Panduan kerja & standar arsitektur (file ini)
├── SISTEM-BLUEPRINT.md           # Strategi bisnis, matematika 30 hari, & roadmap 3 channel
├── .env.example                  # Template secret & override lokal (salin ke .env, JANGAN commit)
├── .gitignore                    # Proteksi secret & file media biner berat
├── _sistem\
│   ├── studio.py                 # CLI utama: doctor, channels, audit, init-episode
│   ├── core\
│   │   ├── config.py             # Konfigurasi terpusat, .env loader, auto-discover ffmpeg/yt-dlp
│   │   ├── models.py             # Kontrak data Pydantic v2 (ChannelProfile, EpisodeManifest)
│   │   ├── validator.py          # Automated Creative DNA & YPP Safety Gate
│   │   ├── db.py                 # SQLite state store (_sistem/state/studio.db): episodes, jobs, metrics
│   │   └── tts.py                # ElevenLabs v4 API + native character-to-word alignment
│   ├── channels\
│   │   ├── C1-fakta.toml         # Profil validasi utama (Fakta Hewan ID)
│   │   ├── C2-momen.toml         # Profil clone (Momen Hewan Terekam Kamera)
│   │   └── C3-reddit.toml        # Profil clone (Misteri / Horor Hewan & Reddit)
│   ├── sourcing\                 # Unified sourcing, licensing, FFmpeg autoqc, YPP preflight gate
│   ├── analytics\                # Growth analytics, Kill/Scale 30-day engine, multi-channel scaler
│   ├── teardown\                 # Forensic 8-dimensi reverse-engineering video & channel referensi
│   ├── editor-engine\            # Engine render v3.6 (60fps, Intra-Unit VO-Keyword Split, Selective Speed-Up 1.0x Proof/CTA & 1.25x Process, Frame-0 blur, GM v2.4, Zero-Gap -32dB WAV Sync)
│   ├── doksli-web\               # FastAPI + Web UI kurasi Exa & Vision Pro
│   ├── tools\dokli.py            # CLI sourcing CC/Commons/iNaturalist/YT + autoqc + CREDITS.md
│   ├── tests\test_core.py        # Unit test fondasi core & validator
│   └── *.md / template-*         # SOP kreatif (`voice.md`, `CTA-bank.md`, `checklist-risiko.md`)
├── C1-fakta\                     # Paket produksi channel C1 (001..013, _ide-harian, _tiket, _rapor)
├── C2-momen\                     # Channel clone 2
├── C3-reddit\                    # Channel clone 3
└── riset\                        # Teardown kompetitor (@edukasikuy, @BrightsBrain, @PintarinAjaa)
```

## Perintah Penting

```powershell
# Cek kesehatan environment, binary (ffmpeg/ffprobe/yt-dlp), secret, & channel
python _sistem/studio.py doctor

# Lihat semua channel profile terdaftar
python _sistem/studio.py channels

# Audit kepatuhan kreatif & YPP seluruh episode di C1-fakta
python _sistem/studio.py audit C1-fakta

# Audit detail 1 episode dan tulis episode.json (Single Source of Truth)
python _sistem/studio.py audit C1-fakta --episode 009-kucing-ek-cicak --write-manifest

# Buat scaffold episode baru sesuai standar channel
python _sistem/studio.py init-episode C1-fakta 014-bebek-berbaris --title "Kenapa Anak Bebek Berbaris?" --subject bebek

# YPP Pre-Flight Safety Gate (script + lisensi + rotasi kreator 7 hari + render check)
python _sistem/studio.py preflight C1-fakta
python _sistem/studio.py preflight C1-fakta --episode 009-kucing-ek-cicak --autoqc

# Technical AutoQC FFmpeg (blur, gelap, freeze, resolusi) & Generate CREDITS.md
python _sistem/studio.py autoqc C1-fakta 009-kucing-ek-cicak
python _sistem/studio.py credits C1-fakta 009-kucing-ek-cicak

# Cari klip terpadu (Wikimedia CC, iNaturalist, Pexels, YouTube, TikTok, Reddit)
python _sistem/studio.py source-search "cat chattering" --cc-only --limit 8

# Render video Shorts v3.6 (FFmpeg cinematic + QC contact sheet / CapCut Draft)
python _sistem/studio.py render C1-fakta 009-kucing-ek-cicak --mode render --strict-gate

# Pindai Golden Moment v2.4 (Per-Shot Semantic Index + Synonym/Triplet Vision + Subject Crop_X + Peak-to-Word Lock)
python _sistem/studio.py golden-moments C1-fakta 008-anjing-tendang-tanah --vision --force

# Jalankan Enterprise Production Studio Dashboard (http://127.0.0.1:8000)
python _sistem/studio.py serve

# Sinkronkan seluruh channel ke SQLite state DB & cek antrean job
python _sistem/studio.py sync
python _sistem/studio.py jobs

# Generate VO ElevenLabs v4 + words_alignment.json otomatis
python _sistem/studio.py tts C1-fakta --episode 012-ayam-kepala-stabil

# Analytics Feedback Loop, Rapor Mingguan & Multi-Channel Scale
python _sistem/studio.py metrics log C1-fakta --episode 009-kucing-ek-cicak --views 15000 --subs 45 --apv 108 --swipe 22
python _sistem/studio.py metrics report C1-fakta --write
python _sistem/studio.py metrics import-csv C1-fakta path/to/youtube-studio.csv
python _sistem/studio.py clone-channel C2-momen --from C1-fakta --activate

# Forensic Reverse-Engineering Video & Channel Referensi (8 Dimensi)
python _sistem/studio.py teardown-channel @BrightsBrain --limit 20
python _sistem/studio.py teardown-video "https://www.youtube.com/shorts/VIDEO_ID"

# Jalankan unit test fondasi & editor
python -m unittest discover -s _sistem/tests -v
```

## Standar Non-Negotiable (Kualitas & Keamanan)

1. **Zero Hardcoded Secrets/Paths:** Semua API key (`EXA_API_KEY`, `AI_API_KEY`, `ELEVENLABS_API_KEY`) wajib dari `.env` / environment backend. Jangan pernah menanam key di HTML/JS atau kode Python.
2. **Konfigurasi Terpusat:** Semua skrip di `_sistem/` harus membaca path, binary (`ffmpeg`, `ffprobe`, `yt-dlp`), model AI, dan profil channel dari `core.config.get_config()`.
3. **DNA Kreatif C1 (`_sistem/voice.md`):**
   - Durasi sweet spot **19–25 detik** (**40–55 kata**, batas keras 60 kata) @ ~131 kpm.
   - Struktur: **Hook pertanyaan domestik (0–2s)** → **Mitos vs Kontras (2–4s)** → **CTA Template B tunggal (4–7s)** → **Mekanisme sebab-akibat (7–18s)** → **Payoff "Makanya..." (18–22s)**.
   - Wajib *audio tags* ElevenLabs v4 (`[curious, fast-paced]`, dst.), eja angka jadi kata, dan **dilarang ada CTA kedua di akhir video**.
4. **Gate YPP & Anti-Reused Content (`_sistem/checklist-risiko.md`):**
   - VO original >= 70% durasi video.
   - Setiap klip sumber <= 5 detik, kena >= 2 transformasi (crop/zoom, flip, speed/color grade, overlay/caption).
   - Minimal 2 sumber fakta ilmiah tier-1 per klaim utama.
   - Dilarang memakai klip berlisensi CC-NC / CC-ND atau klip kotor dengan UI media sosial/watermark berat.
   - Rotasi kreator sumber agar tidak mengambil berulang dari kreator yang sama.
5. **Standar Folder Episode Baru:** Wajib gunakan `assets/` (bukan `asset/`), simpan VO `.mp3` di dalam `assets/`, dan jaga `episode.json` sinkron dengan dokumen markdown.
