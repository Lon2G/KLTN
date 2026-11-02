"""Three separately prompted, tool-using agents with a bounded verification cycle.

The scheduler is deterministic; it is not counted as an AI agent. Provider output
is never executed as code. Detector output remains available if explanations fail.
"""
from copy import deepcopy
import json
import time

from agent_contracts import INSTRUCTIONS, ROLES, TOOLS, gate, schemas, validate
from api_budget import BudgetBlocked, canonical
from evidence_tools import STAGES, sha
from provider import ProviderUnavailable


class Coordinator:
    def __init__(self, provider, run_key="multi-agent-pilot-v1"):
        self.provider, self.run_key = provider, run_key

    def run(self, case):
        started = time.perf_counter()
        counts = {role: 0 for role in ROLES}
        messages, trace, drafts = [], [], {}
        detector = [case.get_frozen_stage_assessment(stage) for stage in STAGES]
        detector_hash = sha(detector)
        baseline = {"version": "coordinated_analysis_v1", "case_key": case.case_key,
                    "as_of_event_time": case.asof, "mode": case.mode, "assessment_revision": case.revision,
                    "run_key": self.run_key, "execution_kind": self.provider.execution_kind,
                    "detector": detector, "cause_status": "unverified",
                    "ground_truth": None, "independent_accuracy_evaluation": False,
                    "messages": messages, "trace": trace, "published_claims": [], "rejected_claims": [],
                    "recommended_checks": [], "explanation_status": "pending"}

        def message(sender, recipient, body, revision=0):
            item = {"case_key": case.case_key, "sender_role": sender, "recipient_role": recipient,
                    "as_of_event_time": case.asof, "assessment_revision": case.revision,
                    "explanation_revision": revision, "body": body,
                    "reply_to": messages[-1]["message_id"] if messages else None}
            item["message_id"] = sha(item)
            messages.append(item)
            return item

        def call_role(role, packet, revision=False):
            functions = schemas(role, case.store.config["peer_plans"])
            terminal = functions[-1]["name"]
            history = [{"role": "user", "content": canonical(message("scheduler", role, packet, int(revision)))}]
            limit = 4 if revision else 3
            while counts[role] < limit:
                counts[role] += 1
                force_submit = revision or counts[role] == limit
                payload = {"model": case.store.config["provider"]["model"],
                           "instructions": INSTRUCTIONS + "\nYour role is " + role,
                           "input": deepcopy(history), "tools": functions,
                           "tool_choice": {"type": "function", "name": terminal} if force_submit else "auto",
                           "parallel_tool_calls": False, "max_output_tokens": 1200,
                           "store": False, "reasoning": {"effort": "none"},
                           "service_tier": "default", "truncation": "disabled"}
                request_id = sha({"run": self.run_key, "case": case.case_key, "asof": case.asof,
                                  "event": case.events[-1]["event_id"], "mode": case.mode,
                                  "role": role, "ordinal": counts[role]})
                before = time.perf_counter()
                response = self.provider.call(request_id=request_id, case_key=case.case_key,
                    run_key=self.run_key, role=role, payload=payload)
                trace.append({"kind": "model_request", "role": role, "request_id": request_id,
                              "seconds": time.perf_counter() - before, "response_id": response.get("id")})
                output = response.get("output", [])
                calls = [r for r in output if r.get("type") == "function_call"]
                if len(calls) != 1:
                    raise ValueError("Agent must return exactly one function call")
                call = calls[0]
                available = {t["name"]: t for t in functions}
                if call["name"] not in available:
                    raise ValueError("Agent requested a tool outside its role")
                args = json.loads(call["arguments"])
                validate(args, available[call["name"]]["parameters"])
                if call["name"] == terminal:
                    message(role, "scheduler", args, int(revision))
                    return args
                if force_submit:
                    raise ValueError("Agent exceeded bounded tool loop")
                result = getattr(case, call["name"])(**args)
                trace.append({"kind": "tool_call", "role": role, "tool": call["name"],
                              "arguments": args, "evidence_id": result["evidence_id"]})
                history.extend(output)
                history.append({"type": "function_call_output", "call_id": call["call_id"], "output": canonical(result)})
            raise BudgetBlocked("Role request limit reached")

        def candidates():
            result = []
            for role, draft in drafts.items():
                result.extend(gate(case, role, c) for c in draft["claims"])
            unique = {c["claim_id"]: c for c in result}
            return list(unique.values())

        def verify_packet(claims):
            return {"claims": claims, "evidence": list({c["evidence_id"]: case.resolve_evidence(c["evidence_id"])
                    for c in claims if c["evidence_id"] in case.registry}.values()),
                    "contract": case.check_temporal_and_comparison_contract(),
                    "recomputed": [case.recompute_reported_metrics(s) for s in STAGES]}

        def verdict_map(response, claims):
            ids = {c["claim_id"] for c in claims}
            verdicts = response["verdicts"]
            if {v["claim_id"] for v in verdicts} != ids or len(verdicts) != len(ids):
                raise ValueError("Verifier must cover each claim exactly once")
            return {v["claim_id"]: v for v in verdicts}

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
                "declared_plans": case.store.config["peer_plans"],
                "questions": drafts["process_analyst"]["recommended_checks"],
                "warning_stages": [r["data"]["stage"] for r in detector if r["data"]["long_stage_warning"] is True]})
            claims = candidates()
            verdicts = verdict_map(call_role("evidence_verifier", verify_packet(claims)), claims)
            revise = {c["origin_role"] for c in claims if c["code_gate"] != "passed" or verdicts[c["claim_id"]]["verdict"] == "revise"}
            if revise:
                # One cycle only; the fourth role request must submit a corrected result.
                for role in ROLES[:2]:
                    if role in revise:
                        drafts[role] = call_role(role, {"previous": drafts[role], "claims": claims,
                            "verdicts": verdicts, "evidence": list(case.registry.values()),
                            "instruction": "Use existing evidence, correct or drop unsupported claims."}, revision=True)
                claims = candidates()
                verdicts = verdict_map(call_role("evidence_verifier", verify_packet(claims), revision=True), claims)
            for claim in claims:
                verdict = verdicts[claim["claim_id"]]
                item = {**claim, "verifier_verdict": verdict}
                accepted = claim["code_gate"] == "passed" and verdict["verdict"] == "accept"
                if accepted:
                    # A consumer must see denominators, coverage and limitations alongside
                    # the selected scalar, not an isolated number stripped of context.
                    item["supporting_evidence"] = case.resolve_evidence(claim["evidence_id"])
                baseline["published_claims" if accepted else "rejected_claims"].append(item)
            baseline["recommended_checks"] = sorted({x for d in drafts.values() for x in d["recommended_checks"]})
            baseline["unknowns"] = sorted({"cause_unverified", *[x for d in drafts.values() for x in d["unknowns"]]})
            baseline["explanation_status"] = "evidence_checked" if baseline["published_claims"] else "inconclusive"
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
