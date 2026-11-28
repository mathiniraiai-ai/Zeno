#!/usr/bin/env python3
"""v3.0.9 visual/plan QC regressions (W47 Telugu "Don't Come Near Me" jets reel).

Covers the four checks added to `scripts/verify.py` (+ the pre-render hook in `scripts/render.py`):
1. LAYOUT-vs-FORMAT: split layouts hard-block only when the DECLARED format's catalog says "never split".
2. SPEAKER FACE: the speaking character's own split panel must show a usable face; trimmable lead/tail
   faceless runs block with an exact in/out, faceless runs during speech only warn.
3. OPENING DEAD AIR: auto-trim the opening cut to first word - 0.05 s (noise-robust, word based).
4. FINAL TAIL: warn (never block) when the last word has < 0.06 s after it.

Real-data tests run only when the jets work project / delivered reel are present on this machine.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import edl_parser  # noqa: E402
import verify  # noqa: E402

JETS_WORK = ROOT / "work" / "gdoc_1sEbveyz37BA_t_0"
JETS_REEL = (
    Path.home()
    / "Downloads"
    / "Zeno Outs"
    / "W47 - Telugu - Don't Come Near Me"
    / "reel_W47 - Telugu - Don't Come Near Me.mp4"
)

JETS_LIKE_EDL = """#output /tmp/zeno_v309_visual_qc/reel_test.mp4
#subs auto y=65
#src_dir /tmp/zeno_v309_visual_qc
#canvas 720x1280@24 loudness=-10.5 preset=hybrid mark_pack=flash

