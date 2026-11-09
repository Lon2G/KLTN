"""Rules control and offline-testable single/three-agent adapters.

There is deliberately no live/API entry point in this package. A real provider
cannot be passed into these adapters. Protocol fixtures are never research runs.
"""
from copy import deepcopy
import json
import time

from comparison_core import (CONTRACT_VERSION, DOMAINS, STAGES, base_result, catalogs,
    catalog_packet, compile_selection, initial_evidence, publish_code_checked, selection_schema, sha)
from agent_contracts import ROLES, schemas, tool, validate
from api_budget import BudgetBlocked, canonical
from coordinator import Coordinator
from provider import ProviderUnavailable

OFFLINE_KINDS = {"technical_scripted_test_not_AI_inference", "offline_saved_receipt_replay_not_new_AI_inference"}
SINGLE_INSTRUCTIONS = """You are the single-agent comparison control for an Olist analysis.
Use only event-visible evidence and local tools. Process and context are output
sections of ONE agent, not separate agents. Select IDs from their catalogs.
Never invent numbers, labels, probabilities or causes. A stage warning is not a
confirmed bottleneck. Keep missing endpoints, negative timelines, unperformed
comparisons, low sample counts and low coverage distinct. Disclose source
availability assumptions. Checks are eligible investigations, not proven causes.
Use declared comparisons; do not relax matching to force a positive conclusion.
Submit both sections using submit_case_analysis. Each section allows at most
three scalar claims, three uncertainty IDs and two check IDs. There is no separate
AI verifier; deterministic source gates apply to all items. You may abstain.
There are four requests total, including tool calls and contract repairs.
"""


class CappedOfflineProvider:
    def __init__(self, provider, cap):
        if getattr(provider, "execution_kind", None) not in OFFLINE_KINDS:
            raise ValueError("This comparison package accepts offline providers only; no new API calls authorized")
        if type(cap) is not int or not 1 <= cap <= 12:
            raise ValueError("Invalid experimental request cap")
        self.provider, self.cap, self.calls = provider, cap, 0
        self.execution_kind = provider.execution_kind

    def call(self, **kwargs):
        if self.calls >= self.cap:
            raise BudgetBlocked("Comparison-wide request cap reached before dispatch")
        self.calls += 1
        return self.provider.call(**kwargs)


def run_three_offline(case, provider, profile="native_bounded"):
    if profile not in {"native_bounded", "matched_4"}:
        raise ValueError("Unknown budget profile")
    sender = CappedOfflineProvider(provider, 12 if profile == "native_bounded" else 4)
    result = Coordinator(sender, "comparison-v1-three-" + profile).run(case)
    result.update(arm="three_agents", profile=profile, dispatched_model_requests=sender.calls,
                  comparison_request_cap=sender.cap, agent_verifier_used=True,
                  research_result=False, execution_kind=sender.execution_kind)
    return result


def run_single_offline(case, provider, profile="native_bounded"):
    if profile not in {"native_bounded", "matched_4"}:
        raise ValueError("Unknown budget profile")
    sender = CappedOfflineProvider(provider, 4)
    run_key = "comparison-v1-single-" + profile
    result = base_result(case, "single_agent", sender.execution_kind, run_key)
    frozen_hash = sha(result["detector"])
    started = time.perf_counter()
    packet, visible = initial_evidence(case)
    choices = catalogs(case, visible)
    packet.update(catalogs=catalog_packet(choices), declared_plans=case.store.config["peer_plans"])
    history = [{"role": "user", "content": canonical(packet)}]
    try:
        for ordinal in range(1, 5):
            tools_by_name = {}
            for role in ROLES:
                for definition in schemas(role, case.store.config["peer_plans"])[:-1]:
                    tools_by_name[definition["name"]] = definition
            terminal = tool("submit_case_analysis", selection_schema(case, choices))
            tools_by_name[terminal["name"]] = terminal
            payload = {"model": case.store.config["provider"]["model"], "instructions": SINGLE_INSTRUCTIONS,
                       "input": deepcopy(history), "tools": list(tools_by_name.values()),
                       "tool_choice": {"type": "function", "name": terminal["name"]} if ordinal == 4 else "auto",
                       "parallel_tool_calls": False, "max_output_tokens": 1200, "store": False,
                       "reasoning": {"effort": "none"}, "service_tier": "default", "truncation": "disabled"}
            request_id = sha({"run": run_key, "case": case.case_key, "asof": case.asof,
                              "event": case.events[-1]["event_id"], "mode": case.mode,
                              "ordinal": ordinal, "contract": CONTRACT_VERSION})
            response = sender.call(request_id=request_id, case_key=case.case_key, run_key=run_key,
                                   role="single_agent_baseline", payload=payload)
            output = response.get("output", [])
            result["trace"].append({"kind": "model_request", "role": "single_agent_baseline", "request_id": request_id})
            result["raw_agent_outputs"].append({"ordinal": ordinal, "output": deepcopy(output)})
            calls = [r for r in output if r.get("type") == "function_call"]
            if len(calls) != 1 or calls[0].get("name") not in tools_by_name:
                raise ValueError("Expected exactly one declared function call")
            call = calls[0]
            try:
                args = json.loads(call["arguments"])
                validate(args, tools_by_name[call["name"]]["parameters"])
                if ordinal == 4 and call["name"] != terminal["name"]:
                    raise ValueError("Final request must submit")
                if call["name"] == terminal["name"]:
                    items = compile_selection(case, args, choices)
                    publish_code_checked(case, result, items)
                    result["selected_output"] = deepcopy(args)
                    break
            except (ValueError, KeyError, TypeError) as exc:
                result["contract_violations"].append({"ordinal": ordinal, "reason": str(exc)})
                if ordinal == 4:
                    raise ValueError("Contract repair exhausted the fixed request cap") from exc
                history.extend(output)
                history.append({"type": "function_call_output", "call_id": call["call_id"],
                                "output": canonical({"status": "contract_rejected", "reason": str(exc)})})
                continue
            evidence = getattr(case, call["name"])(**args)
            result["trace"].append({"kind": "tool_call", "role": "single_agent_baseline", "tool": call["name"],
                                    "arguments": args, "evidence_id": evidence["evidence_id"]})
            visible.add(evidence["evidence_id"])
            old = choices
            choices = catalogs(case, visible)
            # Append only new choices; all original choices remain in history.
            delta = {domain: {kind: {k: v for k, v in c.items() if k not in old[domain][kind]}
                             for kind, c in groups.items()} for domain, groups in choices.items()}
            history.extend(output)
            history.append({"type": "function_call_output", "call_id": call["call_id"],
                            "output": canonical({"evidence": evidence, "catalogs": catalog_packet(delta)})})
    except BudgetBlocked as exc:
        result.update(explanation_status="unavailable_budget", error=str(exc))
    except ProviderUnavailable as exc:
        result.update(explanation_status="unavailable_api", error=str(exc))
    except (ValueError, KeyError, TypeError) as exc:
        result.update(explanation_status="inconclusive", error=str(exc))
    result.update(profile=profile, dispatched_model_requests=sender.calls, comparison_request_cap=4,
                  explanation_seconds=time.perf_counter() - started, evidence=list(case.registry.values()),
                  research_result=False, detector_latency_measured_here=False)
    if frozen_hash != sha([case.get_frozen_stage_assessment(s) for s in STAGES]):
        raise RuntimeError("Comparison changed detector output")
    return result


