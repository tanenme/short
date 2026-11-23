# Naskah — 009 Kenapa Mulut Kucing Bunyi "Ek-Ek" Lihat Cicak?

Status: siap-edit (doksli manual owner) | Slot: 12:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Kucing domestik (*Felis catus*) & Cicak tembok (*Hemidactylus frenatus*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa mulut kucing getar bunyi ek-ek waktu lihat cicak?" -> **36** (The Question, SPEC=34, CUR=42)
2. "Pernah dengar kucingmu bunyi aneh saat menatap burung di jendela?" -> **36** (The Question, SPEC=34, CUR=42)
3. "Kucing bunyi ek-ek-ek ke cicak ternyata bukan lagi ngajak ngobrol." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Suara getar mulut kucing saat lihat mangsa punya arti tersembunyi." -> **34** (Unclassified, SPEC=34, CUR=24)
5. "Kenapa rahang kucing bergetar cepat tiap melihat cicak di tembok?" -> **36** (The Question, SPEC=34, CUR=42)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa mulut kucing bunyi ek-ek pas lihat CICAK di tembok?" -> **36** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu kira kucingmu ngajak ngobrol cicak? Rahangnya lagi latihan menerkam!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Kenapa kucingmu bergetar bunyi ek-ek tiap terhalang kaca dari burung?" -> **36** (The Question, SPEC=34, STAKES=22, CUR=42)
9. "Satu suara aneh kucing waktu lihat cicak ini masih bikin ilmuwan penasaran." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)
10. "Jangan salah sangka saat mulut kucingmu bunyi ek-ek menatap cicak." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)

Keputusan: Pakai versi padat #6 ("Kenapa mulut kucing bunyi ek-ek lihat CICAK?", skor 36 The Question, SPECIFICITY 40 + CURIOSITY 42) — lokalisasi Indonesia ("cicak" bukan tupai). Cadangan A/B: #1 (skor 36), #5 (skor 36), #8 (skor 36).

## VO final Eleven v4 (44 kata, estimasi 20,2 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa mulut kucing bunyi ek-ek lihat CICAK?

[skeptical, then firm] Banyak orang pikir ngajak ngobrol, tapi BUKAN!

[warm, conversational] Tapi kalau kamu suka kucing, Subscribe ya.

[intrigued, hushed revelation] Suara itu luapan ADRENALIN saat mangsanya terhalang tembok... [excited] Rahangnya refleks latihan MENERKAM sambil menghirup bau mangsa!

[punchy, amused conclusion] Makanya pas cicaknya kabur, getarannya langsung BERHENTI.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **44 kata** (standar 40–55 kata).
- Estimasi durasi: **20,2 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[7, 7, 7, 8, 8, 7]` kata (tanpa kalimat >10 kata, ada 2 kalimat napas 8 kata).
- Intonasi: `2/6` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`CICAK`, `BUKAN`, `ADRENALIN`, `MENERKAM`, `BERHENTI`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[excited]`, `[punchy, amused conclusion]`.
- Elipsis (`...`): ada sebelum lonjakan fakta menerkam. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.2 dtk): Extreme close-up wajah kucing mendongak, mulut sedikit terbuka bergetar (biarkan audio asli *chattering* tipis 0,5 dtk di bawah VO).
- MITOS (2.2–4.5 dtk): Cicak merayap di dinding tinggi + teks "NGAJAK NGOBROL? BUKAN!".
- CTA B (4.5–7.0 dtk): Kucing lucu menatap kamera + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–17.5 dtk): Pupil kucing membesar mengintai mangsa -> close-up rahang & kumis kucing menghirup udara ke organ Jacobson.
- PAYOFF (17.5–20.2 dtk): Cicak lari cepat -> kucing kembali duduk tenang menutup mulut (tanpa CTA kedua).
