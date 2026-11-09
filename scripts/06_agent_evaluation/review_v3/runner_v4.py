"""review_v4 = review_v3 engines and loop guard unchanged, run under budget policy v2
(input cap 12000 tokens). Only the run key / request ids carry the new tag, so v4
requests are never confused with the v3 run. No network here."""
from copy import deepcopy
import time

import runner_v3 as v3
from runner_v3 import v2, PROFILES, compact_payload, COVERAGE_INSTRUCTIONS, annotate, GuardedProvider, sha

TAG = 'policy_v2_input12000'
VERSION = v3.VERSION + '-' + TAG


def run_key(arm, profile):
    return v3.run_key(arm, profile) + '-' + TAG


class TaggedSender(v3.ProfileSender):
    def call(self, **request):
        if not self.can_call([request['role']]):
            raise v2.BudgetBlocked('Profile request cap reached')
        source = deepcopy(request['payload'])
        if request['role'] in {'process_analyst', 'single_agent_baseline'}:
            source['instructions'] += COVERAGE_INSTRUCTIONS
        payload, metrics = compact_payload(source)
        forwarded = {**request, 'payload': payload, 'run_key': run_key(self.arm, self.profile),
                     'request_id': sha({'version': VERSION, 'profile': self.profile,
                                        'source_request_id': request['request_id']})}
        self.counts[request['role']] += 1
        row = {'source_request_id': request['request_id'], 'request_id': forwarded['request_id'],
               'run_key': forwarded['run_key'], 'role': request['role'], **metrics, 'attempt_status': 'pending'}
        self.requests.append(row)
        try:
            response = self.provider.call(**forwarded)
            row.update(attempt_status='response_received', response_id=response.get('id'))
            return response
        except (v2.BudgetBlocked, v2.ProviderUnavailable, ValueError, TypeError, KeyError) as exc:
            row.update(attempt_status=type(exc).__name__)
            raise


def run_review(case, provider, arm, profile='native_bounded'):
    if arm not in v2.KEYS or profile not in PROFILES:
        raise ValueError('Unknown arm or profile')
    started = time.perf_counter()
    guard = GuardedProvider(provider, arm, PROFILES[profile][arm])
    sender = TaggedSender(guard, arm, profile)
    baseline = v2.Coordinator(sender, v2.KEYS[arm]).run(case) if arm == 'three_agents' else v2.single(case, sender)
    result = annotate(case, baseline)
    if result['explanation_status'] == 'incomplete_warning_coverage':
        result = v2.repair(case, sender, result)
    else:
        ready = result['explanation_status'] in {'code_checked', 'evidence_checked', 'evidence_checked_with_rejections'}
        result['coverage_repair'] = {'status': 'not_needed' if ready else 'base_run_unavailable'}
    sent = {r['source_request_id']: guard.id_map.get(r['request_id'], r['request_id']) for r in sender.requests}
    for entry in result['trace']:
        if entry['kind'] == 'model_request':
            entry['source_request_id'] = entry['request_id']
            entry['request_id'] = sent[entry['request_id']]
    result.update(arm=arm, profile=profile, wire_format=v2.WIRE_VERSION, review_version=VERSION,
                  run_key=run_key(arm, profile), wire_requests=sender.requests,
                  request_counts=dict(sender.counts), guard_log=guard.log,
                  guard_forced_requests=sum(bool(e['reasons']) for e in guard.log),
                  evidence=list(case.registry.values()), explanation_seconds=time.perf_counter() - started)
    return result
