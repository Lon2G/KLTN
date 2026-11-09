"""Audit original v2 notes against v3 gates offline; never rewrite model answers.

This checks deterministic guards on known development failures, not new AI quality.
Legacy strings can have eligible evidence options, but that is not a new model
selection or verifier approval. New wire contracts reject those legacy strings.
"""
import argparse
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
from unittest.mock import patch

from agent_contracts import CONTRACT_VERSION, claim_catalog, gate, schemas, validate
from audit_readiness import digest
from evidence_tools import EvidenceStore, ROOT, STAGES, sha
from metadata_contracts import metadata_catalog, metadata_gate
from replay_saved_agent_outputs import reconstruct_case
from run_multi_agent import implementation_hashes, save

PILOT = ROOT / "data/experiments/olist_multi_agent_tools_v2/pilot.json"
CASE0 = ROOT / "data/experiments/olist_multi_agent_live_v2_case0"
BATCH = ROOT / "data/experiments/olist_multi_agent_live_v2_batch5_2026-11-04"
PRESERVED = ROOT / "data/experiments/olist_multi_agent_implementation_v2/preservation_manifest.json"
DEFAULT = ROOT / "data/experiments/olist_multi_agent_metadata_regression_v3"


def read(path):
    return json.loads(Path(path).read_text())


