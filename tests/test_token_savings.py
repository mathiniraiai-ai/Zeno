"""Regression checks for accounting isolation, cost estimates and compact execution."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import codex_usage
import token_cost
import token_tracker
import compact_run
import edit_brief

ROOT = Path(__file__).resolve().parents[1]


def event(second, total, cache, out, model="gpt-6.1-sol"):
    return {"type": "event_msg", "timestamp": f"2026-11-16T10:00:{second:02d}Z",
            "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": total, "cached_input_tokens": cache, "output_tokens": out},
                "last_token_usage": {"input_tokens": 1000, "cached_input_tokens": 800, "output_tokens": 10}}}}


def fixture(root=ROOT):
    return [{"type": "session_meta", "payload": {"id": "test-session", "cwd": str(root)}},
            {"type": "turn_context", "payload": {"model": "gpt-6.1-sol"}},
            event(0, 1000, 800, 10), event(10, 2000, 1600, 20), event(10, 2000, 1600, 20),
            event(20, 3000, 2400, 30), event(50, 4000, 3200, 40)]


class TestCodexAccounting(unittest.TestCase):
    def usage(self):
        return codex_usage.window_usage(fixture(), codex_usage.epoch("2026-11-16T10:00:05Z"),
                                        codex_usage.epoch("2026-11-16T10:00:30Z"), ROOT)

    def test_window_uses_deltas_and_deduplicates(self):
        got = self.usage()
        self.assertEqual((got["input_tokens"], got["cached_tokens"], got["output_tokens"]), (400, 1600, 20))
        self.assertEqual(got["total_tokens_including_cache"], 2020)
        self.assertEqual(got["steps_count"], 2)

    def test_foreign_workspace_excluded(self):
        self.assertIsNone(codex_usage.window_usage(fixture(ROOT.parent / "Zeno-other"), 0, float("inf"), ROOT))

    def test_missing_explicit_session_never_uses_another(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "rollout-test-session.jsonl"
            p.write_text("\n".join(json.dumps(x) for x in fixture()))
            self.assertIsNone(codex_usage.detect(0, root=ROOT, session_id="absent", sessions=Path(tmp)))

    def test_partial_line_and_private_content_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "session.jsonl"
            p.write_text("\n".join(json.dumps(x) for x in fixture()) +
                         '\n{"type":"response_item","payload":{"type":"reasoning","text":"secret"}}\n{"type":')
            self.assertEqual(len(list(codex_usage.records(p))), len(fixture()))

    def test_standard_cost(self):
        with patch.dict(os.environ, {"ZENO_COST_SPEED": "standard", "ZENO_USD_INR": "94.1159"}):
            cost = token_cost.estimate(self.usage())
        self.assertAlmostEqual(cost["usd"], .00116)
        self.assertAlmostEqual(cost["inr"], .00116 * 94.1159)

    def test_unknown_model_has_no_guessed_price(self):
        usage = self.usage()
        usage["requests"][0]["model"] = "unpriced-model"
        self.assertFalse(token_cost.estimate(usage)["available"])

    def test_long_context_and_fast_rates(self):
        usage = self.usage()
        for request in usage["requests"]:
            request["context"] = 300000
        with patch.dict(os.environ, {"ZENO_COST_SPEED": "fast"}):
            self.assertAlmostEqual(token_cost.estimate(usage)["usd"], .00444)

    def test_real_job_cost_fixture(self):
        usage = {"token_precision": "exact", "requests": [{
            "model": "gpt-6.1-sol", "input_tokens": 3041089, "cached_input_tokens": 2925824,
            "cache_write_input_tokens": 0, "output_tokens": 5132, "context": 121224}]}
        with patch.dict(os.environ, {"ZENO_COST_SPEED": "standard", "ZENO_USD_INR": "94.1159"}):
            self.assertAlmostEqual(token_cost.estimate(usage)["inr"], 54.06322231516)


class TestLeanWorkflow(unittest.TestCase):
    def test_page_brief_is_small_and_contains_safety_rules(self):
        brief = edit_brief.build({}, {"page": {"mode": "scroll"}})
        self.assertLess(len(brief), 7200)
        for phrase in ("sp=", "0.5", "peer=", "14%", "z=1", "word", "check-plan"):
            self.assertIn(phrase, brief)
        self.assertNotIn("## Zoom", brief)

    def test_entry_rules_stay_identical_and_bounded(self):
        self.assertEqual((ROOT / "AGENTS.md").read_bytes(), (ROOT / "CLAUDE.md").read_bytes())
        self.assertLess((ROOT / "AGENTS.md").stat().st_size, 5500)

    def test_direct_brief_keeps_requested_style_without_duplicate_generic_rules(self):
        import direct_mode
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project = root / "work" / "demo"
            project.mkdir(parents=True)
            (project / "plan.edl").write_text("#style test-style\n")
            (project / "brief.md").write_text("Selected job rules")
            style = root / "styles" / "test-style"
            style.mkdir(parents=True)
            (style / "SKILL.md").write_text("Requested style rules")
            with patch.object(direct_mode, "ROOT", root), patch.object(direct_mode, "rules_text") as fallback:
                system, _, _ = direct_mode.build_brief(project)
                self.assertEqual(system.count("Selected job rules"), 1)
                self.assertIn("Requested style rules", system)
                fallback.assert_not_called()
                # A generated heading/summary is not the actual style skill.
                (project / "brief.md").write_text("# STYLE: test-style\nShort summary only")
                system, _, _ = direct_mode.build_brief(project)
                self.assertIn("Requested style rules", system)
                (project / "brief.md").write_text("# STYLE: test-style\nRequested style rules")
                system, _, _ = direct_mode.build_brief(project)
                self.assertEqual(system.count("Requested style rules"), 1)

    def test_compact_success_is_bounded_and_log_complete(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = compact_run.run([sys.executable, "-c",
                                  "print('noise\\n'*1000); print('[PLAN PASS] good')"], Path(tmp))
            log = next(Path(tmp).glob("*.log")).read_text()
        self.assertEqual(rc, 0)
        self.assertIn("[PLAN PASS]", output.getvalue())
        self.assertLess(len(output.getvalue()), 600)
        self.assertGreater(len(log), 5000)

    def test_compact_failure_preserves_exit_and_reason(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()) as output:
            rc = compact_run.run([sys.executable, "-c", "print('specific failure'); raise SystemExit(2)"], Path(tmp))
        self.assertEqual(rc, 2)
        self.assertIn("specific failure", output.getvalue())

    def test_legacy_cached_usage_is_unknown_without_guessing(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "gemini_transcript.json").write_text(json.dumps({"words": ["hello"], "usage": {}}))
            row = token_tracker.collect_clip_telemetry([{"stem": "clip", "work_dir": p, "hq_words": ["hello"]}], [])[0]
        self.assertEqual(row["total_tokens"], 0)
        self.assertIsNone(row["transcription_reused"])


class TestReportIntegration(unittest.TestCase):
    def test_final_plan_and_retry_and_saved_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            work = root / "work"
            project = work / "example"
            project.mkdir(parents=True)
            (root / "README.md").write_text("# README\n")
            plan_path = project / "plan.edl"
            plan_path.write_text("#canvas 1080x1920@24\n")
            source = str(root / "clip.mp4")
            snapshot = {
                "language": "kn", "job_start_epoch": 100, "raw_seconds": 10,
                "clips": [{"clip": "clip", "src": source, "raw_seconds": 10,
                           "kept_seconds": 0, "words_count": 2, "script_lines": "-",
                           "segments": "dropped", "anomalies": [], "transcribe_model": "gemini",
                           "input_tokens": 100, "output_tokens": 20, "total_tokens": 120,
                           "transcription_reused": True}],
            }
            (project / "token_telemetry.json").write_text(json.dumps(snapshot))
            plan = {"segments": [{"id": "L1", "src": source, "start": 1, "end": 4}], "marks": []}
            agent = {"agent_name": "Codex", "agent_model": "gpt-6.1-sol", "session_id": "test",
                     "input_tokens": 100, "cached_tokens": 900, "output_tokens": 10,
                     "total_tokens": 110, "token_precision": "exact"}
            from contextlib import ExitStack
            import sqlite3
            with ExitStack() as stack:
                for attr, value in (("ROOT", root), ("WORK", work), ("LOCAL_DB", work / "db.sqlite"),
                                    ("LEDGER_MD", root / "TOKEN_LEDGER.md")):
                    stack.enter_context(patch.object(token_tracker, attr, value))
                stack.enter_context(patch.object(token_tracker, "detect_agent_usage", return_value=agent))
                stack.enter_context(patch("auth_session.get_logged_in_email", return_value="test@example.invalid"))
                stack.enter_context(patch("job_report.final_report", return_value={}))
                first = token_tracker.finalize_job_telemetry(plan_path, plan, 3, root / "reel.mp4", sync_cloud=False)
                again = token_tracker.finalize_job_telemetry(plan_path, plan, 3, root / "reel.mp4", sync_cloud=False)
                self.assertEqual(again["clips"][0]["kept_seconds"], 3)
                self.assertEqual(again["clips"][0]["script_lines"], "L1")
                self.assertEqual(again["transcribe_new_total_tokens"], 0)
                self.assertEqual(again["transcribe_reused_total_tokens"], 120)
                self.assertEqual(again["grand_total_tokens"], 1010)
                self.assertEqual(first["job_end_epoch"], again["job_end_epoch"])
                rerender = token_tracker.finalize_job_telemetry(plan_path, plan, 3, root / "reel.mp4",
                                                                sync_cloud=False, completed_at=again["job_end_epoch"] + 60)
                self.assertEqual(rerender["job_end_epoch"], again["job_end_epoch"] + 60)
                self.assertEqual(rerender["overall_in_tokens"] + rerender["overall_out_tokens"], 1010)
                with sqlite3.connect(work / "db.sqlite") as db:
                    self.assertEqual(db.execute("SELECT count(*) FROM job_runs").fetchone()[0], 1)
                    self.assertEqual(db.execute("SELECT count(*) FROM clip_runs").fetchone()[0], 1)
                    stored = json.loads(db.execute("SELECT details_json FROM job_runs").fetchone()[0])
                    self.assertEqual(stored["agent_total_including_cache"], 1010)
                self.assertEqual((root / "README.md").read_text().count("zeno-output:"), 1)
                self.assertEqual((root / "TOKEN_LEDGER.md").read_text().count("zeno-output:"), 1)
                with patch.object(sys, "argv", ["token_tracker.py", "--project", "example"]), \
                     patch.object(token_tracker, "finalize_job_telemetry") as finalize, \
                     contextlib.redirect_stdout(io.StringIO()):
                    token_tracker.main()
                    finalize.assert_not_called()

    def test_launcher_changes_only_session_flags(self):
        import launch_edit
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.toml"
            text = '[mcp_servers.example]\ncommand="private"\n[apps.saved]\nenabled=true\n[plugins."x@y"]\nenabled=true\n'
            p.write_text(text)
            flags = launch_edit.codex_overrides([p])
            self.assertIn('mcp_servers.example.enabled=false', flags)
            self.assertIn('apps.saved.enabled=false', flags)
            self.assertIn('plugins.x@y.enabled=false', flags)
            self.assertNotIn("private", " ".join(flags))
            self.assertEqual(p.read_text(), text)

    def test_job_windows_are_isolated_by_chat_and_video(self):
        import common
        with tempfile.TemporaryDirectory() as tmp, patch.object(common, "JOB_MARKER", Path(tmp) / "marker.json"):
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "chat-a"}), patch("time.time", return_value=1000):
                self.assertEqual(common.job_start_epoch(), 1000)
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "chat-b"}), patch("time.time", return_value=1010):
                self.assertEqual(common.job_begin(identity="video-b"), 1010)
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "chat-a"}), patch("time.time", return_value=1020):
                self.assertEqual(common.job_begin(identity="video-a"), 1000)
                self.assertEqual(common.job_begin(identity="video-a"), 1000)
                self.assertEqual(common.job_begin(identity="video-c"), 1020)
                common.job_done()
            with patch.dict(os.environ, {"CODEX_THREAD_ID": "chat-b"}), patch("time.time", return_value=1030):
                self.assertEqual(common.job_start_epoch(), 1010)


class TestV1018TokenArrestAndNotionQueue(unittest.TestCase):
    def test_100k_intake_limit_and_notion_meta_preserved_in_launcher(self):
        import cost_control, launch_edit, tomllib
        self.assertEqual(cost_control.settings()["new_chat_context_limit"], 5_000_000)
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "config.toml"
            p.write_text(
                '[mcp_servers.notion]\ncommand="npx"\n'
                '[mcp_servers.meta_ads]\ncommand="npx"\n'
                '[mcp_servers.hostinger-vps]\ncommand="npx"\n'
                '[plugins."figma@openai"]\nenabled=true\n'
            )
            flags = " ".join(launch_edit.codex_overrides([p]))
            self.assertNotIn("mcp_servers.notion.enabled=false", flags)
            self.assertNotIn("mcp_servers.meta_ads.enabled=false", flags)
            self.assertIn("mcp_servers.hostinger-vps.enabled=false", flags)
            self.assertIn("plugins.figma@openai.enabled=false", flags)
        repo_codex = ROOT / ".codex" / "config.toml"
        self.assertTrue(repo_codex.is_file())
        parsed_codex = tomllib.loads(repo_codex.read_text(encoding="utf-8"))
        for k in parsed_codex.get("mcp_servers", {}):
            self.assertNotIn("notion", k.lower())
            self.assertNotIn("meta", k.lower())

    def test_claude_and_codex_unified_pricing_and_session_routing(self):
        import cost_control
        claude_usage = {
            "token_precision": "exact",
            "requests": [{
                "model": "claude-sonnet-5-5",
                "input_tokens": 20_000,
                "cached_input_tokens": 0,
                "cache_write_input_tokens": 19_998,
                "ephemeral_1h_input_tokens": 19_998,
                "output_tokens": 3_000,
                "context": 20_000,
            }],
        }
        est = token_cost.estimate(claude_usage)
        self.assertTrue(est["available"])
        self.assertGreater(est["inr"], 0.0)
        # Non-Codex session_id in token_telemetry.json must NOT trap detect_agent_usage in a 0-step Codex stub
        with tempfile.TemporaryDirectory() as tmp:
            proj = Path(tmp)
            (proj / "token_telemetry.json").write_text(json.dumps({
                "job_start_epoch": 10.0,
                "agent_name": "Claude Code",
                "agent_session_id": "claude-uuid-1234",
            }))
            with patch("token_tracker.detect_agent_usage", return_value={
                "agent_name": "Claude Code", "agent_model": "claude-sonnet-5-5",
                "token_precision": "exact", "steps_count": 2,
                "requests": claude_usage["requests"],
            }) as det:
                snap = cost_control.snapshot(proj, until=20.0)
                self.assertIsNone(det.call_args.kwargs.get("session_id"))
                self.assertTrue(snap["available"])
                self.assertEqual(snap["requests"], 2)

    def test_split_breath_pause_threshold_and_compound_line_verify(self):
        import script_order, verify
        ws = [{"w": "hello", "start": 0.20, "end": 0.60}, {"w": "there", "start": 0.98, "end": 1.40}]  # 0.38s breath
        with patch.object(script_order, "_words_of", return_value=ws):
            # 0.38s natural breath in split_v is allowed (< 0.50s), whereas in single monologue (> 0.25s) it warns
            self.assertEqual(script_order.tight_report({"segments": [{"id": "L1", "src": "a.mp4", "start": 0.1, "end": 1.5, "layout": "split_v"}]}), [])
            self.assertEqual(len(script_order.tight_report({"segments": [{"id": "L1", "src": "a.mp4", "start": 0.1, "end": 1.5, "layout": "single"}]})), 1)
        lines = [{"id": "L9", "tokens": ["hello"]}, {"id": "L10", "tokens": ["there"]}]
        plan_c = {"segments": [{"id": "L9_L10", "src": "a.mp4", "start": 0.1, "end": 1.5, "note": ""}]}
        with patch.object(verify, "parse_script_file", return_value=({}, lines)), \
             patch.object(verify, "clip_work_dir", return_value=Path("/nonexistent")):
            ok, cov, tot, dups = verify.check_script_coverage(plan_c, script_path=Path(__file__))
        self.assertEqual((ok, cov, tot, dups), (True, 2, 2, 0))

    def test_gemini_cooldown_moves_429_and_404_models_behind_healthy_buckets(self):
        svc_dir = str(ROOT / "service")
        if svc_dir not in sys.path:
            sys.path.insert(0, svc_dir)
        import app as svc_app

        g = svc_app._Gemini()
        with patch.object(svc_app, "_gemini_keys", return_value=["k0"]), \
             patch.object(svc_app, "MODELS", ["m-429", "m-404", "m-ok"]):
            ttl_daily = g.mark_failure(0, "m-429", RuntimeError("429 RESOURCE_EXHAUSTED generate_content_free_tier_requests"))
            ttl_404 = g.mark_failure(0, "m-404", RuntimeError("404 NOT_FOUND"))
            self.assertEqual(ttl_daily, 300.0)
            self.assertEqual(ttl_404, 3600.0)
            self.assertEqual(g.candidate_models(), ["m-ok", "m-429", "m-404"])
            g.mark_ok(0, "m-429")
            self.assertEqual(g.candidate_models(), ["m-429", "m-ok", "m-404"])


class GeminiQuotaQueueAndSafetyTests(unittest.TestCase):
    def _import_service_app(self):
        svc_dir = str(ROOT / "service")
        if svc_dir not in sys.path:
            sys.path.insert(0, svc_dir)
        import app as svc_app
        return svc_app

    def test_fair_queue_bounds_concurrency_and_alternates_editors(self):
        import concurrent.futures
        import threading
        import time

        svc_app = self._import_service_app()
        g = svc_app._Gemini()
        active = 0
        max_active = 0
        start_order = []
        guard = threading.Lock()
        first_entered = threading.Event()
        release_first = threading.Event()

        def worker(who: str, idx: int, hold: bool = False):
            nonlocal active, max_active
            with patch.dict(os.environ, {"GEMINI_CONCURRENCY": "1", "GEMINI_QUEUE_TIMEOUT": "10"}):
                ok = g.acquire_slot(owner=who, timeout=10.0)
            self.assertTrue(ok)
            try:
                with guard:
                    active += 1
                    max_active = max(max_active, active)
                    start_order.append((who, idx))
                if hold:
                    first_entered.set()
                    release_first.wait(timeout=3.0)
                else:
                    time.sleep(0.01)
            finally:
                with guard:
                    active -= 1
                g.release_slot()

        with patch.dict(os.environ, {"GEMINI_CONCURRENCY": "1", "GEMINI_QUEUE_TIMEOUT": "10"}):
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                f0 = ex.submit(worker, "abhishek", 0, True)
                self.assertTrue(first_entered.wait(timeout=2.0))
                # Enqueue two more jobs for Abhishek while slot is held, then Balesh arrives
                fa1 = ex.submit(worker, "abhishek", 1, False)
                fa2 = ex.submit(worker, "abhishek", 2, False)
                # Poll briefly until abhishek's 2 waiters are registered in FIFO queue
                for _ in range(50):
                    with g._q_cond:
                        if len(g._q_waiters.get("abhishek", ())) >= 2:
                            break
                    time.sleep(0.01)
                fb1 = ex.submit(worker, "balesh", 1, False)
                for _ in range(50):
                    with g._q_cond:
                        if "balesh" in g._q_waiters:
                            break
                    time.sleep(0.01)
                release_first.set()
                for fut in (f0, fa1, fa2, fb1):
                    fut.result(timeout=5.0)

        self.assertEqual(max_active, 1)
        self.assertEqual(start_order[0], ("abhishek", 0))
        # Round-robin fairness: abhishek[1] gets next slot, then balesh[1] goes BEFORE abhishek[2]
        self.assertEqual(start_order[1:], [("abhishek", 1), ("balesh", 1), ("abhishek", 2)])

    def test_parse_retry_after_and_daily_quota_detection(self):
        svc_app = self._import_service_app()
        g = svc_app._Gemini()

        d1 = g._parse_retry_after(RuntimeError("429 RESOURCE_EXHAUSTED. Please retry in 1h57m12.5s."))
        self.assertIsNotNone(d1)
        self.assertAlmostEqual(d1, 7032.5, places=1)

        d2 = g._parse_retry_after(RuntimeError("details: [{'retryDelay': '27s'}]"))
        self.assertEqual(d2, 27.0)

        self.assertTrue(g._is_daily_quota(RuntimeError("429 RESOURCE_EXHAUSTED generate_content_free_tier_requests PerDay")))
        self.assertFalse(g._is_daily_quota(RuntimeError("429 RESOURCE_EXHAUSTED rate limit RPM exceeded")))

    def test_singleflight_deduplicates_concurrent_identical_uploads(self):
        import concurrent.futures
        import threading
        import time

        svc_app = self._import_service_app()
        calls = 0
        call_lock = threading.Lock()
        entered = threading.Event()

        def fake_transcribe(audio, prompt, duration, trace, language_hint="auto", owner="anon"):
            nonlocal calls
            with call_lock:
                calls += 1
            entered.set()
            time.sleep(0.08)
            return {
                "language": "en",
                "full_transcript": "hello world",
                "words": [{"w": "hello", "start": 0.1, "end": 0.4}],
                "cues": [],
                "model_used": "gemini-3.1-flash-lite",
                "usage": {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150},
            }

        with patch.object(svc_app._gemini, "transcribe", side_effect=fake_transcribe):
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
                f1 = ex.submit(svc_app._transcribe_singleflight, "sf_key_1", b"RIFF0000", ["hello"], "en", 1.5, [], "abhishek")
                self.assertTrue(entered.wait(timeout=2.0))
                f2 = ex.submit(svc_app._transcribe_singleflight, "sf_key_1", b"RIFF0000", ["hello"], "en", 1.5, [], "hitesh")
                r1 = f1.result(timeout=3.0)
                r2 = f2.result(timeout=3.0)

        self.assertEqual(calls, 1)
        self.assertEqual(r1.get("model_used"), "gemini-3.1-flash-lite")
        self.assertTrue(r2.get("cached"))
        self.assertEqual(r2["usage"]["total_tokens"], 0)

    def test_cloud_transcriber_fails_closed_after_retries_never_fabricates_cuts(self):
        import wave
        from unittest.mock import MagicMock
        from align_engine.gemini_transcriber import GeminiTranscriber

        with tempfile.TemporaryDirectory() as tmp:
            wav = Path(tmp) / "clip.wav"
            with wave.open(str(wav), "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(b"\x01\x00" * 16000)

            busy = MagicMock()
            busy.status_code = 429
            busy.headers = {"retry-after": "2"}
            busy.json.return_value = {"detail": "All Gemini models hit API quota limits right now. Wait and retry."}
            busy.raise_for_status.side_effect = RuntimeError("HTTP 429")

            with patch.dict(os.environ, {"ZENO_CLOUD_URL": "https://zeno.example.com", "ZENO_CLOUD_TOKEN": "tok"}):
                tr = GeminiTranscriber(api_keys=[])
            with patch("httpx.Client") as mock_client_cls, \
                 patch("time.sleep") as mock_sleep, \
                 patch("random.uniform", side_effect=lambda a, b: a):
                mock_http = mock_client_cls.return_value.__enter__.return_value
                mock_http.post.return_value = busy
                res = tr.transcribe(str(wav), "en", ["Test line one", "Test line two"], work_dir=Path(tmp))

            self.assertEqual(mock_http.post.call_count, 8)
            self.assertEqual(mock_sleep.call_count, 7)
            self.assertEqual(res.get("status"), "cloud_failed")
            self.assertEqual(res.get("model_used"), "cloud_error")
            self.assertEqual(res.get("words"), [])
            self.assertIn("429", str(res.get("error") or ""))

    def test_prepare_main_blocks_cleanly_on_transcript_error_without_plan_output(self):
        import wave
        import prepare

        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            clip_ok = tpath / "C1.mp4"
            clip_bad = tpath / "C2.mp4"
            clip_ok.write_bytes(b"fake")
            clip_bad.write_bytes(b"fake")
            script_f = tpath / "script.txt"
            script_f.write_text("Line one\nLine two\n", encoding="utf-8")
            work_root = tpath / "work" / "proj_block"

            def fake_ensure(clip_stem, resolved_src, work_dir, lang="auto", script_path=None, clean_raw_json=False):
                work_dir.mkdir(parents=True, exist_ok=True)
                wav = work_dir / "audio16k.wav"
                with wave.open(str(wav), "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(16000)
                    wf.writeframes(b"\x10\x00" * 16000)
                if clip_stem == "C1":
                    words = [{"w": "Line", "start": 0.1, "end": 0.4}, {"w": "one", "start": 0.4, "end": 0.7}]
                    (work_dir / "hq_words.json").write_text(json.dumps(words), encoding="utf-8")
                    return {"width": 1080, "height": 1920, "fps": 30}, words, words
                return {
                    "width": 1080,
                    "height": 1920,
                    "fps": 30,
                    "_transcript_error": "HTTP 429: All Gemini models hit API quota limits",
                }, [], []

            with patch.object(prepare, "WORK", tpath / "work"), \
                 patch.object(prepare, "clip_work_dir", side_effect=lambda fp: work_root / fp.stem), \
                 patch.object(prepare, "ensure_cache_current", return_value=False), \
                 patch.object(prepare, "ensure_clip_transcribed", side_effect=fake_ensure), \
                 patch.object(prepare, "compute_10ms_rms_envelope", return_value=[0.05] * 100), \
                 patch.object(prepare, "detect_two_tier_silences", return_value=([], [])), \
                 patch.object(prepare, "clip_has_no_sound", return_value=False), \
                 patch.object(sys, "argv", ["prepare.py", str(clip_ok), str(clip_bad), "--script", str(script_f), "--project", "proj_block"]):
                with self.assertRaises(SystemExit) as ctx:
                    prepare.main()

            self.assertEqual(ctx.exception.code, 2)
            # C1's good transcription remains cached on disk for seamless retry, while no broken plan was emitted
            self.assertTrue((work_root / "C1" / "hq_words.json").is_file())
            self.assertFalse((work_root / "plan.auto.edl").exists())
            self.assertFalse((work_root / "line_map.md").exists())


class CacheCleanupAndMcpIsolationTests(unittest.TestCase):
    def test_zeno_mcp_start_edit_uses_auto_plan_compact_and_hot_reloads_on_update(self):
        import zeno_mcp

        tools = zeno_mcp.handle({"jsonrpc": "2.0", "id": 10, "method": "tools/list"})["result"]["tools"]
        names = [t["name"] for t in tools]
        self.assertIn("zeno_auto_plan", names)

        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            raw_dir.mkdir()
            captured = {}

            def fake_run_tail(cmd, log_path, wait_s, max_chars=7000):
                captured["cmd"] = cmd
                captured["wait_s"] = wait_s
                return 0, "[PLAN PASS] Every line present.\nPitch Card here"

            with patch.object(zeno_mcp, "_run_with_log_tail", side_effect=fake_run_tail):
                res = zeno_mcp.call(
                    "zeno_start_edit",
                    {
                        "script": "https://docs.google.com/document/d/abc/edit?tab=t.0",
                        "footage": str(raw_dir),
                        "name": "W47 Test Video",
                    },
                )
            self.assertFalse(res["isError"])
            self.assertIn("--auto-plan", captured["cmd"])
            self.assertIn("--compact", captured["cmd"])
            self.assertIn("[ZENO DELIVERY]", res["content"][0]["text"])
            self.assertNotIn("read line_map.md, facts.md", res["content"][0]["text"])

    def test_cleanup_after_job_and_after_update_clear_transient_bloat_and_preserve_clips(self):
        import clean

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seg = root / "work" / "_segments"
            seg.mkdir(parents=True)
            (seg / "chunk1.mp4").write_bytes(b"a" * 5000)

            proj = root / "work" / "proj_a"
            (proj / "_sub_frames").mkdir(parents=True)
            (proj / "_sub_frames" / "f001.png").write_bytes(b"p" * 3000)
            (proj / "scratch.tmp").write_bytes(b"t" * 1000)
            (proj / "direct_call_pending.json").write_text("{}", encoding="utf-8")
            (proj / "plan.edl").write_text("#output out.mp4\n", encoding="utf-8")

            clip_dir = root / "work" / "_clips" / "C1"
            clip_dir.mkdir(parents=True)
            (clip_dir / "hq_words.json").write_text("[{\"w\":\"hi\"}]", encoding="utf-8")

            r_job = clean.cleanup_after_job(root, project_dir=proj)
            self.assertTrue(r_job["ran"])
            self.assertFalse((seg / "chunk1.mp4").exists())
            self.assertFalse((proj / "_sub_frames").exists())
            self.assertFalse((proj / "scratch.tmp").exists())
            self.assertFalse((proj / "direct_call_pending.json").exists())
            self.assertTrue((proj / "plan.edl").is_file())
            self.assertTrue((clip_dir / "hq_words.json").is_file())

            # Test cleanup_after_update
            pyc_dir = root / "scripts" / "__pycache__"
            pyc_dir.mkdir(parents=True)
            (pyc_dir / "old.cpython-312.pyc").write_bytes(b"c" * 2000)
            ota_dir = root / "work" / "_ota_stage_999"
            ota_dir.mkdir(parents=True)
            (ota_dir / "file.py").write_bytes(b"x" * 1000)
            (root / "work" / "_update_check.json").write_text("{}", encoding="utf-8")

            r_upd = clean.cleanup_after_update(root, version="1.0.21")
            self.assertTrue(r_upd["ran"])
            self.assertFalse(pyc_dir.exists())
            self.assertFalse(ota_dir.exists())
            self.assertFalse((root / "work" / "_update_check.json").exists())
            self.assertTrue((root / "work" / ".last_cleanup_state.json").is_file())
            self.assertTrue((clip_dir / "hq_words.json").is_file())

    def test_prepare_common_job_start_resets_epoch_on_different_project(self):
        import time
        import common
        import prepare

        with tempfile.TemporaryDirectory() as tmp:
            marker_f = Path(tmp) / "job_marker.json"
            old_t = time.time() - 3600.0
            marker_f.write_text(json.dumps({"start": old_t, "identity": "video_one"}), encoding="utf-8")
            with patch.object(common, "_job_marker_path", return_value=marker_f):
                t_same = prepare.common_job_start("video_one")
                # Older than 30 min -> resets even for same identity; test within 10 min too:
                recent_t = time.time() - 600.0
                marker_f.write_text(json.dumps({"start": recent_t, "identity": "video_one"}), encoding="utf-8")
                t_reuse = prepare.common_job_start("video_one")
                self.assertAlmostEqual(t_reuse, recent_t, places=1)
                t_diff = prepare.common_job_start("video_two")
                self.assertGreater(t_diff, recent_t + 300.0)

    def test_keystore_deduplicates_rerender_checkpoints_of_same_session(self):
        svc_dir = str(ROOT / "service")
        if svc_dir not in sys.path:
            sys.path.insert(0, svc_dir)
        import keystore

        with tempfile.TemporaryDirectory() as tmp:
            ks = keystore.KeyStore(sqlite_path=str(Path(tmp) / "t.db"))
            raw_key = ks.create("Balesh P", "balesh@gosupernova.live")
            kid, _ = ks.lookup(raw_key)

            for turns, cached in ((25, 10_975_124), (40, 18_035_994), (63, 29_658_075)):
                ks.log_run(
                    kid,
                    {
                        "project": "gdoc_1i0Ppu",
                        "language": "te",
                        "agent_name": "Claude Code",
                        "agent_model": "claude-sonnet-5-5",
                        "clips_count": 3,
                        "video_seconds": 86.4,
                        "agent_in_tokens": 50_000,
                        "agent_cached_tokens": cached,
                        "agent_out_tokens": 20_000,
                        "agent_total_tokens": 70_000,
                        "grand_total_tokens": cached + 70_000,
                        "job_report": {"agent": {"turns": turns}, "accounting": {"agent_session_id": "sess-033a8d34"}},
                    },
                    editor_name="Balesh P",
                )
            runs = ks.usage_runs()
            self.assertEqual(len(runs), 1)
            self.assertEqual(runs[0]["agent_cached_tokens"], 29_658_075)

            # A brand-new session on the same project records a distinct second run
            ks.log_run(
                kid,
                {
                    "project": "gdoc_1i0Ppu",
                    "language": "te",
                    "agent_name": "Claude Code",
                    "agent_model": "claude-sonnet-5-5",
                    "clips_count": 3,
                    "video_seconds": 86.4,
                    "agent_in_tokens": 18_000,
                    "agent_cached_tokens": 12_000,
                    "agent_out_tokens": 4_000,
                    "agent_total_tokens": 22_000,
                    "grand_total_tokens": 34_000,
                    "job_report": {"agent": {"turns": 1}, "accounting": {"agent_session_id": "sess-fresh-1call"}},
                },
                editor_name="Balesh P",
            )
            self.assertEqual(len(ks.usage_runs()), 2)

    def test_run_auto_plan_chains_auto_render_by_default_and_honors_plan_only(self):
        import direct_mode
        import zeno

        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proj_auto"
            pdir.mkdir()
            plan_p = pdir / "plan.edl"
            plan_p.write_text("#output out.mp4\n#script s.txt\nL1 C1.mp4 0.00-1.00 sp=\"A\"\n", encoding="utf-8")

            fake_res = {"passed": True, "model": "sonnet", "calls": [{}], "tokens": {"total": 24000}, "seconds": 12.0, "notes": "clean"}
            with patch.object(direct_mode, "resolve_provider", return_value="claude"), \
                 patch.object(direct_mode, "run", return_value=fake_res), \
                 patch.object(zeno, "build_editorial_pitch_card", return_value="PITCH CARD OK"), \
                 patch.object(zeno, "session_gate"), \
                 patch("cost_control.print_checkpoint"), \
                 patch.object(zeno, "_execute_and_finalize", return_value=0) as mock_exec:
                rc_auto = zeno.run_auto_plan(plan_p, planner="claude")
                self.assertEqual(rc_auto, 0)
                self.assertEqual(mock_exec.call_count, 1)

                mock_exec.reset_mock()
                rc_plan_only = zeno.run_auto_plan(plan_p, planner="claude", auto_render=False)
                self.assertEqual(rc_plan_only, 0)
                self.assertEqual(mock_exec.call_count, 0)


class OptionBBatchedWorkflowTests(unittest.TestCase):
    def test_edit_brief_includes_syntax_card_and_keeps_page_brief_compact(self):
        import edit_brief

        page_brief = edit_brief.build({}, {"page": {"mode": "scroll"}})
        self.assertIn("[DO NOT READ DURING EDIT]", page_brief)
        self.assertLess(len(page_brief.encode("utf-8")), 7200)
        self.assertNotIn("# EDL & Visual Syntax Quick Card", page_brief)
        self.assertNotIn("## Zoom", page_brief)

        full_brief = edit_brief.build({}, {})
        self.assertIn("[DO NOT READ DURING EDIT]", full_brief)
        self.assertIn("# EDL & Visual Syntax Quick Card", full_brief)
        self.assertIn("mark:wrong@t", full_brief)
        self.assertIn("#subs auto y=68", full_brief)

    def test_edit_packet_inventory_chunking_and_stale_cleanup(self):
        import edit_packet

        with tempfile.TemporaryDirectory() as tmp:
            tpath = Path(tmp)
            src_dir = tpath / "raw"
            src_dir.mkdir()
            (src_dir / "music.mp3").write_bytes(b"mp3data")

            proj = tpath / "work" / "proj"
            proj.mkdir(parents=True)
            clips_root = tpath / "work" / "_clips"
            (clips_root / "C1").mkdir(parents=True)
            (clips_root / "C1" / "info.json").write_text(
                json.dumps({"width": 1080, "height": 1920, "duration": 4.2, "path": str(src_dir / "C1.mp4")}),
                encoding="utf-8",
            )
            (clips_root / "C1" / "speech_spans.json").write_text("[]", encoding="utf-8")
            (proj / "clip_caches.json").write_text(
                json.dumps({"C1.mp4": "C1"}),
                encoding="utf-8",
            )
            (proj / "brief.md").write_text("## Rules\nsplits z=1\n", encoding="utf-8")
            (proj / "plan.edl").write_text(
                f"#output out.mp4\n#src_dir {src_dir}\nL1 C1.mp4 0.00-1.00 sp=\"A\"\n",
                encoding="utf-8",
            )
            (proj / "line_map.md").write_text(
                "L1 | Hello\n  CAND C1.mp4 0.00-1.00\n\nL2 | Missing line\n  MISSING: no match in footage\n",
                encoding="utf-8",
            )
            # Exceed _CHUNK_TRIGGER_BYTES so write() emits parallel editing_packet_p*.md parts.
            big_words = "\n".join(f"  {i:04d}.00 word{i} (0.20s)" for i in range(1800))
            (proj / "facts.md").write_text("## C1.mp4 (1080x1920, 4.20s)\n" + big_words + "\n", encoding="utf-8")

            out_path = edit_packet.write(proj)
            full_text = out_path.read_text(encoding="utf-8")
            self.assertIn("# Batch Playbook", full_text)
            self.assertIn("--execute", full_text)
            self.assertIn("## Raw Footage Inventory & Diagnostics", full_text)
            self.assertIn("music.mp3", full_text)
            self.assertIn("Unmatched/missing lines (0 candidates): L2", full_text)
            self.assertIn("SILENT/zero words", full_text)

            parts = edit_packet.packet_parts(proj)
            self.assertGreater(len(parts), 1)
            for p in parts:
                txt = p.read_text(encoding="utf-8")
                self.assertLessEqual(len(txt.encode("utf-8")), edit_packet.CHUNK_MAX_BYTES)
                self.assertLessEqual(len(txt.splitlines()), edit_packet.CHUNK_MAX_LINES)

            manifest = json.loads((proj / "editing_packet.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["parts"], [p.name for p in parts])

            # Shrinking under chunk trigger removes stale editing_packet_p*.md files.
            (proj / "facts.md").write_text("## C1.mp4 (1080x1920, 4.20s)\n  0.10 Hello\n", encoding="utf-8")
            edit_packet.write(proj)
            self.assertEqual(list(proj.glob("editing_packet_p*.md")), [])
            self.assertEqual(edit_packet.packet_parts(proj), [proj / "editing_packet.md"])

    def test_a_planner_that_used_its_calls_stops_and_does_not_hand_the_plan_to_the_chat(self):
        import contextlib
        import io
        import os
        from unittest.mock import patch
        import compact_run
        import zeno

        with tempfile.TemporaryDirectory() as tmp:
            plan_p = Path(tmp) / "proj_stop" / "plan.edl"
            plan_p.parent.mkdir()
            plan_p.write_text("#output out.mp4\n", encoding="utf-8")
            buf = io.StringIO()
            with patch.dict(os.environ, {"ZENO_MANUAL_FALLBACK": ""}), contextlib.redirect_stdout(buf):
                zeno.print_planner_stop(plan_p, "plan check did not pass", 2)
            out = buf.getvalue()
            self.assertIn("[PLANNER STOPPED]", out)
            self.assertIn("2 completed model call(s)", out)
            self.assertNotIn("[MANUAL_FALLBACK_PACK]", out)
            self.assertNotIn("[TURN 2]", out)
            self.assertTrue(compact_run.IMPORTANT.match("[PLANNER STOPPED] The planner used 2"))
            buf = io.StringIO()
            with patch.dict(os.environ, {"ZENO_MANUAL_FALLBACK": "1"}), contextlib.redirect_stdout(buf):
                zeno.print_planner_stop(plan_p, "plan check did not pass", 2)
            self.assertNotIn("[MANUAL_FALLBACK_PACK]", buf.getvalue())
            self.assertIn("[PLANNER STOPPED]", buf.getvalue())  # legacy switch cannot bypass the durable budget

    def test_zeno_manual_fallback_pack_and_compact_regex(self):
        import contextlib
        import io
        import compact_run
        import zeno

        with tempfile.TemporaryDirectory() as tmp:
            pdir = Path(tmp) / "proj_fb"
            pdir.mkdir()
            for i in (1, 2, 10):
                (pdir / f"editing_packet_p{i}.md").write_text("part\n", encoding="utf-8")
            (pdir / "clip_sheet_01.jpg").write_bytes(b"jpg")
            plan_p = pdir / "plan.edl"
            plan_p.write_text("#output out.mp4\n", encoding="utf-8")

            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                zeno.print_next_step(plan_p, fallback_reason="Not logged in")
            out = buf.getvalue()
            self.assertIn("[MANUAL_FALLBACK_PACK]", out)
            self.assertIn("[BATCH PLAYBOOK]", out)
            self.assertIn("editing_packet_p1.md", out)
            self.assertLess(out.index("editing_packet_p2.md"), out.index("editing_packet_p10.md"))
            self.assertIn("clip_sheet_01.jpg", out)
            self.assertIn("--execute", out)
            self.assertIn("[DO NOT]", out)
            self.assertIn("NEVER poll", out)

            for tag in ("[PACKET_PARTS] p1 p2", "[MANUAL_FALLBACK_PACK] fallback", "[BATCH PLAYBOOK] ok", "[TURN 2] read", "[DO NOT] stop"):
                self.assertIsNotNone(compact_run.IMPORTANT.search(tag), f"compact_run missed: {tag}")

    def test_antigravity_turns_and_100k_intake_limit(self):
        import agent_log
        import cost_control

        lines = [
            json.dumps({"type": "USER_INPUT", "created_at": "2026-11-17T10:00:01Z", "content": "edit"}),
            json.dumps({"type": "PLANNER_RESPONSE", "step_index": 2, "created_at": "2026-11-17T10:00:05Z", "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 100}),
            json.dumps({"type": "PLANNER_RESPONSE", "step_index": 2, "created_at": "2026-11-17T10:00:06Z", "input_tokens": 1000, "cache_read_tokens": 500, "output_tokens": 120}),
            json.dumps({"type": "PLANNER_RESPONSE", "step_index": 4, "created_at": "2026-11-17T10:00:12Z", "input_tokens": 1200, "cache_read_tokens": 800, "output_tokens": 150}),
            "{bad-json",
            json.dumps({"type": "PLANNER_RESPONSE", "step_index": 9, "created_at": "2026-11-17T10:05:00Z", "input_tokens": 900, "output_tokens": 50}),
        ]
        cutoff = agent_log._epoch("2026-11-17T10:00:00Z")
        ceiling = agent_log._epoch("2026-11-17T10:01:00Z")
        usage = agent_log.antigravity_window_usage(lines, cutoff, ceiling, session_id="agy-test")
        self.assertIsNotNone(usage)
        self.assertEqual(usage["turns"], 2)
        self.assertEqual(cost_control.settings()["new_chat_context_limit"], 5_000_000)


if __name__ == "__main__":
    unittest.main()
