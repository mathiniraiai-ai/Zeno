"""Offline checks for streamed usage, unknown host usage and request attribution."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import agent_log
import codex_usage
import token_tracker

ROOT = Path(__file__).resolve().parents[1]
START = agent_log._epoch("2026-11-17T10:00:00Z")


def agy(step, second, **usage):
    return json.dumps({"type": "PLANNER_RESPONSE", "step_index": step,
                       "created_at": f"2026-11-17T10:00:{second:02d}Z", **usage})


class UsageBudgetAuditTests(unittest.TestCase):
    def test_antigravity_streamed_blocks_count_cache_once(self):
        usage = agent_log.antigravity_window_usage([
            agy(1, 1, input_tokens=10, cache_read_tokens=50000, output_tokens=20),
            agy(1, 2, input_tokens=10, cache_read_tokens=50000, output_tokens=30),
            agy(2, 3, input_tokens=40, cache_read_tokens=0, output_tokens=50),
        ], START, START + 60)
        self.assertEqual(usage["steps_count"], 2)
        self.assertEqual(usage["cached_tokens"], 50000)
        self.assertEqual(usage["output_tokens"], 80)
        self.assertEqual(usage["total_tokens_including_cache"], 50130)
        self.assertEqual([r["context"] for r in usage["requests"]], [50010, 40])
        self.assertEqual(usage["token_precision"], "exact")
        self.assertFalse(usage["host_usage_unknown"])

    def test_antigravity_missing_usage_is_partial_and_counts_request(self):
        usage = agent_log.antigravity_window_usage([
            agy(1, 1, input_tokens=10, cache_read_tokens=20, output_tokens=30),
            agy(2, 2, content="A response without provider usage"),
        ], START, START + 60)
        self.assertEqual(usage["steps_count"], 2)
        self.assertEqual(usage["unknown_request_count"], 1)
        self.assertEqual(usage["token_precision"], "partial")
        self.assertTrue(usage["host_usage_unknown"])
        self.assertEqual(len(usage["requests"]), 1)

    def test_antigravity_cache_only_and_zero_usage_are_measured(self):
        usage = agent_log.antigravity_window_usage([
            agy(1, 1, input_tokens=0, cache_read_tokens=50000, output_tokens=0),
            agy(2, 2, input_tokens=0, cache_read_tokens=0, output_tokens=0),
        ], START, START + 60)
        self.assertEqual(usage["token_precision"], "exact")
        self.assertEqual(usage["cached_tokens"], 50000)
        self.assertEqual(len(usage["requests"]), 2)

    def test_estimate_does_not_use_private_reasoning(self):
        public = agy(1, 1, content="visible answer")
        with_private = agy(1, 1, content="visible answer", thinking="private" * 1000)
        plain = agent_log.antigravity_window_usage([public], START, START + 60)
        private = agent_log.antigravity_window_usage([with_private], START, START + 60)
        self.assertEqual(plain["output_tokens"], private["output_tokens"])
        self.assertTrue(private["host_usage_unknown"])

    def test_claude_parallel_blocks_deduplicate_reads_and_writes(self):
        def block(output):
            return json.dumps({"type": "assistant", "timestamp": "2026-11-17T10:00:01Z",
                               "message": {"id": "one-request", "model": "claude-sonnet-4-5",
                                           "usage": {"input_tokens": 10, "cache_creation_input_tokens": 200,
                                                     "cache_read_input_tokens": 50000, "output_tokens": output}}})
        usage = token_tracker.claude_window_usage([block(10), block(20), block(20)], START, START + 60)
        self.assertEqual(usage["steps_count"], 1)
        self.assertEqual(usage["cached_tokens"], 50000)
        self.assertEqual(usage["cache_write_tokens"], 200)
        self.assertEqual(usage["requests"][0]["input_tokens"], 50210)
        self.assertEqual(usage["total_tokens_including_cache"], 50230)

    def test_missing_codex_counters_are_unknown_not_zero_bill(self):
        usage = codex_usage.window_usage([
            {"type": "session_meta", "payload": {"id": "test", "cwd": str(ROOT)}}
        ], START, START + 60, ROOT)
        self.assertEqual(usage["token_precision"], "unavailable")
        self.assertTrue(usage["host_usage_unknown"])
        with patch.dict(os.environ, {"CODEX_THREAD_ID": "test", "CLAUDECODE": ""}), \
                patch.object(codex_usage, "detect", return_value=None):
            detected = token_tracker.detect_agent_usage(START, START + 60)
        self.assertTrue(detected["host_usage_unknown"])


if __name__ == "__main__":
    unittest.main()
