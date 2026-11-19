#!/usr/bin/env python3
"""Regression tests for Zeno v2.0.0 clean imports, zero-MCP config, delivery root, and CLI flags."""
from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = ROOT / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DELETED_1X_MODULES = (
    "zeno_mcp",
    "ai_director",
    "direct_mode",
    "sol_direct",
    "request_budget",
    "editorial_retrieval",
    "cost_control",
    "chat_guard",
    "call_cap",
    "hooks_setup",
    "launch_edit",
    "preproduce",
    "session_check",
    "eval_alignment",
    "brain_memory",
    "format_learning",
    "style_readiness",
    "team_patterns",
    "knowledge_sync",
    "editor_note",
    "learn_reference",
)


class V2CleanImportsTests(unittest.TestCase):
    def test_deleted_1x_modules_absent_and_unimported(self) -> None:
        """Deleted 1.x modules must not exist in `scripts/` or be imported by any `scripts/*.py` module."""
        deleted_set = set(DELETED_1X_MODULES)
        for mod_name in DELETED_1X_MODULES:
            self.assertFalse(
                (SCRIPTS_DIR / f"{mod_name}.py").exists(),
                f"Legacy 1.x module still present: scripts/{mod_name}.py",
            )

        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        self.assertGreaterEqual(len(py_files), 30)
        for py_path in py_files:
            src = py_path.read_text(encoding="utf-8")
            tree = ast.parse(src, filename=str(py_path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".", 1)[0]
                        self.assertNotIn(
                            top,
                            deleted_set,
                            f"{py_path.name} still imports deleted 1.x module '{alias.name}'",
                        )
                elif isinstance(node, ast.ImportFrom) and node.module:
                    top = node.module.split(".", 1)[0]
                    self.assertNotIn(
                        top,
                        deleted_set,
                        f"{py_path.name} still imports from deleted 1.x module '{node.module}'",
                    )

    def test_all_scripts_syntax_and_clean_import(self) -> None:
        """Every top-level `scripts/*.py` file compiles and imports cleanly without `ModuleNotFoundError`."""
        py_files = sorted(SCRIPTS_DIR.glob("*.py"))
        with mock.patch.dict(os.environ, {"_ZENO_REEXEC": "1"}):
            for py_path in py_files:
                src = py_path.read_text(encoding="utf-8")
                compile(src, str(py_path), "exec")
                mod = importlib.import_module(py_path.stem)
                self.assertIsNotNone(mod, f"Failed to import scripts/{py_path.name}")

        for dead_mod in DELETED_1X_MODULES:
            self.assertNotIn(dead_mod, sys.modules, f"Deleted 1.x module leaked into sys.modules: {dead_mod}")

    def test_common_delivery_base_dir_defaults_and_override(self) -> None:
        """`common._delivery_base_dir()` defaults to `~/Downloads/Zeno Outs` and honors `ZENO_OUTPUT_DIR`."""
        import common

        with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": ""}):
            expected_default = Path.home() / "Downloads" / "Zeno Outs"
            self.assertEqual(common._delivery_base_dir(), expected_default)

        with tempfile.TemporaryDirectory(prefix="zeno_out_override_") as td:
            custom = Path(td) / "Custom Zeno Outs"
            with mock.patch.dict(os.environ, {"ZENO_OUTPUT_DIR": str(custom)}):
                self.assertEqual(common._delivery_base_dir(), custom)

    def test_v2_zero_mcp_and_agents_claude_parity(self) -> None:
        """`AGENTS.md == CLAUDE.md`, `.mcp.json == {}`, and `.claude/settings.json` denies `mcp__*`."""
        agents_bytes = (ROOT / "AGENTS.md").read_bytes()
        claude_bytes = (ROOT / "CLAUDE.md").read_bytes()
        self.assertGreater(len(agents_bytes), 500)
        self.assertEqual(agents_bytes, claude_bytes, "AGENTS.md and CLAUDE.md must be byte-identical")

        mcp_cfg = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
        self.assertEqual(mcp_cfg, {}, f"Expected empty .mcp.json in v2.0.0 Host-AI Direct mode, got: {mcp_cfg}")

        settings_cfg = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        self.assertFalse(settings_cfg.get("enableAllProjectMcpServers", True))
        self.assertEqual(
            str((settings_cfg.get("env") or {}).get("ENABLE_CLAUDEAI_MCP_SERVERS", "")).lower(),
            "false",
        )
        deny_list = (settings_cfg.get("permissions") or {}).get("deny") or []
        self.assertIn("mcp__*", deny_list, f"Missing mcp__* deny rule in .claude/settings.json: {deny_list}")

    def test_zeno_cli_help_v2_flags_and_no_auto_plan(self) -> None:
        """`zeno.py --help` removes `--auto-plan` and exposes `--queue`, `--inspect-only`, `--execute`, `--compact`, `--square`."""
        proc = subprocess.run(
            [sys.executable, str(SCRIPTS_DIR / "zeno.py"), "--help"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        help_out = (proc.stdout or "") + "\n" + (proc.stderr or "")
        self.assertNotIn("--auto-plan", help_out)
        for flag in ("--queue", "--inspect-only", "--execute", "--compact", "--square"):
            self.assertIn(flag, help_out, f"Missing expected v2.0.0 CLI flag {flag} in zeno.py --help")


if __name__ == "__main__":
    unittest.main()
