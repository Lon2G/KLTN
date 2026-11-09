"""Shared evidence, output limits and code gates for development comparisons.

Imports the frozen v3 implementation without changing its files or snapshots.
These checks assess source consistency, never independent bottleneck accuracy.
"""
from copy import deepcopy
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/05_multi_agent"))
from agent_contracts import (CONTRACT_VERSION, ROLES, catalog_view, claim_catalog,
                            gate, materialize_selection, obj, schemas, validate)
from evidence_tools import STAGES, sha
from metadata_contracts import metadata_catalog, metadata_gate, metadata_view

DOMAINS = {"process": "process_analyst", "context": "context_analyst"}


def initial_evidence(case):
    contract = case.check_temporal_and_comparison_contract()["data"]
    if not contract["no_future_stage_endpoints"] or not contract["case_excluded_from_training"]:
        raise ValueError("Invalid temporal or split contract")
    assessments = [case.get_frozen_stage_assessment(stage) for stage in STAGES]
    if not all(case.recompute_reported_metrics(s)["data"]["matches_frozen"] for s in STAGES):
        raise ValueError("Frozen metrics do not match source")
    records = [case.get_observed_state(), *assessments, case.get_available_context()]
    return {"observations": records[0], "assessments": assessments, "context": records[-1]}, {r["evidence_id"] for r in records}


def catalogs(case, visible):
    return {domain: {"claims": claim_catalog(case, role, visible),
                     "metadata": metadata_catalog(case, role, visible)} for domain, role in DOMAINS.items()}


def catalog_packet(choices):
    return {domain: {"claim_catalog": catalog_view(c["claims"]), "metadata_catalog": metadata_view(c["metadata"])}
            for domain, c in choices.items()}


def selection_schema(case, choices):
    return obj({domain: schemas(role, case.store.config["peer_plans"], choices[domain]["claims"],
                metadata=choices[domain]["metadata"])[-1]["parameters"] for domain, role in DOMAINS.items()})


def compile_selection(case, selection, choices):
    validate(selection, selection_schema(case, choices))
    items = []
    for domain, role in DOMAINS.items():
        c = choices[domain]
        draft = materialize_selection(case, role, selection[domain], c["claims"], c["metadata"])
        items.extend(gate(case, role, claim) for claim in draft["claims"])
        items.extend(metadata_gate(case, role, item) for item in draft["unknowns"] + draft["recommended_checks"])
    if any(item["code_gate"] != "passed" for item in items):
        raise ValueError("Selected item failed source verification")
    return items


def base_result(case, arm, execution_kind, run_key):
    return {"version": "agent_comparison_result_v1", "contract_version": CONTRACT_VERSION,
            "arm": arm, "execution_kind": execution_kind, "run_key": run_key,
            "case_key": case.case_key, "as_of_event_time": case.asof, "mode": case.mode,
            "assessment_revision": case.revision, "detector": [case.get_frozen_stage_assessment(s) for s in STAGES],
            "published_claims": [], "verified_notes": [], "verified_recommended_checks": [],
            "rejected_claims": [], "rejected_metadata": [], "unknowns": ["cause_unverified"],
            "recommended_checks": [], "cause_status": "unverified", "ground_truth": None,
            "independent_accuracy_evaluation": False, "human_review_status": "pending_not_an_independent_label",
            "trace": [], "raw_agent_outputs": [], "contract_violations": [],
            "explanation_status": "pending", "agent_verifier_used": False,
            "publication_scope": "code_checked_sources_not_independent_accuracy_or_proven_solution"}


def publish_code_checked(case, result, items):
    """Used only by controls; do not invent a verifier verdict for these arms."""
    for original in items:
        item = deepcopy(original)
        if "metadata_kind" in item:
            item["supporting_evidence"] = [case.resolve_evidence(eid) for eid in item["evidence_ids"]]
            bucket = "verified_notes" if item["metadata_kind"] == "unknown" else "verified_recommended_checks"
        else:
            item["supporting_evidence"] = case.resolve_evidence(item["evidence_id"])
            bucket = "published_claims"
        item["verification_method"] = "deterministic_source_gate_only_no_AI_verifier"
        result[bucket].append(item)
    result["unknowns"] = sorted({"cause_unverified", *[x["code"] for x in result["verified_notes"]]})
    result["recommended_checks"] = sorted({x["code"] for x in result["verified_recommended_checks"]})
    result["explanation_status"] = "code_checked" if items else "inconclusive"


