"""Apply explicit data-use rules to the frozen clean snapshot, without labeling anomalies."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import clean_olist_data as clean
import olist_import_audit as audit


DEFAULT_OUTPUT = audit.PROCESSED_DATA_DIR / "olist_usage_policy_v1"
DURATIONS = audit.DURATIONS + ["total_cycle_time_days"]
GROUPS = {
    "status_delivery_conflict": "Verify source status/delivery timestamp; do not overwrite either.",
    "recorded_timeline_reversal": "Retain temporal evidence; exclude from completed-duration comparisons.",
    "delivered_missing_milestones": "Verify missing milestones in a delivered order; do not reconstruct events.",
    "completed_timing_available": "Available for completed-duration analysis, NOT a normal-case label.",
    "canceled_or_unavailable_incomplete": "Missing completion may relate to recorded status; no automatic anomaly or normal label.",
    "nonfinal_status_cutoff_unknown": "Keep observed partial history; do not compute ongoing age without a verified extraction cutoff.",
}
ISSUE_ACTIONS = {
    "missing_timestamp": "retain_missing_do_not_compute_affected_duration",
    "negative_transition_duration": "retain_evidence_exclude_completed_timing_review_source",
    "recorded_milestones_out_of_order": "retain_evidence_exclude_completed_timing_review_source",
    "delivered_without_delivery_timestamp": "verify_source_status_and_timestamps",
    "non_delivered_status_with_delivery_timestamp": "verify_source_status_and_timestamps",
    "missing_product_attribute": "leave_attribute_missing_do_not_infer",
    "zero_physical_measurement": "mask_measurement_in_derived_feature_only",
    "zero_payment_field_needs_context": "retain_zero_no_automatic_anomaly_or_correction",
    "undefined_payment_type": "retain_unknown_type_do_not_infer_payment_method",
    "unmatched_optional_reference": "retain_source_key_do_not_invent_mapping",
    "missing_child_records": "retain_order_do_not_replace_missing_table_records_with_zero_totals",
    "negative_numeric_value": "retain_source_review_field_semantics_before_feature_use",
    "invalid_review_score": "retain_source_exclude_score_from_satisfaction_statistics",
    "review_answer_before_creation": "retain_source_review_dates_before_review_timing_analysis",
    "invalid_earth_coordinate": "retain_source_exclude_coordinate_from_spatial_computations",
}


def verify_snapshot(snapshot):
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("policy_version") != "olist_standardization_v1":
        raise ValueError("Expected a completed olist_standardization_v1 snapshot")
    required = {"order_quality.csv", "audit/quality_issues.csv", *audit.FILES.values()}
    if not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Snapshot manifest omits required input hashes")
    for filename, digest in manifest["output_hashes"].items():
        path = (snapshot / filename).resolve()
        if not path.is_relative_to(snapshot.resolve()) or not path.is_file():
            raise ValueError(f"Invalid snapshot file: {filename}")
        if audit.file_hash(path) != digest:
            raise ValueError(f"Snapshot hash mismatch: {filename}")
    return manifest


def read_snapshot_table(snapshot, filename, manifest):
    schema = manifest["schemas"][filename]
    dates = [c for c, dtype in schema.items() if dtype.startswith("datetime64")]
    frame = pd.read_csv(Path(snapshot) / filename, dtype={c: t for c, t in schema.items() if c not in dates},
                        keep_default_na=False, na_values=[""])
    if list(frame.columns) != list(schema):
        raise ValueError(f"Snapshot schema mismatch: {filename}")
    for column in dates:
        frame[column] = pd.to_datetime(frame[column], format="%Y-%m-%d %H:%M:%S", errors="raise")
    return frame


def validate_cases(cases, orders):
    if cases.order_id.isna().any() or not cases.order_id.is_unique or not orders.order_id.is_unique:
        raise ValueError("Invalid or duplicate order_id")
    if set(cases.order_id) != set(orders.order_id):
        raise ValueError("Order IDs differ between clean orders and quality view")
    observed = cases.set_index("order_id").sort_index()
    source = orders.set_index("order_id").sort_index()
    pd.testing.assert_frame_equal(observed[source.columns], source)
    dates = cases[audit.TIMES]
    missing = dates.isna().sum(axis=1)
    reversed_times = pd.Series(False, index=cases.index)
    for i, start in enumerate(audit.TIMES):
        for end in audit.TIMES[i + 1:]:
            reversed_times |= dates[end].lt(dates[start])
    for column, (start, end) in zip(DURATIONS, [(0, 1), (1, 2), (2, 3), (0, 3)]):
        expected = (dates.iloc[:, end] - dates.iloc[:, start]).dt.total_seconds() / 86400
        if not np.allclose(cases[column], expected, equal_nan=True, rtol=1e-12, atol=1e-12):
            raise ValueError(f"Duration inconsistent with observations: {column}")
    delivered = cases.order_status.eq("delivered")
    expected_flags = {
        "missing_milestone_count": missing,
        "has_reversed_recorded_milestones": reversed_times,
        "status_delivery_conflict": delivered.ne(dates.iloc[:, -1].notna()),
        "completed_timing_eligible": delivered & missing.eq(0) & ~reversed_times,
        "category_assignment_eligible": cases.item_count.gt(0) & cases.known_category_count.eq(1)
                                        & cases.missing_category_item_count.eq(0),
    }
    for column, expected in expected_flags.items():
        if not cases[column].eq(expected).all():
            raise ValueError(f"Quality flag inconsistent with observations: {column}")
    if not cases.order_status.isin(clean.STATUSES).all():
        raise ValueError("Unknown order status")


def build_case_policy(cases):
    result = cases.sort_values("order_id").reset_index(drop=True).copy()
    missing = result.missing_milestone_count.gt(0)
    delivered = result.order_status.eq("delivered")
    closed = result.order_status.isin(["canceled", "unavailable"])
    masks = [result.status_delivery_conflict, result.has_reversed_recorded_milestones,
             delivered & missing, result.completed_timing_eligible, closed & missing, ~delivered & ~closed & missing]
    result["usage_group"] = pd.Series(pd.NA, index=result.index, dtype="string")
    for name, mask in zip(GROUPS, masks):
        result.loc[result.usage_group.isna() & mask, "usage_group"] = name
    if result.usage_group.isna().any():
        raise ValueError("Unclassified case; extend the explicit policy before exporting")
    result["source_verification_priority"] = result.usage_group.isin(list(GROUPS)[:3])
    result["process_evidence_retained"] = True
    result["ongoing_age_computable"] = False
    exclusions = pd.DataFrame({"not_delivered_status": ~delivered, "missing_milestones": missing,
                               "reversed_recorded_timeline": result.has_reversed_recorded_milestones})
    result["timing_exclusion_reasons"] = exclusions.apply(
        lambda row: json.dumps([name for name, excluded in row.items() if excluded]), axis=1,
    )
    for column in DURATIONS:
        value = result[column]
        state = pd.Series("positive_observed", index=result.index, dtype="string")
        state.loc[value.isna()] = "missing_endpoint"
        state.loc[value.lt(0)] = "negative_observed"
        state.loc[value.eq(0)] = "zero_observed"
        result[f"{column}_state"] = state
        # These are endpoint observations, not fit/scoring eligibility for a whole case.
        result[f"{column}_nonnegative_observed"] = value.notna() & value.ge(0)
    return result


def build_product_policy(products):
    result = products[["product_id", "product_category_name", *clean.PHYSICAL]].copy()
    for column in clean.PHYSICAL:
        usable = result[column].notna() & result[column].gt(0)
        result[f"{column}_usable"] = usable
        result[f"{column}_feature"] = result[column].where(usable)
    return result


def build_frames(cases, products, issues):
    policy = build_case_policy(cases)
    unknown = set(issues.issue_code) - set(ISSUE_ACTIONS)
    if unknown:
        raise ValueError(f"Missing usage policy for issue codes: {sorted(unknown)}")
    issue_actions = issues.copy()
    issue_actions["usage_action"] = issue_actions.issue_code.map(ISSUE_ACTIONS)
    timing_columns = ["order_id", "order_status", *audit.TIMES, "category_scope", "single_category_name",
                      "single_category_name_english", "seller_count", "single_category_timing_eligible", *DURATIONS]
    timing = policy.loc[policy.completed_timing_eligible, timing_columns].copy()
    summary = policy.groupby("usage_group").size().reindex(GROUPS, fill_value=0).rename("case_count").reset_index()
    summary["action"] = summary.usage_group.map(GROUPS)
    status = policy.assign(missing=policy.missing_milestone_count.gt(0)).groupby("order_status").agg(
        all_orders=("order_id", "size"), missing_milestone_orders=("missing", "sum"),
        reversed_timeline_orders=("has_reversed_recorded_milestones", "sum"),
        status_delivery_conflicts=("status_delivery_conflict", "sum"),
        completed_timing_orders=("completed_timing_eligible", "sum"),
        source_verification_priority_orders=("source_verification_priority", "sum"),
    ).reset_index()
    example_columns = ["order_id", "usage_group", "order_status", *audit.TIMES, *DURATIONS,
                       "missing_milestone_count", "has_reversed_recorded_milestones", "timing_exclusion_reasons"]
    examples = policy.groupby("usage_group", sort=True).head(2)[example_columns].sort_values(["usage_group", "order_id"])
    return {"case_usage_policy.csv": policy, "completed_timing_cases.csv": timing,
            "product_measurement_policy.csv": build_product_policy(products), "quality_issue_actions.csv": issue_actions,
            "usage_group_summary.csv": summary, "status_usage_summary.csv": status,
            "real_case_examples.csv": examples.reset_index(drop=True)}


def prepare_policy(snapshot=clean.DEFAULT_OUTPUT):
    snapshot = Path(snapshot)
    before = audit.file_hash(snapshot / "manifest.json")
    manifest = verify_snapshot(snapshot)
    cases = read_snapshot_table(snapshot, "order_quality.csv", manifest)
    orders = read_snapshot_table(snapshot, audit.FILES["orders"], manifest)
    products = read_snapshot_table(snapshot, audit.FILES["products"], manifest)
    issues = read_snapshot_table(snapshot, "audit/quality_issues.csv", manifest)
    validate_cases(cases, orders)
    frames = build_frames(cases, products, issues)
    if audit.file_hash(snapshot / "manifest.json") != before:
        raise ValueError("Source manifest changed during policy preparation")
    return frames, before


def render_readme(frames):
    policy = frames["case_usage_policy.csv"]
    products = frames["product_measurement_policy.csv"]
    invalid_weight = int((products.product_weight_g.notna() & ~products.product_weight_g_usable).sum())
    return "\n\n".join([
        "# Olist data usage policy v1",
        "## Purpose\nA versioned data-use layer over olist_clean_v1. No raw/clean observations are edited and no normal, anomaly, suspicious, probability or human-review labels are generated. These are eligibility and evidence groups, not verified business causes. Category ranking, model training and changes to existing detector decisions remain deferred.",
        "## Mutually exclusive case groups\n" + audit.markdown_table(frames["usage_group_summary.csv"]),
        "Group precedence follows the table order: status/delivery conflict, recorded reversal, delivered with missing milestones, completed timing, incomplete canceled/unavailable, then incomplete nonfinal status. All overlapping original evidence flags remain in case_usage_policy.csv. Group counts sum to the full population; individual evidence counts can overlap.",
        "## Status context\n" + audit.markdown_table(frames["status_usage_summary.csv"]),
        "Missing completion in canceled/unavailable/nonfinal orders may be explained by their recorded state, but this is NOT proof of normality. A real extraction cutoff is unavailable, so ongoing_age_computable is false for all cases. Never use today's date or the maximum observed timestamp as a substitute cutoff. Negative recorded transitions and status/timestamp conflicts require source verification, not automatic timestamp reordering or changes to status.",
        f"## Timing views\ncase_usage_policy.csv retains all {len(policy):,} orders and all four observed durations, including negative, missing and zero values. completed_timing_cases.csv contains {len(frames['completed_timing_cases.csv']):,} delivered orders with all four actual timestamps in nondecreasing order. It is a descriptive complete-case view, NOT an all-purpose training dataset or a collection of normal orders. Long durations and zero-duration transitions remain present. No upper/lower outlier threshold, percentile filter or imputation is applied.",
        "Each duration has an observation-state field (positive, zero, negative or missing endpoint) and a nonnegative_observed flag. A valid observed pair can exist in an otherwise incomplete/conflicting case; the pair flag does NOT override whole-case exclusion from the completed-timing view. timing_exclusion_reasons preserves every applicable exclusion reason. Missing categories do not invalidate observed durations: use single_category_timing_eligible only when the particular analysis requires one fully known category. Multi-category cases remain in the all-order view.",
        f"## Product measurements\nproduct_measurement_policy.csv retains every product and original measurement. Separate *_feature columns leave missing/nonpositive physical measurements unavailable; they do not estimate replacements. Observed invalid weights masked in the derived weight feature: {invalid_weight}. Other valid measurements on the same product remain usable. No whole order is deleted because a product weight is unusable. Unknown categories and missing English translations are not inferred.",
        "## Other issues\nquality_issue_actions.csv preserves the clean snapshot's issue evidence and adds a rule-specific usage action. Zero payment fields and undefined payment types need context and are not automatically corrected or labeled anomalous. Missing payment/review/item records must not become invented records or fabricated zero totals. Unmatched ZIP mappings must not become guessed coordinates; a missing English translation does not erase a known source category. Geolocation remains a one-to-many reference, not a case table. The file specifies usage restrictions; it does not implement payment, satisfaction or geographic model features.",
        f"## Source verification\n{int(policy.source_verification_priority.sum()):,} cases are prioritized for source verification due to status/delivery conflicts, reversals or delivered orders missing milestones. This is not a manual validation result. real_case_examples.csv takes the first two actual order IDs in each group after deterministic sorting. These examples are illustrative, not random, representative, independently reviewed or a model evaluation set. Existing manual-review batches and human labels are untouched.",
        "## Fair comparison boundary\nUse the same field and case eligibility rules for every category. Always report all-case counts, missing/reversal/status counts and exclusions alongside complete-case duration summaries. Compare compatible time periods and consider seller, region and status composition before claiming one category differs. Complete-case selection can be biased. Preserve incomplete and contradictory cases for separate process/data-quality analysis; do not make them disappear from anomaly evaluation. Fit statistical thresholds, scalers and model parameters only after establishing a training/validation/test split. Short positive times cannot yet be called abnormal using a universal day cutoff.",
        "## Reproduce\nRun `.venv/bin/python scripts/01_data_preparation/apply_olist_usage_policy.py`. Existing destinations are never overwritten. Use --snapshot for an explicitly selected completed clean snapshot and --output for a new policy directory. The source manifest and every snapshot output checksum are checked before consumption and before publication. manifest.json records parent manifest hash, source snapshot output hashes, policy code hashes and output hashes. Old scripts/models still use their previous inputs; no downstream migration occurs automatically.",
    ]) + "\n"


def write_policy(frames, source_manifest_hash, snapshot=clean.DEFAULT_OUTPUT, output=DEFAULT_OUTPUT):
    snapshot, output = Path(snapshot), Path(output)
    if output.exists():
        raise FileExistsError(f"Policy output already exists: {output}")
    if audit.file_hash(snapshot / "manifest.json") != source_manifest_hash:
        raise ValueError("Source manifest changed before publication")
    parent = verify_snapshot(snapshot)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "README.md").write_text(render_readme(frames), encoding="utf-8")
        manifest = {
            "status": "complete", "policy_version": "olist_usage_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(), "pandas_version": pd.__version__,
            "source_snapshot": str(snapshot.resolve()), "source_manifest_sha256": source_manifest_hash,
            "source_output_hashes": parent["output_hashes"],
            "code_hashes": {p.name: audit.file_hash(p) for p in [Path(__file__), Path(clean.__file__), Path(audit.__file__)]},
            "output_hashes": {p.name: audit.file_hash(p) for p in sorted(staging.iterdir())},
            "all_order_count": len(frames["case_usage_policy.csv"]),
            "completed_timing_count": len(frames["completed_timing_cases.csv"]),
            "category_selection_performed": False, "model_training_performed": False,
        }
        verify_snapshot(snapshot)
        if audit.file_hash(snapshot / "manifest.json") != source_manifest_hash:
            raise ValueError("Source manifest changed during publication")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise FileExistsError(f"Policy output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=clean.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Policy output already exists: {args.output}")
    print("Checking frozen snapshot and applying explicit usage rules...", flush=True)
    frames, parent_hash = prepare_policy(args.snapshot)
    write_policy(frames, parent_hash, args.snapshot, args.output)
    print(frames["usage_group_summary.csv"].to_string(index=False))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
