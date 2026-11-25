"""Unit tests for Zeno v3.0.5: 5-stage FIFO pipeline queue, per-project isolation, QC summary & Windows prompts."""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import agent_log  # noqa: E402
import clean  # noqa: E402
import render  # noqa: E402
import stage_queue  # noqa: E402


class StageQueueFIFOTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = Path(self.tmp.name) / "_stage_queue.db"

    def test_fifo_serialization_within_same_stage(self) -> None:
        order = []
        active = 0
        overlap = False
        lock = threading.Lock()
        ready_barrier = threading.Event()

        def worker(idx: int, delay: float) -> None:
            nonlocal active, overlap
            if idx > 1:
                ready_barrier.wait(timeout=2.0)
                time.sleep(0.05 * idx)
            with stage_queue.stage_slot(
                "stage4_editing",
                video_name=f"Video_{idx}",
                db_path=self.db_path,
                poll_sec=0.02,
                stale_sec=10.0,
                quiet=True,
            ):
                if idx == 1:
                    ready_barrier.set()
                with lock:
                    active += 1
                    if active > 1:
                        overlap = True
                    order.append(idx)
                time.sleep(delay)
                with lock:
                    active -= 1

        threads = [
            threading.Thread(target=worker, args=(1, 0.25)),
            threading.Thread(target=worker, args=(2, 0.05)),
            threading.Thread(target=worker, args=(3, 0.05)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5.0)

        self.assertFalse(overlap)
        self.assertEqual(order, [1, 2, 3])

    def test_five_stage_pipelining_allows_different_stages_concurrently(self) -> None:
        events = []
        v1_in_stage2 = threading.Event()
        v2_done_stage1 = threading.Event()

        def video1() -> None:
            with stage_queue.stage_slot("stage1_mapping", video_name="V1", db_path=self.db_path, poll_sec=0.02, quiet=True):
                events.append("v1_s1_start")
                time.sleep(0.05)
                events.append("v1_s1_end")
            with stage_queue.stage_slot("stage2_transcribe", video_name="V1", db_path=self.db_path, poll_sec=0.02, quiet=True):
                events.append("v1_s2_start")
                v1_in_stage2.set()
                v2_done_stage1.wait(timeout=2.0)
                time.sleep(0.05)
                events.append("v1_s2_end")

        def video2() -> None:
            v1_in_stage2.wait(timeout=2.0)
            with stage_queue.stage_slot("stage1_mapping", video_name="V2", db_path=self.db_path, poll_sec=0.02, quiet=True):
                events.append("v2_s1_start")
                events.append("v2_s1_end")
                v2_done_stage1.set()
            with stage_queue.stage_slot("stage2_transcribe", video_name="V2", db_path=self.db_path, poll_sec=0.02, quiet=True):
                events.append("v2_s2_start")
                events.append("v2_s2_end")

        t1 = threading.Thread(target=video1)
        t2 = threading.Thread(target=video2)
        t1.start()
        t2.start()
        t1.join(timeout=5.0)
        t2.join(timeout=5.0)

        self.assertLess(events.index("v2_s1_end"), events.index("v1_s2_end"))
        self.assertLess(events.index("v1_s2_end"), events.index("v2_s2_start"))

    def test_stale_or_dead_pid_lock_is_recovered(self) -> None:
        conn = stage_queue._connect(self.db_path)
        now = time.time()
        conn.execute(
            "INSERT INTO stage_tickets (stage, pid, project, video_name, status, enqueued_at, started_at, heartbeat_at) "
            "VALUES (?, ?, ?, ?, 'running', ?, ?, ?)",
            ("stage4_editing", 99999999, "dead_proj", "DeadVideo", now - 120, now - 120, now - 120),
        )
        conn.commit()
        conn.close()

        t0 = time.monotonic()
        with stage_queue.stage_slot("stage4_editing", video_name="LiveVideo", db_path=self.db_path, poll_sec=0.02, stale_sec=5.0, quiet=True):
            elapsed = time.monotonic() - t0
            st = stage_queue.queue_status(db_path=self.db_path, stale_sec=5.0)
            self.assertEqual(st["stages"]["stage4_editing"]["running"]["video_name"], "LiveVideo")
        self.assertLess(elapsed, 1.0)

        conn2 = sqlite3.connect(str(self.db_path))
        statuses = dict(conn2.execute("SELECT video_name, status FROM stage_tickets").fetchall())
        conn2.close()
        self.assertEqual(statuses.get("DeadVideo"), "abandoned")
        self.assertEqual(statuses.get("LiveVideo"), "done")

    def test_same_thread_reentrancy_does_not_deadlock(self) -> None:
        with stage_queue.stage_slot("stage5_mastering", video_name="Outer", db_path=self.db_path, poll_sec=0.02, quiet=True) as t1:
            with stage_queue.stage_slot("stage5_mastering", video_name="Inner", db_path=self.db_path, poll_sec=0.02, quiet=True) as t2:
                self.assertIsNotNone(t1)
                self.assertIsNone(t2)


class IsolationAndTelemetryRegressionTests(unittest.TestCase):
    def test_cleanup_after_job_only_deletes_own_project_segments(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            work = root / "work"
            proj_a = work / "proj_a"
            proj_b = work / "proj_b"
            global_seg = work / "_segments"
            for d in (proj_a / "_segments", proj_b / "_segments", global_seg):
                d.mkdir(parents=True, exist_ok=True)
            (proj_a / "_segments" / "mix.mkv").write_bytes(b"a")
            (proj_b / "_segments" / "mix.mkv").write_bytes(b"b")
            (global_seg / "other.mkv").write_bytes(b"g")

            clean.cleanup_after_job(root=root, keep_hours=0, project_dir=proj_a)

            self.assertFalse((proj_a / "_segments" / "mix.mkv").exists())
            self.assertTrue((proj_b / "_segments" / "mix.mkv").exists())
            self.assertTrue((global_seg / "other.mkv").exists())

    def test_extract_qc_failure_summary_prioritizes_fail_lines(self) -> None:
        qc_log = "\n".join([
            "[MARKS  FAIL] right@00:42.39 FAIL(R=166,G=106) -> marks_contact_sheet.jpg",
            "WHERE  L14 at 42.39s",
            "[SPLIT  PASS] 11 split-screen segs valid",
            "[SUBS   PASS] 120 cues ok",
            "[SQUARE] PASS (1:1 1080x1080)",
        ])
        summary = render._extract_qc_failure_summary(qc_log)
        self.assertIn("[MARKS  FAIL]", summary)
        self.assertIn("WHERE  L14", summary)
        self.assertNotIn("[SUBS   PASS]", summary)
        self.assertNotIn("[SQUARE] PASS", summary)

    def test_windows_path_matching_and_session_pinned_prompts(self) -> None:
        self.assertTrue(agent_log._is_under_root(r"d:\Office\Zeno", Path(r"D:\Office\Zeno")))
        self.assertTrue(agent_log._is_under_root("/d/Office/Zeno/work", Path(r"D:\Office\Zeno")))
        self.assertFalse(agent_log._is_under_root(r"C:\Other\Repo", Path(r"D:\Office\Zeno")))

        with tempfile.TemporaryDirectory() as td:
            home = Path(td)
            proj = home / ".claude" / "projects" / "d--Zeno"
            proj.mkdir(parents=True, exist_ok=True)
            now = time.time()
            ts_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - 5))

            def _row(sid: str, txt: str, cwd: str | None) -> str:
                d = {"uuid": f"u_{sid}", "type": "user", "timestamp": ts_iso, "message": {"content": txt}}
                if cwd is not None:
                    d["cwd"] = cwd
                return json.dumps(d)

            (proj / "sess_a.jsonl").write_text(
                _row("a1", "prompt from chat A", r"d:\Zeno") + "\n" + _row("a2", "second prompt A", None) + "\n",
                encoding="utf-8",
            )
            (proj / "sess_b.jsonl").write_text(
                _row("b1", "prompt from chat B", r"D:\Zeno") + "\n",
                encoding="utf-8",
            )
            with mock.patch("agent_log.os.path.expanduser", side_effect=lambda p: p.replace("~", str(home))):
                coll = agent_log.collect(now - 60, now + 60, root=Path(r"D:\Zeno"), session_id="sess_a")
            texts = [p["text"] for p in coll.get("prompts") or []]
            self.assertEqual(texts, ["prompt from chat A", "second prompt A"])


if __name__ == "__main__":
    unittest.main()
