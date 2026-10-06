"""Review a prepared batch using recorded status and timelines, with detectors hidden."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from auto_validation_evaluation import DURATION_FEATURES, file_sha256
from manual_validation_review import (
    CONFIDENCE_CHOICES, LABEL_CHOICES, REASON_CHOICES, prompt_action,
    prompt_confidence, prompt_notes,
)
from order_status_context_audit import BLIND_COLUMNS, REVIEW_COLUMNS, TIMESTAMP_COLUMNS
from prepare_status_context_review import DEFAULT_BATCH_DIR


CONTEXT_REASONS = {
    **REASON_CHOICES,
    "10": "Recorded status can explain missing later milestones",
    "11": "Recorded status and timestamps need reconciliation",
    "12": "Observation cutoff or status history is unavailable",
    "13": "Short duration needs contextual review",
}
PROGRESS_COLUMNS = [*BLIND_COLUMNS, "reviewed_at_utc"]


def load_review(batch_dir: Path) -> pd.DataFrame:
    blind_path = batch_dir / "review_cases_blind.csv"
    manifest = json.loads((batch_dir / "analyst_only/manifest.json").read_text(encoding="utf-8"))
    if file_sha256(blind_path) != manifest["output_sha256"]["review_cases_blind.csv"]:
        raise ValueError("Blind input changed after preparation; inspect the batch before review")
    original = pd.read_csv(blind_path, dtype="string").fillna("")
    if list(original.columns) != BLIND_COLUMNS or original.case_id.duplicated().any():
        raise ValueError("Invalid blind input schema or duplicate case_id")
    if original[REVIEW_COLUMNS].ne("").any().any():
        raise ValueError("Prepared blind input must not contain human labels")
    progress_path = batch_dir / "review_progress.csv"
    if not progress_path.exists():
        return original.assign(reviewed_at_utc="")
    progress = pd.read_csv(progress_path, dtype="string").fillna("")
    if list(progress.columns) != PROGRESS_COLUMNS:
        raise ValueError("Unexpected progress columns")
    protected = [column for column in BLIND_COLUMNS if column not in REVIEW_COLUMNS]
    if not original[protected].equals(progress[protected]):
        raise ValueError("Progress changed case IDs, order or source fields")
    if not progress.reviewer_label.isin(["", *LABEL_CHOICES.values()]).all():
        raise ValueError("Unsupported reviewer label")
    reviewed = progress.reviewer_label.ne("")
    if not progress.loc[reviewed, "reviewer_confidence"].isin(CONFIDENCE_CHOICES.values()).all():
        raise ValueError("Reviewed case lacks a valid confidence")
    if progress.loc[reviewed, ["reviewer_reason", "reviewed_at_utc"]].eq("").any().any():
        raise ValueError("Reviewed case lacks a reason or actual review timestamp")
    return progress


def save_progress(frame: pd.DataFrame, batch_dir: Path) -> None:
    path = batch_dir / "review_progress.csv"
    temporary = path.with_suffix(".csv.tmp")
    frame.to_csv(temporary, index=False)
    temporary.replace(path)


def display_case(row: pd.Series, position: int, total: int) -> None:
    print("\n" + "-" * 70)
    print(f"STATUS-CONTEXT BLIND REVIEW | Case {position + 1}/{total} | {row.review_id}")
    print(f"Case ID: {row.case_id}")
    print(f"Recorded order status: {row.order_status}")
    print(f"Sequence: {row.event_sequence}")
    print("\nRECORDED TIMESTAMPS (N/A means not recorded):")
    for column in TIMESTAMP_COLUMNS:
        print(f"{column}: {row[column] or 'N/A'}")
    print(f"Missing milestones: {row.missing_activities or 'None'}")
    print("\nMEASURED DURATIONS:")
    for column in DURATION_FEATURES:
        duration = f"{float(row[column]):.4f} days" if row[column] else "N/A"
        print(f"{column}: {duration}")
    print(f"Delivery minus estimated date (calendar days): {row.delivery_vs_estimate_calendar_days or 'N/A'}")
    print("Observation cutoff: unavailable; no pending duration is inferred.")
    if row.reviewer_label:
        print(f"\nYour saved decision: {row.reviewer_label} / {row.reviewer_confidence}")
        print(f"Your reason: {row.reviewer_reason}")
        print(f"Your notes: {row.reviewer_notes}")


def prompt_context_reasons() -> str:
    print("\nReason code(s), separated by commas:")
    for code, reason in CONTEXT_REASONS.items():
        print(f"[{code}] {reason}")
    while True:
        codes = list(dict.fromkeys(part.strip() for part in input("> ").split(",") if part.strip()))
        if codes and all(code in CONTEXT_REASONS for code in codes):
            return "; ".join(CONTEXT_REASONS[code] for code in codes)
        print("Choose at least one valid reason code; use notes for additional context.")


def run_review(batch_dir: Path) -> None:
    frame = load_review(batch_dir)
    remaining = frame.index[frame.reviewer_label.eq("")]
    if remaining.empty:
        print(f"All {len(frame)} cases already have recorded decisions.")
        return
    position = int(remaining[0])
    print("Review recorded facts in context. Model outputs stay hidden throughout this batch.")
    try:
        while 0 <= position < len(frame):
            display_case(frame.loc[position], position, len(frame))
            action = prompt_action()
            if action == "q":
                break
            if action == "s":
                position += 1
                continue
            if action == "b":
                position = max(0, position - 1)
                continue
            confidence = prompt_confidence()
            reason = prompt_context_reasons()
            notes = prompt_notes()
            frame.loc[position, REVIEW_COLUMNS] = [LABEL_CHOICES[action], confidence, reason, notes]
            frame.at[position, "reviewed_at_utc"] = datetime.now(timezone.utc).isoformat()
            save_progress(frame, batch_dir)
            print("Decision saved.")
            position += 1
    except (KeyboardInterrupt, EOFError):
        print("\nReview stopped; completed decisions are preserved.")
    save_progress(frame, batch_dir)
    reviewed = int(frame.reviewer_label.ne("").sum())
    print(f"Reviewed: {reviewed}/{len(frame)}. Remaining: {len(frame) - reviewed}.")
    print(f"Progress: {batch_dir / 'review_progress.csv'}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_DIR)
    args = parser.parse_args()
    run_review(args.batch_dir.resolve())


if __name__ == "__main__":
    main()
