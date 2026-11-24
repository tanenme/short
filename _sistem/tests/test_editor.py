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

from aligner import (
    apply_adaptive_proof_pacing,
    apply_intra_unit_clip_splitting,
    detect_bgm_vacuum_windows,
    detect_cta_window,
    extract_dynamic_highlights,
    synchronize_scenes_fuzzy,
)
from audio_mix import build_audio_filtergraph, compute_sfx_entries
from core.config import get_config
from moment_finder import (
    compute_semantic_match_score,
    pick_best_cross_clip_rescue,
    score_candidate_windows,
    select_subclip2_golden_moment,
    split_clip_into_shots,
)
from subtitles import generate_ass_subtitles, hex_to_ass_bgr
from video_fx import (
    compute_selective_speed,
    filter_clean_clips,
    get_v33_transition_spec,
    plan_v37_transitions,
)
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

    def test_v36_intra_unit_split_selective_speed_and_transitions(self) -> None:
        # 1. Selective speed-up: proof & cta MUST stay 1.0x; process gets 1.25x (<=1.35x cap); hook gets 1.12x
        self.assertEqual(compute_selective_speed("proof", clip_dur=10.0, target_dur=3.5), 1.0)
        self.assertEqual(compute_selective_speed("cta", clip_dur=10.0, target_dur=2.0), 1.0)
        self.assertEqual(compute_selective_speed("process", clip_dur=10.0, target_dur=2.0, max_animal_speed=1.35), 1.25)
        self.assertEqual(compute_selective_speed("hook", clip_dur=10.0, target_dur=2.0), 1.12)

        # 2. Intra-unit transition MUST be clean fade (0.050s), NEVER zoomin/fadewhite!
        self.assertEqual(get_v33_transition_spec(3, is_intra_unit=True), ("fade", 0.050))
        self.assertEqual(get_v33_transition_spec(3, is_intra_unit=False), ("zoomin", 0.085))

        # 3. Intra-unit splitting splits a 3.2s process unit on VO keyword while preserving proof & CTA intact
        scenes = [
            {
                "index": 1, "start_s": 0.0, "end_s": 3.2, "duration_s": 3.2,
                "visual_desc": "Anjing menendang tanah cepat", "vo": "Banyak orang pikir tapi bukan begitu",
                "words": [
                    {"word": "Banyak", "start": 0.1, "end": 0.5},
                    {"word": "orang", "start": 0.55, "end": 0.9},
                    {"word": "pikir", "start": 0.95, "end": 1.4},
                    {"word": "tapi", "start": 1.55, "end": 1.9},
                    {"word": "bukan", "start": 1.95, "end": 2.4},
                    {"word": "begitu", "start": 2.45, "end": 3.1},
                ],
            },
            {
                "index": 2, "start_s": 3.2, "end_s": 6.9, "duration_s": 3.7,
                "is_proof_shot": True, "visual_desc": "Bukti fisik", "vo": "Ini bukti aslinya",
                "words": [{"word": "Ini", "start": 3.3, "end": 4.0}, {"word": "bukti", "start": 4.1, "end": 5.0},
                          {"word": "aslinya", "start": 5.1, "end": 6.0}, {"word": "jelas", "start": 6.1, "end": 6.8}],
            },
        ]
        split_out = apply_intra_unit_clip_splitting(scenes, min_unit_split_s=2.65, min_subclip_s=0.85)
        self.assertEqual(len(split_out), 3)
        self.assertEqual(split_out[0]["unit_id"], split_out[1]["unit_id"])
        self.assertTrue(split_out[1]["is_intra_unit_cut"])
        self.assertEqual(split_out[1]["split_trigger_word"], "tapi")
        self.assertGreaterEqual(split_out[0]["duration_s"], 0.85)
        self.assertGreaterEqual(split_out[1]["duration_s"], 0.85)
        # Proof shot stayed intact (NOT split!)
        self.assertEqual(split_out[2]["scene_role"], "proof")
        self.assertAlmostEqual(split_out[2]["duration_s"], 3.7, places=2)

    def test_golden_moment_v24_synonym_matching_shot_splitting_and_subclip2(self) -> None:
        # 1. Per-shot atomic segmentation
        shots = split_clip_into_shots(12.0, [3.2, 7.8])
        self.assertEqual(len(shots), 3)
        self.assertAlmostEqual(shots[0]["end_s"], 3.2, places=2)
        self.assertAlmostEqual(shots[1]["start_s"], 3.2, places=2)
        self.assertAlmostEqual(shots[2]["start_s"], 7.8, places=2)

        # 2. Bilingual synonym concept matching: zero exact word overlap ("pupil melebar" vs "mata membesar") still matches!
        syn_sc = compute_semantic_match_score("Pupil melebar saat mengincar", "Close up mata membesar fokus")
        unrelated_sc = compute_semantic_match_score("Pupil melebar saat mengincar", "Burung terbang di awan biru")
        self.assertGreater(syn_sc, 25.0)
        self.assertEqual(unrelated_sc, 0.0)

        # 3. Cross-clip rescue uses per-shot synonym matching AND respects caption_zone_clean gate
        with tempfile.TemporaryDirectory() as tmp:
            td = Path(tmp)
            for name in ("cluttered_caption.mp4", "synonym_shot_match.mp4"):
                (td / name).write_bytes(b"0")
            v_index = {
                "cluttered_caption.mp4": {
                    "tags": ["pupil", "melebar"],
                    "summary": "pupil melebar",
                    "has_burned_text": False,
                    "caption_zone_clean": False,
                    "cleanliness": 9,
                },
                "synonym_shot_match.mp4": {
                    "tags": ["hewan"],
                    "summary": "klip kompilasi",
                    "has_burned_text": False,
                    "caption_zone_clean": True,
                    "cleanliness": 9,
                    "shots": [
                        {"shot_id": 1, "start_s": 0.0, "end_s": 3.0, "summary": "tidur santai", "tags": ["tidur"]},
                        {"shot_id": 2, "start_s": 3.0, "end_s": 7.0, "summary": "mata kucing membesar fokus", "tags": ["mata", "fokus"]},
                    ],
                },
            }
            sc = {"index": 4, "visual_desc": "Pupil melebar tajam", "vo": "Pupilnya langsung melebar"}
            rescued = pick_best_cross_clip_rescue(sc, "orig.mp4", v_index, td, {})
            self.assertIsNotNone(rescued)
            self.assertEqual(rescued.name, "synonym_shot_match.mp4")

        # 4. Script-aware Intra-Unit Sub-Clip #2 picks the high-motion non-overlapping candidate when VO shifts to action
        parent_gm = {
            "start_s": 1.0,
            "crop_x_norm": 0.62,
            "signals": {
                "clip_dur_s": 10.0,
                "top_candidates": [
                    {"start_s": 1.0, "end_s": 2.5, "score": 78.0, "motion": 40.0, "sharpness": 80.0, "exposure": 90.0},
                    {"start_s": 3.5, "end_s": 5.0, "score": 74.0, "motion": 30.0, "sharpness": 85.0, "exposure": 90.0},
                    {"start_s": 6.5, "end_s": 8.0, "score": 72.0, "motion": 95.0, "sharpness": 78.0, "exposure": 90.0},
                ],
            },
        }
        sub2_sc = {
            "index": 2,
            "duration_s": 1.4,
            "vo": "tapi langsung kabur lari cepat",
            "split_trigger_word": "tapi",
            "visual_desc": "Cicak kabur",
        }
        sub2_res = select_subclip2_golden_moment(
            subclip2_scene=sub2_sc,
            parent_gm=parent_gm,
            assets_dir=Path("."),
            default_clip_path=Path("gecko.mp4"),
            clips=[Path("gecko.mp4")],
        )
        self.assertEqual(sub2_res["start_s"], 6.5)
        self.assertEqual(sub2_res["crop_x_norm"], 0.62)
        self.assertEqual(sub2_res["mode"], "v24_subclip2_action")

    def test_v37_word_onset_cut_lock_and_single_flash_budget(self) -> None:
        scenes = [
            {"index": 1, "brief_start_s": 0.0, "brief_end_s": 2.5, "vo": "Kenapa kucing lihat cicak?"},
            {"index": 2, "brief_start_s": 2.5, "brief_end_s": 5.0, "vo": "Makanya mulutnya bunyi cepat!"},
        ]
        words = [
            {"word": "Kenapa", "start": 0.05, "end": 0.4},
            {"word": "kucing", "start": 0.45, "end": 0.9},
            {"word": "lihat", "start": 0.95, "end": 1.4},
            {"word": "cicak", "start": 1.45, "end": 2.20},
            # Intentional 0.50s rhetorical pause [2.20 .. 2.70] before "Makanya"
            {"word": "Makanya", "start": 2.70, "end": 3.15},
            {"word": "mulutnya", "start": 3.20, "end": 3.65},
            {"word": "bunyi", "start": 3.70, "end": 4.10},
            {"word": "cepat", "start": 4.15, "end": 4.70},
        ]
        locked = synchronize_scenes_fuzzy(
            scenes, words, total_duration=5.0, cut_on_word_lock=True, word_onset_lead_s=0.033
        )
        # Cut MUST lock 1 frame (0.033s) before "Makanya" (2.70 - 0.033 = 2.667s), NOT midpoint 2.45s!
        self.assertAlmostEqual(locked[0]["end_s"], 2.667, places=3)
        self.assertAlmostEqual(locked[1]["start_s"], 2.667, places=3)

        # Single-Reveal Flash Budget: max 1 fadewhite on payoff entry, ignoring intra-unit cuts
        plan_scenes = [
            {"index": 1, "scene_role": "hook", "is_intra_unit_cut": False},
            {"index": 2, "scene_role": "process", "is_intra_unit_cut": False},
            {"index": 2, "scene_role": "process", "is_intra_unit_cut": True},
            {"index": 3, "scene_role": "proof", "is_intra_unit_cut": False},
            {"index": 4, "scene_role": "payoff", "is_intra_unit_cut": False},
        ]
        flash_plan = plan_v37_transitions(plan_scenes, max_flash_transitions=1)
        self.assertEqual(len(flash_plan), 1)
        self.assertIn(4, flash_plan)
        self.assertEqual(flash_plan[4], ("fadewhite", 0.060))

    def test_v37_rhetorical_pause_bgm_vacuum_and_rsvp_blanking(self) -> None:
        # 1. Selective rhetorical pause preservation before "ternyata" while splicing regular pauses
        silences = [(1.00, 1.60), (3.00, 3.60)]
        words = [
            {"word": "biasa", "start": 1.62, "end": 2.00},
            {"word": "Ternyata", "start": 3.62, "end": 4.10},
        ]
        kept = build_speech_segments(5.0, silences, edge_pad_s=0.025, words=words, pivot_pause_s=0.26)
        # First silence [1.00, 1.60] before non-pivot "biasa" gets cut tightly (0.025s pad each side -> gap 0.55s cut)
        self.assertAlmostEqual(kept[1][0], 1.575, places=3)
        # Second silence [3.00, 3.60] before pivot "Ternyata" preserves ~0.26s pause (starts at 3.60 - 0.235 = 3.365)
        self.assertAlmostEqual(kept[2][0], 3.365, places=3)

        # 2. Pre-pivot BGM vacuum windows (-28 dB -> volume=0.04) avoiding CTA window
        vac_words = [
            {"word": "Orang", "start": 2.2, "end": 2.5},
            {"word": "salah", "start": 3.1, "end": 3.5},
            {"word": "subscribe", "start": 5.5, "end": 6.2},
            {"word": "Makanya", "start": 14.2, "end": 14.7},
        ]
        vac_wins = detect_bgm_vacuum_windows(
            vac_words, [], hook_end_s=2.0, cta_window=(5.2, 6.8), max_events=2
        )
        self.assertEqual(len(vac_wins), 2)
        self.assertAlmostEqual(vac_wins[0][0], 2.82, places=2)
        self.assertAlmostEqual(vac_wins[1][0], 13.92, places=2)

        fg_vac = build_audio_filtergraph(
            vo_input_idx=1,
            vo_duration=18.0,
            profile=self.c1,
            bgm_input_idx=2,
            hook_audio_input_idx=None,
            sfx_inputs=[],
            bgm_start_s=2.0,
            bgm_vacuum_windows=vac_wins,
        )
        self.assertIn("volume=0.04", fg_vac)

        # 3. Zero RSVP double-text overlap during gamified_321 countdown digits
        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "blank_321.ass"
            vis_321 = self.c1.visual.model_copy(
                update={"cta_style": "gamified_321", "suppress_rsvp_on_overlay": True}
            )
            rsvp_words = [
                {"word": "KLIK", "start": 5.10, "end": 5.45},
                {"word": "TIGA", "start": 6.10, "end": 6.45},
                {"word": "DUA", "start": 6.50, "end": 6.85},
                {"word": "SATU", "start": 6.90, "end": 7.20},
                {"word": "TERNYATA", "start": 7.60, "end": 8.10},
            ]
            generate_ass_subtitles(
                rsvp_words, [], set(), vis_321, ass_p, cta_window=(5.0, 7.3), subject_label="kucing"
            )
            txt = ass_p.read_text(encoding="utf-8")
            # Countdown words inside [p1_end..cta_e] MUST be suppressed on Layer 2 while KLIK & TERNYATA survive!
            self.assertIn("KLIK", txt)
            self.assertIn("TERNYATA", txt)
            self.assertNotIn("DUA", txt)
            self.assertNotIn("SATU", txt)

    def test_v37_protected_payoff_hold_and_reward_pop_cta(self) -> None:
        # 1. Payoff unit <= 4.80s stays unsplit at 1.0x when Golden Moment motion is verified, splits when weak
        payoff_words = [
            {"word": "Makanya", "start": 15.0, "end": 15.5},
            {"word": "kucing", "start": 15.6, "end": 16.1},
            {"word": "langsung", "start": 16.5, "end": 17.0},
            {"word": "lompat", "start": 17.1, "end": 18.7},
        ]
        base_scenes = [
            {"index": 1, "start_s": 0.0, "end_s": 2.0, "duration_s": 2.0, "vo": "Hook?", "words": []},
            {"index": 2, "start_s": 15.0, "end_s": 18.8, "duration_s": 3.8, "vo": "Makanya kucing langsung lompat", "words": payoff_words},
        ]
        held = apply_intra_unit_clip_splitting(
            [dict(s) for s in base_scenes],
            protect_payoff_hold=True,
            golden_moments={2: {"motion_verified": True, "metric_score": 78.0}},
            max_payoff_hold_s=4.80,
        )
        self.assertEqual(len(held), 2)
        self.assertTrue(held[1].get("is_payoff_hold"))
        self.assertEqual(compute_selective_speed("payoff", clip_dur=10.0, target_dur=3.8, protect_payoff_hold=True), 1.0)

        fallback_split = apply_intra_unit_clip_splitting(
            [dict(s) for s in base_scenes],
            protect_payoff_hold=True,
            golden_moments={2: {"motion_verified": False, "metric_score": 38.0}},
            max_payoff_hold_s=4.80,
        )
        self.assertEqual(len(fallback_split), 3)

        # 2. 3-Beat Reward Pop CTA (#10 @Squooshify) + Mid-Shot Foley Re-Anchor on held payoff
        with tempfile.TemporaryDirectory() as tmp:
            ass_p = Path(tmp) / "reward.ass"
            vis_rew = self.c1.visual.model_copy(update={"cta_style": "reward_pop"})
            generate_ass_subtitles(
                [{"word": "SUBSCRIBE", "start": 5.5, "end": 6.2}],
                [],
                {"SUBSCRIBE"},
                vis_rew,
                ass_p,
                cta_window=(5.4, 7.6),
                subject_label="kucing",
            )
            txt = ass_p.read_text(encoding="utf-8")
            self.assertIn("Style: CtaReward", txt)
            self.assertIn("+1 KUCING LIKE!", txt)
            self.assertIn("BONUS KUCING IMUT!", txt)

        sfx_rew = compute_sfx_entries(
            held,
            self.cfg.channel_sfx_dir("C1-fakta"),
            {"MAKANYA"},
            cta_window=(5.4, 7.6),
            cta_style="reward_pop",
            subject_label="kucing",
            bgm_vacuum_windows=[(14.74, 15.05)],
        )
        # Held 3.8s payoff [15.0..18.8] MUST receive a mid-shot foley re-anchor around ~16.98s
        self.assertTrue(any(16.7 <= t <= 17.3 for t, _, _ in sfx_rew))


if __name__ == "__main__":
    unittest.main()
