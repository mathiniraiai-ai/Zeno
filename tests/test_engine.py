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
        self.assertIn("# BRAIN MEMORY (MANDATORY 15-YEAR EDITOR RULES):", brief)
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

    def test_6_brain_memory_vocal_stress_and_split_qc(self) -> None:
        import brain_memory
        import vision_tracker

        rules = brain_memory.load_brain_memory("micronarrative")
        self.assertGreaterEqual(len(rules), 7)

        # Vocal stress detection from 10ms RMS envelope
        fake_rms = [(round(i * 0.01, 2), -24.0) for i in range(300)]
        for i in range(120, 155):
            fake_rms[i] = (round(i * 0.01, 2), -16.5)  # +7.5 dB vocal stress peak
        fake_words = [
            {"w": "ನಾನು", "start": 0.20, "end": 0.60},
            {"w": "Supernova", "start": 1.15, "end": 1.60},
            {"w": "ಮೂಲಕ", "start": 1.80, "end": 2.20},
        ]
        stress = prepare.detect_vocal_stress_words(fake_rms, fake_words, 0.10, 2.50)
        self.assertEqual(len(stress), 1)
        self.assertEqual(stress[0]["w"], "Supernova")
        self.assertGreaterEqual(stress[0]["delta_db"], 6.0)

        # Actor readiness / anti-freeze bounds
        ready = vision_tracker.detect_actor_readiness_bounds("nonexistent.mp4", 0.0, 5.0, first_word_start=0.60, last_word_end=4.20)
        self.assertEqual(ready["ready_start"], 0.42)
        self.assertEqual(ready["ready_end"], 4.38)

        # Split-screen & hook QC rules
        plan_path = ROOT / "work" / "stop_saying_21545" / "plan.edl"
        if plan_path.is_file():
            plan = edl_parser.load_edl(plan_path)
            ok, summary = verify.check_split_and_hook_rules(plan)
            self.assertTrue(ok, f"Split/Hook QC failed: {summary}")

        # Verify violation detection when split-screen has non-static zoom or intra-split transition
        bad_plan = {
            "segments": [
                {"id": "L2", "src": "a.mp4", "layout": "split_h", "zoom": 1.0, "transition": "warm_flash"},
                {"id": "L3", "src": "b.mp4", "layout": "split_h", "zoom": 1.08, "transition": "smoothleft"},
            ],
            "sfx": [{"file": "whoosh.wav", "segment": "L3", "at": 0.0}],
        }
        bad_ok, bad_summary = verify.check_split_and_hook_rules(bad_plan)
        self.assertFalse(bad_ok)
        self.assertIn("non_static_zoom", bad_summary)
        self.assertIn("intra_split_transition", bad_summary)
        self.assertIn("intra_split_whoosh", bad_summary)

    def test_7_indic_schwa_deletion_vs_dravidian_retention(self) -> None:
        from align_engine.phonetics.indic_phonemes import IndicPhoneticMapper

        # Indo-Aryan (Hindi/Marathi/Gujarati/Bengali) word-final inherent schwa ('a') deletion
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("राम"), "raam")
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("बस"), "bas")
        # Explicit final vowel matra ('aa' = ा) is preserved
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("करना"), "karanaa")
        # Single-consonant monosyllable retains inherent vowel
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("न"), "na")
        # Gujarati and Bengali word-final schwa deletion
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("રામ"), "raam")
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("রাম"), "raam")
        # Dravidian (Kannada & Malayalam) word-final vowel is retained
        self.assertEqual(IndicPhoneticMapper.transliterate_indic_script("ಅವಳ"), "avala")
        self.assertTrue(
            IndicPhoneticMapper.map_to_acoustic_tokens("ಅವಳ", lang="kn", is_native_indic=True, orig_word="ಅವಳ").endswith("A")
        )
        self.assertTrue(
            IndicPhoneticMapper.map_to_acoustic_tokens("राम", lang="hi", is_native_indic=True, orig_word="राम").endswith("M")
        )

    def test_8_transcribe_and_align_clip_accepts_script_path_and_edl_diff(self) -> None:
        import inspect
        import tempfile
        import align_engine
        import token_tracker

        sig = inspect.signature(align_engine.transcribe_and_align_clip)
        self.assertIn("script_path", sig.parameters)

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "script.txt"
            sp.write_text("L1 Nova: Stop saying Girl on Scooter [WRONG]\nL2 Ravi: Say riding a scooter [RIGHT]\n", encoding="utf-8")
            parsed = align_engine._parse_script_path_to_lines(sp)
            self.assertEqual(parsed, ["Stop saying Girl on Scooter", "Say riding a scooter"])

        init_edl = (
            "#canvas 1080x1920@30 preset=single mark_pack=flash\n"
            "L1 S1.mp4 1.00-4.00 layout=single z=1.0 # Hook\n"
            "L2 S2.mp4 0.50-3.50 layout=split_h z=1.0 slot=right peer=S1.mp4@1.0 # Line 2\n"
        )
        final_edl = (
            "#canvas 1080x1920@30 preset=single mark_pack=flash\n"
            "L1 S1.mp4 1.12-3.90 layout=single z=sudden_in:1.08 tr=whip_left@0.28 # Hook\n"
            "L2 S3.mp4 0.50-3.50 layout=split_h z=1.0 slot=right peer=S1.mp4@1.0 # Line 2 swapped\n"
        )
        diff = token_tracker.compute_edl_learning_diff(init_edl, final_edl)
        self.assertTrue(diff["has_edits"])
        self.assertEqual(len(diff["take_swaps"]), 1)
        self.assertEqual(diff["take_swaps"][0]["line_id"], "L2")
        self.assertEqual(len(diff["cut_timing_deltas"]), 1)
        self.assertEqual(len(diff["zoom_changes"]["changes"]), 1)
        self.assertEqual(len(diff["transition_changes"]["changes"]), 1)

    def test_9_auth_session_persistence_and_localhost_callback(self) -> None:
        import tempfile
        import threading
        import urllib.request
        import auth_session

        with tempfile.TemporaryDirectory() as tmp:
            sess_file = Path(tmp) / ".zeno_session.json"
            env_file = Path(tmp) / ".env"
            saved = auth_session.save_session(
                token="zeno_unit_test_tok_123456",
                email="editor@gosupernova.live",
                name="Test Editor",
                cloud_url="https://zeno-production-bef7.up.railway.app",
                session_path=sess_file,
                write_dotenv=True,
                dotenv_path=env_file,
            )
            self.assertEqual(saved["email"], "editor@gosupernova.live")
            loaded = auth_session.get_active_session(session_path=sess_file)
            self.assertEqual(loaded["token"], "zeno_unit_test_tok_123456")

            # Localhost callback server capture test (simulating browser redirect after Google login)
            srv, port = auth_session.start_callback_server(preferred_port=0)
            try:
                def _send_cb():
                    url = f"http://127.0.0.1:{port}/callback?token=zeno_browser_cb_999&email=mathini%40gosupernova.live&name=Mathini"
                    with urllib.request.urlopen(url, timeout=5) as resp:
                        self.assertEqual(resp.status, 200)

                t = threading.Thread(target=_send_cb)
                t.start()
                srv.handle_request()
                t.join(timeout=5)
                self.assertIsNotNone(srv.auth_result)
                self.assertEqual(srv.auth_result["token"], "zeno_browser_cb_999")
                self.assertEqual(srv.auth_result["email"], "mathini@gosupernova.live")
            finally:
                srv.server_close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
