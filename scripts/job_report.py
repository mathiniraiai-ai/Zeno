"""The small per-job report that goes to the Zeno server with the token numbers: which kind of video it was, what the plan check
said each time, what failed, and what the automatic cleanup freed. Numbers and short reasons only: never the script text, the
footage or the transcript. Standard library only."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT = "job_report.json"
MAX_ENTRIES = 12      # a job rarely needs more checks than this; the report stays small
MAX_TEXT = 200


def _short(s: Any) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


def load(proj_dir: Path) -> Dict[str, Any]:
    p = Path(proj_dir) / REPORT
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(proj_dir: Path, data: Dict[str, Any]) -> None:
    try:
        p = Path(proj_dir) / REPORT
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except Exception:   # a report must never stop a job
        pass


def _append(proj_dir: Path, key: str, entry: Dict[str, Any]) -> None:
    data = load(proj_dir)
    items: List[Dict[str, Any]] = list(data.get(key) or [])
    items.append(entry)
    data[key] = items[-MAX_ENTRIES:]
    _save(proj_dir, data)


def record_plan_check(proj_dir: Path, gate: Dict[str, Any]) -> None:
    """One entry per --check-plan run: passed or blocked, which problems, how many advice lines, the voice note."""
    problems = {str(k): [_short(x) for x in list(v)[:3]] for k, v in (gate.get("problems") or {}).items()}
    entry = {
        "at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "result": "blocked" if gate.get("blocked") else "passed",
        "problems": problems,
        "problem_count": sum(len(v) for v in (gate.get("problems") or {}).values()),
        "tight": len(gate.get("tighten") or []),
        "style": len(gate.get("style") or []),
        "accepted": len(gate.get("accepted") or []),
        "voice_note": _short(gate.get("voice_note") or ""),
    }
    _append(proj_dir, "plan_checks", entry)
    if gate.get("blocked"):
        first = next(iter(problems.items()), ("", []))
        record_failure(proj_dir, "plan_check", f"{first[0]}: {'; '.join(first[1])}" if first[0] else "plan blocked")


def record_failure(proj_dir: Path, stage: str, detail: str) -> None:
    _append(proj_dir, "failures", {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": stage, "detail": _short(detail)})


def record_cleanup(proj_dir: Path, result: Dict[str, Any]) -> None:
    data = load(proj_dir)
    data["cleanup"] = {k: result.get(k) for k in ("ran", "freed_mb", "kept_mb", "files", "error") if k in result}
    _save(proj_dir, data)


def record_subs(proj_dir: Path, style: str, captions: int, adapted: int) -> None:
    data = load(proj_dir)
    data["subs"] = {"style": style, "captions": captions, "colours_adapted": adapted}
    _save(proj_dir, data)


def video_format(proj_dir: Path, plan: Optional[Dict[str, Any]] = None) -> str:
    """The kind of video, as the Director named it on the first line of script_analysis.md (for example 'Format: micro narrative,
    quiz'). Falls back to the plan's preset, then to 'unknown'."""
    p = Path(proj_dir) / "script_analysis.md"
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip().lstrip("#").strip()
            if line:
                low = line.lower()
                if low.startswith("format"):
                    line = line.split(":", 1)[-1].strip() or line
                return _short(line)[:120]
    except Exception:
        pass
    return str((plan or {}).get("style") or (plan or {}).get("preset") or "unknown")


def final_report(proj_dir: Path, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = load(proj_dir)
    data["format"] = video_format(proj_dir, plan)
    checks = data.get("plan_checks") or []
    data["plan_check_runs"] = len(checks)
    data["plan_check_blocked"] = sum(1 for c in checks if c.get("result") == "blocked")
    return data
