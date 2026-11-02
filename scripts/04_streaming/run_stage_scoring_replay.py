"""Replay real milestones through a frozen completed-stage scoring adapter."""

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import sqlite3
import tempfile
from time import perf_counter_ns

import numpy as np
import pandas as pd

import replay_olist_events as replay
import score_event_stages as scoring
import experiment_contextual_delivery as contextual

core, product = scoring.core, scoring.product
ROOT = replay.ROOT
PROTOCOL = ROOT / "templates/event_stage_scoring_v1/protocol.json"
DEFAULT_OUTPUT = ROOT / "data/experiments/olist_event_stage_scoring_v1"
PARITY_FIELDS = ["duration_days", "selected_reference_id", "selected_reference_scope", "selected_reference_n",
                 "selected_reference_mean_days", "selected_reference_median_days", "difference_from_reference_mean_days",
                 "reference_percentile_0_100", "delay_score_0_100", "severity", "long_stage_warning"]


def code_hashes():
    paths = [Path(__file__), Path(scoring.__file__), Path(core.__file__), Path(replay.__file__),
             Path(product.__file__), Path(product.signals.supplement.__file__), Path(contextual.__file__),
             Path(product.shared.__file__), PROTOCOL, product.signals.supplement.TEMPLATE / "protocol.json"]
    return {str(p.relative_to(ROOT)): replay.digest(p) for p in paths}


def read_columns(directory, name, manifest, columns):
    schema = manifest["schemas"][name]
    return pd.read_csv(directory / name, usecols=columns, dtype={c: schema[c] for c in columns},
                       keep_default_na=False, na_values=[""], float_precision="round_trip")[columns]


def context_projection(scope, business_dir, bm, contextual_dir, cm):
    orders = read_columns(business_dir, "order_context.csv", bm, ["order_id", "customer_state"])
    items = read_columns(business_dir, "item_source_evidence.csv", bm, ["order_id", "order_item_id", "product_id", "seller_id"])
    quality = read_columns(contextual_dir, "order_quality.csv", cm,
                           ["order_id", "all_pairs_quality_eligible", "screened_distance_max_km"])
    orders, items, quality = [f.loc[f.order_id.isin(scope)].copy() for f in [orders, items, quality]]
    if any(set(f.order_id) != scope for f in [orders, items, quality]):
        raise ValueError("Static context must cover the whole original validation scope")
    meta, _ = product.order_metadata(items)
    frame = orders.merge(meta, on="order_id", validate="one_to_one").merge(quality, on="order_id", validate="one_to_one")
    frame["seller_attribution_scope"] = np.where(frame.mapped_seller_count.eq(1), "single_seller_order", "multi_seller_order")
    frame["distance_band"] = pd.cut(frame.screened_distance_max_km,
        [*contextual.protocol()["distance_band_edges"], np.inf], labels=contextual.BANDS, right=False).astype("string").fillna("unavailable")
    frame = frame[sorted(scoring.CONTEXT_FIELDS)].sort_values("order_id")
    rows = frame.astype(object).where(frame.notna(), None).to_dict("records")
    return {r["order_id"]: r for r in rows}


def load_inputs():
    protocol = replay.read_json(PROTOCOL)
    sources, manifests, directories = {}, {}, {}
    for name, spec in protocol["snapshots"].items():
        directory = ROOT / spec["path"]
        if replay.digest(directory / "manifest.json") != spec["manifest_sha256"]:
            raise ValueError(f"Frozen parent manifest changed: {name}")
        manifest = replay.read_json(directory / "manifest.json")
        for file, sha in {"manifest.json": spec["manifest_sha256"], **manifest["output_hashes"]}.items():
            path = directory / file
            if replay.digest(path) != sha:
                raise ValueError(f"Frozen parent file changed: {name}/{file}")
            sources[str(path.relative_to(ROOT))] = sha
        manifests[name], directories[name] = manifest, directory
    sources.update(manifests["replay"]["source_files"])
    replay.verify_snapshot(directories["replay"])
    product.load_snapshot(directories["product"])
    selection = replay.read_json(directories["selection"] / "selection.json")
    if (selection["selected_candidate"] != protocol["candidate"] or selection["threshold"] != protocol["score_threshold"]
            or selection["operator"] != protocol["score_operator"] or protocol["modes"] != list(scoring.MODES)):
        raise ValueError("Selected rule or scoring scenarios differ")
    events = [json.loads(line) for line in (directories["replay"] / "events.jsonl").read_text().splitlines()]
    scope = {e["order_id"] for e in events}
    rp = replay.read_json(directories["replay"] / "protocol.json")
    reference = product.shared.read_table(directories["product"], "train_stage_reference.csv", manifests["product"])
    contexts = context_projection(scope, directories["business"], manifests["business"], directories["contextual"], manifests["contextual"])
    pilot = product.shared.read_table(directories["product"], "pilot_scope.csv", manifests["product"])
    held = set(pilot.loc[~pilot.comparison_generated, "order_id"])
    bank = scoring.ReferenceBank(reference, scope, rp["start_inclusive"])
    scorer = scoring.StageScorer(bank, contexts, held)
    saved = product.shared.read_table(directories["product"], "stage_scores.csv", manifests["product"])
    return {"protocol": protocol, "events": events, "start": rp["start_inclusive"], "cutoff": rp["cutoff_exclusive"],
            "scorer": scorer, "saved_scores": saved, "source_files": sources}


