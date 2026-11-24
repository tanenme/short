"""Unit tests for Editor Engine v3.0 (aligner, subtitles, video_fx, audio_mix)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SISTEM_DIR = Path(__file__).resolve().parent.parent
EDITOR_DIR = SISTEM_DIR / "editor-engine"
for p in (str(SISTEM_DIR), str(EDITOR_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from aligner import apply_adaptive_proof_pacing, detect_cta_window, extract_dynamic_highlights, synchronize_scenes_fuzzy
from audio_mix import build_audio_filtergraph, compute_sfx_entries
from core.config import get_config
from moment_finder import pick_best_cross_clip_rescue, score_candidate_windows
from subtitles import generate_ass_subtitles, hex_to_ass_bgr
from video_fx import filter_clean_clips, get_v33_transition_spec
from vo_tightener import build_speech_segments, parse_silencedetect_output, remap_timestamp


class TestEditorEngineV3(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = get_config()
        self.c1 = self.cfg.get_channel("C1-fakta")

    def test_fuzzy_alignment_handles_hyphen_split_and_silent_outro(self) -> None:
        scenes = [
            {"index": 1, "brief_start_s": 0.0, "brief_end_s": 2.5, "vo": "Kenapa mulut kucing bunyi ek-ek lihat CICAK?"},
            {"index": 2, "brief_start_s": 2.5, "brief_end_s": 5.0, "vo": "Banyak orang pikir ngobrol, tapi BUKAN!"},
            {"index": 3, "brief_start_s": 5.0, "brief_end_s": 6.5, "vo": "(payoff tutup fakta)"},
        ]
        # Simulate ASR splitting "ek-ek" into two tokens AND dropping punctuation
        words = [
            {"word": "Kenapa", "start": 0.05, "end": 0.4},
            {"word": "mulut", "start": 0.42, "end": 0.7},
            {"word": "kucing", "start": 0.72, "end": 1.1},
            {"word": "bunyi", "start": 1.12, "end": 1.4},
            {"word": "ek", "start": 1.42, "end": 1.6},
            {"word": "ek", "start": 1.62, "end": 1.85},
            {"word": "lihat", "start": 1.88, "end": 2.1},
            {"word": "cicak", "start": 2.12, "end": 2.5},
            {"word": "Banyak", "start": 2.7, "end": 3.0},
            {"word": "orang", "start": 3.02, "end": 3.3},
            {"word": "pikir", "start": 3.32, "end": 3.6},
            {"word": "ngobrol", "start": 3.62, "end": 4.0},
            {"word": "tapi", "start": 4.05, "end": 4.3},
            {"word": "bukan", "start": 4.32, "end": 4.8},
        ]
        synced = synchronize_scenes_fuzzy(scenes, words, total_duration=6.2)
        self.assertEqual(synced[0]["start_s"], 0.0)
        self.assertEqual(synced[-1]["end_s"], 6.2)
        # Scene 1 ends cleanly between "cicak" (2.5) and "Banyak" (2.7) -> 2.6
        self.assertAlmostEqual(synced[0]["end_s"], 2.6, places=2)
        self.assertEqual(synced[1]["start_s"], synced[0]["end_s"])
        self.assertEqual(synced[2]["start_s"], synced[1]["end_s"])
        self.assertGreater(synced[2]["duration_s"], 0.5)

    def test_dynamic_highlights_not_hardcoded_to_cat_episode(self) -> None:
        scenes = [
            {"index": 1, "vo_raw": "[curious] Kenapa bebek tidur MELEK?", "vo": "Kenapa bebek tidur MELEK?"},
            {"index": 2, "vo_raw": "[excited] Otaknya tidur SEPARUH!", "vo": "Otaknya tidur SEPARUH!"},
        ]
        hl = extract_dynamic_highlights(scenes)
        self.assertIn("MELEK", hl)
        self.assertIn("SEPARUH", hl)
        self.assertIn("SUBSCRIBE", hl)
        self.assertNotIn("CICAK", hl)
        self.assertNotIn("ADRENALIN", hl)

    def test_cleanliness_filter_excludes_dirty_009_clips(self) -> None:
        ep9_assets = self.cfg.episode_assets_dir("009-kucing-ek-cicak", "C1-fakta")
        all_vids = sorted(ep9_assets.glob("*.mp4"))
        clean, excluded = filter_clean_clips(ep9_assets, all_vids, min_cleanliness_score=70)
        clean_names = {p.name for p in clean}
        self.assertNotIn("@whiskerswizardry_7700273278279011598_hd.mp4", clean_names)
        self.assertNotIn("@happyhousecat_7220260573215198507_hd.mp4", clean_names)
        self.assertEqual(len(excluded), 2)
        self.assertEqual(len(clean), len(all_vids) - 2)

    def test_ass_subtitle_generation_and_bgr_conversion(self) -> None:
        self.assertEqual(hex_to_ass_bgr("#FFDD00"), "&H0000DDFF&")
        words = [
            {"word": "di", "start": 0.1, "end": 0.25},
            {"word": "tembok", "start": 0.27, "end": 0.7},
            {"word": "CICAK", "start": 0.72, "end": 1.2},
        ]
        scenes = [{"index": 1, "start_s": 0.0, "end_s": 2.0, "screen_text": "Bunyi EK-EK?!"}]
        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "test.ass"
            vis_grouped = self.c1.visual.model_copy(
                update={"subtitle_mode": "grouped", "highlight_color_hex": "#FFDD00", "include_top_callouts": True}
            )
            generate_ass_subtitles(words, scenes, {"CICAK"}, vis_grouped, ass_p)
            txt = ass_p.read_text(encoding="utf-8")
            self.assertIn("Style: Kinetic", txt)
            self.assertIn("Style: Callout", txt)
            self.assertIn("DI TEMBOK", txt)
            self.assertIn("&H0000DDFF&", txt)

    def test_audio_filtergraph_with_and_without_bgm(self) -> None:
        fg_full = build_audio_filtergraph(
            vo_input_idx=1,
            vo_duration=20.0,
            profile=self.c1,
            bgm_input_idx=2,
            hook_audio_input_idx=3,
            sfx_inputs=[(4, 2.5, 0.55)],
        )
        self.assertIn("sidechaincompress", fg_full)
        self.assertIn("loudnorm=I=-10.8", fg_full)
        self.assertIn("equalizer=f=3600", fg_full)
        self.assertIn("[hook_snd]", fg_full)

        fg_no_bgm = build_audio_filtergraph(
            vo_input_idx=1,
            vo_duration=20.0,
            profile=self.c1,
            bgm_input_idx=None,
            hook_audio_input_idx=None,
            sfx_inputs=[],
        )
        self.assertNotIn("asplit", fg_no_bgm)
        self.assertNotIn("sidechaincompress", fg_no_bgm)
        self.assertIn("loudnorm=I=-10.8", fg_no_bgm)

    def test_detect_cta_window_real_009(self) -> None:
        ep9_assets = self.cfg.episode_assets_dir("009-kucing-ek-cicak", "C1-fakta")
        words = json.loads((ep9_assets / "words_alignment.json").read_text(encoding="utf-8"))
        win = detect_cta_window([], words)
        self.assertIsNotNone(win)
        self.assertGreater(win[0], 6.5)
        self.assertLess(win[0], 8.0)

    def test_v33_transitions_vector_heart_and_bgm_drop(self) -> None:
        self.assertEqual(get_v33_transition_spec(3), ("zoomin", 0.085))
        self.assertEqual(get_v33_transition_spec(1), ("fadewhite", 0.060))
        self.assertEqual(get_v33_transition_spec(2), ("fade", 0.050))

        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "v33.ass"
            generate_ass_subtitles(
                [{"word": "SUBSCRIBE", "start": 7.5, "end": 8.1}],
                [],
                {"SUBSCRIBE"},
                self.c1.visual,
                ass_p,
                cta_window=(7.0, 8.6),
                subject_label="kucing",
            )
            txt = ass_p.read_text(encoding="utf-8")
            self.assertIn("CtaHeartWhite", txt)
            self.assertIn("CtaHeartRed", txt)
            self.assertIn("\\p1", txt)

        fg_drop = build_audio_filtergraph(
            vo_input_idx=1,
            vo_duration=20.0,
            profile=self.c1,
            bgm_input_idx=2,
            hook_audio_input_idx=None,
            sfx_inputs=[],
            bgm_drop_window=(4.8, 5.25),
            bgm_start_s=2.0,
        )
        self.assertIn("between(t,4.800,5.250)", fg_drop)
        self.assertIn("adelay=2000|2000", fg_drop)

        # Verify CTA subscribe triggers ding_chime bell cleanly
        sfx_list = compute_sfx_entries(
            [{"index": 1, "start_s": 0.0, "end_s": 2.0, "sfx": "", "words": []},
             {"index": 2, "start_s": 2.0, "end_s": 4.0, "sfx": "", "words": [{"word": "subscribe", "start": 2.6, "end": 3.1}]}],
            self.cfg.channel_sfx_dir("C1-fakta"),
            {"SUBSCRIBE"},
            cta_window=(2.2, 3.6),
        )
        sfx_names = [p.name for _, p, _ in sfx_list]
        self.assertTrue(any("ding" in n for n in sfx_names))

    def test_acoustic_silence_cutter_and_timestamp_remap(self) -> None:
        sample_stderr = (
            "[silencedetect @ 0x1] silence_start: 0\n"
            "[silencedetect @ 0x1] silence_end: 0.20 | silence_duration: 0.20\n"
            "[silencedetect @ 0x1] silence_start: 2.00\n"
            "[silencedetect @ 0x1] silence_end: 2.60 | silence_duration: 0.60\n"
            "[silencedetect @ 0x1] silence_start: 4.50\n"
        )
        silences = parse_silencedetect_output(sample_stderr, total_dur_s=5.0)
        self.assertEqual(silences, [(0.0, 0.20), (2.0, 2.60), (4.5, 5.0)])

        kept = build_speech_segments(5.0, silences, edge_pad_s=0.025)
        # Expect 2 kept speech islands: [0.175, 2.025] and [2.575, 4.55]
        self.assertEqual(len(kept), 2)
        self.assertAlmostEqual(kept[0][0], 0.175, places=3)
        self.assertAlmostEqual(kept[0][1], 2.025, places=3)
        self.assertAlmostEqual(kept[1][0], 2.575, places=3)
        self.assertAlmostEqual(kept[1][1], 4.55, places=3)

        # Word starting at 2.65s (after 0.55s cut internal silence) shifts cleanly earlier
        mapped = remap_timestamp(2.65, kept, tempo_boost=1.0)
        self.assertAlmostEqual(mapped, (2.025 - 0.175) + (2.65 - 2.575), places=3)

    def test_v35_gamified_321_cta_and_adaptive_proof_pacing(self) -> None:
        # 1. Gamified 3-2-1 CTA generates CtaCountRed/CtaCountWhite + BGM mute window
        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "g321.ass"
            vis_321 = self.c1.visual.model_copy(update={"cta_style": "gamified_321"})
            generate_ass_subtitles(
                [{"word": "SUBSCRIBE", "start": 5.5, "end": 6.1}],
                [],
                {"SUBSCRIBE"},
                vis_321,
                ass_p,
                cta_window=(5.4, 7.2),
                subject_label="kucing",
            )
            txt = ass_p.read_text(encoding="utf-8")
            self.assertIn("CtaCountRed", txt)
            self.assertIn("CtaCountWhite", txt)
            self.assertIn("LIKE  +  SUBSCRIBE", txt)

        fg_mute = build_audio_filtergraph(
            vo_input_idx=1,
            vo_duration=17.0,
            profile=self.c1,
            bgm_input_idx=2,
            hook_audio_input_idx=None,
            sfx_inputs=[],
            bgm_start_s=2.2,
            bgm_mute_window=(5.4, 7.2),
        )
        self.assertIn("between(t,5.400,7.200)", fg_mute)
        self.assertIn("volume=0.05", fg_mute)

        # 2. Adaptive Proof Shot Pacing merges adjacent duplicate body proof clips (<=4.4s) without merging hook/payoff
        scenes = [
            {"index": 1, "start_s": 0.0, "end_s": 2.0, "duration_s": 2.0, "vo": "hook", "words": [], "sfx": ""},
            {"index": 2, "start_s": 2.0, "end_s": 4.0, "duration_s": 2.0, "vo": "mitos", "words": [], "sfx": ""},
            {"index": 3, "start_s": 4.0, "end_s": 6.0, "duration_s": 2.0, "vo": "cta", "words": [], "sfx": ""},
            {"index": 4, "start_s": 6.0, "end_s": 7.5, "duration_s": 1.5, "vo": "p1", "words": [], "sfx": ""},
            {"index": 5, "start_s": 7.5, "end_s": 9.3, "duration_s": 1.8, "vo": "p2a", "words": [], "sfx": ""},
            {"index": 6, "start_s": 9.3, "end_s": 11.2, "duration_s": 1.9, "vo": "p2b", "words": [], "sfx": ""},
            {"index": 7, "start_s": 11.2, "end_s": 13.0, "duration_s": 1.8, "vo": "payoff", "words": [], "sfx": ""},
        ]
        gms = {5: {"filename": "proof_cat.mp4"}, 6: {"filename": "proof_cat.mp4"}}
        merged = apply_adaptive_proof_pacing(scenes, gms, {}, max_proof_s=4.4)
        self.assertEqual(len(merged), 6)
        proof = [s for s in merged if s.get("is_proof_shot")]
        self.assertEqual(len(proof), 1)
        self.assertAlmostEqual(proof[0]["duration_s"], 3.7, places=2)

    def test_golden_moment_v2_shot_boundary_penalty_and_nms(self) -> None:
        # Synthetic 12s clip @ 2fps:
        # - 0.0-4.0s: dark & blurry + internal cut at 2.0s (bad!)
        # - 4.5-7.5s: sharp, bright, clean smooth action (GOLDEN!)
        # - 8.5-11.5s: decent sharp second candidate
        frames = []
        for i in range(24):
            t = i * 0.5
            if t < 4.0:
                frames.append({"t": t, "ydif": 35.0 if t == 2.0 else 1.0, "yavg": 30.0, "satavg": 10.0, "blur": 6.5, "scdet": 20.0 if t == 2.0 else 0.0})
            elif 4.5 <= t <= 7.5:
                frames.append({"t": t, "ydif": 12.0, "yavg": 135.0, "satavg": 50.0, "blur": 1.4, "scdet": 0.0})
            else:
                frames.append({"t": t, "ydif": 6.0, "yavg": 120.0, "satavg": 40.0, "blur": 2.2, "scdet": 0.0})

        cands = score_candidate_windows(12.0, frames, cuts=[2.0], window_dur=2.0, intent="action", top_k=3)
        self.assertGreaterEqual(len(cands), 2)
        # Winner MUST be inside the clean sharp action window [4.5..5.5], NEVER straddling cut at 2.0s
        self.assertGreaterEqual(cands[0]["start_s"], 4.5)
        self.assertLessEqual(cands[0]["start_s"], 5.5)
        self.assertFalse(cands[0]["crosses_cut"])
        # Temporal NMS (IoU < 0.20) ensures second candidate is genuinely distinct from the first
        self.assertGreaterEqual(abs(cands[1]["start_s"] - cands[0]["start_s"]), 1.2)

        # Iterasi 2: Cross-scene anti-repeat exclusion forces Wave 2 on the same clip to pick a non-overlapping window
        wave2 = score_candidate_windows(
            12.0, frames, cuts=[2.0], window_dur=2.0, intent="action", top_k=2,
            excluded_windows=[(cands[0]["start_s"], cands[0]["end_s"])],
        )
        self.assertGreaterEqual(abs(wave2[0]["start_s"] - cands[0]["start_s"]), 1.5)

    def test_golden_moment_v23_cross_clip_rescue_skips_burned_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            for name in ("dirty_text.mp4", "clean_kucing_dorong.mp4", "unrelated_bird.mp4"):
                (td / name).write_bytes(b"0")
            v_index = {
                "dirty_text.mp4": {"tags": ["kucing", "gelas", "dorong"], "summary": "kucing dorong gelas", "has_burned_text": True, "cleanliness": 4},
                "clean_kucing_dorong.mp4": {"tags": ["kucing", "dorong", "gelas", "meja"], "summary": "kucing mendorong gelas di meja", "has_burned_text": False, "cleanliness": 9},
                "unrelated_bird.mp4": {"tags": ["burung", "terbang"], "summary": "burung di langit", "has_burned_text": False, "cleanliness": 10},
            }
            sc = {"index": 5, "visual_desc": "Kucing mendorong gelas di pinggir meja", "vo": "Kucing suka dorong gelas"}
            rescued = pick_best_cross_clip_rescue(sc, "dirty_text.mp4", v_index, td, {})
            self.assertIsNotNone(rescued)
            self.assertEqual(rescued.name, "clean_kucing_dorong.mp4")


if __name__ == "__main__":
    unittest.main()
