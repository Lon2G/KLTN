from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, MANUAL_REVIEW_DIR, REPORT_DIR


ANOMALY_RESULTS_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"
THRESHOLD_CANDIDATES_FILE = ANOMALY_DATA_DIR / "auto_validation_threshold_candidates.csv"

AUTO_VALIDATION_OUTPUT_FILE = ANOMALY_DATA_DIR / "auto_validation_results.csv"
AUTO_VALIDATION_SUMMARY_FILE = ANOMALY_DATA_DIR / "auto_validation_summary.csv"
SELECTED_THRESHOLDS_FILE = ANOMALY_DATA_DIR / "auto_validation_selected_thresholds.csv"
MANUAL_COMPARISON_FILE = MANUAL_REVIEW_DIR / "auto_validation_manual_reference_comparison.csv"
REPORT_OUTPUT_FILE = REPORT_DIR / "auto_validation_report.md"

MANUAL_REFERENCE_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation_labeled.csv"

DURATION_FEATURES = [
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]

FEATURE_LABELS = {
    "purchased_to_approved_days": "Purchased -> Approved",
    "approved_to_carrier_days": "Approved -> Carrier",
    "carrier_to_delivered_days": "Carrier -> Delivered",
    "total_cycle_time_days": "Purchased -> Delivered",
}

MAIN_LONG_METHOD = "iqr_1_5"
SEVERE_LONG_METHODS = ["iqr_3_0", "percentile_01_99"]
SHORT_REVIEW_METHOD = "percentile_01_99"
NEAR_LONG_THRESHOLD_RATIO = 0.95


def load_inputs() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load anomaly results and threshold candidates."""
    anomaly_df = pd.read_csv(ANOMALY_RESULTS_FILE)
    threshold_df = pd.read_csv(THRESHOLD_CANDIDATES_FILE)

    missing_duration_features = [
        feature for feature in DURATION_FEATURES if feature not in anomaly_df.columns
    ]
    if missing_duration_features:
        raise ValueError(
            "anomaly_results.csv is missing duration feature(s): "
            + ", ".join(missing_duration_features)
        )

    required_threshold_columns = [
        "duration_feature",
        "method",
        "effective_lower_threshold_days",
        "upper_threshold_days",
    ]
    missing_threshold_columns = [
        column for column in required_threshold_columns if column not in threshold_df.columns
    ]
    if missing_threshold_columns:
        raise ValueError(
            "auto_validation_threshold_candidates.csv is missing column(s): "
            + ", ".join(missing_threshold_columns)
        )

    return anomaly_df, threshold_df


def build_selected_thresholds(threshold_df: pd.DataFrame) -> pd.DataFrame:
    """Select configurable threshold methods used by Auto Validation."""
    rows = []

    for feature in DURATION_FEATURES:
        feature_thresholds = threshold_df[threshold_df["duration_feature"] == feature]
        main_row = get_threshold_row(feature_thresholds, MAIN_LONG_METHOD, feature)
        short_row = get_threshold_row(feature_thresholds, SHORT_REVIEW_METHOD, feature)
        severe_rows = [
            get_threshold_row(feature_thresholds, method, feature)
            for method in SEVERE_LONG_METHODS
        ]

        severe_upper_values = [
            float(row["upper_threshold_days"])
            for row in severe_rows
            if pd.notna(row["upper_threshold_days"])
        ]
        severe_upper = min(severe_upper_values) if severe_upper_values else pd.NA

        rows.append(
            {
                "duration_feature": feature,
                "duration_label": FEATURE_LABELS[feature],
                "main_long_method": MAIN_LONG_METHOD,
                "main_long_upper_threshold_days": main_row["upper_threshold_days"],
                "severe_long_methods": "; ".join(SEVERE_LONG_METHODS),
                "severe_long_upper_threshold_days": severe_upper,
                "short_review_method": SHORT_REVIEW_METHOD,
                "short_review_lower_threshold_days": short_row[
                    "effective_lower_threshold_days"
                ],
                "policy_note": (
                    "Long-duration thresholds are used as anomaly evidence. "
                    "Short non-negative thresholds are review signals only."
                ),
            }
        )

    return pd.DataFrame(rows)


def get_threshold_row(
    feature_thresholds: pd.DataFrame,
    method: str,
    feature: str,
) -> pd.Series:
    """Return one threshold candidate row for a feature/method pair."""
    matched = feature_thresholds[feature_thresholds["method"] == method]
    if matched.empty:
        raise ValueError(f"Missing threshold method {method} for feature {feature}.")
    return matched.iloc[0]


def prepare_anomaly_data(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce numeric columns needed by Auto Validation."""
    df = df.copy()
    numeric_defaults = {
        "trace_fitness": 1.0,
        "missing_event_count": 0,
        "reversed_order_flag": 0,
        "negative_duration_flag": 0,
    }

    for column, default in numeric_defaults.items():
        if column not in df.columns:
            df[column] = default
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(default)

    for feature in DURATION_FEATURES:
        df[feature] = pd.to_numeric(df[feature], errors="coerce")

    df["case_id"] = df["case_id"].astype(str)
    return df


