"""Acoustic Zero-Gap VO Splicer: cuts real audio silence pockets below vo_silence_db (-32 dB) & remaps word timestamps."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import List, Tuple

from core.config import StudioConfig

_RE_SIL_START = re.compile(r"silence_start:\s*([0-9]+(?:\.[0-9]+)?)")
_RE_SIL_END = re.compile(r"silence_end:\s*([0-9]+(?:\.[0-9]+)?)")
PIVOT_PAUSE_TOKENS = {"ternyata", "bukan", "salah", "makanya", "anehnya", "faktanya", "padahal"}


def parse_silencedetect_output(stderr_text: str, total_dur_s: float) -> List[Tuple[float, float]]:
    """Parse FFmpeg silencedetect stderr into ordered (start_s, end_s) silence intervals."""
    intervals: List[Tuple[float, float]] = []
    pending_start: float | None = None
    for line in stderr_text.splitlines():
        m_st = _RE_SIL_START.search(line)
        if m_st:
            pending_start = float(m_st.group(1))
            continue
        m_en = _RE_SIL_END.search(line)
        if m_en and pending_start is not None:
            en = float(m_en.group(1))
            if en > pending_start:
                intervals.append((pending_start, en))
            pending_start = None
    if pending_start is not None and total_dur_s > pending_start:
        intervals.append((pending_start, total_dur_s))
    return intervals


def _is_pre_pivot_silence(s_st: float, s_en: float, words: List[dict] | None) -> bool:
    """Return True if the first spoken word right after silence [s_st, s_en] is a rhetorical pivot token."""
    if not words:
        return False
    for w in words:
        wst = float(w.get("start", 0.0))
        if wst < s_st - 0.08:
            continue
        if wst <= s_en + 0.35:
            tok = re.sub(r"[^\w]", "", (w.get("word") or "").lower()).strip()
            return tok in PIVOT_PAUSE_TOKENS
        break
    return False


def build_speech_segments(
    total_dur_s: float,
    silences: List[Tuple[float, float]],
    edge_pad_s: float = 0.025,
    words: List[dict] | None = None,
    pivot_pause_s: float = 0.0,
    max_pivot_pauses: int = 2,
) -> List[Tuple[float, float]]:
    """Invert silence intervals into kept speech intervals with soft consonant-protection padding.

    v3.7 Selective Rhetorical Pause Preservation: when `pivot_pause_s > 0` and `words` are supplied,
    preserves up to `max_pivot_pauses` rhetorical silences (`pivot_pause_s`) right before pivot tokens
    ("ternyata", "bukan", "makanya") so payoff/twist sentences land after an intentional acoustic vacuum.
    """
    if total_dur_s <= 0.0:
        return []
    if not silences:
        return [(0.0, total_dur_s)]

    # Build trim regions (strictly inside each silence pocket so edges keep edge_pad_s of breath/consonants)
    cut_regions: List[Tuple[float, float]] = []
    pivots_preserved = 0
    for s_st, s_en in sorted(silences):
        s_st = max(0.0, s_st)
        s_en = min(total_dur_s, s_en)
        if s_st <= 0.02:
            # Leading silence: trim up to edge_pad_s before speech starts
            cut_end = max(0.0, s_en - edge_pad_s)
            if cut_end > 0.02:
                cut_regions.append((0.0, cut_end))
        elif s_en >= total_dur_s - 0.02:
            # Trailing silence: keep a tiny 0.05s tail after last word
            cut_start = min(total_dur_s, s_st + 0.05)
            if total_dur_s - cut_start > 0.02:
                cut_regions.append((cut_start, total_dur_s))
        else:
            keep_right = edge_pad_s
            if (
                pivot_pause_s > (2.0 * edge_pad_s)
                and pivots_preserved < max_pivot_pauses
                and _is_pre_pivot_silence(s_st, s_en, words)
            ):
                keep_right = max(edge_pad_s, float(pivot_pause_s) - edge_pad_s)
                pivots_preserved += 1
            c_st = s_st + edge_pad_s
            c_en = s_en - keep_right
            if c_en - c_st >= 0.02:
                cut_regions.append((c_st, c_en))

    if not cut_regions:
        return [(0.0, total_dur_s)]

    # Invert cut_regions into kept speech segments
    kept: List[Tuple[float, float]] = []
    cursor = 0.0
    for c_st, c_en in cut_regions:
        if c_st > cursor + 0.015:
            kept.append((cursor, c_st))
        cursor = max(cursor, c_en)
    if total_dur_s > cursor + 0.015:
        kept.append((cursor, total_dur_s))

    return kept or [(0.0, total_dur_s)]


def remap_timestamp(t: float, kept_segments: List[Tuple[float, float]], tempo_boost: float = 1.0) -> float:
    """Piecewise-map an original audio timestamp onto the spliced, tempo-boosted timeline."""
    running_s = 0.0
    for seg_st, seg_en in kept_segments:
        if t <= seg_st:
            return round(running_s / tempo_boost, 3)
        if t <= seg_en:
            return round((running_s + (t - seg_st)) / tempo_boost, 3)
        running_s += seg_en - seg_st
    return round(running_s / tempo_boost, 3)


def _probe_duration(audio_path: Path, cfg: StudioConfig) -> float:
    out = subprocess.check_output(
        [
            cfg.ffprobe_cmd(),
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(audio_path),
        ],
        text=True,
        timeout=15,
    ).strip()
    return float(out) if out else 0.0


def tighten_vo_and_alignment(
    vo_file: Path,
    words: List[dict],
    out_mp3: Path,
    cfg: StudioConfig,
    max_pause_s: float = 0.10,
    tempo_boost: float = 1.07,
    silence_db: float = -32.0,
    pivot_pause_s: float = 0.26,
) -> Tuple[Path, List[dict], float]:
    """Detect and cut real acoustic silence pockets (< silence_db) from raw VO and remap all word timestamps.

    Preserves up to 2 rhetorical pre-pivot pauses (`pivot_pause_s`) before twist/payoff words.
    Returns (tightened_vo_path, shifted_words, new_duration_s).
    """
    if not vo_file.exists():
        return vo_file, words, 0.0

    orig_dur = _probe_duration(vo_file, cfg)
    if orig_dur <= 0.1:
        return vo_file, words, orig_dur

    # 1. Measure real acoustic silence pockets on raw VO waveform via FFmpeg silencedetect
    min_sil = max(0.06, float(max_pause_s))
    r_sil = subprocess.run(
        [
            cfg.ffmpeg_cmd(),
            "-i",
            str(vo_file),
            "-af",
            f"silencedetect=n={silence_db:.1f}dB:d={min_sil:.3f}",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    silences = parse_silencedetect_output(r_sil.stderr, orig_dur)
    raw_pivot_s = float(pivot_pause_s) * max(1.0, float(tempo_boost)) if pivot_pause_s > 0.0 else 0.0
    kept_segments = build_speech_segments(
        orig_dur,
        silences,
        edge_pad_s=0.025,
        words=words,
        pivot_pause_s=raw_pivot_s,
        max_pivot_pauses=2,
    )

    # 2. Build FFmpeg filter_complex to splice kept speech segments + apply energetic tempo_boost
    filter_parts: List[str] = []
    concat_labels: List[str] = []
    running_s = 0.0
    for idx, (st, en) in enumerate(kept_segments):
        seg_dur = max(0.02, en - st)
        fade_d = min(0.008, seg_dur * 0.25)
        filter_parts.append(
            f"[0:a]atrim={st:.4f}:{en:.4f},asetpts=PTS-STARTPTS,"
            f"afade=t=in:st=0:d={fade_d:.4f},afade=t=out:st={max(0.0, seg_dur - fade_d):.4f}:d={fade_d:.4f}[seg{idx}]"
        )
        concat_labels.append(f"[seg{idx}]")
        running_s += seg_dur

    tempo_chain = f",atempo={tempo_boost:.4f}" if abs(tempo_boost - 1.0) > 0.005 else ""
    if len(kept_segments) == 1:
        filter_parts.append(f"[seg0]anull{tempo_chain}[aout]")
    else:
        filter_parts.append(
            f"{''.join(concat_labels)}concat=n={len(kept_segments)}:v=0:a=1{tempo_chain}[aout]"
        )

    is_wav = out_mp3.suffix.lower() == ".wav"
    codec_args = ["-c:a", "pcm_s16le"] if is_wav else ["-c:a", "libmp3lame", "-b:a", "192k"]

    cmd = [
        cfg.ffmpeg_cmd(),
        "-y",
        "-i",
        str(vo_file),
        "-filter_complex",
        ";".join(filter_parts),
        "-map",
        "[aout]",
        *codec_args,
        str(out_mp3),
    ]
    subprocess.check_call(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    predicted_dur = max(0.1, running_s / tempo_boost)
    actual_dur = predicted_dur
    try:
        probed = _probe_duration(out_mp3, cfg)
        if probed > 0.1:
            actual_dur = probed
    except Exception:
        pass

    # 3. Piecewise remap every word timestamp + normalize against exact probed WAV duration (0.000s drift)
    drift_scale = actual_dur / predicted_dur
    use_scale = drift_scale if (abs(drift_scale - 1.0) > 0.001 and 0.85 <= drift_scale <= 1.15) else 1.0

    new_words: List[dict] = []
    for w in words:
        st_m = remap_timestamp(float(w["start"]), kept_segments, tempo_boost) * use_scale
        en_m = remap_timestamp(float(w["end"]), kept_segments, tempo_boost) * use_scale
        new_words.append(
            {
                "word": w["word"],
                "start": round(max(0.0, st_m), 3),
                "end": round(max(st_m + 0.06, en_m), 3),
            }
        )

    return out_mp3, new_words, round(actual_dur, 3)
