"""Unit tests for the Enterprise Shorts Studio core layer."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SISTEM_DIR = Path(__file__).resolve().parent.parent
if str(SISTEM_DIR) not in sys.path:
    sys.path.insert(0, str(SISTEM_DIR))

from core.config import load_config
from core.models import ChannelProfile
from core.validator import analyze_script, audit_episode_folder, extract_highlight_words


class TestStudioCore(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = load_config(force_reload=True)
        self.c1 = self.cfg.get_channel("C1-fakta")

    def test_channels_loaded(self) -> None:
        self.assertIn("C1-fakta", self.cfg.channels)
        self.assertIn("C2-momen", self.cfg.channels)
        self.assertIn("C3-reddit", self.cfg.channels)
        self.assertEqual(self.c1.voice.engine, "eleven_v4")
        self.assertEqual(self.c1.pacing.min_words, 40)

    def test_legacy_dir_resolution(self) -> None:
        bgm = self.cfg.channel_bgm_dir("C1-fakta")
        sfx = self.cfg.channel_sfx_dir("C1-fakta")
        overlays = self.cfg.channel_overlays_dir("C1-fakta")
        self.assertTrue(bgm.exists(), f"BGM dir should resolve: {bgm}")
        self.assertTrue(sfx.exists(), f"SFX dir should resolve: {sfx}")
        self.assertTrue(overlays.exists(), f"Overlays dir should resolve: {overlays}")
        ep11_assets = self.cfg.episode_assets_dir("011-bebek-tidur-melek", "C1-fakta")
        self.assertTrue(ep11_assets.exists())
        self.assertEqual(ep11_assets.name, "assets")

        # Legacy `asset/` fallback still works when `assets/` does not exist
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "_sistem" / "channels").mkdir(parents=True)
            (tmp_root / "_sistem" / "channels" / "C1-fakta.toml").write_text(
                (SISTEM_DIR / "channels" / "C1-fakta.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            legacy_asset = tmp_root / "C1-fakta" / "999-legacy" / "asset"
            legacy_asset.mkdir(parents=True)
            tmp_cfg = load_config(root_dir=tmp_root, force_reload=True)
            resolved = tmp_cfg.episode_assets_dir("999-legacy", "C1-fakta")
            self.assertEqual(resolved, legacy_asset)

    def test_script_validator_passes_viral_formula(self) -> None:
        vo = (
            "[curious, fast-paced] Kenapa mulut kucing bunyi ek-ek lihat CICAK?\n"
            "[skeptical, then firm] Banyak orang pikir ngajak ngobrol, tapi BUKAN!\n"
            "[warm, conversational] Tapi kalau kamu suka kucing, Subscribe ya.\n"
            "[intrigued, hushed revelation] Suara itu luapan ADRENALIN saat mangsanya terhalang tembok... "
            "[excited] Rahangnya refleks latihan MENERKAM sambil menghirup bau mangsa!\n"
            "[punchy, amused conclusion] Makanya pas cicaknya kabur, getarannya langsung BERHENTI."
        )
        spec, checks = analyze_script(vo, self.c1)
        self.assertEqual(spec.word_count, 44)
        self.assertEqual(
            spec.highlight_words,
            ["CICAK", "BUKAN", "ADRENALIN", "MENERKAM", "BERHENTI"],
        )
        fails = [c for c in checks if c.severity == "FAIL"]
        self.assertEqual(fails, [])

    def test_script_validator_catches_double_cta_and_too_long(self) -> None:
        long_body = " ".join(["kata"] * 62)
        vo = (
            "[curious] Kenapa kucing melompat? "
            "Kalau kamu suka kucing, Subscribe ya. "
            f"{long_body}. "
            "Jangan lupa Subscribe juga di akhir!"
        )
        _, checks = analyze_script(vo, self.c1)
        codes = {c.code: c.severity for c in checks}
        self.assertEqual(codes.get("SCRIPT_TOO_LONG"), "FAIL")
        self.assertEqual(codes.get("CTA_DOUBLE"), "FAIL")

    def test_extract_highlight_words_skips_metadata_tokens(self) -> None:
        words = extract_highlight_words("[excited] Jam empat SUBUH WIB di CCTV, bukan BUKAN!")
        self.assertEqual(words, ["SUBUH", "BUKAN"])

    def test_audit_real_episode_009(self) -> None:
        manifest, report = audit_episode_folder("009-kucing-ek-cicak", "C1-fakta", self.cfg)
        self.assertEqual(manifest.episode_id, "009-kucing-ek-cicak")
        self.assertTrue(report.passed, f"Expected 009 to pass gate, got: {report.checks}")
        self.assertEqual(manifest.script.word_count, 44)
        self.assertGreaterEqual(len(manifest.fact_sources), 2)
        self.assertGreaterEqual(len(manifest.clips), 10)

    def test_no_hardcoded_exa_key_in_html(self) -> None:
        html = (SISTEM_DIR / "doksli-web" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("3e315b0b-", html)

    def test_doctor_mask_never_leaks_secret(self) -> None:
        import studio

        self.assertEqual(studio._mask("super-secret-api-key-12345"), "TERSET")
        self.assertEqual(studio._mask(""), "BELUM DISET")


if __name__ == "__main__":
    unittest.main()
