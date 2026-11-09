"""Evidence-bound uncertainty and follow-up eligibility, not causal conclusions.

Catalogs use only evidence already visible to a role. A proposed check describes
an eligible investigation; neither its necessity nor its efficacy is proven.
"""
from copy import deepcopy

from evidence_tools import sha

VERSION = "evidence_metadata_v1"
KINDS = ("unknown", "recommended_check")


def metadata_catalog(case, role, evidence_ids):
    if role not in {"process_analyst", "context_analyst"}:
        return {}
    allowed = {"frozen_stage_assessment", "observed_state"}
    if role == "context_analyst":
        allowed.update({"available_context", "peer_comparison"})
    records = [r for eid in sorted(set(evidence_ids)) if (r := case.resolve_evidence(eid))["kind"] in allowed]
    choices = {}

    def add(kind, code, record, fields, *, field=None, requirements=None, extra=()):
        evidence_fields = {record["evidence_id"]: {k: deepcopy(record["data"][k]) for k in fields}}
        for source in extra:
            evidence_fields[source["evidence_id"]] = {"long_stage_warning": source["data"]["long_stage_warning"]}
        item = {"metadata_kind": kind, "code": code, "stage": record["stage"], "field": field,
                "evidence_ids": sorted(evidence_fields), "evidence_fields": evidence_fields,
                "requirements": requirements or {}, "rule_version": VERSION,
                "meaning": "evidence_supported_limitation" if kind == "unknown" else
                           "eligible_follow_up_not_confirmed_cause_or_proven_solution"}
        if extra:
            item["stage"] = extra[0]["stage"]
        key = "m_" + sha({"role": role, "item": item})[:24]
        if key in choices and choices[key] != item:
            raise ValueError("Metadata identifier collision")
        choices[key] = item

    stages = [r for r in records if r["kind"] == "frozen_stage_assessment"]
    # Case-wide source limitations need one evidence option, not the same note
    # repeated for every stage. All selected options still retain their source.
    promise_sources = [r for r in records if r["kind"] == "observed_state"] or stages
    promise_id = promise_sources[0]["evidence_id"] if promise_sources else None
    availability_sources = [r for r in records if r["kind"] == "available_context"] or stages
    availability_id = availability_sources[0]["evidence_id"] if availability_sources else None
    for r in records:
        d, kind = r["data"], r["kind"]
        if kind == "frozen_stage_assessment":
            if r["evidence_id"] == availability_id and d.get("context_availability_basis") in {"assumed_static_at_observed_purchase_not_source_verified", "unverified_no_source_availability_history"}:
                fields = ["context_availability_basis", "context_assumption_used"]
                add("unknown", "context_availability_unverified", r, fields, field="context_availability_basis")
                add("recommended_check", "verify_context_availability", r, fields, field="context_availability_basis")
            for endpoint in ("start_timestamp", "end_timestamp"):
                if endpoint in d and d[endpoint] is None:
                    add("unknown", "stage_endpoint_not_observed", r, [endpoint, "observation_status"], field=endpoint)
                    add("recommended_check", "inspect_source_timestamps", r, [endpoint, "observation_status"], field=endpoint)
            if d.get("signed_duration_seconds") is not None and d["signed_duration_seconds"] < 0:
                fields = ["start_timestamp", "end_timestamp", "signed_duration_seconds"]
                add("unknown", "timeline_conflict", r, fields, field="signed_duration_seconds")
                add("recommended_check", "inspect_source_timestamps", r, fields, field="signed_duration_seconds")
            if d.get("long_stage_warning") is True:
                fields = ["long_stage_warning", "duration_days", "upper_tail_boundary_days"]
                add("recommended_check", "inspect_source_timestamps", r, fields, field="long_stage_warning")
                check = {"transport": "review_carrier_route", "preparation_handoff": "review_seller_preparation"}.get(r["stage"])
                if check:
                    add("recommended_check", check, r, fields, field="long_stage_warning")
        if r["evidence_id"] == promise_id and d.get("promise_basis_status") == "unverified_source_method_not_provided":
            add("unknown", "promise_basis_unverified", r, ["promise_basis_status"], field="promise_basis_status")
            add("recommended_check", "obtain_promise_method", r, ["promise_basis_status"], field="promise_basis_status")
        if kind == "available_context":
            if d.get("context_availability_source_verified") is False:
                add("unknown", "context_availability_unverified", r, ["status", "context_availability_source_verified"], field="context_availability_source_verified")
                add("recommended_check", "verify_context_availability", r, ["status", "context_availability_source_verified"], field="context_availability_source_verified")
            if d.get("status") == "available_under_explicit_assumption":
                for field in ("customer_state", "distance_band", "item_count"):
                    if field in d and d[field] in (None, ""):
                        add("unknown", "missing_context", r, [field], field=field)
                for flag in ("single_product_available", "single_seller_available"):
                    if d.get(flag) is False:
                        add("unknown", "attribution_unavailable", r, [flag], field=flag)
            if d.get("payment_and_review_features") == "not_available_in_this_event_contract":
                add("unknown", "feature_not_in_event_contract", r, ["payment_and_review_features"], field="payment_and_review_features")
            factors = d.get("uncontrolled_factors", [])
            for factor in factors:
                add("unknown", "uncontrolled_factor", r, ["uncontrolled_factors"], field=factor)
            for stage in stages:
                if stage["data"].get("long_stage_warning") is not True:
                    continue
                if "product_weight_dimensions" in factors:
                    add("recommended_check", "collect_weight_dimensions", r, ["uncontrolled_factors"], field="product_weight_dimensions", extra=[stage])
                if stage["stage"] == "preparation_handoff" and {"workload", "stock_availability"} <= set(factors):
                    add("recommended_check", "collect_workload_stock", r, ["uncontrolled_factors"], field="workload_and_stock_availability", extra=[stage])
        if kind == "peer_comparison":
            status = d.get("status")
            if status == "insufficient_comparable_orders":
                # A failed regional comparison may have enough orders but poor
                # matched coverage. Do not call that an insufficient sample count.
                if type(d.get("n")) is int and type(d.get("minimum_n")) is int and d["n"] < d["minimum_n"]:
                    fields = ["status", "n", "minimum_n", "plan"]
                    add("unknown", "insufficient_comparable_orders", r, fields, field="n")
                    add("recommended_check", "collect_more_comparable_history", r, fields, field="n")
                for field in ("target_n", "control_n"):
                    threshold = case.store.config["min_group_orders"]
                    if type(d.get(field)) is int and d[field] < threshold:
                        req = {"min_group_orders": threshold}
                        add("unknown", "insufficient_comparable_orders", r, ["status", field, "plan"], field=field, requirements=req)
                        add("recommended_check", "collect_more_comparable_history", r, ["status", field, "plan"], field=field, requirements=req)
                for field in ("target_coverage", "control_coverage"):
                    key = "min_" + field
                    if type(d.get(field)) in (int, float) and d[field] < case.store.config[key]:
                        req = {key: case.store.config[key]}
                        fields = ["status", field, "target_n", "control_n", "plan"]
                        add("unknown", "insufficient_comparison_coverage", r, fields, field=field, requirements=req)
                        add("recommended_check", "collect_more_comparable_history", r, fields, field=field, requirements=req)
            blockers = {"stage_unassessed": "comparison_not_performed_stage_unassessed",
                        "context_availability_unverified": "comparison_blocked_context_availability",
                        "multi_seller_attribution_unavailable": "comparison_blocked_attribution",
                        "single_product_attribution_unavailable": "comparison_blocked_attribution",
                        "missing_or_ineligible_geography": "comparison_blocked_geography",
                        "alternatives_not_checked": "comparison_prerequisite_not_checked",
                        "geographic_attribution_not_allowed_for_this_stage": "comparison_not_allowed_for_stage"}
            if status in blockers:
                add("unknown", blockers[status], r, ["status", "plan"], field="status")
    return choices


