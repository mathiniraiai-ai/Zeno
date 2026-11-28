#!/usr/bin/env python3
"""Cross-chat 5-stage FIFO pipeline queue (`scripts/stage_queue.py`).

Allows multiple Zeno chats/processes to advance concurrently through different
pipeline stages (one video per stage at a time, strict FIFO within each stage):
  Stage 1 (`stage1_mapping`):    Mapping — probing clips & inputs
  Stage 2 (`stage2_transcribe`): Transcribing — Gemini STT & VAD
  Stage 3 (`stage3_matching`):   Matching — alignment, contact sheets & packet
  Stage 4 (`stage4_editing`):    Editing — segments, mix, overlays & MP4 encode
  Stage 5 (`stage5_mastering`):  Mastering — Premiere export, Deep QC & delivery

Uses SQLite WAL (`work/_stage_queue.db`) with `BEGIN IMMEDIATE`, live
heartbeats, and dead-PID / stale-heartbeat recovery across macOS & Windows.
100% Python stdlib; fail-open if disabled via `ZENO_STAGE_QUEUE=0`.
"""
from __future__ import annotations

import atexit
import contextlib
import ctypes
import json
import os
import sqlite3
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple

_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / "work"
DEFAULT_DB = WORK / "_stage_queue.db"

STAGES: Dict[str, Tuple[int, str]] = {
    "stage1_mapping": (1, "Mapping — probing clips & inputs"),
    "stage2_transcribe": (2, "Transcribing — Gemini STT & VAD"),
    "stage3_matching": (3, "Matching — alignment, contact sheets & packet"),
    "stage4_editing": (4, "Editing — segments, mix, overlays & MP4 encode"),
    "stage5_mastering": (5, "Mastering — Premiere export, Deep QC & delivery"),
}

# Max seconds a ticket may stay 'running' before waiters treat it as hung.
# Sized ~2-3x the slowest normal run of each stage on an editor PC.
_DEFAULT_MAX_HOLD_SEC: Dict[str, float] = {
    "stage1_mapping": 600.0,      # probing clips: seconds normally
    "stage2_transcribe": 1500.0,  # Gemini STT of many clips, with retries
    "stage3_matching": 600.0,     # alignment + contact sheets + packet
    "stage4_editing": 2400.0,     # segments + 9:16 + 1:1 encode (longest)
    "stage5_mastering": 1200.0,   # Premiere export + Deep QC + delivery
}
_DEFAULT_MAX_WAIT_SEC = 5400.0


