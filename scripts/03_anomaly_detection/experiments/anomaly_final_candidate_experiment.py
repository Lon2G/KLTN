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

from project_paths import (
    ANOMALY_DATA_DIR,
    EXPERIMENT_DATA_DIR,
    FINAL_CANDIDATE_FIGURE_DIR,
    MANUAL_REVIEW_DIR,
    PROCESSED_DATA_DIR,
)


ANOMALY_RESULTS_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"
EVENT_LOG_FILE = PROCESSED_DATA_DIR / "event_log.csv"
PREVIOUS_REFINEMENT_FILE = EXPERIMENT_DATA_DIR / "refined_isolation_sensitivity.csv"

SENSITIVITY_FILE = EXPERIMENT_DATA_DIR / "final_candidate_sensitivity.csv"
OVERLAP_FILE = EXPERIMENT_DATA_DIR / "final_candidate_overlap.csv"
COMPARISON_FILE = EXPERIMENT_DATA_DIR / "final_candidate_comparison.csv"
MANUAL_VALIDATION_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation.csv"

CONTAMINATIONS = [0.03, 0.05, 0.08, 0.10]
MANUAL_VALIDATION_CONTAMINATIONS = [0.05, 0.08]

CANDIDATE_IF_FEATURES = [
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
]

FORBIDDEN_IF_FEATURES = {
    "event_count",
    "event_count_from_log",
    "trace_fitness",
    "missing_event_count",
    "reversed_order_flag",
    "total_cycle_time_days",
}

MANUAL_GROUPS = [
    "IF_only",
    "process_IF",
    "statistical_IF",
    "all_three",
    "process_only",
    "statistical_only",
]


# ============================================================
# LOAD INPUTS
# ============================================================

def load_anomaly_results() -> pd.DataFrame:
    """Load baseline anomaly results without modifying any baseline output."""
    df = pd.read_csv(ANOMALY_RESULTS_FILE)
    required = [
        "case_id",
        "trace_fitness",
        "missing_event_count",
        "reversed_order_flag",
        "statistical_anomaly_flag",
    ] + CANDIDATE_IF_FEATURES

    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(
            "anomaly_results.csv is missing required column(s): "
            + ", ".join(missing)
        )

    df["case_id"] = df["case_id"].astype(str)
    return df


def load_event_sequences() -> pd.DataFrame:
    """Read event_log.csv and build event sequence/timestamp strings per case."""
    event_log = pd.read_csv(EVENT_LOG_FILE)
    required = ["case_id", "activity", "timestamp"]
    missing = [column for column in required if column not in event_log.columns]
    if missing:
        raise ValueError(
            "event_log.csv is missing required column(s): "
            + ", ".join(missing)
        )

    event_log = event_log[required].copy()
    event_log["case_id"] = event_log["case_id"].astype(str)
    event_log["timestamp"] = pd.to_datetime(event_log["timestamp"], errors="coerce")
    event_log = event_log.sort_values(["case_id", "timestamp"], kind="mergesort")
    event_log["timestamp_text"] = event_log["timestamp"].dt.strftime("%Y-%m-%d %H:%M:%S")

    sequence_df = (
        event_log.groupby("case_id")
        .agg(
            event_sequence=("activity", lambda values: " -> ".join(values.astype(str))),
            event_timestamps=(
                "timestamp_text",
                lambda values: " -> ".join(values.fillna("NaT").astype(str)),
            ),
        )
        .reset_index()
    )
    return sequence_df


# ============================================================
# PROCESS EVIDENCE
# ============================================================

def add_process_evidence(df: pd.DataFrame) -> pd.DataFrame:
    """Create process-deviation evidence without using duration features."""
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
    return df


# ============================================================
# FINAL-CANDIDATE ISOLATION FOREST
# ============================================================

def validate_candidate_features(df: pd.DataFrame) -> list[str]:
    """Ensure the candidate IF uses only the three allowed duration features."""
    available = []
    for feature in CANDIDATE_IF_FEATURES:
        numeric_values = pd.to_numeric(df[feature], errors="coerce")
        if numeric_values.notna().sum() < 10:
            raise ValueError(f"Feature has too few non-null values: {feature}")
        df[feature] = numeric_values
        available.append(feature)

    leakage_features = sorted(FORBIDDEN_IF_FEATURES.intersection(available))
    if leakage_features:
        raise ValueError(
            "Forbidden feature(s) selected for candidate IF: "
            + ", ".join(leakage_features)
        )

    return available


