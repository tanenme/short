# Naskah 001 — Gurita Punya 3 Jantung & Darah Biru

Status: siap-edit | Hari: 1 | Slot: 1/3 | CTA: subs-1 + komen-1

## Skor hook (hookscore.py, dijalankan nyata 2026-10-30)

Catatan alat: hookscore.py terkalibrasi Bahasa Inggris (ADDRESS mendeteksi kata
"you", STAKES/CURIOSITY pola Inggris). Semua hook Indonesia skor 34–42 WEAK —
artinya skor absolut tidak valid untuk naskah ID. Yang masih berguna: ranking
relatif + SPECIFICITY (angka/detail) + formula match. Putusan final tetap
judgement manusia. Lesson tersimpan untuk script-doctor.

Batch 1 (asli):
1. "Gurita punya tiga jantung, dan dua di antaranya berhenti setiap kali dia berenang." → 34 WEAK
2. "Darah gurita itu biru. Bukan kiasan, darahnya benar-benar berwarna biru karena tembaga." → 34 WEAK
3. "Kenapa gurita lebih suka merangkak daripada berenang? Karena jantungnya berhenti saat dia berenang." → 34 WEAK
4. "Bayangin punya tiga jantung tapi tetap gampang capek. Ini cerita gurita dan darah birunya." → 34 WEAK
5. "Tiga jantung, darah biru dari tembaga. Hewan apa yang seaneh ini? Gurita." → 35 WEAK

Batch 2 (revisi, serang ADDRESS/STAKES):
6. "Kamu punya satu jantung. Gurita punya tiga, tapi dia tetap gampang capek saat berenang." → 34 WEAK
7. "Stop scroll 30 detik. Ini alasan gurita, hewan sepintar anjing, memilih merangkak daripada berenang." → **42 WEAK, formula The Mistake** ✅ PEMENANG
8. "Kalau darahmu biru dan jantungmu berhenti tiap berenang, kamu tidak akan selamat. Gurita bisa." → 34 WEAK
9. "Dua dari tiga jantung gurita mati setiap kali dia berenang. Kenapa evolusi membiarkan ini?" → 36 WEAK, formula The Question (cadangan)
10. "Kamu kira punya tiga jantung itu hebat? Bagi gurita, itu alasan dia tidak bisa berenang jauh." → 34 WEAK

Keputusan: pakai #7 (tertinggi + formula terdeteksi + perintah stop-scroll eksplisit).
Cadangan: #9 (pertanyaan terbuka, cocok untuk varian upload ulang).

## VO final (~95 kata, ±40 detik, ElevenLabs voice C1)

[HOOK] Stop scroll tiga puluh detik. Ini alasan gurita, hewan yang sangat pintar, memilih merangkak daripada berenang.

[ISI] Gurita punya tiga jantung. Dua memompa darah ke insang, satu menyebar ke seluruh tubuh. Darahnya biru, karena pakai tembaga, bukan zat besi seperti darahmu. Tapi tiap kali dia berenang, jantung utamanya melambat, bahkan berhenti sesaat. Tekanan airnya terlalu besar. Makanya dia gampang capek, dan lebih suka merangkak di dasar laut. Tiga jantung, tapi tetap tidak bisa berenang jauh tanpa cepat lelah.

[CTA] Baru tahu kan? Subscribe, tiap hari ada satu fakta yang bikin kamu mikir ulang.

## Arahan visual per segmen

- HOOK: teks besar "3 JANTUNG, TETAP CAPEK?" + footage gurita close-up mata.
- ISI kalimat 1-2: diagram sederhana 3 jantung (animasi panah aliran darah).
- ISI kalimat 3 (darah biru): footage gurita + overlay teks "DARAH BIRU = TEMBAGA".
- ISI kalimat 4-5: footage gurita berenang lalu cut ke merangkak (kontras cepat).
- CTA: footage gurita jalan ke kamera + tombol subscribe animasi.