def parity_check(results, saved):
    current = pd.DataFrame([r for r in results if r["mode"] == scoring.MODES[1]])
    expected = saved.loc[saved.delay_score_0_100.notna()]
    if expected.empty:
        raise ValueError("No existing scored stages for parity")
    keys = ["order_id", "stage"]
    old, new = expected.set_index(keys)[PARITY_FIELDS].sort_index(), current.set_index(keys)[PARITY_FIELDS]
    new = new.loc[old.index].sort_index()
    pd.testing.assert_frame_equal(new, old, check_dtype=False, check_exact=True)
    rows = [{"order_id": oid, "stage": stage, "exact_parity": True} for oid, stage in old.index]
    return rows, {"existing_scored_stages_compared": len(old), "existing_orders_compared": len(expected.order_id.unique()),
                  "fields_compared": PARITY_FIELDS, "all_exactly_equal": True,
                  "meaning": "numerical_regression_on_shared_assessable_scope_not_independent_accuracy"}


def summarize(inputs, results, decisions, orders, parity):
    modes = {}
    for mode in scoring.MODES:
        rows = [r for r in results if r["mode"] == mode]
        os = [r for r in orders if r["mode"] == mode]
        journal = [d for d in decisions if d["result"]["mode"] == mode]
        scored = [r for r in rows if r["delay_score_0_100"] is not None]
        warnings = [r for r in scored if r["long_stage_warning"]]
        emitted = [d for d in journal if d["action"] == "warning_emitted"]
        modes[mode] = {"orders": len(os), "stage_rows": len(rows), "scored_stage_rows": len(scored),
                       "orders_with_any_score": sum(r["scored_stages"] > 0 for r in os),
                       "order_coverage": dict(Counter(r["coverage"] for r in os)),
                       "assessment_status_counts": dict(Counter(r["assessment_status"] for r in rows)),
                       "reference_scope_counts": dict(Counter(r["selected_reference_scope"] for r in scored)),
                       "current_warning_stages": len(warnings),
                       "current_warning_orders": sum(r["any_long_stage_warning"] is True for r in os),
                       "warning_stages_by_stage": dict(Counter(r["stage"] for r in warnings)),
                       "warning_emissions": len(emitted),
                       "warning_emissions_before_delivery_observed": sum(not d["delivery_observed_when_evaluated"] for d in emitted),
                       "orders_warned_before_delivery_observed": len({d["result"]["order_id"] for d in emitted if not d["delivery_observed_when_evaluated"]}),
                       "assessment_withdrawals": sum(d["action"] == "assessment_withdrawn_after_new_evidence" for d in journal)}
    return {"version": scoring.VERSION, "purpose": "engineering_completed_stage_scoring_under_explicit_availability_assumption",
            "events": len(inputs["events"]), "orders": len(inputs["scorer"].contexts),
            "protected_review_orders": len(inputs["scorer"].held_ids), "scenarios": modes, "parity": parity,
            "semantic_stages_sha256": core.fingerprint(results), "semantic_decisions_sha256": core.fingerprint(decisions),
            "model_fit": False, "threshold_tuning": False, "new_labels": False, "synthetic_business_data": False,
            "independent_accuracy_evaluation": False, "ongoing_stage_alerts": False,
            "multi_agent_implemented": False, "production_near_real_time_validated": False}


