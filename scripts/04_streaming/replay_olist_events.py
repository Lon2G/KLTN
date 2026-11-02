"""Publish a separate, reproducible replay of real Olist validation milestones."""

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import platform
import sqlite3
import tempfile
from time import perf_counter_ns

import event_state as core


ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = ROOT / "templates/event_replay_v1/protocol.json"
DEFAULT_OUTPUT = ROOT / "data/experiments/olist_event_replay_v1"
ORDERS_FILE = "olist_orders_dataset.csv"


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True, allow_nan=False) + "\n", encoding="utf-8")


def read_csv(path):
    with Path(path).open(newline="", encoding="utf-8") as handle:
        yield from csv.DictReader(handle)


def write_csv(path, rows, fields):
    with Path(path).open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def write_jsonl(path, values):
    with Path(path).open("w", encoding="utf-8") as handle:
        for value in values:
            handle.write(core.canonical(value) + "\n")


def implementation_hashes():
    paths = [Path(__file__), Path(core.__file__), PROTOCOL]
    return {str(p.relative_to(ROOT)): digest(p) for p in paths}


def select_visible_events(source_rows, scope_ids, start, cutoff):
    """Project to an allowlisted payload BEFORE delivering anything to the state manager."""
    events, lineage, keys = [], [], set()
    for record, row in enumerate(source_rows, 1):
        if set(row) != {"case_id", "activity", "timestamp"}:
            raise ValueError("Unexpected canonical event-log schema")
        if row["case_id"] not in scope_ids:
            continue
        core.parse_time(row["timestamp"])
        if row["activity"] not in core.ACTIVITIES:
            raise ValueError("Unknown source activity")
        if not start <= row["timestamp"] < cutoff:
            continue
        key = (row["case_id"], row["activity"])
        if key in keys:
            raise ValueError("Duplicate source milestone; do not silently deduplicate")
        keys.add(key)
        event = core.make_event(row["case_id"], row["activity"], row["timestamp"])
        core.validate_event(event)
        events.append(event)
        lineage.append({"event_id": event["event_id"], "clean_event_record": record,
                        "source_field": core.ACTIVITIES[event["activity"]]})
    events.sort(key=core.event_key)
    return events, lineage


def load_inputs():
    protocol = read_json(PROTOCOL)
    clean = ROOT / protocol["clean_directory"]
    cohort = ROOT / protocol["cohort_ledger"]
    if digest(clean / "manifest.json") != protocol["clean_manifest_sha256"] or digest(cohort) != protocol["cohort_sha256"]:
        raise ValueError("Frozen clean manifest or original cohort changed")
    manifest = read_json(clean / "manifest.json")
    paths = [clean / "manifest.json", cohort]
    for name in [ORDERS_FILE, "event_log.csv"]:
        path = clean / name
        if digest(path) != manifest["output_hashes"][name]:
            raise ValueError(f"Clean source integrity mismatch: {name}")
        paths.append(path)
    raw_orders = Path(manifest["source_directory"]) / ORDERS_FILE
    if digest(raw_orders) != manifest["sources"][ORDERS_FILE]["sha256"]:
        raise ValueError("Raw orders differ from the clean snapshot's provenance")
    paths.append(raw_orders)
    scope = set()
    for row in read_csv(cohort):
        if row["split"] != protocol["split"]:
            continue
        if row["order_id"] in scope or row["single_category_name_english"] != protocol["category"]:
            raise ValueError("Duplicate or wrong-category cohort member")
        scope.add(row["order_id"])
    if not scope:
        raise ValueError("Empty replay scope")
    start, cutoff = protocol["start_inclusive"], protocol["cutoff_exclusive"]
    expected, positions, counts = {}, {}, Counter()
    for record, row in enumerate(read_csv(clean / ORDERS_FILE), 1):
        if row["order_id"] not in scope:
            continue
        if row["order_id"] in positions or not start <= row["order_purchase_timestamp"] < cutoff:
            raise ValueError("Cohort purchase window or order key differs")
        positions[row["order_id"]] = record
        for activity, field in core.ACTIVITIES.items():
            timestamp = row[field]
            if not timestamp:
                continue
            core.parse_time(timestamp)
            bucket = "before_window" if timestamp < start else ("at_or_after_cutoff" if timestamp >= cutoff else "visible")
            counts[bucket] += 1
            if bucket == "visible":
                expected[(row["order_id"], activity)] = timestamp
    if set(positions) != scope:
        raise ValueError("Cohort order missing from clean orders")
    raw_expected, raw_positions = {}, {}
    for record, row in enumerate(read_csv(raw_orders), 1):
        oid = row["order_id"].strip().lower()
        if oid not in scope:
            continue
        if oid in raw_positions:
            raise ValueError("Duplicate raw source order")
        raw_positions[oid] = record
        for activity, field in core.ACTIVITIES.items():
            timestamp = row[field].strip()
            if timestamp and start <= timestamp < cutoff:
                raw_expected[(oid, activity)] = timestamp
    if raw_expected != expected or set(raw_positions) != scope:
        raise ValueError("Visible clean milestones differ from real raw orders")
    events, lineage = select_visible_events(read_csv(clean / "event_log.csv"), scope, start, cutoff)
    if {(e["order_id"], e["activity"]): e["occurred_at"] for e in events} != expected:
        raise ValueError("Visible canonical events do not match actual clean-order milestones")
    by_id = {e["event_id"]: e for e in events}
    for row in lineage:
        oid = by_id[row["event_id"]]["order_id"]
        row["clean_order_record"] = positions[oid]
        row["raw_order_record"] = raw_positions[oid]
    lineage.sort(key=lambda row: core.event_key(by_id[row["event_id"]]))
    source = {"files": {str(p.relative_to(ROOT)): digest(p) for p in paths},
              "source_record_definition": "one_based_data_record_excluding_header_not_physical_line",
              "scope_orders": len(scope), "scope_ids_sha256": core.fingerprint(sorted(scope)),
              "offline_only_event_counts": dict(counts),
              "future_counts_not_sent_to_consumer": True}
    return protocol, events, lineage, scope, source


