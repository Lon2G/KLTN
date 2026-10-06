from pathlib import Path
import sys

import pandas as pd


# ============================================================
# PROJECT PATHS
# ============================================================

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import MANUAL_REVIEW_DIR


INPUT_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation.csv"
LABELED_FILE = MANUAL_REVIEW_DIR / "final_candidate_manual_validation_labeled.csv"

LABEL_CHOICES = {
    "1": "Normal",
    "2": "Suspicious",
    "3": "Anomaly",
}

CONFIDENCE_CHOICES = {
    "1": "Low",
    "2": "Medium",
    "3": "High",
}

REASON_CHOICES = {
    "1": "Normal process and normal timing",
    "2": "Missing activity",
    "3": "Reversed activity order",
    "4": "Very long transition duration",
    "5": "Multiple abnormal durations",
    "6": "Multivariate temporal pattern",
    "7": "Borderline / uncertain",
    "8": "Data quality concern",
    "9": "Other",
}

REVIEW_COLUMNS = [
    "reviewer_label",
    "reviewer_confidence",
    "reviewer_reason",
    "reviewer_notes",
]


# ============================================================
# DATA LOADING
# ============================================================

def first_existing_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    """Return the first available column from a candidate list."""
    for column in candidates:
        if column in df.columns:
            return column
    return None


def load_review_data() -> pd.DataFrame:
    """Load labeled progress if available; otherwise load original sample."""
    if LABELED_FILE.exists():
        print(f"Loading existing progress: {LABELED_FILE}")
        df = pd.read_csv(LABELED_FILE)
        for column in REVIEW_COLUMNS:
            if column not in df.columns:
                df[column] = ""
            df[column] = df[column].fillna("").astype(str)

        reviewed_count = (
            df["reviewer_label"]
            .fillna("")
            .astype(str)
            .str.strip()
            .ne("")
            .sum()
        )
        if reviewed_count == 0:
            print("No reviewed cases found. Recreating randomized working order.")
            df = pd.read_csv(INPUT_FILE).sample(
                frac=1,
                random_state=42,
            ).reset_index(drop=True)
            for column in REVIEW_COLUMNS:
                if column not in df.columns:
                    df[column] = ""
                df[column] = df[column].fillna("").astype(str)
            save_progress(df)
    else:
        print(f"Loading original manual validation sample: {INPUT_FILE}")
        df = pd.read_csv(INPUT_FILE).sample(
            frac=1,
            random_state=42,
        ).reset_index(drop=True)
        for column in REVIEW_COLUMNS:
            if column not in df.columns:
                df[column] = ""
            df[column] = df[column].fillna("").astype(str)
        save_progress(df)

    if "case_id" not in df.columns:
        raise ValueError("Manual validation file must contain 'case_id'.")

    for column in REVIEW_COLUMNS:
        if column not in df.columns:
            df[column] = ""
        df[column] = df[column].fillna("").astype(str)

    return df


def detect_columns(df: pd.DataFrame) -> dict[str, str | None]:
    """Detect optional columns with known aliases."""
    return {
        "if_flag": first_existing_column(
            df,
            ["candidate_if_flag", "experimental_if_flag", "isolation_forest_flag"],
        ),
        "vote_count": first_existing_column(
            df,
            ["final_candidate_vote_count", "refined_vote_count", "anomaly_vote_count"],
        ),
        "event_count": first_existing_column(
            df,
            ["event_count_from_log", "event_count"],
        ),
    }


def save_progress(df: pd.DataFrame) -> None:
    """Persist review progress after each labeled case."""
    df.to_csv(LABELED_FILE, index=False)


# ============================================================
# DISPLAY HELPERS
# ============================================================

def value(row: pd.Series, column: str | None, default: str = ""):
    """Read a value safely from a row."""
    if column is None or column not in row.index:
        return default
    current = row[column]
    if pd.isna(current):
        return default
    return current


def format_duration(row: pd.Series, column: str) -> str:
    """Format duration values in days."""
    current = value(row, column, "")
    if current == "":
        return "N/A"
    try:
        return f"{float(current):.4f} days"
    except (TypeError, ValueError):
        return str(current)