def execute(inputs, database):
    latencies = []
    with scoring.ScoredStateStore(database, inputs["start"], inputs["cutoff"], inputs["scorer"]) as store:
        before_loop = perf_counter_ns()
        for event in inputs["events"]:
            before = perf_counter_ns()
            result = store.apply(event)
            latencies.append((perf_counter_ns() - before) / 1e6)
            if not result["applied"]:
                raise ValueError("Builder expects a new replay, not duplicate events")
        seconds = (perf_counter_ns() - before_loop) / 1e9
        store.finish()
        results, decisions = store.stage_results(), list(store.scoring_journal())
    orders = scoring.aggregate_orders(results)
    parity_rows, parity = parity_check(results, inputs["saved_scores"])
    summary = summarize(inputs, results, decisions, orders, parity)
    return results, decisions, orders, parity_rows, summary, latencies, seconds


def build(destination=DEFAULT_OUTPUT):
    destination = Path(destination).resolve()
    if destination.parent != ROOT / "data/experiments" or destination.exists():
        raise ValueError("Output must be a NEW direct child of data/experiments; no overwrite")
    inputs = load_inputs()
    implementations = code_hashes()
    with tempfile.TemporaryDirectory(prefix=".stage-replay-", dir=destination.parent) as temp:
        output = Path(temp) / "snapshot"
        output.mkdir()
        started = datetime.now(timezone.utc).isoformat()
        results, decisions, orders, parity_rows, summary, times, seconds = execute(inputs, output / "state.sqlite")
        groups = [inputs["scorer"].bank.registry[k] for k in sorted(inputs["scorer"].bank.registry)]
        replay.write_jsonl(output / "stage_decisions.jsonl", decisions)
        replay.write_jsonl(output / "latest_stage_scores.jsonl", results)
        replay.write_jsonl(output / "static_context_scenario_inputs.jsonl", inputs["scorer"].contexts.values())
        for name, rows in [("stage_scores.csv", results), ("order_scores.csv", orders),
                           ("reference_groups.csv", groups), ("batch_parity.csv", parity_rows)]:
            replay.write_csv(output / name, rows, list(rows[0]))
        replay.write_csv(output / "processing_times.csv",
                         ({"sequence": i + 1, "event_id": e["event_id"], "apply_score_commit_ms": ms}
                          for i, (e, ms) in enumerate(zip(inputs["events"], times))),
                         ["sequence", "event_id", "apply_score_commit_ms"])
        runtime = {"started_at_utc": started, "finished_at_utc": datetime.now(timezone.utc).isoformat(),
                   "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "platform": platform.platform(),
                   "loop_seconds": seconds, "events_per_loop_second": len(times) / seconds,
                   "apply_score_commit_ms_p50": replay.percentile(times, .5),
                   "apply_score_commit_ms_p95": replay.percentile(times, .95), "apply_score_commit_ms_max": max(times),
                   "definition": "single local unpaced run; both scenarios, state, scores and synchronous commit; cold peer lookup cache included",
                   "excluded": "source loading, parity/export, upstream ingestion lag, network, multi-agent, dashboard and notifications"}
        for name, value in [("protocol.json", inputs["protocol"]), ("summary.json", summary), ("parity_summary.json", summary["parity"]),
                            ("runtime.json", runtime)]:
            replay.write_json(output / name, value)
        for name, sha in inputs["source_files"].items():
            if replay.digest(ROOT / name) != sha:
                raise ValueError("Source changed during replay")
        if implementations != code_hashes():
            raise ValueError("Implementation changed during replay")
        replay.write_json(output / "manifest.json", {"status": "complete", "version": scoring.VERSION,
            "scorer_signature": inputs["scorer"].signature, "implementation_hashes": implementations,
            "source_files": inputs["source_files"], "output_hashes": {p.name: replay.digest(p) for p in sorted(output.iterdir())}})
        if destination.exists():
            raise ValueError("Output appeared while building; refusing overwrite")
        output.rename(destination)
    return summary


