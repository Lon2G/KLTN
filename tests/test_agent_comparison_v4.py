"""End-to-end v4 batch runner on a temporary ledger with a technical test transport.
Nothing here is AI inference or a research result; the shared ledger is never touched."""
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'scripts/06_agent_evaluation/live'))
import run_comparison_v4 as batch  # noqa: E402
from test_agent_review_v3 import LoopingFixture  # noqa: E402
from run_smoke import LEDGER, canonical, digest  # noqa: E402


def transport():
    fixtures = {}

    def post(endpoint, body):
        if endpoint == 'responses/input_tokens':
            return {'object': 'response.input_tokens', 'input_tokens': len(canonical(body)) // 4}
        text = body['instructions']
        role = next((r for r in ('process_analyst', 'context_analyst', 'evidence_verifier') if 'Your role is ' + r in text),
                    'single_agent_baseline')
        arm = 'single_agent' if role == 'single_agent_baseline' else 'three_agents'
        fixture = fixtures.setdefault(arm, LoopingFixture(arm))
        reply = fixture.call(role=role, payload=body, request_id='t', case_key='t', run_key='t')
        n = len(canonical(body)) // 4
        return {**reply, 'status': 'completed', 'model': body['model'], 'service_tier': 'default',
                'usage': {'input_tokens': n, 'output_tokens': 50, 'total_tokens': n + 50,
                          'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}}}
    return post


class BatchRunnerTests(unittest.TestCase):
    def setUp(self):
        guard = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ledger = self.tmp / 'ledger_v2.sqlite'  # fresh v2 ledger, as in the real v4 run
        self.shared_before = digest(LEDGER)
        for name, value in (('DESTINATION', self.tmp / 'out'), ('LEDGER', self.ledger)):
            p = patch.object(batch, name, value)
            p.start()
            self.addCleanup(p.stop)

    def test_plan_is_offline_and_bounded(self):
        scope = batch.plan()
        self.assertEqual(scope['slots'], 80)
        self.assertEqual(scope['max_inference_requests'], 20 * (12 + 4 + 4 + 4))
        self.assertLessEqual(scope['cap_usd'], scope['ledger_remaining_usd'])
        self.assertEqual(scope['max_input_tokens_per_request'], 12000)
        self.assertLessEqual(scope['cap_usd'], 2.0)
        self.assertLessEqual(scope['distinct_cases_after_run'], scope['pilot_case_limit'])

    def test_refuses_approval_above_user_limit(self):
        with self.assertRaises(batch.BudgetBlocked):
            batch.run(2.5, post=transport(), limit=1)

    def test_v1_ledger_untouched_and_run_keys_tagged(self):
        self.assertIn('policy_v2_input12000', batch.run_key('three_agents', 'native_bounded'))

    def test_refuses_without_sufficient_approval(self):
        with self.assertRaises(batch.BudgetBlocked):
            batch.run(0.01, post=transport(), limit=1)
        self.assertFalse((self.tmp / 'out').exists())

    def test_first_case_four_slots_run_resume_and_replay(self):
        batch.run(2.0, post=transport(), limit=4)
        files = sorted((self.tmp / 'out' / 'slots').glob('*.json'))
        self.assertEqual(len(files), 4)
        statuses = {json.loads(f.read_text())['result']['explanation_status'] for f in files}
        self.assertNotIn('unavailable_budget', statuses)
        before = {f.name: digest(f) for f in files}
        batch.run(2.0, post=transport(), limit=4)  # resume: nothing re-dispatched
        self.assertEqual(before, {f.name: digest(f) for f in files})
        summary = json.loads((self.tmp / 'out' / 'summary.json').read_text())
        self.assertEqual(summary['finished_slots'], 4)
        self.assertIsNone(summary['ranking'])
        self.assertEqual(batch.audit()['verified_slots'], 4)
        self.assertEqual(digest(LEDGER), self.shared_before)


if __name__ == '__main__':
    unittest.main()
