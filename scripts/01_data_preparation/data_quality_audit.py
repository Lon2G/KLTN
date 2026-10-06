from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import PROCESSED_DATA_DIR, RAW_DATA_DIR, REPORT_DIR


RAW_ORDERS_PATH = RAW_DATA_DIR / "olist_orders_dataset.csv"
EVENT_LOG_PATH = PROCESSED_DATA_DIR / "event_log.csv"
SUMMARY_OUTPUT_PATH = PROCESSED_DATA_DIR / "data_quality_summary.csv"
REPORT_OUTPUT_PATH = REPORT_DIR / "data_quality_audit.md"

RAW_TIMESTAMP_COLUMNS = [
    "order_purchase_timestamp",
    "order_approved_at",
    "order_delivered_carrier_date",
    "order_delivered_customer_date",
    "order_estimated_delivery_date",
]

ACTIVITIES = [
    "Purchased",
    "Approved",
    "Handed to Carrier",
    "Delivered",
]

ACTIVITY_ORDER = [
    ("Purchased", "Approved"),
    ("Approved", "Handed to Carrier"),
    ("Handed to Carrier", "Delivered"),
]


def add_metric(rows: list[dict], section: str, metric: str, value, note: str = "") -> None:
    """Append one audit metric row."""
    rows.append(
        {
            "section": section,
            "metric": metric,
            "value": value,
            "note": note,
        }
    )


def load_raw_orders() -> pd.DataFrame:
    """Load the raw Olist order table and parse lifecycle timestamps."""
    orders = pd.read_csv(RAW_ORDERS_PATH)
    for column in RAW_TIMESTAMP_COLUMNS:
        if column in orders.columns:
            orders[column] = pd.to_datetime(orders[column], errors="coerce")
    return orders


def load_event_log() -> pd.DataFrame:
    """Load the processed event log and parse event timestamps."""
    event_log = pd.read_csv(EVENT_LOG_PATH)
    event_log["timestamp"] = pd.to_datetime(event_log["timestamp"], errors="coerce")
    return event_log


def build_case_activity_times(event_log: pd.DataFrame) -> pd.DataFrame:
    """Create one case-level timestamp table from the event log."""
    activity_times = event_log.pivot_table(
        index="case_id",
        columns="activity",
        values="timestamp",
        aggfunc="min",
    )
    for activity in ACTIVITIES:
        if activity not in activity_times.columns:
            activity_times[activity] = pd.NaT
    return activity_times[ACTIVITIES]


def calculate_duration_days(
    activity_times: pd.DataFrame,
    start_activity: str,
    end_activity: str,
) -> pd.Series:
    """Calculate transition duration in days; negative values are preserved."""
    return (
        activity_times[end_activity] - activity_times[start_activity]
    ).dt.total_seconds() / 86400


def metric_activity_name(activity: str) -> str:
    """Return a compact activity name for metric identifiers."""
    return activity.replace(" ", "_")


def format_report_value(value) -> str:
    """Format metric values for Markdown without unnecessary float suffixes."""
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric_value):
        if float(numeric_value).is_integer():
            return str(int(numeric_value))
        return str(numeric_value)
    return str(value)


def audit_raw_orders(orders: pd.DataFrame, rows: list[dict]) -> None:
    """Add raw-order data quality metrics."""
    add_metric(rows, "raw_orders", "row_count", len(orders))
    add_metric(rows, "raw_orders", "unique_order_id_count", orders["order_id"].nunique())
    add_metric(
        rows,
        "raw_orders",
        "duplicate_order_id_count",
        int(orders["order_id"].duplicated(keep=False).sum()),
    )

    for status, count in orders["order_status"].value_counts(dropna=False).items():
        add_metric(rows, "raw_order_status", str(status), int(count))

    for column in RAW_TIMESTAMP_COLUMNS:
        if column not in orders.columns:
            add_metric(rows, "raw_missing_timestamp", column, "missing_column")
            continue
        add_metric(
            rows,
            "raw_missing_timestamp",
            column,
            int(orders[column].isna().sum()),
        )


