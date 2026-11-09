"""One user-approved replacement run for a v4 slot interrupted by a local technical failure.

Rule (applies to every batch): a slot whose run was interrupted for a technical reason
unrelated to the arm (here: a request sent without a stored receipt) is run once more under
a separate replacement run key. The original slot file, receipts and ledger rows are kept.

    .venv/bin/python -B scripts/06_agent_evaluation/live/run_replacement_v4.py --live --slot <slot>
    .venv/bin/python -B scripts/06_agent_evaluation/live/run_replacement_v4.py --verify
"""
import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'review_v3'))
sys.path.insert(0, str(HERE))
import runner_v4  # noqa: E402
import run_comparison_v4 as batch  # noqa: E402
from run_smoke import (DEFAULT, BudgetBlocked, BudgetLedger, EvidenceStore, accounting, canonical,  # noqa: E402
                       digest, initial_evidence, local_key, read, receipts, save, sha, source_metrics)

SUFFIX = 'replacement1'
OUT = batch.DESTINATION / 'replacements'
MAX_USD = 0.01  # hard cap for one replacement slot, within the user-approved 2 USD


_BASE_RUN_KEY = runner_v4.run_key  # unpatched v4 key


def replacement_key(arm, profile):
    return _BASE_RUN_KEY(arm, profile) + '-' + SUFFIX


class ReplacementSender(runner_v4.TaggedSender):
    def call(self, **request):
        if not self.can_call([request['role']]):
            raise runner_v4.v2.BudgetBlocked('Profile request cap reached')
        source = deepcopy(request['payload'])
        if request['role'] in {'process_analyst', 'single_agent_baseline'}:
            source['instructions'] += runner_v4.COVERAGE_INSTRUCTIONS
        payload, metrics = runner_v4.compact_payload(source)
        forwarded = {**request, 'payload': payload, 'run_key': replacement_key(self.arm, self.profile),
                     'request_id': sha({'version': runner_v4.VERSION, 'profile': self.profile, 'replacement': SUFFIX,
                                        'source_request_id': request['request_id']})}
        self.counts[request['role']] += 1
        row = {'source_request_id': request['request_id'], 'request_id': forwarded['request_id'],
               'run_key': forwarded['run_key'], 'role': request['role'], **metrics, 'attempt_status': 'pending'}
        self.requests.append(row)
        try:
            response = self.provider.call(**forwarded)
            row.update(attempt_status='response_received', response_id=response.get('id'))
            return response
        except (runner_v4.v2.BudgetBlocked, runner_v4.v2.ProviderUnavailable, ValueError, TypeError, KeyError) as exc:
            row.update(attempt_status=type(exc).__name__)
            raise


def run_replacement(case, provider, arm, profile):
    with patch.object(runner_v4, 'TaggedSender', ReplacementSender), \
         patch.object(runner_v4, 'run_key', replacement_key):
        return runner_v4.run_review(case, provider, arm, profile)


class ReplacementProvider(batch.BatchProvider):
    def __init__(self, ledger, key, destination, slot, post=None):
        super().__init__(ledger, key, destination, int(MAX_USD * 1e9), post)
        self.keys = {replacement_key(slot['arm'], slot['profile']): (slot['arm'], slot['profile'])}
        self.case_keys = {slot['case_key']}


def interrupted(saved):
    """Technical interruption: a request left in flight without a stored receipt."""
    return any(r['status'] == 'in_flight' and not r['response'] for r in saved['receipts'])


def live(slot_name):
    original = read(batch.DESTINATION / 'slots' / (slot_name + '.json'))
    if not interrupted(original):
        raise ValueError('Slot was not technically interrupted; replacement not allowed')
    s = original['slot']
    OUT.mkdir(exist_ok=True)
    out = OUT / (slot_name + '.json')
    if out.exists():
        raise ValueError('Replacement already exists; no second replacement')
    protocol = read(DEFAULT / 'protocol.json')
    row = {r['case_key']: r for r in read(DEFAULT / 'cohort.json')}[s['case_key']]
    store = EvidenceStore()
    case = store.case(row['order_id'], row['event_id'], protocol['mode'])
    if sha(initial_evidence(case)[0]) != row['input_fingerprint']:
        raise ValueError('Frozen case input changed')
    case = store.case(row['order_id'], row['event_id'], protocol['mode'])
    os.environ.setdefault('SSL_CERT_FILE', '/etc/ssl/cert.pem')
    with BudgetLedger(batch.LEDGER, policy=read(batch.POLICY)) as ledger:
        provider = ReplacementProvider(ledger, local_key(), batch.DESTINATION, s)
        result = run_replacement(case, provider, s['arm'], s['profile'])
        rows = receipts(ledger, replacement_key(s['arm'], s['profile']), s['case_key'])
        save(out, {'slot': {**s, 'replacement_of': slot_name, 'replacement_run_key': replacement_key(s['arm'], s['profile'])},
                   'reason': 'technical_interruption_request_in_flight_without_receipt',
                   'original_status': original['result']['explanation_status'],
                   'ran_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'result': result, 'receipts': rows,
                   'accounting': accounting(rows), 'metrics': source_metrics(case, result)})
        print(slot_name, '->', result['explanation_status'], canonical(accounting(rows)))


class LedgerReplay(batch.Replay):
    """batch.Replay plus the ledger's behaviour for a request left in flight without a receipt."""
    def call(self, **request):
        r = self.rows.get(request.get('request_id'))
        if r is not None and r['status'] == 'in_flight' and not r['response']:
            if request != next(self.requests):
                raise ValueError('Request differs from saved wire payload')
            raise BudgetBlocked('Prior request has no usable receipt; refusing duplicate dispatch')
        return super().call(**request)


def verify_batch():
    """Full offline audit of the 80 v4 slots with ledger-faithful replay of interrupted requests."""
    with patch.object(batch, 'Replay', LedgerReplay):
        return batch.audit()


def verify():
    requests = [json.loads(x) for x in (batch.DESTINATION / 'requests.jsonl').read_text().splitlines()]
    store, protocol = EvidenceStore(), read(DEFAULT / 'protocol.json')
    cohort = {r['case_key']: r for r in read(DEFAULT / 'cohort.json')}
    checked = 0
    with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Audit is offline')):
        for p in sorted(OUT.glob('*.json')) if OUT.exists() else []:
            saved = read(p)
            s = saved['slot']
            relevant = [r for r in requests if r['run_key'] == s['replacement_run_key'] and r['case_key'] == s['case_key']]
            row = cohort[s['case_key']]
            case = store.case(row['order_id'], row['event_id'], protocol['mode'])
            reproduced = run_replacement(case, batch.Replay(relevant, saved['receipts']), s['arm'], s['profile'])
            for field in ('explanation_status', 'published_claims', 'verified_notes', 'verified_recommended_checks',
                          'raw_agent_outputs', 'request_counts', 'guard_log'):
                if reproduced.get(field) != saved['result'].get(field):
                    raise ValueError(f'Replacement replay differs on {field}')
            checked += 1
    return {'verified_replacements': checked, 'new_API_calls': 0}


if __name__ == '__main__':
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument('--live', action='store_true')
    g.add_argument('--verify', action='store_true')
    ap.add_argument('--slot')
    a = ap.parse_args()
    if a.live:
        if not a.slot:
            ap.error('--live requires --slot')
        live(a.slot)
    else:
        print(canonical({'batch': verify_batch(), 'replacement': verify()}))
