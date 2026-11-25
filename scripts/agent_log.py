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
import shlex
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
_AGY_MODEL_RE = re.compile(
    r"setting `Model Selection` from [^\n]+? to ([A-Za-z0-9 ._-]+?)(?:\s*\(|\.\s|\.\n)"
)


_REPO_CODE_DIRS = ("scripts", "tests", "service", "formats", "subtitles", "app")
_NON_CODE_DIRS = ("work", "input", "output")


def _is_repo_code_path(raw_path: str, root: Optional[Path] = None) -> bool:
    """Return True only when `raw_path` resolves inside `root` under a repo code directory (or non-data `.py` file)."""
    s = str(raw_path or "").strip().strip('"').strip("'")
    if not s:
        return False
    norm = s.replace("\\", "/")
    if os.name != "nt" and re.match(r"^[A-Za-z]:/", norm):
        return False
    eff_root = ROOT if root is None else root
    try:
        p = Path(norm)
        root_res = Path(eff_root).resolve()
        cand = (p if p.is_absolute() else (root_res / p)).resolve()
        rel = cand.relative_to(root_res)
    except Exception:
        return False
    if not rel.parts:
        return False
    top = rel.parts[0].lower()
    if top in _REPO_CODE_DIRS:
        return True
    return cand.suffix.lower() == ".py" and top not in _NON_CODE_DIRS


def _bash_touches_repo_code(cmd: str, root: Optional[Path] = None) -> bool:
    eff_root = ROOT if root is None else root
    cleaned = str(cmd or "").replace("scripts/zeno.py", "").replace("scripts/edit_packet.py", "")
    if not any(w in cleaned for w in ("git commit", "git push", "sed -i", ".write_text(", "unittest")):
        return False
    candidates: List[str] = []
    candidates.extend(re.findall(r"'([^']+)'", cleaned))
    candidates.extend(re.findall(r'"([^"]+)"', cleaned))
    try:
        candidates.extend(shlex.split(cleaned))
    except ValueError:
        candidates.extend(cleaned.split())
    for tok in candidates:
        if " " in tok:
            if _is_repo_code_path(tok, eff_root):
                return True
            continue
        for part in (tok, *re.findall(r"[A-Za-z0-9_./\\:-]+", tok)):
            cleaned_tok = part.strip("()[]{},;\"'")
            if cleaned_tok and _is_repo_code_path(cleaned_tok, eff_root):
                return True
    return False


def _norm_cmp_path(val: Any, force_win: bool = False) -> Tuple[str, bool]:
    s = str(val or "").strip().replace("\\", "/")
    is_win = force_win or bool(re.match(r"^[A-Za-z]:(?:/|$)", s)) or (os.name == "nt") or ("\\" in str(val or ""))
    if is_win:
        m_bash = re.match(r"^/([A-Za-z])(?:/(.*))?$", s)
        if m_bash:
            s = f"{m_bash.group(1)}:/{m_bash.group(2) or ''}"
        s = s.lower()
    while len(s) > 1 and s.endswith("/") and not re.match(r"^[A-Za-z]:/$", s):
        s = s[:-1]
    return s, is_win


def _is_under_root(cwd: Any, root: Optional[Path]) -> bool:
    if not root:
        return True
    raw = str(cwd or "").strip()
    if not raw:
        return False
    root_s = str(root)
    is_win_any = (
        os.name == "nt"
        or bool(re.match(r"^[A-Za-z]:[/\\]", raw))
        or bool(re.match(r"^[A-Za-z]:[/\\]", root_s))
        or ("\\" in raw)
        or ("\\" in root_s)
    )
    if is_win_any:
        c_n, _ = _norm_cmp_path(raw, force_win=True)
        r_n, _ = _norm_cmp_path(root_s, force_win=True)
        return c_n == r_n or c_n.startswith(r_n.rstrip("/") + "/")
    try:
        return Path(raw).resolve().is_relative_to(Path(root).resolve())
    except Exception:
        r_s = str(Path(root).resolve()).rstrip("/\\")
        c_s = raw.rstrip("/\\")
        return c_s == r_s or c_s.startswith(r_s + os.sep) or c_s.startswith(r_s + "/")


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


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


