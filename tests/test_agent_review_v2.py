"""Real Olist sources and saved AI responses. Fixtures test control flow only."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts/06_agent_evaluation/review_v2'))
from catalog_wire import compact_payload, expand_payload, expand_table, canonical
from evidence_wire import expand_payload as expand_v1
from warning_coverage import annotate, coverage, INSTRUCTIONS as COVERAGE_INSTRUCTIONS
from runner import run_review, ReviewProvider, repair, VERSION
from comparison_core import source_metrics, initial_evidence
from comparison_runners import run_rules
from run_smoke import DEFAULT, KEYS, read, EvidenceStore, BudgetBlocked, ProviderUnavailable
from test_multi_agent_integration import ScriptedProvider
from test_agent_comparison import SingleFixture

LIVE = ROOT / 'data/experiments/olist_agent_live_compact_v1_case2'


class RepairFixture:
    """Protocol selections copied from real source catalogs, never research AI output."""
    execution_kind = 'technical_scripted_test_not_AI_inference'

    def __init__(self, arm, omit=False, reject=False, refusal=False, fail=False, exhausted=False):
        self.arm, self.omit, self.reject, self.refusal, self.fail = arm, omit, reject, refusal, fail
        self.records = []
        self.fixture = ScriptedProvider(always_revise=exhausted) if arm == 'three_agents' else SingleFixture()

    def call(self, **request):
        self.records.append(deepcopy(request))
        payload = expand_payload(request['payload'])
        packet = json.loads(payload['input'][0]['content'])
        if 'missing_warning_stages' not in packet and not (request['role'] == 'evidence_verifier' and 'body' not in packet):
            return self.fixture.call(**{**request, 'payload': payload})
        if self.fail:
            raise ProviderUnavailable('Technical repair failure')
        if self.refusal:
            return {'id': 'technical-refusal', 'output': []}
        name = payload['tools'][0]['name']
        if name == 'submit_verdict':
            args = {'verdicts': [{'claim_id': i['claim_id'],
                                  'verdict': 'reject' if self.reject else 'accept',
                                  'reason': 'unsupported' if self.reject else 'supported'} for i in packet['claims']]}
        else:
            groups = packet['catalogs']['process'] if self.arm == 'single_agent' else packet
            records = {r['evidence_id']: r for r in packet['assessments']}
            ids = [k for group in groups['claim_catalog'] for k, v in group['choices'].items()
                   if group['evidence_id'] in records and v['field'] == 'long_stage_warning'
                   and records[group['evidence_id']]['data']['long_stage_warning'] is True]
            unknowns = [k for group in groups['metadata_catalog'] for k, v in group['choices'].items()
                        if v['metadata_kind'] == 'unknown'][:1]
            process = {'claim_ids': [] if self.omit else ids, 'unknown_ids': unknowns, 'check_ids': []}
            args = {'process': process, 'context': {'claim_ids': [], 'unknown_ids': [], 'check_ids': []}} if self.arm == 'single_agent' else process
        return {'id': 'technical-repair', 'output': [{'type': 'function_call', 'call_id': 'technical-call',
                                                     'name': name, 'arguments': canonical(args)}]}


class ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store, cls.rows = EvidenceStore(), read(DEFAULT / 'cohort.json')

    def setUp(self):
        guard = patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline only'))
        guard.start()
        self.addCleanup(guard.stop)

    def case(self, index=2, mode='assumed_static_context_at_purchase'):
        r = self.rows[index]
        return self.store.case(r['order_id'], r['event_id'], mode)

    def saved(self):
        case = self.case()
        result = read(LIVE / 'three_agents.json')
        # Regenerate all saved, source-bound evidence through original tools.
        initial_evidence(case)
        case.compare_declared_peer_groups('transport', 'same_product_peers')
        case.check_temporal_and_comparison_contract()
        return case, result

    def test_saved_requests_exact_roundtrip_and_verifier_no_overhead(self):
        for line in (LIVE / 'requests.jsonl').read_text().splitlines():
            row = json.loads(line)
            original = expand_v1(row['payload'])
            packed, metrics = compact_payload(original)
            self.assertEqual(canonical(expand_payload(packed)), canonical(original))
            self.assertLessEqual(metrics['compact_utf8_bytes'], metrics['evidence_v1_utf8_bytes'])
            self.assertEqual(packed['tools'], original['tools'])
            self.assertIsNone(metrics['official_input_tokens'])

    def test_malformed_catalog_rows_rejected_and_nulls_preserved(self):
        table = {'columns': ['id', 'metadata_kind', 'code', 'stage', 'field'],
                 'groups': [[['real-source-ref'], [['selection', 'unknown', 'promise_basis_unverified', None, None]]]]}
        self.assertIsNone(expand_table('metadata_catalog', table)[0]['choices']['selection']['stage'])
        table['groups'][0][1].append(table['groups'][0][1][0])
        with self.assertRaisesRegex(ValueError, 'duplicate'):
            expand_table('metadata_catalog', table)
        with self.assertRaisesRegex(ValueError, 'columns'):
            expand_table('claim_catalog', table)

    def test_saved_correct_but_incomplete_response_is_flagged_without_rewriting(self):
        case, saved = self.saved()
        original = deepcopy(saved)
        result = annotate(case, saved)
        self.assertEqual(result['source_verification_status'], 'evidence_checked')
        self.assertEqual(result['explanation_status'], 'incomplete_warning_coverage')
        self.assertEqual(result['warning_coverage']['missing_stages'], ['transport'])
        self.assertEqual(source_metrics(case, result)['source_supported_items'], 12)
        self.assertEqual(saved, original)
        self.assertEqual(result['published_claims'], saved['published_claims'])

    def test_recommendations_do_not_replace_warning_and_rules_cover(self):
        case = self.case()
        result = run_rules(case)
        self.assertEqual(coverage(case, result)['fraction'], 1)
        result['published_claims'] = []
        self.assertEqual(coverage(case, result)['fraction'], 0)

    def test_wrong_value_and_support_cannot_count_as_warning(self):
        case = self.case()
        result = run_rules(case)
        item = next(i for i in result['published_claims'] if i['field'] == 'long_stage_warning' and i['value'] is True)
        item['value'] = False
        item['code_gate'] = 'passed'
        self.assertEqual(coverage(case, result)['fraction'], 0)
        item['value'] = True
        item['supporting_evidence'] = {}
        self.assertEqual(coverage(case, result)['fraction'], 0)
        result['case_key'] += '-wrong'
        with self.assertRaisesRegex(ValueError, 'frozen'):
            coverage(case, result)

    def test_no_expected_warning_is_not_one_hundred_percent(self):
        case = self.case(mode='verified_availability_only')
        result = annotate(case, run_rules(case))
        self.assertIsNone(result['warning_coverage']['fraction'])
        self.assertEqual(result['warning_coverage']['status'], 'not_applicable')

    def test_both_engines_repair_source_true_warnings_with_actual_verifier_step(self):
        for arm in KEYS:
            case = self.case()
            provider = RepairFixture(arm)
            result = run_review(case, provider, arm)
            self.assertEqual(result['coverage_repair']['status'], 'completed', result.get('coverage_repair'))
            self.assertEqual(result['warning_coverage']['fraction'], 1)
            self.assertEqual(source_metrics(case, result)['source_supported_fraction'], 1)
            self.assertIsNone(source_metrics(case, result)['independent_accuracy'])
            self.assertFalse(source_metrics(case, result)['model_performance_eligible'])
            self.assertIn('pre_repair_publication', result)
            self.assertTrue(result['verified_notes'])
            self.assertEqual(len(result['raw_agent_outputs']), len(provider.records))
            requests = {r['request_id'] for r in provider.records}
            self.assertEqual({r['request_id'] for r in result['trace'] if r['kind'] == 'model_request'}, requests)
            self.assertTrue(all(r['run_key'].endswith(VERSION) for r in provider.records))
            self.assertLessEqual(max(result['request_counts'].values()), 4)
            self.assertLessEqual(len(provider.records), 12 if arm == 'three_agents' else 4)
            if arm == 'three_agents':
                self.assertEqual(result['raw_agent_outputs'][-1]['phase'], 'coverage_verification')
                self.assertTrue(all(i['verifier_verdict']['verdict'] == 'accept' for i in result['published_claims']))

    def test_persistent_omission_does_not_spend_verifier_turn(self):
        result = run_review(self.case(), RepairFixture('three_agents', omit=True), 'three_agents')
        self.assertEqual(result['explanation_status'], 'incomplete_warning_coverage')
        self.assertEqual(result['coverage_repair']['status'], 'incomplete')
        self.assertEqual(result['request_counts']['evidence_verifier'], 2)
        self.assertEqual(result['warning_coverage']['fraction'], 0)

    def test_rejected_repair_warning_cannot_publish(self):
        result = run_review(self.case(), RepairFixture('three_agents', reject=True), 'three_agents')
        self.assertEqual(result['explanation_status'], 'incomplete_warning_coverage')
        self.assertEqual(result['warning_coverage']['fraction'], 0)
        self.assertEqual(result['raw_agent_outputs'][-1]['phase'], 'coverage_verification')

    def test_refusal_or_api_failure_retains_original_source_checked_publication(self):
        for option in ('refusal', 'fail'):
            result = run_review(self.case(), RepairFixture('three_agents', **{option: True}), 'three_agents')
            self.assertEqual(result['coverage_repair']['status'], 'incomplete')
            self.assertEqual(result['explanation_status'], 'incomplete_warning_coverage')
            self.assertTrue(result['published_claims'])
            self.assertEqual(result['warning_coverage']['fraction'], 0)

    def test_exhausted_verifier_prevents_even_starting_repair(self):
        case, saved = self.saved()
        sender = ReviewProvider(RepairFixture('three_agents'), 'three_agents')
        sender.counts.update(process_analyst=1, context_analyst=2, evidence_verifier=4)
        result = repair(case, sender, annotate(case, saved))
        self.assertEqual(result['coverage_repair']['status'], 'unavailable_request_budget')
        self.assertEqual(sender.requests, [])

    def test_wrapper_enforces_shared_caps_including_failed_requests(self):
        provider = ReviewProvider(RepairFixture('single_agent'), 'single_agent')
        provider.counts['single_agent_baseline'] = 4
        with self.assertRaises(BudgetBlocked):
            provider.call(role='single_agent_baseline')
        self.assertEqual(provider.requests, [])

    def test_base_api_failure_does_not_trigger_extra_calls(self):
        class Failed:
            execution_kind = 'technical_scripted_test_not_AI_inference'
            def call(self, **request):
                raise ProviderUnavailable('Technical base failure')
        result = run_review(self.case(), Failed(), 'three_agents')
        self.assertEqual(result['explanation_status'], 'unavailable_api')
        self.assertEqual(sum(result['request_counts'].values()), 1)
        self.assertEqual(result['coverage_repair']['status'], 'base_run_unavailable')

    def test_twenty_real_orders_both_engines_no_detector_changes(self):
        for index in range(20):
            for arm in KEYS:
                with self.subTest(index=index, arm=arm):
                    case = self.case(index)
                    before = deepcopy(case.scores)
                    result = run_review(case, RepairFixture(arm), arm)
                    self.assertEqual(case.scores, before)
                    self.assertNotEqual(result['warning_coverage']['status'], 'incomplete', result.get('coverage_repair'))
                    self.assertEqual(source_metrics(case, result)['source_supported_fraction'], 1)
                    self.assertFalse(result['independent_accuracy_evaluation'])


if __name__ == '__main__':
    unittest.main()
