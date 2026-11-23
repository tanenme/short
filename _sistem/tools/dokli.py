#!/usr/bin/env python3
"""
dokli.py — Pencari & pengunduh doksli untuk mesin YouTube Shorts.

Tiga sumber tanpa API key + satu dengan client bypass:
  1. Wikimedia Commons  -> video bebas lisensi (CC/PD), metadata lengkap
  2. Archive.org        -> film/footage arkib, metadata lisensi
  3. Openverse         -> gambar/foto, fallback thumbnail
  4. YouTube via yt-dlp -> client "android" (metadata) + "default,android" (download)
  5. TikTok             -> butuh cookies.txt, opsional

Fungsi:
  search   : cari kandidat doksli dengan metadata kaya, skor relevansi
  fetch    : unduh kandidat teratas + probe ffprobe + tulis sources.md
  probe    : metadata video lokal

Contoh:
  python doksli.py search --topic "kukang bangka" --shots hook,detail,fakta --limit 12
  python doksli.py fetch --topic "kukang bangka" --out assets/ --limit 6 --shots hook,detail,fakta
"""
from __future__ import annotations
import argparse, hashlib, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.parse
from pathlib import Path

_SISTEM_DIR = Path(__file__).resolve().parent.parent
if str(_SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(_SISTEM_DIR))
try:
    from core.config import get_config as _get_studio_config
    _STUDIO_CFG = _get_studio_config()
    YTDLP = _STUDIO_CFG.ytdlp_cmd()
except Exception:
    YTDLP = shutil.which("yt-dlp") or "yt-dlp"
UA = "HermesDoksli/1.0 (youtube shorts sourcing pipeline; contact: owner)"
# Wikimedia menolak UA non-browser (robot policy) -> pakai UA browser untuk unduhan Commons
UA_WEB = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"

# ---------------------------------------------------------------- scoring
STOP = set("""a an and are as at be by for from has have how in is it its of on or that the to was what when where which who will with
video videos watch youtube short shorts clip clips footage hd 4k official new best top""".split())

def tokens(s: str) -> list[str]:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return [w for w in s.split() if w and w not in STOP and len(w) > 2]

def score(topic: str, *texts: str) -> float:
    """Skor relevansi 0-100. Topic multi-kata: bobot frasa utuh > token tunggal."""
    tset = set(tokens(topic))
    blob = " ".join(t.lower() for t in texts if t)
    bset = set(tokens(blob))
    if not tset:
        return 0.0
    hit = len(tset & bset)
    single = hit / len(tset) * 70
    phrase = 0.0
    tl = topic.lower().strip()
    if tl and tl in blob.lower():
        phrase = 30.0
    else:
        for n in (3, 2):
            parts = tl.split()
            for i in range(len(parts) - n + 1):
                if " ".join(parts[i:i + n]) in blob.lower():
                    phrase += 15
                    break
            else:
                continue
            break
    return min(100.0, single + phrase)

def fit_bonus(c: dict) -> float:
    """Bonus kecocokan durasi/resolusi/view untuk footage Shorts."""
    b = 0.0
    dur = c.get("dur") or 0
    try: dur = float(dur)
    except Exception: dur = 0
    if dur:
        # 8-120 detik = sumber paling berguna (bisa dipotong 10 klip)
        if 8 <= dur <= 120: b += 12
        elif 120 < dur <= 300: b += 6
        elif dur < 5: b -= 10
    h = c.get("h") or 0
    w = c.get("w") or 0
    if h and h >= 1080: b += 8
    elif h and h >= 720: b += 5
    elif h and h < 480: b -= 5
    if h and w and h > w: b += 10          # vertikal = hook-friendly
    if c.get("vertical_flag"): b += 6
    try:
        v = int(c.get("views") or 0)
        if v >= 100000: b += 8
        elif v >= 20000: b += 5
        elif v and v < 1000: b -= 5
    except Exception:
        pass
    return b


def license_risk(lic: str) -> tuple[str, str]:
    """Return (label, risk) — NC/ND = risiko reused-content tinggi."""
    l = (lic or "").lower()
    if not l or l in ("na", "unknown", "none"):
        return ("TIDAK DIKETAHUI", "TINGGI")
    if "publicdomain" in l or "public domain" in l or "cc0" in l or l.strip() == "pd":
        return (lic, "SANGAT RENDAH")
    if "by-nc" in l:
        return (lic, "TINGGI (NC: non-komersial)")
    if "by-nd" in l:
        return (lic, "TINGGI (ND: tanpa turunan)")
    if "by-sa" in l:
        return (lic, "SEDANG (SA: share-alike)")
    if "by" in l:
        return (lic, "RENDAH (CC BY: wajib atribusi)")
    return (lic, "PERLU CEK")


def license_tier(lic: str) -> int:
    """Skor keamanan lisensi untuk reuse + monetisasi (makin tinggi makin aman).
    0 = paling berisiko. Dipakai untuk mengurutkan kandidat."""
    l = (lic or "").lower()
    if not l or l in ("na", "unknown", "none"):
        return 0
    if "publicdomain" in l or "public domain" in l or "cc0" in l:
        return 5
    if "by-nc" in l or "by-nd" in l:
        return 1
    if "by-sa" in l:
        return 3
    if "by" in l:
        return 4
    return 2

def ffprobe(path: str) -> dict:
    if not shutil.which("ffprobe"):
        return {}
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_format", "-show_streams", path],
            capture_output=True, text=True, timeout=60).stdout
        d = json.loads(out)
        v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
        a = next((s for s in d.get("streams", []) if s.get("codec_type") == "audio"), {})
        fr = v.get("avg_frame_rate", "0/0")
        try:
            num, den = fr.split("/")
            fps = round(float(num) / float(den), 2) if float(den) else 0
        except Exception:
            fps = 0
        return {"dur": round(float(d.get("format", {}).get("duration", 0) or 0), 2),
                "bytes": int(d.get("format", {}).get("size", 0) or 0),
                "w": v.get("width", 0), "h": v.get("height", 0), "fps": fps,
                "vcodec": v.get("codec_name", ""), "acodec": a.get("codec_name", ""),
                "vertical": bool(v.get("height", 0) and v.get("width", 0) and v["height"] > v["width"])}
    except Exception:
        return {}

# ---------------------------------------------------------------- sources
_CACHE_DIR = Path(os.environ.get("TMPDIR") or tempfile.gettempdir()) / "dokli_cache"
_LAST_CALL = {"t": 0.0}


def _cache_path(url: str) -> Path:
    h = hashlib.sha1(url.encode()).hexdigest()[:16]
    host = urllib.parse.urlparse(url).netloc.replace(":", "_")
    return _CACHE_DIR / f"{host}_{h}.json"


def http_json(url: str, timeout: int = 30, cache_ttl: int = 3600) -> dict:
    """HTTP JSON dengan throttle, cache disk, dan retry saat rate-limit (429)."""
    cp = _cache_path(url)
    try:
        if cp.exists() and (time.time() - cp.stat().st_mtime) < cache_ttl:
            return json.loads(cp.read_text(encoding="utf-8"))
    except Exception:
        pass
    # throttle: jeda minimal antar request ke host yang sama
    wait = 0.7 - (time.time() - _LAST_CALL["t"])
    if wait > 0:
        time.sleep(wait)
    for attempt in range(4):
        try:
            r = subprocess.run(["curl", "-s", "-m", str(timeout), "-A", UA,
                                "-w", "\n__HTTP__%{http_code}", url],
                               capture_output=True, text=True, timeout=timeout + 10)
            _LAST_CALL["t"] = time.time()
            body = r.stdout.rsplit("__HTTP__", 1)
            code = body[1].strip() if len(body) > 1 else "000"
            if code == "429":
                time.sleep(3 * (attempt + 1))
                continue
            data = json.loads(body[0])
            try:
                _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps(data), encoding="utf-8")
            except Exception:
                pass
            return data
        except Exception:
            time.sleep(1.5 * (attempt + 1))
    return {}

TAKSON = {
    "kukang": "Nycticebus", "slow loris": "Nycticebus", "nycticebus": "Nycticebus",
    "kuda laut": "Hippocampus", "seahorse": "Hippocampus",
    "axolotl": "Ambystoma", "gurita": "Octopus", "octopus": "Octopus",
    "paus biru": "Balaenoptera", "belalang sembah": "Idolomorpha",
    "tardigrade": "Tardigrada", "berang-berang": "Lutra",
    "kelelawar": "Chiroptera", "sapi": "Bos taurus", "kucing": "Felis silvestris catus",
    "anjing": "Canis lupus familiaris", "ayam": "Gallus gallus domesticus",
    "bebek": "Anas platyrhynchos domesticus", "cicak": "Hemidactylus frenatus",
    "orangutan": "Pongo", "elang": "Accipitridae", "kuda": "Equus",
    "hiu": "Selachimorpha", "burung": "Aves",
}

# Istilah pencarian berbahasa Inggris per topik Indonesia.
# PENTING: "Hippocampus" juga nama bagian otak, jadi kuda laut WAJIB pakai "seahorse".
EN_TERMS = {
    "kuda laut": ["seahorse", "seahorses"],
    "seahorse": ["seahorse", "seahorses"],
    "kukang": ["slow loris", "nycticebus"],
    "slow loris": ["slow loris", "nycticebus"],
    "axolotl": ["axolotl", "ambystoma"],
    "gurita": ["octopus", "octopus vulgaris"],
    "paus biru": ["blue whale", "balaenoptera musculus"],
    "belalang sembah": ["mantis shrimp", "stomatopod"],
    "tardigrade": ["tardigrade", "water bear"],
    "berang-berang": ["otter", "lutra"],
    "kelelawar": ["bat", "chiroptera"],
    "sapi": ["cattle", "cow", "bos taurus"],
    "kucing": ["cat", "cats", "felis catus"],
    "anjing": ["dog", "dogs", "canis lupus familiaris"],
    "ayam": ["chicken", "rooster", "gallus gallus"],
    "bebek": ["duck", "mallard", "anas platyrhynchos"],
    "cicak": ["house gecko", "gecko", "hemidactylus"],
    "orangutan": ["orangutan", "pongo"],
    "elang": ["eagle", "hawk", "raptor"],
    "kuda": ["horse", "equus"],
    "hiu": ["shark", "selachimorpha"],
    "burung": ["bird", "aves"],
    "ikan": ["fish"],
}


def en_terms(topic: str) -> list[str]:
    """Terjemahkan topik Indonesia ke istilah pencarian Inggris.

    Match per KATA UTUH (bukan substring) dan frasa terpanjang didahulukan,
    supaya 'kuda laut' tidak memicu 'horse'/Equus.
    """
    tl = (topic or "").lower()
    words = set(tokens(tl))
    out: list[str] = []
    consumed: set[str] = set()
    for key in sorted((k for k in EN_TERMS if " " in k), key=len, reverse=True):
        parts = key.split()
        if all(w in words for w in parts):
            out.extend(EN_TERMS[key])
            consumed.update(parts)
    for key in sorted((k for k in EN_TERMS if " " not in k), key=len, reverse=True):
        if key in words and key not in consumed:
            out.extend(EN_TERMS[key])
    for w in tokens(tl):
        if w in ("seahorse", "seahorses", "loris", "octopus", "axolotl", "tardigrade",
                 "whale", "shark", "otter", "bat", "cat", "dog", "horse", "eagle", "bird"):
            out.append(w)
    return list(dict.fromkeys(out))



def taxon_for(topic: str) -> list[str]:
    """Takson relevan dari topik, match per kata utuh (frasa terpanjang dulu).
    Mencegah 'kuda laut' ikut memicu takson 'kuda' (Equus)."""
    tl = (topic or "").lower()
    words = set(tokens(tl))
    out: list[str] = []
    consumed: set[str] = set()
    for key in sorted((k for k in TAKSON if " " in k), key=len, reverse=True):
        parts = key.split()
        if all(w in words for w in parts):
            out.append(TAKSON[key])
            consumed.update(parts)
    for key in sorted((k for k in TAKSON if " " not in k), key=len, reverse=True):
        if key in words and key not in consumed:
            out.append(TAKSON[key])
    return list(dict.fromkeys(out))

def _commons_cats(taxon: str) -> list[str]:
    """Kumpulkan kategori Commons relevan untuk satu taksom (1-2 tingkat)."""
    cats = [taxon]
    for sub in ("Videos of " + taxon, taxon + " videos"):
        cats.append(sub)
    return cats


def _commons_species_cats(taxon: str, limit: int = 40) -> list[str]:
    """Kategori spesies di bawah satu genus (mis. Nycticebus -> Nycticebus coucang)."""
    subs = [m for m in _commons_members(taxon, limit)
            if m.startswith("Category:") and m.lower().endswith(taxon.lower())]
    return [s.replace("Category:", "") for s in subs]

def _commons_members(cat: str, limit: int = 60) -> list[str]:
    u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
         "&list=categorymembers&cmtitle=" + urllib.parse.quote("Category:" + cat) +
         f"&cmtype=file|subcat&cmlimit={limit}")
    d = http_json(u)
    return [m["title"] for m in d.get("query", {}).get("categorymembers", [])]


def _commons_tree(cat: str, limit: int = 60, depth: int = 2) -> list[str]:
    """File media dalam kategori + subkategori (rekursif).

    Kategori taksom di Commons sering cuma berisi subkategori
    (mis. "Nycticebus" -> "Nycticebus coucang"), jadi perlu turun 1-2 tingkat.
    """
    out, seen_cat, queue = [], {cat.lower()}, [(cat, 0)]
    while queue:
        c, lvl = queue.pop(0)
        if len(out) >= limit * 3:
            break
        for m in _commons_members(c, limit):
            title = m.get("title", "") if isinstance(m, dict) else m
            if title.lower().endswith((".webm", ".ogv", ".mp4", ".mov", ".m4v",
                                       ".jpg", ".jpeg", ".png")):
                out.append(title)
            elif lvl < depth and title not in out:
                sub = title.replace("Category:", "")
                if sub.lower() not in seen_cat:
                    seen_cat.add(sub.lower())
                    queue.append((sub, lvl + 1))
    # unik,zza
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    return uniq


