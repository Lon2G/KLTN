"""Compare saved baseline and Auto Validation outputs without treating either as truth."""

import hashlib
from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, REPORT_DIR


BASELINE_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"
AUTO_FILE = ANOMALY_DATA_DIR / "auto_validation_results.csv"
REPORT_FILE = REPORT_DIR / "auto_validation_evaluation_report.md"

DURATION_FEATURES = [
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]
DETECTORS = {
    "Rule-based": "rule_anomaly_flag",
    "Statistical IQR": "statistical_anomaly_flag",
    "Isolation Forest": "isolation_forest_flag",
    "Final Hybrid": "anomaly_flag",
}
VOTE_LEVELS = {
    0: "Normal", 1: "Warning", 2: "Anomaly", 3: "High-confidence anomaly",
}
BASELINE_CATEGORIES = ["Normal", "Warning", "Anomaly"]
AUTO_LABELS = ["Normal", "Suspicious", "Anomaly"]
LABEL_ALIGNMENT = dict(zip(BASELINE_CATEGORIES, AUTO_LABELS))
AUTO_COUNTS = [
    "auto_process_evidence_count",
    "auto_long_duration_count",
    "auto_severe_long_duration_count",
    "auto_short_duration_review_count",
    "auto_near_long_duration_review_count",
]
BASELINE_COLUMNS = [
    "case_id", "variant", "trace_fitness", "missing_event_count",
    "reversed_order_flag", *DURATION_FEATURES, *DETECTORS.values(),
    "anomaly_vote_count", "anomaly_level", "anomaly_reason",
]
AUTO_COLUMNS = [
    "auto_validation_label", "auto_validation_confidence",
    "auto_validation_reason", "auto_validation_evidence", *AUTO_COUNTS,
]
EVIDENCE_TAGS = [
    "missing_event", "reversed_order", "low_trace_fitness", "negative_duration",
    "severe_long_duration", "long_duration", "short_duration_review_signal",
    "near_long_duration_review_signal", "none",
]


def validate_table(df: pd.DataFrame, required: list[str], name: str) -> None:
    missing = sorted(set(required) - set(df.columns))
    if missing:
        raise ValueError(f"{name}: missing column(s): {', '.join(missing)}")
    if df.empty:
        raise ValueError(f"{name}: no cases to evaluate")
    if df["case_id"].isna().any() or df["case_id"].astype(str).str.strip().eq("").any():
        raise ValueError(f"{name}: null or blank case_id")
    if df["case_id"].duplicated().any():
        raise ValueError(f"{name}: duplicate case_id; comparison requires one row per case")


def validate_values(df: pd.DataFrame) -> None:
    """Reject invalid evidence instead of filling missing values with normal defaults."""
    for column in [*DETECTORS.values(), "reversed_order_flag"]:
        if not df[column].isin([0, 1]).all():
            raise ValueError(f"Invalid binary flag: {column}")
    votes = df[list(DETECTORS.values())[:3]].sum(axis=1)
    if not df["anomaly_vote_count"].eq(votes).all():
        raise ValueError("Baseline vote count does not match detector flags")
    if not df["anomaly_flag"].eq(votes.ge(2).astype(int)).all():
        raise ValueError("Baseline anomaly_flag does not match the two-vote policy")
    if not df["anomaly_level"].eq(votes.map(VOTE_LEVELS)).all():
        raise ValueError("Baseline anomaly_level does not match vote count")
    for column, allowed in [
        ("auto_validation_label", AUTO_LABELS),
        ("auto_validation_confidence", ["Low", "Medium", "High"]),
    ]:
        if not df[column].isin(allowed).all():
            raise ValueError(f"Missing or unsupported value in {column}")
    for column in ["variant", "anomaly_reason", "auto_validation_reason", "auto_validation_evidence"]:
        if df[column].isna().any() or df[column].astype(str).str.strip().eq("").any():
            raise ValueError(f"Missing text in {column}")
    for column in ["missing_event_count", *AUTO_COUNTS]:
        values = pd.to_numeric(df[column], errors="raise")
        if not (np.isfinite(values) & values.ge(0) & values.mod(1).eq(0)).all():
            raise ValueError(f"Invalid non-negative integer count: {column}")
    fitness = pd.to_numeric(df["trace_fitness"], errors="raise")
    if not fitness.between(0, 1).all():
        raise ValueError("Invalid or missing trace_fitness")
    for column in DURATION_FEATURES:
        values = pd.to_numeric(df[column], errors="raise")
        if not np.isfinite(values.dropna()).all():
            raise ValueError(f"Non-finite duration: {column}")
    tokens = df["auto_validation_evidence"].str.split(";").explode().str.strip()
    if not tokens.isin(EVIDENCE_TAGS).all():
        raise ValueError("Unsupported Auto Validation evidence tag")


