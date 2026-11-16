"""What happened between the editor and the AI agent during one edit, for the job archive.

Reads the agent's own session log on this computer (Claude Code: ~/.claude/projects/**/*.jsonl; Antigravity:
~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl) for the job's time window and keeps:
  - prompts: what the editor typed, word for word (their instructions, corrections and complaints);
  - steps: what the agent did, in order: what it said (shortened), each tool it ran (command or file, shortened),
    and each tool error (the debugging trail);
  - counts: prompts, tool calls per tool, errors, plan checks and renders the agent started.
The agent's private reasoning is never read. Anything that looks like a key or token is replaced by [REDACTED].
Nothing here can stop an edit: every failure returns an empty result. Standard library only.
"""
from __future__ import annotations

import glob
import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent.parent

MAX_PROMPTS = 80
MAX_PROMPT_CHARS = 4000
MAX_STEPS = 700
MAX_SAY_CHARS = 1500
MAX_TOOL_CHARS = 400
MAX_ERROR_CHARS = 700

_SECRET = re.compile(
    r"(zeno_[A-Za-z0-9_\-]{16,}|sk-[A-Za-z0-9_\-]{16,}|AIza[0-9A-Za-z_\-]{20,}|ghp_[A-Za-z0-9]{20,}|xox[abp]-[A-Za-z0-9\-]{10,}"
    r"|(?i:bearer)\s+[A-Za-z0-9._\-]{16,}|(?i:(?:api[_-]?key|token|secret|password)\s*[=:]\s*)[^\s'\"]{8,})"
)
_WRAPPERS = re.compile(
    r"<(system-reminder|local-command-caveat|local-command-stdout|command-message|ide_selection|ide_opened_file)>.*?</\1>", re.S)


def redact(text: str) -> str:
    return _SECRET.sub("[REDACTED]", str(text or ""))


def _cut(text: str, limit: int) -> str:
    text = redact(text).strip()
    return text if len(text) <= limit else text[:limit] + f" … [+{len(text) - limit} chars]"


def _epoch(ts: Any) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _tool_summary(name: str, args: Any) -> str:
    """One short line for a tool call: the command, the file, or the skill."""
    if not isinstance(args, dict):
        return _cut(str(args or ""), MAX_TOOL_CHARS)
    for key in ("command", "CommandLine", "file_path", "AbsolutePath", "TargetFile", "path", "skill", "url", "pattern", "query"):
        if args.get(key):
            return _cut(str(args[key]), MAX_TOOL_CHARS)
    return _cut(json.dumps(args, ensure_ascii=False), MAX_TOOL_CHARS)


def _counts(prompts: List[Dict[str, Any]], steps: List[Dict[str, Any]]) -> Dict[str, Any]:
    by_tool: Dict[str, int] = {}
    checks = renders = errors = 0
    for s in steps:
        if s["kind"] == "tool":
            by_tool[s["tool"]] = by_tool.get(s["tool"], 0) + 1
            cmd = s.get("input", "")
            checks += "--check-plan" in cmd
            renders += "--execute" in cmd
        elif s["kind"] == "error":
            errors += 1
    return {"prompts": len(prompts), "tool_calls": sum(by_tool.values()), "by_tool": by_tool, "errors": errors,
            "check_plan_runs": checks, "render_runs": renders,
            "touched_zeno_code": any(s["kind"] == "tool" and s["tool"] in ("Edit", "Write", "MultiEdit", "NotebookEdit")
                                     and re.search(r"/(scripts|service|agent|styles)/", s.get("input", "")) for s in steps)}


