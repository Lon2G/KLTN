"""One authorized live development comparison. The parent offline snapshot stays immutable."""
import argparse
from collections import Counter
from copy import deepcopy
import json
import os
from pathlib import Path
import shlex
import shutil
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparison_core import (CONTRACT_VERSION, ROOT, base_result, catalogs, catalog_packet,
                             compile_selection, initial_evidence, publish_code_checked,
                             selection_schema, sha, source_metrics)
from comparison_runners import SINGLE_INSTRUCTIONS
from prepare_comparison import DEFAULT, PILOT, read, save, validate_protocol, verify_manifest
from agent_contracts import ROLES, schemas, tool, validate
from api_budget import BudgetBlocked, BudgetLedger, canonical
from audit_readiness import digest
from coordinator import Coordinator
from evidence_tools import EvidenceStore
from provider import ProviderUnavailable, ResponsesProvider
from run_multi_agent import LEDGER

INDEX = 2
PROFILE = "native_bounded"
OUTPUT = ROOT / "data/experiments/olist_agent_live_comparison_v1_case2"
KEYS = {arm: "comparison-v1-" + name + "-" + PROFILE
        for arm, name in [("three_agents", "three"), ("single_agent", "single")]}


def single(case, provider):
    """Implement the frozen single-role wire protocol; both tools and publication gates are shared."""
    run_key = KEYS["single_agent"]
    result = base_result(case, "single_agent", provider.execution_kind, run_key)
    started = time.perf_counter()
    packet, visible = initial_evidence(case)
    choices = catalogs(case, visible)
    plans = case.store.config["peer_plans"]
    packet.update(catalogs=catalog_packet(choices), declared_plans=plans)
    history = [{"role": "user", "content": canonical(packet)}]
    functions = {definition["name"]: definition for role in ROLES for definition in schemas(role, plans)[:-1]}
    terminal = "submit_case_analysis"
    try:
        for ordinal in range(1, 5):
            functions[terminal] = tool(terminal, selection_schema(case, choices))
            payload = {"model": case.store.config["provider"]["model"], "instructions": SINGLE_INSTRUCTIONS,
                       "input": deepcopy(history), "tools": list(functions.values()), "parallel_tool_calls": False,
                       "tool_choice": {"type": "function", "name": terminal} if ordinal == 4 else "auto",
                       "max_output_tokens": 1200, "store": False, "reasoning": {"effort": "none"},
                       "service_tier": "default", "truncation": "disabled"}
            request_id = sha({"run": run_key, "case": case.case_key, "asof": case.asof,
                              "event": case.events[-1]["event_id"], "mode": case.mode,
                              "ordinal": ordinal, "contract": CONTRACT_VERSION})
            response = provider.call(request_id=request_id, case_key=case.case_key, run_key=run_key,
                                     role="single_agent_baseline", payload=payload)
            output = response.get("output", [])
            result["raw_agent_outputs"].append({"ordinal": ordinal, "output": deepcopy(output)})
            result["trace"].append({"kind": "model_request", "role": "single_agent_baseline",
                                    "request_id": request_id, "response_id": response["id"]})
            calls = [r for r in output if r.get("type") == "function_call"]
            if len(calls) != 1 or calls[0].get("name") not in functions:
                raise ValueError("Expected exactly one declared function call")
            call = calls[0]
            name = call["name"]
            try:
                args = json.loads(call["arguments"])
                validate(args, functions[name]["parameters"])
                if ordinal == 4 and name != terminal:
                    raise ValueError("Final request must submit")
                if name == terminal:
                    publish_code_checked(case, result, compile_selection(case, args, choices))
                    result["selected_output"] = args
                    break
            except (ValueError, TypeError, KeyError) as exc:
                result["contract_violations"].append({"ordinal": ordinal, "reason": str(exc)})
                if ordinal == 4:
                    raise ValueError("Contract repair exhausted the fixed request cap") from exc
                reply = {"status": "contract_rejected", "reason": str(exc)}
            else:
                evidence = getattr(case, name)(**args)
                result["trace"].append({"kind": "tool_call", "role": "single_agent_baseline", "tool": name,
                                        "arguments": args, "evidence_id": evidence["evidence_id"]})
                visible.add(evidence["evidence_id"])
                previous = choices
                choices = catalogs(case, visible)
                delta = {domain: {kind: {k: v for k, v in values.items() if k not in previous[domain][kind]}
                                  for kind, values in groups.items()} for domain, groups in choices.items()}
                reply = {"evidence": evidence, "catalogs": catalog_packet(delta)}
            history.extend(output)
            history.append({"type": "function_call_output", "call_id": call["call_id"], "output": canonical(reply)})
    except BudgetBlocked as exc:
        result.update(explanation_status="unavailable_budget", error=str(exc))
    except ProviderUnavailable as exc:
        result.update(explanation_status="unavailable_api", error=str(exc))
    except (ValueError, TypeError, KeyError) as exc:
        result.update(explanation_status="inconclusive", error=str(exc))
    result.update(evidence=list(case.registry.values()), explanation_seconds=time.perf_counter() - started,
                  detector_latency_measured_here=False)
    return result


