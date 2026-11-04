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

    def test_3_rms_minimum(self) -> None:
        # Verify find_rms_minimum picks the lowest energy valley
        rms_env = [(0.10, -18.0), (0.20, -24.0), (0.30, -44.5), (0.40, -29.0)]
        min_t, min_db = prepare.find_rms_minimum(rms_env, 0.10, 0.40)
        self.assertAlmostEqual(min_t, 0.30, places=2)
        self.assertAlmostEqual(min_db, -44.5, places=1)

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

        rules = brain_memory.load_brain_memory("micronarrative")
        self.assertGreaterEqual(len(rules), 7)

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
            v1 = {"transcript": "t1", "align": "a1", "vision": "v1"}
            put(d, "vision_abc123.json")
            with patch.dict(common._engine_versions, v1, clear=True):
                self.assertEqual(common.ensure_cache_current(d), ["vision_abc123.json"], "no marker yet: transcripts are adopted, the cheap vision results are rebuilt")
                self.assertEqual(json.loads((d / "engine_version.json").read_text()), v1)
                self.assertEqual(common.ensure_cache_current(d), [], "same code: nothing to do")
            put(d, "vision_abc123.json")
            with patch.dict(common._engine_versions, {"transcript": "t1", "align": "a1", "vision": "v2"}, clear=True):
                self.assertEqual(common.ensure_cache_current(d), ["vision_abc123.json"], "vision code changed: only the vision results go")
                self.assertTrue((d / "words.json").is_file() and (d / "gemini_transcript.json").is_file())
            with patch.dict(common._engine_versions, {"transcript": "t1", "align": "a2", "vision": "v2"}, clear=True):
                self.assertEqual(common.ensure_cache_current(d), ["hq_words.json", "words.json"], "aligner changed: only alignment results go")
                self.assertTrue((d / "gemini_transcript.json").is_file(), "the paid transcript must survive an aligner change")
            put(d, "words.json")
            with patch.dict(common._engine_versions, {"transcript": "t2", "align": "a2", "vision": "v2"}, clear=True):
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
        self.assertEqual(set(common.engine_versions()), {"transcript", "align", "vision"})

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


    def test_knowledge_comes_from_the_server_without_a_deploy(self):
        import json
        import tempfile
        from unittest.mock import MagicMock, patch

        import common
        import format_library
        import knowledge_sync

        item = {"kind": "format", "name": "split_v", "updated_at": 5.0,
                "payload": {"id": "split_v", "layout": "split_v", "names": ["horizontal split"], "description": "published on the server"}}
        reply = MagicMock(status_code=200)
        reply.raise_for_status = lambda: None
        reply.json.return_value = {"now": 123.0, "items": [item]}
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            with patch.object(knowledge_sync, "KNOW_DIR", work / "_knowledge"), patch.object(knowledge_sync, "STATE", work / "_knowledge" / "state.json"), \
                    patch("auth_session.get_active_token", return_value="zeno_x"), patch("auth_session.get_cloud_url", return_value="https://example.test"), \
                    patch("httpx.get", return_value=reply) as got:
                self.assertEqual(knowledge_sync.pull()["pulled"], 1)
                self.assertEqual(json.loads((work / "_knowledge" / "state.json").read_text())["since"], 123.0)
                knowledge_sync.pull()
                self.assertEqual(got.call_args.kwargs["params"]["since"], 123.0, "the second pull asks only for what is new")
            with patch.object(common, "WORK", work):
                recipes = {r["id"]: r for r in format_library.load_recipes()}
            self.assertEqual(recipes["split_v"]["description"], "published on the server", "a published recipe replaces the committed one")
            self.assertIn("split_h", recipes, "committed recipes the server did not replace are kept")
            with patch("auth_session.get_active_token", return_value=""):
                self.assertFalse(knowledge_sync.pull()["ok"], "no key: quietly does nothing")

    def test_match_falls_back_to_the_local_library(self):
        from unittest.mock import patch

        import knowledge_sync

        with patch("auth_session.get_active_token", return_value="zeno_x"), patch("auth_session.get_cloud_url", return_value="https://example.test"), \
                patch("httpx.post", side_effect=OSError("offline")):
            res = knowledge_sync.match("a floating window of the robot")
        self.assertEqual(res["source"], "local")
        self.assertEqual(res["matches"][0]["layout"], "video_call")

    def test_push_publishes_formats_and_references(self):
        from unittest.mock import MagicMock, patch

        import knowledge_sync

        items = knowledge_sync.collect_local()
        kinds = {i["kind"] for i in items}
        self.assertEqual(kinds, {"format", "reference"})
        self.assertIn("micronarrative/w43_hi_farm", {i["name"] for i in items if i["kind"] == "reference"})
        ok = MagicMock(status_code=200)
        with patch("auth_session.get_active_token", return_value=""), patch("auth_session.get_cloud_url", return_value="https://example.test"), \
                patch.dict("os.environ", {"ZENO_ADMIN_PASSWORD": "pw"}), patch("httpx.put", return_value=ok) as put:
            res = knowledge_sync.push()
        self.assertTrue(res["ok"])
        self.assertEqual(res["published"], len(items))
        self.assertEqual(put.call_args.kwargs["headers"]["X-Admin-Password"], "pw")
        self.assertEqual(put.call_args.kwargs["headers"]["X-Zeno-Admin"], "1")
        self.assertTrue(put.call_args_list[0].args[0].startswith("https://example.test/admin/api/knowledge/"))


    def test_short_lines_are_sent_and_never_guessed(self):
        """W43: a 0.49 s line ("सच में?") was refused by the server, and the offline fallback then gave that one clip the whole script."""
        import io
        import tempfile
        import wave

        from align_engine import gemini_transcriber as gt

        def wav_bytes(seconds, rate=16000):
            buf = io.BytesIO()
            with wave.open(buf, "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(b"\x01\x00" * int(seconds * rate))
            return buf.getvalue()

        def seconds_of(data):
            with wave.open(io.BytesIO(data)) as w:
                return w.getnframes() / w.getframerate()

        self.assertGreaterEqual(seconds_of(gt._pad_short_wav(wav_bytes(0.488))), 1.2)
        long_audio = wav_bytes(3.0)
        self.assertEqual(gt._pad_short_wav(long_audio), long_audio, "audio that is long enough is sent untouched")
        self.assertEqual(gt._pad_short_wav(b"not a wav"), b"not a wav", "anything unreadable is passed through for the server to judge")

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            wav.write_bytes(wav_bytes(0.5))
            many = gt.transcribe_offline_fallback(str(wav), "hi", ["पहली लाइन है", "दूसरी लाइन है", "तीसरी लाइन है"], Path(tmp))
            self.assertEqual(many["words"], [], "with several script lines nothing says which one this clip speaks")
            self.assertEqual(many["model_used"], "none")
            one = gt.transcribe_offline_fallback(str(wav), "hi", ["सच में?"], Path(tmp))
            self.assertEqual(one["words"], ["सच", "में?"], "with exactly one line the clip is that line")

    def test_phantom_faces_do_not_make_a_two_shot(self):
        """W43 robot clips: the robot (0.47 x 0.35) plus a phantom corner detection (0.34 x 0.15) was read as a two-shot."""
        import vision_tracker

        robot = {"bbox_norm": [0.30, 0.20, 0.77, 0.55]}
        phantom = {"bbox_norm": [0.65, 0.00, 0.99, 0.15]}
        self.assertEqual(vision_tracker.prominent_faces([robot, phantom]), [robot])
        a, b = {"bbox_norm": [0.05, 0.2, 0.40, 0.6]}, {"bbox_norm": [0.55, 0.22, 0.88, 0.58]}
        self.assertEqual(len(vision_tracker.prominent_faces([a, b])), 2, "two people of similar size are still a two-shot")
        self.assertEqual(vision_tracker.prominent_faces([robot]), [robot])

    def test_cloud_transcription_waits_when_the_server_is_busy(self):
        import tempfile
        from unittest.mock import MagicMock, patch

        from align_engine import gemini_transcriber as gt

        busy = MagicMock(status_code=429, headers={"retry-after": "3"})
        ok = MagicMock(status_code=200, headers={})
        ok.json.return_value = {"words": ["hello", "world"], "model_used": "stub"}
        ok.raise_for_status = lambda: None
        client = MagicMock()
        client.__enter__.return_value.post.side_effect = [busy, busy, ok]
        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "a.wav"
            import wave
            with wave.open(str(wav), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(16000)
                w.writeframes(b"\x01\x00" * 24000)
            tr = gt.GeminiTranscriber(api_keys=[])
            tr.cloud_url, tr.cloud_token = "https://example.test", "zeno_x"
            with patch("httpx.Client", return_value=client), patch("time.sleep") as slept:
                res = tr._transcribe_via_cloud(str(wav), "en", [])
        self.assertEqual(res["words"], ["hello", "world"])
        self.assertEqual(client.__enter__.return_value.post.call_count, 3, "it asked again after each 'too many requests'")
        self.assertEqual(slept.call_count, 2)

    # ------------------------------------------------------------------ the listener rule

    LEFT_RIGHT = """-- Miss Nova -- AI teacher, left screen in Scene 3
-- Woman -- learner, right screen in Scene 3
[Visual: SCENE 3 \u2014 SPLIT SCREEN | Woman - Right screen | Miss Nova - Left screen]
Miss Nova: Hello there, how are you today
Woman: I am fine thank you very much
Miss Nova: Great, let us start the lesson now
Woman: Yes please, I am ready to begin
"""







    def test_plan_file_keeps_a_held_listener(self):
        plan = {"segments": [{"id": "L1", "src": "S1.mp4", "start": 0.0, "end": 3.0, "layout": "split_v", "slot": "top",
                              "peer": {"speaker": "Lady 1", "src": "S2.mp4", "mode": "still", "still_at": 1.25, "slot": "bottom"}}], "marks": []}
        back = edl_parser.parse_edl(edl_parser.format_edl(plan)) if hasattr(edl_parser, "parse_edl") else None
        peer = (back or {}).get("segments", [{}])[0].get("peer", {})
        self.assertEqual((peer.get("mode"), peer.get("still_at"), peer.get("slot")), ("still", 1.25, "bottom"))


    def test_script_panel_wordings(self):
        self.assertEqual(prepare.panels_from_text("Top: Robot | Bottom: Lady 1"), {"top": "Robot", "bottom": "Lady 1"})
        self.assertEqual(prepare.panels_from_text("SPLIT SCREEN | Woman - Right screen | Miss Nova - Left screen"), {"right": "Woman", "left": "Miss Nova"})
        self.assertEqual(prepare.split_layout_from_panels({"left": "A", "right": "B"}), "split_h")
        self.assertEqual(prepare.split_layout_from_panels({"top": "A", "bottom": "B"}), "split_v")

    def test_a_job_carries_its_style_and_is_counted(self):
        import tempfile

        import style_readiness

        os = __import__("os")
        os.environ["_ZENO_REEXEC"] = "1"
        import zeno

        plan = {"output": "out.mp4", "style": "micronarrative", "segments": [{"id": "L1", "src": "S1.mp4", "start": 0.0, "end": 2.0}], "marks": []}
        text = edl_parser.format_edl(plan)
        self.assertIn("#style micronarrative", text)
        with tempfile.TemporaryDirectory() as tmp:
            pp = Path(tmp) / "plan.edl"
            pp.write_text(text, encoding="utf-8")
            self.assertEqual(edl_parser.load_edl(pp)["style"], "micronarrative", "the style survives a Stage 3 quick tweak (which rewrites the plan)")
            work = Path(tmp) / "root"
            (work / "work" / "job1").mkdir(parents=True)
            (work / "work" / "job1" / "plan.edl").write_text(text, encoding="utf-8")
            (work / "work" / "job2").mkdir()
            (work / "work" / "job2" / "plan.edl").write_text(edl_parser.format_edl({**plan, "style": "other"}), encoding="utf-8")
            self.assertEqual(style_readiness.tagged_projects(work, "micronarrative"), ["job1"], "only jobs of that style are counted")
        self.assertIn("none set", zeno.style_hint(""))
        self.assertEqual(zeno.style_hint("micronarrative"), "[style] micronarrative")
        self.assertIn("not a learned style", zeno.style_hint("nonsense"))

    def test_a_zip_installed_folder_can_be_connected_to_github_and_then_updates(self):
        """Windows PC with no Git and a ZIP download: connect the folder to the repository without touching its files, then update."""
        import shutil
        import subprocess
        import tempfile
        from unittest.mock import patch

        import auto_updater

        git = shutil.which("git")
        if not git:
            self.skipTest("git is not installed here")

        def g(cwd, *args):
            return subprocess.run([git, "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=str(cwd), capture_output=True, text=True, check=True)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_p = Path(tmp)
            remote, seed, zipdir = tmp_p / "remote.git", tmp_p / "seed", tmp_p / "Zeno"
            subprocess.run([git, "init", "-q", "--bare", "-b", "main", str(remote)], check=True)
            seed.mkdir()
            g(seed, "init", "-q", "-b", "main")
            (seed / "scripts").mkdir()
            (seed / "scripts" / "tool.py").write_text("v1\n", encoding="utf-8")
            (seed / ".gitignore").write_text(".env\nwork/\n.venv/\n", encoding="utf-8")
            g(seed, "add", "-A")
            g(seed, "commit", "-q", "-m", "one")
            g(seed, "remote", "add", "origin", str(remote))
            g(seed, "push", "-q", "origin", "main")
            # the folder as a ZIP download plus what the installer made: no .git, a secret, a venv and work files
            shutil.copytree(seed, zipdir, ignore=shutil.ignore_patterns(".git"))
            (zipdir / ".env").write_text("SECRET=1\n", encoding="utf-8")
            (zipdir / "work").mkdir()
            (zipdir / "work" / "keep.txt").write_text("mine\n", encoding="utf-8")

            with patch.object(auto_updater, "ROOT", zipdir), patch.object(auto_updater, "WORK", zipdir / "work"), \
                    patch.object(auto_updater, "STATE_FILE", zipdir / "work" / ".state.json"), patch.object(auto_updater, "LOG_FILE", zipdir / "work" / "u.log"), \
                    patch.dict("os.environ", {k: "" for k in ("ZENO_NO_AUTO_UPDATE", "CLAUDE_EDITOR_OFFLINE", "SCRIPTCUT_OFFLINE", "ZENO_NO_LIVE")}):
                blocked = auto_updater.ensure_latest_update(force=True, quiet=True, sync_brain=False)
                self.assertEqual(blocked["status"], "no_git_repo")
                self.assertIn("install.bat", blocked["message"], "it says what to do instead of failing silently")

                res = auto_updater.connect_to_git(zipdir, remote=str(remote), branch="main")
                self.assertEqual(res["status"], "connected", res)
                self.assertEqual((zipdir / ".env").read_text(), "SECRET=1\n", "the secret is untouched")
                self.assertEqual((zipdir / "work" / "keep.txt").read_text(), "mine\n")
                self.assertEqual(g(zipdir, "status", "--porcelain").stdout.strip(), "", "the files already match the repository")
                self.assertEqual(g(zipdir, "config", "core.fileMode").stdout.strip(), "false", "lost executable flags are not edits")
                self.assertEqual(auto_updater.connect_to_git(zipdir, remote=str(remote))["status"], "already_connected")

                (seed / "scripts" / "tool.py").write_text("v2\n", encoding="utf-8")
                g(seed, "commit", "-aq", "-m", "two")
                g(seed, "push", "-q", "origin", "main")
                upd = auto_updater.ensure_latest_update(force=True, quiet=True, sync_brain=False)
                self.assertEqual(upd["status"], "updated", upd)
                self.assertEqual((zipdir / "scripts" / "tool.py").read_text(), "v2\n")
                self.assertEqual((zipdir / ".env").read_text(), "SECRET=1\n")

            # a repository that cannot be reached leaves no half-made .git behind, so a retry starts clean
            other = tmp_p / "Other"
            other.mkdir()
            bad = auto_updater.connect_to_git(other, remote=str(tmp_p / "does_not_exist.git"), branch="main")
            self.assertEqual(bad["status"], "fetch_failed")
            self.assertFalse((other / ".git").exists())
            self.assertIn("internet connection", bad["message"])

    def test_missing_git_is_explained(self):
        from unittest.mock import patch

        import auto_updater

        with patch.object(auto_updater, "find_git", return_value=""):
            self.assertEqual(auto_updater.connect_to_git(Path("."))["status"], "git_missing")
            self.assertIn("winget install --id Git.Git", auto_updater.connect_to_git(Path("."))["message"])
            with patch.dict("os.environ", {k: "" for k in ("ZENO_NO_AUTO_UPDATE", "CLAUDE_EDITOR_OFFLINE", "SCRIPTCUT_OFFLINE", "ZENO_NO_LIVE")}):
                res = auto_updater.ensure_latest_update(force=True, quiet=True, sync_brain=False)
            self.assertEqual(res["status"], "git_missing")

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

class TestGpu(unittest.TestCase):
    """GPU use: device choice, hardware encoder arguments, CUDA torch selection. No GPU is needed to run these."""

    def test_device_can_be_forced_to_cpu(self):
        import os
        from unittest import mock
        import common

        with mock.patch.dict(os.environ, {"ZENO_DEVICE": "cpu"}):
            self.assertEqual(common.compute_device(), "cpu")

    def test_encoder_args_swap_libx264_for_hardware(self):
        from unittest import mock
        import common

        args = ["-i", "a.mp4", "-c:v", "libx264", "-preset", "veryfast", "-crf", "12", "-c:a", "pcm_s16le", "out.mkv"]
        for enc, marker in (("h264_videotoolbox", "-allow_sw"), ("h264_nvenc", "-cq"), ("h264_qsv", "-global_quality"), ("h264_amf", "-qp_i")):
            with mock.patch.object(common, "hardware_encoder", return_value=enc):
                out = common.with_hardware_encoder(args)
            self.assertIsNotNone(out)
            self.assertIn(enc, out)
            self.assertIn(marker, out)
            for gone in ("libx264", "-preset" if enc in ("h264_videotoolbox", "h264_amf") else "-crf", "-crf"):
                self.assertNotIn(gone, out)
            self.assertEqual(out[-1], "out.mkv")
            self.assertIn("pcm_s16le", out)

    def test_no_hardware_encoder_means_cpu_path(self):
        from unittest import mock
        import common

        with mock.patch.object(common, "hardware_encoder", return_value=""):
            self.assertIsNone(common.with_hardware_encoder(["-c:v", "libx264", "-crf", "18", "o.mp4"]))

    def test_gpu_encode_failure_falls_back_to_cpu(self):
        from unittest import mock
        import common

        calls = []

        def fake(*a, **k):
            calls.append(a)
            if "h264_nvenc" in a:
                raise RuntimeError("no gpu")
            return "ok"

        with mock.patch.object(common, "hardware_encoder", return_value="h264_nvenc"), mock.patch.object(common, "ffmpeg", side_effect=fake):
            self.assertEqual(common.ffmpeg_encode("-c:v", "libx264", "-crf", "18", "o.mp4"), "ok")
        self.assertEqual(len(calls), 2)
        self.assertIn("libx264", calls[1])

    def test_cuda_wheel_tag_from_driver(self):
        import gpu_install

        self.assertEqual(gpu_install.cuda_tag("| NVIDIA-SMI 560 Driver Version: 560.94 CUDA Version: 12.6 |"), "cu126")
        self.assertEqual(gpu_install.cuda_tag("CUDA Version: 12.9"), "cu126")
        self.assertEqual(gpu_install.cuda_tag("CUDA Version: 13.0"), "cu130")
        self.assertIsNone(gpu_install.cuda_tag("CUDA Version: 11.8"))
        self.assertIsNone(gpu_install.cuda_tag(""))

    def test_requirements_refresh_keeps_cuda_torch(self):
        import gpu_install

        lines = gpu_install.requirements_without_torch(ROOT / "requirements.txt")
        self.assertFalse([l for l in lines if l.strip().startswith(("torch==", "torchaudio=="))])
        self.assertTrue(lines)


class TestScriptIsTheTimeline(unittest.TestCase):
    """The script decides order, never file names. Zeno never guesses a line. A plan that breaks script order cannot render."""

    LINES = ["my grandmother wants to go to america next month", "but she never finished tenth standard at school",
             "she learned english on her phone with a teacher", "now she speaks to the officer with confidence",
             "everyone in the village is proud of her today", "you can also start learning from home right now"]

    def _lines(self):
        return [{"id": f"L{i + 1}", "index": i + 1, "text": t, "tokens": t.split(), "speaker": ""} for i, t in enumerate(self.LINES)]

    def _clip(self, name, text):
        ws = [{"w": w, "start": i * 0.4, "end": i * 0.4 + 0.35} for i, w in enumerate(text.split())]
        return {"stem": name, "src": name + ".mp4", "hq_words": ws, "base_words": list(ws), "info": {"duration": len(ws) * 0.4 + 0.3}, "anomalies": [], "rms_env": []}




    def test_long_speech_sharing_common_words_is_not_pulled_to_a_short_earlier_line(self):
        import script_order

        lines = [{"id": "L1", "index": 1, "tokens": "you are so very good".split()}, {"id": "L2", "index": 2, "tokens": "the sun rose above the river".split()},
                 {"id": "L3", "index": 3, "tokens": "this game gets easier when you are so very good at practice every day and you are very good with so much care".split()}]
        tokens = lines[2]["tokens"]
        self.assertEqual(script_order.place_in_script(tokens, lines)[0], 3)

    def test_clip_that_opens_with_the_earlier_line_sorts_there(self):
        import script_order

        lines = self._lines()
        self.assertEqual(script_order.place_in_script((self.LINES[1] + " " + self.LINES[2]).split(), lines)[0], 2)


    def test_order_check_catches_a_wrong_order(self):
        import script_order

        rows = [(f"L{i + 1}", self.LINES[i].split()) for i in (2, 0, 1, 3, 4, 5)]
        rep = script_order.check_order(rows, self._lines())
        self.assertTrue(rep["out_of_order"])

    def test_order_check_catches_words_that_contradict_the_label(self):
        import script_order

        rows = [("L1", self.LINES[2].split()), ("L2", self.LINES[0].split()), ("L3", self.LINES[1].split())]
        self.assertTrue(script_order.check_order(rows, self._lines())["out_of_order"])

    def test_order_check_passes_a_correct_plan(self):
        import script_order

        rep = script_order.check_order([(f"L{i + 1}", t.split()) for i, t in enumerate(self.LINES)], self._lines())
        self.assertEqual((rep["out_of_order"], rep["missing"], rep["repeated"], rep["unknown"]), ([], [], [], []))

    def test_split_line_and_combined_ids_are_not_false_alarms(self):
        import script_order

        w = self.LINES[0].split()
        rows = [("L1a", w[:4]), ("L1b", w[4:]), ("L2_L3", (self.LINES[1] + " " + self.LINES[2]).split()), ("L4", self.LINES[3].split()),
                ("L5", self.LINES[4].split()), ("L6", self.LINES[5].split())]
        rep = script_order.check_order(rows, self._lines())
        self.assertEqual((rep["out_of_order"], rep["missing"], rep["repeated"]), ([], [], []))

    def test_a_long_line_split_over_four_cuts_is_still_heard(self):
        import script_order

        long = "one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen".split()
        lines = [{"id": "L1", "index": 1, "text": " ".join(long), "tokens": long}]
        rows = [("L1a", long[:4]), ("L1b", long[4:8]), ("L1c", long[8:12]), ("L1d", long[12:])]
        rep = script_order.check_order(rows, lines)
        self.assertEqual((rep["unproven"], rep["missing"], rep["repeated"], rep["out_of_order"]), ([], [], [], []))

    def test_order_check_catches_missing_and_repeated(self):
        import script_order

        rows = [("L1", self.LINES[0].split()), ("L1b", self.LINES[0].split()), ("L3", self.LINES[2].split())]
        rep = script_order.check_order(rows, self._lines())
        self.assertIn("L2", rep["missing"])
        self.assertTrue(rep["repeated"])

    def test_silent_beat_with_no_clip_is_an_open_decision(self):
        import script_order

        lines = self._lines()[:2] + [{"id": "L3", "index": 3, "text": "(she looks shocked)", "tokens": [], "beat": True}]
        rep = script_order.check_order([("L1", self.LINES[0].split()), ("L2", self.LINES[1].split())], lines)
        self.assertEqual(rep["missing"], ["L3"])

    def test_director_can_record_a_deliberate_exception(self):
        import tempfile
        import script_order

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "decisions.txt").write_text("accept L2 cut for time, client asked\n", encoding="utf-8")
            self.assertEqual(script_order.read_accepted(Path(tmp)), {"L2": "cut for time, client asked"})


    def test_zeno_makes_no_editing_decisions(self):
        for name in ("group_takes", "select_best_takes", "cut_clips", "assign_segment_ids_marks_and_layouts", "choose_listener", "format_brief_txt"):
            self.assertFalse(hasattr(prepare, name), f"{name} is an editing decision; it belongs to the Director")

    def test_line_map_shows_full_script_candidates_and_unplaced_speech(self):
        import script_order

        a, b, junk = self._clip("A", self.LINES[0]), self._clip("B", self.LINES[0]), self._clip("J", "hello okay camera rolling")
        lines = self._lines()[:2]
        lines[0]["mark"] = "wrong"
        text = script_order.build_line_map(lines, [a, b, junk])
        self.assertIn(self.LINES[0], text)
        self.assertIn("mark=wrong", text)
        self.assertIn("CAND A.mp4", text)
        self.assertIn("CAND B.mp4", text)
        self.assertIn("NO CLIP says this line", text)
        self.assertIn("J.mp4: hello okay", text)

    def test_facts_give_every_word_a_time_and_mark_silent_clips(self):
        c = self._clip("A", "one two three")
        c["macro_silences"] = [{"start": 1.3, "end": 2.0}]
        silent = self._clip("S", "")
        text = prepare.format_facts([c, silent])
        self.assertIn("0.00 one 0.40 two 0.80 three |1.15", text)
        self.assertIn("pauses: 1.30-2.00", text)
        self.assertRegex(text, r"## S\.mp4 \(.*SILENT")

    def _with_words(self, words):
        from unittest import mock
        import script_order

        return mock.patch.object(script_order, "_words_of", return_value=words)

    def test_cut_inside_a_word_is_reported_and_a_cut_on_the_edge_is_not(self):
        import script_order

        ws = [{"w": "hello", "start": 1.0, "end": 1.6}, {"w": "world", "start": 1.8, "end": 2.4}]
        with self._with_words(ws):
            self.assertEqual(len(script_order.cut_problems({"segments": [{"id": "L1", "src": "a", "start": 1.3, "end": 2.5}]})), 1)
            self.assertEqual(script_order.cut_problems({"segments": [{"id": "L1", "src": "a", "start": 0.9, "end": 2.45}]}), [])

    def test_voiced_spans_follow_the_sound_not_the_aligner(self):
        env = [(round(i * 0.01, 2), -20.0 if (0.40 <= i * 0.01 <= 1.20 or 3.0 <= i * 0.01 <= 3.5) else -140.0) for i in range(500)]
        spans = prepare.voiced_spans(env)
        self.assertEqual(len(spans), 2)
        self.assertAlmostEqual(spans[0][0], 0.40, places=1)
        self.assertAlmostEqual(spans[1][1], 3.5, places=1)
        ghost = prepare.words_in_silence([{"w": "Oh", "start": 1.8, "end": 2.4}, {"w": "ok", "start": 0.5, "end": 0.9}], env)
        self.assertEqual([w["w"] for w in ghost], ["Oh"])

    def test_cut_in_a_misaligned_word_over_silence_is_not_a_problem(self):
        from unittest import mock
        import script_order

        ws = [{"w": "end", "start": 43.2, "end": 44.5}]
        env = [(round(43.0 + i * 0.01, 2), -20.0 if i < 70 else -140.0) for i in range(300)]
        plan = {"segments": [{"id": "L13", "src": "a", "start": 43.0, "end": 43.9}]}
        with self._with_words(ws), mock.patch.object(script_order, "_env_of", return_value=env):
            self.assertEqual(script_order.cut_problems(plan), [])
            plan["segments"][0]["end"] = 43.5      # still sounding here: a real cut inside the word
            self.assertEqual(len(script_order.cut_problems(plan)), 1)

    def test_zoom_style_and_moment_survive_save_and_reload(self):
        plan = {"canvas": {"width": 1080, "height": 1920, "fps": 30}, "preset": "single", "segments": [
            {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.0, "zoom": 1.0, "zoom_to": 1.08, "zoom_style": "sudden_in", "zoom_at": 0.25},
            {"id": "L2", "src": "a.mp4", "start": 2.0, "end": 4.0, "zoom": 1.08, "zoom_to": 1.0, "zoom_style": "sudden_out", "zoom_at": 0.1},
            {"id": "L3", "src": "a.mp4", "start": 4.0, "end": 6.0, "zoom": 1.0, "zoom_to": 1.06}]}
        back = edl_parser.parse_edl(edl_parser.format_edl(plan))["segments"]
        self.assertEqual([(s.get("zoom"), s.get("zoom_to"), s.get("zoom_style"), s.get("zoom_at")) for s in back],
                         [(1.0, 1.08, "sudden_in", 0.25), (1.08, 1.0, "sudden_out", 0.1), (1.0, 1.06, None, None)])

    def test_silent_stretches_are_found_and_short_dips_are_not(self):
        import numpy as np
        from align_engine.onset_verifier import AcousticOnsetVerifier

        v = AcousticOnsetVerifier()
        y = np.zeros(16000 * 8, dtype="float32")
        y[:16000 * 2] = 0.2 * np.sin(np.arange(16000 * 2) * 0.1)
        y[16000 * 5:16000 * 7] = 0.2 * np.sin(np.arange(16000 * 2) * 0.1)
        y[int(16000 * 1.0):int(16000 * 1.05)] = 0.0      # a 50 ms closure inside a word
        runs = v.silent_runs(y)
        self.assertEqual(len(runs), 2)
        self.assertAlmostEqual(runs[0][0], 2.0, places=1)
        self.assertAlmostEqual(runs[1][1], 8.0, places=1)

    def test_word_edges_are_pulled_out_of_silence(self):
        import numpy as np
        from align_engine.onset_verifier import AcousticOnsetVerifier

        v = AcousticOnsetVerifier()
        y = np.zeros(16000 * 10, dtype="float32")
        y[:16000 * 3] = 0.2 * np.sin(np.arange(16000 * 3) * 0.1)
        y[16000 * 6:16000 * 7] = 0.2 * np.sin(np.arange(16000) * 0.1)
        run_on = v.clamp_words_to_sound([{"word": "a", "start": 2.5, "end": 4.5}], y)[0]
        overshoot = v.clamp_words_to_sound([{"word": "b", "start": 2.5, "end": 6.02}], y)[0]
        ghost = v.clamp_words_to_sound([{"word": "c", "start": 4.0, "end": 4.4}], y)[0]
        late = v.clamp_words_to_sound([{"word": "d", "start": 4.5, "end": 6.5}], y)[0]
        self.assertLess(run_on["end"], 3.2)
        self.assertLess(overshoot["end"], 3.2)
        self.assertTrue(ghost.get("no_sound"))
        self.assertGreater(late["start"], 5.9)

    def test_aligner_cannot_place_a_word_in_silence(self):
        import numpy as np
        from align_engine.viterbi import ViterbiAligner

        T, C = 200, 5      # 0.02 s per frame: sound at 0-1 s and 3-4 s, silence between
        lp = np.full((T, C), -8.0, dtype="float32")
        lp[:, 0] = -0.01
        lp[130:140, 1] = -0.01       # the model hears token 1 at 2.6 s (inside the silence): the wrong place
        lp[130:140, 0] = -6.0
        lp[170:180, 2] = -0.01
        lp[170:180, 0] = -6.0
        meta = [{"word": "w1", "num_chars": 1, "orig_idx": 0}, {"word": "w2", "num_chars": 1, "orig_idx": 1}]
        plain = ViterbiAligner().align(lp, [1, 2], meta, 0.02)
        guarded = ViterbiAligner().align(lp, [1, 2], meta, 0.02, silent_spans=[(1.0, 3.0)])
        self.assertTrue(1.0 < plain[0]["start"] < 3.0)
        self.assertFalse(1.0 < guarded[0]["start"] < 3.0)

    def test_cut_and_voice_checks_read_the_cache_word_file(self):
        import tempfile
        import json
        from unittest import mock
        import script_order

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "words.json").write_text(json.dumps([{"w": "hi", "start": 1.0, "end": 1.5}]), encoding="utf-8")
            with mock.patch("common.clip_work_dir", return_value=Path(tmp)):
                self.assertEqual(script_order._word_file("x.mp4"), Path(tmp) / "words.json")
                self.assertEqual(script_order._words_of({"src": "x.mp4"})[0]["w"], "hi")

    def test_a_short_voice_miss_is_a_doubt_when_the_files_other_turns_agree(self):
        from unittest import mock
        import script_order, speaker_check

        words = lambda a: [{"w": "w", "start": a + i * 0.2, "end": a + i * 0.2 + 0.15} for i in range(6)]
        plan = {"segments": [{"id": "L1", "src": "a.mp4", "start": 0, "end": 2, "speaker": "A"},
                             {"id": "L2", "src": "b.mp4", "start": 0, "end": 2, "speaker": "B"},
                             {"id": "L3", "src": "a.mp4", "start": 0, "end": 2, "speaker": "A"}]}
        res = {"ok": True, "clips": {0: {"spans": [{"first": 0, "last": 5, "expected": "A", "heard": "B"}]}, 1: {"spans": []}, 2: {"spans": []}}}
        with mock.patch.object(speaker_check, "analyze", return_value=res), \
             mock.patch("common.clip_work_dir", return_value=Path("/nonexistent")), \
             mock.patch.object(script_order, "_word_file", return_value=Path("/x.json")), \
             mock.patch("common.read_json", side_effect=lambda f: words(0)), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch("prepare.compute_10ms_rms_envelope", return_value=[]):
            problems, note = script_order.voice_problems(plan)
        self.assertEqual(problems, [])
        self.assertIn("check by ear", note)

    def test_snapping_never_moves_a_cut_across_a_long_gap(self):
        from unittest import mock
        import script_order

        ws = [{"w": "a", "start": 1.0, "end": 1.4}, {"w": "b", "start": 5.0, "end": 5.4}]
        env = [(round(i * 0.01, 2), -140.0) for i in range(700)]      # a 3.6 s gap of digital silence: every point is "quietest"
        plan = {"segments": [{"id": "L1", "src": "a", "start": 4.9, "end": 6.0}]}
        with self._with_words(ws), mock.patch("common.clip_work_dir", return_value=Path("/nonexistent")), \
             mock.patch("prepare.compute_10ms_rms_envelope", return_value=env), mock.patch.object(Path, "is_file", return_value=True):
            script_order.snap_cuts(plan)
        self.assertGreaterEqual(plan["segments"][0]["start"], 4.9 - script_order.SNAP_REACH_SEC - 1e-6)

    def test_banner_stack_sits_below_the_face_and_moves_with_the_zoom(self):
        from unittest import mock
        import vision_tracker

        vis = {"bbox_norm": [0.36, 0.23, 0.71, 0.50], "focus": [0.55, 0.27]}
        with mock.patch.object(vision_tracker, "analyze_clip_vision", return_value=vis):
            plain = layouts.banner_stack_top({"src": "a.mp4"})
            zoomed = layouts.banner_stack_top({"src": "a.mp4", "zoom": 1.0, "zoom_to": 1.08})
        self.assertGreater(plain, 0.50)               # below the bottom of the face box
        self.assertGreaterEqual(zoomed, plain)         # a zoom pushes the chin down, so the stack moves down too
        with mock.patch.object(vision_tracker, "analyze_clip_vision", side_effect=RuntimeError("no face")):
            self.assertEqual(layouts.banner_stack_top({"src": "a.mp4"}), 0.445)

    def test_tight_report_names_loose_joins_and_pauses_but_allows_the_mark_tail(self):
        from unittest import mock
        import script_order

        words = {"a.mp4": [{"w": "x", "start": 0.1, "end": 0.5}, {"w": "y", "start": 1.2, "end": 1.6}]}
        plan = {"segments": [{"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.2}, {"id": "L2", "src": "a.mp4", "start": 0.0, "end": 2.2}],
                "marks": [{"type": "wrong", "segment": "L1", "at": 1.5, "duration": 0.56}]}
        with mock.patch.object(script_order, "_words_of", side_effect=lambda seg: words[seg["src"]]):
            report = script_order.tight_report(plan)
        text = " ".join(report)
        self.assertIn("pause between 'x' and 'y'", text)                        # a 0.7 s pause inside a cut
        plan["marks"] = []
        with mock.patch.object(script_order, "_words_of", side_effect=lambda seg: words[seg["src"]]):
            self.assertTrue(any("L1 -> L2" in r for r in script_order.tight_report(plan)))   # without a mark the long tail is a loose join

    def test_job_window_starts_once_and_resets_when_the_render_is_done(self):
        import tempfile
        from unittest import mock
        import common

        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(common, "JOB_MARKER", Path(tmp) / "m.json"):
            t1 = common.job_start_epoch()
            t2 = common.job_start_epoch()
            self.assertEqual(t1, t2)
            common.job_done()
            self.assertFalse((Path(tmp) / "m.json").exists())
            self.assertGreaterEqual(common.job_start_epoch(), t1)

    def test_empty_plan_cannot_render(self):
        from unittest import mock
        import tempfile
        import script_order

        with tempfile.TemporaryDirectory() as tmp:
            sp = Path(tmp) / "s.txt"
            sp.write_text("L1 Lady: Hello there my friend\n", encoding="utf-8")
            with mock.patch.object(script_order, "voice_problems", return_value=([], "")):
                g = script_order.full_gate({"segments": []}, sp, Path(tmp))
        self.assertTrue(g["blocked"])
        self.assertIn("EMPTY PLAN", g["problems"])

    def test_clip_sheet_builds_one_image_for_all_clips(self):
        import subprocess
        import tempfile
        import clip_sheet
        import common

        with tempfile.TemporaryDirectory() as tmp:
            clips = []
            for i in range(3):
                f = Path(tmp) / f"c{i}.mp4"
                subprocess.run([str(common.FFMPEG), "-v", "error", "-f", "lavfi", "-i", "testsrc=s=180x320:r=10:d=1", str(f)], check=True)
                clips.append(str(f))
            sheets = clip_sheet.build(Path(tmp) / "out", clips)
            self.assertEqual(len(sheets), 1)
            self.assertTrue(sheets[0].is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
