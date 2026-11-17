"""Run Zeno with bounded console output, inline pacing/style notes, and a 25s keepalive heartbeat.

The process still owns its work until exit. Failures retain their exit status and
log tail. progress.json and the desktop events continue to update normally.
"""
from __future__ import annotations

import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HEARTBEAT_SECONDS = 25.0
MAX_INLINE_WARNINGS = 6
IMPORTANT = re.compile(
    r"^\[(?:BLOCKED|ORDER BLOCKED|PLAN PASS|VERIFY|VOICE|AUDIO|CUTS|SCRIPT|FRAMES|PICTURE|SPLIT|MARKS|SUBS|SQUARE|REVIEW|DIRECT|TOKENS|COST|REPORT|PACKET_PARTS|PACKET|JOB FOLDER|ARCHIVE|PASS|FAIL|WARNING|ERROR|LISTENER|PANEL|MANUAL_FALLBACK_PACK|BATCH PLAYBOOK|DO NOT|PLAN READY|AUTO-RENDER|TURN [0-9])",
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


def run(command: list[str], log_dir: Path | None = None) -> int:
    directory = log_dir or ROOT / "work" / "_logs"
    directory.mkdir(parents=True, exist_ok=True)
    logfile = directory / f"run-{uuid.uuid4().hex}.log"
    print(f"[LOG] {logfile}", flush=True)
    tail: deque[str] = deque(maxlen=30)
    last_stage = None
    warning_lines: list[str] = []
    in_pitch = False
    pending_box_top: str | None = None
    started = time.monotonic()

    with logfile.open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
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
                if "ZENO EDITORIAL PITCH" in line:
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
                elif IMPORTANT.match(line) or "[NEXT STEP]" in line or "Rules for this job" in line or "Plan to write" in line:
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
