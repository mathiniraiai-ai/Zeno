#!/usr/bin/env python3
"""Tests for Zeno v3.0.3 FastAPI v2 video tracker, verified OTA receipts, and client outbox."""
from __future__ import annotations

import base64
import gzip
import http.server
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
SERVICE_DIR = ROOT / "service"
SCRIPTS_DIR = ROOT / "scripts"
for p in (str(SERVICE_DIR), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fastapi.testclient import TestClient  # noqa: E402
import clean  # noqa: E402
import zeno_tracker  # noqa: E402


def _make_test_service(tmpdir: str):
    os.environ["ZENO_SIGNUP_CODE"] = "team-code"
    os.environ["ZENO_ADMIN_PASSWORD"] = "admin-pw"
    os.environ["ZENO_DB_PATH"] = str(Path(tmpdir) / "tracker_test.db")
    os.environ["ZENO_RATE_PER_MIN"] = "200"
    os.environ["ZENO_MAX_SIGNUPS_PER_HOUR"] = "500"
    os.environ.pop("DATABASE_URL", None)

    import app as service_app
    from keystore import KeyStore

    service_app._store = KeyStore(sqlite_path=os.environ["ZENO_DB_PATH"])
    return service_app


class FastAPITrackerServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp(prefix="zeno_trk_srv_")
        self.service = _make_test_service(self.tmp)
        self.client = TestClient(self.service.app)
        self.admin_hdr = {
            "Authorization": "Basic " + base64.b64encode(b"admin:admin-pw").decode("ascii"),
            "X-Zeno-Admin": "1",
        }
        self.key1 = self.service.store().create("Madhu Sudhan", "madhusudhan@gosupernova.live")
        self.key2 = self.service.store().create("Madhu Sudhan", "madhusudhan@gosupernova.live")

    def _post_gzip(self, key: str, payload: dict, extra_headers: dict | None = None):
        raw = json.dumps(payload).encode("utf-8")
        gz = gzip.compress(raw)
        hdrs = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Content-Encoding": "gzip",
            "X-Zeno-Version": "3.0.3",
            "X-Zeno-Platform": "Darwin arm64",
            "X-Zeno-Machine-Id": "m_mac_01",
        }
        if extra_headers:
            hdrs.update(extra_headers)
        return self.client.post("/v2/track/events", content=gz, headers=hdrs)

    def test_video_lifecycle_gemini_retry_and_post_render_revision(self) -> None:
        base_ts = time.time() - 300.0
        # 1. Open job via key1 + Gemini initial pass + Gemini retry on same clip
        res = self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_open_1",
                        "event_type": "job_open",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts,
                        "payload": {
                            "notion_name": "W48 - Telugu - AI Only - Restaurant Skit",
                            "script_source": "https://docs.google.com/document/d/DOC123/edit?tab=t.TAB9",
                            "project_name": "gdoc_DOC123_t_TAB9",
                            "clip_hashes": ["clip_a", "clip_b"],
                            "unique_clips_count": 2,
                        },
                    },
                    {
                        "event_id": "ev_gem_1",
                        "event_type": "gemini_call",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 10,
                        "payload": {"clip_hash": "clip_a", "in_tokens": 1000, "out_tokens": 200, "audio_seconds": 12.0},
                    },
                    {
                        "event_id": "ev_gem_retry",
                        "event_type": "gemini_retry",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 20,
                        "payload": {"clip_hash": "clip_a", "is_retry": True, "in_tokens": 1100, "out_tokens": 250, "audio_seconds": 12.0},
                    },
                ]
            },
        )
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["accepted"], 3)

        # Duplicate event_id is idempotent
        dup = self._post_gzip(
            self.key1,
            {"events": [{"event_id": "ev_gem_retry", "event_type": "gemini_retry", "job_key": "vj_madhu_1", "ts": base_ts + 20}]},
        )
        self.assertEqual(dup.status_code, 200)
        self.assertEqual(dup.json()["duplicates"], 1)

        # 2. Rendered + back-to-back delivered + job_done within 90s (using key2 for same email!)
        res2 = self._post_gzip(
            self.key2,
            {
                "events": [
                    {
                        "event_id": "ev_rnd_1",
                        "event_type": "video_rendered",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 120,
                        "payload": {"agent_name": "Claude Code", "agent_model": "claude-sonnet-5-5"},
                    },
                    {
                        "event_id": "ev_deliv_1",
                        "event_type": "video_delivered",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 125,
                        "payload": {
                            "output_folder": "/Users/madhu/Downloads/Zeno Outs/W48 - Telugu - AI Only - Restaurant Skit",
                            "prompts": [{"at": base_ts + 5, "text": "edit this video"}],
                            "agent_calls": 6,
                            "agent_in_tokens": 4000,
                            "agent_cache_write_tokens": 1000,
                            "agent_cache_read_tokens": 12000,
                            "agent_out_tokens": 800,
                            "agent_total_tokens": 16800,
                        },
                    },
                    {
                        "event_id": "ev_deliv_1b",
                        "event_type": "video_delivered",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 130,
                        "payload": {"prompts": [{"at": base_ts + 5, "text": "edit this video"}]},
                    },
                ]
            },
        )
        self.assertEqual(res2.status_code, 200, res2.text)

        # 3. Post-render revision (>90s later) -> MUST stay 1 video, revision_count == 1
        res3 = self._post_gzip(
            self.key2,
            {
                "events": [
                    {
                        "event_id": "ev_rev_1",
                        "event_type": "video_delivered",
                        "job_key": "vj_madhu_1",
                        "ts": base_ts + 300,
                        "payload": {
                            "prompts": [{"at": base_ts + 250, "text": "fix subtitle timing in L3"}],
                            "agent_calls": 9,
                            "agent_total_tokens": 21000,
                        },
                    }
                ]
            },
        )
        self.assertEqual(res3.status_code, 200, res3.text)

        summary = self.client.get("/v2/track/summary?days=1", headers=self.admin_hdr).json()
        self.assertEqual(summary["totals"]["done"], 1)
        self.assertEqual(summary["totals"]["revisions"], 1)
        self.assertEqual(summary["totals"]["unique_clips"], 2)
        self.assertEqual(summary["totals"]["gemini_retries"], 1)
        # Unified by email across key1 + key2!
        self.assertEqual(len(summary["editors"]), 1)
        ed = summary["editors"][0]
        self.assertEqual(ed["editor_email"], "madhusudhan@gosupernova.live")
        self.assertEqual(ed["done"], 1)
        self.assertEqual(ed["prompts"], 2)

        detail = self.client.get("/v2/track/jobs/vj_madhu_1", headers=self.admin_hdr).json()["job"]
        self.assertEqual(detail["unique_clips_count"], 2)
        self.assertEqual(detail["gemini_calls"], 2)
        self.assertEqual(detail["gemini_retry_calls"], 1)
        self.assertEqual(detail["revision_count"], 1)
        self.assertEqual(detail["user_prompt_count"], 2)
        self.assertEqual(len(detail["revisions"]), 1)

    def test_folder_rename_merge_vs_distinct_variants_on_same_doc(self) -> None:
        now = time.time()
        # Folder rename merge: inspect in gdoc_DOCX_t_1, then render in hotel_clean with same Notion name
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_ren_1",
                        "event_type": "job_open",
                        "job_key": "vj_old_folder",
                        "ts": now - 60,
                        "payload": {
                            "notion_name": "W47 - Hindi - Hotel Skit",
                            "script_source": "https://docs.google.com/document/d/DOCX/edit?tab=t.1",
                            "project_name": "gdoc_DOCX_t_1",
                        },
                    },
                    {
                        "event_id": "ev_ren_2",
                        "event_type": "video_delivered",
                        "job_key": "vj_clean_folder",
                        "ts": now - 10,
                        "payload": {
                            "notion_name": "reel_W47 - Hindi - Hotel Skit.mp4",
                            "project_name": "hotel_clean",
                        },
                    },
                ]
            },
        )
        # Distinct variants on same script doc (Vertical vs Horizontal) MUST remain 2 separate videos!
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_var_1",
                        "event_type": "video_delivered",
                        "job_key": "vj_var_vert",
                        "ts": now - 5,
                        "payload": {
                            "notion_name": "W47 - Hindi - Roti Making - Vertical",
                            "script_source": "https://docs.google.com/document/d/DOC_ROTI/edit?tab=t.0",
                        },
                    },
                    {
                        "event_id": "ev_var_2",
                        "event_type": "video_delivered",
                        "job_key": "vj_var_horiz",
                        "ts": now - 2,
                        "payload": {
                            "notion_name": "W47 - Hindi - Roti Making - Horizontal",
                            "script_source": "https://docs.google.com/document/d/DOC_ROTI/edit?tab=t.0",
                        },
                    },
                ]
            },
        )
        summary = self.client.get("/v2/track/summary?days=1", headers=self.admin_hdr).json()
        self.assertEqual(summary["totals"]["done"], 3)
        self.assertEqual(summary["totals"]["opened"], 3)

    def test_errors_stuck_detection_and_progress_compat(self) -> None:
        now = time.time()
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_stuck_open",
                        "event_type": "job_open",
                        "job_key": "vj_stuck_1",
                        "ts": now - 2400,  # 40 min idle -> stuck (>30m)
                        "payload": {"notion_name": "W48 - Stuck Video", "project_name": "p_stuck"},
                    },
                    {
                        "event_id": "ev_blk_1",
                        "event_type": "job_blocked",
                        "job_key": "vj_blk_1",
                        "ts": now - 30,
                        "payload": {
                            "notion_name": "W48 - Blocked Video",
                            "stage": "plan_gate",
                            "error_message": "[WRONG VOICE] L3 speaker mismatch",
                        },
                    },
                ]
            },
        )
        # /v1/jobs/progress backward-compatible heartbeat must return 200 OK
        hb = self.client.post(
            "/v1/jobs/progress",
            headers={"Authorization": f"Bearer {self.key1}", "X-Zeno-Version": "3.0.3"},
            json={"progress_uid": "p1", "progress": {"project": "p_live", "stage_name": "Render", "pct": 70}},
        )
        self.assertEqual(hb.status_code, 200)

        errs = self.client.get("/v2/track/errors?days=1", headers=self.admin_hdr).json()
        self.assertEqual(errs["counts"]["qc_plan_block"], 1)
        self.assertEqual(errs["counts"]["stuck_abandoned"], 1)

    def test_verified_ota_receipt_and_platform_preservation(self) -> None:
        # First request sets platform
        self.client.get(
            "/v1/version",
            headers={"Authorization": f"Bearer {self.key1}", "X-Zeno-Version": "3.0.2", "X-Zeno-Platform": "Windows AMD64"},
        )
        # Blank platform update must NOT wipe Windows AMD64
        self.service.store().note_version(1, "3.0.3", "", "downloaded update", "Madhu Sudhan")
        keys = self.client.get("/admin/api/versions", headers=self.admin_hdr).json()["keys"]
        self.assertTrue(any(k["zeno_version"] == "3.0.3" and k["client_platform"] == "Windows AMD64" for k in keys))

        # Verified install receipt updates fleet status
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_ota_1",
                        "event_type": "update_installed",
                        "payload": {
                            "before_version": "3.0.2",
                            "after_version": "3.0.3",
                            "bundle_sha256": "abc123def",
                            "files_updated": 42,
                        },
                    }
                ]
            },
        )
        fleet = self.client.get("/v2/track/fleet", headers=self.admin_hdr).json()
        self.assertEqual(len(fleet["receipts"]), 1)
        self.assertEqual(fleet["receipts"][0]["after_version"], "3.0.3")
        self.assertEqual(fleet["editors"][0]["verified_version"], "3.0.3")

    def test_partial_batch_rejection_does_not_block_valid_events(self) -> None:
        res = self._post_gzip(
            self.key1,
            {
                "events": [
                    {"event_id": "ev_bad_1", "payload": {}},  # missing event_type -> rejected
                    {"event_id": "ev_good_1", "event_type": "heartbeat", "payload": {"pending_outbox": 0}},
                ]
            },
        )
        self.assertEqual(res.status_code, 200)
        data = res.json()
        self.assertEqual(data["rejected"], 1)
        self.assertEqual(data["accepted"], 1)

    def test_transcribe_header_linking_helper(self) -> None:
        ks = self.service.store()
        ks.ingest_track_event(1, "Madhu Sudhan", "madhusudhan@gosupernova.live", {
            "event_id": "ev_tr_open", "event_type": "job_open", "job_key": "vj_tr_1",
            "payload": {"notion_name": "W48 - Transcribe Link", "clip_hashes": ["h1"]},
        })
        ks.record_transcribe_for_job(
            1, "Madhu Sudhan", "madhusudhan@gosupernova.live",
            "vj_tr_1", "h1", clip_name="S1", is_retry=True, status="ok",
            in_tokens=500, out_tokens=100, audio_seconds=8.5, version="3.0.3", platform="Darwin arm64",
        )
        det = self.client.get("/v2/track/jobs/vj_tr_1", headers=self.admin_hdr).json()["job"]
        self.assertEqual(det["unique_clips_count"], 1)
        self.assertEqual(det["gemini_retry_calls"], 1)
        self.assertEqual(det["gemini_retry_in_tokens"], 500)

    def test_chunked_long_clips_keep_unique_clip_count_and_detect_silent_chunk_retry(self) -> None:
        now = time.time()
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_chk_open",
                        "event_type": "job_open",
                        "job_key": "vj_chunk_1",
                        "ts": now - 40,
                        "payload": {
                            "notion_name": "W48 - Long Clip Chunks",
                            "clip_hashes": ["raw_1", "raw_2"],
                        },
                    },
                    *[
                        {
                            "event_id": f"ev_chk_{i}",
                            "event_type": "gemini_call",
                            "job_key": "vj_chunk_1",
                            "ts": now - 30 + i,
                            "payload": {
                                "clip_hash": f"chunk_h{i}",
                                "is_retry": False,
                                "in_tokens": 400,
                                "out_tokens": 80,
                                "audio_seconds": 15.0,
                            },
                        }
                        for i in range(1, 5)
                    ],
                ]
            },
        )
        det1 = self.client.get("/v2/track/jobs/vj_chunk_1", headers=self.admin_hdr).json()["job"]
        self.assertEqual(det1["unique_clips_count"], 2)
        self.assertEqual(det1["gemini_calls"], 4)
        self.assertEqual(det1["gemini_retry_calls"], 0)
        self.assertEqual(det1["gemini_retry_in_tokens"], 0)

        # Silent duplicate chunk retry (without explicit is_retry=True) must increment retry stats, not unique clips
        self._post_gzip(
            self.key1,
            {
                "events": [
                    {
                        "event_id": "ev_chk_dup",
                        "event_type": "gemini_call",
                        "job_key": "vj_chunk_1",
                        "ts": now - 5,
                        "payload": {
                            "clip_hash": "chunk_h2",
                            "is_retry": False,
                            "in_tokens": 400,
                            "out_tokens": 80,
                            "audio_seconds": 15.0,
                        },
                    }
                ]
            },
        )
        det2 = self.client.get("/v2/track/jobs/vj_chunk_1", headers=self.admin_hdr).json()["job"]
        self.assertEqual(det2["unique_clips_count"], 2)
        self.assertEqual(det2["gemini_calls"], 5)
        self.assertEqual(det2["gemini_retry_calls"], 1)
        self.assertEqual(det2["gemini_retry_in_tokens"], 400)


