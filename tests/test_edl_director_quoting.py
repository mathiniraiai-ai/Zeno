#!/usr/bin/env python3
"""Unit tests for unquoted spaced clip/peer normalization and quoting in v2.0.0 (`edl_parser`, `script_order`, `zeno`, `edit_packet`)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))

import edit_packet  # noqa: E402
import edl_parser  # noqa: E402
import script_order  # noqa: E402
import zeno  # noqa: E402


class EdlQuotingTests(unittest.TestCase):
    def test_parse_edl_handles_unquoted_spaced_src_and_peer(self) -> None:
        edl = (
            '#src_dir "/tmp/raw"\n'
            'L8 Robot Silent.mp4 0-0.40 layout=split_v z=1 sp="Female Robot" '
            'slot=top peer=Rahul:Rahul Silent.mp4@0-0.40@bottom\n'
            'L9 "1.mp4" 0.25-1.18 z=1 sp=Rahul\n'
        )
        parsed = edl_parser.parse_edl(edl)
        segs = parsed["segments"]
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["id"], "L8")
        self.assertEqual(segs[0]["src"], "/tmp/raw/Robot Silent.mp4")
        self.assertEqual(segs[0]["start"], 0.0)
        self.assertEqual(segs[0]["end"], 0.4)
        self.assertEqual(segs[0]["peer"]["speaker"], "Rahul")
        self.assertEqual(segs[0]["peer"]["src"], "/tmp/raw/Rahul Silent.mp4")
        self.assertEqual(segs[0]["peer"]["slot"], "bottom")
        self.assertEqual(segs[1]["src"], "/tmp/raw/1.mp4")

    def test_format_edl_auto_quotes_spaced_filenames(self) -> None:
        edl = (
            '#output "/tmp/Zeno Outs/Demo/reel_Demo.mp4"\n'
            '#script "/tmp/s.txt"\n'
            '#src_dir "/tmp/raw"\n'
            'L8 Robot Silent.mp4 0-0.40 layout=split_v z=1 sp="Female Robot" '
            'peer=Rahul:Rahul Silent.mp4@0-0.40@bottom\n'
            'L9a Robot.mp4 0.01-5.93 layout=split_v z=1 sp="Female Robot" '
            'peer="Rahul:Rahul Silent.mp4"@0-5.92@bottom\n'
        )
        parsed = edl_parser.parse_edl(edl)
        formatted = edl_parser.format_edl(parsed)
        self.assertIn(
            'L8 "Robot Silent.mp4" 0.00-0.40 layout=split_v z=1 sp="Female Robot" '
            'peer="Rahul:Rahul Silent.mp4"@0-0.4@bottom',
            formatted,
        )
        self.assertIn(
            'L9a Robot.mp4 0.01-5.93 layout=split_v z=1 sp="Female Robot" '
            'peer="Rahul:Rahul Silent.mp4"@0-5.92@bottom',
            formatted,
        )
        with tempfile.TemporaryDirectory() as tmp:
            edl_path = Path(tmp) / "plan.edl"
            edl_path.write_text(formatted, encoding="utf-8")
            reparsed = edl_parser.parse_edl(edl_path)
            self.assertEqual(len(reparsed["segments"]), 2)
            self.assertEqual(script_order.unread_plan_lines(reparsed, edl_path), [])

    def test_v2_stage3_tweaks_and_pipe_rows_preserve_quotes_and_line_detail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            orig = '#output "/tmp/Zeno Outs/Demo/reel_Demo.mp4"\n#script "/tmp/s.txt"\n#src_dir "/tmp/raw"\n'
            cur = (
                orig
                + "\n"
                + "L1 1.mp4 0.10-1.00 z=1 sp=A\n"
                + "L2 1.mp4 1.10-2.00 z=1 sp=A\n"
                + "L3 1.mp4 2.10-3.00 z=1 sp=A\n"
                + "L4 1.mp4 3.10-4.00 z=1 sp=A\n"
                + 'L8 "Robot Silent.mp4" 0.00-0.40 z=1 sp=A\n'
            )
            plan_path = proj / "plan.edl"
            plan_path.write_text(cur, encoding="utf-8")

            # v2.0.0 take swap via zeno.apply_stage3_tweaks preserves L1..L4 and auto-quotes spaced clip names
            zeno.apply_stage3_tweaks(plan_path, swap_takes=["L8=/tmp/raw/New Robot Silent.mp4"])
            patched = plan_path.read_text(encoding="utf-8")
            self.assertIn('L8 "New Robot Silent.mp4" 0.00-0.40 z=1 sp=A', patched)
            self.assertIn("L1 1.mp4 0.10-1.00 z=1 sp=A", patched)
            loaded = edl_parser.load_edl(plan_path)
            self.assertEqual(loaded["segments"][-1]["src"], "/tmp/raw/New Robot Silent.mp4")
            self.assertEqual(script_order.unread_plan_lines(loaded, plan_path), [])

            # Also verify a bare spaced path in swap_takes still auto-quotes and preserves L1..L4
            zeno.apply_stage3_tweaks(plan_path, swap_takes=["L8=Another Silent Clip.mp4"])
            patched_bare = plan_path.read_text(encoding="utf-8")
            self.assertIn('L8 "Another Silent Clip.mp4" 0.00-0.40 z=1 sp=A', patched_bare)
            self.assertIn("L1 /tmp/raw/1.mp4 0.10-1.00 z=1 sp=A", patched_bare)
            self.assertEqual(script_order.unread_plan_lines(edl_parser.load_edl(plan_path), plan_path), [])

            # Pipe-delimited spaced clip & peer row normalizes cleanly through edl_parser
            pipe_edl = (
                '#src_dir "/tmp/raw"\n'
                'L10 | Robot Silent.mp4 | 0.00-1.20 | split_h | 1 | cut | '
                'sp="Female Robot" peer="Rahul:Rahul Silent.mp4"@0-1.20@right\n'
            )
            pipe_parsed = edl_parser.parse_edl(pipe_edl)
            self.assertEqual(len(pipe_parsed["segments"]), 1)
            self.assertEqual(pipe_parsed["segments"][0]["src"], "/tmp/raw/Robot Silent.mp4")
            pipe_fmt = edl_parser.format_edl(pipe_parsed)
            self.assertIn('"Robot Silent.mp4"', pipe_fmt)
            self.assertIn('"Rahul:Rahul Silent.mp4"@0-1.2@right', pipe_fmt)

            # edit_packet --line lookup handles spaced clip names in line_map.md + facts.md
            (proj / "line_map.md").write_text(
                'L8 Female Robot:\n  CAND "Robot Silent.mp4" 0.00-0.40 heard=100%\n',
                encoding="utf-8",
            )
            (proj / "facts.md").write_text(
                "## Robot Silent.mp4 (4.0s focus=0.50,0.35)\n0.00 silent|0.40\n",
                encoding="utf-8",
            )
            detail = edit_packet.line_detail(proj, "L8")
            self.assertIn('CAND "Robot Silent.mp4"', detail)
            self.assertIn("## Robot Silent.mp4 (4.0s focus=0.50,0.35)", detail)


if __name__ == "__main__":
    unittest.main()