def commons_search(terms: list[str], filt: str = "filetype:video", limit: int = 30) -> list[str]:
    """Search Commons per term (filter tipe file di tiap query).

    PENTING: "a OR b filetype:video" menghasilkan 0 hasil di CirrusSearch —
    operator OR tidak bisa digabung dengan filter tipe file. Jadi satu request
    per term lalu digabung.
    """
    terms = [x for x in dict.fromkeys(x for x in terms if x)]
    if not terms:
        return []
    out, seen = [], set()
    for x in terms[:4]:
        q = f'"{x}" {filt}'.strip()
        u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
             f"&list=search&srsearch={urllib.parse.quote(q)}"
             f"&srnamespace=6&srlimit={max(10, limit // 2)}")
        d = http_json(u)
        for s in d.get("query", {}).get("search", []):
            ttl = s.get("title", "")
            if ttl and ttl not in seen:
                seen.add(ttl)
                out.append(ttl)
    return out


def relevance_terms(topic: str) -> list[str]:
    """Istilah yang WAJIB muncul agar kandidat dianggap relevan (judul/deskripsi)."""
    tl = (topic or "").lower()
    terms = set(en_terms(topic))
    terms |= set(taxon_for(topic))
    for w in tokens(tl):
        if len(w) > 3:
            terms.add(w)
    return sorted({x.lower() for x in terms if x})


OTHER_ANIMALS = [
    "nettle", "jellyfish", "anemone", "coral", "sponge", "starfish", "urchin",
    "crab", "lobster", "shrimp", "prawn", "seal", "dolphin", "whale", "shark",
    "octopus", "squid", "turtle", "dolphins", "penguin", "panda", "tiger",
    "lion", "elephant", "monkey", "gibbon", "orangutan", "flamingo", "heron",
    "horse", "horses", "wild horse", "mule", "donkey", "stallion", "mare",
    "cavalry", "carousel", "carnival", "bull", "goat", "sheep", "deer",
]

# video/berkas akademik, medis, atau non-hewan yang lolos kata kunci TAKSON
ACADEMIC_NOISE = (
    "potentiation", "astrocyte", "gabaergic", "tetanus", "immunomodulat",
    "expression of", "gene expression", "rna", "protein", "polym",
    "insecticide", "polymer clay", "pest control", "pathogen", "bacteri",
    "virus", "vaccine", "cell line", "in vitro", "knockout", "mice model",
    "learning and memory", "place-learning", "place learning", "behavioral",
    "network-gabaergic", "network gabaergic", "layer-specific", "layer specific",
    "fornix", "calcium imaging", "two-photon", "in vivo imaging", "whole-brain",
    "hippocampal formation", "temporal lobe", "brain", "demonstration by dr",
    "specimen collection", "dry skin", "floriculture", "harvest", "yield",
)


# spesies yang namanya mirip tapi beda hewan -> jangan ikut terambil
CONFUSABLES = {
    "kukang": ("kungkang", "kuskus", "colugo"),
    "slow loris": ("kungkang", "kuskus", "colugo", "tarsier", "cuscus"),
    "kuda laut": ("kuda", "equus", "seahorse fish", "pipefish"),
    "axolotl": ("axolot", "salamander", "newt"),
}


def _has_word(hay: str, needle: str) -> bool:
    """Cocok kata utuh (bukan substring) — 'kukang' tidak boleh match 'kungkang'."""
    return re.search(r"(?<![a-z])" + re.escape(needle.lower()) + r"(?![a-z])", hay.lower()) is not None


def is_confusable(topic: str, title: str, desc: str) -> bool:
    """True kalau materi menyebut hewan mirip nama tapi spesies lain.

    Contoh: topik 'kukang/slow loris' tapi materi 'kungkang' (kuskus/colugo).
    """
    blob = ((title or "") + " " + (desc or "")[:250]).lower()
    tl = (topic or "").lower()
    for key, bad in CONFUSABLES.items():
        if not _has_word(tl, key):
            continue
        if any(_has_word(blob, b) for b in bad):
            return True
    return False


def wrong_species(title: str, desc: str, terms: list[str]) -> bool:
    """True kalau judul menyebut hewan lain yang BUKAN topik kita (mis. 'Sea Nettle'
    untuk topik kuda laut). Judul yang menyebut topik kita tetap diterima."""
    low = (title or "").lower()
    blob = (title or "").lower() + " " + (desc or "").lower()[:200]
    if any(a in low for a in ACADEMIC_NOISE):
        return True
    if any(tm in low for tm in terms):
        return False
    if any(a in blob for a in OTHER_ANIMALS):
        return True
    return False


def is_relevant(topic: str, title: str, desc: str, creator: str = "") -> bool:
    """True kalau minimal satu istilah inti muncul di judul ATAU deskripsi."""
    terms = relevance_terms(topic)
    blob = " ".join([title or "", desc or "", creator or ""]).lower()
    return any(tm in blob for tm in terms)



def _commons_meta(em: dict) -> dict:
    """Ekstrak metadata lisensi lengkap dari extmetadata Commons."""
    g = lambda k: (em.get(k, {}) or {}).get("value", "")
    clean = lambda s: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", s or "")).strip()
    lic_short = clean(g("LicenseShortName")) or "NA"
    lic_code = clean(g("License"))
    usage = clean(g("UsageTerms"))
    lic_url = clean(g("LicenseUrl"))
    artist = clean(g("Artist"))
    credit = clean(g("Credit"))
    desc = clean(g("ImageDescription"))
    date = clean(g("DateTimeOriginal"))[:10]
    obj = clean(g("ObjectName"))
    cats = [clean(c) for c in re.findall(r"<[^>]+>", g("Categories") or "")][:10]
    cats = [re.sub(r"^Category:", "", c).strip() for c in cats]
    # URL lisensi standar jika Commons tidak memberi
    if not lic_url:
        m = re.search(r"creativecommons\.org/licenses/([a-z-]+)/([\d.]+)", (lic_short + " " + lic_code).lower())
        if m:
            lic_url = f"https://creativecommons.org/licenses/{m.group(1)}/{m.group(2)}/"
    attribution_needed = bool(re.search(r"by", lic_short.lower()))
    return {"license": lic_short, "license_code": lic_code, "license_url": lic_url,
            "usage_terms": usage, "creator": artist or "NA", "credit": credit,
            "attribution": credit or artist or "NA", "description": desc,
            "date": date, "object_name": obj, "categories": cats,
            "attribution_required": attribution_needed,
            "license_risk": license_risk(lic_short)[1]}


def commons_video(topic: str, limit: int = 20) -> list[dict]:
    """Wikimedia Commons via kategori taksom + pencarian video, filter noise."""
    tl = topic.lower()
    taxons = taxon_for(topic)
    # fallback: kata topic yang panjang
    if not taxons:
        taxons = [w.capitalize() for w in tokens(topic)[:1]]

    titles: list[str] = []
    for tx in dict.fromkeys(taxons):
        cats = _commons_cats(tx) + _commons_species_cats(tx)
        for cat in dict.fromkeys(cats):
            for m in _commons_tree(cat, 60):
                if m.lower().endswith((".webm", ".ogv", ".mp4", ".mov", ".m4v")):
                    titles.append(m)
    # pencarian tambahan: video — satu request OR multi-term (hemat rate-limit)
    search_terms = set(en_terms(topic))
    search_terms |= {topic, " ".join(tokens(topic)[:2])}
    search_terms |= {tx for tx in taxons}
    for filt in ("filetype:video", "filemime:video/webm"):
        titles.extend(commons_search(sorted(search_terms), filt, limit))

    # uniq + filter noise (video non-hewan)
    seen, keep = set(), []
    for t_ in titles:
        if t_ in seen:
            continue
        seen.add(t_)
        low = t_.lower()
        if any(b in low for b in ("loris francesco", "kereta", "gandhi", "capovilla",
                                   "suzan-lori", "godwari", "temperature", "heat map",
                                   "anthem", "speech", "interview")):
            continue
        keep.append(t_)
    titles = keep[:40]
    if not titles:
        return []

    res = []
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        tq = urllib.parse.quote("|".join(chunk))
        u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
             "&prop=imageinfo&iiprop=url|size|mime|extmetadata|mediatype&titles=" + tq)
        d = http_json(u)
        for p in d.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            if not ii.get("url"):
                continue
            cm = _commons_meta(ii.get("extmetadata", {}) or {})
            lic = cm["license"]
            desc = cm["description"]
            artist = cm["creator"]
            name = p.get("title", "").replace("File:", "")
            if not name.lower().endswith((".webm", ".ogv", ".mp4", ".mov", ".m4v")):
                continue
            if is_confusable(topic, name, desc):
                continue
            if not is_relevant(topic, name, desc, artist):
                continue
            if wrong_species(name, desc, relevance_terms(topic)):
                continue
            # relevansi: keyword HARUS muncul di judul (bukan cuma deskripsi)
            s_title = score(topic, name)
            s_desc = score(topic, desc, artist) * 0.5
            res.append({
                "source": "Wikimedia Commons", "title": name,
                "url": ii["url"].split("?")[0], "creator": artist or "NA",
                "license": lic, "license_risk": cm["license_risk"],
                "license_url": cm["license_url"], "usage_terms": cm["usage_terms"],
                "attribution": cm["attribution"], "credit": cm["credit"],
                "attribution_required": cm["attribution_required"],
                "upload_date": cm["date"], "object_name": cm["object_name"],
                "categories": cm["categories"],
                "dur": ii.get("duration", 0), "w": ii.get("width"), "h": ii.get("height"),
                "desc": desc.strip()[:300],
                "description": desc.strip()[:600],
                "page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(p.get('title',''))}",
                "media": "video",
                "score": min(100.0, s_title + s_desc + cc_bonus(lic)),
            })
    return res


def commons_photos(topic: str, limit: int = 20) -> list[dict]:
    """Foto (still) berlisensi CC dari Wikimedia Commons. Dipakai sebagai sumber
    visual kedua: foto bagus -> video vertikal dengan efek Ken Burns."""
    tl = topic.lower()
    taxons = taxon_for(topic)
    terms = set(en_terms(topic)) | {topic, " ".join(tokens(topic)[:2])} | set(taxons)
    titles: list[str] = []
    titles.extend(commons_search(sorted(terms), "filetype:bitmap", limit))
    # kategori taksom
    for tx in dict.fromkeys(taxons):
        cats = _commons_cats(tx) + _commons_species_cats(tx)
        for cat in dict.fromkeys(cats):
            for m in _commons_tree(cat, 40):
                if m.lower().endswith((".jpg", ".jpeg", ".png")):
                    titles.append(m)
    seen, keep = set(), []
    for x in titles:
        if x in seen:
            continue
        seen.add(x)
        low = x.lower()
        if any(b in low for b in ("map", "diagram", "distribution", "range", "logo", "icon",
                                  "skull", "skeleton", "skull", "stamp", "coin", "flag")):
            continue
        keep.append(x)
    titles = keep[:50]
    if not titles:
        return []
    res = []
    for i in range(0, len(titles), 20):
        chunk = titles[i:i + 20]
        tq = urllib.parse.quote("|".join(chunk))
        u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
             "&prop=imageinfo&iiprop=url|size|mime|extmetadata|mediatype&titles=" + tq)
        d = http_json(u)
        for pg in d.get("query", {}).get("pages", {}).values():
            ii = (pg.get("imageinfo") or [{}])[0]
            if not ii.get("url"):
                continue
            cm = _commons_meta(ii.get("extmetadata", {}) or {})
            lic = cm["license"]
            desc = cm["description"]
            artist = cm["creator"]
            name = pg.get("title", "").replace("File:", "")
            if not name.lower().endswith((".jpg", ".jpeg", ".png")):
                continue
            w, h = ii.get("width") or 0, ii.get("height") or 0
            if h < 900 or w < 700:
                continue
            low = (name + " " + desc).lower()
            if any(b in low for b in ("extinct animals", "bhl", "watercolor", "cardboard",
                                      "stamp", "coin", "engraving", "lithograph", "drawing of",
                                      "page 1", "book", "plate ", "illustration of a",
                                      "preserved", "specimen", "skull", "skeleton", "fossil",
                                      "museum", "herbarium", "dead", "epibiosis", "parasite",
                                      "snake", "eel", "puzzle", "logo", "poster",
                                      "teeth", "tooth", "histology", "jaw", "mandible",
                                      "bone", "cortex", "neuron", "mri", "eeg", "ct scan",
                                      "x-ray", "radiograph", "trauma", "surgery",
                                      "geography", "distribution map", "range map",
                                      "locator", "diagram", "infographic", "chart",
                                      "graph ", "map.", "map ", "label", "sign",
                                      "ointment", "cream", "bottle", "product",
                                      "plush", "toy", "figurine", "stuffed",
                                      "fmib", "epibiosi", "illustration", "lithograph",
                                      "goupil", "brevourt", "randall", "gunther",
                                      "plate", "fig. ", "figure ", "schematic",
                                      "line art", "clip art", "lineart", "psf",
                                      "drawing", "cartoon", "icon", "vector",
                                      "black and white drawing", "outline")):
                continue
            if is_confusable(topic, name, desc):
                continue
            if not is_relevant(topic, name, desc, artist):
                continue
            if wrong_species(name, desc, relevance_terms(topic)):
                continue
            s_title = score(topic, name) + score(" ".join(relevance_terms(topic)[:2]), name) * 0.5
            s_desc = score(topic, desc, artist) * 0.5
            res.append({
                "source": "Wikimedia Commons (foto)", "title": name,
                "url": ii["url"].split("?")[0], "creator": artist or "NA",
                "license": lic, "license_risk": cm["license_risk"],
                "license_url": cm["license_url"], "usage_terms": cm["usage_terms"],
                "attribution": cm["attribution"], "credit": cm["credit"],
                "attribution_required": cm["attribution_required"],
                "upload_date": cm["date"], "object_name": cm["object_name"],
                "categories": cm["categories"],
                "dur": 0, "w": w, "h": h, "desc": desc.strip()[:300],
                "description": desc.strip()[:600], "media": "foto",
                "page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(pg.get('title',''))}",
                "score": min(100.0, s_title + s_desc + cc_bonus(lic)),
            })
    return res

