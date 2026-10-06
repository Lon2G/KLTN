from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import PROCESSED_DATA_DIR


INPUT_PATH = PROCESSED_DATA_DIR / "event_log.csv"
PERFORMANCE_SUMMARY_PATH = PROCESSED_DATA_DIR / "process_performance_summary.csv"
TEMPORAL_ANOMALIES_PATH = PROCESSED_DATA_DIR / "temporal_anomalies.csv"

CASE_ID_COL = "case_id"
ACTIVITY_COL = "activity"
TIMESTAMP_COL = "timestamp"

DURATION_DEFINITIONS = [
    ("Purchased to Approved", "Purchased", "Approved"),
    ("Approved to Handed to Carrier", "Approved", "Handed to Carrier"),
    ("Handed to Carrier to Delivered", "Handed to Carrier", "Delivered"),
    ("Purchased to Delivered (cycle time)", "Purchased", "Delivered"),
]


def read_event_log(input_path: Path = INPUT_PATH) -> pd.DataFrame:
    """Read the event log and convert timestamps to datetime."""
    event_log = pd.read_csv(input_path)
    event_log[TIMESTAMP_COL] = pd.to_datetime(event_log[TIMESTAMP_COL], errors="coerce")
    return event_log


def build_case_variants(event_log: pd.DataFrame) -> pd.DataFrame:
    """Build process variants per case based on timestamp order."""
    sorted_log = event_log.sort_values(
        [CASE_ID_COL, TIMESTAMP_COL],
        kind="mergesort",
    )
    variants = (
        sorted_log.groupby(CASE_ID_COL)[ACTIVITY_COL]
        .agg(lambda activities: " -> ".join(activities))
        .reset_index(name="variant")
    )
    return variants


def calculate_variant_statistics(case_variants: pd.DataFrame) -> pd.DataFrame:
    """Calculate case count and percentage for each process variant."""
    total_cases = len(case_variants)
    variant_stats = (
        case_variants["variant"]
        .value_counts()
        .rename_axis("variant")
        .reset_index(name="case_count")
    )
    variant_stats["case_percentage"] = (
        variant_stats["case_count"] / total_cases * 100
    ).round(4)
    return variant_stats


def build_case_activity_times(event_log: pd.DataFrame) -> pd.DataFrame:
    """Create one row per case with one timestamp column per activity."""
    return event_log.pivot_table(
        index=CASE_ID_COL,
        columns=ACTIVITY_COL,
        values=TIMESTAMP_COL,
        aggfunc="min",
    )


