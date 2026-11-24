"""
Forensic YouTube Shorts Video & Channel Teardown Engine (v1.0)

Extracts 8 dimensions that basic scene/transcript scrapers miss:
1. Debounced Cut & Flash-Transition Detection (separates real editorial cuts from flash transitions)
2. Cut-by-Cut Midpoint Storyboard Grid (1 frame per cut, labeled with cut #, timestamp, and duration)
3. Pacing Curve Across 4 Narrative Zones (Hook 0-3s, CTA 3-13s, Mechanism 13-21s, Payoff 21s+)
4. Word-Level Cadence & Micro-Pause Map from .vtt / ASR (Cold-open latency, WPM per zone, dramatic pauses)
5. Audio Forensics (Integrated LUFS, LRA, True Peak, silence pockets, waveform + spectrogram image)
6. Multimodal AI Vision Forensic Breakdown (Typography spec, Y-position, 1-word RSVP vs grouped,
   Blurred-Canvas vs Full-Crop framing, Visual-to-Word Anchor mapping, Gamified CTA Red-Circle Easter Eggs)
7. Channel Outlier Miner (median views vs outlier multiplier, winning subjects, title hooks)
8. Executable Style Cloner (emits ready-to-use ChannelProfile TOML snippet for Editor Engine v3.1)
"""

from __future__ import annotations

import base64
import json
import re
import shutil
import statistics
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Literal, Optional, Tuple

import openai
from pydantic import BaseModel, Field

from core.config import StudioConfig, get_config, resolve_ai_model


class CutSegment(BaseModel):
    cut_index: int
    start_s: float
    end_s: float
    duration_s: float
    mid_s: float
    zone: Literal["hook", "cta_interrupt", "mechanism", "payoff"]
    scene_score: float = 0.0
    had_flash_transition: bool = False


class ScriptCadenceMetrics(BaseModel):
    total_words: int = 0
    cold_open_latency_s: float = 0.0
    overall_wpm: float = 0.0
    hook_wpm: float = 0.0
    body_wpm: float = 0.0
    dramatic_pauses: List[Dict[str, object]] = Field(default_factory=list)
    verbal_signatures: List[str] = Field(default_factory=list)
    cta_mechanic_type: str = "unknown"
    cta_transcript: str = ""
    full_transcript: str = ""


class AudioForensicsMetrics(BaseModel):
    integrated_lufs: float = -14.0
    loudness_range_lu: float = 0.0
    true_peak_dbfs: float = 0.0
    silence_pockets_count: int = 0
    waveform_image: Optional[str] = None


class VisionDirectorAnalysis(BaseModel):
    subtitle_mode: Literal["rsvp_1word", "grouped_2_3_words", "full_sentence", "none"] = "rsvp_1word"
    subtitle_y_zone: Literal["center_middle_55_68pct", "lower_third_70_82pct", "top_third"] = "center_middle_55_68pct"
    subtitle_color_hex: str = "#FFFFFF"
    highlight_color_hex: str = "#FFE600"
    font_character: str = "Extra-Black Geometric Sans / Heavy Italic"
    framing_strategy: Literal["auto_blur_canvas_mix", "full_9_16_crop_only", "split_screen"] = "auto_blur_canvas_mix"
    cta_visual_pattern: str = ""
    visual_anchor_examples: List[str] = Field(default_factory=list)
    why_it_retains_viewers: List[str] = Field(default_factory=list)


class VideoTeardownReport(BaseModel):
    video_id: str
    video_path: str
    analyzed_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_s: float
    width: int
    height: int
    fps: float
    total_cuts: int
    flash_transitions_count: int
    avg_cut_duration_s: float
    median_cut_duration_s: float
    hook_cut_avg_s: float
    cta_cut_avg_s: float
    body_cut_avg_s: float
    cuts: List[CutSegment] = Field(default_factory=list)
    cadence: ScriptCadenceMetrics = Field(default_factory=ScriptCadenceMetrics)
    audio: AudioForensicsMetrics = Field(default_factory=AudioForensicsMetrics)
    vision: Optional[VisionDirectorAnalysis] = None
    storyboard_image: Optional[str] = None
    recommended_toml_override: str = ""


