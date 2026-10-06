"""Freeze the selected real Olist category and an event-time-aware chronological split."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pandas as pd

import compare_olist_categories as compare
from project_paths import EXPERIMENT_DATA_DIR, MANUAL_REVIEW_DIR


usage = compare.usage
audit = compare.audit
CATEGORY = "cama_mesa_banho"
CATEGORY_ENGLISH = "bed_bath_table"
DEFAULT_OUTPUT = EXPERIMENT_DATA_DIR / "bed_bath_table_v1"
TRAIN_END = "2018-03-01"
VALIDATION_END = "2018-06-01"
SPLITS = ["train", "validation", "test"]
FEATURES = list(audit.DURATIONS)


def read_comparison(comparison_dir, parents):
    comparison_dir = Path(comparison_dir)
    manifest = json.loads((comparison_dir / "manifest.json").read_text())
    if (manifest.get("status") != "complete"
            or manifest.get("comparison_version") != "olist_category_comparison_v1"
            or manifest.get("parents") != parents):
        raise ValueError("Comparison must refer to the same completed clean/policy snapshots")
    if not manifest.get("output_hashes"):
        raise ValueError("Comparison has no output hashes")
    for filename, digest in manifest["output_hashes"].items():
        path = (comparison_dir / filename).resolve()
        if not path.is_relative_to(comparison_dir.resolve()) or not path.is_file() or audit.file_hash(path) != digest:
            raise ValueError(f"Comparison hash mismatch: {filename}")
    if CATEGORY not in manifest["design"]["window"]["candidates"]:
        raise ValueError("Selected category is absent from the linked comparison")
    return manifest


def review_exposure(review_dir):
    """Audit previously recorded human-review IDs without copying their labels."""
    review_dir = Path(review_dir)
    if not review_dir.is_dir():
        raise FileNotFoundError(f"Missing manual review directory: {review_dir}")
    ids, sources, hashes = set(), [], {}
    for path in sorted(review_dir.rglob("*.csv")):
        relative = str(path.relative_to(review_dir))
        hashes[relative] = audit.file_hash(path)
        header = pd.read_csv(path, nrows=0)
        if not {"case_id", "reviewer_label"}.issubset(header.columns):
            continue
        data = pd.read_csv(path, usecols=["case_id", "reviewer_label"], dtype="string",
                           keep_default_na=False, na_values=[""])
        marked = data.reviewer_label.fillna("").str.strip().ne("")
        if data.loc[marked, "case_id"].isna().any():
            raise ValueError(f"Human label without case ID: {relative}")
        selected = set(data.loc[marked, "case_id"])
        ids.update(selected)
        sources.append({"file": relative, "reviewed_rows": int(marked.sum()),
                        "unique_reviewed_cases": len(selected)})
    return ids, {"csv_hashes": hashes, "sources": sources, "unique_reviewed_cases": len(ids)}


def split_design(window, train_end=TRAIN_END, validation_end=VALIDATION_END):
    boundaries = [pd.Timestamp(value) for value in
                  [window["start_inclusive"], train_end, validation_end, window["end_exclusive"]]]
    if any(pd.isna(value) or value.tzinfo is not None for value in boundaries):
        raise ValueError("Split boundaries must be nonmissing timezone-naive source-clock dates")
    if any(left >= right for left, right in zip(boundaries, boundaries[1:])):
        raise ValueError("Require start < train end < validation end < purchase end")
    return [{"split": name, "purchase_start_inclusive": str(boundaries[index]),
             "purchase_end_exclusive": str(boundaries[index + 1]),
             "event_time_cutoff_exclusive": str(boundaries[index + 1]) if name != "test" else None}
            for index, name in enumerate(SPLITS)]


def build_experiment(cases, membership, window, reviewed_ids, train_end=TRAIN_END,
                     validation_end=VALIDATION_END):
    if cases.order_id.isna().any() or not cases.order_id.is_unique:
        raise ValueError("Source case IDs must be nonmissing and unique")
    selected_ids = set(membership.loc[membership.product_category_name.eq(CATEGORY), "order_id"])
    if not selected_ids or not selected_ids.issubset(set(cases.order_id)):
        raise ValueError("Category membership is empty or has unknown order IDs")
    partitions = split_design(window, train_end, validation_end)
    candidates = cases.loc[cases.order_id.isin(selected_ids)].sort_values("order_id").reset_index(drop=True)
    single = candidates.category_assignment_eligible & candidates[compare.CAT].eq(CATEGORY)
    if not candidates.loc[single, "single_category_name_english"].eq(CATEGORY_ENGLISH).all():
        raise ValueError("Selected category translation differs from bed_bath_table")
    in_period = (candidates.order_purchase_timestamp.ge(pd.Timestamp(window["start_inclusive"]))
                 & candidates.order_purchase_timestamp.lt(pd.Timestamp(window["end_exclusive"])))
    columns = ["order_id", "order_purchase_timestamp", "category_scope", "categories_json",
               compare.CAT, "single_category_name_english", "usage_group"]
    ledger = candidates[columns].copy()
    ledger["in_purchase_period"] = in_period
    ledger["in_primary_cohort"] = single & in_period
    ledger["disposition"] = "primary_cohort"
    ledger.loc[~in_period, "disposition"] = "outside_purchase_period"
    ledger.loc[~single, "disposition"] = "category_scope:" + candidates.category_scope
    cohort = candidates.loc[ledger.in_primary_cohort].copy().reset_index(drop=True)
    cohort["split"] = pd.Series(pd.NA, index=cohort.index, dtype="string")
    cohort["event_time_cutoff_exclusive"] = pd.NaT
    for partition in partitions:
        mask = (cohort.order_purchase_timestamp.ge(pd.Timestamp(partition["purchase_start_inclusive"]))
                & cohort.order_purchase_timestamp.lt(pd.Timestamp(partition["purchase_end_exclusive"])))
        cohort.loc[mask, "split"] = partition["split"]
        if partition["event_time_cutoff_exclusive"] is not None:
            cohort.loc[mask, "event_time_cutoff_exclusive"] = pd.Timestamp(partition["event_time_cutoff_exclusive"])
    if cohort.split.isna().any() or set(cohort.split) != set(SPLITS):
        raise ValueError("Every case must have one split and every split must be nonempty")
    cohort["previously_human_reviewed"] = cohort.order_id.isin(reviewed_ids)
    cohort["recorded_event_count"] = cohort[audit.TIMES].notna().sum(axis=1)
    has_cutoff = cohort.event_time_cutoff_exclusive.notna()
    # A case stays in its purchase cohort even when its outcome crosses a boundary.
    crosses = cohort[audit.TIMES].ge(cohort.event_time_cutoff_exclusive, axis=0).any(axis=1)
    cohort["has_event_at_or_after_cutoff"] = crosses.astype("boolean").where(has_cutoff)
    cohort["timing_input_eligible"] = cohort.completed_timing_eligible & (~has_cutoff | ~crosses)
    cohort["timing_input_reason"] = "eligible_completed_timing"
    cohort.loc[has_cutoff & crosses, "timing_input_reason"] = "completion_not_before_partition_cutoff"
    invalid = ~cohort.completed_timing_eligible
    cohort.loc[invalid, "timing_input_reason"] = "timing_ineligible:" + cohort.loc[invalid, "usage_group"]
    ledger = ledger.merge(cohort[["order_id", "split"]], on="order_id", how="left", validate="one_to_one")

    events = usage.clean.build_event_log(cohort).merge(
        cohort[["order_id", "split", "event_time_cutoff_exclusive"]].rename(columns={"order_id": "case_id"}),
        on="case_id", how="left", validate="many_to_one",
    )
    events["timestamp_before_partition_cutoff"] = events.timestamp.lt(events.event_time_cutoff_exclusive).astype("boolean").where(events.event_time_cutoff_exclusive.notna())
    frames = {"category_membership_ledger.csv": ledger, "cohort_cases.csv": cohort, "event_log.csv": events}
    summary = []
    for partition in partitions:
        name = partition["split"]
        group = cohort.loc[cohort.split.eq(name)]
        inputs = group.loc[group.timing_input_eligible, ["order_id", *FEATURES]].copy()
        if inputs.empty or inputs[FEATURES].isna().any().any() or inputs[FEATURES].lt(0).any().any():
            raise ValueError(f"Invalid or empty timing inputs for {name}")
        frames[f"{name}_timing_features.csv"] = inputs.reset_index(drop=True)
        summary.append({**partition, "cohort_orders": len(group),
                        "completed_timing_orders_in_snapshot": int(group.completed_timing_eligible.sum()),
                        "timing_ineligible_orders": int((~group.completed_timing_eligible).sum()),
                        "completed_orders_crossing_cutoff": int((group.completed_timing_eligible & group.has_event_at_or_after_cutoff.fillna(False)).sum()) if name != "test" else None,
                        "timing_input_orders": len(inputs),
                        "missing_milestone_orders": int(group.missing_milestone_count.gt(0).sum()),
                        "reversed_timeline_orders": int(group.has_reversed_recorded_milestones.sum()),
                        "source_verification_priority_orders": int(group.source_verification_priority.sum()),
                        "previously_human_reviewed_orders": int(group.previously_human_reviewed.sum()),
                        "previously_reviewed_timing_input_orders": int(group.loc[group.timing_input_eligible, "previously_human_reviewed"].sum())})
    frames["split_summary.csv"] = pd.DataFrame(summary)
    frames["scope_summary.csv"] = ledger.groupby("disposition").size().rename("orders").reset_index()
    frames["monthly_counts.csv"] = cohort.groupby(["split", "purchase_month"]).agg(
        cohort_orders=("order_id", "size"), timing_input_orders=("timing_input_eligible", "sum"),
    ).reset_index().sort_values("purchase_month").reset_index(drop=True)
    frames["usage_by_split.csv"] = cohort.groupby(["split", "usage_group"]).size().rename("orders").reset_index()
    design = {"category_original": CATEGORY, "category_english": CATEGORY_ENGLISH,
              "selection_authority": "User explicitly selected bed_bath_table as the primary industry.",
              "category_selection_was_exploratory": True,
              "source_orders": len(cases), "orders_with_category_nonexclusive": len(candidates),
              "cohort_orders": len(cohort), "recorded_events": len(events),
              "partitions": partitions,
              "boundary_rationale": "Fixed calendar periods: 12 months train, 3 validation, 3 test under defaults. Not optimized against anomaly scores or labels.",
              "split_unit": "order_id assigned only by purchase timestamp",
              "scoring_scope": "Retrospective completed-order timing; not an early-warning deployment benchmark.",
              "source_extraction_cutoff": None, "record_ingestion_times_available": False,
              "timezone_policy": "Source wall-clock timestamps preserved; no timezone invented.",
              "fit_policy": "Fit any preprocessing, thresholds and models only on train_timing_features.csv. No refit using validation/test in v1.",
              "validation_policy": "Choose configurations using validation only. No test feedback for tuning.",
              "test_policy": "Observed completed outcomes from the snapshot; unresolved orders retained. No assumed follow-up cutoff.",
              "label_policy": "No generated human labels. Prior review exposure is audit-only and not a predictor."}
    return frames, design


def feature_contract():
    return {
        "identifier": "order_id", "model_feature_columns": FEATURES, "units": "days (seconds / 86400)",
        "formulas": {name: f"({end} - {start}).total_seconds() / 86400"
                     for name, start, end in zip(FEATURES, audit.TIMES, audit.TIMES[1:])},
        "missing_policy": "Do not impute missing milestones or durations. Retain excluded cases in cohort/event views.",
        "negative_policy": "Keep in cohort/event evidence, exclude from completed-timing input matrices.",
        "zero_and_tail_policy": "Keep zero durations and long tails; no outlier trimming. Short nonnegative durations alone are warnings, not automatic anomalies.",
        "forbidden_predictors": ["order_id", "order_status", "split", "previously_human_reviewed",
                                 "reviewer_label", "baseline_label", "auto_label", "usage_group",
                                 "source_verification_priority", "total_cycle_time_days"],
        "total_duration": "Preserved for description; excluded from this matrix because it equals the sum of its three components.",
        "other_columns": "Only the allowlisted three durations are features for this first timing experiment. Other metadata is for auditing/context, not implicit feature selection.",
        "event_log": "Observed events only. A full audit log, NOT a historical process-training log. Apply timestamp_before_partition_cutoff when appropriate; test has no verified cutoff.",
        "probability_policy": "Anomaly scores are not calibrated probabilities.",
        "training_performed": False,
    }


def prepare_experiment(clean_dir=usage.clean.DEFAULT_OUTPUT, policy_dir=usage.DEFAULT_OUTPUT,
                       comparison_dir=compare.DEFAULT_OUTPUT, review_dir=MANUAL_REVIEW_DIR,
                       train_end=TRAIN_END, validation_end=VALIDATION_END):
    cases, _, membership, parents = compare.load_inputs(clean_dir, policy_dir)
    comparison = read_comparison(comparison_dir, parents)
    reviewed, exposure = review_exposure(review_dir)
    if not reviewed.issubset(set(cases.order_id)):
        raise ValueError("Recorded human-review IDs do not all exist in the source orders")
    frames, design = build_experiment(cases, membership, comparison["design"]["window"], reviewed,
                                      train_end, validation_end)
    parents = {**parents, "comparison_manifest_sha256": audit.file_hash(Path(comparison_dir) / "manifest.json"),
               "comparison_output_hashes": comparison["output_hashes"], "review_exposure": exposure}
    return frames, design, parents


def render_readme(frames, design):
    shown = frames["split_summary.csv"][["split", "cohort_orders", "completed_timing_orders_in_snapshot",
                                         "timing_ineligible_orders", "completed_orders_crossing_cutoff", "timing_input_orders"]]
    return "\n\n".join([
        "# Bed bath table experiment v1",
        "## Scope\nUser-selected bed_bath_table (cama_mesa_banho). One case is one real imported order, not an item, payment or review. Only fully known single-category orders enter the primary cohort. The purchase period comes from the frozen category comparison. Multi-category and partially unknown orders containing this category, and orders outside the period, remain in category_membership_ledger.csv. No source data or labels are changed.",
        audit.markdown_table(frames["scope_summary.csv"]),
        "## Chronological split\n" + audit.markdown_table(shown),
        "Partitions use purchase timestamps with inclusive starts and exclusive ends, without random shuffling. The default calendar design is March 2017-February 2018 for train, March-May 2018 for validation, June-August 2018 for test. Boundaries are operational design choices, not learned optimal ratios. Exact chosen boundaries are in manifest.json and split_summary.csv.",
        "A train case delivered at or after the train cutoff stays in train but is excluded from the training timing matrix. The same rule applies to validation at the start of test, so late validation outcomes cannot guide historical model selection. No case is reassigned based on its outcome. Strict timestamp < cutoff is required. This necessarily underrepresents long unfinished cases near each boundary; exclusions remain visible rather than being labeled normal. Test uses completed outcomes observed in the snapshot, without inventing an extraction date.",
        "## Files and use\ncohort_cases.csv retains all primary orders, original observed timestamps, durations, source-use groups, split, review exposure and timing-input reasons. Missing/reversed/canceled/nonfinal cases remain available for separate structural or status-context analysis. usage_by_split.csv shows those denominators. monthly_counts.csv supports coverage checks, not model selection by test outcomes.",
        "event_log.csv contains only actual nonmissing milestones for the cohort, including out-of-order and post-boundary evidence. timestamp_before_partition_cutoff is a nullable flag: test is blank because no authoritative follow-up cutoff exists. This is an audit event log, not a ready-to-fit historical process log. Equal-time ordering is a display convention, not evidence of causality. Estimated delivery dates are not actual events.",
        "train_timing_features.csv, validation_timing_features.csv and test_timing_features.csv contain order_id plus three raw transition durations. order_id is join metadata, never an input feature. Use the exact feature_contract.json allowlist. No medians, quantiles, imputation, scaling, anomaly thresholds or detector fitting have been computed for this experiment. Long tails and zeros are retained. The training subset is usable for timing, NOT guaranteed normal.",
        "## Leakage and interpretation limits\nAll status/category/customer/seller metadata comes from the final imported snapshot. Record-ingestion times and historical status/category revisions are unavailable. Event-time filtering reduces a specific future-outcome leak but cannot prove what was actually known in a live system at the cutoff. Snapshot delivered status is only a retrospective eligibility check, not a historically observed predictor. The experiment is completed-order retrospective analysis, not purchase-time prediction or a proven online deployment.",
        "The category was selected after exploratory comparisons of the full history, including volume and problem indicators. Earlier global detectors also used the full imported data. Therefore this new chronological split is a holdout for FUTURE train-only fitting, not a claim that the test period has never been inspected. Previously recorded human-review IDs are flagged but their labels are not copied, used to split, or used as features. Existing reviewed cases are not a representative accuracy benchmark. Plan an independent blind review before reporting detector accuracy; isolate previously reviewed cases in that evaluation.",
        "No extraction cutoff is known, so ongoing age and unresolved outcomes are not inferred from today, the purchase end, or the largest event timestamp. Train/validation event cutoffs are experiment boundaries, NOT asserted source extraction cutoffs. Repeated customers or sellers may span periods; this is an order-level temporal split, not an unseen-customer/seller generalization test. It cannot establish transfer to another marketplace.",
        "## Next experiment\nFit preprocessing and timing detectors on train only. Compare declared candidates on validation without reading test outcomes for tuning, freeze the selected configuration, then run test. Keep a separate process/status-evidence branch for cases that cannot enter timing matrices. Do not turn a short nonnegative duration into an automatic anomaly, interpret an uncalibrated score as probability, or use old rules as human ground truth. Per-marketplace training and a user import/train/save workflow remain subsequent implementation work.",
        "## Reproduce\nRun `.venv/bin/python scripts/01_data_preparation/prepare_bed_bath_table_experiment.py`. The default destination is data/experiments/bed_bath_table_v1 and existing destinations are refused. --output selects a new version. --train-end and --validation-end explicitly change experiment boundaries; --clean, --policy, --comparison and --reviews select inputs. manifest.json records linked source hashes, review exposure, exact dates, schemas, code hashes and output checksums. This README is a technical run record, not a thesis chapter.",
    ]) + "\n"


def write_experiment(frames, design, parents, clean_dir=usage.clean.DEFAULT_OUTPUT,
                     policy_dir=usage.DEFAULT_OUTPUT, comparison_dir=compare.DEFAULT_OUTPUT,
                     review_dir=MANUAL_REVIEW_DIR, output=DEFAULT_OUTPUT):
    clean_dir, policy_dir, comparison_dir, review_dir, output = map(
        Path, [clean_dir, policy_dir, comparison_dir, review_dir, output])
    if output.exists():
        raise FileExistsError(f"Experiment already exists: {output}")

    def check_parents():
        clean_manifest, policy_manifest = compare.verify_inputs(clean_dir, policy_dir)
        current = {"clean_manifest_sha256": audit.file_hash(clean_dir / "manifest.json"),
                   "policy_manifest_sha256": audit.file_hash(policy_dir / "manifest.json"),
                   "clean_output_hashes": clean_manifest["output_hashes"],
                   "policy_output_hashes": policy_manifest["output_hashes"]}
        comparison = read_comparison(comparison_dir, current)
        _, exposure = review_exposure(review_dir)
        current.update({"comparison_manifest_sha256": audit.file_hash(comparison_dir / "manifest.json"),
                        "comparison_output_hashes": comparison["output_hashes"], "review_exposure": exposure})
        if current != parents:
            raise ValueError("Source changed since experiment preparation")

    check_parents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "README.md").write_text(render_readme(frames, design), encoding="utf-8")
        (staging / "feature_contract.json").write_text(json.dumps(feature_contract(), indent=2) + "\n", encoding="utf-8")
        manifest = {"status": "complete", "experiment_version": "bed_bath_table_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "pandas_version": pd.__version__,
                    "design": design, "parents": parents,
                    "source_directories": {"clean": str(clean_dir.resolve()), "policy": str(policy_dir.resolve()),
                                           "comparison": str(comparison_dir.resolve()), "reviews": str(review_dir.resolve())},
                    "schemas": {name: {column: str(dtype) for column, dtype in frame.dtypes.items()}
                                for name, frame in frames.items()},
                    "row_counts": {name: len(frame) for name, frame in frames.items()},
                    "code_hashes": {p.name: audit.file_hash(p) for p in
                                    [Path(__file__), Path(compare.__file__), Path(usage.__file__),
                                     Path(usage.clean.__file__), Path(audit.__file__)]},
                    "output_hashes": {p.name: audit.file_hash(p) for p in sorted(staging.iterdir())},
                    "model_training_performed": False, "human_labels_created": False}
        check_parents()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise FileExistsError(f"Experiment appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", type=Path, default=usage.clean.DEFAULT_OUTPUT)
    parser.add_argument("--policy", type=Path, default=usage.DEFAULT_OUTPUT)
    parser.add_argument("--comparison", type=Path, default=compare.DEFAULT_OUTPUT)
    parser.add_argument("--reviews", type=Path, default=MANUAL_REVIEW_DIR)
    parser.add_argument("--train-end", default=TRAIN_END)
    parser.add_argument("--validation-end", default=VALIDATION_END)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Experiment already exists: {args.output}")
    print("Verifying snapshots and preparing the selected real category...", flush=True)
    frames, design, parents = prepare_experiment(args.clean, args.policy, args.comparison,
                                                args.reviews, args.train_end, args.validation_end)
    write_experiment(frames, design, parents, args.clean, args.policy, args.comparison, args.reviews, args.output)
    print(frames["split_summary.csv"].to_string(index=False))
    print(frames["scope_summary.csv"].to_string(index=False))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