# ---------------------------------------------------------------- Claude Code
def _claude_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _claude_lines(since: float, until: float, root: Path) -> Iterable[Dict[str, Any]]:
    root_s = str(root.resolve())
    files = [f for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True)
             if os.path.getmtime(f) >= since - 60]
    rows = []
    for f in files:
        try:
            for line in Path(f).read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if not str(d.get("cwd") or "").startswith(root_s):
                    continue
                t = _epoch(d.get("timestamp"))
                if since <= t <= until:
                    d["_t"] = t
                    d["_session"] = Path(f).stem
                    rows.append(d)
        except Exception:
            continue
    rows.sort(key=lambda d: d["_t"])
    return rows


def _from_claude(since: float, until: float, root: Path) -> Optional[Dict[str, Any]]:
    rows = _claude_lines(since, until, root)
    if not rows:
        return None
    prompts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    models: Dict[str, int] = {}
    sessions: List[str] = []
    seen = set()
    for d in rows:
        if d["_session"] not in sessions:
            sessions.append(d["_session"])
        uid = d.get("uuid")
        if uid in seen:
            continue
        seen.add(uid)
        msg = d.get("message") or {}
        at = round(d["_t"], 1)
        if d.get("type") == "user" and not d.get("isMeta"):
            content = msg.get("content")
            if isinstance(content, list):
                for b in content:
                    if isinstance(b, dict) and b.get("type") == "tool_result" and b.get("is_error"):
                        steps.append({"at": at, "kind": "error", "text": _cut(_claude_text(b.get("content")) or str(b.get("content")), MAX_ERROR_CHARS)})
            text = _WRAPPERS.sub("", _claude_text(content))
            text = re.sub(r"<command-name>(.*?)</command-name>\s*(?:<command-args>(.*?)</command-args>)?",
                          lambda m: f"{m.group(1)} {m.group(2) or ''}", text, flags=re.S)
            text = re.sub(r"</?command-[a-z]+>", "", text).strip()
            if text and not d.get("toolUseResult"):
                prompts.append({"at": at, "text": _cut(text, MAX_PROMPT_CHARS)})
                steps.append({"at": at, "kind": "prompt", "text": _cut(text, 300)})
        elif d.get("type") == "assistant":
            if msg.get("model") and not str(msg["model"]).startswith("<"):
                models[msg["model"]] = models.get(msg["model"], 0) + 1
            for b in msg.get("content") or []:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "text" and str(b.get("text") or "").strip():
                    steps.append({"at": at, "kind": "say", "text": _cut(b["text"], MAX_SAY_CHARS)})
                elif b.get("type") == "tool_use":
                    steps.append({"at": at, "kind": "tool", "tool": str(b.get("name") or "")[:80],
                                  "input": _tool_summary(str(b.get("name") or ""), b.get("input"))})
    return {"agent": "Claude Code", "models": models, "sessions": sessions, "prompts": prompts, "steps": steps}


