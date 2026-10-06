from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import time
import urllib.error
import urllib.request

import matplotlib.pyplot as plt
import pandas as pd


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import FINAL_CANDIDATE_FIGURE_DIR, MANUAL_REVIEW_DIR


# ============================================================
# CONFIG
# ============================================================

INPUT_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation.csv"
RESULTS_FILE = MANUAL_REVIEW_DIR / "automated_validation_results.csv"
SUMMARY_FILE = MANUAL_REVIEW_DIR / "automated_validation_summary.csv"
BY_CONTAMINATION_FILE = MANUAL_REVIEW_DIR / "ai_validation_by_contamination.csv"
HUMAN_AUDIT_FILE = MANUAL_REVIEW_DIR / "ai_validation_human_audit_20.csv"

LABEL_FIGURE_FILE = FINAL_CANDIDATE_FIGURE_DIR / "ai_validation_labels.png"
COMPARISON_FIGURE_FILE = (
    FINAL_CANDIDATE_FIGURE_DIR / "ai_validation_candidate_comparison.png"
)

OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
OPENAI_MODEL_ENV = "OPENAI_MODEL"
OPENAI_BASE_URL_ENV = "OPENAI_BASE_URL"

DEFAULT_MODEL = "gpt-4.1-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

VALID_LABELS = ["Normal", "Suspicious", "Anomaly"]
VALID_CONFIDENCE = ["Low", "Medium", "High"]
VALID_REASONS = [
    "Normal process and timing",
    "Missing activity",
    "Reversed activity order",
    "Negative duration",
    "Very long transition duration",
    "Multiple abnormal durations",
    "Borderline temporal behavior",
    "Data quality concern",
    "Other",
]

BLIND_INPUT_COLUMNS = [
    "case_id",
    "variant",
    "event_sequence",
    "event_timestamps",
    "trace_fitness",
    "missing_event_count",
    "reversed_order_flag",
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]

AI_OUTPUT_COLUMNS = [
    "ai_validation_label",
    "ai_validation_confidence",
    "ai_validation_reason",
    "ai_validation_explanation",
]


# ============================================================
# LOAD / RESUME
# ============================================================