def source_metrics(case, result):
    """Re-check published values and disclosed observations; no accuracy labels."""
    if result is None:
        return {"status": "not_run", "source_supported_fraction": None, "warning_stage_coverage": None,
                "missing_endpoint_coverage": None, "timeline_conflict_coverage": None,
                "human_usefulness": None, "independent_accuracy": None}
    if (result["contract_version"] != CONTRACT_VERSION or result["case_key"] != case.case_key or
            result["mode"] != case.mode or result["as_of_event_time"] != case.asof or
            result["assessment_revision"] != case.revision):
        raise ValueError("Result belongs to another contract/case/time/mode")
    expected = [case.get_frozen_stage_assessment(s) for s in STAGES]
    if sha(result["detector"]) != sha(expected):
        raise ValueError("Comparison arm changed the frozen detector")
    source_ok, seen, valid = [], set(), []
    all_items = result["published_claims"] + result["verified_notes"] + result["verified_recommended_checks"]
    duplicate_count = 0
    for item in all_items:
        if "metadata_kind" in item:
            payload = {k: v for k, v in item.items() if k in
                       {"metadata_kind", "code", "stage", "field", "evidence_ids", "evidence_fields", "requirements", "rule_version", "meaning"}}
            checked = metadata_gate(case, item["origin_role"], payload)
            evidence = [case.resolve_evidence(eid) for eid in item["evidence_ids"]]
        else:
            payload = {k: item[k] for k in ("claim_type", "evidence_id", "field", "value")}
            checked = gate(case, item["origin_role"], payload)
            evidence = case.resolve_evidence(item["evidence_id"])
        key = sha(payload)
        if key in seen:
            duplicate_count += 1
            continue
        seen.add(key)
        good = checked["code_gate"] == "passed" and item.get("supporting_evidence") == evidence
        source_ok.append(good)
        if good:
            valid.append(item)
    # Coverage describes faithful reporting of frozen observations, not whether
    # an order is truly bottlenecked. Zero eligible observations -> null, not 100%.
    warnings = {s for s in STAGES if case.scores[s]["long_stage_warning"] is True}
    covered_warnings = {i.get("stage") for i in valid if i.get("field") == "long_stage_warning" and i.get("value") is True}
    missing = {(s, field) for s in STAGES for field in ("start_timestamp", "end_timestamp") if case.scores[s][field] is None}
    covered_missing = {(i["stage"], i["field"]) for i in valid if i.get("code") == "stage_endpoint_not_observed"}
    conflicts = {s for s in STAGES if case.scores[s]["signed_duration_seconds"] is not None and case.scores[s]["signed_duration_seconds"] < 0}
    covered_conflicts = {i["stage"] for i in valid if i.get("code") == "timeline_conflict"}
    fraction = lambda target, found: len(target & found) / len(target) if target else None
    return {"status": result["explanation_status"], "detector_unchanged": True,
            "distinct_published_items": len(source_ok), "duplicate_published_items": duplicate_count,
            "source_supported_items": sum(source_ok), "source_supported_fraction": sum(source_ok) / len(source_ok) if source_ok else None,
            "warning_stages": len(warnings), "warning_stage_coverage": fraction(warnings, covered_warnings),
            "missing_endpoints": len(missing), "missing_endpoint_coverage": fraction(missing, covered_missing),
            "conflicting_stages": len(conflicts), "timeline_conflict_coverage": fraction(conflicts, covered_conflicts),
            "human_usefulness": None, "independent_accuracy": None,
            "metric_scope": "engineering_source_consistency_not_bottleneck_ground_truth",
            "model_performance_eligible": result["execution_kind"] == "live_openai_api"}
