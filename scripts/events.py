"""A live, human-readable event log for each edit, so a desktop app (or anyone watching) can see what the backend is doing as it happens:
which clip is being scanned for faces, transcribed, cut, rendered or checked. One JSON line per event in `work/<project>/events.jsonl`.
It only reports; it never decides or changes anything, and it never raises (a missing log must not stop an edit). Standard library only."""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
FILE = "events.jsonl"
KINDS = ("stage", "face", "speech", "cut", "render", "check", "verify", "subs", "info", "warn", "error")
_lock = threading.Lock()


def set_project(name: str) -> None:
    """Which edit the events belong to (the work folder's name). Set by the progress tracker when a job starts."""
    if name:
        os.environ["ZENO_JOB_PROJECT"] = str(name)


def _project_dir() -> Optional[Path]:
    name = os.environ.get("ZENO_JOB_PROJECT")
    if not name:
        try:
            name = json.loads((WORK / "latest_progress.json").read_text(encoding="utf-8")).get("project")
        except Exception:   # noqa: BLE001
            name = None
    return (WORK / str(name)) if name else None


def emit(kind: str, text: str, **data: Any) -> None:
    """Add one event. `kind` is one of KINDS; `data` carries small facts (clip name, counts, pass/fail) for the app to show."""
    if os.environ.get("ZENO_EVENTS") == "off":      # the tests set this so they never write into a real job
        return
    try:
        d = _project_dir()
        if d is None or not d.is_dir():
            return
        row: Dict[str, Any] = {"id": time.time_ns() // 1000, "t": round(time.time(), 3), "kind": kind if kind in KINDS else "info", "text": str(text)[:400]}
        row.update({k: v for k, v in data.items() if isinstance(v, (str, int, float, bool, list, dict, type(None)))})
        with _lock, open(d / FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:   # noqa: BLE001
        pass


def read(project_dir: Path, since: int = 0, limit: int = 300) -> List[Dict[str, Any]]:
    """The newest `limit` events with id greater than `since`, oldest first."""
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
