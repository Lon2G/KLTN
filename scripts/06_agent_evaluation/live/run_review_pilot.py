"""One approved live pair using the frozen review_v2 adapter; no automatic retry."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'review_v2'))
from runner import run_review, VERSION
from catalog_wire import expand_payload
from validate_review import OUTPUT as OFFLINE
from run_compact_pilot import LoggedProvider, COUNT_FIELDS, verify_receipt_trace
from run_smoke import (DEFAULT, PILOT, LEDGER, ROOT, INDEX, KEYS, BudgetBlocked, BudgetLedger,
                       EvidenceStore, accounting, canonical, digest, initial_evidence, local_key,
                       read, receipts, save, sha, source_metrics, validate_protocol, verify_manifest)

DESTINATION = ROOT / 'data/experiments/olist_agent_live_review_v2_case2'
CAP_NANOUSD = 21_600_000
RUN_KEYS = {arm: base + '-' + VERSION for arm, base in KEYS.items()}
PRICE_CHECK = {'checked_on': '2026-11-07', 'source': 'https://developers.openai.com/api/docs/pricing',
               'model': 'gpt-6-luna', 'service_tier': 'default',
               'nanousd_per_token': {'input_nanousd_per_token': 100, 'cached_input_nanousd_per_token': 10,
                                    'cache_write_nanousd_per_token': 125, 'output_nanousd_per_token': 500}}


def check_policy(policy):
    if (policy['max_input_tokens_per_request'] != 6000 or policy['max_output_tokens_per_request'] != 1200 or
            policy['models']['gpt-6-luna'] != PRICE_CHECK['nanousd_per_token']):
        raise BudgetBlocked('Policy differs from the approved token limits or checked prices')
    rates = policy['models']['gpt-6-luna']
    reservation = 6000 * max(v for k, v in rates.items() if k != 'output_nanousd_per_token') + 1200 * rates['output_nanousd_per_token']
    if reservation * 16 > CAP_NANOUSD:
        raise BudgetBlocked('Worst-case cost exceeds the approved pair cap')
    return reservation


class TrialProvider(LoggedProvider):
    """Additional persistent scope and combined budget limits for this approval."""
    def __init__(self, ledger, key, destination, case_key):
        super().__init__(ledger, key, destination)
        self.case_key = case_key
        self.reservation = check_policy(ledger._policy)

    def call(self, **request):
        arm = next((a for a, k in RUN_KEYS.items() if k == request['run_key']), None)
        allowed = {'single_agent_baseline'} if arm == 'single_agent' else {'process_analyst', 'context_analyst', 'evidence_verifier'}
        if arm is None or request['case_key'] != self.case_key or request['role'] not in allowed:
            raise BudgetBlocked('Request is outside the approved case, configuration or role')
        if request['payload']['model'] != PRICE_CHECK['model']:
            raise BudgetBlocked('Unapproved model')
        rows = [r for key in RUN_KEYS.values() for r in receipts(self.ledger, key, self.case_key)]
        used = sum(r['actual_nanousd'] or 0 for r in rows)
        held = sum(r['reserved_nanousd'] for r in rows if r['status'] in {'reserved', 'in_flight'})
        per_arm = [r for r in rows if r['request_id'] in {
            x[0] for x in self.ledger.connection.execute('SELECT request_id FROM budget_requests WHERE run_key=?', (request['run_key'],))}]
        if (len(rows) >= 16 or len(per_arm) >= (4 if arm == 'single_agent' else 12) or
                sum(r['role'] == request['role'] for r in per_arm) >= 4 or
                used + held + self.reservation > CAP_NANOUSD or any(r['request_id'] == request['request_id'] for r in rows)):
            raise BudgetBlocked('Approved pair request/cost limit reached; no repeat dispatch')
        return super().call(**request)


def write_manifest():
    frozen = read(OFFLINE / 'manifest.json')
    sources = dict(frozen['source_files'])
    sources.update({str(p.relative_to(ROOT)): digest(p) for p in OFFLINE.iterdir() if p.is_file()})
    implementation = dict(frozen['implementation_hashes'])
    for p in (Path(__file__).resolve(), ROOT / 'tests/test_agent_review_live.py'):
        implementation[str(p.relative_to(ROOT))] = digest(p)
    save(DESTINATION / 'manifest.json', {'source_files': sources, 'implementation_hashes': implementation,
         'output_hashes': {p.name: digest(p) for p in DESTINATION.iterdir() if p.is_file()}})


def run():
    for directory in (DEFAULT, PILOT, OFFLINE):
        verify_manifest(directory)
    protocol = read(DEFAULT / 'protocol.json')
    validate_protocol(protocol)
    row = read(DEFAULT / 'cohort.json')[INDEX]
    if row['case_key'] != 'olist-04029aa370b3a937565a' or protocol['model'] != PRICE_CHECK['model']:
        raise ValueError('Unexpected case or model')
    if DESTINATION.exists() or not LEDGER.is_file():
        raise ValueError('Existing trial or missing shared ledger; refusing another dispatch')
    check_policy(read(ROOT / 'templates/multi_agent_v1/budget.json'))
    store = EvidenceStore()
    case = store.case(row['order_id'], row['event_id'], protocol['mode'])
    if sha(initial_evidence(case)[0]) != row['input_fingerprint']:
        raise ValueError('Case input differs from frozen comparison')
    key = local_key()
    os.environ.setdefault('SSL_CERT_FILE', '/etc/ssl/cert.pem')
    before_hash = digest(LEDGER)
    DESTINATION.mkdir()
    shutil.copyfile(LEDGER, DESTINATION / 'budget_before.sqlite')
    if digest(DESTINATION / 'budget_before.sqlite') != before_hash:
        raise ValueError('Ledger changed while archiving')
    with BudgetLedger(LEDGER) as ledger:
        before = ledger.snapshot()
        if before['breached'] or before['reserved_nanousd'] or before['remaining_nanousd'] < CAP_NANOUSD:
            raise BudgetBlocked('Shared ledger cannot fund the approved pair')
        if any(receipts(ledger, key, row['case_key']) for key in RUN_KEYS.values()):
            raise ValueError('Prior attempt already exists for this version')
        save(DESTINATION / 'plan.json', {'case': row, 'review_version': VERSION, 'profile': 'native_bounded',
             'model': protocol['model'], 'mode': protocol['mode'], 'run_keys': RUN_KEYS,
             'max_requests': {'single_agent': 4, 'three_agents': 12}, 'cap_nanousd': CAP_NANOUSD,
             'budget_before': before, 'ledger_before_sha256': before_hash, 'price_check': PRICE_CHECK,
             'implementation_sha256': digest(Path(__file__)), 'phase': 'preflight_then_one_live_pair',
             'comparison_scope': 'unequal_native_caps_not_isolated_effect_of_agent_count'})
        report = {'case_key': row['case_key'], 'case_index': INDEX, 'review_version': VERSION,
                  'profile': 'native_bounded', 'preflight_tokens': {}, 'arms': {}, 'status': 'running',
                  'scope': 'known_development_case_engineering_smoke', 'independent_accuracy': None,
                  'human_review_status': 'pending'}
        try:
            provider = TrialProvider(ledger, key, DESTINATION, row['case_key'])
            prepared = read(OFFLINE / 'prepared_requests.json')
            for arm, role in [('single_agent', 'single_agent_baseline'), ('three_agents', 'evidence_verifier')]:
                saved = [r for r in prepared if r['role'] == role][-1]
                provider.active = {'phase': 'saved_history_preflight', 'role': role,
                                   'request_id': saved['historical_request_id'], 'arm': arm}
                reply = provider._post('responses/input_tokens', {k: saved['payload'][k] for k in COUNT_FIELDS})
                count = reply.get('input_tokens')
                if reply.get('object') != 'response.input_tokens' or type(count) is not int or count < 0:
                    raise ValueError('Invalid official token count')
                report['preflight_tokens'][arm] = count
            save(DESTINATION / 'preflight_counts.json', report['preflight_tokens'])
            if max(report['preflight_tokens'].values()) > 6000:
                raise BudgetBlocked('Prepared saved history still exceeds the approved token cap')
            for arm in ('three_agents', 'single_agent'):
                print('Starting', arm, flush=True)
                case = store.case(row['order_id'], row['event_id'], protocol['mode'])
                if sha(initial_evidence(case)[0]) != row['input_fingerprint']:
                    raise ValueError('Frozen case changed')
                result = run_review(case, provider, arm)
                save(DESTINATION / (arm + '.json'), result)
                rows = receipts(ledger, RUN_KEYS[arm], row['case_key'])
                save(DESTINATION / (arm + '_receipts.json'), rows)
                verify_receipt_trace(result, rows)
                report['arms'][arm] = {'accounting': accounting(rows), 'metrics': source_metrics(case, result),
                    'warning_coverage': result['warning_coverage'], 'coverage_repair': result['coverage_repair'],
                    'seconds': result['explanation_seconds'], 'error': result.get('error'),
                    'metadata_verification_complete': result.get('metadata_verification_complete')}
                print(arm, canonical(report['arms'][arm]), flush=True)
            report['status'] = 'pair_finished_see_arm_statuses'
        except Exception as exc:
            report.update(status='trial_stopped', error=f'{type(exc).__name__}: {exc}')
            print('Trial stopped:', report['error'], flush=True)
        finally:
            rows = [r for key in RUN_KEYS.values() for r in receipts(ledger, key, row['case_key'])]
            save(DESTINATION / 'all_receipts.json', rows)
            report.update(budget_after=ledger.snapshot(), total_accounting=accounting(rows))
            save(DESTINATION / 'summary.json', report)
    write_manifest()
    print('Saved', DESTINATION, flush=True)


class Replay:
    execution_kind = 'offline_saved_receipt_replay_not_new_AI_inference'
    def __init__(self, requests, rows):
        self.requests = iter(requests)
        self.rows = {r['request_id']: r for r in rows}

    def call(self, **request):
        if request != next(self.requests):
            raise ValueError('Request differs from saved wire payload')
        r = self.rows[request['request_id']]
        if r['status'] == 'cancelled_unsent':
            raise BudgetBlocked('Serialized input exceeds token cap or count response is invalid')
        if r['status'] != 'settled' or r['response']['status'] != 'completed':
            raise ValueError('Receipt needs separate incomplete/uncertain outcome review')
        return deepcopy(r['response'])


def audit():
    for directory in (DEFAULT, PILOT, OFFLINE, DESTINATION):
        verify_manifest(directory)
    plan, summary = read(DESTINATION / 'plan.json'), read(DESTINATION / 'summary.json')
    request_path = DESTINATION / 'requests.jsonl'
    requests = [json.loads(s) for s in request_path.read_text().splitlines()] if request_path.exists() else []
    stored = {r['request_id']: r for r in read(DESTINATION / 'all_receipts.json')}
    with closing(sqlite3.connect(LEDGER.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        db.execute('ATTACH DATABASE ? AS historical', ((DESTINATION / 'budget_before.sqlite').resolve().as_uri() + '?mode=ro',))
        for table in ('budget_requests', 'provider_receipts'):
            if db.execute(f'SELECT * FROM historical.{table} EXCEPT SELECT * FROM main.{table} LIMIT 1').fetchone():
                raise ValueError('Historical ledger changed')
        for request in requests:
            expand_payload(request['payload'])
            row = stored[request['request_id']]
            if sha(request['payload']) != row['payload_sha256']:
                raise ValueError('Payload differs from receipt fingerprint')
            actual = db.execute('SELECT status,actual_nanousd,payload_sha256 FROM budget_requests WHERE request_id=?', (request['request_id'],)).fetchone()
            if actual != (row['status'], row['actual_nanousd'], row['payload_sha256']):
                raise ValueError('Receipt differs from ledger')
            raw = db.execute('SELECT response_json FROM provider_receipts WHERE request_id=?', (request['request_id'],)).fetchone()
            if (json.loads(raw[0]) if raw else None) != row['response']:
                raise ValueError('Actual response differs from export')
    store, metrics = EvidenceStore(), {}
    with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Audit is offline')):
        for arm in summary['arms']:
            saved, rows = read(DESTINATION / (arm + '.json')), read(DESTINATION / (arm + '_receipts.json'))
            relevant = [r for r in requests if r['run_key'] == RUN_KEYS[arm]]
            provider = Replay(relevant, rows)
            row = plan['case']
            case = store.case(row['order_id'], row['event_id'], plan['mode'])
            initial_evidence(case)
            reproduced = run_review(case, provider, arm)
            for field in ('detector', 'explanation_status', 'source_verification_status', 'warning_coverage',
                          'coverage_repair', 'raw_agent_outputs', 'published_claims', 'verified_notes',
                          'verified_recommended_checks', 'rejected_claims', 'rejected_metadata',
                          'contract_violations', 'evidence', 'request_counts'):
                if reproduced[field] != saved[field]:
                    raise ValueError('Replay differs on ' + field)
            if next(provider.requests, None) is not None:
                raise ValueError('Unused requests')
            verify_receipt_trace(saved, rows)
            metrics[arm] = source_metrics(case, saved)
    if len(requests) > 16 or summary['total_accounting']['settled_nanousd'] + summary['total_accounting']['held_nanousd'] > CAP_NANOUSD:
        raise ValueError('Trial exceeds approved limits')
    return {'verified': True, 'arms': metrics, 'new_API_calls': 0, 'requests_verified': len(requests)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--live', action='store_true')
    group.add_argument('--verify', action='store_true')
    if parser.parse_args().live:
        run()
    else:
        print(canonical(audit()))
