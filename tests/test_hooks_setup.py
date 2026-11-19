"""Zeno's Claude Code hooks are written per computer in exec form, so no editor needs Git Bash."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import hooks_setup


def _root(td: str, win: bool = False, venv: bool = True) -> Path:
    root = Path(td)
    if venv:
        rel = ".venv/Scripts/python.exe" if win else ".venv/bin/python"
        (root / rel).parent.mkdir(parents=True)
        (root / rel).write_text("")
    return root


def _read(root: Path) -> dict:
    return json.loads((root / ".claude" / "settings.local.json").read_text())


class TestHooksSetup(unittest.TestCase):
    def test_mac_entries_use_the_venv_python_directly_with_args_and_no_shell(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            self.assertTrue(hooks_setup.ensure(root, "darwin"))
            hooks = _read(root)["hooks"]
            self.assertEqual({e: len(g) for e, g in hooks.items()}, {"UserPromptSubmit": 1, "PreToolUse": 1})
            first = hooks["UserPromptSubmit"][0]["hooks"]
            self.assertEqual([h["args"][-1].split("/")[-1] for h in first], ["chat_guard.py", "--reset"])
            for event in hooks.values():
                for group in event:
                    for h in group["hooks"]:
                        self.assertEqual(h["command"], "${CLAUDE_PROJECT_DIR}/.venv/bin/python")
                        self.assertNotIn("shell", h)
                        self.assertTrue(h["args"][0].startswith("${CLAUDE_PROJECT_DIR}/scripts/"))
                        self.assertNotIn("[ -x", json.dumps(h))
            self.assertEqual(hooks["PreToolUse"][0]["matcher"], "*")

    def test_windows_entries_point_at_the_real_exe(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td, win=True)
            self.assertTrue(hooks_setup.ensure(root, "win32"))
            h = _read(root)["hooks"]["PreToolUse"][0]["hooks"][0]
            self.assertEqual(h["command"], "${CLAUDE_PROJECT_DIR}/.venv/Scripts/python.exe")
            self.assertTrue(h["command"].endswith(".exe"))

    def test_second_run_changes_nothing_and_other_settings_and_hooks_survive(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            (root / ".claude").mkdir()
            mine = {"permissions": {"allow": ["Bash(ls)"]},
                    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
            (root / ".claude" / "settings.local.json").write_text(json.dumps(mine))
            self.assertTrue(hooks_setup.ensure(root, "darwin"))
            before = (root / ".claude" / "settings.local.json").read_text()
            self.assertFalse(hooks_setup.ensure(root, "darwin"))
            self.assertEqual((root / ".claude" / "settings.local.json").read_text(), before)
            data = _read(root)
            self.assertEqual(data["permissions"], {"allow": ["Bash(ls)"]})
            commands = [h.get("command") for g in data["hooks"]["PreToolUse"] for h in g["hooks"]]
            self.assertIn("echo mine", commands)
            self.assertEqual(sum(1 for c in commands if c and c.endswith("python")), 1)

    def test_old_zeno_entries_are_replaced_not_duplicated(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            (root / ".claude").mkdir()
            old = {"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", "command":
                   'p="$CLAUDE_PROJECT_DIR/.venv/bin/python"; "$p" "$CLAUDE_PROJECT_DIR/scripts/call_cap.py"'}]}]}}
            (root / ".claude" / "settings.local.json").write_text(json.dumps(old))
            hooks_setup.ensure(root, "darwin")
            groups = _read(root)["hooks"]["PreToolUse"]
            self.assertEqual(len(groups), 1)
            self.assertIn("args", groups[0]["hooks"][0])

    def test_no_venv_writes_no_hooks_and_removes_stale_ones(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td, venv=False)
            self.assertFalse(hooks_setup.ensure(root, "darwin"))
            self.assertFalse((root / ".claude" / "settings.local.json").exists())
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.local.json").write_text(json.dumps(hooks_setup.merged_settings({}, ".venv/bin/python")))
            self.assertTrue(hooks_setup.ensure(root, "darwin"))
            self.assertNotIn("hooks", _read(root))

    def test_a_file_that_is_not_json_is_left_alone(self):
        with tempfile.TemporaryDirectory() as td:
            root = _root(td)
            (root / ".claude").mkdir()
            (root / ".claude" / "settings.local.json").write_text("{ not json")
            self.assertFalse(hooks_setup.ensure(root, "darwin"))
            self.assertEqual((root / ".claude" / "settings.local.json").read_text(), "{ not json")

    def test_committed_settings_have_no_bash_hooks_and_local_settings_never_ship(self):
        committed = json.loads((ROOT / ".claude" / "settings.json").read_text())
        self.assertNotIn("hooks", committed)
        self.assertIn("settings.local.json", (ROOT / "scripts" / "release.py").read_text())
        self.assertIn(".claude/settings.local.json", (ROOT / "scripts" / "auto_updater.py").read_text())
        self.assertIn(".claude/settings.local.json", (ROOT / ".gitignore").read_text())


if __name__ == "__main__":
    unittest.main()
