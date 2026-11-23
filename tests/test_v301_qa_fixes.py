#!/usr/bin/env python3
"""Comprehensive regression suite for Zeno v3.0.1 production QA fixes.

Covers all 6 production QA bug clusters:
  1. Uniform subtitle typography across code-switched Indic+English reels (weight, family, size, box height, wordPop).
  2. Strikethrough banner & wrong-mark caption suppression + audio_end clamp + verify.py SRT QC parity.
  3. Split-screen continuity hard gate (no mid-split drop to single) + sticky split script parsing & format routing.
  4. Split-screen proportion, eyeline alignment, canonical Dialogue/Silent clip locking, fake-box & B-roll guards.
  5. Gap/pause tightening (pass order, de-spiked acoustics, head-trim on at=0 flashcards, tr="cut", SFX dedup, -4.5 dB wrong buzzer).
  6. Madhu's uniform flashcards across scenes & micronarrative y=65% (single) vs y=50% (split_v).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import edl_parser
import flashcard_placer
import format_library
import layouts
import prepare
import render
import script_order
import subtitles
import verify
import vision_tracker


class TestBug1UniformTypography(unittest.TestCase):
    """Bug 1: Telugu/Kannada + English reels must keep uniform fontWeight, harmonized fontFamily, fontSize & box height."""

    def test_bilingual_telugu_english_render_cues_uniform_font_and_box_height(self) -> None:
        if not subtitles.ensure_ready():
            self.skipTest("node-canvas not available")
        from PIL import Image

        style = subtitles.resolve_style("White Box") or subtitles.styles()[0]
        cues = [
            {
                "startTime": 0.0,
                "endTime": 1.2,
                "text": "I am having a doubt",
                "words": [
                    {"word": "I", "start": 0.0, "end": 0.2},
                    {"word": "am", "start": 0.2, "end": 0.4},
                    {"word": "having", "start": 0.4, "end": 0.7},
                    {"word": "a", "start": 0.7, "end": 0.85},
                    {"word": "doubt", "start": 0.85, "end": 1.2},
                ],
            },
            {
                "startTime": 1.3,
                "endTime": 2.6,
                "text": "మీకు doubtలో ఉంది",
                "words": [
                    {"word": "మీకు", "start": 1.3, "end": 1.7},
                    {"word": "doubtలో", "start": 1.7, "end": 2.15},
                    {"word": "ఉంది", "start": 2.15, "end": 2.6},
                ],
            },
            {
                "startTime": 2.7,
                "endTime": 3.8,
                "text": "I have a question",
                "words": [
                    {"word": "I", "start": 2.7, "end": 2.9},
                    {"word": "have", "start": 2.9, "end": 3.1},
                    {"word": "a", "start": 3.1, "end": 3.3},
                    {"word": "question", "start": 3.3, "end": 3.8},
                ],
            },
        ]
        with tempfile.TemporaryDirectory() as td:
            out_dir = Path(td)
            manifest = subtitles.render_frames(
                cues,
                style,
                1080,
                1920,
                out_dir,
                y_percent=65.0,
                fps=25,
                return_meta=True,
                font_size=54,
            )
            frames = manifest["frames"]
            self.assertGreater(len(frames), 0)
            weights = {f.get("fontWeight") for f in frames if f.get("fontWeight") is not None}
            sizes = {f.get("fontSize") for f in frames if f.get("fontSize") is not None}
            families = {f.get("fontFamily") for f in frames if f.get("fontFamily")}
            self.assertEqual(weights, {700}, f"fontWeight flipped across bilingual cues: {weights}")
            self.assertEqual(len(sizes), 1, f"fontSize jumped across normal cues: {sizes}")
            self.assertEqual(len(families), 1, f"fontFamily jumped across bilingual cues: {families}")

            # Verify rendered non-transparent box height does not jump wildly between English-only and mixed Telugu cues
            heights_by_cue: dict[int, list[int]] = {}
            for f in frames:
                im = Image.open(f["path"]).convert("RGBA")
                alpha = im.getchannel("A")
                bb = alpha.getbbox()
                if bb:
                    heights_by_cue.setdefault(int(f["captionIndex"]), []).append(bb[3] - bb[1])
            cue_medians = [sorted(vs)[len(vs) // 2] for vs in heights_by_cue.values() if vs]
            self.assertEqual(len(cue_medians), 3)
            ratio = max(cue_medians) / max(1, min(cue_medians))
            self.assertLessEqual(ratio, 1.15, f"Box height jumped across bilingual cues: {cue_medians} (ratio={ratio:.2f})")


class TestBug2StrikethroughCaptionSuppression(unittest.TestCase):
    """Bug 2: Captions must suppress during strikethrough banners & audio_end tails, and verify.py SRT QC must agree."""

    def test_strikethrough_windows_suppress_cues_and_pass_verify_srt_qc(self) -> None:
        seg = {
            "id": "L1",
            "src": "clip.mp4",
            "start": 0.0,
            "end": 3.0,
            "audio_end": 2.3,
            "speaker": "A",
            "banners": [{"text": "Save me", "at": 0.6, "duration": 1.1, "strike": True}],
        }
        plan = {
            "segments": [seg],
            "subs": {"style": "White Box", "y": 65},
            "marks": [],
        }
        wins = subtitles.segment_strikethrough_windows(seg, 0.0, 3.0, plan)
        self.assertEqual(len(wins), 1)
        self.assertAlmostEqual(wins[0][0], 0.6, places=2)
        self.assertAlmostEqual(wins[0][1], 1.7, places=2)

        words = [
            {"w": "Wait", "start": 0.10, "end": 0.45},
            {"w": "save", "start": 0.70, "end": 1.10},
            {"w": "me", "start": 1.15, "end": 1.55},
            {"w": "help", "start": 1.85, "end": 2.20},
            {"w": "ghost", "start": 2.50, "end": 2.90},  # past audio_end=2.3 -> must not appear!
        ]
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            cdir = wd / "clip"
            cdir.mkdir()
            (cdir / "words.json").write_text(json.dumps(words), encoding="utf-8")
            (cdir / "clip_caches.json").write_text(json.dumps({"clip.mp4": str(cdir)}), encoding="utf-8")
            (wd / "clip_caches.json").write_text(json.dumps({"clip.mp4": str(cdir)}), encoding="utf-8")
            cues = subtitles.build_cues([seg], [0.0], [], work_dir=wd, plan=plan)

        # No cue may overlap [0.6, 1.7] or extend past audio_end=2.3
        all_texts = [c["text"] for c in cues]
        self.assertIn("Wait", " ".join(all_texts))
        self.assertIn("help", " ".join(all_texts))
        self.assertNotIn("save", " ".join(all_texts).lower())
        self.assertNotIn("ghost", " ".join(all_texts).lower())
        for c in cues:
            ov = min(float(c["endTime"]), 1.7) - max(float(c["startTime"]), 0.6)
            self.assertLessEqual(ov, 0.01, f"Cue overlapped strikethrough banner: {c}")
            self.assertLessEqual(float(c["endTime"]), 2.35, f"Cue bled past audio_end: {c}")

        # Full-segment banner suppression must pass verify.check_subtitles_srt without false [SUBS FAIL]
        full_banner_seg = {
            "id": "L2",
            "src": "clip.mp4",
            "start": 0.0,
            "end": 2.6,
            "speaker": "A",
            "banners": [{"text": "I am having a doubt", "at": 0.0, "duration": 2.6, "strike": True}],
        }
        full_plan = {"segments": [full_banner_seg], "subs": {"style": "White Box"}}
        with tempfile.TemporaryDirectory() as td:
            ok, msg = verify.check_subtitles_srt(full_plan, Path(td) / "out.mp4")
            self.assertTrue(ok, f"Expected banner-suppressed segment to pass SRT QC: {msg}")


class TestBug3SplitScreenContinuity(unittest.TestCase):
    """Bug 3: Split-screen grammar reels must route to split_screen, keep sticky layout, and hard-block mid-split single drops."""

    def test_sticky_split_script_and_format_routing(self) -> None:
        script_txt = """Visual: Split screen — Top: Female Robot | Bottom: Ramya in kitchen

