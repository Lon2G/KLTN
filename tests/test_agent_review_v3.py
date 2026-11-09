"""Loop guard and budget profiles on real Olist cases. Fixtures test control flow only;
no fixture output is AI reasoning or a research result."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'scripts/06_agent_evaluation/review_v3'))
from test_agent_review_v2 import RepairFixture  # noqa: E402
from runner_v3 import run_review, PROFILES, run_key, VERSION  # noqa: E402
from loop_guard import (GuardedProvider, forced_terminal, repeated_tool_call, no_new_evidence,  # noqa: E402
                        TERMINALS, CAP_MESSAGE)
from catalog_wire import expand_payload  # noqa: E402
from run_smoke import DEFAULT, read, EvidenceStore, BudgetBlocked  # noqa: E402

LOOP_TOOLS = {'process_analyst': ('get_reference_membership', {'stage': 'transport'}),
              'context_analyst': ('compare_declared_peer_groups', {'stage': 'transport', 'plan': 'category_peers'}),
              'evidence_verifier': ('recompute_reported_metrics', {'stage': 'transport'}),
              'single_agent_baseline': ('compare_declared_peer_groups', {'stage': 'transport', 'plan': 'same_product_peers'})}


class LoopingFixture:
    """Keeps calling the same tool whenever the request allows any tool; submits only when forced.
    Reproduces the control-flow pattern of the 07/11 blocked single-agent run."""
    execution_kind = 'technical_scripted_test_not_AI_inference'

    def __init__(self, arm, cap_after=None):
        self.inner = RepairFixture(arm)
        self.records, self.cap_after = [], cap_after

    def call(self, **request):
        self.records.append(deepcopy(request))
        payload = request['payload']
        if self.cap_after is not None and payload['tool_choice'] == 'auto' and len(self.records) > self.cap_after:
            raise BudgetBlocked(CAP_MESSAGE + ' or count response is invalid')
        if payload['tool_choice'] == 'auto':
            name, args = LOOP_TOOLS[request['role']]
            return {'id': 'technical-loop-' + str(len(self.records)),
                    'output': [{'type': 'function_call', 'call_id': 'loop-' + str(len(self.records)),
                                'name': name, 'arguments': json.dumps(args)}]}
        response = self.inner.call(**request)
        forced = payload['tool_choice']['name']
        calls = [o for o in response['output'] if o.get('type') == 'function_call']
        if calls and calls[0]['name'] != forced:
            # A forced tool_choice can only return that function (API contract). Technical
            # stand-ins: an empty analyst selection, or verdicts that follow the code gate.
            if forced == 'submit_verdict':
                packet = json.loads(expand_payload(payload)['input'][0]['content'])['body']
                args = {'verdicts': [{'claim_id': c['claim_id'],
                                      'verdict': 'accept' if c['code_gate'] == 'passed' else 'reject',
                                      'reason': 'supported' if c['code_gate'] == 'passed' else 'unsupported'}
                                     for c in packet['claims']]}
            else:
                args = {'claim_ids': [], 'check_ids': [], 'unknown_ids': []}
            response = {'id': response.get('id', 'technical-forced'),
                        'output': [{'type': 'function_call', 'call_id': 'forced-' + str(len(self.records)),
                                    'name': forced, 'arguments': json.dumps(args)}]}
        return response


class LoopGuardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.rows = EvidenceStore(), read(DEFAULT / 'cohort.json')

    def setUp(self):
        guard = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)

    def case(self, index=2):
        r = self.rows[index]
        return self.store.case(r['order_id'], r['event_id'], 'assumed_static_context_at_purchase')

    def test_forced_terminal_offers_only_terminal_tool(self):
        payload = {'tools': [{'name': 'a'}, {'name': 'submit_analysis'}], 'tool_choice': 'auto', 'input': []}
        forced = forced_terminal(payload, 'process_analyst')
        self.assertEqual([t['name'] for t in forced['tools']], ['submit_analysis'])
        self.assertEqual(forced['tool_choice'], {'type': 'function', 'name': 'submit_analysis'})
        self.assertEqual(payload['tool_choice'], 'auto')  # original untouched

    def test_detectors(self):
        call = {'type': 'function_call', 'name': 't', 'arguments': '{"stage":"transport"}'}
        self.assertFalse(repeated_tool_call([call]))
        self.assertTrue(repeated_tool_call([call, {'type': 'function_call_output', 'output': '{}'}, dict(call)]))
        empty = {'type': 'function_call_output', 'output': json.dumps({'new_evidence': {}, 'body': {}})}
        fresh = {'type': 'function_call_output', 'output': json.dumps({'new_evidence': {'e5': {}}, 'body': {}})}
        self.assertTrue(no_new_evidence([empty]))
        self.assertFalse(no_new_evidence([fresh]))
        self.assertFalse(no_new_evidence([]))

    def test_single_agent_loop_now_publishes_within_cap(self):
        for index in (0, 2, 8):
            fixture = LoopingFixture('single_agent')
            result = run_review(self.case(index), fixture, 'single_agent')
            self.assertIn(result['explanation_status'], {'code_checked', 'evidence_checked', 'evidence_checked_with_rejections'})
            self.assertLessEqual(len(fixture.records), PROFILES['native_bounded']['single_agent'])
            self.assertTrue(any('no_new_evidence' in e['reasons'] or 'repeated_tool_call' in e['reasons'] for e in result['guard_log']))

    def test_three_agents_loop_publishes_with_all_roles(self):
        fixture = LoopingFixture('three_agents')
        result = run_review(self.case(), fixture, 'three_agents')
        self.assertIn(result['explanation_status'], {'evidence_checked', 'evidence_checked_with_rejections'})
        roles = {r['role'] for r in fixture.records}
        self.assertEqual(roles, {'process_analyst', 'context_analyst', 'evidence_verifier'})
        self.assertLessEqual(len(fixture.records), 12)

    def test_matched_4_reserves_turns_for_later_roles(self):
        fixture = LoopingFixture('three_agents')
        result = run_review(self.case(), fixture, 'three_agents', 'matched_4')
        self.assertLessEqual(len(fixture.records), 4)
        self.assertEqual({r['role'] for r in fixture.records}, {'process_analyst', 'context_analyst', 'evidence_verifier'})
        self.assertTrue(any('request_reserve' in e['reasons'] for e in result['guard_log']))
        # All three roles finish; with no spare request, an omitted warning stays visible as incomplete.
        self.assertIn(result['explanation_status'], {'evidence_checked', 'evidence_checked_with_rejections',
                                                     'incomplete_warning_coverage'})
        self.assertNotIn(result['coverage_repair']['status'], {'completed'})

    def test_token_cap_refusal_retries_once_as_forced_terminal_with_new_id(self):
        fixture = LoopingFixture('single_agent', cap_after=1)
        result = run_review(self.case(), fixture, 'single_agent')
        fallback = [e for e in result['guard_log'] if 'token_cap_fallback' in e['reasons']]
        self.assertEqual(len(fallback), 1)
        self.assertNotEqual(fallback[0]['dispatched_request_id'], fallback[0]['request_id'])
        dispatched = [r['request_id'] for r in fixture.records]
        self.assertEqual(len(dispatched), len(set(dispatched)))
        self.assertIn(fallback[0]['dispatched_request_id'], {e['request_id'] for e in result['trace'] if e['kind'] == 'model_request'})

    def test_forced_requests_never_edit_evidence_or_detector(self):
        case = self.case()
        plain = run_review(self.case(), RepairFixture('single_agent'), 'single_agent')
        looped = run_review(case, LoopingFixture('single_agent'), 'single_agent')
        self.assertEqual(plain['detector'], looped['detector'])
        self.assertEqual(plain['evidence'][:3], looped['evidence'][:3])

    def test_request_ids_unique_per_profile_and_version(self):
        a, b = LoopingFixture('three_agents'), LoopingFixture('three_agents')
        run_review(self.case(), a, 'three_agents', 'native_bounded')
        run_review(self.case(), b, 'three_agents', 'matched_4')
        self.assertFalse({r['request_id'] for r in a.records} & {r['request_id'] for r in b.records})
        self.assertNotEqual(run_key('three_agents', 'native_bounded'), run_key('three_agents', 'matched_4'))
        self.assertIn('loop_guard_v1', VERSION)

    def test_payloads_still_roundtrip_and_contract_fields_unchanged(self):
        fixture = LoopingFixture('single_agent')
        run_review(self.case(), fixture, 'single_agent')
        for r in fixture.records:
            expand_payload(r['payload'])
            self.assertEqual(set(r['payload']), {'model', 'instructions', 'input', 'tools', 'tool_choice',
                'parallel_tool_calls', 'max_output_tokens', 'store', 'reasoning', 'service_tier', 'truncation'})

    def test_twenty_real_cases_both_arms_both_profiles(self):
        statuses = {}
        for index in range(20):
            for arm in ('single_agent', 'three_agents'):
                for profile in PROFILES:
                    fixture = LoopingFixture(arm)
                    result = run_review(self.case(index), fixture, arm, profile)
                    self.assertLessEqual(len(fixture.records), PROFILES[profile][arm])
                    statuses[(index, arm, profile)] = result['explanation_status']
        self.assertEqual(len(statuses), 80)
        self.assertNotIn('unavailable_budget', set(statuses.values()))


if __name__ == '__main__':
    unittest.main()
