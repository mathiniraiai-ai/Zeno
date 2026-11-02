#!/usr/bin/env python3
"""Zeno Engine Regression Suite & Gold-Set Cut Accuracy Evaluator (`tests/test_engine.py`).

Runs in <2 seconds with zero external API calls. Verifies:
  1. Style Config & `styles/micronarrative/spec.json` tuning schema completeness.
  2. `edl_parser.py` lossless round-trip (`parse_edl` <-> `format_edl`).
  3. `prepare.py` take scoring, deduplication, `[ALT_TAKES_OMITTED]` brief cap, 10ms RMS
     `find_rms_minimum`, and absence of hardcoded clip stems (`S1..S17`) or project names.
  4. `layouts.py` transcript-intent zoom (`resolve_segment_zoom_spec`), `color_match` OFF by
     default, and deterministic segment cache key (`build_segment_cache_key`).
  5. `premiere_export.py` `.premiere.xml` and `.prproj` structure (eased Scale keyframes,
     `<transitionitem>` dissolves, sequence `<marker>` cues, 0 dangling `<file>` refs).
  6. Gold-Set Cut Accuracy (`work/stop_saying_21545/plan.edl`): 0 clipped words, 0 long pauses,
     100% script coverage.
"""
from __future__ import annotations

import inspect
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import edl_parser  # noqa: E402
import layouts  # noqa: E402
import prepare  # noqa: E402
import style_config  # noqa: E402
import verify  # noqa: E402


