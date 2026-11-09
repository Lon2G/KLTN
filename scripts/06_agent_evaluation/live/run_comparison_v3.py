"""Preregistered 20-case AI comparison with the review_v3 loop guard.

Slots: 20 frozen cases x {three_agents, single_agent} x {native_bounded, matched_4} = 80.
The rules control already exists (olist_agent_comparison_v1, 0 API calls).

Modes
  --plan                     offline: print scope, caps and worst-case cost; no network.
  --live --approve-usd X     dispatch; refuses unless X covers the cap written in the plan.
                             Re-running resumes: finished slots are skipped and the shared
                             ledger refuses any duplicate request id.
  --verify                   offline: replay every slot from saved receipts.

No automatic retry of failed inference, no model substitution, no best-of-many.
The API key is read locally and never written to logs.
"""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'review_v3'))
from runner_v3 import run_review, run_key, PROFILES, VERSION  # noqa: E402
from catalog_wire import expand_payload  # noqa: E402
from run_compact_pilot import LoggedProvider  # noqa: E402
from run_smoke import (DEFAULT, LEDGER, ROOT, BudgetBlocked, BudgetLedger, EvidenceStore, ProviderUnavailable,  # noqa: E402
                       accounting, canonical, digest, initial_evidence, local_key, read, receipts, save, sha,
                       source_metrics, validate_protocol, verify_manifest)

DESTINATION = ROOT / 'data/experiments/olist_agent_comparison_v3'
ARMS = ('three_agents', 'single_agent')
MODEL = 'gpt-6-luna'
ALLOWED_ROLES = {'three_agents': {'process_analyst', 'context_analyst', 'evidence_verifier'},
                 'single_agent': {'single_agent_baseline'}}
PRICE_CHECK = {'checked_on': '2026-11-07', 'source': 'https://developers.openai.com/api/docs/pricing',
               'note': 'Re-check prices on the provider page before approving a live run.'}
MAX_CONSECUTIVE_UNAVAILABLE = 3


def overwrite(path, value):
    """Only for derived files that are rebuilt on resume (summary, manifest)."""
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    tmp.replace(path)


def slots():
    cohort = read(DEFAULT / 'cohort.json')
    out = []
    for row in cohort:  # interleave arms/profiles per case to limit time-drift bias
        for profile in PROFILES:
            for arm in ARMS:
                out.append({'slot': f"{row['case_key']}__{arm}__{profile}", 'case_index': row['case_index'],
                            'case_key': row['case_key'], 'arm': arm, 'profile': profile})
    return out


def per_request_reservation(policy):
    rates = policy['models'][MODEL]
    return (policy['max_input_tokens_per_request'] * max(v for k, v in rates.items() if k != 'output_nanousd_per_token')
            + policy['max_output_tokens_per_request'] * rates['output_nanousd_per_token'])


def plan():
    policy = read(ROOT / 'templates/multi_agent_v1/budget.json')
    reserve = per_request_reservation(policy)
    planned = slots()
    # Worst case: every slot uses its full cap, each role may add one cap-fallback (cancelled before
    # inference, released), and each slot may add the bounded coverage repair inside the same caps.
    max_inferences = sum(PROFILES[s['profile']][s['arm']] for s in planned)
    worst = max_inferences * reserve
    with closing(sqlite3.connect(LEDGER.resolve().as_uri() + '?mode=ro', uri=True)) as db:
        spent = db.execute("SELECT COALESCE(SUM(actual_nanousd),0) FROM budget_requests WHERE status='settled'").fetchone()[0]
        held = db.execute("SELECT COALESCE(SUM(reserved_nanousd),0) FROM budget_requests WHERE status IN ('reserved','in_flight')").fetchone()[0]
        cases = {r[0] for r in db.execute('SELECT DISTINCT case_key FROM budget_requests')}
    cohort_keys = {s['case_key'] for s in planned}
    return {'version': VERSION, 'model': MODEL, 'slots': len(planned),
            'profiles': PROFILES, 'max_input_tokens_per_request': policy['max_input_tokens_per_request'],
            'max_output_tokens_per_request': policy['max_output_tokens_per_request'],
            'max_inference_requests': max_inferences,
            'worst_case_usd': worst / 1e9, 'cap_usd': round(worst / 1e9 + 0.0005, 4),
            'ledger_remaining_usd': (policy['api_cap_usd_nanounits'] - spent - held) / 1e9,
            'distinct_cases_after_run': len(cases | cohort_keys), 'pilot_case_limit': policy['pilot_case_limit'],
            'reference_cost_usd_07_11_pair': 0.004518515, 'price_check': PRICE_CHECK,
            'scope': 'development_comparison_of_explanation_workflows_not_bottleneck_accuracy'}


