"""Validate evidence serialization using real saved replies and all frozen pilot evidence."""
import argparse
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from evidence_wire import VERSION, EvidenceDecoder, EvidenceEncoder, expand_payload
from compact_runner import run_compact
from run_smoke import (DEFAULT, OUTPUT as LIVE, PILOT, LEDGER, ROOT, BudgetBlocked, EvidenceStore,
                       canonical, digest, initial_evidence, read, save, sha, verify_manifest)
from audit_case2 import audit

DEFAULT_OUTPUT = ROOT / "data/experiments/olist_agent_evidence_compaction_v1"


class SavedWireReplay:
    execution_kind = "offline_saved_receipt_replay_not_new_AI_inference"

    def __init__(self, arm):
        self.receipts = iter(read(LIVE / (arm + "_receipts.json")))
        self.prepared_requests = []

    def call(self, *, request_id, run_key, role, payload, **kwargs):
        row = next(self.receipts)
        original = expand_payload(payload)
        if (row["payload_sha256"] != sha(original) or row["role"] != role or
                request_id != sha({"wire_format": VERSION, "source_request_id": row["request_id"]}) or
                not run_key.endswith("-" + VERSION)):
            raise ValueError("Expanded request differs from the original receipt")
        self.prepared_requests.append({"request_id": request_id, "run_key": run_key, "role": role,
                                      "payload": deepcopy(payload), "historical_status": row["status"]})
        if row["status"] == "cancelled_unsent":
            raise BudgetBlocked("Serialized input exceeds token cap or count response is invalid")
        if row["status"] != "settled" or row["response"]["status"] != "completed":
            raise ValueError("Expected a settled historical response")
        return deepcopy(row["response"])


def validate_offline(destination=DEFAULT_OUTPUT):
    destination = Path(destination).resolve()
    if destination.parent != ROOT / "data/experiments" or destination.exists():
        raise ValueError("Use a new experiment directory")
    protected = {str(LEDGER.relative_to(ROOT)): digest(LEDGER)}
    for directory in (DEFAULT, PILOT, LIVE):
        verify_manifest(directory)
        manifest = read(directory / "manifest.json")
        protected.update(manifest["source_files"])
        protected.update(manifest["implementation_hashes"])
        protected.update({str(p.relative_to(ROOT)): digest(p) for p in directory.rglob("*") if p.is_file()})
    if read(ROOT / "templates/multi_agent_v1/budget.json")["max_input_tokens_per_request"] != 6000:
        raise ValueError("The approved input limit is 6000 tokens")
    arms, packets = {}, []
    with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline validation forbids HTTP")) as network, \
         patch("provider.ResponsesProvider.call", side_effect=AssertionError("Offline validation forbids inference")):
        audit()
        store = EvidenceStore()
        row = read(LIVE / "plan.json")["case"]
        for arm in ("single_agent", "three_agents"):
            case = store.case(row["order_id"], row["event_id"], read(DEFAULT / "protocol.json")["mode"])
            initial_evidence(case)
            provider = SavedWireReplay(arm)
            result = run_compact(case, provider, arm)
            original = read(LIVE / (arm + ".json"))
            for field in ("detector", "explanation_status", "raw_agent_outputs", "published_claims", "verified_notes",
                          "verified_recommended_checks", "rejected_claims", "contract_violations", "evidence"):
                if result[field] != original[field]:
                    raise ValueError("Saved-reply replay differs on " + field)
            if next(provider.receipts, None) is not None:
                raise ValueError("Incomplete replay")
            arms[arm] = {"requests": result["wire_requests"], "historical_stop_reproduced": True,
                         "historical_inference_responses_replayed": len(result["raw_agent_outputs"]),
                         "new_model_response_count": 0, "prepared_requests": provider.prepared_requests}
        # Examine the actual 20-case evidence inventories. Every field, including
        # nulls and signed durations, must survive repeated serialization.
        for packet in read(PILOT / "evidence_packets.json"):
            groups = {"evidence": packet["evidence"], "strict": packet["strict_mode_assessments"]}
            # Strict and assumed modes are distinct requests, as in production.
            for key, records in groups.items():
                encoder, decoder = EvidenceEncoder(), EvidenceDecoder()
                first = encoder.packet(records)
                repeated = encoder.packet(records)
                if decoder.packet(first) != records or decoder.packet(repeated) != records:
                    raise ValueError("Pilot evidence changed during round trip")
                if repeated["new_evidence"] or repeated["new_scopes"]:
                    raise ValueError("Repeated source records were sent again")
                packets.append({"case_key": packet["case_key"], "mode_group": key,
                                "records": len(records), "exact_round_trip": True,
                                "repeat_utf8_bytes": len(canonical(repeated).encode()),
                                "original_utf8_bytes": len(canonical(records).encode())})
        if network.call_count:
            raise ValueError("Unexpected network attempt")
    if any(digest(ROOT / name) != value for name, value in protected.items()):
        raise ValueError("An input, previous result, implementation or budget ledger changed")
    destination.mkdir()
    for arm, result in arms.items():
        save(destination / (arm + "_prepared_requests.json"), result.pop("prepared_requests"))
    summary = {"version": VERSION, "arms": arms, "pilot_evidence_round_trips": packets,
               "historical_responses_replayed": sum(a["historical_inference_responses_replayed"] for a in arms.values()),
               "original_requests_verified": sum(len(a["requests"]) for a in arms.values()),
               "new_API_calls": 0, "new_model_inferences": 0, "cost_usd": 0,
               "input_token_cap": 6000, "official_tokens_after_compaction": None,
               "protected_files_checked": len(protected), "protected_files_unchanged": True,
               "scope": "serialization_and_saved_response_replay", "human_quality_rating": None,
               "live_completion_verified": False}
    save(destination / "summary.json", summary)
    save(destination / "preservation.json", protected)
    sources = {name: value for name, value in protected.items() if name != str(LEDGER.relative_to(ROOT))}
    save(destination / "manifest.json", {"source_files": sources,
         "implementation_hashes": {str(p.relative_to(ROOT)): digest(p) for p in Path(__file__).parent.glob("*.py")},
         "output_hashes": {p.name: digest(p) for p in destination.iterdir() if p.is_file()}})
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    result = validate_offline(args.output_dir)
    print(canonical(result))
