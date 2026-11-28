"""v3.0.9: stage queue max-hold / max-wait safety and timing telemetry."""
from __future__ import annotations

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

import stage_queue  # noqa: E402
import zeno_tracker  # noqa: E402


class StageQueueSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "q.db"
        p = mock.patch.dict(os.environ, {"ZENO_STAGE_QUEUE": "1", "ZENO_STAGE_MAX_HOLD_SEC": "1"})
        p.start()
        self.addCleanup(p.stop)

    def _slot(self, name: str, **kw):
        return stage_queue.stage_slot("stage4_editing", video_name=name, db_path=self.db,
                                      poll_sec=0.05, quiet=True, **kw)

    def _statuses(self):
        c = sqlite3.connect(str(self.db))
        try:
            return [r[0] for r in c.execute("SELECT status FROM stage_tickets ORDER BY ticket_id")]
        finally:
            c.close()

    def test_fifo_preserved(self) -> None:
        os.environ["ZENO_STAGE_MAX_HOLD_SEC"] = "60"
        order = []

        def w(i):
            with self._slot(f"v{i}"):
                order.append(i)
                time.sleep(0.15)

        ts = []
        for i in range(3):
            t = threading.Thread(target=w, args=(i,))
            t.start()
            ts.append(t)
            time.sleep(0.1)
        for t in ts:
            t.join(10)
        self.assertEqual(order, [0, 1, 2])

    def test_hung_holder_abandoned_and_late_release_harmless(self) -> None:
        got_a = threading.Event()
        release_a = threading.Event()
        b_in = threading.Event()
        b_release = threading.Event()

        def holder_a():
            with self._slot("A"):
                got_a.set()
                release_a.wait(20)

        def holder_b():
            with self._slot("B"):
                b_in.set()
                b_release.wait(20)

        ta = threading.Thread(target=holder_a)
        ta.start()
        self.assertTrue(got_a.wait(5))
        tb = threading.Thread(target=holder_b)
        tb.start()
        self.assertTrue(b_in.wait(8), "waiter should proceed after max hold")
        self.assertEqual(self._statuses(), ["abandoned", "running"])
        # abandoned holder finishes late: must not touch B's ticket
        release_a.set()
        ta.join(5)
        self.assertEqual(self._statuses(), ["abandoned", "running"])
        # a third waiter queues behind B (still FIFO), proceeds after B releases
        c_in = threading.Event()

        def holder_c():
            with self._slot("C"):
                c_in.set()

        tc = threading.Thread(target=holder_c)
        os.environ["ZENO_STAGE_MAX_HOLD_SEC"] = "60"
        tc.start()
        time.sleep(0.3)
        self.assertFalse(c_in.is_set())
        b_release.set()
        tb.join(5)
        self.assertTrue(c_in.wait(5))
        tc.join(5)
        self.assertEqual(self._statuses(), ["abandoned", "done", "done"])

    def test_max_wait_forces_progress(self) -> None:
        os.environ["ZENO_STAGE_MAX_HOLD_SEC"] = "600"
        os.environ["ZENO_STAGE_MAX_WAIT_SEC"] = "0.5"
        a_in, rel = threading.Event(), threading.Event()

        def a():
            with self._slot("A"):
                a_in.set()
                rel.wait(10)

        t = threading.Thread(target=a)
        t.start()
        a_in.wait(5)
        t0 = time.monotonic()
        with self._slot("B"):
            waited = time.monotonic() - t0
        rel.set()
        t.join(5)
        self.assertLess(waited, 5.0)

    def test_timing_event_payload(self) -> None:
        os.environ["ZENO_STAGE_MAX_HOLD_SEC"] = "60"
        proj = Path(self.tmp.name) / "proj"
        proj.mkdir()
        calls = []
        with mock.patch.object(zeno_tracker, "record_stage",
                               side_effect=lambda *a, **k: calls.append((a, k))):
            with self._slot("A", project_dir=proj):
                time.sleep(0.1)
        self.assertEqual(len(calls), 1)
        a, k = calls[0]
        self.assertEqual(a[1], "stage_check")
        self.assertEqual(k["stage"], "queue_stage4_editing")
        ex = k["extra"]
        for key in ("queue_stage", "queue_wait_sec", "queue_run_sec", "queue_position_initial"):
            self.assertIn(key, ex)
        self.assertGreaterEqual(ex["queue_run_sec"], 0.1)

    def test_queued_tracker_uses_extra(self) -> None:
        proj = Path(self.tmp.name)
        with mock.patch.object(zeno_tracker, "record_stage") as rs:
            stage_queue._emit_queued_tracker(proj, "stage4_editing", 2, "x")
        self.assertEqual(rs.call_args.kwargs["extra"]["detail"], "Queued #2 behind x")

    def test_disabled_fail_open(self) -> None:
        with mock.patch.dict(os.environ, {"ZENO_STAGE_QUEUE": "0"}):
            with self._slot("A") as tid:
                self.assertIsNone(tid)


if __name__ == "__main__":
    unittest.main()