class ClientTrackerOutboxTests(unittest.TestCase):
    def test_outbox_gzip_flush_reconcile_and_fail_open(self) -> None:
        received_batches = []

        class _MockHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, fmt, *args):
                pass

            def do_POST(self):
                raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                if (self.headers.get("Content-Encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                body = json.loads(raw.decode("utf-8"))
                received_batches.append(body)
                results = []
                for ev in body.get("events") or []:
                    if ev.get("event_type") == "bad_event":
                        results.append({"event_id": ev["event_id"], "status": "rejected", "reason": "bad"})
                    else:
                        results.append({"event_id": ev["event_id"], "status": "accepted"})
                out = json.dumps({"ok": True, "results": results}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = http.server.HTTPServer(("127.0.0.1", 0), _MockHandler)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                db = root / "_tracker_queue.db"
                proj = root / "work" / "proj_one"
                proj.mkdir(parents=True)
                dl_dir = root / "Downloads" / "Zeno Outs"
                vid_dir = dl_dir / "W48 - Malayalam - Demo"
                vid_dir.mkdir(parents=True)
                (vid_dir / "reel_W48 - Malayalam - Demo.mp4").write_bytes(b"mp4")
                (vid_dir / "W48 - Malayalam - Demo.plan.edl").write_text("#script https://docs.google.com/document/d/D1/edit\n", encoding="utf-8")

                with patch.dict(os.environ, {"ZENO_MACHINE_ID": "m_test_client"}):
                    jkey = zeno_tracker.open_job(proj, notion_name="W48 - Demo", script_source="D1", db_path=db)
                    self.assertTrue(jkey.startswith("vj_"))
                    zeno_tracker.enqueue_event("bad_event", job_key=jkey, db_path=db)
                    zeno_tracker.record_delivered(proj, proj / "plan.edl", {"output": "reel_W48 - Demo.mp4"}, folder=vid_dir, files=["reel_W48 - Demo.mp4"], db_path=db)
                    added = zeno_tracker.reconcile_recent_deliveries(max_age_hours=24, delivery_dir=dl_dir, db_path=db)
                    self.assertEqual(added, 0)  # already marked delivered by record_delivered!

                    # Add a second manual folder not sent via record_delivered -> reconciled once
                    vid2 = dl_dir / "W48 - Manual Render"
                    vid2.mkdir(parents=True)
                    (vid2 / "reel_W48 - Manual Render.mp4").write_bytes(b"mp4")
                    self.assertEqual(zeno_tracker.reconcile_recent_deliveries(max_age_hours=24, delivery_dir=dl_dir, db_path=db), 1)
                    self.assertEqual(zeno_tracker.reconcile_recent_deliveries(max_age_hours=24, delivery_dir=dl_dir, db_path=db), 0)

                    stats = zeno_tracker.flush(cloud_url=f"http://127.0.0.1:{srv.server_port}", token="zeno_test", db_path=db)
                    self.assertEqual(stats["accepted"], 3)
                    self.assertEqual(stats["rejected"], 1)
                    self.assertEqual(stats["remaining"], 0)
                    self.assertTrue(len(received_batches) == 1)

                # Fail-open when server unreachable (never raises)
                zeno_tracker.enqueue_event("heartbeat", db_path=db)
                bad_stats = zeno_tracker.flush(cloud_url="http://127.0.0.1:1", token="zeno_test", timeout=0.4, db_path=db)
                self.assertEqual(bad_stats["sent"], 0)
                self.assertEqual(bad_stats["remaining"], 1)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_cleanup_after_update_does_not_fire_receipt_on_test_root(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "work").mkdir()
            with patch.object(zeno_tracker, "record_update_receipt") as mock_rec:
                clean.cleanup_after_update(root=root, version="3.0.3", prev_version="3.0.2", home_dir=root / "home")
                mock_rec.assert_not_called()


if __name__ == "__main__":
    unittest.main()