def verify_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = replay.read_json(directory / "manifest.json")
    if manifest["implementation_hashes"] != code_hashes():
        raise ValueError("Scoring implementation changed; use a new version")
    for base, files in [(directory, manifest["output_hashes"]), (ROOT, manifest["source_files"])]:
        for name, sha in files.items():
            if replay.digest(base / name) != sha:
                raise ValueError(f"Integrity mismatch: {name}")
    inputs = load_inputs()
    results, decisions, orders, parity_rows, summary, _, _ = execute(inputs, ":memory:")
    if (manifest["scorer_signature"] != inputs["scorer"].signature
            or replay.read_json(directory / "summary.json") != summary
            or replay.read_json(directory / "protocol.json") != inputs["protocol"]
            or replay.read_json(directory / "parity_summary.json") != summary["parity"]):
        raise ValueError("Summary, protocol or frozen scorer differs")
    groups = [inputs["scorer"].bank.registry[k] for k in sorted(inputs["scorer"].bank.registry)]
    for name, expected in [("stage_decisions.jsonl", decisions), ("latest_stage_scores.jsonl", results),
                           ("static_context_scenario_inputs.jsonl", list(inputs["scorer"].contexts.values()))]:
        actual = [json.loads(line) for line in (directory / name).read_text().splitlines()]
        if actual != expected:
            raise ValueError(f"Semantic replay mismatch: {name}")
    for name, rows in [("stage_scores.csv", results), ("order_scores.csv", orders),
                       ("reference_groups.csv", groups), ("batch_parity.csv", parity_rows)]:
        if list(replay.read_csv(directory / name)) != [{k: "" if v is None else str(v) for k, v in row.items()} for row in rows]:
            raise ValueError(f"Reproduced CSV differs: {name}")
    connection = sqlite3.connect((directory / "state.sqlite").resolve().as_uri() + "?mode=ro", uri=True)
    try:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("SQLite integrity check failed")
        if connection.execute("PRAGMA foreign_key_check").fetchall():
            raise ValueError("SQLite journal foreign key mismatch")
        raw_states = [json.loads(r[0]) for r in connection.execute("SELECT state_json FROM states ORDER BY order_id")]
        raw_journal = [{"sequence": seq, "event": json.loads(event), "state_after": json.loads(state), "state_sha256": sha}
                       for seq, event, state, sha in connection.execute(
                           "SELECT sequence,payload,state_after,state_sha256 FROM journal ORDER BY sequence")]
        parent = ROOT / inputs["protocol"]["snapshots"]["replay"]["path"]
        for name, actual in [("states.jsonl", raw_states), ("journal.jsonl", raw_journal)]:
            if actual != [json.loads(line) for line in (parent / name).read_text().splitlines()]:
                raise ValueError("Raw milestone state/journal changed under scoring adapter")
        saved = [json.loads(r[0]) for r in connection.execute("SELECT result_json FROM stage_states ORDER BY mode,order_id,stage")]
        saved_decisions = [{"sequence": seq, "event_id": event, "event_time": time, "action": action,
                            "delivery_observed_when_evaluated": bool(delivered), "result": json.loads(result), "result_sha256": sha}
                           for seq, event, time, action, delivered, result, sha in connection.execute(
                               "SELECT sequence,event_id,event_time,action,delivery_observed,result_json,result_sha256 FROM score_journal ORDER BY sequence,mode,stage")]
        if saved != results or saved_decisions != decisions:
            raise ValueError("SQLite scores differ from reproduced results")
        if connection.execute("SELECT value FROM metadata WHERE key='scorer_signature'").fetchone()[0] != inputs["scorer"].signature:
            raise ValueError("SQLite scorer contract differs")
        if connection.execute("SELECT value FROM metadata WHERE key='closed_at_exclusive'").fetchone()[0] != inputs["cutoff"]:
            raise ValueError("SQLite scoring replay boundary differs")
        for encoded, sha in connection.execute("SELECT result_json,result_sha256 FROM stage_states"):
            if core.fingerprint(json.loads(encoded)) != sha:
                raise ValueError("SQLite stage fingerprint differs")
    finally:
        connection.close()
    return {"verified": True, "events": len(inputs["events"]), "stage_rows": len(results),
            "decision_records": len(decisions), "batch_parity_stages": len(parity_rows)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    print(json.dumps(verify_snapshot(args.output) if args.verify else build(args.output), indent=2))


if __name__ == "__main__":
    main()