# ---------------------------------------------------------------- Antigravity
def _from_antigravity(since: float, until: float, root: Path) -> Optional[Dict[str, Any]]:
    files = [f for f in glob.glob(os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl"))
             if os.path.getmtime(f) >= since - 60]
    prompts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    sessions: List[str] = []
    marker = root.name
    for f in files:
        try:
            lines = Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        if marker not in "\n".join(lines[-200:]) and "zeno.py" not in "\n".join(lines[-200:]):
            continue
        sessions.append(Path(f).parts[-4] if len(Path(f).parts) >= 4 else Path(f).stem)
        for line in lines:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = _epoch(d.get("created_at"))
            if not since <= t <= until:
                continue
            at = round(t, 1)
            kind = str(d.get("type") or "")
            content = str(d.get("content") or "")
            if kind == "USER_INPUT" and content.strip():
                prompts.append({"at": at, "text": _cut(content, MAX_PROMPT_CHARS)})
                steps.append({"at": at, "kind": "prompt", "text": _cut(content, 300)})
            elif kind == "PLANNER_RESPONSE":
                if content.strip():
                    steps.append({"at": at, "kind": "say", "text": _cut(content, MAX_SAY_CHARS)})
                for call in d.get("tool_calls") or []:
                    if isinstance(call, dict):
                        args = call.get("args") or call.get("arguments") or {}
                        if isinstance(args, str):
                            try:
                                args = json.loads(args)
                            except Exception:
                                pass
                        name = str(call.get("name") or call.get("tool") or "")
                        steps.append({"at": at, "kind": "tool", "tool": name[:80], "input": _tool_summary(name, args)})
            elif kind == "TOOL_RESPONSE" and re.search(r"(?i)\b(error|traceback|exit code [1-9]|failed)\b", content[:2000]):
                steps.append({"at": at, "kind": "error", "text": _cut(content, MAX_ERROR_CHARS)})
    if not prompts and not steps:
        return None
    steps.sort(key=lambda s: s["at"])
    return {"agent": "Antigravity", "models": {}, "sessions": sessions, "prompts": prompts, "steps": steps}


# ---------------------------------------------------------------- entry point
def collect(since: float, until: Optional[float] = None, root: Path = ROOT) -> Dict[str, Any]:
    """The editor-agent conversation inside [since, until]. Empty dict when nothing is found or anything fails."""
    try:
        if not since or since <= 0:
            return {}
        until = until or time.time() + 60
        found = [c for c in (_from_claude(since, until, root), _from_antigravity(since, until, root)) if c]
        if not found:
            return {}
        best = max(found, key=lambda c: len(c["prompts"]) + len(c["steps"]))
        prompts, steps = best["prompts"], best["steps"]
        truncated = len(prompts) > MAX_PROMPTS or len(steps) > MAX_STEPS
        prompts = prompts[:5] + prompts[5:][-(MAX_PROMPTS - 5):] if len(prompts) > MAX_PROMPTS else prompts
        steps = steps[:50] + steps[50:][-(MAX_STEPS - 50):] if len(steps) > MAX_STEPS else steps
        return {"agent": best["agent"], "models": best["models"], "sessions": best["sessions"][:10],
                "window": [round(since, 1), round(until, 1)], "counts": _counts(best["prompts"], best["steps"]),
                "prompts": prompts, "steps": steps, "truncated": truncated}
    except Exception:
        return {}


TAIL_BYTES = 3_000_000


def session_size(root: Path = ROOT, max_age: float = 300.0) -> Optional[Dict[str, Any]]:
    """How big the Claude Code chat that is running Zeno right now already is.

    Every step the agent takes re-sends the whole chat (the "cache read" tokens), so a chat that already holds earlier
    videos makes every step of this one cost several times more. Finds the most recently written Claude Code log whose
    working folder is Zeno and returns the size of its last request: {"tokens", "steps", "session", "file"}.
    Returns None when it cannot tell (another agent, no log, a read error): callers must then let the job go on."""
    try:
        root_s = str(root.resolve())
        now = time.time()
        files = [f for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")) if now - os.path.getmtime(f) <= max_age]
        for f in sorted(files, key=os.path.getmtime, reverse=True)[:6]:
            size = os.path.getsize(f)
            with open(f, "rb") as fh:
                if size > TAIL_BYTES:
                    fh.seek(size - TAIL_BYTES)
                data = fh.read().decode("utf-8", errors="ignore")
            last: Optional[Dict[str, Any]] = None
            ids = set()
            ours = False
            for line in data.splitlines():
                if '"usage"' not in line and '"cwd"' not in line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if str(d.get("cwd") or "").startswith(root_s):
                    ours = True
                m = d.get("message") if d.get("type") == "assistant" else None
                u = (m or {}).get("usage") if isinstance(m, dict) else None
                if u:
                    ids.add(m.get("id"))
                    last = u
            if ours and last:
                tokens = int(last.get("input_tokens") or 0) + int(last.get("cache_creation_input_tokens") or 0) + int(last.get("cache_read_input_tokens") or 0)
                return {"tokens": tokens, "steps": len(ids), "session": Path(f).stem, "file": f}
        return None
    except Exception:
        return None
