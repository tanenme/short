"""Unit tests for the Forensic Video & Channel Teardown Engine."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SISTEM_DIR = Path(__file__).resolve().parent.parent
EDITOR_DIR = SISTEM_DIR / "editor-engine"
for p in (str(SISTEM_DIR), str(EDITOR_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import load_config
from subtitles import generate_ass_subtitles
from teardown.engine import parse_vtt_word_timings, teardown_channel


class TestForensicTeardown(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(force_reload=True)

    def test_vtt_word_level_cadence_and_gamified_cta_detection(self) -> None:
        vtt = self.cfg.root_dir / "riset" / "transkip" / "kl2qMaSkHC0.id.vtt"
        cad = parse_vtt_word_timings(vtt, total_duration_s=27.6)
        self.assertGreater(cad.total_words, 55)
        self.assertGreater(cad.overall_wpm, 120.0)
        self.assertEqual(cad.cta_mechanic_type, "gamified_easter_egg_hp_bagus")
        self.assertTrue(any("cuy" in s for s in cad.verbal_signatures))
        self.assertGreaterEqual(len(cad.dramatic_pauses), 1)

    def test_channel_outlier_miner_on_real_list(self) -> None:
        list_file = self.cfg.root_dir / "riset" / "latest" / "brightsbrain.list"
        rep = teardown_channel(str(list_file), config=self.cfg)
        self.assertEqual(rep.channel_handle, "brightsbrain")
        self.assertEqual(rep.videos_sampled, 20)
        self.assertEqual(rep.max_views, 1_800_000)
        self.assertGreater(rep.outliers[0].outlier_multiplier, 30.0)
        self.assertTrue(rep.outliers[0].is_viral_outlier)

    def test_rsvp_1word_subtitle_mode_and_grouped_mode(self) -> None:
        c1 = self.cfg.get_channel("C1-fakta")
        self.assertEqual(c1.visual.subtitle_mode, "squooshify_white_3d")
        self.assertEqual(c1.visual.framing_mode, "auto_blur_canvas")
        # Both words are spoken long enough to stand alone (>= rsvp_min_hold_s); sub-min-hold words
        # are intentionally merged by v3.8.1 readability rescue, covered by test_editor.
        words = [
            {"word": "di", "start": 0.1, "end": 0.45},
            {"word": "tembok", "start": 0.47, "end": 0.9},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "rsvp.ass"
            generate_ass_subtitles(words, [], {"TEMBOK"}, c1.visual, ass_p, cta_window=(1.0, 3.0), subject_label="kucing")
            txt = ass_p.read_text(encoding="utf-8")
            self.assertNotIn("DI TEMBOK", txt)
            self.assertIn(",440,1", txt)
            self.assertIn("SUBSCRIBE  +  LIKE", txt)
            self.assertIn("+1 KUCING LIKE!", txt)


if __name__ == "__main__":
    unittest.main()