def state_tables(states, cutoff):
    orders, stages = [], []
    for state in states:
        orders.append({"order_id": state["order_id"], "as_of_exclusive": cutoff,
                       "observed_event_count": state["observed_event_count"],
                       "highest_milestone_observed": state["highest_milestone_observed"],
                       "all_four_observed": state["all_four_observed"], "delivery_observed": state["delivery_observed"],
                       "not_observed_milestones_json": core.canonical(state["not_observed_milestones"]),
                       "timestamp_quality": state["timestamp_quality"],
                       "observed_inversions_json": core.canonical(state["observed_inversions"]),
                       "observed_equal_time_pairs_json": core.canonical(state["observed_equal_time_pairs"]),
                       "promise_basis_status": state["promise_basis_status"],
                       "anomaly_assessment": state["anomaly_assessment"]})
        for name, stage in state["stages"].items():
            stages.append({"order_id": state["order_id"], "stage": name, "as_of_exclusive": cutoff, **stage})
    return orders, stages


def semantic_summary(events, states):
    return {"orders_observed": len(states), "events_applied": len(events),
            "first_event_time": events[0]["occurred_at"], "last_event_time": events[-1]["occurred_at"],
            "activity_counts": dict(Counter(e["activity"] for e in events)),
            "orders_by_visible_event_count": dict(Counter(str(s["observed_event_count"]) for s in states)),
            "orders_with_delivery_observed": sum(s["delivery_observed"] for s in states),
            "orders_without_delivery_observed": sum(not s["delivery_observed"] for s in states),
            "orders_with_all_four_observed": sum(s["all_four_observed"] for s in states),
            "orders_with_observed_inversions": sum(bool(s["observed_inversions"]) for s in states),
            "orders_with_equal_time_pairs": sum(bool(s["observed_equal_time_pairs"]) for s in states),
            "stage_observation_counts": dict(Counter(v["observation_status"] for s in states for v in s["stages"].values())),
            "events_semantic_sha256": core.fingerprint(events), "states_semantic_sha256": core.fingerprint(states)}


def percentile(values, fraction):
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    return ordered[low] + (ordered[min(low + 1, len(ordered) - 1)] - ordered[low]) * (position - low)


