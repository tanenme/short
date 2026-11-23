# MESIN SHORTS — Blueprint v2.0 (niche hewan)
Target: YPP 30 hari (1000 subs via Shorts) + fondasi duplikasi 3 channel
Update: 2026-11-03 | Owner: ronyt | Orkestrator: sarah
Perubahan v2.0: niche C1 dikunci FAKTA HEWAN (keputusan owner 3 Nov). C2/C3 menyesuaikan.

## 1. Kunci Keputusan
- Channel validasi: 1 dulu, baru clone ke 3. Validasi = 30 hari, 90 video.
- Long music original untuk 4000 jam tayang = jalur terpisah (milik owner). Mesin ini fokus Shorts: subs + sinyal algoritma.
- Format: faceless + VO ElevenLabs Bahasa Indonesia.
- Source: TikTok, Reddit, X/Twitter, IG, sosmed lain. Campur 5-10 klip per Short.
- Edit: manual CapCut, kapasitas >6 jam/hari.
- Niche (keputusan owner 3 Nov — KUNCI: hewan):
  - C1 VALIDASI: FAKTA HEWAN MENGEJUTKAN ID. Sub-tema putar: anatomi aneh
    (jantung gurita, darah biru), rekor hewan (tercepat, terkuat, tertua),
    perilaku unik (merangkak vs berenang, tidur, berburu), bayi hewan,
    mitos-vs-fakta. Visual hewan = retensi alami + evergreen + aman YPP.
  - C2 CLONE: MOMEN HEWAN TEREKAM KAMERA (CCTV, aksi lucu, penyelamatan,
    predator vs mangsa). 5-7 klip + VO bridging.
  - C3 CLONE: MISTERI / HOROR HEWAN + CERITA REDDIT (penampakan, cryptid,
    kisah aneh). Loyal, bahan melimpah.

## 2. Peringatan YPP & Copyright (baca penuh)
Mixing 5-10 klip dalam 1 Short MENGURANGI deteksi Content ID otomatis, tetapi TIDAK menghapus risiko kebijakan reused content saat review YPP. Reviewer manusia menolak channel yang dinilai "kompilasi tanpa komentar original bermakna".
Aturan aman yang dipakai mesin ini:
1. Setiap Short WAJIB ada VO original (naskah sendiri, bukan reupload suara asli) minimal 70% durasi.
2. Setiap klip maksimal 3-5 detik, di-crop/zoom/flip, speed 1.05-1.15x, color-grade ringan, caption + SFX sendiri.
3. Struktur: hook VO 0-2 dtk + bridging komentar antar klip + CTA subs. Bukan tempel klip mentah.
4. Simpan bukti transformasi: link source + timestamp + lisensi + edit-note per klip di sources.md.
5. Musik long kompilasi original: pastikan rekaman/aranseemen benar-benar original atau lisensi jelas. Klaim Content ID pada musik adalah penyebab gagal monet paling umum.
6. Risiko strike tetap ada dari pemilik klip. Mitigasi: rotasi source, jangan ambil 2x dari kreator sama dalam 7 hari, hapus klip yang kena claim.

## 3. Matematika 30 Hari
- Target validasi: 1000 subs dalam 30 hari, 3 Shorts/hari = 90 video.
- Jadwal post WIB: 06:30, 12:30, 19:30.
- Formula: hook 0-2 dtk + 1 fakta/momen + bukti visual + CTA subs putar (3 varian).
- VO: ~130 kata per 60 dtk, ElevenLabs eleven_multilingual_v2, voice ID tetap per channel.
- Kapasitas: 3 video x ~1.5-2 jam = 4.5-6 jam/hari. Masih dalam 6 jam+.
- KPI harian: views/video, CTR feed, retention 0-3 dtk, subs/video.
- Kill/scale per 30 video: format dengan subs/video tertinggi digandakan, 2 terbawah dibuang. Butuh 1-2 video meledak (>500rb views) untuk 1000 subs.

## 4. Armada Bot Final (7 inti, 2 cadangan)
| Bot | Peran | Senjata (sudah ada, tinggal pasang) |
|---|---|---|
| @kepala-konten | Orkestrator tiket, jaga format per niche, approv final | kanban, delegate_task, message_agent |
| @trend-scout | Tren ID harian, skor view potensial | web_search, web_extract, youtube-mcp (BK927 / n8nfelipe), cron 07:00 |
| @script-doctor | Naskah ID + hook score + prompt ElevenLabs | Jakeschincariol/youtube-agent-skill (yt-script, hooks.json, hookscore.py), humanizer |
| @frida | Sourcer klip + link + timestamp + lisensi | yt-dlp-mcp (kevinwatt), Douyin_TikTok_Download_API (Evil0ctal, 21k stars), xurl, youtube-content |
| @risiko-filter | Fact-check + reused-risk gate, checklist transformasi | grounded-citations, web_search |
| @brief-packer | Paket siap-edit: naskah + VO mp3 + SRT + shot list + judul/deskripsi/tag | docx, xlsx, sergebulaev/youtube-skills (title, desc, hook, planner) |
| @analis-tumbuh | Baca analytics mingguan, format menang/kalah | xlsx, yt-retention.py, yt-viral/swipe.py |
| @thumb-maker (cadangan) | Cover frame Shorts + A/B log | claude-design |
| @repurpose-x (cadangan) | Long -> Shorts, adaptasi TikTok/Reels | youtube-content, gif-search |

