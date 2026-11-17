#!/usr/bin/env python3
"""Claude Code hook (UserPromptSubmit): stop a new edit typed into a chat that already holds earlier work.

Every step re-reads the whole chat, so a new video started in a long chat costs several times more (16 Nov: 5-10M
re-read tokens per job instead of about 1M). Claude Code runs this before the message is sent, so a blocked message
costs nothing. It blocks only when the message starts a new edit (/zeno, /edit, or a Google Doc script link) and the
chat is over ZENO_SESSION_LIMIT (default 250k). Anything it cannot read lets the message through.
Exit 2 = blocked (Claude Code shows the reason to the editor); exit 0 = go on.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

LIMIT = 1_000_000
_NEW_EDIT = re.compile(r"(?is)^\s*/(zeno|edit)\b|docs\.google\.com/document/")
_CONTINUE = re.compile(r"(?i)\bcontinue the edit\b")


def decide(event: dict) -> str:
    """The message to show when the prompt must be blocked, or '' to let it through."""
    prompt = str(event.get("prompt") or "")
    if not _NEW_EDIT.search(prompt) or _CONTINUE.search(prompt):
        return ""
    path = str(event.get("transcript_path") or "")
    if not path or not os.path.exists(path):
        return ""
    import agent_log
    s = agent_log.chat_size(path)
    try:
        from cost_control import settings
        limit = int(os.environ.get("ZENO_SESSION_LIMIT") or settings()["new_chat_context_limit"])
    except ValueError:
        limit = LIMIT
    if not s or s["tokens"] <= limit:
        return ""
    return (f"Zeno: this chat already holds {s['tokens'] // 1000}k tokens of earlier work, and Claude re-reads all of it on every step, "
            f"which uses up the team's Claude limit. Type /clear, then send the same message again (it is still in your history: press the up arrow). "
            f"Nothing is lost: Zeno keeps every clip it already measured.")


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
        msg = decide(event)
    except Exception:
        return 0
    if msg:
        print(msg, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
