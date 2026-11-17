"""Read usage counters from the current Codex chat, without reading message contents.

Input counters include cache reads. Public telemetry uses uncached input separately.
A specified session never falls back to a different chat or another agent.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import time
from pathlib import Path
from typing import Any, Iterable

FIELDS = ("input_tokens", "cached_input_tokens", "cache_write_input_tokens", "output_tokens", "reasoning_output_tokens")


def epoch(value: str) -> float:
    try:
        return dt.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (ValueError, TypeError):
        return 0.0


def records(path: Path) -> Iterable[dict]:
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            # Skip prompts, tool arguments and private reasoning before JSON parsing.
            if not any(marker in line for marker in ('"session_meta"', '"turn_context"', '"token_count"')):
                continue
            try:
                item = json.loads(line)
            except ValueError:   # the writer may not have finished the last line
                continue
            if item.get("type") in {"session_meta", "turn_context"} or (
                item.get("type") == "event_msg" and item.get("payload", {}).get("type") == "token_count"
            ):
                yield item


def window_usage(items: Iterable[dict], since: float, until: float, root: Path) -> dict | None:
    previous = None
    model = "unknown"
    ours = False
    session = ""
    requests = []
    last_context = 0
    for item in items:
        payload = item.get("payload") or {}
        kind = item.get("type")
        if kind == "session_meta":
            session = str(payload.get("id") or "")
            cwd = payload.get("cwd")
            ours = bool(cwd and Path(cwd).resolve().is_relative_to(root.resolve()))
            continue
        stamp = epoch(item.get("timestamp", ""))
        if stamp > until:
            continue
        if kind == "turn_context":
            model = payload.get("model") or model
            continue
        info = payload.get("info")
        if payload.get("type") != "token_count" or not isinstance(info, dict):
            continue
        total = info.get("total_token_usage")
        if not isinstance(total, dict):
            continue
        current = {k: int(total.get(k) or 0) for k in FIELDS}
        last_raw = info.get("last_token_usage")
        last = last_raw if isinstance(last_raw, dict) else {}
        # Cumulative deltas suppress duplicate events and count every request once.
        if previous is None or any(current[k] < previous[k] for k in FIELDS):
            if previous is None and not last_raw and any(current.values()):
                delta = dict(current)
            else:
                delta = {k: int(last.get(k) or 0) for k in FIELDS}
        else:
            delta = {k: current[k] - previous[k] for k in FIELDS}
        last_context = int(last.get("input_tokens") or delta["input_tokens"] or 0)
        previous = current
        if since <= stamp <= until and any(delta.values()):
            requests.append({**delta, "model": model, "context": last_context, "timestamp": stamp})
    if not ours:
        return None
    sums = {k: sum(r[k] for r in requests) for k in FIELDS}
    cached = sums["cached_input_tokens"]
    fresh = max(0, sums["input_tokens"] - cached)
    out = sums["output_tokens"]
    return {
        "agent_name": "Codex", "agent_model": model,
        "session_id": session, "input_tokens": fresh, "cached_tokens": cached,
        "cache_write_tokens": sums["cache_write_input_tokens"], "output_tokens": out,
        "reasoning_tokens": sums["reasoning_output_tokens"],
        "total_tokens": fresh + out, "total_tokens_including_cache": fresh + cached + out,
        "context_start": requests[0]["context"] if requests else 0,
        "context_end": last_context, "steps_count": len(requests), "turns": len(requests),
        "token_precision": "exact" if requests else "unavailable",
        "requests": requests,
    }


def detect(since: float, until: float | None = None, root: Path | None = None,
           session_id: str | None = None, sessions: Path | None = None) -> dict | None:
    root = root or Path(__file__).resolve().parent.parent
    sessions = sessions or Path(os.getenv("CODEX_HOME", str(Path.home() / ".codex"))) / "sessions"
    sid = session_id or os.getenv("CODEX_THREAD_ID", "")
    until = until or time.time()
    if not sessions.is_dir():
        return None
    # Restrict to the named session whenever the harness provides it.
    files = sessions.rglob(f"*{sid}.jsonl" if sid else "*.jsonl")
    candidates = []
    for path in files:
        try:
            if not sid and path.stat().st_mtime < since:
                continue
            usage = window_usage(records(path), since, until, root)
            if usage and (not sid or usage["session_id"] == sid):
                candidates.append(usage)
        except (OSError, ValueError, TypeError):
            continue
    if len(candidates) == 1:
        return candidates[0]
    active = [c for c in candidates if c.get("steps_count", 0) > 0]
    if len(active) == 1:
        return active[0]
    return None  # simultaneous chats are ambiguous, never bill the newest by guess
