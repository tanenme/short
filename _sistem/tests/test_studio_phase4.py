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
            self.assertEqual(len(eps), 13)
            by_id = {e["episode_id"]: e for e in eps}
            self.assertEqual(by_id["009-kucing-ek-cicak"]["preflight_verdict"], "READY_TO_PUBLISH")
            self.assertTrue(by_id["009-kucing-ek-cicak"]["has_output"])
            self.assertTrue(by_id["009-kucing-ek-cicak"]["has_qc_sheet"])

            jid = db.create_job("C1-fakta", "009-kucing-ek-cicak", "render")
            db.update_job(jid, status="running", progress_msg="Rendering...")
            db.update_job(jid, status="succeeded", progress_msg="Done", result={"ok": True})
            job = db.get_job(jid)
            self.assertIsNotNone(job)
            self.assertEqual(job["status"], "succeeded")
            self.assertEqual(job["result"], {"ok": True})

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
        self.assertEqual(b["summary"]["total"], 13)
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


if __name__ == "__main__":
    unittest.main()