def build_threshold_lookup(selected_thresholds: pd.DataFrame) -> dict[str, dict]:
    """Build a dictionary for fast per-row threshold checks."""
    lookup = {}
    for _, row in selected_thresholds.iterrows():
        lookup[row["duration_feature"]] = {
            "label": row["duration_label"],
            "main_upper": row["main_long_upper_threshold_days"],
            "severe_upper": row["severe_long_upper_threshold_days"],
            "short_lower": row["short_review_lower_threshold_days"],
        }
    return lookup


def run_auto_validation(
    df: pd.DataFrame,
    selected_thresholds: pd.DataFrame,
) -> pd.DataFrame:
    """Apply process-aware Auto Validation rules to each case."""
    threshold_lookup = build_threshold_lookup(selected_thresholds)
    rows = []

    for _, row in df.iterrows():
        validation = validate_case(row, threshold_lookup)
        rows.append(validation)

    validation_df = pd.DataFrame(rows)
    output_df = df.merge(validation_df, on="case_id", how="left")
    return output_df


def validate_case(row: pd.Series, threshold_lookup: dict[str, dict]) -> dict:
    """Validate one case using structural and temporal evidence."""
    evidence: list[str] = []
    reasons: list[str] = []

    missing_event_count = int(row["missing_event_count"])
    reversed_order = int(row["reversed_order_flag"]) > 0
    trace_fitness = float(row["trace_fitness"])

    negative_duration_features = []
    long_duration_features = []
    severe_long_duration_features = []
    short_duration_features = []
    near_long_duration_features = []

    for feature in DURATION_FEATURES:
        duration = row[feature]
        if pd.isna(duration):
            continue

        thresholds = threshold_lookup[feature]
        label = thresholds["label"]

        if duration < 0:
            negative_duration_features.append(f"{label}={duration:.3f} days")
            continue

        main_upper = thresholds["main_upper"]
        severe_upper = thresholds["severe_upper"]
        short_lower = thresholds["short_lower"]

        if pd.notna(main_upper) and duration > main_upper:
            long_duration_features.append(
                f"{label}={duration:.3f} days > {float(main_upper):.3f}"
            )
        elif (
            pd.notna(main_upper)
            and main_upper > 0
            and duration >= float(main_upper) * NEAR_LONG_THRESHOLD_RATIO
        ):
            near_long_duration_features.append(
                f"{label}={duration:.3f} days near {float(main_upper):.3f}"
            )

        if pd.notna(severe_upper) and duration > severe_upper:
            severe_long_duration_features.append(
                f"{label}={duration:.3f} days > {float(severe_upper):.3f}"
            )

        if pd.notna(short_lower) and short_lower > 0 and duration < short_lower:
            short_duration_features.append(
                f"{label}={duration:.3f} days < {float(short_lower):.3f}"
            )

    if missing_event_count > 0:
        evidence.append("missing_event")
        reasons.append(f"Missing event(s): {missing_event_count}")

    if reversed_order:
        evidence.append("reversed_order")
        reasons.append("Reversed activity order")

    if trace_fitness < 1.0:
        evidence.append("low_trace_fitness")
        reasons.append(f"Trace fitness={trace_fitness:.3f}")

    if negative_duration_features:
        evidence.append("negative_duration")
        reasons.append("Negative duration: " + "; ".join(negative_duration_features))

    if severe_long_duration_features:
        evidence.append("severe_long_duration")
        reasons.append(
            "Severe long duration: " + "; ".join(severe_long_duration_features)
        )
    elif long_duration_features:
        evidence.append("long_duration")
        reasons.append("Long duration: " + "; ".join(long_duration_features))

    if short_duration_features:
        evidence.append("short_duration_review_signal")
        reasons.append(
            "Short non-negative duration candidate: "
            + "; ".join(short_duration_features)
        )

    if near_long_duration_features and not long_duration_features:
        evidence.append("near_long_duration_review_signal")
        reasons.append(
            "Near-threshold long duration candidate: "
            + "; ".join(near_long_duration_features)
        )

    label = classify_case(
        missing_event_count=missing_event_count,
        reversed_order=reversed_order,
        trace_fitness=trace_fitness,
        negative_duration_count=len(negative_duration_features),
        long_duration_count=len(long_duration_features),
        severe_long_duration_count=len(severe_long_duration_features),
        short_duration_count=len(short_duration_features),
        near_long_duration_count=len(near_long_duration_features),
    )
    confidence = classify_confidence(
        label=label,
        missing_event_count=missing_event_count,
        reversed_order=reversed_order,
        negative_duration_count=len(negative_duration_features),
        long_duration_count=len(long_duration_features),
        severe_long_duration_count=len(severe_long_duration_features),
        short_duration_count=len(short_duration_features),
        near_long_duration_count=len(near_long_duration_features),
    )

    if not reasons:
        reasons = ["No validation anomaly evidence"]

    return {
        "case_id": row["case_id"],
        "auto_validation_label": label,
        "auto_validation_confidence": confidence,
        "auto_validation_reason": "; ".join(reasons),
        "auto_validation_evidence": "; ".join(evidence) if evidence else "none",
        "auto_process_evidence_count": count_process_evidence(
            missing_event_count,
            reversed_order,
            trace_fitness,
            len(negative_duration_features),
        ),
        "auto_long_duration_count": len(long_duration_features),
        "auto_severe_long_duration_count": len(severe_long_duration_features),
        "auto_short_duration_review_count": len(short_duration_features),
        "auto_near_long_duration_review_count": len(near_long_duration_features),
        "auto_validation_explanation": build_explanation(label, reasons),
    }