def records_visible_to_role(case, saved, role):
    ids = set()

    def visit(value):
        if isinstance(value, dict):
            if "kind" in value and "data" in value and value.get("evidence_id") in case.registry:
                ids.add(value["evidence_id"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    # All six original v2 cases had one terminal submission per analyst and no
    # revision. Refuse other histories rather than borrowing later evidence.
    terminal = [m for m in saved["messages"] if m["sender_role"] == role]
    if len(terminal) != 1 or terminal[0]["explanation_revision"] != 0:
        raise ValueError("This historical auditor requires one un-revised submission per role")
    for message in saved["messages"]:
        if message["message_id"] == terminal[0]["message_id"]:
            break
        if message["sender_role"] == "scheduler" and message["recipient_role"] == role:
            visit(message["body"])
    for item in saved["trace"]:
        if item["kind"] == "tool_call" and item["role"] == role:
            ids.add(item["evidence_id"])
    return ids


def assess_legacy_metadata(case, saved):
    checks = []
    for role in ("process_analyst", "context_analyst"):
        visible_ids = records_visible_to_role(case, saved, role)
        visible = metadata_catalog(case, role, visible_ids)
        all_records = metadata_catalog(case, role, case.registry)
        body = next(m["body"] for m in saved["messages"] if m["sender_role"] == role)
        for field, kind in [("unknowns", "unknown"), ("recommended_checks", "recommended_check")]:
            for code in body[field]:
                eligible = {key: item for key, item in all_records.items() if item["code"] == code and item["metadata_kind"] == kind}
                shown = {key: item for key, item in eligible.items() if key in visible}
                if code == "cause_unverified" and kind == "unknown":
                    status = "system_limitation_retained_not_an_agent_claim"
                elif code == "missing_context":
                    status = "rejected_unscoped_context_note"
                elif shown:
                    status = "eligible_evidence_exists_not_a_new_model_selection"
                elif eligible:
                    status = "requires_evidence_in_role_packet"
                else:
                    status = "rejected_no_supporting_condition"
                # No eligibility result is published as a new agent selection.
                options = [metadata_gate(case, role, item) for item in shown.values()]
                if any(item["code_gate"] != "passed" for item in options):
                    raise ValueError("Catalog generated a gate-invalid metadata choice")
                checks.append({"origin_role": role, "legacy_field": field, "legacy_code": code,
                    "status": status, "eligible_visible_options": options,
                    "new_model_selection": False, "published_as_new_verified_metadata": False})
    return checks


def run_offline():
    preserved = read(PRESERVED)
    protected = preserved["protected_file_hashes"]
    if not all(digest(ROOT / name) == expected for name, expected in protected.items()):
        raise ValueError("Original data, receipts or shared ledger changed since v2 preservation")
    for name, expected in preserved["implementation_hashes"].items():
        if digest(PRESERVED.parent / name) != expected:
            raise ValueError("Archived v2 implementation changed")
    receipts = {r["request_id"]: r["response"] for r in read(CASE0 / "provider_receipts.json")["runs"]["v2"]}
    receipts.update({r["request_id"]: r["response"] for r in read(BATCH / "provider_receipts.json")["receipts"]})
    pilot = read(PILOT)
    reports = []
    with patch("provider.ResponsesProvider.call", side_effect=AssertionError("Live API forbidden in offline regression")), \
         patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("HTTP forbidden in offline regression")) as network:
        store = EvidenceStore()
        for index in (0, 1, 2, 8, 9, 16):
            row = pilot[index]
            path = ROOT / "data/runtime/multi_agent_results" / (sha({"run": "multi-agent-pilot-v2", "case": row["case_key"]}) + ".json")
            saved = read(path)
            original = deepcopy(saved)
            if saved["execution_kind"] != "live_openai_api" or saved["contract_version"] != "multi_agent_contract_v2":
                raise ValueError("Expected original v2 API results")
            model_trace = [r for r in saved["trace"] if r["kind"] == "model_request"]
            for trace, output in zip(model_trace, saved["raw_agent_outputs"], strict=True):
                response = receipts[trace["request_id"]]
                if response["id"] != trace["response_id"] or response["output"] != output["output"]:
                    raise ValueError("Raw model output differs from saved provider receipt")
            case = reconstruct_case(store, saved, PILOT)
            if saved["detector"] != [case.get_frozen_stage_assessment(s) for s in STAGES]:
                raise ValueError("Detector differs from frozen source")
            for stage in STAGES:
                if not case.recompute_reported_metrics(stage)["data"]["matches_frozen"]:
                    raise ValueError("Frozen metric recomputation failed")
            for c in saved["published_claims"]:
                checked = gate(case, c["origin_role"], {k: c[k] for k in ["claim_type", "evidence_id", "field", "value"]})
                if checked["code_gate"] != "passed" or checked["claim_id"] != c["claim_id"]:
                    raise ValueError("Historical scalar fact no longer matches evidence")
            schema_rejections = []
            for role in ("process_analyst", "context_analyst"):
                visible = records_visible_to_role(case, saved, role)
                catalog = claim_catalog(case, role, visible)
                schema = schemas(role, store.config["peer_plans"], catalog, metadata=metadata_catalog(case, role, visible))[-1]["parameters"]
                submission = next(m["body"] for m in saved["messages"] if m["sender_role"] == role)
                try:
                    validate(submission, schema)
                    raise AssertionError("Legacy unchecked-string submission must not pass the new contract")
                except ValueError:
                    schema_rejections.append({"role": role, "legacy_wire_format_rejected": True})
            metadata_checks = assess_legacy_metadata(case, saved)
            if original != saved:
                raise ValueError("Historical response was modified")
            reports.append({"case_index": index, **row, "source_result": str(path.relative_to(ROOT)),
                "source_result_sha256": digest(path), "original_model_requests_verified": len(model_trace),
                "original_scalar_claims_still_match": len(saved["published_claims"]),
                "detector_unchanged": True, "metadata_checks": metadata_checks,
                "legacy_submission_checks": schema_rejections, "raw_model_responses_unchanged": True,
                "ground_truth_labels_created": 0, "human_review_status": saved["human_review_status"]})
        if network.call_count:
            raise ValueError("Offline audit attempted HTTP")
    items = [item for row in reports for item in row["metadata_checks"]]
    endpoint_cases = [row["case_index"] for row in reports if any(c["legacy_code"] == "stage_endpoint_not_observed" and c["status"] == "rejected_no_supporting_condition" for c in row["metadata_checks"])]
    if endpoint_cases != [2, 8, 9, 16]:
        raise ValueError("Known false endpoint notes were not all rejected")
    unchanged = all(digest(ROOT / name) == expected for name, expected in protected.items())
    if not unchanged:
        raise ValueError("Offline regression changed protected artifacts")
    summary = {"version": "saved_metadata_regression_v3", "contract_version": CONTRACT_VERSION,
        "cases": reports, "real_v2_cases": len(reports),
        "original_model_requests_verified": sum(r["original_model_requests_verified"] for r in reports),
        "historical_scalar_claims_still_match": sum(r["original_scalar_claims_still_match"] for r in reports),
        "metadata_entries_checked": len(items), "metadata_outcomes": dict(Counter(r["status"] for r in items)),
        "known_false_missing_endpoint_cases_blocked": endpoint_cases,
        "historical_submissions_rejected_by_new_schema": sum(len(r["legacy_submission_checks"]) for r in reports),
        "protected_files_unchanged": len(protected), "shared_ledger_unchanged": unchanged,
        "new_API_requests": 0, "additional_API_cost_usd": 0, "network_guard_attempts": 0,
        "new_model_inference": False, "AI_quality_improvement_demonstrated": False,
        "ground_truth_labels_created": 0, "original_responses_rewritten": False,
        "interpretation": "deterministic_gate_regression_on_known_v2_failures_not_new_AI_performance",
        "limitations": ["Legacy strings are audited for eligibility; code does not impersonate agent selections or verifier approval.",
                        "Old v2 responses are incompatible with the new wire schema; live v3 behavior remains untested.",
                        "Eligible follow-up checks are not confirmed causes or proven effective interventions."]}
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    destination = args.output_dir.resolve()
    if destination.parent != (ROOT / "data/experiments").resolve() or destination.exists():
        raise ValueError("Use a new direct child of data/experiments; preserve prior runs")
    summary = run_offline()
    destination.mkdir()
    save(destination / "summary.json", summary)
    sources = [PILOT, CASE0 / "provider_receipts.json", BATCH / "provider_receipts.json", PRESERVED]
    sources.extend(ROOT / r["source_result"] for r in summary["cases"])
    save(destination / "manifest.json", {"version": summary["version"],
        "source_files": {str(p.relative_to(ROOT)): digest(p) for p in sources},
        "implementation_hashes": implementation_hashes(),
        "output_hashes": {p.name: digest(p) for p in destination.iterdir() if p.is_file()},
        "research_claim": "offline_metadata_gate_regression_not_AI_accuracy"})
    print(json.dumps({k: v for k, v in summary.items() if k != "cases"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
