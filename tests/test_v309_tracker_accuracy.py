#!/usr/bin/env python3
"""v3.0.9 tracker accuracy: job-key rotation, agent env detection, unknown-model cost labelling, stalled flag, outbox."""
from __future__ import annotations

import gzip
import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for p in (str(ROOT / "service"), str(ROOT / "scripts"), str(ROOT / "tests")):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient  # noqa: E402
import token_cost  # noqa: E402
import token_tracker  # noqa: E402
import zeno_tracker  # noqa: E402
from test_v303_fastapi_tracker import _make_test_service  # noqa: E402


class JobKeyTests(unittest.TestCase):
    def test_rotation_and_revision(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            proj = Path(d) / "gdoc_x_t_0"
            f1, f2 = str(Path(d) / "footage1"), str(Path(d) / "footage2")
            k1 = zeno_tracker.resolve_job_key(proj, notion_name="Video A", footage_path=f1, allow_rotate=True)
            # revision: same name + footage
            self.assertEqual(k1, zeno_tracker.resolve_job_key(proj, notion_name="reel_Video A", footage_path=f1, allow_rotate=True))
            # non-opening hooks never rotate
            self.assertEqual(k1, zeno_tracker.resolve_job_key(proj, notion_name="Other"))
            # new name -> new key
            k2 = zeno_tracker.resolve_job_key(proj, notion_name="Video B", footage_path=f1, allow_rotate=True)
            self.assertNotEqual(k1, k2)
            self.assertEqual(k2, zeno_tracker.get_active_job_key(proj))
            # new footage, same name -> new key
            k3 = zeno_tracker.resolve_job_key(proj, notion_name="Video B", footage_path=f2, allow_rotate=True)
            self.assertNotEqual(k2, k3)


class AgentEnvTests(unittest.TestCase):
    def test_env_detection(self) -> None:
        d = token_tracker.detect_agent_env
        self.assertEqual(d({"CLAUDECODE": "1", "ZENO_MODEL": "claude-opus-5"})["agent_name"], "Claude Code")
        self.assertEqual(d({"CLAUDECODE": "1", "ZENO_MODEL": "claude-opus-5"})["agent_model"], "claude-opus-5")
        self.assertEqual(d({"CLAUDE_CODE_ENTRYPOINT": "cli"})["agent_name"], "Claude Code")
        self.assertEqual(d({"CODEX_THREAD_ID": "t1"})["agent_name"], "Codex")
        none = d({})
        self.assertEqual(none["agent_name"], "unknown")
        self.assertTrue(none["agent_detect_reason"])


class CostLabelTests(unittest.TestCase):
    def test_unknown_model_cost(self) -> None:
        r = token_cost.estimate({"token_precision": "exact", "requests": [{"model": "mystery-9", "input_tokens": 10, "output_tokens": 5}]})
        self.assertFalse(r["available"])
        self.assertEqual(r["cost_status"], "unknown")
        self.assertIsNone(token_cost._resolve_rates("unknown"))


class ServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="zeno_v309_")
        self.service = _make_test_service(self.tmp)
        self.client = TestClient(self.service.app)
        self.key = self.service.store().create("Ed One", "edone@gosupernova.live")
        self.admin = {"x-admin-password": "admin-pw"}

    def _post(self, events):
        r = self.client.post("/v2/track/events", content=gzip.compress(json.dumps({"events": events}).encode()),
                             headers={"Authorization": f"Bearer {self.key}", "Content-Encoding": "gzip", "Content-Type": "application/json"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _ev(self, i, typ, ts, **payload):
        return {"event_id": f"e{i}_{ts}", "event_type": typ, "job_key": "vj_a", "ts": ts, "payload": payload}

    def test_stalled_unknown_agent_and_split(self) -> None:
        now = time.time()
        t0 = now - 3 * 3600
        self._post([
            self._ev(1, "job_open", t0, notion_name="Vid A", project_name="p"),
            self._ev(2, "run_telemetry", t0 + 5, agent_name="Claude Code", agent_model="claude-sonnet-5-5"),
            self._ev(3, "run_telemetry", t0 + 6, agent_name="unknown", agent_model="unknown"),
            self._ev(4, "job_blocked", t0 + 60, reason="plan blocked"),
        ])
        s = self.client.get("/v2/track/summary?days=2", headers=self.admin).json()
        job = [j for j in s["jobs"] if j["job_key"] == "vj_a"][0]
        self.assertEqual(job["agent_name"], "Claude Code")
        self.assertEqual(job["agent_model"], "claude-sonnet-5-5")
        self.assertEqual(job["agent_cost_status"], "estimated")
        self.assertTrue(job["is_stalled"])
        self.assertEqual(s["totals"]["stalled"], 1)
        # recent block is not stalled yet
        self._post([self._ev(5, "job_open", now - 60, notion_name="Vid C", project_name="p")] )
        # delivery A, then a different output folder under the same key -> separate video
        self._post([self._ev(6, "video_delivered", now - 50, output_folder="/o/Zeno Outs/Vid A")])
        self._post([self._ev(7, "video_delivered", now - 20, output_folder="/o/Zeno Outs/Vid A")])  # same folder, > 0s later
        self._post([self._ev(8, "video_delivered", now - 10, output_folder="/o/Zeno Outs/Vid B")])
        s = self.client.get("/v2/track/summary?days=2", headers=self.admin).json()
        keys = {j["job_key"] for j in s["jobs"]}
        self.assertEqual(len([k for k in keys if k.startswith("vj_a")]), 2)
        a = [j for j in s["jobs"] if j["job_key"] == "vj_a"][0]
        self.assertEqual(a["revision_count"], 0)  # same-folder re-delivery inside 90s is not a new video
        self.assertEqual(s["totals"]["done"], 2)
        # job with unknown model labelled
        unk = [j for j in s["jobs"] if j["job_key"] != "vj_a" and j["agent_name"] == ""]
        self.assertTrue(all(j["agent_cost_status"] == "unknown" for j in unk))


class OutboxTests(unittest.TestCase):
    def test_pending_survives_and_requeue(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "q.db"
            os.environ.pop("ZENO_TRACKER", None)
            zeno_tracker.enqueue_event("job_open", job_key="vj_x", payload={"notion_name": "n"}, db_path=db)
            conn = zeno_tracker._connect_db(db)
            conn.execute("UPDATE queue SET status='dead_letter', attempts=10, last_error='http_400'")
            conn.commit()
            zeno_tracker._maintain_outbox(conn)
            self.assertEqual(conn.execute("SELECT status FROM queue").fetchone()[0], "pending")
            conn.close()


if __name__ == "__main__":
    unittest.main()