def classify_case(
    missing_event_count: int,
    reversed_order: bool,
    trace_fitness: float,
    negative_duration_count: int,
    long_duration_count: int,
    severe_long_duration_count: int,
    short_duration_count: int,
    near_long_duration_count: int,
) -> str:
    """Classify a case as Normal, Suspicious, or Anomaly."""
    if (
        reversed_order
        or negative_duration_count > 0
        or missing_event_count >= 2
        or severe_long_duration_count > 0
        or long_duration_count >= 2
        or (missing_event_count >= 1 and long_duration_count >= 1)
        or (trace_fitness < 1.0 and long_duration_count >= 1)
    ):
        return "Anomaly"

    if (
        missing_event_count == 1
        or trace_fitness < 1.0
        or long_duration_count == 1
        or short_duration_count > 0
        or near_long_duration_count > 0
    ):
        return "Suspicious"

    return "Normal"


def classify_confidence(
    label: str,
    missing_event_count: int,
    reversed_order: bool,
    negative_duration_count: int,
    long_duration_count: int,
    severe_long_duration_count: int,
    short_duration_count: int,
    near_long_duration_count: int,
) -> str:
    """Assign a transparent confidence level for the rule-based label."""
    if label == "Normal":
        return "High"

    if (
        reversed_order
        or negative_duration_count > 0
        or missing_event_count >= 2
        or severe_long_duration_count > 0
        or long_duration_count >= 2
    ):
        return "High"

    if label == "Anomaly":
        return "Medium"

    if (
        (short_duration_count > 0 or near_long_duration_count > 0)
        and long_duration_count == 0
        and missing_event_count == 0
    ):
        return "Low"

    return "Medium"


