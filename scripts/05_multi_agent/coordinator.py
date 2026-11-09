"""Three separately prompted, tool-using agents with a bounded verification cycle.

The scheduler is deterministic; it is not counted as an AI agent. Provider output
is never executed as code. Detector output remains available if explanations fail.
"""
from copy import deepcopy
import json
import time

from agent_contracts import (CONTRACT_VERSION, INSTRUCTIONS, ROLES, catalog_view,
    checked_verdicts, claim_catalog, gate, materialize_selection, schemas, validate)
from api_budget import BudgetBlocked, canonical
from evidence_tools import STAGES, sha
from metadata_contracts import metadata_catalog, metadata_gate, metadata_view
from provider import ProviderUnavailable


class Coordinator:
    def __init__(self, provider, run_key="multi-agent-pilot-v3"):
        self.provider, self.run_key = provider, run_key

    def run(self, case):
        started = time.perf_counter()
        counts = {role: 0 for role in ROLES}
        messages, trace, drafts = [], [], {}
        visible_evidence = {role: set() for role in ROLES}
        detector = [case.get_frozen_stage_assessment(stage) for stage in STAGES]
        detector_hash = sha(detector)
        baseline = {"version": "coordinated_analysis_v3", "contract_version": CONTRACT_VERSION,
                    "case_key": case.case_key,
                    "as_of_event_time": case.asof, "mode": case.mode, "assessment_revision": case.revision,
                    "run_key": self.run_key, "execution_kind": self.provider.execution_kind,
                    "detector": detector, "cause_status": "unverified",
                    "ground_truth": None, "independent_accuracy_evaluation": False,
                    "messages": messages, "trace": trace, "published_claims": [], "rejected_claims": [],
                    "raw_agent_outputs": [], "contract_violations": [],
                    "recommended_checks": [], "unknowns": ["cause_unverified"],
                    "verified_notes": [], "verified_recommended_checks": [], "rejected_metadata": [],
                    "system_limitations": [{"code": "cause_unverified", "basis": "analysis_contract_no_causal_labels",
                                            "origin": "system_not_model_inference"}],
                    "metadata_verification_complete": False,
                    "publication_scope": "published_items_only_source_and_rule_checked_not_proven_cause_or_usefulness",
                    "explanation_status": "pending"}

        def remember_evidence(role, value):
            if isinstance(value, dict):
                if "kind" in value and "data" in value and value.get("evidence_id") in case.registry:
                    visible_evidence[role].add(value["evidence_id"])
                for child in value.values():
                    remember_evidence(role, child)
            elif isinstance(value, list):
                for child in value:
                    remember_evidence(role, child)

        def message(sender, recipient, body, revision=0):
            item = {"case_key": case.case_key, "sender_role": sender, "recipient_role": recipient,
                    "as_of_event_time": case.asof, "assessment_revision": case.revision,
                    "explanation_revision": revision, "body": body,
                    "reply_to": messages[-1]["message_id"] if messages else None}
            item["message_id"] = sha(item)
            messages.append(item)
            return item

        def call_role(role, packet, revision=False):
            packet = deepcopy(packet)
            remember_evidence(role, packet)
            catalog = claim_catalog(case, role, visible_evidence[role])
            metadata = metadata_catalog(case, role, visible_evidence[role])
            if role != "evidence_verifier":
                packet["claim_catalog"] = catalog_view(catalog)
                packet["metadata_catalog"] = metadata_view(metadata)
            terminal = "submit_verdict" if role == "evidence_verifier" else "submit_analysis"
            history = [{"role": "user", "content": canonical(message("scheduler", role, packet, int(revision)))}]
            limit = 4 if revision else 3
            while counts[role] < limit:
                counts[role] += 1
                force_submit = revision or counts[role] == limit
                functions = schemas(role, case.store.config["peer_plans"], catalog, packet.get("claims", ()), metadata)
                payload = {"model": case.store.config["provider"]["model"],
                           "instructions": INSTRUCTIONS + "\nYour role is " + role,
                           "input": deepcopy(history), "tools": functions,
                           "tool_choice": {"type": "function", "name": terminal} if force_submit else "auto",
                           "parallel_tool_calls": False, "max_output_tokens": 1200,
                           "store": False, "reasoning": {"effort": "none"},
                           "service_tier": "default", "truncation": "disabled"}
                request_id = sha({"run": self.run_key, "case": case.case_key, "asof": case.asof,
                                  "event": case.events[-1]["event_id"], "mode": case.mode,
                                  "role": role, "ordinal": counts[role], "contract": CONTRACT_VERSION})
                before = time.perf_counter()
                response = self.provider.call(request_id=request_id, case_key=case.case_key,
                    run_key=self.run_key, role=role, payload=payload)
                trace.append({"kind": "model_request", "role": role, "request_id": request_id,
                              "seconds": time.perf_counter() - before, "response_id": response.get("id")})
                output = response.get("output", [])
                baseline["raw_agent_outputs"].append({"role": role, "ordinal": counts[role],
                    "explanation_revision": int(revision), "response_id": response.get("id"),
                    "output": deepcopy(output)})
                calls = [r for r in output if r.get("type") == "function_call"]
                if len(calls) != 1:
                    raise ValueError("Agent must return exactly one function call")
                call = calls[0]
                available = {t["name"]: t for t in functions}
                if call["name"] not in available:
                    raise ValueError("Agent requested a tool outside its role")
                try:
                    args = json.loads(call["arguments"])
                    validate(args, available[call["name"]]["parameters"])
                    if call["name"] == terminal:
                        if role == "evidence_verifier":
                            checked_verdicts(args, packet.get("claims", ()))
                            compiled = args
                        else:
                            compiled = materialize_selection(case, role, args, catalog, metadata)
                        # Keep the actual model submission, separately from code-copied facts.
                        message(role, "scheduler", args, int(revision))
                        return compiled
                except (ValueError, KeyError, TypeError) as exc:
                    violation = {"role": role, "ordinal": counts[role], "tool": call["name"],
                        "reason": str(exc), "explanation_revision": int(revision)}
                    baseline["contract_violations"].append(violation)
                    if counts[role] >= limit:
                        raise ValueError("Role exhausted its request limit after contract rejection") from exc
                    history.extend(output)
                    history.append({"type": "function_call_output", "call_id": call["call_id"],
                        "output": canonical({"status": "contract_rejected", "reason": str(exc),
                            "repair": "Follow the current tool schema. Select claim_ids from claim_catalog and "
                                      "unknown_ids/check_ids from metadata_catalog; do not write notes, field names or values. "
                                      "Verifier must cover all scalar AND metadata items and cannot "
                                      "accept code-rejected claims or pair reject/revise with supported."})})
                    continue
                if force_submit:
                    raise ValueError("Agent exceeded bounded tool loop")
                result = getattr(case, call["name"])(**args)
                trace.append({"kind": "tool_call", "role": role, "tool": call["name"],
                              "arguments": args, "evidence_id": result["evidence_id"]})
                history.extend(output)
                remember_evidence(role, result)
                catalog = claim_catalog(case, role, visible_evidence[role])
                previous_metadata = set(metadata)
                metadata = metadata_catalog(case, role, visible_evidence[role])
                augmented = deepcopy(result)
                if role != "evidence_verifier":
                    augmented["claim_catalog"] = catalog_view(claim_catalog(case, role, [result["evidence_id"]]))
                    # Cross-source checks use all visible evidence, but only new
                    # choices need to be appended to the conversation history.
                    augmented["metadata_catalog"] = metadata_view({k: v for k, v in metadata.items() if k not in previous_metadata})
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": canonical(augmented)})
            raise BudgetBlocked("Role request limit reached")

        def candidates():
            result = []
            for role, draft in drafts.items():
                result.extend(gate(case, role, c) for c in draft["claims"])
                result.extend(metadata_gate(case, role, item) for item in draft["unknowns"] + draft["recommended_checks"])
            unique = {c["claim_id"]: c for c in result}
            return list(unique.values())

        def verify_packet(claims):
            ids = {eid for c in claims for eid in c.get("evidence_ids", [c.get("evidence_id")]) if eid in case.registry}
            return {"claims": claims, "evidence": [case.resolve_evidence(eid) for eid in sorted(ids)],
                    "system_limitations": baseline["system_limitations"],
                    "contract": case.check_temporal_and_comparison_contract(),
                    "recomputed": [case.recompute_reported_metrics(s) for s in STAGES]}

        try:
            contract = case.check_temporal_and_comparison_contract()["data"]
            if not contract["no_future_stage_endpoints"] or not contract["case_excluded_from_training"]:
                raise ValueError("Temporal or split contract failed")
            if any(not case.recompute_reported_metrics(s)["data"]["matches_frozen"] for s in STAGES):
                raise ValueError("Frozen metrics failed recomputation")
            drafts["process_analyst"] = call_role("process_analyst", {
                "observations": case.get_observed_state(), "assessments": detector})
            process_claims = candidates()
            drafts["context_analyst"] = call_role("context_analyst", {
                "process_claims": process_claims, "context": case.get_available_context(),
                "assessments": detector,
                "declared_plans": case.store.config["peer_plans"],
                "questions": drafts["process_analyst"]["recommended_checks"],
                "warning_stages": [r["data"]["stage"] for r in detector if r["data"]["long_stage_warning"] is True]})
            claims = candidates()
            verdicts = checked_verdicts(call_role("evidence_verifier", verify_packet(claims)), claims)
            revise = {c["origin_role"] for c in claims if c["code_gate"] != "passed" or verdicts[c["claim_id"]]["verdict"] == "revise"}
            if revise:
                # One cycle only; the fourth role request must submit a corrected result.
                for role in ROLES[:2]:
                    if role in revise:
                        drafts[role] = call_role(role, {"previous": drafts[role], "claims": claims,
                            "verdicts": verdicts,
                            "evidence": [case.resolve_evidence(eid) for eid in sorted(visible_evidence[role])],
                            "instruction": "Choose claim IDs from the supplied catalog, or drop unsupported claims. "
                                           "Keep uncertainty when peers are insufficient."}, revision=True)
                claims = candidates()
                verdicts = checked_verdicts(call_role("evidence_verifier", verify_packet(claims), revision=True), claims)
            for claim in claims:
                verdict = verdicts[claim["claim_id"]]
                item = {**claim, "verifier_verdict": verdict}
                accepted = claim["code_gate"] == "passed" and verdict["verdict"] == "accept"
                if accepted:
                    # A consumer must see denominators, coverage and limitations alongside
                    # the selected scalar, not an isolated number stripped of context.
                    item["supporting_evidence"] = ([case.resolve_evidence(eid) for eid in claim["evidence_ids"]]
                        if "metadata_kind" in claim else case.resolve_evidence(claim["evidence_id"]))
                if "metadata_kind" in claim:
                    target = ("verified_notes" if claim["metadata_kind"] == "unknown" else "verified_recommended_checks") if accepted else "rejected_metadata"
                else:
                    target = "published_claims" if accepted else "rejected_claims"
                baseline[target].append(item)
            # Compatibility summaries contain only verified metadata. Never
            # union unchecked strings from analyst drafts into the final output.
            baseline["recommended_checks"] = sorted({x["code"] for x in baseline["verified_recommended_checks"]})
            baseline["unknowns"] = sorted({"cause_unverified", *[x["code"] for x in baseline["verified_notes"]]})
            baseline["metadata_verification_complete"] = True
            published = baseline["published_claims"] or baseline["verified_notes"] or baseline["verified_recommended_checks"]
            rejected = baseline["rejected_claims"] or baseline["rejected_metadata"]
            baseline["explanation_status"] = ("evidence_checked_with_rejections" if rejected else "evidence_checked") if published else "inconclusive"
        except BudgetBlocked as exc:
            baseline.update(explanation_status="unavailable_budget", error=str(exc))
        except ProviderUnavailable as exc:
            baseline.update(explanation_status="unavailable_api", error=str(exc))
        except (KeyError, TypeError, ValueError) as exc:
            baseline.update(explanation_status="inconclusive", error=f"Contract rejected: {type(exc).__name__}: {exc}")
        baseline.update(request_counts=counts, evidence=list(case.registry.values()),
                        explanation_seconds=time.perf_counter() - started,
                        detector_latency_measured_here=False,
                        human_review_status="pending_not_an_independent_label")
        if sha([case.get_frozen_stage_assessment(s) for s in STAGES]) != detector_hash:
            raise RuntimeError("Detector mutation detected")
        return baseline