Install nyata:
- git clone https://github.com/Jakeschincariol/youtube-agent-skill.git ke skills/
- git clone https://github.com/sergebulaev/youtube-skills.git ke skills/
- winget install yt-dlp ; npx -y @kevinwatt/yt-dlp-mcp (via hermes mcp add)
- Docker: Evil0ctal/Douyin_TikTok_Download_API (TikTok/Douyin/Bilibili no-watermark + MCP + STT)
- MCP trending: https://github.com/BK927/youtube-research-mcp atau https://github.com/n8nfelipe/youtube-mcp (butuh YOUTUBE_API_KEY untuk trending/search resmi)
- Referensi direktori: awesome-claude-video-skills (379 repo, grading keamanan), awesome-social-media-skills

## 5. Struktur Folder (satu channel = satu pohon, clone untuk C2/C3)
```
C:\Users\ronyt\konten\
  _sistem\ (SOP, template CapCut, voice.md, CTA-bank, checklist-risiko.md)
  C1-fakta\
    kalender-30hari.xlsx
    001-judul-kerja\ naskah.md + vo.mp3 + vo.srt + sources.md + assets\ + brief-edit.md + paket-upload.txt
    002-...\
  C2-momen\ (clone setelah validasi)
  C3-reddit\ (clone setelah validasi)
```
File per video: naskah.md (hook 5 varian + skor + VO final), vo.mp3 + vo.srt, sources.md (5-10 link + timestamp + transform-note), brief-edit.md (shot list detik-per-detik + SFX + caption style), paket-upload.txt (judul 3 varian + deskripsi + tag + jam post).

## 6. Alur Tiket (kanban)
Kolom: ide -> riset -> naskah -> sourcing -> risiko-pass -> vo-render -> siap-edit -> diedit-owner -> terjadwal -> posted -> dibaca-analis
- 1 tiket = 1 Short. WIP limit: riset 5, sourcing 3, siap-edit 6 (sesuai 6 jam edit).
- Serah terima via message_agent antar bot + group chat per channel.
- Cron: trend-scout 07:00 setor 5 ide, analis-tumbuh Senin 09:00 rapor mingguan.

## 7. SOP 1 Video (CapCut, ~90 menit)
1. Ambil brief-packer (15 mnt baca).
2. VO ElevenLabs render dulu, jadikan tulang timeline.
3. Potong 5-10 klip @frida, masing-masing 2-5 dtk, crop/zoom/flip, speed 1.1x.
4. Caption kata-per-kata + SFX whoosh/pop + zoom-punch tiap 2-3 dtk.
5. Cover frame 0.5 dtk + judul 4 kata.
6. Ekspor 1080x1920, 30fps, loudness -14 LUFS.
7. Upload via paket-upload.txt, pin komen CTA.

## 8. Roadmap
- H0-H2: install 2 skill pack + yt-dlp + MCP, tes hookscore + 1 download nyata, setup folder + kanban + voice.md.
- H3-H7: @frida + @script-doctor + @trend-scout jalan, produksi 3/hari mulai.
- H8-H30: gas 3/hari, rapor mingguan, iterasi format.
- H31: keputusan clone. Lolos jika >=1000 subs atau 1 video >500rb + subs/video >15. Clone = duplicate group + ganti niche memory + voice ID baru.

## 9. Uang (jujur)
Shorts ID RPM kecil ($0.02-0.07). YPP Shorts = tiket masuk, bukan mesin utama. Mesin uang = volume 3 channel x affiliate/produk digital + konversi ke long + jual jasa/shoutout setelah 10k subs. Target 12 bulan: 3 channel monet, 1 video long/minggu per channel untuk RPM besar.

## 10. Langkah Berikutnya (tunggu OK owner)
Tahap 1: install + tes nyata (hookscore, 1 download TikTok via yt-dlp, 1 transkrip via youtube-content). Lapor output real.


## Update 5 Nov — riset 21 video viral
- Template pertanyaan domestik wajib untuk C1 domestik.
- CTA varian B ("HP bagus") didukung; A/B test vs varian A.
- Pacing hewan: 1,3-1,5 detik/potong (10 klip untuk 18-22 detik).
- 30 hari pertama: fokus anjing, kucing, ayam, sapi, bebek (14/21 video teratas = domestik).
- Blueprint, voice.md, CTA-bank.md, template-brief-edit.md, skill shorts-id-pipeline, dan 3 SOUL bot sudah diperbarui.
