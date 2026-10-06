from pathlib import Path
import sys

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, EXPERIMENT_DATA_DIR, MANUAL_REVIEW_DIR, REFINEMENT_FIGURE_DIR


INPUT_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"

REFINED_SENSITIVITY_FILE = EXPERIMENT_DATA_DIR / "refined_isolation_sensitivity.csv"
REFINED_OVERLAP_FILE = EXPERIMENT_DATA_DIR / "refined_detector_overlap.csv"
REFINEMENT_COMPARISON_FILE = EXPERIMENT_DATA_DIR / "refinement_comparison.csv"
MANUAL_REVIEW_FILE = MANUAL_REVIEW_DIR / "refinement_manual_review_sample.csv"

CONTAMINATIONS = [0.01, 0.03, 0.05, 0.08, 0.10, 0.15]
MANUAL_REVIEW_CONTAMINATIONS = [0.03, 0.05, 0.08, 0.10]

BASELINE_COLUMNS = {
    "rule_anomaly_flag": "baseline_rule_flag",
    "statistical_anomaly_flag": "baseline_statistical_flag",
    "isolation_forest_flag": "baseline_if_flag",
    "anomaly_vote_count": "baseline_vote_count",
    "anomaly_flag": "baseline_hybrid_flag",
}

IF_PERFORMANCE_FEATURES = [
    "event_count_from_log",
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
]

FALLBACK_EVENT_COUNT_FEATURE = "event_count"

MANUAL_REVIEW_COLUMNS = [
    "case_id",
    "variant",
    "trace_fitness",
    "missing_event_count",
    "reversed_order_flag",
    "event_count_from_log",
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
    "process_deviation_flag",
    "statistical_anomaly_flag",
    "experimental_if_flag",
    "refined_vote_count",
    "anomaly_reason",
]


# ============================================================
# LOAD BASELINE
# ============================================================

def load_baseline(input_file: Path = INPUT_FILE) -> pd.DataFrame:
    """Load baseline anomaly results without modifying baseline files."""
    df = pd.read_csv(input_file)
    required_columns = [
        "case_id",
        "trace_fitness",
        "missing_event_count",
        "reversed_order_flag",
        "statistical_anomaly_flag",
        "isolation_forest_flag",
        "anomaly_flag",
    ]
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(
            "anomaly_results.csv is missing required column(s): "
            + ", ".join(missing_columns)
        )

    for source_col, target_col in BASELINE_COLUMNS.items():
        if source_col in df.columns:
            df[target_col] = pd.to_numeric(df[source_col], errors="coerce").fillna(0)

    return df


# ============================================================
# REFINED PROCESS EVIDENCE
# ============================================================

def add_process_deviation_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Create conformance-only rule evidence and severity labels."""
    df = df.copy()
    df["trace_fitness"] = pd.to_numeric(df["trace_fitness"], errors="coerce").fillna(1.0)
    df["missing_event_count"] = pd.to_numeric(
        df["missing_event_count"],
        errors="coerce",
    ).fillna(0)
    df["reversed_order_flag"] = pd.to_numeric(
        df["reversed_order_flag"],
        errors="coerce",
    ).fillna(0)
    df["statistical_anomaly_flag"] = pd.to_numeric(
        df["statistical_anomaly_flag"],
        errors="coerce",
    ).fillna(0).astype(int)

    df["process_deviation_flag"] = (
        (df["missing_event_count"] > 0)
        | (df["reversed_order_flag"] > 0)
        | (df["trace_fitness"] < 1.0)
    ).astype(int)

    conditions = [
        (df["trace_fitness"] < 0.5) | (df["reversed_order_flag"] > 0),
        (df["trace_fitness"] >= 0.5) & (df["trace_fitness"] < 0.8),
        (df["trace_fitness"] >= 0.8) & (df["trace_fitness"] < 1.0),
    ]
    choices = ["Severe", "Significant", "Mild"]
    df["process_deviation_severity"] = np.select(
        conditions,
        choices,
        default="Normal",
    )
    return df


# ============================================================
# DECOUPLED ISOLATION FOREST
# ============================================================

def select_if_features(df: pd.DataFrame) -> list[str]:
    """Select performance-only features for the experimental IF model."""
    feature_candidates = IF_PERFORMANCE_FEATURES.copy()
    if "event_count_from_log" not in df.columns and FALLBACK_EVENT_COUNT_FEATURE in df.columns:
        feature_candidates[0] = FALLBACK_EVENT_COUNT_FEATURE

    features = []
    for feature in feature_candidates:
        if feature not in df.columns:
            continue
        numeric_values = pd.to_numeric(df[feature], errors="coerce")
        if numeric_values.notna().sum() >= 10:
            df[feature] = numeric_values
            features.append(feature)

    if not features:
        raise ValueError("No usable process-performance features for experimental IF.")

    return list(dict.fromkeys(features))


def prepare_feature_matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Apply median imputation and StandardScaler like the baseline pipeline."""
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    return scaler.fit_transform(imputer.fit_transform(df[features]))


