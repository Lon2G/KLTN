"""review_v3: review_v2 engines + loop guard + explicit budget profiles.

Engines, evidence tools, gates, catalogs and the completeness repair are the
frozen review_v2 / v1 code. This module only adds:
- per-profile request caps (native_bounded 12/4, matched_4 4/4);
- unique run keys / request ids per version and profile, so no request can
  collide with an earlier ledger entry;
- the loop guard in loop_guard.py.
No network is initiated here; the caller supplies the provider.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import sys
import time

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / 'review_v2'))
import runner as v2  # noqa: E402
from catalog_wire import compact_payload  # noqa: E402
from warning_coverage import INSTRUCTIONS as COVERAGE_INSTRUCTIONS, annotate  # noqa: E402
from loop_guard import GuardedProvider, VERSION as GUARD_VERSION, sha  # noqa: E402

VERSION = v2.VERSION + '-' + GUARD_VERSION
PROFILES = {'native_bounded': {'three_agents': 12, 'single_agent': 4},
            'matched_4': {'three_agents': 4, 'single_agent': 4}}
PER_ROLE_CAP = 4


def run_key(arm, profile):
    return v2.KEYS[arm] + '-' + VERSION + '-' + profile


class ProfileSender(v2.ReviewProvider):
    """review_v2 sender with a profile cap and version/profile-unique request ids."""

    def __init__(self, provider, arm, profile):
        super().__init__(provider, arm)
        self.profile, self.max_requests = profile, PROFILES[profile][arm]

    def can_call(self, roles):
        added = Counter(roles)
        return (sum(self.counts.values()) + len(roles) <= self.max_requests
                and all(self.counts[r] + n <= PER_ROLE_CAP for r, n in added.items()))

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
    sender = ProfileSender(guard, arm, profile)
    if arm == 'three_agents':
        baseline = v2.Coordinator(sender, v2.KEYS[arm]).run(case)
    else:
        baseline = v2.single(case, sender)
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