def calculate_duration_records(
    case_activity_times: pd.DataFrame,
    case_variants: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Calculate valid durations and collect reversed timestamp cases separately."""
    variant_lookup = case_variants.set_index(CASE_ID_COL)["variant"]
    duration_records = []
    reversed_records = []

    for duration_name, start_activity, end_activity in DURATION_DEFINITIONS:
        cases_with_both_events = case_activity_times[
            case_activity_times[start_activity].notna()
            & case_activity_times[end_activity].notna()
        ]

        for case_id, row in cases_with_both_events.iterrows():
            start_timestamp = row[start_activity]
            end_timestamp = row[end_activity]
            duration_days = (end_timestamp - start_timestamp).total_seconds() / 86400

            record = {
                "case_id": case_id,
                "duration_name": duration_name,
                "start_activity": start_activity,
                "end_activity": end_activity,
                "start_timestamp": start_timestamp,
                "end_timestamp": end_timestamp,
                "duration_days": duration_days,
                "variant": variant_lookup.get(case_id),
            }

            if end_timestamp >= start_timestamp:
                duration_records.append(record)
            else:
                reversed_records.append(
                    {
                        **record,
                        "anomaly_type": "reversed_timestamp_order",
                        "threshold_days": pd.NA,
                    }
                )

    return pd.DataFrame(duration_records), pd.DataFrame(reversed_records)


def calculate_duration_summary(valid_durations: pd.DataFrame) -> pd.DataFrame:
    """Calculate descriptive statistics and IQR outlier thresholds."""
    summary_rows = []

    for duration_name, group in valid_durations.groupby("duration_name", sort=False):
        durations = group["duration_days"]
        q1 = durations.quantile(0.25)
        q3 = durations.quantile(0.75)
        iqr = q3 - q1
        outlier_threshold = q3 + 1.5 * iqr
        outlier_count = (durations > outlier_threshold).sum()

        summary_rows.append(
            {
                "duration_name": duration_name,
                "count": int(durations.count()),
                "mean_days": durations.mean(),
                "median_days": durations.median(),
                "q1_days": q1,
                "q3_days": q3,
                "iqr_days": iqr,
                "p95_days": durations.quantile(0.95),
                "min_days": durations.min(),
                "max_days": durations.max(),
                "outlier_threshold_days": outlier_threshold,
                "outlier_case_count": int(outlier_count),
            }
        )

    return pd.DataFrame(summary_rows)


def find_duration_outliers(
    valid_durations: pd.DataFrame,
    duration_summary: pd.DataFrame,
) -> pd.DataFrame:
    """Find cases with duration above Q3 + 1.5 * IQR for each duration type."""
    outlier_records = []
    threshold_lookup = duration_summary.set_index("duration_name")[
        "outlier_threshold_days"
    ].to_dict()

    for duration_name, threshold in threshold_lookup.items():
        duration_rows = valid_durations[
            (valid_durations["duration_name"] == duration_name)
            & (valid_durations["duration_days"] > threshold)
        ].copy()
        duration_rows["anomaly_type"] = "duration_above_iqr_threshold"
        duration_rows["threshold_days"] = threshold
        outlier_records.append(duration_rows)

    if not outlier_records:
        return pd.DataFrame()

    return pd.concat(outlier_records, ignore_index=True)


def save_outputs(
    duration_summary: pd.DataFrame,
    temporal_anomalies: pd.DataFrame,
) -> None:
    """Save performance summary and temporal anomaly cases."""
    duration_summary.to_csv(PERFORMANCE_SUMMARY_PATH, index=False)
    temporal_anomalies.to_csv(TEMPORAL_ANOMALIES_PATH, index=False)


def print_variant_statistics(variant_stats: pd.DataFrame) -> None:
    """Print all process variants."""
    print("Process variants")
    print(f"Total variants: {len(variant_stats)}")
    for index, row in variant_stats.iterrows():
        print(
            f"{index + 1}. {row['variant']}: "
            f"{row['case_count']} cases ({row['case_percentage']:.4f}%)"
        )
    print()


def print_duration_summary(duration_summary: pd.DataFrame) -> None:
    """Print duration statistics."""
    print("Duration summary, unit: days")
    if duration_summary.empty:
        print("No valid durations found.")
        print()
        return

    print(duration_summary.round(4).to_string(index=False))
    print()


def print_anomaly_summary(temporal_anomalies: pd.DataFrame) -> None:
    """Print temporal anomaly counts."""
    print("Temporal anomalies")
    if temporal_anomalies.empty:
        print("No temporal anomalies found.")
        return

    counts = (
        temporal_anomalies.groupby(["duration_name", "anomaly_type"])
        .size()
        .reset_index(name="case_count")
    )
    print(counts.to_string(index=False))
    print()
    print(f"Saved temporal anomalies: {TEMPORAL_ANOMALIES_PATH}")


def main() -> None:
    event_log = read_event_log()
    total_cases = event_log[CASE_ID_COL].nunique()
    total_events = len(event_log)

    case_variants = build_case_variants(event_log)
    variant_stats = calculate_variant_statistics(case_variants)
    case_activity_times = build_case_activity_times(event_log)
    valid_durations, reversed_anomalies = calculate_duration_records(
        case_activity_times,
        case_variants,
    )
    duration_summary = calculate_duration_summary(valid_durations)
    outlier_anomalies = find_duration_outliers(valid_durations, duration_summary)
    temporal_anomalies = pd.concat(
        [reversed_anomalies, outlier_anomalies],
        ignore_index=True,
    )

    save_outputs(duration_summary, temporal_anomalies)

    print("Process performance analysis")
    print(f"Total cases: {total_cases}")
    print(f"Total events: {total_events}")
    print()
    print_variant_statistics(variant_stats)
    print_duration_summary(duration_summary)
    print_anomaly_summary(temporal_anomalies)
    print()
    print(f"Saved duration summary: {PERFORMANCE_SUMMARY_PATH}")


if __name__ == "__main__":
    main()
