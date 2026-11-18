"""Offline dispatch guard tests: persistence, concurrency and unknown billing."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cost_control
import request_budget as budget


class RequestBudgetTests(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.policy = dict(cost_control.DEFAULTS)
        self.mock = patch.object(budget, "_policy", return_value=self.policy)
        self.mock.start()
        self.addCleanup(self.mock.stop)

    def reserve(self, kind="targeted", size=100):
        return budget.reserve_call(self.project, provider="mock", context_kind=kind, input_bytes=size)

    def finish(self, call_id, **kwargs):
        budget.finish_call(self.project, call_id, usage_known=True,
                           usage={"input_tokens": 100, "output_tokens": 10}, **kwargs)

    def test_eight_total_across_invocations(self):
        for _ in range(8):
            self.finish(self.reserve())
        with self.assertRaisesRegex(budget.BudgetExceeded, "limit"):
            self.reserve()
        self.assertEqual(budget.status(self.project)["requests"], 8)

    def test_two_full_context_then_targeted_allowed(self):
        for _ in range(2):
            self.finish(self.reserve("full"))
        with self.assertRaisesRegex(budget.BudgetExceeded, "Full-context request limit"):
            self.reserve("full")
        self.finish(self.reserve())

    def test_full_aggregate_reserve_and_targeted_bound(self):
        self.finish(self.reserve("full", 50000))
        with self.assertRaisesRegex(budget.BudgetExceeded, "reserve"):
            self.reserve("full", 50000)
        with self.assertRaisesRegex(budget.BudgetExceeded, "targeted"):
            self.reserve(size=24001)
        self.assertEqual(budget.status(self.project)["requests"], 1)

    def test_image_allowance_counted(self):
        with self.assertRaisesRegex(budget.BudgetExceeded, "reserve"):
            budget.reserve_call(self.project, provider="mock", context_kind="full", input_bytes=1000, image_count=10)

    def test_targeted_images_cannot_bypass_full_context_budget(self):
        with self.assertRaisesRegex(budget.BudgetExceeded, "text-only"):
            budget.reserve_call(self.project, provider="mock", context_kind="targeted", input_bytes=100, image_count=1)
        self.assertEqual(budget.status(self.project)["requests"], 0)

    def test_pending_survives_restart_and_changed_inspect(self):
        call = self.reserve()
        (self.project / "token_telemetry.json").write_text('{"job_start_epoch": 100}')
        with self.assertRaisesRegex(budget.BudgetExceeded, "Unresolved"):
            self.reserve()
        budget.finish_call(self.project, call, usage_known=False)
        with self.assertRaisesRegex(budget.BudgetExceeded, "Unresolved"):
            self.reserve()
        self.finish(call)
        self.finish(self.reserve())

    def test_reinspect_does_not_reset_same_project_budget(self):
        (self.project / "token_telemetry.json").write_text('{"job_start_epoch": 1}')
        self.finish(self.reserve())
        self.assertEqual(budget.status(self.project)["requests"], 1)
        (self.project / "token_telemetry.json").write_text('{"job_start_epoch": 2}')
        self.assertEqual(budget.status(self.project)["requests"], 1)

    def test_known_flag_without_actual_counters_stays_unknown(self):
        call = self.reserve()
        budget.finish_call(self.project, call, usage_known=True, usage={"input_tokens": 10})
        self.assertTrue(budget.status(self.project)["blocked"])
        with self.assertRaisesRegex(budget.BudgetExceeded, "Unresolved"):
            self.reserve()

    def test_completion_merges_concurrent_journal_import(self):
        call = self.reserve()
        (self.project / "direct_usage.jsonl").write_text(json.dumps({
            "call_id": "billing-row", "timestamp": 10, "usage_known": True,
            "input_tokens": 100, "output_tokens": 10}) + "\n")
        self.assertEqual(budget.status(self.project)["requests"], 2)
        self.finish(call, usage_call_id="billing-row")
        self.assertEqual(budget.status(self.project)["requests"], 1)

    def test_concurrent_dispatch_only_one_pending(self):
        def attempt(_):
            try:
                return self.reserve()
            except budget.BudgetExceeded:
                return None
        with ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(attempt, range(6)))
        self.assertEqual(sum(value is not None for value in results), 1)
        self.assertEqual(budget.status(self.project)["requests"], 1)

    def test_corrupt_ledger_fails_closed(self):
        (self.project / "request_budget.sqlite3").write_bytes(b"damaged database")
        self.assertTrue(budget.status(self.project)["blocked"])
        with self.assertRaises(budget.BudgetExceeded):
            self.reserve()

    def test_truncated_ledger_fails_closed(self):
        (self.project / "request_budget.sqlite3").write_bytes(b"")
        self.assertTrue(budget.status(self.project)["blocked"])

    def test_report_only_legacy_calls_are_counted(self):
        (self.project / "direct_report.json").write_text(json.dumps({
            "started": 1, "provider": "claude", "calls": [
                {"input": 100, "cache_read": 1000, "cache_write": 200, "output": 10}]}))
        report = budget.status(self.project)
        self.assertEqual(report["requests"], 1)
        self.assertEqual(report["full_context_reserved_tokens"], 1300)

    def test_actual_input_above_reserve_reduces_remaining_allowance(self):
        call = self.reserve("full")
        budget.finish_call(self.project, call, usage_known=True,
                           usage={"input_tokens": 99000, "output_tokens": 1})
        with self.assertRaisesRegex(budget.BudgetExceeded, "reserve"):
            self.reserve("full")

    def test_bad_metadata_and_bad_history_fail_closed(self):
        (self.project / "token_telemetry.json").write_text("{")
        self.assertTrue(budget.status(self.project)["blocked"])
        (self.project / "token_telemetry.json").write_text("{}")
        (self.project / "direct_usage.jsonl").write_text("{bad}")
        self.assertTrue(budget.status(self.project)["blocked"])
        with self.assertRaises(budget.BudgetExceeded):
            self.reserve()

    def test_old_calls_accounted_and_deduplicated(self):
        row = {"call_id": "old", "timestamp": 5, "usage_known": True, "input_tokens": 12000, "output_tokens": 100}
        (self.project / "direct_usage.jsonl").write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
        self.assertEqual(budget.status(self.project)["requests"], 1)
        call = self.reserve()
        row.update(call_id="usage-id")
        with (self.project / "direct_usage.jsonl").open("a") as handle:
            handle.write(json.dumps(row) + "\n")
        self.finish(call, usage_call_id="usage-id")
        self.assertEqual(budget.status(self.project)["requests"], 2)

    def test_unknown_historical_charge_blocks(self):
        (self.project / "token_telemetry.json").write_text('{"job_start_epoch": 10}')
        (self.project / "direct_usage.jsonl").write_text(json.dumps({"call_id": "old", "timestamp": 1, "usage_known": False}) + "\n")
        self.assertTrue(budget.status(self.project)["blocked"])
        with self.assertRaisesRegex(budget.BudgetExceeded, "Unresolved"):
            self.reserve()

    def test_unknown_host_stays_unknown(self):
        row = {"call_id": "d", "timestamp": 2, "usage_known": True, "input_tokens": 10, "output_tokens": 1}
        (self.project / "direct_usage.jsonl").write_text(json.dumps(row) + "\n")
        report = cost_control.combine({"agent_name": "unknown", "token_precision": "unavailable"}, self.project, 1, 3)
        self.assertTrue(report["host_usage_unknown"])
        self.assertEqual(report["steps_count"], 1)


if __name__ == "__main__":
    unittest.main()
