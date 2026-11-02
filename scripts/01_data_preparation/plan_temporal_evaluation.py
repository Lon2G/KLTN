"""Plan an outcome-blind 80/20 temporal evaluation; do not activate or train it."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import sys
import tempfile

import pandas as pd

import build_geographic_features as shared

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts/03_anomaly_detection"))
import review_business_cases as review


VERSION = "temporal_evaluation_plan_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_temporal_evaluation_plan_v1"
README = ROOT/"templates/temporal_evaluation_v1/README.md"
SOURCES = {
    "cohort": (ROOT/"data/experiments/bed_bath_table_v1", "manifest.json", "experiment_version", "bed_bath_table_v1"),
    "ablation": (ROOT/"data/experiments/olist_business_ablation_v1", "manifest.json", "study_version", "business_feature_ablation_v1"),
    "delivery": (ROOT/"data/experiments/olist_seller_delivery_v1", "manifest.json", "version", "seller_delivery_ablation_v1"),
    "pilot": (review.DEFAULT_BATCH, "analyst_only/manifest.json", "version", review.VERSION),
}
TIME = "order_purchase_timestamp"
METADATA = ["order_id", TIME, "split", "previously_human_reviewed"]
READ_COLUMNS = {
    ("cohort", "cohort_cases.csv"): METADATA,
    ("ablation", "scope_ledger.csv"): ["order_id", "split", "analysis_role"],
    ("delivery", "frozen_forecasts.csv"): ["order_id"],
    ("delivery", "predictions.csv"): ["order_id", "inner_role"],
    ("pilot", "analyst_only/selection_key.csv"): ["order_id"],
}
TABLES = {"analyst_only/partition_plan.csv", "split_counts.csv", "migration_counts.csv", "exposure_counts.csv"}


def protocol():
    return {
        "version": VERSION, "lifecycle": "proposal_not_activated", "scope": "frozen_olist_bed_bath_table_cohort",
        "outer_unit": "whole_order_id", "requested_test_fraction": "1/5",
        "integer_rounding": "ceil(N/5)", "boundary": "purchase_time_of_first_of_latest_ceil_N_over_5_orders",
        "equal_purchase_times": "entire_boundary_timestamp_group_goes_to_test; report_actual_ratio",
        "development": "purchase_timestamp_strictly_before_boundary", "test": "purchase_timestamp_at_or_after_boundary",
        "timezone": "source_wall_clock_naive; no_timezone_invented",
        "old_role_or_exposure_affects_assignment": False, "outcomes_affect_assignment": False,
        "label_policy": "no_labels_read_or_created; legacy_review_boolean_is_audit_metadata_only",
        "pilot_policy": "preserve_all_original_120; no_resampling_or_new_pseudonym_mapping",
        "membership_is_model_eligibility": False,
        "event_availability": "rebuild_from_real_source_events_strictly_before_each_future_model_boundary",
        "snapshot_payment_items": "retrospective_only; no_payment_arrival_times_invented",
        "old_validation_results": "cannot_be_reused_as_pre_boundary_model_selection_results",
        "inner_design": "declare_fit_calibration_selection_boundaries_within_development_before_any_new_fit",
        "model_selection": "freeze_metric_candidates_and_selection_rule_before_test; no_test_feedback_for_tuning",
        "test_reporting": "report_full_proposed_test_and_predeclared_old_test_vs_reused_validation_strata",
        "independence": "not_untouched; earlier_full_history_exploration_and_reused_validation_disclosed",
        "exposure_audit_scope": "named_saved_sources_and_known_local_activity_inventory; not_a_complete_human_access_log",
        "accuracy": "not_available_without_a_defined_reference_target_and_actual_reference_assessment",
        "near_real_time": "separate_future_recorded_event_replay; not_proven_by_this_partition_plan",
        "activation_requirements": ["new_versioned_development_inputs_and_inner_cutoffs", "feature_availability_audit",
                                    "frozen_metric_and_model_selection_protocol", "explicit_reference_target_for_any_accuracy_claim"],
    }


def code_hashes():
    return {str(path.relative_to(ROOT)): review.digest(path) for path in [Path(__file__), Path(shared.__file__), Path(review.__file__), README]}


def activity_inventory():
    # Hash, but do not parse labels or inspector payloads. Absence is not proof of no human exposure.
    patterns = ["data/manual_review/**/*.csv", "data/manual_review/**/session.json", "data/manual_review/**/journal.json",
                "data/experiments/*/review/review_progress.csv", "outputs/inspections/*/manifest.json", "outputs/inspections/*/result.json"]
    paths = sorted({path for pattern in patterns for path in ROOT.glob(pattern) if path.is_file()})
    return {str(path.relative_to(ROOT)): review.digest(path) for path in paths}


def verify_sources(provenance):
    for key, record in provenance["snapshots"].items():
        directory = Path(record["directory"])
        if review.digest(directory/record["manifest_file"]) != record["manifest_sha256"]:
            raise ValueError(f"Source manifest changed: {key}")
        shared.verify_files(directory, record["output_hashes"])
    if activity_inventory() != provenance["activity_inventory"]:
        raise ValueError("Recorded review/inspection activity changed; prepare a new audit version")
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Temporal planning code or protocol changed")


def read_metadata(key, name, snapshots):
    columns = READ_COLUMNS[(key, name)]
    record = snapshots[key]
    if name not in record["output_hashes"]:
        raise ValueError("Metadata source not bound by its manifest")
    frame = pd.read_csv(Path(record["directory"])/name, usecols=columns, dtype="string", keep_default_na=False)
    if set(frame.columns) != set(columns) or frame[columns].eq("").any().any():
        raise ValueError("Missing planning metadata")
    frame = frame[columns]
    if TIME in columns:
        frame[TIME] = pd.to_datetime(frame[TIME], format="%Y-%m-%d %H:%M:%S", errors="raise")
    if "previously_human_reviewed" in columns:
        if not frame.previously_human_reviewed.isin(["True", "False"]).all():
            raise ValueError("Invalid frozen review-exposure marker")
        frame["previously_human_reviewed"] = frame.previously_human_reviewed.eq("True").astype(bool)
    return frame


def validate_cohort(cohort):
    if (cohort.columns.tolist() != METADATA or len(cohort) < 2 or cohort.isna().any().any()
            or not cohort.order_id.str.fullmatch(r"[0-9a-f]{32}").all() or not cohort.order_id.is_unique
            or not cohort.split.isin(["train", "validation", "test"]).all()
            or str(cohort.previously_human_reviewed.dtype) != "bool"
            or not pd.api.types.is_datetime64_dtype(cohort[TIME]) or cohort[TIME].dt.tz is not None):
        raise ValueError("Invalid unique, timezone-naive cohort metadata")


def load_inputs():
    snapshots, manifests = {}, {}
    for key, (directory, filename, version_key, version) in SOURCES.items():
        before = review.digest(directory/filename)
        manifest = review.read_json(directory/filename)
        if manifest.get("status") != "complete" or manifest.get(version_key) != version:
            raise ValueError(f"Unexpected source version: {key}")
        shared.verify_files(directory, manifest["output_hashes"])
        snapshots[key] = {"directory": str(directory.resolve()), "manifest_file": filename,
                          "manifest_sha256": before, "output_hashes": manifest["output_hashes"]}
        manifests[key] = manifest
    ablation_sources = manifests["ablation"]["provenance"]
    if (ablation_sources["feature_sources"]["reservation_manifest_sha256"] != snapshots["cohort"]["manifest_sha256"]
            or manifests["pilot"]["provenance"]["study_manifest_sha256"] != snapshots["ablation"]["manifest_sha256"]
            or manifests["delivery"]["provenance"]["seller_sources"]["business_manifest_sha256"] != ablation_sources["features_manifest_sha256"]):
        raise ValueError("Planning sources do not share the frozen cohort lineage")
    provenance = {"snapshots": snapshots, "activity_inventory": activity_inventory(), "code_hashes": code_hashes(),
                  "parsed_columns": [{"snapshot": key, "file": name, "columns": columns} for (key, name), columns in READ_COLUMNS.items()]}
    tables = {(key, name): read_metadata(key, name, snapshots) for key, name in READ_COLUMNS}
    cohort = tables[("cohort", "cohort_cases.csv")]
    validate_cohort(cohort)
    design = manifests["cohort"]["design"]
    if len(cohort) != design["cohort_orders"] or design["category_original"] != "cama_mesa_banho":
        raise ValueError("Wrong cohort size/category")
    for part in design["partitions"]:
        expected = cohort[TIME].between(pd.Timestamp(part["purchase_start_inclusive"]), pd.Timestamp(part["purchase_end_exclusive"]), inclusive="left")
        if not expected.eq(cohort.split.eq(part["split"])).all():
            raise ValueError("Old split metadata differs from original calendar design")
    ablation = tables[("ablation", "scope_ledger.csv")]
    old_dev = cohort.loc[cohort.split.ne("test")].set_index("order_id").split
    if (not ablation.order_id.is_unique or set(ablation.order_id) != set(old_dev.index)
            or not ablation.set_index("order_id").split.eq(old_dev.reindex(ablation.order_id)).all()):
        raise ValueError("Ablation metadata differs from old development scope")
    pilot = tables[("pilot", "analyst_only/selection_key.csv")]
    protected = frozenset(pilot.order_id)
    if len(pilot) != 120 or len(protected) != 120 or not protected.issubset(cohort.loc[cohort.split.eq("validation"), "order_id"]):
        raise ValueError("Original protected pilot differs")
    forecasts = tables[("delivery", "frozen_forecasts.csv")]
    predictions = tables[("delivery", "predictions.csv")]
    if (not set(forecasts.order_id).issubset(cohort.loc[cohort.split.eq("validation"), "order_id"])
            or not set(predictions.order_id).issubset(old_dev.index)
            or not predictions.inner_role.isin(["calibration", "validation"]).all()):
        raise ValueError("Delivery exposure outside recorded development roles")
    exposure_sets = {
        "old_development_membership": set(old_dev.index),
        "legacy_human_review_marker_at_cohort_freeze": set(cohort.loc[cohort.previously_human_reviewed, "order_id"]),
        "protected_blind_pilot_membership_not_a_completed_review": protected,
        **{f"business_ablation_{role}_membership": set(ablation.loc[ablation.analysis_role.eq(role), "order_id"])
           for role in ["fit", "calibration", "validation"]},
        "seller_delivery_saved_forecast": set(forecasts.order_id),
        "seller_delivery_observed_validation_evaluation": set(predictions.loc[predictions.inner_role.eq("validation"), "order_id"]),
    }
    verify_sources(provenance)
    return {"cohort": cohort, "exposure_sets": exposure_sets}, provenance


def assign_outer(cohort):
    validate_cohort(cohort)
    ordered = cohort.sort_values([TIME, "order_id"]).reset_index(drop=True).copy()
    target = (len(ordered)+4)//5
    boundary = ordered.iloc[len(ordered)-target][TIME]
    is_test = ordered[TIME].ge(boundary)
    if is_test.all():
        raise ValueError("Timestamp ties leave no development orders; do not split simultaneous purchases")
    ordered = ordered.rename(columns={"split": "old_split"})
    ordered["proposed_role"] = "development"
    ordered.loc[is_test, "proposed_role"] = "test"
    ordered["test_exposure_stratum"] = "not_test"
    ordered.loc[is_test & ordered.old_split.eq("test"), "test_exposure_stratum"] = "old_reserved_test"
    ordered.loc[is_test & ordered.old_split.ne("test"), "test_exposure_stratum"] = "reused_old_development"
    return ordered, boundary, target


def build_plan(inputs):
    ledger, boundary, target = assign_outer(inputs["cohort"])
    exposures = inputs["exposure_sets"]
    if any(not set(ids).issubset(ledger.order_id) for ids in exposures.values()):
        raise ValueError("Exposure IDs outside frozen cohort")
    counts = ledger.groupby("proposed_role", sort=True).agg(orders=("order_id", "size"), first_purchase=(TIME, "min"), last_purchase=(TIME, "max")).reset_index()
    counts["cohort_fraction"] = counts.orders/len(ledger)
    migrations = ledger.groupby(["old_split", "proposed_role"], sort=True).size().rename("orders").reset_index()
    rows = []
    for role, group in ledger.groupby("proposed_role", sort=True):
        for name, ids in exposures.items():
            rows.append({"proposed_role": role, "recorded_exposure": name, "orders": int(group.order_id.isin(ids).sum())})
    exposure_counts = pd.DataFrame(rows)
    test = ledger.loc[ledger.proposed_role.eq("test")]
    moved = test.loc[test.old_split.ne("test")]
    summary = {
        "version": VERSION, "lifecycle": "proposal_not_activated", "cohort_orders": len(ledger),
        "proposed_development_orders": len(ledger)-len(test), "proposed_test_orders": len(test),
        "requested_test_orders_after_rounding": target, "extra_test_orders_due_to_timestamp_ties": len(test)-target,
        "boundary_purchase_timestamp_inclusive_for_test": str(boundary),
        "proposed_test_fraction": len(test)/len(ledger), "old_split_counts": ledger.old_split.value_counts().to_dict(),
        "old_reserved_test_retained_in_proposed_test": int(test.old_split.eq("test").sum()),
        "old_reserved_test_moved_to_development": int((ledger.old_split.eq("test") & ledger.proposed_role.eq("development")).sum()),
        "old_development_moved_to_proposed_test": len(moved),
        "old_development_moved_by_split": moved.old_split.value_counts().to_dict(),
        "proposed_test_recorded_exposures": {name: int(test.order_id.isin(ids).sum()) for name, ids in exposures.items()},
        "recorded_exposure_counts_overlap": True, "full_historical_exploration_preceded_reservation": True,
        "test_is_never_seen_independent_data": False, "exposure_audit_exhaustive": False,
        "split_activated": False, "training_performed": False, "model_weights_loaded": False,
        "test_feature_values_parsed": False, "test_scored": False, "accuracy_computed": False,
        "human_labels_created": False, "synthetic_observations_used": False, "pilot_changed": False,
        "near_real_time_validated": False,
    }
    # No protected-pilot marker or review pseudonym is written beside an order ID.
    return {"analyst_only/partition_plan.csv": ledger, "split_counts.csv": counts,
            "migration_counts.csv": migrations, "exposure_counts.csv": exposure_counts}, summary


def destination_guard(output, provenance):
    output = Path(output).resolve()
    protected = [ROOT/name for name in ["scripts", "tests", "templates", "models", "data/raw", "data/manual_review"]]
    protected += [Path(record["directory"]) for record in provenance["snapshots"].values()]
    if any(output == path or output.is_relative_to(path) or path.is_relative_to(output) for path in protected):
        raise ValueError("Output overlaps a protected source or code directory")
    if any((parent/"manifest.json").exists() for parent in output.parents):
        raise ValueError("Do not place a new plan inside an existing snapshot")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite temporal plan: {output}")
    return output


def prepare(output=DEFAULT_OUTPUT):
    inputs, provenance = load_inputs()
    output = destination_guard(output, provenance)
    frames, summary = build_plan(inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        schemas = {}
        for name, frame in frames.items():
            (staging/name).parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text(README.read_text()+"\n## Recorded Run\n\n"
                                       +f"Development: {summary['proposed_development_orders']}; test: {summary['proposed_test_orders']}; "
                                       +f"boundary: {summary['boundary_purchase_timestamp_inclusive_for_test']}.\n"
                                       +f"Previously used development orders moved into proposed test: {summary['old_development_moved_to_proposed_test']}.\n")
        manifest = {"version": VERSION, "status": "complete", "lifecycle": "proposal_not_activated",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "summary": summary, "schemas": schemas,
                    "provenance": provenance, "output_hashes": {str(path.relative_to(staging)): review.digest(path)
                                                                for path in sorted(staging.rglob("*")) if path.is_file()}}
        review.write_json(staging/"manifest.json", manifest)
        verify_sources(provenance)
        destination_guard(output, provenance)
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or manifest.get("lifecycle") != "proposal_not_activated" or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != TABLES | {"README.md", "summary.json", "protocol.json"}):
        raise ValueError("Invalid temporal planning manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    if review.read_json(directory/"summary.json") != manifest["summary"] or review.read_json(directory/"protocol.json") != protocol():
        raise ValueError("Temporal plan summary/protocol differs")
    verify_sources(manifest["provenance"])
    return manifest


def main(argv=None):
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--summary", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = prepare(args.output) if args.prepare else load_snapshot(args.output)["summary"]
    except (ValueError, FileExistsError, FileNotFoundError) as exc:
        parser.exit(2, f"{exc}\n")
    print(json.dumps(summary, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
