"""Versioned serialization and bounded completeness repair over frozen engines.

No network is initiated here; the caller supplies the provider. A repaired
multi-agent draft is published only after a new verifier response. Existing
source observations and historical responses are never changed.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import time

from catalog_wire import VERSION as WIRE_VERSION, compact_payload
from warning_coverage import VERSION as COVERAGE_VERSION, INSTRUCTIONS as COVERAGE_INSTRUCTIONS, annotate, recheck
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'live'))
from run_smoke import (Coordinator, KEYS, single, BudgetBlocked, ProviderUnavailable,
                      catalogs, catalog_packet, compile_selection,
                      initial_evidence, publish_code_checked, selection_schema, sha)
from agent_contracts import (INSTRUCTIONS, catalog_view, checked_verdicts, claim_catalog,
                            materialize_selection, schemas, tool, validate)
from metadata_contracts import metadata_catalog, metadata_view
from api_budget import canonical
from comparison_core import STAGES, gate, metadata_gate

VERSION = WIRE_VERSION + '-' + COVERAGE_VERSION
BUCKETS = ('published_claims', 'verified_notes', 'verified_recommended_checks',
           'rejected_claims', 'rejected_metadata')


class ReviewProvider:
    def __init__(self, provider, arm):
        self.provider, self.arm = provider, arm
        self.execution_kind = provider.execution_kind
        self.requests, self.counts = [], Counter()

    def can_call(self, roles):
        added = Counter(roles)
        return (sum(self.counts.values()) + len(roles) <= (12 if self.arm == 'three_agents' else 4)
                and all(self.counts[r] + n <= 4 for r, n in added.items()))

    def call(self, **request):
        if not self.can_call([request['role']]):
            raise BudgetBlocked('Completeness repair shares the original role/case request cap')
        source = deepcopy(request['payload'])
        if request['role'] in {'process_analyst', 'single_agent_baseline'}:
            source['instructions'] += COVERAGE_INSTRUCTIONS
        payload, metrics = compact_payload(source)
        forwarded = {**request, 'payload': payload, 'run_key': request['run_key'] + '-' + VERSION,
                     'request_id': sha({'version': VERSION, 'source_request_id': request['request_id']})}
        self.counts[request['role']] += 1
        row = {'source_request_id': request['request_id'], 'request_id': forwarded['request_id'],
               'run_key': forwarded['run_key'], 'role': request['role'], **metrics,
               'attempt_status': 'pending'}
        self.requests.append(row)
        try:
            response = self.provider.call(**forwarded)
            row.update(attempt_status='response_received', response_id=response.get('id'))
            return response
        except (BudgetBlocked, ProviderUnavailable, ValueError, TypeError, KeyError) as exc:
            row.update(attempt_status=type(exc).__name__)
            raise


def terminal_call(case, sender, result, role, packet, definition, phase):
    payload = {'model': case.store.config['provider']['model'],
               'instructions': INSTRUCTIONS + '\nYour role is ' + role,
               'input': [{'role': 'user', 'content': canonical(packet)}], 'tools': [definition],
               'tool_choice': {'type': 'function', 'name': definition['name']},
               'parallel_tool_calls': False, 'max_output_tokens': 1200, 'store': False,
               'reasoning': {'effort': 'none'}, 'service_tier': 'default', 'truncation': 'disabled'}
    request_id = sha({'run': KEYS[sender.arm], 'case': case.case_key, 'asof': case.asof,
                      'mode': case.mode, 'event': case.events[-1]['event_id'],
                      'phase': phase, 'ordinal': sender.counts[role] + 1, 'version': VERSION})
    response = sender.call(request_id=request_id, case_key=case.case_key,
                           run_key=KEYS[sender.arm], role=role, payload=payload)
    output = response.get('output', [])
    result['trace'].append({'kind': 'model_request', 'role': role, 'phase': phase,
                            'request_id': request_id, 'response_id': response.get('id')})
    result['raw_agent_outputs'].append({'role': role, 'phase': phase, 'ordinal': sender.counts[role],
                                        'response_id': response.get('id'), 'output': deepcopy(output)})
    calls = [r for r in output if r.get('type') == 'function_call']
    if len(calls) != 1 or calls[0].get('name') != definition['name']:
        raise ValueError('Completeness repair must submit the forced terminal function')
    args = json.loads(calls[0]['arguments'])
    validate(args, definition['parameters'])
    return args


def accepted_items(case, original):
    items = [recheck(case, item) for bucket in BUCKETS[:3] for item in original[bucket]]
    if any(i['code_gate'] != 'passed' for i in items):
        raise ValueError('Previously published item no longer matches frozen evidence')
    return items


def draft_result(case, original, items):
    candidate = deepcopy(original)
    for bucket in BUCKETS:
        candidate[bucket] = []
    publish_code_checked(case, candidate, items)
    return candidate


def verifier_packet(case, result, items):
    ids = {eid for item in items for eid in item.get('evidence_ids', [item.get('evidence_id')])}
    return {'claims': items, 'evidence': [case.resolve_evidence(eid) for eid in sorted(ids)],
            'system_limitations': result['system_limitations'],
            'contract': case.check_temporal_and_comparison_contract(),
            'recomputed': [case.recompute_reported_metrics(s) for s in STAGES]}


def verified_result(case, original, items, verdicts):
    result = deepcopy(original)
    for bucket in BUCKETS:
        result[bucket] = []
    for claim in items:
        verdict = verdicts[claim['claim_id']]
        item = {**claim, 'verifier_verdict': verdict}
        accepted = claim['code_gate'] == 'passed' and verdict['verdict'] == 'accept'
        if accepted:
            item['supporting_evidence'] = ([case.resolve_evidence(eid) for eid in claim['evidence_ids']]
                if 'metadata_kind' in claim else case.resolve_evidence(claim['evidence_id']))
        if 'metadata_kind' in claim:
            bucket = ('verified_notes' if claim['metadata_kind'] == 'unknown' else 'verified_recommended_checks') if accepted else 'rejected_metadata'
        else:
            bucket = 'published_claims' if accepted else 'rejected_claims'
        result[bucket].append(item)
    result['unknowns'] = sorted({'cause_unverified', *[i['code'] for i in result['verified_notes']]})
    result['recommended_checks'] = sorted({i['code'] for i in result['verified_recommended_checks']})
    result['metadata_verification_complete'] = True
    result['agent_verifier_used'] = True
    published = any(result[b] for b in BUCKETS[:3])
    rejected = result['rejected_claims'] or result['rejected_metadata']
    result['explanation_status'] = ('evidence_checked_with_rejections' if rejected else 'evidence_checked') if published else 'inconclusive'
    return result


def repair(case, sender, result):
    """One bounded repair, retaining original publication on any incomplete repair."""
    roles = ['process_analyst', 'evidence_verifier'] if sender.arm == 'three_agents' else ['single_agent_baseline']
    if not sender.can_call(roles):
        result['coverage_repair'] = {'status': 'unavailable_request_budget'}
        return result
    packet, visible = initial_evidence(case)
    packet.update(missing_warning_stages=result['warning_coverage']['missing_stages'],
                  instruction='Revise the selection to explicitly cover each warned stage; retain supported limitations.',
                  previous_published={b: result[b] for b in BUCKETS[:3]})
    # Previously selected source records remain available, with their full context.
    previous_items = accepted_items(case, result)
    for item in previous_items:
        visible.update(item.get('evidence_ids', [item.get('evidence_id')]))
    packet['previous_evidence'] = [case.resolve_evidence(eid) for eid in sorted(visible)]
    result['coverage_repair'] = {'status': 'attempted', 'before': deepcopy(result['warning_coverage'])}
    try:
        if sender.arm == 'single_agent':
            choices = catalogs(case, visible)
            packet['catalogs'] = catalog_packet(choices)
            definition = tool('submit_case_analysis', selection_schema(case, choices))
            args = terminal_call(case, sender, result, roles[0], packet, definition, 'coverage_selection')
            items = compile_selection(case, args, choices)
        else:
            role = 'process_analyst'
            choices, metadata = claim_catalog(case, role, visible), metadata_catalog(case, role, visible)
            packet.update(claim_catalog=catalog_view(choices), metadata_catalog=metadata_view(metadata))
            definition = schemas(role, case.store.config['peer_plans'], choices, metadata=metadata)[-1]
            args = terminal_call(case, sender, result, role, packet, definition, 'coverage_selection')
            draft = materialize_selection(case, role, args, choices, metadata)
            items = [gate(case, role, i) for i in draft['claims']]
            items.extend(metadata_gate(case, role, i) for i in draft['unknowns'] + draft['recommended_checks'])
            items.extend(i for i in previous_items if i['origin_role'] == 'context_analyst')
            if any(i['code_gate'] != 'passed' for i in items):
                raise ValueError('Completeness repair failed source gate')
        candidate = annotate(case, draft_result(case, result, items))
        if candidate['warning_coverage']['status'] != 'complete':
            raise ValueError('Repair selection still omits a warned stage')
        if sender.arm == 'three_agents':
            definition = schemas('evidence_verifier', case.store.config['peer_plans'], claims=items)[-1]
            args = terminal_call(case, sender, result, 'evidence_verifier', verifier_packet(case, result, items), definition, 'coverage_verification')
            verdicts = checked_verdicts(args, items)
            candidate = annotate(case, verified_result(case, result, items, verdicts))
            if candidate['warning_coverage']['status'] != 'complete':
                raise ValueError('Verifier did not accept every warned stage')
        # Keep the exact initial publication and every response for audit.
        candidate['pre_repair_publication'] = {b: deepcopy(result[b]) for b in BUCKETS}
        candidate['coverage_repair'] = {**result['coverage_repair'], 'status': 'completed'}
        return candidate
    except (BudgetBlocked, ProviderUnavailable, ValueError, TypeError, KeyError) as exc:
        result['coverage_repair'].update(status='incomplete', reason=f'{type(exc).__name__}: {exc}')
        return result


def run_review(case, provider, arm):
    if arm not in KEYS:
        raise ValueError('Unknown comparison arm')
    started = time.perf_counter()
    sender = ReviewProvider(provider, arm)
    baseline = Coordinator(sender, KEYS[arm]).run(case) if arm == 'three_agents' else single(case, sender)
    result = annotate(case, baseline)
    if result['explanation_status'] == 'incomplete_warning_coverage':
        result = repair(case, sender, result)
    else:
        ready = result['explanation_status'] in {'code_checked', 'evidence_checked', 'evidence_checked_with_rejections'}
        result['coverage_repair'] = {'status': 'not_needed' if ready else 'base_run_unavailable'}
    ids = {r['source_request_id']: r['request_id'] for r in sender.requests}
    for entry in result['trace']:
        if entry['kind'] == 'model_request':
            entry['source_request_id'] = entry['request_id']
            entry['request_id'] = ids[entry['request_id']]
    result.update(arm=arm, profile='native_bounded', wire_format=WIRE_VERSION,
                  review_version=VERSION, run_key=KEYS[arm] + '-' + VERSION,
                  wire_requests=sender.requests, request_counts=dict(sender.counts),
                  evidence=list(case.registry.values()), explanation_seconds=time.perf_counter() - started)
    return result
