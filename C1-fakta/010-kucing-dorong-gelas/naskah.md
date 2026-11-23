# Naskah — 010 Kenapa Kucing Hobi Dorong Gelas dari Pinggir Meja?

Status: siap-edit (doksli manual owner) | Slot: 19:30 WIB | Template: pertanyaan domestik | CTA: Template B
Spesies: Kucing domestik (*Felis catus*)
Engine VO: ElevenLabs Eleven v4 (`eleven_v4`) | Voice ID C1 tetap

## Skor hook (hookscore.py nyata — 2026-11-06)

Batch 1:
1. "Kenapa kucing hobi dorong gelas dari pinggir meja?" -> **34** (The Question, SPEC=34, CUR=42)
2. "Kucingmu suka sengaja menjatuhkan barang dari meja sambil menatapmu?" -> **36** (The Question, SPEC=34, CUR=42)
3. "Ternyata kucing menjatuhkan gelas dari meja bukan karena dendam." -> **34** (Unclassified, SPEC=34, CUR=24)
4. "Jangan teriak saat kucingmu mendorong barang jatuh dari atas meja." -> **34** (Unclassified, SPEC=34, CUR=24)
5. "Alasan sebenarnya kucing suka menjatuhkan benda satu per satu dari meja." -> **34** (Unclassified, SPEC=34, CUR=24)

Batch 2 (serang SPECIFICITY + CURIOSITY + STAKES):
6. "Kenapa kucing hobi dorong GELAS sampai jatuh dari pinggir meja?" -> **36** (The Question, SPEC=40, STAKES=22, CUR=42) ✅ PEMENANG FINAL
7. "Kamu sering teriak pas kucingmu jatuhkan gelas? Justru itu yang dia mau!" -> **34** (Unclassified, SPEC=40, STAKES=22, CUR=24)
8. "Satu sentuhan cakar kucing di gelasmu ternyata tes berburu plus cari perhatian." -> **34** (The Stack, SPEC=34, STAKES=22, CUR=24)
9. "Stop teriak ke kucingmu! Reaksi panikmu bikin dia makin rajin jatuhkan gelas." -> **38** (The Mistake, SPEC=40, STAKES=48, CUR=24)
10. "Kenapa kucing menatap matamu tepat saat mendorong gelas jatuh dari meja?" -> **36** (The Question, SPEC=34, STAKES=22, CUR=42)

Keputusan: Pakai versi padat #6 ("Kenapa kucing hobi dorong GELAS dari pinggir meja?", skor 36 The Question, SPECIFICITY 40 + CURIOSITY 42). Cadangan A/B: #9 (skor 38 The Mistake), #2 (skor 36), #10 (skor 36).

## VO final Eleven v4 (53 kata, estimasi 24,3 detik @131 kpm — siap paste)

[curious, fast-paced] Kenapa kucing hobi dorong GELAS dari pinggir meja?

[skeptical, then firm] Banyak yang kira kucing jahat, padahal BUKAN!

[warm, conversational] Tapi kalau kamu suka kucing, Subscribe ya.

[intrigued, hushed revelation] Cakar kucing mengetes apakah benda diam itu MANGSA... [urgent] Lalu dia sadar suara jatuh bikin kamu langsung LARI!

[amused] Reaksi panikmu justru dianggap hadiah perhatian. [punchy, confident conclusion] Makanya makin kamu teriak, dia makin RAJIN menjatuhkannya.

## Audit kepatuhan rules Eleven v4
- Jumlah kata: **53 kata** (standar 40–55 kata).
- Estimasi durasi: **24,3 detik** @131 kpm (range 19–25 dtk).
- Pacing kalimat: `[8, 7, 7, 8, 9, 6, 8]` kata (tanpa kalimat >10 kata, ada kalimat napas 8–9 kata).
- Intonasi: `2/7` tanda seru (`!`) di kalimat klimaks; CAPS 1 kata per kalimat (`GELAS`, `BUKAN`, `MANGSA`, `LARI`, `RAJIN`).
- Audio tags: `[curious, fast-paced]`, `[skeptical, then firm]`, `[warm, conversational]`, `[intrigued, hushed revelation]`, `[urgent]`, `[amused]`, `[punchy, confident conclusion]`.
- Elipsis (`...`): ada sebelum twist reaksi pemilik. Angka digit: 0.

## Arahan visual per segmen (CapCut)
- HOOK (0–2.3 dtk): Kucing di atas meja menatap gelas di tepi meja, cakar terulur pelan.
- MITOS (2.3–4.5 dtk): Wajah kucing polos tanpa rasa bersalah + teks "SENGAJA JAHAT? BUKAN!".
- CTA B (4.5–7.0 dtk): Kucing menatap kamera + tombol subscribe ringan SEKALI (detik 4.5–7.0).
- MEKANISME (7.0–19.0 dtk): Close-up cakar menyentuh benda -> kucing menoleh tajam mendengar reaksi pemilik -> pemilik mengelus/menghampiri kucing.
- PAYOFF (19.0–24.3 dtk): Kucing duduk santai di meja menatap bawah dengan puas (tanpa CTA kedua).