def prepare_feature_matrix(df: pd.DataFrame, features: list[str]) -> np.ndarray:
    """Coerce numeric values, median-impute, and standardize candidate IF features."""
    imputer = SimpleImputer(strategy="median")
    scaler = StandardScaler()
    return scaler.fit_transform(imputer.fit_transform(df[features]))


def run_candidate_if(X_scaled: np.ndarray, contamination: float) -> np.ndarray:
    """Run a candidate Isolation Forest model in memory only."""
    model = IsolationForest(
        n_estimators=300,
        contamination=contamination,
        random_state=42,
        n_jobs=-1,
    )
    return (model.fit_predict(X_scaled) == -1).astype(int)


# ============================================================
# METRICS
# ============================================================

def jaccard(first: pd.Series, second: pd.Series) -> float:
    """Calculate Jaccard similarity for binary flags."""
    first_bool = first.astype(bool)
    second_bool = second.astype(bool)
    union = (first_bool | second_bool).sum()
    if union == 0:
        return np.nan
    return (first_bool & second_bool).sum() / union


def add_overlap_group(case_df: pd.DataFrame) -> pd.DataFrame:
    """Assign mutually exclusive overlap group labels."""
    process = case_df["process_deviation_flag"] == 1
    stat = case_df["statistical_anomaly_flag"] == 1
    if_flag = case_df["candidate_if_flag"] == 1

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
            "IF_only",
            "process_statistical",
            "process_IF",
            "statistical_IF",
            "all_three",
        ],
        default="unknown",
    )
    case_df = case_df.copy()
    case_df["candidate_overlap_group"] = labels
    return case_df


def evaluate_contamination(
    df: pd.DataFrame,
    X_scaled: np.ndarray,
    contamination: float,
) -> tuple[dict, pd.DataFrame]:
    """Evaluate one contamination value for the final-candidate IF."""
    case_df = df.copy()
    case_df["candidate_if_flag"] = run_candidate_if(X_scaled, contamination)
    case_df["final_candidate_vote_count"] = (
        case_df["process_deviation_flag"]
        + case_df["statistical_anomaly_flag"]
        + case_df["candidate_if_flag"]
    )
    case_df["candidate_hybrid_flag"] = (
        case_df["final_candidate_vote_count"] >= 2
    ).astype(int)
    case_df = add_overlap_group(case_df)

    total_cases = len(case_df)
    overlap_counts = (
        case_df["candidate_overlap_group"]
        .value_counts()
        .reindex(
            [
                "none",
                "process_only",
                "statistical_only",
                "IF_only",
                "process_statistical",
                "process_IF",
                "statistical_IF",
                "all_three",
            ],
            fill_value=0,
        )
    )

    vote_counts = (
        case_df["final_candidate_vote_count"]
        .value_counts()
        .reindex([0, 1, 2, 3], fill_value=0)
    )

    process = case_df["process_deviation_flag"]
    stat = case_df["statistical_anomaly_flag"]
    if_flag = case_df["candidate_if_flag"]
    if_count = int(if_flag.sum())
    hybrid_count = int(case_df["candidate_hybrid_flag"].sum())

    metrics = {
        "contamination": contamination,
        "if_anomaly_count": if_count,
        "if_anomaly_rate": if_count / total_cases * 100,
        "hybrid_count": hybrid_count,
        "hybrid_rate": hybrid_count / total_cases * 100,
        "jaccard_if_process": jaccard(if_flag, process),
        "kappa_if_process": cohen_kappa_score(if_flag, process),
        "jaccard_if_statistical": jaccard(if_flag, stat),
        "kappa_if_statistical": cohen_kappa_score(if_flag, stat),
        "vote_0_count": int(vote_counts.loc[0]),
        "vote_1_count": int(vote_counts.loc[1]),
        "vote_2_count": int(vote_counts.loc[2]),
        "vote_3_count": int(vote_counts.loc[3]),
    }
    metrics.update({group: int(count) for group, count in overlap_counts.items()})

    return metrics, case_df


# ============================================================
# PREVIOUS REFINEMENT COMPARISON
# ============================================================