def build(destination=DEFAULT_OUTPUT):
    destination = Path(destination).resolve()
    parent = ROOT / "data/experiments"
    if destination.parent != parent or destination.exists():
        raise ValueError("Output must be a NEW direct child of data/experiments; no overwrite")
    protocol, events, lineage, scope, source = load_inputs()
    implementations = implementation_hashes()
    started = datetime.now(timezone.utc).isoformat()
    with tempfile.TemporaryDirectory(prefix=".event-replay-", dir=parent) as temporary:
        output = Path(temporary) / "snapshot"
        output.mkdir()
        write_jsonl(output / "events.jsonl", events)
        write_csv(output / "event_lineage.csv", lineage,
                  ["event_id", "clean_event_record", "source_field", "clean_order_record", "raw_order_record"])
        latencies = []
        with core.StateStore(output / "state.sqlite", protocol["start_inclusive"], protocol["cutoff_exclusive"]) as store:
            loop_start = perf_counter_ns()
            for event in events:
                before = perf_counter_ns()
                result = store.apply(event)
                latencies.append((perf_counter_ns() - before) / 1e6)
                if not result["applied"]:
                    raise ValueError("Unexpected duplicate in source replay")
            loop_seconds = (perf_counter_ns() - loop_start) / 1e9
            store.finish()
            states = store.states()
            if {s["order_id"] for s in states} != scope:
                raise ValueError("Replay lost a cohort order")
            write_jsonl(output / "journal.jsonl", store.journal())
        write_jsonl(output / "states.jsonl", states)
        orders, stages = state_tables(states, protocol["cutoff_exclusive"])
        write_csv(output / "order_states.csv", orders, list(orders[0]))
        write_csv(output / "stage_observations.csv", stages, list(stages[0]))
        write_csv(output / "processing_times.csv",
                  ({"sequence": i + 1, "event_id": e["event_id"], "apply_commit_ms": ms}
                   for i, (e, ms) in enumerate(zip(events, latencies))), ["sequence", "event_id", "apply_commit_ms"])
        runtime = {"started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                   "python": platform.python_version(), "platform": platform.platform(), "sqlite": sqlite3.sqlite_version,
                   "loop_seconds": loop_seconds, "events_per_loop_second": len(events) / loop_seconds,
                   "apply_commit_ms_p50": percentile(latencies, .5), "apply_commit_ms_p95": percentile(latencies, .95),
                   "apply_commit_ms_max": max(latencies), "sqlite_synchronous": "FULL",
                   "definition": "single local unpaced run; apply includes validation/state derivation/SQLite commit; loop excludes source loading/export/finish",
                   "not_measured": "source ingestion lag, network, detectors, agents, dashboard, notification, production end-to-end latency"}
        summary = {"version": protocol["version"], "status": "engineering_replay_not_model_evaluation",
                   **semantic_summary(events, states), "model_fit": False, "threshold_tuning": False,
                   "new_labels": False, "anomaly_scoring": False, "multi_agent_implemented": False,
                   "production_near_real_time_validated": False, "synthetic_business_events": False}
        for name, value in [("protocol.json", protocol), ("source_audit.json", source), ("summary.json", summary), ("runtime.json", runtime)]:
            write_json(output / name, value)
        for name, expected in source["files"].items():
            if digest(ROOT / name) != expected:
                raise ValueError("Source changed during replay")
        if implementations != implementation_hashes():
            raise ValueError("Implementation changed during replay")
        write_json(output / "manifest.json", {"status": "complete", "version": protocol["version"],
                   "implementation_hashes": implementations, "source_files": source["files"],
                   "output_hashes": {str(p.relative_to(output)): digest(p) for p in sorted(output.iterdir())}})
        if destination.exists():
            raise ValueError("Output appeared while building; refusing overwrite")
        output.rename(destination)
    return summary


def verify_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = read_json(directory / "manifest.json")
    if manifest["implementation_hashes"] != implementation_hashes():
        raise ValueError("Replay implementation changed; use a new version")
    for base, hashes in [(directory, manifest["output_hashes"]), (ROOT, manifest["source_files"])]:
        for name, expected in hashes.items():
            if digest(base / name) != expected:
                raise ValueError(f"Integrity mismatch: {name}")
    protocol, events, lineage, scope, source = load_inputs()
    read_lines = lambda name: [json.loads(line) for line in (directory / name).read_text(encoding="utf-8").splitlines()]
    if read_json(directory / "protocol.json") != protocol or read_json(directory / "source_audit.json") != source:
        raise ValueError("Protocol or source audit differs")
    if read_lines("events.jsonl") != events:
        raise ValueError("Published events differ from real visible source milestones")
    with core.StateStore(":memory:", protocol["start_inclusive"], protocol["cutoff_exclusive"]) as rebuilt:
        for event in events:
            rebuilt.apply(event)
        rebuilt.finish()
        states, journal = rebuilt.states(), list(rebuilt.journal())
    if read_lines("states.jsonl") != states or read_lines("journal.jsonl") != journal:
        raise ValueError("Replay state or journal is not reproducible")
    orders, stages = state_tables(states, protocol["cutoff_exclusive"])
    for name, rows in [("order_states.csv", orders), ("stage_observations.csv", stages), ("event_lineage.csv", lineage)]:
        serialized = [{k: "" if v is None else str(v) for k, v in row.items()} for row in rows]
        if list(read_csv(directory / name)) != serialized:
            raise ValueError(f"Reproduced table differs: {name}")
    if {s["order_id"] for s in states} != scope:
        raise ValueError("Order coverage differs")
    summary = read_json(directory / "summary.json")
    if any(summary.get(k) != v for k, v in semantic_summary(events, states).items()):
        raise ValueError("Summary differs from reproduced state")
    connection = sqlite3.connect((directory / "state.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        saved_states = [json.loads(r[0]) for r in connection.execute("SELECT state_json FROM states ORDER BY order_id")]
        saved_journal = [{"sequence": seq, "event": json.loads(e), "state_after": json.loads(s), "state_sha256": h}
                         for seq, e, s, h in connection.execute("SELECT sequence, payload, state_after, state_sha256 FROM journal ORDER BY sequence")]
        if saved_states != states or saved_journal != journal:
            raise ValueError("SQLite state/journal differs from reproduced replay")
        metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        if (json.loads(metadata["contract"]) != rebuilt.contract
                or metadata["closed_at_exclusive"] != protocol["cutoff_exclusive"]):
            raise ValueError("SQLite checkpoint boundary differs")
        for state_json, sha in connection.execute("SELECT state_json, state_sha256 FROM states"):
            if core.fingerprint(json.loads(state_json)) != sha:
                raise ValueError("SQLite state fingerprint differs")
    finally:
        connection.close()
    return {"verified": True, "orders": len(states), "events": len(events), "semantic_replay_reproduced": True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_snapshot(args.output) if args.verify else build(args.output), indent=2))


if __name__ == "__main__":
    main()
