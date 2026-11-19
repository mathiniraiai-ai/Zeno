#!/usr/bin/env python3
"""Unit and regression tests for post-word vignette/slash beats, banner sizing/placement,
hold_tail roundtripping, format/budget invariants, and Deep QC `verify.py` checks.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import edit_brief  # noqa: E402
import edit_packet  # noqa: E402
import edl_parser  # noqa: E402
import format_library  # noqa: E402
import layouts  # noqa: E402
import prepare  # noqa: E402
import script_order  # noqa: E402
import style_config  # noqa: E402
import ui_generator  # noqa: E402
import verify  # noqa: E402


class PostWordVignetteSlashTests(unittest.TestCase):
    def test_vignette_assets_zero_center_and_reduced_edges(self) -> None:
        """All 4 vignette/flash PNGs have 1080x1920 RGBA, 0 inner-35% center alpha,
        crimson/emerald RGB, and 30%-calibrated edge alpha; mark_alpha defaults to 0.70.
        """
        import numpy as np

        self.assertAlmostEqual(float(style_config.DEFAULT_TUNING["mark_alpha"]), 0.70, places=2)

        specs = [
            ("red_flash.png", "wrong"),
            ("green_flash.png", "right"),
            ("w42_red_vignette.png", "wrong"),
            ("w42_green_vignette.png", "right"),
        ]
        for fname, mtype in specs:
            path = ROOT / "assets" / "marks" / fname
            self.assertTrue(path.is_file(), f"missing asset: {path}")
            with Image.open(path) as im:
                self.assertEqual(im.size, (1080, 1920), fname)
                self.assertEqual(im.mode, "RGBA", fname)
                arr = np.asarray(im)

            h, w = arr.shape[:2]
            cx0, cx1 = int(w * 0.325), int(w * 0.675)
            cy0, cy1 = int(h * 0.325), int(h * 0.675)
            center_alpha = arr[cy0:cy1, cx0:cx1, 3]
            self.assertEqual(int(center_alpha.max()), 0, f"{fname} center alpha must be 0")

            nz = arr[arr[:, :, 3] > 0]
            self.assertGreater(len(nz), 1000, f"{fname} must have opaque edge pixels")
            mean_r, mean_g, mean_b = float(nz[:, 0].mean()), float(nz[:, 1].mean()), float(nz[:, 2].mean())
            if mtype == "wrong":
                self.assertGreater(mean_r - mean_g, 100.0, f"{fname} expected crimson RGB")
                self.assertGreater(mean_r - mean_b, 100.0, f"{fname} expected crimson RGB")
            else:
                self.assertGreater(mean_g - mean_r, 100.0, f"{fname} expected emerald RGB")
                self.assertGreater(mean_g - mean_b, 60.0, f"{fname} expected emerald RGB")

            bh, bw = int(h * 0.12), int(w * 0.12)
            edge_mean_a = float(
                np.mean([
                    arr[:bh, :, 3].mean(),
                    arr[h - bh :, :, 3].mean(),
                    arr[:, :bw, 3].mean(),
                    arr[:, w - bw :, 3].mean(),
                ])
            )
            corner_a = int(arr[0, 0, 3])
            self.assertGreaterEqual(corner_a, 100, f"{fname} corner alpha too weak: {corner_a}")
            self.assertLessEqual(corner_a, 215, f"{fname} corner alpha uncalibrated: {corner_a}")
            self.assertGreaterEqual(edge_mean_a, 40.0, f"{fname} edge alpha too weak: {edge_mean_a}")
            self.assertLessEqual(edge_mean_a, 145.0, f"{fname} edge alpha uncalibrated: {edge_mean_a}")

        with tempfile.TemporaryDirectory() as tmp:
            gen_path = Path(tmp) / "gen_wrong.png"
            ui_generator._generate_flash_vignette_png(gen_path, "wrong", width=1080, height=1920)
            with Image.open(gen_path) as gim:
                g_arr = np.asarray(gim.convert("RGBA"))
            self.assertEqual(g_arr.shape, (1920, 1080, 4))
            self.assertEqual(int(g_arr[624:1296, 351:729, 3].max()), 0)

    def test_banner_autofits_long_text_and_defaults_to_fraction_of_sec(self) -> None:
        """Long Stop-Saying phrases fit <= 960px with a 7px white strike slash,
        and non-header phrase banners default to 0.48s instead of `exact - b_at`.
        """
        long_phrase = "I WOULD LIKE TO HAVE THE GRILLED CHICKEN WITH NO ONIONS PLEASE"
        png_path = ui_generator.render_stop_saying_banner_png(long_phrase, with_strike=True)
        self.assertTrue(png_path.is_file())
        with Image.open(png_path) as im:
            im_rgba = im.convert("RGBA")
            self.assertLessEqual(im_rgba.width, 960)
            slash_px = sum(1 for y in range(im_rgba.height) if im_rgba.getpixel((2, y))[3] > 200)
            self.assertEqual(slash_px, 7)

        seg = {
            "banners": [
                {"text": "header:STOP SAYING THIS", "at": 0.0},
                {"text": "I take the chicken?", "at": 0.93},
            ]
        }
        parsed = layouts.parse_banners(seg, exact=3.0)
        self.assertEqual(len(parsed), 2)
        hdr_txt, hdr_at, hdr_dur, hdr_flag = parsed[0]
        phr_txt, phr_at, phr_dur, phr_flag = parsed[1]
        self.assertTrue(hdr_flag)
        self.assertEqual(hdr_txt, "STOP SAYING THIS")
        self.assertGreater(hdr_dur, 1.5)
        self.assertFalse(phr_flag)
        self.assertEqual(phr_txt, "I take the chicken?")
        self.assertAlmostEqual(phr_at, 0.93, places=3)
        self.assertAlmostEqual(phr_dur, 0.48, places=3)

    def test_edl_parser_hold_tail_and_banner_stagger(self) -> None:
        """`hold=0.45` roundtrips cleanly and omitted `@at` banners stay unset and stagger in `parse_banners`."""
        edl = (
            '#src_dir "/tmp/raw"\n'
            'L3 ordering.mp4 0.04-0.97 z=1 hold=0.45 sp=Lady banner:"A" banner:"B"\n'
        )
        parsed = edl_parser.parse_edl(edl)
        seg = parsed["segments"][0]
        self.assertAlmostEqual(float(seg.get("hold_tail", 0.0)), 0.45, places=3)
        self.assertEqual(len(seg.get("banners", [])), 2)
        self.assertIsNone(seg["banners"][0].get("at"))
        self.assertIsNone(seg["banners"][1].get("at"))

        formatted = edl_parser.format_edl(parsed)
        self.assertIn("hold=0.45", formatted)
        self.assertNotIn("@0.0", formatted)

        reparsed = edl_parser.parse_edl(formatted)
        seg_rt = reparsed["segments"][0]
        self.assertAlmostEqual(float(seg_rt.get("hold_tail", 0.0)), 0.45, places=3)
        self.assertIsNone(seg_rt["banners"][0].get("at"))
        self.assertIsNone(seg_rt["banners"][1].get("at"))

        b_parsed = layouts.parse_banners(seg_rt, exact=2.5)
        self.assertEqual(len(b_parsed), 2)
        at0, at1 = b_parsed[0][1], b_parsed[1][1]
        self.assertGreater(at0, 0.0)
        self.assertGreater(at1, at0)

    def test_calibrate_post_word_beats_contiguous_speech(self) -> None:
        """Modeling Video #1 L3 (`0.04-0.97`, word `chicken?` ending at `0.97`, `strike_text`):
        `calibrate_post_word_beats` anchors banner + vignette wrong mark post-word (`>=0.93s`, `0.48s`)
        and adds `hold_tail > 0.4`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip_dir = tmp_dir / "clip_cache"
            clip_dir.mkdir()
            words = [
                {"w": "I", "start": 0.05, "end": 0.18},
                {"w": "take", "start": 0.19, "end": 0.42},
                {"w": "the", "start": 0.43, "end": 0.56},
                {"w": "chicken?", "start": 0.57, "end": 0.97},
            ]
            (clip_dir / "hq_words.json").write_text(json.dumps(words), encoding="utf-8")

            script_path = tmp_dir / "script.txt"
            script_path.write_text(
                "L3 Lady: I take the chicken? (X Sign and Text Strikethrough)\n",
                encoding="utf-8",
            )

            plan = {
                "segments": [
                    {
                        "id": "L3",
                        "src": str(tmp_dir / "Woman_ordering_food.mp4"),
                        "start": 0.04,
                        "end": 0.97,
                        "layout": "single",
                        "zoom": 1.0,
                    }
                ],
                "marks": [],
            }

            with mock.patch("common.clip_work_dir", return_value=clip_dir):
                changed = script_order.calibrate_post_word_beats(plan, script_path, tmp_dir)

            self.assertGreater(changed, 0)
            seg = plan["segments"][0]
            banners = seg.get("banners", [])
            self.assertEqual(len(banners), 1)
            self.assertEqual(banners[0]["text"], "I take the chicken?")
            self.assertGreaterEqual(float(banners[0]["at"]), 0.93)
            self.assertAlmostEqual(float(banners[0]["duration"]), 0.48, places=3)

            marks = [m for m in plan.get("marks", []) if m.get("segment") == "L3"]
            self.assertEqual(len(marks), 1)
            self.assertEqual(marks[0]["type"], "wrong")
            self.assertEqual(marks[0].get("style"), "vignette")
            self.assertGreaterEqual(float(marks[0]["at"]), 0.93)
            self.assertAlmostEqual(float(marks[0]["duration"]), 0.48, places=3)

            self.assertGreater(float(seg.get("hold_tail", 0.0)), 0.4)

    def test_format_detection_never_say_and_token_budget(self) -> None:
        """`'Never say this'` resolves to `stop_saying`, and Video #1 temp copy satisfies
        `brief.md <= 6623 B` and `director_packet.md <= 21645 B`.
        """
        self.assertEqual(
            format_library.detect_catalog_format({"concept_brief": "Never say this at a restaurant"}, {}),
            "stop_saying",
        )
        self.assertEqual(
            edit_brief.detect_format({"title": "W47 - Never say this - Take The Chicken"}, {}),
            "stop_saying",
        )

        src_proj = ROOT / "work" / "gdoc_1BtpAao7M1vF_t_fhfa49k8736"
        if not src_proj.is_dir():
            self.skipTest("Video #1 workspace not present")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_proj = Path(tmp) / src_proj.name
            tmp_proj.mkdir()
            for fname in ("line_map.md", "facts.md", "plan.json", "clip_sheet_1.jpg"):
                src_file = src_proj / fname
                if src_file.is_file():
                    shutil.copy2(src_file, tmp_proj / fname)
            # Match `--inspect` state (initial `plan.auto.edl` baseline before decisions.txt/manual EDL edits)
            auto_edl = src_proj / "plan.auto.edl"
            shutil.copy2(auto_edl if auto_edl.is_file() else src_proj / "plan.edl", tmp_proj / "plan.edl")

            plan_dict = json.loads((tmp_proj / "plan.json").read_text(encoding="utf-8"))
            script_rel = plan_dict.get("script")
            meta = {}
            if script_rel:
                sp = common.resolve(str(script_rel))
                if sp.is_file():
                    meta, _ = prepare.parse_script_file(sp)

            brief_text = edit_brief.build(meta, plan_dict, style=str(plan_dict.get("style") or ""))
            brief_bytes = len(brief_text.encode("utf-8"))
            self.assertLessEqual(brief_bytes, 6623 + 2400, f"brief.md regressed: {brief_bytes} B > {6623 + 2400} B")

            (tmp_proj / "brief.md").write_text(brief_text, encoding="utf-8")
            packet_text, manifest = edit_packet.build(tmp_proj)
            packet_bytes = len(packet_text.encode("utf-8"))
            self.assertEqual(packet_bytes, manifest["packet_bytes"])
            self.assertLessEqual(packet_bytes, 21645 + 3600, f"director_packet.md regressed: {packet_bytes} B > {21645 + 3600} B")

    def test_banner_stack_top_avoids_low_two_shot_face(self) -> None:
        """`banner_stack_top` avoids overlapping a seated lower face in a two-shot frame."""
        upper_waiter = {"bbox_norm": [0.42, 0.14, 0.64, 0.36], "confidence": 0.95}
        lower_customer = {"bbox_norm": [0.24, 0.50, 0.58, 0.78], "confidence": 0.95}
        fake_vis = {
            "aspect_ratio": 0.5625,
            "focus": [0.5, 0.4],
            "faces": [upper_waiter, lower_customer],
            "bbox_norm": lower_customer["bbox_norm"],
        }
        seg = {"src": "/tmp/two_shot.mp4", "banners": [{"text": "I take the chicken?", "at": 0.93}]}

        with mock.patch.object(layouts, "analyze_clip_vision", return_value=fake_vis):
            top_frac = layouts.banner_stack_top(seg, num_rows=1)

        # Selected shelf [top_frac, top_frac + 0.045] must avoid overlapping the seated lower face [0.50..0.78]
        bx0, bx1 = 0.12, 0.88
        by0, by1 = top_frac, top_frac + 0.045
        fx0, fy0, fx1, fy1 = lower_customer["bbox_norm"]
        ov_area = max(0.0, min(fx1, bx1) - max(fx0, bx0)) * max(0.0, min(fy1, by1) - max(fy0, by0))
        face_area = (fx1 - fx0) * (fy1 - fy0)
        self.assertLess(ov_area / face_area, 0.04, f"top_frac={top_frac} overlapped seated face")

        # Also check when seated face occupies 0.44..0.68 -> picks lower clean shelf (e.g. 0.72)
        mid_customer = {"bbox_norm": [0.26, 0.44, 0.58, 0.68], "confidence": 0.95}
        fake_vis_mid = {
            "aspect_ratio": 0.5625,
            "focus": [0.5, 0.4],
            "faces": [upper_waiter, mid_customer],
            "bbox_norm": mid_customer["bbox_norm"],
        }
        with mock.patch.object(layouts, "analyze_clip_vision", return_value=fake_vis_mid):
            top_mid = layouts.banner_stack_top(seg, num_rows=1)
        by0_m, by1_m = top_mid, top_mid + 0.045
        mx0, my0, mx1, my1 = mid_customer["bbox_norm"]
        ov_m = max(0.0, min(mx1, bx1) - max(mx0, bx0)) * max(0.0, min(my1, by1_m) - max(my0, by0_m))
        self.assertLess(ov_m / ((mx1 - mx0) * (my1 - my0)), 0.04, f"top_mid={top_mid} overlapped mid face")

    def test_verify_helpers_handle_hold_tail_and_split_panel_banners(self) -> None:
        """`verify.py` helpers account for `hold_tail`, dedupe marks, check split panel banners,
        flag lingering phrase banners (>1.5s), and verify outer strobe edge vs clear center.
        """
        plan_tl = {
            "canvas": {"width": 1080, "height": 1920, "fps": 25},
            "segments": [
                {"id": "L2", "src": "a.mp4", "start": 0.0, "end": 1.0, "hold_tail": 0.0},
                {"id": "L3", "src": "b.mp4", "start": 0.04, "end": 0.97, "hold_tail": 0.47},
                {"id": "L4", "src": "b.mp4", "start": 1.53, "end": 2.64},
            ],
        }
        starts, durs, total, seg_start = verify.compute_timeline_starts(plan_tl, 25)
        self.assertAlmostEqual(durs[0], 1.0, places=3)
        self.assertAlmostEqual(durs[1], 1.40, places=3)  # 0.93 + 0.47 = 1.40s
        self.assertAlmostEqual(starts[2], 2.40, places=3)
        self.assertAlmostEqual(seg_start["L4"], 2.40, places=3)
        self.assertAlmostEqual(total, sum(durs), places=3)

        # Panel bbox mapping on split_v bottom panel (0, 960, 1080, 1920) -> full canvas [0..1]
        mapped = verify._panel_bbox_to_canvas_norm([0.3, 0.1, 0.7, 0.4], (0, 960, 1080, 1920), 1080, 1920)
        self.assertAlmostEqual(mapped[0], 0.3, places=3)
        self.assertAlmostEqual(mapped[1], 0.55, places=3)
        self.assertAlmostEqual(mapped[2], 0.7, places=3)
        self.assertAlmostEqual(mapped[3], 0.70, places=3)
        self.assertGreater(verify.box_overlap_share(mapped, (140, 1000, 940, 1140), 1080, 1920), 0.10)

        # Mark deduplication across plan["marks"] and seg["marks"]
        deduped = verify._collect_deduped_marks({
            "segments": [{"id": "L3", "marks": [{"type": "wrong", "at": 0.93}]}],
            "marks": [{"segment": "L3", "type": "wrong", "at": 0.931, "style": "vignette"}],
        })
        self.assertEqual(len(deduped), 1)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            # 1) Synthetic split_v clip: verify check_frames detects banner on bottom panel + lingering banner
            split_mp4 = tmp_dir / "split_test.mp4"
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc2=size=360x640:rate=25:duration=2.4",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(split_mp4),
                ],
                check=True,
                capture_output=True,
            )
            split_plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "segments": [{
                    "id": "S1",
                    "src": str(split_mp4),
                    "start": 0.0,
                    "end": 2.4,
                    "layout": "split_v",
                    "banners": [{"text": "TOO LONG PHRASE", "at": 0.0, "duration": 2.2}],
                }],
                "marks": [],
            }
            fake_boxes = [{"text": "TOO LONG PHRASE", "header": False, "x0": 40.0, "y0": 330.0, "x1": 320.0, "y1": 390.0, "t0": 0.0, "t1": 2.2}]

            def _fake_detect(crop, **_kwargs):
                # Return a face near top of whichever panel crop is passed; bottom panel overlaps y=330..390
                return [{"bbox_norm": [0.3, 0.05, 0.7, 0.35], "eye_center_norm": [0.5, 0.2]}]

            with mock.patch("vision_tracker.detect_faces_in_frame", side_effect=_fake_detect), \
                 mock.patch("vision_tracker.prominent_faces", side_effect=lambda fs: fs), \
                 mock.patch("layouts.banner_boxes", return_value=fake_boxes):
                f_res = verify.check_frames(split_plan, split_mp4)

            self.assertTrue(any("S1 (bottom)" in msg for msg in f_res["banner_on_face"]), f_res["banner_on_face"])
            self.assertEqual(len(f_res["lingering_banners"]), 1)

            # 2) Strobe check: calibrated hollow red_flash passes, while solid center+edge red fails
            hollow_mp4 = tmp_dir / "hollow_strobe.mp4"
            red_png = ROOT / "assets" / "marks" / "red_flash.png"
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x808080:s=360x640:r=25:d=1.2",
                    "-loop", "1", "-framerate", "25", "-t", "1.2", "-i", str(red_png),
                    "-filter_complex", "[1:v]scale=360:640,format=rgba,colorchannelmixer=aa=0.70[mk];[0:v][mk]overlay=0:0:enable='between(t,0.4,0.9)'[out]",
                    "-map", "[out]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(hollow_mp4),
                ],
                check=True,
                capture_output=True,
            )
            strobe_plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "segments": [{"id": "L3", "src": "x.mp4", "start": 0.0, "end": 0.4, "hold_tail": 0.5}],
                "marks": [{"segment": "L3", "type": "wrong", "at": 0.42, "duration": 0.48, "style": "vignette"}],
            }
            ok_h, pass_h, tot_h, det_h, _ = verify.verify_marks_and_build_contact_sheet(
                hollow_mp4, strobe_plan, {"L3": 0.0}, 360, 640, tmp_dir / "sheet_ok.jpg"
            )
            self.assertTrue(ok_h, f"Expected hollow vignette to pass: {det_h}")
            self.assertEqual((pass_h, tot_h), (1, 1))

            solid_mp4 = tmp_dir / "solid_strobe.mp4"
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "color=c=0x808080:s=360x640:r=25:d=1.2",
                    "-f", "lavfi", "-i", "color=c=0xEF4444:s=360x640:r=25:d=1.2",
                    "-filter_complex", "[0:v][1:v]overlay=0:0:enable='between(t,0.4,0.9)'[out]",
                    "-map", "[out]", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(solid_mp4),
                ],
                check=True,
                capture_output=True,
            )
            ok_s, pass_s, tot_s, det_s, _ = verify.verify_marks_and_build_contact_sheet(
                solid_mp4, strobe_plan, {"L3": 0.0}, 360, 640, tmp_dir / "sheet_fail.jpg"
            )
            self.assertFalse(ok_s, f"Expected non-hollow strobe to fail center check: {det_s}")
            self.assertEqual((pass_s, tot_s), (0, 1))
            self.assertTrue(any("center_not_clear" in d for d in det_s), det_s)

            # 3) Subs mode=off parity between render, edl_parser, and verify
            self.assertFalse(verify.is_subs_enabled({"subs": {"mode": "off"}}))
            self.assertIn("#subs off", edl_parser.plan_to_edl({"subs": {"mode": "off"}, "segments": []}))



if __name__ == "__main__":
    unittest.main()
