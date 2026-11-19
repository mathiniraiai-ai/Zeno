#!/usr/bin/env python3
"""Unit and regression tests for post-word vignette/slash beats, banner sizing/placement,
hold_tail roundtripping, format/budget invariants, and Deep QC `verify.py` checks.
"""
from __future__ import annotations

import array
import json
import math
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

import cv2
import numpy as np
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
import premiere_export  # noqa: E402
import script_order  # noqa: E402
import style_config  # noqa: E402
import ui_generator  # noqa: E402
import verify  # noqa: E402


def _write_pcm_wav(
    path: Path,
    duration_sec: float,
    *,
    sample_rate: int = 16000,
    tone_windows: list[tuple[float, float, float]] | None = None,
    freq_hz: float = 440.0,
) -> Path:
    """Write a deterministic 16-bit mono PCM WAV with optional `(t0, t1, dbFS)` sine tone bursts."""
    n_samples = int(round(duration_sec * sample_rate))
    buf = array.array("h", [0] * n_samples)
    for t0, t1, db in (tone_windows or []):
        amp = int(round(32767.0 * (10.0 ** (db / 20.0))))
        i0 = max(0, int(round(t0 * sample_rate)))
        i1 = min(n_samples, int(round(t1 * sample_rate)))
        for idx in range(i0, i1):
            val = int(round(amp * math.sin(2.0 * math.pi * freq_hz * (idx / float(sample_rate)))))
            buf[idx] = max(-32767, min(32767, val))
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(buf.tobytes())
    return path