def _is_code_touch(step: Dict[str, Any], root: Optional[Path] = None) -> bool:
    if step.get("kind") != "tool":
        return False
    eff_root = ROOT if root is None else root
    tool = str(step.get("tool") or "")
    inp = str(step.get("input") or "")
    if tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "write_to_file", "replace_file_content", "multi_replace_file_content"):
        return _is_repo_code_path(inp.split(" … [+")[0], eff_root)
    if tool in ("Bash", "run_command"):
        return _bash_touches_repo_code(inp, eff_root)
    return False


def _counts(prompts: List[Dict[str, Any]], steps: List[Dict[str, Any]], root: Optional[Path] = None) -> Dict[str, Any]:
    by_tool: Dict[str, int] = {}
    checks = renders = errors = 0
    for s in steps:
        if s["kind"] == "tool":
            by_tool[s["tool"]] = by_tool.get(s["tool"], 0) + 1
            cmd = s.get("input", "")
            checks += "--check-plan" in cmd
            renders += ("--execute" in cmd or "scripts/render.py" in cmd)
        elif s["kind"] == "error":
            errors += 1
    return {"prompts": len(prompts), "tool_calls": sum(by_tool.values()), "by_tool": by_tool, "errors": errors,
            "check_plan_runs": checks, "render_runs": renders,
            "touched_zeno_code": any(_is_code_touch(s, root=root) for s in steps)}


# ---------------------------------------------------------------- Claude Code
def _claude_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text") or "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def _safe_int(val: Any) -> int:
    try:
        return int(val or 0)
    except (TypeError, ValueError):
        return 0


def _claude_lines(since: float, until: float, root: Path, session_id: Optional[str] = None) -> Iterable[Dict[str, Any]]:
    files = [f for f in glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True)
             if _mtime(f) >= since - 60]
    if session_id:
        sid_clean = str(session_id).strip()
        pinned = [f for f in files if Path(f).stem == sid_clean]
        if pinned:
            files = pinned
    rows = []
    for f in files:
        try:
            file_rows: List[Dict[str, Any]] = []
            file_under_root = not root
            for line in Path(f).read_text(encoding="utf-8", errors="ignore").splitlines():
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if root and d.get("cwd") and _is_under_root(d.get("cwd"), root):
                    file_under_root = True
                file_rows.append(d)
            if not file_under_root:
                continue
            for d in file_rows:
                if root and d.get("cwd") and not _is_under_root(d.get("cwd"), root):
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


def _from_claude(since: float, until: float, root: Path, session_id: Optional[str] = None) -> Optional[Dict[str, Any]]:
    rows = _claude_lines(since, until, root, session_id=session_id)
    if not rows:
        return None
    prompts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    models: Dict[str, int] = {}
    sessions: List[str] = []
    seen = set()
    assistant_turns = set()
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
            assistant_turns.add(msg.get("id") or uid or f"row{len(steps)}")
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
    return {
        "agent": "Claude Code",
        "models": models,
        "sessions": sessions,
        "prompts": prompts,
        "steps": steps,
        "turns": len(assistant_turns) if assistant_turns else None,
    }


# ---------------------------------------------------------------- Antigravity
def _is_zeno_transcript(tail: str, root: Path = ROOT) -> bool:
    marker = root.name
    return (
        marker in tail
        or "zeno.py" in tail
        or "Zeno" in tail
    )


