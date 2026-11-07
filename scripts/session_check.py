"""Keep an edit session lean: how big the session starts, and the editor's own settings that switch the claude.ai connectors off.

Measured on this project (2026-11-06): a plain Claude Code session starts at about 53k tokens of context; turning the connectors off removes 7.5k of
it (4.6k with the setting below, all 7.5k with `claude --strict-mcp-config`, which the launchers use). Standard library only.
"""
from __future__ import annotations

import glob
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

LEAN_ENV = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false"}
WARN_AT = 60000        # a session that starts above this is carrying something heavy (connectors, many plugins)


def write_lean_settings(root: Path) -> Dict[str, Any]:
    """Merge the connector-off setting into this computer's own `.claude/settings.local.json` (never shared through Git). Other keys are kept."""
    f = Path(root) / ".claude" / "settings.local.json"
    data: Dict[str, Any] = {}
    if f.is_file():
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
    env = data.get("env") if isinstance(data.get("env"), dict) else {}
    changed = any(env.get(k) != v for k, v in LEAN_ENV.items())
    env.update(LEAN_ENV)
    data["env"] = env
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return {"path": str(f), "changed": changed}


def first_turn_context(lines: List[str]) -> Optional[Dict[str, int]]:
    """The size of a session's first model call: what it started with. None when the transcript has no usage yet."""
    for line in lines:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        u = (d.get("message") or {}).get("usage") if d.get("type") == "assistant" else None
        if u:
            w = int(u.get("cache_creation_input_tokens") or 0)
            r = int(u.get("cache_read_input_tokens") or 0)
            f = int(u.get("input_tokens") or 0)
            return {"new": w + f, "cached": r, "total": w + r + f}
    return None


def newest_session_file(root: Path) -> Optional[Path]:
    """The most recent Claude Code transcript of this project folder."""
    key = str(Path(root).resolve()).replace("/", "-").replace(" ", "-").replace("_", "-")
    pat = os.path.expanduser(f"~/.claude/projects/{key}/*.jsonl")
    files = sorted(glob.glob(pat), key=os.path.getmtime)
    return Path(files[-1]) if files else None


def report(root: Path) -> str:
    f = newest_session_file(root)
    if f is None:
        return "No Claude Code session found for this folder yet. Start one (edit.command / edit.bat), then run this check again."
    ctx = first_turn_context(f.read_text(encoding="utf-8", errors="ignore").splitlines())
    if ctx is None:
        return "The session has not made its first model call yet."
    msg = f"This session started with {ctx['total']:,} tokens of context ({ctx['new']:,} new, {ctx['cached']:,} already cached)."
    if ctx["total"] > WARN_AT:
        msg += (f"\n  [HEAVY START] Above {WARN_AT:,}. Likely causes: claude.ai connectors or extra MCP servers (about 7k), plugins, or the app's own tools."
                "\n  Start edits with edit.command (Mac) or edit.bat (Windows), or run `python3 scripts/setup.py --lean-session`, then open a fresh session.")
    else:
        msg += "\n  [OK] A lean start."
    return msg
