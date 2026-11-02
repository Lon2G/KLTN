"""Build descriptive delivery BI tables without relabeling or fitting models."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import inspect_process_evidence as inspection
import prepare_hierarchical_review as hierarchy

ROOT = inspection.ROOT
review = inspection.review
shared = inspection.shared
VERSION = "delivery_performance_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_delivery_performance_v1"
PLAN = ROOT/"data/experiments/olist_temporal_evaluation_plan_v1"
TEMPLATE = ROOT/"templates/delivery_performance_v1"
TIMES = hierarchy.TIMES
ESTIMATE = "order_estimated_delivery_date"
STAGES = [(TIMES[0], TIMES[1], "approval"), (TIMES[1], TIMES[2], "preparation_handoff"),
          (TIMES[2], TIMES[3], "transport"), (TIMES[0], TIMES[3], "total_cycle")]
TABLES = {"order_performance.csv", "stage_durations.csv", "overview.csv", "context_summary.csv",
          "product_order_links.csv", "seller_order_links.csv", "product_summary.csv", "seller_summary.csv",
          "stage_summary.csv", "stage_context_summary.csv", "saved_signals.csv"}
STATES = ["early", "on_date", "late", "unknown", "excluded_source_conflict"]


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {**inspection.code_hashes(), **hierarchy.code_hashes(),
            **{str(p.relative_to(ROOT)): review.digest(p) for p in
               [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}}


def load_boundary(plan=PLAN):
    plan = Path(plan).resolve()
    manifest = review.read_json(plan/"manifest.json")
    if manifest.get("version") != "temporal_evaluation_plan_v1" or manifest.get("status") != "complete":
        raise ValueError("Expected frozen, unactivated outer-split proposal")
    shared.verify_files(plan, manifest["output_hashes"])
    summary = review.read_json(plan/"summary.json")
    if summary["split_activated"] or summary != manifest["summary"]:
        raise ValueError("Split proposal identity differs")
    boundary = pd.Timestamp(summary["boundary_purchase_timestamp_inclusive_for_test"])
    if pd.isna(boundary) or boundary.tz is not None:
        raise ValueError("Expected recorded naive boundary")
    return boundary, {"directory": str(plan), "manifest_sha256": review.digest(plan/"manifest.json")}


def verify_boundary(record):
    directory = Path(record["directory"])
    if review.digest(directory/"manifest.json") != record["manifest_sha256"]:
        raise ValueError("Frozen split proposal changed")
    return load_boundary(directory)[0]


def verify_sources(provenance):
    inspection.verify_sources(provenance["inspection"])
    if str(verify_boundary(provenance["outer_plan"])) != provenance["boundary"]:
        raise ValueError("Boundary differs")
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Performance code/protocol changed; create a new version")


def load_inputs():
    repository = inspection.load_repository()
    boundary, plan = load_boundary()
    provenance = {"inspection": repository["provenance"], "outer_plan": plan, "boundary": str(boundary),
                  "code_hashes": code_hashes()}
    verify_sources(provenance)
    return repository, boundary, provenance


def order_performance(source, boundary):
    inspection.integration.business.require_key(source, ["order_id"], "performance source")
    if not source.split.isin(["train", "validation"]).all():
        raise ValueError("Final-test rows cannot enter delivery performance")
    source = source.loc[source.order_purchase_timestamp.lt(boundary)].sort_values("order_id").reset_index(drop=True)
    fields = ["order_id", "split", "clean_order_record", "customer_state", "distance_band",
              "all_pairs_quality_eligible", "geography_quality_reasons", "seller_count", ESTIMATE]
    result = source[fields].copy()
    result["snapshot_order_status"] = source.order_status
    cutoff = source.event_time_cutoff_exclusive.clip(upper=boundary)
    result["visible_before_exclusive"] = cutoff
    result["purchase_month"] = source.order_purchase_timestamp.dt.strftime("%Y-%m")
    result["seller_attribution_scope"] = np.where(source.seller_count.eq(1), "single_seller_order", "multi_seller_order")
    result["recorded_status_delivery_conflict"] = source.status_delivery_conflict
    for field in TIMES:
        result[field] = source[field].where(source[field].lt(cutoff))
    result["source_missing_milestones"] = source[TIMES].isna().sum(axis=1)
    result["post_cutoff_milestones"] = sum(source[field].ge(cutoff).astype(int) for field in TIMES)
    result["visible_chronology_inversions"] = sum(result[b].lt(result[a]).astype(int) for a, b in combinations(TIMES, 2))
    result["promise_before_purchase_date"] = result[ESTIMATE].dt.normalize().lt(result[TIMES[0]].dt.normalize())
    result["quality_conflict"] = (result.visible_chronology_inversions.gt(0)
                                  | result.recorded_status_delivery_conflict | result.promise_before_purchase_date)
    result["delivery_visibility"] = np.select(
        [source[TIMES[3]].isna(), source[TIMES[3]].ge(cutoff)], ["source_missing", "at_or_after_cutoff"], default="visible")
    delta = (result[TIMES[3]].dt.normalize()-result[ESTIMATE].dt.normalize()).dt.days.astype("Int64")
    result["promise_delta_calendar_days"] = delta
    result["days_early"] = (-delta).clip(lower=0)
    result["days_late"] = delta.clip(lower=0)
    result["delivery_timing_observation"] = "unknown"
    for mask, state in [(delta.lt(0), "early"), (delta.eq(0), "on_date"), (delta.gt(0), "late")]:
        result.loc[mask.fillna(False), "delivery_timing_observation"] = state
    result["performance_eligible"] = delta.notna() & ~result.quality_conflict
    result["performance_state"] = result.delivery_timing_observation.mask(result.quality_conflict, "excluded_source_conflict")
    return result


def stage_durations(orders):
    pieces = []
    for start, end, stage in STAGES:
        rows = orders[["order_id", "split", "purchase_month", "customer_state", "distance_band",
                       "performance_state", "quality_conflict", "visible_before_exclusive"]].copy()
        rows["stage"] = stage
        rows["start_timestamp"], rows["end_timestamp"] = orders[start], orders[end]
        rows["duration_days"] = (orders[end]-orders[start]).dt.total_seconds()/86400
        rows["duration_state"] = np.select(
            [rows.duration_days.isna(), rows.duration_days.lt(0), rows.duration_days.eq(0)],
            ["endpoint_not_visible", "negative_recorded_duration", "zero_recorded_duration"], default="positive_observed")
        rows["duration_eligible"] = rows.duration_days.ge(0) & ~rows.quality_conflict
        pieces.append(rows)
    return pd.concat(pieces, ignore_index=True).sort_values(["order_id", "stage"]).reset_index(drop=True)


def rate_summary(frame, keys):
    results = []
    for group_key, group in frame.groupby(keys, dropna=False, sort=True):
        values = group_key if isinstance(group_key, tuple) else (group_key,)
        if not group.order_id.is_unique:
            raise ValueError("An order was counted twice within an entity/context group")
        valid = group.loc[group.performance_eligible]
        row = {**dict(zip(keys, values)), "orders": len(group), "comparable_orders": len(valid),
               "single_seller_orders": int(group.seller_count.eq(1).sum()),
               "multi_seller_orders": int(group.seller_count.gt(1).sum())}
        row.update({state+"_orders": int(group.performance_state.eq(state).sum()) for state in STATES})
        row.update({state+"_rate": float(valid.performance_state.eq(state).mean()) if len(valid) else np.nan
                    for state in ["early", "on_date", "late"]})
        row["median_days_early_among_early"] = valid.loc[valid.performance_state.eq("early"), "days_early"].median()
        row["median_days_late_among_late"] = valid.loc[valid.performance_state.eq("late"), "days_late"].median()
        row["median_signed_promise_delta_days"] = valid.promise_delta_calendar_days.median()
        results.append(row)
    result = pd.DataFrame(results).replace({pd.NA: np.nan})
    numeric = ["median_days_early_among_early", "median_days_late_among_late", "median_signed_promise_delta_days"]
    return result.astype({field: "float64" for field in numeric})


def duration_summary(frame, keys):
    results = []
    for group_key, group in frame.groupby(keys, dropna=False, sort=True):
        values = group_key if isinstance(group_key, tuple) else (group_key,)
        valid = group.loc[group.duration_eligible, "duration_days"]
        results.append({**dict(zip(keys, values)), "orders": len(group), "duration_eligible_orders": len(valid),
                        "unknown_duration_orders": int(group.duration_days.isna().sum()),
                        "negative_duration_orders": int(group.duration_days.lt(0).sum()),
                        "zero_duration_orders": int(group.duration_days.eq(0).sum()),
                        "quality_conflict_orders": int(group.quality_conflict.sum()),
                        "p25_duration_days": valid.quantile(.25), "median_duration_days": valid.median(),
                        "p75_duration_days": valid.quantile(.75)})
    return pd.DataFrame(results)


def build_tables(repository, boundary):
    source = repository["frames"]["case_evidence.csv"]
    orders = order_performance(source, boundary)
    durations = stage_durations(orders)
    items = repository["frames"]["item_source_evidence.csv"]
    items = items.loc[items.order_id.isin(orders.order_id)]
    links = {}
    for entity in ["product", "seller"]:
        field = entity+"_id"
        if items[field].isna().any() or items[field].eq("").any():
            raise ValueError("Missing entity ID cannot be silently dropped")
        link = items[["order_id", field]].drop_duplicates().sort_values([field, "order_id"]).reset_index(drop=True)
        if set(link.order_id) != set(orders.order_id):
            raise ValueError("Every accessible order requires its real item links")
        links[entity] = link
    saved_columns = ["order_id", "business_profiles_scored", "business_profiles_with_warning", "delivery_models_forecast",
                     "delivery_models_evaluated", "delivery_models_long_warning", "delivery_models_short_warning",
                     "delivery_models_zero_duration_warning", "evidence_codes_json"]
    saved = source.loc[source.order_id.isin(orders.order_id), saved_columns].sort_values("order_id").reset_index(drop=True)
    saved["historical_event_time_cutoff_exclusive"] = source.set_index("order_id").loc[saved.order_id, "event_time_cutoff_exclusive"].to_numpy()
    withheld = orders.post_cutoff_milestones.gt(0)
    saved["saved_signals_visibility"] = np.where(withheld, "withheld_post_cutoff_observations", "historical_snapshot_not_new_model_selection")
    # Old warnings can encode outcomes after the stricter diagnostic cutoff.
    saved.loc[withheld, saved_columns[1:]] = pd.NA
    frames = {"order_performance.csv": orders, "stage_durations.csv": durations,
              "overview.csv": rate_summary(orders, ["split"]),
              "context_summary.csv": rate_summary(orders, ["split", "purchase_month", "customer_state", "distance_band"]),
              "stage_summary.csv": duration_summary(durations, ["split", "performance_state", "stage"]),
              "stage_context_summary.csv": duration_summary(durations, ["split", "purchase_month", "customer_state",
                                                                         "distance_band", "performance_state", "stage"]),
              "saved_signals.csv": saved}
    for entity, link in links.items():
        frames[entity+"_order_links.csv"] = link
        keys = [entity+"_id", "split"] + (["seller_attribution_scope"] if entity == "seller" else [])
        frames[entity+"_summary.csv"] = rate_summary(link.merge(orders, on="order_id", validate="many_to_one"), keys)
    if set(frames) != TABLES or orders.order_id.isin(repository["protected_ids"]).any():
        raise ValueError("Invalid performance output/blind protection")
    counts = orders.performance_state.value_counts().to_dict()
    summary = {"version": VERSION, "source_development_orders": repository["source_orders"],
               "protected_original_pilot_orders": len(repository["protected_ids"]),
               "additional_proposed_test_purchases_withheld": len(source)-len(orders), "orders": len(orders),
               "performance_counts": {key: int(counts.get(key, 0)) for key in STATES},
               "comparable_orders": int(orders.performance_eligible.sum()),
               "delivery_visibility_counts": {str(k): int(v) for k, v in orders.delivery_visibility.value_counts().items()},
               "visible_chronology_conflict_orders": int(orders.visible_chronology_inversions.gt(0).sum()),
               "estimated_date_non_midnight_records": int((orders[ESTIMATE].notna() & orders[ESTIMATE].ne(orders[ESTIMATE].dt.normalize())).sum()),
               "stage_rows": len(durations), "stage_eligible_rows": int(durations.duration_eligible.sum()),
               "product_order_links": len(links["product"]), "seller_order_links": len(links["seller"]),
               "distinct_products": int(links["product"].product_id.nunique()), "distinct_sellers": int(links["seller"].seller_id.nunique()),
               "original_item_rows": len(items), "outer_boundary_guard": str(boundary),
               "saved_signal_rows_withheld_for_post_cutoff_observations": int(withheld.sum()),
               **{key: False for key in ["split_activated", "model_trained", "threshold_changed", "human_labels_created",
                                        "test_scored", "anomaly_accuracy_computed", "causal_bottleneck_identified", "near_real_time_validated"]}}
    return frames, summary


def destination_guard(output, provenance):
    return hierarchy.destination_guard(output, {"batch_directory": provenance["inspection"]["pilot_directory"]})


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = destination_guard(output, provenance)
    if output.exists():
        raise FileExistsError("Refusing to overwrite performance snapshot")
    if set(frames) != TABLES:
        raise ValueError("Missing performance tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".delivery-performance-", dir=output.parent) as temporary:
        staging = Path(temporary)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            actual = shared.read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(actual, frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "schemas": schemas, "summary": summary,
                    "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())}}
        review.write_json(staging/"manifest.json", manifest)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != TABLES | {"README.md", "protocol.json", "summary.json"}):
        raise ValueError("Invalid performance manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Performance protocol/summary mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        if args.summary:
            result = load_snapshot(args.output)["summary"]
        else:
            if args.output.exists():
                raise FileExistsError("Output already exists")
            repository, boundary, provenance = load_inputs()
            frames, summary = build_tables(repository, boundary)
            result = write_snapshot(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Delivery performance stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
