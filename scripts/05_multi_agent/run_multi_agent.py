"""Prepare a real validation pilot offline, or explicitly run budgeted model calls."""
import argparse
from collections import Counter
import json
import os
from pathlib import Path
import time

from api_budget import BudgetLedger, canonical
from agent_contracts import CONTRACT_VERSION
from audit_readiness import digest, inspect_manifest
from coordinator import Coordinator
from evidence_tools import EvidenceStore, ROOT, SNAPSHOT, STAGES, sha
from provider import ResponsesProvider, ProviderUnavailable

DEFAULT = ROOT / "data/experiments/olist_multi_agent_tools_v3"
LEDGER = ROOT / "data/runtime/multi_agent_budget.sqlite"


def save(path, value):
    with path.open("x") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def implementation_hashes():
    paths = sorted((ROOT / "scripts/05_multi_agent").glob("*.py"))
    paths += sorted((ROOT / "templates/multi_agent_v1").glob("*.json"))
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def prepare(destination=DEFAULT):
    destination = Path(destination).resolve()
    if destination.parent != (ROOT / "data/experiments").resolve() or destination.exists():
        raise ValueError("Use a new direct child of data/experiments; existing snapshots are immutable")
    for name in ["data/experiments/olist_event_replay_v1", SNAPSHOT]:
        check = inspect_manifest(ROOT, name)
        if not check["integrity_ok"] or not check["source_code_available"]:
            raise ValueError("Frozen pipeline integrity failed")
    start = time.perf_counter()
    store = EvidenceStore()
    pilot = store.pilot()
    destination.mkdir()
    save(destination / "pilot.json", pilot)
    statuses = Counter()
    assessment_checks = Counter()
    packets = []
    review = []
    for row in pilot:
        case = store.case(row["order_id"], row["event_id"])
        case.get_observed_state()
        case.get_available_context()
        for stage in STAGES:
            case.get_frozen_stage_assessment(stage)
            metric_check = case.recompute_reported_metrics(stage)["data"]
            assessment_checks["scored" if metric_check["scored"] else "unassessed"] += 1
            if not metric_check["matches_frozen"]:
                raise ValueError("Metric recomputation failed")
            for plan in store.config["peer_plans"]:
                comparison = case.compare_declared_peer_groups(stage, plan)
                statuses[comparison["data"]["status"]] += 1
        case.check_temporal_and_comparison_contract()
        strict = store.case(row["order_id"], row["event_id"], "verified_availability_only")
        if any(r["delay_score_0_100"] is not None for r in strict.scores.values()):
            raise ValueError("Verified-only mode unexpectedly scored static context")
        packets.append({"case_key": case.case_key, "stratum": row["stratum"],
                        "evidence": list(case.registry.values()),
                        "strict_mode_assessments": [strict.get_frozen_stage_assessment(s) for s in STAGES]})
        review.append({"case_key": case.case_key, "review_kind": "explanation_quality_not_bottleneck_ground_truth",
                       "reviewer": None, "reviewed_at": None, "numbers_match_sources": None,
                       "context_assumption_disclosed": None, "comparison_support_disclosed": None,
                       "uncertainty_respected": None, "useful_next_checks": None, "notes": None})
    save(destination / "evidence_packets.json", packets)
    save(destination / "explanation_review_template.json", review)
    summary = {"version": "multi_agent_tools_preflight_v3", "contract_version": CONTRACT_VERSION, "cases": len(pilot),
               "strata": dict(Counter(r["stratum"] for r in pilot)), "real_olist_only": True,
               "comparison_statuses": dict(statuses), "stage_metric_recomputations": len(pilot) * 3,
               "assessment_checks": dict(assessment_checks),
               "verified_only_stage_checks": len(pilot) * 3, "provider_calls": 0,
               "model_inference_performed": False, "ground_truth_labels_created": 0,
               "peer_results_are_historical_descriptions": True,
               "api_key_in_environment": bool(os.environ.get("OPENAI_API_KEY", "").strip()),
               "remaining": ["live_provider_smoke_test", "human_explanation_review",
                             "baseline_comparison", "dashboard_integration"],
               "seconds": time.perf_counter() - start}
    save(destination / "summary.json", summary)
    save(destination / "manifest.json", {"version": "multi_agent_tools_preflight_v3", "contract_version": CONTRACT_VERSION,
        "source_files": store.source_hashes, "implementation_hashes": implementation_hashes(),
        "output_hashes": {p.name: digest(p) for p in destination.iterdir() if p.is_file()},
        "pilot_hash": sha(pilot), "research_claim": "engineering_preflight_not_AI_performance"})
    return summary


def run_api(pilot_dir, index, run_key):
    pilot_dir = Path(pilot_dir)
    manifest = json.loads((pilot_dir / "manifest.json").read_text())
    if manifest.get("contract_version") != CONTRACT_VERSION:
        raise ValueError("Pilot contract version differs; prepare a new snapshot, preserve previous results")
    for section, base in [("source_files", ROOT), ("implementation_hashes", ROOT), ("output_hashes", pilot_dir)]:
        for name, expected in manifest[section].items():
            path = (base / name).resolve()
            if not path.is_relative_to(base.resolve()) or digest(path) != expected:
                raise ValueError("Pilot inputs or implementation changed; do not silently reuse the pilot")
    pilot = json.loads((pilot_dir / "pilot.json").read_text())
    if sha(pilot) != manifest["pilot_hash"] or not 0 <= index < len(pilot):
        raise ValueError("Invalid frozen pilot / index")
    # No runtime ledger is created when credentials are absent.
    if not os.environ.get("OPENAI_API_KEY", "").strip():
        raise ProviderUnavailable("Set OPENAI_API_KEY locally; never paste it into the chat")
    store = EvidenceStore()
    row = pilot[index]
    case = store.case(row["order_id"], row["event_id"])
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    result_dir = ROOT / "data/runtime/multi_agent_results"
    result_dir.mkdir(exist_ok=True)
    destination = result_dir / (sha({"run": run_key, "case": case.case_key}) + ".json")
    if destination.exists():
        raise ValueError("Saved run already exists; inspect it rather than silently spending again")
    with BudgetLedger(LEDGER) as ledger:
        provider = ResponsesProvider(ledger)
        result = Coordinator(provider, run_key).run(case)
        result["budget"] = ledger.snapshot()
        save(destination, result)
    return {"result": str(destination), "status": result["explanation_status"],
            "request_counts": result["request_counts"], "budget": result["budget"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    operation = parser.add_mutually_exclusive_group(required=True)
    operation.add_argument("--prepare", action="store_true")
    operation.add_argument("--run-api", action="store_true")
    parser.add_argument("--pilot-dir", type=Path, default=DEFAULT)
    parser.add_argument("--case-index", type=int, default=0)
    parser.add_argument("--run-key", default="multi-agent-pilot-v3")
    args = parser.parse_args()
    try:
        result = prepare(args.pilot_dir) if args.prepare else run_api(args.pilot_dir, args.case_index, args.run_key)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except (ValueError, ProviderUnavailable) as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}, ensure_ascii=False))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
