"""Regression suite for Zeno v3.0.2 retry-loop & false-QC reliability fixes."""

from __future__ import annotations

import math
import struct
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import common
import edl_parser
import script_order
import verify
import zeno


def _write_pcm_wav(path: Path, segments_db_dur: list[tuple[float, float]], sample_rate: int = 16000) -> None:
    """Write a mono 16-bit WAV built from `(amplitude_dbfs, duration_sec)` tone/silence blocks."""
    path.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    phase = 0.0
    freq = 220.0
    for db, dur in segments_db_dur:
        n = max(1, int(round(dur * sample_rate)))
        amp = 0.0 if db <= -100.0 else min(30000.0, 32767.0 * (10.0 ** (db / 20.0)))
        for k in range(n):
            val = int(round(amp * math.sin(2.0 * math.pi * freq * ((k + phase) / sample_rate))))
            val = max(-32767, min(32767, val))
            frames.extend(struct.pack("<h", val))
        phase += n
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(bytes(frames))


class TestV302RetryLoopFixes(unittest.TestCase):
    def test_1_split_id_mark_and_hold_no_duplication_over_5_roundtrips(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            script_p = wd / "script.txt"
            script_p.write_text("S1: Wrong phrase one correct phrase one\n", encoding="utf-8")
            clip_dir = wd / "clip_cache"
            clip_dir.mkdir(parents=True, exist_ok=True)
            _write_pcm_wav(clip_dir / "audio16k.wav", [(-16.0, 4.0)])
            common.write_json(
                clip_dir / "words.json",
                [
                    {"word": "Wrong", "start": 0.20, "end": 0.55},
                    {"word": "phrase", "start": 0.60, "end": 0.95},
                    {"word": "one", "start": 1.00, "end": 1.35},
                    {"word": "correct", "start": 2.40, "end": 2.80},
                    {"word": "phrase", "start": 2.85, "end": 3.20},
                    {"word": "one", "start": 3.25, "end": 3.60},
                ],
            )
            edl_text = (
                "#script script.txt\n"
                "#fps 30\n"
                'L1 clip.mp4 0.15-1.50 sp="S1" hold=0.60 mark:wrong@1.20/0.48 # Wrong phrase one\n'
                'L1 clip.mp4 2.30-3.70 sp="S1" mark:right@1.10/0.48 # correct phrase one\n'
            )
            plan_p = wd / "plan.edl"
            plan_p.write_text(edl_text, encoding="utf-8")
            plan = edl_parser.parse_edl(plan_p)
            self.assertEqual(len(plan["marks"]), 2)
            self.assertEqual(plan["marks"][0].get("seg_index"), 0)
            self.assertEqual(plan["marks"][1].get("seg_index"), 1)

            with mock.patch.object(script_order, "_resolve_clip_cache_dir", return_value=clip_dir):
                for _ in range(5):
                    script_order.calibrate_post_word_beats(plan, script_p, wd)
                    formatted = edl_parser.format_edl(plan)
                    plan = edl_parser.parse_edl(formatted)

            self.assertEqual(len(plan["marks"]), 2, "marks must not exponentially duplicate on split IDs")
            self.assertEqual(plan["marks"][0]["type"], "wrong")
            self.assertEqual(plan["marks"][1]["type"], "right")
            # Roundtrip again via format_edl alone should stay strictly 2 marks and identical end timestamps
            ends_before = [s["end"] for s in plan["segments"]]
            holds_before = [m.get("hold") for m in plan["marks"]]
            formatted2 = edl_parser.format_edl(plan)
            plan2 = edl_parser.parse_edl(formatted2)
            self.assertEqual(len(plan2["marks"]), 2)
            self.assertEqual([s["end"] for s in plan2["segments"]], ends_before)
            self.assertEqual([m.get("hold") for m in plan2["marks"]], holds_before)
            qc_marks = verify._collect_deduped_marks(plan2)
            self.assertEqual(len(qc_marks), 2)
            self.assertEqual([m.get("seg_index") for m in qc_marks], [0, 1])

    def test_2_sudden_in_zoom_zero_drift_across_protect_tails_and_tighten_joins(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            clip_dir = wd / "clip_cache"
            clip_dir.mkdir(parents=True, exist_ok=True)
            # 0.00-0.16s silence, 0.16-0.25s voice onset (tail of pause), 0.25-1.50s speech
            _write_pcm_wav(
                clip_dir / "audio16k.wav",
                [(-70.0, 0.20), (-16.0, 1.30)],
            )
            words = [{"word": "hello", "start": 0.22, "end": 0.60}, {"word": "world", "start": 0.70, "end": 1.35}]
            common.write_json(clip_dir / "words.json", words)
            common.write_json(clip_dir / "speech_spans.json", {"spans": [[0.20, 1.40]]})

            seg = {
                "id": "L2",
                "src": "clip.mp4",
                "start": 0.12,
                "end": 1.45,
                "speaker": "A",
                "zoom": "sudden_in:1.08@0.80",
                "zoom_at": 0.80,
                "marks": [],
                "sfx": [],
            }
            plan = {"segments": [seg], "marks": [{"segment": "L2", "seg_index": 0, "type": "wrong", "at": 0.50}]}

            # Raw clip time of zoom cue is 0.12 + 0.80 = 0.92s
            # Expand head backward by -0.05s, then trim forward by +0.05s
            seg["start"] = 0.07
            script_order._shift_seg_relative_cues(seg, -0.05, plan=plan, seg_idx=0)
            self.assertAlmostEqual(float(seg["zoom_at"]), 0.85, places=3)
            self.assertIn("@0.85", str(seg["zoom"]))
            self.assertAlmostEqual(float(plan["marks"][0]["at"]), 0.55, places=3)

            seg["start"] = 0.12
            script_order._shift_seg_relative_cues(seg, 0.05, plan=plan, seg_idx=0)
            self.assertAlmostEqual(float(seg["zoom_at"]), 0.80, places=3)
            self.assertIn("@0.80", str(seg["zoom"]))
            self.assertAlmostEqual(float(plan["marks"][0]["at"]), 0.50, places=3)

            # End-to-end tighten_joins -> protect_tails invariance for multiple cycles
            seg["start"] = 0.02  # extra leading silence -> tighten_joins trims head forward -> shifts zoom_at & @t
            plan_cycle = {"segments": [seg], "marks": []}
            raw_zoom_src = float(seg["start"]) + float(seg["zoom_at"])
            with mock.patch.object(script_order, "_resolve_clip_cache_dir", return_value=clip_dir):
                for _ in range(3):
                    script_order.tighten_joins(plan_cycle)
                    script_order.protect_tails(plan_cycle)
                    script_order.tighten_joins(plan_cycle)
            self.assertAlmostEqual(float(seg["start"]) + float(seg["zoom_at"]), raw_zoom_src, delta=0.025)

    def test_3_verify_eof_guard_and_decisions_txt_in_separate_work_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work_dir = root / "work_proj"
            outs_dir = root / "downloads_outs"
            clip_dir = root / "clip_cache"
            work_dir.mkdir(parents=True, exist_ok=True)
            outs_dir.mkdir(parents=True, exist_ok=True)
            clip_dir.mkdir(parents=True, exist_ok=True)

            # 1.00s clip where speech reaches the very end (physical EOF = ~0.99s)
            _write_pcm_wav(clip_dir / "audio16k.wav", [(-16.0, 1.00)])
            common.write_json(
                clip_dir / "words.json",
                [
                    {"word": "one", "start": 0.10, "end": 0.45},
                    {"word": "two", "start": 0.50, "end": 1.00},
                ],
            )
            common.write_json(clip_dir / "speech_spans.json", {"spans": [[0.08, 1.00]]})

            # L4 ends cleanly at physical EOF (en=1.00); L5 cuts mid-word "one" at 0.28s, accepted via decisions.txt
            (work_dir / "decisions.txt").write_text("accept L5 unavoidable unscripted speech\n", encoding="utf-8")
            fake_mp4 = outs_dir / "reel_test.mp4"
            fake_mp4.write_bytes(b"")

            plan = {
                "_plan_dir": str(work_dir),
                "canvas": {"fps": 30},
                "segments": [
                    {"id": "L4", "src": "clip.mp4", "start": 0.04, "end": 1.00, "speaker": "A"},
                    {"id": "L5", "src": "clip.mp4", "start": 0.04, "end": 0.28, "speaker": "A"},
                    {"id": "L5a", "src": "clip.mp4", "start": 0.48, "end": 0.72, "speaker": "A"},
                ],
                "marks": [],
            }

            with mock.patch.object(verify, "_resolve_clip_cache_dir", return_value=clip_dir):
                passed, clipped, pauses, _, clicks = verify.check_cuts_and_pauses(plan, mp4_path=fake_mp4)
            self.assertEqual(clipped, 0)
            self.assertEqual(pauses, 0)
            self.assertEqual(clicks, 0)
            self.assertTrue(passed)

    def test_4_silent_reaction_shot_exempt_from_loose_join_and_pause(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            clip_dir = root / "clip_cache"
            react_dir = root / "react_cache"
            clip_dir.mkdir(parents=True, exist_ok=True)
            react_dir.mkdir(parents=True, exist_ok=True)
            common.write_json(react_dir / "words.json", [])
            common.write_json(clip_dir / "words.json", [{"word": "spoken", "start": 0.10, "end": 0.90}])

            seg_react = {"id": "L6", "src": "react.mp4", "start": 0.0, "end": 1.1, "speaker": "", "text": ""}
            self.assertTrue(verify._segment_is_silent_reaction(seg_react, react_dir))
            # Untapped segment WITH live words must NOT be exempted as a silent reaction
            seg_untagged_speech = {"id": "L6b", "src": "clip.mp4", "start": 0.0, "end": 1.1, "speaker": "", "text": ""}
            self.assertFalse(verify._segment_is_silent_reaction(seg_untagged_speech, clip_dir))

            # Build rendered 16kHz PCM across [L5 (0-1s spoken), L6 (1-2.1s reaction silent), L7 (2.1-3s spoken)]
            pcm_vals: list[int] = []
            sr = 16000
            for k in range(sr * 1):
                pcm_vals.append(int(6000 * math.sin(2.0 * math.pi * 220.0 * k / sr)))
            pcm_vals.extend([0] * int(round(sr * 1.1)))
            for k in range(sr * 1):
                pcm_vals.append(int(6000 * math.sin(2.0 * math.pi * 220.0 * k / sr)))
            import array as _arr

            stats = verify._measure_pcm_join_gaps(
                _arr.array("h", pcm_vals),
                [
                    {"id": "L5", "start": 0.0, "end": 1.0, "speaker": "A"},
                    seg_react,
                    {"id": "L7", "start": 2.1, "end": 3.0, "speaker": "B"},
                ],
                [0.0, 1.0, 2.1],
                timeline_mark_holds=[(1.0, 2.1)],
                sample_rate=sr,
                silent_reaction_indices={1},
            )
            self.assertEqual(stats.get("excessive_joins") or [], [])

    def test_5_split_h_face_scale_tolerance_with_aligned_eyelines(self) -> None:
        import numpy as np

        class _FakeCap:
            def __init__(self, *_a, **_kw) -> None:
                self._t = 0.0

            def isOpened(self) -> bool:
                return True

            def get(self, prop: int) -> float:
                # width=1080, height=1920
                if prop == 3:
                    return 1080.0
                if prop == 4:
                    return 1920.0
                return 0.0

            def set(self, _prop: int, val: float) -> bool:
                self._t = float(val) / 1000.0
                return True

            def read(self):
                # Distinct non-flat/non-black frame gradient varying over time
                base = np.full((1920, 1080, 3), 90, dtype=np.uint8)
                shift = int((self._t * 50) % 80)
                base[100:400, 100 + shift : 250 + shift] = 180
                return True, base

            def release(self) -> None:
                pass

        def _run_case(fh_l: float, fh_r: float, ey_l: float, ey_r: float) -> dict:
            plan = {
                "canvas": {"fps": 30},
                "segments": [{"id": "L10", "src": "a.mp4", "start": 0.0, "end": 1.6, "layout": "split_h"}],
                "marks": [],
            }
            call_n = {"n": 0}

            def _detect(_subframe):
                idx = call_n["n"] % 2
                call_n["n"] += 1
                if idx == 0:
                    return [{"bbox_norm": [0.2, 0.2, 0.6, 0.2 + fh_l], "eye_center_norm": [0.4, ey_l], "confidence": 0.95}]
                return [{"bbox_norm": [0.2, 0.2, 0.6, 0.2 + fh_r], "eye_center_norm": [0.4, ey_r], "confidence": 0.95}]

            with tempfile.TemporaryDirectory() as td:
                mp4 = Path(td) / "dummy.mp4"
                mp4.write_bytes(b"00")
                with mock.patch("cv2.VideoCapture", _FakeCap), mock.patch(
                    "vision_tracker.detect_faces_in_frame", side_effect=_detect
                ), mock.patch("vision_tracker.prominent_faces", side_effect=lambda x: x):
                    return verify.check_frames(plan, mp4)

        # Case A: ratio = 1.40 with aligned eyelines (diff=0.01 <= 0.035) -> pass + warning
        res_ok = _run_case(0.28, 0.20, 0.24, 0.25)
        self.assertTrue(res_ok["ok"])
        self.assertEqual(res_ok["split_alignment"], [])
        self.assertTrue(res_ok["split_alignment_warnings"])

        # Case B: ratio = 1.40 with misaligned eyelines (diff=0.06 > 0.035) -> fail
        res_ey_bad = _run_case(0.28, 0.20, 0.20, 0.26)
        self.assertFalse(res_ey_bad["ok"])
        self.assertTrue(res_ey_bad["split_alignment"])

        # Case C: ratio = 1.94 even with aligned eyelines -> fail (> 1.55)
        res_extreme = _run_case(0.35, 0.18, 0.24, 0.25)
        self.assertFalse(res_extreme["ok"])
        self.assertTrue(res_extreme["split_alignment"])

    def test_6_post_mix_safety_limiter_and_mark_dedup_in_render(self) -> None:
        render_src = (SCRIPTS / "render.py").read_text(encoding="utf-8")
        self.assertTrue("alimiter=limit=0.76:attack=5:release=50:asc=true:level=false:latency=true" in render_src)
        self.assertTrue('plan["_plan_dir"] = str(plan_path.parent)' in render_src)
        self.assertTrue("seen_marks" in render_src and "m_key = (t_idx, mt, m_at, st_k)" in render_src)

    def test_7_prerender_pause_risk_from_cached_silences_json(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            clip_dir = wd / "clip_cache"
            clip_dir.mkdir(parents=True, exist_ok=True)
            common.write_json(clip_dir / "words.json", [{"word": "hi", "start": 0.2, "end": 0.6}])
            common.write_json(clip_dir / "silences.json", [{"start": 2.00, "end": 2.65}])

            plan = {
                "segments": [{"id": "L8", "src": "clip.mp4", "start": 1.0, "end": 4.0, "speaker": "A"}],
                "marks": [],
            }
            with mock.patch.object(verify, "_resolve_clip_cache_dir", return_value=clip_dir):
                risks = zeno._scan_cached_silences_pause_risks(plan, wd)
            self.assertEqual(len(risks), 1)
            self.assertIn("L8:", risks[0])

            # Clamped by audio_end before 2.00s -> no risk
            plan_ae = {
                "segments": [{"id": "L8", "src": "clip.mp4", "start": 1.0, "end": 4.0, "audio_end": 0.85, "speaker": "A"}],
                "marks": [],
            }
            with mock.patch.object(verify, "_resolve_clip_cache_dir", return_value=clip_dir):
                self.assertEqual(zeno._scan_cached_silences_pause_risks(plan_ae, wd), [])


if __name__ == "__main__":
    unittest.main()
