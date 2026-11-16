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


if __name__ == "__main__":
    unittest.main()
