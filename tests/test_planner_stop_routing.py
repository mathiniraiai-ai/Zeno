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
        measured = {"tokens": 6000000, "steps": 50}
        with patch.object(agent_log, "session_size", return_value=measured), \
                patch.object(zeno, "_archive_blocked"), contextlib.redirect_stdout(io.StringIO()):
            zeno.session_gate("inspect", block=True, isolated_planner=True)
            with self.assertRaises(SystemExit):
                zeno.session_gate("inspect", block=True, isolated_planner=False)

    def test_the_planner_finds_claude_inside_the_desktop_app_when_it_is_not_on_path(self):
        import os
        import stat
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / "claude"
            exe.write_text("#!/bin/sh\n")
            exe.chmod(exe.stat().st_mode | stat.S_IXUSR)
            with patch.object(direct_mode.shutil, "which", return_value=None), \
                    patch.dict(os.environ, {"CLAUDE_CODE_EXECPATH": str(exe)}):
                self.assertEqual(direct_mode.find_claude_binary(), str(exe))
                self.assertEqual(direct_mode.resolve_provider("auto"), "claude")
            app = Path(tmp) / "Library" / "Application Support" / "Claude" / "claude-code" / "9.9.9" / "abc"
            app.mkdir(parents=True)
            (app / "claude").write_text("#!/bin/sh\n")
            (app / "claude").chmod(0o755)
            env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_EXECPATH"}
            with patch.object(direct_mode.shutil, "which", return_value=None), patch.dict(os.environ, env, clear=True), \
                    patch.object(direct_mode.Path, "home", return_value=Path(tmp)):
                self.assertEqual(direct_mode.find_claude_binary(), str(app / "claude"))

    def test_preflight_is_not_blocked_by_a_fresh_chat_that_already_starts_large(self):
        src = (Path(zeno.__file__)).read_text(encoding="utf-8")
        line = next(l for l in src.splitlines() if 'session_gate("preflight"' in l)
        self.assertIn("isolated_planner=True", line)

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