def local_key():
    if os.environ.get("OPENAI_API_KEY"):
        return os.environ["OPENAI_API_KEY"]
    values = []
    for line in (ROOT / ".env").read_text().splitlines():
        name, _, raw = line.removeprefix("export ").partition("=")
        if name.strip() == "OPENAI_API_KEY":
            values.extend(shlex.split(raw, comments=True))
    if len(values) != 1 or not values[0].startswith("sk-"):
        raise ValueError("Expected one local OpenAI project key")
    return values[0]


def receipts(ledger, run_key, case_key):
    rows = ledger.connection.execute("""
        SELECT b.request_id, b.role, b.status, b.model, b.payload_sha256, b.reserved_nanousd, b.actual_nanousd,
               b.usage, r.payload_sha256 AS receipt_hash, r.response_json
        FROM budget_requests b LEFT JOIN provider_receipts r USING(request_id)
        WHERE run_key=? AND case_key=? ORDER BY b.rowid""", (run_key, case_key))
    records = []
    for row in rows:
        record = dict(row)
        raw = record.pop("response_json")
        record["response"] = json.loads(raw) if raw else None
        record["usage"] = json.loads(record["usage"]) if record["usage"] else None
        if raw and record.pop("receipt_hash") != record["payload_sha256"]:
            raise ValueError("Provider receipt payload fingerprint mismatch")
        records.append(record)
    return records


def accounting(rows):
    states = Counter(r["status"] for r in rows)
    settled = [r for r in rows if r["status"] == "settled"]
    return {"request_states": dict(states), "inference_dispatches": len(settled) + states["in_flight"],
            "model_responses_received": sum(bool(r["response"]) for r in rows),
            "settled_nanousd": sum(r["actual_nanousd"] for r in settled),
            "held_nanousd": sum(r["reserved_nanousd"] for r in rows if r["status"] in {"reserved", "in_flight"}),
            "input_tokens": sum(r["usage"]["input_tokens"] for r in settled),
            "output_tokens": sum(r["usage"]["output_tokens"] for r in settled)}