def print_case(
    row: pd.Series,
    position: int,
    total: int,
    columns: dict[str, str | None],
) -> None:
    """Print one case in a blind manual-validation layout."""
    progress = position / total * 100 if total else 0

    print("\n" + "-" * 70)
    print("MANUAL VALIDATION - BLIND REVIEW")
    print(f"Case {position} / {total}")
    print(f"Progress: {progress:.2f}%")
    print("-" * 70)
    print(f"Case ID: {value(row, 'case_id', 'N/A')}")
    print(f"Variant: {value(row, 'variant', 'N/A')}")
    print(f"Event sequence: {value(row, 'event_sequence', 'N/A')}")
    print(f"Event timestamps: {value(row, 'event_timestamps', 'N/A')}")

    print("\nPROCESS:")
    print(f"Trace fitness: {value(row, 'trace_fitness', 'N/A')}")
    print(f"Missing events: {value(row, 'missing_event_count', 'N/A')}")
    print(f"Reversed order: {value(row, 'reversed_order_flag', 'N/A')}")

    print("\nDURATIONS:")
    print(f"Purchased -> Approved: {format_duration(row, 'purchased_to_approved_days')}")
    print(f"Approved -> Carrier: {format_duration(row, 'approved_to_carrier_days')}")
    print(f"Carrier -> Delivered: {format_duration(row, 'carrier_to_delivered_days')}")
    print(f"Total cycle time: {format_duration(row, 'total_cycle_time_days')}")

    if value(row, "reviewer_label", ""):
        print("\nCURRENT REVIEW:")
        print(f"Label: {value(row, 'reviewer_label')}")
        print(f"Confidence: {value(row, 'reviewer_confidence')}")
        print(f"Reason: {value(row, 'reviewer_reason')}")
        print(f"Notes: {value(row, 'reviewer_notes')}")

    print("-" * 70)


def print_detector_information(row: pd.Series, columns: dict[str, str | None]) -> None:
    """Reveal detector information only after a human label has been saved."""
    print("\n" + "-" * 70)
    print("DETECTOR INFORMATION - REVEALED AFTER LABEL SAVE")
    print("-" * 70)
    print(f"Contamination: {value(row, 'contamination', 'N/A')}")
    print(f"Process deviation: {value(row, 'process_deviation_flag', 'N/A')}")
    print(f"Statistical anomaly: {value(row, 'statistical_anomaly_flag', 'N/A')}")
    print(f"Isolation Forest: {value(row, columns['if_flag'], 'N/A')}")
    print(f"Vote count: {value(row, columns['vote_count'], 'N/A')}")
    print(f"\nExisting anomaly reason:\n{value(row, 'anomaly_reason', 'N/A')}")
    print("-" * 70)


def prompt_reveal_detector_information() -> bool:
    """Ask whether to reveal detector information after label save."""
    choice = input("\nShow detector information? [y/N]\n> ").strip().lower()
    return choice == "y"


def prompt_action() -> str:
    """Prompt for review action."""
    print("\nChoose reviewer label:")
    print("[1] Normal")
    print("[2] Suspicious")
    print("[3] Anomaly")
    print("[s] Skip")
    print("[b] Previous case")
    print("[q] Save and quit")

    while True:
        choice = input("> ").strip().lower()
        if choice in {"1", "2", "3", "s", "b", "q"}:
            return choice
        print("Invalid choice. Enter 1, 2, 3, s, b, or q.")


def prompt_confidence() -> str:
    """Prompt reviewer confidence."""
    print("\nConfidence:")
    print("[1] Low")
    print("[2] Medium")
    print("[3] High")

    while True:
        choice = input("> ").strip()
        if choice in CONFIDENCE_CHOICES:
            return CONFIDENCE_CHOICES[choice]
        print("Invalid choice. Enter 1, 2, or 3.")