def count_process_evidence(
    missing_event_count: int,
    reversed_order: bool,
    trace_fitness: float,
    negative_duration_count: int,
) -> int:
    """Count process-related evidence groups."""
    return sum(
        [
            missing_event_count > 0,
            reversed_order,
            trace_fitness < 1.0,
            negative_duration_count > 0,
        ]
    )


def build_explanation(label: str, reasons: list[str]) -> str:
    """Build a concise audit-friendly explanation."""
    if label == "Normal":
        return "No structural or temporal evidence exceeded the configured Auto Validation rules."
    return f"{label} because " + "; ".join(reasons) + "."


def build_summary(output_df: pd.DataFrame) -> pd.DataFrame:
    """Build summary metrics for Auto Validation results."""
    rows = []
    total = len(output_df)

    add_metric(rows, "auto_validation", "total_cases", total)
    for label in ["Normal", "Suspicious", "Anomaly"]:
        count = int((output_df["auto_validation_label"] == label).sum())
        add_metric(rows, "auto_label_distribution", label, count, rate(count, total))

    for confidence in ["Low", "Medium", "High"]:
        count = int((output_df["auto_validation_confidence"] == confidence).sum())
        add_metric(
            rows,
            "auto_confidence_distribution",
            confidence,
            count,
            rate(count, total),
        )

    evidence_series = output_df["auto_validation_evidence"].fillna("none").astype(str)
    evidence_counts: dict[str, int] = {}
    for evidence_text in evidence_series:
        for evidence in evidence_text.split(";"):
            evidence = evidence.strip()
            if evidence:
                evidence_counts[evidence] = evidence_counts.get(evidence, 0) + 1

    for evidence, count in sorted(evidence_counts.items()):
        add_metric(rows, "auto_evidence_distribution", evidence, count, rate(count, total))

    return pd.DataFrame(rows)


def add_metric(
    rows: list[dict],
    section: str,
    metric: str,
    value,
    percentage: float | None = None,
) -> None:
    """Append one summary metric."""
    rows.append(
        {
            "section": section,
            "metric": metric,
            "value": value,
            "percentage": percentage,
        }
    )


def rate(count: int, total: int) -> float:
    """Return percentage rate rounded to six decimals."""
    if total == 0:
        return 0.0
    return round(count / total * 100, 6)


def compare_with_manual_reference(output_df: pd.DataFrame) -> pd.DataFrame:
    """Compare Auto Validation labels with unique manual-reference cases if available."""
    if not MANUAL_REFERENCE_FILE.exists():
        return pd.DataFrame()

    manual_df = pd.read_csv(MANUAL_REFERENCE_FILE)
    if "case_id" not in manual_df.columns or "reviewer_label" not in manual_df.columns:
        return pd.DataFrame()

    manual_df["case_id"] = manual_df["case_id"].astype(str)
    manual_reference = (
        manual_df.sort_values(["case_id", "contamination"], kind="mergesort")
        .drop_duplicates(subset=["case_id"], keep="first")
        [
            [
                "case_id",
                "reviewer_label",
                "reviewer_confidence",
                "reviewer_reason",
            ]
        ]
    )

    comparison = manual_reference.merge(
        output_df[
            [
                "case_id",
                "auto_validation_label",
                "auto_validation_confidence",
                "auto_validation_reason",
            ]
        ],
        on="case_id",
        how="left",
    )
    comparison["manual_auto_label_match"] = (
        comparison["reviewer_label"] == comparison["auto_validation_label"]
    )
    comparison.to_csv(MANUAL_COMPARISON_FILE, index=False)
    return comparison


def format_value(value) -> str:
    """Format a value for Markdown."""
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric_value):
        if float(numeric_value).is_integer():
            return str(int(numeric_value))
        return str(numeric_value)
    return str(value)