def run():
    verify_manifest(DEFAULT)
    verify_manifest(PILOT)
    protocol = read(DEFAULT / "protocol.json")
    validate_protocol(protocol)
    row = read(DEFAULT / "cohort.json")[INDEX]
    if row["case_index"] != INDEX or row["case_key"] != "olist-04029aa370b3a937565a":
        raise ValueError("Unexpected development order")
    store = EvidenceStore()

    def get_case():
        case = store.case(row["order_id"], row["event_id"], protocol["mode"])
        if sha(initial_evidence(case)[0]) != row["input_fingerprint"]:
            raise ValueError("Development case differs from frozen comparison input")
        return case

    get_case()
    key = local_key()
    if OUTPUT.exists() or not LEDGER.is_file():
        raise ValueError("Existing comparison or missing shared ledger; refusing dispatch")
    if Path("/etc/ssl/cert.pem").is_file():
        os.environ.setdefault("SSL_CERT_FILE", "/etc/ssl/cert.pem")
    # Preserve the exact historical ledger before the first write, including all old receipts.
    before_hash = digest(LEDGER)
    OUTPUT.mkdir()
    archive = OUTPUT / "budget_before.sqlite"
    shutil.copyfile(LEDGER, archive)
    if digest(archive) != before_hash:
        raise ValueError("Ledger changed during archival")
    with BudgetLedger(LEDGER) as ledger:
        provider = ResponsesProvider(ledger, api_key=key)
        before = ledger.snapshot()
        if before["breached"] or before["reserved_nanousd"] or before["remaining_nanousd"] < 21_600_000:
            raise BudgetBlocked("Cannot fund both arms within the shared ledger")
        if any(receipts(ledger, run_key, row["case_key"]) for run_key in KEYS.values()):
            raise ValueError("Prior attempt in ledger; refusing repeat")
        save(OUTPUT / "plan.json", {"case": row, "profile": PROFILE, "run_keys": KEYS,
             "model": protocol["model"], "request_caps": protocol["profiles"][PROFILE],
             "max_cost_nanousd": 21_600_000, "budget_before": before, "archived_ledger_sha256": before_hash,
             "runner_sha256": digest(Path(__file__)), "comparison_manifest_sha256": digest(DEFAULT / "manifest.json")})
        rules = read(DEFAULT / "rules_metrics.json")[INDEX]
        if rules["case_key"] != row["case_key"]:
            raise ValueError("Wrong rules control")
        report = {"case_index": INDEX, "case_key": row["case_key"], "profile": PROFILE, "rules": rules,
                  "scope": "known_development_case_engineering_smoke", "arms": {}, "ground_truth": None}
        for arm, run_key in KEYS.items():
            print("Starting", arm, flush=True)
            case = get_case()
            result = Coordinator(provider, run_key).run(case) if arm == "three_agents" else single(case, provider)
            result.update(arm=arm, profile=PROFILE)
            save(OUTPUT / (arm + ".json"), result)
            rows = receipts(ledger, run_key, row["case_key"])
            save(OUTPUT / (arm + "_receipts.json"), rows)
            totals = accounting(rows)
            responses = {r["request_id"]: r["response"] for r in rows if r["response"]}
            traces = [t for t in result["trace"] if t["kind"] == "model_request"]
            for trace, raw in zip(traces, result["raw_agent_outputs"], strict=True):
                response = responses[trace["request_id"]]
                if response["id"] != trace["response_id"] or response["output"] != raw["output"]:
                    raise ValueError("Agent trace differs from provider receipt")
            metrics = source_metrics(case, result)
            report["arms"][arm] = {"accounting": totals, "metrics": metrics, "error": result.get("error"),
                                   "seconds": result["explanation_seconds"]}
            print(arm, metrics["status"], totals, flush=True)
        report["budget_after"] = ledger.snapshot()
        report["settled_nanousd"] = sum(v["accounting"]["settled_nanousd"] for v in report["arms"].values())
        save(OUTPUT / "summary.json", report)
        save(OUTPUT / "manifest.json", {"source_files": {str((DEFAULT / "manifest.json").relative_to(ROOT)): digest(DEFAULT / "manifest.json")},
             "implementation_hashes": {str(Path(__file__).resolve().relative_to(ROOT)): digest(Path(__file__))},
             "output_hashes": {p.name: digest(p) for p in OUTPUT.iterdir() if p.is_file()}})
        print("Saved", OUTPUT)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--live", action="store_true")
    action.add_argument("--verify", action="store_true")
    if parser.parse_args().live:
        run()
    else:
        for directory in (DEFAULT, PILOT, OUTPUT):
            verify_manifest(directory)
        print("Verified source and output hashes")
