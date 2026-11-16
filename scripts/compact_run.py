"""Run Zeno with bounded console output and a complete local log.

The process still owns its work until exit. Failures retain their exit status and
log tail. progress.json and the desktop events continue to update normally.
"""
from __future__ import annotations

import re
import subprocess
import sys
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IMPORTANT = re.compile(r"^\[(?:BLOCKED|PLAN PASS|VERIFY|VOICE|AUDIO|CUTS|SCRIPT|FRAMES|PICTURE|SPLIT|MARKS|TOKENS|COST|REPORT|PACKET|JOB FOLDER|ARCHIVE|PASS|FAIL|WARNING|ERROR|LISTENER|PANEL)", re.I)


def run(command: list[str], log_dir: Path | None = None) -> int:
    directory = log_dir or ROOT / "work" / "_logs"
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / f"run-{uuid.uuid4().hex}.log"
    print(f"[LOG] {logfile}", flush=True)
    tail: deque[str] = deque(maxlen=30)
    last_stage = None
    warnings = 0
    with logfile.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8", errors="replace", bufsize=1)
        try:
            assert process.stdout is not None
            for raw in process.stdout:
                log.write(raw)
                log.flush()
                line = raw.strip()
                if not line:
                    continue
                tail.append(line)
                stage = re.search(r"Stage (\d)/5", line)
                if stage and stage.group(1) != last_stage:
                    last_stage = stage.group(1)
                    print(f"[PROGRESS] Stage {last_stage}/5", flush=True)
                if line.startswith(("[TIGHT]", "[STYLE]")):
                    warnings += 1
                elif IMPORTANT.match(line) or "[NEXT STEP]" in line or "Rules for this job" in line or "Plan to write" in line:
                    print(line, flush=True)
            process.stdout.close()
            code = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
    if warnings:
        print(f"[WARNINGS] {warnings} pacing/style notes. Read {logfile} before approving the plan.")
    if code:
        print(f"[FAILED] exit={code}; last output:\n" + "\n".join(tail), flush=True)
    else:
        print(f"[COMPLETE] exit=0; full log {logfile}", flush=True)
    return code
