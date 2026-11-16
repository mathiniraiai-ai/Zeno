"""Offline regressions for the low-cost workflow. No paid API or archive calls."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import cost_control
import direct_mode
import edit_packet
import script_order
import sol_direct


def words(text):
    return [{"w": word, "start": i * .25, "end": i * .25 + .20} for i, word in enumerate(text.split())]


class WorkspaceTest(unittest.TestCase):
    def setUp(self):
        (ROOT / "work").mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "work")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.project = self.root / "work" / "demo"
        self.project.mkdir(parents=True)
        self.policy_patch = patch.object(cost_control, "ROOT", self.root)
        self.policy_patch.start()
        self.addCleanup(self.policy_patch.stop)

    def fixture(self):
        p = self.project
        (p / "brief.md").write_text("Rules: splits z=1, sp=, captions at seam, no clipped words")
        (p / "plan.edl").write_text('#canvas 1080x1920@30\n#output "safe.mp4"\n#script script.txt\n')
        (p / "line_map.md").write_text("L1 [A] Hello\n  CAND A.mp4 0.00-1.00 heard=1.0\nL2 [B] Bye\n  CAND B.mp4 1.00-2.00 heard=1.0\n")
        (p / "facts.md").write_text("## A.mp4 (2s)\n  0.00 Hello |1.00\n  ! REPEAT keep this warning\n"
                                    "  speech (voice detector): 0-1\n  voiced (real sound): 0-1\n"
                                    "  speech gaps (pauses): 1-2\n## B.mp4 (3s)\n  1.00 Bye |2.00\n")
        (p / "token_telemetry.json").write_text(json.dumps({"job_start_epoch": 1, "agent_name": "Codex", "agent_session_id": "test"}))
        return p


class TestPacket(WorkspaceTest):
    def test_keeps_all_words_warnings_candidates_and_rules(self):
        packet, manifest = edit_packet.build(self.fixture())
        for text in ("0.00 Hello", "1.00 Bye", "REPEAT keep", "CAND A", "CAND B", "splits z=1", "speech gaps"):
            self.assertIn(text, packet)
        self.assertNotIn("voiced (real sound)", packet)
        self.assertNotIn("speech (voice detector)", packet)
        self.assertEqual(manifest["tokens"], "not measured; bytes are not tokens")

    def test_line_retrieval_has_source_evidence_and_no_other_line(self):
        detail = edit_packet.line_detail(self.fixture(), "L1")
        self.assertIn("REPEAT keep", detail)
        self.assertNotIn("Bye", detail)
        with self.assertRaises(ValueError):
            edit_packet.line_detail(self.project, "L10")

    def test_missing_evidence_blocks_packet(self):
        with self.assertRaises(ValueError):
            edit_packet.build(self.project)

    def test_changed_script_archives_stale_exceptions(self):
        p = self.fixture()
        (p / "decisions.txt").write_text("accept L1 heading, not spoken\n")
        edit_packet.refresh_line_map(p, "L1 [A] New spoken dialogue\n")
        self.assertFalse((p / "decisions.txt").exists())
        archived = list((p / "_history").glob("*decisions.txt"))
        self.assertEqual(archived[0].read_text(), "accept L1 heading, not spoken\n")

    def test_candidate_changes_do_not_discard_approved_exceptions(self):
        p = self.fixture()
        (p / "decisions.txt").write_text("accept L1 approved reason\n")
        mapping = (p / "line_map.md").read_text().replace("0.00-1.00", "0.10-1.00")
        edit_packet.refresh_line_map(p, mapping)
        self.assertTrue((p / "decisions.txt").exists())

    def test_no_clip_sheet_silently_dropped(self):
        p = self.fixture()
        for i in range(6):
            (p / f"clip_sheet_{i}.jpg").write_bytes(b"not decoded in this test")
        self.assertEqual(len(direct_mode.build_brief(p)[2]), 6)


class TestWarningRegressions(WorkspaceTest):
    def test_scene_dash_variants_are_directions(self):
        import prepare
        for dash in ("-", "–", "—", ":"):
            p = self.project / "script.txt"
            p.write_text(f"Scene 1 {dash} Living room\nHusband: Hello.\nScene 2 {dash} Horizontal split screen\nRobot: Come here.\n")
            meta, lines = prepare.parse_script_file(p)
            self.assertEqual([line["text"] for line in lines], ["Hello.", "Come here."])
            self.assertEqual([line["scene"] for line in lines], ["Scene 1", "Scene 2"])

    def test_scripted_contraction_repetition_is_allowed(self):
        transcript = words("I got to go repeat I got to go")
        self.assertEqual(script_order.find_unscripted_repeats(transcript, "I gotta go repeat I gotta go"), [])

    def test_extra_contraction_repetition_still_blocked(self):
        self.assertTrue(script_order.find_unscripted_repeats(words("I got to go repeat I got to go"), "I gotta go"))

    def test_contraction_edge_trim_preserves_real_word_boundaries(self):
        ws = words("I gotta go I gotta go")
        wtoks = [script_order.clean(w["w"].split()) for w in ws]
        _, start, end, _ = script_order._trim_edge_stumbles(ws, wtoks, 0, 5,
                                                          ["i", "gotta", "go"], "I gotta go")
        self.assertEqual((start, end), (3, 5))

    def test_real_stutter_still_blocked(self):
        self.assertTrue(script_order.find_unscripted_repeats(words("please please come here"), "please come here"))

    def test_candidates_show_all_sources_before_extra_takes(self):
        clips = [{"src": "Teacher.mp4", "hq_words": words("Come here Come here Come here Come here")},
                 {"src": "Student.mp4", "hq_words": words("Come here")}]
        line = {"id": "L1", "speaker": "Student", "text": "Come here", "tokens": ["come", "here"]}
        result = script_order.build_line_map([line], clips, limit=1)
        self.assertIn("CAND Student.mp4", result)
        self.assertIn("CAND Teacher.mp4", result)

    def test_short_voice_review_remains_blocking(self):
        base = {"accepted": [], "problems": {}}
        with patch.object(script_order, "gate", return_value=base), \
             patch.object(script_order, "cut_problems", return_value=[]), \
             patch.object(script_order, "repeat_problems", return_value=[]), \
             patch.object(script_order, "voice_problems", return_value=(["L1 [SHORT VOICE REVIEW] inspect"], "")), \
             patch.object(script_order, "tight_report", return_value=[]), \
             patch.object(script_order, "style_report", return_value=[]):
            report = script_order.full_gate({"segments": [{"id": "L1"}]}, "script", self.project)
        self.assertTrue(report["blocked"])
        self.assertIn("VOICE REVIEW REQUIRED", report["problems"])


class TestBudget(WorkspaceTest):
    def test_invalid_policy_cannot_bypass_intake_guard(self):
        import zeno
        with patch.dict(os.environ, {"ZENO_MODEL_BUDGET_INR": "nan", "ZENO_SESSION_LIMIT": "100000"}), \
             patch("agent_log.session_size") as measure, self.assertRaises(ValueError):
            zeno.session_gate("inspect", block=True)
        measure.assert_not_called()

    def test_invalid_budget_fails_closed(self):
        for value in ("nan", "-1", "0", "inf"):
            with patch.dict(os.environ, {"ZENO_MODEL_BUDGET_INR": value}), self.assertRaises(ValueError):
                cost_control.settings()

    def test_stage_costs_sum_to_total(self):
        p = self.fixture()
        (p / "stage_events.jsonl").write_text(json.dumps({"at": 20, "kind": "inspect_done"}) + "\n")
        req = {"model": "gpt-6.1-sol", "input_tokens": 1000, "cached_input_tokens": 800,
               "cache_write_input_tokens": 0, "output_tokens": 20, "context": 1000}
        agent = cost_control.usage_from_requests([{**req, "timestamp": 10}, {**req, "timestamp": 30}])
        report = cost_control.stage_report(p, agent, 1, 40)
        self.assertEqual([row["stage"] for row in report], ["intake", "planning"])
        self.assertAlmostEqual(sum(row["cost"]["inr"] for row in report), cost_control.token_cost.estimate(agent)["inr"])

    def test_over_budget_stops_and_unknown_not_zero(self):
        p = self.fixture()
        request = {"model": "gpt-6.1-sol", "input_tokens": 10000000, "cached_input_tokens": 0,
                   "cache_write_input_tokens": 0, "output_tokens": 0, "context": 100000, "timestamp": 2}
        with patch("token_tracker.detect_agent_usage", return_value=cost_control.usage_from_requests([request])):
            self.assertTrue(cost_control.snapshot(p)["blocked"])
        with patch("token_tracker.detect_agent_usage", return_value={}):
            result = cost_control.snapshot(p)
            self.assertFalse(result["available"])
            self.assertIn("not zero", result["reason"])

    def test_direct_usage_window_and_host_counts_combined_once(self):
        p = self.fixture()
        req = sol_direct.measured_usage({"usage": {"input_tokens": 50, "output_tokens": 10}}, "a", "direct planning", 20)
        (p / "direct_usage.jsonl").write_text(json.dumps(req) + "\n")
        host = cost_control.usage_from_requests([{**req, "timestamp": 10}])
        combined = cost_control.combine(host, p, 1, 30)
        self.assertEqual(combined["total_tokens_including_cache"], 120)
        self.assertEqual(cost_control.combine(host, p, 21, 30)["total_tokens_including_cache"], 60)

    def test_completed_budget_window_excludes_later_development(self):
        p = self.fixture()
        (p / "token_telemetry.json").write_text(json.dumps({"job_start_epoch": 1, "job_end_epoch": 10}))
        with patch("token_tracker.detect_agent_usage", return_value={}) as detect:
            cost_control.snapshot(p)
        self.assertEqual(detect.call_args.kwargs["until_epoch"], 10)

    def test_unresolved_api_reservation_blocks_host_checkpoint(self):
        p = self.fixture()
        (p / "direct_call_pending.json").write_text('{"call_id":"unresolved"}')
        with patch("token_tracker.detect_agent_usage", return_value={}):
            report = cost_control.snapshot(p)
        self.assertTrue(report["blocked"])
        self.assertTrue(report["pending_direct_call"])


class TestDirectPlanner(WorkspaceTest):
    answer = '```analysis\nFormat: dialogue\nL1 calm\n```\n```plan\nL1 A.mp4 0-1 z=1 sp=A\n```\n```decisions\n\n```\nNOTES:\nReview framing.'

    def response(self, answer=None):
        return {"status": "completed", "model": "gpt-6.1-sol",
                "usage": {"input_tokens": 1000, "input_tokens_details": {"cached_tokens": 800}, "output_tokens": 100},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": answer or self.answer}]}]}

    @contextlib.contextmanager
    def mocked(self, responses=None, checks=None):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-not-a-key"}), \
             patch.object(sol_direct, "send", side_effect=responses or [self.response()]) as send, \
             patch.object(cost_control, "snapshot", return_value={"available": True, "cost": {"inr": 0}, "blocked": False}), \
             patch.object(direct_mode, "run_check", side_effect=checks or [(True, "[PLAN PASS]\n[STYLE] deliberate")]) as check, \
             contextlib.redirect_stdout(io.StringIO()):
            yield send, check

    def test_dry_run_no_paid_calls(self):
        p = self.fixture()
        with patch.object(sol_direct, "send") as send:
            result = sol_direct.run(p, dry_run=True)
        send.assert_not_called()
        self.assertEqual(result["paid_calls"], 0)

    def test_success_one_call_one_check_no_render(self):
        p = self.fixture()
        with self.mocked() as (send, check):
            result = sol_direct.run(p)
        self.assertTrue(result["passed"])
        self.assertEqual((send.call_count, check.call_count), (1, 1))
        self.assertIn('safe.mp4', (p / "plan.edl").read_text())
        self.assertEqual(len(cost_control.rows(p / "direct_usage.jsonl")), 1)
        self.assertFalse((p / "direct_call_pending.json").exists())

    def test_corrections_have_no_growing_conversation(self):
        p = self.fixture()
        with self.mocked([self.response(), self.response()], [(False, "[BLOCKED] cut L1"), (True, "[PLAN PASS]")]) as (send, check):
            result = sol_direct.run(p)
        self.assertTrue(result["passed"])
        self.assertEqual(send.call_count, 2)
        for call in send.call_args_list:
            body = call.args[0]
            self.assertEqual(len(body["input"]), 1)
            self.assertNotIn("previous_response_id", body)
            self.assertFalse(body["store"])

    def test_same_failure_stops_and_restores_original(self):
        p = self.fixture()
        original = (p / "plan.edl").read_text()
        with self.mocked([self.response(), self.response()], [(False, "same blocker"), (False, "same blocker")]) as (send, _):
            result = sol_direct.run(p)
        self.assertFalse(result["passed"])
        self.assertEqual(send.call_count, 2)
        self.assertEqual((p / "plan.edl").read_text(), original)

    def test_uncertain_voice_stops_without_waiving_check(self):
        p = self.fixture()
        with self.mocked(checks=[(False, "VOICE REVIEW REQUIRED L1")]) as (send, _):
            result = sol_direct.run(p)
        self.assertFalse(result["passed"])
        self.assertEqual(send.call_count, 1)

    def test_exception_needs_human_review(self):
        p = self.fixture()
        answer = self.answer.replace('```decisions\n\n', '```decisions\naccept L1 wrong voice is fine\n')
        with self.mocked([self.response(answer)]) as (_, check):
            result = sol_direct.run(p)
        self.assertFalse(result["passed"])
        check.assert_not_called()
        self.assertFalse((p / "decisions.txt").exists())

    def test_timeout_stops_retries_and_retains_reservation(self):
        p = self.fixture()
        with self.mocked([TimeoutError("private transport details")]) as (send, _):
            result = sol_direct.run(p)
        self.assertEqual(send.call_count, 1)
        self.assertNotIn("private transport", json.dumps(result))
        self.assertTrue((p / "direct_call_pending.json").exists())
        with self.mocked() as (send, _), self.assertRaises(ValueError):
            sol_direct.run(p)
        send.assert_not_called()

    def test_unknown_usage_stops_and_incomplete_does_not_install_plan(self):
        p = self.fixture()
        response = self.response()
        response.pop("usage")
        with self.mocked([response]) as (send, check):
            result = sol_direct.run(p)
        self.assertFalse(result["passed"])
        self.assertEqual(send.call_count, 1)
        check.assert_not_called()

    def test_budget_reserves_before_sending(self):
        p = self.fixture()
        with self.mocked() as (send, _), patch.dict(os.environ, {"ZENO_MODEL_BUDGET_INR": "0.01"}):
            result = sol_direct.run(p)
        send.assert_not_called()
        self.assertIn("budget", result["stopped"])

    def test_no_key_no_network(self):
        p = self.fixture()
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}), patch.object(sol_direct, "send") as send, self.assertRaises(ValueError):
            sol_direct.run(p)
        send.assert_not_called()

    def test_failed_check_cannot_pass_by_printing_pass(self):
        class Result:
            returncode = 2
            stdout = "[PLAN PASS] old log"
            stderr = "actual failure"
        with patch.object(direct_mode.subprocess, "run", return_value=Result()):
            self.assertFalse(direct_mode.run_check(self.project)[0])


if __name__ == "__main__":
    unittest.main()