def _make_synthetic_av_clip(
    out_path: Path,
    *,
    duration: float,
    width: int = 360,
    height: int = 640,
    fps: int = 25,
    source_filter: str = "testsrc2",
    vf_extra: str = "",
    tone_windows: list[tuple[float, float, float]] | None = None,
    include_audio: bool = True,
) -> Path:
    """Generate a fast synthetic moving MP4 with optional bursty audio."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src_expr = f"{source_filter}=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    cmd: list[str] = [
        str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", src_expr,
    ]
    wav_tmp: Path | None = None
    if include_audio:
        wav_tmp = out_path.with_suffix(".mux.wav")
        _write_pcm_wav(wav_tmp, duration, sample_rate=48000, tone_windows=tone_windows)
        cmd.extend(["-i", str(wav_tmp)])
    if vf_extra:
        cmd.extend(["-vf", vf_extra])
    cmd.extend(["-t", f"{duration:.3f}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"])
    if include_audio:
        cmd.extend(["-c:a", "aac", "-b:a", "128k"])
    else:
        cmd.append("-an")
    cmd.append(str(out_path))
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    finally:
        if wav_tmp is not None and wav_tmp.exists():
            wav_tmp.unlink()
    return out_path


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

    def test_wrong_detection_scene_strike_telugu_and_split_multisegment_beats(self) -> None:
        """Scene strike directions, 'Never, ever say', 'No!', and Telugu correction cues mark wrong lines
        while preserving '?' student questions; multi-sentence wrong lines attach marks to every sub-cut.
        """
        raw_script = "\n".join([
            "Characters",
            "* Rahul -- candidate",
            "* Interviewer -- HR manager",
            "* Female Robot -- AI teacher",
            "Scene 1",
            "Rahul: Sir, Myself Rahul",
            "Interviewer: Never, ever say Myself Rahul",
            "Scene 2 -- Flashback",
            "Flashback. Each line gets struck out on screen with a big red X.",
            "Rahul: Myself Rahul. I'm Graduated. I'm hardworking.",
            "Scene 3",
            "Rahul: Myself Rahul. I am from Hyderabad, but working at Bangalore.",
            'Female Robot: No! ఎప్పుడూ "myself" తో మొదలుపెట్టకూడదు. Say: "I\'m Rahul."',
            "Rahul: I am having three years experience in Fullstack.",
            'Female Robot: మీరు ఇలా చెప్పాలి: "I have three years of experience in fullstack development."',
            "Rahul: ఇది English లో ఎలా చెప్పాలి??",
            'Female Robot: Say: "I\'m looking for a role where I can grow with the company."',
        ])
        _, parsed = prepare._parse_script_raw(raw_script, "w47_wrong_marks")
        by_txt = {ln["text"]: ln for ln in parsed}
        self.assertEqual(by_txt["Sir, Myself Rahul"].get("mark"), "wrong")
        self.assertEqual(by_txt["Myself Rahul. I'm Graduated. I'm hardworking."].get("mark"), "wrong")
        self.assertEqual(by_txt["Myself Rahul. I am from Hyderabad, but working at Bangalore."].get("mark"), "wrong")
        self.assertEqual(by_txt["I am having three years experience in Fullstack."].get("mark"), "wrong")
        self.assertIsNone(by_txt["ఇది English లో ఎలా చెప్పాలి??"].get("mark"))
        self.assertIsNone(by_txt["Never, ever say Myself Rahul"].get("mark"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip_dir = tmp_dir / "clip_2"
            clip_dir.mkdir()
            (clip_dir / "hq_words.json").write_text(
                json.dumps([
                    {"w": "Myself", "start": 0.25, "end": 0.68},
                    {"w": "Rahul.", "start": 0.69, "end": 1.15},
                    {"w": "I'm", "start": 1.79, "end": 2.02},
                    {"w": "Graduated.", "start": 2.03, "end": 2.63},
                    {"w": "I'm", "start": 3.20, "end": 3.45},
                    {"w": "hardworking.", "start": 3.46, "end": 4.18},
                ]),
                encoding="utf-8",
            )
            scr_file = tmp_dir / "script.txt"
            scr_file.write_text(raw_script, encoding="utf-8")
            multi_line = [ln for ln in parsed if ln["text"].startswith("Myself Rahul. I'm Graduated")][0]
            lid = multi_line["id"]
            plan = {
                "mark_pack": "vignette",
                "segments": [
                    {"id": f"{lid}a", "src": str(tmp_dir / "2.mp4"), "start": 0.25, "end": 1.17, "layout": "single", "zoom": 1.0},
                    {"id": f"{lid}b", "src": str(tmp_dir / "2.mp4"), "start": 1.79, "end": 2.65, "layout": "single", "zoom": 1.0},
                    {"id": f"{lid}c", "src": str(tmp_dir / "2.mp4"), "start": 3.20, "end": 4.21, "layout": "single", "zoom": 1.0},
                ],
                "marks": [],
            }
            with mock.patch("common.clip_work_dir", return_value=clip_dir):
                script_order.calibrate_post_word_beats(plan, scr_file, tmp_dir)
            marked_segs = [m["segment"] for m in plan["marks"] if m.get("type") == "wrong"]
            self.assertEqual(marked_segs, [f"{lid}a", f"{lid}b", f"{lid}c"])

    def test_edl_parser_hold_src_and_audio_end_roundtrip(self) -> None:
        """`hold_src="Silent Clip.mp4"@0.10-0.55` and `audio_end=0.80` parse and format losslessly."""
        edl = (
            '#src_dir "/tmp/raw"\n'
            'L3 ordering.mp4 0.04-0.97 z=1 hold=0.45 hold_src="Silent Clip.mp4"@0.10-0.55 '
            'audio_end=0.80 sp=Lady banner:"A"\n'
        )
        parsed = edl_parser.parse_edl(edl)
        seg = parsed["segments"][0]
        self.assertEqual(seg.get("hold_src"), "/tmp/raw/Silent Clip.mp4")
        self.assertEqual(seg.get("hold_span"), [0.1, 0.55])
        self.assertAlmostEqual(float(seg.get("audio_end", 0.0)), 0.80, places=3)
        self.assertAlmostEqual(float(seg.get("hold_tail", 0.0)), 0.45, places=3)

        formatted = edl_parser.format_edl(parsed)
        self.assertIn('hold_src="Silent Clip.mp4"@0.1-0.55', formatted)
        self.assertIn("audio_end=0.8", formatted)
        self.assertIn("hold=0.45", formatted)

        reparsed = edl_parser.parse_edl(formatted)
        seg_rt = reparsed["segments"][0]
        self.assertEqual(seg_rt.get("hold_src"), "/tmp/raw/Silent Clip.mp4")
        self.assertEqual(seg_rt.get("hold_span"), [0.1, 0.55])
        self.assertAlmostEqual(float(seg_rt.get("audio_end", 0.0)), 0.80, places=3)
        self.assertAlmostEqual(float(seg_rt.get("hold_tail", 0.0)), 0.45, places=3)

    def test_priority1_live_tail_extension_ghost_immunity_and_audio_gate(self) -> None:
        """Priority 1 extends `seg["end"]` into live silent reaction frames (`>= 1.25s`),
        sets `audio_end` near `0.77s`, keeps `hold_tail == 0.0` (no `hold_src` / no freeze),
        ignores silent-tail ghost `"I'm"` in `seg_tokens` and `check_script_coverage`,
        and renders moving pixels in `[0.85..1.25s]` with silent audio after `audio_end`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            work_dir = tmp_dir / "work"
            clip_cache_root = work_dir / "_clips"
            proj_dir = work_dir / "proj_p1"
            raw_dir = tmp_dir / "raw"
            work_dir.mkdir(parents=True)
            clip_cache_root.mkdir(parents=True)
            proj_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True)

            prev_common_work = common.WORK
            prev_common_clip_cache = common.CLIP_CACHE
            prev_verify_work = verify.WORK
            prev_so_wd = script_order._ACTIVE_WORK_DIR
            has_so_work_dir = hasattr(script_order, "WORK_DIR")
            prev_so_work_dir = getattr(script_order, "WORK_DIR", None)
            try:
                common.WORK = work_dir
                common.CLIP_CACHE = clip_cache_root
                verify.WORK = work_dir
                script_order._ACTIVE_WORK_DIR = proj_dir
                if has_so_work_dir:
                    script_order.WORK_DIR = work_dir

                src_mp4 = _make_synthetic_av_clip(
                    raw_dir / "alice_p1.mp4",
                    duration=2.2,
                    width=360,
                    height=640,
                    fps=25,
                    source_filter="testsrc2",
                    tone_windows=[(0.0, 0.78, -14.0)],
                    include_audio=True,
                )
                cdir = common.clip_work_dir(src_mp4)
                cdir.mkdir(parents=True, exist_ok=True)
                (cdir / "hq_words.json").write_text(
                    json.dumps([
                        {"w": "Wrong", "start": 0.05, "end": 0.72},
                        {"w": "I'm", "start": 1.10, "end": 2.00},
                    ]),
                    encoding="utf-8",
                )
                (cdir / "speech_spans.json").write_text(
                    json.dumps({"spans": [[0.0, 0.78]]}),
                    encoding="utf-8",
                )
                _write_pcm_wav(
                    cdir / "audio16k.wav",
                    2.2,
                    sample_rate=16000,
                    tone_windows=[(0.0, 0.78, -14.0)],
                )

                script_path = proj_dir / "script.txt"
                script_path.write_text(
                    "L1 Alice: Wrong (X Sign and Text Strikethrough)\n",
                    encoding="utf-8",
                )

                plan = {
                    "_plan_dir": str(proj_dir),
                    "canvas": {"width": 360, "height": 640, "fps": 25},
                    "segments": [
                        {
                            "id": "L1",
                            "src": str(src_mp4),
                            "start": 0.0,
                            "end": 0.78,
                            "layout": "single",
                            "zoom": 1.0,
                            "speaker": "Alice",
                        }
                    ],
                    "marks": [
                        {
                            "type": "wrong",
                            "segment": "L1",
                            "at": 0.72,
                            "duration": 0.56,
                            "style": "vignette",
                        }
                    ],
                }

                changed = script_order.calibrate_post_word_beats(plan, script_path, proj_dir)
                self.assertGreater(changed, 0)

                seg = plan["segments"][0]
                self.assertGreaterEqual(float(seg["end"]), 1.25, f"Expected live extension >= 1.25s: {seg}")
                self.assertIsNotNone(seg.get("audio_end"))
                self.assertAlmostEqual(float(seg["audio_end"]), 0.77, delta=0.04)
                self.assertAlmostEqual(float(seg.get("hold_tail", 0.0)), 0.0, places=3)
                self.assertIsNone(seg.get("hold_src"))

                # Ghost "I'm" is filtered from seg_tokens (with and without audio_end) and check_script_coverage
                self.assertEqual(script_order.seg_tokens(seg), ["wrong"])
                probe_no_ae = {k: v for k, v in seg.items() if k != "audio_end"}
                probe_no_ae["end"] = 2.05
                self.assertEqual(script_order.seg_tokens(probe_no_ae), ["wrong"])

                ok_cov, cov_lines, tot_lines, b_dups = verify.check_script_coverage(
                    plan, script_path=script_path
                )
                self.assertTrue(ok_cov)
                self.assertEqual((cov_lines, tot_lines, b_dups), (1, 1, 0))

                # Ghost-only cut (even spanning [1.05..2.05]) must NOT satisfy coverage for a line containing "I'm"
                ghost_script = proj_dir / "ghost_script.txt"
                ghost_script.write_text("L1 Alice: I'm\n", encoding="utf-8")
                ghost_plan = {
                    "_plan_dir": str(proj_dir),
                    "segments": [{"id": "S1", "src": str(src_mp4), "start": 1.05, "end": 2.05}],
                }
                ok_ghost, cov_g, tot_g, _ = verify.check_script_coverage(ghost_plan, script_path=ghost_script)
                self.assertFalse(ok_ghost, "Silent-tail ghost word must not satisfy check_script_coverage")
                self.assertEqual((cov_g, tot_g), (0, 1))

                # Combined script (Wrong + I'm) with unlabelled segment covering [0..2.05] only proves Wrong, never I'm
                both_script = proj_dir / "both_script.txt"
                both_script.write_text("L1 Alice: Wrong\nL2 Alice: I'm\n", encoding="utf-8")
                both_plan = {
                    "_plan_dir": str(proj_dir),
                    "segments": [{"id": "S1", "src": str(src_mp4), "start": 0.0, "end": 2.05}],
                }
                ok_both, cov_b, tot_b, _ = verify.check_script_coverage(both_plan, script_path=both_script)
                self.assertFalse(ok_both)
                self.assertEqual((cov_b, tot_b), (1, 2))

                # Render via layouts.build_segment_ffmpeg_args and verify live motion + audio gate
                out_mkv = proj_dir / "p1_rendered.mkv"
                args, exact, n_frames = layouts.build_segment_ffmpeg_args(
                    seg, plan, 360, 640, 25, out_mkv, draft=True
                )
                self.assertGreater(n_frames, 25)
                subprocess.run(
                    [str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *[str(x) for x in args]],
                    check=True,
                    capture_output=True,
                )
                self.assertTrue(out_mkv.is_file())

                with mock.patch(
                    "vision_tracker.detect_faces_in_frame",
                    return_value=[{"bbox_norm": [0.3, 0.2, 0.7, 0.5], "eye_center_norm": [0.5, 0.35]}],
                ), mock.patch("vision_tracker.prominent_faces", side_effect=lambda fs: fs):
                    f_res = verify.check_frames(plan, out_mkv)
                self.assertEqual(f_res["frozen"], [], f"Unexpected frozen frames: {f_res}")

                cap = cv2.VideoCapture(str(out_mkv))
                tail_frames: list[np.ndarray] = []
                for ts in (0.85, 0.95, 1.05, 1.15, 1.24):
                    cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
                    ok_f, fr = cap.read()
                    self.assertTrue(ok_f and fr is not None, f"Failed reading tail frame at {ts}s")
                    tail_frames.append(fr)
                cap.release()
                tail_diffs = [
                    float(np.abs(tail_frames[i + 1].astype(np.float32) - tail_frames[i].astype(np.float32)).mean())
                    for i in range(len(tail_frames) - 1)
                ]
                self.assertTrue(
                    all(d > 1.0 for d in tail_diffs),
                    f"Expected live moving pixels across [0.85..1.25s], got diffs={tail_diffs}",
                )

                out_wav = proj_dir / "p1_audio.wav"
                subprocess.run(
                    [
                        str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(out_mkv), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        str(out_wav),
                    ],
                    check=True,
                    capture_output=True,
                )
                with wave.open(str(out_wav), "rb") as wf:
                    sr = wf.getframerate()
                    pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float64) / 32768.0
                spoken_slice = pcm[int(0.10 * sr) : int(0.65 * sr)]
                tail_slice = pcm[int((float(seg["audio_end"]) + 0.06) * sr) : int((exact - 0.02) * sr)]
                self.assertGreater(len(tail_slice), 200)
                spoken_db = 20.0 * math.log10(max(float(np.sqrt(np.mean(spoken_slice * spoken_slice))), 1e-10))
                tail_db = 20.0 * math.log10(max(float(np.sqrt(np.mean(tail_slice * tail_slice))), 1e-10))
                self.assertGreater(spoken_db, -30.0, f"Spoken portion too quiet: {spoken_db:.1f} dBFS")
                self.assertLess(tail_db, -55.0, f"Audio not gated after audio_end: {tail_db:.1f} dBFS")
            finally:
                common.WORK = prev_common_work
                common.CLIP_CACHE = prev_common_clip_cache
                verify.WORK = prev_verify_work
                script_order._ACTIVE_WORK_DIR = prev_so_wd
                if has_so_work_dir:
                    script_order.WORK_DIR = prev_so_work_dir

    def test_priority2_live_hold_src_split_v_and_premiere_xml(self) -> None:
        """Priority 2 assigns `hold_src` + advancing `hold_span` across two back-to-back segments
        when next speech blocks Priority 1; `split_v` renders live moving pixels in both top and
        bottom panels during the hold tail (`frozen == []`); and `premiere_export` emits `0`
        `hold_*.png` files with `Alice_silent.mp4` on `V1` and continuous `Peer.mp4` on `V2`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            work_dir = tmp_dir / "work"
            clip_cache_root = work_dir / "_clips"
            proj_dir = work_dir / "proj_p2"
            raw_dir = tmp_dir / "raw"
            out_pr_dir = tmp_dir / "premiere_out"
            work_dir.mkdir(parents=True)
            clip_cache_root.mkdir(parents=True)
            proj_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True)
            out_pr_dir.mkdir(parents=True)

            prev_common_work = common.WORK
            prev_common_clip_cache = common.CLIP_CACHE
            prev_verify_work = verify.WORK
            prev_so_wd = script_order._ACTIVE_WORK_DIR
            has_so_work_dir = hasattr(script_order, "WORK_DIR")
            prev_so_work_dir = getattr(script_order, "WORK_DIR", None)
            try:
                common.WORK = work_dir
                common.CLIP_CACHE = clip_cache_root
                verify.WORK = work_dir
                script_order._ACTIVE_WORK_DIR = proj_dir
                if has_so_work_dir:
                    script_order.WORK_DIR = work_dir

                # Spoken clip with 3 contiguous speech windows so both L1 and L2 are blocked from Priority 1
                alice_speaking = _make_synthetic_av_clip(
                    raw_dir / "Alice_speaking.mp4",
                    duration=2.1,
                    width=360,
                    height=640,
                    fps=25,
                    source_filter="testsrc2",
                    tone_windows=[(0.04, 0.60, -14.0), (0.65, 1.22, -14.0), (1.27, 1.85, -14.0)],
                    include_audio=True,
                )
                alice_silent = _make_synthetic_av_clip(
                    raw_dir / "Alice_silent.mp4",
                    duration=1.6,
                    width=360,
                    height=640,
                    fps=25,
                    source_filter="testsrc2",
                    vf_extra="eq=brightness=0.02",
                    include_audio=False,
                )
                peer_mp4 = _make_synthetic_av_clip(
                    raw_dir / "Peer.mp4",
                    duration=2.5,
                    width=360,
                    height=640,
                    fps=25,
                    source_filter="testsrc2",
                    vf_extra="hflip,hue=h=120",
                    include_audio=False,
                )

                cdir = common.clip_work_dir(alice_speaking)
                cdir.mkdir(parents=True, exist_ok=True)
                (cdir / "hq_words.json").write_text(
                    json.dumps([
                        {"w": "Wrong", "start": 0.06, "end": 0.58},
                        {"w": "Never", "start": 0.68, "end": 1.20},
                        {"w": "Stop", "start": 1.30, "end": 1.80},
                    ]),
                    encoding="utf-8",
                )
                (cdir / "speech_spans.json").write_text(
                    json.dumps({"spans": [[0.04, 0.60], [0.65, 1.22], [1.27, 1.85]]}),
                    encoding="utf-8",
                )
                _write_pcm_wav(
                    cdir / "audio16k.wav",
                    2.1,
                    sample_rate=16000,
                    tone_windows=[(0.04, 0.60, -14.0), (0.65, 1.22, -14.0), (1.27, 1.85, -14.0)],
                )

                script_path = proj_dir / "script.txt"
                script_path.write_text(
                    "L1 Alice: Wrong (X Sign and Text Strikethrough)\n"
                    "L2 Alice: Never (X Sign and Text Strikethrough)\n",
                    encoding="utf-8",
                )

                plan = {
                    "_plan_dir": str(proj_dir),
                    "output": str(proj_dir / "reel_p2.mp4"),
                    "src_dir": str(raw_dir),
                    "canvas": {"width": 360, "height": 640, "fps": 25},
                    "segments": [
                        {
                            "id": "L1",
                            "src": str(alice_speaking),
                            "start": 0.0,
                            "end": 0.60,
                            "layout": "split_v",
                            "zoom": 1.0,
                            "speaker": "Alice",
                            "slot": "top",
                            "peer": {"src": str(peer_mp4), "speaker": "Peer", "slot": "bottom"},
                        },
                        {
                            "id": "L2",
                            "src": str(alice_speaking),
                            "start": 0.65,
                            "end": 1.25,
                            "layout": "split_v",
                            "zoom": 1.0,
                            "speaker": "Alice",
                            "slot": "top",
                            "peer": {"src": str(peer_mp4), "speaker": "Peer", "slot": "bottom"},
                        },
                    ],
                    "marks": [],
                }

                changed = script_order.calibrate_post_word_beats(plan, script_path, proj_dir)
                self.assertGreater(changed, 0)

                s1, s2 = plan["segments"][0], plan["segments"][1]
                for seg in (s1, s2):
                    self.assertTrue(seg.get("hold_src"), f"Expected Priority 2 hold_src on {seg['id']}: {seg}")
                    self.assertEqual(Path(str(seg["hold_src"])).resolve(), alice_silent.resolve())
                    self.assertGreater(float(seg.get("hold_tail", 0.0)), 0.35)
                    h_span = seg.get("hold_span")
                    self.assertIsInstance(h_span, list)
                    self.assertEqual(len(h_span), 2)
                    self.assertGreater(float(h_span[1]) - float(h_span[0]), 0.20)
                self.assertGreaterEqual(
                    float(s2["hold_span"][0]),
                    float(s1["hold_span"][1]) - 1e-6,
                    f"Expected advancing hold_span across L1->L2: {s1['hold_span']} -> {s2['hold_span']}",
                )

                # Render L1 split_v via layouts.build_segment_ffmpeg_args and verify both panels move during hold tail
                out_split_mkv = proj_dir / "p2_split_v.mkv"
                args, exact, _ = layouts.build_segment_ffmpeg_args(
                    s1, plan, 360, 640, 25, out_split_mkv, draft=True
                )
                subprocess.run(
                    [str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y", *[str(x) for x in args]],
                    check=True,
                    capture_output=True,
                )
                self.assertTrue(out_split_mkv.is_file())

                play_dur_s1 = float(s1["end"]) - float(s1["start"])
                with mock.patch(
                    "vision_tracker.detect_faces_in_frame",
                    return_value=[{"bbox_norm": [0.3, 0.2, 0.7, 0.5], "eye_center_norm": [0.5, 0.35]}],
                ), mock.patch("vision_tracker.prominent_faces", side_effect=lambda fs: fs):
                    f_res = verify.check_frames(
                        {"canvas": {"width": 360, "height": 640, "fps": 25}, "segments": [s1], "marks": []},
                        out_split_mkv,
                    )
                self.assertEqual(f_res["frozen"], [], f"Unexpected frozen frames on split_v: {f_res}")

                cap = cv2.VideoCapture(str(out_split_mkv))
                hold_times = [round(play_dur_s1 + dt, 3) for dt in (0.06, 0.16, 0.26, 0.36) if play_dur_s1 + dt < exact - 0.03]
                self.assertGreaterEqual(len(hold_times), 3)
                top_frames: list[np.ndarray] = []
                bot_frames: list[np.ndarray] = []
                for ts in hold_times:
                    cap.set(cv2.CAP_PROP_POS_MSEC, ts * 1000.0)
                    ok_f, fr = cap.read()
                    self.assertTrue(ok_f and fr is not None, f"Failed reading split_v hold frame at {ts}s")
                    top_frames.append(fr[:315, :, :])
                    bot_frames.append(fr[325:, :, :])
                cap.release()
                top_diffs = [
                    float(np.abs(top_frames[i + 1].astype(np.float32) - top_frames[i].astype(np.float32)).mean())
                    for i in range(len(top_frames) - 1)
                ]
                bot_diffs = [
                    float(np.abs(bot_frames[i + 1].astype(np.float32) - bot_frames[i].astype(np.float32)).mean())
                    for i in range(len(bot_frames) - 1)
                ]
                self.assertTrue(all(d > 1.0 for d in top_diffs), f"Top hold panel froze: {top_diffs}")
                self.assertTrue(all(d > 1.0 for d in bot_diffs), f"Bottom peer panel froze: {bot_diffs}")

                # Export Premiere XML & verify 0 hold_*.png + Alice_silent.mp4 on V1 + continuous Peer.mp4 on V2
                plan_edl_path = proj_dir / "plan.edl"
                plan_edl_path.write_text(edl_parser.format_edl(plan), encoding="utf-8")
                pr_res = premiere_export.export_premiere_project(
                    plan,
                    plan_edl_path,
                    out_dir=out_pr_dir,
                    work_dir=proj_dir,
                    fps=25,
                    width=360,
                    height=640,
                )
                xml_path = Path(pr_res["xml"])
                self.assertTrue(xml_path.is_file())
                hold_pngs = list((proj_dir / "overlays").glob("hold_*.png")) if (proj_dir / "overlays").is_dir() else []
                self.assertEqual(hold_pngs, [], f"Expected 0 hold_*.png files, found {hold_pngs}")

                tree = ET.parse(xml_path)
                root = tree.getroot()
                file_info: dict[str, str] = {}
                for f_el in root.findall(".//file"):
                    fid = f_el.get("id")
                    name_el = f_el.find("name")
                    url_el = f_el.find("pathurl")
                    if fid and (name_el is not None or url_el is not None):
                        file_info[fid] = (name_el.text if name_el is not None else "") or (url_el.text or "")

                v_tracks = root.findall("./sequence/media/video/track")
                self.assertGreaterEqual(len(v_tracks), 2, "Expected V1 and V2 tracks in Premiere XML")

                def _track_items(track_el: ET.Element) -> list[dict]:
                    items = []
                    for ci in track_el.findall("clipitem"):
                        f_el = ci.find("file")
                        fid = f_el.get("id") if f_el is not None else ""
                        fname = file_info.get(fid, "")
                        items.append({
                            "name": (ci.findtext("name") or ""),
                            "file": fname,
                            "start": int(ci.findtext("start") or 0),
                            "end": int(ci.findtext("end") or 0),
                        })
                    return items

                v1_items = _track_items(v_tracks[0])
                v2_items = _track_items(v_tracks[1])
                v1_hold_items = [it for it in v1_items if "Alice_silent.mp4" in it["file"]]
                self.assertGreaterEqual(len(v1_hold_items), 2, f"Expected Alice_silent.mp4 hold clips on V1: {v1_items}")
                self.assertFalse(
                    any(it["file"].endswith(".png") and "hold_" in it["file"] for it in v1_items),
                    f"Unexpected PNG still hold on V1: {v1_items}",
                )

                v2_peer_items = [it for it in v2_items if "Peer.mp4" in it["file"]]
                self.assertEqual(len(v2_peer_items), 2, f"Expected 2 Peer.mp4 clips on V2: {v2_items}")
                self.assertEqual(v2_peer_items[0]["start"], 0)
                self.assertEqual(v2_peer_items[0]["end"], v2_peer_items[1]["start"])
                self.assertEqual(v2_peer_items[-1]["end"], v1_items[-1]["end"])
            finally:
                common.WORK = prev_common_work
                common.CLIP_CACHE = prev_common_clip_cache
                verify.WORK = prev_verify_work
                script_order._ACTIVE_WORK_DIR = prev_so_wd
                if has_so_work_dir:
                    script_order.WORK_DIR = prev_so_work_dir


if __name__ == "__main__":
    unittest.main()
