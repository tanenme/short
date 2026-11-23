# Naskah — 011 Kenapa Bebek Tidur Berbaris Sambil Melek Sebelah Mata?

Status: siap-edit (doksli manual owner) | Slot: 06:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Bebek domestik (*Anas platyrhynchos domesticus*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa bebek suka tidur sambil melek sebelah mata?" -> **34** (The Question, SPEC=34, CUR=42)
2. "Pernah lihat bebek tidur tapi satu matanya tetap terbuka?" -> **36** (The Question, SPEC=34, CUR=42)
3. "Banyak orang salah sangka saat melihat bebek tidur melek." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Jangan kaget kalau melihat bebek tidur dengan mata terbuka." -> **34** (Unclassified, SPEC=34, CUR=24)
5. "Alasan unik kenapa bebek bisa tidur sambil membuka matanya." -> **34** (Unclassified, SPEC=34, CUR=24)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa bebek tidur berbaris sambil MELEK sebelah mata?" -> **35** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu kira bebek ini belum ngantuk? Separuh otaknya lagi tidur!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Kenapa bebek di pinggir barisan selalu melek sebelah mata?" -> **36** (The Question, SPEC=34, STAKES=22, CUR=42)
9. "Satu mata bebek yang terbuka saat tidur ternyata satpam hidup." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)
10. "Stop kira bebek insomnia! Ini alasan satu matanya tetap melek." -> **38** (The Mistake, SPEC=40, STAKES=48, CUR=24)

Keputusan: Pakai versi #6 ("Kenapa bebek tidur berbaris sambil MELEK sebelah mata?", skor 35 The Question, SPEC=40, CUR=42). Cadangan A/B: #10 (skor 38 The Mistake), #8 (skor 36 The Question), #2 (skor 36).

## VO final Eleven v4 (45 kata, estimasi 20,6 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa bebek suka tidur sambil MELEK sebelah mata?

[skeptical, then firm] Banyak orang pikir bebeknya insomnia, tapi SALAH!

[warm, conversational] Tapi kalau kamu suka bebek, Subscribe ya.

[intrigued, hushed revelation] Bebek bisa mematikan separuh otaknya saat tidur... [excited] Sementara satu mata luar tetap AWAS mengintai pemangsa!

[punchy, amused conclusion] Makanya bebek di ujung barisan jadi satpam kelompok.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **45 kata** (standar 40–55 kata).
- Estimasi durasi: **20,6 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[8, 7, 7, 7, 8, 8]` kata (tanpa kalimat >10 kata, ada kalimat napas 8 kata).
- Intonasi: `2/6` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`MELEK`, `SALAH`, `AWAS`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[excited]`, `[punchy, amused conclusion]`.
- Elipsis (`...`): ada sebelum fakta satu mata tetap awas. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.2 dtk): Bebek tidur meringkuk dalam barisan, satu mata terpejam dan satu mata membuka menatap tajam.
- MITOS (2.2–4.5 dtk): Barisan bebek tidur santai di rumput + teks silang merah "INSOMNIA? SALAH!".
- CTA B (4.5–7.0 dtk): Bebek/anak bebek lucu menatap kamera + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–17.5 dtk): Close-up mata bebek berkedip setengah sadar -> ilustrasi/grafis belahan otak (*unihemispheric sleep*) -> bebek di tepi luar barisan menatap ke arah luar.
- PAYOFF (17.5–20.6 dtk): Seluruh barisan bebek aman tertidur dengan bebek pinggir berjaga (tanpa CTA kedua).
