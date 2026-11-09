"""Use the existing agent engines with versioned evidence serialization."""
from copy import deepcopy
from pathlib import Path
import sys

from evidence_wire import VERSION, compact_payload, sha

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "live"))
from run_smoke import Coordinator, KEYS, single


class CompactEvidenceProvider:
    def __init__(self, provider):
        self.provider = provider
        self.execution_kind = provider.execution_kind
        self.requests = []

    def call(self, **request):
        payload, metrics = compact_payload(request["payload"])
        # A changed wire format identifies a new experiment, including on retry.
        # Budget accounting continues through the provider's shared ledger.
        forwarded = {**request, "payload": payload,
                     "run_key": request["run_key"] + "-" + VERSION,
                     "request_id": sha({"wire_format": VERSION, "source_request_id": request["request_id"]})}
        self.requests.append({"source_request_id": request["request_id"], "request_id": forwarded["request_id"],
                              "run_key": forwarded["run_key"], "role": request["role"], **metrics})
        return self.provider.call(**forwarded)


def run_compact(case, provider, arm):
    if arm not in KEYS:
        raise ValueError("Unknown comparison arm")
    sender = CompactEvidenceProvider(provider)
    result = Coordinator(sender, KEYS[arm]).run(case) if arm == "three_agents" else single(case, sender)
    result = deepcopy(result)
    transport_ids = {row["source_request_id"]: row["request_id"] for row in sender.requests}
    for entry in result["trace"]:
        if entry["kind"] == "model_request":
            entry["source_request_id"] = entry["request_id"]
            entry["request_id"] = transport_ids[entry["request_id"]]
    result.update(arm=arm, profile="native_bounded", wire_format=VERSION,
                  run_key=KEYS[arm] + "-" + VERSION, wire_requests=sender.requests,
                  original_contract_version=result["contract_version"])
    return result
