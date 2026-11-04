#!/usr/bin/env python3
"""Measure how ready an editing style is, from files and tests only (no opinions).

Usage:
    python3 scripts/style_readiness.py                 # every style under styles/
    python3 scripts/style_readiness.py micronarrative  # one style
    python3 scripts/style_readiness.py --json          # machine-readable

Levels (each needs everything below it):
    0 NOT READY      spec incomplete, or the engine tests fail
    1 RENDER-ONLY    spec complete and the reference was reproduced, but nothing is proven on real raw footage
    2 PILOT          at least 3 real jobs tagged to this style and at most 2 open unknowns
    3 PRODUCTION     at least 10 tagged real jobs, no open unknowns, a hand-marked gold set of at least 5 clips

A real job is tagged to a style when it ran with `--style <name>`, which writes `#style <name>` into its `work/<project>/plan.edl`.
A gold set is Audacity label files (`*.txt`) in `styles/<name>/gold/`, scored with `scripts/eval_alignment.py`.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_SECTIONS = ["structure", "timing", "audio", "framing", "zoom", "transition_between_scenes", "script_cues", "engine", "tuning"]
LEVEL_NAMES = {0: "NOT READY", 1: "RENDER-ONLY", 2: "PILOT", 3: "PRODUCTION"}
STYLE_TAG_RE = re.compile(r"^#\s*style\s*:?\s*(\S+)", re.I | re.M)


def _check(name: str, ok: Any, detail: str) -> Dict[str, Any]:
    state = "N/A" if ok is None else ("PASS" if ok else "FAIL")
    return {"name": name, "state": state, "detail": detail}


def real_projects(root: Path) -> List[str]:
    """Projects that ran on real footage: not test names (leading underscore), 3 or more clips, from the telemetry database."""
    db = root / "work" / "zeno_telemetry.db"
    if not db.is_file():
        return []
    try:
        with sqlite3.connect(str(db)) as conn:
            rows = conn.execute("SELECT DISTINCT project FROM job_runs WHERE clips_count >= 3").fetchall()
    except sqlite3.Error:
        return []
    return sorted(r[0] for r in rows if r[0] and not r[0].startswith("_"))


def tagged_projects(root: Path, style: str) -> List[str]:
    out = []
    for plan in sorted((root / "work").glob("*/plan.edl")):
        try:
            m = STYLE_TAG_RE.search(plan.read_text(encoding="utf-8", errors="ignore")[:2000])
        except OSError:
            continue
        if m and m.group(1).strip().lower() == style.lower():
            out.append(plan.parent.name)
    return out


def engine_tests_pass(root: Path) -> bool:
    py = root / ".venv" / "bin" / "python"
    if not py.is_file():
        py = root / ".venv" / "Scripts" / "python.exe"
    exe = str(py) if py.is_file() else sys.executable
    r = subprocess.run([exe, "-m", "unittest", "tests.test_engine"], cwd=str(root), capture_output=True, text=True, timeout=300)
    return r.returncode == 0


def assess(style: str, root: Path = ROOT, run_tests: bool = True) -> Dict[str, Any]:
    sdir = root / "styles" / style
    spec_path = sdir / "spec.json"
    checks: List[Dict[str, Any]] = []
    if not spec_path.is_file():
        checks.append(_check("spec.json exists", False, f"{spec_path} is missing"))
        return {"style": style, "level": 0, "verdict": LEVEL_NAMES[0], "checks": checks, "next": ["create styles/%s/spec.json" % style]}
    spec = json.loads(spec_path.read_text(encoding="utf-8"))

    missing = [k for k in REQUIRED_SECTIONS if not spec.get(k)]
    checks.append(_check("spec sections filled", not missing, f"{len(REQUIRED_SECTIONS) - len(missing)}/{len(REQUIRED_SECTIONS)}" + (f", missing {missing}" if missing else "")))
    tuning_n = len(spec.get("tuning") or {})
    checks.append(_check("tuning parameters", tuning_n >= 20, f"{tuning_n} (needs 20 or more)"))

    refs = spec.get("learned_from")
    n_refs = (len(refs) if isinstance(refs, list) else (1 if refs else 0)) + len(spec.get("variants") or [])
    checks.append(_check("reference videos learned from", n_refs >= 1, str(n_refs)))

    rb = spec.get("rebuild_check") or {}
    diffs = [rb.get(k) for k in ("scene1_clean_mean_diff", "scene2_clean_mean_diff", "scene3_clean_mean_diff") if isinstance(rb.get(k), (int, float))]
    repro = bool(diffs) and max(diffs) <= 4.0 and "lag 0" in str(rb.get("audio", "")) and str(rb.get("frames", "")).split(" vs ")[0:1] == str(rb.get("frames", "")).split(" vs ")[1:2]
    checks.append(_check("reference rebuilt frame by frame", repro if rb else False,
                         (f"worst clean-scene difference {max(diffs)} of 255 (limit 4), audio {('in sync' if 'lag 0' in str(rb.get('audio', '')) else 'not proven')}, frames {rb.get('frames')}" if rb else "no rebuild_check recorded")))
    if rb.get("caveat"):
        checks.append(_check("rebuild proves more than the render side", False, str(rb["caveat"])[:150]))

    unknowns = spec.get("unknowns") or []
    checks.append(_check("open unknowns", len(unknowns) == 0, f"{len(unknowns)} open" + (f": {unknowns[0][:70]}..." if unknowns else "")))

    tests_ok = engine_tests_pass(root) if run_tests else None
    checks.append(_check("engine tests pass", tests_ok, "tests.test_engine" if run_tests else "skipped"))

    reals, tagged = real_projects(root), tagged_projects(root, style)
    checks.append(_check("real jobs recorded (all styles)", len(reals) >= 1, f"{len(reals)}: {', '.join(reals[:5])}" if reals else "none"))
    checks.append(_check("real jobs tagged to this style", len(tagged) >= 3, f"{len(tagged)} (pilot needs 3, production 10); run the job with --style {style}"))

    gold = sorted((sdir / "gold").glob("*.txt")) if (sdir / "gold").is_dir() else []
    checks.append(_check("hand-marked gold set", len(gold) >= 5, f"{len(gold)} label files in styles/{style}/gold/ (needs 5); alignment accuracy on real footage stays unmeasured without it"))

    brain = sdir / "brain_memory.json"
    n_rules = 0
    if brain.is_file():
        try:
            n_rules = len(json.loads(brain.read_text(encoding="utf-8")).get("rules") or [])
        except (OSError, ValueError):
            pass
    checks.append(_check("editor rules learned", n_rules >= 1, f"{n_rules} rules in brain_memory.json"))

    st = {c["name"]: c["state"] for c in checks}
    level = 0
    if st["spec sections filled"] == "PASS" and st["tuning parameters"] == "PASS" and st["engine tests pass"] != "FAIL" and st["reference videos learned from"] == "PASS":
        level = 1 if st["reference rebuilt frame by frame"] == "PASS" else 0
    if level >= 1 and len(tagged) >= 3 and len(unknowns) <= 2:
        level = 2
    if level >= 2 and len(tagged) >= 10 and not unknowns and len(gold) >= 5:
        level = 3

    nxt: List[str] = []
    if level < 2:
        nxt.append("run real raw footage for this style and run each job with --style %s (needs 3 jobs)" % style)
    if unknowns:
        nxt.append(f"answer the {len(unknowns)} open unknowns in spec.json")
    if len(gold) < 5:
        nxt.append(f"hand-mark 5 clips (Audacity labels) into styles/{style}/gold/ and score them with scripts/eval_alignment.py")
    return {"style": style, "level": level, "verdict": LEVEL_NAMES[level], "checks": checks, "next": nxt}


def print_report(r: Dict[str, Any]) -> None:
    print(f"\n=== {r['style']}: level {r['level']} - {r['verdict']} ===")
    for c in r["checks"]:
        print(f"  [{c['state']:<4}] {c['name']:<38} {c['detail']}")
    if r["next"]:
        print("  Next:")
        for n in r["next"]:
            print(f"    - {n}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Measure style readiness from files and tests")
    ap.add_argument("style", nargs="?", help="style folder name under styles/ (default: all)")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--no-tests", action="store_true", help="skip running the engine tests")
    a = ap.parse_args()
    names = [a.style] if a.style else sorted(p.name for p in (ROOT / "styles").iterdir() if (p / "spec.json").is_file())
    reports = [assess(n, run_tests=not a.no_tests) for n in names]
    if a.json:
        print(json.dumps(reports, indent=2, ensure_ascii=False))
    else:
        for r in reports:
            print_report(r)
        print("\nOne line: " + "; ".join(f"{r['style']} = {r['verdict']}" for r in reports))


if __name__ == "__main__":
    main()
