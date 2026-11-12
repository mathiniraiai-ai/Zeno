#!/usr/bin/env python3
"""
Zeno Live Stage Progress & Heartbeat Reporter (`scripts/progress.py`).

Tracks the 5 canonical editing stages in `work/<project>/progress.json` and emits
unbuffered console heartbeat updates every ~45-50 seconds (and on every stage
transition) so both standalone terminal users and chat-box agents (Claude Code /
Antigravity) can show live backend progress without silence.

Stages:
  Stage 1/5: Mapping       — Scanning raw clips, probing resolution/FPS & extracting audio
  Stage 2/5: Transcribing  — Transcribing clips, scoring retakes & checking speaker voices
  Stage 3/5: Matching      — Aligning script lines, building plan.edl & Editorial Pitch Card
  Stage 4/5: Editing       — Cutting segments, compositing layouts/overlays & transitions
  Stage 5/5: Mastering     — Normalizing -11.5 LUFS audio, exporting .prproj & running Deep QC
"""
from __future__ import annotations

import datetime
import json
import pathlib
import sys
import threading
import time
from typing import Optional

STAGE_NAMES = {
    0: "System Readiness Check",
    1: "Mapping — Collecting & Probing Clips + Audio",
    2: "Transcribing — Speech-to-Text & Voice Matching",
    3: "Matching — Script Alignment & Editorial Pitch",
    4: "Editing — Cutting Segments, Layouts & Effects",
    5: "Mastering — Audio -11.5 LUFS, Premiere .prproj & Deep QC",
}


def _fmt_mmss(seconds: float) -> str:
    sec = max(0, int(round(seconds)))
    return f"{sec // 60:02d}:{sec % 60:02d}"


