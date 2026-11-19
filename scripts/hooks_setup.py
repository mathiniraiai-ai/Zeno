#!/usr/bin/env python3
"""Write Zeno's Claude Code hooks for THIS computer into `.claude/settings.local.json` (git-ignored, never in an update bundle).

Why: the hooks used to be bash one-liners in the committed `.claude/settings.json`. On a Windows PC without Git Bash
Claude Code runs them in PowerShell, where `$CLAUDE_PROJECT_DIR` is empty, so they fail and Claude Code shows a
"Git Bash wasn't found" notice on every message. Claude Code has no per-OS hook setting and no command line valid in both
shells, but it does run a hook in "exec form" (`command` + `args`, no shell at all, `${CLAUDE_PROJECT_DIR}` expanded).
So each computer writes its own entries, pointing at its own venv Python (`.venv/bin/python` on Mac/Linux,
`.venv/Scripts/python.exe` on Windows). Editors install nothing and set nothing.

`ensure()` is cheap and safe to call often: it changes nothing when the file is already right, leaves every key and hook
that is not Zeno's untouched, leaves a file it cannot parse alone, and never raises.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
SETTINGS_LOCAL = ROOT / ".claude" / "settings.local.json"
_PROJECT = "${CLAUDE_PROJECT_DIR}"

# (event, matcher, script, extra args, timeout seconds): the hooks the project had in settings.json
HOOKS = (
    ("UserPromptSubmit", None, "chat_guard.py", (), 10),
    ("UserPromptSubmit", None, "call_cap.py", ("--reset",), 5),
    ("PreToolUse", "*", "call_cap.py", (), 5),
)
_OURS = tuple(sorted({h[2] for h in HOOKS}))


def venv_python(root: Path = ROOT, platform_name: Optional[str] = None) -> Optional[str]:
    """Project-relative path of the venv Python for this OS, or None when the venv does not exist yet."""
    win = (platform_name or sys.platform).startswith("win")
    rel = ".venv/Scripts/python.exe" if win else ".venv/bin/python"
    return rel if (root / rel).is_file() else None


def build_hooks(py_rel: str) -> Dict[str, List[Dict[str, Any]]]:
    """Claude Code `hooks` block in exec form (no shell): one group per event/matcher."""
    groups: Dict[tuple, Dict[str, Any]] = {}
    out: Dict[str, List[Dict[str, Any]]] = {}
    for event, matcher, script, extra, timeout in HOOKS:
        key = (event, matcher)
        if key not in groups:
            group: Dict[str, Any] = {"hooks": []}
            if matcher:
                group["matcher"] = matcher
            groups[key] = group
            out.setdefault(event, []).append(group)
        groups[key]["hooks"].append({
            "type": "command",
            "command": f"{_PROJECT}/{py_rel}",
            "args": [f"{_PROJECT}/scripts/{script}", *extra],
            "timeout": timeout,
        })
    return out


def _is_ours(hook: Dict[str, Any]) -> bool:
    text = " ".join([str(hook.get("command") or ""), *[str(a) for a in (hook.get("args") or [])]]).replace("\\", "/")
    return any(f"scripts/{name}" in text for name in _OURS)


def _without_ours(hooks: Dict[str, Any]) -> Dict[str, Any]:
    cleaned: Dict[str, Any] = {}
    for event, groups in (hooks or {}).items():
        kept = []
        for group in groups if isinstance(groups, list) else []:
            inner = [h for h in (group.get("hooks") or []) if isinstance(h, dict) and not _is_ours(h)]
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            cleaned[event] = kept
    return cleaned


def merged_settings(existing: Dict[str, Any], py_rel: Optional[str]) -> Dict[str, Any]:
    """`existing` with Zeno's hooks replaced by the current ones (removed when there is no venv Python)."""
    result = dict(existing)
    hooks = _without_ours(existing.get("hooks") or {})
    if py_rel:
        for event, groups in build_hooks(py_rel).items():
            hooks.setdefault(event, []).extend(groups)
    if hooks:
        result["hooks"] = hooks
    else:
        result.pop("hooks", None)
    return result


def ensure(root: Path = ROOT, platform_name: Optional[str] = None) -> bool:
    """Make `.claude/settings.local.json` carry this computer's Zeno hooks. True when the file was changed."""
    try:
        path = root / ".claude" / "settings.local.json"
        existing: Dict[str, Any] = {}
        if path.is_file():
            try:
                existing = json.loads(path.read_text(encoding="utf-8") or "{}")
            except ValueError:
                return False   # somebody's own file that is not valid JSON: never overwrite it
            if not isinstance(existing, dict):
                return False
        new = merged_settings(existing, venv_python(root, platform_name))
        if new == existing:
            return False
        if not new and not path.exists():
            return False
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(new, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:
        return False


def main() -> int:
    changed = ensure()
    py = venv_python()
    print(f"[HOOKS] {'written' if changed else 'already correct'}: {SETTINGS_LOCAL} (python: {py or 'venv not found, no hooks'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
