"""Visual Intent & Kinematic 3-Tier Query Planner (Sourcing & Vision v4.0).

Decomposes Indonesian VO scripts / edit briefs into Scene Beats (U1..UN) and converts each
beat into:
  1. `literal_ugc`: natural specific queries for YouTube Shorts / TikTok / Reddit
  2. `conceptual_stock`: short 2-4 word physical kinematic queries for Pexels / Pixabay / Wikimedia
     (strictly stripped of non-visual causal clauses such as "habis buang air / after poop")
  3. `metaphorical_broll`: anatomy / reaction / contrast fallback B-roll queries
  + `negative_terms`: wrong-action blockers (e.g. ["eating", "rolling", "sleeping"])
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Literal, Optional, Tuple

import openai
from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config, resolve_ai_model


class VisualIntent(BaseModel):
    subject: str = "animal"
    subject_id: str = "hewan"
    kinematic_action: str = ""
    target_object: str = ""
    emotion_or_energy: str = "curious"
    recommended_shot: Literal["close_up", "medium_full", "low_angle", "wide"] = "medium_full"
    emphasis_tokens: List[str] = Field(default_factory=list)
    stripped_clauses: List[str] = Field(default_factory=list)
    negative_terms: List[str] = Field(default_factory=list)


class TieredQueries(BaseModel):
    literal_ugc: List[str] = Field(default_factory=list)
    conceptual_stock: List[str] = Field(default_factory=list)
    metaphorical_broll: List[str] = Field(default_factory=list)


class BeatSourcingPlan(BaseModel):
    beat_id: str
    beat_index: int
    role: Literal["hook", "contrast", "cta", "proof", "process", "payoff"] = "process"
    vo_line: str
    visual_intent: VisualIntent
    queries: TieredQueries
    recommended_providers: Dict[str, List[str]] = Field(default_factory=dict)


class EpisodeQueryPlan(BaseModel):
    planner_mode: Literal["llm_structured", "kinematic_ontology"] = "kinematic_ontology"
    planner_model: str = ""
    primary_subject: str = "animal"
    primary_intent: VisualIntent = Field(default_factory=VisualIntent)
    queries: TieredQueries = Field(default_factory=TieredQueries)
    negative_terms: List[str] = Field(default_factory=list)
    beats: List[BeatSourcingPlan] = Field(default_factory=list)
    recommended_queries: List[str] = Field(default_factory=list)


# ---------------- Deterministic Kinematic Ontology (Zero-Pollution Baseline) ----------------
SUBJECT_MAP: Dict[str, Tuple[str, List[str]]] = {
    "kuda laut": ("seahorse", ["seahorse"]),
    "anak bebek": ("duckling", ["duckling", "baby duck"]),
    "kucing": ("cat", ["cat", "kitten", "feline"]),
    "anjing": ("dog", ["dog", "puppy", "canine"]),
    "bebek": ("duck", ["duck", "duckling", "mallard"]),
    "ayam": ("chicken", ["chicken", "rooster", "hen"]),
    "sapi": ("cow", ["cow", "cattle", "calf"]),
    "cicak": ("gecko", ["house gecko", "gecko", "lizard"]),
    "gurita": ("octopus", ["octopus", "cephalopod"]),
    "axolotl": ("axolotl", ["axolotl", "salamander"]),
    "kukang": ("slow loris", ["slow loris"]),
    "burung": ("bird", ["bird", "songbird"]),
    "kelinci": ("rabbit", ["rabbit", "bunny"]),
    "ular": ("snake", ["snake", "reptile"]),
    "semut": ("ant", ["ants", "ant colony"]),
    "ikan": ("fish", ["fish", "underwater fish"]),
    "serigala": ("wolf", ["wolf"]),
    "gajah": ("elephant", ["elephant"]),
    "lumba-lumba": ("dolphin", ["dolphin"]),
    "beruang": ("bear", ["bear"]),
    "katak": ("frog", ["frog"]),
    "kura-kura": ("turtle", ["turtle", "tortoise"]),
    "bunglon": ("chameleon", ["chameleon"]),
}

# Non-visual temporal/causal clauses that MUST be pruned before querying stock APIs
NON_VISUAL_CLAUSES_ID = [
    r"\bhabis\s+buang\s+air(?:\s+besar|\s+kecil)?\b",
    r"\bsetelah\s+buang\s+air(?:\s+besar|\s+kecil)?\b",
    r"\bhabis\s+(?:pup|pipis|bab|kencing)\b",
    r"\bsetelah\s+(?:pup|pipis|bab|kencing)\b",
    r"\bpada\s+malam\s+hari\s+sebelum\b",
    r"\bbanyak\s+orang\s+(?:pikir|kira|sangka)\b",
    r"\bpadahal\s+sebenarnya\b",
    r"\bkalau\s+kamu\s+sayang\b",
    r"\bdaripada\s+kasur\s+mahal\b",
]

# Maps Indonesian behavioral/object stems -> (kinematic_stock_phrases, literal_ugc_phrases, metaphor_broll_phrases, negative_terms, shot)
KINEMATIC_ONTOLOGY: Dict[str, dict] = {
    "kardus": {
        "action": "squeezing and hiding inside small cardboard box",
        "object": "cardboard box",
        "stock": ["cat in cardboard box", "cat jumping into box", "cat sitting small box"],
        "ugc": ["cat squeezing into tiny box", "cat chooses box over expensive bed", "cat hiding in cardboard box"],
        "metaphor": ["cat peeking from box close up", "cozy cat curled up box"],
        "negatives": ["eating", "litter box", "bathing", "vet", "cartoon", "outdoor street"],
        "shot": "medium_full",
    },
    "kotak": {
        "action": "squeezing inside small cardboard box",
        "object": "cardboard box",
        "stock": ["cat in cardboard box", "cat sitting small box", "cat jumping into box"],
        "ugc": ["cat squeezing into tight box", "cat sitting in cardboard box"],
        "metaphor": ["cat peeking out box", "cat eyes wide hiding"],
        "negatives": ["eating", "litter box", "bathing", "cartoon"],
        "shot": "medium_full",
    },
    "dengkur": {
        "action": "relaxed cat purring softly with gentle vibration",
        "object": "vibrating throat fur",
        "stock": ["cat purring close up", "happy cat sleeping owner lap", "cat kneading blanket"],
        "ugc": ["cat loud purring sound close up", "cat purring healing vibration"],
        "metaphor": ["cat sleeping peacefully macro", "sound wave vibration therapy"],
        "negatives": ["hissing", "fighting", "angry", "vet injection", "cartoon"],
        "shot": "close_up",
    },
    "purr": {
        "action": "relaxed cat purring softly with gentle vibration",
        "object": "vibrating throat fur",
        "stock": ["cat purring close up", "happy cat resting lap", "cat kneading blanket"],
        "ugc": ["cat loud purring close up", "cat purring healing bones"],
        "metaphor": ["cat whiskers macro calm", "cat closing eyes relaxed"],
        "negatives": ["hissing", "fighting", "angry", "cartoon"],
        "shot": "close_up",
    },
    "berbaris": {
        "action": "ducklings swimming in a neat single-file line behind mother",
        "object": "pond water wake",
        "stock": ["ducklings swimming line", "mother duck babies pond", "baby ducks following mother"],
        "ugc": ["ducklings swimming single file behind mother", "baby ducks lined up water"],
        "metaphor": ["water ripples wake close up", "cute duckling paddling close up"],
        "negatives": ["cooking", "roasted", "hunting", "cartoon"],
        "shot": "wide",
    },
    "gurita": {
        "action": "octopus gliding underwater flexing tentacles",
        "object": "underwater tentacles",
        "stock": ["octopus swimming underwater", "octopus tentacles close up", "octopus ocean reef"],
        "ugc": ["octopus moving camouflage underwater", "octopus wild ocean footage"],
        "metaphor": ["ocean coral reef deep blue", "octopus eye macro underwater"],
        "negatives": ["cooking", "sushi", "grilled", "market", "dead"],
        "shot": "medium_full",
    },
    "axolotl": {
        "action": "axolotl swimming gently with pink external gills",
        "object": "pink gills aquarium",
        "stock": ["axolotl swimming water", "axolotl close up face", "pink axolotl gills"],
        "ugc": ["axolotl regeneration gills close up", "cute axolotl underwater"],
        "metaphor": ["microscopic cells regenerating", "axolotl smile macro"],
        "negatives": ["cartoon", "plush", "toy", "minecraft"],
        "shot": "close_up",
    },
    "kuda laut": {
        "action": "male seahorse floating underwater with swollen brood pouch",
        "object": "seahorse brood pouch",
        "stock": ["seahorse swimming underwater", "pregnant male seahorse", "seahorse macro ocean"],
        "ugc": ["male seahorse giving birth babies", "seahorse brood pouch underwater"],
        "metaphor": ["tiny baby seahorses swimming", "coral seagrass ocean"],
        "negatives": ["cartoon", "dried", "drawing", "tattoo"],
        "shot": "close_up",
    },
    "kukang": {
        "action": "slow loris climbing branch slowly with wide nocturnal eyes",
        "object": "tree branch night",
        "stock": ["slow loris tree branch", "slow loris close up eyes", "nocturnal primate forest"],
        "ugc": ["slow loris raising arms defense", "slow loris wild jungle"],
        "metaphor": ["slow loris big eyes macro", "tropical forest canopy night"],
        "negatives": ["cartoon", "pet abuse", "cage"],
        "shot": "close_up",
    },
    "nendang": {
        "action": "hind legs kicking dirt backward",
        "object": "dirt soil grass",
        "stock": ["digging dirt", "paw scratching ground", "dirt flying paws"],
        "ugc": ["kicking dirt back legs", "scratching grass with hind legs", "kicking ground paws"],
        "metaphor": ["paw pads close up", "sniffing grass turf"],
        "negatives": ["eating", "chewing", "rolling", "sleeping", "pooping", "defecating"],
        "shot": "low_angle",
    },
    "tendang": {
        "action": "hind legs kicking dirt backward",
        "object": "dirt soil grass",
        "stock": ["digging dirt", "paw scratching ground", "dirt flying paws"],
        "ugc": ["kicking dirt back legs", "scratching grass with hind legs", "kicking ground paws"],
        "metaphor": ["paw pads close up", "sniffing grass turf"],
        "negatives": ["eating", "chewing", "rolling", "sleeping", "pooping", "defecating"],
        "shot": "low_angle",
    },
    "garuk": {
        "action": "paws scratching surface",
        "object": "dirt ground",
        "stock": ["paw scratching ground", "digging soil close up"],
        "ugc": ["scratching dirt with paws", "digging grass furiously"],
        "metaphor": ["claws paws macro", "scratched turf"],
        "negatives": ["sleeping", "eating", "sitting still"],
        "shot": "close_up",
    },
    "ek-ek": {
        "action": "rapid jaw chattering at prey",
        "object": "window gecko prey",
        "stock": ["cat looking up window", "cat hunting stare close up", "cat mouth meowing close up"],
        "ugc": ["cat chattering at bird", "cat ekekek at gecko", "cat chirping window"],
        "metaphor": ["cat dilated pupils macro", "house gecko on wall"],
        "negatives": ["sleeping", "eating food", "litter box"],
        "shot": "close_up",
    },
    "dorong": {
        "action": "paw pushing object off table edge",
        "object": "glass cup table",
        "stock": ["cat paw touching glass", "cat on table paw", "water glass falling slow motion"],
        "ugc": ["cat knocking glass off table", "cat pushing cup off counter"],
        "metaphor": ["cat paw pads close up", "curious cat whiskers macro"],
        "negatives": ["sleeping", "drinking milk", "bathing"],
        "shot": "medium_full",
    },
    "melek": {
        "action": "sleeping calmly with one eye open",
        "object": "pond eye",
        "stock": ["duck resting water", "duck eye macro close up", "flock ducks pond"],
        "ugc": ["duck sleeping one eye open", "unihemispheric sleep bird"],
        "metaphor": ["bird blinking slow motion", "predator hawk sky"],
        "negatives": ["cooking", "roasted", "hunting"],
        "shot": "close_up",
    },
    "stabil": {
        "action": "head staying completely still while body moves",
        "object": "steady chicken head",
        "stock": ["chicken head close up", "rooster walking farm", "hen staring camera"],
        "ugc": ["chicken head stabilization", "holding chicken steady head gimbal"],
        "metaphor": ["camera gimbal stabilizer", "chicken eye macro"],
        "negatives": ["fried chicken", "cooking", "sleeping"],
        "shot": "medium_full",
    },
    "mengunyah": {
        "action": "jaw chewing cud rhythmically",
        "object": "grass meadow",
        "stock": ["cow chewing grass close up", "cattle grazing pasture", "cow mouth macro"],
        "ugc": ["cow chewing cud close up", "cow rumination jaw"],
        "metaphor": ["green meadow grass wind", "dairy cow portrait"],
        "negatives": ["steak", "butcher", "sleeping"],
        "shot": "close_up",
    },
    "kelenjar": {
        "action": "scent glands on paw pads touching turf",
        "object": "paw pads",
        "stock": ["dog paw pads close up", "dog paw macro", "dog walking grass"],
        "ugc": ["dog paw scent glands close up", "inspecting dog paw pads"],
        "metaphor": ["dog sniffing ground", "paw print in dirt"],
        "negatives": ["eating", "rolling"],
        "shot": "close_up",
    },
    "feromon": {
        "action": "leaving invisible scent marker on ground",
        "object": "grass scent trail",
        "stock": ["dog sniffing grass close up", "dog paw touching grass", "dog nose macro"],
        "ugc": ["dog scent marking paws", "dog sniffing trail"],
        "metaphor": ["dog nose sniffing air", "paw print mud"],
        "negatives": ["eating", "sleeping"],
        "shot": "close_up",
    },
    "sembunyi": {
        "action": "animal hiding and peeking from tight shelter",
        "object": "tight shelter",
        "stock": ["hiding peeking close up", "curled up cozy shelter", "eyes peeking dark"],
        "ugc": ["hiding in tight spot", "peeking from cardboard box"],
        "metaphor": ["dilated eyes alert close up", "safe cozy den"],
        "negatives": ["eating", "cartoon", "sleeping outdoors"],
        "shot": "close_up",
    },
    "stres": {
        "action": "nervous animal calming down inside cozy hiding spot",
        "object": "cozy shelter",
        "stock": ["cat hiding box calm", "anxious cat relaxing", "cat curling up safe"],
        "ugc": ["cat calming down inside box", "stressed cat hiding"],
        "metaphor": ["cat slow blinking relaxed", "cozy warm shelter"],
        "negatives": ["fighting", "eating", "cartoon"],
        "shot": "close_up",
    },
    "hangat": {
        "action": "animal curled up tightly staying warm",
        "object": "warm cozy box",
        "stock": ["curled up warm box", "cozy sleeping curled up", "pet tucked inside box"],
        "ugc": ["cat curled up warm cardboard box", "kitten sleeping cozy box"],
        "metaphor": ["warm sunlight fur close up", "cozy blanket"],
        "negatives": ["eating", "outdoor rain", "cartoon"],
        "shot": "close_up",
    },
    "subscribe": {
        "action": "adorable expressive animal looking directly at camera",
        "object": "expressive face",
        "stock": ["cute happy portrait", "adorable looking camera", "cute pet tilting head"],
        "ugc": ["cute funny pet reaction", "adorable head tilt close up"],
        "metaphor": ["happy pet close up"],
        "negatives": ["sad", "injured", "aggressive", "cartoon"],
        "shot": "close_up",
    },
}

OBJECT_TRANSLATION = {
    "kardus": "cardboard box",
    "kotak": "cardboard box",
    "kasur": "pet bed",
    "bantal": "cushion",
    "tanah": "dirt soil",
    "rumput": "grass turf",
    "pasir": "sand",
    "gelas": "glass cup",
    "meja": "table edge",
    "cicak": "gecko lizard",
    "tembok": "wall",
    "air": "water",
    "kolam": "pond water",
    "danau": "lake water",
    "taman": "park grass",
    "kaki": "hind paws",
    "telapak": "paw pads",
    "mata": "eyes close up",
    "kepala": "head",
    "mulut": "mouth jaw",
    "tulang": "bones",
    "jantung": "heart",
    "darah": "blue blood",
    "otak": "brain",
    "kantung": "brood pouch",
    "telur": "eggs",
    "racun": "venom",
    "tentakel": "tentacles",
    "bulu": "fur",
    "cakar": "claws",
    "ekor": "tail",
    "paruh": "beak",
    "sayap": "wings",
    "lubang": "burrow",
    "pohon": "tree branch",
    "kereta": "train tracks",
    "ruang": "tight space",
    "sempit": "tight box",
    "hangat": "warm shelter",
    "predator": "hunting ambush",
    "mangsa": "prey stalking",
}

DEFAULT_SUBJECT_NEGATIVES: Dict[str, List[str]] = {
    "cat": ["eating", "litter box", "bathing", "cartoon", "drawing"],
    "dog": ["eating", "rolling", "pooping", "cartoon", "drawing"],
    "duck": ["cooking", "roasted", "hunting", "cartoon"],
    "duckling": ["cooking", "roasted", "cartoon"],
    "chicken": ["fried chicken", "cooking", "roasted", "cartoon"],
    "cow": ["steak", "butcher", "cooking", "cartoon"],
    "octopus": ["cooking", "sushi", "grilled", "market", "cartoon"],
    "seahorse": ["dried", "cartoon", "drawing", "tattoo"],
    "axolotl": ["cartoon", "plush", "toy", "minecraft"],
    "slow loris": ["cartoon", "drawing", "cage"],
}


SYSTEM_QUERY_PLANNER_PROMPT = """Anda adalah Senior Stock Footage Researcher & Short-Form Visual Director.
Tugas Anda: mengubah naskah Voiceover Bahasa Indonesia menjadi rencana pencarian raw video Bahasa Inggris yang 100% berbasis BUKTI VISUAL FISIK di depan lensa kamera.