def build_comparison(metrics_df: pd.DataFrame) -> pd.DataFrame:
    """Compare duration-only candidate IF with previous refinement if available."""
    comparison_df = metrics_df[
        [
            "contamination",
            "if_anomaly_count",
            "if_anomaly_rate",
            "hybrid_count",
            "hybrid_rate",
            "IF_only",
            "jaccard_if_process",
            "jaccard_if_statistical",
            "statistical_IF",
            "process_IF",
            "all_three",
        ]
    ].copy()
    comparison_df = comparison_df.rename(
        columns={
            "if_anomaly_count": "candidate_if_count",
            "if_anomaly_rate": "candidate_if_rate",
            "hybrid_count": "candidate_hybrid_count",
            "hybrid_rate": "candidate_hybrid_rate",
            "IF_only": "candidate_if_only",
            "jaccard_if_process": "candidate_jaccard_if_process",
            "jaccard_if_statistical": "candidate_jaccard_if_statistical",
            "statistical_IF": "candidate_statistical_if",
            "process_IF": "candidate_process_if",
            "all_three": "candidate_all_three",
        }
    )

    if not PREVIOUS_REFINEMENT_FILE.exists():
        comparison_df["previous_refinement_available"] = False
        return comparison_df

    previous_df = pd.read_csv(PREVIOUS_REFINEMENT_FILE)
    previous_columns = [
        "contamination",
        "if_anomaly_count",
        "if_anomaly_rate",
        "refined_hybrid_count",
        "refined_hybrid_rate",
        "if_only",
        "jaccard_if_process",
        "jaccard_if_statistical",
        "statistical_if",
        "process_if",
        "all_three",
    ]
    previous_df = previous_df[
        [column for column in previous_columns if column in previous_df.columns]
    ].copy()
    previous_df = previous_df.rename(
        columns={
            "if_anomaly_count": "previous_if_count",
            "if_anomaly_rate": "previous_if_rate",
            "refined_hybrid_count": "previous_hybrid_count",
            "refined_hybrid_rate": "previous_hybrid_rate",
            "if_only": "previous_if_only",
            "jaccard_if_process": "previous_jaccard_if_process",
            "jaccard_if_statistical": "previous_jaccard_if_statistical",
            "statistical_if": "previous_statistical_if",
            "process_if": "previous_process_if",
            "all_three": "previous_all_three",
        }
    )

    comparison_df = comparison_df.merge(
        previous_df,
        on="contamination",
        how="left",
    )
    comparison_df["previous_refinement_available"] = True
    comparison_df["if_only_delta"] = (
        comparison_df["candidate_if_only"] - comparison_df["previous_if_only"]
    )
    comparison_df["jaccard_process_delta"] = (
        comparison_df["candidate_jaccard_if_process"]
        - comparison_df["previous_jaccard_if_process"]
    )
    comparison_df["jaccard_statistical_delta"] = (
        comparison_df["candidate_jaccard_if_statistical"]
        - comparison_df["previous_jaccard_if_statistical"]
    )
    comparison_df["hybrid_minus_if_count"] = (
        comparison_df["candidate_hybrid_count"] - comparison_df["candidate_if_count"]
    )
    return comparison_df


# ============================================================
# MANUAL VALIDATION SAMPLE
# ============================================================

def take_diverse_group_sample(group_df: pd.DataFrame, n: int) -> pd.DataFrame:
    """Take a small sample with variant diversity where possible."""
    if group_df.empty or n <= 0:
        return pd.DataFrame(columns=group_df.columns)

    sorted_group = group_df.sort_values(
        [
            "final_candidate_vote_count",
            "trace_fitness",
            "total_cycle_time_days",
        ],
        ascending=[False, True, False],
    )

    if "variant" not in sorted_group.columns:
        return sorted_group.head(n)

    selected_parts = []
    remaining = sorted_group.copy()
    while len(selected_parts) < n and not remaining.empty:
        one_per_variant = remaining.groupby("variant", dropna=False).head(1)
        remaining = remaining.drop(index=one_per_variant.index)
        selected_parts.append(one_per_variant)
        if sum(len(part) for part in selected_parts) >= n:
            break

    return pd.concat(selected_parts, ignore_index=False).head(n)


