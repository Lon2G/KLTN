from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, REPORT_DIR


INPUT_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"
THRESHOLD_OUTPUT_FILE = ANOMALY_DATA_DIR / "auto_validation_threshold_candidates.csv"
DISTRIBUTION_OUTPUT_FILE = ANOMALY_DATA_DIR / "auto_validation_duration_distribution.csv"
REPORT_OUTPUT_FILE = REPORT_DIR / "auto_validation_threshold_analysis.md"

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

PERCENTILE_METHODS = [
    ("percentile_01_99", 0.01, 0.99),
    ("percentile_05_95", 0.05, 0.95),
]

IQR_METHODS = [
    ("iqr_1_5", 1.5),
    ("iqr_3_0", 3.0),
]

MAD_METHODS = [
    ("mad_3_5", 3.5),
]


def load_anomaly_results() -> pd.DataFrame:
    """Load anomaly results and ensure required duration features exist."""
    df = pd.read_csv(INPUT_FILE)
    missing_features = [feature for feature in DURATION_FEATURES if feature not in df.columns]
    if missing_features:
        raise ValueError(
            "anomaly_results.csv is missing duration feature(s): "
            + ", ".join(missing_features)
        )
    return df


def clean_duration_series(df: pd.DataFrame, feature: str) -> pd.Series:
    """Return one numeric duration series in days."""
    return pd.to_numeric(df[feature], errors="coerce")


def non_negative_duration(series: pd.Series) -> pd.Series:
    """Return non-missing, non-negative durations for threshold estimation."""
    return series.dropna()[series.dropna() >= 0]


