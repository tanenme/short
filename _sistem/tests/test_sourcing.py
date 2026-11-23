"""Unit tests for Fase 3 Sourcing, Licensing, Creator Rotation, AutoQC, and YPP Pre-Flight Gate."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

SISTEM_DIR = Path(__file__).resolve().parent.parent
if str(SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SISTEM_DIR))

from core.config import load_config
from sourcing.licensing import check_creator_rotation, classify_license, generate_credits_markdown
from sourcing.preflight import run_preflight_gate
from sourcing.providers import download_to_episode


class TestSourcingAndPreflight(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(force_reload=True)

    def test_license_classifier_tiers(self) -> None:
        v_cc0 = classify_license("CC0 1.0 Public Domain")
        self.assertEqual(v_cc0.risk_level, "LOW")
        self.assertTrue(v_cc0.monetization_safe)
        self.assertFalse(v_cc0.attribution_required)

        v_by = classify_license("CC BY 4.0")
        self.assertEqual(v_by.risk_level, "LOW")
        self.assertTrue(v_by.monetization_safe)
        self.assertTrue(v_by.attribution_required)

        v_sa = classify_license("CC BY-SA 3.0")
        self.assertEqual(v_sa.risk_level, "MEDIUM")
        self.assertTrue(v_sa.monetization_safe)
        self.assertTrue(v_sa.share_alike_required)

        v_nc = classify_license("CC BY-NC 4.0")
        self.assertEqual(v_nc.risk_level, "BLOCKED")
        self.assertFalse(v_nc.monetization_safe)

        v_nd = classify_license("CC BY-ND 4.0")
        self.assertEqual(v_nd.risk_level, "BLOCKED")
        self.assertFalse(v_nd.monetization_safe)

        v_pexels = classify_license("", "pexels")
        self.assertEqual(v_pexels.risk_level, "LOW")
        self.assertTrue(v_pexels.monetization_safe)

        v_tt = classify_license("", "tiktok")
        self.assertEqual(v_tt.risk_level, "HIGH")
        self.assertFalse(v_tt.monetization_safe)

    def test_download_blocks_nc_nd_licenses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "_sistem" / "channels").mkdir(parents=True)
            (tmp_root / "_sistem" / "channels" / "C1-fakta.toml").write_text(
                (SISTEM_DIR / "channels" / "C1-fakta.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            tmp_cfg = load_config(root_dir=tmp_root, force_reload=True)
            with self.assertRaises(ValueError):
                download_to_episode(
                    url="https://example.com/clip.mp4",
                    episode_id="099-test",
                    channel_id="C1-fakta",
                    license_str="CC BY-NC 4.0",
                    config=tmp_cfg,
                )

    def test_creator_rotation_detects_cross_episode_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "_sistem" / "channels").mkdir(parents=True)
            (tmp_root / "_sistem" / "channels" / "C1-fakta.toml").write_text(
                (SISTEM_DIR / "channels" / "C1-fakta.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            for ep in ("001-a", "002-b"):
                ad = tmp_root / "C1-fakta" / ep / "assets"
                ad.mkdir(parents=True)
                (ad / "@samecreator_123456_hd.mp4").write_bytes(b"fake")
            tmp_cfg = load_config(root_dir=tmp_root, force_reload=True)
            rot = check_creator_rotation("002-b", "C1-fakta", window_days=7, config=tmp_cfg)
            self.assertIn("@samecreator", rot["cross_episode_overlaps_7d"])
            self.assertIn("001-a", rot["cross_episode_overlaps_7d"]["@samecreator"])

    def test_preflight_real_009_ready_to_publish_and_012_blocked(self) -> None:
        rep009 = run_preflight_gate("009-kucing-ek-cicak", "C1-fakta", run_autoqc=False, config=self.cfg, write_report=False)
        self.assertTrue(rep009.can_render)
        self.assertTrue(rep009.can_publish)
        self.assertEqual(rep009.stage_verdict, "READY_TO_PUBLISH")

        rep012 = run_preflight_gate("012-ayam-kepala-stabil", "C1-fakta", run_autoqc=False, config=self.cfg, write_report=False)
        self.assertFalse(rep012.can_render)
        self.assertEqual(rep012.stage_verdict, "BLOCKED")
        codes = {c.code for c in rep012.checks if c.severity == "FAIL"}
        self.assertIn("USABLE_CLIPS_LOW", codes)

    def test_credits_markdown_generation_dry_run(self) -> None:
        md, out = generate_credits_markdown("009-kucing-ek-cicak", "C1-fakta", self.cfg, write_file=False)
        self.assertIsNone(out)
        self.assertIn("# Kredit & Atribusi Materi — 009-kucing-ek-cicak", md)
        self.assertIn("Ringkasan Risiko Lisensi", md)


if __name__ == "__main__":
    unittest.main()