class ChannelOutlierItem(BaseModel):
    video_id: str
    title: str
    url: str
    views: int
    outlier_multiplier: float
    is_viral_outlier: bool
    hook_pattern: str


class ChannelTeardownReport(BaseModel):
    channel_handle: str
    analyzed_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    videos_sampled: int
    median_views: int
    mean_views: int
    max_views: int
    outliers: List[ChannelOutlierItem] = Field(default_factory=list)
    winning_patterns: List[str] = Field(default_factory=list)


def _probe_video(video_path: Path, cfg: StudioConfig) -> dict:
    out = subprocess.check_output(
        [
            cfg.ffprobe_cmd(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(video_path),
        ],
        text=True,
        timeout=30,
    )
    d = json.loads(out)
    v = next((s for s in d.get("streams", []) if s.get("codec_type") == "video"), {})
    fr = v.get("avg_frame_rate", "30/1")
    try:
        n, den = fr.split("/")
        fps = round(float(n) / float(den), 2) if float(den) else 30.0
    except Exception:
        fps = 30.0
    return {
        "duration": round(float(d.get("format", {}).get("duration", 0.0) or 0.0), 3),
        "width": int(v.get("width", 0) or 0),
        "height": int(v.get("height", 0) or 0),
        "fps": fps,
    }


def detect_debounced_cuts(
    video_path: Path,
    duration_s: float,
    cfg: StudioConfig,
    threshold: float = 0.36,
    min_cut_gap_s: float = 0.45,
) -> Tuple[List[CutSegment], int]:
    """Detect true editorial cuts while collapsing <0.45s rapid spikes into flash/glitch transitions."""
    r = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-v",
            "error",
            "-i",
            str(video_path),
            "-vf",
            f"select='gt(scene,{threshold})',metadata=print:file=-",
            "-an",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    raw_spikes: List[Tuple[float, float]] = []
    curr_ts: Optional[float] = None
    for line in r.stdout.splitlines():
        if "pts_time:" in line:
            try:
                curr_ts = float(line.split("pts_time:")[1].split()[0])
            except Exception:
                curr_ts = None
        elif "lavfi.scene_score=" in line and curr_ts is not None:
            try:
                sc = float(line.split("lavfi.scene_score=")[1].strip())
                raw_spikes.append((round(curr_ts, 3), round(sc, 3)))
            except Exception:
                pass

    # Debounce rapid spikes (< min_cut_gap_s) into flash transition markers
    clean_boundaries: List[Tuple[float, float, bool]] = [(0.0, 1.0, False)]
    flash_count = 0
    for ts, sc in raw_spikes:
        if ts <= 0.25 or ts >= duration_s - 0.25:
            continue
        prev_ts, prev_sc, prev_flash = clean_boundaries[-1]
        if (ts - prev_ts) < min_cut_gap_s:
            flash_count += 1
            clean_boundaries[-1] = (prev_ts, max(prev_sc, sc), True)
        else:
            clean_boundaries.append((ts, sc, False))

    segments: List[CutSegment] = []
    for idx in range(len(clean_boundaries)):
        st, sc, had_flash = clean_boundaries[idx]
        en = clean_boundaries[idx + 1][0] if idx + 1 < len(clean_boundaries) else duration_s
        dur = round(max(0.2, en - st), 3)
        mid = round(st + dur / 2.0, 3)
        if st < 3.0:
            zone: Literal["hook", "cta_interrupt", "mechanism", "payoff"] = "hook"
        elif st < 13.5:
            zone = "cta_interrupt"
        elif st < duration_s - 4.5:
            zone = "mechanism"
        else:
            zone = "payoff"
        segments.append(
            CutSegment(
                cut_index=idx + 1,
                start_s=round(st, 3),
                end_s=round(en, 3),
                duration_s=dur,
                mid_s=mid,
                zone=zone,
                scene_score=sc,
                had_flash_transition=had_flash,
            )
        )
    return segments, flash_count


def parse_vtt_word_timings(vtt_path: Path, total_duration_s: float = 25.0) -> ScriptCadenceMetrics:
    """Parse YouTube word-level VTT (<00:00:00.520><c> word</c>) into WPM, pauses, and CTA mechanics."""
    if not vtt_path.exists():
        return ScriptCadenceMetrics()

    raw = vtt_path.read_text(encoding="utf-8", errors="ignore")
    word_events: List[Tuple[float, str]] = []

    block_re = re.compile(r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})[^\n]*\n([\s\S]*?)(?=\n\n|\Z)")
    tag_re = re.compile(r"<(\d{2}:\d{2}:\d{2}\.\d{3})><c>\s*([^<]+)</c>")

    def _ts_to_s(ts: str) -> float:
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    for b_start, b_end, body in block_re.findall(raw):
        st_s = _ts_to_s(b_start)
        en_s = _ts_to_s(b_end)
        if (en_s - st_s) < 0.05:
            continue
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        for line in lines:
            if "<c>" in line:
                first_word = re.sub(r"<[^>]+>", "", line.split("<")[0]).strip()
                if first_word:
                    word_events.append((round(st_s, 3), first_word))
                for t_str, w_str in tag_re.findall(line):
                    w_clean = w_str.strip()
                    if w_clean and not w_clean.startswith("["):
                        word_events.append((round(_ts_to_s(t_str), 3), w_clean))

    if not word_events:
        # Fallback for plain VTT without <c> word tags
        clean_lines = []
        for _, _, body in block_re.findall(raw):
            for l in body.splitlines():
                c = re.sub(r"<[^>]+>", "", l).strip()
                if c and c not in clean_lines:
                    clean_lines.append(c)
        full_txt = " ".join(clean_lines)
        words = full_txt.split()
        return ScriptCadenceMetrics(
            total_words=len(words),
            overall_wpm=round(len(words) / max(1.0, total_duration_s) * 60.0, 1),
            full_transcript=full_txt,
        )

    # Deduplicate any identical (ts, word) pairs
    dedup: List[Tuple[float, str]] = []
    for ts, w in word_events:
        if not dedup or (ts != dedup[-1][0] or w != dedup[-1][1]):
            dedup.append((ts, w))

    total_words = len(dedup)
    first_ts = dedup[0][0]
    last_ts = max(dedup[-1][0] + 0.35, total_duration_s)
    active_span = max(1.0, last_ts - first_ts)
    overall_wpm = round((total_words / active_span) * 60.0, 1)

    hook_words = [w for ts, w in dedup if ts <= 3.2]
    hook_wpm = round((len(hook_words) / 3.2) * 60.0, 1) if hook_words else overall_wpm

    body_words = [w for ts, w in dedup if ts > 12.0]
    body_span = max(1.0, last_ts - 12.0)
    body_wpm = round((len(body_words) / body_span) * 60.0, 1) if body_words else overall_wpm

    # Dramatic pauses (gaps >= 0.55s between consecutive words)
    pauses = []
    for i in range(len(dedup) - 1):
        gap = round(dedup[i + 1][0] - dedup[i][0], 3)
        if gap >= 0.55:
            pauses.append({"after_word": dedup[i][1], "at_s": dedup[i][0], "gap_s": gap})

    full_text = " ".join(w for _, w in dedup)
    low_full = full_text.lower()

    # Verbal signatures / persona anchors
    signatures = []
    for sig in ("cuy", "loh", "banget", "guys", "gaes", "bro", "ternyata", "makanya"):
        cnt = len(re.findall(r"\b" + sig + r"\b", low_full))
        if cnt > 0:
            signatures.append(f"{sig} ({cnt}x)")

    # Detect CTA mechanic archetype
    cta_type = "none"
    cta_excerpt = ""
    if "hp" in low_full and ("bagus" in low_full or "muncul" in low_full):
        cta_type = "gamified_easter_egg_hp_bagus"
        m = re.search(r"(tapi kalau hp[^.?!]+[.?!]\s*(?:komen[^.?!]+[.?!])?)", low_full)
        cta_excerpt = m.group(1) if m else "Tapi kalau HP-mu bagus, klik subscribe terus like, nanti muncul animasi..."
    elif "suka" in low_full and "subscribe" in low_full:
        cta_type = "template_b_affinity"
    elif "subscribe" in low_full:
        cta_type = "standard_subscribe"

    return ScriptCadenceMetrics(
        total_words=total_words,
        cold_open_latency_s=round(first_ts, 3),
        overall_wpm=overall_wpm,
        hook_wpm=hook_wpm,
        body_wpm=body_wpm,
        dramatic_pauses=pauses[:6],
        verbal_signatures=signatures,
        cta_mechanic_type=cta_type,
        cta_transcript=cta_excerpt,
        full_transcript=full_text,
    )


