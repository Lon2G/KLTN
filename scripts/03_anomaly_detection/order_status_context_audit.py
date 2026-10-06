"""Audit recorded order status alongside existing predictions, without relabeling cases."""

from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, RAW_DATA_DIR, REPORT_DIR
from auto_validation_evaluation import (
    AUTO_FILE, BASELINE_FILE, DURATION_FEATURES, build_comparison,
    file_sha256, markdown_table, validate_table,
)


ORDERS_FILE = RAW_DATA_DIR / "olist_orders_dataset.csv"
CONTEXT_FILE = ANOMALY_DATA_DIR / "order_status_context_cases.csv"
REPORT_FILE = REPORT_DIR / "order_status_context_audit.md"
ACTIVITY_COLUMNS = {
    "order_purchase_timestamp": "Purchased",
    "order_approved_at": "Approved",
    "order_delivered_carrier_date": "Handed to Carrier",
    "order_delivered_customer_date": "Delivered",
}
TIMESTAMP_COLUMNS = [*ACTIVITY_COLUMNS, "order_estimated_delivery_date"]
RAW_COLUMNS = ["case_id", "order_status", *TIMESTAMP_COLUMNS]
STATUS_CONTEXT = {
    "delivered": "delivery_recorded_in_status",
    "canceled": "cancellation_recorded_in_status",
    "unavailable": "unavailability_recorded_in_status",
    "created": "nonfinal_status_recorded",
    "approved": "nonfinal_status_recorded",
    "invoiced": "nonfinal_status_recorded",
    "processing": "nonfinal_status_recorded",
    "shipped": "nonfinal_status_recorded",
}
REVIEW_COLUMNS = ["reviewer_label", "reviewer_confidence", "reviewer_reason", "reviewer_notes"]
BLIND_COLUMNS = [
    "review_id", *RAW_COLUMNS, "event_sequence", "event_timestamps",
    "recorded_event_count", "raw_missing_event_count", "missing_activities",
    *DURATION_FEATURES, "delivery_vs_estimate_calendar_days", *REVIEW_COLUMNS,
]


def build_order_context(comparison: pd.DataFrame, orders: pd.DataFrame) -> pd.DataFrame:
    raw = orders.rename(columns={"order_id": "case_id"})
    validate_table(raw, RAW_COLUMNS, "Raw orders")
    validate_table(comparison, ["case_id", "missing_event_count", *DURATION_FEATURES], "Comparison")
    if set(raw.case_id) != set(comparison.case_id):
        raise ValueError("Raw orders and comparison case_id sets differ")
    if not raw.order_status.isin(STATUS_CONTEXT).all():
        raise ValueError("Unknown or missing order_status; define its context explicitly")
    context = comparison.merge(raw[RAW_COLUMNS], on="case_id", validate="one_to_one")
    dates = context[TIMESTAMP_COLUMNS].apply(pd.to_datetime, errors="raise")
    actual_dates = dates[list(ACTIVITY_COLUMNS)]
    missing = actual_dates.isna()
    context["raw_missing_event_count"] = missing.sum(axis=1)
    if not context.raw_missing_event_count.eq(context.missing_event_count).all():
        raise ValueError("Raw missing timestamps differ from the saved missing-event counts")
    context["recorded_event_count"] = actual_dates.notna().sum(axis=1)
    pairs = [(0, 1), (1, 2), (2, 3), (0, 3)]
    for feature, (start, end) in zip(DURATION_FEATURES, pairs):
        derived = (actual_dates.iloc[:, end] - actual_dates.iloc[:, start]).dt.total_seconds() / 86400
        if not np.allclose(derived, context[feature], rtol=1e-10, atol=1e-10, equal_nan=True):
            raise ValueError(f"Duration does not match raw timestamps: {feature}")
    context["missing_activities"] = missing.apply(
        lambda row: "; ".join(ACTIVITY_COLUMNS[column] for column in ACTIVITY_COLUMNS if row[column]),
        axis=1,
    )
    context["recorded_event_profile"] = np.where(
        context.raw_missing_event_count.gt(0), "missing_milestones", "all_milestones_recorded",
    )
    context["status_context"] = context.order_status.map(STATUS_CONTEXT)
    context["delivered_status_missing_milestone"] = (
        context.order_status.eq("delivered") & context.raw_missing_event_count.gt(0)
    ).astype(int)
    context["delivered_status_without_delivery_timestamp"] = (
        context.order_status.eq("delivered") & dates.order_delivered_customer_date.isna()
    ).astype(int)
    context["non_delivered_status_with_delivery_timestamp"] = (
        context.order_status.ne("delivered") & dates.order_delivered_customer_date.notna()
    ).astype(int)
    # Estimated delivery is date-based. Do not mark delivery later on that same date as late.
    context["delivery_vs_estimate_calendar_days"] = (
        dates.order_delivered_customer_date.dt.normalize()
        - dates.order_estimated_delivery_date.dt.normalize()
    ).dt.days
    return context


def load_context() -> pd.DataFrame:
    baseline = pd.read_csv(BASELINE_FILE, dtype={"case_id": "string"})
    auto = pd.read_csv(AUTO_FILE, dtype={"case_id": "string"})
    orders = pd.read_csv(ORDERS_FILE, dtype={"order_id": "string"})
    return build_order_context(build_comparison(baseline, auto), orders)


