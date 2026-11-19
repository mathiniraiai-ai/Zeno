#!/usr/bin/env python3
"""Comprehensive regression test suite for Editor-Sheet Fixes (Agents 1–7).

Covers:
1. 720p caption double-scaling elimination + Kannada static font registration + wider box padding (`styles.json`).
2. Missing-subtitle per-segment backfill (`L7a` + blank `L7b`), flashcard auto-suppress, `subs=off`/`subs=on`,
   `#page scroll` disabling `#subs`, and `verify.check_subtitles_srt` per-segment coverage gate.
3. Bruno's delayed beep fix: phrase-matched `script_order.calibrate_post_word_beats` (including pulling back
   an overly late beep) and `render._mark_sfx_preroll` attack pre-roll.
4. Bruno's tightened clipped-word gate (`0.07s` margin, `-28.0 dBFS` floor, `words.json` + `clip_caches.json`
   fallback) and Vidhat's mid-sentence transition hard block in `script_order.full_gate`.
5. Madhu's variety checks (`3/4` SFX dominance & `3/3` identical transitions), Balesh's filler/truncated stutters
   (`"I uh I think"`, `"th- the"`), and `verify.check_script_coverage` dict-message extraction.
6. Hitesh's per-panel `offset_y` EDL roundtrip (`offset_y`, `peer@offset_y`, `subs=off`, `subs=on`),
   `vision_tracker.compute_paired_split_crop_filters(..., live_offset_y=0.08, peer_offset_y=0.0)`,
   modal mixed-FPS detection, wider flashcard preset, and `verify.check_split_and_hook_rules` headroom & zoom sync.
7. Hitesh's cut-seam black-flicker scanner (`verify.check_frames` & `verify.check_square_output`) plus smooth
   2x supersampled `zoom_in`/`zoom_out` transitions and 16ms zero-bleed audio splice.
"""
from __future__ import annotations

import array
import json
import math
import subprocess
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import edl_parser  # noqa: E402
import flashcard_placer  # noqa: E402
import layouts  # noqa: E402
import render  # noqa: E402
import script_order  # noqa: E402
import subtitles  # noqa: E402
import verify  # noqa: E402
import vision_tracker  # noqa: E402


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
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(buf.tobytes())
    return path


def _make_synthetic_clip(
    out_path: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps: int = 30,
    duration: float = 1.0,
    source_filter: str = "testsrc2",
) -> Path:
    """Generate a fast synthetic video-only MP4 with FFmpeg."""
    src_expr = f"{source_filter}=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    subprocess.run(
        [
            str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", src_expr,
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        check=True,
        capture_output=True,
    )
    return out_path


class EditorSheetFixesTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 1. 720p Double-Scaling + Kannada Static Font + Wider Box Padding
    # ------------------------------------------------------------------
    def test_01_720p_no_double_scaling_kannada_font_and_wider_box_padding(self) -> None:
        """`caption_font_size` returns 1080w design-space pixels (no double scaling on 720p),
        real Node `render_cues.js` scales Kannada ink cleanly (~66%, NOT ~44%), and box styles
        have `paddingX >= 30`.
        """
        self.assertEqual(subtitles.caption_font_size(720), 54)
        self.assertEqual(subtitles.caption_font_size(1080), 54)

        styles_path = ROOT / "subtitles" / "styles.json"
        styles = json.loads(styles_path.read_text(encoding="utf-8"))
        box_styles = [s for s in styles if int(s.get("paddingX") or 0) > 0]
        self.assertTrue(box_styles, "Expected box styles with paddingX > 0 in subtitles/styles.json")
        for st in box_styles:
            self.assertGreaterEqual(
                int(st["paddingX"]),
                30,
                f"Style {st.get('id')} ({st.get('name')}) paddingX too narrow: {st.get('paddingX')}",
            )

        node_bin = subtitles.node_path()
        render_script = ROOT / "subtitles" / "render_cues.js"
        self.assertTrue(render_script.is_file(), f"Missing {render_script}")

        kn_text = "ಕನ್ನಡದಲ್ಲಿ ಸರಿಯಾಗಿ ಮಾತಾಡೋಣ"
        kn_words = [
            {"text": "ಕನ್ನಡದಲ್ಲಿ", "startTime": 0.0, "endTime": 0.4},
            {"text": "ಸರಿಯಾಗಿ", "startTime": 0.4, "endTime": 0.8},
            {"text": "ಮಾತಾಡೋಣ", "startTime": 0.8, "endTime": 1.2},
        ]
        cue = {"text": kn_text, "startTime": 0.0, "endTime": 1.2, "words": kn_words}

        def _render_ink_height(width: int, height: int, out_dir: Path) -> int:
            out_dir.mkdir(parents=True, exist_ok=True)
            job = {
                "width": width,
                "height": height,
                "fps": 25,
                "styleId": 5,  # White Box (exercises box padding + Kannada glyph rendering)
                "outDir": str(out_dir),
                "positionY": 65,
                "fontSize": subtitles.caption_font_size(width),
                "cues": [cue],
            }
            jf = out_dir / "job.json"
            jf.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run(
                [node_bin, str(render_script), str(jf)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                timeout=60,
            )
            self.assertEqual(proc.returncode, 0, f"render_cues.js failed ({width}x{height}): {proc.stderr}")
            fatal_errs = [
                ln for ln in proc.stderr.splitlines()
                if any(tok in ln for tok in ("Error:", "TypeError:", "ReferenceError:", "SyntaxError:", "Unhandled"))
            ]
            self.assertEqual(fatal_errs, [], f"JS error in stderr on {width}x{height}: {proc.stderr}")
            manifest_line = [ln for ln in proc.stdout.splitlines() if ln.startswith("{")][-1]
            manifest = json.loads(manifest_line)
            frames = manifest.get("frames") or []
            self.assertTrue(frames, f"No frames rendered for {width}x{height}")
            png_path = Path(str(frames[0].get("path") or frames[0].get("file") or ""))
            self.assertTrue(png_path.is_file(), f"Missing rendered PNG: {png_path} (frame={frames[0]})")
            with Image.open(png_path) as im:
                arr = np.asarray(im.convert("RGBA"))
            ys = np.where(arr[:, :, 3] > 20)[0]
            self.assertGreater(len(ys), 0, f"Blank transparent PNG rendered for Kannada at {width}x{height}")
            return int(ys.max() - ys.min() + 1)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            h_720 = _render_ink_height(720, 1280, tmp_dir / "r720")
            h_1080 = _render_ink_height(1080, 1920, tmp_dir / "r1080")
            ratio = h_720 / float(max(1, h_1080))
            self.assertGreaterEqual(h_720, 24, f"720p Kannada ink unexpectedly tiny: {h_720}px")
            self.assertGreaterEqual(
                ratio,
                0.58,
                f"720p Kannada ink looks double-shrunk: h_720={h_720}, h_1080={h_1080}, ratio={ratio:.3f}",
            )
            self.assertLessEqual(
                ratio,
                0.76,
                f"720p Kannada ink ratio out of expected ~0.667 range: h_720={h_720}, h_1080={h_1080}, ratio={ratio:.3f}",
            )

    # ------------------------------------------------------------------
    # 2. Per-Segment Backfill + Flashcard Suppress + subs=off + SRT QC
    # ------------------------------------------------------------------
    def test_02_per_segment_backfill_flashcard_suppress_subs_off_and_srt_coverage_qc(self) -> None:
        """`build_cues` backfills blank `L7b` even when `L7a` is aligned, auto-suppresses subs on
        flashcard segments unless `subs_on=True`, skips `subs_off=True`, and `verify.check_subtitles_srt`
        catches uncaptioned >=1.8s spoken segments while respecting `#page`, muted, flashcard, and `subs_off`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip_cache = tmp_dir / "clip_cache"
            clip_cache.mkdir()
            # L7a (0.0..2.0) has aligned words; L7b (2.0..4.0) has zero aligned words
            hq_words = [
                {"w": "Never", "start": 0.15, "end": 0.48},
                {"w": "say", "start": 0.50, "end": 0.80},
                {"w": "this", "start": 0.82, "end": 1.15},
                {"w": "wrong", "start": 1.20, "end": 1.75},
            ]
            (clip_cache / "hq_words.json").write_text(json.dumps(hq_words), encoding="utf-8")
            (clip_cache / "silences.json").write_text("[]", encoding="utf-8")

            script_lines = [
                {"id": "L7", "text": "Never say this wrong phrase when ordering lunch at restaurants"}
            ]
            segs = [
                {"id": "L7a", "src": "clip.mp4", "start": 0.0, "end": 2.0},
                {"id": "L7b", "src": "clip.mp4", "start": 2.0, "end": 4.0},
            ]
            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                cues = subtitles.build_cues(segs, [0.0, 2.0], script_lines=script_lines, work_dir=tmp_dir)
            seg0_cues = [c for c in cues if c.get("segIndex") == 0]
            seg1_cues = [c for c in cues if c.get("segIndex") == 1]
            self.assertTrue(seg0_cues, f"Expected cues on aligned L7a: {cues}")
            self.assertTrue(seg1_cues, f"Expected per-segment backfill cues on blank L7b: {cues}")
            self.assertGreaterEqual(float(seg1_cues[0]["startTime"]), 1.99)

            # Flashcard auto-suppress vs subs_on=True vs subs_off=True
            fc_seg = {
                "id": "L7a",
                "src": "clip.mp4",
                "start": 0.0,
                "end": 2.0,
                "flashcards": [{"title": "ORDER", "subtitle": "MEANING"}],
            }
            fc_seg_on = {**fc_seg, "subs_on": True}
            off_seg = {"id": "L7a", "src": "clip.mp4", "start": 0.0, "end": 2.0, "subs_off": True}

            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                cues_fc = subtitles.build_cues([fc_seg], [0.0], script_lines=script_lines, work_dir=tmp_dir)
                cues_fc_on = subtitles.build_cues([fc_seg_on], [0.0], script_lines=script_lines, work_dir=tmp_dir)
                cues_off = subtitles.build_cues([off_seg], [0.0], script_lines=script_lines, work_dir=tmp_dir)
            self.assertEqual(cues_fc, [], f"Active flashcard should suppress bottom #subs: {cues_fc}")
            self.assertTrue(cues_fc_on, "subs_on=True should opt flashcard segment back into #subs")
            self.assertEqual(cues_off, [], f"subs_off=True must skip segment: {cues_off}")

            # verify.is_subs_enabled disables #subs when #page scroll is active
            self.assertFalse(verify.is_subs_enabled({"subs": "hormozi", "page": {"mode": "scroll"}}))
            self.assertTrue(verify.is_subs_enabled({"subs": "hormozi"}))

            # verify.check_subtitles_srt per-segment coverage gate
            mp4_dummy = tmp_dir / "reel_test.mp4"
            srt_path = tmp_dir / "reel_test.srt"
            plan_two = {
                "subs": "hormozi",
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [
                    {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.5},
                    {"id": "L2", "src": "a.mp4", "start": 2.5, "end": 5.0},
                ],
            }
            srt_missing_l2 = (
                "1\n"
                "00:00:00,100 --> 00:00:02,350\n"
                "First segment spoken\n\n"
            )
            srt_path.write_text(srt_missing_l2, encoding="utf-8")
            ok_miss, msg_miss = verify.check_subtitles_srt(plan_two, mp4_dummy)
            self.assertFalse(ok_miss, f"Expected SUBS FAIL when spoken 2.5s L2 has 0 cues: {msg_miss}")
            self.assertIn("L2", msg_miss)

            srt_both = (
                srt_missing_l2
                + "2\n"
                + "00:00:02,650 --> 00:00:04,750\n"
                + "Second segment spoken\n\n"
            )
            srt_path.write_text(srt_both, encoding="utf-8")
            ok_cov, msg_cov = verify.check_subtitles_srt(plan_two, mp4_dummy)
            self.assertTrue(ok_cov, f"Expected SUBS PASS when both segments covered: {msg_cov}")

            # Muted, flashcard-suppressed, and subs_off exemptions on L2
            srt_path.write_text(srt_missing_l2, encoding="utf-8")
            for extra in (
                {"mute": True},
                {"flashcards": [{"title": "WORD", "subtitle": "DEF"}]},
                {"subs_off": True},
            ):
                plan_ex = {
                    "subs": "hormozi",
                    "canvas": {"width": 1080, "height": 1920, "fps": 30},
                    "segments": [
                        {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.5},
                        {"id": "L2", "src": "a.mp4", "start": 2.5, "end": 5.0, **extra},
                    ],
                }
                ok_ex, msg_ex = verify.check_subtitles_srt(plan_ex, mp4_dummy)
                self.assertTrue(ok_ex, f"Expected SUBS PASS for exempt L2 ({extra}): {msg_ex}")

            # All segments suppressed passes gracefully even if SRT is missing
            srt_path.unlink()
            plan_all_suppressed = {
                "subs": "hormozi",
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [
                    {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.5, "flashcards": [{"title": "A"}]},
                    {"id": "L2", "src": "a.mp4", "start": 2.5, "end": 5.0, "mute": True},
                ],
            }
            ok_all, msg_all = verify.check_subtitles_srt(plan_all_suppressed, mp4_dummy)
            self.assertTrue(ok_all, f"All-suppressed plan should pass gracefully: {msg_all}")

    # ------------------------------------------------------------------
    # 3. Bruno's Delayed Beep Fix (Phrase-Matched Calibration + Pre-Roll)
    # ------------------------------------------------------------------
    def test_03_bruno_delayed_beep_phrase_match_and_peak_preroll(self) -> None:
        """Multi-phrase cut keeps `mark:wrong` at the wrong phrase end (`~1.40s`, NOT `4.30s`),
        pulls an overly late `mark:wrong@3.80` back to `1.40s`, and computes positive attack pre-roll.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip_cache = tmp_dir / "clip_cache"
            clip_cache.mkdir()
            words = [
                {"w": "I", "start": 0.10, "end": 0.25},
                {"w": "take", "start": 0.26, "end": 0.55},
                {"w": "the", "start": 0.56, "end": 0.75},
                {"w": "chicken?", "start": 0.76, "end": 1.40},
                {"w": "Instead", "start": 1.60, "end": 2.10},
                {"w": "say", "start": 2.15, "end": 2.50},
                {"w": "I'll", "start": 2.55, "end": 2.90},
                {"w": "have", "start": 2.95, "end": 3.40},
                {"w": "the", "start": 3.45, "end": 3.70},
                {"w": "chicken", "start": 3.75, "end": 4.30},
            ]
            (clip_cache / "hq_words.json").write_text(json.dumps(words), encoding="utf-8")

            script_path = tmp_dir / "script.txt"
            script_path.write_text(
                "L3 Lady: I take the chicken? (X Sign and Text Strikethrough)\n",
                encoding="utf-8",
            )

            # Case A: Auto-placed / early mark anchors to matched phrase end ~1.40s (NOT 4.30s!)
            plan_a = {
                "segments": [
                    {
                        "id": "L3",
                        "src": str(tmp_dir / "multi_phrase.mp4"),
                        "start": 0.0,
                        "end": 4.5,
                        "layout": "single",
                        "zoom": 1.0,
                        "banners": [{"text": "I take the chicken?", "at": None}],
                    }
                ],
                "marks": [{"type": "wrong", "segment": "L3", "at": 1.40, "duration": 0.48}],
            }
            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                script_order.calibrate_post_word_beats(plan_a, script_path, tmp_dir)
            m_a = plan_a["marks"][0]
            self.assertAlmostEqual(float(m_a["at"]), 1.40, delta=0.08, msg=f"mark drifted to tail: {m_a}")
            self.assertAlmostEqual(float(plan_a["segments"][0]["banners"][0]["at"]), 1.40, delta=0.08)

            # Case B: Overly late mark:wrong@3.80 is pulled BACK to 1.40s
            plan_b = {
                "segments": [
                    {
                        "id": "L3",
                        "src": str(tmp_dir / "multi_phrase.mp4"),
                        "start": 0.0,
                        "end": 4.5,
                        "layout": "single",
                        "zoom": 1.0,
                        "banners": [{"text": "I take the chicken?", "at": 3.80}],
                    }
                ],
                "marks": [{"type": "wrong", "segment": "L3", "at": 3.80, "duration": 0.48}],
            }
            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                changed_b = script_order.calibrate_post_word_beats(plan_b, script_path, tmp_dir)
            self.assertGreater(changed_b, 0)
            m_b = plan_b["marks"][0]
            self.assertAlmostEqual(
                float(m_b["at"]),
                1.40,
                delta=0.08,
                msg=f"Late mark@3.80 was not pulled back to phrase end 1.40s: {m_b}",
            )
            self.assertAlmostEqual(float(plan_b["segments"][0]["banners"][0]["at"]), 1.40, delta=0.08)

            # Peak pre-roll: delayed-attack beep vs instant-attack click
            late_beep = _write_pcm_wav(
                tmp_dir / "late_beep.wav",
                0.45,
                sample_rate=48000,
                tone_windows=[(0.16, 0.30, -2.0)],
            )
            instant_beep = _write_pcm_wav(
                tmp_dir / "instant_beep.wav",
                0.25,
                sample_rate=48000,
                tone_windows=[(0.0, 0.05, -2.0)],
            )
            pr_late = render._mark_sfx_preroll(late_beep)
            pr_inst = render._mark_sfx_preroll(instant_beep)
            self.assertGreater(pr_late, 0.04, f"Expected positive pre-roll for delayed-attack beep: {pr_late}")
            self.assertLessEqual(pr_late, 0.12)
            self.assertEqual(pr_inst, 0.0, f"Instant-attack beep should have 0 pre-roll: {pr_inst}")

    # ------------------------------------------------------------------
    # 4. Bruno's Clipped-Word Gate + Vidhat's Mid-Sentence Hard Block
    # ------------------------------------------------------------------
    def test_04_bruno_clipped_word_gate_and_vidhat_mid_sentence_hard_block(self) -> None:
        """`verify.check_cuts_and_pauses` catches a mid-word slice on a 0.24s word (`>0.07s` inside,
        `-22 dBFS > -28 dBFS`) via `words.json` + `clip_caches.json` fallback, and
        `script_order.full_gate` hard-blocks `L4a -> L4b` with `tr=whip_left`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            proj_dir = tmp_dir / "proj"
            proj_dir.mkdir()
            cache_dir = tmp_dir / "mapped_clip_cache"
            cache_dir.mkdir()

            # Write ONLY words.json (no hq_words.json) + clip_caches.json mapping to verify both fallbacks
            words = [
                {"w": "please", "start": 0.40, "end": 0.90},
                {"w": "take", "start": 1.00, "end": 1.24},  # 0.24s word -> old 0.18s margin exempted it!
                {"w": "this", "start": 1.35, "end": 1.70},
            ]
            (cache_dir / "words.json").write_text(json.dumps(words), encoding="utf-8")
            (cache_dir / "silences.json").write_text("[]", encoding="utf-8")
            # Active speech at -22 dBFS (above -28 dBFS floor, below old -18 dBFS floor!)
            _write_pcm_wav(
                cache_dir / "audio16k.wav",
                2.0,
                sample_rate=16000,
                tone_windows=[(0.35, 1.75, -22.0)],
            )
            (proj_dir / "clip_caches.json").write_text(
                json.dumps({"bruno_clip.mp4": str(cache_dir)}),
                encoding="utf-8",
            )

            bad_plan = {
                "_plan_dir": str(proj_dir),
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [
                    {"id": "L1", "src": "bruno_clip.mp4", "start": 0.35, "end": 1.12},
                ],
            }
            clean_plan = {
                "_plan_dir": str(proj_dir),
                "canvas": {"width": 1080, "height": 1920, "fps": 30},
                "segments": [
                    {"id": "L1", "src": "bruno_clip.mp4", "start": 0.35, "end": 1.30},
                ],
            }
            missing_default = tmp_dir / "nonexistent_default_cache"
            with mock.patch.object(verify, "clip_work_dir", return_value=missing_default), \
                 mock.patch.object(common, "clip_work_dir", return_value=missing_default):
                ok_bad, clipped_bad, _, _, _ = verify.check_cuts_and_pauses(bad_plan)
                ok_clean, clipped_clean, _, _, _ = verify.check_cuts_and_pauses(clean_plan)
            self.assertFalse(ok_bad, "Mid-word slice at 1.12 inside 1.00..1.24 word must FAIL")
            self.assertGreaterEqual(clipped_bad, 1)
            self.assertTrue(ok_clean, "Clean cut at 1.30 outside word boundaries must PASS")
            self.assertEqual(clipped_clean, 0)

            # Vidhat: script_order.full_gate HARD-BLOCKS L4a -> L4b with tr=whip_left
            v_cache = tmp_dir / "vidhat_cache"
            v_cache.mkdir()
            v_words = [
                {"w": "When", "start": 0.10, "end": 0.35},
                {"w": "you", "start": 0.36, "end": 0.55},
                {"w": "order", "start": 0.56, "end": 0.90},
                {"w": "food", "start": 0.92, "end": 1.30},
                {"w": "always", "start": 1.75, "end": 2.15},
                {"w": "speak", "start": 2.16, "end": 2.55},
                {"w": "politely", "start": 2.56, "end": 3.00},
            ]
            (v_cache / "hq_words.json").write_text(json.dumps(v_words), encoding="utf-8")
            (v_cache / "silences.json").write_text("[]", encoding="utf-8")
            v_script = tmp_dir / "vidhat_script.txt"
            v_script.write_text("L4 Host: When you order food always speak politely.\n", encoding="utf-8")

            plan_whip = {
                "segments": [
                    {"id": "L4a", "src": "host.mp4", "start": 0.05, "end": 1.38, "layout": "single", "zoom": 1.0},
                    {
                        "id": "L4b",
                        "src": "host.mp4",
                        "start": 1.70,
                        "end": 3.05,
                        "layout": "single",
                        "zoom": 1.08,
                        "transition": "whip_left",
                    },
                ],
                "marks": [],
                "sfx": [],
            }
            plan_cut = {
                "segments": [
                    {"id": "L4a", "src": "host.mp4", "start": 0.05, "end": 1.38, "layout": "single", "zoom": 1.0},
                    {
                        "id": "L4b",
                        "src": "host.mp4",
                        "start": 1.70,
                        "end": 3.05,
                        "layout": "single",
                        "zoom": 1.08,
                        "transition": "cut",
                    },
                ],
                "marks": [],
                "sfx": [],
            }
            with mock.patch("common.clip_work_dir", return_value=v_cache), \
                 mock.patch.object(script_order, "voice_problems", return_value=([], "ok")):
                gate_whip = script_order.full_gate(plan_whip, v_script, tmp_dir)
                gate_cut = script_order.full_gate(plan_cut, v_script, tmp_dir)

            self.assertTrue(gate_whip["blocked"], f"L4a->L4b whip_left must be hard-blocked: {gate_whip}")
            prob_blob = " ".join(
                f"{k}: {' '.join(v)}" for k, v in (gate_whip.get("problems") or {}).items()
            ).lower()
            self.assertIn("whip_left", prob_blob, f"Expected mid-sentence whip_left blocker: {gate_whip['problems']}")
            self.assertIn("l4b", prob_blob)
            self.assertFalse(gate_cut["blocked"], f"Clean tr=cut L4a->L4b should pass: {gate_cut['problems']}")

    # ------------------------------------------------------------------
    # 5. Madhu's Variety Checks & Balesh's Stutters + Dict Stringification
    # ------------------------------------------------------------------
    def test_05_madhu_variety_checks_and_balesh_stutters(self) -> None:
        """`variety_report` catches 3/4 SFX dominance AND 3/3 identical transitions;
        `find_unscripted_repeats` catches `"I uh I think"` and `"th- the"`;
        `verify.check_script_coverage` formats dict repeat items via `message`.
        """
        plan_sfx_dom = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 1.5},
                {"id": "L2", "src": "b.mp4", "start": 0.0, "end": 1.5, "transition": "zoom_in"},
            ],
            "sfx": [
                {"segment": "L1", "file": "whoosh.wav", "at": 0.0},
                {"segment": "L1", "file": "whoosh.wav", "at": 0.5},
                {"segment": "L2", "file": "whoosh.wav", "at": 0.0},
                {"segment": "L2", "file": "pop.wav", "at": 0.5},
            ],
        }
        v_sfx = script_order.variety_report(plan_sfx_dom)
        self.assertTrue(v_sfx, f"3/4 identical SFX should trigger variety_report: {v_sfx}")
        self.assertTrue(any("whoosh.wav" in msg for msg in v_sfx), v_sfx)

        plan_tr_dom = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 1.5},
                {"id": "L2", "src": "b.mp4", "start": 0.0, "end": 1.5, "transition": "whip_left"},
                {"id": "L3", "src": "c.mp4", "start": 0.0, "end": 1.5, "transition": "whip_left"},
                {"id": "L4", "src": "d.mp4", "start": 0.0, "end": 1.5, "transition": "whip_left"},
            ],
            "sfx": [],
        }
        v_tr = script_order.variety_report(plan_tr_dom)
        self.assertTrue(v_tr, f"3/3 identical transitions should trigger variety_report: {v_tr}")
        self.assertTrue(any("whip_left" in msg for msg in v_tr), v_tr)

        # Balesh's stutters: filler-separated ("I uh I think") and truncated-prefix ("th- the")
        words_filler = [
            {"w": "I", "start": 0.10, "end": 0.22},
            {"w": "uh", "start": 0.25, "end": 0.40},
            {"w": "I", "start": 0.44, "end": 0.56},
            {"w": "think", "start": 0.58, "end": 0.90},
        ]
        reps_filler = script_order.find_unscripted_repeats(words_filler, "I think")
        self.assertTrue(reps_filler, f"Expected filler stutter catch for 'I uh I think': {reps_filler}")

        words_trunc = [
            {"w": "th-", "start": 0.10, "end": 0.25},
            {"w": "the", "start": 0.28, "end": 0.48},
            {"w": "book", "start": 0.50, "end": 0.85},
        ]
        reps_trunc = script_order.find_unscripted_repeats(words_trunc, "the book")
        self.assertTrue(reps_trunc, f"Expected truncated stutter catch for 'th- the': {reps_trunc}")

        # Verify check_script_coverage extracts dict["message"] cleanly instead of str(dict)
        fake_plan = {
            "segments": [{"id": "L1", "src": "a.mp4", "start": 0.0, "end": 1.5}],
        }
        with mock.patch.object(
            script_order,
            "repeat_problems",
            return_value=[{"id": "L1", "message": "L1: stutter 'i uh i'"}],
        ):
            ok_cov, _, _, dups = verify.check_script_coverage(fake_plan, script_path=Path("/nonexistent/script.txt"))
        self.assertFalse(ok_cov)
        self.assertEqual(dups, 1)
        self.assertEqual(fake_plan.get("_repeated_words"), ["L1: stutter 'i uh i'"])

    # ------------------------------------------------------------------
    # 6. Hitesh's Per-Panel offset_y + Modal FPS + Wider Flashcard + Split QC
    # ------------------------------------------------------------------
    def test_06_hitesh_per_panel_offset_y_modal_fps_and_wider_flashcard(self) -> None:
        """EDL roundtrips `offset_y=+0.08`, `peer@offset_y=+0.08`, `subs=off`, `subs=on` (dialogue & silent cuts);
        `compute_paired_split_crop_filters(..., live_offset_y=0.08, peer_offset_y=0.0)` shifts ONLY live panel;
        modal FPS picks 30fps over minority 24fps; flashcard preset is wider; `check_split_and_hook_rules`
        enforces `z=1.0` and real multi-segment headroom consistency.
        """
        edl_src = (
            '#src_dir "/tmp/raw"\n'
            '#subs "hormozi"\n'
            'L1 "Son.mp4" 0.00-3.00 layout=split_h z=1 sp="Son" slot=right '
            'peer="Mother:m.mp4"@0-3@left@offset_y=+0.08 offset_y=+0.08 subs=off\n'
            'L2 "Son.mp4" 3.00-5.00 layout=split_h z=1 sp="Son" slot=right '
            'peer="Mother:m.mp4"@3-5@left mute beat offset_y=-0.06 subs=on\n'
        )
        parsed = edl_parser.parse_edl(edl_src)
        s1, s2 = parsed["segments"][0], parsed["segments"][1]
        self.assertAlmostEqual(float(s1.get("offset_y", 0.0)), 0.08, places=3)
        self.assertAlmostEqual(float(s1["peer"].get("offset_y", 0.0)), 0.08, places=3)
        self.assertTrue(s1.get("subs_off"))
        self.assertAlmostEqual(float(s2.get("offset_y", 0.0)), -0.06, places=3)
        self.assertTrue(s2.get("subs_on"))
        self.assertTrue(s2.get("mute"))

        formatted = edl_parser.format_edl(parsed)
        self.assertIn("offset_y=+0.08", formatted)
        self.assertIn("@offset_y=+0.08", formatted)
        self.assertIn("subs=off", formatted)
        self.assertIn("subs=on", formatted)

        reparsed = edl_parser.parse_edl(formatted)
        rs1, rs2 = reparsed["segments"][0], reparsed["segments"][1]
        self.assertAlmostEqual(float(rs1.get("offset_y", 0.0)), 0.08, places=3)
        self.assertAlmostEqual(float(rs1["peer"].get("offset_y", 0.0)), 0.08, places=3)
        self.assertTrue(rs1.get("subs_off"))
        self.assertAlmostEqual(float(rs2.get("offset_y", 0.0)), -0.06, places=3)
        self.assertTrue(rs2.get("subs_on"))

        # compute_paired_split_crop_filters shifts ONLY live panel headroom +0.08 without moving peer
        base_live, base_peer = vision_tracker.compute_paired_split_crop_filters(
            "nonexistent_live.mp4",
            "nonexistent_peer.mp4",
            1080, 1920,
            1080, 1920,
            540, 1920,
            540, 1920,
            live_focus=[0.5, 0.35],
            peer_focus=[0.5, 0.35],
            live_offset_y=0.0,
            peer_offset_y=0.0,
        )
        sh_live, sh_peer = vision_tracker.compute_paired_split_crop_filters(
            "nonexistent_live.mp4",
            "nonexistent_peer.mp4",
            1080, 1920,
            1080, 1920,
            540, 1920,
            540, 1920,
            live_focus=[0.5, 0.35],
            peer_focus=[0.5, 0.35],
            live_offset_y=0.08,
            peer_offset_y=0.0,
        )
        self.assertAlmostEqual(float(sh_live.get("offset_y", 0.0)), 0.08, places=3)
        self.assertAlmostEqual(float(sh_peer.get("offset_y", 0.0)), 0.0, places=3)
        self.assertAlmostEqual(
            float(sh_live["slot_focus"][1]) - float(base_live["slot_focus"][1]),
            0.08,
            delta=0.015,
            msg=f"Live panel eyeline did not shift by +0.08: base={base_live['slot_focus']}, shifted={sh_live['slot_focus']}",
        )
        self.assertEqual(sh_peer["filter"], base_peer["filter"], "Peer filter must not shift when peer_offset_y=0.0")
        self.assertEqual(sh_peer["slot_focus"], base_peer["slot_focus"])

        # Modal mixed-FPS: 1x 24fps (first!) + 3x 30fps -> chooses 30fps
        fps_map = {"c24.mp4": 24.0, "c30a.mp4": 30.0, "c30b.mp4": 30.0, "c30c.mp4": 30.0}
        mixed_segs = [{"src": k} for k in ("c24.mp4", "c30a.mp4", "c30b.mp4", "c30c.mp4")]
        with mock.patch.object(render, "resolve", side_effect=lambda p: Path(str(p))), \
             mock.patch.object(Path, "is_file", return_value=True), \
             mock.patch.object(render, "probe", side_effect=lambda p: {"fps": fps_map.get(p.name, 24.0)}):
            chosen_fps = render._detect_modal_fps(mixed_segs, first_info={"fps": 24.0})
        self.assertEqual(chosen_fps, 30, f"Expected modal 30fps over minority 24fps first clip, got {chosen_fps}")

        # Wider portrait flashcard preset (standard >= 66% of W, compact_wide >= 72% of W)
        presets_916 = flashcard_placer._card_size_presets(1080, 1920, square=False)
        self.assertGreaterEqual(presets_916["standard"][0], int(0.65 * 1080))
        self.assertGreaterEqual(presets_916["compact_wide"][0], int(0.71 * 1080))

        # verify.check_split_and_hook_rules: z=1.08 on split fails; intentional offset_y passes with range; wild drift fails
        ok_z108, msg_z108 = verify.check_split_and_hook_rules({
            "segments": [{"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "zoom": 1.08}],
            "sfx": [],
        })
        self.assertFalse(ok_z108, f"z=1.08 on split_h must fail SPLIT_MAX_ZOOM=1.0 check: {msg_z108}")
        self.assertIn("excessive_split_zoom", msg_z108)

        ok_off, msg_off = verify.check_split_and_hook_rules(parsed)
        self.assertTrue(ok_off, f"Intentional offset_y split should pass cleanly: {msg_off}")
        self.assertIn("headroom", msg_off)

        # Simulated wild cross-segment drift without offset_y MUST fail
        drift_seq = iter([
            ({"common_headroom_pct": 14.0, "offset_y": 0.0, "slot_focus": [0.5, 0.26]},
             {"common_headroom_pct": 14.0, "offset_y": 0.0, "slot_focus": [0.5, 0.28]}),
            ({"common_headroom_pct": 14.0, "offset_y": 0.0, "slot_focus": [0.5, 0.46]},
             {"common_headroom_pct": 14.0, "offset_y": 0.0, "slot_focus": [0.5, 0.28]}),
        ])
        drift_plan = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "slot": "left",
                 "peer": {"src": "b.mp4", "slot": "right"}},
                {"id": "L2", "src": "c.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "slot": "left",
                 "peer": {"src": "b.mp4", "slot": "right"}},
            ],
            "sfx": [],
        }
        with mock.patch("vision_tracker.compute_paired_split_crop_filters", side_effect=lambda *a, **kw: next(drift_seq)):
            ok_drift, msg_drift = verify.check_split_and_hook_rules(drift_plan)
        self.assertFalse(ok_drift, f"Wild eyeline drift across split segments must fail: {msg_drift}")
        self.assertIn("eyeline_drift", msg_drift)

    # ------------------------------------------------------------------
    # 7. Hitesh's Cut-Seam Black-Flicker Scanner + Smooth 2x Zooms & 16ms Splice
    # ------------------------------------------------------------------
    def test_07_hitesh_cut_seam_black_flicker_scanner_and_smooth_2x_zoom_transitions(self) -> None:
        """Real FFmpeg clip with a 2-frame black flicker at a cut seam (`t=1.00s`) is CAUGHT by
        `verify.check_frames` (while intentional `dip_to_black` passes) and `verify.check_square_output`
        only forgives intentional dark fades; smooth 2x `zoom_in` & `zoom_out` + 16ms audio splice
        render exact durations with zero black borders and zero audio bleed.
        """
        import cv2

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            W, H, fps = 360, 640, 30

            # Build 2.0s clip (60 frames @ 30fps): frames 0..28 bright, 29..30 BLACK (2 frames right at t=1.00s), 31..59 bright
            seam_mp4 = tmp_dir / "seam_flicker.mp4"
            fc_seam = (
                f"testsrc2=size={W}x{H}:rate={fps}:duration=1.0,trim=start_frame=0:end_frame=29,setpts=PTS-STARTPTS[v0];"
                f"color=c=black:s={W}x{H}:r={fps},trim=start_frame=0:end_frame=2,setpts=PTS-STARTPTS[vb];"
                f"smptebars=size={W}x{H}:rate={fps}:duration=1.0,trim=start_frame=0:end_frame=29,setpts=PTS-STARTPTS[v1];"
                "[v0][vb][v1]concat=n=3:v=1:a=0,format=yuv420p[out]"
            )
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-filter_complex", fc_seam,
                    "-map", "[out]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "1", "-pix_fmt", "yuv420p",
                    str(seam_mp4),
                ],
                check=True,
                capture_output=True,
            )

            plan_cut = {
                "canvas": {"width": W, "height": H, "fps": fps},
                "segments": [
                    {"id": "S1", "src": str(seam_mp4), "start": 0.0, "end": 1.0, "layout": "single"},
                    {"id": "S2", "src": str(seam_mp4), "start": 1.0, "end": 2.0, "layout": "single", "transition": "cut"},
                ],
                "marks": [],
            }
            plan_dip = {
                "canvas": {"width": W, "height": H, "fps": fps},
                "segments": [
                    {"id": "S1", "src": str(seam_mp4), "start": 0.0, "end": 1.0, "layout": "single"},
                    {
                        "id": "S2",
                        "src": str(seam_mp4),
                        "start": 1.0,
                        "end": 2.0,
                        "layout": "single",
                        "transition": "dip_to_black",
                    },
                ],
                "marks": [],
            }
            with mock.patch("vision_tracker.detect_faces_in_frame", return_value=[]):
                res_cut = verify.check_frames(plan_cut, seam_mp4)
                res_dip = verify.check_frames(plan_dip, seam_mp4)

            self.assertGreaterEqual(
                len(res_cut["black"]),
                1,
                f"2-frame black flicker at t=1.00s cut seam MUST be caught: {res_cut}",
            )
            self.assertFalse(res_cut["ok"], f"Black flicker at cut seam must fail check_frames: {res_cut}")
            self.assertEqual(
                res_dip["black"],
                [],
                f"Intentional dip_to_black transition must pass check_frames: {res_dip}",
            )
            self.assertTrue(res_dip["ok"], f"Intentional dip_to_black must pass: {res_dip}")

            # check_square_output: black flicker at 50% sample point (t=1.00s on 2.0s video) is NOT forgiven on cut,
            # but IS forgiven when plan has intentional dip_to_black at t=1.00s
            sq_mp4 = tmp_dir / "SQ_seam.mp4"
            fc_sq = (
                f"testsrc2=size=360x360:rate={fps}:duration=1.0,trim=start_frame=0:end_frame=28,setpts=PTS-STARTPTS[s0];"
                f"color=c=black:s=360x360:r={fps},trim=start_frame=0:end_frame=4,setpts=PTS-STARTPTS[sb];"
                f"smptebars=size=360x360:rate={fps}:duration=1.0,trim=start_frame=0:end_frame=28,setpts=PTS-STARTPTS[s1];"
                "[s0][sb][s1]concat=n=3:v=1:a=0,format=yuv420p[out]"
            )
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-filter_complex", fc_sq,
                    "-map", "[out]",
                    "-c:v", "libx264", "-preset", "ultrafast", "-g", "1", "-pix_fmt", "yuv420p",
                    str(sq_mp4),
                ],
                check=True,
                capture_output=True,
            )
            with mock.patch("vision_tracker.detect_faces_in_frame", return_value=[]):
                sq_ok_cut, sq_msg_cut = verify.check_square_output(sq_mp4, sample_count=5, plan=plan_cut)
                sq_ok_dip, sq_msg_dip = verify.check_square_output(sq_mp4, sample_count=5, plan=plan_dip)
            self.assertFalse(sq_ok_cut, f"Square black frame at cut seam must NOT be forgiven: {sq_msg_cut}")
            self.assertIn("black frames", sq_msg_cut)
            self.assertTrue(sq_ok_dip, f"Square intentional dip_to_black should pass: {sq_msg_dip}")

            # Smooth 2x supersampled zoom_in & zoom_out transitions: exact duration, zero black borders
            fps_tr, n_tr = 25, 8
            clip_a = _make_synthetic_clip(tmp_dir / "ca.mp4", width=W, height=H, fps=fps_tr, duration=0.8, source_filter="smptebars")
            clip_b = _make_synthetic_clip(tmp_dir / "cb.mp4", width=W, height=H, fps=fps_tr, duration=0.8, source_filter="testsrc2")
            for tr_name in ("zoom_in", "zoom_out"):
                tr_fc = render._build_physical_transition_fc(tr_name, idx=1, W=W, H=H, fps=fps_tr, n_tr=n_tr)
                self.assertIn(f"{W * 2}", tr_fc, f"Expected 2x supersampling in {tr_name} filtergraph")
                fc_tr = (
                    f"[0:v]trim=start_frame=0:end_frame={n_tr},setpts=N/({fps_tr}*TB),format=yuv420p[oa1];"
                    f"[1:v]trim=start_frame=0:end_frame={n_tr},setpts=N/({fps_tr}*TB),format=yuv420p[ib1];"
                    + tr_fc
                )
                tr_out = tmp_dir / f"tr_{tr_name}.mp4"
                subprocess.run(
                    [
                        str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                        "-i", str(clip_a), "-i", str(clip_b),
                        "-filter_complex", fc_tr,
                        "-map", "[x1]",
                        "-frames:v", str(n_tr),
                        "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                        str(tr_out),
                    ],
                    check=True,
                    capture_output=True,
                )
                tr_info = common.probe(tr_out)
                self.assertAlmostEqual(float(tr_info["duration"]), n_tr / float(fps_tr), delta=0.05)

                cap = cv2.VideoCapture(str(tr_out))
                cap.set(cv2.CAP_PROP_POS_FRAMES, n_tr // 2)
                ok_f, fr = cap.read()
                cap.release()
                self.assertTrue(ok_f and fr is not None)
                gray = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
                self.assertFalse(verify.is_black_frame(gray), f"{tr_name} midpoint is black")
                b = 8
                for side, val in (
                    ("top", float(gray[:b, :].mean())),
                    ("bottom", float(gray[-b:, :].mean())),
                    ("left", float(gray[:, :b].mean())),
                    ("right", float(gray[:, -b:].mean())),
                ):
                    self.assertGreaterEqual(val, 18.0, f"{tr_name} has dark {side} border ({val:.1f})")

            # 16ms zero-drift audio splice: verify render.py configures 16ms crossfade & FFmpeg splice yields exact 2.00s + zero bleed
            render_src = (ROOT / "scripts" / "render.py").read_text(encoding="utf-8")
            self.assertIn("xf_audio_dur = 0.016", render_src)
            wav_a = _write_pcm_wav(tmp_dir / "tone_a.wav", 1.0, sample_rate=48000, tone_windows=[(0.0, 1.0, -6.0)])
            wav_b = _write_pcm_wav(tmp_dir / "silent_b.wav", 1.0, sample_rate=48000, tone_windows=[])
            spliced_wav = tmp_dir / "spliced.wav"
            xf_d = 0.016
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(wav_a), "-i", str(wav_b),
                    "-filter_complex",
                    f"[0:a]asetnsamples=n=256:p=0,asetpts=PTS-STARTPTS,apad=pad_dur={xf_d:.4f}[ap1];"
                    f"[1:a]asetnsamples=n=256:p=0,asetpts=PTS-STARTPTS[sa1];"
                    f"[ap1][sa1]acrossfade=d={xf_d:.4f}:c1=tri:c2=tri[aout]",
                    "-map", "[aout]",
                    "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le",
                    str(spliced_wav),
                ],
                check=True,
                capture_output=True,
            )
            with wave.open(str(spliced_wav), "rb") as wf:
                sr = wf.getframerate()
                n_fr = wf.getnframes()
                raw_pcm = wf.readframes(n_fr)
            tot_sec = n_fr / float(sr)
            self.assertAlmostEqual(tot_sec, 2.0, delta=0.02, msg=f"16ms splice drifted duration: {tot_sec:.4f}s")
            pcm = np.frombuffer(raw_pcm, dtype=np.int16).astype(np.float64) / 32768.0
            # Verify no audio bleed from cut A into cut B past 1.03s (well clear of 16ms splice)
            bleed_slice = pcm[int(1.03 * sr) : int(1.90 * sr)]
            bleed_rms_db = 20.0 * math.log10(max(float(np.sqrt(np.mean(bleed_slice * bleed_slice))), 1e-10))
            self.assertLess(bleed_rms_db, -60.0, f"Audio bled into cut B past 16ms splice: {bleed_rms_db:.1f} dBFS")

    def test_layouts_frame_quantization_matches_verify_timeline(self) -> None:
        """Multi-cut 24fps timelines must not accumulate +1 frame/cut ceil drift against verify.py."""
        sample_durs = [
            0.42, 2.71, 1.70, 2.38, 2.30, 2.71, 2.10, 1.34, 1.25, 5.19,
            9.00, 3.05, 2.86, 2.47, 9.85, 0.63, 3.11, 3.78, 7.87, 4.09, 7.64,
        ]
        segs = [
            {"id": f"L{i + 1}", "src": "missing_clip.mp4", "start": 0.50, "end": round(0.50 + d, 2)}
            for i, d in enumerate(sample_durs)
        ]
        segs.append({"id": "L22", "src": "missing_clip.mp4", "start": 0.50, "end": 1.52, "hold_tail": 0.45})
        plan = {"canvas": {"width": 720, "height": 1280, "fps": 24}, "segments": segs}

        _, _, verify_total, _ = verify.compute_timeline_starts(plan, 24)
        layout_total = 0.0
        for idx, seg in enumerate(segs):
            _, exact, frames = layouts.build_segment_ffmpeg_args(seg, plan, 720, 1280, 24, Path(f"/tmp/seg_{idx}.mkv"))
            expected_frames = max(1, round(common.segment_duration(seg) * 24))
            self.assertEqual(frames, expected_frames, f"Frame mismatch on {seg['id']}")
            layout_total += exact

        self.assertAlmostEqual(layout_total, verify_total, delta=1e-6)

    def test_cross_res_smooth_background_ecc_registration_and_mismatch_rejection(self) -> None:
        """Smooth studio ring backgrounds across 1080p vs 720p align via masked ECC without jumping,
        while unrelated backgrounds still return `None`.
        """
        import cv2

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            a_ref = np.full((1280, 720), 28, dtype=np.uint8)
            cv2.circle(a_ref, (360, 420), 250, 235, 10, lineType=cv2.LINE_AA)
            a_hi = cv2.resize(a_ref, (1080, 1920), interpolation=cv2.INTER_CUBIC)
            a_hi[340:820, 380:720] = 210  # subject inside person mask

            M = np.float32([[1, 0, -10.0], [0, 1, 6.0]])
            b_lo = cv2.warpAffine(a_ref, M, (720, 1280), borderValue=28)
            b_lo[210:540, 245:495] = 175  # slightly moved mouth/head inside mask

            # Unrelated studio background with tilted gradient bands
            c_lo = np.fromfunction(lambda y, x: ((x * 2 + y) % 180) + 40, (1280, 720)).astype(np.uint8)

            def _mock_gray(src: Path | str, _t: float):
                s = str(src)
                if s.endswith("silent.mp4"):
                    return a_hi
                if s.endswith("live.mp4"):
                    return b_lo
                return c_lo

            bbox_a = [0.35, 0.17, 0.69, 0.43]
            bbox_b = [0.33, 0.13, 0.70, 0.42]
            with mock.patch.object(vision_tracker, "_read_gray_frame", side_effect=_mock_gray):
                reg_ok = vision_tracker.register_background(
                    tmp_dir / "silent.mp4", 0.0, tmp_dir / "live.mp4", 0.5, bbox_a, bbox_b
                )
                reg_bad = vision_tracker.register_background(
                    tmp_dir / "silent.mp4", 0.0, tmp_dir / "other.mp4", 0.5, bbox_a, bbox_b
                )

            self.assertIsNotNone(reg_ok, "Cross-res smooth studio pair should register via masked ECC")
            assert reg_ok is not None
            self.assertAlmostEqual(reg_ok["a"], 720.0 / 1080.0, delta=0.02)
            self.assertAlmostEqual(reg_ok["tx"], -10.0, delta=2.5)
            self.assertAlmostEqual(reg_ok["ty"], 6.0, delta=2.5)
            self.assertIsNone(reg_bad, "Unrelated studio background must be rejected")

    # ------------------------------------------------------------------
    # 8. v2.0.4: Subtitle y=65 / split_v=50, W47 L7 Face Filter & Ghost Immunity
    # ------------------------------------------------------------------
    def test_08_subtitle_y65_vs_split_v_y50_w47_l7_face_and_ghost_words(self) -> None:
        """`resolve_safe_y_percent` defaults to `65.0` on `single` (including multi-face
        live conversations and W47 L7's chin@0.56 + tiny <12% background shelf photo face),
        `split_h`, and `top_pip`, uses `50.0` on `split_v`/`top_bottom`/`vstack`, and
        `subtitles.build_cues` strips `no_sound` and silent-tail ghost words.
        """
        def _mk_cue(t0: float, t1: float, seg_idx: int = 0) -> dict:
            return {
                "text": "Test cue",
                "startTime": t0,
                "endTime": t1,
                "segIndex": seg_idx,
                "words": [
                    {"text": "Test", "startTime": t0, "endTime": (t0 + t1) / 2.0},
                    {"text": "cue", "startTime": (t0 + t1) / 2.0, "endTime": t1},
                ],
            }

        spec_auto = {"style": "white_pill"}
        # 1. single, multi-face live conversation, split_h, and top_pip all return 65.0
        for label, seg in (
            (
                "single",
                {
                    "id": "L1",
                    "layout": "single",
                    "start": 0.0,
                    "end": 2.0,
                    "faces": [{"bbox_norm": [0.30, 0.18, 0.70, 0.52], "confidence": 0.96}],
                },
            ),
            (
                "single_multiface_live_conv",
                {
                    "id": "L2",
                    "layout": "single",
                    "start": 0.0,
                    "end": 2.0,
                    "faces": [
                        {"bbox_norm": [0.12, 0.18, 0.44, 0.52], "confidence": 0.95},
                        {"bbox_norm": [0.56, 0.20, 0.88, 0.54], "confidence": 0.94},
                    ],
                },
            ),
            (
                "split_h",
                {
                    "id": "L3",
                    "layout": "split_h",
                    "start": 0.0,
                    "end": 2.0,
                    "faces": [{"bbox_norm": [0.25, 0.44, 0.65, 0.68], "confidence": 0.95}],
                },
            ),
            (
                "top_pip",
                {
                    "id": "L4",
                    "layout": "top_pip",
                    "start": 0.0,
                    "end": 2.0,
                    "faces": [{"bbox_norm": [0.25, 0.44, 0.65, 0.68], "confidence": 0.95}],
                },
            ),
        ):
            cues = [_mk_cue(0.1, 1.8, 0)]
            y_res = subtitles.resolve_safe_y_percent(
                spec_auto, [seg], [0.0], 2.0, 1080, 1920, cues=cues
            )
            self.assertEqual(y_res, 65.0, f"{label} expected 65.0, got {y_res}")
            self.assertNotIn("positionY", cues[0].get("style") or {}, f"{label} unexpectedly shifted cue: {cues[0]}")

        # 2. W47 L7 regression: chin at y1=0.56 + tiny background shelf photo face (height < 0.12)
        self.assertFalse(subtitles._has_lower_mid_face([(0.25, 0.18, 0.65, 0.56), (0.72, 0.48, 0.80, 0.59)]))
        self.assertFalse(subtitles._has_lower_mid_face([(0.25, 0.18, 0.65, 0.56), (0.72, 0.54, 0.80, 0.64)]))
        self.assertTrue(subtitles._has_lower_mid_face([(0.25, 0.44, 0.65, 0.66)]))

        for shelf_box, bg_conf, z_to in (
            ([0.72, 0.48, 0.80, 0.59], 0.72, 1.0),
            ([0.72, 0.50, 0.80, 0.60], 0.72, 1.0),
            ([0.72, 0.54, 0.80, 0.64], 0.72, 1.0),
            ([0.7357, 0.474, 0.9359, 0.6078], 0.64, 1.06),
        ):
            l7_seg = {
                "id": "L7",
                "layout": "single",
                "start": 0.0,
                "end": 2.0,
                "zoom": 1.0,
                "zoom_to": z_to,
                "focus": [0.4972, 0.3242],
                "faces": [
                    {"bbox_norm": [0.3593, 0.3078, 0.6335, 0.5289], "confidence": 0.955},
                    {"bbox_norm": shelf_box, "confidence": bg_conf},
                ],
            }
            cues_l7 = [_mk_cue(0.1, 1.8, 0)]
            y_l7 = subtitles.resolve_safe_y_percent(
                spec_auto, [l7_seg], [0.0], 2.0, 1080, 1920, cues=cues_l7
            )
            self.assertEqual(y_l7, 65.0)
            self.assertNotIn(
                "positionY",
                cues_l7[0].get("style") or {},
                f"Background shelf face {shelf_box} must NOT drop caption to 79%: {cues_l7[0]}",
            )

        # Contrast: genuine >=12% tall foreground lower-mid face drops single caption to 79.0
        fg_low_seg = {
            "id": "L8",
            "layout": "single",
            "start": 0.0,
            "end": 2.0,
            "faces": [{"bbox_norm": [0.25, 0.44, 0.65, 0.66], "confidence": 0.96}],
        }
        cues_fg = [_mk_cue(0.1, 1.8, 0)]
        y_fg = subtitles.resolve_safe_y_percent(spec_auto, [fg_low_seg], [0.0], 2.0, 1080, 1920, cues=cues_fg)
        self.assertEqual(y_fg, 65.0)
        self.assertEqual(cues_fg[0].get("style", {}).get("positionY"), 79.0)

        # 3. split_v and aliases (top_bottom, vstack) return 50.0; mixed timeline overrides per-cue
        for alias in ("split_v", "top_bottom", "vstack"):
            sv_seg = {"id": "L1", "layout": alias, "start": 0.0, "end": 2.0}
            cues_sv = [_mk_cue(0.1, 1.8, 0)]
            y_sv = subtitles.resolve_safe_y_percent(spec_auto, [sv_seg], [0.0], 2.0, 1080, 1920, cues=cues_sv)
            self.assertEqual(y_sv, 50.0, f"{alias} expected 50.0, got {y_sv}")
            self.assertNotIn("positionY", cues_sv[0].get("style") or {})

        mixed_segs = [
            {"id": "L1", "layout": "single", "start": 0.0, "end": 2.0, "faces": [{"bbox_norm": [0.3, 0.18, 0.7, 0.5], "confidence": 0.95}]},
            {"id": "L2", "layout": "split_v", "start": 0.0, "end": 1.2},
        ]
        mixed_cues = [_mk_cue(0.1, 1.8, 0), _mk_cue(2.1, 3.0, 1)]
        y_mix = subtitles.resolve_safe_y_percent(spec_auto, mixed_segs, [0.0, 2.0], 3.2, 1080, 1920, cues=mixed_cues)
        self.assertEqual(y_mix, 65.0)
        self.assertNotIn("positionY", mixed_cues[0].get("style") or {})
        self.assertEqual(mixed_cues[1].get("style", {}).get("positionY"), 50.0)

        # 4. Ghost word filtering in build_cues + _visual_units check
        self.assertGreater(subtitles._visual_units("ಕನ್ನಡ"), subtitles._visual_units("ii"))
        self.assertGreater(subtitles._visual_units("हिन्दी"), subtitles._visual_units("ii"))

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip_cache = tmp_dir / "clip_cache"
            clip_cache.mkdir()
            hq_words = [
                {"w": "Never", "start": 0.10, "end": 0.40},
                {"w": "say", "start": 0.42, "end": 0.72},
                {"w": "this", "start": 0.74, "end": 1.05},
                {"w": "uh", "start": 1.15, "end": 1.35, "no_sound": True},
                {"w": "ghost", "start": 1.45, "end": 2.10},
            ]
            (clip_cache / "hq_words.json").write_text(json.dumps(hq_words), encoding="utf-8")
            (clip_cache / "silences.json").write_text("[]", encoding="utf-8")
            (clip_cache / "speech_spans.json").write_text(json.dumps({"spans": [[0.08, 1.08]]}), encoding="utf-8")
            _write_pcm_wav(clip_cache / "audio16k.wav", 2.3, tone_windows=[(0.08, 1.08, -18.0)])

            script_lines = [{"id": "L1", "text": "Never say this"}]
            segs = [{"id": "L1", "src": "clip.mp4", "start": 0.0, "end": 2.2}]
            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                built = subtitles.build_cues(segs, [0.0], script_lines=script_lines, work_dir=tmp_dir)
            self.assertTrue(built, "Expected spoken cues from build_cues")
            all_words = [w["text"].lower() for c in built for w in c.get("words", [])]
            self.assertEqual(all_words, ["never", "say", "this"])
            self.assertNotIn("uh", all_words)
            self.assertNotIn("ghost", all_words)

    # ------------------------------------------------------------------
    # 9. v2.0.4: Strict Single-Line Enforcement (Long Multilingual + Newlines)
    # ------------------------------------------------------------------
    def test_09_strict_single_line_enforcement_node_render_cues(self) -> None:
        """Long English/Kannada/Devanagari cues and cues with embedded `\\n`/`\\r` render
        strictly on ONE row across styles (including White Box #5 and Docu Split-Bar #28),
        never jumping to ~2x 2-line height and flattening newlines cleanly.
        """
        styles_path = ROOT / "subtitles" / "styles.json"
        styles_by_id = {int(s["id"]): s for s in json.loads(styles_path.read_text(encoding="utf-8"))}
        target_styles = [styles_by_id[5], styles_by_id[28], styles_by_id[4]]  # White Box, Docu Split-Bar, Blue Highlight

        def _ink_height(png_path: Path) -> int:
            with Image.open(png_path) as im:
                arr = np.asarray(im.convert("RGBA"))
            ys = np.where(arr[:, :, 3] > 20)[0]
            self.assertGreater(len(ys), 0, f"Blank PNG rendered: {png_path}")
            return int(ys.max() - ys.min() + 1)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            for st in target_styles:
                sid = int(st["id"])
                sname = str(st["name"])
                cues = [
                    {
                        "startTime": 0.0,
                        "endTime": 0.5,
                        "text": "Hello",
                        "words": [{"text": "Hello", "startTime": 0.0, "endTime": 0.5}],
                    },
                    {
                        "startTime": 0.6,
                        "endTime": 1.1,
                        "text": "ಕನ್ನಡ",
                        "words": [{"text": "ಕನ್ನಡ", "startTime": 0.6, "endTime": 1.1}],
                    },
                    {
                        "startTime": 1.2,
                        "endTime": 1.7,
                        "text": "नमस्ते",
                        "words": [{"text": "नमस्ते", "startTime": 1.2, "endTime": 1.7}],
                    },
                    {
                        "startTime": 1.8,
                        "endTime": 2.4,
                        "text": "Never say this overly long restaurant ordering phrase when talking to the waiter",
                        "words": [
                            {"text": w, "startTime": 1.8 + i * 0.04, "endTime": 1.84 + i * 0.04}
                            for i, w in enumerate(
                                "Never say this overly long restaurant ordering phrase when talking to the waiter".split()
                            )
                        ],
                    },
                    {
                        "startTime": 2.5,
                        "endTime": 3.1,
                        "text": "ಕನ್ನಡದಲ್ಲಿ ತುಂಬಾ ಉದ್ದವಾದ ವಾಕ್ಯವನ್ನು ಒಂದೇ ಸಾಲಿನಲ್ಲಿ ತೋರಿಸಬೇಕು",
                        "words": [
                            {"text": w, "startTime": 2.5 + i * 0.08, "endTime": 2.58 + i * 0.08}
                            for i, w in enumerate(
                                "ಕನ್ನಡದಲ್ಲಿ ತುಂಬಾ ಉದ್ದವಾದ ವಾಕ್ಯವನ್ನು ಒಂದೇ ಸಾಲಿನಲ್ಲಿ ತೋರಿಸಬೇಕು".split()
                            )
                        ],
                    },
                    {
                        "startTime": 3.2,
                        "endTime": 3.8,
                        "text": "आज हम बहुत लंबा वाक्य बिना किसी रुकावट के एक ही लाइन में बोलेंगे",
                        "words": [
                            {"text": w, "startTime": 3.2 + i * 0.04, "endTime": 3.24 + i * 0.04}
                            for i, w in enumerate(
                                "आज हम बहुत लंबा वाक्य बिना किसी रुकावट के एक ही लाइन में बोलेंगे".split()
                            )
                        ],
                    },
                    {
                        "startTime": 3.9,
                        "endTime": 4.4,
                        "text": "Never\nsay\r\nthis phrase",
                        "words": [
                            {"text": "Never\nsay", "startTime": 3.9, "endTime": 4.15},
                            {"text": "this\r\nphrase", "startTime": 4.15, "endTime": 4.4},
                        ],
                    },
                    {
                        "startTime": 4.5,
                        "endTime": 5.0,
                        "text": "Never say this phrase",
                        "words": [
                            {"text": "Never", "startTime": 4.5, "endTime": 4.62},
                            {"text": "say", "startTime": 4.62, "endTime": 4.74},
                            {"text": "this", "startTime": 4.74, "endTime": 4.86},
                            {"text": "phrase", "startTime": 4.86, "endTime": 5.0},
                        ],
                    },
                ]
                manifest = subtitles.render_frames(
                    cues,
                    st,
                    1080,
                    1920,
                    tmp_dir / f"s_{sid}",
                    y_percent=65.0,
                    fps=25,
                    return_meta=True,
                    font_size=54,
                )
                frames = manifest.get("frames") or []
                self.assertTrue(frames, f"No frames rendered for style {sid} ({sname})")
                by_cap: dict[int, list[int]] = {}
                for fr in frames:
                    ci = int(fr["captionIndex"])
                    by_cap.setdefault(ci, []).append(_ink_height(Path(fr["path"])))
                for ci in range(len(cues)):
                    self.assertIn(ci, by_cap, f"Missing rendered frames for cue {ci} in {sname}")

                base_en = max(by_cap[0])
                base_kn = max(by_cap[1])
                base_hi = max(by_cap[2])
                long_en = max(by_cap[3])
                long_kn = max(by_cap[4])
                long_hi = max(by_cap[5])
                nl_en = max(by_cap[6])
                sp_en = max(by_cap[7])

                self.assertLessEqual(
                    long_en,
                    int(base_en * 1.25),
                    f"{sname}: long English cue wrapped/ballooned ({long_en}px vs 1-word {base_en}px)",
                )
                self.assertLessEqual(
                    long_kn,
                    int(base_kn * 1.25),
                    f"{sname}: long Kannada cue wrapped/ballooned ({long_kn}px vs 1-word {base_kn}px)",
                )
                self.assertLessEqual(
                    long_hi,
                    int(base_hi * 1.25),
                    f"{sname}: long Devanagari cue wrapped/ballooned ({long_hi}px vs 1-word {base_hi}px)",
                )
                self.assertLessEqual(
                    abs(nl_en - sp_en),
                    2,
                    f"{sname}: embedded \\n/\\r\\n caused line-break height jump ({nl_en}px vs {sp_en}px)",
                )


if __name__ == "__main__":
    unittest.main()
