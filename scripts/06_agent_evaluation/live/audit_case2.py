"""Replay the saved smoke test offline. Optionally count the two rejected inputs without inference."""
import argparse
from contextlib import closing
import hashlib
import json
import os
import sqlite3
import urllib.request

from run_smoke import (DEFAULT, INDEX, KEYS, LEDGER, OUTPUT, PILOT, ROOT, BudgetBlocked,
                       Coordinator, EvidenceStore, canonical, digest, initial_evidence, local_key,
                       read, single, verify_manifest)


class Replay:
    execution_kind = "offline_saved_receipt_replay_not_new_AI_inference"

    def __init__(self, rows):
        self.rows = iter(rows)
        self.blocked = None

    def call(self, *, request_id, role, payload, **kwargs):
        row = next(self.rows)
        if (row["request_id"] != request_id or row["role"] != role or
                row["payload_sha256"] != hashlib.sha256(canonical(payload).encode()).hexdigest()):
            raise RuntimeError("Reconstructed request differs from original")
        if row["status"] == "cancelled_unsent":
            self.blocked = {k: payload[k] for k in ("model", "instructions", "input", "tools",
                                                    "tool_choice", "parallel_tool_calls", "reasoning")}
            raise BudgetBlocked("Replayed original pre-dispatch stop")
        if row["status"] != "settled" or row["response"]["status"] != "completed":
            raise RuntimeError("Expected a settled inference with a completed receipt")
        return row["response"]


def audit():
    for directory in (DEFAULT, PILOT, OUTPUT):
        verify_manifest(directory)
    store = EvidenceStore()
    plan = read(OUTPUT / "plan.json")
    row = plan["case"]
    if row["case_index"] != INDEX:
        raise ValueError("Unexpected case")
    inputs = {}
    with closing(sqlite3.connect(LEDGER.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("ATTACH DATABASE ? AS historical", ((OUTPUT / "budget_before.sqlite").resolve().as_uri() + "?mode=ro",))
        for table in ("budget_requests", "provider_receipts"):
            if db.execute(f"SELECT * FROM historical.{table} EXCEPT SELECT * FROM main.{table} LIMIT 1").fetchone():
                raise ValueError("A pre-existing accounting record changed")
        for arm, run_key in KEYS.items():
            case = store.case(row["order_id"], row["event_id"], read(DEFAULT / "protocol.json")["mode"])
            initial_evidence(case)
            rows = read(OUTPUT / (arm + "_receipts.json"))
            for record in rows:
                found = db.execute("SELECT status,actual_nanousd,payload_sha256 FROM budget_requests WHERE request_id=?",
                                   (record["request_id"],)).fetchone()
                if found != (record["status"], record["actual_nanousd"], record["payload_sha256"]):
                    raise ValueError("Accounting export differs from live ledger")
                response = db.execute("SELECT response_json FROM provider_receipts WHERE request_id=?",
                                      (record["request_id"],)).fetchone()
                if (json.loads(response[0]) if response else None) != record["response"]:
                    raise ValueError("Stored response differs from live ledger")
            replay = Replay(rows)
            result = Coordinator(replay, run_key).run(case) if arm == "three_agents" else single(case, replay)
            saved = read(OUTPUT / (arm + ".json"))
            for field in ("detector", "explanation_status", "raw_agent_outputs", "verified_notes", "published_claims",
                          "verified_recommended_checks"):
                if saved[field] != result[field]:
                    raise ValueError("Offline replay differs on " + field)
            if next(replay.rows, None) is not None or replay.blocked is None:
                raise ValueError("Incomplete replay of pre-dispatch failure")
            inputs[arm] = replay.blocked
    return inputs


def count_only(inputs):
    if os.path.isfile("/etc/ssl/cert.pem"):
        os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    key = local_key()
    result = {}
    for arm, body in inputs.items():
        request = urllib.request.Request("https://api.openai.com/v1/responses/input_tokens",
                data=canonical(body).encode(), headers={"Authorization": "Bearer " + key,
                "Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(request, timeout=45) as response:
            count = json.load(response)
        if count.get("object") != "response.input_tokens" or type(count.get("input_tokens")) is not int:
            raise ValueError("Invalid token-count response")
        result[arm] = count["input_tokens"]
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count-only", action="store_true", help="Two token-count requests, no generation endpoint")
    args = parser.parse_args()
    inputs = audit()
    print("Verified both replays and historical ledger rows")
    if args.count_only:
        print(json.dumps(count_only(inputs)))