def build_distribution_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Build distribution statistics for each duration feature."""
    rows = []

    for feature in DURATION_FEATURES:
        series = clean_duration_series(df, feature)
        observed = series.dropna()
        non_negative = observed[observed >= 0]
        negative = observed[observed < 0]

        row = {
            "duration_feature": feature,
            "duration_label": FEATURE_LABELS[feature],
            "total_case_count": len(df),
            "observed_count": int(observed.count()),
            "missing_count": int(series.isna().sum()),
            "negative_count": int((observed < 0).sum()),
            "zero_count": int((observed == 0).sum()),
            "positive_count": int((observed > 0).sum()),
            "negative_rate_pct": rate((observed < 0).sum(), len(df)),
            "missing_rate_pct": rate(series.isna().sum(), len(df)),
        }

        if not negative.empty:
            row["min_negative_days"] = round(float(negative.min()), 6)
        else:
            row["min_negative_days"] = np.nan

        if not non_negative.empty:
            row.update(
                {
                    "min_non_negative_days": round(float(non_negative.min()), 6),
                    "q01_days": round(float(non_negative.quantile(0.01)), 6),
                    "q05_days": round(float(non_negative.quantile(0.05)), 6),
                    "q25_days": round(float(non_negative.quantile(0.25)), 6),
                    "median_days": round(float(non_negative.quantile(0.50)), 6),
                    "q75_days": round(float(non_negative.quantile(0.75)), 6),
                    "q90_days": round(float(non_negative.quantile(0.90)), 6),
                    "q95_days": round(float(non_negative.quantile(0.95)), 6),
                    "q99_days": round(float(non_negative.quantile(0.99)), 6),
                    "max_days": round(float(non_negative.max()), 6),
                    "mean_days": round(float(non_negative.mean()), 6),
                    "std_days": round(float(non_negative.std()), 6),
                }
            )
        rows.append(row)

    return pd.DataFrame(rows)


def build_threshold_candidates(df: pd.DataFrame) -> pd.DataFrame:
    """Build candidate lower/upper thresholds using several robust methods."""
    rows = []

    for feature in DURATION_FEATURES:
        series = clean_duration_series(df, feature)
        observed = series.dropna()
        non_negative = observed[observed >= 0]
        if non_negative.empty:
            continue

        context = {
            "duration_feature": feature,
            "duration_label": FEATURE_LABELS[feature],
            "observed_count": int(observed.count()),
            "negative_count": int((observed < 0).sum()),
            "non_negative_count": int(non_negative.count()),
        }

        for method_name, lower_q, upper_q in PERCENTILE_METHODS:
            lower = float(non_negative.quantile(lower_q))
            upper = float(non_negative.quantile(upper_q))
            rows.append(
                threshold_row(
                    context,
                    method_name,
                    lower,
                    upper,
                    "Percentile thresholds estimated from non-negative observed durations.",
                    non_negative,
                )
            )

        q1 = float(non_negative.quantile(0.25))
        q3 = float(non_negative.quantile(0.75))
        iqr = q3 - q1
        for method_name, multiplier in IQR_METHODS:
            lower = q1 - multiplier * iqr
            upper = q3 + multiplier * iqr
            rows.append(
                threshold_row(
                    context,
                    method_name,
                    lower,
                    upper,
                    "IQR thresholds estimated from non-negative observed durations.",
                    non_negative,
                )
            )

        median = float(non_negative.median())
        mad = float((non_negative - median).abs().median())
        for method_name, robust_z in MAD_METHODS:
            if mad == 0:
                lower = np.nan
                upper = np.nan
                note = "MAD is zero, so MAD-based thresholds are unavailable."
            else:
                distance = robust_z * mad / 0.6745
                lower = median - distance
                upper = median + distance
                note = "MAD thresholds estimated from non-negative observed durations using robust z-score."
            rows.append(
                threshold_row(
                    context,
                    method_name,
                    lower,
                    upper,
                    note,
                    non_negative,
                )
            )

    return pd.DataFrame(rows)


def threshold_row(
    context: dict,
    method_name: str,
    raw_lower: float,
    raw_upper: float,
    note: str,
    non_negative: pd.Series,
) -> dict:
    """Build one threshold candidate row with outlier counts."""
    lower_available = pd.notna(raw_lower)
    upper_available = pd.notna(raw_upper)
    effective_lower = max(raw_lower, 0.0) if lower_available else np.nan

    lower_outlier_count = (
        int((non_negative < effective_lower).sum())
        if lower_available and effective_lower > 0
        else 0
    )
    upper_outlier_count = int((non_negative > raw_upper).sum()) if upper_available else 0

    return {
        **context,
        "method": method_name,
        "raw_lower_threshold_days": round_float(raw_lower),
        "effective_lower_threshold_days": round_float(effective_lower),
        "upper_threshold_days": round_float(raw_upper),
        "short_non_negative_outlier_count": lower_outlier_count,
        "short_non_negative_outlier_rate_pct": rate(lower_outlier_count, len(non_negative)),
        "long_outlier_count": upper_outlier_count,
        "long_outlier_rate_pct": rate(upper_outlier_count, len(non_negative)),
        "note": note,
    }


def rate(count: int | float, total: int | float) -> float:
    """Return a percentage rate rounded to six decimals."""
    if total == 0:
        return 0.0
    return round(float(count) / float(total) * 100, 6)


def round_float(value: float) -> float:
    """Round floats for stable CSV/report output."""
    if pd.isna(value):
        return np.nan
    return round(float(value), 6)


def format_value(value) -> str:
    """Format values for Markdown tables."""
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric_value):
        if float(numeric_value).is_integer():
            return str(int(numeric_value))
        return str(numeric_value)
    return str(value)


def write_markdown_report(
    distribution_df: pd.DataFrame,
    threshold_df: pd.DataFrame,
) -> None:
    """Write a human-readable threshold analysis report."""
    lines = [
        "# Auto Validation Threshold Analysis",
        "",
        "This report analyzes candidate temporal thresholds from the current full anomaly dataset.",
        "It does not create labels, change anomaly flags, or hard-code a final threshold rule.",
        "",
        "## Policy",
        "",
        "- Thresholds are estimated from the imported dataset, not from fabricated values.",
        "- Negative durations are handled separately as temporal inconsistency/data-quality evidence.",
        "- Upper thresholds identify unusually long durations and are the primary delay-anomaly candidates.",
        "- Lower thresholds identify unusually short non-negative durations only as candidates for cautious review.",
        "- When applying the framework to another marketplace, these thresholds should be recalculated from that marketplace's data.",
        "",
        "## Duration Distribution Summary",
        "",
        "| Feature | Observed | Missing | Negative | Zero | Median | P95 | P99 | Max |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]

    for _, row in distribution_df.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['duration_feature']}`",
                    format_value(row["observed_count"]),
                    format_value(row["missing_count"]),
                    format_value(row["negative_count"]),
                    format_value(row["zero_count"]),
                    format_value(row["median_days"]),
                    format_value(row["q95_days"]),
                    format_value(row["q99_days"]),
                    format_value(row["max_days"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Candidate Upper Thresholds",
            "",
            "The table below shows candidate upper thresholds for unusually long durations.",
            "",
            "| Feature | Method | Upper Threshold Days | Long Outliers | Long Outlier Rate % |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )

    preferred_methods = ["percentile_05_95", "percentile_01_99", "iqr_1_5", "iqr_3_0", "mad_3_5"]
    feature_order = {feature: index for index, feature in enumerate(DURATION_FEATURES)}
    report_threshold_df = threshold_df.copy()
    report_threshold_df["method_order"] = report_threshold_df["method"].apply(
        lambda method: preferred_methods.index(method)
        if method in preferred_methods
        else len(preferred_methods)
    )
    report_threshold_df["feature_order"] = report_threshold_df["duration_feature"].map(feature_order)
    report_threshold_df = report_threshold_df.sort_values(
        ["feature_order", "method_order", "method"]
    )

    for _, row in report_threshold_df.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['duration_feature']}`",
                    f"`{row['method']}`",
                    format_value(row["upper_threshold_days"]),
                    format_value(row["long_outlier_count"]),
                    format_value(row["long_outlier_rate_pct"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Candidate Lower Thresholds",
            "",
            "Lower thresholds are included for analysis only. Very short positive durations may be legitimate in ecommerce operations, especially for payment approval. Negative durations remain the stronger evidence of temporal inconsistency.",
            "",
            "| Feature | Method | Effective Lower Threshold Days | Short Non-Negative Outliers | Short Outlier Rate % |",
            "| --- | --- | ---: | ---: | ---: |",
        ]
    )

    for _, row in report_threshold_df.iterrows():
        lines.append(
            "| "
            + " | ".join(
                [
                    f"`{row['duration_feature']}`",
                    f"`{row['method']}`",
                    format_value(row["effective_lower_threshold_days"]),
                    format_value(row["short_non_negative_outlier_count"]),
                    format_value(row["short_non_negative_outlier_rate_pct"]),
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Interpretation Notes",
            "",
            "- The current analysis should be used to compare threshold candidates before finalizing Auto Validation rules.",
            "- The upper threshold is more suitable for delivery-delay anomaly evidence.",
            "- Lower non-negative thresholds should not automatically imply anomaly without domain confirmation.",
            "- The final Auto Validation procedure should store the selected method and recalculate thresholds when the input dataset changes.",
            "",
        ]
    )

    REPORT_OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    df = load_anomaly_results()

    distribution_df = build_distribution_summary(df)
    threshold_df = build_threshold_candidates(df)

    distribution_df.to_csv(DISTRIBUTION_OUTPUT_FILE, index=False)
    threshold_df.to_csv(THRESHOLD_OUTPUT_FILE, index=False)
    write_markdown_report(distribution_df, threshold_df)

    print("Auto Validation threshold analysis completed.")
    print(f"Saved duration distribution: {DISTRIBUTION_OUTPUT_FILE}")
    print(f"Saved threshold candidates: {THRESHOLD_OUTPUT_FILE}")
    print(f"Saved report: {REPORT_OUTPUT_FILE}")


if __name__ == "__main__":
    main()
