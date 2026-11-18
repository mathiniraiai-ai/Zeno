"""A stopped or failed managed workflow must not expand context in the outer chat."""
import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import direct_mode
import zeno
import zeno_mcp


class TestPlannerStopRouting(unittest.TestCase):
    def test_shared_chat_allowed_only_for_isolated_planning(self):
        import agent_log
        measured = {"tokens": 500000, "steps": 50}
        with patch.object(agent_log, "session_size", return_value=measured), \
                patch.object(zeno, "_archive_blocked"), contextlib.redirect_stdout(io.StringIO()):
            zeno.session_gate("inspect", block=True, isolated_planner=True)
            with self.assertRaises(SystemExit):
                zeno.session_gate("inspect", block=True, isolated_planner=False)

    def test_zero_response_budget_failure_does_not_offer_manual_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            plan = Path(tmp) / "plan.edl"
            plan.write_text("#canvas 1080x1920@30\n")
            output = io.StringIO()
            with patch.object(direct_mode, "resolve_provider", return_value="claude"), \
                    patch.object(direct_mode, "run", side_effect=ValueError("Unresolved model request")), \
                    contextlib.redirect_stdout(output):
                self.assertEqual(zeno.run_auto_plan(plan), 2)
            self.assertIn("[PLANNER STOPPED]", output.getvalue())
            self.assertNotIn("[MANUAL_FALLBACK_PACK]", output.getvalue())

    def test_render_failure_does_not_become_manual_planning(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "editing_packet.md").write_text("large evidence")
            with patch.object(zeno_mcp, "_has_packet", return_value=True):
                self.assertFalse(zeno_mcp._is_manual_fallback(2, "[VERIFY FAILED] pause L1", project))
                self.assertTrue(zeno_mcp._is_manual_fallback(2, "[MANUAL_FALLBACK_PACK] explicit", project))


if __name__ == "__main__":
    unittest.main()