def antigravity_window_usage(
    lines: Iterable[str],
    cutoff: float,
    ceiling: float,
    session_id: str = "unknown",
    root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Parse Antigravity transcript lines inside [cutoff, ceiling] for token usage and planner turn count."""
    try:
        eff_root = ROOT if root is None else root
        model_name = "unknown"
        window_count = 0
        in_chars = 0
        out_chars = 0
        code_edits = 0
        step_epochs: List[float] = []
        seen_turns = set()
        planner_usage: Dict[Any, List[int]] = {}
        planner_meta: Dict[Any, Dict[str, Any]] = {}

        for n, line in enumerate(lines):
            if not line or not line.strip():
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if not isinstance(d, dict):
                continue

            if "USER_SETTINGS_CHANGE" in line and "Model Selection" in line:
                content_str = str(d.get("content") or "")
                m = _AGY_MODEL_RE.search(content_str)
                if m:
                    cand_model = m.group(1).strip()
                    if len(cand_model) >= 4:
                        model_name = cand_model

            step_ts = _epoch(d.get("created_at"))
            if not (cutoff <= step_ts <= ceiling):
                continue

            window_count += 1
            if step_ts > 0:
                step_epochs.append(step_ts)
            st = str(d.get("type") or "")
            if st in ("USER_INPUT", "GENERIC", "SYSTEM_MESSAGE", "TOOL_RESPONSE", "SYSTEM"):
                in_chars += len(str(d.get("content") or ""))
            elif st == "PLANNER_RESPONSE":
                step_idx = d.get("step_index")
                turn_key = ("step", step_idx) if isinstance(step_idx, (int, str)) else ("line", n)
                if turn_key not in seen_turns:
                    seen_turns.add(turn_key)
                    out_chars += (
                        len(str(d.get("content") or ""))
                        + len(json.dumps(d.get("tool_calls") or [], ensure_ascii=False))
                    )
                for call in d.get("tool_calls") or []:
                    if not isinstance(call, dict):
                        continue
                    tname = str(call.get("name") or call.get("tool") or "").strip()
                    raw_args = call.get("args") if call.get("args") is not None else call.get("arguments")
                    if isinstance(raw_args, str):
                        try:
                            raw_args = json.loads(raw_args)
                        except Exception:
                            raw_args = {}
                    args = raw_args if isinstance(raw_args, dict) else {}
                    if tname in (
                        "edit_file",
                        "write_file",
                        "write_to_file",
                        "replace_file_content",
                        "multi_replace_file_content",
                        "Edit",
                        "Write",
                        "MultiEdit",
                        "NotebookEdit",
                    ):
                        target_path = ""
                        for pk in ("Path", "TargetFile", "AbsolutePath", "file_path", "notebook_path"):
                            if args.get(pk):
                                target_path = str(args.get(pk))
                                break
                        if target_path and _is_repo_code_path(target_path, eff_root):
                            code_edits += 1
                    elif tname in ("run_command", "Bash"):
                        cmd = str(args.get("CommandLine") or args.get("command") or "")
                        if cmd and _bash_touches_repo_code(cmd, eff_root):
                            code_edits += 1
                in_tok = _safe_int(d.get("input_tokens"))
                cache_tok = _safe_int(d.get("cache_read_tokens"))
                out_tok = _safe_int(d.get("output_tokens"))
                if "input_tokens" in d and "output_tokens" in d:
                    prev = planner_usage.get(turn_key)
                    if prev is None:
                        planner_usage[turn_key] = [in_tok, cache_tok, out_tok]
                        planner_meta[turn_key] = {"timestamp": step_ts, "model": model_name}
                    else:
                        prev[0] = max(prev[0], in_tok)
                        prev[1] = max(prev[1], cache_tok)
                        prev[2] = max(prev[2], out_tok)

        if not window_count:
            return None

        usage_rows = list(planner_usage.values())
        turns = len(seen_turns) if seen_turns else None
        unknown_requests = len(seen_turns - planner_usage.keys())
        requests = [
            {**planner_meta[key], "request_id": str(key[1]),
             "input_tokens": row[0] + row[1], "cached_input_tokens": row[1],
             "cache_write_input_tokens": 0, "output_tokens": row[2],
             "context": row[0] + row[1]}
            for key, row in planner_usage.items()
        ]
        if usage_rows:
            edit_in_tok = sum(r[0] for r in usage_rows)
            cached_in = sum(r[1] for r in usage_rows)
            edit_out_tok = sum(r[2] for r in usage_rows)
            first_ctx = usage_rows[0][0] + usage_rows[0][1]
            last_ctx = usage_rows[-1][0] + usage_rows[-1][1]
        else:
            edit_in_tok = max(1, in_chars // 4)
            cached_in = 0
            edit_out_tok = max(1, out_chars // 4)
            first_ctx = 0
            last_ctx = 0

        window_minutes = round((max(step_epochs) - min(step_epochs)) / 60.0, 1) if len(step_epochs) >= 2 else 0.0

        return {
            "agent_name": "Antigravity",
            "agent_model": model_name,
            "steps_count": turns if turns is not None else window_count,
            "turns": turns,
            "context_start": first_ctx,
            "context_end": last_ctx,
            "input_tokens": edit_in_tok,
            "cached_tokens": cached_in,
            "output_tokens": edit_out_tok,
            "total_tokens": edit_in_tok + edit_out_tok,
            "total_tokens_including_cache": edit_in_tok + cached_in + edit_out_tok,
            "token_precision": ("partial" if unknown_requests else "exact") if usage_rows else "est",
            "host_usage_unknown": bool(unknown_requests or not usage_rows),
            "unknown_request_count": unknown_requests,
            "requests": requests,
            "code_edits": code_edits,
            "window_minutes": window_minutes,
            "mixed_with_development": code_edits > 0,
            "session_id": session_id,
        }
    except Exception:
        return None


def antigravity_turns(since: float, until: Optional[float] = None, root: Path = ROOT) -> Optional[int]:
    """Count PLANNER_RESPONSE turns for the active Antigravity run in [since, until]. None when unavailable."""
    try:
        if not since or since <= 0:
            return None
        until = until or time.time() + 60
        now = time.time()
        pattern = os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl")
        files = sorted(glob.glob(pattern), key=_mtime, reverse=True)
        if not files:
            return None
        fallback: Optional[int] = None
        for path_str in files[:6]:
            mtime = _mtime(path_str)
            if mtime < since - 60:
                continue
            try:
                lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            except Exception:
                continue
            if not lines:
                continue
            tail = "\n".join(lines[-200:])
            is_ours = _is_zeno_transcript(tail, root)
            if len(files) > 1 and not is_ours and (now - mtime > 300):
                continue
            sess_id = Path(path_str).parts[-4] if len(Path(path_str).parts) >= 4 else "unknown"
            usage = antigravity_window_usage(lines, since, until, session_id=sess_id)
            if usage is None:
                continue
            turns = usage.get("turns")
            if is_ours or len(files) == 1:
                return turns
            if fallback is None and turns is not None:
                fallback = turns
        return fallback
    except Exception:
        return None


def _from_antigravity(since: float, until: float, root: Path) -> Optional[Dict[str, Any]]:
    files = [f for f in glob.glob(os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl"))
             if _mtime(f) >= since - 60]
    prompts: List[Dict[str, Any]] = []
    steps: List[Dict[str, Any]] = []
    sessions: List[str] = []
    turn_keys = set()
    for f in files:
        try:
            lines = Path(f).read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        if not _is_zeno_transcript("\n".join(lines[-200:]), root):
            continue
        sess = Path(f).parts[-4] if len(Path(f).parts) >= 4 else Path(f).stem
        sessions.append(sess)
        for idx, line in enumerate(lines):
            if not line.strip():
                continue
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
                step_idx = d.get("step_index")
                turn_key = (sess, "step", step_idx) if isinstance(step_idx, (int, str)) else (sess, "line", idx)
                turn_keys.add(turn_key)
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
    if not prompts and not steps and not turn_keys:
        return None
    steps.sort(key=lambda s: s["at"])
    return {
        "agent": "Antigravity",
        "models": {},
        "sessions": sessions,
        "prompts": prompts,
        "steps": steps,
        "turns": len(turn_keys) if turn_keys else None,
    }


# ---------------------------------------------------------------- entry point
def collect(since: float, until: Optional[float] = None, root: Path = ROOT, session_id: Optional[str] = None) -> Dict[str, Any]:
    """The editor-agent conversation inside [since, until]. Empty dict when nothing is found or anything fails."""
    try:
        if not since or since <= 0:
            return {}
        until = until or time.time() + 60
        claude_res = _from_claude(since, until, root, session_id=session_id)
        if session_id and claude_res and session_id in (claude_res.get("sessions") or []):
            best = claude_res
        else:
            found = [c for c in (claude_res, _from_antigravity(since, until, root)) if c]
            if not found:
                return {}
            best = max(found, key=lambda c: len(c["prompts"]) + len(c["steps"]))
        prompts, steps = best["prompts"], best["steps"]
        truncated = len(prompts) > MAX_PROMPTS or len(steps) > MAX_STEPS
        prompts = prompts[:5] + prompts[5:][-(MAX_PROMPTS - 5):] if len(prompts) > MAX_PROMPTS else prompts
        steps = steps[:50] + steps[50:][-(MAX_STEPS - 50):] if len(steps) > MAX_STEPS else steps
        turns = best.get("turns")
        counts = _counts(best["prompts"], best["steps"], root=root)
        if turns is not None:
            counts["turns"] = turns
        return {"agent": best["agent"], "models": best["models"], "sessions": best["sessions"][:10],
                "window": [round(since, 1), round(until, 1)], "counts": counts,
                "turns": turns,
                "prompts": prompts, "steps": steps, "truncated": truncated}
    except Exception:
        return {}


TAIL_BYTES = 3_000_000


def chat_size(path: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Size of the last request of one Claude Code chat log: {"tokens", "steps", "session", "file"}.
    With `root`, only a chat whose working folder is inside `root` counts. None when it cannot tell."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > TAIL_BYTES:
                fh.seek(size - TAIL_BYTES)
            data = fh.read().decode("utf-8", errors="ignore")
        last: Optional[Dict[str, Any]] = None
        ids = set()
        ours = not root
        for line in data.splitlines():
            if '"usage"' not in line and '"cwd"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if root and _is_under_root(d.get("cwd"), root):
                ours = True
            m = d.get("message") if d.get("type") == "assistant" else None
            u = (m or {}).get("usage") if isinstance(m, dict) else None
            if u:
                ids.add(m.get("id"))
                last = u
        if not (ours and last):
            return None
        tokens = int(last.get("input_tokens") or 0) + int(last.get("cache_creation_input_tokens") or 0) + int(last.get("cache_read_input_tokens") or 0)
        return {"tokens": tokens, "steps": len(ids), "session": Path(path).stem, "file": str(path)}
    except Exception:
        return None


def session_size(root: Path = ROOT, max_age: float = 300.0) -> Optional[Dict[str, Any]]:
    """How big the Claude Code chat that is running Zeno right now already is.

    Every step the agent takes re-sends the whole chat (the "cache read" tokens), so a chat that already holds earlier
    videos makes every step of this one cost several times more. Finds the most recently written Claude Code log whose
    working folder is Zeno and returns the size of its last request: {"tokens", "steps", "session", "file"}. Inside Claude
    Code (CLAUDECODE=1) it measures the chat named by CLAUDE_CODE_SESSION_ID; outside Claude Code (e.g. a standalone
    terminal run) there is no chat to protect, so it returns None. Returns None also when it cannot tell (another agent, no log, a read error): callers must then let the job go on."""
    try:
        if os.getenv("CODEX_THREAD_ID") and not os.getenv("CLAUDECODE"):
            import codex_usage
            usage = codex_usage.detect(time.time() - max_age, root=root)
            if usage and usage.get("context_end"):
                return {"tokens": usage["context_end"], "steps": usage["steps_count"],
                        "session": usage["session_id"], "agent": "Codex"}
            return None
        if not os.environ.get("CLAUDECODE"):        # run from a terminal or by Zeno itself: no chat is paying for it
            return None
        sid = os.environ.get("CLAUDE_CODE_SESSION_ID", "").strip()
        if sid:                                      # Claude Code names its own chat: measure exactly that one
            exact = glob.glob(os.path.expanduser(f"~/.claude/projects/*/{glob.escape(sid)}.jsonl"))
            return chat_size(exact[0], root) if exact else None
        now = time.time()
        files = [f for f in glob.glob(os.path.expanduser("~/.claude/projects/*/*.jsonl")) if now - os.path.getmtime(f) <= max_age]
        for f in sorted(files, key=os.path.getmtime, reverse=True)[:6]:
            r = chat_size(f, root)
            if r:
                return r
        return None
    except Exception:
        return None