def audit_event_log(event_log: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    """Add processed event-log data quality metrics."""
    add_metric(rows, "event_log", "row_count", len(event_log))
    add_metric(rows, "event_log", "unique_case_id_count", event_log["case_id"].nunique())
    add_metric(rows, "event_log", "null_timestamp_count", int(event_log["timestamp"].isna().sum()))
    add_metric(
        rows,
        "event_log",
        "duplicate_event_count",
        int(event_log.duplicated(["case_id", "activity", "timestamp"], keep=False).sum()),
    )

    for activity, count in event_log["activity"].value_counts().reindex(ACTIVITIES, fill_value=0).items():
        add_metric(rows, "event_activity_count", activity, int(count))

    events_per_case = event_log.groupby("case_id").size()
    for event_count in [4, 3, 2, 1]:
        case_count = int((events_per_case == event_count).sum())
        add_metric(rows, "case_event_count", f"{event_count}_events", case_count)

    activity_times = build_case_activity_times(event_log)
    for activity in ACTIVITIES:
        missing_count = int(activity_times[activity].isna().sum())
        add_metric(rows, "case_missing_activity", activity, missing_count)

    return activity_times


def audit_temporal_consistency(activity_times: pd.DataFrame, rows: list[dict]) -> None:
    """Add timestamp-order and duration quality metrics."""
    for start_activity, end_activity in ACTIVITY_ORDER:
        duration = calculate_duration_days(activity_times, start_activity, end_activity)
        cases_with_both = duration.notna()
        negative_duration = duration < 0
        transition_name = (
            f"{metric_activity_name(start_activity)}_to_{metric_activity_name(end_activity)}"
        )

        add_metric(
            rows,
            "transition_duration",
            f"{transition_name}_case_count",
            int(cases_with_both.sum()),
        )
        add_metric(
            rows,
            "transition_duration",
            f"{transition_name}_negative_duration_count",
            int(negative_duration.sum()),
            "Negative durations are preserved as observed temporal inconsistency signals.",
        )

        valid_non_negative = duration[duration >= 0]
        if not valid_non_negative.empty:
            for quantile in [0.5, 0.75, 0.9, 0.95, 0.99]:
                add_metric(
                    rows,
                    "transition_duration_quantile_days",
                    f"{transition_name}_q{int(quantile * 100)}",
                    round(float(valid_non_negative.quantile(quantile)), 6),
                )
            add_metric(
                rows,
                "transition_duration_quantile_days",
                f"{transition_name}_max",
                round(float(valid_non_negative.max()), 6),
            )

    total_cycle_time = calculate_duration_days(
        activity_times,
        "Purchased",
        "Delivered",
    )
    add_metric(
        rows,
        "transition_duration",
        "Purchased_to_Delivered_case_count",
        int(total_cycle_time.notna().sum()),
    )
    add_metric(
        rows,
        "transition_duration",
        "Purchased_to_Delivered_negative_duration_count",
        int((total_cycle_time < 0).sum()),
    )

    valid_total = total_cycle_time[total_cycle_time >= 0]
    if not valid_total.empty:
        for quantile in [0.5, 0.75, 0.9, 0.95, 0.99]:
            add_metric(
                rows,
                "transition_duration_quantile_days",
                f"Purchased_to_Delivered_q{int(quantile * 100)}",
                round(float(valid_total.quantile(quantile)), 6),
            )
        add_metric(
            rows,
            "transition_duration_quantile_days",
            "Purchased_to_Delivered_max",
            round(float(valid_total.max()), 6),
        )


def write_markdown_report(summary: pd.DataFrame) -> None:
    """Write a human-readable data quality audit report."""
    lines = [
        "# Data Quality Audit",
        "",
        "This report audits the raw Olist order table and the processed event log.",
        "It does not modify, impute, synthesize, or remove any observed records.",
        "",
        "## Data Policy",
        "",
        "- No synthetic cases are introduced.",
        "- No artificial timestamps are generated.",
        "- Missing lifecycle timestamps are not filled.",
        "- Missing activities are preserved as incomplete traces.",
        "- Temporally inconsistent traces are preserved as anomaly/data-quality evidence.",
        "- No ground-truth anomaly labels are fabricated for the full dataset.",
        "",
        "## Audit Metrics",
        "",
    ]

    for section, section_df in summary.groupby("section", sort=False):
        lines.extend([f"### {section}", "", "| Metric | Value | Note |", "| --- | ---: | --- |"])
        for _, row in section_df.iterrows():
            note = "" if pd.isna(row["note"]) else str(row["note"])
            lines.append(f"| `{row['metric']}` | {format_report_value(row['value'])} | {note} |")
        lines.append("")

    REPORT_OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    rows: list[dict] = []

    orders = load_raw_orders()
    event_log = load_event_log()

    audit_raw_orders(orders, rows)
    activity_times = audit_event_log(event_log, rows)
    audit_temporal_consistency(activity_times, rows)

    summary = pd.DataFrame(rows)
    summary.to_csv(SUMMARY_OUTPUT_PATH, index=False)
    write_markdown_report(summary)

    print("Data quality audit completed.")
    print(f"Saved summary: {SUMMARY_OUTPUT_PATH}")
    print(f"Saved report: {REPORT_OUTPUT_PATH}")


if __name__ == "__main__":
    main()