L4 1.mp4 0.00-4.01 layout=split_h z=1 focus=0.5,0.24 sp=Man slot=left peer="Female Robot:robot_listener.mp4"@0.12-3.85@right
L5 1.mp4 3.99-6.72 layout=split_h z=1 focus=0.5,0.24 sp="Female Robot" slot=right peer=Man:man_listener.mp4@0.27-2.62@left
L6 1.mp4 7.25-8.89 layout=split_h z=1 focus=0.5,0.24 sp=Man slot=left peer="Female Robot:robot_listener.mp4"@3.85-5.41@right
"""

# Word timings of 1.mp4 as measured in the jets project (`facts.md`): first word at 1.745 s.
JETS_WORDS = [
    {"w": "దూరం", "start": 1.745, "end": 2.021},
    {"w": "పో!", "start": 2.041, "end": 2.435},
    {"w": "రాకు!", "start": 2.94, "end": 3.60},
    {"w": "అయితే", "start": 4.12, "end": 4.48},
    {"w": "చూపించు!", "start": 5.60, "end": 6.30},
    {"w": "నాకు", "start": 7.41, "end": 7.70},
    {"w": "రాదు!", "start": 8.11, "end": 8.80},
]


def _speech(words, spans=None, duration=10.03):
    return {"words": list(words), "spans": list(spans or []), "duration": duration}


class _TmpProject:
    """Temp project folder with plan.edl (+ optional script_analysis.md / decisions.txt)."""

    def __init__(self, edl: str, fmt_line: str | None = None, decisions: str = "") -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="zeno_v309_vqc_"))
        self.plan_path = self.dir / "plan.edl"
        self.plan_path.write_text(edl, encoding="utf-8")
        if fmt_line is not None:
            (self.dir / "script_analysis.md").write_text(fmt_line + "\n\nnotes\n", encoding="utf-8")
        if decisions:
            (self.dir / "decisions.txt").write_text(decisions, encoding="utf-8")

    def plan(self):
        p = edl_parser.load_edl(self.plan_path)
        p["_plan_dir"] = str(self.dir)
        return p

    def cleanup(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)


class LayoutVsFormatTests(unittest.TestCase):
    def _check(self, fmt_line, edl=JETS_LIKE_EDL, decisions=""):
        prj = _TmpProject(edl, fmt_line, decisions)
        try:
            return verify.check_layout_vs_format(prj.plan(), prj.dir)
        finally:
            prj.cleanup()

    def test_live_conversation_with_splits_blocks_every_line_at_once(self) -> None:
        ok, bad, msg = self._check("Format: live_conversation (2.3 Interactive scene)")
        self.assertFalse(ok)
        self.assertEqual(bad, ["L4 split_h", "L5 split_h", "L6 split_h"])
        self.assertIn("[LAYOUT FAIL]", msg)
        self.assertIn("layout=single", msg)
        self.assertIn("L4 split_h, L5 split_h, L6 split_h", msg)

    def test_story_pitch_with_split_blocks(self) -> None:
        ok, bad, _ = self._check("Format: story_pitch (5.1 Story Pitch)")
        self.assertFalse(ok)
        self.assertEqual(len(bad), 3)

    def test_accept_in_decisions_authorises_a_scripted_split(self) -> None:
        ok, bad, _ = self._check(
            "Format: live_conversation (2.2 tablet)",
            decisions="accept L4 scripted split\naccept L5 scripted split\naccept L6 scripted split\n",
        )
        self.assertTrue(ok)
        self.assertEqual(bad, [])
        ok2, bad2, _ = self._check("Format: live_conversation (2.2 tablet)", decisions="accept L5 scripted split\n")
        self.assertFalse(ok2)
        self.assertEqual(bad2, ["L4 split_h", "L6 split_h"])

    def test_formats_that_allow_splits_never_block(self) -> None:
        for fmt in (
            "Format: skit (1.1 Skit-Based Micro-Narrative)",
            "Format: split_screen (3.1 Split screen)",
            "Format: stop_saying (1.4 Stop Saying / Never Say Micro-Narrative)",
            "Format: translate_drill (3.4 With flash card)",
            "Format: scrolling_text (4.2 Two people in split)",
            "Format: direct_hook (1.2 Direct Hook Micro-Narrative)",
        ):
            ok, bad, msg = self._check(fmt)
            self.assertTrue(ok, fmt)
            self.assertEqual((bad, msg), ([], ""), fmt)

    def test_single_only_plan_on_live_conversation_passes(self) -> None:
        edl = "\n".join(
            ln.replace("layout=split_h", "layout=single").split(" slot=")[0] for ln in JETS_LIKE_EDL.splitlines()
        )
        ok, bad, _ = self._check("Format: live_conversation (2.3 Interactive scene)", edl=edl)
        self.assertTrue(ok)
        self.assertEqual(bad, [])

    def test_missing_or_unknown_format_passes(self) -> None:
        self.assertTrue(self._check(None)[0])
        self.assertTrue(self._check("Format: something_new (x)")[0])

    @unittest.skipUnless((JETS_WORK / "plan.edl").is_file(), "jets work project not on this machine")
    def test_real_jets_plan(self) -> None:
        real_edl = (JETS_WORK / "plan.edl").read_text(encoding="utf-8")
        # As delivered the job declared `skit` (splits allowed) -> no layout block.
        ok, _, _ = self._check("Format: skit (1.1 Skit-Based Micro-Narrative)", edl=real_edl)
        self.assertTrue(ok)
        # Declared as live_conversation, every one of its 16 split cuts is listed in ONE message.
        ok2, bad2, msg2 = self._check("Format: live_conversation (2.3 Interactive scene)", edl=real_edl)
        self.assertFalse(ok2)
        self.assertEqual(len(bad2), 16)
        self.assertIn("L16 split_h", msg2)


class SpeakerFaceTests(unittest.TestCase):
    SEG = {
        "id": "L4",
        "src": "/tmp/zeno_v309_visual_qc/1.mp4",
        "start": 0.0,
        "end": 4.01,
        "layout": "split_h",
        "speaker": "Man",
        "slot": "left",
    }

    def _findings(self, seg, samples, words=JETS_WORDS):
        cache = {str(seg["src"]): _speech(words)}
        dur = float(seg["end"]) - float(seg["start"])
        return verify._speaker_face_findings(seg, seg["speaker"], seg["slot"], samples, 0.0, dur, {}, cache)

    @staticmethod
    def _grid(dur, faceless):
        out, t = [], 0.2
        while t < dur - 0.2:
            out.append((round(t, 2), not faceless(t)))
            t += 0.25
        return out

    def test_usable_face_thresholds(self) -> None:
        real = {"bbox_norm": [0.18, 0.13, 0.80, 0.44], "confidence": 0.91}
        speck = {"bbox_norm": [0.72, 0.50, 0.87, 0.58], "confidence": 0.91}  # pilot inside a wide jet shot
        ghost = {"bbox_norm": [0.01, 0.85, 0.50, 0.99], "confidence": 0.51}  # low-confidence edge hit
        self.assertTrue(verify._speaker_face_usable([real]))
        self.assertFalse(verify._speaker_face_usable([speck]))
        self.assertFalse(verify._speaker_face_usable([ghost]))
        self.assertFalse(verify._speaker_face_usable([]))

    def test_leading_faceless_wide_shot_blocks_with_exact_in_point(self) -> None:
        samples = self._grid(4.01, lambda t: t < 1.5)  # jets wide shot until 1.5 s, speech from 1.745 s
        blocks, warns = self._findings(self.SEG, samples)
        self.assertEqual(len(blocks), 1)
        self.assertEqual(warns, [])
        self.assertIn("L4 left panel (Man)", blocks[0])
        self.assertIn("start the cut at 1.69", blocks[0])

    def test_faceless_while_speaking_only_warns(self) -> None:
        samples = self._grid(4.01, lambda t: 2.4 <= t < 3.2)
        blocks, warns = self._findings(self.SEG, samples)
        self.assertEqual(blocks, [])
        self.assertEqual(len(warns), 1)
        self.assertIn("while Man speaks", warns[0])

    def test_trailing_faceless_after_last_word_blocks_with_exact_out_point(self) -> None:
        seg = dict(self.SEG, start=4.0, end=7.3, speaker="Female Robot", slot="right", id="L5")
        samples = self._grid(3.3, lambda t: t >= 2.6)  # src >= 6.6, last word ends 6.30
        blocks, warns = self._findings(seg, samples, words=[w for w in JETS_WORDS if 4.0 <= w["start"] < 7.0])
        self.assertEqual(len(blocks), 1, (blocks, warns))
        self.assertIn("end the cut at 6.38", blocks[0])

    def test_single_faceless_sample_and_healthy_cut_are_silent(self) -> None:
        self.assertEqual(self._findings(self.SEG, self._grid(4.01, lambda t: abs(t - 2.2) < 0.01)), ([], []))
        self.assertEqual(self._findings(self.SEG, self._grid(4.01, lambda t: False)), ([], []))

    @unittest.skipUnless(JETS_REEL.is_file() and (JETS_WORK / "plan.edl").is_file(), "delivered jets reel not present")
    def test_real_jets_reel_flags_l4_and_warns_l16(self) -> None:
        _, plan = verify.load_plan(JETS_WORK / "plan.edl")
        plan["_plan_dir"] = str(JETS_WORK)
        fr = verify.check_frames(plan, JETS_REEL)
        self.assertFalse(fr["ok"])
        self.assertEqual(len(fr["speaker_face"]), 1, fr["speaker_face"])
        self.assertTrue(fr["speaker_face"][0].startswith("L4 left panel (Man)"))
        self.assertIn("start the cut at 1.69", fr["speaker_face"][0])
        warned = {w.split()[0] for w in fr["speaker_face_warnings"]}
        self.assertIn("L16", warned)
        self.assertIn("L9", warned)
        # Pre-existing checks are unchanged on this reel.
        self.assertEqual((fr["black"], fr["frozen"], fr["split_alignment"]), ([], [], []))


class OpeningAndTailTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prj = _TmpProject(JETS_LIKE_EDL, "Format: skit (1.1 Skit-Based Micro-Narrative)")
        self.data = _speech(JETS_WORDS, spans=[(1.7, 3.6), (4.1, 6.3), (7.3, 8.8)])
        self.patch = mock.patch.object(verify, "_clip_speech_data", side_effect=lambda src, plan=None: self.data)
        self.patch.start()

    def tearDown(self) -> None:
        self.patch.stop()
        self.prj.cleanup()

    def test_noisy_opening_is_auto_trimmed_and_written_back(self) -> None:
        plan = edl_parser.load_edl(self.prj.plan_path)
        note = verify.trim_leading_dead_air(plan, self.prj.plan_path)
        self.assertIn("[SNAP] Opening dead air trimmed: L4 in 0.00 -> 1.69", note)
        self.assertNotIn("_plan_dir", plan)
        reloaded = edl_parser.load_edl(self.prj.plan_path)
        seg0 = reloaded["segments"][0]
        self.assertAlmostEqual(seg0["start"], 1.69)
        self.assertAlmostEqual(seg0["end"], 4.01)
        self.assertEqual([round(x, 2) for x in seg0["peer"]["idle_span"]], [1.81, 3.85])  # listener advances too
        # Only the opening line changed.
        before = JETS_LIKE_EDL.splitlines()
        after = self.prj.plan_path.read_text(encoding="utf-8").splitlines()
        changed = [a for a in after if a.strip() and a not in before]
        self.assertEqual(len(changed), 1)
        self.assertTrue(changed[0].startswith("L4 "))
        # Idempotent: a second run changes nothing.
        text1 = self.prj.plan_path.read_text(encoding="utf-8")
        self.assertEqual(verify.trim_leading_dead_air(edl_parser.load_edl(self.prj.plan_path), self.prj.plan_path), "")
        self.assertEqual(self.prj.plan_path.read_text(encoding="utf-8"), text1)

    def test_healthy_opening_is_untouched(self) -> None:
        healthy = JETS_LIKE_EDL.replace("0.00-4.01", "1.69-4.01")
        self.prj.plan_path.write_text(healthy, encoding="utf-8")
        plan = edl_parser.load_edl(self.prj.plan_path)
        self.assertEqual(verify.trim_leading_dead_air(plan, self.prj.plan_path), "")
        self.assertEqual(self.prj.plan_path.read_text(encoding="utf-8"), healthy)
        short_lead = JETS_LIKE_EDL.replace("0.00-4.01", "1.30-4.01")  # 0.45 s lead < 0.50 s rule
        self.prj.plan_path.write_text(short_lead, encoding="utf-8")
        self.assertEqual(verify.trim_leading_dead_air(edl_parser.load_edl(self.prj.plan_path), self.prj.plan_path), "")
        self.assertEqual(self.prj.plan_path.read_text(encoding="utf-8"), short_lead)

    def test_voiced_audio_before_first_word_is_never_trimmed(self) -> None:
        self.data = _speech(JETS_WORDS, spans=[(0.4, 3.6)])  # possible untranscribed speech
        plan = edl_parser.load_edl(self.prj.plan_path)
        note = verify.trim_leading_dead_air(plan, self.prj.plan_path)
        self.assertTrue(note.startswith("[OPENING WARN] L4"))
        self.assertIn("Start the cut at", note)
        self.assertEqual(self.prj.plan_path.read_text(encoding="utf-8"), JETS_LIKE_EDL)

    def test_timed_cues_or_muted_hook_are_not_trimmed(self) -> None:
        plan = edl_parser.load_edl(self.prj.plan_path)
        plan["segments"][0]["zoom_at"] = 2.2
        note = verify.trim_leading_dead_air(plan, self.prj.plan_path)
        self.assertTrue(note.startswith("[OPENING WARN]"))
        self.assertIn("zoom_at", note)
        plan2 = edl_parser.load_edl(self.prj.plan_path)
        plan2["segments"][0]["mute"] = True
        self.assertEqual(verify.trim_leading_dead_air(plan2, self.prj.plan_path), "")
        self.assertEqual(self.prj.plan_path.read_text(encoding="utf-8"), JETS_LIKE_EDL)

    def test_final_tail_warns_only(self) -> None:
        plan = edl_parser.load_edl(self.prj.plan_path)
        plan["segments"][-1]["end"] = 8.82  # last word ends 8.80
        notes = verify.check_opening_and_tail(plan)
        self.assertTrue(any(n.startswith("OPENING: L4") for n in notes))
        tail = [n for n in notes if n.startswith("TAIL")]
        self.assertEqual(len(tail), 1)
        self.assertIn("end the cut at 8.88", tail[0])
        self.data = _speech(JETS_WORDS, duration=8.83)
        tail2 = [n for n in verify.check_opening_and_tail(plan) if n.startswith("TAIL")]
        self.assertIn("cannot extend past the source", tail2[0])
        plan["segments"][-1]["end"] = 8.89
        self.assertEqual([n for n in verify.check_opening_and_tail(plan) if n.startswith("TAIL")], [])


@unittest.skipUnless((JETS_WORK / "plan.edl").is_file(), "jets work project not on this machine")
class RealJetsPlanTests(unittest.TestCase):
    def test_opening_and_tail_on_real_plan(self) -> None:
        _, plan = verify.load_plan(JETS_WORK / "plan.edl")
        plan["_plan_dir"] = str(JETS_WORK)
        notes = verify.check_opening_and_tail(plan)
        self.assertTrue(any(n.startswith("OPENING: L4") and "start the cut at 1.69" in n for n in notes), notes)
        self.assertTrue(any(n.startswith("TAIL: L17") and "cannot extend" in n for n in notes), notes)

    def test_auto_trim_on_copy_changes_only_l4(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="zeno_v309_jets_"))
        try:
            for name in ("plan.edl", "script_analysis.md", "decisions.txt", "clip_caches.json"):
                if (JETS_WORK / name).is_file():
                    shutil.copy2(JETS_WORK / name, tmp / name)
            p_file, plan = verify.load_plan(tmp / "plan.edl")
            note = verify.trim_leading_dead_air(plan, p_file)
            self.assertIn("L4 in 0.00 -> 1.69", note)
            before = (JETS_WORK / "plan.edl").read_text(encoding="utf-8").splitlines()
            after = (tmp / "plan.edl").read_text(encoding="utf-8").splitlines()
            diff = [(a, b) for a, b in zip(before, after) if a != b]
            self.assertEqual(len(before), len(after))
            self.assertEqual(len(diff), 1)
            self.assertIn("1.69-4.01", diff[0][1])
            self.assertIn("@1.81-3.85@right", diff[0][1])
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