def build_status_summary(context: pd.DataFrame) -> pd.DataFrame:
    work = context.assign(
        has_missing=context.raw_missing_event_count.gt(0).astype(int),
        auto_suspicious=context.auto_validation_label.eq("Suspicious").astype(int),
        auto_normal=context.auto_validation_label.eq("Normal").astype(int),
        disagreement=(~context.aligned_label_match).astype(int),
    )
    summary = work.groupby(["order_status", "status_context"]).agg(
        case_count=("case_id", "size"), missing_milestone_cases=("has_missing", "sum"),
        baseline_anomaly_cases=("anomaly_flag", "sum"),
        auto_anomaly_cases=("auto_binary_anomaly", "sum"),
        auto_suspicious_cases=("auto_suspicious", "sum"),
        auto_normal_cases=("auto_normal", "sum"), disagreement_cases=("disagreement", "sum"),
        delivered_status_missing_milestone=("delivered_status_missing_milestone", "sum"),
        delivered_status_without_delivery_timestamp=("delivered_status_without_delivery_timestamp", "sum"),
        non_delivered_status_with_delivery_timestamp=("non_delivered_status_with_delivery_timestamp", "sum"),
    ).reset_index()
    summary["missing_milestone_pct"] = summary.missing_milestone_cases / summary.case_count * 100
    summary["auto_anomaly_pct"] = summary.auto_anomaly_cases / summary.case_count * 100
    return summary


def build_blind_cases(sample: pd.DataFrame) -> pd.DataFrame:
    """Use an allowlist so predictions, scores and sampling groups cannot leak into review."""
    blind = sample[["review_id", *RAW_COLUMNS, "recorded_event_count", "raw_missing_event_count",
                    "missing_activities", *DURATION_FEATURES, "delivery_vs_estimate_calendar_days"]].copy()
    sequences, timestamps = [], []
    for row in blind.itertuples(index=False):
        events = [(pd.Timestamp(getattr(row, column)), activity)
                  for column, activity in ACTIVITY_COLUMNS.items() if pd.notna(getattr(row, column))]
        events.sort(key=lambda event: event[0])
        sequences.append(" -> ".join(activity for _, activity in events))
        timestamps.append("; ".join(f"{activity}: {timestamp}" for timestamp, activity in events))
    blind["event_sequence"] = sequences
    blind["event_timestamps"] = timestamps
    for column in REVIEW_COLUMNS:
        blind[column] = ""
    return blind[BLIND_COLUMNS].reset_index(drop=True)


def main() -> None:
    context = load_context()
    summary = build_status_summary(context)
    missing_by_status = context.groupby(["order_status", "raw_missing_event_count"]).size().reset_index(name="case_count")
    disagreements = context.loc[~context.aligned_label_match].groupby(
        ["comparison_group", "order_status", "recorded_event_profile"]
    ).size().reset_index(name="case_count")
    tables = {
        "order_status_context_cases": context,
        "order_status_context_summary": summary,
        "order_status_missing_milestones": missing_by_status,
        "order_status_disagreement_summary": disagreements,
    }
    for name, table in tables.items():
        table.to_csv(ANOMALY_DATA_DIR / f"{name}.csv", index=False)

    missing_total = int(context.raw_missing_event_count.gt(0).sum())
    delivered_missing = int(context.delivered_status_missing_milestone.sum())
    lines = [
        "# Order Status Context Audit", "",
        f"Evaluated {len(context):,} real orders, joined one-to-one by case_id. "
        "Saved duration values and missing-event counts reconcile with raw timestamps.", "",
        f"Of {missing_total:,} cases with missing milestones, {missing_total - delivered_missing:,} "
        f"have a status other than delivered; {delivered_missing:,} have delivered status.", "",
        "## Status Summary", "", *markdown_table(summary), "",
        "## Missing Milestones by Status", "", *markdown_table(missing_by_status), "",
        "## Disagreements by Status", "", *markdown_table(disagreements), "",
        "## Interpretation", "",
        "- Status is the value recorded in the imported dataset, not the current state of a live order.",
        "- created/approved/invoiced/processing/shipped describe nonfinal recorded states. "
        "canceled/unavailable describe recorded non-delivery outcomes. Missing later milestones "
        "may reflect those states; status alone does not establish normality or anomaly.",
        "- delivered with missing timestamps warrants a record-completeness review. "
        "Non-delivered status with a delivery timestamp needs context, such as status history; "
        "it is not automatically classified as corrupt data.",
        "- The four-milestone model has no cancellation or unavailability event. "
        "No such event or timestamp is fabricated here.",
        "- An authoritative extraction/observation cutoff is not supplied. "
        "The largest event timestamp is not assumed to be that cutoff. "
        "No pending duration is filled using today or an inferred snapshot date.",
        "- delivery_vs_estimate_calendar_days = delivery calendar date minus estimated delivery "
        "calendar date. Negative means early, zero means same date, positive means later. "
        "Missing delivery stays missing; this descriptive difference does not assign a label.",
        "- Original baseline and Auto Validation labels remain unchanged. "
        "These counts describe their existing behavior, not validated detection accuracy.", "",
        "## Next Step", "",
        "Prepare a fresh, detector-blinded review batch stratified by comparison group, "
        "recorded status and milestone completeness. Include agreement and disagreement "
        "groups. Exclude previously labeled cases and record sampling probabilities. "
        "Human reviewers must inspect status context before deciding whether a missing "
        "milestone reflects expected non-completion, uncertain timing or a data/process issue.", "",
        "## Input Fingerprints", "",
    ]
    for path in [ORDERS_FILE, BASELINE_FILE, AUTO_FILE, Path(__file__).resolve()]:
        lines.append(f"- `{path.relative_to(PROJECT_ROOT)}` SHA-256: `{file_sha256(path)}`")
    lines.extend(["", "## Reproduce", "", "```sh",
                  ".venv/bin/python scripts/03_anomaly_detection/order_status_context_audit.py", "```", ""])
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(summary.to_string(index=False))
    print(f"Report: {REPORT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
