#!/usr/bin/env python3
"""Automated Per-Clip & Per-Agent Token Telemetry Tracker for Zeno.

Tracks and stores:
1. Per-clip raw process telemetry during `prepare.py`:
   - Clip name, raw duration -> kept duration, words aligned, script lines matched,
     anomalies pruned ([RETAKE], [TAIL_BABBLE_CUT], [PAUSE_SPLIT], [BOUNDARY_DUP]),
     and Gemini transcription tokens (input_tokens, output_tokens, total_tokens).
2. Active AI Agent & Model telemetry during `prepare.py` -> `render.py`:
   - Reads the named Codex chat's counters, or scoped Claude/Antigravity harness usage.
   - Computes agent input tokens, cached tokens, output tokens, and total tokens spent on the job.
3. Persists every run to:
   - `work/<project>/token_telemetry.json` & `work/<project>/token_report.md`
   - Local SQL database: `work/zeno_telemetry.db` (`job_runs` and `clip_runs` tables)
   - Local markdown ledger: `work/TOKEN_LEDGER.md`
   - Railway PostgreSQL database via `POST /v1/telemetry` (when `.zeno_session.json` or `ZENO_CLOUD_URL` + `ZENO_CLOUD_TOKEN` are set)

CLI usage:
    .venv/bin/python scripts/token_tracker.py --project <project_name> [--refresh] [--no-cloud]
    .venv/bin/python scripts/token_tracker.py --list
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import OUTPUT, ROOT, WORK, read_json, tc, write_json  # noqa: E402

LEDGER_MD = WORK / "TOKEN_LEDGER.md"
LOCAL_DB = WORK / "zeno_telemetry.db"
MAX_EDIT_WINDOW_SEC = 4 * 3600


def _iso_to_epoch(ts_str: str) -> float:
    if not ts_str:
        return 0.0
    try:
        return dt.datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _is_repo_code_path(raw_path: str, root: Optional[Path] = None) -> bool:
    """Return True only when `raw_path` resolves inside `root` under a repo code directory (or non-data `.py` file)."""
    import agent_log

    return agent_log._is_repo_code_path(raw_path, ROOT if root is None else root)


def claude_window_usage(
    lines: List[str],
    cutoff: float,
    ceiling: float,
    root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    """Exact token sums of the assistant turns of one Claude Code transcript that fall inside [cutoff, ceiling]: fresh input, cache writes, cache
    reads and output, each summed over every turn (the old report took the last turn's cache and the growth of the context). It also notes whether the
    window holds work on Zeno's own code, in which case the number is not the cost of one edit."""
    # Claude Code writes one log line per content block of a response (each parallel tool call is its own line) and every line repeats that response's
    # usage, so lines are grouped by message id and each response is counted once, taking the largest value of each field.
    by_id: Dict[str, List[Any]] = {}
    order: List[str] = []
    code_edits = 0
    model_name = "unknown"
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") != "assistant" or not isinstance(d.get("message"), dict):
            continue
        msg = d["message"]
        u = msg.get("usage") or {}
        if not u:
            continue
        ts = _iso_to_epoch(str(d.get("timestamp") or ""))
        if not (cutoff <= ts <= ceiling):
            continue
        raw_model = str(msg.get("model") or "").strip()
        if raw_model and not raw_model.startswith("<"):
            model_name = raw_model
            turn_model = raw_model
        else:
            turn_model = model_name
        out_details = u.get("output_tokens_details")
        think_val = int((out_details if isinstance(out_details, dict) else {}).get("thinking_tokens") or 0)
        cc_details = u.get("cache_creation")
        eph1h_val = int((cc_details if isinstance(cc_details, dict) else {}).get("ephemeral_1h_input_tokens") or 0)
        row_usage = {
            "input_tokens": int(u.get("input_tokens") or 0),
            "cache_creation_input_tokens": int(u.get("cache_creation_input_tokens") or 0),
            "cache_read_input_tokens": int(u.get("cache_read_input_tokens") or 0),
            "output_tokens": int(u.get("output_tokens") or 0),
            "thinking_tokens": think_val,
            "ephemeral_1h_input_tokens": eph1h_val,
        }
        mid = str(msg.get("id") or f"line{n}")
        cur = by_id.get(mid)
        if cur is None:
            by_id[mid] = [ts, row_usage, turn_model]
            order.append(mid)
        else:
            for k, val in row_usage.items():
                cur[1][k] = max(cur[1][k], val)
            if turn_model != "unknown":
                cur[2] = turn_model
        eff_root = ROOT if root is None else root
        for blk in msg.get("content") or []:
            if isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                inp_dict = blk.get("input") or {}
                raw_fp = str(inp_dict.get("file_path") or inp_dict.get("notebook_path") or "")
                if _is_repo_code_path(raw_fp, eff_root):
                    code_edits += 1
            elif isinstance(blk, dict) and blk.get("type") == "tool_use" and blk.get("name") == "Bash":
                import agent_log
                cmd = str((blk.get("input") or {}).get("command") or "")
                if agent_log._bash_touches_repo_code(cmd, eff_root):
                    code_edits += 1
    turns = sorted((by_id[m] for m in order), key=lambda item: item[0])
    if not turns:
        return None
    g = lambda u, k: int(u.get(k) or 0)   # noqa: E731
    fresh = sum(g(u, "input_tokens") for _, u, _ in turns)
    write = sum(g(u, "cache_creation_input_tokens") for _, u, _ in turns)
    read = sum(g(u, "cache_read_input_tokens") for _, u, _ in turns)
    out = sum(g(u, "output_tokens") for _, u, _ in turns)
    sum_thinking = sum(g(u, "thinking_tokens") for _, u, _ in turns)
    first, last = turns[0][1], turns[-1][1]
    ctx = lambda u: g(u, "input_tokens") + g(u, "cache_creation_input_tokens") + g(u, "cache_read_input_tokens")   # noqa: E731
    requests = []
    for ts_epoch, u_dict, t_model in turns:
        fresh_i = g(u_dict, "input_tokens")
        write_i = g(u_dict, "cache_creation_input_tokens")
        read_i = g(u_dict, "cache_read_input_tokens")
        eph1h_i = g(u_dict, "ephemeral_1h_input_tokens")
        out_i = g(u_dict, "output_tokens")
        think_i = g(u_dict, "thinking_tokens")
        ctx_i = fresh_i + read_i + write_i
        requests.append({
            "timestamp": ts_epoch,
            "model": t_model if t_model != "unknown" else model_name,
            "input_tokens": ctx_i,
            "cached_input_tokens": read_i,
            "cache_write_input_tokens": write_i,
            "ephemeral_1h_input_tokens": eph1h_i,
            "output_tokens": out_i,
            "reasoning_output_tokens": think_i,
            "context": ctx_i,
        })
    return {
        "agent_name": "Claude Code",
        "agent_model": model_name,
        "steps_count": len(turns),
        "turns": len(turns),
        "context_start": ctx(first),
        "context_end": ctx(last),
        "input_tokens": fresh + write,
        "cache_write_tokens": write,
        "cached_tokens": read,
        "output_tokens": out,
        "reasoning_tokens": sum_thinking,
        "total_tokens": fresh + write + out,
        "total_tokens_including_cache": fresh + write + read + out,
        "token_precision": "exact" if requests else "unavailable",
        "requests": requests,
        "code_edits": code_edits,
        "window_minutes": round((turns[-1][0] - turns[0][0]) / 60.0, 1),
        "mixed_with_development": code_edits > 0,
    }


_DELEGATION_TOOL_NAMES = frozenset({
    "invoke_subagent",
    "send_message",
    "manage_subagents",
    "Agent",
    "Task",
    "SendMessage",
    "Subagent",
})


def _transcript_hint_signals(
    lines: List[str],
    raw_full: str,
    hint: str,
    cutoff: float,
    ceiling: float,
    *,
    kind: str,
) -> Tuple[bool, bool, bool, bool, bool]:
    """Return (in_tool_strict, in_window, in_tool_near, in_full, in_doc_tool) for `hint`."""
    if not hint:
        return (False, False, False, False, False)
    alt_doc = ""
    if hint.startswith("gdoc_"):
        parts = hint.split("_")
        if len(parts) >= 2 and len(parts[1]) >= 8:
            alt_doc = parts[1]
    has_hint = hint in raw_full
    has_alt = bool(alt_doc and alt_doc in raw_full)
    if not has_hint and not has_alt:
        return (False, False, False, False, False)

    in_tool_strict = False
    in_window = False
    in_tool_near = False
    in_doc_tool = False
    for line in lines:
        line_has_hint = has_hint and hint in line
        line_has_alt = has_alt and alt_doc in line
        if not line_has_hint and not line_has_alt:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        if not isinstance(d, dict):
            continue
        if kind == "antigravity":
            import agent_log

            ts = agent_log._epoch(d.get("created_at"))
        else:
            ts = _iso_to_epoch(str(d.get("timestamp") or ""))
        in_win = cutoff <= ts <= ceiling
        near_win = (cutoff - 60.0) <= ts <= ceiling
        if line_has_hint and in_win:
            in_window = True
        if not near_win:
            continue
        if kind == "antigravity" and d.get("type") == "PLANNER_RESPONSE":
            for call in d.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                tname = str(call.get("name") or call.get("tool") or "").strip()
                if tname in _DELEGATION_TOOL_NAMES:
                    continue
                call_txt = json.dumps(call, ensure_ascii=False)
                if line_has_hint and hint in call_txt:
                    in_tool_near = True
                    if in_win:
                        in_tool_strict = True
                elif line_has_alt and alt_doc in call_txt and in_win:
                    in_doc_tool = True
        elif kind == "claude" and d.get("type") == "assistant":
            msg = d.get("message") or {}
            for blk in (msg.get("content") if isinstance(msg, dict) else None) or []:
                if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                    continue
                tname = str(blk.get("name") or "").strip()
                if tname in _DELEGATION_TOOL_NAMES:
                    continue
                blk_txt = json.dumps(blk, ensure_ascii=False)
                if line_has_hint and hint in blk_txt:
                    in_tool_near = True
                    if in_win:
                        in_tool_strict = True
                elif line_has_alt and alt_doc in blk_txt and in_win:
                    in_doc_tool = True
    return (in_tool_strict, in_window, in_tool_near, has_hint, in_doc_tool)


def _cand_rank(
    in_tool_strict: bool,
    in_window: bool,
    in_tool_near: bool,
    has_project_hint: bool,
    in_doc_tool: bool,
    is_matched_sid: bool,
    got: Dict[str, Any],
    mtime: float,
) -> Tuple[bool, bool, bool, bool, bool, bool, bool, float]:
    mixed = bool(got.get("mixed_with_development"))
    return (
        in_tool_strict,
        in_window,
        in_tool_near,
        has_project_hint,
        in_doc_tool,
        is_matched_sid,
        not mixed,
        mtime,
    )



_CLAUDE_ENV_KEYS = ("CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT", "CLAUDE_CODE_SESSION_ID", "CLAUDE_CODE_SSE_PORT")


def _model_from_claude_transcript(session_id: str) -> str:
    """Last assistant `message.model` of this Claude Code session (distinguishes Sonnet from Opus); '' when unreadable."""
    sid = str(session_id or "").strip()
    if not sid:
        return ""
    try:
        files = sorted(
            glob.glob(os.path.expanduser(f"~/.claude/projects/**/{glob.escape(sid)}.jsonl"), recursive=True),
            key=os.path.getmtime, reverse=True,
        )
        if not files:
            return ""
        size = os.path.getsize(files[0])
        with open(files[0], "rb") as fh:
            if size > 400_000:
                fh.seek(size - 400_000)
            tail = fh.read().decode("utf-8", errors="ignore").splitlines()
        for line in reversed(tail):
            if '"model"' not in line:
                continue
            try:
                m = (json.loads(line).get("message") or {}).get("model")
            except Exception:
                continue
            m = str(m or "").strip()
            if m and not m.startswith("<"):
                return m
    except Exception:
        pass
    return ""


def _codex_config_model() -> str:
    try:
        home = Path(os.getenv("CODEX_HOME") or Path.home() / ".codex")
        for line in (home / "config.toml").read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.match(r"\s*model\s*=\s*[\"']([^\"']+)[\"']", line)
            if m:
                return m.group(1).strip()
    except Exception:
        pass
    return ""


def detect_agent_env(environ: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """Cheap environment-based host detection (no transcript scan): {agent_name, agent_model, session_id, agent_detect_reason}.
    agent_name is 'unknown' with a reason when nothing identifies the host."""
    env = os.environ if environ is None else environ
    forced = str(env.get("ZENO_AGENT") or "").strip()
    model_env = str(env.get("ZENO_MODEL") or "").strip()
    if forced and forced.lower() != "unknown":
        return {"agent_name": forced, "agent_model": model_env or "unknown", "session_id": "", "agent_detect_reason": "env:ZENO_AGENT"}
    hit = [k for k in _CLAUDE_ENV_KEYS if str(env.get(k) or "").strip()]
    if hit:
        sid = str(env.get("CLAUDE_CODE_SESSION_ID") or "").strip()
        model = model_env or str(env.get("ANTHROPIC_MODEL") or "").strip() or (_model_from_claude_transcript(sid) if environ is None else "")
        return {"agent_name": "Claude Code", "agent_model": model or "unknown", "session_id": sid, "agent_detect_reason": "env:" + hit[0]}
    codex = [k for k in env if str(k).startswith("CODEX_") and str(env.get(k) or "").strip()]
    if codex:
        model = model_env or (_codex_config_model() if environ is None else "")
        return {"agent_name": "Codex", "agent_model": model or "unknown", "session_id": str(env.get("CODEX_THREAD_ID") or ""),
                "agent_detect_reason": "env:" + sorted(codex)[0]}
    if any(str(env.get(k) or "").strip() for k in ("ANTIGRAVITY_AGENT", "ANTIGRAVITY_SESSION_ID", "GEMINI_CLI")):
        return {"agent_name": "Antigravity", "agent_model": model_env or "unknown", "session_id": "", "agent_detect_reason": "env:antigravity"}
    return {"agent_name": "unknown", "agent_model": model_env or "unknown", "session_id": "",
            "agent_detect_reason": "no agent env vars (CLAUDECODE/CLAUDE_CODE_*/CODEX_*) and no matching transcript"}


def _is_unknown(v: Any) -> bool:
    return str(v or "").strip().lower() in ("", "unknown", "none", "n/a")


def _detect_agent_usage_impl(
    since_epoch: Optional[float] = None,
    until_epoch: Optional[float] = None,
    session_id: Optional[str] = None,
    project_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Auto-detect which AI agent harness (Antigravity vs Claude Code) and model are active,
    and measure ONLY the input/output tokens spent during this specific video edit window.
    """
    now = time.time()
    cutoff = since_epoch if (since_epoch and since_epoch > 0) else (now - 900.0)
    ceiling = until_epoch if (until_epoch and until_epoch > 0) else (now + 60.0)
    hint = str(project_hint or "").strip()

    import codex_usage
    codex_thread = os.getenv("CODEX_THREAD_ID", "").strip()
    clean_sid = str(session_id).strip() if (session_id and str(session_id).strip() not in ("", "local", "unknown")) else None
    codex_sid = clean_sid if not os.getenv("CLAUDECODE") else None
    use_codex = not bool(os.getenv("CLAUDECODE"))
    codex = codex_usage.detect(cutoff, ceiling, ROOT, session_id=codex_sid) if use_codex else None
    if codex is not None:
        return codex
    if codex_thread and not os.getenv("CLAUDECODE"):
        return {"agent_name": "Codex", "agent_model": os.getenv("ZENO_MODEL", "unknown"),
                "steps_count": 0, "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0,
                "total_tokens": 0, "token_precision": "unavailable",
                "host_usage_unknown": True,
                "session_id": codex_sid or codex_thread}

    candidates: List[Tuple[Tuple[bool, bool, bool, bool, bool, bool, bool, float], Dict[str, Any]]] = []

    # 1. Check Antigravity transcripts (~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl)
    agy_pattern = os.path.expanduser("~/.gemini/antigravity/brain/*/.system_generated/logs/transcript.jsonl")
    agy_files = [] if os.getenv("CLAUDECODE") else sorted(glob.glob(agy_pattern), key=os.path.getmtime, reverse=True)
    if clean_sid and agy_files:
        matched_agy = [p for p in agy_files if len(Path(p).parts) >= 4 and Path(p).parts[-4] == clean_sid]
        other_agy = [p for p in agy_files if not (len(Path(p).parts) >= 4 and Path(p).parts[-4] == clean_sid)]
        ordered_agy = matched_agy + other_agy[:12]
    else:
        ordered_agy = agy_files[:12]
    fallback_agy: Optional[Tuple[Tuple[bool, bool, bool, bool, bool, bool, bool, float], Dict[str, Any]]] = None
    agy_matched_any = False
    for path_str in ordered_agy:
        try:
            mtime = os.path.getmtime(path_str)
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            if not lines:
                continue
            raw_full = "\n".join(lines)
            is_zeno = ROOT.name in raw_full or "Zeno" in raw_full or "zeno.py" in raw_full
            sess_id = Path(path_str).parts[-4] if len(Path(path_str).parts) >= 4 else "unknown"
            in_tool_strict, in_window, in_tool_near, has_project_hint, in_doc_tool = _transcript_hint_signals(
                lines, raw_full, hint, cutoff, ceiling, kind="antigravity"
            )
            is_matched_sid = bool(clean_sid and sess_id == clean_sid and (not hint or has_project_hint))
            if len(agy_files) > 1 and not is_matched_sid and not is_zeno and not has_project_hint and (now - mtime > 300):
                continue

            import agent_log
            got = agent_log.antigravity_window_usage(lines, cutoff, ceiling, session_id=sess_id)
            if got is not None:
                rank = _cand_rank(
                    in_tool_strict, in_window, in_tool_near, has_project_hint, in_doc_tool, is_matched_sid, got, mtime
                )
                if is_matched_sid or is_zeno or has_project_hint or len(agy_files) == 1:
                    candidates.append((rank, got))
                    agy_matched_any = True
                elif fallback_agy is None or rank > fallback_agy[0]:
                    fallback_agy = (rank, got)
        except Exception:
            continue
    if fallback_agy is not None and not agy_matched_any:
        candidates.append(fallback_agy)

    # 2. Check Claude Code transcripts (~/.claude/projects/**/*.jsonl, including subagents/*.jsonl)
    claude_sid = os.getenv("CLAUDE_CODE_SESSION_ID", "").strip() or (clean_sid or "")
    claude_sid_files: List[str] = []
    if claude_sid:
        claude_sid_files = sorted(
            glob.glob(os.path.expanduser(f"~/.claude/projects/**/{glob.escape(claude_sid)}.jsonl"), recursive=True),
            key=os.path.getmtime,
            reverse=True,
        )
    all_claude_files = sorted(
        glob.glob(os.path.expanduser("~/.claude/projects/**/*.jsonl"), recursive=True),
        key=os.path.getmtime,
        reverse=True,
    )
    seen_claude: set[str] = set()
    claude_scan_list: List[Tuple[str, bool]] = []
    for p in claude_sid_files:
        rp = os.path.realpath(p)
        if rp not in seen_claude:
            seen_claude.add(rp)
            claude_scan_list.append((p, False))
    for p in all_claude_files[:25]:
        rp = os.path.realpath(p)
        if rp not in seen_claude:
            seen_claude.add(rp)
            claude_scan_list.append((p, True))

    for path_str, require_recent in claude_scan_list:
        try:
            mtime = os.path.getmtime(path_str)
            if require_recent and mtime < cutoff - 60:
                continue
            lines = Path(path_str).read_text(encoding="utf-8", errors="ignore").splitlines()
            if not lines:
                continue
            raw_full = "\n".join(lines)
            import agent_log
            is_subagent = "/subagents/" in path_str.replace("\\", "/")
            # A transcript named by this process's own CLAUDE_CODE_SESSION_ID is trusted even when Claude Code was started outside Zeno's folder.
            if require_recent and not (agent_log.chat_size(path_str, ROOT) or (is_subagent and ROOT.name in raw_full)):
                continue
            got = claude_window_usage(lines, cutoff, ceiling)
            if got is None:
                continue
            sess_id = Path(path_str).stem
            got["session_id"] = sess_id
            in_tool_strict, in_window, in_tool_near, has_project_hint, in_doc_tool = _transcript_hint_signals(
                lines, raw_full, hint, cutoff, ceiling, kind="claude"
            )
            is_matched_sid = bool(claude_sid and sess_id == claude_sid and (not hint or has_project_hint))
            rank = _cand_rank(
                in_tool_strict, in_window, in_tool_near, has_project_hint, in_doc_tool, is_matched_sid, got, mtime
            )
            candidates.append((rank, got))
        except Exception:
            continue

    if not candidates:
        return {
            "agent_name": os.getenv("ZENO_AGENT", "unknown"),
            "agent_model": os.getenv("ZENO_MODEL", "unknown"),
            "steps_count": 0,
            "context_start": 0,
            "context_end": 0,
            "input_tokens": 0,
            "cached_tokens": 0,
            "output_tokens": 0,
            "total_tokens": 0,
            "token_precision": "unavailable",
            "host_usage_unknown": True,
            "session_id": "local",
        }

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def detect_agent_usage(
    since_epoch: Optional[float] = None,
    until_epoch: Optional[float] = None,
    session_id: Optional[str] = None,
    project_hint: Optional[str] = None,
) -> Dict[str, Any]:
    """Transcript-based usage (see _detect_agent_usage_impl) with environment detection first for identity and as the fallback,
    so a job reports agent + model even when no transcript window matches. Unresolved hosts say why in agent_detect_reason."""
    env_info = detect_agent_env()
    got = _detect_agent_usage_impl(since_epoch, until_epoch, session_id, project_hint)
    if not isinstance(got, dict):
        got = {}
    if _is_unknown(got.get("agent_name")):
        if not _is_unknown(env_info["agent_name"]):
            got["agent_name"] = env_info["agent_name"]
            got["agent_detect_reason"] = env_info["agent_detect_reason"] + "; no usable transcript window"
        else:
            got["agent_name"] = "unknown"
            got["agent_detect_reason"] = env_info["agent_detect_reason"]
    else:
        got.setdefault("agent_detect_reason", "transcript")
    if _is_unknown(got.get("agent_model")) and not _is_unknown(env_info["agent_model"]) and (
        _is_unknown(got.get("agent_name")) or str(got["agent_name"]).lower() == env_info["agent_name"].lower()
    ):
        got["agent_model"] = env_info["agent_model"]
    got.setdefault("agent_model", "unknown")
    return got


def collect_clip_telemetry(
    clips_data: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Build per-clip raw process + token usage entries from `prepare.py` structures."""
    out: List[Dict[str, Any]] = []
    for c in clips_data:
        stem = str(c.get("stem") or "")
        wdir = Path(c.get("work_dir") or (WORK / stem))
        info = c.get("info") or {}
        raw_dur = round(float(info.get("duration") or 0.0), 2)
        c_segs = [s for s in segments if str(s.get("src", "")) == str(c.get("src", ""))]
        kept_dur = round(sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in c_segs), 2)
        words = c.get("hq_words") or []
        l_ids = ",".join(l["id"] for l in c.get("matched_lines", [])) or "-"
        segs_str = " + ".join(f"{s['id']}[{float(s['start']):.2f}-{float(s['end']):.2f}s]" for s in c_segs) or ("unused take" if c.get("dropped_take") else "dropped")
        anoms = list(c.get("anomalies") or [])

        # Read Gemini transcription token usage from work/<stem>/gemini_transcript.json
        gt = {}
        gt_path = wdir / "gemini_transcript.json"
        model_used = "offline_omniasr_ctc"
        in_tok = 0
        out_tok = 0
        tot_tok = 0
        if gt_path.is_file():
            try:
                gt = read_json(gt_path)
                model_used = str(gt.get("model_used") or "gemini-3.5-flash-lite")
                usage = gt.get("usage") if isinstance(gt.get("usage"), dict) else {}
                in_tok = int(usage.get("input_tokens") or 0)
                out_tok = int(usage.get("output_tokens") or 0)
                tot_tok = int(usage.get("total_tokens") or (in_tok + out_tok))
            except Exception:
                pass

        out.append({
            "clip": stem,
            "src": str(c.get("src") or f"input/footage/{stem}.mp4"),
            "raw_seconds": raw_dur,
            "kept_seconds": kept_dur,
            "words_count": len(words),
            "script_lines": l_ids,
            "segments": segs_str,
            "anomalies": anoms,
            "transcribe_model": model_used,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": tot_tok,
            "transcription_reused": c.get("transcription_reused"),
            "transcription_usage_known": bool(gt_path.is_file() and isinstance(gt.get("usage"), dict)
                                              and any(k in gt["usage"] for k in ("input_tokens", "output_tokens", "total_tokens"))),
        })
    return out


def _seg_zoom_repr(seg: Dict[str, Any]) -> str:
    z0 = float(seg.get("zoom", 1.0) or 1.0)
    z1 = float(seg.get("zoom_to", z0) or z0)
    z_style = str(seg.get("zoom_style") or "").strip()
    z_at = seg.get("zoom_at")
    if z_style:
        base = f"{z_style}:{z1:g}" if abs(z0 - 1.0) < 1e-4 else f"{z_style}:{z0:g}->{z1:g}"
    elif abs(z1 - z0) > 1e-4:
        base = f"{z0:g}->{z1:g}"
    else:
        base = f"{z0:g}"
    return f"{base}@{float(z_at):.2f}" if z_at is not None else base


def _base_line_id(seg_id: str) -> str:
    m = re.match(r"^(L\d+)", str(seg_id or "").strip(), re.I)
    return m.group(1).upper() if m else str(seg_id or "").strip()


def compute_edl_learning_diff(initial_edl_text: str, final_edl_text: str) -> Dict[str, Any]:
    """Compare baseline `plan.auto.edl` vs refined `plan.edl` to extract structured editorial learnings."""
    import edl_parser

    init_plan = edl_parser.parse_edl(initial_edl_text) if initial_edl_text.strip() else {}
    final_plan = edl_parser.parse_edl(final_edl_text) if final_edl_text.strip() else {}

    init_segs = init_plan.get("segments") or []
    final_segs = final_plan.get("segments") or []
    init_by_id = {str(s.get("id")): s for s in init_segs}
    final_by_id = {str(s.get("id")): s for s in final_segs}

    init_by_line: Dict[str, List[Dict[str, Any]]] = {}
    for s in init_segs:
        init_by_line.setdefault(_base_line_id(str(s.get("id"))), []).append(s)
    final_by_line: Dict[str, List[Dict[str, Any]]] = {}
    for s in final_segs:
        final_by_line.setdefault(_base_line_id(str(s.get("id"))), []).append(s)

    take_swaps: List[Dict[str, Any]] = []
    for line_id, f_list in final_by_line.items():
        i_list = init_by_line.get(line_id) or []
        if not i_list:
            continue
        i_clips = sorted({Path(str(s.get("src") or "")).name for s in i_list if s.get("src")})
        f_clips = sorted({Path(str(s.get("src") or "")).name for s in f_list if s.get("src")})
        if i_clips != f_clips:
            take_swaps.append({
                "line_id": line_id,
                "from_clips": i_clips,
                "to_clips": f_clips,
            })

    cut_timing_deltas: List[Dict[str, Any]] = []
    zoom_changes: List[Dict[str, Any]] = []
    transition_changes: List[Dict[str, Any]] = []
    layout_changes: List[Dict[str, Any]] = []

    zoom_dist: Dict[str, int] = {}
    tr_dist: Dict[str, int] = {}
    layout_dist: Dict[str, Dict[str, Any]] = {}

    default_init_layout = str(init_plan.get("preset") or "single")
    default_final_layout = str(final_plan.get("preset") or "single")

    for f_seg in final_segs:
        sid = str(f_seg.get("id") or "")
        dur = round(max(0.0, float(f_seg.get("end", 0.0)) - float(f_seg.get("start", 0.0))), 3)
        lay = str(f_seg.get("layout") or default_final_layout)
        z_repr = _seg_zoom_repr(f_seg)
        tr_name = str(f_seg.get("transition") or "cut")
        tr_dur = f_seg.get("transition_duration")
        tr_repr = f"{tr_name}@{float(tr_dur):g}" if tr_dur else tr_name

        zoom_dist[z_repr] = zoom_dist.get(z_repr, 0) + 1
        tr_dist[tr_repr] = tr_dist.get(tr_repr, 0) + 1
        ld = layout_dist.setdefault(lay, {"segments": 0, "seconds": 0.0})
        ld["segments"] += 1
        ld["seconds"] = round(ld["seconds"] + dur, 2)

        i_seg = init_by_id.get(sid)
        if not i_seg:
            continue

        ds = round(float(f_seg.get("start", 0.0)) - float(i_seg.get("start", 0.0)), 3)
        de = round(float(f_seg.get("end", 0.0)) - float(i_seg.get("end", 0.0)), 3)
        if abs(ds) >= 0.01 or abs(de) >= 0.01:
            cut_timing_deltas.append({
                "seg_id": sid,
                "clip": Path(str(f_seg.get("src") or "")).name,
                "start_delta_sec": ds,
                "end_delta_sec": de,
                "dur_delta_sec": round(de - ds, 3),
            })

        i_z = _seg_zoom_repr(i_seg)
        if i_z != z_repr:
            zoom_changes.append({"seg_id": sid, "from": i_z, "to": z_repr})

        i_tr_name = str(i_seg.get("transition") or "cut")
        i_tr_dur = i_seg.get("transition_duration")
        i_tr = f"{i_tr_name}@{float(i_tr_dur):g}" if i_tr_dur else i_tr_name
        if i_tr != tr_repr:
            transition_changes.append({"seg_id": sid, "from": i_tr, "to": tr_repr})

        i_lay = str(i_seg.get("layout") or default_init_layout)
        if i_lay != lay:
            layout_changes.append({"seg_id": sid, "from": i_lay, "to": lay})

    init_marks = init_plan.get("marks") or []
    final_marks = final_plan.get("marks") or []
    wrong_cnt = sum(1 for m in final_marks if str(m.get("type", "")).lower() in ("wrong", "x", "cross", "error"))
    right_cnt = sum(1 for m in final_marks if str(m.get("type", "")).lower() in ("right", "check", "correct"))

    marks_summary = {
        "mark_pack": str(final_plan.get("mark_pack") or init_plan.get("mark_pack") or "flash"),
        "initial_count": len(init_marks),
        "final_count": len(final_marks),
        "wrong_count": wrong_cnt,
        "right_count": right_cnt,
        "marks": [
            {"segment": m.get("segment"), "type": m.get("type"), "at": m.get("at"), "sfx": m.get("sfx")}
            for m in final_marks
        ],
    }

    segments_added = [sid for sid in final_by_id if sid not in init_by_id]
    segments_removed = [sid for sid in init_by_id if sid not in final_by_id]

    has_edits = bool(
        take_swaps or cut_timing_deltas or zoom_changes or transition_changes
        or layout_changes or segments_added or segments_removed
        or len(init_marks) != len(final_marks)
    )

    return {
        "has_edits": has_edits,
        "initial_segments_count": len(init_segs),
        "final_segments_count": len(final_segs),
        "segments_added": segments_added,
        "segments_removed": segments_removed,
        "take_swaps": take_swaps,
        "cut_timing_deltas": cut_timing_deltas,
        "zoom_changes": {"distribution": zoom_dist, "changes": zoom_changes},
        "transition_changes": {"distribution": tr_dist, "changes": transition_changes},
        "layout_distribution": {"distribution": layout_dist, "changes": layout_changes},
        "marks_summary": marks_summary,
    }


def save_prepare_snapshot(
    project_name: str,
    lang: str,
    clips_data: List[Dict[str, Any]],
    segments: List[Dict[str, Any]],
    marks: List[Dict[str, Any]],
    job_start_epoch: float,
) -> Dict[str, Any]:
    """Save the per-clip telemetry snapshot at the end of `prepare.py`."""
    proj_dir = WORK / project_name
    proj_dir.mkdir(parents=True, exist_ok=True)
    snap_path = proj_dir / "token_telemetry.json"
    clip_rows = collect_clip_telemetry(clips_data, segments)
    t_in = sum(r["input_tokens"] for r in clip_rows)
    t_out = sum(r["output_tokens"] for r in clip_rows)
    t_tot = sum(r["total_tokens"] for r in clip_rows)
    raw_sec = round(sum(r["raw_seconds"] for r in clip_rows), 2)
    kept_sec = round(sum(r["kept_seconds"] for r in clip_rows), 2)
    agent = detect_agent_usage(since_epoch=job_start_epoch, project_hint=project_name)

    snapshot = {
        "project": project_name,
        "language": lang,
        "job_start_epoch": job_start_epoch,
        "prepared_at": dt.datetime.now().isoformat(timespec="seconds"),
        "agent_name": agent["agent_name"],
        "agent_detect_reason": agent.get("agent_detect_reason", ""),
        "agent_model": agent["agent_model"],
        "agent_session_id": agent.get("session_id"),
        "clips_count": len(clip_rows),
        "segments_count": len(segments),
        "marks_count": len(marks),
        "raw_seconds": raw_sec,
        "video_seconds": kept_sec,
        "transcribe_in_tokens": t_in,
        "transcribe_out_tokens": t_out,
        "transcribe_total_tokens": t_tot,
        "clips": clip_rows,
    }
    write_json(snap_path, snapshot)
    return snapshot


def _init_local_db(conn: sqlite3.Connection) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS job_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        created_at TEXT NOT NULL,
        project TEXT NOT NULL,
        language TEXT NOT NULL,
        agent_name TEXT NOT NULL,
        agent_model TEXT NOT NULL,
        clips_count INTEGER NOT NULL,
        raw_seconds REAL NOT NULL,
        video_seconds REAL NOT NULL,
        marks_count INTEGER NOT NULL,
        transcribe_in_tokens INTEGER NOT NULL,
        transcribe_out_tokens INTEGER NOT NULL,
        transcribe_total_tokens INTEGER NOT NULL,
        agent_in_tokens INTEGER NOT NULL,
        agent_cached_tokens INTEGER NOT NULL,
        agent_out_tokens INTEGER NOT NULL,
        agent_total_tokens INTEGER NOT NULL,
        grand_total_tokens INTEGER NOT NULL,
        output_mp4 TEXT NOT NULL
    )""")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(job_runs)")}
    if "details_json" not in columns:
        conn.execute("ALTER TABLE job_runs ADD COLUMN details_json TEXT NOT NULL DEFAULT '{}'")
    conn.execute("""CREATE TABLE IF NOT EXISTS clip_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        clip_stem TEXT NOT NULL,
        raw_seconds REAL NOT NULL,
        kept_seconds REAL NOT NULL,
        words_count INTEGER NOT NULL,
        script_lines TEXT NOT NULL,
        segments_summary TEXT NOT NULL,
        anomalies_summary TEXT NOT NULL,
        transcribe_model TEXT NOT NULL,
        input_tokens INTEGER NOT NULL,
        output_tokens INTEGER NOT NULL,
        total_tokens INTEGER NOT NULL
    )""")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            try:
                val = json.loads(line)
                if isinstance(val, dict):
                    out.append(val)
            except ValueError:
                continue
    except OSError:
        pass
    return out


def _usage_from_requests(requests: List[Dict[str, Any]]) -> Dict[str, Any]:
    inp = sum(int(r.get("input_tokens") or 0) for r in requests)
    cached = sum(int(r.get("cached_input_tokens") or 0) for r in requests)
    uncached = max(0, inp - cached)
    out = sum(int(r.get("output_tokens") or 0) for r in requests)
    return {
        "token_precision": "exact" if requests else "unavailable",
        "requests": requests,
        "input_tokens": uncached,
        "cached_tokens": cached,
        "output_tokens": out,
        "cache_write_tokens": sum(int(r.get("cache_write_input_tokens") or 0) for r in requests),
        "reasoning_tokens": sum(int(r.get("reasoning_output_tokens") or 0) for r in requests),
        "total_tokens": uncached + out,
        "total_tokens_including_cache": uncached + cached + out,
        "context_start": int(requests[0].get("context", 0) or 0) if requests else 0,
        "context_end": int(requests[-1].get("context", 0) or 0) if requests else 0,
        "steps_count": len(requests),
        "turns": len(requests),
    }


def _stage_report(proj_dir: Path, agent: Dict[str, Any], since: float, until: float) -> List[Dict[str, Any]]:
    import token_cost

    transitions: List[Tuple[float, str]] = [(since, "intake")]
    stages = {
        "inspect_start": "measurement",
        "inspect_done": "planning",
        "check": "corrections/review",
        "render_start": "render/review",
        "render_done": "final review",
    }
    for ev in _read_jsonl(proj_dir / "stage_events.jsonl"):
        at = float(ev.get("at") or 0)
        if since <= at <= until and ev.get("kind") in stages:
            transitions.append((at, stages[str(ev["kind"])]))
    transitions.sort()
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for req in agent.get("requests") or []:
        ts = float(req.get("timestamp") or since)
        stage = str(req.get("stage") or next((name for at, name in reversed(transitions) if at <= ts), "intake"))
        groups.setdefault(stage, []).append(req)
    return [
        {"stage": stage, **u, "cost": token_cost.estimate(u)}
        for stage, reqs in groups.items()
        for u in (_usage_from_requests(reqs),)
    ]


def finalize_job_telemetry(
    plan_path: Path,
    plan: Dict[str, Any],
    video_seconds: float,
    output_mp4: Path,
    sync_cloud: bool = True,
    completed_at: Optional[float] = None,
) -> Dict[str, Any]:
    """Finalize clip + agent token telemetry after `render.py`, write SQL + Markdown ledgers, and sync to Railway Postgres."""
    proj_dir = plan_path.parent
    project_name = proj_dir.name
    snap_path = proj_dir / "token_telemetry.json"
    snap: Dict[str, Any] = read_json(snap_path) if snap_path.is_file() else {}

    job_end = float(completed_at if completed_at is not None else snap.get("job_end_epoch") or time.time())
    cand_snap: Optional[float] = None
    try:
        if snap.get("job_start_epoch") and float(snap["job_start_epoch"]) > 0:
            cand_snap = float(snap["job_start_epoch"])
    except (TypeError, ValueError):
        cand_snap = None

    cand_started: Optional[float] = None
    started_file = proj_dir / "job_started.json"
    if started_file.is_file():
        try:
            s_val = (read_json(started_file) or {}).get("start")
            if s_val and float(s_val) > 0:
                cand_started = float(s_val)
        except Exception:
            cand_started = None

    valid_cands = [c for c in (cand_snap, cand_started) if c is not None and c <= job_end + 1.0]
    if valid_cands:
        job_start = max(valid_cands)
    else:
        started_at = 0.0
        try:
            from common import job_start_epoch
            started_at = float(job_start_epoch(create=False))
        except Exception:
            started_at = 0.0
        job_start = started_at if 0 < started_at <= job_end + 1.0 else (job_end - 900.0)

    if job_end - job_start > MAX_EDIT_WINDOW_SEC:
        window_lo = job_end - MAX_EDIT_WINDOW_SEC
        earliest: Optional[float] = None
        for ev in _read_jsonl(proj_dir / "stage_events.jsonl"):
            try:
                at = float(ev.get("at") or 0.0)
            except (TypeError, ValueError):
                continue
            if window_lo <= at <= job_end:
                if earliest is None or at < earliest:
                    earliest = at
        job_start = earliest if earliest is not None else (job_end - 1800.0)

    agent = detect_agent_usage(
        since_epoch=job_start,
        until_epoch=job_end,
        session_id=snap.get("agent_session_id"),
        project_hint=project_name,
    )

    clips: List[Dict[str, Any]] = snap.get("clips") or []
    t_in = int(snap.get("transcribe_in_tokens") or sum(int(c.get("input_tokens") or 0) for c in clips))
    t_out = int(snap.get("transcribe_out_tokens") or sum(int(c.get("output_tokens") or 0) for c in clips))
    t_tot = int(snap.get("transcribe_total_tokens") or (t_in + t_out))

    # An inspect has no cuts. Recompute kept duration from the final parsed plan.
    for clip in clips:
        def source_path(value: Any) -> Path:
            path = Path(str(value or ""))
            return (path if path.is_absolute() else ROOT / path).resolve()
        source = source_path(clip.get("src"))
        kept = [seg for seg in plan.get("segments", [])
                if source_path(seg.get("src")) == source]
        clip["kept_seconds"] = round(sum(float(seg["end"]) - float(seg["start"]) for seg in kept), 3)
        clip["segments"] = " + ".join(str(seg["id"]) for seg in kept) or "listener only / unused"
        clip["script_lines"] = ",".join(dict.fromkeys(_base_line_id(str(seg["id"])) for seg in kept)) or "-"

    fresh_clips = [c for c in clips if c.get("transcription_reused") is False]
    reused_clips = [c for c in clips if c.get("transcription_reused") is True]
    fresh_transcription = sum(c.get("total_tokens", 0) for c in fresh_clips)
    reused_transcription = sum(c.get("total_tokens", 0) for c in reused_clips)
    import token_cost
    cost = token_cost.estimate(agent)
    a_in = int(agent.get("input_tokens") or 0)
    a_cache = int(agent.get("cached_tokens") or 0)
    a_out = int(agent.get("output_tokens") or 0)
    a_tot = int(agent.get("total_tokens") or (a_in + a_out))
    grand_in = sum(c.get("input_tokens", 0) for c in fresh_clips) + a_in + a_cache
    grand_out = sum(c.get("output_tokens", 0) for c in fresh_clips) + a_out
    grand_total = fresh_transcription + a_tot + a_cache

    now_dt = dt.datetime.now()
    date_str = now_dt.strftime("%Y-%m-%d")
    ts_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
    lang = str(snap.get("language") or "auto")
    raw_sec = float(snap.get("raw_seconds") or 0.0)
    vid_sec = round(float(video_seconds or snap.get("video_seconds") or 0.0), 2)
    marks_cnt = len(plan.get("marks") or [])
    clips_cnt = int(snap.get("clips_count") or len(clips) or len(plan.get("segments") or []))
    rel_out = str(output_mp4)
    tok_per_min = int(round(a_tot / max(0.25, vid_sec / 60.0)))

    initial_edl_p = proj_dir / "plan.auto.edl"
    final_edl_p = proj_dir / "plan.edl"
    initial_edl = initial_edl_p.read_text(encoding="utf-8", errors="ignore") if initial_edl_p.is_file() else ""
    final_edl = final_edl_p.read_text(encoding="utf-8", errors="ignore") if final_edl_p.is_file() else ""
    if not initial_edl and final_edl:
        initial_edl = final_edl
    try:
        edl_diff = compute_edl_learning_diff(initial_edl, final_edl)
    except Exception:
        edl_diff = {}

    learned_rules: List[Any] = []

    spec_tuning: Dict[str, Any] = {}
    try:
        import style_config
        spec_tuning = style_config.get_tuning(str(plan.get("style") or "micronarrative"), plan)
    except Exception:
        spec_path = ROOT / "formats" / "specs" / "micronarrative_spec.json"
        if spec_path.is_file():
            try:
                spec_tuning = (read_json(spec_path) or {}).get("tuning") or {}
            except Exception:
                spec_tuning = {}
    assets_used = {
        "preset": str(plan.get("preset") or "auto"),
        "mark_pack": str(plan.get("mark_pack") or "flash"),
        "sfx_pack": str(plan.get("sfx_pack") or "default"),
        "wrong_sfx_primary": str(plan.get("error_sfx") or spec_tuning.get("wrong_sfx_primary") or "sn_wrong.wav"),
        "wrong_sfx_layer": str(spec_tuning.get("wrong_sfx_layer") or "none"),
        "right_sfx_primary": str(spec_tuning.get("right_sfx_primary") or "correct_ding.wav"),
        "right_sfx_layer": str(spec_tuning.get("right_sfx_layer") or "sn_correct.wav"),
        "whoosh_sfx": str(spec_tuning.get("whoosh_sfx") or "whoosh.wav"),
    }

    try:
        import auth_session
        user_email = auth_session.get_logged_in_email()
    except Exception:
        user_email = os.getenv("ZENO_USER_EMAIL", "")

    record = {
        "created_at": ts_str,
        "date": date_str,
        "project": project_name,
        "language": lang,
        "user_email": user_email,
        "job_start_epoch": job_start,
        "job_end_epoch": job_end,
        "agent_name": agent["agent_name"],
        "agent_detect_reason": agent.get("agent_detect_reason", ""),
        "agent_model": agent["agent_model"],
        "agent_token_precision": agent.get("token_precision", "unavailable"),
        "agent_session_id": agent.get("session_id"),
        "agent_cache_write_tokens": agent.get("cache_write_tokens", 0),
        "agent_reasoning_tokens": agent.get("reasoning_tokens", 0),
        "agent_steps": agent.get("steps_count", 0),
        "agent_total_including_cache": a_tot + a_cache,
        "transcribe_new_total_tokens": fresh_transcription,
        "transcribe_reused_total_tokens": reused_transcription,
        "transcribe_reuse_known": all(c.get("transcription_reused") is not None for c in clips),
        "cost_estimate": cost,
        "cost_stages": _stage_report(proj_dir, agent, job_start, job_end),
        "host_usage_unknown": agent.get("host_usage_unknown", False),
        "direct_usage_unmerged": bool(agent.get("direct_usage_unmerged", False)),
        "agent_context_start": agent.get("context_start", 0),
        "agent_context_end": agent.get("context_end", 0),
        "clips_count": clips_cnt,
        "raw_seconds": raw_sec,
        "video_seconds": vid_sec,
        "marks_count": marks_cnt,
        "transcribe_in_tokens": t_in,
        "transcribe_out_tokens": t_out,
        "transcribe_total_tokens": t_tot,
        "agent_in_tokens": a_in,
        "agent_cached_tokens": a_cache,
        "agent_out_tokens": a_out,
        "agent_total_tokens": a_tot,
        "overall_in_tokens": grand_in,
        "overall_out_tokens": grand_out,
        "grand_total_tokens": grand_total,
        "grand_total_definition": "agent including cached input + known new transcription",
        "transcription_unknown_clips": sum(c.get("transcription_reused") is None or
                                           (c.get("transcription_reused") is False and not c.get("transcription_usage_known"))
                                           for c in clips),
        "tokens_per_min": tok_per_min,
        "output_mp4": rel_out,
        "clips": clips,
        "initial_edl": initial_edl,
        "final_edl": final_edl,
        "edl_diff": edl_diff,
        "learned_rules": learned_rules,
        "assets_used": assets_used,
    }
    try:
        import job_report
        record["job_report"] = job_report.final_report(proj_dir, plan)
        record["job_report"]["agent"] = {
            k: agent.get(k)
            for k in ("turns", "cache_write_tokens", "code_edits", "window_minutes", "mixed_with_development")
            if k in agent and agent.get(k) is not None
        }
        record["job_report"]["accounting"] = {k: record[k] for k in (
            "agent_session_id", "agent_token_precision", "agent_total_including_cache",
            "agent_reasoning_tokens", "transcribe_new_total_tokens", "transcribe_reused_total_tokens",
            "transcription_unknown_clips", "cost_estimate", "grand_total_definition")}
    except Exception:
        pass
    write_json(snap_path, record)

    # 1. Write per-project markdown report: work/<project>/token_report.md
    report_md = _format_project_report_md(record)
    (proj_dir / "token_report.md").write_text(report_md, encoding="utf-8")

    # 2. Save into local SQLite database: work/zeno_telemetry.db
    try:
        LOCAL_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(LOCAL_DB)) as conn:
            _init_local_db(conn)
            # Retrying a report for the same output replaces that output's local row atomically.
            previous = conn.execute("SELECT id FROM job_runs WHERE project=? AND output_mp4=?",
                                    (project_name, rel_out)).fetchall()
            for (old_id,) in previous:
                conn.execute("DELETE FROM clip_runs WHERE job_id=?", (old_id,))
                conn.execute("DELETE FROM job_runs WHERE id=?", (old_id,))
            cur = conn.execute(
                """INSERT INTO job_runs (
                    created_at, project, language, agent_name, agent_model,
                    clips_count, raw_seconds, video_seconds, marks_count,
                    transcribe_in_tokens, transcribe_out_tokens, transcribe_total_tokens,
                    agent_in_tokens, agent_cached_tokens, agent_out_tokens, agent_total_tokens,
                    grand_total_tokens, output_mp4
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    ts_str, project_name, lang, record["agent_name"], record["agent_model"],
                    clips_cnt, raw_sec, vid_sec, marks_cnt,
                    t_in, t_out, t_tot, a_in, a_cache, a_out, a_tot, grand_total, rel_out,
                ),
            )
            job_id = int(cur.lastrowid or 0)
            conn.execute("UPDATE job_runs SET details_json=? WHERE id=?", (json.dumps(record, ensure_ascii=False), job_id))
            for c in clips:
                conn.execute(
                    """INSERT INTO clip_runs (
                        job_id, clip_stem, raw_seconds, kept_seconds, words_count,
                        script_lines, segments_summary, anomalies_summary, transcribe_model,
                        input_tokens, output_tokens, total_tokens
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        job_id,
                        str(c.get("clip") or ""),
                        float(c.get("raw_seconds") or 0.0),
                        float(c.get("kept_seconds") or 0.0),
                        int(c.get("words_count") or 0),
                        str(c.get("script_lines") or "-"),
                        str(c.get("segments") or ""),
                        " ; ".join(c.get("anomalies") or []) or "clean",
                        str(c.get("transcribe_model") or ""),
                        int(c.get("input_tokens") or 0),
                        int(c.get("output_tokens") or 0),
                        int(c.get("total_tokens") or 0),
                    ),
                )
    except Exception as exc:
        print(f"  [telemetry] local SQL warning: {exc}", file=sys.stderr)

    # 3. Update work/TOKEN_LEDGER.md
    _update_ledger_md(record)

    _update_readme(record)

    # 4. Sync to Railway PostgreSQL (/v1/telemetry) if configured
    if sync_cloud:
        _sync_to_cloud(record)

    return record


def _format_project_report_md(r: Dict[str, Any]) -> str:
    precision = r.get("agent_token_precision", "unavailable")
    total = r.get("agent_total_including_cache", r["agent_total_tokens"] + r.get("agent_cached_tokens", 0))
    lines = [
        f"# Zeno token report — {r['project']}", "",
        f"{r['created_at']} · {r['agent_name']} · {r['agent_model']} · measurement {precision}",
        f"{r['clips_count']} clips · {tc(r['video_seconds'])} final · {r['output_mp4']}", "",
        "| Agent usage | Tokens |", "|---|---:|",
        f"| Uncached input (includes cache writes) | {r['agent_in_tokens']:,} |",
        f"| Cached input | {r.get('agent_cached_tokens', 0):,} |",
        f"| Cache writes, included above | {r.get('agent_cache_write_tokens', 0):,} |",
        f"| Output (includes reasoning) | {r['agent_out_tokens']:,} |",
        f"| **Total including cached input** | **{total:,}** |",
        f"| Uncached input + output | {r['agent_total_tokens']:,} |", "",
        "Counts cover recorded usage during this job window. Provider records may arrive after the report. "
        "Unavailable counters do not establish zero usage.", "",
        f"Transcription newly measured this run: {r.get('transcribe_new_total_tokens', 0):,} tokens.",
        f"Prior transcription reused: {r.get('transcribe_reused_total_tokens', 0):,} tokens.",
    ]
    if not r.get("transcribe_reuse_known"):
        lines.append("Some legacy clips have unknown reuse status; their prior counts cannot be assigned to this run.")
    if r.get("host_usage_unknown") or r.get("direct_usage_unmerged"):
        lines.append("Host/direct accounting is incomplete; measured totals are not a complete job bill.")
    if r.get("transcription_unknown_clips"):
        lines.append(f"Transcription accounting is incomplete for {r['transcription_unknown_clips']} clip(s); missing usage is not zero spending.")
    cost = r.get("cost_estimate") or {}
    if cost.get("available"):
        lines += ["", f"Estimated model cost: USD {cost['usd']:.6f} / INR {cost['inr']:.2f}.",
                  f"Basis: {cost['basis']}. Speed assumption: {cost['speed_assumption']}.",
                  f"Rates verified {cost['rates_as_of']}: {cost['rate_source']}",
                  f"USD/INR {cost['usd_inr']} as of {cost['fx_as_of']}: {cost['fx_source']}",
                  "Set ZENO_USD_INR and ZENO_COST_SPEED for a different exchange rate or speed. "
                  "Transcription, tool fees, tax and post-render chat are excluded."]
        lines += ["", "| Cost component | USD | INR |", "|---|---:|---:|"]
        for label, amount in cost.get("usd_by_type", {}).items():
            lines.append(f"| {label.replace('_', ' ')} | {amount:.6f} | {amount * cost['usd_inr']:.2f} |")
    else:
        lines += ["", "Cost estimate unavailable: " + cost.get("reason", "No verified price.")]
    if r.get("cost_stages"):
        lines += ["", "Stage attribution follows request timestamps; it is not a bill for local Python/FFmpeg work.",
                  "| Stage | Requests | New input | Cached input | Output | Est. INR |",
                  "|---|---:|---:|---:|---:|---:|"]
        for stage in r["cost_stages"]:
            amount = stage["cost"]
            price = f"{amount['inr']:.2f}" if amount.get("available") else "unknown"
            lines.append(f"| {stage['stage']} | {stage['steps_count']} | {stage['input_tokens']:,} | "
                         f"{stage['cached_tokens']:,} | {stage['output_tokens']:,} | {price} |")
    lines += ["", "| Clip | Raw seconds | Kept seconds | Cuts | Transcription model | Input | Output | Reused |",
              "|---|---:|---:|---|---|---:|---:|---|"]
    for c in r.get("clips", []):
        reuse = {True: "yes", False: "no", None: "unknown"}[c.get("transcription_reused")]
        lines.append(f"| {c['clip']} | {c['raw_seconds']:.2f} | {c['kept_seconds']:.2f} | "
                     f"{c['segments']} | {c['transcribe_model']} | {c['input_tokens']:,} | {c['output_tokens']:,} | {reuse} |")
    return "\n".join(lines) + "\n"


def _update_readme(r: Dict[str, Any]) -> None:
    """Replace only this output's row when README.md contains ledger markers; never fail or pollute a clean README."""
    path = ROOT / "README.md"
    if not path.is_file():
        return
    try:
        content = path.read_text(encoding="utf-8")
    except OSError:
        return
    if "## Token usage log" not in content and "<!-- zeno-output:" not in content and "<!-- zeno-ledger" not in content:
        return
    marker = f"<!-- zeno-output:{r['output_mp4']} -->"
    row = (f"| {r['date']} | **{r['language']} – {r['project']}** ({r['clips_count']} clips): "
           f"{tc(r['video_seconds'])}, {r['marks_count']} marks → \u0060{r['output_mp4']}\u0060 {marker} | "
           f"{r.get('agent_context_start', 0):,} → {r.get('agent_context_end', 0):,} | "
           f"{r['agent_total_tokens']:,} uncached input + output; {r['agent_cached_tokens']:,} cached "
           f"({r.get('agent_token_precision', 'unavailable')}) | unavailable | unavailable |")
    rows = content.splitlines()
    for i, line in enumerate(rows):
        if marker in line:
            rows[i] = row
            break
    else:
        if "## Token usage log" not in content and "<!-- zeno-ledger" not in content:
            return
        rows.append(row)
    try:
        path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    except OSError:
        pass


def _update_ledger_md(r: Dict[str, Any]) -> None:
    header = """# Zeno — Automated Token & Clip Telemetry Ledger

Automatically updated by [`scripts/token_tracker.py`](../scripts/token_tracker.py) after every video edit (`prepare.py` → `render.py`).
The latest record per output is also stored in local SQLite (`work/zeno_telemetry.db`) and synced to Railway PostgreSQL (`/admin`). Historical rows retain their original inline labels.

## 1. All Video Edit Runs (AI Agent Tokens & Gemini Transcription Registered Separately)

| Date | Project (`Lang`) | Agent | Model | Clips (`Raw → Final`) | Agent Tokens (uncached / cached / output / total) | Transcription Tokens (new / reused; unknown where unavailable) |
|---|---|---|---|---|---|---|
"""
    marker = f"<!-- zeno-output:{r['output_mp4']} -->"
    row = (
        f"| `{r['created_at']}` | **`{r['project']}`** (`{r['language']}`) | **{r['agent_name']}** | `{r['agent_model']}` | "
        f"{r['clips_count']} (`{r['raw_seconds']:.0f}s → {tc(r['video_seconds'])}`) | "
        f"`{r['agent_in_tokens']:,}` uncached in / `{r['agent_cached_tokens']:,}` cached / "
        f"`{r['agent_out_tokens']:,}` out / **`{r.get('agent_total_including_cache', r['agent_total_tokens'] + r['agent_cached_tokens']):,}` total including cache** | "
        f"`{r.get('transcribe_new_total_tokens', 0):,}` new / `{r.get('transcribe_reused_total_tokens', 0):,}` reused; "
        f"{r.get('transcription_unknown_clips', 0)} unknown clips {marker} |"
    )

    existing_rows: List[str] = []
    if LEDGER_MD.is_file():
        for line in LEDGER_MD.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("| `20") and marker not in line and not (f"**`{r['project']}`**" in line and f"`{r['created_at']}`" in line):
                existing_rows.append(line)
    existing_rows.append(row)

    clip_section = [
        "",
        f"## 2. Latest Job Clip-by-Clip Breakdown — `{r['project']}` (`{r['created_at']}`)",
        "",
        "| Clip | Raw → Kept | Words | Script Lines | Kept Segments | Raw Clip Process (Retakes / Cuts / Anomalies) | Gemini In | Gemini Out | Gemini Total |",
        "|---|---|---|---|---|---|---:|---:|---:|",
    ]
    for c in r.get("clips") or []:
        anom_txt = "; ".join(c.get("anomalies") or []) or "Clean take (pause-snapped)"
        clip_section.append(
            f"| `{c['clip']}` | `{c['raw_seconds']:.1f}s → {c['kept_seconds']:.1f}s` | {c['words_count']} | `{c['script_lines']}` | `{c['segments']}` | {anom_txt} | `{c['input_tokens']:,}` | `{c['output_tokens']:,}` | **`{c['total_tokens']:,}`** |"
        )
    clip_section.append("")

    content = header + "\n".join(existing_rows) + "\n" + "\n".join(clip_section)
    LEDGER_MD.parent.mkdir(parents=True, exist_ok=True)
    LEDGER_MD.write_text(content, encoding="utf-8")


def _version_headers() -> Dict[str, str]:
    import platform as _platform
    try:
        ver = (ROOT / "VERSION").read_text(encoding="utf-8").strip()[:40]
    except Exception:
        ver = ""
    return {"X-Zeno-Version": ver, "X-Zeno-Platform": f"{_platform.system()} {_platform.machine()}"[:80]}


def _sync_to_cloud(r: Dict[str, Any]) -> bool:
    if os.getenv("ZENO_NO_CLOUD_TELEMETRY", "").strip().lower() in ("1", "true", "yes", "on"):
        return False
    url = ""
    token = ""
    try:
        import auth_session
        sess = auth_session.load_session()
        url = str(sess.get("cloud_url") or auth_session.get_cloud_url()).strip()
        token = str(sess.get("token") or "").strip()
    except Exception:
        pass
    if not url:
        url = (os.getenv("ZENO_CLOUD_URL") or os.getenv("SCRIPTCUT_CLOUD_URL") or "").strip()
    if not token:
        token = (os.getenv("ZENO_CLOUD_TOKEN") or os.getenv("SCRIPTCUT_CLOUD_TOKEN") or "").strip()
    if not url or not token:
        return False
    hdrs = {"Authorization": f"Bearer {token}", **_version_headers()}
    payload = {**r, "zeno_version": hdrs["X-Zeno-Version"]}
    try:
        import httpx
        resp = httpx.post(
            url.rstrip("/") + "/v1/telemetry",
            headers=hdrs,
            json=payload,
            timeout=10.0,
        )
        return resp.status_code == 200
    except ImportError:
        pass
    except Exception:
        return False
    try:
        import urllib.request
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            url.rstrip("/") + "/v1/telemetry",
            data=body,
            headers={**hdrs, "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10.0) as resp:  # noqa: S310
            return resp.status == 200
    except Exception:
        return False


_push_to_zeno_cloud = _sync_to_cloud


def print_console_summary(r: Dict[str, Any]) -> None:
    total = r.get("agent_total_including_cache", r["agent_total_tokens"] + r.get("agent_cached_tokens", 0))
    print(f"[TOKENS] {r['agent_name']} / {r['agent_model']} ({r.get('agent_token_precision', 'unavailable')})")
    print(f"[TOKENS] uncached input={r['agent_in_tokens']:,}, cached input={r.get('agent_cached_tokens', 0):,}, "
          f"output={r['agent_out_tokens']:,}, total including cache={total:,}")
    print(f"[TOKENS] transcription new={r.get('transcribe_new_total_tokens', 0):,}, "
          f"prior reused={r.get('transcribe_reused_total_tokens', 0):,}, "
          f"reuse status known={r.get('transcribe_reuse_known', False)}")
    cost = r.get("cost_estimate") or {}
    if cost.get("available"):
        print(f"[COST] API-equivalent {cost['speed_assumption']} estimate USD {cost['usd']:.4f} / INR {cost['inr']:.2f}; "
              f"FX dated {cost['fx_as_of']}, before tax. Subscription billing differs.")
    else:
        print("[COST] unavailable: " + cost.get("reason", "No verified price."))
    print(f"[REPORT] work/{r['project']}/token_report.md")


def main() -> None:
    ap = argparse.ArgumentParser(description="Inspect or finalize Zeno per-clip and per-agent token telemetry.")
    ap.add_argument("--project", help="Project folder name under work/<project>")
    ap.add_argument("--refresh", action="store_true", help="Explicitly recompute and persist a historical report")
    ap.add_argument("--list", action="store_true", help="List recorded runs from work/zeno_telemetry.db")
    ap.add_argument("--no-cloud", action="store_true", help="Skip syncing to Railway Postgres")
    args = ap.parse_args()

    if args.list:
        if not LOCAL_DB.is_file():
            print("No runs recorded in work/zeno_telemetry.db yet.")
            return
        with sqlite3.connect(str(LOCAL_DB)) as conn:
            rows = conn.execute(
                "SELECT id, created_at, project, agent_name, agent_model, clips_count, video_seconds, "
                "transcribe_total_tokens, agent_total_tokens, grand_total_tokens FROM job_runs ORDER BY id DESC LIMIT 20"
            ).fetchall()
        for r in rows:
            print(f"#{r[0]} [{r[1]}] {r[2]} | {r[3]} ({r[4]}) | {r[5]} clips ({r[6]:.1f}s) | Transcribe: {r[7]:,} | Agent: {r[8]:,} | Total: {r[9]:,}")
        return

    if args.project:
        pdir = WORK / args.project
        snapshot = pdir / "token_telemetry.json"
        if snapshot.is_file() and not args.refresh:
            record = read_json(snapshot)
            if "agent_in_tokens" in record:
                print_console_summary(record)
                return
        if not args.refresh:
            raise SystemExit("No completed report yet. Finish the render, or use --refresh explicitly.")
        plan_edl = pdir / "plan.edl"
        plan_json = pdir / "plan.json"
        if plan_edl.is_file() and (not plan_json.is_file() or plan_edl.stat().st_mtime >= plan_json.stat().st_mtime):
            import edl_parser
            plan_path = plan_edl
            plan = edl_parser.load_edl(plan_edl)
        elif plan_json.is_file():
            plan_path = plan_json
            plan = read_json(plan_json)
        else:
            raise SystemExit(f"No plan.edl or plan.json found in {pdir}")
        segs = plan.get("segments") or []
        vid_sec = sum(max(0.0, float(s.get("end", 0.0)) - float(s.get("start", 0.0))) for s in segs)
        out_mp4 = Path(str(plan.get("output") or (OUTPUT / f"{args.project}.mp4")))
        out_mp4 = out_mp4 if out_mp4.is_absolute() else ROOT / out_mp4
        rec = finalize_job_telemetry(plan_path, plan, vid_sec, out_mp4, sync_cloud=not args.no_cloud)
        print_console_summary(rec)
    else:
        agent = detect_agent_usage()
        print(json.dumps(agent, indent=2))


if __name__ == "__main__":
    main()
