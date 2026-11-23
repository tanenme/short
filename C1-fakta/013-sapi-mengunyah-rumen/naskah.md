# Naskah — 013 Kenapa Sapi Terus Mengunyah Padahal Gak Ada Rumput?

Status: siap-edit (doksli manual owner) | Slot: 19:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Sapi domestik (*Bos taurus*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa sapi terus mengunyah padahal tidak ada rumput?" -> **34** (The Question, SPEC=34, CUR=42)
2. "Pernah lihat sapi mengunyah santai di kandang yang kosong?" -> **36** (The Question, SPEC=34, CUR=42)
3. "Banyak orang bingung kenapa sapi mengunyah tanpa henti seharian." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Alasan sebenarnya kenapa sapi selalu mengunyah makanan dua kali." -> **34** (Unclassified, SPEC=34, CUR=24)
5. "Ternyata sapi tidak sedang mengunyah angin di kandangnya." -> **32** (Unclassified, SPEC=34, CUR=24)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa sapi terus MENGUNYAH padahal rumput di depannya habis?" -> **36** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu kira sapi ngunyah angin? Dia muntahkan rumput tadi pagi!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Kenapa sapi menghabiskan separuh harinya cuma buat mengunyah?" -> **34** (The Question, SPEC=34, STAKES=22, CUR=42)
9. "Satu lambung raksasa sapi ternyata menyimpan makanan berjam-jam." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)
10. "Stop kira sapi doyan ngemil! Ini rahasia dia mengunyah terus." -> **38** (The Mistake, SPEC=40, STAKES=48, CUR=24)

Keputusan: Pakai versi #6 ("Kenapa sapi terus MENGUNYAH padahal rumput di depannya habis?", skor 36 The Question, SPEC=40, CUR=42). Cadangan A/B: #10 (skor 38 The Mistake), #2 (skor 36), #4 (skor 34).

## VO final Eleven v4 (46 kata, estimasi 21,1 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa sapi terus MENGUNYAH padahal gak ada rumput?

[skeptical, then firm] Banyak yang kira sapi ngunyah angin, padahal BUKAN!

[warm, conversational] Tapi kalau kamu suka sapi, Subscribe ya.

[intrigued, hushed revelation] Sapi memuntahkan balik rumput dari lambung besarnya... [excited] Gumpalan makanan itu dikunyah ulang agar FERMENTASINYA sempurna!

[punchy, amused conclusion] Makanya sapi menghabiskan separuh harinya cuma mengunyah santai.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **46 kata** (standar 40–55 kata).
- Estimasi durasi: **21,1 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[8, 8, 7, 7, 8, 8]` kata (tanpa kalimat >10 kata, ada kalimat napas 8 kata).
- Intonasi: `2/6` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`MENGUNYAH`, `BUKAN`, `FERMENTASINYA`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[excited]`, `[punchy, amused conclusion]`.
- Elipsis (`...`): ada sebelum fakta muntahan makanan dikunyah ulang. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.2 dtk): Sapi di kandang bersih tanpa rumput di depannya, rahang mengunyah memutar santai.
- MITOS (2.2–4.5 dtk): Wajah sapi melongo sambil mengunyah + teks "NGUNYAH ANGIN? BUKAN!".
- CTA B (4.5–7.0 dtk): Anak sapi lucu menatap kamera + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–17.5 dtk): Gelombang kerongkongan sapi menaikkan bolus makanan (*regurgitasi*) -> close-up gigi geraham menggilas rumput halus -> ilustrasi bilik lambung rumen.
- PAYOFF (17.5–21.1 dtk): Sapi-sapi berbaring santai di padang rumput mengunyah tenang (tanpa CTA kedua).
