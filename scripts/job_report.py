"""Compact per-job quality & execution report (`work/<project>/job_report.json`).

Records plan gate outcomes, Deep QC verification results, subtitle counts, failures, and scratch cleanup
metrics for each edit. Standard library only; never raises so reporting can never interrupt a job.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPORT = "job_report.json"
MAX_ENTRIES = 12
MAX_TEXT = 200


def _short(s: Any) -> str:
    s = " ".join(str(s).split())
    return s if len(s) <= MAX_TEXT else s[: MAX_TEXT - 1] + "…"


def load(proj_dir: Path | str) -> Dict[str, Any]:
    p = Path(proj_dir) / REPORT
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save(proj_dir: Path | str, data: Dict[str, Any]) -> None:
    try:
        p = Path(proj_dir)
        p.mkdir(parents=True, exist_ok=True)
        report_file = p / REPORT
        tmp = report_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(report_file)
    except Exception:
        pass


def _append(proj_dir: Path | str, key: str, entry: Dict[str, Any]) -> None:
    data = load(proj_dir)
    items: List[Dict[str, Any]] = list(data.get(key) or [])
    items.append(entry)
    data[key] = items[-MAX_ENTRIES:]
    _save(proj_dir, data)


def record_plan_check(proj_dir: Path | str, gate: Dict[str, Any]) -> None:
    """Record one `--check-plan` run: passed/blocked status, problem categories, tightening notes, and voice note."""
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


def record_failure(proj_dir: Path | str, stage: str, detail: str) -> None:
    """Record a failure event at `stage` with a bounded summary string."""
    _append(
        proj_dir,
        "failures",
        {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "stage": str(stage), "detail": _short(detail)},
    )


def record_verify(
    proj_dir: Path | str,
    result: Any = None,
    detail: str = "",
    **extra: Any,
) -> None:
    """Record a post-render Deep QC verification run (`verify.py`).

    Accepts either a verification dictionary `record_verify(proj_dir, report_dict)`
    or boolean/keyword arguments `record_verify(proj_dir, passed=True, detail='...', ...)`.
    """
    entry: Dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M:%S")}
    if isinstance(result, dict):
        passed = bool(
            result.get(
                "passed",
                result.get("ok", not bool(result.get("errors") or result.get("failed"))),
            )
        )
        entry["passed"] = passed
        entry["result"] = "passed" if passed else "failed"
        for k, v in result.items():
            if k in ("at", "passed", "result"):
                continue
            if isinstance(v, (bool, int, float)) or v is None:
                entry[str(k)] = v
            elif isinstance(v, str):
                entry[str(k)] = _short(v)
            elif isinstance(v, list):
                entry[str(k)] = [_short(item) for item in v[:5]]
        if detail:
            entry["detail"] = _short(detail)
    elif isinstance(result, bool):
        entry["passed"] = result
        entry["result"] = "passed" if result else "failed"
        if detail:
            entry["detail"] = _short(detail)
    else:
        passed = bool(extra.pop("passed", True))
        entry["passed"] = passed
        entry["result"] = "passed" if passed else "failed"
        summary = detail or (str(result) if result is not None else "")
        if summary:
            entry["detail"] = _short(summary)

    for k, v in extra.items():
        if isinstance(v, (bool, int, float)) or v is None:
            entry[str(k)] = v
        elif isinstance(v, str):
            entry[str(k)] = _short(v)

    data = load(proj_dir)
    data["verify"] = entry
    items: List[Dict[str, Any]] = list(data.get("verify_runs") or [])
    items.append(entry)
    data["verify_runs"] = items[-MAX_ENTRIES:]
    _save(proj_dir, data)

    if not entry.get("passed", True):
        record_failure(proj_dir, "verify", str(entry.get("detail") or "post-render verification failed"))


def record_cleanup(proj_dir: Path | str, result: Dict[str, Any]) -> None:
    """Record post-job scratch cleanup statistics."""
    data = load(proj_dir)
    data["cleanup"] = {k: result.get(k) for k in ("ran", "freed_mb", "kept_mb", "files", "error") if k in result}
    _save(proj_dir, data)


def record_subs(proj_dir: Path | str, style: str, captions: int, adapted: int = 0) -> None:
    """Record subtitle style and rendered caption count."""
    data = load(proj_dir)
    data["subs"] = {"style": str(style), "captions": int(captions), "colours_adapted": int(adapted)}
    _save(proj_dir, data)


def video_format(proj_dir: Path | str, plan: Optional[Dict[str, Any]] = None) -> str:
    """Resolve the video's format label from `script_analysis.md` or plan metadata."""
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
    return str((plan or {}).get("format") or (plan or {}).get("style") or (plan or {}).get("preset") or "unknown")


def final_report(proj_dir: Path | str, plan: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Return the consolidated job report dictionary."""
    data = load(proj_dir)
    data["format"] = video_format(proj_dir, plan)
    checks = data.get("plan_checks") or []
    data["plan_check_runs"] = len(checks)
    data["plan_check_blocked"] = sum(1 for c in checks if c.get("result") == "blocked")
    verify_info = data.get("verify")
    if isinstance(verify_info, dict):
        data["verified"] = bool(verify_info.get("passed"))
    return data
