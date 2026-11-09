"""Isolated transport/accounting tests; no research data or AI performance outputs."""
from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts/06_agent_evaluation/live'))
import run_review_pilot as live
from run_compact_pilot import ResponsesProvider
from test_multi_agent_integration import fixture_payload, receipt


class ApprovedReviewLiveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name)
        self.ledger = live.BudgetLedger(self.path / 'test.sqlite')
        self.calls = []
        self.tokens = 500
        def post(endpoint, body):
            self.calls.append(endpoint)
            if endpoint.endswith('input_tokens'):
                return {'object': 'response.input_tokens', 'input_tokens': self.tokens}
            return receipt()
        self.network = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline test'))
        self.transport = patch.object(ResponsesProvider, '_post', side_effect=post)
        self.network.start()
        self.transport.start()
        self.provider = live.TrialProvider(self.ledger, 'technical-test-key', self.path, 'technical-case')
        self.provider.execution_kind = 'technical_test_transport_not_live_inference'

    def tearDown(self):
        self.transport.stop()
        self.network.stop()
        self.ledger.close()
        self.temp.cleanup()

    def request(self, arm='three_agents', role='process_analyst', identifier='request-1'):
        return dict(request_id=identifier, case_key='technical-case', run_key=live.RUN_KEYS[arm],
                    role=role, payload=fixture_payload())

    def test_wrong_case_role_model_run_cannot_dispatch(self):
        for field, value in [('case_key', 'different-case'), ('run_key', 'unapproved-run'), ('role', 'single_agent_baseline')]:
            request = self.request()
            request[field] = value
            with self.assertRaises(live.BudgetBlocked):
                self.provider.call(**request)
        request = self.request()
        request['payload']['model'] = 'gpt-6.1-terra'
        with self.assertRaises(live.BudgetBlocked):
            self.provider.call(**request)
        self.assertEqual(self.calls, [])

    def test_sixteen_total_and_four_per_role_cannot_be_exceeded(self):
        for role in ('process_analyst', 'context_analyst', 'evidence_verifier', 'single_agent_baseline'):
            arm = 'single_agent' if role == 'single_agent_baseline' else 'three_agents'
            for index in range(4):
                self.provider.call(**self.request(arm, role, f'{role}-{index}'))
            with self.assertRaises(live.BudgetBlocked):
                self.provider.call(**self.request(arm, role, f'{role}-extra'))
        self.assertEqual(self.ledger.snapshot()['request_states'], {'settled': 16})
        self.assertEqual(len(self.calls), 32)
        self.assertNotIn('technical-test-key', (self.path / 'requests.jsonl').read_text())

    def test_duplicate_id_cannot_repeat_dispatch(self):
        self.provider.call(**self.request())
        with self.assertRaises(live.BudgetBlocked):
            self.provider.call(**self.request())
        self.assertEqual(len(self.calls), 2)

    def test_combined_cost_guard_blocks_before_count_or_inference(self):
        self.provider.call(**self.request())
        with patch.object(live, 'CAP_NANOUSD', self.provider.reservation):
            with self.assertRaises(live.BudgetBlocked):
                self.provider.call(**self.request(identifier='request-2'))
        self.assertEqual(len(self.calls), 2)

    def test_over_token_limit_logs_and_cancels_without_inference(self):
        self.tokens = 6001
        with self.assertRaises(live.BudgetBlocked):
            self.provider.call(**self.request())
        self.assertEqual(self.calls, ['responses/input_tokens'])
        self.assertEqual(self.ledger.snapshot()['request_states'], {'cancelled_unsent': 1})
        self.assertEqual(self.ledger.snapshot()['spent_nanousd'], 0)

    def test_policy_change_is_rejected_and_original_cap_is_exact(self):
        self.assertEqual(live.check_policy(self.ledger._policy) * 16, live.CAP_NANOUSD)
        policy = deepcopy(self.ledger._policy)
        policy['models']['gpt-6-luna']['input_nanousd_per_token'] += 1
        with self.assertRaises(live.BudgetBlocked):
            live.check_policy(policy)