def first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first existing column from a list of aliases."""
    for column in candidates:
        if column in df.columns:
            return column
    return None


def load_or_create_results() -> pd.DataFrame:
    """Load checkpoint if available; otherwise create it from the input sample."""
    if RESULTS_FILE.exists():
        print(f"Loading checkpoint: {RESULTS_FILE}")
        df = pd.read_csv(RESULTS_FILE)
    else:
        print(f"Loading input: {INPUT_FILE}")
        df = pd.read_csv(INPUT_FILE)

    missing_blind_columns = [
        column for column in BLIND_INPUT_COLUMNS if column not in df.columns
    ]
    if missing_blind_columns:
        raise ValueError(
            "Input is missing required blind-review column(s): "
            + ", ".join(missing_blind_columns)
        )

    for column in AI_OUTPUT_COLUMNS:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)

    return df


def save_checkpoint(df: pd.DataFrame) -> None:
    """Save validation results after each completed case."""
    df.to_csv(RESULTS_FILE, index=False)


# ============================================================
# LLM VALIDATION
# ============================================================

def get_openai_config() -> tuple[str, str, str]:
    """Read OpenAI API configuration from environment variables."""
    api_key = os.environ.get(OPENAI_API_KEY_ENV, "").strip()
    model = os.environ.get(OPENAI_MODEL_ENV, DEFAULT_MODEL).strip()
    base_url = os.environ.get(OPENAI_BASE_URL_ENV, DEFAULT_BASE_URL).strip().rstrip("/")
    return api_key, model, base_url


def provider_available_for_unfinished_cases(df: pd.DataFrame) -> bool:
    """Return whether an API provider is available for unfinished cases."""
    unfinished = df["ai_validation_label"].str.strip().eq("").sum()
    api_key, _, _ = get_openai_config()

    if unfinished > 0 and not api_key:
        print(
            "OPENAI_API_KEY is not set, and there are unfinished cases. "
            "No automated labels were generated. Set OPENAI_API_KEY and rerun."
        )
        return False

    return True


def row_to_blind_payload(row: pd.Series) -> dict:
    """Extract only allowed blind-validation fields from a case row."""
    payload = {}
    for column in BLIND_INPUT_COLUMNS:
        value = row[column]
        if pd.isna(value):
            payload[column] = None
        else:
            payload[column] = value
    return payload


def build_validation_prompt(row: pd.Series) -> str:
    """Build the blind AI-assisted validation prompt for one case."""
    blind_payload = row_to_blind_payload(row)

    return (
        "You are performing independent AI-assisted plausibility validation "
        "for an ecommerce fulfillment process case.\n\n"
        "Use ONLY the case data below. Do not infer or use detector predictions, "
        "contamination groups, anomaly flags, vote counts, or prior anomaly reasons.\n\n"
        "Rubric:\n"
        "NORMAL: process sequence is reasonable, no clear reversed activity, no important "
        "missing activity causing incomplete fulfillment, and transition durations do not "
        "show a clear operational delay.\n"
        "SUSPICIOUS: signs are abnormal or borderline; timing is long but not clearly enough "
        "to conclude operational anomaly; or the data is insufficient for certainty.\n"
        "ANOMALY: important missing activity makes fulfillment incomplete; clear reversed "
        "activity order; negative duration due to abnormal ordering; extremely long transition; "
        "multiple abnormal durations; or a temporal pattern clearly worth managerial review.\n\n"
        "Allowed reason groups:\n"
        + "\n".join(f"- {reason}" for reason in VALID_REASONS)
        + "\n\n"
        "Return strict JSON only with this exact schema:\n"
        "{\n"
        '  "label": "Normal|Suspicious|Anomaly",\n'
        '  "confidence": "Low|Medium|High",\n'
        '  "reasons": ["..."],\n'
        '  "explanation": "1-3 concise sentences based only on the case data."\n'
        "}\n\n"
        "Case data:\n"
        f"{json.dumps(blind_payload, ensure_ascii=False, indent=2)}"
    )


def call_openai_json(prompt: str) -> dict:
    """Call OpenAI Responses API and parse a strict JSON response."""
    api_key, model, base_url = get_openai_config()
    url = f"{base_url}/responses"
    payload = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": prompt,
                    }
                ],
            }
        ],
        "temperature": 0,
        "text": {
            "format": {
                "type": "json_schema",
                "name": "case_validation",
                "strict": True,
                "schema": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "label": {
                            "type": "string",
                            "enum": VALID_LABELS,
                        },
                        "confidence": {
                            "type": "string",
                            "enum": VALID_CONFIDENCE,
                        },
                        "reasons": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": VALID_REASONS,
                            },
                        },
                        "explanation": {
                            "type": "string",
                        },
                    },
                    "required": [
                        "label",
                        "confidence",
                        "reasons",
                        "explanation",
                    ],
                },
            }
        },
    }

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            response_data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI API HTTP {error.code}: {body}") from error

    output_text = extract_response_text(response_data)
    return json.loads(output_text)


def extract_response_text(response_data: dict) -> str:
    """Extract text content from a Responses API payload."""
    if "output_text" in response_data:
        return response_data["output_text"]

    for output_item in response_data.get("output", []):
        for content_item in output_item.get("content", []):
            if "text" in content_item:
                return content_item["text"]

    raise RuntimeError("Could not extract JSON text from OpenAI response.")


def validate_model_output(result: dict) -> dict:
    """Validate and normalize one model result."""
    label = result.get("label")
    confidence = result.get("confidence")
    reasons = result.get("reasons", [])
    explanation = str(result.get("explanation", "")).strip()

    if label not in VALID_LABELS:
        raise ValueError(f"Invalid label from model: {label}")
    if confidence not in VALID_CONFIDENCE:
        raise ValueError(f"Invalid confidence from model: {confidence}")
    if not isinstance(reasons, list) or not reasons:
        raise ValueError("Model returned no validation reasons.")
    invalid_reasons = [reason for reason in reasons if reason not in VALID_REASONS]
    if invalid_reasons:
        raise ValueError(f"Invalid reason(s) from model: {invalid_reasons}")
    if not explanation:
        raise ValueError("Model returned empty explanation.")

    return {
        "ai_validation_label": label,
        "ai_validation_confidence": confidence,
        "ai_validation_reason": "; ".join(reasons),
        "ai_validation_explanation": explanation,
    }


def evaluate_case(row: pd.Series, max_retries: int = 3) -> dict:
    """Evaluate one case using blind case data only."""
    prompt = build_validation_prompt(row)

    for attempt in range(1, max_retries + 1):
        try:
            result = call_openai_json(prompt)
            return validate_model_output(result)
        except Exception as error:
            if attempt == max_retries:
                raise
            wait_seconds = 2 * attempt
            print(f"Evaluation failed on attempt {attempt}: {error}")
            print(f"Retrying in {wait_seconds} seconds...")
            time.sleep(wait_seconds)

    raise RuntimeError("Unreachable retry state.")


def run_validation(df: pd.DataFrame) -> pd.DataFrame:
    """Run checkpointed AI-assisted validation for all unfinished cases."""
    if not provider_available_for_unfinished_cases(df):
        return df

    unfinished_indices = df.index[df["ai_validation_label"].str.strip().eq("")]
    total_cases = len(df)

    for position, index in enumerate(unfinished_indices, start=1):
        row = df.loc[index]
        print(
            f"Evaluating case {position}/{len(unfinished_indices)} "
            f"(overall row {index + 1}/{total_cases}): {row['case_id']}"
        )

        result = evaluate_case(row)
        for column, value in result.items():
            df.at[index, column] = value

        save_checkpoint(df)

    return df


# ============================================================
# QUALITY CONTROL AND ANALYSIS
# ============================================================

def quality_control(df: pd.DataFrame) -> None:
    """Validate completed AI-assisted labels."""
    if len(df) != 100:
        raise ValueError(f"Expected 100 cases, found {len(df)}.")

    missing_labels = df["ai_validation_label"].str.strip().eq("").sum()
    if missing_labels:
        raise ValueError(f"Missing AI labels: {missing_labels}")

    invalid_labels = sorted(set(df["ai_validation_label"]) - set(VALID_LABELS))
    if invalid_labels:
        raise ValueError(f"Invalid AI labels: {invalid_labels}")

    invalid_confidence = sorted(
        set(df["ai_validation_confidence"]) - set(VALID_CONFIDENCE)
    )
    if invalid_confidence:
        raise ValueError(f"Invalid confidence values: {invalid_confidence}")

    missing_explanation = df["ai_validation_explanation"].str.strip().eq("").sum()
    if missing_explanation:
        raise ValueError(f"Missing explanations: {missing_explanation}")


def build_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Build overall summary table."""
    rows = []
    total_cases = len(df)

    for label in VALID_LABELS:
        count = int((df["ai_validation_label"] == label).sum())
        rows.append(
            {
                "section": "overall",
                "group": "all",
                "metric": label,
                "count": count,
                "percentage": count / total_cases * 100,
            }
        )

    for confidence in VALID_CONFIDENCE:
        count = int((df["ai_validation_confidence"] == confidence).sum())
        rows.append(
            {
                "section": "confidence",
                "group": "all",
                "metric": confidence,
                "count": count,
                "percentage": count / total_cases * 100,
            }
        )

    summary_df = pd.DataFrame(rows)
    summary_df.to_csv(SUMMARY_FILE, index=False)
    return summary_df