def analyze_audio_forensics(
    video_path: Path,
    out_dir: Path,
    video_id: str,
    cfg: StudioConfig,
) -> AudioForensicsMetrics:
    """Measure EBU R128 loudness, silence pockets, and render a waveform/spectrogram visual."""
    r_eb = subprocess.run(
        [cfg.ffmpeg_cmd(), "-i", str(video_path), "-af", "ebur128=peak=true,silencedetect=n=-32dB:d=0.20", "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=90,
    )
    lufs, lra, peak = -14.0, 0.0, 0.0
    silences = r_eb.stderr.count("silence_start:")
    lines = r_eb.stderr.splitlines()
    for idx, line in enumerate(lines):
        s = line.strip()
        if s.startswith("I:") and "LUFS" in s:
            try:
                lufs = float(s.split("I:")[1].split("LUFS")[0].strip())
            except Exception:
                pass
        elif s.startswith("LRA:") and "LU" in s:
            try:
                lra = float(s.split("LRA:")[1].split("LU")[0].strip())
            except Exception:
                pass
        elif s.startswith("Peak:") and "dBFS" in s:
            try:
                peak = float(s.split("Peak:")[1].split("dBFS")[0].strip())
            except Exception:
                pass

    wave_path = out_dir / f"waveform_{video_id}.png"
    subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-y",
            "-i",
            str(video_path),
            "-filter_complex",
            "aformat=channel_layouts=mono,showwavespic=s=1200x240:colors=#6366f1",
            "-frames:v",
            "1",
            "-update",
            "1",
            str(wave_path),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=60,
    )
    return AudioForensicsMetrics(
        integrated_lufs=lufs,
        loudness_range_lu=lra,
        true_peak_dbfs=peak,
        silence_pockets_count=silences,
        waveform_image=str(wave_path) if wave_path.exists() else None,
    )


