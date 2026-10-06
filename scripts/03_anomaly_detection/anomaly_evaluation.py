from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.metrics import cohen_kappa_score
from sklearn.preprocessing import StandardScaler


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, BASELINE_FIGURE_DIR, MANUAL_REVIEW_DIR


INPUT_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"

SUMMARY_FILE = ANOMALY_DATA_DIR / "anomaly_evaluation_summary.csv"
OVERLAP_FILE = ANOMALY_DATA_DIR / "detector_overlap.csv"
AGREEMENT_FILE = ANOMALY_DATA_DIR / "detector_agreement.csv"
VARIANT_FILE = ANOMALY_DATA_DIR / "anomaly_by_variant.csv"
FITNESS_BAND_FILE = ANOMALY_DATA_DIR / "anomaly_by_fitness_band.csv"
DURATION_COMPARISON_FILE = ANOMALY_DATA_DIR / "anomaly_duration_comparison.csv"
REASON_FREQUENCY_FILE = ANOMALY_DATA_DIR / "anomaly_reason_frequency.csv"
MANUAL_REVIEW_FILE = MANUAL_REVIEW_DIR / "anomaly_manual_review_sample.csv"
IF_SENSITIVITY_FILE = ANOMALY_DATA_DIR / "isolation_forest_sensitivity.csv"

REQUIRED_COLUMNS = [
    "case_id",
    "rule_anomaly_flag",
    "statistical_anomaly_flag",
    "isolation_forest_flag",
    "anomaly_vote_count",
    "anomaly_level",
    "anomaly_flag",
]

DETECTOR_COLUMNS = [
    "rule_anomaly_flag",
    "statistical_anomaly_flag",
    "isolation_forest_flag",
]

DURATION_FEATURES = [
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]

IF_FEATURE_PRIORITY = [
    "event_count",
    "trace_fitness",
    "missing_event_count",
    "reversed_order_flag",
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]

CONTAMINATION_VALUES = [
    0.01,
    0.03,
    0.05,
    0.08,
    0.10,
    0.15,
]


# ============================================================
# LOAD AND VALIDATE DATA
# ============================================================

def load_anomaly_results(input_file: Path = INPUT_FILE) -> pd.DataFrame:
    """Load anomaly results and validate mandatory columns."""
    df = pd.read_csv(input_file)
    missing_columns = [column for column in REQUIRED_COLUMNS if column not in df.columns]

    if missing_columns:
        raise ValueError(
            "anomaly_results.csv is missing required column(s): "
            + ", ".join(missing_columns)
        )

    for column in DETECTOR_COLUMNS + ["anomaly_vote_count", "anomaly_flag"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)

    return df


# ============================================================
# BASIC ANOMALY RATES
# ============================================================