def prompt_reasons() -> str:
    """Prompt one or more reviewer reasons."""
    print("\nReviewer reason. Enter one or more numbers separated by commas:")
    for number, reason in REASON_CHOICES.items():
        print(f"{number}. {reason}")

    while True:
        raw = input("> ").strip()
        if not raw:
            return ""

        selected_numbers = [part.strip() for part in raw.split(",") if part.strip()]
        invalid = [number for number in selected_numbers if number not in REASON_CHOICES]
        if invalid:
            print(f"Invalid reason number(s): {', '.join(invalid)}")
            continue

        reasons = [REASON_CHOICES[number] for number in selected_numbers]
        if "9" in selected_numbers:
            other_text = input("Other reason text: ").strip()
            if other_text:
                reasons.append(f"Other: {other_text}")

        return "; ".join(reasons)


def prompt_notes() -> str:
    """Prompt optional notes."""
    print("\nReviewer notes. Press Enter to skip:")
    return input("> ").strip()


def choose_review_filter(df: pd.DataFrame) -> pd.Index:
    """Ask reviewer which subset to review."""
    print("\nReview:")
    print("[1] All remaining")
    print("[2] contamination 0.05 only")
    print("[3] contamination 0.08 only")
    print("Press Enter for All remaining.")

    choice = input("> ").strip()
    if choice == "2":
        return df.index[df["contamination"].astype(str) == "0.05"]
    if choice == "3":
        return df.index[df["contamination"].astype(str) == "0.08"]
    return df.index


# ============================================================
# SUMMARY
# ============================================================

def print_summary(df: pd.DataFrame) -> None:
    """Print manual-validation progress summary."""
    reviewed_mask = df["reviewer_label"].fillna("").astype(str).str.strip() != ""
    reviewed_df = df[reviewed_mask]

    print("\nMANUAL VALIDATION PROGRESS")
    print("=" * 70)
    print(f"Total cases: {len(df)}")
    print(f"Reviewed: {int(reviewed_mask.sum())}")
    print(f"Remaining: {int((~reviewed_mask).sum())}")

    print()
    for label in ["Normal", "Suspicious", "Anomaly"]:
        print(f"{label}: {int((reviewed_df['reviewer_label'] == label).sum())}")

    if "contamination" not in df.columns:
        return

    print("\nBy contamination:")
    for contamination in sorted(df["contamination"].dropna().unique()):
        sub_df = df[df["contamination"] == contamination]
        sub_reviewed = sub_df[
            sub_df["reviewer_label"].fillna("").astype(str).str.strip() != ""
        ]

        print(f"\n{contamination}:")
        print(f"reviewed: {len(sub_reviewed)}")
        for label in ["Normal", "Suspicious", "Anomaly"]:
            print(f"{label}: {int((sub_reviewed['reviewer_label'] == label).sum())}")


# ============================================================
# REVIEW LOOP
# ============================================================

def first_unreviewed_position(df: pd.DataFrame, review_indices: list[int]) -> int:
    """Find the first unreviewed position within the selected review indices."""
    for position, index in enumerate(review_indices):
        if not str(df.at[index, "reviewer_label"]).strip():
            return position
    return 0


def run_review() -> None:
    """Run the interactive terminal review tool."""
    df = load_review_data()
    columns = detect_columns(df)
    selected_indices = list(choose_review_filter(df))

    if not selected_indices:
        print("No cases match the selected filter.")
        print_summary(df)
        return

    current_position = first_unreviewed_position(df, selected_indices)

    while 0 <= current_position < len(selected_indices):
        row_index = selected_indices[current_position]
        row = df.loc[row_index]

        print_case(
            row,
            current_position + 1,
            len(selected_indices),
            columns,
        )

        action = prompt_action()
        if action == "q":
            save_progress(df)
            print_summary(df)
            return

        if action == "s":
            current_position += 1
            continue

        if action == "b":
            current_position = max(0, current_position - 1)
            continue

        df.at[row_index, "reviewer_label"] = LABEL_CHOICES[action]
        df.at[row_index, "reviewer_confidence"] = prompt_confidence()
        df.at[row_index, "reviewer_reason"] = prompt_reasons()
        df.at[row_index, "reviewer_notes"] = prompt_notes()

        save_progress(df)
        print(f"\nSaved progress to: {LABELED_FILE}")

        if prompt_reveal_detector_information():
            print_detector_information(df.loc[row_index], columns)

        current_position += 1

    save_progress(df)
    print("\nReview selection completed.")
    print_summary(df)


if __name__ == "__main__":
    run_review()
