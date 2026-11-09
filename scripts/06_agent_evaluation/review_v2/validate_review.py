"""Offline audit of v2 transport and completeness on saved real AI responses."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
from unittest.mock import patch

from catalog_wire import compact_payload, expand_payload, canonical
from warning_coverage import annotate, INSTRUCTIONS
from runner import VERSION
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'live'))
from run_compact_pilot import audit, DESTINATION as LIVE, COMPACT
from evidence_wire import expand_payload as expand_v1, sha
from run_smoke import (ROOT, DEFAULT, PILOT, LEDGER, EvidenceStore, read, save, digest,
                       initial_evidence, source_metrics, verify_manifest)

OUTPUT = ROOT / 'data/experiments/olist_agent_review_v2_offline'
PARENTS = (DEFAULT, PILOT, COMPACT, LIVE)


def analyze():
    audit()  # Exact historical request/response replay plus read-only ledger audit.
    store = EvidenceStore()
    row = read(LIVE / 'plan.json')['case']
    prepared, sizes, annotations, metrics = [], [], {}, {}
    for line in (LIVE / 'requests.jsonl').read_text().splitlines():
        original_request = json.loads(line)
        old = original_request['payload']
        source = expand_v1(old)
        packed, size = compact_payload(source)
        if canonical(expand_payload(packed)) != canonical(source):
            raise ValueError('Source payload lost information')
        # Include the new policy instruction in the version prepared for a later trial.
        policy_source = deepcopy(source)
        if original_request['role'] in {'process_analyst', 'single_agent_baseline'}:
            policy_source['instructions'] += INSTRUCTIONS
        policy_packed, policy_size = compact_payload(policy_source)
        prepared.append({'historical_request_id': original_request['request_id'],
                         'role': original_request['role'], 'payload': policy_packed,
                         'purpose': 'prepared_only_not_dispatched'})
        sizes.append({'historical_request_id': original_request['request_id'], 'role': original_request['role'],
                      'old_wire_utf8_bytes': len(canonical(old).encode()),
                      'catalog_only_utf8_bytes': size['compact_utf8_bytes'],
                      'new_wire_with_policy_utf8_bytes': policy_size['compact_utf8_bytes'],
                      'exact_source_round_trip': True, 'official_new_input_tokens': None})
    for arm in ('three_agents', 'single_agent'):
        saved = read(LIVE / (arm + '.json'))
        case = store.case(row['order_id'], row['event_id'], saved['mode'])
        initial_evidence(case)
        # Replay only recorded local tool calls to rebuild the evidence registry.
        for event in saved['trace']:
            if event['kind'] == 'tool_call':
                record = getattr(case, event['tool'])(**event['arguments'])
                if record['evidence_id'] != event['evidence_id']:
                    raise ValueError('Historical local tool differs from source')
        annotations[arm] = annotate(case, saved)
        annotations[arm]['offline_annotation'] = {'new_model_response': False, 'historical_source': str(LIVE.relative_to(ROOT))}
        metrics[arm] = {'source_metrics': source_metrics(case, annotations[arm]),
                       'coverage': annotations[arm]['warning_coverage'],
                       'original_status': saved['explanation_status'],
                       'annotated_status': annotations[arm]['explanation_status']}
    total_old = sum(r['old_wire_utf8_bytes'] for r in sizes)
    total_new = sum(r['new_wire_with_policy_utf8_bytes'] for r in sizes)
    summary = {'version': VERSION, 'scope': 'offline_transport_and_saved_response_annotation',
               'saved_requests_checked': len(sizes), 'requests': sizes, 'historical_arms': metrics,
               'old_wire_utf8_bytes_total': total_old, 'new_wire_with_policy_utf8_bytes_total': total_new,
               'utf8_reduction_fraction': (total_old - total_new) / total_old,
               'official_new_input_tokens': None, 'input_token_cap': 6000,
               'new_API_calls': 0, 'new_model_responses': 0, 'cost_usd': 0,
               'live_v2_completion_verified': False, 'independent_accuracy': None}
    return {'summary.json': summary, 'prepared_requests.json': prepared,
            **{arm + '_annotated.json': r for arm, r in annotations.items()}}


def validate_review(destination=OUTPUT, verify=False):
    protected = {str(LEDGER.relative_to(ROOT)): digest(LEDGER)}
    for directory in PARENTS:
        verify_manifest(directory)
        manifest = read(directory / 'manifest.json')
        protected.update(manifest['source_files'])
        protected.update(manifest['implementation_hashes'])
        protected.update({str(p.relative_to(ROOT)): digest(p) for p in directory.rglob('*') if p.is_file()})
    if verify:
        verify_manifest(destination)
    elif destination.exists():
        raise ValueError('Refusing to overwrite a frozen offline snapshot')
    with patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Offline only')) as network, \
         patch('provider.ResponsesProvider.call', side_effect=AssertionError('No provider inference')):
        outputs = analyze()
        if network.call_count:
            raise ValueError('Unexpected network attempt')
    if any(digest(ROOT / name) != value for name, value in protected.items()):
        raise ValueError('A source, historical result or budget ledger changed')
    if verify:
        for name, value in outputs.items():
            if read(destination / name) != value:
                raise ValueError('Offline snapshot differs on ' + name)
        return {'verified': True, 'files_checked': len(protected), 'new_API_calls': 0}
    destination.mkdir()
    for name, value in outputs.items():
        save(destination / name, value)
    save(destination / 'preservation.json', {'before_after_sha256': protected, 'all_unchanged': True})
    sources = {k: v for k, v in protected.items() if k != str(LEDGER.relative_to(ROOT))}
    implementation = list(Path(__file__).parent.glob('*.py')) + [ROOT / 'tests/test_agent_review_v2.py']
    save(destination / 'manifest.json', {'source_files': sources,
         'implementation_hashes': {str(p.relative_to(ROOT)): digest(p) for p in implementation},
         'output_hashes': {p.name: digest(p) for p in destination.iterdir() if p.is_file()}})
    return outputs['summary.json']


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    print(canonical(validate_review(args.output, args.verify)))