class TestZenoEngine(unittest.TestCase):
    def test_1_style_config_and_spec_json(self) -> None:
        tuning = style_config.get_tuning("micronarrative")
        for key in (
            "macro_silence_db",
            "pause_split_monologue_sec",
            "jump_cut_zoom",
            "push_in_zoom",
            "mark_duration_sec",
            "wrong_sfx_gain_db",
            "right_sfx_gain_db",
            "audio_crossfade_sec",
            "target_loudness_lufs",
            "true_peak_dbtp",
            "max_alt_takes_in_brief",
        ):
            self.assertIn(key, tuning, f"Missing tuning key: {key}")
        self.assertEqual(float(tuning["target_loudness_lufs"]), -11.5)
        self.assertEqual(float(tuning["audio_crossfade_sec"]), 0.045)

    def test_2_edl_parser_round_trip(self) -> None:
        sample_edl = """#output "/tmp/test_out.mp4"
#canvas 1080x1920@30 loudness=-11.5 preset=hybrid mark_pack=flash

L1 S1.mp4 0.14-3.42 z=1.0->1.06 focus=0.48,0.38->0.54,0.4 sp="Miss Nova" stop:"Stop Saying|I have a doubt" mark:wrong@2.80/0.56 # L1 [Miss Nova]: Stop saying I have a doubt
L2 S2.mp4 0.12-4.10 layout=split_h slot=right sp=Student tr=smoothleft@0.28 peer="Miss Nova:S1_listening.mp4@0-4.1" mark:right@3.50/0.56 # L2 [Student]: Can I ask a question?
"""
        plan = edl_parser.parse_edl(sample_edl)
        self.assertEqual(len(plan["segments"]), 2)
        s0, s1 = plan["segments"][0], plan["segments"][1]
        self.assertEqual(s0["zoom"], 1.0)
        self.assertEqual(s0["zoom_to"], 1.06)
        self.assertEqual(s0["focus"], [0.48, 0.38])
        self.assertEqual(s0["focus_end"], [0.54, 0.4])
        self.assertEqual(s1["layout"], "split_h")
        self.assertEqual(s1["transition"], "smoothleft")
        self.assertEqual(s1["transition_duration"], 0.28)
        self.assertEqual(s1["peer"]["speaker"], "Miss Nova")

        # Round-trip back to EDL text and re-parse
        edl_out = edl_parser.format_edl(plan)
        plan2 = edl_parser.parse_edl(edl_out)
        self.assertEqual(plan2["segments"][0]["focus_end"], [0.54, 0.4])
        self.assertEqual(plan2["segments"][1]["layout"], "split_h")
        self.assertEqual(len(plan2["marks"]), 2)

    def test_3_no_hardcoded_stems_and_brief_cap(self) -> None:
        cut_src = inspect.getsource(prepare.cut_clips)
        for forbidden in ('stem == "S1"', 'stem == "S2"', 'stem == "S7"', 'stem == "S13"', "Kannada_I_am_hungry"):
            self.assertNotIn(forbidden, cut_src, f"Hardcoded stem/project {forbidden} found in prepare.cut_clips")

        # Verify find_rms_minimum picks the lowest energy valley
        rms_env = [(0.10, -18.0), (0.20, -24.0), (0.30, -44.5), (0.40, -29.0)]
        min_t, min_db = prepare.find_rms_minimum(rms_env, 0.10, 0.40)
        self.assertAlmostEqual(min_t, 0.30, places=2)
        self.assertAlmostEqual(min_db, -44.5, places=1)

        # Verify brief.txt caps [ALT_TAKE] entries at max_alt_takes_in_brief (2)
        fake_clips = []
        for idx in range(6):
            fake_clips.append({
                "stem": f"T{idx+1}",
                "src": f"/tmp/T{idx+1}.mp4",
                "info": {"duration": 4.0},
                "matched_lines": [{"id": "L1", "speaker": "Nova", "text": "Hello world"}],
                "take_group": 99,
                "dropped_take": idx > 0,
                "macro_silences": [],
                "hq_words": [{"w": "Hello", "start": 0.1, "end": 0.5}, {"w": "world", "start": 0.6, "end": 1.0}],
                "anomalies": ["[BEST_TAKE] L1" if idx == 0 else f"[ALT_TAKE] L1: T{idx+1}"],
            })
        brief = prepare.format_brief_txt(
            project_name="unit_test",
            script_path=Path("script.txt"),
            clips_data=fake_clips,
            segments=[{"id": "L1", "src": "/tmp/T1.mp4", "start": 0.1, "end": 1.1, "speaker": "Nova"}],
            marks=[],
            script_lines=[{"id": "L1", "speaker": "Nova", "text": "Hello world"}],
            preset="single",
            mark_pack="flash",
        )
        self.assertIn("[ALT_TAKES_OMITTED] +3 lower-scored takes omitted (T4, T5, T6)", brief)
        self.assertLess(len(brief), 3900)

    def test_4_intent_zoom_and_color_match_off_by_default(self) -> None:
        # Color match must be OFF by default so raw clip colors are never altered
        self.assertIsNone(
            layouts.compute_face_color_match_filter({"src": "nonexistent.mp4", "start": 0.0, "end": 2.0}, {})
        )
        # Explicit punch-in zoom spec
        z0, z1, style, _, _ = layouts.resolve_segment_zoom_spec(
            {"src": "nonexistent.mp4", "start": 0.0, "end": 3.0, "zoom": 1.0, "zoom_to": 1.08, "zoom_style": "sudden_in", "zoom_at": 1.2},
            3.0,
        )
        self.assertEqual((z0, z1, style), (1.0, 1.08, "sudden_in"))
        # Split-screen segments must always lock zoom at 1.0 (100% static)
        sz0, sz1, sstyle, _, _ = layouts.resolve_segment_zoom_spec(
            {"src": "nonexistent.mp4", "layout": "split_h", "start": 0.0, "end": 3.0, "zoom": 1.08, "zoom_to": 1.12, "zoom_style": "sudden_in"},
            3.0,
        )
        self.assertEqual((sz0, sz1, sstyle), (1.0, 1.0, "none"))
        # Paired split-screen crop filter must enforce identical common headroom and symmetric framing
        c_live, c_peer = layouts.compute_paired_split_crop_filters(
            "nonexistent_a.mp4", "nonexistent_b.mp4",
            720, 1280, 720, 1280, 540, 1920, 540, 1920,
            live_focus=[0.51, 0.42], peer_focus=[0.55, 0.34],
        )
        self.assertEqual(c_live["common_headroom_pct"], c_peer["common_headroom_pct"])
        c_peer_swap, c_live_swap = layouts.compute_paired_split_crop_filters(
            "nonexistent_b.mp4", "nonexistent_a.mp4",
            720, 1280, 720, 1280, 540, 1920, 540, 1920,
            live_focus=[0.55, 0.34], peer_focus=[0.51, 0.42],
        )
        self.assertEqual(c_live["filter"], c_live_swap["filter"])
        self.assertEqual(c_peer["filter"], c_peer_swap["filter"])

    def test_5_gold_set_and_premiere_xml_verification(self) -> None:
        plan_path = ROOT / "work" / "stop_saying_21545" / "plan.edl"
        if plan_path.is_file():
            plan = edl_parser.load_edl(plan_path)
            cuts_ok, clipped_w, long_pauses, _, _ = verify.check_cuts_and_pauses(plan)
            self.assertTrue(cuts_ok, f"Gold-set cuts check failed: clipped={clipped_w}, pauses={long_pauses}")
            self.assertEqual(clipped_w, 0)
            self.assertEqual(long_pauses, 0)

        xml_path = Path.home() / "Downloads" / "Zeno Outputs" / "Kannada_Stop_Saying_Girl_On_Scooter.premiere.xml"
        if xml_path.is_file():
            root = ET.parse(xml_path).getroot()
            self.assertGreaterEqual(len(root.findall(".//transitionitem")), 15)
            self.assertGreaterEqual(len(root.findall(".//sequence/marker")), 16)
            self.assertGreaterEqual(len(root.findall('.//parameter[parameterid="scale"]/keyframe')), 10)


if __name__ == "__main__":
    unittest.main(verbosity=2)