class BatchProvider(LoggedProvider):
    """Scope guard for one approved batch: model, run keys, roles, per-slot caps and total spend."""

    def __init__(self, ledger, key, destination, cap_nanousd, post=None):
        super().__init__(ledger, key, destination)
        if post is not None:  # technical test transport only
            self.post, self.execution_kind = post, 'technical_test_transport_not_live_inference'
        self.cap = cap_nanousd
        self.reserve = per_request_reservation(ledger._policy)
        self.keys = {run_key(a, p): (a, p) for a in ARMS for p in PROFILES}
        self.case_keys = {s['case_key'] for s in slots()}

    def spent_and_held(self):
        rows = [r for k in self.keys for ck in self.case_keys for r in receipts(self.ledger, k, ck)]
        used = sum(r['actual_nanousd'] or 0 for r in rows)
        held = sum(r['reserved_nanousd'] for r in rows if r['status'] in {'reserved', 'in_flight'})
        return used, held

    def call(self, **request):
        scope = self.keys.get(request['run_key'])
        if scope is None or request['case_key'] not in self.case_keys or request['role'] not in ALLOWED_ROLES[scope[0]]:
            raise BudgetBlocked('Request is outside the approved batch scope')
        if request['payload']['model'] != MODEL:
            raise BudgetBlocked('Unapproved model')
        sent = [r for r in receipts(self.ledger, request['run_key'], request['case_key']) if r['status'] != 'cancelled_unsent']
        if len(sent) >= PROFILES[scope[1]][scope[0]]:
            raise BudgetBlocked('Slot request cap reached')
        used, held = self.spent_and_held()
        if used + held + self.reserve > self.cap:
            raise BudgetBlocked('Approved batch spend cap reached')
        return super().call(**request)


def run(approve_usd, post=None, limit=None):
    for directory in (DEFAULT,):
        verify_manifest(directory)
    protocol = read(DEFAULT / 'protocol.json')
    validate_protocol(protocol)
    if protocol['model'] != MODEL:
        raise ValueError('Unexpected model in frozen protocol')
    scope = plan()
    if approve_usd < scope['cap_usd']:
        raise BudgetBlocked(f"Approval {approve_usd} USD is below the batch cap {scope['cap_usd']} USD")
    if scope['distinct_cases_after_run'] > scope['pilot_case_limit']:
        raise BudgetBlocked('Batch would exceed the distinct-case limit')
    cap = int(scope['cap_usd'] * 1e9)
    DESTINATION.mkdir(exist_ok=True)
    (DESTINATION / 'slots').mkdir(exist_ok=True)
    if not (DESTINATION / 'plan.json').exists():
        shutil.copyfile(LEDGER, DESTINATION / 'budget_before.sqlite')
        save(DESTINATION / 'plan.json', {**scope, 'approved_usd': approve_usd, 'approved_at': time.strftime('%Y-%m-%d %H:%M:%S'),
                                         'ledger_before_sha256': digest(LEDGER),
                                         'implementation_sha256': {p: digest(ROOT / p) for p in IMPLEMENTATION}})
    else:
        saved = read(DESTINATION / 'plan.json')
        if saved['implementation_sha256'] != {p: digest(ROOT / p) for p in IMPLEMENTATION}:
            raise ValueError('Implementation changed since the batch started; start a new version instead')
    key = 'technical-test-no-key' if post is not None else local_key()
    os.environ.setdefault('SSL_CERT_FILE', '/etc/ssl/cert.pem')
    store, unavailable = EvidenceStore(), 0
    cohort = {r['case_key']: r for r in read(DEFAULT / 'cohort.json')}
    with BudgetLedger(LEDGER) as ledger:
        provider = BatchProvider(ledger, key, DESTINATION, cap, post)
        for s in slots()[:limit]:
            out = DESTINATION / 'slots' / (s['slot'] + '.json')
            if out.exists():
                continue
            row = cohort[s['case_key']]
            case = store.case(row['order_id'], row['event_id'], protocol['mode'])
            if sha(initial_evidence(case)[0]) != row['input_fingerprint']:
                raise ValueError('Frozen case input changed: ' + s['slot'])
            case = store.case(row['order_id'], row['event_id'], protocol['mode'])
            print('Slot', s['slot'], flush=True)
            result = run_review(case, provider, s['arm'], s['profile'])
            rows = receipts(ledger, run_key(s['arm'], s['profile']), s['case_key'])
            save(out, {'slot': s, 'result': result, 'receipts': rows, 'accounting': accounting(rows),
                       'metrics': source_metrics(case, result)})
            print(' ->', result['explanation_status'], canonical(accounting(rows)), flush=True)
            unavailable = unavailable + 1 if result['explanation_status'] == 'unavailable_api' else 0
            if unavailable >= MAX_CONSECUTIVE_UNAVAILABLE:
                print('Stopping: provider unavailable on consecutive slots', flush=True)
                break
            if ledger.snapshot()['breached']:
                print('Stopping: ledger frozen', flush=True)
                break
        overwrite(DESTINATION / 'summary.json', summarize(ledger))
    write_manifest()


