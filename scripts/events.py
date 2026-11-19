"""Live structured JSONL event log per editing job (`work/<project>/events.jsonl`).

Records stage, alignment, vision, cut, render, subtitle, and verification events in real time.
Standard library only; never raises so logging can never interrupt a render.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
FILE = "events.jsonl"
KINDS = ("stage", "face", "scan", "speech", "cut", "render", "check", "verify", "subs", "info", "warn", "error")
_lock = threading.Lock()


def set_project(name: str) -> None:
    """Bind subsequent `emit()` calls to `work/<name>/events.jsonl`."""
    if name:
        os.environ["ZENO_JOB_PROJECT"] = str(name)


def get_project() -> Optional[str]:
    """Return the active project name bound for event logging, if any."""
    name = os.environ.get("ZENO_JOB_PROJECT")
    if name:
        return name
    try:
        return json.loads((WORK / "latest_progress.json").read_text(encoding="utf-8")).get("project")
    except Exception:
        return None


def _project_dir() -> Optional[Path]:
    name = get_project()
    return (WORK / str(name)) if name else None


project_dir = _project_dir


def emit(kind: str, text: str, **data: Any) -> None:
    """Append one structured event to `work/<project>/events.jsonl` (`ZENO_EVENTS=off` disables)."""
    if os.environ.get("ZENO_EVENTS") == "off":
        return
    try:
        d = _project_dir()
        if d is None or not d.is_dir():
            return
        row: Dict[str, Any] = {
            "id": time.time_ns() // 1000,
            "t": round(time.time(), 3),
            "kind": kind if kind in KINDS else "info",
            "text": str(text)[:400],
        }
        row.update({k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))})
        with _lock, open(d / FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read(project_dir: Path | str, since: int = 0, limit: int = 300) -> List[Dict[str, Any]]:
    """Return up to `limit` events from `project_dir/events.jsonl` with `id > since`, oldest first."""
    f = Path(project_dir) / FILE
    rows: List[Dict[str, Any]] = []
    try:
        for line in f.read_text(encoding="utf-8", errors="ignore").splitlines()[-4000:]:
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if int(r.get("id", 0)) > since:
                rows.append(r)
    except OSError:
        return []
    return rows[-limit:]


def clear(project_dir: Optional[Path | str] = None) -> None:
    """Remove `events.jsonl` inside `project_dir` (or the current project directory)."""
    d = Path(project_dir) if project_dir is not None else _project_dir()
    if d is None:
        return
    try:
        (d / FILE).unlink(missing_ok=True)
    except OSError:
        pass