def _env_float(name: str, default: float) -> float:
    try:
        v = float(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except Exception:
        return default


def max_hold_sec(stage: str) -> float:
    """Per-stage hold limit; ZENO_STAGE_MAX_HOLD_SEC overrides every stage."""
    return _env_float("ZENO_STAGE_MAX_HOLD_SEC", _DEFAULT_MAX_HOLD_SEC.get(stage, 1800.0))


def max_wait_sec() -> float:
    return _env_float("ZENO_STAGE_MAX_WAIT_SEC", _DEFAULT_MAX_WAIT_SEC)


_HELD_LOCK = threading.Lock()
_HELD_STAGES: Dict[Tuple[str, str, int], int] = {}


def enabled() -> bool:
    return os.environ.get("ZENO_STAGE_QUEUE", "1").strip().lower() not in ("0", "off", "false", "no")


def _db_path(db_path: Optional[Path | str] = None) -> Path:
    if db_path is not None:
        return Path(db_path)
    env_p = os.environ.get("ZENO_STAGE_QUEUE_DB", "").strip()
    return Path(env_p) if env_p else DEFAULT_DB


def _pid_alive(pid: int) -> bool:
    """Return True if process `pid` is still alive (cross-platform macOS/Linux/Windows)."""
    if pid <= 0:
        return False
    if pid == os.getpid():
        return True
    if os.name == "nt":
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        handle = None
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
            if not handle:
                return False
            code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            if not ok:
                return True
            return int(code.value) == STILL_ACTIVE
        except Exception:
            return True
        finally:
            if handle:
                try:
                    ctypes.windll.kernel32.CloseHandle(handle)
                except Exception:
                    pass
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS stage_tickets (
            ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
            stage TEXT NOT NULL,
            pid INTEGER NOT NULL,
            project TEXT NOT NULL DEFAULT '',
            video_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'waiting',
            enqueued_at REAL NOT NULL,
            started_at REAL,
            heartbeat_at REAL NOT NULL,
            finished_at REAL
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_stage_tickets_active ON stage_tickets(stage, status, ticket_id)"
    )
    return conn


def _run_immediate(path: Path, fn: Callable[[sqlite3.Connection], Any]) -> Any:
    """Execute `fn(conn)` inside `BEGIN IMMEDIATE` with Windows SQLite lock retry."""
    last_exc: Optional[BaseException] = None
    for attempt in range(10):
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = _connect(path)
            conn.execute("BEGIN IMMEDIATE")
            res = fn(conn)
            conn.execute("COMMIT")
            return res
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                time.sleep(0.05 * (attempt + 1))
                continue
            raise
        except BaseException:
            if conn is not None:
                try:
                    conn.execute("ROLLBACK")
                except Exception:
                    pass
            raise
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:
                    pass
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("Failed to execute SQLite transaction")


def _sweep_stale(conn: sqlite3.Connection, stage: Optional[str], now: float, stale_sec: float) -> None:
    cutoff = now - max(1.0, float(stale_sec))
    if stage:
        rows = conn.execute(
            "SELECT ticket_id, pid, heartbeat_at FROM stage_tickets "
            "WHERE stage = ? AND status IN ('waiting', 'running')",
            (stage,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT ticket_id, pid, heartbeat_at FROM stage_tickets "
            "WHERE status IN ('waiting', 'running')",
        ).fetchall()
    cur_pid = os.getpid()
    stale_ids: List[int] = []
    for tid, pid, hb in rows:
        if float(hb or 0.0) < cutoff or (int(pid) != cur_pid and not _pid_alive(int(pid))):
            stale_ids.append(int(tid))
    for tid in stale_ids:
        conn.execute(
            "UPDATE stage_tickets SET status = 'abandoned', finished_at = ? "
            "WHERE ticket_id = ? AND status IN ('waiting', 'running')",
            (now, tid),
        )


def _holder_label(row: Tuple[int, int, str, str, str]) -> str:
    _, pid, project, video_name, _ = row
    label = (video_name or project or "").strip()
    if label:
        return f"'{label[:60]}' (pid {pid})"
    return f"pid {pid}"


def _emit_queue_timing(project_dir: Optional[Path], stage: str, extra: Dict[str, Any]) -> None:
    """One cheap tracker event per stage per video on release (never raises)."""
    if project_dir is None:
        return
    try:
        pdir = Path(project_dir)
        if not pdir.is_dir():
            return
        import zeno_tracker as _zt
        _zt.record_stage(pdir, "stage_check", stage=f"queue_{stage}"[:40], extra=extra)
    except BaseException:
        pass


def _emit_queued_tracker(project_dir: Optional[Path], stage: str, pos: int, holder: str) -> None:
    if project_dir is None:
        return
    try:
        pdir = Path(project_dir)
        if not pdir.is_dir():
            return
        import zeno_tracker as _zt
        _zt.record_stage(
            pdir,
            "stage_check",
            stage=f"queued_{stage}",
            extra={"detail": f"Queued #{pos} behind {holder}"},
        )
    except BaseException:
        pass


@contextlib.contextmanager
def stage_slot(
    stage: str,
    *,
    project_dir: Optional[Path | str] = None,
    video_name: str = "",
    db_path: Optional[Path | str] = None,
    poll_sec: float = 0.5,
    stale_sec: float = 45.0,
    quiet: bool = False,
) -> Iterator[Optional[int]]:
    """Acquire strict FIFO exclusive execution for `stage` across chats/processes."""
    if not enabled():
        yield None
        return

    path = _db_path(db_path).resolve()
    tid_key = (str(path), str(stage), threading.get_ident())
    with _HELD_LOCK:
        depth = _HELD_STAGES.get(tid_key, 0)
        if depth > 0:
            _HELD_STAGES[tid_key] = depth + 1
            reentered = True
        else:
            reentered = False
    if reentered:
        try:
            yield None
        finally:
            with _HELD_LOCK:
                cur = _HELD_STAGES.get(tid_key, 1)
                if cur <= 1:
                    _HELD_STAGES.pop(tid_key, None)
                else:
                    _HELD_STAGES[tid_key] = cur - 1
        return

    proj_path = Path(project_dir) if project_dir is not None else None
    project_label = proj_path.name if proj_path is not None else ""
    vid_label = str(video_name or "").strip()
    stage_num, _stage_desc = STAGES.get(stage, (0, stage))
    cur_pid = os.getpid()

    def _enqueue(conn: sqlite3.Connection) -> int:
        now = time.time()
        _sweep_stale(conn, stage, now, stale_sec)
        cur = conn.execute(
            "INSERT INTO stage_tickets (stage, pid, project, video_name, status, enqueued_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?, 'waiting', ?, ?)",
            (stage, cur_pid, project_label[:120], vid_label[:180], now, now),
        )
        return int(cur.lastrowid)

    ticket_id = _run_immediate(path, _enqueue)
    stop_evt = threading.Event()
    released = False
    t_enq = time.monotonic()
    timing: Dict[str, Any] = {"acquired": None, "pos0": None, "forced": ""}
    hold_limit = max_hold_sec(stage)
    wait_limit = max_wait_sec()

    def _release() -> None:
        nonlocal released
        if released:
            return
        released = True
        stop_evt.set()
        try:
            def _mark_done(conn: sqlite3.Connection) -> None:
                now = time.time()
                conn.execute(
                    "UPDATE stage_tickets SET status = 'done', finished_at = ?, heartbeat_at = ? "
                    "WHERE ticket_id = ? AND status IN ('waiting', 'running')",
                    (now, now, ticket_id),
                )
                conn.execute(
                    "DELETE FROM stage_tickets WHERE status IN ('done', 'abandoned') AND finished_at < ?",
                    (now - 86400.0,),
                )
            _run_immediate(path, _mark_done)
        except Exception:
            pass
        try:
            t_acq = timing["acquired"]
            t_now = time.monotonic()
            _emit_queue_timing(proj_path, stage, {
                "queue_stage": stage,
                "queue_wait_sec": round(((t_acq if t_acq is not None else t_now) - t_enq), 2),
                "queue_run_sec": round((t_now - t_acq), 2) if t_acq is not None else 0.0,
                "queue_position_initial": int(timing["pos0"] or 0),
                "queue_forced": timing["forced"],
            })
        except BaseException:
            pass

    atexit.register(_release)
    hb_thread: Optional[threading.Thread] = None
    try:
        last_log_ts = 0.0
        last_track_ts = 0.0
        while True:
            force = (time.monotonic() - t_enq) >= wait_limit

            def _try_acquire(conn: sqlite3.Connection) -> Tuple[bool, int, str, List[str]]:
                now = time.time()
                _sweep_stale(conn, stage, now, stale_sec)
                notes: List[str] = []
                while True:
                    active = conn.execute(
                        "SELECT ticket_id, pid, project, video_name, status, started_at FROM stage_tickets "
                        "WHERE stage = ? AND status IN ('waiting', 'running') ORDER BY ticket_id ASC",
                        (stage,),
                    ).fetchall()
                    if not active or int(active[0][0]) == ticket_id:
                        break
                    front = active[0]
                    held = now - float(front[5]) if front[5] else 0.0
                    if front[4] == "running" and held > hold_limit:
                        reason = f"held {int(held)}s > max {int(hold_limit)}s (hung?)"
                    elif force:
                        reason = f"waiter exceeded max wait {int(wait_limit)}s"
                    else:
                        break
                    conn.execute(
                        "UPDATE stage_tickets SET status = 'abandoned', finished_at = ? "
                        "WHERE ticket_id = ? AND status IN ('waiting', 'running')",
                        (now, int(front[0])),
                    )
                    notes.append(f"{_holder_label(front[:5])} {reason}")
                if not active or int(active[0][0]) == ticket_id:
                    conn.execute(
                        "UPDATE stage_tickets SET status = 'running', started_at = COALESCE(started_at, ?), heartbeat_at = ? "
                        "WHERE ticket_id = ?",
                        (now, now, ticket_id),
                    )
                    return True, 0, "", notes
                conn.execute(
                    "UPDATE stage_tickets SET heartbeat_at = ? WHERE ticket_id = ?",
                    (now, ticket_id),
                )
                pos = 1
                for idx, r in enumerate(active):
                    if int(r[0]) == ticket_id:
                        pos = idx
                        break
                return False, max(1, pos), _holder_label(active[0][:5]), notes

            acquired, pos, holder, notes = _run_immediate(path, _try_acquire)
            for note in notes:
                timing["forced"] = note[:120]
                print(f"[QUEUE] Stage {stage_num}/5 ({stage}): marking holder {note}; proceeding (holder not killed).", flush=True)
            if timing["pos0"] is None:
                timing["pos0"] = 0 if acquired else pos
            if acquired:
                timing["acquired"] = time.monotonic()
                break
            now_mono = time.monotonic()
            if not quiet and (last_log_ts == 0.0 or (now_mono - last_log_ts) >= 30.0):
                print(
                    f"[QUEUE] Stage {stage_num}/5 ({stage}): waiting in FIFO queue (#{pos} behind {holder})...",
                    flush=True,
                )
                last_log_ts = now_mono
            if last_track_ts == 0.0 or (now_mono - last_track_ts) >= 600.0:
                _emit_queued_tracker(proj_path, stage, pos, holder)
                last_track_ts = now_mono
            time.sleep(max(0.02, float(poll_sec)))

        with _HELD_LOCK:
            _HELD_STAGES[tid_key] = _HELD_STAGES.get(tid_key, 0) + 1

        hb_interval = min(5.0, max(0.25, float(stale_sec) / 4.0))

        def _heartbeat_loop() -> None:
            while not stop_evt.wait(hb_interval):
                try:
                    def _bump(conn: sqlite3.Connection) -> None:
                        conn.execute(
                            "UPDATE stage_tickets SET heartbeat_at = ? WHERE ticket_id = ? AND status = 'running'",
                            (time.time(), ticket_id),
                        )
                    _run_immediate(path, _bump)
                except Exception:
                    pass

        hb_thread = threading.Thread(target=_heartbeat_loop, name=f"zeno-qhb-{stage}", daemon=True)
        hb_thread.start()
        yield ticket_id
    finally:
        stop_evt.set()
        if hb_thread is not None:
            hb_thread.join(timeout=1.0)
        with _HELD_LOCK:
            cur = _HELD_STAGES.get(tid_key, 0)
            if cur <= 1:
                _HELD_STAGES.pop(tid_key, None)
            else:
                _HELD_STAGES[tid_key] = cur - 1
        _release()
        try:
            atexit.unregister(_release)
        except Exception:
            pass


def queue_status(db_path: Optional[Path | str] = None, stale_sec: float = 45.0) -> Dict[str, Any]:
    """Return active running and waiting tickets grouped by stage."""
    path = _db_path(db_path).resolve()
    if not path.is_file():
        return {
            "ok": True,
            "db_path": str(path),
            "stages": {
                k: {"stage_num": STAGES[k][0], "label": STAGES[k][1], "running": None, "waiting": []}
                for k in STAGES
            },
        }

    def _read(conn: sqlite3.Connection) -> Dict[str, Any]:
        now = time.time()
        _sweep_stale(conn, None, now, stale_sec)
        rows = conn.execute(
            "SELECT ticket_id, stage, pid, project, video_name, status, enqueued_at, started_at, heartbeat_at "
            "FROM stage_tickets WHERE status IN ('waiting', 'running') ORDER BY ticket_id ASC"
        ).fetchall()
        stages_out: Dict[str, Any] = {k: {"stage_num": STAGES[k][0], "label": STAGES[k][1], "running": None, "waiting": []} for k in STAGES}
        for tid, stage, pid, proj, vname, st, enq_at, st_at, hb_at in rows:
            bucket = stages_out.setdefault(stage, {"stage_num": 0, "label": stage, "running": None, "waiting": []})
            item = {
                "ticket_id": tid,
                "pid": pid,
                "project": proj,
                "video_name": vname,
                "status": st,
                "enqueued_at": enq_at,
                "started_at": st_at,
                "heartbeat_at": hb_at,
            }
            if st == "running" and bucket["running"] is None:
                bucket["running"] = item
            else:
                bucket["waiting"].append(item)
        return {"ok": True, "db_path": str(path), "stages": stages_out}

    return _run_immediate(path, _read)


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Inspect Zeno 5-stage FIFO pipeline queue.")
    ap.add_argument("--status", action="store_true", help="Print current running and queued tickets per stage")
    ap.add_argument("--db", default=None, help="Override path to _stage_queue.db")
    ap.add_argument("--json", action="store_true", help="Emit JSON output")
    args = ap.parse_args(argv)
    st = queue_status(db_path=args.db)
    if args.json:
        print(json.dumps(st, indent=2))
        return 0
    print(f"Zeno 5-Stage Queue ({st['db_path']}):")
    for stage_key, info in st["stages"].items():
        run = info.get("running")
        wait = info.get("waiting") or []
        r_txt = f"{run['video_name'] or run['project'] or 'job'} (pid {run['pid']})" if run else "idle"
        print(f"  Stage {info['stage_num']}/5 {stage_key}: {r_txt} | waiting={len(wait)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
