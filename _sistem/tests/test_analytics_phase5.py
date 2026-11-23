"""Unit and integration tests for Fase 5: Growth Analytics Feedback Loop & Multi-Channel Scaling."""

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

from analytics.engine import (
    compute_channel_analytics,
    compute_virality_score,
    generate_weekly_report,
    import_youtube_csv,
)
from analytics.scaler import provision_channel
from core.config import get_config, load_config
from core.db import StudioDB


class TestAnalyticsAndScalingPhase5(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = get_config()

    def test_virality_score_and_clone_ready_verdict(self) -> None:
        high = compute_virality_score(views=600_000, subs_gained=1200, avg_view_pct=112.0, swipe_away_pct=18.0)
        low = compute_virality_score(views=400, subs_gained=0, avg_view_pct=55.0, swipe_away_pct=60.0)
        self.assertGreater(high, 80.0)
        self.assertLess(low, 35.0)

        with tempfile.TemporaryDirectory() as tmp:
            db = StudioDB(db_path=Path(tmp) / "an.db", config=self.cfg)
            # Empty -> NO_DATA
            s0 = compute_channel_analytics("C1-fakta", db, self.cfg)
            self.assertEqual(s0.validation_verdict, "NO_DATA")

            # Seed viral kucing hits + weak sapi video
            db.record_metrics("C1-fakta", "009-kucing-ek-cicak", views=620_000, subs_gained=1150, avg_view_pct=114.0, swipe_away_pct=17.0)
            db.record_metrics("C1-fakta", "010-kucing-dorong-gelas", views=85_000, subs_gained=180, avg_view_pct=103.0, swipe_away_pct=21.0)
            db.record_metrics("C1-fakta", "013-sapi-mengunyah-rumen", views=600, subs_gained=0, avg_view_pct=60.0, swipe_away_pct=58.0)

            s1 = compute_channel_analytics("C1-fakta", db, self.cfg)
            self.assertEqual(s1.validation_verdict, "CLONE_READY")
            self.assertEqual(s1.total_subs, 1330)
            self.assertEqual(s1.top_episodes[0].episode_id, "009-kucing-ek-cicak")
            actions = {x.subject: x.action for x in s1.subject_breakdown}
            self.assertEqual(actions["kucing"], "SCALE")
            self.assertEqual(actions["sapi"], "KILL_OR_REWORK")

            md, _ = generate_weekly_report("C1-fakta", db, self.cfg, write_file=False)
            self.assertIn("CLONE_READY", md)
            self.assertIn("009-kucing-ek-cicak", md)

    def test_youtube_csv_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            db = StudioDB(db_path=tmp_p / "csv.db", config=self.cfg)
            csv_file = tmp_p / "yt_export.csv"
            csv_file.write_text(
                "episode_id,Views,Likes,Comments,Subscribers,Average percentage viewed (%),Swiped away (%)\n"
                "009-kucing-ek-cicak,25400,1200,85,78,107.5,20.2\n"
                "008-anjing-tendang-tanah,9800,410,22,19,98.1,26.5\n",
                encoding="utf-8",
            )
            res = import_youtube_csv(csv_file, "C1-fakta", db, self.cfg)
            self.assertEqual(res["imported"], 2)
            s = compute_channel_analytics("C1-fakta", db, self.cfg)
            self.assertEqual(s.tracked_videos, 2)
            self.assertEqual(s.total_views, 35200)
            self.assertEqual(s.total_subs, 97)

    def test_provision_channel_clones_structure_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_root = Path(tmp)
            (tmp_root / "_sistem" / "channels").mkdir(parents=True)
            (tmp_root / "_sistem" / "channels" / "C1-fakta.toml").write_text(
                (SISTEM_DIR / "channels" / "C1-fakta.toml").read_text(encoding="utf-8"),
                encoding="utf-8",
            )
            # Fake shared BGM/SFX/overlay in C1-fakta
            for sub, fname in [("bgm", "track.mp3"), ("sfx", "pop.mp3"), ("_overlays", "sub_greenscreen.mp4")]:
                p = tmp_root / "C1-fakta" / sub
                p.mkdir(parents=True, exist_ok=True)
                (p / fname).write_bytes(b"mock-media")

            tmp_cfg = load_config(root_dir=tmp_root, force_reload=True)
            res = provision_channel(
                channel_id="C4-sains",
                display_name="C4 — Fakta Sains Cepat",
                niche="sains_kilat",
                source_channel="C1-fakta",
                copy_audio_bank=True,
                copy_overlays=True,
                activate=True,
                config=tmp_cfg,
            )
            self.assertTrue(res["success"])
            self.assertTrue(res["created_profile"])
            self.assertEqual(res["copied_shared_assets"], 3)
            self.assertTrue((tmp_root / "C4-sains" / "bgm" / "track.mp3").exists())
            self.assertTrue((tmp_root / "C4-sains" / "_overlays" / "sub_greenscreen.mp4").exists())
            self.assertTrue((tmp_root / "_sistem" / "channels" / "C4-sains.toml").exists())

    def test_fastapi_analytics_summary_endpoint(self) -> None:
        import server

        client = TestClient(server.app)
        r = client.get("/api/analytics/summary?channel_id=C1-fakta")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["success"])
        self.assertIn("validation_verdict", d["summary"])
        self.assertIn("creative_directives", d["summary"])


if __name__ == "__main__":
    unittest.main()
