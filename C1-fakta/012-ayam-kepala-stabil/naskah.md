# Naskah — 012 Kenapa Kepala Ayam Diam Terkunci Walau Badannya Digoyang?

Status: siap-edit (doksli manual owner) | Slot: 12:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Ayam domestik (*Gallus gallus domesticus*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa kepala ayam diam walau badannya digoyang-goyang?" -> **34** (The Question, SPEC=34, CUR=42)
2. "Pernah coba goyang badan ayam tapi kepalanya tetap diam?" -> **36** (The Question, SPEC=34, CUR=42)
3. "Rahasia di balik kepala ayam yang tidak bergerak sama sekali." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Kenapa ayam punya kepala yang stabil persis seperti gimbal?" -> **36** (The Question, SPEC=34, CUR=42)
5. "Alasan ilmiah kenapa kepala ayam terkunci di satu titik." -> **34** (Unclassified, SPEC=34, CUR=24)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa kepala ayam diam TERKUNCI walau badannya digoyang?" -> **35** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu kira ayam sengaja kaku? Bola matanya gak bisa melirik!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Kenapa kepala ayam kampung stabil persis kayak gimbal kamera?" -> **36** (The Question, SPEC=34, STAKES=22, CUR=42)
9. "Satu rahasia leher ayam yang bikin kameramen seluruh dunia iri." -> **34** (Unclassified, SPEC=34, STAKES=22, CUR=24)
10. "Stop heran lihat ayam kaku! Ini alasan kepalanya diam mematung." -> **38** (The Mistake, SPEC=40, STAKES=48, CUR=24)

Keputusan: Pakai versi #6 ("Kenapa kepala ayam diam TERKUNCI walau badannya digoyang?", skor 35 The Question, SPEC=40, CUR=42). Cadangan A/B: #10 (skor 38 The Mistake), #4 (skor 36), #8 (skor 36).

## VO final Eleven v4 (47 kata, estimasi 21,5 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa kepala ayam diam TERKUNCI walau badannya digoyang?

[skeptical, then firm] Banyak orang pikir ayam sengaja kaku, tapi BUKAN!

[warm, conversational] Tapi kalau kamu suka ayam, Subscribe ya.

[intrigued, hushed revelation] Bola mata ayam nyaris gak bisa MELIRIK... [excited] Saraf lehernya otomatis mengunci kepala supaya pandangan gak ngeblur!

[punchy, amused conclusion] Makanya waktu jalan pun kepalanya persis gimbal KAMERA.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **47 kata** (standar 40–55 kata).
- Estimasi durasi: **21,5 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[8, 8, 7, 7, 9, 8]` kata (tanpa kalimat >10 kata, ada kalimat napas 8–9 kata).
- Intonasi: `2/6` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`TERKUNCI`, `BUKAN`, `MELIRIK`, `KAMERA`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[excited]`, `[punchy, amused conclusion]`.
- Elipsis (`...`): ada sebelum penjelasan saraf leher refleks. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.2 dtk): Orang memegang ayam dan menggoyangkan badannya ke kiri-kanan, kepala ayam mematung diam di udara.
- MITOS (2.2–4.5 dtk): Ayam tegak kaku di lantai tanah + teks "SENGAJA KAKU? BUKAN!".
- CTA B (4.5–7.0 dtk): Ayam kampung/anak ayam lucu menatap kamera + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–17.5 dtk): Close-up bola mata ayam yang bundar besar -> demonstrasi leher lentur memanjang/berputar -> diagram/overlay sensor keseimbangan (*vestibulocollic reflex*).
- PAYOFF (17.5–21.5 dtk): Ayam berjalan dengan gerakan kepala maju-diam (*head-bobbing*) persis seperti kamera stabilizer gimbal (tanpa CTA kedua).