class ProgressTracker:
    """
    Thread-safe stage progress writer + 45-50s background heartbeat emitter.
    Writes atomic snapshots to:
      - `work/<project>/progress.json`
      - `work/latest_progress.json` (so any external watcher can read current status)
    """

    def __init__(
        self,
        work_dir: Optional[pathlib.Path] = None,
        project_name: str = "zeno_job",
        heartbeat_sec: float = 48.0,
        total_stages: int = 5,
    ):
        self.repo_root = pathlib.Path(__file__).resolve().parent.parent
        self.work_root = self.repo_root / "work"
        self.work_dir = pathlib.Path(work_dir).resolve() if work_dir else (self.work_root / project_name)
        self.project_name = project_name
        try:
            import events

            events.set_project(self.work_dir.name)
        except Exception:
            pass
        self.heartbeat_sec = float(heartbeat_sec)
        self.total_stages = int(total_stages)

        self._lock = threading.Lock()
        self._start_ts = time.monotonic()
        self._stop_evt = threading.Event()
        self._thread: Optional[threading.Thread] = None

        self.stage = 1
        self.stage_name = STAGE_NAMES.get(1, "Mapping")
        self.detail = "Initializing..."
        self.completed = 0
        self.total = 0
        self.pct = 0
        self.state = "running"

    def set_work_dir(self, work_dir: pathlib.Path, project_name: Optional[str] = None) -> None:
        with self._lock:
            self.work_dir = pathlib.Path(work_dir).resolve()
            if project_name:
                self.project_name = project_name
            try:
                import events

                events.set_project(self.work_dir.name)
            except Exception:
                pass
            self._write_snapshot_unlocked()

    def start_heartbeat(self) -> "ProgressTracker":
        if self._thread is not None and self._thread.is_alive():
            return self
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._heartbeat_loop, name="zeno-heartbeat", daemon=True)
        self._thread.start()
        return self

    def stop_heartbeat(self, final_state: str = "completed", final_detail: Optional[str] = None) -> None:
        self._stop_evt.set()
        with self._lock:
            self.state = final_state
            if final_detail:
                self.detail = final_detail
            if final_state == "completed":
                self.pct = 100
            self._write_snapshot_unlocked()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def update(
        self,
        stage: Optional[int] = None,
        detail: Optional[str] = None,
        completed: Optional[int] = None,
        total: Optional[int] = None,
        pct: Optional[int] = None,
        emit_now: bool = True,
    ) -> None:
        with self._lock:
            if stage is not None:
                self.stage = int(stage)
                self.stage_name = STAGE_NAMES.get(self.stage, f"Stage {self.stage}")
            if detail is not None:
                self.detail = str(detail)
            if completed is not None:
                self.completed = int(completed)
            if total is not None:
                self.total = int(total)
            if pct is not None:
                self.pct = max(0, min(100, int(pct)))
            elif self.total > 0:
                stage_base = max(0, (self.stage - 1) * 20)
                stage_span = int(round(20.0 * min(1.0, self.completed / max(1, self.total))))
                self.pct = min(99, stage_base + stage_span)
            self._write_snapshot_unlocked()
            if emit_now:
                self._print_line_unlocked(prefix="STAGE")
            if detail is not None:
                try:
                    import events

                    events.emit("stage", f"{self.stage_name}: {self.detail}", stage=self.stage, pct=self.pct)
                except Exception:
                    pass

    def _snapshot_dict_unlocked(self) -> dict:
        elapsed = round(time.monotonic() - self._start_ts, 1)
        owner_email = ""
        try:
            import common

            owner_email = common.read_job_owner(self.work_dir) or common.active_user_email()
        except Exception:
            pass
        return {
            "project": self.project_name,
            "owner_email": owner_email,
            "stage": self.stage,
            "total_stages": self.total_stages,
            "stage_name": self.stage_name,
            "detail": self.detail,
            "completed": self.completed,
            "total": self.total,
            "pct": self.pct,
            "elapsed_sec": elapsed,
            "elapsed_mmss": _fmt_mmss(elapsed),
            "state": self.state,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def _write_snapshot_unlocked(self) -> None:
        snap = self._snapshot_dict_unlocked()
        try:
            import common

            if snap.get("owner_email"):
                common.stamp_job_owner(self.work_dir, snap["owner_email"])
        except Exception:
            pass
        payload = json.dumps(snap, indent=2, ensure_ascii=False)
        for target_dir in (self.work_dir, self.work_root):
            try:
                target_dir.mkdir(parents=True, exist_ok=True)
                fname = "progress.json" if target_dir == self.work_dir else "latest_progress.json"
                tmp = target_dir / f".{fname}.tmp"
                out = target_dir / fname
                tmp.write_text(payload, encoding="utf-8")
                tmp.replace(out)
            except Exception:
                pass

    def _print_line_unlocked(self, prefix: str = "HEARTBEAT") -> None:
        elapsed = time.monotonic() - self._start_ts
        count_str = f" [{self.completed}/{self.total}]" if self.total > 0 else ""
        msg = (
            f"[{_fmt_mmss(elapsed)}] [{prefix} | Stage {self.stage}/{self.total_stages}: "
            f"{self.stage_name}]{count_str} {self.detail} ({self.pct}%)"
        )
        print(msg, flush=True)

    def _heartbeat_loop(self) -> None:
        while not self._stop_evt.wait(timeout=self.heartbeat_sec):
            with self._lock:
                if self.state != "running":
                    break
                self._write_snapshot_unlocked()
                self._print_line_unlocked(prefix="HEARTBEAT")


def read_latest_progress(work_dir: Optional[pathlib.Path] = None) -> Optional[dict]:
    """Read the latest progress snapshot for a project or workspace."""
    repo_root = pathlib.Path(__file__).resolve().parent.parent
    candidates = []
    if work_dir:
        candidates.append(pathlib.Path(work_dir) / "progress.json")
    candidates.append(repo_root / "work" / "latest_progress.json")
    for c in candidates:
        if c.is_file():
            try:
                return json.loads(c.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None


if __name__ == "__main__":
    snap = read_latest_progress(pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else None)
    if snap:
        print(
            f"[{snap.get('elapsed_mmss', '00:00')}] Stage {snap.get('stage')}/{snap.get('total_stages')} — "
            f"{snap.get('stage_name')}: {snap.get('detail')} ({snap.get('pct')}% | {snap.get('state')})"
        )
    else:
        print("No active Zeno job progress found.")