def create_manual_validation_dataset(
    case_level_by_contamination: dict[float, pd.DataFrame],
) -> pd.DataFrame:
    """Create manual-validation dataset with event sequences and empty reviewer fields."""
    event_sequences = load_event_sequences()
    samples = []
    max_per_contamination = 50
    per_group = max(1, max_per_contamination // len(MANUAL_GROUPS))

    for contamination in MANUAL_VALIDATION_CONTAMINATIONS:
        case_df = case_level_by_contamination[contamination].copy()
        case_df["contamination"] = contamination

        selected_parts = []
        for group in MANUAL_GROUPS:
            group_df = case_df[case_df["candidate_overlap_group"] == group]
            selected_parts.append(take_diverse_group_sample(group_df, per_group))

        selected = pd.concat(selected_parts, ignore_index=False)

        if len(selected) < max_per_contamination:
            remaining = case_df.drop(index=selected.index, errors="ignore")
            remaining = remaining.sort_values(
                [
                    "final_candidate_vote_count",
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

        selected = selected.head(max_per_contamination).reset_index(drop=True)
        samples.append(selected)

    manual_df = pd.concat(samples, ignore_index=True)
    manual_df = manual_df.merge(event_sequences, on="case_id", how="left")

    manual_df["reviewer_label"] = ""
    manual_df["reviewer_confidence"] = ""
    manual_df["reviewer_reason"] = ""
    manual_df["reviewer_notes"] = ""

    preferred_columns = [
        "contamination",
        "candidate_overlap_group",
        "case_id",
        "variant",
        "trace_fitness",
        "missing_event_count",
        "reversed_order_flag",
        "purchased_to_approved_days",
        "approved_to_carrier_days",
        "carrier_to_delivered_days",
        "total_cycle_time_days",
        "process_deviation_flag",
        "statistical_anomaly_flag",
        "candidate_if_flag",
        "final_candidate_vote_count",
        "candidate_hybrid_flag",
        "anomaly_reason",
        "event_sequence",
        "event_timestamps",
        "reviewer_label",
        "reviewer_confidence",
        "reviewer_reason",
        "reviewer_notes",
    ]
    output_columns = [column for column in preferred_columns if column in manual_df.columns]
    manual_df = manual_df[output_columns]
    manual_df.to_csv(MANUAL_VALIDATION_FILE, index=False)
    return manual_df


# ============================================================
# OUTPUTS AND FIGURES
# ============================================================

def save_outputs(metrics_df: pd.DataFrame, comparison_df: pd.DataFrame) -> pd.DataFrame:
    """Save final-candidate CSV outputs."""
    metrics_df.to_csv(SENSITIVITY_FILE, index=False)

    overlap_columns = [
        "contamination",
        "none",
        "process_only",
        "statistical_only",
        "IF_only",
        "process_statistical",
        "process_IF",
        "statistical_IF",
        "all_three",
    ]
    overlap_df = metrics_df[overlap_columns].copy()
    overlap_df.to_csv(OVERLAP_FILE, index=False)

    comparison_df.to_csv(COMPARISON_FILE, index=False)
    return overlap_df


def create_figures(metrics_df: pd.DataFrame, comparison_df: pd.DataFrame) -> list[Path]:
    """Create final-candidate figures."""
    FINAL_CANDIDATE_FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    created_files = []

    output_path = FINAL_CANDIDATE_FIGURE_DIR / "final_candidate_sensitivity.png"
    plt.figure(figsize=(10, 6))
    plt.plot(
        metrics_df["contamination"],
        metrics_df["if_anomaly_rate"],
        marker="o",
        label="Duration-only IF rate",
    )
    plt.plot(
        metrics_df["contamination"],
        metrics_df["hybrid_rate"],
        marker="o",
        label="Candidate Hybrid rate",
    )
    plt.title("Final-Candidate Sensitivity")
    plt.xlabel("Contamination")
    plt.ylabel("Anomaly rate (%)")
    plt.legend()
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    output_path = FINAL_CANDIDATE_FIGURE_DIR / "overlap_comparison.png"
    overlap_columns = [
        "process_only",
        "statistical_only",
        "IF_only",
        "process_statistical",
        "process_IF",
        "statistical_IF",
        "all_three",
    ]
    plt.figure(figsize=(12, 7))
    bottom = np.zeros(len(metrics_df))
    x_labels = metrics_df["contamination"].astype(str)
    for column in overlap_columns:
        plt.bar(x_labels, metrics_df[column], bottom=bottom, label=column)
        bottom += metrics_df[column].to_numpy()
    plt.title("Final-Candidate Detector Overlap")
    plt.xlabel("Contamination")
    plt.ylabel("Case count")
    plt.legend(fontsize=8)
    plt.tight_layout()
    plt.savefig(output_path, dpi=220)
    plt.close()
    created_files.append(output_path)

    output_path = FINAL_CANDIDATE_FIGURE_DIR / "previous_vs_duration_only_if.png"
    plt.figure(figsize=(10, 6))
    plt.plot(
        comparison_df["contamination"],
        comparison_df["candidate_if_rate"],
        marker="o",
        label="Duration-only IF rate",
    )
    if "previous_if_rate" in comparison_df.columns:
        plt.plot(
            comparison_df["contamination"],
            comparison_df["previous_if_rate"],
            marker="o",
            label="Previous refinement IF rate",
        )
    plt.plot(
        comparison_df["contamination"],
        comparison_df["candidate_hybrid_rate"],
        marker="o",
        label="Duration-only Hybrid rate",
    )
    if "previous_hybrid_rate" in comparison_df.columns:
        plt.plot(
            comparison_df["contamination"],
            comparison_df["previous_hybrid_rate"],
            marker="o",
            label="Previous refinement Hybrid rate",
        )
    plt.title("Previous Refinement vs Duration-Only IF")
    plt.xlabel("Contamination")
    plt.ylabel("Rate (%)")
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
    comparison_df: pd.DataFrame,
    output_files: list[Path],
) -> None:
    """Print required terminal summary and short interpretation."""
    display_columns = [
        "contamination",
        "if_anomaly_count",
        "if_anomaly_rate",
        "hybrid_count",
        "hybrid_rate",
        "process_only",
        "statistical_only",
        "IF_only",
        "process_statistical",
        "process_IF",
        "statistical_IF",
        "all_three",
        "jaccard_if_process",
        "kappa_if_process",
        "jaccard_if_statistical",
        "kappa_if_statistical",
    ]

    print("\nFINAL-CANDIDATE ISOLATION FOREST EXPERIMENT")
    print("=" * 70)
    print(metrics_df[display_columns].round(4).to_string(index=False))

    print("\nInterpretation:")
    if "previous_jaccard_if_process" in comparison_df.columns:
        jaccard_delta = comparison_df[
            [
                "contamination",
                "jaccard_process_delta",
                "if_only_delta",
                "jaccard_statistical_delta",
                "hybrid_minus_if_count",
            ]
        ].round(4)
        print("\nCompared with previous refinement:")
        print(jaccard_delta.to_string(index=False))

        process_more_independent = (
            comparison_df["jaccard_process_delta"].dropna().mean() < 0
        )
        if process_more_independent:
            print(
                "- Removing event_count_from_log reduced average Jaccard with "
                "the Process detector, so the IF is more independent from process evidence."
            )
        else:
            print(
                "- Removing event_count_from_log did not reduce average Jaccard with "
                "the Process detector in this run."
            )
    else:
        print("- Previous refinement file was not found, so only candidate results were saved.")

    if_only_min = int(metrics_df["IF_only"].min())
    if_only_max = int(metrics_df["IF_only"].max())
    print(
        f"- IF-only ranges from {if_only_min:,} to {if_only_max:,} cases "
        "across tested contaminations."
    )

    row_005 = metrics_df[metrics_df["contamination"] == 0.05].iloc[0]
    row_008 = metrics_df[metrics_df["contamination"] == 0.08].iloc[0]
    print(
        "- At 0.05, IF rate is "
        f"{row_005['if_anomaly_rate']:.2f}% and Hybrid rate is "
        f"{row_005['hybrid_rate']:.2f}%."
    )
    print(
        "- At 0.08, IF rate is "
        f"{row_008['if_anomaly_rate']:.2f}% and Hybrid rate is "
        f"{row_008['hybrid_rate']:.2f}%, with more Statistical+IF overlap."
    )
    print("- No final contamination is selected in this experiment.")

    print("\nFiles created:")
    for output_file in output_files:
        print(f"  {output_file}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 70)
    print("FINAL-CANDIDATE ISOLATION FOREST EXPERIMENT")
    print("=" * 70)

    print("\n[1/6] Loading anomaly results...")
    df = load_anomaly_results()
    df = add_process_evidence(df)

    print("\n[2/6] Preparing duration-only IF features...")
    features = validate_candidate_features(df)
    X_scaled = prepare_feature_matrix(df, features)
    print("Candidate IF features:")
    for feature in features:
        print(f"  - {feature}")

    print("\n[3/6] Running contamination experiments...")
    metrics = []
    case_level_by_contamination = {}
    for contamination in CONTAMINATIONS:
        row, case_df = evaluate_contamination(df, X_scaled, contamination)
        metrics.append(row)
        case_level_by_contamination[contamination] = case_df

    metrics_df = pd.DataFrame(metrics)

    print("\n[4/6] Comparing with previous refinement...")
    comparison_df = build_comparison(metrics_df)

    print("\n[5/6] Creating manual validation dataset...")
    manual_df = create_manual_validation_dataset(case_level_by_contamination)

    print("\n[6/6] Saving outputs and figures...")
    save_outputs(metrics_df, comparison_df)
    figure_files = create_figures(metrics_df, comparison_df)

    output_files = [
        SENSITIVITY_FILE,
        OVERLAP_FILE,
        COMPARISON_FILE,
        MANUAL_VALIDATION_FILE,
    ] + figure_files

    print_summary(metrics_df, comparison_df, output_files)
    print(f"\nManual validation rows: {len(manual_df):,}")
    print("\nDone.")


if __name__ == "__main__":
    main()