def archive_video(topic: str, limit: int = 20) -> list[dict]:
    """Archive.org advancedsearch -> metadata files."""
    q = urllib.parse.quote(f"{topic} AND mediatype:(movies)")
    url = ("https://archive.org/advancedsearch.php?q=" + q +
           "&fl%5B%5D=identifier&fl%5B%5D=title&fl%5B%5D=licenseurl&fl%5B%5D=year&fl%5B%5D=creator"
           f"&rows={limit}&output=json")
    d = http_json(url)
    res = []
    for doc in d.get("response", {}).get("docs", []):
        ident = doc.get("identifier")
        if not ident:
            continue
        md = http_json(f"https://archive.org/metadata/{ident}")
        meta = md.get("metadata", {}) or {}
        lic = meta.get("licenseurl") or "NA"
        lic_label = lic.replace("http://creativecommons.org/licenses/", "CC ").replace("/", " ") if lic != "NA" else "NA"
        vid = None
        for f in md.get("files", []):
            n = f.get("name", "")
            if n.lower().endswith((".mp4", ".webm", ".ogv", ".mov")):
                vid = n
                break
        if not vid:
            continue
        creator = meta.get("creator") or "NA"
        if isinstance(creator, list):
            creator = ", ".join(map(str, creator))
        res.append({
            "source": "Archive.org", "title": meta.get("title") or ident,
            "url": f"https://archive.org/download/{ident}/{urllib.parse.quote(vid)}",
            "creator": str(creator)[:80], "license": lic_label,
            "license_risk": license_risk(lic_label)[1], "dur": 0, "w": 0, "h": 0,
            "desc": str(meta.get("description", ""))[:300],
            "page": f"https://archive.org/details/{ident}",
            "score": score(topic, meta.get("title", ""), meta.get("description", ""), creator),
        })
    return res

def youtube_video(topic: str, limit: int = 12, extra: str = "") -> list[dict]:
    """yt-dlp ytsearch via client android (metadata lolos bot-check)."""
    if not shutil.which("yt-dlp") and not os.path.exists(YTDLP):
        return []
    cmd = [YTDLP, "--skip-download", "--no-warnings",
           "--extractor-args", "youtube:player_client=android",
           "--playlist-end", str(limit),
           "--print", "%(id)s\t%(duration)s\t%(uploader)s\t%(upload_date)s\t%(view_count)s\t%(title)s\t%(webpage_url)s\t%(description).200s"]
    queries = [f"{topic} {extra}".strip()] + [f"{q} {extra}".strip() for q in en_terms(topic)]
    queries = list(dict.fromkeys([q for q in queries if q]))[:3]
    res, seen_vid = [], set()
    for q in queries:
        # cache disk per query (ttl 6 jam) -> fetch berikutnya jauh lebih cepat
        cp = _CACHE_DIR / ("yt_" + __import__("hashlib").sha1(q.encode()).hexdigest()[:16] + ".txt")
        try:
            if cp.exists() and (time.time() - cp.stat().st_mtime) < 21600:
                lines = cp.read_text(encoding="utf-8", errors="ignore").splitlines()
            else:
                cmd2 = list(cmd); cmd2.append(f"ytsearch{limit}:{q}")
                r = subprocess.run(cmd2, capture_output=True, text=True, timeout=180)
                lines = r.stdout.splitlines()
                try:
                    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
                    cp.write_text("\n".join(lines), encoding="utf-8")
                except Exception:
                    pass
        except Exception:
            lines = []
        for line in lines:
            p = line.split("\t")
            if len(p) < 7:
                continue
            vid = p[0]
            if vid in seen_vid:
                continue
            seen_vid.add(vid)
            dur, upl, date, views, title, url = p[1], p[2], p[3], p[4], p[5], p[6]
            desc = p[7] if len(p) > 7 else ""
            if is_confusable(topic, title, desc):
                continue
            res.append({
                "source": "YouTube", "title": title, "url": url,
                "creator": upl or "NA", "license": "NA (YouTube ToS)",
                "license_risk": "TINGGI (butuh transformasi berat)",
                "dur": float(dur) if dur.replace(".", "").isdigit() else 0,
                "w": 0, "h": 0, "desc": desc.replace("\n", " "),
                "page": url, "upload_date": date, "views": views, "vid": vid,
                "vertical_flag": False,
                "score": score(topic, title, desc, upl),
            })
    return res


def _youtube_video_old_unused(topic: str, limit: int = 12, extra: str = "") -> list[dict]:
    return []


def openverse_img(topic: str, limit: int = 6) -> list[dict]:
    d = http_json(f"https://api.openverse.org/v1/images/?q={urllib.parse.quote(topic)}&page_size={limit}")
    res = []
    for r in d.get("results", []):
        lic = r.get("license", "") or "NA"
        title = r.get("title", "") or "NA"
        tags = " ".join(x.get("name", "") for x in (r.get("tags") or []))
        desc = (r.get("tags") and " ".join(x.get("name","") for x in r.get("tags",[]))) or ""
        # filter spesies mirip & substansi non-hewan (umumnya hasil Pencarian LLM)
        if is_confusable(topic, title, desc):
            continue
        if not is_relevant(topic, title, desc, r.get("creator", "")):
            continue
        res.append({"source": "Openverse", "title": title,
                    "url": r.get("url", ""), "creator": r.get("creator", "") or "NA",
                    "license": f"{lic} {r.get('license_version','')}".strip(),
                    "license_risk": license_risk(lic)[1], "dur": 0, "w": r.get("width"), "h": r.get("height"),
                    "desc": desc[:200], "page": r.get("foreign_landing_url", ""),
                    "score": score(topic, title, desc)})
    return res

# ---------------------------------------------------------------- pipeline

def cc_bonus(lic: str) -> float:
    """Bonus skor untuk lisensi CC — sumber reuse-safe diutamakan."""
    l = (lic or "").lower()
    if "cc0" in l or "public domain" in l:
        return 25.0
    if "cc by" in l or "cc-by" in l or "creativecommons" in l:
        return 15.0
    if l in ("na", "", "tidak ada lisensi"):
        return 0.0
    return 0.0


def is_cc(lic: str) -> bool:
    l = (lic or "").lower()
    return ("cc" in l) or ("creativecommons" in l) or ("public domain" in l)



def inaturalist_photos(topic: str, limit: int = 12) -> list[dict]:
    """iNaturalist API (tanpa API key) — foto:mmuatan berlisensi CC.

    Bagus untuk stok hewan liar: royalty-free + metadatataxon lengkap.
    """
    terms = en_terms(topic)[:3] or [topic]
    res = []
    for term in terms[:2]:
        u = ("https://api.inaturalist.org/v1/observations?"
             + urllib.parse.urlencode({
                 "q": term, "photos": "true", "quality_grade": "research",
                 "per_page": 20, "order_by": "votes", "locale": "en"}))
        d = http_json(u, timeout=30)
        for r in d.get("results", []):
            for ph in (r.get("photos") or [])[:1]:
                lic = (ph.get("license_code") or "").upper()
                if not lic or lic in ("ALL RIGHTS RESERVED", "NONE"):
                    continue
                tax = r.get("taxon") or {}
                sci = tax.get("name") or ""
                com = tax.get("preferred_common_name") or ""
                title = f"{com or sci} ({sci}) - {tax.get('name') or ''}".strip(" -")
                if is_confusable(topic, title, (r.get("place_guess") or "")):
                    continue
                if not is_relevant(topic, title, (r.get("place_guess") or ""), sci):
                    continue
                creator = ph.get("attribution") or r.get("user", {}).get("login") or "NA"
                iurl = (ph.get("url") or "").replace("/square.", "/original.")
                if not iurl:
                    continue
                lic_url = ""
                m = re.match(r"CC-?([A-Z]+)-?([\d.]+)?", lic)
                if m:
                    fam = {"BY": "by", "BY-NC": "by-nc", "BY-ND": "by-nd",
                           "BY-SA": "by-sa", "BY-NC-SA": "by-nc-sa",
                           "BY-NC-ND": "by-nc-nd", "PD": "publicdomain",
                           "CC0": "publicdomain"}.get(m.group(1), m.group(1).lower())
                    ver = (m.group(2) or "4.0").rstrip(".")
                    lic_url = f"https://creativecommons.org/licenses/{fam}/{ver}/"
                res.append({
                    "source": "iNaturalist",
                    "title": title[:90],
                    "url": iurl,
                    "page": f"https://www.inaturalist.org/observations/{r.get('id')}",
                    "creator": str(creator)[:80],
                    "license": f"CC {lic}" if lic.startswith("CC") else lic,
                    "license_risk": license_risk(lic)[1],
                    "license_url": lic_url,
                    "usage_terms": f"Creative Commons {lic}",
                    "attribution": str(creator)[:120],
                    "credit": str(creator)[:120],
                    "attribution_required": True,
                    "upload_date": (r.get("observed_on") or "")[:10],
                    "observed_on": r.get("observed_on"),
                    "place": (r.get("place_guess") or "")[:160],
                    "country": (r.get("place_guess") or "")[:80],
                    "taxon": sci, "taxon_rank": tax.get("rank", ""),
                    "taxon_common": tax.get("preferred_common_name", ""),
                    "observation_id": r.get("id"),
                    "quality_grade": r.get("quality_grade", ""),
                    "dur": 0, "w": ph.get("width") or 0, "h": ph.get("height") or 0,
                    "desc": (tax.get("preferred_common_name") or sci) + " | " +
                            (r.get("place_guess") or "")[:120],
                    "description": f"{com or sci} ({sci}) — {r.get('place_guess') or 'lokasi tidak dicatat'}"
                                   f" — diamati {(r.get('observed_on') or '')[:10]}",
                    "media": "foto",
                    "score": score(topic, title, (r.get("place_guess") or ""), sci),
                })
        if len(res) >= limit:
            break
    return res


def is_reusable(lic: str) -> bool:
    """CC yang boleh dipakai untuk channel monetized.

    NC (non-komersial) dan ND (no-derivatives) TIDAK boleh dipakai karena
    Shorts YouTube = konten komersial & sudah di-crop (derivatif).
    """
    l = (lic or "").upper()
    if not is_cc(lic):
        return False
    if "-NC" in l or "NONCOMMERCIAL" in l or "NON-COMMERCIAL" in l:
        return False
    if "-ND" in l or "NODERIV" in l or "NO-DERIVATIVES" in l:
        return False
    return True


def gather(topic: str, limit: int, extra: str = "") -> list[dict]:
    rows = []
    rows += commons_video(topic)
    rows += commons_photos(topic, limit=limit)
    rows += archive_video(topic)
    rows += inaturalist_photos(topic, limit=limit)
    rows += youtube_video(topic, limit=limit, extra=extra)
    try:
        rows += openverse_img(topic, limit=8)
    except Exception:
        pass
    # dedup by title/url
    seen, uniq = set(), []
    for r in rows:
        k = (r.get("url") or r.get("title", ""))[:120]
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    for r in uniq:
        r["score"] = min(100.0, r.get("score", 0) + fit_bonus(r))
    uniq.sort(key=lambda r: r.get("score", 0), reverse=True)
    return uniq

def to_row_md(c: dict, slot: int, shot: str, note: str = "") -> str:
    return (f"{slot} | {c['source']} | {c['url']} | {c['title'][:70]} / {c.get('creator','')[:30]} | "
            f"cut 00:00-00:00 | {c['license']} | {c.get('license_risk','')} | skor {c.get('score',0):.0f} | {shot} | {note}")

def cmd_search(a):
    rows = gather(a.topic, a.limit, a.extra)
    if a.license == "cc":
        rows = [r for r in rows if is_cc(r.get("license", ""))]
    elif a.license == "cc-preferred":
        rows.sort(key=lambda r: -is_cc(r.get("license", "")))
    if a.reuse_only:
        rows = [r for r in rows if is_reusable(r.get("license", ""))]
    if a.exclude_source:
        ex = {s.strip().lower() for s in a.exclude_source.split(",") if s.strip()}
        rows = [r for r in rows if r.get("source", "").lower() not in ex
                and not any(e in r.get("source", "").lower() for e in ex)]
    rows = rows[: a.limit]
    print(f"# DOKLI SEARCH — topic: {a.topic!r}  (limit {a.limit})\n")
    if not rows:
        print("TIDAK ADA KANDIDAT.")
        return
    print(f"{'#':>2} {'SKOR':>5} {'SUMBER':<18} {'LISENSI':<22} {'RISIKO':<28} DUR  JUDUL")
    print("-" * 130)
    for i, c in enumerate(rows, 1):
        print(f"{i:>2} {c.get('score',0):>5.0f} {c['source']:<18} {str(c.get('license'))[:21]:<22} "
              f"{str(c.get('license_risk'))[:27]:<28} {str(c.get('dur','')):>4}  {c['title'][:48]}")
    print("\n## Detail (JSON)")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def _curl_download(url: str, dest: Path, ua: str = UA, tries: int = 5) -> tuple[bool, str]:
    """Unduh file dengan retry bertingkat (Wikimedia balas 429 saat burst)."""
    for i in range(tries):
        try:
            r = subprocess.run(["curl", "-sL", "-A", ua, "-o", str(dest),
                                "-w", "%{http_code}", url],
                               capture_output=True, text=True, timeout=300)
            code = (r.stdout or "").strip()[-3:]
            size = dest.stat().st_size if dest.exists() else 0
            if code == "200" and size > 1024:
                return True, ""
            if code == "429":
                time.sleep(4 * (i + 1))
                continue
            if code in ("200", "206"):
                return True, ""
            time.sleep(2 * (i + 1))
        except Exception as e:
            time.sleep(2 * (i + 1))
    return False, f"http {code or '??'}"


