"""Authorized one-case live comparison with evidence_refs_v1 and durable request logs."""
import argparse
from contextlib import closing
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compact"))
from compact_runner import run_compact
from evidence_wire import VERSION, expand_payload
from validate_offline import DEFAULT_OUTPUT as COMPACT
from run_smoke import (DEFAULT, PILOT, LEDGER, ROOT, INDEX, KEYS, BudgetBlocked, BudgetLedger,
                       EvidenceStore, ResponsesProvider, accounting, canonical, digest, initial_evidence,
                       local_key, read, receipts, save, sha, source_metrics, validate_protocol, verify_manifest)

DESTINATION = ROOT / "data/experiments/olist_agent_live_compact_v1_case2"
CAP_NANOUSD = 21_600_000
COUNT_FIELDS = ("model", "instructions", "input", "tools", "tool_choice", "parallel_tool_calls", "reasoning")


class LoggedProvider(ResponsesProvider):
    def __init__(self, ledger, key, destination):
        super().__init__(ledger, api_key=key)
        self.destination = destination
        self.active = {}

    def _post(self, endpoint, body):
        started = time.perf_counter()
        response = super()._post(endpoint, body)
        if endpoint == "responses/input_tokens":
            entry = {**self.active, "endpoint": endpoint, "body_sha256": sha(body),
                     "response": response, "seconds": time.perf_counter() - started}
            with (self.destination / "token_counts.jsonl").open("a") as stream:
                stream.write(canonical(entry) + "\n")
            print(self.active["phase"], self.active["role"], "input_tokens", response.get("input_tokens"), flush=True)
        return response

    def call(self, **request):
        self.active = {"phase": "live", "role": request["role"], "request_id": request["request_id"]}
        with (self.destination / "requests.jsonl").open("a") as stream:
            stream.write(canonical(request) + "\n")
        return super().call(**request)


def verify_receipt_trace(result, rows):
    responses = {r["request_id"]: r["response"] for r in rows if r["response"]}
    trace = [r for r in result["trace"] if r["kind"] == "model_request"]
    for item, raw in zip(trace, result["raw_agent_outputs"], strict=True):
        response = responses[item["request_id"]]
        if response["id"] != item["response_id"] or response["output"] != raw["output"]:
            raise ValueError("Agent output differs from its live receipt")


def run():
    for directory in (DEFAULT, PILOT, COMPACT):
        verify_manifest(directory)
    protocol = read(DEFAULT / "protocol.json")
    validate_protocol(protocol)
    row = read(DEFAULT / "cohort.json")[INDEX]
    if row["case_key"] != "olist-04029aa370b3a937565a":
        raise ValueError("Unexpected case")
    store = EvidenceStore()
    if DESTINATION.exists() or not LEDGER.is_file():
        raise ValueError("Existing trial or missing shared ledger; refusing another dispatch")
    key = local_key()
    os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    before_hash = digest(LEDGER)
    DESTINATION.mkdir()
    shutil.copyfile(LEDGER, DESTINATION / "budget_before.sqlite")
    if digest(DESTINATION / "budget_before.sqlite") != before_hash:
        raise ValueError("Ledger changed while archiving")
    with BudgetLedger(LEDGER) as ledger:
        before = ledger.snapshot()
        if before["breached"] or before["reserved_nanousd"] or before["remaining_nanousd"] < CAP_NANOUSD:
            raise BudgetBlocked("Shared ledger cannot fund the approved trial")
        for base in KEYS.values():
            if receipts(ledger, base + "-" + VERSION, row["case_key"]):
                raise ValueError("Prior attempt already recorded for this configuration")
        policy = ledger._policy
        rates = policy["models"][protocol["model"]]
        reservation = (6000 * max(v for k, v in rates.items() if k != "output_nanousd_per_token")
                       + 1200 * rates["output_nanousd_per_token"])
        if reservation * 16 > CAP_NANOUSD or policy["max_input_tokens_per_request"] != 6000:
            raise BudgetBlocked("Policy differs from approved caps")
        save(DESTINATION / "plan.json", {"case": row, "wire_format": VERSION, "profile": "native_bounded",
             "max_requests": {"single_agent": 4, "three_agents": 12}, "cap_nanousd": CAP_NANOUSD,
             "model": protocol["model"], "budget_before": before, "ledger_before_sha256": before_hash,
             "implementation_sha256": digest(Path(__file__)), "phase": "token_counts_then_one_live_pair"})
        provider = LoggedProvider(ledger, key, DESTINATION)
        counts = {}
        for arm in ("single_agent", "three_agents"):
            prepared = read(COMPACT / (arm + "_prepared_requests.json"))[-1]
            provider.active = {"phase": "saved_history_preflight", "role": prepared["role"],
                               "request_id": prepared["request_id"], "arm": arm}
            reply = provider._post("responses/input_tokens", {k: prepared["payload"][k] for k in COUNT_FIELDS})
            if reply.get("object") != "response.input_tokens" or type(reply.get("input_tokens")) is not int:
                raise ValueError("Invalid token count response")
            counts[arm] = reply["input_tokens"]
        save(DESTINATION / "preflight_counts.json", counts)
        if max(counts.values()) > 6000:
            raise BudgetBlocked("Optimized saved history still exceeds input cap; no inference dispatched")
        report = {"case_key": row["case_key"], "case_index": INDEX, "wire_format": VERSION,
                  "profile": "native_bounded", "preflight_tokens": counts, "arms": {},
                  "scope": "known_development_case_engineering_smoke", "independent_accuracy": None,
                  "human_review_status": "pending"}
        for arm in ("three_agents", "single_agent"):
            print("Starting", arm, flush=True)
            case = store.case(row["order_id"], row["event_id"], protocol["mode"])
            if sha(initial_evidence(case)[0]) != row["input_fingerprint"]:
                raise ValueError("Case input differs from frozen comparison")
            result = run_compact(case, provider, arm)
            save(DESTINATION / (arm + ".json"), result)
            rows = receipts(ledger, result["run_key"], row["case_key"])
            save(DESTINATION / (arm + "_receipts.json"), rows)
            verify_receipt_trace(result, rows)
            totals = accounting(rows)
            metrics = source_metrics(case, result)
            report["arms"][arm] = {"accounting": totals, "metrics": metrics, "seconds": result["explanation_seconds"],
                                   "error": result.get("error"), "metadata_verification_complete": result.get("metadata_verification_complete")}
            print(arm, metrics["status"], totals, flush=True)
        report["budget_after"] = ledger.snapshot()
        report["settled_nanousd"] = sum(r["accounting"]["settled_nanousd"] for r in report["arms"].values())
        save(DESTINATION / "summary.json", report)
    sources = read(COMPACT / "manifest.json")["source_files"]
    sources.update({str(p.relative_to(ROOT)): digest(p) for p in COMPACT.iterdir() if p.is_file()})
    implementation = read(COMPACT / "manifest.json")["implementation_hashes"]
    implementation[str(Path(__file__).resolve().relative_to(ROOT))] = digest(Path(__file__))
    save(DESTINATION / "manifest.json", {"source_files": sources, "implementation_hashes": implementation,
         "output_hashes": {p.name: digest(p) for p in DESTINATION.iterdir() if p.is_file()}})
    print("Saved", DESTINATION, flush=True)


