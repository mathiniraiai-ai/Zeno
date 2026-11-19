#!/usr/bin/env python3
"""Comprehensive regression test suite for W47 edit patterns:
- Format detection across all 4 W47 scripts/titles (including no 'visa interview' hijack
  and title-less `stop_saying` detection via 'never to say' / ❌ cues).
- Lossless EDL parse + `format_edl` roundtrip for new zoom curves, speed/mute/beat, and `#audio`.
- Audio loudness target sync (`common.DEFAULT_LOUDNESS_LUFS == -10.5`, `style_config`, `verify.py`).
- Hook transition cap unblock (3 & 4 hook transitions pass; intra-split transitions/whooshes still fail),
  plus `script_order` muted/beat exemption and `style_report` still-share accuracy.
- Real FFmpeg synthetic render test for `speed=0.5 mute` + `z=bell:1.20->1.55->1.0` slo-mo hook
  (exact 2.0s duration, zero FROZEN, zero CUTS dead-air fail, zero ghost captions in `subtitles.build_cues`).
- Real FFmpeg synthetic Stage 2 transition render test for `zoom_in`, `zoom_out`, `zoom_blur`
  (exact duration, zero black border frames on transition midpoints).
- Premiere Pro XML/prproj export checks (`sudden_out` scale >= 100%, no Dip to Black on zoom/flash transitions,
  `speed=0.5 mute` duration, no linked audio, and `timeremap` XML).
- Token budget & `edit_packet.compact_brief()` preservation check for W47 `skit` & `stop_saying` cards.
"""
from __future__ import annotations

import gzip
import inspect
import json
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import common  # noqa: E402
import edit_brief  # noqa: E402
import edit_packet  # noqa: E402
import edl_parser  # noqa: E402
import format_library  # noqa: E402
import layouts  # noqa: E402
import premiere_export  # noqa: E402
import render  # noqa: E402
import script_order  # noqa: E402
import style_config  # noqa: E402
import subtitles  # noqa: E402
import verify  # noqa: E402