def build_comparison(baseline: pd.DataFrame, auto: pd.DataFrame) -> pd.DataFrame:
    validate_table(baseline, BASELINE_COLUMNS, "Baseline")
    validate_table(auto, [*baseline.columns, *AUTO_COLUMNS], "Auto Validation")
    if set(baseline["case_id"]) != set(auto["case_id"]):
        raise ValueError("Input case_id sets differ; an inner join would discard cases")

    # Auto Validation stores a baseline snapshot. Check it before using its labels.
    baseline_snapshot = baseline.set_index("case_id").sort_index()
    auto_snapshot = auto.set_index("case_id").sort_index()[baseline_snapshot.columns]
    try:
        pd.testing.assert_frame_equal(
            baseline_snapshot, auto_snapshot, check_dtype=False,
            check_exact=False, rtol=1e-12, atol=1e-12,
        )
    except AssertionError as exc:
        raise ValueError(
            "Auto Validation contains a different baseline snapshot. "
            "Regenerate upstream outputs together before evaluation."
        ) from exc

    comparison = baseline.merge(
        auto[["case_id", *AUTO_COLUMNS]], on="case_id", how="left", validate="one_to_one",
    )
    validate_values(comparison)
    comparison["baseline_category"] = comparison["anomaly_vote_count"].map(
        {0: "Normal", 1: "Warning", 2: "Anomaly", 3: "Anomaly"}
    )
    comparison["aligned_label_match"] = comparison["baseline_category"].map(
        LABEL_ALIGNMENT
    ).eq(comparison["auto_validation_label"])
    comparison["comparison_group"] = (
        "baseline_" + comparison["baseline_category"].str.lower()
        + "_auto_" + comparison["auto_validation_label"].str.lower()
    )
    comparison["auto_binary_anomaly"] = comparison["auto_validation_label"].eq("Anomaly").astype(int)
    comparison["baseline_review_flag"] = comparison["anomaly_vote_count"].gt(0).astype(int)
    comparison["auto_review_flag"] = comparison["auto_validation_label"].ne("Normal").astype(int)
    return comparison


def build_cross_tab(df: pd.DataFrame) -> pd.DataFrame:
    counts = pd.crosstab(df["baseline_category"], df["auto_validation_label"]).reindex(
        index=BASELINE_CATEGORIES, columns=AUTO_LABELS, fill_value=0,
    )
    rows = []
    for baseline_category in BASELINE_CATEGORIES:
        baseline_count = int(counts.loc[baseline_category].sum())
        for auto_label in AUTO_LABELS:
            count = int(counts.loc[baseline_category, auto_label])
            rows.append({
                "baseline_category": baseline_category, "auto_validation_label": auto_label,
                "case_count": count, "population_pct": count / len(df) * 100,
                "within_baseline_category_pct": count / baseline_count * 100 if baseline_count else np.nan,
            })
    return pd.DataFrame(rows)


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    counts = {"total_cases": len(df)}
    counts.update({f"baseline_{label.lower()}": int(df["baseline_category"].eq(label).sum())
                   for label in BASELINE_CATEGORIES})
    counts.update({f"auto_{label.lower()}": int(df["auto_validation_label"].eq(label).sum())
                   for label in AUTO_LABELS})
    counts["aligned_label_matches"] = int(df["aligned_label_match"].sum())
    counts["aligned_label_disagreements"] = int((~df["aligned_label_match"]).sum())
    counts["binary_anomaly_agreement"] = int(df["anomaly_flag"].eq(df["auto_binary_anomaly"]).sum())
    counts["review_flag_agreement"] = int(df["baseline_review_flag"].eq(df["auto_review_flag"]).sum())
    return pd.DataFrame([
        {"metric": metric, "case_count": count, "population_pct": count / len(df) * 100}
        for metric, count in counts.items()
    ])