def calculate_basic_summary(df: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    """Calculate detector anomaly rates, vote distribution, and level distribution."""
    total_cases = len(df)
    detector_rows = []

    detector_mapping = {
        "Rule-based": "rule_anomaly_flag",
        "Statistical IQR": "statistical_anomaly_flag",
        "Isolation Forest": "isolation_forest_flag",
        "Final Hybrid": "anomaly_flag",
    }

    for detector_name, column in detector_mapping.items():
        anomaly_count = int(df[column].sum())
        detector_rows.append(
            {
                "metric": detector_name,
                "case_count": anomaly_count,
                "percentage": anomaly_count / total_cases * 100,
            }
        )

    vote_distribution = (
        df["anomaly_vote_count"]
        .value_counts()
        .reindex([0, 1, 2, 3], fill_value=0)
        .sort_index()
    )

    for vote_count, case_count in vote_distribution.items():
        detector_rows.append(
            {
                "metric": f"vote_count_{vote_count}",
                "case_count": int(case_count),
                "percentage": int(case_count) / total_cases * 100,
            }
        )

    level_distribution = df["anomaly_level"].value_counts()

    for anomaly_level, case_count in level_distribution.items():
        detector_rows.append(
            {
                "metric": f"level_{anomaly_level}",
                "case_count": int(case_count),
                "percentage": int(case_count) / total_cases * 100,
            }
        )

    summary_df = pd.DataFrame(detector_rows)
    summary_df.to_csv(SUMMARY_FILE, index=False)
    return summary_df, vote_distribution, level_distribution


# ============================================================
# DETECTOR OVERLAP
# ============================================================

def calculate_detector_overlap(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate eight mutually exclusive detector-overlap groups."""
    total_cases = len(df)
    rule = df["rule_anomaly_flag"] == 1
    stat = df["statistical_anomaly_flag"] == 1
    isolation = df["isolation_forest_flag"] == 1

    groups = {
        "none": (~rule & ~stat & ~isolation),
        "rule_only": (rule & ~stat & ~isolation),
        "statistical_only": (~rule & stat & ~isolation),
        "isolation_only": (~rule & ~stat & isolation),
        "rule_statistical": (rule & stat & ~isolation),
        "rule_isolation": (rule & ~stat & isolation),
        "statistical_isolation": (~rule & stat & isolation),
        "all_three": (rule & stat & isolation),
    }

    rows = []
    for group_name, mask in groups.items():
        case_count = int(mask.sum())
        rows.append(
            {
                "overlap_group": group_name,
                "case_count": case_count,
                "percentage": case_count / total_cases * 100,
            }
        )

    overlap_df = pd.DataFrame(rows)
    overlap_total = int(overlap_df["case_count"].sum())
    if overlap_total != total_cases:
        raise ValueError(
            f"Detector-overlap groups sum to {overlap_total:,}, "
            f"but total cases is {total_cases:,}."
        )

    overlap_df.to_csv(OVERLAP_FILE, index=False)
    return overlap_df


# ============================================================
# PAIRWISE AGREEMENT
# ============================================================

def calculate_pairwise_agreement(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate descriptive agreement metrics for each detector pair."""
    detector_pairs = [
        ("Rule vs Statistical", "rule_anomaly_flag", "statistical_anomaly_flag"),
        ("Rule vs Isolation Forest", "rule_anomaly_flag", "isolation_forest_flag"),
        (
            "Statistical vs Isolation Forest",
            "statistical_anomaly_flag",
            "isolation_forest_flag",
        ),
    ]

    rows = []
    total_cases = len(df)

    for pair_name, first_col, second_col in detector_pairs:
        first = df[first_col] == 1
        second = df[second_col] == 1

        both_anomaly = int((first & second).sum())
        first_only = int((first & ~second).sum())
        second_only = int((~first & second).sum())
        both_normal = int((~first & ~second).sum())
        union_anomaly = both_anomaly + first_only + second_only

        rows.append(
            {
                "detector_pair": pair_name,
                "both_anomaly": both_anomaly,
                "first_only": first_only,
                "second_only": second_only,
                "both_normal": both_normal,
                "observed_agreement_pct": (
                    both_anomaly + both_normal
                ) / total_cases * 100,
                "jaccard_similarity": (
                    both_anomaly / union_anomaly
                    if union_anomaly > 0
                    else np.nan
                ),
                "cohen_kappa": cohen_kappa_score(df[first_col], df[second_col]),
            }
        )

    agreement_df = pd.DataFrame(rows)
    agreement_df.to_csv(AGREEMENT_FILE, index=False)
    return agreement_df


# ============================================================
# PROCESS VARIANT ANALYSIS
# ============================================================

def analyze_by_variant(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze anomaly distribution by process variant when the column exists."""
    if "variant" not in df.columns:
        return pd.DataFrame()

    total_cases = len(df)
    aggregations = {
        "total_cases": ("case_id", "count"),
        "anomaly_cases": ("anomaly_flag", "sum"),
        "average_anomaly_vote_count": ("anomaly_vote_count", "mean"),
    }

    if "trace_fitness" in df.columns:
        aggregations["average_trace_fitness"] = ("trace_fitness", "mean")

    variant_df = df.groupby("variant", dropna=False).agg(**aggregations).reset_index()
    variant_df["anomaly_rate"] = (
        variant_df["anomaly_cases"] / variant_df["total_cases"] * 100
    )
    variant_df["case_share_pct"] = variant_df["total_cases"] / total_cases * 100

    variant_df = variant_df.sort_values(
        ["anomaly_cases", "anomaly_rate"],
        ascending=[False, False],
    )
    variant_df.to_csv(VARIANT_FILE, index=False)
    return variant_df


# ============================================================
# CONFORMANCE RELATIONSHIP
# ============================================================

def analyze_by_fitness_band(df: pd.DataFrame) -> pd.DataFrame:
    """Calculate anomaly rate by trace-fitness band."""
    required = ["trace_fitness", "missing_event_count", "reversed_order_flag"]
    if any(column not in df.columns for column in required):
        return pd.DataFrame()

    working_df = df.copy()
    working_df["trace_fitness"] = pd.to_numeric(
        working_df["trace_fitness"],
        errors="coerce",
    )

    conditions = [
        working_df["trace_fitness"] == 1.0,
        (working_df["trace_fitness"] >= 0.8) & (working_df["trace_fitness"] < 1.0),
        (working_df["trace_fitness"] >= 0.5) & (working_df["trace_fitness"] < 0.8),
        working_df["trace_fitness"] < 0.5,
    ]
    labels = [
        "1.0",
        "0.8-<1.0",
        "0.5-<0.8",
        "<0.5",
    ]

    working_df["fitness_band"] = np.select(
        conditions,
        labels,
        default="missing",
    )

    band_df = (
        working_df.groupby("fitness_band", dropna=False)
        .agg(
            total_cases=("case_id", "count"),
            anomaly_cases=("anomaly_flag", "sum"),
            avg_missing_event_count=("missing_event_count", "mean"),
            reversed_order_cases=("reversed_order_flag", "sum"),
            avg_anomaly_vote_count=("anomaly_vote_count", "mean"),
        )
        .reset_index()
    )
    band_df["anomaly_rate"] = band_df["anomaly_cases"] / band_df["total_cases"] * 100

    band_order = {
        "1.0": 0,
        "0.8-<1.0": 1,
        "0.5-<0.8": 2,
        "<0.5": 3,
        "missing": 4,
    }
    band_df["sort_order"] = band_df["fitness_band"].map(band_order)
    band_df = band_df.sort_values("sort_order").drop(columns=["sort_order"])
    band_df.to_csv(FITNESS_BAND_FILE, index=False)
    return band_df


# ============================================================
# DURATION ANALYSIS
# ============================================================

def analyze_duration_comparison(df: pd.DataFrame) -> pd.DataFrame:
    """Compare duration distributions for normal and final hybrid anomaly cases."""
    available_durations = [column for column in DURATION_FEATURES if column in df.columns]
    if not available_durations:
        return pd.DataFrame()

    rows = []
    for duration_col in available_durations:
        numeric_duration = pd.to_numeric(df[duration_col], errors="coerce")

        for group_name, mask in {
            "Normal": df["anomaly_flag"] == 0,
            "Final Hybrid Anomaly": df["anomaly_flag"] == 1,
        }.items():
            values = numeric_duration[mask].dropna()

            rows.append(
                {
                    "duration_feature": duration_col,
                    "case_group": group_name,
                    "count": int(values.count()),
                    "median": values.median(),
                    "mean": values.mean(),
                    "std": values.std(),
                    "p75": values.quantile(0.75),
                    "p90": values.quantile(0.90),
                    "p95": values.quantile(0.95),
                    "max": values.max(),
                }
            )

    duration_df = pd.DataFrame(rows)
    duration_df.to_csv(DURATION_COMPARISON_FILE, index=False)
    return duration_df


# ============================================================
# ANOMALY REASON ANALYSIS
# ============================================================

def analyze_anomaly_reasons(df: pd.DataFrame) -> pd.DataFrame:
    """Split semicolon-separated anomaly reasons and count each reason."""
    if "anomaly_reason" not in df.columns:
        return pd.DataFrame()

    reasons = (
        df["anomaly_reason"]
        .fillna("No anomaly detected")
        .astype(str)
        .str.split(";")
        .explode()
        .str.strip()
    )
    reasons = reasons[reasons != ""]

    reason_df = reasons.value_counts().rename_axis("reason").reset_index(name="count")
    reason_df["percentage_of_cases"] = reason_df["count"] / len(df) * 100
    reason_df.to_csv(REASON_FREQUENCY_FILE, index=False)
    return reason_df


# ============================================================
# MANUAL REVIEW SAMPLE
# ============================================================

def create_manual_review_sample(df: pd.DataFrame, max_cases: int = 100) -> pd.DataFrame:
    """Create a diverse manual-review sample prioritized by anomaly severity."""
    working_df = df.copy()
    available_durations = [column for column in DURATION_FEATURES if column in df.columns]

    if available_durations:
        duration_values = working_df[available_durations].apply(
            pd.to_numeric,
            errors="coerce",
        )
        working_df["max_duration_for_priority"] = duration_values.max(axis=1)
    else:
        working_df["max_duration_for_priority"] = np.nan

    if "isolation_anomaly_score" not in working_df.columns:
        working_df["isolation_anomaly_score"] = np.nan

    if "trace_fitness" not in working_df.columns:
        working_df["trace_fitness"] = np.nan

    sort_columns = [
        "anomaly_vote_count",
        "isolation_anomaly_score",
        "max_duration_for_priority",
        "trace_fitness",
    ]
    ascending = [
        False,
        False,
        False,
        True,
    ]

    sorted_df = working_df.sort_values(sort_columns, ascending=ascending)

    if "variant" in sorted_df.columns:
        selected_parts = []
        remaining = sorted_df.copy()

        while len(selected_parts) < max_cases and not remaining.empty:
            one_per_variant = remaining.groupby("variant", dropna=False).head(1)
            remaining = remaining.drop(index=one_per_variant.index)
            selected_parts.append(one_per_variant)

            if sum(len(part) for part in selected_parts) >= max_cases:
                break

        sample_df = pd.concat(selected_parts, ignore_index=False).head(max_cases)
    else:
        sample_df = sorted_df.head(max_cases)

    preferred_columns = [
        "case_id",
        "variant",
        "trace_fitness",
        "purchased_to_approved_days",
        "approved_to_carrier_days",
        "carrier_to_delivered_days",
        "total_cycle_time_days",
        "rule_anomaly_flag",
        "statistical_anomaly_flag",
        "isolation_forest_flag",
        "isolation_anomaly_score",
        "anomaly_vote_count",
        "anomaly_level",
        "anomaly_reason",
    ]
    output_columns = [column for column in preferred_columns if column in sample_df.columns]

    sample_df = sample_df[output_columns]
    sample_df.to_csv(MANUAL_REVIEW_FILE, index=False)
    return sample_df


# ============================================================
# ISOLATION FOREST SENSITIVITY ANALYSIS
# ============================================================

def run_isolation_forest_sensitivity(df: pd.DataFrame) -> pd.DataFrame:
    """Run new Isolation Forest models for sensitivity comparison only."""
    features = []
    for column in IF_FEATURE_PRIORITY:
        if column not in df.columns:
            continue

        numeric_values = pd.to_numeric(df[column], errors="coerce")
        if numeric_values.notna().sum() < 10:
            continue

        df[column] = numeric_values
        features.append(column)

    if not features:
        raise ValueError("No usable numeric features found for sensitivity analysis.")

    X = df[features].copy()
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(imputer.fit_transform(X))

    rule = df["rule_anomaly_flag"] == 1
    statistical = df["statistical_anomaly_flag"] == 1
    total_cases = len(df)

    rows = []
    for contamination in CONTAMINATION_VALUES:
        model = IsolationForest(
            n_estimators=300,
            contamination=contamination,
            random_state=42,
            n_jobs=-1,
        )
        prediction = model.fit_predict(X_scaled)
        if_flag = prediction == -1

        if_count = int(if_flag.sum())
        overlap_rule = int((if_flag & rule).sum())
        overlap_statistical = int((if_flag & statistical).sum())

        rule_union = int((if_flag | rule).sum())
        stat_union = int((if_flag | statistical).sum())

        hybrid_votes = (
            df["rule_anomaly_flag"]
            + df["statistical_anomaly_flag"]
            + if_flag.astype(int)
        )
        hybrid_count = int((hybrid_votes >= 2).sum())

        rows.append(
            {
                "contamination": contamination,
                "if_anomaly_count": if_count,
                "if_anomaly_rate": if_count / total_cases * 100,
                "overlap_with_rule": overlap_rule,
                "overlap_with_statistical": overlap_statistical,
                "jaccard_if_vs_rule": (
                    overlap_rule / rule_union
                    if rule_union > 0
                    else np.nan
                ),
                "jaccard_if_vs_statistical": (
                    overlap_statistical / stat_union
                    if stat_union > 0
                    else np.nan
                ),
                "hybrid_anomaly_count": hybrid_count,
                "hybrid_anomaly_rate": hybrid_count / total_cases * 100,
            }
        )

    sensitivity_df = pd.DataFrame(rows)
    sensitivity_df.to_csv(IF_SENSITIVITY_FILE, index=False)
    return sensitivity_df


# ============================================================
# FIGURES
# ============================================================

def save_bar_chart(labels, values, title, xlabel, ylabel, output_path, rotation=0):
    """Save a simple matplotlib bar chart."""
    plt.figure(figsize=(10, 6))
    plt.bar(labels, values)
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.xticks(rotation=rotation, ha="right" if rotation else "center")
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()


def create_figures(
    summary_df: pd.DataFrame,
    overlap_df: pd.DataFrame,
    vote_distribution: pd.Series,
    sensitivity_df: pd.DataFrame,
    variant_df: pd.DataFrame,
) -> list[Path]:
    """Create requested PNG figures."""
    BASELINE_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    created_files = []

    detector_rates = summary_df[
        summary_df["metric"].isin(
            [
                "Rule-based",
                "Statistical IQR",
                "Isolation Forest",
                "Final Hybrid",
            ]
        )
    ]
    output_path = BASELINE_FIGURE_DIR / "detector_anomaly_rates.png"
    save_bar_chart(
        detector_rates["metric"],
        detector_rates["percentage"],
        "Detector Anomaly Rates",
        "Detector",
        "Anomaly rate (%)",
        output_path,
        rotation=20,
    )
    created_files.append(output_path)

    output_path = BASELINE_FIGURE_DIR / "detector_overlap.png"
    save_bar_chart(
        overlap_df["overlap_group"],
        overlap_df["case_count"],
        "Detector Overlap Groups",
        "Overlap group",
        "Case count",
        output_path,
        rotation=30,
    )
    created_files.append(output_path)

    output_path = BASELINE_FIGURE_DIR / "anomaly_vote_distribution.png"
    save_bar_chart(
        vote_distribution.index.astype(str),
        vote_distribution.values,
        "Anomaly Vote Distribution",
        "Number of detectors voting anomaly",
        "Case count",
        output_path,
    )
    created_files.append(output_path)

    output_path = BASELINE_FIGURE_DIR / "isolation_forest_sensitivity.png"
    plt.figure(figsize=(10, 6))
    plt.plot(
        sensitivity_df["contamination"],
        sensitivity_df["if_anomaly_rate"],
        marker="o",
        label="Isolation Forest anomaly rate",
    )
    plt.plot(
        sensitivity_df["contamination"],
        sensitivity_df["hybrid_anomaly_rate"],
        marker="o",
        label="Hybrid anomaly rate",
    )
    plt.title("Isolation Forest Sensitivity")
    plt.xlabel("Contamination")
    plt.ylabel("Anomaly rate (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    if not variant_df.empty:
        top_variants = variant_df.head(10).copy()
        output_path = BASELINE_FIGURE_DIR / "top_anomaly_variants.png"
        save_bar_chart(
            top_variants["variant"],
            top_variants["anomaly_cases"],
            "Top 10 Variants by Anomaly Case Count",
            "Process variant",
            "Anomaly case count",
            output_path,
            rotation=45,
        )
        created_files.append(output_path)

    return created_files


# ============================================================
# PRINT SUMMARY
# ============================================================

def print_final_summary(
    df: pd.DataFrame,
    summary_df: pd.DataFrame,
    vote_distribution: pd.Series,
    overlap_df: pd.DataFrame,
    agreement_df: pd.DataFrame,
    output_files: list[Path],
) -> None:
    """Print the requested terminal summary."""
    summary_lookup = summary_df.set_index("metric")

    print("\nANOMALY EVALUATION SUMMARY")
    print("=" * 70)
    print(f"Total cases: {len(df):,}")
    print(
        "Rule anomaly rate: "
        f"{summary_lookup.loc['Rule-based', 'percentage']:.2f}%"
    )
    print(
        "Statistical anomaly rate: "
        f"{summary_lookup.loc['Statistical IQR', 'percentage']:.2f}%"
    )
    print(
        "Isolation Forest anomaly rate: "
        f"{summary_lookup.loc['Isolation Forest', 'percentage']:.2f}%"
    )
    print(
        "Hybrid anomaly rate: "
        f"{summary_lookup.loc['Final Hybrid', 'percentage']:.2f}%"
    )

    print("\nVote distribution:")
    for vote_count in [0, 1, 2, 3]:
        print(f"{vote_count} detectors: {int(vote_distribution.loc[vote_count]):,}")

    print("\nDetector overlap:")
    overlap_lookup = overlap_df.set_index("overlap_group")["case_count"]
    labels = {
        "rule_only": "Rule only",
        "statistical_only": "Statistical only",
        "isolation_only": "Isolation only",
        "rule_statistical": "Rule + Statistical",
        "rule_isolation": "Rule + Isolation",
        "statistical_isolation": "Statistical + Isolation",
        "all_three": "All three",
    }
    print(f"None: {int(overlap_lookup.loc['none']):,}")
    for key, label in labels.items():
        print(f"{label}: {int(overlap_lookup.loc[key]):,}")

    print("\nPairwise agreement:")
    for _, row in agreement_df.iterrows():
        print(
            f"{row['detector_pair']}: "
            f"Jaccard={row['jaccard_similarity']:.4f}, "
            f"Cohen Kappa={row['cohen_kappa']:.4f}"
        )

    print("\nFiles created:")
    for output_file in output_files:
        print(f"  {output_file}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 70)
    print("ANOMALY EVALUATION")
    print("=" * 70)

    print("\n[1/8] Loading anomaly results...")
    df = load_anomaly_results()
    print(f"Loaded cases: {len(df):,}")

    print("\n[2/8] Calculating basic anomaly rates...")
    summary_df, vote_distribution, _ = calculate_basic_summary(df)

    print("\n[3/8] Calculating detector overlap...")
    overlap_df = calculate_detector_overlap(df)

    print("\n[4/8] Calculating pairwise agreement...")
    agreement_df = calculate_pairwise_agreement(df)

    print("\n[5/8] Analyzing variants, conformance, durations, and reasons...")
    variant_df = analyze_by_variant(df)
    fitness_band_df = analyze_by_fitness_band(df)
    duration_df = analyze_duration_comparison(df)
    reason_df = analyze_anomaly_reasons(df)
    manual_sample_df = create_manual_review_sample(df)

    print("\n[6/8] Running Isolation Forest sensitivity analysis...")
    sensitivity_df = run_isolation_forest_sensitivity(df)

    print("\n[7/8] Creating figures...")
    figure_files = create_figures(
        summary_df,
        overlap_df,
        vote_distribution,
        sensitivity_df,
        variant_df,
    )

    print("\n[8/8] Writing final summary...")
    output_files = [
        SUMMARY_FILE,
        OVERLAP_FILE,
        AGREEMENT_FILE,
    ]
    if not variant_df.empty:
        output_files.append(VARIANT_FILE)
    if not fitness_band_df.empty:
        output_files.append(FITNESS_BAND_FILE)
    if not duration_df.empty:
        output_files.append(DURATION_COMPARISON_FILE)
    if not reason_df.empty:
        output_files.append(REASON_FREQUENCY_FILE)
    if not manual_sample_df.empty:
        output_files.append(MANUAL_REVIEW_FILE)
    output_files.append(IF_SENSITIVITY_FILE)
    output_files.extend(figure_files)

    print_final_summary(
        df,
        summary_df,
        vote_distribution,
        overlap_df,
        agreement_df,
        output_files,
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
