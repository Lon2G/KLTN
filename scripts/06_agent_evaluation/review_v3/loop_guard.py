"""Tool-loop guard for the frozen review_v2 engines (no network here).

Problem seen in the approved live pair of 07/11/2026: the single agent re-queried
evidence it already had, the history grew, and the next request (6.024 tokens)
was cancelled before inference, so no conclusion was published.

The guard sits between the review_v2 wire compaction and the real provider. It
never edits evidence, detector output or catalogs. It only changes *when* an
agent must submit, by forcing the role's terminal function and offering that
function alone:

1. repeated_tool_call   - the same tool with the same arguments was already called;
2. no_new_evidence      - the last tool output introduced no new source record;
3. request_reserve      - submitting now is required so later roles keep a turn
                          (matters for the matched_4 profile);
4. token_cap_fallback   - the provider refused the request before inference
                          because the official count exceeded the cap; resend once
                          as a forced terminal request under a new request id.

Every guard decision is logged; the engines' own caps and gates still apply.
"""
from copy import deepcopy
import hashlib
import json

VERSION = 'loop_guard_v1'
TERMINALS = {'process_analyst': 'submit_analysis', 'context_analyst': 'submit_analysis',
             'evidence_verifier': 'submit_verdict', 'single_agent_baseline': 'submit_case_analysis'}
THREE_ORDER = ('process_analyst', 'context_analyst', 'evidence_verifier')
CAP_MESSAGE = 'Serialized input exceeds token cap'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def forced_terminal(payload, role):
    name = TERMINALS[role]
    tools = [t for t in payload['tools'] if t.get('name') == name]
    if len(tools) != 1:
        raise ValueError('Terminal function missing from role tools')
    return {**deepcopy(payload), 'tools': tools, 'tool_choice': {'type': 'function', 'name': name}}


def repeated_tool_call(history):
    calls = [(i.get('name'), canonical(json.loads(i.get('arguments') or '{}')))
             for i in history if i.get('type') == 'function_call']
    return bool(calls) and calls[-1] in calls[:-1]


def no_new_evidence(history):
    outputs = [i for i in history if i.get('type') == 'function_call_output']
    if not outputs:
        return False
    try:
        last = json.loads(outputs[-1]['output'])
    except (TypeError, ValueError):
        return False
    # Wire format evidence_refs_v1 lists records first introduced by this output.
    return isinstance(last, dict) and last.get('new_evidence') == {} and 'body' in last \
        and last['body'].get('status') != 'contract_rejected'


class GuardedProvider:
    """Wraps the dispatching provider. `max_requests` is the arm's total request cap."""

    def __init__(self, provider, arm, max_requests):
        if arm not in {'three_agents', 'single_agent'}:
            raise ValueError('Unknown arm')
        self.provider, self.arm, self.max_requests = provider, arm, max_requests
        self.execution_kind = getattr(provider, 'execution_kind', 'unknown')
        self.sent = 0
        self.roles_seen = []
        self.log = []
        self.id_map = {}            # request id seen by the engine -> id actually dispatched

    def _reserve_needed(self, role):
        """A tool call now implies one more request for this role's own submission,
        plus at least one terminal request for each later role not yet started."""
        needed_after = 0
        if self.arm == 'three_agents':
            if role not in self.roles_seen:
                self.roles_seen.append(role)
            later = THREE_ORDER[THREE_ORDER.index(role) + 1:]
            needed_after = len([r for r in later if r not in self.roles_seen])
        return self.max_requests - self.sent - 1 < 1 + needed_after

    def call(self, **request):
        role, payload = request['role'], request['payload']
        entry = {'request_id': request['request_id'], 'role': role, 'reasons': [],
                 'original_tool_choice': payload['tool_choice'], 'dispatched_request_id': request['request_id']}
        if payload['tool_choice'] == 'auto':
            history = payload['input']
            if repeated_tool_call(history):
                entry['reasons'].append('repeated_tool_call')
            if no_new_evidence(history):
                entry['reasons'].append('no_new_evidence')
            if self._reserve_needed(role):
                entry['reasons'].append('request_reserve')
        elif self.arm == 'three_agents' and role not in self.roles_seen:
            self.roles_seen.append(role)
        if entry['reasons']:
            payload = forced_terminal(payload, role)
        self.log.append(entry)
        self.id_map[request['request_id']] = request['request_id']
        try:
            self.sent += 1
            return self.provider.call(**{**request, 'payload': payload})
        except Exception as exc:
            capped = CAP_MESSAGE in str(exc)
            if not capped or payload['tool_choice'] != 'auto':
                raise
            # Refused before inference: no model output was produced or billed.
            self.sent -= 1
            retry_id = sha({'version': VERSION, 'fallback_of': request['request_id']})
            entry['reasons'].append('token_cap_fallback')
            entry['dispatched_request_id'] = retry_id
            self.id_map[request['request_id']] = retry_id
            self.sent += 1
            return self.provider.call(**{**request, 'request_id': retry_id,
                                         'payload': forced_terminal(payload, role)})