def _make_synthetic_clip(
    out_path: Path,
    *,
    width: int = 360,
    height: int = 640,
    fps: int = 25,
    duration: float = 2.0,
    source_filter: str = "testsrc2",
    with_audio: bool = True,
    freq: int = 440,
) -> Path:
    """Create a tiny fast deterministic synthetic clip via bundled FFmpeg."""
    if source_filter == "testsrc2":
        v_src = f"testsrc2=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    elif source_filter == "smptebars":
        v_src = f"smptebars=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    elif source_filter == "rgbtestsrc":
        v_src = f"rgbtestsrc=size={width}x{height}:rate={fps}:duration={duration:.3f}"
    else:
        v_src = source_filter

    cmd = [
        str(common.FFMPEG),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        v_src,
    ]
    if with_audio:
        cmd.extend([
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency={freq}:sample_rate=48000:duration={duration:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ac",
            "2",
            "-b:a",
            "128k",
            "-shortest",
            str(out_path),
        ])
    else:
        cmd.extend([
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(out_path),
        ])
    subprocess.run(cmd, check=True, capture_output=True)
    return out_path


class W47EditPatternsTests(unittest.TestCase):
    # ------------------------------------------------------------------
    # 1. Format detection across all 4 W47 scripts/titles
    # ------------------------------------------------------------------
    def test_format_detection_w47_scripts_and_no_visa_interview_hijack(self) -> None:
        """All 4 W47 scripts/titles route accurately (`v1/v2/v4 -> skit`, title-less `v3 -> stop_saying`)
        without 'visa interview' hijacking domestic drama skits to `street_interview`.
        """
        # V1: Husband relocating abroad — contains "visa interview" in dialogue/body!
        v1_title = "REEL - W47 - Kannada - AI Only - Skit - husband relocating abroad without wife"
        v1_body = (
            "Visual: On the terrace, a wife happily tells her mother her husband got a job in Canada.\n"
            "Characters\n"
            "Meera -- Woman, late 20s.\n"
            "Aneesh -- Man, early 30s.\n"
            "Miss Nova -- The AI teacher.\n"
            "Scene 1 -- Terrace, the happy call\n"
            "Meera: His Canada visa arrived today.\n"
            "Scene 2 -- Staircase\n"
            "Aneesh: She cannot even clear a simple visa interview in English.\n"
            "Scene 5 -- Practice with Miss Nova\n"
        )
        v1_meta = {"title": v1_title, "concept_brief": "husband relocating abroad without wife"}
        self.assertEqual(format_library.detect_catalog_format(v1_meta, {}, script_text=v1_body), "skit")
        self.assertEqual(edit_brief.detect_format(v1_meta, {}, script_text=v1_body), "skit")

        # Also test title-less V1 (gdoc_* stem) + "visa interview" in body and fallback detector
        v1_gdoc_meta = {"title": "gdoc_1husband_tab0"}
        self.assertEqual(format_library.detect_catalog_format(v1_gdoc_meta, {}, script_text=v1_body), "skit")
        self.assertEqual(edit_brief.detect_format(v1_gdoc_meta, {}, script_text=v1_body), "skit")
        if hasattr(edit_brief, "_builtin_detect_format"):
            self.assertEqual(
                edit_brief._builtin_detect_format(v1_gdoc_meta, {}, "", script_text=v1_body),
                "skit",
            )

        # Even a skit title mentioning "job interview" must NOT hijack to street_interview
        interview_skit_meta = {"title": "W47 - Telugu - Skit - failed job interview"}
        self.assertEqual(
            format_library.detect_catalog_format(interview_skit_meta, {}, script_text="Scene 1: Office room."),
            "skit",
        )

        # V2: Fisherwoman
        v2_meta = {
            "title": "Reel - W47 - Kannada - AI Only - Skit - Fisherwoman",
            "concept_brief": "Fisherwoman not paid by customer because she cannot argue in English",
        }
        v2_body = (
            "Scene 1 -- Fish market\n"
            "Fisherwoman: Sir, please pay the full amount.\n"
            "Scene 2 -- Practicing with Miss Nova\n"
        )
        self.assertEqual(format_library.detect_catalog_format(v2_meta, {}, script_text=v2_body), "skit")
        self.assertEqual(edit_brief.detect_format(v2_meta, {}, script_text=v2_body), "skit")

        # V3: Raw script WITHOUT W47 title (gdoc_* download) -> must STILL resolve to `stop_saying`!
        v3_raw_body = (
            'Visual description: a young man tells a girl "You are very beautiful" at a party. '
            'She tells him never to say "very beautiful," flashes back to himself using "very" with everything.\n'
            "Characters\n* Guy\n* Girl\n* Female Robot\n"
            "Scene 1 - Party\n"
            'Guy: "You are very beautiful."\n'
            'Girl: ಯಾವತ್ತೂ, ಎಂದಿಗೂ "very beautiful" ಅಂತ ಹೇಳ್ಬೇಡ. ❌\n'
            "Scene 2 -- Flashback, quick cuts.\n"
            'Guy: "very interesting." ❌\n'
            'Guy: "Very beautiful" ❌\n'
            'Guy: "very, very busy." ❌\n'
            "Scene 3: Split screen\n"
            "(Top: the female robot. Bottom: the guest.)\n"
        )
        v3_no_title_meta = {"title": "gdoc_1v3beautiful_t_0"}
        self.assertEqual(
            format_library.detect_catalog_format(v3_no_title_meta, {}, script_text=v3_raw_body),
            "stop_saying",
        )
        self.assertEqual(
            edit_brief.detect_format(v3_no_title_meta, {}, script_text=v3_raw_body),
            "stop_saying",
        )

        # V4: Flight slo-mo hook
        v4_meta = {
            "title": "Reel - W47 - Telugu - AI Only - Skit Micronarrative - long head girl insults man on the flight (slo-mo hook)",
        }
        v4_body = (
            "Scene 1 -- Inside airplane cabin\n"
            "Girl flips her hair in a slo-mo fashion. All of this will happen in slow motion.\n"
            "Man: Excuse me.\n"
            "Scene changes: man doing dishes at home, practices with Miss Nova.\n"
        )
        self.assertEqual(format_library.detect_catalog_format(v4_meta, {}, script_text=v4_body), "skit")
        self.assertEqual(edit_brief.detect_format(v4_meta, {}, script_text=v4_body), "skit")

    # ------------------------------------------------------------------
    # 2. EDL parse & lossless `format_edl` roundtrip
    # ------------------------------------------------------------------
    def test_edl_parse_and_lossless_format_edl_roundtrip(self) -> None:
        """All new W47 zoom tokens, speed/mute/beat flags, and `#audio` directives parse cleanly
        and survive `format_edl` -> `parse_edl` roundtrip with zero field loss or unread lines.
        """
        edl_text = (
            '#src_dir "/tmp/raw"\n'
            "#canvas 1080x1920@30 loudness=-10.5\n"
            "#audio gain_db=+1.5dB music=test.mp3 music_gain_db=-18dB\n"
            "HOOK1 clip.mp4 0.50-1.50 speed=0.5 mute beat z=bell:1.2->1.55->1.0\n"
            "L1 clip.mp4 1.50-3.00 z=zoom_in:1.10 tr=zoom_in\n"
            "L2 clip.mp4 3.00-4.50 z=zoom_out:1.30 tr=zoom_out\n"
            "L3 clip.mp4 4.50-6.00 z=mild_out tr=zoom_blur\n"
        )
        parsed = edl_parser.parse_edl(edl_text)
        segs = parsed.get("segments", [])
        self.assertEqual(len(segs), 4)

        audio = parsed.get("audio") or {}
        self.assertAlmostEqual(float(audio.get("loudness")), -10.5, places=2)
        self.assertAlmostEqual(float(audio.get("gain_db")), 1.5, places=2)
        self.assertEqual(str(audio.get("music")), "test.mp3")
        self.assertAlmostEqual(float(audio.get("music_gain_db")), -18.0, places=2)

        s_hook, s1, s2, s3 = segs
        self.assertAlmostEqual(float(s_hook.get("speed", 1.0)), 0.5, places=3)
        self.assertTrue(common.segment_is_muted(s_hook))
        self.assertTrue(common.segment_is_beat(s_hook))
        self.assertEqual(str(s_hook.get("zoom_style", "")).lower(), "bell")
        self.assertAlmostEqual(float(s_hook.get("zoom", 0.0)), 1.2, places=3)
        self.assertAlmostEqual(float(s_hook.get("zoom_peak", 0.0)), 1.55, places=3)
        self.assertAlmostEqual(float(s_hook.get("zoom_to", 0.0)), 1.0, places=3)

        self.assertAlmostEqual(float(s1.get("zoom", 0.0)), 1.0, places=3)
        self.assertAlmostEqual(float(s1.get("zoom_to", 0.0)), 1.10, places=3)
        self.assertEqual(s1.get("transition"), "zoom_in")

        self.assertAlmostEqual(float(s2.get("zoom", 0.0)), 1.30, places=3)
        self.assertAlmostEqual(float(s2.get("zoom_to", 0.0)), 1.0, places=3)
        self.assertEqual(s2.get("transition"), "zoom_out")

        # `z=mild_out` MUST zoom out (z0 > 1.0, z1 == 1.0), never zoom in!
        self.assertGreater(float(s3.get("zoom", 1.0)), 1.0)
        self.assertAlmostEqual(float(s3.get("zoom_to", 0.0)), 1.0, places=3)
        self.assertEqual(s3.get("transition"), "zoom_blur")

        # Lossless roundtrip via format_edl -> parse_edl
        formatted = edl_parser.format_edl(parsed)
        reparsed = edl_parser.parse_edl(formatted)
        self.assertEqual(len(reparsed.get("segments", [])), 4)

        with tempfile.TemporaryDirectory() as tmp:
            edl_file = Path(tmp) / "plan.edl"
            edl_file.write_text(formatted, encoding="utf-8")
            self.assertEqual(script_order.unread_plan_lines(reparsed, edl_file), [])

        audio_rt = reparsed.get("audio") or {}
        self.assertAlmostEqual(float(audio_rt.get("loudness")), -10.5, places=2)
        self.assertAlmostEqual(float(audio_rt.get("gain_db")), 1.5, places=2)
        self.assertEqual(str(audio_rt.get("music")), "test.mp3")
        self.assertAlmostEqual(float(audio_rt.get("music_gain_db")), -18.0, places=2)

        rt_hook, rt1, rt2, rt3 = reparsed["segments"]
        self.assertAlmostEqual(float(rt_hook.get("speed", 1.0)), 0.5, places=3)
        self.assertTrue(common.segment_is_muted(rt_hook))
        self.assertTrue(common.segment_is_beat(rt_hook))
        self.assertEqual(str(rt_hook.get("zoom_style", "")).lower(), "bell")
        self.assertAlmostEqual(float(rt_hook.get("zoom", 0.0)), 1.2, places=3)
        self.assertAlmostEqual(float(rt_hook.get("zoom_peak", 0.0)), 1.55, places=3)
        self.assertAlmostEqual(float(rt_hook.get("zoom_to", 0.0)), 1.0, places=3)

        self.assertAlmostEqual(float(rt1.get("zoom", 0.0)), 1.0, places=3)
        self.assertAlmostEqual(float(rt1.get("zoom_to", 0.0)), 1.10, places=3)
        self.assertEqual(rt1.get("transition"), "zoom_in")

        self.assertAlmostEqual(float(rt2.get("zoom", 0.0)), 1.30, places=3)
        self.assertAlmostEqual(float(rt2.get("zoom_to", 0.0)), 1.0, places=3)
        self.assertEqual(rt2.get("transition"), "zoom_out")

        self.assertGreater(float(rt3.get("zoom", 1.0)), 1.0)
        self.assertAlmostEqual(float(rt3.get("zoom_to", 0.0)), 1.0, places=3)
        self.assertEqual(rt3.get("transition"), "zoom_blur")

    # ------------------------------------------------------------------
    # 3. Audio loudness target sync (`common`, `style_config`, `verify`)
    # ------------------------------------------------------------------
    def test_audio_loudness_target_sync_and_verify_gain_db(self) -> None:
        """`DEFAULT_LOUDNESS_LUFS == -10.5` is synced across `common`, `style_config`, and `verify`,
        and `verify.run_verification` accepts `target_lufs` + honors `plan['audio']['gain_db']`.
        """
        self.assertAlmostEqual(float(common.DEFAULT_LOUDNESS_LUFS), -10.5, places=2)
        self.assertAlmostEqual(float(style_config.DEFAULT_TUNING["target_loudness_lufs"]), -10.5, places=2)
        self.assertAlmostEqual(float(style_config.get_tuning()["target_loudness_lufs"]), -10.5, places=2)
        self.assertAlmostEqual(float(verify.DEFAULT_LOUDNESS_LUFS), -10.5, places=2)
        self.assertIn("target_lufs", inspect.signature(verify.run_verification).parameters)

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            mp4 = _make_synthetic_clip(tmp_dir / "out.mp4", duration=1.0, fps=25)
            plan_path = tmp_dir / "plan.json"

            def _run_and_capture_target(audio_cfg: dict, explicit_target: float | None = None) -> float:
                plan_obj = {
                    "output": str(mp4),
                    "canvas": {"width": 360, "height": 640, "fps": 25},
                    "audio": audio_cfg,
                    "subs": {"mode": "off"},
                    "segments": [{"id": "L1", "src": str(mp4), "start": 0.0, "end": 1.0}],
                    "marks": [],
                    "sfx": [],
                }
                plan_path.write_text(json.dumps(plan_obj), encoding="utf-8")
                seen: list[float] = []

                def _fake_loudness(_p: Path, target_lufs: float | None = None) -> tuple[bool, float, float]:
                    val = float(common.DEFAULT_LOUDNESS_LUFS if target_lufs is None else target_lufs)
                    seen.append(val)
                    return True, val, -1.3

                with mock.patch.object(verify, "check_loudness", side_effect=_fake_loudness), \
                     mock.patch.object(verify, "check_cuts_and_pauses", return_value=(True, 0, 0, 0, 0)), \
                     mock.patch.object(verify, "check_script_coverage", return_value=(True, 1, 1, 0)), \
                     mock.patch.object(verify, "verify_marks_and_build_contact_sheet", return_value=(True, 0, 0, [], 1)), \
                     mock.patch.object(verify, "check_split_and_hook_rules", return_value=(True, "ok")), \
                     mock.patch.object(verify, "check_subtitles_srt", return_value=(True, "disabled")), \
                     mock.patch.object(verify, "check_picture_timeline", return_value=(True, [], 1)), \
                     mock.patch.object(verify, "build_review_sheet", return_value=None), \
                     mock.patch.object(verify, "check_frames", return_value={
                         "ok": True, "frames": 4, "black": [], "frozen": [],
                         "banner_on_face": [], "lingering_banners": [], "face_crop": [], "no_face": [], "note": "",
                     }):
                    ok = verify.run_verification(plan_path, target_lufs=explicit_target)
                self.assertTrue(ok)
                self.assertEqual(len(seen), 1)
                return seen[0]

            # Default loudness when omitted -> -10.5
            self.assertAlmostEqual(_run_and_capture_target({}), -10.5, places=2)
            # Honors audio.gain_db on top of loudness -> -10.5 + 1.5 = -9.0
            self.assertAlmostEqual(_run_and_capture_target({"loudness": -10.5, "gain_db": 1.5}), -9.0, places=2)
            # Honors explicit target_lufs override (e.g. CLI --loudness)
            self.assertAlmostEqual(
                _run_and_capture_target({"loudness": -11.5, "gain_db": 0.0}, explicit_target=-10.0),
                -10.0,
                places=2,
            )

    # ------------------------------------------------------------------
    # 4. Hook transition cap unblocked & intra-split guard + script_order checks
    # ------------------------------------------------------------------
    def test_hook_transition_cap_unblocked_and_intra_split_still_blocked(self) -> None:
        """3 and 4 hook transitions pass `verify.check_split_and_hook_rules`, while intra-split
        transitions, intra-split whooshes, and split bell zoom remain strictly blocked.
        """
        plan_3tr = {
            "segments": [
                {"id": "H1", "src": "a.mp4", "start": 0.0, "end": 1.0, "speed": 0.5, "mute": True, "layout": "single"},
                {"id": "L1", "src": "b.mp4", "start": 0.0, "end": 2.0, "layout": "single", "transition": "zoom_in"},
                {"id": "L2", "src": "c.mp4", "start": 0.0, "end": 2.0, "layout": "single", "transition": "zoom_out"},
                {"id": "L3", "src": "d.mp4", "start": 0.0, "end": 2.0, "layout": "single", "transition": "whip_left"},
                {"id": "L4", "src": "e.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "transition": "split_reveal"},
            ],
            "sfx": [],
        }
        ok3, msg3 = verify.check_split_and_hook_rules(plan_3tr)
        self.assertTrue(ok3, f"3 hook transitions should pass: {msg3}")

        plan_4tr = {
            "tuning": {"max_hook_transitions": 4},
            "segments": [
                {"id": "H1", "src": "a.mp4", "start": 0.0, "end": 1.5, "layout": "single"},
                {"id": "L1", "src": "b.mp4", "start": 0.0, "end": 1.5, "layout": "single", "transition": "zoom_in"},
                {"id": "L2", "src": "c.mp4", "start": 0.0, "end": 1.5, "layout": "single", "transition": "zoom_out"},
                {"id": "L3", "src": "d.mp4", "start": 0.0, "end": 1.5, "layout": "single", "transition": "zoom_blur"},
                {"id": "L4", "src": "e.mp4", "start": 0.0, "end": 1.5, "layout": "single", "transition": "whip_right"},
                {"id": "L5", "src": "f.mp4", "start": 0.0, "end": 2.0, "layout": "split_v", "transition": "split_reveal"},
            ],
            "sfx": [],
        }
        ok4, msg4 = verify.check_split_and_hook_rules(plan_4tr)
        self.assertTrue(ok4, f"4 hook transitions should pass with tuning cap=4: {msg4}")

        # Intra-split transition MUST STILL FAIL
        plan_bad_split = {
            "segments": [
                {"id": "L4", "src": "e.mp4", "start": 0.0, "end": 2.0, "layout": "split_h"},
                {"id": "L5", "src": "f.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "transition": "whip_left"},
            ],
            "sfx": [],
        }
        ok_bad, msg_bad = verify.check_split_and_hook_rules(plan_bad_split)
        self.assertFalse(ok_bad, "Intra-split transition must remain blocked")
        self.assertIn("intra_split_transition", msg_bad)

        # Intra-split whoosh MUST STILL FAIL
        plan_bad_whoosh = {
            "segments": [
                {"id": "L4", "src": "e.mp4", "start": 0.0, "end": 2.0, "layout": "split_h"},
                {"id": "L5", "src": "f.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "transition": "cut"},
            ],
            "sfx": [{"segment": "L5", "file": "whoosh.wav", "at": 0.10}],
        }
        ok_w, msg_w = verify.check_split_and_hook_rules(plan_bad_whoosh)
        self.assertFalse(ok_w, "Intra-split whoosh must remain blocked")
        self.assertIn("intra_split_whoosh", msg_w)

        # Bell curve peak zoom on split layout MUST be caught by _extract_zoom_pair
        bell_seg = {"id": "S1", "src": "e.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "zoom": 1.0, "zoom_peak": 1.55, "zoom_to": 1.0, "zoom_style": "bell"}
        self.assertEqual(verify._extract_zoom_pair(bell_seg), (1.0, 1.55))
        ok_bell_split, msg_bell_split = verify.check_split_and_hook_rules({"segments": [bell_seg], "sfx": []})
        self.assertFalse(ok_bell_split)
        self.assertIn("excessive_split_zoom", msg_bell_split)

        # script_order.style_report: bell/sudden_out/smooth clean & static z=1.08 NOT double-counted as still
        plan_style = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.0, "layout": "single", "zoom": 1.08},
                {"id": "L2", "src": "b.mp4", "start": 0.0, "end": 2.0, "layout": "single", "zoom": 1.2, "zoom_peak": 1.55, "zoom_to": 1.0, "zoom_style": "bell"},
                {"id": "L3", "src": "c.mp4", "start": 0.0, "end": 2.0, "layout": "single", "zoom": 1.10, "zoom_to": 1.0, "zoom_style": "sudden_out"},
                {"id": "L4", "src": "d.mp4", "start": 0.0, "end": 2.0, "layout": "single", "zoom": 1.0, "zoom_to": 1.06, "zoom_style": "smooth"},
            ],
            "sfx": [],
        }
        notes = script_order.style_report(plan_style)
        # All 4 cuts either move or have static punch-in crop -> 0/4 calm z=1 cuts -> warns still share (<25%)
        self.assertTrue(any("0/4 single-speaker cuts stay still" in n for n in notes), notes)
        self.assertFalse(any("back-to-back rapid zoom" in n for n in notes), notes)

    # ------------------------------------------------------------------
    # 5. Real FFmpeg synthetic render test for slo-mo muted bell hook
    # ------------------------------------------------------------------
    def test_ffmpeg_slomo_muted_bell_hook_render_qc_and_no_ghost_captions(self) -> None:
        """Real FFmpeg render of `speed=0.5 mute beat` + `z=bell:1.20->1.55->1.0` produces exact 2.0s duration,
        passes `verify.check_frames` (zero FROZEN) and `verify.check_cuts_and_pauses` (zero dead-air fail),
        emits zero ghost captions in `subtitles.build_cues`, and skips spoken `script_order` blockers.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            src_clip = _make_synthetic_clip(tmp_dir / "src.mp4", width=360, height=640, fps=25, duration=2.0)

            seg = {
                "id": "HOOK1",
                "src": str(src_clip),
                "start": 0.0,
                "end": 1.0,
                "speed": 0.5,
                "mute": True,
                "beat": True,
                "layout": "single",
                "zoom": 1.20,
                "zoom_peak": 1.55,
                "zoom_to": 1.0,
                "zoom_style": "bell",
            }
            plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "use_hw_accel": False,
                "segments": [seg],
                "marks": [],
                "sfx": [],
            }
            out_mkv = tmp_dir / "hook_seg.mkv"
            exact = layouts.render_segment_layout(seg, plan, 360, 640, 25, out_mkv, draft=True)
            self.assertAlmostEqual(exact, 2.0, places=2)

            # Remux to MP4 container (AAC audio) so verify runs full MP4 checks
            out_mp4 = tmp_dir / "hook_final.mp4"
            subprocess.run(
                [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(out_mkv), "-c:v", "copy", "-c:a", "aac", "-b:a", "128k",
                    str(out_mp4),
                ],
                check=True,
                capture_output=True,
            )

            info = common.probe(out_mp4)
            self.assertAlmostEqual(float(info["duration"]), 2.0, delta=0.08)

            # Deep QC: frames (zero FROZEN, zero BLACK)
            with mock.patch("vision_tracker.detect_faces_in_frame", return_value=[]):
                f_res = verify.check_frames(plan, out_mp4)
            self.assertEqual(f_res["frozen"], [], f"Unexpected FROZEN alarm on slo-mo bell hook: {f_res}")
            self.assertEqual(f_res["black"], [], f"Unexpected BLACK alarm on slo-mo bell hook: {f_res}")
            self.assertTrue(f_res["ok"], f_res)

            # Deep QC: cuts/pauses (zero dead-air fail despite 2.0s muted hook!)
            cuts_ok, clipped_w, long_pauses, _jc, clicks = verify.check_cuts_and_pauses(plan, mp4_path=out_mp4)
            self.assertTrue(cuts_ok, f"Muted slo-mo hook failed cuts check: clipped={clipped_w}, pauses={long_pauses}, clicks={clicks}")
            self.assertEqual(long_pauses, 0)
            self.assertEqual(clipped_w, 0)

            # Zero ghost captions in subtitles.build_cues + muted hook exemption in script_order
            clip_cache = tmp_dir / "clip_cache"
            clip_cache.mkdir()
            fake_words = [
                {"w": "Hello", "start": 0.10, "end": 0.45},
                {"w": "world", "start": 0.46, "end": 0.90},
            ]
            (clip_cache / "hq_words.json").write_text(json.dumps(fake_words), encoding="utf-8")
            with mock.patch("common.clip_work_dir", return_value=clip_cache):
                cues = subtitles.build_cues([seg], [0.0], work_dir=tmp_dir)
                self.assertEqual(cues, [], f"Muted slo-mo hook leaked ghost captions: {cues}")
                self.assertEqual(script_order.seg_tokens(seg), [])
                self.assertEqual(script_order.cut_problems(plan), [])

            # Also confirm check_frames exempts intentional hold_tail freeze (>= 1.5s)
            # while STILL catching a genuinely stuck 2.2s non-hold stream
            stuck_mp4 = _make_synthetic_clip(
                tmp_dir / "stuck.mp4",
                width=360,
                height=640,
                fps=25,
                duration=2.2,
                source_filter="testsrc2=size=360x640:rate=25:duration=2.2,trim=end_frame=1,tpad=stop_mode=clone:stop=54,fps=25",
                with_audio=False,
            )
            stuck_plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "segments": [{"id": "L1", "src": str(stuck_mp4), "start": 0.0, "end": 2.2}],
                "marks": [],
            }
            hold_plan = {
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "segments": [{"id": "L1", "src": str(stuck_mp4), "start": 0.0, "end": 0.4, "hold_tail": 1.8}],
                "marks": [],
            }
            with mock.patch("vision_tracker.detect_faces_in_frame", return_value=[]):
                stuck_res = verify.check_frames(stuck_plan, stuck_mp4)
                hold_res = verify.check_frames(hold_plan, stuck_mp4)
            self.assertTrue(bool(stuck_res["frozen"]), "Genuinely stuck 2.2s video must trigger FROZEN")
            self.assertEqual(hold_res["frozen"], [], f"Intentional hold_tail must be exempted from FROZEN: {hold_res}")

    # ------------------------------------------------------------------
    # 6. Real FFmpeg synthetic Stage 2 transition render test
    # ------------------------------------------------------------------
    def test_ffmpeg_stage2_zoom_transitions_exact_duration_and_no_black_borders(self) -> None:
        """Stage 2 transitions `zoom_in`, `zoom_out`, `zoom_blur` render exact frame duration
        with zero black border frames at transition midpoints.
        """
        import cv2
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            W, H, fps, n_tr = 360, 640, 25, 8
            clip_a = _make_synthetic_clip(
                tmp_dir / "clip_a.mp4", width=W, height=H, fps=fps, duration=0.8, source_filter="smptebars", with_audio=False
            )
            clip_b = _make_synthetic_clip(
                tmp_dir / "clip_b.mp4", width=W, height=H, fps=fps, duration=0.8, source_filter="testsrc2", with_audio=False
            )

            for tr_name in ("zoom_in", "zoom_out", "zoom_blur"):
                self.assertIn(tr_name, render._META_TRANSITIONS, f"{tr_name} missing from render._META_TRANSITIONS")
                tr_fc = render._build_physical_transition_fc(tr_name, idx=1, W=W, H=H, fps=fps, n_tr=n_tr)
                fc = (
                    f"[0:v]trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[oa1];"
                    f"[1:v]trim=start_frame=0:end_frame={n_tr},setpts=N/({fps}*TB),format=yuv420p[ib1];"
                    + tr_fc
                )
                out_mp4 = tmp_dir / f"tr_{tr_name}.mp4"
                cmd = [
                    str(common.FFMPEG), "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(clip_a), "-i", str(clip_b),
                    "-filter_complex", fc,
                    "-map", "[x1]",
                    "-frames:v", str(n_tr),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
                    str(out_mp4),
                ]
                subprocess.run(cmd, check=True, capture_output=True)

                info = common.probe(out_mp4)
                expected_dur = n_tr / float(fps)
                self.assertAlmostEqual(
                    float(info["duration"]),
                    expected_dur,
                    delta=0.05,
                    msg=f"{tr_name} duration mismatch: {info}",
                )

                # Inspect midpoint frame for zero black frames and zero black border bars
                cap = cv2.VideoCapture(str(out_mp4))
                cap.set(cv2.CAP_PROP_POS_FRAMES, n_tr // 2)
                ok_fr, frame = cap.read()
                cap.release()
                self.assertTrue(ok_fr and frame is not None, f"Could not decode midpoint of {tr_name}")
                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                self.assertFalse(verify.is_black_frame(gray), f"{tr_name} midpoint frame is black")

                border = 8
                top_m = float(gray[:border, :].mean())
                bot_m = float(gray[-border:, :].mean())
                left_m = float(gray[:, :border].mean())
                right_m = float(gray[:, -border:].mean())
                for side_name, mean_v in (("top", top_m), ("bottom", bot_m), ("left", left_m), ("right", right_m)):
                    self.assertGreaterEqual(
                        mean_v,
                        18.0,
                        f"{tr_name} midpoint shows dark/black {side_name} border (mean={mean_v:.1f})",
                    )

    # ------------------------------------------------------------------
    # 7. Premiere Pro export checks
    # ------------------------------------------------------------------
    def test_premiere_export_sudden_out_scale_transitions_and_slomo_mute(self) -> None:
        """Premiere export keeps `sudden_out` scale >= 100.0%, never maps `flash_zoom`/`zoom_in`/`zoom_out`
        to Dip to Black, and exports `speed=0.5 mute` with 2.0s timeline span, no linked A1 audio, and `timeremap`.
        """
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            clip = _make_synthetic_clip(tmp_dir / "clip.mp4", width=360, height=640, fps=25, duration=3.0, with_audio=True)
            plan = {
                "output": str(tmp_dir / "reel_w47_test.mp4"),
                "canvas": {"width": 360, "height": 640, "fps": 25},
                "subs": {"mode": "off"},
                "segments": [
                    {
                        "id": "HOOK1",
                        "src": str(clip),
                        "start": 0.0,
                        "end": 1.0,
                        "speed": 0.5,
                        "mute": True,
                        "beat": True,
                        "layout": "single",
                        "zoom": 1.0,
                    },
                    {
                        "id": "L1",
                        "src": str(clip),
                        "start": 0.5,
                        "end": 1.7,
                        "layout": "single",
                        "zoom": 1.10,
                        "zoom_to": 1.0,
                        "zoom_style": "sudden_out",
                        "zoom_at": 0.25,
                        "transition": "zoom_in",
                    },
                    {
                        "id": "L2",
                        "src": str(clip),
                        "start": 1.0,
                        "end": 2.2,
                        "layout": "single",
                        "zoom": 1.0,
                        "transition": "zoom_out",
                    },
                    {
                        "id": "L3",
                        "src": str(clip),
                        "start": 1.2,
                        "end": 2.4,
                        "layout": "single",
                        "zoom": 1.0,
                        "transition": "flash_zoom",
                    },
                ],
                "marks": [],
                "sfx": [],
            }
            plan_path = tmp_dir / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")

            out_dir = tmp_dir / "premiere_out"
            res = premiere_export.export_premiere_project(
                plan=plan,
                plan_path=plan_path,
                out_dir=out_dir,
                work_dir=tmp_dir,
                fps=25,
                width=360,
                height=640,
            )
            xml_path = Path(res["xml"])
            prproj_path = Path(res["prproj"])
            self.assertTrue(xml_path.is_file())
            self.assertTrue(prproj_path.is_file())

            # Validate gzipped .prproj XML is well-formed
            with gzip.open(prproj_path, "rb") as gz:
                ET.fromstring(gz.read())

            tree = ET.parse(xml_path)
            root = tree.getroot()

            # 1) Every Scale parameter value & keyframe must be >= 100.0% (no sudden_out 98.5% black border!)
            scale_vals: list[float] = []
            for param in root.findall(".//parameter"):
                pid = (param.findtext("parameterid") or "").strip().lower()
                if pid == "scale":
                    base_v = param.findtext("value")
                    if base_v is not None:
                        scale_vals.append(float(base_v))
                    for kf in param.findall("keyframe"):
                        kv = kf.findtext("value")
                        if kv is not None:
                            scale_vals.append(float(kv))
            self.assertTrue(scale_vals, "Expected scale parameters in Premiere XML")
            for sv in scale_vals:
                self.assertGreaterEqual(sv, 99.99, f"Under-scaled clip (<100%) detected in Premiere XML: {sv}")

            # 2) flash_zoom / zoom_in / zoom_out MUST NOT map to Dip to Black
            tr_items = root.findall(".//video/track[1]/transitionitem")
            self.assertGreaterEqual(len(tr_items), 3, "Expected transitions on V1")
            for tr_el in tr_items:
                ef_id = (tr_el.findtext("effect/effectid") or "").strip().lower()
                ef_name = (tr_el.findtext("effect/name") or "").strip().lower()
                self.assertNotEqual(ef_id, "dip2black", f"Zoom/flash transition mapped to dip2black: {ef_name}")
                self.assertNotIn("dip to black", ef_name)

            # 3) HOOK1 speed=0.5 mute: 50-frame (2.0s) timeline duration, timeremap XML filter, no A1 clip at [0, 50)
            v1_clips = root.findall(".//video/track[1]/clipitem")
            self.assertGreaterEqual(len(v1_clips), 4)
            hook_clip = v1_clips[0]
            h_start = int(hook_clip.findtext("start") or 0)
            h_end = int(hook_clip.findtext("end") or 0)
            self.assertEqual(h_end - h_start, 50, f"Expected 50 frames (2.0s at 25fps) for 0.5x HOOK1, got {h_end - h_start}")

            hook_effect_ids = [
                (ef.text or "").strip().lower()
                for ef in hook_clip.findall(".//filter/effect/effectid")
            ]
            self.assertIn("timeremap", hook_effect_ids, f"Missing timeremap filter on slo-mo HOOK1: {hook_effect_ids}")

            a1_clips = root.findall(".//audio/track[1]/clipitem")
            for a_el in a1_clips:
                a_start = int(a_el.findtext("start") or 0)
                a_end = int(a_el.findtext("end") or 0)
                self.assertFalse(
                    a_start < 50 and a_end > 0,
                    f"Muted HOOK1 should have no linked A1 audio clip overlapping [0, 50), found [{a_start}, {a_end})",
                )

    # ------------------------------------------------------------------
    # 8. Token budget & `compact_brief()` W47 section preservation
    # ------------------------------------------------------------------
    def test_w47_format_cards_token_budget_and_compact_brief_preserves_sections(self) -> None:
        """W47 `skit` & `stop_saying` catalog cards stay well inside +1,000 tokens (+4,000 B)
        over baseline, and `edit_packet.compact_brief()` preserves their new W47 sections!
        """
        skit_card_bytes = (ROOT / "formats" / "catalog" / "skit.md").stat().st_size
        stop_card_bytes = (ROOT / "formats" / "catalog" / "stop_saying.md").stat().st_size

        # Raw baseline card sizes from Phase 1 audit: skit.md = 4,439 B, stop_saying.md = 2,861 B
        self.assertLessEqual(
            skit_card_bytes - 4439,
            4000,
            f"skit.md grew by {skit_card_bytes - 4439} B (> +4,000 B / +1,000 tok budget)",
        )
        self.assertLessEqual(
            stop_card_bytes - 2861,
            4000,
            f"stop_saying.md grew by {stop_card_bytes - 2861} B (> +4,000 B / +1,000 tok budget)",
        )

        # Compacted brief sizes vs bare compact baselines (skit = 5,977 B, stop_saying = 5,503 B)
        skit_brief = edit_brief.build({"format": "skit", "title": "W47 Skit"}, {"segments": []})
        skit_compact = edit_packet.compact_brief(skit_brief)
        skit_compact_delta = len(skit_compact.encode("utf-8")) - 5977
        self.assertLessEqual(
            skit_compact_delta,
            4000,
            f"Compacted skit brief grew by {skit_compact_delta} B (> +4,000 B / +1,000 tok budget)",
        )
        self.assertIn("## W47 Approved Edit Pattern", skit_compact)
        self.assertIn("4-Act Pacing", skit_compact)
        self.assertIn("bell", skit_compact)

        stop_brief = edit_brief.build({"format": "stop_saying", "title": "W47 Never say this"}, {"segments": []})
        stop_compact = edit_packet.compact_brief(stop_brief)
        stop_compact_delta = len(stop_compact.encode("utf-8")) - 5503
        self.assertLessEqual(
            stop_compact_delta,
            4000,
            f"Compacted stop_saying brief grew by {stop_compact_delta} B (> +4,000 B / +1,000 tok budget)",
        )
        self.assertIn("## Transitions (Max 5)", stop_compact)
        self.assertIn("zoom_blur", stop_compact)
        self.assertIn("green outer glow", stop_compact)


if __name__ == "__main__":
    unittest.main()
