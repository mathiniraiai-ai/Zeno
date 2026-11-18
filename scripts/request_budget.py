"""Durable dispatch budgets for Zeno-managed requests, never for the chat host.

Reservations commit before network/subprocess dispatch. A crash or unknown outcome
keeps its slot and stops further calls until usage is explicitly reconciled. SQLite
transactions serialize concurrent workers. No prompts or credentials are stored.

The full-context reserve is UTF-8 bytes + framing + a configured image allowance.
This deliberately conservative preflight estimate is NOT tokenizer measurement,
provider hidden-context accounting, or an invoice/cache billing guarantee.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import time
import uuid


class BudgetExceeded(ValueError):
    """Dispatch is not safe under the saved budget."""


def _policy():
    from cost_control import settings
    return settings()


def _window(project):
    path = project / "token_telemetry.json"
    if not path.exists():
        # Older projects without telemetry share one persistent budget, never a
        # new allowance on each invocation.
        return "project", 0.0
    try:
        meta = json.loads(path.read_text())
        start = meta.get("job_start_epoch")
        if start is None:
            return "project", 0.0
        if isinstance(start, bool) or not isinstance(start, (int, float)) or not math.isfinite(start) or start <= 0:
            raise ValueError("invalid edit start")
        # Inspect/telemetry windows are reporting boundaries, never budget resets.
        # A different video must use a different project directory.
        return "project", 0.0
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise BudgetExceeded("Invalid edit metadata; model requests stopped.") from exc


@contextmanager
def _transaction(project):
    connection = None
    try:
        ledger = project / "request_budget.sqlite3"
        if ledger.exists() and ledger.stat().st_size == 0:
            raise BudgetExceeded("Empty request budget ledger; model requests stopped.")
        connection = sqlite3.connect(str(ledger), timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute("BEGIN IMMEDIATE")
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 1):
            raise BudgetExceeded("Unsupported request budget ledger; model requests stopped.")
        connection.execute("""CREATE TABLE IF NOT EXISTS calls (
            call_id TEXT PRIMARY KEY, edit_key TEXT NOT NULL, provider TEXT NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('full','targeted')),
            scope TEXT NOT NULL, input_bytes INTEGER NOT NULL CHECK(input_bytes >= 0),
            reserve_tokens INTEGER NOT NULL CHECK(reserve_tokens >= 0),
            created REAL NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','unknown','complete')),
            usage_call_id TEXT UNIQUE, input_tokens INTEGER, output_tokens INTEGER
        )""")
        connection.execute("PRAGMA user_version=1")
        if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise BudgetExceeded("Damaged request budget ledger; model requests stopped.")
        yield connection
        connection.commit()
    except (sqlite3.Error, OSError) as exc:
        raise BudgetExceeded("Cannot safely read/write request budget ledger; model requests stopped.") from exc
    finally:
        if connection is not None:
            connection.close()


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _migrate(connection, project, edit_key, since):
    """Account pre-guard calls; unidentified historical context is treated as full."""
    path = project / "direct_usage.jsonl"
    try:
        lines = path.read_text().splitlines() if path.exists() else []
        report_path = project / "direct_report.json"
        if not any(line.strip() for line in lines) and report_path.exists():
            # Some older installations saved only the last invocation report.
            # Preserve its known calls rather than granting a clean allowance.
            report = json.loads(report_path.read_text())
            calls = report.get("calls", [])
            if not isinstance(calls, list):
                raise ValueError("invalid legacy call report")
            for call in calls:
                if not isinstance(call, dict):
                    raise ValueError("invalid legacy call")
                row = dict(call)
                row.setdefault("timestamp", report.get("started", since))
                row.setdefault("provider", report.get("provider", "legacy"))
                if "input_tokens" not in row and _integer(row.get("input")):
                    parts = [row.get(key, 0) for key in ("input", "cache_read", "cache_write")]
                    if all(_integer(part) for part in parts):
                        row["input_tokens"] = sum(parts)
                if "output_tokens" not in row and _integer(row.get("output")):
                    row["output_tokens"] = row["output"]
                lines.append(json.dumps(row, sort_keys=True))
        for index, line in enumerate(lines):
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("non-object usage")
            stamp = row.get("timestamp", since)
            if isinstance(stamp, bool) or not isinstance(stamp, (int, float)) or not math.isfinite(stamp):
                raise ValueError("invalid usage timestamp")
            legacy_id = str(row.get("call_id") or "legacy-" + hashlib.sha256(f"{index}:{line}".encode()).hexdigest())
            if connection.execute("SELECT 1 FROM calls WHERE call_id=? OR usage_call_id=?", (legacy_id, legacy_id)).fetchone():
                continue
            known = row.get("usage_known", _integer(row.get("input_tokens"))) is True
            known = known and _integer(row.get("input_tokens")) and _integer(row.get("output_tokens"))
            inp = row.get("input_tokens") if known else None
            out = row.get("output_tokens", 0) if known else None
            # An older unknown charge blocks even if an inspect changed the window.
            key = edit_key if stamp >= since else "historical"
            connection.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                legacy_id, key, str(row.get("provider", "legacy")), "full", "legacy", 0,
                inp if inp is not None else 100000, stamp,
                "complete" if known else "unknown", legacy_id, inp, out))
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise BudgetExceeded("Unreadable historical usage; model requests stopped rather than resetting its budget.") from exc


def _summary(connection, edit_key, policy):
    rows = connection.execute("SELECT * FROM calls").fetchall()
    unresolved = connection.execute("SELECT COUNT(*) FROM calls WHERE state!='complete'").fetchone()[0]
    full = [row for row in rows if row["kind"] == "full"]
    limit = min(8, policy["managed_max_calls"])
    full_limit = min(2, policy["planner_full_context_max_calls"])
    reserve_limit = min(100000, policy["planner_full_context_token_reserve"])
    used = sum(row["reserve_tokens"] for row in full)
    reason = ("Unresolved model request; reconcile its usage before another dispatch." if unresolved else
              "Managed model request limit reached." if len(rows) >= limit else "")
    return {"blocked": bool(reason), "reason": reason, "requests": len(rows), "max_requests": limit,
            "full_context_requests": len(full), "max_full_context_requests": full_limit,
            "full_context_reserved_tokens": used, "max_full_context_reserved_tokens": reserve_limit,
            "unresolved_requests": unresolved, "coverage": "Zeno-managed dispatches only; chat host not controlled",
            "reserve_precision": "conservative UTF-8 bytes plus framing/images; not billed tokens"}


def status(project: Path) -> dict:
    """Return a fail-closed checkpoint without making a model request."""
    project = Path(project)
    try:
        policy = _policy()
        edit_key, since = _window(project)
        with _transaction(project) as connection:
            _migrate(connection, project, edit_key, since)
            return _summary(connection, edit_key, policy)
    except (BudgetExceeded, ValueError) as exc:
        return {"blocked": True, "reason": str(exc), "coverage": "Zeno-managed dispatches only; chat host not controlled"}


def reserve_call(project: Path, *, provider: str, context_kind: str,
                 input_bytes: int, image_count: int = 0, scope: str = "planner") -> str:
    """Commit a slot before dispatch. No automatic cancellation/refund is allowed."""
    project = Path(project)
    if context_kind not in {"full", "targeted"} or not _integer(input_bytes) or not _integer(image_count):
        raise BudgetExceeded("Invalid request shape; model request stopped.")
    if context_kind == "targeted" and image_count:
        raise BudgetExceeded("Targeted corrections must be text-only; image context requires a full-context slot.")
    policy = _policy()
    if input_bytes > policy["direct_max_input_bytes"]:
        raise BudgetExceeded("Request exceeds the full prompt byte bound.")
    if context_kind == "targeted" and input_bytes > policy["planner_targeted_max_input_bytes"]:
        raise BudgetExceeded("Correction exceeds the targeted prompt byte bound; request stopped.")
    reserve = input_bytes + 2048 + image_count * policy["direct_image_token_reserve"]
    edit_key, since = _window(project)
    with _transaction(project) as connection:
        _migrate(connection, project, edit_key, since)
        report = _summary(connection, edit_key, policy)
        if report["blocked"]:
            raise BudgetExceeded(report["reason"])
        if context_kind == "full":
            if report["full_context_requests"] >= report["max_full_context_requests"]:
                raise BudgetExceeded("Full-context request limit reached; use a bounded correction.")
            if report["full_context_reserved_tokens"] + reserve > report["max_full_context_reserved_tokens"]:
                raise BudgetExceeded("Full-context conservative input reserve exceeds 100,000; reduce the packet.")
        call_id = str(uuid.uuid4())
        connection.execute("INSERT INTO calls VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
            call_id, edit_key, provider, context_kind, scope, input_bytes, reserve,
            time.time(), "pending", None, None, None))
    return call_id


def finish_call(project: Path, call_id: str, *, usage_known: bool,
                usage: dict | None = None, usage_call_id: str | None = None) -> None:
    """Record a provider outcome, or explicitly reconcile an unknown outcome.

    Callers assert usage_known only after validating provider usage. The journal
    call ID links billing rows to reservations so migration never double counts.
    """
    usage = usage or {}
    inp = usage.get("input_tokens")
    out = usage.get("output_tokens")
    inp = inp if _integer(inp) else None
    out = out if _integer(out) else None
    usage_known = usage_known is True and inp is not None and out is not None
    with _transaction(Path(project)) as connection:
        existing = connection.execute("SELECT state FROM calls WHERE call_id=?", (call_id,)).fetchone()
        if existing is None:
            raise BudgetExceeded("Unknown reservation; model requests stopped.")
        if existing["state"] == "complete":
            if not usage_known:
                raise BudgetExceeded("Cannot replace measured usage with an unknown outcome.")
            return
        if usage_call_id:
            duplicate = connection.execute("SELECT call_id, scope FROM calls WHERE usage_call_id=? AND call_id!=?",
                                           (usage_call_id, call_id)).fetchone()
            if duplicate:
                # A reporting process may have imported the journal between its
                # append and this completion. Merge only that legacy import.
                if duplicate["scope"] != "legacy":
                    raise BudgetExceeded("Usage already belongs to another reservation.")
                connection.execute("DELETE FROM calls WHERE call_id=?", (duplicate["call_id"],))
        connection.execute("UPDATE calls SET state=?, usage_call_id=?, input_tokens=?, output_tokens=?, "
                           "reserve_tokens=MAX(reserve_tokens, ?) WHERE call_id=?", (
            "complete" if usage_known else "unknown", usage_call_id, inp, out, inp or 0, call_id))