ATURAN KERAS:
1. JANGAN menerjemahkan kata tanya retoris atau konteks waktu/sebab yang TIDAK perlu tampak di layar (contoh: "habis buang air / after poop" WAJIB dibuang dari pencarian stok karena meracuni hasil pencarian Pexels menjadi "dog eating grass").
2. Prioritaskan kata HURUF KAPITAL pada naskah sebagai fokus visual utama.
3. Ekstrak `negative_terms` berisi kata kerja/konsep salah yang sering muncul di mesin pencari stok (contoh: untuk "anjing menendang tanah", blokir ["eating", "chewing", "rolling", "sleeping", "pooping"]).
4. Hasilkan tepat 3 kategori query berbahasa Inggris untuk naskah secara keseluruhan DAN per kalimat/beat:
   - "literal_ugc": 2-3 query spesifik perilaku nyata untuk YouTube Shorts, TikTok, Reddit (4-6 kata).
   - "conceptual_stock": 2-3 query kinematik pendek (2-4 kata saja: subjek + aksi fisik/objek) khusus Pexels, Pixabay, Wikimedia.
   - "metaphorical_broll": 2 query cutaway pendukung/anatomi/reaksi jika klip utama langka.

Jawab HANYA JSON valid sesuai skema ini:
{
  "primary_subject": "dog",
  "primary_intent": {
    "subject": "dog",
    "subject_id": "anjing",
    "kinematic_action": "hind legs kicking dirt backward",
    "target_object": "dirt soil grass",
    "emotion_or_energy": "energetic",
    "recommended_shot": "low_angle",
    "negative_terms": ["eating", "chewing", "rolling", "sleeping", "pooping"]
  },
  "queries": {
    "literal_ugc": ["dog kicking dirt back legs", "dog scratching grass hind legs"],
    "conceptual_stock": ["dog digging dirt", "dog paw scratching ground", "dirt flying dog paws"],
    "metaphorical_broll": ["dog paw pads close up", "dog sniffing grass turf"]
  },
  "beats": [
    {
      "beat_id": "U1",
      "role": "hook",
      "vo_line": "...",
      "kinematic_action": "...",
      "target_object": "...",
      "recommended_shot": "low_angle",
      "negative_terms": ["eating", "rolling"],
      "literal_ugc": ["..."],
      "conceptual_stock": ["..."],
      "metaphorical_broll": ["..."]
    }
  ]
}"""


def _extract_emphasis_tokens(raw_text: str) -> List[str]:
    """Capture ALL-CAPS words (>=3 letters, excluding audio tags) emphasized in the script."""
    without_tags = re.sub(r"\[.*?\]", " ", raw_text or "")
    caps = re.findall(r"\b[A-Z]{3,}\b", without_tags)
    ignore = {"CTA", "VO", "SFX", "BGM", "DAN", "YANG", "INI", "ITU"}
    return list(dict.fromkeys(c for c in caps if c not in ignore))


def _strip_non_visual_clauses(line: str) -> Tuple[str, List[str]]:
    cleaned = re.sub(r"\[.*?\]", " ", line or "").strip()
    stripped: List[str] = []
    for pat in NON_VISUAL_CLAUSES_ID:
        for m in re.finditer(pat, cleaned, flags=re.IGNORECASE):
            stripped.append(m.group(0).strip())
        cleaned = re.sub(pat, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned, stripped


def _split_script_into_beats(naskah: str) -> List[Tuple[str, str]]:
    """Split a raw Indonesian VO script into clean narrative beat lines with inferred roles."""
    raw_lines = [
        ln.strip()
        for ln in (naskah or "").splitlines()
        if ln.strip()
        and not ln.strip().startswith(("#", "---", "Status:", "Spesies:", "Engine:", "Batch:", "Keputusan:", "|"))
    ]
    sentences: List[str] = []
    for ln in raw_lines:
        clean = re.sub(r"^[-*0-9.)]+\s*", "", ln).strip()
        if not clean:
            continue
        parts = [p.strip() for p in re.split(r"(?<=[?!.])\s+", clean) if p.strip()]
        sentences.extend(parts if parts else [clean])

    if not sentences and naskah.strip():
        sentences = [naskah.strip()]

    beats: List[Tuple[str, str]] = []
    total = len(sentences)
    for idx, sent in enumerate(sentences):
        low = sent.lower()
        if idx == 0 or ("?" in sent and idx <= 1):
            role = "hook"
        elif any(k in low for k in ("subscribe", "like", "pencet", "klik")):
            role = "cta"
        elif any(k in low for k in ("bukan", "salah", "mitos", "pikir", "kira")) and idx <= 2:
            role = "contrast"
        elif idx == total - 1 or low.lstrip("[").startswith(("makanya", "itulah", "jadinya")):
            role = "payoff"
        elif any(k in low for k in ("kelenjar", "feromon", "otot", "otak", "saraf", "bukti", "ternyata", "faktanya")):
            role = "proof"
        else:
            role = "process"
        beats.append((role, sent))
    return beats


def _default_provider_routing() -> Dict[str, List[str]]:
    return {
        "conceptual_stock": ["vault", "pexels", "pixabay", "wikimedia"],
        "literal_ugc": ["youtube", "tiktok", "reddit"],
        "metaphorical_broll": ["pexels", "wikimedia", "inaturalist"],
    }


def _build_beat_plan_deterministic(
    beat_idx: int,
    role: str,
    vo_line: str,
    fallback_subj_id: str,
    fallback_subj_en: str,
) -> BeatSourcingPlan:
    emphasis = _extract_emphasis_tokens(vo_line)
    pruned_line, stripped = _strip_non_visual_clauses(vo_line)
    low = pruned_line.lower()

    subj_id, subj_en = fallback_subj_id, fallback_subj_en
    for k_id, (k_en, _) in SUBJECT_MAP.items():
        if k_id in low:
            subj_id, subj_en = k_id, k_en
            break

    matched_entries: List[dict] = []
    if role == "cta":
        matched_entries.append(KINEMATIC_ONTOLOGY["subscribe"])
    for stem, spec in KINEMATIC_ONTOLOGY.items():
        if stem == "subscribe":
            continue
        if stem in low:
            matched_entries.append(spec)

    # Translate emphasized / physical nouns (excluding non-visual clauses already pruned!)
    obj_en_parts: List[str] = []
    for emp in emphasis:
        tr = OBJECT_TRANSLATION.get(emp.lower())
        if tr and tr not in obj_en_parts:
            obj_en_parts.append(tr)
    for id_obj, en_obj in OBJECT_TRANSLATION.items():
        if re.search(rf"\b{re.escape(id_obj)}\b", low) and en_obj not in obj_en_parts:
            obj_en_parts.append(en_obj)
    target_obj = obj_en_parts[0] if obj_en_parts else "natural habitat"

    stock_qs: List[str] = []
    ugc_qs: List[str] = []
    meta_qs: List[str] = []
    negatives: List[str] = []
    shot: Literal["close_up", "medium_full", "low_angle", "wide"] = "medium_full"
    action_desc = f"{subj_en} natural behavior"

    if matched_entries:
        primary = matched_entries[0]
        action_desc = primary["action"]
        shot = primary.get("shot", "medium_full")
        if target_obj == "natural habitat" and primary.get("object"):
            target_obj = str(primary["object"])
        for entry in matched_entries[:2]:
            for sq in entry.get("stock", []):
                q = sq if subj_en in sq else f"{subj_en} {sq}"
                stock_qs.append(q)
            for uq in entry.get("ugc", []):
                q = uq if subj_en in uq else f"{subj_en} {uq}"
                ugc_qs.append(q)
            for mq in entry.get("metaphor", []):
                q = mq if subj_en in mq or "gimbal" in mq or "gecko" in mq or "sound" in mq or "cell" in mq else f"{subj_en} {mq}"
                meta_qs.append(q)
            for neg in entry.get("negatives", []):
                if neg not in negatives:
                    negatives.append(neg)
    else:
        obj_phrase = target_obj if target_obj != "natural habitat" else "close up"
        stock_qs = [f"{subj_en} {obj_phrase}", f"{subj_en} portrait close up", f"{subj_en} active movement"]
        ugc_qs = [f"{subj_en} {obj_phrase} behavior", f"funny {subj_en} {obj_phrase}"]
        meta_qs = [f"{subj_en} eyes close up", f"{subj_en} paws macro"]

    # Always inject stripped causal words' English equivalents into negative_terms if relevant
    if any("buang air" in s.lower() or "pup" in s.lower() for s in stripped):
        for tok in ("pooping", "defecating", "eating", "rolling"):
            if tok not in negatives:
                negatives.append(tok)

    if not negatives:
        negatives.extend(DEFAULT_SUBJECT_NEGATIVES.get(subj_en, ["cartoon", "animation", "drawing", "text overlay"]))

    dedup = lambda seq: list(dict.fromkeys(s.strip() for s in seq if s.strip()))
    vi = VisualIntent(
        subject=subj_en,
        subject_id=subj_id,
        kinematic_action=action_desc,
        target_object=target_obj,
        emotion_or_energy="energetic" if role in ("hook", "process") else ("warm" if role == "cta" else "focused"),
        recommended_shot=shot,
        emphasis_tokens=emphasis,
        stripped_clauses=stripped,
        negative_terms=negatives[:8],
    )
    tq = TieredQueries(
        literal_ugc=dedup(ugc_qs)[:3],
        conceptual_stock=dedup(stock_qs)[:3],
        metaphorical_broll=dedup(meta_qs)[:2],
    )
    return BeatSourcingPlan(
        beat_id=f"U{beat_idx}",
        beat_index=beat_idx,
        role=role,  # type: ignore[arg-type]
        vo_line=re.sub(r"\[.*?\]", "", vo_line).strip(),
        visual_intent=vi,
        queries=tq,
        recommended_providers=_default_provider_routing(),
    )


def plan_visual_queries(
    naskah: str,
    subject_hint: str = "",
    use_llm: bool = True,
    config: Optional[StudioConfig] = None,
) -> EpisodeQueryPlan:
    """Generate a complete Scene-Beat & 3-Tier Visual Intent Query Plan from an Indonesian script."""
    cfg = config or get_config()
    text_low = f"{subject_hint} {naskah}".lower()

    # Detect primary subject first
    prim_id, prim_en = "hewan", "animal"
    for k_id, (k_en, _) in SUBJECT_MAP.items():
        if k_id in text_low:
            prim_id, prim_en = k_id, k_en
            break

    raw_beats = _split_script_into_beats(naskah)
    det_beats = [
        _build_beat_plan_deterministic(i + 1, role, line, prim_id, prim_en)
        for i, (role, line) in enumerate(raw_beats)
    ]

    # Inherit episode-level visual anchor for abstract beats (e.g., myth/contrast lines without concrete nouns)
    anchor_beat = next(
        (
            b
            for b in det_beats
            if b.role != "cta"
            and (
                not b.visual_intent.kinematic_action.endswith("natural behavior")
                or b.visual_intent.target_object != "natural habitat"
            )
        ),
        det_beats[0] if det_beats else None,
    )
    if anchor_beat is not None:
        for b in det_beats:
            if b.role == "cta":
                continue
            if b.visual_intent.kinematic_action.endswith("natural behavior") and b.visual_intent.target_object == "natural habitat":
                b.visual_intent.kinematic_action = anchor_beat.visual_intent.kinematic_action
                b.visual_intent.target_object = anchor_beat.visual_intent.target_object
                b.visual_intent.recommended_shot = anchor_beat.visual_intent.recommended_shot
                b.queries = anchor_beat.queries.model_copy(deep=True)
                for neg in anchor_beat.visual_intent.negative_terms:
                    if neg not in b.visual_intent.negative_terms:
                        b.visual_intent.negative_terms.append(neg)

    # Aggregate deterministic baseline across non-CTA beats
    non_cta = [b for b in det_beats if b.role != "cta"] or det_beats
    agg_stock: List[str] = []
    agg_ugc: List[str] = []
    agg_meta: List[str] = []
    agg_neg: List[str] = []
    for b in non_cta:
        agg_stock.extend(b.queries.conceptual_stock)
        agg_ugc.extend(b.queries.literal_ugc)
        agg_meta.extend(b.queries.metaphorical_broll)
        for n in b.visual_intent.negative_terms:
            if n not in agg_neg:
                agg_neg.append(n)

    dedup = lambda seq: list(dict.fromkeys(s.strip() for s in seq if s.strip()))
    baseline_intent = (anchor_beat.visual_intent if anchor_beat else None) or (
        non_cta[0].visual_intent if non_cta else VisualIntent(subject=prim_en, subject_id=prim_id)
    )
    baseline_queries = TieredQueries(
        conceptual_stock=dedup(agg_stock)[:4],
        literal_ugc=dedup(agg_ugc)[:4],
        metaphorical_broll=dedup(agg_meta)[:3],
    )

    # Optional LLM enrichment using auto-resolved live model on configured AI endpoint
    if use_llm and cfg.ai_api_key and naskah.strip():
        try:
            text_model = resolve_ai_model("text", cfg)
            beats_outline = "\n".join(
                f"{b.beat_id} [{b.role.upper()}]: {b.vo_line}" for b in det_beats
            )
            client = openai.OpenAI(base_url=cfg.ai_base_url, api_key=cfg.ai_api_key, timeout=16.0, max_retries=0)
            resp = client.chat.completions.create(
                model=text_model,
                messages=[
                    {"role": "system", "content": SYSTEM_QUERY_PLANNER_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"Naskah VO:\n{naskah.strip()}\n\n"
                            f"Daftar Beat WAJIB (hasilkan array `beats` tepat sejumlah beat ini, 1-to-1):\n{beats_outline}"
                        ),
                    },
                ],
                temperature=0.15,
                max_tokens=1600,
            )
            raw_content = resp.choices[0].message.content or ""
            m = re.search(r"\{.*\}", raw_content, re.DOTALL)
            if m:
                parsed = json.loads(m.group(0))
                q_obj = parsed.get("queries") or {}
                pi_obj = parsed.get("primary_intent") or {}
                llm_neg = dedup([*(pi_obj.get("negative_terms") or []), *agg_neg])[:10]
                llm_queries = TieredQueries(
                    conceptual_stock=dedup([*(q_obj.get("conceptual_stock") or []), *baseline_queries.conceptual_stock])[:5],
                    literal_ugc=dedup([*(q_obj.get("literal_ugc") or []), *baseline_queries.literal_ugc])[:5],
                    metaphorical_broll=dedup([*(q_obj.get("metaphorical_broll") or []), *baseline_queries.metaphorical_broll])[:4],
                )
                valid_shots = {"close_up", "medium_full", "low_angle", "wide"}
                pi_shot = str(pi_obj.get("recommended_shot") or baseline_intent.recommended_shot)
                if pi_shot not in valid_shots:
                    pi_shot = baseline_intent.recommended_shot
                merged_intent = baseline_intent.model_copy(
                    update={
                        "subject": str(pi_obj.get("subject") or baseline_intent.subject),
                        "kinematic_action": str(pi_obj.get("kinematic_action") or baseline_intent.kinematic_action),
                        "target_object": str(pi_obj.get("target_object") or baseline_intent.target_object),
                        "recommended_shot": pi_shot,
                        "negative_terms": llm_neg,
                    }
                )
                llm_beats_raw = parsed.get("beats") or []
                for idx_b, b_plan in enumerate(det_beats):
                    if idx_b < len(llm_beats_raw) and isinstance(llm_beats_raw[idx_b], dict):
                        lb = llm_beats_raw[idx_b]
                        # LLM beat queries take priority (fallback to baseline only if LLM returned empty)
                        lb_stock = dedup(lb.get("conceptual_stock") or [])
                        lb_ugc = dedup(lb.get("literal_ugc") or [])
                        lb_meta = dedup(lb.get("metaphorical_broll") or [])
                        b_plan.queries = TieredQueries(
                            conceptual_stock=(lb_stock or b_plan.queries.conceptual_stock)[:3],
                            literal_ugc=(lb_ugc or b_plan.queries.literal_ugc)[:3],
                            metaphorical_broll=(lb_meta or b_plan.queries.metaphorical_broll)[:2],
                        )
                        if lb.get("kinematic_action"):
                            b_plan.visual_intent.kinematic_action = str(lb["kinematic_action"])
                        if lb.get("target_object"):
                            b_plan.visual_intent.target_object = str(lb["target_object"])
                        if lb.get("recommended_shot") in valid_shots:
                            b_plan.visual_intent.recommended_shot = lb["recommended_shot"]
                        if lb.get("negative_terms"):
                            b_plan.visual_intent.negative_terms = dedup(
                                [*lb["negative_terms"], *b_plan.visual_intent.negative_terms]
                            )[:8]

                rec = dedup(
                    [*llm_queries.conceptual_stock[:3], *llm_queries.literal_ugc[:2], *llm_queries.metaphorical_broll[:1]]
                )[:6]
                return EpisodeQueryPlan(
                    planner_mode="llm_structured",
                    planner_model=text_model,
                    primary_subject=merged_intent.subject,
                    primary_intent=merged_intent,
                    queries=llm_queries,
                    negative_terms=llm_neg,
                    beats=det_beats,
                    recommended_queries=rec,
                )
        except Exception:
            pass

    rec_det = dedup(
        [*baseline_queries.conceptual_stock[:3], *baseline_queries.literal_ugc[:2], *baseline_queries.metaphorical_broll[:1]]
    )[:6]
    return EpisodeQueryPlan(
        planner_mode="kinematic_ontology",
        planner_model="deterministic",
        primary_subject=prim_en,
        primary_intent=baseline_intent.model_copy(update={"negative_terms": agg_neg[:10]}),
        queries=baseline_queries,
        negative_terms=agg_neg[:10],
        beats=det_beats,
        recommended_queries=rec_det,
    )