def run_experimental_if(X_scaled: np.ndarray, contamination: float) -> np.ndarray:
    """Run one experimental Isolation Forest model in memory only."""
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    return (model.fit_predict(X_scaled) == -1).astype(int)


# ============================================================
# OVERLAP AND HYBRID METRICS
# ============================================================

def jaccard(first: pd.Series, second: pd.Series) -> float:
    """Calculate Jaccard similarity for binary anomaly flags."""
    first_bool = first.astype(bool)
    second_bool = second.astype(bool)
    union = (first_bool | second_bool).sum()
    if union == 0:
        return np.nan
    return (first_bool & second_bool).sum() / union


def calculate_overlap_groups(df: pd.DataFrame) -> dict[str, int]:
    """Calculate mutually exclusive refined detector-overlap groups."""
    process = df["process_deviation_flag"] == 1
    stat = df["statistical_anomaly_flag"] == 1
    if_flag = df["experimental_if_flag"] == 1

    return {
        "none": int((~process & ~stat & ~if_flag).sum()),
        "process_only": int((process & ~stat & ~if_flag).sum()),
        "statistical_only": int((~process & stat & ~if_flag).sum()),
        "if_only": int((~process & ~stat & if_flag).sum()),
        "process_statistical": int((process & stat & ~if_flag).sum()),
        "process_if": int((process & ~stat & if_flag).sum()),
        "statistical_if": int((~process & stat & if_flag).sum()),
        "all_three": int((process & stat & if_flag).sum()),
    }


def evaluate_contamination(
    df: pd.DataFrame,
    X_scaled: np.ndarray,
    contamination: float,
) -> tuple[dict, pd.DataFrame]:
    """Evaluate one contamination value and return metrics plus case-level flags."""
    case_df = df.copy()
    case_df["experimental_if_flag"] = run_experimental_if(X_scaled, contamination)
    case_df["refined_vote_count"] = (
        case_df["process_deviation_flag"]
        + case_df["statistical_anomaly_flag"]
        + case_df["experimental_if_flag"]
    )
    case_df["refined_hybrid_flag"] = (case_df["refined_vote_count"] >= 2).astype(int)

    overlap = calculate_overlap_groups(case_df)
    total_cases = len(case_df)
    if_count = int(case_df["experimental_if_flag"].sum())
    hybrid_count = int(case_df["refined_hybrid_flag"].sum())

    process = case_df["process_deviation_flag"]
    stat = case_df["statistical_anomaly_flag"]
    if_flag = case_df["experimental_if_flag"]

    metrics = {
        "contamination": contamination,
        "if_anomaly_count": if_count,
        "if_anomaly_rate": if_count / total_cases * 100,
        "process_if_both": int(((process == 1) & (if_flag == 1)).sum()),
        "process_only_vs_if": int(((process == 1) & (if_flag == 0)).sum()),
        "if_only_vs_process": int(((process == 0) & (if_flag == 1)).sum()),
        "jaccard_if_process": jaccard(if_flag, process),
        "statistical_if_both": int(((stat == 1) & (if_flag == 1)).sum()),
        "statistical_only_vs_if": int(((stat == 1) & (if_flag == 0)).sum()),
        "if_only_vs_statistical": int(((stat == 0) & (if_flag == 1)).sum()),
        "jaccard_if_statistical": jaccard(if_flag, stat),
        "vote_0_count": int((case_df["refined_vote_count"] == 0).sum()),
        "vote_1_count": int((case_df["refined_vote_count"] == 1).sum()),
        "vote_2_count": int((case_df["refined_vote_count"] == 2).sum()),
        "vote_3_count": int((case_df["refined_vote_count"] == 3).sum()),
        "refined_hybrid_count": hybrid_count,
        "refined_hybrid_rate": hybrid_count / total_cases * 100,
        **overlap,
    }

    return metrics, case_df


