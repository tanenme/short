# Naskah — 008 Kenapa Anjing Nendang Tanah Habis Buang Air?

Status: siap-edit (doksli manual owner) | Slot: 06:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Anjing domestik (*Canis lupus familiaris*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa anjing selalu nendang tanah habis buang air?" -> **34** (The Question, SPEC=34, CUR=42)
2. "Anjingmu suka nendang rumput habis pup? Alasannya bukan biar bersih." -> **34** (Unclassified, SPEC=40, CUR=24)
3. "Banyak orang salah paham saat lihat anjing garuk tanah habis pipis." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Jangan marahi anjingmu kalau dia nendang tanah ke belakang setelah pup." -> **34** (Unclassified, SPEC=34, CUR=24)
5. "Ternyata anjing tidak sedang mengubur kotorannya saat menendang tanah." -> **34** (Unclassified, SPEC=34, CUR=24)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa anjing suka nendang TANAH ke belakang habis buang air?" -> **36** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu kira anjingmu nendang tanah buat ngubur kotoran? Justru kebalikannya!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Kenapa anjingmu garuk tanah habis pup? Rahasianya tersembunyi di telapak kakinya." -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
9. "Satu tendangan kaki anjing habis pup ternyata ninggalin pesan rahasia." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)
10. "Stop kira anjing bersih-bersih! Ini alasan dia nendang tanah habis pup." -> **38** (The Mistake, SPEC=40, STAKES=48, CUR=24)

Keputusan: Pakai versi padat #6 ("Kenapa anjing suka nendang TANAH habis buang air?", skor 36 The Question, SPECIFICITY 40 + CURIOSITY 42). Cadangan A/B: #10 (skor 38 The Mistake), #7 (skor 34), #8 (skor 34).

## VO final Eleven v4 (46 kata, estimasi 21,1 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa anjing suka nendang TANAH habis buang air?

[skeptical, then firm] Banyak orang pikir mau ngubur kotoran, tapi SALAH!

[warm, conversational] Tapi kalau kamu suka anjing, Subscribe ya.

[intrigued, hushed revelation] Di telapak kakinya ada kelenjar bau FEROMON... [excited] Tendangan itu menyebar tanda WILAYAH yang awet ke udara!

[punchy, amused conclusion] Makanya makin ramai taman, tendangannya makin HEBOH.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **46 kata** (standar 40–55 kata).
- Estimasi durasi: **21,1 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[8, 8, 7, 7, 9, 7]` kata (tanpa kalimat >10 kata, ada kalimat napas 8–9 kata).
- Intonasi: `2/6` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`TANAH`, `SALAH`, `FEROMON`, `WILAYAH`, `HEBOH`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[excited]`, `[punchy, amused conclusion]`.
- Elipsis (`...`): ada sebelum lonjakan fakta feromon. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.2 dtk): Close-up kaki belakang anjing menendang rumput/tanah berhamburan, zoom-punch 110%.
- MITOS (2.2–4.5 dtk): Anjing mengendus rumput di taman, teks silang merah "NGUBUR KOTORAN? SALAH!".
- CTA B (4.5–7.0 dtk): Anjing menatap kamera ramah + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–17.5 dtk): Makro bantalan telapak kaki anjing (*paw pads*) -> anjing lain mengendus tanah bekas garukan di taman.
- PAYOFF (17.5–21.1 dtk): Beberapa anjing di taman terbuka, langkah gagah menjauh (tanpa CTA kedua).
