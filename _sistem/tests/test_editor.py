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

from aligner import detect_cta_window, extract_dynamic_highlights, synchronize_scenes_fuzzy
from audio_mix import build_audio_filtergraph
from core.config import get_config
from subtitles import generate_ass_subtitles, hex_to_ass_bgr
from video_fx import filter_clean_clips


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
            generate_ass_subtitles(words, scenes, {"CICAK"}, self.c1.visual, ass_p)
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
        self.assertIn("loudnorm=I=-14.0", fg_full)
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
        self.assertIn("loudnorm=I=-14.0", fg_no_bgm)

    def test_detect_cta_window_real_009(self) -> None:
        ep9_assets = self.cfg.episode_assets_dir("009-kucing-ek-cicak", "C1-fakta")
        words = json.loads((ep9_assets / "words_alignment.json").read_text(encoding="utf-8"))
        win = detect_cta_window([], words)
        self.assertIsNotNone(win)
        self.assertGreater(win[0], 6.5)
        self.assertLess(win[0], 8.0)


if __name__ == "__main__":
    unittest.main()