def build_cut_storyboard(
    video_path: Path,
    cuts: List[CutSegment],
    out_jpg: Path,
    cfg: StudioConfig,
    max_frames: int = 16,
    cols: int = 4,
) -> Path:
    """Render a cut-by-cut storyboard sampling the midpoint of every editorial cut."""
    sampled = cuts[:max_frames]
    tmp_dir = out_jpg.parent / f"_sb_tmp_{out_jpg.stem}"
    shutil.rmtree(tmp_dir, ignore_errors=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)

    frames: List[Path] = []
    for c in sampled:
        fr = tmp_dir / f"cut_{c.cut_index:02d}.jpg"
        fl_tag = "*" if c.had_flash_transition else ""
        label = f"#{c.cut_index}{fl_tag} {c.start_s:.1f}-{c.end_s:.1f}s ({c.duration_s:.1f}s)"
        subprocess.run(
            [
                cfg.ffmpeg_cmd(),
                "-y",
                "-ss",
                f"{c.mid_s:.2f}",
                "-i",
                str(video_path),
                "-frames:v",
                "1",
                "-vf",
                f"scale=360:640:force_original_aspect_ratio=decrease,pad=360:640:(ow-iw)/2:(oh-ih)/2,"
                f"drawtext=text='{label}':fontcolor=yellow:fontsize=22:x=8:y=8:box=1:boxcolor=black@0.75",
                "-update",
                "1",
                "-q:v",
                "3",
                str(fr),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
        if fr.exists():
            frames.append(fr)

    if not frames:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise RuntimeError("Gagal membuat frame storyboard.")

    # Pad last row with black frames so grid is rectangular
    while len(frames) % cols != 0:
        pad = tmp_dir / f"pad_{len(frames)}.jpg"
        subprocess.run(
            [
                cfg.ffmpeg_cmd(),
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=c=black:s=360x640:d=1",
                "-frames:v",
                "1",
                "-update",
                "1",
                str(pad),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        frames.append(pad)

    row_imgs: List[Path] = []
    for r_i in range(0, len(frames), cols):
        grp = frames[r_i : r_i + cols]
        ro = tmp_dir / f"row_{r_i}.png"
        cmd = [cfg.ffmpeg_cmd(), "-y"]
        for g in grp:
            cmd.extend(["-i", str(g)])
        fc = "".join(f"[{j}:v]" for j in range(len(grp))) + f"hstack=inputs={len(grp)}[row]"
        cmd.extend(["-filter_complex", fc, "-map", "[row]", "-frames:v", "1", "-update", "1", str(ro)])
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        row_imgs.append(ro)

    if len(row_imgs) == 1:
        shutil.copy2(row_imgs[0], out_jpg)
    else:
        cmd = [cfg.ffmpeg_cmd(), "-y"]
        for ro in row_imgs:
            cmd.extend(["-i", str(ro)])
        fc = "".join(f"[{j}:v]" for j in range(len(row_imgs))) + f"vstack=inputs={len(row_imgs)}[out]"
        cmd.extend(["-filter_complex", fc, "-map", "[out]", "-frames:v", "1", "-update", "1", "-q:v", "3", str(out_jpg)])
        subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    shutil.rmtree(tmp_dir, ignore_errors=True)
    return out_jpg


def _run_vision_forensic_analysis(
    storyboard_path: Path,
    cadence: ScriptCadenceMetrics,
    cuts: List[CutSegment],
    cfg: StudioConfig,
) -> Optional[VisionDirectorAnalysis]:
    if not storyboard_path.exists():
        return None
    try:
        client = openai.OpenAI(base_url=cfg.ai_base_url, api_key=cfg.ai_api_key or "local")
        b64 = base64.b64encode(storyboard_path.read_bytes()).decode("utf-8")
        prompt = f"""Kamu adalah Senior Viral YouTube Shorts Reverse-Engineering Director.
Bedah gambar storyboard cut-by-cut ini bersama transkripnya:
Transkrip: "{cadence.full_transcript}"
Total Cut: {len(cuts)} (rata-rata {round(sum(c.duration_s for c in cuts)/max(1,len(cuts)), 2)} dtk/cut).

Analisis dengan sangat tajam:
1. subtitle_mode: Apakah subtitle muncul 1 kata per satu waktu ("rsvp_1word"), 2-3 kata ("grouped_2_3_words"), kalimat penuh ("full_sentence"), atau "none"?
2. subtitle_y_zone: Di mana posisi vertikal subtitle ("center_middle_55_68pct", "lower_third_70_82pct", "top_third")?
3. subtitle_color_hex & highlight_color_hex: Estimasi kode hex warna teks utama dan highlight.
4. font_character: Karakter font (mis. Extra-Black Sans Putih dengan shadow / Heavy Italic Kuning outline hitam).
5. framing_strategy: Bagaimana mereka menangani klip horizontal/persegi ("auto_blur_canvas_mix" jika memakai background blur atas-bawah + watermark paw kecil, atau "full_9_16_crop_only")?
6. cta_visual_pattern: Apa trik visual yang ditampilkan di layar saat kalimat CTA Subscribe/Like diucapkan (mis. B-roll orang pegang HP -> screen-recording UI YouTube Shorts dengan lingkaran merah di tombol Subscribe & animasi custom ikon kucing di tombol Like -> reaksi hewan lucu)?
7. visual_anchor_examples: 3-4 contoh konkret bagaimana visual di cut tertentu membuktikan kata yang sedang diucapkan (mis. kata 'telinga' -> tangan membuka telinga kucing).
8. why_it_retains_viewers: 3 rahasia psikologis/editing kenapa video ini menahan penonton sampai akhir.

Jawab HANYA JSON valid sesuai schema:
{{
  "subtitle_mode": "rsvp_1word" | "grouped_2_3_words" | "full_sentence" | "none",
  "subtitle_y_zone": "center_middle_55_68pct" | "lower_third_70_82pct" | "top_third",
  "subtitle_color_hex": "#FFFFFF",
  "highlight_color_hex": "#FFE600",
  "font_character": "...",
  "framing_strategy": "auto_blur_canvas_mix" | "full_9_16_crop_only" | "split_screen",
  "cta_visual_pattern": "...",
  "visual_anchor_examples": ["...", "..."],
  "why_it_retains_viewers": ["...", "..."]
}}"""
        resp = client.chat.completions.create(
            model=resolve_ai_model("vision", cfg),
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                    ],
                }
            ],
            max_tokens=700,
            temperature=0.1,
        )
        raw = (resp.choices[0].message.content or "").strip()
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            return VisionDirectorAnalysis.model_validate_json(m.group(0))
    except Exception as e:
        print(f"[WARN] Vision forensic analysis skipped/failed: {e}")
    return None


def teardown_video(
    video_path_or_url: str,
    vtt_path: Optional[Path] = None,
    out_dir: Optional[Path] = None,
    run_vision: bool = True,
    config: Optional[StudioConfig] = None,
) -> VideoTeardownReport:
    """Perform a complete 8-dimension forensic reverse-engineering teardown on a local video or YouTube URL."""
    cfg = config or get_config()
    target_dir = out_dir or (cfg.root_dir / "riset" / "forensics")
    target_dir.mkdir(parents=True, exist_ok=True)

    # 1. Resolve local file or download via yt-dlp
    if video_path_or_url.startswith(("http://", "https://")):
        m = re.search(r"(?:v=|shorts/|youtu\.be/)([a-zA-Z0-9_-]{11})", video_path_or_url)
        vid_id = m.group(1) if m else f"vid_{int(datetime.now().timestamp())}"
        dl_tpl = str(target_dir / f"{vid_id}.%(ext)s")
        ff_args = ["--ffmpeg-location", str(cfg.ffmpeg_bin)] if cfg.ffmpeg_bin else []
        subprocess.run(
            [
                cfg.ytdlp_cmd(),
                "--no-warnings",
                "--ignore-errors",
                "--js-runtimes",
                "node",
                *ff_args,
                "--write-auto-sub",
                "--sub-lang",
                "id,en",
                "-o",
                dl_tpl,
                video_path_or_url,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        v_cands = [p for p in target_dir.glob(f"{vid_id}.*") if p.suffix.lower() in {".mp4", ".webm", ".mkv"}]
        if not v_cands:
            raise RuntimeError(f"Gagal mengunduh video referensi: {video_path_or_url}")
        video_path = v_cands[0]
        vtt_cands = sorted(target_dir.glob(f"{vid_id}*.vtt"))
        if vtt_cands and vtt_path is None:
            vtt_path = vtt_cands[0]
    else:
        video_path = Path(video_path_or_url)
        if not video_path.exists():
            raise FileNotFoundError(f"File video tidak ditemukan: {video_path}")
        vid_id = video_path.stem.split(" - ")[0].strip()[:16]
        if vtt_path is None:
            # Auto-discover matching .vtt in riset/transkip/ or riset/latest/transkip/
            for sub in ("riset/transkip", "riset/latest/transkip"):
                cand = cfg.root_dir / sub / f"{vid_id}.id.vtt"
                if cand.exists():
                    vtt_path = cand
                    break

    probe = _probe_video(video_path, cfg)
    dur = probe["duration"]

    # 2. Debounced cut & flash transition detection
    cuts, flashes = detect_debounced_cuts(video_path, dur, cfg)
    cut_durs = [c.duration_s for c in cuts] or [dur]
    avg_cut = round(sum(cut_durs) / len(cut_durs), 2)
    med_cut = round(statistics.median(cut_durs), 2)

    def _zone_avg(z: str) -> float:
        zs = [c.duration_s for c in cuts if c.zone == z]
        return round(sum(zs) / len(zs), 2) if zs else avg_cut

    # 3. Word-level VTT cadence & pause analysis
    cadence = parse_vtt_word_timings(vtt_path, dur) if vtt_path else ScriptCadenceMetrics()

    # 4. Audio forensics & waveform image
    audio_metrics = analyze_audio_forensics(video_path, target_dir, vid_id, cfg)

    # 5. Cut-by-cut midpoint storyboard
    sb_path = target_dir / f"storyboard_{vid_id}.jpg"
    build_cut_storyboard(video_path, cuts, sb_path, cfg, max_frames=min(16, len(cuts)), cols=4)

    # 6. Optional AI Vision Forensic Director analysis
    vision = _run_vision_forensic_analysis(sb_path, cadence, cuts, cfg) if run_vision else None

    # 7. Generate ready-to-apply ChannelProfile TOML override
    sub_mode = vision.subtitle_mode if (vision and vision.subtitle_mode in ("rsvp_1word", "grouped_2_3_words")) else "rsvp_1word"
    sub_mode_toml = "rsvp_1word" if "1word" in sub_mode else "grouped"
    margin_v = 620 if (not vision or "center" in vision.subtitle_y_zone) else 450
    prim_hex = vision.subtitle_color_hex if vision else "#FFFFFF"
    hl_hex = vision.highlight_color_hex if vision else "#FFE600"
    framing_toml = "auto_blur_canvas" if (not vision or "blur" in vision.framing_strategy) else "crop_9_16"

    toml_snippet = f"""# Cloned Style Preset from {vid_id}
[pacing]
target_clip_count = {len(cuts)}
min_clip_duration_s = {max(1.0, round(min(cut_durs), 2))}
max_clip_duration_s = {min(5.0, round(max(cut_durs), 2))}

[visual]
font_size = 104
primary_color_hex = "{prim_hex}"
highlight_color_hex = "{hl_hex}"
target_lufs = {round(max(-16.0, min(-12.0, audio_metrics.integrated_lufs)), 1)}
subtitle_mode = "{sub_mode_toml}"
subtitle_margin_v = {margin_v}
framing_mode = "{framing_toml}"
"""

    report = VideoTeardownReport(
        video_id=vid_id,
        video_path=str(video_path),
        duration_s=dur,
        width=probe["width"],
        height=probe["height"],
        fps=probe["fps"],
        total_cuts=len(cuts),
        flash_transitions_count=flashes,
        avg_cut_duration_s=avg_cut,
        median_cut_duration_s=med_cut,
        hook_cut_avg_s=_zone_avg("hook"),
        cta_cut_avg_s=_zone_avg("cta_interrupt"),
        body_cut_avg_s=_zone_avg("mechanism"),
        cuts=cuts,
        cadence=cadence,
        audio=audio_metrics,
        vision=vision,
        storyboard_image=str(sb_path) if sb_path.exists() else None,
        recommended_toml_override=toml_snippet.strip(),
    )

    (target_dir / f"teardown_{vid_id}.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def teardown_channel(
    channel_url_or_list: str,
    limit: int = 20,
    config: Optional[StudioConfig] = None,
) -> ChannelTeardownReport:
    """Analyze a YouTube channel's Shorts (via yt-dlp or local .list file) to mine viral outliers & patterns."""
    cfg = config or get_config()
    entries: List[Tuple[str, str, int]] = []
    p = Path(channel_url_or_list)

    if p.exists() and p.is_file():
        handle = p.stem
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 3 and parts[2].isdigit():
                entries.append((parts[0].strip(), parts[1].strip(), int(parts[2])))
    else:
        url = channel_url_or_list.strip()
        if url.startswith("@"):
            url = f"https://www.youtube.com/{url}/shorts"
        elif "youtube.com" in url and not url.endswith("/shorts"):
            url = url.rstrip("/") + "/shorts"
        m = re.search(r"@([A-Za-z0-9._-]+)", url)
        handle = m.group(1) if m else "channel"
        res = subprocess.run(
            [
                cfg.ytdlp_cmd(),
                "--flat-playlist",
                "--no-warnings",
                "--playlist-end",
                str(limit),
                "--print",
                "%(id)s|%(title)s|%(view_count)s",
                url,
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        for line in res.stdout.splitlines():
            parts = line.strip().split("|")
            if len(parts) >= 3:
                v_cnt = int(parts[2]) if parts[2].isdigit() else 0
                entries.append((parts[0], parts[1], v_cnt))

    if not entries:
        raise RuntimeError(f"Tidak ada data video terbaca dari: {channel_url_or_list}")

    views_list = [v for _, _, v in entries if v > 0] or [1]
    med_views = int(statistics.median(views_list)) or 1
    mean_views = int(statistics.mean(views_list))
    max_views = max(views_list)

    outliers: List[ChannelOutlierItem] = []
    for vid, title, v in sorted(entries, key=lambda x: x[2], reverse=True):
        mult = round(v / max(1, med_views), 2)
        low_t = title.lower()
        if any(k in low_t for k in ("kenapa", "why", "alasan")):
            pat = "Curiosity Reason / Why Hook"
        elif any(k in low_t for k in ("jangan", "stop", "never", "tidak boleh")):
            pat = "Warning / Negative Hook"
        elif any(k in low_t for k in ("rahasia", "secret", "ternyata", "turns out")):
            pat = "Secret / Revelation Hook"
        elif any(k in low_t for k in ("vs", "eksperimen", "experiment")):
            pat = "Experiment / VS Comparison"
        else:
            pat = "Direct Statement Hook"

        outliers.append(
            ChannelOutlierItem(
                video_id=vid,
                title=title,
                url=f"https://www.youtube.com/shorts/{vid}",
                views=v,
                outlier_multiplier=mult,
                is_viral_outlier=(mult >= 2.5),
                hook_pattern=pat,
            )
        )

    viral_only = [o for o in outliers if o.is_viral_outlier]
    pat_counts: Dict[str, int] = {}
    for o in viral_only or outliers[:5]:
        pat_counts[o.hook_pattern] = pat_counts.get(o.hook_pattern, 0) + 1
    top_pats = [f"{k} ({cnt} video outlier)" for k, cnt in sorted(pat_counts.items(), key=lambda x: -x[1])]

    return ChannelTeardownReport(
        channel_handle=handle,
        videos_sampled=len(entries),
        median_views=med_views,
        mean_views=mean_views,
        max_views=max_views,
        outliers=outliers,
        winning_patterns=top_pats,
    )
