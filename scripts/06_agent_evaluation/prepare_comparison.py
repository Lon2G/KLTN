"""Prepare a real Olist rules control and pending comparison slots, entirely offline."""
import argparse
from collections import Counter
from contextlib import ExitStack
import json
from pathlib import Path
from unittest.mock import patch

from comparison_core import ROOT, initial_evidence, source_metrics, sha
from comparison_runners import run_rules
from audit_readiness import digest
from evidence_tools import EvidenceStore

PILOT = ROOT / "data/experiments/olist_multi_agent_tools_v3"
PROTOCOL = ROOT / "templates/agent_comparison_v1/protocol.json"
DEFAULT = ROOT / "data/experiments/olist_agent_comparison_v1"


def read(path):
    return json.loads(Path(path).read_text())


def save(path, data):
    with path.open("x") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def verify_manifest(directory):
    directory = Path(directory).resolve()
    manifest = read(directory / "manifest.json")
    for section, base in [("source_files", ROOT), ("implementation_hashes", ROOT), ("output_hashes", directory)]:
        if not manifest.get(section):
            raise ValueError("Missing integrity map")
        for name, expected in manifest[section].items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()) or not path.is_file() or digest(path) != expected:
                raise ValueError("Missing, changed or unsafe snapshot file: " + name)
    return True


def implementation_hashes():
    return {str(p.relative_to(ROOT)): digest(p) for p in sorted(Path(__file__).parent.glob("*.py"))}


def validate_protocol(protocol):
    budget = read(ROOT / "templates/multi_agent_v1/budget.json")
    integration = read(ROOT / "templates/multi_agent_v1/integration.json")
    expected = {"native_bounded": (4, 12), "matched_4": (4, 4)}
    if set(protocol["profiles"]) != set(expected):
        raise ValueError("Protocol profiles differ from implemented adapters")
    for name, (single, multi) in expected.items():
        profile = protocol["profiles"][name]
        if (profile["single_agent_max_requests"], profile["three_agents_max_requests"], profile["rules_max_requests"]) != (single, multi, 0):
            raise ValueError("Protocol request cap differs from implemented adapters")
    if budget["max_requests_per_role_per_case"] != 4 or budget["max_requests_per_case"] != 12:
        raise ValueError("Shared budget request policy changed")
    for field in ["max_input_tokens_per_request", "max_output_tokens_per_request"]:
        if protocol["limits"][field] != budget[field]:
            raise ValueError("Protocol token cap differs from shared budget")
    if protocol["model"] != integration["provider"]["model"] or protocol["mode"] != "assumed_static_context_at_purchase":
        raise ValueError("Model or event context mode differs")
    if tuple(protocol["limits"][k] for k in ["claims_per_domain", "unknowns_per_domain", "checks_per_domain"]) != (3, 3, 2):
        raise ValueError("Output caps differ from frozen v3 contract")
    return True


