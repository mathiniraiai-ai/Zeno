"""Offline regressions for isolated, bounded planner repairs."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import direct_mode
import request_budget
import cost_control


class TestPlannerContext(unittest.TestCase):
    def setUp(self):
        checkpoint = patch('cost_control.snapshot', return_value={'blocked': False})
        checkpoint.start()
        self.addCleanup(checkpoint.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proj = Path(self.tmp.name)
        self.plan = ('#output safe.mp4\n#subs auto y=68\n\n'
                     'L1 A.mp4 0.00-1.00 z=1 sp=Alice\n'
                     'L2 A.mp4 2.00-3.00 z=1 sp=Alice\n'
                     'L3 A.mp4 4.00-5.00 z=1 sp=Alice\n'
                     'L4 B.mp4 0.00-1.00 z=1 sp=Bob\n')
        for name, value in {
            'plan.edl': self.plan, 'script_analysis.md': 'Original analysis\n',
            'decisions.txt': 'accept L4 deliberate script cue\n',
            'brief.md': 'FULL RULES ONLY IN FIRST REQUEST',
            'line_map.md': 'L1 [Alice] Hi\nL2 [Alice] There\nL3 [Alice] Friend\nL4 [Bob] Bye\n',
            'facts.md': ('## A.mp4 (100.00s 1080x1920)\n  0.00 Hi |1.00\n'
                         '  2.00 There |3.00\n  4.00 Friend |5.00\n'
                         '  99.00 UNRELATED_SOURCE_WORD |99.50\n'
                         '  speech gaps: 0.10-99.90 GLOBAL_SUMMARY\n'
                         '## B.mp4 (1.00s 1080x1920)\n  0.00 Bye |1.00\n')
        }.items():
            (self.proj / name).write_text(value)

    def blocks(self):
        return {'plan': 'L2 A.mp4 2.05-2.95 z=1 sp=Alice', 'analysis': 'Trimmed L2.', 'decisions': ''}

    def response(self, correction=False, known=True):
        blocks = self.blocks() if correction else {
            'plan': '\n'.join(line for line in self.plan.splitlines() if line.startswith('L')),
            'analysis': 'Initial analysis', 'decisions': ''}
        answer = '\n'.join(f'```{key}\n{value}\n```' for key, value in blocks.items())
        return {'text': answer, 'session': 'must-not-resume', 'error': '', 'seconds': 1,
                'usage': {'input': 20, 'cache_read': 10, 'output': 5, 'total': 35}, 'usage_known': known}

    def test_only_affected_lines_neighbors_and_windows(self):
        system, text, scope = direct_mode.build_correction(self.proj, '[BLOCKED] L2 pause too long')
        self.assertEqual(scope, {'L2'})
        self.assertIn('L1 A.mp4', text)
        self.assertIn('L3 A.mp4', text)
        for excluded in ('L4 B.mp4', 'L4 [Bob]', 'UNRELATED_SOURCE_WORD', 'GLOBAL_SUMMARY', 'FULL RULES ONLY'):
            self.assertNotIn(excluded, system + text)
        self.assertIn('There |3.00', text)
        self.assertIn('Original analysis', text)
        self.assertIn('accept L4 deliberate script cue', text)

    def test_merge_preserves_all_unaffected_artifacts(self):
        result = direct_mode.merge_correction(self.proj, self.blocks(), {'L2'})
        self.assertEqual(result['plan'], self.plan.replace('2.00-3.00', '2.05-2.95'))
        self.assertEqual(result['decisions'], 'accept L4 deliberate script cue\n')
        self.assertTrue(result['analysis'].startswith('Original analysis\n'))
        self.assertEqual((self.proj / 'plan.edl').read_text(), self.plan)

    def test_rejects_out_of_scope_missing_duplicate_and_identity_changes(self):
        changes = [ {'plan': ''}, {'plan': self.blocks()['plan'] + '\nL9 A.mp4 1.00-2.00 sp=Alice'},
                   {'plan': self.blocks()['plan'] + '\n' + self.blocks()['plan']},
                   {'plan': self.blocks()['plan'].replace('A.mp4', 'B.mp4')},
                   {'plan': self.blocks()['plan'].replace('sp=Alice', 'sp=Bob')},
                   {'decisions': 'accept L2 uncertain voice'},
                   {'plan': '#subs off\n' + self.blocks()['plan']}, {'analysis': ''} ]
        for change in changes:
            with self.subTest(change=change), self.assertRaises(ValueError):
                direct_mode.merge_correction(self.proj, self.blocks() | change, {'L2'})

    def test_unscoped_uncertain_or_oversized_correction_stops(self):
        for diagnostic in ('[BLOCKED] unknown problem', '[BLOCKED] L99', 'WRONG VOICE L2'):
            with self.subTest(diagnostic=diagnostic), self.assertRaises(ValueError):
                direct_mode.build_correction(self.proj, diagnostic)
        with patch('cost_control.settings', return_value={'planner_targeted_max_input_bytes': 10}), self.assertRaises(ValueError):
            direct_mode.build_correction(self.proj, '[BLOCKED] L2')

    def runner(self, provider='claude', known=True):
        runner = direct_mode.run if provider == 'claude' else direct_mode.run_codex
        with patch.object(direct_mode, 'call_' + provider, side_effect=[self.response(known=known), self.response(True)]) as call, \
             patch.object(direct_mode.shutil, 'which', return_value='/mock/claude'), \
             patch.object(direct_mode, 'find_codex_binary', return_value='/mock/codex'), \
             patch.object(direct_mode, 'run_check', side_effect=[(False, '[BLOCKED] L2 pause'), (True, '[PLAN PASS]')]) as check, \
             contextlib.redirect_stdout(io.StringIO()):
            result = runner(self.proj)
        return result, call, check

    def test_claude_fresh_targeted_second_request(self):
        result, call, check = self.runner()
        self.assertTrue(result['passed'])
        self.assertEqual((call.call_count, check.call_count), (2, 2))
        self.assertIsNone(call.call_args_list[1].args[4])
        self.assertEqual(call.call_args_list[1].args[2], [])
        self.assertNotIn('FULL RULES ONLY', call.call_args_list[1].args[0])
        self.assertEqual([c['context_kind'] for c in result['calls']], ['full', 'targeted'])
        saved = request_budget.status(self.proj)
        self.assertEqual(saved['requests'], 2)
        self.assertEqual(saved['full_context_requests'], 1)

    def test_codex_fresh_targeted_second_request(self):
        result, call, check = self.runner('codex')
        self.assertTrue(result['passed'])
        self.assertNotIn('FULL RULES ONLY', call.call_args_list[1].args[0])
        self.assertEqual(call.call_args_list[1].args[2], [])

    def test_unknown_usage_stops_and_restores_all_files(self):
        result, call, check = self.runner(known=False)
        self.assertFalse(result['passed'])
        self.assertEqual(call.call_count, 1)
        self.assertEqual((self.proj / 'plan.edl').read_text(), self.plan)
        self.assertEqual((self.proj / 'script_analysis.md').read_text(), 'Original analysis\n')
        self.assertEqual((self.proj / 'decisions.txt').read_text(), 'accept L4 deliberate script cue\n')
        self.assertTrue(request_budget.status(self.proj)['blocked'])

    def test_restart_cannot_resume_pending_or_reset_budget(self):
        request_budget.reserve_call(self.proj, provider='claude', context_kind='full', input_bytes=10)
        result, call, check = self.runner()
        self.assertFalse(result['passed'])
        call.assert_not_called()
        check.assert_not_called()
        self.assertIn('Unresolved', result['stopped'])

    def test_codex_fallback_disables_essential_connectors(self):
        with patch('launch_edit.codex_overrides', return_value=[]) as overrides:
            direct_mode._safe_codex_overrides()
        self.assertFalse(overrides.call_args.kwargs['keep_essential'])

    def test_existing_project_lock_blocks_dispatch(self):
        (self.proj / 'direct.lock').touch()
        with patch.object(direct_mode, 'call_claude') as call, self.assertRaises(ValueError):
            direct_mode.run(self.proj)
        call.assert_not_called()
        self.assertTrue((self.proj / 'direct.lock').exists())

    def test_spending_checkpoint_blocks_before_dispatch(self):
        with patch('cost_control.snapshot', return_value={'blocked': True, 'reason': 'INR limit reached'}):
            result, call, check = self.runner()
        call.assert_not_called()
        self.assertTrue(result['budget_blocked'])
        self.assertEqual(result['stopped'], 'INR limit reached')

    def test_initial_plan_cannot_add_exception(self):
        response = self.response()
        response['text'] = response['text'].replace('```decisions\n\n```', '```decisions\naccept L2 bad voice\n```')
        with patch.object(self, 'response', return_value=response):
            result, call, check = self.runner()
        self.assertFalse(result['passed'])
        self.assertEqual(call.call_count, 1)
        check.assert_not_called()
        self.assertIn('exception', result['stopped'])

    def test_journal_failure_keeps_reservation_unresolved(self):
        with patch.object(direct_mode, '_append_direct_usage', side_effect=OSError('disk full')), self.assertRaises(OSError):
            self.runner()
        self.assertTrue(request_budget.status(self.proj)['blocked'])
        self.assertEqual((self.proj / 'plan.edl').read_text(), self.plan)
        self.assertFalse((self.proj / 'direct.lock').exists())

    def test_codex_isolation_failure_stops_instead_of_enabling_tools(self):
        with patch('launch_edit.codex_overrides', side_effect=ValueError('invalid connector')), self.assertRaises(ValueError):
            direct_mode._safe_codex_overrides()

    def test_claude_supported_turn_cap(self):
        with patch.object(direct_mode, '_cli_help', return_value='--max-turns --no-chrome'):
            flags = direct_mode._claude_flags('/mock/claude', 'sonnet', 'rules', None)
        self.assertEqual(flags[flags.index('--max-turns') + 1], '1')

    def test_codex_extracts_subprocess_session_only(self):
        self.assertEqual(direct_mode._codex_session_id(
            'invalid\n{"type":"thread.started","thread_id":"child-123"}\n'), 'child-123')
        self.assertIsNone(direct_mode._codex_session_id('{"type":"turn.completed"}'))

    def test_matching_subprocess_log_is_not_billed_twice(self):
        row = direct_mode._append_direct_usage(
            self.proj, 'claude', 'sonnet', {'input': 20, 'cache_read': 10, 'output': 5},
            True, session_id='child-123')
        agent = {'agent_name': 'Claude Code', 'session_id': 'child-123', 'token_precision': 'exact',
                 'requests': [row], 'steps_count': 1}
        same = cost_control.combine(agent, self.proj, 0, row['timestamp'] + 1)
        self.assertEqual(same['steps_count'], 1)
        self.assertEqual(same['total_tokens_including_cache'], 35)
        separate = cost_control.combine(agent | {'session_id': 'external-host'},
                                        self.proj, 0, row['timestamp'] + 1)
        self.assertEqual(separate['steps_count'], 2)
        self.assertEqual(separate['total_tokens_including_cache'], 70)
