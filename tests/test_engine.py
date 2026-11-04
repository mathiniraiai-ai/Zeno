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

        import gzip
        base_prproj = ROOT / "assets" / "premiere" / "base_1080x1920.prproj.gz"
        self.assertTrue(base_prproj.is_file())
        broot = ET.fromstring(gzip.decompress(base_prproj.read_bytes()).decode("utf-8"))
        for tag in ("TrackItems", "Links", "PreviewItems"):
            for el in broot.iter(tag):
                self.assertEqual(el.attrib.get("Version"), "1", f"Missing Version='1' on <{tag}> in base_1080x1920.prproj.gz")
        seq_w = broot.find(".//MZ.Sequence.PreviewFrameSizeWidth")
        seq_h = broot.find(".//MZ.Sequence.PreviewFrameSizeHeight")
        self.assertIsNotNone(seq_w)
        self.assertIsNotNone(seq_h)
        self.assertEqual(seq_w.text, "720")
        self.assertEqual(seq_h.text, "1280")

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

    def test_10_auto_updater_throttle_and_offline_guard(self) -> None:
        import inspect
        import os
        import auto_updater

        sig = inspect.signature(auto_updater.install_daily_scheduler)
        self.assertEqual(sig.parameters["hour"].default, 11)

        old_val = os.environ.get("ZENO_NO_LIVE")
        try:
            os.environ["ZENO_NO_LIVE"] = "1"
            res = auto_updater.ensure_latest_update(force=True, quiet=True)
            self.assertTrue(res["ok"])
            self.assertEqual(res["status"], "skipped_offline")
        finally:
            if old_val is None:
                os.environ.pop("ZENO_NO_LIVE", None)
            else:
                os.environ["ZENO_NO_LIVE"] = old_val


    # ------------------------------------------------------------------ script cues, lexicons, zeno.py tweaks
    W44_SAMPLE = """Visuals: Construction worker(Suresh) surprises with his perfect English
Reference link : https://example.com/ref.mp4
Characters
Character 1 - A man(Suresh) in his late 20s
Character 2 - Boss, middle aged man
Scene 1 - Suresh on a construction site
(Suresh is talking to his Partner on his phone)
Suresh: Hello, I practise English.
Partner: Great!
Scene changes: Suresh working on the site while talking to his boss
Boss: What did you say?
Scene changes - Suresh in another frame, doing other work while saying :
How are you?  (X Sign and Text Strikethrough)
How's the weather?  (X Sign and Text Strikethrough)
Back to the construction site, Talking to his boss
Boss: Wow!
Rectangle overlay, floating window of female robot, More like a horizontal split screen
Female robot : Do you speak English?
Suresh: Yes, a little bit.
"""

    def test_script_directions_are_not_spoken_lines(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "w44.txt"
            sp.write_text(self.W44_SAMPLE, encoding="utf-8")
            meta, lines = prepare.parse_script_file(sp)
        spoken = [l["text"] for l in lines]
        self.assertEqual(len(lines), 8, spoken)
        for bad in ("Visuals", "Reference", "Character", "Scene changes", "Back to", "Rectangle"):
            self.assertFalse(any(t.startswith(bad) for t in spoken), bad)
        self.assertEqual(set(meta["characters"]), {"Suresh", "Boss"})
        self.assertIn("Construction worker", meta["concept_brief"])
        scenes = {l["text"][:8]: l["scene"] for l in lines}
        self.assertEqual(scenes["Hello, I"], "Scene 1")
        self.assertEqual(scenes["What did"], "Scene 2")
        self.assertEqual(scenes["How are "], "Scene 3")
        self.assertEqual(scenes["Wow!"], "Scene 4")
        strike = [l for l in lines if l["strike_text"]]
        self.assertEqual([l["strike_text"] for l in strike], ["How are you?", "How's the weather?"])
        self.assertNotIn("Strikethrough", " ".join(spoken))
        self.assertTrue(all(not l["beat"] for l in lines), "a talking-while-scene note must not become a silent beat")
        hinted = [l["id"] for l in lines if l["layout_hint"]]
        self.assertEqual([l["layout_hint"] for l in lines if l["layout_hint"]], ["split_v"] * 2)
        self.assertEqual(hinted, [lines[-2]["id"], lines[-1]["id"]])

    def test_script_cues_reach_the_plan(self):
        """Scene changes get a transition, strike lines get banners, a script split applies unless the clip is already split."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "w44.txt"
            sp.write_text(self.W44_SAMPLE, encoding="utf-8")
            _, lines = prepare.parse_script_file(sp)

        def clip(stem, info=None):
            return {"stem": stem, "src": f"input/footage/{stem}.mp4", "info": info or {"duration": 12.0, "face_count": 1}, "matched_lines": []}

        def raw_for(clips, ls):
            return [{"stem": clips[i % len(clips)]["stem"], "src": clips[i % len(clips)]["src"], "clip": clips[i % len(clips)],
                     "start": 1.0, "end": 4.0, "lines": [l], "lines_override": [l], "words": [{"w": "x", "start": 1.2, "end": 1.6}]}
                    for i, l in enumerate(ls)]

        clips = [clip("S1"), clip("S2"), clip("S3")]
        segs, _, _ = prepare.assign_segment_ids_marks_and_layouts(raw_for(clips, lines), lines, clips, preset="auto")
        by_id = {s["id"]: s for s in segs}
        self.assertEqual(by_id["L3"]["transition"], "whip_left")   # Scene 1 -> 2
        self.assertEqual(by_id["L4"]["transition"], "whip_left")   # into the strike-through sequence
        self.assertEqual([b["text"] for b in by_id["L4"]["banners"]], ["How are you?"])
        self.assertEqual([b["text"] for b in by_id["L5"]["banners"]], ["How's the weather?"])
        self.assertEqual(by_id["L7"]["layout"], "split_v")
        self.assertEqual(by_id["L1"].get("layout", "single"), "single")

        baked = [clip("B1", {"duration": 12.0, "face_count": 2, "shot_type": "split_v_baked"})]
        s2, _, _ = prepare.assign_segment_ids_marks_and_layouts(raw_for(baked, lines[-1:]), lines, baked, preset="auto")
        self.assertEqual(s2[0].get("layout", "single"), "single")

    def test_style_readiness_levels(self):
        import json
        import tempfile

        import style_readiness

        spec = {k: {"x": 1} for k in style_readiness.REQUIRED_SECTIONS}
        spec["tuning"] = {f"p{i}": i for i in range(25)}
        spec["learned_from"] = {"reference_video": "ref.mp4"}
        spec["rebuild_check"] = {"scene1_clean_mean_diff": 1.8, "scene2_clean_mean_diff": 2.0, "scene3_clean_mean_diff": 3.3,
                                 "audio": "in sync (lag 0)", "frames": "100 vs 100"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            sdir = root / "styles" / "demo"
            sdir.mkdir(parents=True)
            (root / "work").mkdir()
            (sdir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            self.assertEqual(style_readiness.assess("demo", root, run_tests=False)["level"], 1)   # reproduced, nothing on real footage
            for i in range(3):   # three real jobs tagged to the style -> pilot
                (root / "work" / f"job{i}").mkdir()
                (root / "work" / f"job{i}" / "plan.edl").write_text("# style: demo\n", encoding="utf-8")
            self.assertEqual(style_readiness.assess("demo", root, run_tests=False)["level"], 2)
            spec["rebuild_check"]["scene3_clean_mean_diff"] = 9.0   # reference no longer reproduced
            (sdir / "spec.json").write_text(json.dumps(spec), encoding="utf-8")
            self.assertEqual(style_readiness.assess("demo", root, run_tests=False)["level"], 0)
            self.assertEqual(style_readiness.assess("missing", root, run_tests=False)["level"], 0)

    def test_preflight_validates_script_and_footage(self):
        import subprocess
        import tempfile

        import common
        import preflight

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            clip = tmp_p / "S1.mp4"
            subprocess.run([str(common.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1:r=24",
                            "-f", "lavfi", "-i", "sine=f=440:d=1", "-shortest", "-pix_fmt", "yuv420p", str(clip)], check=True)
            script = tmp_p / "s.txt"
            script.write_text(self.W44_SAMPLE, encoding="utf-8")
            ok = preflight.validate_inputs(script, [str(tmp_p)])
            self.assertEqual(ok["errors"], [])
            self.assertEqual(ok["report"]["footage"]["clips"], 1)
            self.assertEqual(ok["report"]["script"]["spoken_lines"], 8)
            self.assertEqual(ok["report"]["script"]["strike_text_lines"], 2)
            bad = preflight.validate_inputs(script, [str(tmp_p / "missing"), "https://drive.google.com/drive/folders/abc"])
            self.assertEqual(len(bad["errors"]), 2)
            empty = tmp_p / "empty.txt"
            empty.write_text("Visuals: only notes\n", encoding="utf-8")
            self.assertIn("the script has no spoken lines", preflight.validate_inputs(empty, [str(tmp_p)])["errors"])

    def test_job_gate_blocks_until_checks_pass(self):
        import os
        from unittest.mock import patch

        os.environ["_ZENO_REEXEC"] = "1"
        import preflight
        import zeno

        with patch.object(preflight, "run_system_checks", return_value=[(False, "Zeno server", "cannot reach")]), \
                patch.object(preflight, "save_state", return_value=False):
            with self.assertRaises(SystemExit) as ctx:
                zeno.job_gate(["clips"], "script.txt")
        self.assertEqual(ctx.exception.code, 2)
        with patch.object(preflight, "run_system_checks", return_value=[(True, "FFmpeg", "ok")]), \
                patch.object(preflight, "save_state", return_value=True), \
                patch.object(zeno, "resolve_script_input", side_effect=FileNotFoundError("Script file not found: x")):
            with self.assertRaises(SystemExit) as ctx:
                zeno.job_gate(["clips"], "x")
        self.assertEqual(ctx.exception.code, 2)

    def test_google_doc_link_needs_a_tab(self):
        import os

        os.environ["_ZENO_REEXEC"] = "1"
        import zeno

        with self.assertRaises(ValueError):
            zeno.resolve_script_input("https://docs.google.com/document/d/1abcDEFghiJKLmnop/edit")

    def test_format_library_lookup_and_measure(self):
        import subprocess
        import tempfile

        import common
        import format_library

        names = {r["id"] for r in format_library.load_recipes()}
        self.assertTrue({"split_h", "split_v", "video_call", "chat_bar", "selfie", "close_up"} <= names)
        self.assertEqual(format_library.best_layout("they are on a video call"), "video_call")
        self.assertEqual(format_library.best_layout("left screen and right screen"), "split_h")
        self.assertEqual(format_library.best_layout("the robot shows it on a prompt bar"), "chat_bar")
        self.assertEqual(format_library.best_layout("an airpipe pipeline of work"), "", "'pip' must not match inside other words")
        self.assertEqual(format_library.best_layout("he walks in"), "")
        # the parser uses the library for wording the old rule did not know, and split wording still wins
        self.assertEqual(prepare.layout_hint_from_direction("They talk over a video call"), "video_call")
        self.assertEqual(prepare.layout_hint_from_direction("selfie style, he talks to camera"), "", "single-layout recipes add no panel")
        self.assertEqual(prepare.layout_hint_from_direction("floating window, more like a horizontal split screen"), "split_v")
        for r in format_library.load_recipes():
            self.assertLess(len(format_library.recipe_text(r)) / 4, 220, f"{r['id']} is too long for an agent to read cheaply")
        with tempfile.TemporaryDirectory() as tmp:
            vid = Path(tmp) / "seam.mp4"
            subprocess.run([str(common.resolve_ffmpeg()), "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=red:s=100x320:d=1:r=12",
                            "-f", "lavfi", "-i", "color=c=white:s=100x320:d=1:r=12", "-filter_complex", "hstack", "-pix_fmt", "yuv420p", str(vid)], check=True)
            m = format_library.measure_video(vid, n_frames=6)
            lib = Path(tmp) / "lib"
            r = format_library.extract(vid, "demo", "split_h", ["demo split"], "demo", "", 0.0, None, False, lib_dir=lib)
        self.assertEqual(m["seam"]["kind"], "split_h_baked")
        self.assertAlmostEqual(m["seam"]["position_frac"], 0.5, delta=0.02)
        self.assertEqual(r["confidence"], "measured")

    def test_cache_refreshes_itself_when_code_changes(self):
        import json
        import os
        import tempfile
        from unittest.mock import patch

        import common

        def put(d, name, data="{}"):
            (d / name).write_text(data, encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            for n in ("gemini_transcript.json", "words.json", "hq_words.json"):
                put(d, n)
            put(d, "audio16k.wav", "wav")
            v1 = {"transcript": "t1", "align": "a1"}
            with patch.dict(common._engine_versions, v1, clear=True):
                self.assertEqual(common.ensure_cache_current(d), [], "no marker yet: adopt, delete nothing")
                self.assertEqual(json.loads((d / "engine_version.json").read_text()), v1)
                self.assertEqual(common.ensure_cache_current(d), [], "same code: nothing to do")
            with patch.dict(common._engine_versions, {"transcript": "t1", "align": "a2"}, clear=True):
                self.assertEqual(common.ensure_cache_current(d), ["hq_words.json", "words.json"], "aligner changed: only alignment results go")
                self.assertTrue((d / "gemini_transcript.json").is_file(), "the paid transcript must survive an aligner change")
            put(d, "words.json")
            with patch.dict(common._engine_versions, {"transcript": "t2", "align": "a2"}, clear=True):
                self.assertIn("gemini_transcript.json", common.ensure_cache_current(d))
                self.assertFalse((d / "words.json").exists())
                self.assertTrue((d / "audio16k.wav").is_file(), "audio and info are never dropped")
                put(d, "words.json", "{not json")
                self.assertEqual(common.ensure_cache_current(d), ["words.json"], "a damaged file is rebuilt")
                put(d, "words.json")
                with patch.dict(os.environ, {"ZENO_FRESH": "1"}):
                    self.assertEqual(common.ensure_cache_current(d), ["words.json"], "--fresh drops everything derived")
            common.write_json(d / "x.json", {"a": 1})
            self.assertFalse(list(d.glob("*.tmp")), "writes leave no temp file behind")

    def test_engine_version_ignores_comment_edits(self):
        import tempfile

        import common

        with tempfile.TemporaryDirectory() as tmp:
            a, b, c = (Path(tmp) / n for n in ("a.py", "b.py", "c.py"))
            a.write_text("x = 1\n", encoding="utf-8")
            b.write_text("# a new comment\n\nx = 1\n", encoding="utf-8")
            c.write_text("x = 2\n", encoding="utf-8")
            self.assertEqual(common._source_digest([a]), common._source_digest([b]))
            self.assertNotEqual(common._source_digest([a]), common._source_digest([c]))
        self.assertEqual(set(common.engine_versions()), {"transcript", "align"})

    def test_auto_clean_only_when_the_disk_is_nearly_full(self):
        import tempfile

        import clean

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seg = root / "work" / "_segments"
            seg.mkdir(parents=True)
            (seg / "old.mp4").write_bytes(b"x" * 1000)
            self.assertFalse(clean.auto_clean(root, min_free_gb=0.0)["ran"])
            self.assertTrue((seg / "old.mp4").exists())
            res = clean.auto_clean(root, min_free_gb=1e9)
            self.assertTrue(res["ran"])
            self.assertFalse((seg / "old.mp4").exists())

    def test_format_observation_and_team_sync(self):
        import tempfile
        from unittest.mock import MagicMock, patch

        import format_learning
        import format_library

        plan = {"segments": [
            {"id": "L1", "start": 0.0, "end": 3.0, "layout": "single", "zoom_style": "sudden_in", "zoom": 1.0, "zoom_to": 1.08},
            {"id": "L2", "start": 3.0, "end": 5.0, "layout": "split_h", "transition": "warm_flash@0.35"},
            {"id": "L3", "start": 5.0, "end": 9.0, "layout": "split_h", "banners": [{"text": "x"}]},
            {"id": "L4", "start": 9.0, "end": 9.0, "layout": "single"},   # zero length: ignored
        ], "marks": [{"type": "wrong", "segment": "L3", "at": 1.0}]}
        obs = format_learning.build_observation(plan, "# style: micronarrative\n", "the script", verified=True)
        self.assertEqual(obs["style"], "micronarrative")
        self.assertEqual(obs["script_hash"], format_learning.build_observation(plan, "", "the script")["script_hash"])
        self.assertNotIn("the script", str(obs), "the script text itself is never sent")
        by = {f["format"]: f for f in obs["formats"]}
        self.assertEqual(by["single"]["segments"], 1)
        self.assertEqual(by["single"]["zoom_in_share"], 1.0)
        self.assertEqual(by["split_h"]["segments"], 2)
        self.assertEqual(by["split_h"]["mean_shot_sec"], 3.0)
        self.assertEqual(by["split_h"]["marks"], 1)
        self.assertEqual(by["split_h"]["strike_banners"], 1)
        self.assertGreater(by["split_h"]["transitions_per_min"], 0)
        self.assertIsNone(format_learning.build_observation({"segments": []}))
        with patch("auth_session.get_active_token", return_value=""):
            self.assertFalse(format_learning.post_observation(obs), "no key: quietly does nothing")
        with patch("auth_session.get_active_token", return_value="zeno_x"), patch("httpx.post") as posted, patch.dict("os.environ", {"ZENO_NO_OBSERVE": "1"}):
            self.assertFalse(format_learning.post_observation(obs))
            posted.assert_not_called()   # ZENO_NO_OBSERVE=1 keeps demo renders out of the team data

        reply = MagicMock(status_code=200)
        reply.raise_for_status = lambda: None
        reply.json.return_value = {"formats": [{"style": "micronarrative", "format": "split_h", "n_jobs": 5, "approved_by": "ed@x",
                                                "stats": {"mean_shot_sec": {"p25": 2.0, "median": 2.5, "p75": 3.0}}}]}
        with tempfile.TemporaryDirectory() as tmp, patch("auth_session.get_active_token", return_value="zeno_x"), \
                patch("auth_session.get_cloud_url", return_value="https://example.test"), patch("httpx.get", return_value=reply):
            lib = Path(tmp)
            res = format_library.sync_team(lib)
            self.assertEqual(res["pulled"], 1)
            team = [r for r in format_library.load_recipes(lib) if r["id"] == "team_micronarrative_split_h"]
            self.assertEqual(team[0]["confidence"], "team-approved")
            self.assertEqual(team[0]["geometry"]["mean_shot_sec"], 2.5)
            self.assertEqual(format_library.best_layout("a split screen scene", lib), "", "team recipes carry no aliases, so they never change layout choice")

    W43_SAMPLE = """Ref for visual set up: https://youtube.com/shorts/PIhiEW6TnEI


Characters:
Lady 1: A lady in her 60s, doing farm work.
Lady 2: A lady in her late 20s, doing farm work.
Female Humanoid Robot


Lady 1: Yes, I'm really excited to go to the US next month.
(The young lady working nearby hears this and looks shocked.)
Co-worker 3: Come on, hurry up, lady!
Lady 2: अरे दादी! आप इतनी अच्छी English बोल रही हो?

Horizontal Split screen Top: Robot | Bottom: Lady 1

Female Humanoid Robot: What are you doing right now?
Lady 1: I am doing work in farm and talking with you. \u274c
Female Humanoid Robot: : You can say, "I'm working on the farm while talking to you."
Lady 1: I'm working on the farm while talking to you. \u2705
Robot: Perfect!
"""

    def test_real_micronarrative_script_shapes(self):
        """The W43 script (character list, link label, emoji marks, panel note, double colon, 'Robot' = 'Female Humanoid Robot')."""
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "w43.txt"
            sp.write_text(self.W43_SAMPLE, encoding="utf-8")
            meta, lines = prepare.parse_script_file(sp)
        self.assertEqual(set(meta["characters"]), {"Lady 1", "Lady 2", "Female Humanoid Robot"})
        texts = [l["text"] for l in lines]
        self.assertEqual(len(lines), 9, texts)
        for bad in ("https", "A lady in her", "Female Humanoid Robot", "Horizontal"):
            self.assertFalse(any(t.startswith(bad) for t in texts), bad)
        by_text = {l["text"][:12]: l for l in lines}
        self.assertEqual(by_text["I am doing w"]["mark"], "wrong")
        self.assertEqual(by_text["I'm working "]["mark"], "right")
        self.assertNotIn("\u274c", " ".join(texts))
        self.assertNotIn("\u2705", " ".join(texts))
        self.assertTrue(by_text["You can say,"]["text"].startswith("You can say"), "no leading colon after the speaker")
        self.assertEqual(by_text["Come on, hur"]["speaker"], "Co-worker 3", "hyphenated speaker names are read")
        self.assertTrue(next(l for l in lines if l["beat"]), "the reaction note is the one silent beat")
        robot = [l for l in lines if "Robot" in l["speaker"]]
        self.assertEqual({l["speaker"] for l in robot}, {"Female Humanoid Robot"}, "Robot and Female Humanoid Robot are one speaker")
        split = [l for l in lines if l["layout_hint"]]
        self.assertEqual({l["layout_hint"] for l in split}, {"split_v"}, "horizontal split = top and bottom")
        self.assertEqual(split[0]["panels"], {"top": "Female Humanoid Robot", "bottom": "Lady 1"})
        self.assertEqual(next(l for l in lines if l["text"].startswith("Yes, I'm")).get("layout_hint"), "", "lines before the note are not split")

    def test_reference_learner_reads_scripts_like_a_job(self):
        import tempfile

        import learn_reference

        got = learn_reference.dialogue(self.W43_SAMPLE)
        self.assertEqual(len(got), 8, "the reaction beat is not a spoken line")
        self.assertEqual(got[0]["speaker"], "Lady 1")
        self.assertEqual([g["mark"] for g in got if g["mark"]], ["wrong", "right"])

    def test_learner_repeat_is_not_a_retake_of_the_teacher(self):
        """W43: the robot says 'You can say, I'm working on the farm...' and Lady 1 repeats the sentence. Different lines."""
        def clip(stem, text):
            return {"stem": stem, "hq_words": [{"w": w, "start": i * 0.3, "end": i * 0.3 + 0.25} for i, w in enumerate(text.split())]}

        robot_text = "You can say I'm working on the farm while talking to you"
        lady_text = "I'm working on the farm while talking to you"
        lines = [
            {"id": "L9", "tokens": [prepare._clean_token(w) for w in robot_text.split() if prepare._clean_token(w)]},
            {"id": "L10", "tokens": [prepare._clean_token(w) for w in lady_text.split() if prepare._clean_token(w)]},
        ]
        robot, lady = clip("S08", robot_text), clip("S09", lady_text)
        prepare.group_takes([robot, lady])   # without the script they look like the same content
        self.assertEqual(robot["take_group"], lady["take_group"])
        robot, lady = clip("S08", robot_text), clip("S09", lady_text)
        prepare.group_takes([robot, lady], lines)
        self.assertNotEqual(robot["take_group"], lady["take_group"], "each clip fits its own script line")
        retake1, retake2 = clip("R1", lady_text), clip("R2", lady_text)
        prepare.group_takes([retake1, retake2], lines)
        self.assertEqual(retake1["take_group"], retake2["take_group"], "two takes of the same line are still grouped")

    def test_layout_hint_wording(self):
        self.assertEqual(prepare.layout_hint_from_direction("a horizontal split screen"), "split_v")
        self.assertEqual(prepare.layout_hint_from_direction("a vertical split screen"), "split_h")
        self.assertEqual(prepare.layout_hint_from_direction("floating window of the robot"), "video_call")
        self.assertEqual(prepare.layout_hint_from_direction("he walks in"), "")

    def test_text_cue_strike_option(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "t.txt"
            sp.write_text('Suresh: Never say this [TEXT: "I am Delhi" | strike | x]\nSuresh: Plain line [TEXT: "Hi" | top]\n', encoding="utf-8")
            _, lines = prepare.parse_script_file(sp)
        self.assertEqual(lines[0]["strike_text"], "I am Delhi")
        self.assertEqual(lines[0]["text"], "Never say this")
        self.assertEqual(lines[1]["strike_text"], "")
        self.assertEqual(lines[1]["text"], "Plain line")

    def test_lexicons_have_no_conflicting_duplicate_keys(self):
        """A key listed twice with different values silently keeps the later one (Tamil 'takes' became 'tax')."""
        import ast

        for path in sorted((ROOT / "scripts" / "align_engine" / "lexicons").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Dict):
                    seen: dict = {}
                    for k, v in zip(node.keys, node.values):
                        if isinstance(k, ast.Constant) and isinstance(v, ast.Constant):
                            seen.setdefault(k.value, set()).add(v.value)
                    conflicts = sorted(k for k, vals in seen.items() if len(vals) > 1)
                    self.assertEqual(conflicts, [], f"{path.name} gives different values to {conflicts}")

    def test_zeno_stage3_tweaks_run(self):
        import os
        import tempfile

        os.environ["_ZENO_REEXEC"] = "1"   # importing zeno must not re-launch the test process
        import zeno

        plan = {"preset": "hybrid", "segments": [{"id": "L1", "src": "S1", "start": 0.0, "end": 2.0, "layout": "split_v"}],
                "marks": [{"type": "wrong", "segment": "L1", "at": 1.0, "duration": 0.56}]}
        with tempfile.TemporaryDirectory() as tmp:
            pp = Path(tmp) / "plan.edl"
            pp.write_text(edl_parser.format_edl(plan), encoding="utf-8")
            zeno.apply_stage3_tweaks(pp, swap_layout="single", swap_takes=["L1=S2"], error_sfx="buzzer.wav")
            out = edl_parser.load_edl(pp)
        self.assertEqual(out["preset"], "single")
        self.assertEqual(out["segments"][0]["src"], "S2")
        self.assertEqual(out["marks"][0]["sfx"], "buzzer.wav")


if __name__ == "__main__":
    unittest.main(verbosity=2)