def analyze_by_contamination(df: pd.DataFrame) -> pd.DataFrame:
    """Analyze AI-assisted labels after contamination is revealed."""
    rows = []
    for contamination, group in df.groupby("contamination", dropna=False):
        total = len(group)
        normal = int((group["ai_validation_label"] == "Normal").sum())
        suspicious = int((group["ai_validation_label"] == "Suspicious").sum())
        anomaly = int((group["ai_validation_label"] == "Anomaly").sum())

        row = {
            "contamination": contamination,
            "total_cases": total,
            "ai_normal": normal,
            "ai_suspicious": suspicious,
            "ai_anomaly": anomaly,
            "anomaly_percentage": anomaly / total * 100,
            "suspicious_plus_anomaly_percentage": (
                suspicious + anomaly
            ) / total * 100,
        }

        for confidence in VALID_CONFIDENCE:
            row[f"confidence_{confidence.lower()}"] = int(
                (group["ai_validation_confidence"] == confidence).sum()
            )

        rows.append(row)

    by_contamination_df = pd.DataFrame(rows).sort_values("contamination")
    by_contamination_df.to_csv(BY_CONTAMINATION_FILE, index=False)
    return by_contamination_df


def compare_detectors_with_ai(df: pd.DataFrame) -> pd.DataFrame:
    """Compare detector flags with independent AI-assisted validation labels."""
    if_col = first_existing_column(
        df,
        ["candidate_if_flag", "experimental_if_flag", "isolation_forest_flag"],
    )
    hybrid_col = first_existing_column(
        df,
        ["candidate_hybrid_flag", "anomaly_flag"],
    )

    rows = []
    for contamination, group in df.groupby("contamination", dropna=False):
        for detector_name, detector_col in [
            ("candidate_if", if_col),
            ("candidate_hybrid", hybrid_col),
        ]:
            if detector_col is None:
                continue

            flagged = group[pd.to_numeric(group[detector_col], errors="coerce") == 1]
            rows.append(
                {
                    "contamination": contamination,
                    "detector": detector_name,
                    "flagged_cases": len(flagged),
                    "ai_anomaly_among_flagged": int(
                        (flagged["ai_validation_label"] == "Anomaly").sum()
                    ),
                    "ai_suspicious_among_flagged": int(
                        (flagged["ai_validation_label"] == "Suspicious").sum()
                    ),
                    "ai_normal_among_flagged": int(
                        (flagged["ai_validation_label"] == "Normal").sum()
                    ),
                }
            )

    detector_comparison_df = pd.DataFrame(rows)
    detector_comparison_df.to_csv(
        MANUAL_REVIEW_DIR / "ai_validation_detector_comparison.csv",
        index=False,
    )
    return detector_comparison_df


