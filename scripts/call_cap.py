#!/usr/bin/env python3
"""Claude Code hook: a hard cap on tool calls once a video edit has started in the current editor message.

Every tool call re-sends the whole chat (a cache re-read of 150k-450k tokens). Written rules ("4-5 turns", "never poll")
are only advice the model can ignore; on 17 Nov edits still took 8-54 calls each. Claude Code runs this hook before
every tool call, so the cap holds whatever the model decides.

  UserPromptSubmit (`--reset`): a new editor message starts a fresh count.
  PreToolUse: a call that starts an edit (zeno_start_edit / zeno_edit / zeno_auto_plan / zeno_render MCP tools, or a
  `zeno.py --inspect` / `--execute` / `--auto-plan` command) switches the cap on. After that, ZENO_CALL_CAP more calls
  (default 4: read the review sheet, report) are allowed; the next one is refused with a message telling the model to
  report and stop. Work that never starts an edit (questions, code work) is never counted.

Anything unexpected (bad input, unreadable state, any exception) lets the call through: this hook must never be the
reason an edit fails. Set ZENO_CALL_CAP=0 to switch it off.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = ROOT / "work" / "_logs" / "call_cap"
DEFAULT_CAP = 4
_EDIT_TOOLS = re.compile(r"zeno_(start_edit|edit|auto_plan|render)$")
_EDIT_CMD = re.compile(r"zeno\.py\b.*--(inspect|execute|auto-plan)\b")

DENY_TEXT = (
    "[ZENO CALL LIMIT] This edit has used its tool calls for this message. Every extra call re-reads the whole chat. "
    "Do not call any more tools. Report to the editor now from what you already have (Pitch Card, MP4 paths, checks, "
    "token report, or the [BLOCKED]/still-running lines) and end the turn. The editor's next message gets a fresh count."
)


def _cap() -> int:
    try:
        return max(0, int(os.environ.get("ZENO_CALL_CAP", DEFAULT_CAP)))
    except ValueError:
        return DEFAULT_CAP


def _state_path(session: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", session)[:80] or "unknown"
    return STATE_DIR / f"{safe}.json"


def _load(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save(p: Path, st: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(st))
    tmp.replace(p)


def starts_edit(tool: str, tool_input: dict) -> bool:
    if _EDIT_TOOLS.search(tool or ""):
        return True
    if tool == "Bash":
        return bool(_EDIT_CMD.search(str((tool_input or {}).get("command") or "")))
    return False


def decide(event: dict, st: dict, cap: int) -> tuple[bool, dict]:
    """(allow?, new state). Pure, so the dry test can drive it without Claude Code."""
    st = dict(st)
    if cap <= 0:
        return True, st
    if st.get("active"):
        st["used"] = int(st.get("used", 0)) + 1
        if st["used"] > cap:
            st["refused"] = int(st.get("refused", 0)) + 1
            return False, st
        return True, st
    if starts_edit(str(event.get("tool_name") or ""), event.get("tool_input") or {}):
        st.update(active=True, used=0, started=time.time())
    return True, st


def main() -> int:
    try:
        event = json.load(sys.stdin)
        session = str(event.get("session_id") or "")
        if not session:
            return 0
        p = _state_path(session)
        if "--reset" in sys.argv:
            _save(p, {"reset_at": time.time()})
            return 0
        allow, st = decide(event, _load(p), _cap())
        _save(p, st)
        if not allow:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": DENY_TEXT}}))
        return 0
    except Exception:
        return 0


if __name__ == "__main__":
    sys.exit(main())