def metadata_view(catalog):
    groups = {}
    for key, item in catalog.items():
        group = groups.setdefault(tuple(item["evidence_ids"]), {})
        group[key] = {k: item[k] for k in ("metadata_kind", "code", "stage", "field")}
    return [{"evidence_ids": list(ids), "choices": values} for ids, values in groups.items()]


def materialize_metadata(case, role, ids, catalog, kind, maximum):
    if len(ids) > maximum or len(ids) != len(set(ids)):
        raise ValueError("Too many or duplicate metadata selections")
    current = metadata_catalog(case, role, {eid for item in catalog.values() for eid in item["evidence_ids"]})
    result = []
    for key in ids:
        if key not in catalog or current.get(key) != catalog[key] or catalog[key]["metadata_kind"] != kind:
            raise ValueError("Metadata ID unavailable for this role, evidence revision or kind")
        result.append(deepcopy(catalog[key]))
    return result


def metadata_gate(case, role, item):
    key = "m_" + sha({"role": role, "item": item})[:24]
    result = {**deepcopy(item), "origin_role": role, "claim_id": key,
              "code_gate": "rejected", "reason": "unsupported_metadata_evidence"}
    try:
        catalog = metadata_catalog(case, role, item["evidence_ids"])
    except (ValueError, KeyError, TypeError):
        return result
    if catalog.get(key) == item:
        result.update(code_gate="passed", reason="metadata_rule_and_source_match")
    return result