def create_human_audit_sample(df: pd.DataFrame) -> pd.DataFrame:
    """Create a 20-case audit sample balanced by AI labels where possible."""
    quotas = {
        "Normal": 7,
        "Suspicious": 6,
        "Anomaly": 7,
    }

    samples = []
    for label, quota in quotas.items():
        label_df = df[df["ai_validation_label"] == label].copy()
        if label_df.empty:
            continue

        sort_columns = [
            "contamination",
            "candidate_overlap_group",
            "variant",
            "case_id",
        ]
        existing_sort_columns = [
            column for column in sort_columns if column in label_df.columns
        ]
        label_df = label_df.sort_values(existing_sort_columns)

        selected_parts = []
        remaining = label_df.copy()
        while len(selected_parts) < quota and not remaining.empty:
            group_cols = [
                column
                for column in ["contamination", "candidate_overlap_group", "variant"]
                if column in remaining.columns
            ]
            if group_cols:
                one_per_group = remaining.groupby(group_cols, dropna=False).head(1)
            else:
                one_per_group = remaining.head(1)
            remaining = remaining.drop(index=one_per_group.index)
            selected_parts.append(one_per_group)
            if sum(len(part) for part in selected_parts) >= quota:
                break

        samples.append(pd.concat(selected_parts, ignore_index=False).head(quota))

    audit_df = pd.concat(samples, ignore_index=True) if samples else pd.DataFrame()
    audit_df["human_label"] = ""
    audit_df["human_confidence"] = ""
    audit_df["human_notes"] = ""
    audit_df.to_csv(HUMAN_AUDIT_FILE, index=False)
    return audit_df