def summarize(ledger=None):
    rows = []
    for s in slots():
        p = DESTINATION / 'slots' / (s['slot'] + '.json')
        if not p.exists():
            rows.append({**s, 'status': 'not_run'})
            continue
        saved = read(p)
        r, acc = saved['result'], saved['accounting']
        rows.append({**s, 'status': r['explanation_status'], 'coverage_repair': r['coverage_repair']['status'],
                     'guard_forced_requests': r.get('guard_forced_requests'),
                     'model_responses': acc['model_responses_received'], 'settled_usd': acc['settled_nanousd'] / 1e9,
                     'seconds': r['explanation_seconds'], **{k: saved['metrics'].get(k) for k in (
                         'source_supported_fraction', 'warning_stage_coverage', 'missing_endpoint_coverage',
                         'timeline_conflict_coverage', 'distinct_published_items')}})
    done = [r for r in rows if r['status'] != 'not_run']
    return {'version': VERSION, 'slots': len(rows), 'finished_slots': len(done),
            'status_counts': {k: sum(r['status'] == k for r in done) for k in sorted({r['status'] for r in done})},
            'settled_usd': sum(r['settled_usd'] for r in done), 'rows': rows,
            'budget_after': ledger.snapshot() if ledger else None,
            'ranking': None, 'why_no_ranking': 'Automatic checks are gate consistency; usefulness needs independent review.',
            'independent_accuracy': None}


IMPLEMENTATION = ['scripts/06_agent_evaluation/live/run_comparison_v3.py',
                  'scripts/06_agent_evaluation/review_v3/runner_v3.py',
                  'scripts/06_agent_evaluation/review_v3/loop_guard.py',
                  'scripts/06_agent_evaluation/review_v2/runner.py',
                  'scripts/06_agent_evaluation/review_v2/catalog_wire.py',
                  'scripts/06_agent_evaluation/review_v2/warning_coverage.py',
                  'scripts/05_multi_agent/coordinator.py',
                  'scripts/05_multi_agent/provider.py',
                  'scripts/05_multi_agent/api_budget.py']


def write_manifest():
    overwrite(DESTINATION / 'manifest.json', {
        'implementation_hashes': {p: digest(ROOT / p) for p in IMPLEMENTATION},
        'output_hashes': {str(p.relative_to(DESTINATION)): digest(p) for p in sorted(DESTINATION.rglob('*'))
                          if p.is_file() and p.name != 'manifest.json'}})


class Replay:
    execution_kind = 'offline_saved_receipt_replay_not_new_AI_inference'

    def __init__(self, requests, rows):
        self.requests, self.rows = iter(requests), {r['request_id']: r for r in rows}

    def call(self, **request):
        if request != next(self.requests):
            raise ValueError('Request differs from saved wire payload')
        r = self.rows[request['request_id']]
        if r['status'] == 'cancelled_unsent':
            raise BudgetBlocked('Serialized input exceeds token cap or count response is invalid')
        if r['status'] != 'settled' or r['response']['status'] != 'completed':
            raise ProviderUnavailable('Receipt is not a completed response')
        return deepcopy(r['response'])


def audit():
    path = DESTINATION / 'requests.jsonl'
    requests = [json.loads(s) for s in path.read_text().splitlines()] if path.exists() else []
    store, cohort, checked = EvidenceStore(), {r['case_key']: r for r in read(DEFAULT / 'cohort.json')}, 0
    protocol = read(DEFAULT / 'protocol.json')
    with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Audit is offline')):
        for s in slots():
            p = DESTINATION / 'slots' / (s['slot'] + '.json')
            if not p.exists():
                continue
            saved = read(p)
            key = run_key(s['arm'], s['profile'])
            relevant = [r for r in requests if r['run_key'] == key and r['case_key'] == s['case_key']]
            for r in relevant:
                expand_payload(r['payload'])
            row = cohort[s['case_key']]
            case = store.case(row['order_id'], row['event_id'], protocol['mode'])
            reproduced = run_review(case, Replay(relevant, saved['receipts']), s['arm'], s['profile'])
            for field in ('detector', 'explanation_status', 'warning_coverage', 'coverage_repair', 'raw_agent_outputs',
                          'published_claims', 'verified_notes', 'verified_recommended_checks', 'rejected_claims',
                          'rejected_metadata', 'request_counts', 'guard_log'):
                if reproduced.get(field) != saved['result'].get(field):
                    raise ValueError(f"Replay differs on {field} in {s['slot']}")
            checked += 1
    return {'verified_slots': checked, 'new_API_calls': 0, 'requests_replayed': len(requests)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--plan', action='store_true')
    group.add_argument('--live', action='store_true')
    group.add_argument('--verify', action='store_true')
    parser.add_argument('--approve-usd', type=float)
    args = parser.parse_args()
    if args.plan:
        print(json.dumps(plan(), ensure_ascii=False, indent=2))
    elif args.live:
        if args.approve_usd is None:
            parser.error('--live requires --approve-usd')
        run(args.approve_usd)
    else:
        print(canonical(audit()))
