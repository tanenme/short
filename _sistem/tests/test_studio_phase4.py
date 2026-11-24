"""Unit and API integration tests for Fase 4: SQLite StudioDB, ElevenLabs TTS aligner, and FastAPI Studio."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

SISTEM_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = SISTEM_DIR / "doksli-web"
for p in (str(SISTEM_DIR), str(WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from core.config import get_config
from core.db import StudioDB
from core.tts import alignment_chars_to_words


class TestStudioPhase4(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = get_config()

    def test_sqlite_db_jobs_and_sync(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = StudioDB(db_path=Path(tmp) / "test_studio.db", config=self.cfg)
            eps = db.sync_channel("C1-fakta")
            self.assertGreaterEqual(len(eps), 13)
            by_id = {e["episode_id"]: e for e in eps}
            self.assertEqual(by_id["009-kucing-ek-cicak"]["preflight_verdict"], "READY_TO_PUBLISH")
            self.assertTrue(by_id["009-kucing-ek-cicak"]["has_output"])
            self.assertTrue(by_id["009-kucing-ek-cicak"]["has_qc_sheet"])

            jid = db.create_job("C1-fakta", "009-kucing-ek-cicak", "render")
            db.update_job(jid, status="running", progress_msg="Rendering...")
            db.update_job(jid, status="succeeded", progress_msg="Done", result={"ok": True, "blur_mean": float("nan")})
            job = db.get_job(jid)
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["result"], {"ok": True, "blur_mean": None})

            # Analytics table accepts metric snapshots cleanly
            db.record_metrics("C1-fakta", "009-kucing-ek-cicak", views=12500, subs_gained=42, avg_view_pct=104.5)

    def test_elevenlabs_char_alignment_skips_audio_tags(self) -> None:
        sample = "[curious] Kenapa kucing ek-ek?"
        chars = list(sample)
        starts = [round(i * 0.05, 3) for i in range(len(chars))]
        ends = [round((i + 1) * 0.05, 3) for i in range(len(chars))]
        words = alignment_chars_to_words(chars, starts, ends)
        self.assertEqual([w["word"] for w in words], ["Kenapa", "kucing", "ek-ek"])
        self.assertGreater(words[0]["start"], 0.4)
        self.assertLess(words[0]["end"], words[1]["start"])

    def test_fastapi_studio_endpoints(self) -> None:
        import server

        client = TestClient(server.app)

        r_home = client.get("/")
        self.assertEqual(r_home.status_code, 200)
        self.assertIn("Enterprise Shorts Studio", r_home.text)

        r_health = client.get("/api/studio/health")
        self.assertEqual(r_health.status_code, 200)
        h = r_health.json()
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["ffmpeg_ready"])
        self.assertGreaterEqual(h["channels_count"], 3)

        r_board = client.get("/api/studio/board?channel_id=C1-fakta&refresh=true")
        self.assertEqual(r_board.status_code, 200)
        b = r_board.json()
        self.assertTrue(b["success"])
        self.assertGreaterEqual(b["summary"]["total"], 13)
        self.assertGreaterEqual(b["summary"]["ready_to_publish"], 1)

        r_status = client.get("/api/editor/status/009-kucing-ek-cicak?channel_id=C1-fakta")
        self.assertEqual(r_status.status_code, 200)
        st = r_status.json()
        self.assertTrue(st["has_output"])
        self.assertTrue(st["has_qc_sheet"])

        r_qc = client.get("/api/editor/qc-sheet/009-kucing-ek-cicak?channel_id=C1-fakta")
        self.assertEqual(r_qc.status_code, 200)
        self.assertEqual(r_qc.headers["content-type"], "image/jpeg")

        r_jobs = client.get("/api/jobs")
        self.assertEqual(r_jobs.status_code, 200)
        self.assertTrue(r_jobs.json()["success"])

        r_ai = client.get("/api/settings/ai")
        self.assertEqual(r_ai.status_code, 200)
        ai_data = r_ai.json()
        self.assertTrue(ai_data["success"])
        s = ai_data["settings"]
        self.assertIn("ai_base_url", s)
        self.assertIn("presets", s)
        self.assertGreaterEqual(len(s["presets"]), 8)
        # Never leak raw API key back to client
        self.assertNotIn("ai_api_key", s)

    def test_ai_provider_settings_persistence_and_masking(self) -> None:
        from core.config import get_ai_provider_settings, load_config, save_ai_provider_settings

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "_sistem" / "channels").mkdir(parents=True)
            cfg = load_config(root_dir=root)
            saved = save_ai_provider_settings(
                {
                    "provider_preset": "custom",
                    "provider_name": "Test Custom Gateway",
                    "ai_base_url": "https://example.invalid/v1/",
                    "ai_api_key": "sk-test-secret-9876",
                    "ai_text_model": "custom-text-v1",
                    "ai_vision_model": "custom-vision-v1",
                    "ai_audio_model": "custom-audio-v1",
                    "auto_fallback": False,
                },
                config=cfg,
            )
            self.assertTrue((root / "_sistem" / "state" / "ai_provider.json").exists())
            # Reload from temp root and verify values loaded & key masked
            reloaded = load_config(root_dir=root, force_reload=True)
            self.assertEqual(reloaded.ai_provider_name, "Test Custom Gateway")
            self.assertEqual(reloaded.ai_base_url, "https://example.invalid/v1")
            self.assertEqual(reloaded.ai_api_key, "sk-test-secret-9876")
            self.assertEqual(reloaded.ai_text_model, "custom-text-v1")
            self.assertFalse(reloaded.ai_auto_fallback)
            pub = get_ai_provider_settings(reloaded)
            self.assertTrue(pub["has_api_key"])
            self.assertEqual(pub["api_key_masked"], "sk-t...9876")
            self.assertNotIn("ai_api_key", pub)
            # Cleanly restore global cached config back to real workspace
            load_config(force_reload=True)


if __name__ == "__main__":
    unittest.main()
