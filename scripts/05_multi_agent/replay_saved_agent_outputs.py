"""Replay real saved API outputs offline. This measures guards, not new AI quality."""
import argparse
from collections import defaultdict
from contextlib import closing
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
from unittest.mock import patch

from agent_contracts import (CONTRACT_VERSION, TOOLS, checked_verdicts, claim_catalog,
                            gate, schemas, validate)
from audit_readiness import digest
from coordinator import Coordinator
from evidence_tools import EvidenceStore, ROOT, STAGES
from run_multi_agent import implementation_hashes, save

RESULT = ROOT / "data/runtime/multi_agent_results/aa695ac621d975fa000376fede2284b4ae06ae4e0c19146ccc94cc26c06dbcee.json"
LEDGER = ROOT / "data/runtime/multi_agent_budget.sqlite"
PILOT = ROOT / "data/experiments/olist_multi_agent_tools_v1/pilot.json"
DEFAULT = ROOT / "data/experiments/olist_multi_agent_contract_regression_v2"


class SavedReceiptProvider:
    execution_kind = "offline_saved_receipt_replay_not_new_AI_inference"

    def __init__(self, result, ledger_path):
        self.pending = defaultdict(list)
        self.records = []
        with closing(sqlite3.connect(Path(ledger_path).resolve().as_uri() + "?mode=ro", uri=True)) as connection:
            for item in result["trace"]:
                if item["kind"] != "model_request":
                    continue
                row = connection.execute("SELECT response_json FROM provider_receipts WHERE request_id=?",
                                         (item["request_id"],)).fetchone()
                if row is None:
                    raise ValueError("Missing historical receipt")
                response = json.loads(row[0])
                if response.get("id") != item["response_id"] or response.get("status") != "completed":
                    raise ValueError("Receipt does not match the historical trace")
                self.pending[item["role"]].append(response)

    def call(self, **kwargs):
        role = kwargs["role"]
        if not self.pending[role]:
            raise ValueError("No saved response for this role; never fall back to live API")
        response = self.pending[role].pop(0)
        self.records.append({"role": role, "response_id": response["id"]})
        return deepcopy(response)


def reconstruct_case(store, saved, pilot_path=PILOT):
    rows = json.loads(Path(pilot_path).read_text())
    row = next(r for r in rows if r["case_key"] == saved["case_key"])
    case = store.case(row["order_id"], row["event_id"], saved["mode"])
    case.get_observed_state()
    case.get_available_context()
    case.check_temporal_and_comparison_contract()
    for stage in STAGES:
        case.get_frozen_stage_assessment(stage)
        case.recompute_reported_metrics(stage)
    for item in saved["trace"]:
        if item["kind"] != "tool_call":
            continue
        if item["tool"] not in TOOLS[item["role"]]:
            raise ValueError("Historical trace asks for an undeclared tool")
        getattr(case, item["tool"])(**item["arguments"])
    for record in saved["evidence"]:
        if case.registry.get(record["evidence_id"]) != record:
            raise ValueError("Saved evidence differs from source-derived evidence")
    return case


def run_offline(result_path=RESULT, ledger_path=LEDGER):
    result_path, ledger_path = Path(result_path), Path(ledger_path)
    before = {str(p.relative_to(ROOT)): digest(p) for p in [result_path, ledger_path, PILOT]}
    saved = json.loads(result_path.read_text())
    if saved["execution_kind"] != "live_openai_api":
        raise ValueError("Regression requires real saved model responses")
    with patch("provider.ResponsesProvider.call", side_effect=AssertionError("Live API forbidden in offline regression")), \
         patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("HTTP forbidden in offline regression")):
        store = EvidenceStore()
        case = reconstruct_case(store, saved)
        old_claims = [gate(case, c["origin_role"], {k: c[k] for k in
                      ["claim_type", "evidence_id", "field", "value"]}) for c in saved["rejected_claims"]]
        submission_checks = []
        for message in saved["messages"]:
            role = message["sender_role"]
            if role not in TOOLS:
                continue
            rejected = False
            try:
                if role == "evidence_verifier":
                    checked_verdicts(message["body"], old_claims)
                else:
                    catalog = claim_catalog(case, role, case.registry)
                    validate(message["body"], schemas(role, store.config["peer_plans"], catalog)[-1]["parameters"])
            except ValueError:
                rejected = True
            submission_checks.append({"role": role, "revision": message["explanation_revision"],
                                      "historical_submission_rejected_by_new_contract": rejected})
        provider = SavedReceiptProvider(saved, ledger_path)
        # New case prevents historical tool state from prepopulating a new role's view.
        row = next(r for r in json.loads(PILOT.read_text()) if r["case_key"] == saved["case_key"])
        replayed = Coordinator(provider, "offline-contract-regression-v2").run(
            store.case(row["order_id"], row["event_id"], saved["mode"]))
    unchanged = all(digest(ROOT / name) == expected for name, expected in before.items())
    checks = {
        "all_historical_claims_still_rejected": len(old_claims) == 5 and all(c["code_gate"] == "rejected" for c in old_claims),
        "all_six_historical_submissions_rejected": len(submission_checks) == 6 and all(
            c["historical_submission_rejected_by_new_contract"] for c in submission_checks),
        "replay_stops_before_publishing_invalid_claims": replayed["explanation_status"] == "inconclusive" and
            not replayed["published_claims"] and bool(replayed["contract_violations"]),
        "replay_detector_unchanged": replayed["detector"] == saved["detector"],
        "saved_results_ledger_and_pilot_unchanged": unchanged,
    }
    summary = {"version": "saved_api_output_regression_v2", "contract_version": CONTRACT_VERSION,
        "case_key": saved["case_key"], "checks": checks, "all_checks_passed": all(checks.values()),
        "historical_claim_diagnostics": [{k: c[k] for k in ["origin_role", "field", "code_gate", "reason"]} for c in old_claims],
        "historical_submission_checks": submission_checks, "replayed_saved_receipts": len(provider.records),
        "live_api_calls": 0, "additional_api_spend_usd": 0, "new_model_inference": False,
        "ground_truth_labels_created": 0, "ai_improvement_demonstrated": False,
        "source_hashes": before, "interpretation": "historical_failures_remain_blocked_not_evidence_of_new_model_success"}
    if not summary["all_checks_passed"]:
        raise ValueError("Offline regression failed: " + json.dumps(checks))
    return summary, replayed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT)
    args = parser.parse_args()
    target = args.output_dir.resolve()
    if target.parent != (ROOT / "data/experiments").resolve() or target.exists():
        raise ValueError("Use a new direct child of data/experiments; do not overwrite snapshots")
    summary, replayed = run_offline()
    target.mkdir()
    save(target / "summary.json", summary)
    save(target / "replayed_result.json", replayed)
    save(target / "manifest.json", {"version": summary["version"], "source_files": summary["source_hashes"],
        "implementation_hashes": implementation_hashes(),
        "output_hashes": {p.name: digest(p) for p in target.iterdir() if p.is_file()},
        "research_claim": "offline_contract_regression_not_AI_accuracy"})
    print(json.dumps({"output_dir": str(target), **summary}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
