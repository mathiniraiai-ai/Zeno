"""Unit tests for Zeno v3.0.6: speedups, Stop Saying fixes, QC regression fix, and Download folder replacement."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import deliver  # noqa: E402
import layouts  # noqa: E402
import notify  # noqa: E402
import script_order  # noqa: E402
import verify  # noqa: E402


class SpeedAndFFmpegConfigTests(unittest.TestCase):
    def test_common_ffmpeg_uses_fast_threads_by_default_and_caps_in_cool_mode(self) -> None:
        captured = []

        def fake_run(cmd, *, low_priority=False, timeout=180, **kwargs):
            captured.append((list(cmd), bool(low_priority)))
            return None

        with mock.patch.object(common, "run", side_effect=fake_run):
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop("ZENO_COOL", None)
                os.environ.pop("ZENO_FFMPEG_THREADS", None)
                common.ffmpeg("-i", "in.mp4", "out.mkv")
            with mock.patch.dict(os.environ, {"ZENO_COOL": "1"}, clear=False):
                os.environ.pop("ZENO_FFMPEG_THREADS", None)
                common.ffmpeg("-i", "in.mp4", "out.mkv")

        self.assertEqual(len(captured), 2)
        cmd_fast, lp_fast = captured[0]
        cmd_cool, lp_cool = captured[1]
        self.assertFalse(lp_fast)
        self.assertTrue(lp_cool)
        ft_fast = int(cmd_fast[cmd_fast.index("-filter_threads") + 1])
        ft_cool = int(cmd_cool[cmd_cool.index("-filter_threads") + 1])
        self.assertGreaterEqual(ft_fast, 4)
        self.assertLessEqual(ft_cool, 2)


class DeliveryFolderReplacementTests(unittest.TestCase):
    def test_unique_job_output_uses_stable_per_video_folder_without_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_root = Path(td)
            existing = out_root / "My Video"
            existing.mkdir(parents=True)
            (existing / "reel_My Video.mp4").write_bytes(b"old")
            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": str(out_root)}):
                p = common.unique_job_output("My Video")
            self.assertEqual(p, existing / "reel_My Video.mp4")
            self.assertNotIn("(2)", str(p))

    def test_deliver_replaces_existing_folder_and_deletes_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            td_p = Path(td)
            work_dir = td_p / "work" / "proj1"
            work_dir.mkdir(parents=True)
            staging_dir = td_p / "staging"
            staging_dir.mkdir(parents=True)
            out_root = td_p / "Zeno Outs"
            old_folder = out_root / "Demo Edit"
            old_folder.mkdir(parents=True)
            (old_folder / "reel_Demo Edit.mp4").write_bytes(b"OLD_REEL_BYTES")
            (old_folder / "stale_old.mp4").write_bytes(b"STALE_LEFTOVER")

            fresh_mp4 = staging_dir / "reel_Demo Edit.mp4"
            fresh_mp4.write_bytes(b"NEW_REEL_BYTES_123456")
            plan_json = work_dir / "plan.json"
            plan_json.write_text(json.dumps({"output": str(fresh_mp4), "segments": []}), encoding="utf-8")

            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": str(out_root)}), \
                 mock.patch.object(deliver, "_export_premiere_if_missing"), \
                 mock.patch.object(notify, "notify_delivery", return_value={"notified": True, "reason": "ok"}):
                res = deliver.deliver_to_downloads(plan_json, name="Demo Edit")

            self.assertEqual(Path(res["folder"]).resolve(), old_folder.resolve())
            self.assertFalse((old_folder / "stale_old.mp4").exists())
            self.assertEqual((old_folder / "reel_Demo Edit.mp4").read_bytes(), b"NEW_REEL_BYTES_123456")


class StopSayingAndVerifyRegressionTests(unittest.TestCase):
    def test_append_peer_video_input_loops_when_idle_span_exceeds_peer_duration(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            peer_reg = Path(td) / "W1.mp4"
            peer_reg.write_bytes(b"dummy")
            inputs_reg, fc_reg = [], []
            lbl_reg = layouts._append_peer_video_input(
                peer={"src": str(peer_reg), "idle_span": [2.74, 6.52]},
                peer_src=peer_reg,
                peer_dur=5.87,
                dur=3.78,
                exact=3.78,
                fps=24,
                inputs=inputs_reg,
                fc=fc_reg,
                peer_idx=1,
            )
            self.assertEqual(lbl_reg, "[peer_loop_in]")
            self.assertTrue(any("loop=loop=-1" in node for node in fc_reg))

            peer_sil = Path(td) / "R_extra.mp4"
            peer_sil.write_bytes(b"dummy")
            inputs_sil, fc_sil = [], []
            lbl_sil = layouts._append_peer_video_input(
                peer={"src": str(peer_sil), "idle_span": [2.74, 6.52]},
                peer_src=peer_sil,
                peer_dur=5.87,
                dur=3.78,
                exact=3.78,
                fps=24,
                inputs=inputs_sil,
                fc=fc_sil,
                peer_idx=1,
            )
            self.assertEqual(lbl_sil, "[peer_loop_in]")
            self.assertIn("-stream_loop", inputs_sil)

    def test_calibrate_post_word_beats_preserves_explicit_phrase_spanning_banner(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmp_dir = Path(td)
            clip_dir = tmp_dir / "clip_cache"
            clip_dir.mkdir()
            words = [
                {"w": "I'm", "start": 0.10, "end": 0.35},
                {"w": "very", "start": 0.36, "end": 0.70},
                {"w": "tired", "start": 0.71, "end": 1.12},
            ]
            (clip_dir / "hq_words.json").write_text(json.dumps(words), encoding="utf-8")
            script_path = tmp_dir / "script.txt"
            script_path.write_text('L4 Woman: "I\'m very tired" (X Sign and Text Strikethrough)\n', encoding="utf-8")

            plan = {
                "segments": [
                    {
                        "id": "L4",
                        "src": str(tmp_dir / "clip.mp4"),
                        "start": 0.0,
                        "end": 1.65,
                        "layout": "single",
                        "zoom": 1.0,
                        "banners": [{"text": "I'm very tired", "at": 0.0, "duration": 1.15}],
                    }
                ],
                "marks": [{"type": "wrong", "segment": "L4", "at": 1.12, "duration": 0.48, "style": "flash"}],
            }
            with mock.patch("common.clip_work_dir", return_value=clip_dir):
                script_order.calibrate_post_word_beats(plan, script_path, tmp_dir)

            seg = plan["segments"][0]
            self.assertAlmostEqual(float(seg["banners"][0]["at"]), 0.0, places=3)
            self.assertAlmostEqual(float(seg["banners"][0]["duration"]), 1.15, places=3)
            self.assertLessEqual(float(seg.get("hold_tail", 0.0)), 0.01)

    def test_long_stop_saying_banner_slashes_late_while_short_beat_slashes_at_0_08s(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            src = Path(td) / "clip.mp4"
            src.write_bytes(b"mp4")
            with mock.patch.object(layouts, "probe", return_value={"width": 720, "height": 1280, "has_audio": True, "duration": 10.0}), \
                 mock.patch.object(layouts, "_measure_clip_rms_gain_db", return_value=0.0), \
                 mock.patch.object(layouts, "compute_face_color_match_filter", return_value=""), \
                 mock.patch.object(layouts, "render_stop_saying_banner_png", return_value=Path(td) / "bn.png"):
                args_long, _, _ = layouts.build_segment_ffmpeg_args(
                    {"id": "L4", "src": str(src), "start": 0.0, "end": 1.5, "banners": [{"text": "I'm very tired", "at": 0.0, "duration": 1.30}]},
                    {}, 720, 1280, 24, Path(td) / "out1.mkv",
                )
                args_short, _, _ = layouts.build_segment_ffmpeg_args(
                    {"id": "L4", "src": str(src), "start": 0.0, "end": 1.5, "banners": [{"text": "I'm very tired", "at": 0.8, "duration": 0.48}]},
                    {}, 720, 1280, 24, Path(td) / "out2.mkv",
                )
            fc_long = args_long[args_long.index("-filter_complex") + 1]
            fc_short = args_short[args_short.index("-filter_complex") + 1]
            self.assertIn("between(t,0.000,0.884)", fc_long)
            self.assertIn("between(t,0.800,0.880)", fc_short)

    def test_verify_check_picture_timeline_accepts_plan_dir_without_name_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td) / "proj"
            (pdir / "_segments").mkdir(parents=True)
            mp4 = Path(td) / "reel.mp4"
            mp4.write_bytes(b"mp4")
            plan = {"canvas": {"width": 720, "height": 1280, "fps": 24}, "segments": [{"id": "L1", "src": "clip.mp4", "start": 0.0, "end": 1.0}]}
            with mock.patch("cv2.VideoCapture"):
                ok, bad, compared = verify.check_picture_timeline(plan, mp4, plan_dir=pdir)
            self.assertTrue(ok)
            self.assertEqual(bad, [])
            self.assertEqual(compared, 0)


if __name__ == "__main__":
    unittest.main()
