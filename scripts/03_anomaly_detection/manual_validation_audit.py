from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import MANUAL_REVIEW_DIR, REPORT_DIR


INPUT_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation_labeled.csv"
SUMMARY_OUTPUT_FILE = MANUAL_REVIEW_DIR / "manual_validation_consistency_summary.csv"
DUPLICATE_OUTPUT_FILE = MANUAL_REVIEW_DIR / "manual_validation_duplicate_audit.csv"
ISSUE_OUTPUT_FILE = MANUAL_REVIEW_DIR / "manual_validation_issue_audit.csv"
REPORT_OUTPUT_FILE = REPORT_DIR / "manual_validation_audit.md"

VALID_LABELS = ["Normal", "Suspicious", "Anomaly"]
VALID_CONFIDENCE = ["Low", "Medium", "High"]
VALID_REASONS = [
    "Normal process and normal timing",
    "Missing activity",
    "Reversed activity order",
    "Very long transition duration",
    "Multiple abnormal durations",
    "Multivariate temporal pattern",
    "Borderline / uncertain",
    "Data quality concern",
    "Other",
]

DETECTOR_COLUMNS = [
    "process_deviation_flag",
    "statistical_anomaly_flag",
    "candidate_if_flag",
]

PROCESS_CONTENT_COLUMNS = [
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
    "candidate_overlap_group",
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


def load_manual_validation() -> pd.DataFrame:
    """Load the labeled manual-validation file."""
    df = pd.read_csv(INPUT_FILE)
    required_columns = [
        "case_id",
        "contamination",
        "reviewer_label",
        "reviewer_confidence",
        "reviewer_reason",
        "process_deviation_flag",
        "statistical_anomaly_flag",
        "candidate_if_flag",
        "final_candidate_vote_count",
        "candidate_hybrid_flag",
    ]
    missing_columns = [column for column in required_columns if column not in df.columns]
    if missing_columns:
        raise ValueError(
            "Manual-validation file is missing required column(s): "
            + ", ".join(missing_columns)
        )

    for column in ["case_id", "reviewer_label", "reviewer_confidence", "reviewer_reason"]:
        df[column] = df[column].fillna("").astype(str).str.strip()

    return df


def split_reasons(reason_text: str) -> list[str]:
    """Split semicolon-separated manual reason codes."""
    if not reason_text:
        return []
    return [reason.strip() for reason in reason_text.split(";") if reason.strip()]


def normalize_numeric_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Coerce detector and vote columns to numeric values."""
    df = df.copy()
    numeric_columns = DETECTOR_COLUMNS + [
        "final_candidate_vote_count",
        "candidate_hybrid_flag",
        "missing_event_count",
        "reversed_order_flag",
    ]
    for column in numeric_columns:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0).astype(int)
    return df


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Build high-level consistency metrics for the reviewed candidate set."""
    rows: list[dict] = []

    add_metric(rows, "manual_review", "reviewed_row_count", len(df))
    add_metric(rows, "manual_review", "unique_case_id_count", df["case_id"].nunique())
    add_metric(
        rows,
        "manual_review",
        "duplicate_case_id_count",
        int((df.groupby("case_id").size() > 1).sum()),
    )
    add_metric(
        rows,
        "manual_review",
        "duplicate_extra_row_count",
        int(len(df) - df["case_id"].nunique()),
    )

    for contamination, count in df["contamination"].value_counts(dropna=False).sort_index().items():
        add_metric(rows, "contamination_distribution", str(contamination), int(count))

    for label in VALID_LABELS:
        add_metric(
            rows,
            "label_distribution",
            label,
            int((df["reviewer_label"] == label).sum()),
        )

    unique_case_df = df.drop_duplicates(subset=["case_id"]).copy()
    for label in VALID_LABELS:
        add_metric(
            rows,
            "unique_case_label_distribution",
            label,
            int((unique_case_df["reviewer_label"] == label).sum()),
        )

    for contamination in sorted(df["contamination"].dropna().astype(str).unique()):
        contamination_df = df[df["contamination"].astype(str) == contamination]
        for label in VALID_LABELS:
            add_metric(
                rows,
                "label_by_contamination",
                f"{contamination}_{label}",
                int((contamination_df["reviewer_label"] == label).sum()),
            )

    for confidence in VALID_CONFIDENCE:
        add_metric(
            rows,
            "confidence_distribution",
            confidence,
            int((df["reviewer_confidence"] == confidence).sum()),
        )

    for contamination in sorted(df["contamination"].dropna().astype(str).unique()):
        contamination_df = df[df["contamination"].astype(str) == contamination]
        for confidence in VALID_CONFIDENCE:
            add_metric(
                rows,
                "confidence_by_contamination",
                f"{contamination}_{confidence}",
                int((contamination_df["reviewer_confidence"] == confidence).sum()),
            )

    if "candidate_overlap_group" in df.columns:
        for overlap_group, count in (
            df["candidate_overlap_group"].value_counts(dropna=False).sort_index().items()
        ):
            add_metric(
                rows,
                "overlap_group_distribution",
                str(overlap_group),
                int(count),
            )

    reason_counts = count_reason_frequency(df)
    for reason in VALID_REASONS:
        add_metric(
            rows,
            "reason_distribution",
            reason,
            int(reason_counts.get(reason, 0)),
        )

    issues = build_issue_audit(df)
    for issue_type, count in issues["issue_type"].value_counts().sort_index().items():
        add_metric(rows, "issue_distribution", issue_type, int(count))
    add_metric(rows, "issue_distribution", "total_issue_count", len(issues))

    duplicate_audit = build_duplicate_audit(df)
    add_metric(
        rows,
        "duplicate_consistency",
        "duplicate_case_with_label_conflict_count",
        int(duplicate_audit["label_conflict"].sum()) if not duplicate_audit.empty else 0,
    )
    add_metric(
        rows,
        "duplicate_consistency",
        "duplicate_case_with_confidence_conflict_count",
        int(duplicate_audit["confidence_conflict"].sum()) if not duplicate_audit.empty else 0,
    )
    add_metric(
        rows,
        "duplicate_consistency",
        "duplicate_case_with_reason_conflict_count",
        int(duplicate_audit["reason_conflict"].sum()) if not duplicate_audit.empty else 0,
    )
    add_metric(
        rows,
        "duplicate_consistency",
        "duplicate_case_with_process_content_conflict_count",
        int(duplicate_audit["process_content_conflict"].sum()) if not duplicate_audit.empty else 0,
    )

    return pd.DataFrame(rows)


def count_reason_frequency(df: pd.DataFrame) -> dict[str, int]:
    """Count manual reason-code frequency after splitting multi-reason cells."""
    counts: dict[str, int] = {}
    for reason_text in df["reviewer_reason"]:
        for reason in split_reasons(reason_text):
            counts[reason] = counts.get(reason, 0) + 1
    return counts


def build_duplicate_audit(df: pd.DataFrame) -> pd.DataFrame:
    """Create one row per duplicated case_id with consistency indicators."""
    rows = []
    duplicated = df.groupby("case_id").filter(lambda group: len(group) > 1)
    if duplicated.empty:
        return pd.DataFrame(
            columns=[
                "case_id",
                "reviewed_row_count",
                "contaminations",
                "labels",
                "confidences",
                "reasons",
                "overlap_groups",
                "detector_patterns",
                "label_conflict",
                "confidence_conflict",
                "reason_conflict",
                "detector_pattern_conflict",
                "process_content_conflict",
                "different_process_content_columns",
            ]
        )

    for case_id, group in duplicated.groupby("case_id", sort=True):
        labels = sorted(group["reviewer_label"].dropna().astype(str).unique())
        confidences = sorted(group["reviewer_confidence"].dropna().astype(str).unique())
        reasons = sorted(group["reviewer_reason"].dropna().astype(str).unique())
        contaminations = sorted(group["contamination"].dropna().astype(str).unique())
        overlap_groups = sorted(group.get("candidate_overlap_group", pd.Series(dtype=str)).dropna().astype(str).unique())

        detector_patterns = sorted(
            {
                f"P{int(row['process_deviation_flag'])}-S{int(row['statistical_anomaly_flag'])}-IF{int(row['candidate_if_flag'])}-V{int(row['final_candidate_vote_count'])}-H{int(row['candidate_hybrid_flag'])}"
                for _, row in group.iterrows()
            }
        )
        different_process_content_columns = find_different_columns(
            group,
            PROCESS_CONTENT_COLUMNS,
        )

        rows.append(
            {
                "case_id": case_id,
                "reviewed_row_count": len(group),
                "contaminations": "; ".join(contaminations),
                "labels": "; ".join(labels),
                "confidences": "; ".join(confidences),
                "reasons": " || ".join(reasons),
                "overlap_groups": "; ".join(overlap_groups),
                "detector_patterns": "; ".join(detector_patterns),
                "label_conflict": len(labels) > 1,
                "confidence_conflict": len(confidences) > 1,
                "reason_conflict": len(reasons) > 1,
                "detector_pattern_conflict": len(detector_patterns) > 1,
                "process_content_conflict": bool(different_process_content_columns),
                "different_process_content_columns": "; ".join(different_process_content_columns),
            }
        )

    return pd.DataFrame(rows)


def find_different_columns(df: pd.DataFrame, columns: list[str]) -> list[str]:
    """Return columns whose values differ within a duplicated-case group."""
    different_columns = []
    for column in columns:
        if column not in df.columns:
            continue
        values = df[column].fillna("__NA__").astype(str).unique()
        if len(values) > 1:
            different_columns.append(column)
    return different_columns


def build_issue_audit(df: pd.DataFrame) -> pd.DataFrame:
    """Build row-level manual-validation consistency issues."""
    issues = []

    for index, row in df.iterrows():
        case_id = row["case_id"]
        label = row["reviewer_label"]
        confidence = row["reviewer_confidence"]
        reasons = split_reasons(row["reviewer_reason"])

        if not label:
            issues.append(issue(index, case_id, "missing_label", "Missing reviewer_label."))
        elif label not in VALID_LABELS:
            issues.append(issue(index, case_id, "invalid_label", f"Unexpected label: {label}"))

        if not confidence:
            issues.append(issue(index, case_id, "missing_confidence", "Missing reviewer_confidence."))
        elif confidence not in VALID_CONFIDENCE:
            issues.append(issue(index, case_id, "invalid_confidence", f"Unexpected confidence: {confidence}"))

        if not reasons:
            issues.append(issue(index, case_id, "missing_reason", "Missing reviewer_reason."))
        else:
            for reason in reasons:
                if reason not in VALID_REASONS and not reason.startswith("Other:"):
                    issues.append(issue(index, case_id, "invalid_reason", f"Unexpected reason: {reason}"))

        if label == "Normal":
            abnormal_reasons = [
                reason
                for reason in reasons
                if reason not in {"Normal process and normal timing", "Borderline / uncertain"}
            ]
            if abnormal_reasons:
                issues.append(
                    issue(
                        index,
                        case_id,
                        "normal_label_with_abnormal_reason",
                        "; ".join(abnormal_reasons),
                    )
                )

        expected_vote_count = int(row["process_deviation_flag"]) + int(row["statistical_anomaly_flag"]) + int(row["candidate_if_flag"])
        actual_vote_count = int(row["final_candidate_vote_count"])
        if expected_vote_count != actual_vote_count:
            issues.append(
                issue(
                    index,
                    case_id,
                    "vote_count_mismatch",
                    f"expected {expected_vote_count}, found {actual_vote_count}",
                )
            )

        expected_hybrid_flag = int(actual_vote_count >= 2)
        actual_hybrid_flag = int(row["candidate_hybrid_flag"])
        if expected_hybrid_flag != actual_hybrid_flag:
            issues.append(
                issue(
                    index,
                    case_id,
                    "hybrid_flag_mismatch",
                    f"expected {expected_hybrid_flag}, found {actual_hybrid_flag}",
                )
            )

    return pd.DataFrame(
        issues,
        columns=["row_number", "case_id", "issue_type", "details"],
    )


def issue(row_index: int, case_id: str, issue_type: str, details: str) -> dict:
    """Build one issue row."""
    return {
        "row_number": row_index + 2,
        "case_id": case_id,
        "issue_type": issue_type,
        "details": details,
    }


def format_value(value) -> str:
    """Format a metric value for Markdown."""
    if pd.isna(value):
        return ""
    numeric_value = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.notna(numeric_value):
        if float(numeric_value).is_integer():
            return str(int(numeric_value))
        return str(numeric_value)
    return str(value)


def write_markdown_report(
    summary: pd.DataFrame,
    duplicate_audit: pd.DataFrame,
    issue_audit: pd.DataFrame,
) -> None:
    """Write a human-readable manual-validation audit report."""
    lines = [
        "# Manual Validation Audit",
        "",
        "This report audits the completed detector-blinded manual-validation candidate set.",
        "It does not relabel cases, fabricate labels, or modify the reviewed sample.",
        "",
        "## Audit Policy",
        "",
        "- Manual labels are treated as human-reviewed references for this candidate set only.",
        "- They are not treated as full-population ground truth.",
        "- Duplicate case IDs are reported explicitly instead of being silently removed.",
        "- Detector flags are checked for internal vote consistency only.",
        "",
        "## Summary Metrics",
        "",
    ]

    for section, section_df in summary.groupby("section", sort=False):
        lines.extend([f"### {section}", "", "| Metric | Value | Note |", "| --- | ---: | --- |"])
        for _, row in section_df.iterrows():
            note = "" if pd.isna(row["note"]) else str(row["note"])
            lines.append(f"| `{row['metric']}` | {format_value(row['value'])} | {note} |")
        lines.append("")

    lines.extend(["## Duplicate Case Audit", ""])
    if duplicate_audit.empty:
        lines.append("No duplicated `case_id` values were found.")
    else:
        lines.extend(
            [
                f"Duplicated `case_id` values found: {len(duplicate_audit)}.",
                "",
                "| Metric | Count |",
                "| --- | ---: |",
                f"| Duplicate cases with label conflict | {int(duplicate_audit['label_conflict'].sum())} |",
                f"| Duplicate cases with confidence conflict | {int(duplicate_audit['confidence_conflict'].sum())} |",
                f"| Duplicate cases with reason conflict | {int(duplicate_audit['reason_conflict'].sum())} |",
                f"| Duplicate cases with detector-pattern conflict | {int(duplicate_audit['detector_pattern_conflict'].sum())} |",
                f"| Duplicate cases with process-content conflict | {int(duplicate_audit['process_content_conflict'].sum())} |",
            ]
        )
    lines.append("")

    lines.extend(["## Row-Level Issues", ""])
    if issue_audit.empty:
        lines.append("No row-level consistency issues were found.")
    else:
        lines.extend(["| Issue Type | Count |", "| --- | ---: |"])
        for issue_type, count in issue_audit["issue_type"].value_counts().sort_index().items():
            lines.append(f"| `{issue_type}` | {int(count)} |")
    lines.append("")

    REPORT_OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    df = normalize_numeric_columns(load_manual_validation())

    summary = build_summary(df)
    duplicate_audit = build_duplicate_audit(df)
    issue_audit = build_issue_audit(df)

    summary.to_csv(SUMMARY_OUTPUT_FILE, index=False)
    duplicate_audit.to_csv(DUPLICATE_OUTPUT_FILE, index=False)
    issue_audit.to_csv(ISSUE_OUTPUT_FILE, index=False)
    write_markdown_report(summary, duplicate_audit, issue_audit)

    print("Manual validation audit completed.")
    print(f"Saved summary: {SUMMARY_OUTPUT_FILE}")
    print(f"Saved duplicate audit: {DUPLICATE_OUTPUT_FILE}")
    print(f"Saved issue audit: {ISSUE_OUTPUT_FILE}")
    print(f"Saved report: {REPORT_OUTPUT_FILE}")


if __name__ == "__main__":
    main()