def download(c: dict, outdir: Path, idx: int, max_height: int = 1080, min_height: int = 480) -> dict:
    outdir.mkdir(parents=True, exist_ok=True)
    src = c["source"]
    base = f"{idx:02d}-{re.sub(r'[^a-z0-9]+','-', c['title'].lower())[:40].strip('-')}"
    # ekstensi: ikut jenis media & URL (foto jpg/png, video mp4/webm)
    u_ext = Path(urllib.parse.urlparse(c.get("url", "")).path).suffix.lower()
    if u_ext in (".jpg", ".jpeg", ".png", ".webp", ".gif", ".webm", ".ogv",
                 ".mp4", ".mov", ".m4v"):
        ext = u_ext
    elif c.get("media") == "foto":
        ext = ".jpg"
    else:
        ext = ".mp4"
    dest = outdir / (base + ext)
    if src == "YouTube":
        # coba bertingkat: 1080 avc1 -> 720 avc1 -> mp4 terbaik -> terbaik apa pun
        tries = [
            f"bv*[height<={max_height}][vcodec^=avc1]+ba/b[height<={max_height}][ext=mp4]",
            f"bv*[height<=720][vcodec^=avc1]+ba/b[height<=720][ext=mp4]",
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        ]
        cmd = None
        for sel in tries:
            cmd = [YTDLP, "--extractor-args", "youtube:player_client=default,android",
                   "-f", sel, "--merge-output-format", "mp4",
                   "-o", str(dest.with_suffix(".%(ext)s")), c["url"]]
            subprocess.run(cmd, capture_output=True, text=True, timeout=300)
            produced = [p for p in outdir.glob(base + ".*")
                        if p.suffix.lower() in (".mp4", ".mkv", ".webm", ".mov")]
            if produced:
                # cek resolusi hasil; kalau jelek coba level berikutnya
                tmp = max(produced, key=lambda p: p.stat().st_size)
                m = ffprobe(str(tmp))
                if (m.get("h") or 0) >= min_height:
                    break
                for p_ in produced:
                    try: p_.unlink()
                    except Exception: pass
                produced = []
        if not produced:
            return {**c, "download": "GAGAL", "err": "resolusi tidak cukup"}
    elif src.startswith("Wikimedia Commons"):
        # ekstensi asli dari URL supaya ffmpeg/ffprobe tidak salah baca
        ext = Path(urllib.parse.urlparse(c["url"]).path).suffix.lower()
        if ext not in (".webm", ".ogv", ".mp4", ".mov", ".jpg", ".jpeg", ".png"):
            ext = ".jpg" if c.get("media") == "foto" else ".mp4"
        dest = outdir / (base + ext)
        ok, err = _curl_download(c["url"], dest, UA_WEB)
        if not ok:
            dest.unlink(missing_ok=True)
            return {**c, "download": "GAGAL", "err": err or "gagal unduh"}
        return {**c, "download": "OK", "file": str(dest), "probe": ffprobe(str(dest)),
                "is_photo": ext in (".jpg", ".jpeg", ".png")}
    else:
        ok, err = _curl_download(c["url"], dest, UA)
        if not ok:
            dest.unlink(missing_ok=True)
            return {**c, "download": "GAGAL", "err": err or "gagal unduh"}
    # validasi: file HTML/pesan error bukan media -> hapus & gagalkan
    try:
        head = dest.open("rb").read(200).lstrip().lower()
        if head.startswith((b"<!doctype", b"<html")):
            dest.unlink(missing_ok=True)
            return {**c, "download": "GAGAL", "err": "server menolak (HTML/error page)"}
    except Exception:
        pass
    # yt-dlp merge bisa menghasilkan .mkv -> normalisasi
    produced = [p for p in outdir.glob(base + ".*") if p.suffix.lower() in
                (".mp4", ".mkv", ".webm", ".mov", ".ogv", ".jpg", ".jpeg", ".png", ".webp")]
    if not produced:
        return {**c, "download": "GAGAL", "err": "tidak ada file terunduh"}
    final = max(produced, key=lambda p: p.stat().st_size)
    if final.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        # foto: biarkan sebagai gambar (dipakai lewat Ken Burns)
        return {**c, "download": "OK", "file": str(final), "probe": ffprobe(str(final)),
                "is_photo": True}
    if final.suffix.lower() != ".mp4":
        newf = final.with_suffix(".mp4")
        r = subprocess.run(["ffmpeg", "-y", "-i", str(final), "-c", "copy", "-movflags", "+faststart", str(newf)],
                           capture_output=True, text=True, timeout=300)
        if r.returncode == 0 and newf.exists() and newf.stat().st_size > 0:
            try:
                final.unlink()
            except Exception:
                pass
            final = newf
        else:
            newf.unlink(missing_ok=True)
    for p in produced:
        if p != final:
            try: p.unlink()
            except Exception: pass
    meta = ffprobe(str(final))
    return {**c, "file": str(final), "probe": meta, "download": "OK"}


