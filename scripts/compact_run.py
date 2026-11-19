#!/usr/bin/env python3
"""Run Zeno commands with concise console filtering, 25s keepalive heartbeats, and full log capture.

Writes full unfiltered stdout/stderr to `work/_logs/run-<uuid>.log` while surfacing stage transitions,
editorial pitch cards, key verification lines, and failure tails on stdout.
"""
from __future__ import annotations

import os
import sys

sys.dont_write_bytecode = True
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")

import queue
import re
import subprocess
import threading
import time
import uuid
from collections import deque
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_SECONDS = 25.0
MAX_INLINE_WARNINGS = 6
IMPORTANT = re.compile(
    r"^\[(?:BLOCKED|ORDER BLOCKED|PLAN PASS|GATE PASSED|VERIFY|VOICE|AUDIO|CUTS|SCRIPT|FRAMES|PICTURE|SPLIT|MARKS|SUBS|SQUARE|REVIEW|SNAP|CARDS|BEATS|DIRECT|REPORT|BRIEF|PACKET_PARTS|PACKET|JOB FOLDER|OUTPUT|DELIVERY|DELIVERED|QUEUE|MULTI-VIDEO GUIDE|AUTH|FEEDBACK|PASS|FAIL|WARN|WARNING|ERROR|LISTENER|PANEL|DO NOT|WAIT|TURN [0-9])",
    re.I,
)


def _reader(pipe, out_queue: "queue.Queue[str | None]") -> None:
    try:
        for raw in pipe:
            out_queue.put(raw)
    finally:
        try:
            pipe.close()
        except Exception:
            pass
        out_queue.put(None)


def run(command: List[str], log_dir: Optional[Path] = None, cwd: Optional[Path] = None) -> int:
    """Execute `command`, save full output to `work/_logs/run-<uuid>.log`, and emit concise progress."""
    directory = Path(log_dir) if log_dir is not None else (ROOT / "work" / "_logs")
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / f"run-{uuid.uuid4().hex}.log"
    print(f"[LOG] {logfile}", flush=True)

    tail: deque[str] = deque(maxlen=30)
    last_stage: Optional[str] = None
    warning_lines: List[str] = []
    in_pitch = False
    pending_box_top: Optional[str] = None
    started = time.monotonic()

    env = dict(os.environ)
    env.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    env.setdefault("PYTHONUNBUFFERED", "1")

    with logfile.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [str(c) for c in command],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=str(cwd) if cwd is not None else None,
            env=env,
        )
        assert process.stdout is not None
        line_queue: queue.Queue[str | None] = queue.Queue()
        reader = threading.Thread(target=_reader, args=(process.stdout, line_queue), daemon=True)
        reader.start()
        try:
            while True:
                try:
                    raw = line_queue.get(timeout=HEARTBEAT_SECONDS)
                except queue.Empty:
                    if process.poll() is not None and line_queue.empty():
                        break
                    elapsed = int(round(time.monotonic() - started))
                    print(f"[PROGRESS] still working ({elapsed}s)...", flush=True)
                    continue
                if raw is None:
                    break
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

                if line.startswith("╔"):
                    pending_box_top = line
                    continue
                if (
                    "EDITORIAL PITCH" in line
                    or "ZENO EDIT BAY" in line
                    or (pending_box_top is not None and "ZENO" in line)
                ):
                    if pending_box_top:
                        print(pending_box_top, flush=True)
                        pending_box_top = None
                    in_pitch = True
                else:
                    pending_box_top = None

                if in_pitch:
                    print(raw.rstrip("\r\n"), flush=True)
                    if line.startswith("────"):
                        in_pitch = False
                    continue

                if line.startswith(("[TIGHT]", "[STYLE]")):
                    warning_lines.append(line[:220])
                    if len(warning_lines) <= MAX_INLINE_WARNINGS:
                        print(line[:220], flush=True)
                elif (
                    IMPORTANT.match(line)
                    or "[NEXT STEP]" in line
                    or "Rules for this job" in line
                    or "Plan to write" in line
                ):
                    print(line, flush=True)

            reader.join(timeout=2.0)
            code = process.wait()
        except BaseException:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise

    if len(warning_lines) > MAX_INLINE_WARNINGS:
        extra = len(warning_lines) - MAX_INLINE_WARNINGS
        print(f"[WARNINGS] +{extra} more notes in {logfile}", flush=True)
    if code:
        print(f"[FAILED] exit={code}; last output:\n" + "\n".join(tail), flush=True)
    else:
        print(f"[COMPLETE] exit=0; full log {logfile}", flush=True)
    return code


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit("Usage: python3 scripts/compact_run.py <command> [args...]")
    sys.exit(run(sys.argv[1:]))