# ============================================================
# FIGURES
# ============================================================

def create_figures(df: pd.DataFrame, by_contamination_df: pd.DataFrame) -> None:
    """Create requested final-candidate AI validation figures."""
    FINAL_CANDIDATE_FIGURE_DIR.mkdir(parents=True, exist_ok=True)

    label_counts = (
        df.groupby(["contamination", "ai_validation_label"])
        .size()
        .unstack(fill_value=0)
        .reindex(columns=VALID_LABELS, fill_value=0)
    )

    ax = label_counts.plot(kind="bar", figsize=(9, 6))
    ax.set_title("AI-Assisted Validation Labels by Contamination")
    ax.set_xlabel("Contamination")
    ax.set_ylabel("Case count")
    ax.legend(title="AI label")
    plt.tight_layout()
    plt.savefig(LABEL_FIGURE_FILE, dpi=220)
    plt.close()

    comparison_plot = by_contamination_df.set_index("contamination")[
        [
            "anomaly_percentage",
            "suspicious_plus_anomaly_percentage",
        ]
    ]
    ax = comparison_plot.plot(kind="bar", figsize=(9, 6))
    ax.set_title("AI-Assisted Plausibility Comparison")
    ax.set_xlabel("Contamination")
    ax.set_ylabel("Percentage of cases")
    ax.legend(["AI Anomaly", "AI Suspicious + Anomaly"])
    plt.tight_layout()
    plt.savefig(COMPARISON_FIGURE_FILE, dpi=220)
    plt.close()


# ============================================================
# PRINT SUMMARY
# ============================================================

def print_summary(
    df: pd.DataFrame,
    by_contamination_df: pd.DataFrame,
    audit_df: pd.DataFrame,
) -> None:
    """Print requested terminal summary."""
    print("\nAI-ASSISTED VALIDATION SUMMARY")
    print("=" * 70)
    print(f"Total cases: {len(df)}")

    print("\nOverall:")
    for label in VALID_LABELS:
        print(f"{label}: {int((df['ai_validation_label'] == label).sum())}")

    for _, row in by_contamination_df.iterrows():
        print(f"\n{row['contamination']}:")
        print(f"Normal: {int(row['ai_normal'])}")
        print(f"Suspicious: {int(row['ai_suspicious'])}")
        print(f"Anomaly: {int(row['ai_anomaly'])}")

    print()
    for confidence in ["High", "Medium", "Low"]:
        print(
            f"{confidence} confidence: "
            f"{int((df['ai_validation_confidence'] == confidence).sum())}"
        )

    print(f"\nHuman audit sample: {len(audit_df)} cases")
    if not audit_df.empty:
        print(audit_df[["case_id", "ai_validation_label"]].to_string(index=False))

    print("\nFiles created:")
    for path in [
        RESULTS_FILE,
        SUMMARY_FILE,
        BY_CONTAMINATION_FILE,
        HUMAN_AUDIT_FILE,
        MANUAL_REVIEW_DIR / "ai_validation_detector_comparison.csv",
        LABEL_FIGURE_FILE,
        COMPARISON_FIGURE_FILE,
    ]:
        print(f"  {path}")


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 70)
    print("AI-ASSISTED CASE VALIDATION")
    print("=" * 70)
    print("This is not human manual validation and not ground truth labeling.")

    df = load_or_create_results()
    save_checkpoint(df)

    df = run_validation(df)
    if df["ai_validation_label"].str.strip().eq("").any():
        print("Validation is incomplete. Skipping quality control and analysis outputs.")
        return

    quality_control(df)

    summary_df = build_summary(df)
    by_contamination_df = analyze_by_contamination(df)
    _ = compare_detectors_with_ai(df)
    audit_df = create_human_audit_sample(df)
    create_figures(df, by_contamination_df)

    print_summary(df, by_contamination_df, audit_df)
    print("\nDone.")


if __name__ == "__main__":
    main()