def sha256_file(path: str) -> str:
    """Hash SHA-256 file — jejak audit provenance (bukti file tidak berubah)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except Exception:
        return ""
    return h.hexdigest()


def credit_line(m: dict, fname: str = "") -> str:
    """Baris kredit siap tempel untuk deskripsi YouTube."""
    title = (m.get("title") or fname or "materi").strip()
    cre = (m.get("creator") or "Tidak diketahui").strip()
    lic = (m.get("license") or "TIDAK ADA LISENSI - jangan pakai").strip()
    url = m.get("page") or m.get("url") or ""
    src = m.get("source") or "?"
    lurl = m.get("license_url") or ""
    extra = f" ({lurl})" if lurl else ""
    if not is_cc(lic):
        return (f"- [{fname}] {src}: {title} - kreator {cre} - **LISENSI: {lic}** "
                f"- JANGAN PUBLISH tanpa izin kreator. {url}")
    sa = " — WAJIB share-alike (derivatif harus lisensi sama)" if "by-sa" in lic.lower() else ""
    return (f"- [{fname}] {title} — oleh {cre} — {lic}{extra} — {url}{sa}")



# ---------- KONTEKS: bridge antara naskah/brief dan materi doksli ----------
# Setiap slot narasi punya tipe shot + kata kunci visual. Doksli dicocokkan ke
# slot supaya metadata bukan sekadar "file Unduh", tapi "materi untuk beat X".

CTX_SHOTS = {
    "hook":      ("wajah mata kepala portrait face eye muzzle snout", "hook"),
    "detail":    ("macro detail tekstur closeup zoom fur skin scales", "detail"),
    "action":    ("berenang swims swimming running climbing moving walk jump", "action"),
    "establish": ("panorama laut underwater whole body full body landscape habitat", "establish"),
    "fakta":     ("diagram peta facts infographic grafik map chart", "fakta"),
    "ajaib":     ("unusual unik aneh weird baby lahir birth newborn rare", "ajaib"),
}

CTX_KEY_ID = {"hook": 1.0, "ajaib": 0.95, "action": 0.9, "detail": 0.85,
              "establish": 0.7, "fakta": 0.5}


def parse_brief(path: str) -> list[dict]:
    """Baca brief-edit.md -> daftar slot narasi (beat, detik, naskah, visual)."""
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except Exception:
        return []
    slots = []
    for line in raw.splitlines():
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 4:
            continue
        num, detik, naskah, visual = cells[0], cells[1], cells[2], cells[3]
        if not num.isdigit():
            continue
        # slot CTA & outro tidak butuh footage doksli (button subscribe / logo)
        low_all = (naskah + " " + visual).lower()
        if ("subscribe" in low_all or "tombol subscribe" in low_all) and int(num) <= 3:
            best = "cta"
        elif "logo" in low_all or "outro" in low_all:
            best = "outro"
        else:
            # tentukan tipe shot dari teks visual + naskah
            blob = low_all
            best, best_score = "detail", 0
            for shot, (kws, _) in CTX_SHOTS.items():
                sc = sum(1 for k in kws.split() if k and k in blob)
                if sc > best_score:
                    best, best_score = shot, sc
            if int(num) == 1:
                best = "hook"
            elif best_score == 0:
                best = "detail"
        slots.append({"n": int(num), "waktu": detik, "naskah": naskah,
                      "visual": visual, "shot": best,
                      "keyword": [k for k in re.findall(r"[a-zA-Z]{4,}", visual.lower())
                                  if k not in STOP][:8]})
    return slots


def ctx_match(cand: dict, slot: dict) -> tuple[float, str]:
    """Skor kecocokan kandidat doksli ke satu slot narasi + alasan.

    Memakai metadata: judul, deskripsi, shot_hint, durasi, orientasi.
    """
    blob = " ".join(str(cand.get(k) or "") for k in
                    ("title", "desc", "description", "shot_hint", "creator")).lower()
    shot = slot["shot"]
    kws = [k for k in CTX_SHOTS[shot][0].split() if len(k) > 2]
    hit = [k for k in kws if k in blob]
    base = CTX_KEY_ID.get(shot, 0.6) * (len(hit) / max(1, len(kws))) * 40
    # keyword visual dari brief
    vhit = [k for k in slot.get("keyword", []) if k in blob]
    base += min(25.0, len(vhit) * 8.0)
    # frasa naskah (kata panjang) di judul/deskripsi
    words = [w for w in re.findall(r"[a-z]{5,}", slot["naskah"].lower()) if w not in STOP]
    nhit = [w for w in words if w in blob]
    base += min(20.0, len(nhit) * 6.0)
    # durasi: untuk 'action'/'ajaib' durasi pendek lebih cocok (potong momen)
    dur = float(cand.get("dur") or 0)
    if dur:
        if shot in ("hook", "ajaib") and 3 <= dur <= 45:
            base += 8
        elif shot == "detail" and dur >= 20:
            base += 6
        elif shot == "establish" and dur >= 15:
            base += 6
    # orientasi vertikal menguntungkan hook
    if cand.get("h") and cand.get("w") and cand["h"] > cand["w"] and shot == "hook":
        base += 8
    # realitas visual: garis/klip art tidak cocok untuk Shorts fakta
    blob_l = blob.lower()
    if any(b in blob_l for b in ("line art", "clip art", "cartoon", "drawing",
                                 "sketch", "illustration", "vector", "psf",
                                 "black and white")):
        base -= 20
    # video motion lebih MDL untuk beat action/ajaib
    if cand.get("media") == "video" and shot in ("action", "ajaib", "establish"):
        base += 8
    # lisensi: reuse-safe jauh lebih berharga untuk channel monetized
    lic = cand.get("license", "") or ""
    tier = license_tier(lic)
    if is_cc(lic):
        base += 5 + tier * 2          # CC0/PD paling tinggi
    elif tier == 0:
        base -= 12                    # lisensi kosong / YouTube ToS: bukan reuse-safe
    return round(min(100.0, base), 1), (f"shot={shot} hit={hit or '-'} vhit={vhit or '-'} " +
                                       f"nhit={nhit or '-'} dur={dur or '-'}")


def assign_ctx(cands: list[dict], slots: list[dict]) -> list[dict]:
    """Greedy: setiap slot dapat 1 kandidat terbaik, tiap kandidat 1 slot.

    Slot CTA/outro dilewati: butuh tombol subscribe / logo, bukan footage.
    """
    pairs = []
    for si, sl in enumerate(slots):
        if sl.get("shot") in ("cta", "outro"):
            continue
        for ci, c in enumerate(cands):
            sc, why = ctx_match(c, sl)
            pairs.append((sc, si, ci, why))
    pairs.sort(key=lambda x: -x[0])
    slot_used, cand_used, out = set(), set(), []
    for sc, si, ci, why in pairs:
        if si in slot_used or ci in cand_used:
            continue
        slot_used.add(si); cand_used.add(ci)
        out.append((sc, si, ci, why))
    return out



def cmd_ctx(a):
    """Petakan brief-edit.md + kandidat doksli -> slot narasi (tanpa unduh).

    Output: per slot beat, tipe shot, kandidat terbaik + alasan kecocokan +
    skor relevansi umum + lisensi. Ini dipakai_frida untuk memilih materi
    SEBELUM mengunduh (hemat kuota & waktu).
    """
    slots = parse_brief(a.brief)
    if not slots:
        print("TIDAK ADA SLOT terbaca dari", a.brief)
        return
    rows = gather(a.topic, a.limit, a.extra)
    if a.license == "cc":
        rows = [r for r in rows if is_cc(r.get("license", ""))]
    elif a.license == "cc-preferred":
        rows.sort(key=lambda r: -license_tier(r.get("license", "")))
    if a.reuse_only:
        rows = [r for r in rows if is_reusable(r.get("license", ""))]
    pairs = assign_ctx(rows, slots)
    best: dict[int, dict] = {}
    for sc, si, ci, why in pairs:
        if si not in best or sc > best[si]["score"]:
            best[si] = {"score": sc, "cand": rows[ci], "why": why}
    print(f"# PETA KONTEKS — {a.topic}\n")
    print(f"{'SLOT':>4} {'WAKTU':<12} {'SHOT':<10} {'SKOR':>5} {'LISENSI':<16} KANDIDAT")
    print("-" * 120)
    for i, sl in enumerate(slots):
        b = best.get(i)
        if not b:
            print(f"{sl['n']:>4} {sl['waktu']:<12} {sl['shot']:<10} {'-':>5} "
                  f"{'-':<16} (tidak ada kandidat)")
            continue
        c = b["cand"]
        print(f"{sl['n']:>4} {sl['waktu']:<12} {sl['shot']:<10} {b['score']:>5.1f} "
              f"{str(c.get('license'))[:15]:<16} {c['title'][:44]}")
        print(f"       beat: {sl['naskah'][:78]}")
        print(f"       visual: {sl['visual'][:78]}")
        print(f"       src: {c['source']} | {c.get('creator','')[:26]} | "
              f"relevansi {c.get('score',0):.0f} | {b['why']}")
    # ringkasan cakupan
    covered = len(best)
    print(f"\nCakupan: {covered}/{len(slots)} slot punya kandidat.")
    if covered < len(slots):
        missing = [slots[i]['n'] for i in range(len(slots)) if i not in best]
        print("Slot tanpa kandidat:", missing)
    print("\n## JSON")
    print(json.dumps([{"slot": slots[i]["n"], "waktu": sl["waktu"], "shot": sl["shot"],
                       "beat": sl["naskah"], "visual": sl["visual"],
                       "ctx_score": b["score"], "why": b["why"],
                       "title": b["cand"]["title"], "source": b["cand"]["source"],
                       "url": b["cand"]["url"], "creator": b["cand"].get("creator"),
                       "license": b["cand"].get("license"),
                       "license_risk": b["cand"].get("license_risk"),
                       "dur": b["cand"].get("dur"), "w": b["cand"].get("w"),
                       "h": b["cand"].get("h"), "relevansi": b["cand"].get("score")}
                      for i, sl in enumerate(slots) if (b := best.get(i))],
                     ensure_ascii=False, indent=2))


def cmd_fetch(a):
    rows = gather(a.topic, a.limit, a.extra)
    if not rows:
        print("TIDAK ADA KANDIDAT."); return
    # filter: minimal durasi 6 dtk (kalau diketahui), skor >= threshold
    cand = [r for r in rows if r.get("score", 0) >= a.min_score]
    if a.max_risk:
        cand = [r for r in cand if "TINGGI" not in r.get("license_risk", "")]
    if a.license == "cc":
        cand = [r for r in cand if is_cc(r.get("license", ""))]
    # NC/ND hanya relevan saat mode CC: filter license dulu, lalu buang NC/ND
    if a.license in ("cc", "cc-preferred") and not a.allow_ncnd:
        cand = [r for r in cand if is_reusable(r.get("license", ""))]
    if a.exclude_source:
        ex = {s.strip().lower() for s in a.exclude_source.split(",") if s.strip()}
        cand = [r for r in cand if r.get("source", "").lower() not in ex
                and not any(e in r.get("source", "").lower() for e in ex)]
    elif a.license == "cc-preferred":
        # CC + lisensi paling aman (CC0/PD > CC BY > CC BY-SA) didahulukan
        cand.sort(key=lambda r: (-int(is_cc(r.get("license", ""))),
                                 -license_tier(r.get("license", ""))))
    # filter resolusi minimum (harus layak crop ke 1080x1920)
    if a.min_height > 0:
        cand = [r for r in cand if (r.get("h") or 0) >= a.min_height or not r.get("h")]
    # rotasi kreator: maksimal 1 kandidat per kreator
    if a.max_per_creator > 0:
        seen_c, rot = {}, []
        for r in cand:
            c = (r.get("creator") or "NA").strip().lower()
            if seen_c.get(c, 0) >= a.max_per_creator:
                continue
            seen_c[c] = seen_c.get(c, 0) + 1
            rot.append(r)
        cand = rot
    # diversity: max N per sumber supaya 1 sumber tidak mendominasi
    per_src, diverse = {}, []
    for r in cand:
        s = r["source"]
        if per_src.get(s, 0) >= a.per_source:
            continue
        per_src[s] = per_src.get(s, 0) + 1
        diverse.append(r)
    cand = diverse
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    # --- konteks narasi: brief-edit.md -> kandidat dipetakan ke slot beat ---
    slots = parse_brief(a.brief) if getattr(a, "brief", "") else []
    ctxmap = {}
    if slots:
        pairs = assign_ctx(cand, slots)
        ctxmap = {ci: (si, sc, why) for sc, si, ci, why in pairs}
        print(f"\n# KONTEKS: {len(slots)} slot narasi, {len(ctxmap)} kandidat terpetakan")
        for sc, si, ci, why in pairs:
            sl = slots[si]
            print(f"  slot {sl['n']:>2} [{sl['shot']:<10}] <- {cand[ci]['title'][:40]:<42} "
                  f"skor ctx {sc:>5.1f}  ({why})")
        slots_by_idx = {i: sl for i, sl in enumerate(slots)}
        if ctxmap:
            # jaga indeks asli supaya ctxmap tetap konsisten
            cand = [(i, c) for i, c in enumerate(cand) if i in ctxmap]
    shots = a.shots.split(",") if a.shots else ["" for _ in range(99)]
    done, idx = [], 0
    for ci, c in cand:
        if len(done) >= a.limit:
            break
        idx += 1
        shot = shots[(idx - 1) % len(shots)] if shots else ""
        ctx = ctxmap.get(ci)
        res = download(c, outdir, idx, a.max_height, a.min_height)
        if res.get("download") != "OK":
            print(f"  [{idx}] GAGAL: {c['title'][:50]}"); continue
        pr = res.get("probe", {})
        print(f"  [{idx}] OK  {Path(res['file']).name}  {pr.get('w','?')}x{pr.get('h','?')} "
              f"{pr.get('dur','?')}s {round(pr.get('bytes',0)/1e6,1)}MB  skor {c.get('score',0):.0f}  [{shot}]")
        done.append({**res, "shot": shot,
                     "ctx": ({"slot_n": slots[ctx[0]]["n"], "beat": slots[ctx[0]]["naskah"][:120],
                              "waktu": slots[ctx[0]]["waktu"], "shot_type": slots[ctx[0]]["shot"],
                              "visual": slots[ctx[0]]["visual"][:120],
                              "ctx_score": ctx[1], "ctx_why": ctx[2]} if ctx else None)})
    # tulis sources.md
    if done and a.sources:
        sp = Path(a.sources)
        lines = [f"\n## Hasil fetch otomatis ({a.topic}) — {time.strftime('%Y-%m-%d %H:%M')}",
                 "Format: slot | sumber | url | judul/creator | timestamp potong | lisensi | risiko | skor | shot | transform-note"]
        for i, d in enumerate(done, 1):
            pr = d.get("probe", {})
            cut = f"00:00-00:0{min(5, int(pr.get('dur',5) or 5))}"
            lines.append(to_row_md({**d, "dur": pr.get("dur", d.get("dur"))}, i, d.get("shot",""),
                                   d.get("transform_note","")))
        sp.parent.mkdir(parents=True, exist_ok=True)
        with sp.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print(f"\n-> {len(done)} klip ditulis ke {sp}")
    # sidecar metadata: sumber resmi -> aset lokal (dipakai manifest & audit)
    if done:
        side = outdir / "_metadata.json"
        old_meta = {}
        if side.exists():
            try:
                old_meta = json.loads(side.read_text(encoding="utf-8"))
            except Exception:
                old_meta = {}
        credits = []
        for d in done:
            pr = d.get("probe", {})
            full = _full_meta(d.get("url", "")) if d.get("source") == "YouTube" and d.get("url") else {}
            lic, risk = license_kind(full) if full else (d.get("license", ""), d.get("license_risk", ""))
            fn = Path(d["file"]).name
            entry = {
                # --- identitas sumber ---
                "source": d.get("source"), "url": d.get("url"),
                "page": d.get("page", ""),
                "title": full.get("title") or d.get("title"),
                "creator": full.get("channel") or d.get("creator"),
                # --- hak penggunaan ---
                "license": lic, "license_risk": risk,
                "license_url": full.get("license_url") or d.get("license_url") or "",
                "usage_terms": d.get("usage_terms", ""),
                "attribution": d.get("attribution") or (full.get("channel") or d.get("creator") or ""),
                "credit": d.get("credit", ""),
                "attribution_required": d.get("attribution_required", is_cc(lic)),
                "reusable": is_reusable(lic) if is_cc(lic) else False,
                "publikasi_aman": bool(is_reusable(lic)),
                # --- waktu & ukuran ---
                "upload_date": full.get("upload_date") or d.get("upload_date"),
                "observed_on": d.get("observed_on"),
                "views": full.get("view_count") or d.get("views"),
                "duration": pr.get("dur"), "width": pr.get("w"), "height": pr.get("h"),
                "aspect": (round(pr.get("w", 0) / pr.get("h", 1), 3) if pr.get("h") else None),
                "fps": pr.get("fps"), "vcodec": pr.get("vcodec"), "acodec": pr.get("acodec"),
                "bytes": pr.get("bytes"), "mime": pr.get("mime"),
                "media": "foto" if d.get("is_photo") else "video",
                "sha256": sha256_file(d["file"]),
                # --- konteks biologis ---
                "description": (d.get("description") or d.get("desc") or "")[:600],
                "taxon": d.get("taxon", ""),
                "taxon_rank": d.get("taxon_rank", ""),
                "taxon_common": d.get("taxon_common", ""),
                "categories": d.get("categories", []),
                "object_name": d.get("object_name", ""),
                "place": d.get("place", ""),
                "observation_id": d.get("observation_id"),
                "quality_grade": d.get("quality_grade", ""),
                # --- konteks narasi ---
                "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
                "shot_hint": d.get("shot", "") or ((d.get("ctx") or {}).get("shot_type") or ""),
                "relevansi": d.get("score"),
                "konteks": d.get("ctx") or None,
            }
            old_meta[fn] = entry
            credits.append(credit_line(entry, fn))
        side.write_text(json.dumps(old_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n-> metadata sidecar: {side}")
        cred_path = outdir.parent / "CREDITS.md"
        cred_path.write_text(
            "# Kredit & Atribusi Materi\n\n"
            "Format: `Judul/Deskripsi` by KREATOR — LISENSI — URL\n\n"
            "Tempel blok ini ke deskripsi YouTube (WAJIB untuk materi berlisensi CC).\n\n"
            + "\n".join(credits) + "\n", encoding="utf-8")
        print(f"-> kredit/atribusi: {cred_path}")
    # ringkas json
    print(json.dumps([{k: v for k, v in d.items() if k in ("title","source","url","creator","license","license_risk","score","file","shot","ctx","probe")} for d in done],
                     ensure_ascii=False, indent=2))



def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (title or "").lower())[:40].strip("-")


def cmd_manifest(a):
    """Audit per-klip: plan-klip.json + aset + metadata -> tabel di sources.md.

    Setiap baris memuat: klip, file sumber, URL, judul, kreator, lisensi, risiko,
    timestamp potong (in-out), resolusi, durasi, transform-note.
    """
    base = Path(a.base)
    plan = json.loads((base / "plan-klip.json").read_text(encoding="utf-8"))
    assets = base / "assets"
    side_path = assets / "_metadata.json"
    meta = {}
    if side_path.exists():
        meta = json.loads(side_path.read_text(encoding="utf-8"))
    # rekonstruksi metadata dari nama file bila sidecar tidak ada (aset lama)
    if a.topic and not meta:
        rows = gather(a.topic, a.limit or 40, a.extra or "")
        by_slug = {}
        for r in rows:
            by_slug.setdefault(_slug(r.get("title", "")), r)
        for f in assets.glob("*.mp4"):
            m = re.match(r"^\d+-(.+)$", f.stem)
            if not m:
                continue
            hit = by_slug.get(m.group(1))
            if hit:
                meta[f.name] = {"source": hit.get("source"), "url": hit.get("url"),
                                 "title": hit.get("title"), "creator": hit.get("creator"),
                                 "license": hit.get("license"), "license_risk": hit.get("license_risk"),
                                 "upload_date": hit.get("upload_date"), "views": hit.get("views"),
                                 "description": "", "reconstructed": True}
    missing = []
    lines = ["", f"## Manifest per-klip (audit otomatis — {time.strftime('%Y-%m-%d %H:%M')})", "",
             "| # | Klip | Beat narasi | Sumber | URL | Judul | Kreator | Lisensi | Risiko | Publikasi aman | Timestamp | Resolusi | Durasi | Transform-note |",
             "|---|------|------------|--------|-----|-------|---------|---------|-------|----------------|-----------|----------|--------|----------------|"]
    seen_creators = {}
    for i, it in enumerate(plan, 1):
        src = Path(it["src"])
        key = src.name
        m = meta.get(key, {})
        lic = m.get("license") or "TIDAK ADA LISENSI - PERIKSA MANUAL"
        risk = m.get("license_risk") or ""
        if not m:
            missing.append(key)
        st = float(it.get("start", 0))
        dur = float(it.get("dur", 2.4))
        ts = f"{int(st//60):02d}:{int(st%60):02d}-{int((st+dur)//60):02d}:{int((st+dur)%60):02d}"
        z = it.get("zoom", 1.0)
        note = (f"crop 9:16 dari tengah; zoom-punch {z:.2f}x; eq contrast 1.06 sat 1.05; h264 crf20 aac128k; "
                f"VO ElevenLabs ID original; caption ID burned-in")
        creator = m.get("creator") or "TIDAK TERCATAT"
        cr = creator.strip().lower()
        seen_creators[cr] = seen_creators.get(cr, 0) + 1
        ctx = m.get("konteks") or {}
        beat = (ctx.get("beat") or it.get("beat") or "-")
        beat = beat.replace("|", "/")[:46]
        safe = "YA" if (m.get("publikasi_aman") if "publikasi_aman" in m
                        else is_reusable(lic)) else "TIDAK"
        lines.append(f"| {i} | `{it.get('name','')}` | {beat} | {m.get('source','?')} | {m.get('url','TIDAK TERCATAT')} "
                     f"| {(m.get('title') or src.stem)[:60]} | {creator[:34]} | {lic[:40]} | {risk[:26]} | {safe} "
                     f"| {ts} | {m.get('width','?')}x{m.get('height','?')} | {m.get('duration','?')}s | {note} |")
    lines.append("")
    lines.append("### Cek konsistensi")
    lines.append(f"- Total klip: {len(plan)}")
    lines.append(f"- Aset tanpa metadata: {len(missing)}" + (f" -> {missing}" if missing else ""))
    lines.append(f"- Kreator dipakai: {len(seen_creators)} (rotasi: tidak boleh 2x kreator sama >1 kali dalam 7 hari untuk slot yang sama)")
    for c, n in sorted(seen_creators.items(), key=lambda x: -x[1]):
        lines.append(f"  - {c}: {n} klip")
    bad = [k for k, v in meta.items() if not v.get("license") or "TIDAK" in str(v.get("license"))]
    lines.append(f"- Klip berisiko lisensi kosong/tidak ada: {len(bad)} -> WAJIB cek manual sebelum publish")
    unsafe = [k for k, v in meta.items()
              if not (v.get("publikasi_aman") if "publikasi_aman" in v else is_reusable(v.get("license", "")))]
    lines.append(f"- Klip TIDAK reuse-safe (NC/ND/ToS/kosong): {len(unsafe)} -> {unsafe or 'tidak ada'}")
    nocontext = [k for k, v in meta.items() if not v.get("konteks")]
    lines.append(f"- Aset tanpa konteks narasi: {len(nocontext)} -> {nocontext or 'tidak ada'}")
    noattr = [k for k, v in meta.items() if v.get("attribution_required") and not v.get("attribution")]
    lines.append(f"- Wajib atribusi tapi kredit kosong: {len(noattr)} -> {noattr or 'tidak ada'}")
    out = base / "sources.md"
    with out.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(json.dumps({"sources_md": str(out), "klip": len(plan),
                      "tanpa_metadata": missing, "kreator": seen_creators,
                      "risiko_lisensi_kosong": bad,
                      "tidak_reuse_safe": unsafe,
                      "tanpa_konteks": nocontext,
                      "tanpa_kredit": noattr}, ensure_ascii=False, indent=2))


def _ytsearch_json(query: str, n: int = 5) -> list[dict]:
    """Cari metadata YouTube via yt-dlp ytsearch (tanpa download)."""
    cmd = [YTDLP, "--extractor-args", "youtube:player_client=default,android",
           "--dump-single-json", "--flat-playlist", f"ytsearch{n}:{query}"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    except Exception:
        return []
    if not r.stdout.strip():
        return []
    try:
        data = json.loads(r.stdout)
    except Exception:
        return []
    return data.get("entries") or []



def _full_meta(url: str) -> dict:
    """Metadata lengkap 1 video (tanpa download): lisensi, tanggal, deskripsi, durasi."""
    cmd = [YTDLP, "--extractor-args", "youtube:player_client=default,android",
           "--dump-single-json", "--skip-download", "--no-warnings", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
        return json.loads(r.stdout) if r.stdout.strip() else {}
    except Exception:
        return {}


def license_kind(d: dict) -> tuple[str, str]:
    """-> (lisensi, risiko) dari metadata YouTube. License None = standard = all rights reserved."""
    lic = (d.get("license") or "").strip()
    if lic and "creative commons" in lic.lower():
        return lic, "RENDAH - reuse allowed (CC, wajib cantung kredit)"
    if lic:
        return lic, "PERIKSA - lisensi non-standar"
    ch = (d.get("channel") or d.get("uploader") or "").lower()
    if any(k in ch for k in ("nat geo", "national geographic", "ted", "ted-ed")):
        return "Standard YouTube License (all rights reserved)", "TINGGI - materi Nowhere, kutip fair-use seperlunya"
    return "Standard YouTube License (all rights reserved)", "TINGGI - all rights reserved, WAJIB izin/transformasi besar"

def cmd_remeta(a):
    """Rekonstruksi metadata aset yang sudah terunduh (sidecar _metadata.json).

    MenCocokkan judul asli dari nama file dengan hasil ytsearch, lalu ambil
    metadata lengkap: url, judul, kreator, lisensi, tanggal, durasi, views.
    """
    assets = Path(a.dir)
    side = assets / "_metadata.json"
    meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
    files = sorted(assets.glob("*.mp4"))
    for f in files:
        if f.name in meta and meta[f.name].get("url") and meta[f.name].get("license_risk"):
            print(f"  SKIP (ada): {f.name}")
            continue
        m = re.match(r"^\d+-(.+)$", f.stem)
        if not m:
            continue
        slug0 = m.group(1)
        queries = [slug0.replace("-", " ")]
        # fallback: potong kata dari belakang progressively (judul aslinya lebih panjang)
        parts = slug0.split("-")
        for cut in (3, 5, 7):
            if len(parts) > cut:
                queries.append(" ".join(parts[:cut]))
        best = None
        cands = []
        for q in queries:
            cands = _ytsearch_json(q, a.n)
            for c in cands:
                if _slug(c.get("title", "")) == slug0:
                    best = c
                    break
            if best:
                break
        if not best and cands:
            # ter Fuzzy: slug terbaik yang share prefix kata pertama >= 3 kata
            def sim(s):
                sw = set(s.split("-"))
                qw = set(slug0.split("-"))
                return len(sw & qw) / max(1, len(qw))
            cands.sort(key=lambda c: -sim(_slug(c.get("title", ""))))
            best = cands[0]
        if not best:
            print(f"  GAGAL: {f.name}")
            continue
        pr = ffprobe(str(f))
        url = best.get("webpage_url") or best.get("url", "")
        full = _full_meta(url) if url else {}
        lic, risk = license_kind(full)
        meta[f.name] = {
            "source": "YouTube",
            "url": url,
            "title": full.get("title") or best.get("title", ""),
            "creator": full.get("channel") or full.get("uploader") or best.get("channel", ""),
            "license": lic,
            "license_risk": risk,
            "upload_date": (full.get("upload_date") or best.get("upload_date") or ""),
            "views": full.get("view_count") or best.get("view_count"),
            "duration": pr.get("dur") or full.get("duration"),
            "width": pr.get("w"), "height": pr.get("h"),
            "bytes": pr.get("bytes"), "mime": pr.get("mime"),
            "description": (full.get("description") or best.get("description") or "")[:600],
            "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
            "reconstructed": True,
        }
        print(f"  OK  {f.name}  ->  {best.get('channel','?')[:24]} | {best.get('title','')[:44]}")
    side.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n-> sidecar: {side} ({len(meta)} entri)")


def _kenburns(src: Path, outdir: Path, name: str, dur: float, zoom: float,
              fx: float = 0.5, fy: float = 0.5, pan: float = 0.03,
              crop: str = "") -> None:
    """Foto -> klip vertikal 1080x1920 dengan gerak Ken Burns (zoom-in halus).

    fx/fy = titik fokus (0..1) untuk pemotongan; subjek bisa diperbesar
    tanpa kehilangan posisi aslinya. pan = pergeseran horizontal saat zoom.
    """
    outdir.mkdir(parents=True, exist_ok=True)
    dest = outdir / (name + ".mp4")
    W, H = 1080, 1920
    z0 = max(1.0, zoom)
    z1 = z0 * 1.12
    frames = int(dur * 30)
    # zoompan: z0 -> z1 linear, fokus pada (fx,fy) + pan halus
    # NOTE: ekspresi min() di zoompan bikin ffmpeg error -> pakai linear on/
    x_expr = f"iw*{fx}-(iw/zoom)/2+({pan}*iw*on/{max(1,frames-1)})"
    y_expr = f"ih*{fy}-(ih/zoom)/2"
    pre = f"crop={crop}," if crop else ""
    vf = (f"{pre}scale={W*2}:{H*2}:force_original_aspect_ratio=increase,"
          f"zoompan=z='{z0:.4f}+{z1-z0:.4f}*on/{max(1,frames-1)}':"
          f"d={frames}:x='{x_expr}':y='{y_expr}':s={W}x{H}:fps=30,"
          f"eq=contrast=1.06:saturation=1.05")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(src), "-t", str(dur),
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not dest.exists():
        print("GAGAL:", r.stderr[-400:]); return
    m = ffprobe(str(dest))
    print(json.dumps({"file": str(dest), "w": m.get("w"), "h": m.get("h"), "dur": m.get("dur"),
                      "bytes": m.get("bytes"), "from": "foto (Ken Burns)"}, ensure_ascii=False))


def cmd_cut(a):
    """Potong video sumber menjadi klip vertikal 1080x1920 siap Shorts.
    Jika sumber berupa FOTO (jpg/png), dibuat video Ken Burns 2,4 detik."""
    src = Path(a.file)
    if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
        return _kenburns(src, Path(a.out), a.name, a.duration, a.zoom,
                         getattr(a, "fx", 0.5), getattr(a, "fy", 0.5),
                         getattr(a, "pan", 0.03), getattr(a, "crop", "") or "")
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    dur = a.duration
    dest = outdir / (a.name + ".mp4")
    # filter: crop ke 9:16 dari tengah, scale, zoom-punch opsional, fps 30
    vf = (f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9):(iw-ow)/2:(ih-oh)/2,"
          f"scale=1080:1920:force_original_aspect_ratio=increase,"
          f"crop=1080:1920,fps=30,eq=contrast=1.06:saturation=1.05")
    if a.zoom > 1.0:
        vf = (f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9):(iw-ow)/2:(ih-oh)/2,"
              f"scale={int(1080*a.zoom)}:{int(1920*a.zoom)},"
              f"crop=1080:1920,fps=30,eq=contrast=1.06:saturation=1.05")
    cmd = ["ffmpeg", "-y", "-ss", str(a.start), "-t", str(dur), "-i", str(src),
           "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
           "-movflags", "+faststart", "-shortest", str(dest)]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if not dest.exists():
        print("GAGAL:", r.stderr[-800:]); return
    m = ffprobe(str(dest))
    print(json.dumps({"file": str(dest), "probe": m}, indent=2))


def cmd_batch(a):
    """Potong banyak klip dari banyak file sumber sesuai rencana timeline.

    --plan accepts JSON list: [{"src":"file.mp4","start":0.0,"dur":2.5,"name":"01-hook","shot":"hook"}, ...]
    --anchor: "start" = timestamp ts_verified di TENGAH klip (default), sehingga
               frame QC di detik 1.2 = frame yang sudah diverifikasi vision.
    """
    anchor = (a.anchor == "center")
    plan = json.loads(Path(a.plan).read_text(encoding="utf-8"))
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    rows = []
    for item in plan:
        src = Path(item["src"])
        if not src.exists():
            rows.append({"name": item["name"], "status": "MISSING_SRC", "src": str(src)}); continue
        dest = outdir / (item["name"] + ".mp4")
        dur = float(item.get("dur", 2.5)); st = float(item.get("start", 0.0))
        zoom = float(item.get("zoom", 1.05))
        if src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp"):
            # sumber FOTO -> klip vertikal dengan gerak Ken Burns
            _kenburns(src, outdir, item["name"], dur, zoom,
                      float(item.get("fx", 0.5)), float(item.get("fy", 0.5)),
                      float(item.get("pan", 0.03)), item.get("crop", ""))
            m = ffprobe(str(dest)) if dest.exists() else {}
            rows.append({"name": item["name"], "status": "OK" if dest.exists() else "FAIL",
                         "file": str(dest), "dur": m.get("dur"), "w": m.get("w"),
                         "h": m.get("h"), "bytes": m.get("bytes"),
                         "shot": item.get("shot", ""), "from": "foto-KenBurns"})
            print(f"  [{item['name']}] FOTO -> {m.get('w')}x{m.get('h')} {m.get('dur')}s (Ken Burns)")
            continue
        cx = float(item.get("cx", 0.5)); cy = float(item.get("cy", 0.5))
        vf = (f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9):(iw-ow)*{cx}:(ih-oh)*{cy},"
              f"scale={int(1080*zoom)}:{int(1920*zoom)},crop=1080:1920,"
              f"fps=30,eq=contrast=1.06:saturation=1.05")
        # anchor: timestamp menandai TENGAH klip, jadi frame QC (at=1.2) = timestamp itu
        ss = max(0.0, st - dur / 2) if anchor else st
        cmd = ["ffmpeg", "-y", "-ss", str(ss), "-t", str(dur), "-i", str(src),
               "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", "-shortest", str(dest)]
        subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if not dest.exists():
            rows.append({"name": item["name"], "status": "FAIL"}); continue
        m = ffprobe(str(dest))
        rows.append({"name": item["name"], "status": "OK", "file": str(dest),
                     "dur": m.get("dur"), "w": m.get("w"), "h": m.get("h"),
                     "bytes": m.get("bytes"), "shot": item.get("shot", "")})
        print(f"  [{item['name']}] {st:.1f}s +{dur:.1f}s -> {m.get('w')}x{m.get('h')} "
              f"{m.get('dur')}s {round(m.get('bytes',0)/1e6,2)}MB")
    print(json.dumps(rows, ensure_ascii=False, indent=2))



def cmd_cands(a):
    """Render kandidat klip vertikal + contact sheet berlabel.

    Menerapkan PERSIS filter yang dipakai cmd_batch (crop 9:16 + zoom), jadi
    frame sheet sama dengan hasil akhir — kandidat bisa langsung dipilih
    tanpa harus render ulang. Input: JSON list of
    {"src":..., "start":..., "name":..., "cx":0.5, "cy":0.5, "zoom":1.1, "dur":2.4}
    """
    import shutil
    items = json.loads(Path(a.cands).read_text(encoding="utf-8"))
    tmp = Path(a.tmp); shutil.rmtree(tmp, ignore_errors=True); tmp.mkdir(parents=True, exist_ok=True)
    labels, frames = [], []
    for it in items:
        src = Path(it["src"])
        if not src.exists():
            print("  MISSING:", src.name); continue
        dur = float(it.get("dur", 2.4)); st = float(it.get("start", 0.0))
        zoom = float(it.get("zoom", 1.10))
        cx = float(it.get("cx", 0.5)); cy = float(it.get("cy", 0.5))
        name = it.get("name", "c")
        is_photo = src.suffix.lower() in (".jpg", ".jpeg", ".png", ".webp")
        dest = tmp / (name + ".mp4")
        if is_photo:
            _kenburns(src, tmp, name, dur, zoom, cx, cy, 0.03, it.get("crop", ""))
        else:
            vf = (f"crop=min(iw\\,ih*9/16):min(ih\\,iw*16/9):(iw-ow)*{cx}:(ih-oh)*{cy},"
                  f"scale={int(1080*zoom)}:{int(1920*zoom)},crop=1080:1920,"
                  f"fps=30,eq=contrast=1.06:saturation=1.05")
            ss = max(0.0, st - dur / 2) if a.anchor == "center" else st
            subprocess.run(["ffmpeg", "-y", "-ss", str(ss), "-t", str(dur), "-i", str(src),
                            "-vf", vf, "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                            "-pix_fmt", "yuv420p", "-an", str(dest)],
                           capture_output=True, text=True, timeout=300)
        if not dest.exists():
            print("  FAIL:", name); continue
        fr = tmp / (name + ".jpg")
        lab = it.get("label", name)
        subprocess.run(["ffmpeg", "-y", "-ss", str(a.at), "-i", str(dest), "-frames:v", "1",
                        "-vf", f"scale={a.w}:{a.h},drawtext=text='{lab}':fontcolor=yellow:"
                               f"fontsize=32:x=6:y=6:box=1:boxcolor=black@0.75",
                        "-update", "1", "-q:v", "4", str(fr)], capture_output=True, timeout=60)
        if fr.exists():
            frames.append(fr); labels.append(lab)
    # sheet: rows x cols, pad baris terakhir
    cols = a.cols
    rows = []
    for i in range(0, len(frames), cols):
        grp = list(frames[i:i + cols])
        while len(grp) < cols:
            pp = tmp / f"_pad{i}_{len(grp)}.png"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            f"color=c=black:s={a.w}:{a.h}:d=1", "-frames:v", "1",
                            "-update", "1", str(pp)], capture_output=True, timeout=30)
            grp.append(pp)
        ro = tmp / f"_row{i // cols}.png"
        cmd = ["ffmpeg", "-y"]
        for g in grp:
            cmd += ["-i", str(g)]
        fc = "".join(f"[{j}:v]" for j in range(len(grp))) + f"hstack=inputs={len(grp)}[row]"
        cmd += ["-filter_complex", fc, "-map", "[row]", "-frames:v", "1", "-update", "1", str(ro)]
        subprocess.run(cmd, capture_output=True, timeout=120)
        if ro.exists():
            rows.append(ro)
    out = Path(a.out)
    if len(rows) == 1:
        subprocess.run(["ffmpeg", "-y", "-i", str(rows[0]), "-frames:v", "1", "-update", "1",
                        "-q:v", "4", str(out)], capture_output=True, timeout=90)
    else:
        cmd = ["ffmpeg", "-y"]
        for rr in rows:
            cmd += ["-i", str(rr)]
        fc = "".join(f"[{j}:v]" for j in range(len(rows))) + f"vstack=inputs={len(rows)}[out]"
        cmd += ["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-update", "1",
                "-q:v", "4", str(out)]
        subprocess.run(cmd, capture_output=True, timeout=120)
    print(json.dumps({"out": str(out), "exists": out.exists(), "n": len(frames),
                      "labels": labels}, ensure_ascii=False))


def cmd_sheet(a):
    """Buat contact sheet dari klip untuk QC visual (agent vision).
    Baris terakhir dipad dengan frame hitam agar semua frame muncul."""
    klipdir = Path(a.dir)
    files = sorted(klipdir.glob("*.mp4"))
    if not files:
        print("TIDAK ADA KLIP.")
        return
    tmp = Path(a.tmp) if a.tmp else Path(a.out).parent / "_qc_tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    frames = []
    for f in files:
        out = tmp / f"fr_{f.stem}.jpg"
        subprocess.run(["ffmpeg", "-y", "-ss", str(a.at), "-i", str(f), "-frames:v", "1",
                        "-vf", f"scale={a.w}:{a.h}", "-update", "1", str(out)],
                       capture_output=True, timeout=60)
        if out.exists():
            frames.append(out)
    if not frames:
        print("GAGAL: tidak ada frame.")
        return
    # pad file terakhir supaya kolom penuh
    rows = []
    for i in range(0, len(frames), a.cols):
        grp = list(frames[i:i + a.cols])
        rowout = tmp / f"_row{i // a.cols}.png"
        while len(grp) < a.cols:
            padf = tmp / f"_pad_{i}_{len(grp)}.png"
            subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                            f"color=c=black:s={a.w}x{a.h}:d=1", "-frames:v", "1",
                            "-update", "1", str(padf)], capture_output=True, timeout=60)
            if padf.exists():
                grp.append(padf)
            else:
                break
        cmd = ["ffmpeg", "-y"]
        for g in grp:
            cmd += ["-i", str(g)]
        fc = "".join(f"[{j}:v]" for j in range(len(grp))) + f"hstack=inputs={len(grp)}[row]"
        cmd += ["-filter_complex", fc, "-map", "[row]", "-frames:v", "1",
                "-update", "1", str(rowout)]
        subprocess.run(cmd, capture_output=True, timeout=60)
        if rowout.exists():
            rows.append(rowout)
        else:
            print("  row gagal:", i)
    if not rows:
        print("GAGAL sheet.")
        return
    cmd = ["ffmpeg", "-y"]
    for rr in rows:
        cmd += ["-i", str(rr)]
    fc = "".join(f"[{j}:v]" for j in range(len(rows))) + f"vstack=inputs={len(rows)}[out]"
    cmd += ["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-update", "1", str(a.out)]
    subprocess.run(cmd, capture_output=True, timeout=60)
    ok = Path(a.out).exists()
    print(json.dumps({"sheet": a.out, "exists": ok, "n": len(frames),
                      "frames": [f.name for f in files],
                      "order": "kiri-ke-kanan, atas-ke-bawah (1-N)"}, ensure_ascii=False, indent=2))


def cmd_credit(a):
    """Buat CREDITS.md dari _metadata.json + kredit siap tempel ke deskripsi."""
    base = Path(a.base)
    assets = base / "assets"
    side = assets / "_metadata.json"
    if not side.exists():
        print("TIDAK ADA _metadata.json di", assets); return
    meta = json.loads(side.read_text(encoding="utf-8"))
    plan_p = base / "plan-klip.json"
    used = []
    if plan_p.exists():
        plan = json.loads(plan_p.read_text(encoding="utf-8"))
        for it in plan:
            used.append(Path(it["src"]).name)
    order = [u for u in used if u in meta] + [k for k in meta if k not in used]
    lines = ["# Kredit & Atribusi Materi", "",
             f"Sumber: {side}", "",
             "Tempel blok di bawah ke deskripsi YouTube. Materi berlisensi CC **wajib** "
             "mengikuti syarat lisensinya (atribusi, share-alike, tanpa NC/ND).",
             "Materi tanpa lisensi = all rights reserved: jangan publish tanpa izin kreator.", "",
             "| # | File | Judul | Kreator | Lisensi | Risiko | URL |",
             "|---|------|-------|---------|---------|-------|-----|"]
    for i, k in enumerate(order, 1):
        m = meta[k]
        lines.append(f"| {i} | `{k}` | {str(m.get('title') or '')[:60]} | {str(m.get('creator') or '')[:30]} "
                     f"| {str(m.get('license') or 'TIDAK ADA')[:30]} | {str(m.get('license_risk') or '')[:26]} "
                     f"| {m.get('page') or m.get('url') or ''} |")
    lines += ["", "## Blok kredit siap tempel", ""]
    for k in order:
        lines.append(credit_line(meta[k], k))
    n_reuse = sum(1 for k in order if is_cc(meta[k].get("license", "")))
    lines += ["", "## Ringkasan", "",
              f"- Total materi terpakai: {len(order)}",
              f"- Berlisensi CC (reuse-safe dengan syarat): {n_reuse}",
              f"- All rights reserved / tanpa lisensi: {len(order) - n_reuse} "
              f"-> wajib ganti dengan materi CC atau minta izin",
              ""]
    out = base / "CREDITS.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"out": str(out), "total": len(order), "cc": n_reuse,
                      "all_rights": len(order) - n_reuse}, ensure_ascii=False, indent=2))



def cmd_cats(a):
    """Daftar file Commons per kategori taksom (video/foto) + lisensi.

    Lebih andal daripada search: kategori = inventaris terverifikasi spesies.
    """
    raw = a.taxon if a.taxon else ", ".join(taxon_for(a.topic))
    cats = [c.strip() for c in raw.split(",") if c.strip()]
    if not cats:
        print("TIDAK ADA TAKSON untuk topic ini. Pakai --taxon manual.")
        return
    out = _cats_list(cats, a.limit)
    if not out:
        print("TIDAK ADA FILE di kategori:", cats)
        return
    out.sort(key=lambda r: (r["media"], r["title"]))
    print(f"# Commons kategori: {cats}\n")
    print(f"{'#':>2} {'JENIS':<6} {'LISENSI':<16} {'REUSE':<6} {'RESO':<12} KATEGORI | FILE")
    print("-" * 130)
    for i, r in enumerate(out, 1):
        print(f"{i:>2} {r['media']:<6} {str(r['license'])[:15]:<16} "
              f"{('YA' if r['reusable'] else 'bukan'):<6} {str(r['w'])+'x'+str(r['h']):<12} {r['cat'][:26]:<27} {r['title'][:52]}")
    print(f"\nTotal {len(out)} file. CC reuse-safe: {sum(1 for r in out if r['reusable'])}")
    print(json.dumps(out, ensure_ascii=False, indent=2))


CAT_NOISE = ("tooth", "teeth", "skull", "skeleton", "fossil", "1891", "bhl",
             "white background", "area.png", "logo", "range", "distribution",
             "map", "illustration", "drawing", "painting", "engraving",
             "cambridge natural history", "illegal", "illicit", "market",
             "confiscat", "zoo signage", "cage", "trap", "rescue centre",
             "stamp", "coin", "plaque", "museum specimen", "taxidermy")


def cmd_catfetch(a):
    """Unduh aset langsung dari kategori Commons (inventaris terverifikasi).

   lebih andal daripada search: kategori hanya berisi file spesies itu.
    """
    raw = a.taxon if a.taxon else ", ".join(taxon_for(a.topic))
    cats = [c.strip() for c in raw.split(",") if c.strip()]
    # ambil inventaris jauh lebih banyak, lalu potong setelah filter
    items = _cats_list(cats, max(60, a.limit * 12))
    kept = []
    for r in items:
        low = r["title"].lower()
        if any(b in low for b in CAT_NOISE):
            continue
        if r["media"] == "foto" and (r["w"] < a.min_height or r["h"] < a.min_height):
            continue
        if a.reuse_only and not r["reusable"]:
            continue
        kept.append(r)
    kept.sort(key=lambda r: (-(r["w"] * r["h"]), r["title"]))
    # rotasi kreator: maksimal 1 per kreator supaya visual beragam
    seen_c, rot = {}, []
    for r in kept:
        c = (r.get("creator") or "NA").strip().lower()
        if seen_c.get(c, 0) >= a.max_per_creator:
            continue
        seen_c[c] = seen_c.get(c, 0) + 1
        rot.append(r)
    kept = rot[:a.limit]
    outdir = Path(a.out); outdir.mkdir(parents=True, exist_ok=True)
    done = []
    for i, r in enumerate(kept, 1):
        ext = Path(urllib.parse.urlparse(r["url"]).path).suffix.lower()
        if ext not in (".webm", ".ogv", ".mp4", ".mov", ".jpg", ".jpeg", ".png"):
            ext = ".mp4" if r["media"] == "video" else ".jpg"
        dest = outdir / (f"{i:02d}-" + re.sub(r"[^a-z0-9]+", "-", r["title"].lower())[:40].strip("-") + ext)
        ok, err = _curl_download(r["url"], dest, UA_WEB)
        if not ok:
            dest.unlink(missing_ok=True)
            print(f"  [{i}] GAGAL {r['title'][:40]} {err}"); continue
        probe = ffprobe(str(dest))
        if probe.get("dur", 0) == 0 and probe.get("h", 0) < a.min_height:
            dest.unlink(missing_ok=True); print(f"  [{i}] BUANG (resolusi kecil) {r['title'][:40]}"); continue
        print(f"  [{i}] OK  {dest.name}  {probe.get('w')}x{probe.get('h')} "
              f"{probe.get('dur')}s  {r['license']}  by {r['creator'][:24]}")
        done.append({**r, "file": str(dest), "probe": probe, "download": "OK"})
    # metadata sidecar
    if done:
        side = outdir / "_metadata.json"
        old_meta = json.loads(side.read_text(encoding="utf-8")) if side.exists() else {}
        for d in done:
            pr = d.get("probe", {})
            fn = Path(d["file"]).name
            old_meta[fn] = {
                "source": "Wikimedia Commons" + (" (foto)" if d["media"] == "foto" else ""),
                "url": d["url"], "page": d["page"], "title": d["title"],
                "creator": d["creator"], "license": d["license"],
                "license_risk": d["license_risk"], "license_url": "",
                "attribution": d["creator"], "upload_date": None, "views": None,
                "duration": pr.get("dur"), "width": pr.get("w"), "height": pr.get("h"),
                "aspect": (round(pr.get("w", 0) / pr.get("h", 1), 3) if pr.get("h") else None),
                "fps": pr.get("fps"), "vcodec": pr.get("vcodec"), "acodec": pr.get("acodec"),
                "bytes": pr.get("bytes"), "mime": pr.get("mime"), "media": d["media"],
                "sha256": sha256_file(d["file"]),
                "description": "", "taxon": "", "fetched_at": time.strftime("%Y-%m-%d %H:%M"),
                "shot_hint": "", "reusable": is_reusable(d["license"]),
                "category": d.get("cat", ""),
            }
        side.write_text(json.dumps(old_meta, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n-> {len(done)} aset | sidecar: {side}")
    print(json.dumps([{k: d[k] for k in ("title", "url", "creator", "license", "file")} for d in done],
                     ensure_ascii=False, indent=2))


def _cats_list(cats: list[str], limit: int) -> list[dict]:
    """Baris kategori + metadata lisensi (fungsi bersama cats & catfetch)."""
    rows = []
    for cat in cats:
        cname = cat if cat.lower().startswith("category:") else "Category:" + cat
        u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
             "&list=categorymembers&cmtitle=" + urllib.parse.quote(cname) +
             "&cmtype=file|subcat&cmlimit=" + str(limit))
        d = http_json(u)
        for m in (d.get("query") or {}).get("categorymembers") or []:
            ttl = m.get("title", "")
            if not ttl.lower().endswith((".webm", ".ogv", ".mp4", ".mov", ".m4v",
                                          ".jpg", ".jpeg", ".png")):
                continue
            rows.append((cat, ttl))
    cat_of = {ttl: cat for cat, ttl in rows}
    out = []
    for i in range(0, len(rows), 20):
        chunk = rows[i:i + 20]
        tq = urllib.parse.quote("|".join(tt for _, tt in chunk))
        u = ("https://commons.wikimedia.org/w/api.php?action=query&format=json"
             "&prop=imageinfo&iiprop=url|size|mime|extmetadata&titles=" + tq)
        d = http_json(u)
        for p in d.get("query", {}).get("pages", {}).values():
            ii = (p.get("imageinfo") or [{}])[0]
            if not ii.get("url"):
                continue
            em = ii.get("extmetadata", {}) or {}
            g = lambda k: (em.get(k, {}) or {}).get("value", "")
            lic = g("LicenseShortName") or "NA"
            artist = re.sub(r"<[^>]+>", "", g("Artist") or "").strip()
            name = p.get("title", "").replace("File:", "")
            out.append({
                "cat": cat_of.get(p.get("title", ""), ""), "title": name,
                "url": ii["url"].split("?")[0], "creator": artist or "NA",
                "license": lic, "license_risk": license_risk(lic)[1],
                "w": ii.get("width") or 0, "h": ii.get("height") or 0,
                "mime": ii.get("mime") or "",
                "media": "video" if (ii.get("mime") or "").startswith("video") else "foto",
                "page": f"https://commons.wikimedia.org/wiki/{urllib.parse.quote(p.get('title',''))}",
                "reusable": is_reusable(lic),
            })
    return out



def _vid_metrics(f: Path) -> dict:
    """Metrik QC per video. Nilai ambang sudah dikalibrasi dengan ffmpeg 9 (blurdetect).

   blur_mean naik = makin blur (tajam ~6, blur ~24). edge_YMAX = proxy detail.
    """
    def stats(vf: str) -> dict:
        r = subprocess.run(["ffmpeg", "-v", "error", "-i", str(f), "-vf",
                            vf + ",metadata=print:file=-", "-f", "null", "-"],
                           capture_output=True, text=True, timeout=120)
        d = {}
        for line in r.stdout.splitlines():
            if "=" in line and "pts" not in line:
                k, v = line.split("=", 1)
                try:
                    d.setdefault(k, []).append(float(v))
                except Exception:
                    pass
        return {k.split(".")[-1]: round(sum(v) / len(v), 3) for k, v in d.items()
                if k.split(".")[-1] in ("YAVG", "YDIF", "YMIN", "YMAX", "SATAVG")}

    rb = subprocess.run(["ffmpeg", "-i", str(f), "-vf", "blurdetect", "-f", "null", "-"],
                        capture_output=True, text=True, timeout=120)
    blurs = [float(l.split("blur mean:")[1].split()[0]) for l in rb.stderr.splitlines()
             if "blur mean:" in l]
    base = stats("signalstats")
    edge = stats("edgedetect=mode=colormix,signalstats")
    probe = ffprobe(str(f))
    return {"dur": probe.get("dur") or 0, "w": probe.get("w"), "h": probe.get("h"),
            "blur_mean": round(sum(blurs) / len(blurs), 2) if blurs else None,
            "yavg": base.get("YAVG", 0), "ydif": base.get("YDIF", 0),
            "satavg": base.get("SATAVG", 0),
            "edge_ymax": edge.get("YMAX", 0), "edge_yavg": edge.get("YAVG", 0)}


def cmd_autoqc(a):
    """QC otomatis tanpa vision: ketajaman, brightness, detail, freeze, durasi.

    Men Complement vision: menyaring klip gelap/suram/blur/beku sebelum sheet.
    Ambang tervalidasi ffmpeg 9: blur_mean > 12 = blur, < 3 = terlalu tajam/noise.
    """
    d = Path(a.dir)
    files = sorted(d.glob("*.mp4"))
    rows = []
    for f in files:
        m = _vid_metrics(f)
        flags = []
        bm = m["blur_mean"]
        warn = []
        if bm is None:
            flags.append("blur tak terbaca")
        elif bm > 18:
            flags.append(f"blur({bm})")
        elif bm > 12:
            warn.append(f"agak blur({bm})")
        elif bm < 2.5:
            flags.append(f"noise/oversharp({bm})")
        if m["yavg"] < 30:
            flags.append(f"gelap({m['yavg']})")
        elif m["yavg"] > 240:
            flags.append(f"terlalu terang({m['yavg']})")
        if m["ydif"] < 0.15 and m["dur"] > 1:
            flags.append("beku/freeze")
        if m["edge_ymax"] < 130:
            flags.append(f"detail rendah({m['edge_ymax']})")
        elif m["edge_ymax"] < 180:
            warn.append(f"detail agak rendah({m['edge_ymax']})")
        if m["satavg"] < 3:
            flags.append(f"warna washed({m['satavg']})")
        if m["dur"] < 1.6:
            flags.append(f"durasi pendek({m['dur']}s)")
        if m["w"] != 1080 or m["h"] != 1920:
            flags.append(f"reso {m['w']}x{m['h']}")
        rows.append({**m, "file": f.name, "flags": flags, "warn": warn,
                     "verdict": "GAGAL" if flags else ("PERHATIAN" if warn else "OK")})
    if a.out:
        outp = Path(a.out)
        outp.parent.mkdir(parents=True, exist_ok=True)
        lines = ["| File | Dur | Reso | Blur | YAVG | YDIF | Edge | Sat | Vonis | Catatan |",
                 "|------|-----|------|------|------|------|------|-----|-------|---------|"]
        for r in rows:
            cat = ", ".join(r["flags"] + r.get("warn", []))
            lines.append(f"| {r['file']} | {r['dur']}s | {r['w']}x{r['h']} | {r['blur_mean']} | "
                         f"{r['yavg']} | {r['ydif']} | {r['edge_ymax']} | {r['satavg']} | "
                         f"{r['verdict']} | {cat} |")
        outp.write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"-> {outp}")
    ok = [r for r in rows if r["verdict"] == "OK"]
    per = [r for r in rows if r["verdict"] == "PERHATIAN"]
    print(f"{len(ok)}/{len(rows)} klip OK | {len(per)} perlu perhatian | "
          f"{len(rows)-len(ok)-len(per)} GAGAL")
    for r in rows:
        if r["verdict"] != "OK":
            print(f"  {r['verdict']} {r['file']}: {', '.join(r['flags'] + r.get('warn', []))}")
    print(json.dumps(rows, ensure_ascii=False, indent=2))


def cmd_probe(a):
    m = ffprobe(a.file)
    print(json.dumps(m, indent=2))

def main():
    ap = argparse.ArgumentParser(description="dokli.py — cari & unduh doksli untuk Shorts")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search"); s.add_argument("--topic", required=True); s.add_argument("--limit", type=int, default=12)
    s.add_argument("--extra", default="")
    s.add_argument("--license", default="any", choices=["any", "cc", "cc-preferred"])
    s.set_defaults(func=cmd_search)

    f = sub.add_parser("fetch"); f.add_argument("--topic", required=True); f.add_argument("--out", required=True)
    f.add_argument("--limit", type=int, default=6); f.add_argument("--min-score", type=float, default=20)
    f.add_argument("--max-height", type=int, default=1080); f.add_argument("--shots", default="")
    f.add_argument("--brief", default="", help="brief-edit.md untuk pemetaan slot narasi")
    f.add_argument("--sources", default=""); f.add_argument("--extra", default="")
    f.add_argument("--max-risk", action="store_true", help="buang kandidat lisensi risiko TINGGI")
    f.add_argument("--per-source", type=int, default=6, help="maks kandidat per sumber")
    f.add_argument("--min-height", type=int, default=720, help="resolusi tinggi minimal (0=biarkan)")
    f.add_argument("--max-per-creator", type=int, default=1, help="maks kandidat per kreator (rotasi)")
    f.add_argument("--license", default="any", choices=["any", "cc", "cc-preferred"],
                   help="cc = hanya Creative Commons (reuse-safe); cc-preferred = CC diurutkan depan")
    f.set_defaults(func=cmd_fetch)

    cx = sub.add_parser("ctx")
    cx.add_argument("--topic", required=True)
    cx.add_argument("--brief", required=True)
    cx.add_argument("--limit", type=int, default=40)
    cx.add_argument("--extra", default="")
    cx.add_argument("--license", default="any", choices=["any", "cc", "cc-preferred"])
    cx.add_argument("--reuse-only", action="store_true")
    cx.set_defaults(func=cmd_ctx)

    c = sub.add_parser("cut"); c.add_argument("--file", required=True); c.add_argument("--out", required=True)
    c.add_argument("--name", required=True); c.add_argument("--start", type=float, default=0.0)
    c.add_argument("--duration", type=float, default=2.5); c.add_argument("--zoom", type=float, default=1.05)
    c.add_argument("--fx", type=float, default=0.5, help="titik fokus X (0..1) untuk foto")
    c.add_argument("--fy", type=float, default=0.5, help="titik fokus Y (0..1) untuk foto")
    c.add_argument("--pan", type=float, default=0.03, help="pergeseran horizontal (foto)")
    c.add_argument("--crop", default="", help="crop piksel asli untuk foto, mis. 900:1500:1700:0")
    c.set_defaults(func=cmd_cut)

    b = sub.add_parser("batch"); b.add_argument("--plan", required=True); b.add_argument("--out", required=True)
    sub.choices["fetch"].add_argument("--allow-ncnd", action="store_true",
                        help="izinkan CC-NC/ND (JANGAN untuk channel monetized)")
    sub.choices["fetch"].add_argument("--exclude-source", default="", help="comma daftar sumber dibuang")
    sub.choices["search"].add_argument("--reuse-only", action="store_true",
                        help="hanya CC yang boleh komersial (buang NC/ND)")
    sub.choices["search"].add_argument("--exclude-source", default="", help="comma daftar sumber dibuang")
    cf = sub.add_parser("catfetch")
    cf.add_argument("--topic", default="")
    cf.add_argument("--taxon", default="", help="pisah dengan koma")
    cf.add_argument("--out", required=True)
    cf.add_argument("--limit", type=int, default=10)
    cf.add_argument("--min-height", type=int, default=900)
    cf.add_argument("--reuse-only", action="store_true")
    cf.add_argument("--max-per-creator", type=int, default=1)
    cf.set_defaults(func=cmd_catfetch)
    ca = sub.add_parser("cats")
    ca.add_argument("--topic", default="")
    ca.add_argument("--taxon", default="", help="pisah dengan koma; mis. 'Nycticebus coucang'")
    ca.add_argument("--limit", type=int, default=200)
    ca.set_defaults(func=cmd_cats)
    aq = sub.add_parser("autoqc")
    aq.add_argument("--dir", required=True)
    aq.add_argument("--out", default="")
    aq.set_defaults(func=cmd_autoqc)
    cr = sub.add_parser("credit"); cr.add_argument("--base", required=True)
    cr.set_defaults(func=cmd_credit)
    k = sub.add_parser("cands")
    k.add_argument("--cands", required=True, help="JSON list kandidat")
    k.add_argument("--out", required=True, help="contact sheet jpg")
    k.add_argument("--tmp", required=True)
    k.add_argument("--at", type=float, default=1.2)
    k.add_argument("--w", type=int, default=540); k.add_argument("--h", type=int, default=960)
    k.add_argument("--cols", type=int, default=5)
    k.add_argument("--anchor", default="center", choices=["center", "start"])
    k.set_defaults(func=cmd_cands)
    b.add_argument("--anchor", default="center", choices=["center", "start"])
    m = sub.add_parser("manifest"); m.add_argument("--base", required=True)
    m.add_argument("--topic", default=""); m.add_argument("--limit", type=int, default=40)
    m.add_argument("--extra", default="")
    m.set_defaults(func=cmd_manifest)
    rm = sub.add_parser("remeta"); rm.add_argument("--dir", required=True)
    rm.add_argument("--n", type=int, default=5); rm.set_defaults(func=cmd_remeta)
    b.set_defaults(func=cmd_batch)

    sh = sub.add_parser("sheet"); sh.add_argument("--dir", required=True); sh.add_argument("--out", required=True)
    sh.add_argument("--tmp", default=""); sh.add_argument("--cols", type=int, default=3)
    sh.add_argument("--w", type=int, default=270); sh.add_argument("--h", type=int, default=480)
    sh.add_argument("--at", type=float, default=1.0); sh.set_defaults(func=cmd_sheet)

    p = sub.add_parser("probe"); p.add_argument("file"); p.set_defaults(func=cmd_probe)
    a = ap.parse_args(); a.func(a)

if __name__ == "__main__":
    main()