def prepare(output_dir=DEFAULT):
    output_dir = Path(output_dir).resolve()
    if output_dir.parent != (ROOT / "data/experiments").resolve() or output_dir.exists():
        raise ValueError("Use a new direct child of data/experiments; never overwrite a snapshot")
    verify_manifest(PILOT)
    protocol = read(PROTOCOL)
    validate_protocol(protocol)
    pilot = read(PILOT / "pilot.json")
    if len(pilot) != 20 or protocol["new_model_calls_in_this_package"] != 0:
        raise ValueError("Unexpected development cohort or execution mode")
    ledger = ROOT / "data/runtime/multi_agent_budget.sqlite"
    protected = read(ROOT / "data/experiments/olist_multi_agent_implementation_v2/preservation_manifest.json")["protected_file_hashes"]
    # Preserve today's ledger state across this offline preparation. Historical
    # experiment inputs are immutable; later approved ledger additions are valid.
    protected = {n: h for n, h in protected.items() if n != str(ledger.relative_to(ROOT))}
    protected.update({str(p.relative_to(ROOT)): digest(p) for p in PILOT.iterdir() if p.is_file()})
    protected.update(read(PILOT / "manifest.json")["implementation_hashes"])
    protected[str(ledger.relative_to(ROOT))] = digest(ledger)
    if not all(digest(ROOT / n) == h for n, h in protected.items()):
        raise ValueError("Protected historical input changed")
    historical_receipts = [ROOT / "data/experiments/olist_multi_agent_live_v2_case0/provider_receipts.json",
                           ROOT / "data/experiments/olist_multi_agent_live_v2_batch5_2026-11-04/provider_receipts.json"]
    prior_keys = {r["case_key"] for r in read(historical_receipts[0])["runs"]["v2"]}
    prior_keys.update(r["budget_request"]["case_key"] for r in read(historical_receipts[1])["receipts"])
    if prior_keys != {pilot[i]["case_key"] for i in protocol["cases_already_called_with_v2"]}:
        raise ValueError("Declared prior model exposure differs from saved real receipts")
    output_dir.mkdir()
    (output_dir / "rules_results").mkdir()
    summaries, common_packets, slots, review, key_rows, cohort = [], [], [], [], [], []
    with ExitStack() as guard:
        network = guard.enter_context(patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline preparation forbids HTTP")))
        guard.enter_context(patch("provider.ResponsesProvider.call", side_effect=AssertionError("Offline preparation forbids API")))
        store = EvidenceStore()
        for index, row in enumerate(pilot):
            case = store.case(row["order_id"], row["event_id"], protocol["mode"])
            packet, _ = initial_evidence(case)
            common_packets.append({"case_key": case.case_key, "input_fingerprint": sha(packet), "evidence": packet})
            result = run_rules(case)
            metrics = source_metrics(case, result)
            relative = "rules_results/" + case.case_key + ".json"
            save(output_dir / relative, result)
            summaries.append({"case_index": index, "case_key": case.case_key, "stratum": row["stratum"],
                "metrics": metrics, "local_seconds": result["explanation_seconds"], "model_requests": 0, "API_cost_usd": 0})
            cohort.append({"case_index": index, **row, "split_use": "development",
                "prior_v2_API_exposure": index in protocol["cases_already_called_with_v2"],
                "developer_unseen": False, "input_fingerprint": sha(packet)})
            case_slots = [{"case_key": case.case_key, "arm": "rules", "profile": "shared_control",
                           "status": "completed_rules_only", "result_file": relative, "metrics": metrics,
                           "model_requests": 0, "API_cost_usd": 0}]
            for profile in protocol["profiles"]:
                for arm in ["single_agent", "three_agents"]:
                    case_slots.append({"case_key": case.case_key, "arm": arm, "profile": profile,
                        "status": "not_run", "result_file": None, "metrics": source_metrics(case, None),
                        "model_requests": None, "API_cost_usd": None})
            slots.extend(case_slots)
            for ordinal, slot in enumerate(sorted(case_slots, key=lambda s: sha(["review-v1", case.case_key, s["arm"], s["profile"]]))):
                review_id = "review-" + sha([case.case_key, ordinal])[:16]
                # This is an empty review registry, not fabricated AI output.
                review.append({"review_id": review_id, "case_key": case.case_key, "display_label": chr(65 + ordinal),
                    "output_ready": slot["result_file"] is not None, "review_kind": protocol["human_review"]["review_kind"],
                    "reviewer": None, "reviewed_at": None,
                    "ratings": {criterion: None for criterion in protocol["human_review"]["criteria"]},
                    "not_assessable_reasons": {}, "notes": None})
                key_rows.append({"review_id": review_id, "arm": slot["arm"], "profile": slot["profile"], "result_file": slot["result_file"]})
        if network.call_count:
            raise ValueError("Unexpected network attempt")
    unchanged = all(digest(ROOT / n) == h for n, h in protected.items())
    if not unchanged:
        raise ValueError("Offline preparation mutated historical evidence, implementation or ledger")
    n_support = [row["metrics"]["source_supported_fraction"] for row in summaries]
    summary = {"version": "agent_comparison_preparation_v1", "status": "offline_controls_ready_AI_comparison_pending",
        "real_olist_cases": len(pilot), "strata": dict(Counter(r["stratum"] for r in pilot)),
        "rules_outputs": len(summaries), "pending_AI_slots": sum(s["status"] == "not_run" for s in slots),
        "completed_paired_AI_comparisons": 0, "ranking": None, "selected_best_arm": None,
        "why_no_ranking": "No new single-agent or three-agent results; useful explanations also require independent review.",
        "all_rules_source_checks_passed": all(x == 1 for x in n_support),
        "rules_observation_coverage": {key: {"applicable_cases": sum(r["metrics"][key] is not None for r in summaries),
            "fully_covered_cases": sum(r["metrics"][key] == 1 for r in summaries)}
            for key in ["warning_stage_coverage", "missing_endpoint_coverage", "timeline_conflict_coverage"]},
        "prior_v2_API_cases": sum(r["prior_v2_API_exposure"] for r in cohort), "developer_unseen_cases": 0,
        "new_API_requests": 0, "additional_API_cost_usd": 0, "new_model_inference": False,
        "ground_truth_labels_created": 0, "human_ratings_completed": 0,
        "protected_file_count": len(protected), "protected_files_unchanged": unchanged,
        "shared_ledger_unchanged": True, "frozen_v3_implementation_unchanged": True,
        "research_claim": "offline_rules_control_and_tested_comparison_infrastructure_not_AI_quality",
        "remaining": ["advisor_feedback_on_provisional_rubric", "separately_approved_live_paired_runs",
                      "actual_token_cost_latency_accounting", "human_explanation_review", "repeated_run_stability"]}
    save(output_dir / "protocol.json", protocol)
    save(output_dir / "cohort.json", cohort)
    save(output_dir / "common_evidence.json", common_packets)
    save(output_dir / "rules_metrics.json", summaries)
    save(output_dir / "comparison_slots.json", slots)
    save(output_dir / "review_template.json", review)
    save(output_dir / "review_key.json", key_rows)
    save(output_dir / "preservation.json", {"protected_file_hashes_before_and_after": protected, "unchanged": unchanged})
    save(output_dir / "summary.json", summary)
    sources = {str(PROTOCOL.relative_to(ROOT)): digest(PROTOCOL)}
    sources.update({str(p.relative_to(ROOT)): digest(p) for p in PILOT.iterdir() if p.is_file()})
    sources.update(store.source_hashes)
    sources.update({str(p.relative_to(ROOT)): digest(p) for p in historical_receipts})
    code = {**read(PILOT / "manifest.json")["implementation_hashes"], **implementation_hashes()}
    save(output_dir / "manifest.json", {"version": summary["version"], "source_files": sources,
        "implementation_hashes": code, "output_hashes": {str(p.relative_to(output_dir)): digest(p) for p in output_dir.rglob("*") if p.is_file()},
        "mutable_ledger_is_not_a_manifest_source": True, "no_live_API_execution": True})
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--prepare", action="store_true")
    group.add_argument("--verify", type=Path)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    result = {"integrity_ok": verify_manifest(args.verify)} if args.verify else prepare(args.output_dir)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