def build_detector_comparison(df: pd.DataFrame) -> pd.DataFrame:
    auto_anomaly = df["auto_binary_anomaly"].eq(1)
    rows = []
    for detector, column in DETECTORS.items():
        flagged = df[column].eq(1)
        intersection = int((flagged & auto_anomaly).sum())
        union = int((flagged | auto_anomaly).sum())
        both_unflagged = int((~flagged & ~auto_anomaly).sum())
        rows.append({
            "detector": detector, "flagged_cases": int(flagged.sum()),
            "flagged_population_pct": flagged.mean() * 100,
            "auto_anomaly_cases": int(auto_anomaly.sum()),
            "both_anomaly": intersection,
            "detector_only_anomaly": int((flagged & ~auto_anomaly).sum()),
            "auto_only_anomaly": int((~flagged & auto_anomaly).sum()),
            "neither_anomaly": both_unflagged,
            "binary_agreement_pct": (intersection + both_unflagged) / len(df) * 100,
            "anomaly_jaccard": intersection / union if union else np.nan,
        })
    return pd.DataFrame(rows)


def summarize_groups(df: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    work = df.assign(
        auto_suspicious=df["auto_validation_label"].eq("Suspicious").astype(int),
        auto_normal=df["auto_validation_label"].eq("Normal").astype(int),
        disagreement=(~df["aligned_label_match"]).astype(int),
    )
    summary = work.groupby(columns, dropna=False).agg(
        case_count=("case_id", "size"), baseline_anomaly_cases=("anomaly_flag", "sum"),
        auto_anomaly_cases=("auto_binary_anomaly", "sum"),
        auto_suspicious_cases=("auto_suspicious", "sum"), auto_normal_cases=("auto_normal", "sum"),
        label_disagreement_cases=("disagreement", "sum"), mean_trace_fitness=("trace_fitness", "mean"),
    ).reset_index()
    summary["population_pct"] = summary["case_count"] / len(df) * 100
    for source, target in [
        ("baseline_anomaly_cases", "baseline_anomaly_pct"),
        ("auto_anomaly_cases", "auto_anomaly_pct"),
        ("label_disagreement_cases", "label_disagreement_pct"),
    ]:
        summary[target] = summary[source] / summary["case_count"] * 100
    summary["auto_review_pct"] = (
        summary["auto_anomaly_cases"] + summary["auto_suspicious_cases"]
    ) / summary["case_count"] * 100
    return summary.sort_values(["case_count", *columns], ascending=[False, *([True] * len(columns))]).reset_index(drop=True)


def build_evidence_summary(df: pd.DataFrame) -> pd.DataFrame:
    tokens = df["auto_validation_evidence"].str.split(";").map(
        lambda values: {value.strip() for value in values}
    )
    masks = [("recorded_tag", tag, tokens.map(lambda values: tag in values)) for tag in EVIDENCE_TAGS]
    signals = {
        "missing_event": df["missing_event_count"].gt(0),
        "reversed_order": df["reversed_order_flag"].eq(1),
        "low_trace_fitness": df["trace_fitness"].lt(1),
        "negative_duration": df[DURATION_FEATURES].lt(0).any(axis=1),
        "any_long_duration": df["auto_long_duration_count"].gt(0),
        "severe_long_duration": df["auto_severe_long_duration_count"].gt(0),
        "short_duration_review": df["auto_short_duration_review_count"].gt(0),
        "near_long_duration_review": df["auto_near_long_duration_review_count"].gt(0),
    }
    masks.extend(("computed_signal", signal, mask) for signal, mask in signals.items())
    rows = []
    for source, evidence, mask in masks:
        subset = df.loc[mask]
        rows.append({
            "evidence_source": source, "evidence": evidence, "case_count": len(subset),
            "population_pct": len(subset) / len(df) * 100,
            "baseline_anomaly_cases": int(subset["anomaly_flag"].sum()),
            "auto_anomaly_cases": int(subset["auto_binary_anomaly"].sum()),
            "auto_suspicious_cases": int(subset["auto_validation_label"].eq("Suspicious").sum()),
            "auto_normal_cases": int(subset["auto_validation_label"].eq("Normal").sum()),
            "label_disagreement_cases": int((~subset["aligned_label_match"]).sum()),
        })
    return pd.DataFrame(rows)


def build_disagreement_summary(df: pd.DataFrame) -> pd.DataFrame:
    subset = df.loc[~df["aligned_label_match"]]
    groups = ["baseline_category", "auto_validation_label", "auto_validation_evidence"]
    summary = subset.groupby(groups).size().reset_index(name="case_count")
    summary["population_pct"] = summary["case_count"] / len(df) * 100
    summary["within_disagreements_pct"] = summary["case_count"] / len(subset) * 100 if len(subset) else np.nan
    return summary.sort_values(["case_count", *groups], ascending=[False, True, True, True]).reset_index(drop=True)


def markdown_table(df: pd.DataFrame) -> list[str]:
    def cell(value) -> str:
        if pd.isna(value):
            return "N/A"
        if isinstance(value, (float, np.floating)):
            return f"{value:.4f}"
        return str(value).replace("|", "\\|").replace("\n", " ")

    return [
        "| " + " | ".join(df.columns) + " |",
        "| " + " | ".join(["---"] * len(df.columns)) + " |",
        *("| " + " | ".join(cell(value) for value in row) + " |"
          for row in df.itertuples(index=False, name=None)),
    ]


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_report(tables: dict[str, pd.DataFrame]) -> None:
    summary = tables["auto_validation_evaluation_summary"]
    cross = tables["auto_validation_cross_tab"].pivot(
        index="baseline_category", columns="auto_validation_label", values="case_count",
    ).reindex(index=BASELINE_CATEGORIES, columns=AUTO_LABELS).reset_index()
    cross.columns.name = None
    variants = tables["auto_validation_by_variant"]
    lines = [
        "# Auto Validation vs Baseline Evaluation", "",
        "Descriptive comparison of saved outputs on the imported Olist cases. "
        "Neither output is ground truth. No accuracy, precision, recall or F1 is estimated here.", "",
        "## Inputs and Integrity", "",
        "- Cases are joined one-to-one by case_id; duplicate, blank or unmatched IDs stop evaluation.",
        "- All baseline columns stored in Auto Validation are checked against the baseline input, "
        "including missing-value positions (numeric tolerance: rtol=atol=1e-12).",
        "- Required labels, detector votes, flags and counts are validated before output is written.",
        "- No source data, timestamps, detector outputs or human labels are changed. "
        "No synthetic cases or human labels are generated.", "",
    ]
    for path in [BASELINE_FILE, AUTO_FILE, Path(__file__).resolve()]:
        lines.append(f"- `{path.relative_to(PROJECT_ROOT)}` SHA-256: `{file_sha256(path)}`")
    lines.extend([
        "", "## Comparison Definitions", "",
        "- Baseline Normal: 0 votes; Warning: 1 vote; Anomaly: 2 or 3 votes. "
        "The original High-confidence anomaly level is retained in the case-level CSV.",
        "- Aligned-label agreement maps Normal to Normal, Warning to Suspicious, and Anomaly "
        "to Anomaly for comparison. Warning and Suspicious are not assumed to have identical rules.",
        "- Binary anomaly agreement compares baseline anomaly_flag with Auto label Anomaly. "
        "Suspicious counts as not Anomaly for this calculation, but is still a review case.",
        "- Review agreement compares baseline votes > 0 with Auto label other than Normal.",
        "- Jaccard = cases flagged Anomaly by both / cases flagged Anomaly by either. "
        "An empty union is N/A. Neither-anomaly includes Suspicious cases.",
        "- All population percentages use all evaluated cases. Within-category and variant "
        "rates use their respective group size.", "",
        "## Population Summary", "", *markdown_table(summary), "",
        "## Label Cross-tab", "", *markdown_table(cross), "",
        "Rows are baseline categories; columns are Auto Validation labels.", "",
        "## Detector Overlap", "",
        *markdown_table(tables["auto_validation_detector_comparison"]), "",
        "## Disagreement Evidence", "",
        *markdown_table(tables["auto_validation_disagreement_summary"]), "",
        "These groups identify review priorities, not false positives or false negatives. "
        "The case-level CSV retains IDs, four durations, fitness, detector flags and both explanations.", "",
        "## Evidence Coverage", "",
        "Evidence groups overlap and must not be summed into a population total. "
        "Each case is counted once per evidence row.", "",
        "recorded_tag counts the explanation tags saved by Auto Validation. "
        "computed_signal counts cases satisfying source fields/counters. Severe-long cases can "
        "also satisfy any_long_duration; the explanation emits severe_long_duration instead of "
        "long_duration. A near-long signal may also be omitted from the explanation when another "
        "duration is already long. These are intentionally separate counts.", "",
        *markdown_table(tables["auto_validation_evidence_summary"]), "",
        "## Process Variants", "",
        *markdown_table(variants[[
            "variant", "case_count", "baseline_anomaly_cases", "auto_anomaly_cases",
            "auto_suspicious_cases", "label_disagreement_cases", "auto_anomaly_pct",
        ]]), "",
        "## Interpretation and Limits", "",
        "- The methods share process evidence and duration features, so agreement is not "
        "independent validation. Larger anomaly counts do not establish better detection.",
        "- Baseline IQR uses observed durations including negative values; Auto thresholds "
        "use non-negative observed durations. Threshold estimation populations differ.",
        "- Thresholds are estimated on the same imported population being described. "
        "This is not a held-out or cross-marketplace performance estimate.",
        "- The existing manual reference contains selected candidates, not a representative "
        "population sample. Cases used to design rules are not an independent test set.",
        "- Short non-negative durations and near-threshold durations are review signals. "
        "High/Medium/Low Auto confidence is a rule category, not a calibrated probability.",
        "- Total cycle time overlaps with component durations, and missing events/fitness or "
        "negative durations/reversed order can reflect the same underlying issue. "
        "Evidence counts are not counts of independent causes.",
        "- Missing steps require order-status and observation-window context: a cancelled or "
        "unfinished order may legitimately lack later fulfillment events. This comparison "
        "does not resolve that business interpretation.",
        "- The baseline artifact is evaluated as saved. This does not select between the "
        "separate contamination 0.05 and 0.08 candidate experiments.", "",
        "## Next Research Step", "",
        "Freeze this exploratory configuration, then prepare a new detector-blinded human "
        "review sample covering disagreement groups and agreement groups, including Normal "
        "cases. Record selection probabilities and exclude previously reviewed cases from "
        "independent testing. Keep human labels empty until actual review. Check order status "
        "and event timestamps before changing rules; use a held-out time period or another "
        "real dataset for subsequent generalization experiments.", "",
        "## Reproduce", "", "```sh",
        ".venv/bin/python scripts/03_anomaly_detection/auto_validation_evaluation.py", "```", "",
        "## Output Files", "",
    ])
    lines.extend(f"- `data/anomaly/{name}.csv`" for name in tables)
    REPORT_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    baseline = pd.read_csv(BASELINE_FILE, dtype={"case_id": "string"})
    auto = pd.read_csv(AUTO_FILE, dtype={"case_id": "string"})
    comparison = build_comparison(baseline, auto)
    tables = {
        "auto_validation_vs_baseline": comparison,
        "auto_validation_evaluation_summary": build_summary(comparison),
        "auto_validation_cross_tab": build_cross_tab(comparison),
        "auto_validation_detector_comparison": build_detector_comparison(comparison),
        "auto_validation_evidence_summary": build_evidence_summary(comparison),
        "auto_validation_by_variant": summarize_groups(comparison, ["variant"]),
        "auto_validation_disagreement_summary": build_disagreement_summary(comparison),
    }
    for name, table in tables.items():
        path = ANOMALY_DATA_DIR / f"{name}.csv"
        table.to_csv(path, index=False)
        print(f"Saved {len(table):,} rows: {path.relative_to(PROJECT_ROOT)}")
    write_report(tables)
    print(tables["auto_validation_evaluation_summary"].to_string(index=False))
    print(f"Report: {REPORT_FILE.relative_to(PROJECT_ROOT)}")


if __name__ == "__main__":
    main()