def write_markdown_report(
    summary_df: pd.DataFrame,
    selected_thresholds: pd.DataFrame,
    manual_comparison: pd.DataFrame,
) -> None:
    """Write a human-readable Auto Validation report."""
    lines = [
        "# Auto Validation Report",
        "",
        "This report summarizes the process-aware Auto Validation rule experiment.",
        "The generated labels are rule-based validation labels for analysis; they are not full-population ground truth.",
        "",
        "## Policy",
        "",
        "- No synthetic labels are introduced as ground truth.",
        "- The rules use process evidence and temporal thresholds estimated from the imported dataset.",
        "- Negative durations and reversed activity order are treated as strong temporal/process inconsistency evidence.",
        "- Very short non-negative durations are review signals only, not standalone anomaly evidence.",
        f"- Durations from {NEAR_LONG_THRESHOLD_RATIO:.0%} of the main upper threshold up to the threshold are treated as near-threshold review signals.",
        "- Thresholds should be recalculated when applying the framework to another marketplace dataset.",
        "",
        "## Selected Thresholds",
        "",
        "| Feature | Main Long Method | Main Upper Days | Severe Methods | Severe Upper Days | Short Review Lower Days |",
        "| --- | --- | ---: | --- | ---: | ---: |",
    ]

    for _, row in selected_thresholds.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['duration_feature']}`",
                    f"`{row['main_long_method']}`",
                    format_value(row["main_long_upper_threshold_days"]),
                    row["severe_long_methods"],
                    format_value(row["severe_long_upper_threshold_days"]),
                    format_value(row["short_review_lower_threshold_days"]),
                ]
            )
            + " |"
        )

    lines.extend(["", "## Summary Metrics", ""])
    for section, section_df in summary_df.groupby("section", sort=False):
        lines.extend([f"### {section}", "", "| Metric | Value | Percentage |", "| --- | ---: | ---: |"])
        for _, row in section_df.iterrows():
            lines.append(
                f"| `{row['metric']}` | {format_value(row['value'])} | {format_value(row['percentage'])} |"
            )
        lines.append("")

    lines.extend(["## Manual Reference Comparison", ""])
    if manual_comparison.empty:
        lines.append("No manual-reference comparison was generated.")
    else:
        total = len(manual_comparison)
        matches = int(manual_comparison["manual_auto_label_match"].sum())
        lines.extend(
            [
                f"Compared unique manual-reference cases: {total}.",
                f"Exact label matches: {matches} ({rate(matches, total)}%).",
                "",
                "| Manual Label | Auto Label | Count |",
                "| --- | --- | ---: |",
            ]
        )
        cross_tab = (
            manual_comparison.groupby(["reviewer_label", "auto_validation_label"])
            .size()
            .reset_index(name="count")
            .sort_values(["reviewer_label", "auto_validation_label"])
        )
        for _, row in cross_tab.iterrows():
            lines.append(
                f"| `{row['reviewer_label']}` | `{row['auto_validation_label']}` | {int(row['count'])} |"
            )

    lines.append("")
    REPORT_OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    anomaly_df, threshold_df = load_inputs()
    anomaly_df = prepare_anomaly_data(anomaly_df)
    selected_thresholds = build_selected_thresholds(threshold_df)

    output_df = run_auto_validation(anomaly_df, selected_thresholds)
    summary_df = build_summary(output_df)
    manual_comparison = compare_with_manual_reference(output_df)

    selected_thresholds.to_csv(SELECTED_THRESHOLDS_FILE, index=False)
    output_df.to_csv(AUTO_VALIDATION_OUTPUT_FILE, index=False)
    summary_df.to_csv(AUTO_VALIDATION_SUMMARY_FILE, index=False)
    write_markdown_report(summary_df, selected_thresholds, manual_comparison)

    print("Auto Validation completed.")
    print(f"Saved selected thresholds: {SELECTED_THRESHOLDS_FILE}")
    print(f"Saved Auto Validation results: {AUTO_VALIDATION_OUTPUT_FILE}")
    print(f"Saved Auto Validation summary: {AUTO_VALIDATION_SUMMARY_FILE}")
    if not manual_comparison.empty:
        print(f"Saved manual-reference comparison: {MANUAL_COMPARISON_FILE}")
    print(f"Saved report: {REPORT_OUTPUT_FILE}")


if __name__ == "__main__":
    main()
