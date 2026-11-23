# Voice VO — C1 Fakta (ElevenLabs Eleven v4, Bahasa Indonesia)

## Profil suara
- **Engine:** ElevenLabs Eleven v4 (`eleven_v4`) — model paling ekspresif & responsif terhadap *audio tags* dan dinamika emosi.
- **Voice ID:** Tetap untuk semua video C1 (konsistensi identitas channel). Gunakan IVC/PVC suara narator manusia jernih atau preset Voice Design.
- **Gaya:** Narator dokumenter sains energik. Cepat, penasaran, cerdas, tidak datar/monoton.
- **Pace:** Khas Shorts. Target 19–25 detik ≈ 40–55 kata per naskah @110–150 kpm (median terukur 131 kpm, maksimum 60 kata).
- **Setting acuan v4:**
  - **Stability:** 35–45% (stabilitas lebih rendah memberi dinamika emosi lebih hidup & tidak robotik).
  - **Similarity:** 75–85% (menjaga karakter vokal konsisten).
  - **Clone Strength (khusus PVC):** 0–15% (smooth natural).
  - *Catatan:* Eleven v4 tidak mendukung slider Speed/Style dan tidak mendukung tag SSML `<break>`.

## Pengaturan "Nyawa" Suara di Eleven v4 (WAJIB tiap naskah)
Sutradarai pengucapan VO per segmen dengan *audio tags* dalam tanda kurung siku `[...]`:
1. **Hook (0–2 dtk):** `[curious, fast-paced]` — nada penasaran memancing telinga penonton berhenti scroll.
2. **Mitos vs Kontras (2–5 dtk):** `[skeptical, then firm]` — nada skeptis membantah mitos, tegas di kata kontras.
3. **CTA Dingin (5–8 dtk):** `[warm, conversational]` — nada hangat bersahabat tanpa kesan memohon/jualan.
4. **Mekanisme (8–18 dtk):** `[intrigued, hushed revelation]` lalu `[excited]` — membuka rahasia sains dengan antusias.
5. **Payoff Tutup (18–22 dtk):** `[punchy, amused conclusion]` — kesimpulan menggelitik/memuaskan.

## Aturan Intonasi & Teks Eleven v4
- **CAPS:** 1 kata kunci per kalimat untuk menaikkan volume & penekanan alami (jangan CAPS seluruh kalimat).
- **Tanda Seru (!):** Maksimal 1 kalimat seru per 3 kalimat (di klimaks & bantahan) agar AI menaikkan pitch tanpa teriak-teriak.
- **Elipsis (...):** Gunakan jeda 3 titik sebelum fakta mengejutkan untuk jeda dramatis mikro (pengganti tag break).
- **Pelafalan Khusus (IPA):** Bila ada istilah latin/ilmiah sulit, bungkus dengan garis miring IPA: `"/ˈfeɪlɪs ˈkætəs/"`.
- **Ejaan Angka:** Tulis huruf kata ("sembilan puluh lima liter", bukan "95 liter").

## Template CTA Standar (owner 6 Nov — WAJIB Template B)
Gunakan ajakan ringan 100% terkait hewan yang sedang dibahas:
- **Anjing:** `"Tapi kalau kamu suka anjing, Subscribe ya."`
- **Kucing:** `"Tapi kalau kamu suka kucing, Subscribe ya."`
- **Hewan lain:** `"Tapi kalau kamu suka [hewan], Subscribe ya."`
- **Posisi:** Detik 2–5 (setelah mitos/kontras, sebelum mekanisme).
- **Aturan:** Tutup video = fakta murni, DILARANG ada CTA kedua di akhir video.

## Formula Juara FYP — "Pola Kucing Ek-Ek" (Terbukti Viral 009)
Studi kasus `009-kucing-ek-cicak` membuktikan 5 elemen pemicu ledakan FYP & retensi >100%:
1. **Onomatopoeia + Hook Audio Nyata (Detik 0–1):** Tirukan bunyi asli dengan kata (*"ek-ek"*, *"guk"*, *"melet"*, *"kriuk"*), dan pasang snippet audio asli hewan tersebut 0,5 detik di bawah VO detik pertama. Telinga penonton terkunci sebelum mata sempat scroll.
2. **Objek Pemicu Hiper-Lokal Indonesia:** Gunakan pemicu yang ada di rumah 99% orang Indonesia (contoh: *"cicak di tembok"*, bukan *"tupai di jendela"*). Memicu validasi instan di kolom komentar: *"Kucingku persis kayak gini!"*.
3. **Bantahan Mitos Lucu & Absurd:** Angkat mitos yang sering dipikirkan pemilik hewan (*"ngajak ngobrol"*, *"lagi manggil cicak"*), lalu patahkan tegas: *"[skeptical] tapi BUKAN!"*.
4. **Durasi Super Ringkas (Sweet Spot 42–48 kata / 19–21 dtk):** Script 44 kata terbukti paling mematikan untuk metrik APV (*Average Percentage Viewed*) >100% dan looping mulus. Semakin padat, semakin cepat algoritma meledakkannya.
5. **CTA Template B Mulus (Detik 4–6):** `"Tapi kalau kamu suka [hewan], Subscribe ya."` — tidak menghentikan alur cerita dan tidak ada CTA kedua di akhir video.

## Struktur VO per video (STANDAR 19-25 DTK — 40–55 kata)
1. **[HOOK 0–2 dtk]** `[curious, fast-paced]` 1 kalimat pertanyaan domestik.
2. **[MITOS + KONTRAS 2–4 dtk]** `[skeptical, then firm]` 1 kalimat bantahan mitos.
3. **[CTA DINGIN 4–7 dtk]** `[warm, conversational]` Template B ringan terkait hewan.
4. **[MEKANISME 7–18 dtk]** `[intrigued]` -> `[excited]` 2–3 kalimat sebab-akibat nyambung (ada 1 kalimat napas 8–10 kata).
5. **[PAYOFF 18–22 dtk]** `[punchy conclusion]` 1 kalimat akibat konkret `"Makanya..."`.
