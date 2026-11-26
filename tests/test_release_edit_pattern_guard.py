"""Releases must not change the edit pattern unless made on purpose with --training."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import release  # noqa: E402


class EditPatternGuardTests(unittest.TestCase):
    def test_flags_only_edit_pattern_files(self) -> None:
        out = "scripts/common.py\nservice/usage_page.py\nformats/catalog/stop_saying.md\nCLAUDE.md\nscripts/layouts.py\nscripts/render.py\n.claude/commands/edit.md\n"
        fake = subprocess.CompletedProcess([], 0, stdout=out, stderr="")
        with mock.patch.object(release.subprocess, "run", return_value=fake):
            hit = release.edit_pattern_changes("abc1234")
        self.assertEqual(hit, [".claude/commands/edit.md", "CLAUDE.md", "formats/catalog/stop_saying.md", "scripts/layouts.py"])

    def test_nothing_flagged_for_speed_dashboard_changes(self) -> None:
        fake = subprocess.CompletedProcess([], 0, stdout="scripts/common.py\nscripts/render.py\nservice/usage_page.py\nscripts/token_cost.py\n", stderr="")
        with mock.patch.object(release.subprocess, "run", return_value=fake):
            self.assertEqual(release.edit_pattern_changes("abc1234"), [])
        self.assertEqual(release.edit_pattern_changes(""), [])

    def test_publish_is_blocked_without_training_flag(self) -> None:
        with mock.patch.object(release, "check_live_status", return_value={"data": {"sha": "abc1234"}}), \
             mock.patch.object(release, "edit_pattern_changes", return_value=["formats/core_rules.md"]), \
             mock.patch.object(sys, "argv", ["release.py", "--no-bump", "x"]):
            self.assertEqual(release.main(), 3)


if __name__ == "__main__":
    unittest.main()
