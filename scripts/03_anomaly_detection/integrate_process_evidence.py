"""Integrate real development evidence without retraining, labeling or ranking cases."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd
import pm4py

import experiment_seller_delivery as delivery
import prepare_business_review as pilot

ROOT = delivery.ROOT
sys.path.insert(0, str(ROOT/"scripts/02_process_mining"))
import conformance_analysis as reference

shared = delivery.shared
business = shared.business
ablation = pilot.ablation
VERSION = "integrated_process_evidence_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_integrated_evidence_v1"
README = ROOT/"templates/integrated_evidence_v1/README.md"
TIMES = delivery.seller.TIMES
ACTIVITIES = reference.REFERENCE_PROCESS
ANCHOR = pilot.protocol()["anchor_candidate"]
TABLES = {"case_evidence.csv", "milestone_evidence.csv", "visible_events.csv", "process_cases.csv",
          "process_durations.csv", "process_variants.csv", "process_dfg.csv", "business_anchor_evidence.csv",
          "delivery_model_evidence.csv", "seller_pair_evidence.csv", "coverage.csv", "evidence_catalog.csv"}


def protocol():
    return {"version": VERSION, "scope": "all_frozen_development_orders; no_reserved_test",
            "event_view": "recorded_timestamp_strictly_before_original_partition_cutoff",
            "event_clock": "source_wall_clock_naive; not_record_arrival_time",
            "reference_process": ACTIVITIES, "reference_source": "existing_sequential_Petri_net",
            "fitness_scope": "all_four_visible_milestones_and_distinct_timestamps_only",
            "fitness_interpretation": "descriptive_reference_fit; not_anomaly_label_or_probability",
            "ties": "canonical_reference_order_for_display; exclude_tied_traces_from_fitness_and_DFG",
            "inversions": "all_pairs_of_visible_milestones_in_reference_order",
            "missing": "source_missing_and_post_cutoff_are_separate; never_impute_events_or_final_status",
            "business_anchor": ANCHOR, "anchor_purpose": "existing_pilot_display_anchor_not_selected_winner",
            "delivery_models": [x[0] for x in delivery.MODELS], "delivery_evidence": "all_saved_profiles; no_selection_or_rescoring",
            "source_fields": "retrospective_snapshot; arrival_and_revision_times_unknown",
            "fit_policy": "none; original_models_scores_and_thresholds_unchanged",
            "case_output": "evidence_and_separate_signals_only; no_combined_label_score_or_priority",
            "review_policy": "analyst_only_output; do_not_show_to_blinded_reviewers"}


def code_hashes():
    return {**delivery.code_hashes(), **ablation.code_hashes(), **pilot.code_hashes(),
            **{str(path.relative_to(ROOT)): business.audit.file_hash(path)
               for path in [Path(__file__), Path(reference.__file__), README]}}


def verify_sources(provenance):
    for key, item in provenance["snapshots"].items():
        path = Path(item["directory"])
        if business.audit.file_hash(path/"manifest.json") != item["manifest_sha256"]:
            raise ValueError(f"Integrated source manifest changed: {key}")
        shared.verify_files(path, item["output_hashes"])
    delivery.verify_sources(provenance["delivery_sources"])
    ablation.verify_sources(provenance["ablation_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Integration code/protocol changed")


def load_inputs(delivery_dir=delivery.DEFAULT_OUTPUT, ablation_dir=ablation.DEFAULT_OUTPUT):
    delivery_dir, ablation_dir = Path(delivery_dir).resolve(), Path(ablation_dir).resolve()
    dm = delivery.load_snapshot(delivery_dir)
    am = shared.read_json(ablation_dir/"manifest.json")
    if (am.get("status") != "complete" or am.get("study_version") != ablation.VERSION
            or am.get("identity") != business.IDENTITY or am.get("code_hashes") != ablation.code_hashes()):
        raise ValueError("Expected unchanged business ablation")
    parent = Path(am["provenance"]["features_directory"])
    if am["provenance"]["features_manifest_sha256"] != dm["provenance"]["seller_sources"]["business_manifest_sha256"]:
        raise ValueError("Business and delivery experiments have different source lineage")
    bm = shared.read_json(parent/"manifest.json")
    seller_dir = Path(dm["provenance"]["seller_directory"])
    sm = delivery.seller.load_snapshot(seller_dir)
    snapshots = {"delivery": (delivery_dir, dm), "ablation": (ablation_dir, am), "business": (parent, bm), "seller": (seller_dir, sm)}
    provenance = {"snapshots": {name: {"directory": str(path), "manifest_sha256": business.audit.file_hash(path/"manifest.json"),
                                       "output_hashes": manifest["output_hashes"]} for name, (path, manifest) in snapshots.items()},
                  "delivery_sources": dm["provenance"], "ablation_sources": am["provenance"], "code_hashes": code_hashes(),
                  "reserved_test_orders_excluded": dm["summary"]["reserved_test_orders_excluded"]}
    verify_sources(provenance)
    read = shared.read_table
    inputs = {"context": read(parent, "order_context.csv", bm), "features": read(parent, "order_features.csv", bm),
              "ledger": read(delivery_dir, "analysis_ledger.csv", dm), "predicted": read(delivery_dir, "predictions.csv", dm),
              "forecasts": read(delivery_dir, "frozen_forecasts.csv", dm),
              "seller_orders": read(seller_dir, "order_seller_history.csv", sm),
              "seller_pairs": read(seller_dir, "seller_order_history.csv", sm),
              "business_tables": {profile: {kind: read(ablation_dir, f"{profile}_{name}.csv", am)
                                            for kind, name in [("scores", "validation_scores"), ("flags", "validation_flags"), ("candidates", "candidates")]}
                                  for profile in ablation.PROFILES}}
    validate_inputs(inputs)
    return inputs, provenance


def validate_inputs(inputs):
    context = inputs["context"]
    business.require_key(context, ["order_id"], "integrated context")
    if context.empty or not context.split.isin(["train", "validation"]).all():
        raise ValueError("Development-only integration required")
    for field, value in [("dataset_id", business.IDENTITY["dataset_id"]), ("marketplace_id", business.IDENTITY["marketplace_id"]),
                         ("product_category_name", business.IDENTITY["primary_category"])]:
        if not context[field].eq(value).all():
            raise ValueError("Integration dataset identity differs")
    if not context.order_purchase_timestamp.lt(context.event_time_cutoff_exclusive).all():
        raise ValueError("Every purchase must precede its declared cutoff")
    for name in ["features", "ledger", "seller_orders"]:
        frame = inputs[name]
        business.require_key(frame, ["order_id"], name)
        if set(frame.order_id) != set(context.order_id):
            raise ValueError(f"Integration must retain identical order scope: {name}")
        keys = ["split", "order_purchase_timestamp"]
        pd.testing.assert_frame_equal(frame.set_index("order_id")[keys].sort_index(), context.set_index("order_id")[keys].sort_index(),
                                      check_dtype=False, check_index_type=False, check_exact=True)
    business.require_key(inputs["seller_pairs"], ["order_id", "seller_id"], "integrated seller pairs")
    if set(inputs["seller_pairs"].order_id) != set(context.order_id):
        raise ValueError("Seller pair scope differs")
    validation = set(context.loc[context.split.eq("validation"), "order_id"])
    for name in ["forecasts", "predicted"]:
        frame = inputs[name]
        selected = frame if name == "forecasts" else frame.loc[frame.inner_role.eq("validation")]
        business.require_key(selected, ["order_id", "model"], name)
        if not set(selected.order_id).issubset(validation) or set(selected.model) != set(protocol()["delivery_models"]):
            raise ValueError("Delivery evidence scope differs")
    for group in inputs["business_tables"].values():
        business.require_key(group["scores"], ["order_id"], "business scores")
        if not set(group["scores"].order_id).issubset(validation):
            raise ValueError("Business evidence outside validation")


def process_evidence(context):
    base = context[["order_id", "split", "clean_order_record", "event_time_cutoff_exclusive", *TIMES]].sort_values("order_id").reset_index(drop=True)
    pieces = []
    for rank, (field, activity) in enumerate(zip(TIMES, ACTIVITIES)):
        rows = base[["order_id", "split", "clean_order_record", "event_time_cutoff_exclusive", field]].rename(columns={field: "recorded_timestamp"})
        rows["source_column"], rows["activity"], rows["activity_rank"] = field, activity, rank
        rows["evidence_state"] = np.where(rows.recorded_timestamp.isna(), "source_missing",
                                         np.where(rows.recorded_timestamp.lt(rows.event_time_cutoff_exclusive), "visible", "at_or_after_cutoff"))
        pieces.append(rows)
    milestones = pd.concat(pieces, ignore_index=True).sort_values(["order_id", "activity_rank"]).reset_index(drop=True)
    events = milestones.loc[milestones.evidence_state.eq("visible")].sort_values(["order_id", "recorded_timestamp", "activity_rank"]).reset_index(drop=True)
    if events.duplicated(["order_id", "activity"]).any():
        raise ValueError("Duplicate observed milestone")
    cases = base[["order_id", "split", "event_time_cutoff_exclusive"]].copy()
    by_order = events.groupby("order_id", sort=False)
    cases["process_variant"] = cases.order_id.map(by_order.activity.agg(" -> ".join))
    cases["process_visible_events"] = cases.order_id.map(by_order.size()).fillna(0).astype(int)
    for state, name in [("source_missing", "process_missing_source_events"), ("at_or_after_cutoff", "process_post_cutoff_events")]:
        counts = milestones.loc[milestones.evidence_state.eq(state)].groupby("order_id").size()
        cases[name] = cases.order_id.map(counts).fillna(0).astype(int)
    ties = events.loc[events.duplicated(["order_id", "recorded_timestamp"], keep=False)].order_id
    cases["process_has_equal_timestamps"] = cases.order_id.isin(ties)
    visible = base.set_index("order_id")[TIMES].copy()
    for field in TIMES:
        visible[field] = visible[field].where(visible[field].lt(base.set_index("order_id").event_time_cutoff_exclusive))
    inverse = pd.Series(0, index=visible.index, dtype="int64")
    for first, second in combinations(TIMES, 2):
        inverse += (visible[first].notna() & visible[second].notna() & visible[second].lt(visible[first])).astype(int)
    cases["process_inverted_pairs"] = cases.order_id.map(inverse)
    cases["process_replay_status"] = "incomplete_observed_trace_not_scored"
    complete = cases.process_visible_events.eq(4)
    cases.loc[complete & cases.process_has_equal_timestamps, "process_replay_status"] = "equal_time_order_unresolved_not_scored"
    usable = complete & ~cases.process_has_equal_timestamps
    cases.loc[usable, "process_replay_status"] = "complete_distinct_trace_replayed"
    case_columns = {"trace_fitness": "process_trace_fitness", "is_fit": "process_reference_is_fit"}
    cases["process_trace_fitness"] = np.nan
    cases["process_reference_is_fit"] = pd.Series(pd.NA, index=cases.index, dtype="boolean")
    selected = events.loc[events.order_id.isin(cases.loc[usable, "order_id"])]
    if len(selected):
        log = reference.prepare_pm4py_dataframe(selected.rename(columns={"order_id": "case_id", "recorded_timestamp": "timestamp"}))
        net, initial, final = reference.build_reference_petri_net()
        result = pm4py.conformance_diagnostics_token_based_replay(log, net, initial, final, return_diagnostics_dataframe=True,
                                                                 opt_parameters={"show_progress_bar": False})
        business.require_key(result, ["case_id"], "PM4Py replay IDs")
        if set(result.case_id) != set(cases.loc[usable, "order_id"]):
            raise ValueError("PM4Py returned a different trace population")
        for source, target in case_columns.items():
            values = cases.order_id.map(result.set_index("case_id")[source])
            cases.loc[usable, target] = values.loc[usable]
    durations = []
    for first, second in [(0, 1), (1, 2), (2, 3), (0, 3)]:
        rows = base[["order_id", "split", "event_time_cutoff_exclusive"]].copy()
        rows["start_activity"], rows["end_activity"] = ACTIVITIES[first], ACTIVITIES[second]
        rows["start_timestamp"] = rows.order_id.map(visible[TIMES[first]])
        rows["end_timestamp"] = rows.order_id.map(visible[TIMES[second]])
        rows["duration_days"] = (rows.end_timestamp-rows.start_timestamp).dt.total_seconds()/86400
        rows["duration_state"] = np.select([rows.duration_days.isna(), rows.duration_days.lt(0), rows.duration_days.eq(0)],
                                            ["endpoint_not_visible", "negative_recorded_duration", "zero_recorded_duration"], default="positive_recorded_duration")
        durations.append(rows)
    variants = cases.groupby(["split", "process_variant", "process_has_equal_timestamps"], dropna=False).size().rename("orders").reset_index()
    dfg_rows = []
    for split in ["train", "validation"]:
        selected = events.loc[events.split.eq(split) & ~events.order_id.isin(ties)]
        if selected.empty:
            continue
        log = reference.prepare_pm4py_dataframe(selected.rename(columns={"order_id": "case_id", "recorded_timestamp": "timestamp"}))
        graph, _, _ = pm4py.discover_dfg(log)
        for (first, second), count in sorted(graph.items()):
            dfg_rows.append({"split": split, "start_activity": first, "end_activity": second, "observed_connections": count,
                             "scope": "visible_events_excluding_entire_equal_time_traces"})
    return {"milestone_evidence.csv": milestones, "visible_events.csv": events, "process_cases.csv": cases,
            "process_durations.csv": pd.concat(durations, ignore_index=True), "process_variants.csv": variants,
            "process_dfg.csv": pd.DataFrame(dfg_rows, columns=["split", "start_activity", "end_activity", "observed_connections", "scope"])}


def saved_model_evidence(inputs):
    anchors = []
    for profile, tables in inputs["business_tables"].items():
        candidate = tables["candidates"].loc[lambda x: x.candidate.eq(ANCHOR)]
        if len(candidate) != 1:
            raise ValueError("Existing sampling anchor missing")
        calculated = ablation.training.engine.apply_thresholds(tables["scores"], candidate)
        expected = tables["flags"][["order_id", ANCHOR]]
        pd.testing.assert_frame_equal(calculated, expected, check_dtype=False, check_exact=True)
        item = candidate.iloc[0]
        rows = tables["scores"][["order_id", item.score_column]].rename(columns={item.score_column: "score"}).copy()
        rows["profile"], rows["candidate"], rows["score_column"] = profile, ANCHOR, item.score_column
        rows["threshold"], rows["threshold_origin"], rows["warning"] = float(item.threshold), item.threshold_origin, expected[ANCHOR].to_numpy()
        rows["interpretation"] = "existing_exploratory_warning_not_label_probability_or_feature_attribution"
        anchors.append(rows)
    forecasts = inputs["forecasts"].copy()
    scored = inputs["predicted"].loc[lambda x: x.inner_role.eq("validation")].copy()
    columns = ["order_id", "model", delivery.context.TARGET, "long_warning", "short_warning", "zero_duration_warning"]
    rows = forecasts.merge(scored[columns], on=["order_id", "model"], how="left", validate="one_to_one")
    for field in ["long_warning", "short_warning", "zero_duration_warning"]:
        rows[field] = rows[field].astype("boolean")
    rows["outcome_available_for_saved_comparison"] = rows[delivery.context.TARGET].notna()
    rows["interpretation"] = "saved_interval_signal_not_confirmed_bottleneck_or_seller_fault"
    return pd.concat(anchors, ignore_index=True), rows


def build_integration(inputs):
    validate_inputs(inputs)
    frames = process_evidence(inputs["context"])
    anchors, forecasts = saved_model_evidence(inputs)
    cases = inputs["context"].sort_values("order_id").reset_index(drop=True).copy()
    geographic = ["inner_role", "seller_states_json", "all_pairs_quality_eligible", "geography_quality_reasons", *delivery.context.DISTANCES, "distance_band"]
    for source in [inputs["features"], inputs["ledger"][["order_id", *geographic]], inputs["seller_orders"], frames["process_cases.csv"]]:
        extra = [column for column in source if column not in cases]
        cases = cases.merge(source[["order_id", *extra]], on="order_id", how="left", validate="one_to_one")
    for name, counts in [("business_profiles_scored", anchors.groupby("order_id").size()),
                         ("business_profiles_with_warning", anchors.groupby("order_id").warning.sum()),
                         ("delivery_models_forecast", forecasts.groupby("order_id").size())]:
        cases[name] = cases.order_id.map(counts).astype("Int64")
    evaluated = forecasts.loc[forecasts.outcome_available_for_saved_comparison]
    cases["delivery_models_evaluated"] = cases.order_id.map(evaluated.groupby("order_id").size()).astype("Int64")
    for field in ["long_warning", "short_warning", "zero_duration_warning"]:
        cases["delivery_models_"+field] = cases.order_id.map(evaluated.groupby("order_id")[field].sum()).astype("Int64")
    codes = [[] for _ in range(len(cases))]
    masks = {
        "visible_milestone_inversion": cases.process_inverted_pairs.gt(0),
        "incomplete_visible_trace": cases.process_visible_events.lt(4),
        "equal_time_order_unresolved": cases.process_has_equal_timestamps,
        "geographic_context_withheld": ~cases.all_pairs_quality_eligible,
        "multi_seller_no_individual_outcome_attribution": cases.seller_count.gt(1),
        "limited_seller_history": cases.history_route.ne("single_seller_with_prior_outcomes"),
        "no_prior_scoped_customer_purchase": cases.customer_prior_orders_in_scope.eq(0),
        "observed_accounting_difference_not_fraud": cases.payment_minus_items_and_freight_minor_units.ne(0),
        "business_anchor_warning": cases.business_profiles_with_warning.gt(0),
        "delivery_long_interval_warning": cases.delivery_models_long_warning.gt(0),
        "delivery_short_interval_warning": cases.delivery_models_short_warning.gt(0),
        "delivery_zero_duration_warning": cases.delivery_models_zero_duration_warning.gt(0),
        "no_validation_model_result_here": cases.business_profiles_scored.isna() & cases.delivery_models_forecast.isna(),
    }
    for code, mask in masks.items():
        for index in cases.index[mask.fillna(False)]:
            codes[index].append(code)
    cases["evidence_codes_json"] = [json.dumps(value) for value in codes]
    cases["interpretation"] = "analyst_evidence_only; no_combined_verdict; source_status_and_business_fields_are_retrospective"
    catalog = [
        ("process", "process_cases.csv; visible_events.csv; process_durations.csv", "original_partition_cutoff", "PM4Py reference fit is not anomaly truth; partial traces unscored"),
        ("transaction_payment_order", "case_evidence.csv", "retrospective_snapshot_arrival_unknown", "Multiple payments/installments and accounting differences can be legitimate"),
        ("customer", "case_evidence.csv", "strictly_prior_scoped_purchases", "Limited category history; no peer-based customer anomaly detector"),
        ("geography", "case_evidence.csv", "retrospective_ZIP_snapshot", "Straight-line representative ZIP distance; provisional quality screen; not road distance"),
        ("seller", "seller_pair_evidence.csv; case_evidence.csv", "strictly_before_current_purchase", "Conditional observed historical rates; not seller fault or cancellation rate"),
        ("business_model", "business_anchor_evidence.csv", "saved_validation_snapshot", "Existing pilot anchor only; joint feature score is not a payment-specific explanation"),
        ("delivery_model", "delivery_model_evidence.csv", "saved_handoff_context_with_purchase_time_seller_history", "All configurations retained; absent outcome means warning unknown; no operational winner"),
    ]
    frames.update({"case_evidence.csv": cases, "business_anchor_evidence.csv": anchors, "delivery_model_evidence.csv": forecasts,
                   "seller_pair_evidence.csv": inputs["seller_pairs"].copy(),
                   "coverage.csv": cases.groupby(["split", "process_replay_status", "history_route"], dropna=False).size().rename("orders").reset_index(),
                   "evidence_catalog.csv": pd.DataFrame(catalog, columns=["group", "table", "availability", "limitation"])})
    summary = {"orders_retained": len(cases), "split_orders": cases.split.value_counts().to_dict(),
               "visible_events": len(frames["visible_events.csv"]), "source_missing_milestones": int(cases.process_missing_source_events.sum()),
               "post_cutoff_milestones_withheld": int(cases.process_post_cutoff_events.sum()),
               "process_replay_status": cases.process_replay_status.value_counts().to_dict(),
               "orders_with_visible_inversions": int(cases.process_inverted_pairs.gt(0).sum()),
               "orders_with_equal_timestamps": int(cases.process_has_equal_timestamps.sum()),
               "business_anchor_orders": int(anchors.order_id.nunique()), "delivery_forecast_orders": int(forecasts.order_id.nunique()),
               "delivery_evaluated_orders": int(evaluated.order_id.nunique()), "seller_pairs_retained": len(inputs["seller_pairs"]),
               **{key: False for key in ["models_trained", "model_weights_loaded", "thresholds_changed", "test_scored", "human_labels_created",
                                        "synthetic_data_used", "probabilities_computed", "accuracy_computed", "winner_selected", "real_time_validated"]}}
    return frames, summary


def write_integration(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite integration: {output}")
    if set(frames) != TABLES:
        raise ValueError("Integration evidence tables missing")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
    with tempfile.TemporaryDirectory(prefix=".integrated-evidence-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            (staging/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text(README.read_text()+"\n## Actual Run\n```json\n"+json.dumps(summary, indent=2)+"\n```\n")
        manifest = {"version": VERSION, "status": "complete", "identity": business.IDENTITY,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "schemas": schemas, "summary": summary,
                    "provenance": provenance, "runtime": {"pm4py": pm4py.__version__, "pandas": pd.__version__, "numpy": np.__version__},
                    "output_hashes": {path.name: business.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = shared.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete" or manifest.get("identity") != business.IDENTITY
            or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "protocol.json", "README.md"}):
        raise ValueError("Expected complete integrated evidence snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    if shared.read_json(directory/"summary.json") != manifest["summary"] or shared.read_json(directory/"protocol.json") != protocol():
        raise ValueError("Integration summary/protocol differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying existing source snapshots; no model loading or training.", flush=True)
        inputs, provenance = load_inputs()
        print("Building cutoff-bound process and separate model evidence.", flush=True)
        frames, summary = build_integration(inputs)
        summary = write_integration(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Integration stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