# ============================================================
# MANUAL REVIEW SUPPORT
# ============================================================

def choose_manual_review_cases(
    case_level_by_contamination: dict[float, pd.DataFrame],
    max_per_contamination: int = 30,
) -> pd.DataFrame:
    """Create diverse manual-review samples for selected contamination values."""
    samples = []
    priority_groups = ["if_only", "statistical_if", "process_if", "all_three"]

    for contamination in MANUAL_REVIEW_CONTAMINATIONS:
        case_df = case_level_by_contamination[contamination].copy()
        overlap = calculate_case_overlap_label(case_df)
        case_df["refined_overlap_group"] = overlap
        case_df["contamination"] = contamination

        selected_parts = []
        per_group_limit = max(1, max_per_contamination // len(priority_groups))

        for group_name in priority_groups:
            group_df = case_df[case_df["refined_overlap_group"] == group_name].copy()
            if group_df.empty:
                continue

            group_df = group_df.sort_values(
                [
                    "refined_vote_count",
                    "trace_fitness",
                    "total_cycle_time_days",
                ],
                ascending=[False, True, False],
            )
            selected_parts.append(group_df.head(per_group_limit))

        selected = (
            pd.concat(selected_parts, ignore_index=False)
            if selected_parts
            else pd.DataFrame()
        )

        if len(selected) < max_per_contamination:
            remaining = case_df.drop(index=selected.index, errors="ignore")
            remaining = remaining.sort_values(
                [
                    "refined_vote_count",
                    "trace_fitness",
                    "total_cycle_time_days",
                ],
                ascending=[False, True, False],
            )
            selected = pd.concat(
                [
                    selected,
                    remaining.head(max_per_contamination - len(selected)),
                ],
                ignore_index=False,
            )

        available_columns = [
            column for column in ["contamination", "refined_overlap_group"] + MANUAL_REVIEW_COLUMNS
            if column in selected.columns
        ]
        samples.append(
            selected[available_columns]
            .head(max_per_contamination)
            .reset_index(drop=True)
        )

    sample_df = pd.concat(samples, ignore_index=True)
    sample_df.to_csv(MANUAL_REVIEW_FILE, index=False)
    return sample_df


def calculate_case_overlap_label(df: pd.DataFrame) -> pd.Series:
    """Assign refined overlap-group label per case."""
    process = df["process_deviation_flag"] == 1
    stat = df["statistical_anomaly_flag"] == 1
    if_flag = df["experimental_if_flag"] == 1

    labels = np.select(
        [
            ~process & ~stat & ~if_flag,
            process & ~stat & ~if_flag,
            ~process & stat & ~if_flag,
            ~process & ~stat & if_flag,
            process & stat & ~if_flag,
            process & ~stat & if_flag,
            ~process & stat & if_flag,
            process & stat & if_flag,
        ],
        [
            "none",
            "process_only",
            "statistical_only",
            "if_only",
            "process_statistical",
            "process_if",
            "statistical_if",
            "all_three",
        ],
        default="unknown",
    )
    return pd.Series(labels, index=df.index)


# ============================================================
# OUTPUTS AND FIGURES
# ============================================================

def save_experiment_outputs(
    metrics_df: pd.DataFrame,
    baseline_summary: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Save sensitivity, overlap, and comparison tables."""
    metrics_df.to_csv(REFINED_SENSITIVITY_FILE, index=False)

    overlap_columns = [
        "contamination",
        "none",
        "process_only",
        "statistical_only",
        "if_only",
        "process_statistical",
        "process_if",
        "statistical_if",
        "all_three",
    ]
    overlap_df = metrics_df[overlap_columns].copy()
    overlap_df.to_csv(REFINED_OVERLAP_FILE, index=False)

    comparison_df = metrics_df[
        [
            "contamination",
            "if_anomaly_count",
            "if_anomaly_rate",
            "refined_hybrid_count",
            "refined_hybrid_rate",
            "jaccard_if_process",
            "jaccard_if_statistical",
        ]
    ].copy()
    comparison_df["baseline_if_count"] = baseline_summary["baseline_if_count"]
    comparison_df["baseline_if_rate"] = baseline_summary["baseline_if_rate"]
    comparison_df["baseline_hybrid_count"] = baseline_summary["baseline_hybrid_count"]
    comparison_df["baseline_hybrid_rate"] = baseline_summary["baseline_hybrid_rate"]
    comparison_df.to_csv(REFINEMENT_COMPARISON_FILE, index=False)

    return overlap_df, comparison_df


def create_figures(metrics_df: pd.DataFrame, baseline_summary: dict) -> list[Path]:
    """Create refinement experiment figures."""
    REFINEMENT_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    created_files = []

    output_path = REFINEMENT_FIGURE_DIR / "baseline_vs_refined_hybrid.png"
    plt.figure(figsize=(10, 6))
    plt.axhline(
        baseline_summary["baseline_if_rate"],
        linestyle="--",
        label="Baseline IF rate",
    )
    plt.axhline(
        baseline_summary["baseline_hybrid_rate"],
        linestyle="--",
        label="Baseline Hybrid rate",
    )
    plt.plot(
        metrics_df["contamination"],
        metrics_df["if_anomaly_rate"],
        marker="o",
        label="Refined IF rate",
    )
    plt.plot(
        metrics_df["contamination"],
        metrics_df["refined_hybrid_rate"],
        marker="o",
        label="Refined Hybrid rate",
    )
    plt.title("Baseline vs Refined Hybrid Anomaly Rates")
    plt.xlabel("Experimental IF contamination")
    plt.ylabel("Anomaly rate (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    output_path = REFINEMENT_FIGURE_DIR / "refined_detector_unique_contribution.png"
    overlap_group_cols = [
        "process_only",
        "statistical_only",
        "if_only",
        "process_statistical",
        "process_if",
        "statistical_if",
        "all_three",
    ]
    plt.figure(figsize=(12, 7))
    bottom = np.zeros(len(metrics_df))
    x_labels = metrics_df["contamination"].astype(str)
    for column in overlap_group_cols:
        plt.bar(x_labels, metrics_df[column], bottom=bottom, label=column)
        bottom += metrics_df[column].to_numpy()
    plt.title("Refined Detector Unique Contribution")
    plt.xlabel("Experimental IF contamination")
    plt.ylabel("Case count")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    output_path = REFINEMENT_FIGURE_DIR / "refined_contamination_sensitivity.png"
    plt.figure(figsize=(10, 6))
    plt.plot(
        metrics_df["contamination"],
        metrics_df["if_anomaly_rate"],
        marker="o",
        label="Experimental IF rate",
    )
    plt.plot(
        metrics_df["contamination"],
        metrics_df["refined_hybrid_rate"],
        marker="o",
        label="Refined Hybrid rate",
    )
    plt.title("Refined Contamination Sensitivity")
    plt.xlabel("Experimental IF contamination")
    plt.ylabel("Anomaly rate (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    return created_files


# ============================================================
# SUMMARY
# ============================================================

def print_summary(
    metrics_df: pd.DataFrame,
    baseline_summary: dict,
    if_features: list[str],
    output_files: list[Path],
) -> None:
    """Print required experiment summary."""
    print("\nREFINED HYBRID ANOMALY EXPERIMENT")
    print("=" * 70)
    print("Baseline:")
    print(f"IF anomalies: {baseline_summary['baseline_if_count']:,} "
          f"({baseline_summary['baseline_if_rate']:.2f}%)")
    print(f"Hybrid anomalies: {baseline_summary['baseline_hybrid_count']:,} "
          f"({baseline_summary['baseline_hybrid_rate']:.2f}%)")
    print("\nExperimental IF features:")
    for feature in if_features:
        print(f"  - {feature}")

    display_columns = [
        "contamination",
        "if_anomaly_count",
        "if_anomaly_rate",
        "refined_hybrid_count",
        "refined_hybrid_rate",
        "process_only",
        "statistical_only",
        "if_only",
        "process_statistical",
        "process_if",
        "statistical_if",
        "all_three",
        "jaccard_if_process",
        "jaccard_if_statistical",
    ]

    print("\nContamination results:")
    print(metrics_df[display_columns].round(4).to_string(index=False))

    baseline_if_rate = baseline_summary["baseline_if_rate"]
    closest_row = metrics_df.iloc[
        (metrics_df["if_anomaly_rate"] - baseline_if_rate).abs().argsort().iloc[0]
    ]

    print("\nNotes:")
    print(
        "- The refined IF is more decoupled because it excludes trace_fitness, "
        "missing_event_count, and reversed_order_flag."
    )
    print(
        "- IF-only is still zero at conservative contaminations, but becomes visible "
        "as contamination increases; it ranges from "
        f"{int(metrics_df['if_only'].min()):,} to {int(metrics_df['if_only'].max()):,} cases."
    )
    print(
        "- Refined Hybrid is not simply equal to IF; the gap depends on contamination "
        "and should be checked in manual validation."
    )
    print(
        "- For manual validation, compare 0.03, 0.05, 0.08, and 0.10 because they "
        "span conservative to baseline-like IF rates without changing the baseline."
    )
    print(
        "- The contamination closest to the baseline IF rate in this experiment is "
        f"{closest_row['contamination']:.2f}, but this is not selected as final."
    )

    print("\nFiles created:")
    for output_file in output_files:
        print(f"  {output_file}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 70)
    print("REFINED HYBRID ANOMALY EXPERIMENT")
    print("=" * 70)

    print("\n[1/6] Loading baseline anomaly results...")
    df = load_baseline()
    df = add_process_deviation_evidence(df)

    baseline_summary = {
        "baseline_if_count": int(df["baseline_if_flag"].sum()),
        "baseline_if_rate": df["baseline_if_flag"].mean() * 100,
        "baseline_hybrid_count": int(df["baseline_hybrid_flag"].sum()),
        "baseline_hybrid_rate": df["baseline_hybrid_flag"].mean() * 100,
    }

    print("\n[2/6] Preparing decoupled performance features...")
    if_features = select_if_features(df)
    X_scaled = prepare_feature_matrix(df, if_features)

    print("\n[3/6] Running contamination experiments...")
    metrics = []
    case_level_by_contamination = {}
    for contamination in CONTAMINATIONS:
        contamination_metrics, case_df = evaluate_contamination(
            df,
            X_scaled,
            contamination,
        )
        metrics.append(contamination_metrics)
        case_level_by_contamination[contamination] = case_df

    metrics_df = pd.DataFrame(metrics)

    print("\n[4/6] Creating manual-review sample...")
    manual_sample_df = choose_manual_review_cases(case_level_by_contamination)

    print("\n[5/6] Saving CSV outputs and figures...")
    _, _ = save_experiment_outputs(metrics_df, baseline_summary)
    figure_files = create_figures(metrics_df, baseline_summary)

    print("\n[6/6] Printing summary...")
    output_files = [
        REFINED_SENSITIVITY_FILE,
        REFINED_OVERLAP_FILE,
        REFINEMENT_COMPARISON_FILE,
        MANUAL_REVIEW_FILE,
    ] + figure_files

    print_summary(
        metrics_df,
        baseline_summary,
        if_features,
        output_files,
    )
    print(f"\nManual-review sample cases: {len(manual_sample_df):,}")
    print("\nDone.")


if __name__ == "__main__":
    main()