Scene 1
Ramya: I am having a doubt. [wrong]
Female Robot: No, say I have a question.

Scene 2
Ramya: Return back my book. [wrong]
Female Robot: Say return my book.
Ramya: I am agree. [wrong]
Female Robot: Say I agree.
"""
        with tempfile.TemporaryDirectory() as td:
            sp = Path(td) / "script.txt"
            sp.write_text(script_txt, encoding="utf-8")
            meta, lines = prepare.parse_script_file(sp)
            self.assertEqual(len(lines), 6)
            hints = [l.get("layout_hint") for l in lines]
            self.assertTrue(all(h in ("split_h", "split_v") for h in hints), f"Scene 2 lost sticky split hint: {hints}")
            fmt_id = format_library.detect_catalog_format(meta, lines)
            self.assertEqual(fmt_id, "split_screen", f"Misclassified split grammar script as {fmt_id}")

    def test_split_continuity_blocks_mid_split_single_drop(self) -> None:
        plan = {
            "segments": [
                {"id": "L1", "src": "r.mp4", "start": 0.0, "end": 2.0, "layout": "split_h", "speaker": "Robot"},
                {"id": "L2", "src": "h.mp4", "start": 0.0, "end": 2.0, "layout": "single", "speaker": "Ramya"},
                {"id": "L3", "src": "r.mp4", "start": 2.0, "end": 4.0, "layout": "split_h", "speaker": "Robot"},
            ]
        }
        s_lines = [
            {"id": "L1", "speaker": "Robot", "text": "Hello", "layout_hint": "split_h"},
            {"id": "L2", "speaker": "Ramya", "text": "Hi", "layout_hint": "split_h"},
            {"id": "L3", "speaker": "Robot", "text": "Great", "layout_hint": "split_h"},
        ]
        with tempfile.TemporaryDirectory() as td:
            wd = Path(td)
            probs = script_order.split_continuity_problems(plan, s_lines, wd)
            self.assertTrue(any("L2" in p for p in probs), f"Expected L2 mid-split single drop to block: {probs}")

            # Accepted via decisions.txt -> filtered cleanly in full_gate / read_accepted
            (wd / "decisions.txt").write_text("accept L2 intentional cutaway\n", encoding="utf-8")
            accepted = script_order.read_accepted(wd)
            remaining = [p for p in probs if p.split()[0].upper() not in {k.upper() for k in accepted}]
            self.assertEqual(remaining, [], f"Accepted L2 should be cleared by decisions.txt: {remaining}")


class TestBug4SplitAlignmentAndCanonicalLock(unittest.TestCase):
    """Bug 4: Canonical Dialogue/Silent matching, fake-box guard, eyeline+fh parity even when |dy0|>=0.08, and B-roll guard."""

    def test_canonical_split_clip_matches_dialogue_and_double_space_silent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for name in (
                "ROBOT Dialogue Video.mp4",
                "ROBOT  Silent Video.mp4",
                "Woman With Dialogue.mp4",
                "Woman Without Dialogue.mp4",
            ):
                (d / name).write_bytes(b"0")
            self.assertEqual(
                vision_tracker._canonical_split_clip(d / "ROBOT Dialogue Video.mp4").name,
                "ROBOT  Silent Video.mp4",
            )
            self.assertEqual(
                vision_tracker._canonical_split_clip(d / "ROBOT  Silent Video.mp4").name,
                "ROBOT  Silent Video.mp4",
            )
            self.assertEqual(
                vision_tracker._canonical_split_clip(d / "Woman With Dialogue.mp4").name,
                "Woman Without Dialogue.mp4",
            )

    def test_paired_split_matches_fh_and_eyeline_when_y0_diff_ge_008_and_guards_broll(self) -> None:
        vis_robot = {
            "confidence": 0.92,
            "face_count": 1,
            "bbox_norm": [0.34, 0.14, 0.66, 0.44],  # fh=0.30, y0=0.14
            "focus": [0.50, 0.27],
            "faces": [{"confidence": 0.92, "bbox_norm": [0.34, 0.14, 0.66, 0.44], "eye_center_norm": [0.50, 0.26]}],
        }
        vis_human = {
            "confidence": 0.91,
            "face_count": 1,
            "bbox_norm": [0.38, 0.26, 0.62, 0.48],  # fh=0.22, y0=0.26 (|y0 diff| = 0.12 >= 0.08!)
            "focus": [0.50, 0.35],
            "faces": [{"confidence": 0.91, "bbox_norm": [0.38, 0.26, 0.62, 0.48], "eye_center_norm": [0.50, 0.35]}],
        }
        vis_broll = {
            "confidence": 0.72,
            "face_count": 1,
            "bbox_norm": [0.46, 0.28, 0.54, 0.36],  # tiny distant B-roll face fh=0.08 < 0.13
            "focus": [0.50, 0.32],
            "faces": [{"confidence": 0.72, "bbox_norm": [0.46, 0.28, 0.54, 0.36]}],
        }
        fake_map = {"robot.mp4": vis_robot, "human.mp4": vis_human, "broll.mp4": vis_broll}

        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            for fn in fake_map:
                (d / fn).write_bytes(b"0")
            with (
                mock.patch.object(vision_tracker, "_canonical_split_clip", side_effect=lambda p: Path(p)),
                mock.patch.object(vision_tracker, "analyze_clip_vision", side_effect=lambda p: fake_map[Path(p).name]),
            ):
                c_r, c_h = vision_tracker.compute_paired_split_crop_filters(
                    d / "robot.mp4", d / "human.mp4", 1080, 1920, 1080, 1920, 540, 1920, 540, 1920
                )
                self.assertNotIn("fillborders", c_r["filter"])
                self.assertNotIn("fillborders", c_h["filter"])
                self.assertTrue(c_h.get("is_talking_head"))
                # Human face (fh=0.22) must zoom up toward Robot (fh=0.30) even though |y0_r - y0_h| = 0.12 >= 0.08
                self.assertGreater(float(c_h["extra_zoom"]), 1.15, f"Expected human extra_zoom > 1.15, got {c_h}")
                # B-roll in split_v must NEVER zoom when paired with talking head
                c_br, _c_th = vision_tracker.compute_paired_split_crop_filters(
                    d / "broll.mp4", d / "human.mp4", 1080, 1920, 1080, 1920, 1080, 960, 1080, 960
                )
                self.assertEqual(float(c_br["extra_zoom"]), 1.0, f"B-roll must stay at extra_zoom=1.0: {c_br}")


class TestBug5GapsSfxDedupAndBuzzerVolume(unittest.TestCase):
    """Bug 5: Head-trim on at=0 flashcards, tr='cut' target gap, plosive-burst preservation, SFX dedup, -4.5 dB wrong buzzer."""

    def test_tighten_joins_trims_at0_flashcard_head_and_preserves_final_plosive(self) -> None:
        # Seg A ends in "slipped." (0.20..0.75) with stop closure 0.45..0.59 (-70 dB) and 10ms -pt burst at 0.62 (-30 dB)
        # Seg B starts with 0.30s leading silence + at=0.0 flashcard + tr="cut"
        env_a = [
            (round(k * 0.01, 2), -25.0 if (20 <= k <= 44 or k == 62) else -70.0)
            for k in range(150)
        ]
        env_b = [
            (round(k * 0.01, 2), -25.0 if 30 <= k <= 95 else -70.0)
            for k in range(120)
        ]
        ws_a = [{"w": "slipped.", "start": 0.20, "end": 0.74}]
        ws_b = [{"w": "hello", "start": 0.30, "end": 0.90}]
        plan = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.10, "end": 1.20, "speaker": "A"},
                {
                    "id": "L2",
                    "src": "b.mp4",
                    "start": 0.00,
                    "end": 1.00,
                    "speaker": "A",
                    "transition": "cut",
                    "flashcards": [{"text": "Hi | హాయ్", "en": "Hi", "native": "హాయ్", "at": 0.0}],
                },
            ]
        }
        with (
            mock.patch.object(script_order, "_env_of", side_effect=lambda s: env_a if s["src"] == "a.mp4" else env_b),
            mock.patch.object(script_order, "_words_of", side_effect=lambda s: ws_a if s["src"] == "a.mp4" else ws_b),
        ):
            moved = script_order.tighten_joins(plan)
        self.assertGreater(moved, 0)
        # Plosive release at 0.62s + word end 0.74s MUST stay preserved (never chopped at 0.50s!)
        self.assertGreaterEqual(float(plan["segments"][0]["end"]), 0.78)
        # Seg B head with at=0.0 flashcard MUST trim leading 0.30s silence and keep flashcard pinned at 0.0
        self.assertGreaterEqual(float(plan["segments"][1]["start"]), 0.20)
        self.assertEqual(float(plan["segments"][1]["flashcards"][0]["at"]), 0.0)
        self.assertEqual(verify.DEAD_AIR_THRESHOLD, 0.48)

    def test_edl_sfx_dedup_and_wrong_buzzer_gain_minus_4_5(self) -> None:
        edl_txt = (
            "#output out.mp4\n"
            "#canvas 1080x1920@25\n"
            'L1 "clip.mp4" 0.0-2.0 sp=A sfx:whoosh.wav@0.1/-4 sfx:whoosh.wav@0.1/-4\n'
        )
        plan = edl_parser.parse_edl(edl_txt)
        sfx_entries = [s for s in plan.get("sfx", []) if s.get("segment") == "L1"]
        self.assertEqual(len(sfx_entries), 1, f"Duplicate inline SFX not deduplicated: {sfx_entries}")

        # Default wrong buzzer gain must calibrate cleanly to -4.5 dB (not clamped to -4.0 or +1.0)
        cal_wrong = render._calibrate_sfx_gain_db(Path("sn_wrong.wav"), None, category="mark", default_gain_db=-4.5)
        self.assertAlmostEqual(cal_wrong, -4.5, places=2)
        cal_standalone = render._calibrate_sfx_gain_db(Path("buzzer.wav"), None)
        self.assertAlmostEqual(cal_standalone, -4.5, places=2)


class TestBug6MadhuUniformFlashcardsAndMicronarrativeY(unittest.TestCase):
    """Bug 6: Uniform flashcard style/size across scenes & micronarrative single=65% vs split_v=50%."""

    def test_flashcard_style_stays_clean_and_uniform_across_scenes(self) -> None:
        self.assertEqual(flashcard_placer._resolve_card_style("body_center", True, None), "clean")
        self.assertEqual(flashcard_placer._resolve_card_style("bottom_safe", True, "auto"), "clean")
        self.assertEqual(flashcard_placer._resolve_card_style("top_empty", False, "minimal"), "minimal")

        # Two cards across two single-frame segments with slightly different face boxes -> uniform (w, h, compact, style)
        plan = {
            "segments": [
                {"id": "L1", "src": "a.mp4", "start": 0.0, "end": 2.0, "layout": "single",
                 "flashcards": [{"en": "I have a question", "native": "నాకు సందేహం ఉంది", "pos": "auto"}]},
                {"id": "L2", "src": "b.mp4", "start": 0.0, "end": 2.0, "layout": "single",
                 "flashcards": [{"en": "Return my book", "native": "నా పుస్తకం ఇవ్వండి", "pos": "auto"}]},
            ]
        }
        with (
            tempfile.TemporaryDirectory() as td,
            mock.patch.object(flashcard_placer, "_render_cards_into_dir"),
        ):
            res = flashcard_placer.place_timeline_flashcards(
                plan,
                plan["segments"],
                [0.0, 2.0],
                [2.0, 2.0],
                4.0,
                Path(td) / "missing.mkv",
                1080,
                1920,
                Path(td),
            )
            cards = res["cards"]
            self.assertEqual(len(cards), 2)
            self.assertEqual((cards[0]["w"], cards[0]["h"], cards[0]["compact"], cards[0]["style"]),
                             (cards[1]["w"], cards[1]["h"], cards[1]["compact"], cards[1]["style"]))

    def test_micronarrative_single_before_split_v_uses_y65_even_with_y50_header(self) -> None:
        segs = [
            {"id": "L1", "src": "hook.mp4", "start": 0.0, "end": 3.0, "layout": "single"},
            {"id": "L2", "src": "teach.mp4", "start": 0.0, "end": 5.0, "layout": "split_v", "peer": "p.mp4@0-5@top"},
        ]
        cues = [
            {"startTime": 0.2, "endTime": 2.8, "text": "Hook line", "segIndex": 0},
            {"startTime": 3.2, "endTime": 7.5, "text": "Split line", "segIndex": 1},
        ]
        default_y = subtitles.resolve_safe_y_percent(
            {"style": "auto", "y": 50},
            segs,
            [0.0, 3.0],
            8.0,
            1080,
            1920,
            cues=cues,
        )
        single_y = (cues[0].get("style") or {}).get("positionY", default_y)
        split_y = (cues[1].get("style") or {}).get("positionY", default_y)
        self.assertAlmostEqual(float(single_y), 65.0, places=1)
        self.assertAlmostEqual(float(split_y), 50.0, places=1)

        # Pure-single reel with explicit y=50 must still honor explicit y=50
        pure_segs = [{"id": "L1", "src": "hook.mp4", "start": 0.0, "end": 3.0, "layout": "single"}]
        pure_cues = [{"startTime": 0.2, "endTime": 2.8, "text": "Explicit", "segIndex": 0}]
        pure_y = subtitles.resolve_safe_y_percent(
            {"style": "auto", "y": 50},
            pure_segs,
            [0.0],
            3.0,
            1080,
            1920,
            cues=pure_cues,
        )
        self.assertAlmostEqual(float((pure_cues[0].get("style") or {}).get("positionY", pure_y)), 50.0, places=1)


if __name__ == "__main__":
    unittest.main()
