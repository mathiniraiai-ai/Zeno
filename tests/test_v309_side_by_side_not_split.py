"""'Side by side' that describes where people are (two fighter jets, standing together) must not become a split screen."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import format_library as fl  # noqa: E402
import prepare  # noqa: E402

JET_VISUAL = (
    "Visual Description : High in the city sky at golden hour. A man and a Female Robot fly side by side in two "
    "separate fighter jets. Her jet swerves in to crash into his, and he shouts for her to keep away from him."
)
JET_SCRIPT = JET_VISUAL + "\nMan: keep away\nFemale Robot: say it in English\nThe whole conversation happens like this."


class SideBySideTests(unittest.TestCase):
    def test_physical_side_by_side_is_scrubbed(self):
        self.assertNotIn("side by side", fl.scrub_physical_side_by_side(JET_VISUAL).lower())
        self.assertNotIn("side by side", fl.scrub_physical_side_by_side("they stand side-by-side in a kitchen").lower())

    def test_screen_side_by_side_is_kept(self):
        for t in ("two videos side by side", "teacher and learner in side-by-side panels", "side by side split screen lesson",
                  "windows side by side"):
            self.assertEqual(fl.scrub_physical_side_by_side(t), t, t)

    def test_jet_scene_gets_no_split_layout_hint(self):
        self.assertEqual(prepare.layout_hint_from_direction(JET_VISUAL), "")

    def test_real_split_cues_still_resolve(self):
        self.assertEqual(prepare.layout_hint_from_direction("Teacher and learner in side-by-side panels"), "split_h")
        self.assertEqual(prepare.layout_hint_from_direction("two videos side by side"), "split_h")

    def test_jet_script_is_not_split_screen_and_live_conversation_wins_by_name(self):
        by_name = fl.detect_catalog_format(
            {"title": "W47 - Telugu - AI Only - Live conversation - fighter jets - don't come near me"}, {}, script_text=JET_SCRIPT)
        self.assertEqual(by_name, "live_conversation")
        no_cue = fl.detect_catalog_format({"title": "W47 - Telugu - Don't Come Near Me"}, {}, script_text=JET_SCRIPT)
        self.assertNotIn(no_cue, ("split_screen", "direct_hook", "stop_saying", "translate_drill"))

    def test_explicit_split_screen_script_still_split_screen(self):
        txt = "Visual: split screen, learner on the left, AI teacher on the right\nLearner: hello"
        self.assertEqual(fl.detect_catalog_format({"title": "W45 - Hindi - Split screen"}, {}, script_text=txt), "split_screen")


if __name__ == "__main__":
    unittest.main()