def _pick_metadata(catalog, kind, maximum):
    priority = ["timeline_conflict", "stage_endpoint_not_observed", "comparison_not_performed_stage_unassessed",
                "insufficient_comparable_orders", "insufficient_comparison_coverage", "context_availability_unverified",
                "promise_basis_unverified", "inspect_source_timestamps", "review_seller_preparation",
                "review_carrier_route", "collect_more_comparable_history", "verify_context_availability",
                "obtain_promise_method", "collect_weight_dimensions", "collect_workload_stock"]
    rank = {code: i for i, code in enumerate(priority)}
    seen, selected = set(), []
    for key, item in sorted(catalog.items(), key=lambda pair: (rank.get(pair[1]["code"], 99), pair[1]["stage"] or "", pair[0])):
        meaning = (item["code"], item["stage"], item["field"])
        if item["metadata_kind"] == kind and meaning not in seen and len(selected) < maximum:
            selected.append(key)
            seen.add(meaning)
    return selected


def run_rules(case):
    started = time.perf_counter()
    initial, visible = initial_evidence(case)
    result = base_result(case, "rules", "deterministic_rules_real_olist", "comparison-v1-rules")
    warnings = [s for s in STAGES if case.scores[s]["long_stage_warning"] is True]
    unassessed = [s for s in STAGES if case.scores[s]["long_stage_warning"] is None]
    targets = warnings or unassessed[:1] or ["transport"]
    for stage in targets:
        evidence = case.compare_declared_peer_groups(stage, "same_product_peers")
        visible.add(evidence["evidence_id"])
        result["trace"].append({"kind": "tool_call", "role": "deterministic_rules", "tool": "compare_declared_peer_groups",
            "arguments": {"stage": stage, "plan": "same_product_peers"}, "evidence_id": evidence["evidence_id"]})
    choices = catalogs(case, visible)
    selection = {}
    for domain, c in choices.items():
        def rank(pair):
            key, claim = pair
            record = case.resolve_evidence(claim["evidence_id"])
            field, stage = claim["field"], record["stage"]
            if domain == "process":
                preferred = [(s, "long_stage_warning") for s in warnings]
                if unassessed:
                    preferred += [(s, "assessment_status") for s in unassessed]
                if warnings:
                    preferred += [(warnings[0], "duration_days"), (warnings[0], "difference_from_reference_mean_days")]
                preferred += [(s, "long_stage_warning") for s in STAGES]
                order = preferred.index((stage, field)) if (stage, field) in preferred else 99
            else:
                order = 0 if record["kind"] == "peer_comparison" and field == "status" else {"context_availability_source_verified": 1, "single_seller_available": 2}.get(field, 99)
            return order, key
        selection[domain] = {"claim_ids": [k for k, c in sorted(c["claims"].items(), key=rank)[:3]],
                             "unknown_ids": _pick_metadata(c["metadata"], "unknown", 3),
                             "check_ids": _pick_metadata(c["metadata"], "recommended_check", 2)}
    items = compile_selection(case, selection, choices)
    publish_code_checked(case, result, items)
    result.update(selected_output=selection, dispatched_model_requests=0, API_cost_usd=0,
                  explanation_seconds=time.perf_counter() - started, evidence=list(case.registry.values()),
                  research_result=True, research_scope="development_rules_control_not_AI_accuracy",
                  detector_latency_measured_here=False)
    return result