class ExactReplay:
    execution_kind = "offline_saved_receipt_replay_not_new_AI_inference"

    def __init__(self, requests, rows):
        self.requests = iter(requests)
        self.rows = {r["request_id"]: r for r in rows}

    def call(self, **request):
        if request != next(self.requests):
            raise ValueError("Replayed request differs from recorded wire payload")
        row = self.rows[request["request_id"]]
        if row["status"] == "cancelled_unsent":
            raise BudgetBlocked("Serialized input exceeds token cap or count response is invalid")
        if row["status"] != "settled" or row["response"]["status"] != "completed":
            raise ValueError("Expected settled completed response")
        return deepcopy(row["response"])


def audit():
    for directory in (DEFAULT, PILOT, COMPACT, DESTINATION):
        verify_manifest(directory)
    requests = [json.loads(line) for line in (DESTINATION / "requests.jsonl").read_text().splitlines()]
    store = EvidenceStore()
    row = read(DESTINATION / "plan.json")["case"]
    metrics = {}
    with closing(sqlite3.connect(LEDGER.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS historical", ((DESTINATION / "budget_before.sqlite").resolve().as_uri() + "?mode=ro",))
        for table in ("budget_requests", "provider_receipts"):
            if db.execute(f"SELECT * FROM historical.{table} EXCEPT SELECT * FROM main.{table} LIMIT 1").fetchone():
                raise ValueError("Historical accounting changed")
        for arm in KEYS:
            saved = read(DESTINATION / (arm + ".json"))
            rows = read(DESTINATION / (arm + "_receipts.json"))
            relevant = [r for r in requests if r["run_key"] == saved["run_key"]]
            for request in relevant:
                expand_payload(request["payload"])
                stored = next(r for r in rows if r["request_id"] == request["request_id"])
                if sha(request["payload"]) != stored["payload_sha256"]:
                    raise ValueError("Payload differs from receipt")
                actual = db.execute("SELECT status,actual_nanousd,payload_sha256 FROM budget_requests WHERE request_id=?",
                                    (request["request_id"],)).fetchone()
                if actual != (stored["status"], stored["actual_nanousd"], stored["payload_sha256"]):
                    raise ValueError("Export differs from live accounting")
                raw = db.execute("SELECT response_json FROM provider_receipts WHERE request_id=?", (request["request_id"],)).fetchone()
                if (json.loads(raw[0]) if raw else None) != stored["response"]:
                    raise ValueError("Provider receipt differs")
            case = store.case(row["order_id"], row["event_id"], saved["mode"])
            initial_evidence(case)
            provider = ExactReplay(relevant, rows)
            reproduced = run_compact(case, provider, arm)
            for field in ("detector", "explanation_status", "raw_agent_outputs", "published_claims", "rejected_claims",
                          "verified_notes", "verified_recommended_checks", "rejected_metadata", "contract_violations", "evidence"):
                if reproduced[field] != saved[field]:
                    raise ValueError("Offline replay differs on " + field)
            if next(provider.requests, None) is not None:
                raise ValueError("Unused requests in replay")
            verify_receipt_trace(saved, rows)
            metrics[arm] = source_metrics(case, saved)
    return metrics


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--live", action="store_true")
    group.add_argument("--verify", action="store_true")
    if parser.parse_args().live:
        run()
    else:
        print(canonical(audit()))
