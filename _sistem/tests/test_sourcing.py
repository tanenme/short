"""Unit tests for Fase 3 Sourcing, Licensing, Creator Rotation, AutoQC, and YPP Pre-Flight Gate."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

SISTEM_DIR = Path(__file__).resolve().parent.parent
WEB_DIR = SISTEM_DIR / "doksli-web"
for _p in (str(SISTEM_DIR), str(WEB_DIR)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from core.config import load_config
from core.db import StudioDB
from sourcing.licensing import check_creator_rotation, classify_license, generate_credits_markdown
from sourcing.preflight import run_preflight_gate
from sourcing.providers import SourcedCandidate, compute_ready_score, download_to_episode, search_local_vault
from sourcing.query_planner import plan_visual_queries
from sourcing.vision_gate import auto_trim_and_lock_golden_moment, build_three_frame_strip_data_url


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

    def test_query_planner_causal_pruning_dog_kicking_dirt(self) -> None:
        naskah = (
            "[curious] Kenapa anjing suka nendang TANAH habis buang air?\n"
            "Bukan buat nutupin kotorannya lho!\n"
            "Klik like dulu kalau kamu sayang anjing!\n"
            "Telapak kaki anjing punya kelenjar feromon rahasia.\n"
            "Makanya sekali garuk tanah, aromanya langsung menandai wilayah!"
        )
        plan = plan_visual_queries(naskah, use_llm=False, config=self.cfg)
        self.assertEqual(plan.primary_subject, "dog")
        self.assertGreaterEqual(len(plan.beats), 4)

        # Causal pruning must strip "habis buang air" and NEVER pollute stock queries with "poop"
        self.assertTrue(any("buang air" in s for s in plan.beats[0].visual_intent.stripped_clauses))
        for q in plan.queries.conceptual_stock:
            self.assertNotIn("poop", q.lower())
            self.assertNotIn("buang air", q.lower())
        self.assertTrue(
            any("digging dirt" in q.lower() or "scratching ground" in q.lower() for q in plan.queries.conceptual_stock)
        )

        # Negative action terms must explicitly guard against the "Dog Eating / Rolling Grass" trap
        for neg in ("eating", "rolling", "pooping"):
            self.assertIn(neg, plan.negative_terms)

    def test_query_planner_episode_007_cat_cardboard_box_offline_and_inheritance(self) -> None:
        naskah = (
            "[curious, fast-paced] Kenapa kucing selalu pilih KARDUS sempit daripada kasur mahal?\n"
            "[skeptical, then firm] Banyak orang pikir itu kebiasaan aneh, padahal BUKAN!\n"
            "[warm, conversational] Tapi kalau kamu suka kucing, Subscribe ya.\n"
            "[intrigued] Kotak sempit bikin tubuh kucing hangat dan aman dari predator.\n"
            "[punchy] Makanya kucing langsung masuk kardus baru."
        )
        plan = plan_visual_queries(naskah, use_llm=False, config=self.cfg)
        self.assertEqual(plan.primary_subject, "cat")
        self.assertIn("cardboard box", plan.primary_intent.target_object.lower())
        self.assertTrue(
            any("box" in q.lower() for q in plan.queries.conceptual_stock),
            f"Expected cardboard box queries, got {plan.queries.conceptual_stock}",
        )
        self.assertTrue(len(plan.negative_terms) >= 3)
        # Abstract contrast beat (U2) must inherit cardboard box context instead of generic "natural behavior"
        u2 = plan.beats[1]
        self.assertEqual(u2.role, "contrast")
        self.assertIn("box", u2.visual_intent.target_object.lower())
        self.assertTrue(len(u2.visual_intent.negative_terms) >= 3)

    def test_ready_score_blocks_dog_eating_and_rolling_traps(self) -> None:
        negs = ["eating", "chewing", "rolling", "sleeping", "pooping"]
        trap_eat = SourcedCandidate(
            platform="pexels",
            title="Cute Dog Eating Grass in Sunny Park",
            url="https://cdn.example.com/eat.mp4",
            license="Pexels License",
            risk_level="LOW",
            monetization_safe=True,
            width=1080,
            height=1920,
            duration_s=5.0,
        )
        trap_roll = SourcedCandidate(
            platform="pexels",
            title="Happy Dog Rolling on Grass",
            url="https://cdn.example.com/roll.mp4",
            license="Pexels License",
            risk_level="LOW",
            monetization_safe=True,
            width=1080,
            height=1920,
            duration_s=6.0,
        )
        good_dig = SourcedCandidate(
            platform="pexels",
            title="Dog Digging Dirt Scratching Ground with Paws",
            url="https://cdn.example.com/dig.mp4",
            license="Pexels License",
            risk_level="LOW",
            monetization_safe=True,
            width=1080,
            height=1920,
            duration_s=4.5,
        )

        compute_ready_score(trap_eat, "dog digging dirt", negative_terms=negs)
        compute_ready_score(trap_roll, "dog digging dirt", negative_terms=negs)
        compute_ready_score(good_dig, "dog digging dirt", negative_terms=negs)

        self.assertEqual(trap_eat.negative_hit, "eating")
        self.assertEqual(trap_eat.ready_status, "REJECTED")
        self.assertLessEqual(trap_eat.score, 39.0)

        self.assertEqual(trap_roll.negative_hit, "rolling")
        self.assertEqual(trap_roll.ready_status, "REJECTED")
        self.assertLessEqual(trap_roll.score, 39.0)

        self.assertEqual(good_dig.negative_hit, "")
        self.assertEqual(good_dig.ready_status, "READY")
        self.assertGreaterEqual(good_dig.score, 78.0)

    def test_broll_vault_sync_and_local_search(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = StudioDB(db_path=Path(tmp) / "vault_test.db", config=self.cfg)
            indexed = db.sync_broll_vault("C1-fakta")
            self.assertGreaterEqual(indexed, 5)
            rows = db.list_broll_vault(exclude_episode_id="009-kucing-ek-cicak")
            self.assertTrue(all(r["episode_id"] != "009-kucing-ek-cicak" for r in rows))

    def test_three_frame_strip_and_auto_trim_golden_moment_bridge(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            assets_dir = Path(tmp) / "assets"
            assets_dir.mkdir(parents=True)
            bloated_mp4 = assets_dir / "stock_bloated_10s.mp4"
            subprocess.run(
                [
                    self.cfg.ffmpeg_cmd(),
                    "-y",
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "testsrc2=size=640x360:rate=24:duration=10",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:duration=10",
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    str(bloated_mp4),
                ],
                check=True,
                timeout=30,
            )

            strip_url = build_three_frame_strip_data_url([], local_video_path=bloated_mp4, cfg=self.cfg)
            self.assertIsNotNone(strip_url)
            self.assertTrue(strip_url.startswith("data:image/jpeg;base64,"))

            trim_res = auto_trim_and_lock_golden_moment(
                bloated_mp4,
                episode_id="008-anjing-tendang-tanah",
                channel_id="C1-fakta",
                beat_index=1,
                scenario="dog digging dirt",
                crop_x_norm_hint=0.55,
                target_trim_s=3.8,
                trim_threshold_s=8.5,
                cfg=self.cfg,
            )
            self.assertTrue(trim_res["trimmed"])
            self.assertTrue(trim_res["golden_moment_locked"])
            ready_path = Path(trim_res["trimmed_path"])
            self.assertTrue(ready_path.exists())
            self.assertIn("_ready.mp4", ready_path.name)

            gm_file = assets_dir / "golden_moments.json"
            self.assertTrue(gm_file.exists())
            gm_data = json.loads(gm_file.read_text(encoding="utf-8"))
            self.assertIn("1", gm_data)
            self.assertEqual(gm_data["1"]["filename"], ready_path.name)
            self.assertLessEqual(gm_data["1"]["end_s"] - gm_data["1"]["start_s"], 4.8)

    def test_fastapi_sourcing_v4_endpoints(self) -> None:
        import server

        client = TestClient(server.app)
        r_plan = client.post(
            "/api/sourcing/plan-queries",
            json={
                "naskah": "Kenapa anjing suka nendang TANAH habis buang air? Klik like dulu!",
                "use_llm": False,
            },
        )
        self.assertEqual(r_plan.status_code, 200)
        pdata = r_plan.json()
        self.assertTrue(pdata["success"])
        self.assertEqual(pdata["primary_subject"], "dog")
        self.assertIn("eating", pdata["negative_terms"])
        self.assertTrue(len(pdata["queries"]["conceptual_stock"]) >= 1)

        r_vb = client.post(
            "/api/sourcing/vision-batch",
            json={
                "scenario": "dog digging dirt",
                "negative_terms": ["eating", "rolling"],
                "candidates": [
                    {
                        "platform": "pexels",
                        "title": "Dog Eating Grass Closeup",
                        "url": "https://cdn.example.com/eat.mp4",
                        "license": "Pexels License",
                        "risk_level": "LOW",
                        "monetization_safe": True,
                    },
                    {
                        "platform": "pexels",
                        "title": "Dog Digging Dirt in Yard",
                        "url": "https://cdn.example.com/dig.mp4",
                        "license": "Pexels License",
                        "risk_level": "LOW",
                        "monetization_safe": True,
                        "width": 1080,
                        "height": 1920,
                        "duration_s": 4.2,
                    },
                ],
            },
        )
        self.assertEqual(r_vb.status_code, 200)
        vb = r_vb.json()
        self.assertTrue(vb["success"])
        self.assertEqual(vb["count"], 1)
        self.assertEqual(vb["rejected_count"], 1)
        self.assertEqual(vb["results"][0]["title"], "Dog Digging Dirt in Yard")
        self.assertEqual(vb["rejected"][0]["negative_hit"], "eating")


if __name__ == "__main__":
    unittest.main()
