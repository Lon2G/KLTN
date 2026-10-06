"""Blinded evidence preview and guarded human entry; never assigns a label itself."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BATCH = ROOT / "data/manual_review/business_review_pilot_v1"
VERSION = "business_review_pilot_v1"
LABELS = ["Normal", "Suspicious", "Anomaly"]
CONFIDENCES = ["Low", "Medium", "High"]
REASONS = ["timing_observation", "payment_amount", "payment_structure", "order_contents",
           "source_consistency", "insufficient_context", "within_review_criteria", "other"]
REVIEW_COLUMNS = ["reviewer_label", "reviewer_confidence", "reviewer_reason", "reviewer_notes", "reviewed_at_utc"]
CONTEXT_COLUMNS = ["order_status", "product_category_name", "order_purchase_timestamp", "order_approved_at",
                   "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date",
                   "purchased_to_approved_days", "approved_to_carrier_days", "carrier_to_delivered_days", "total_cycle_time_days"]
FEATURE_COLUMNS = ["payment_value_sum", "payment_record_count", "payment_types_json", "payment_type_count",
                   "payment_installments_max", "payment_sequential_max", "item_count", "product_count", "seller_count",
                   "item_price_sum", "freight_sum", "items_plus_freight_sum", "item_price_max", "item_price_mean",
                   "freight_to_item_price_ratio", "payment_minus_items_and_freight"]
PAYMENT_COLUMNS = ["payment_sequential", "payment_type", "payment_installments", "payment_value"]
ITEM_COLUMNS = ["order_item_id", "product_id", "seller_id", "shipping_limit_date", "price", "freight_value"]
SCHEMAS = {"cases.csv": ["review_id", *CONTEXT_COLUMNS, *FEATURE_COLUMNS, *REVIEW_COLUMNS],
           "payment_records.csv": ["review_id", *PAYMENT_COLUMNS], "item_records.csv": ["review_id", *ITEM_COLUMNS]}
CRITERIA_FIELDS = {"version", "status", "protocol_id", "target_definition", "normal_criteria", "suspicious_criteria",
                   "anomaly_criteria", "insufficient_evidence_policy", "approved_by", "approval_reference"}


def digest(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=unique_keys)


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, ensure_ascii=True)+"\n")


def read_strings(path):
    return pd.read_csv(path, dtype="string", keep_default_na=False)


def load_evidence(batch=DEFAULT_BATCH):
    """Only open reviewer-facing files, never the analyst key or model artifacts."""
    directory = Path(batch)/"reviewer"
    manifest = read_json(directory/"manifest.json")
    required = {*SCHEMAS, "criteria_draft.json", "README.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "blank_pilot_pending_criteria"
            or set(manifest.get("output_hashes", {})) != required):
        raise ValueError("Invalid blind evidence manifest")
    for name, expected in manifest["output_hashes"].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or digest(path) != expected:
            raise ValueError(f"Blind evidence changed: {name}")
    frames = {name: read_strings(directory/name) for name in SCHEMAS}
    for name, columns in SCHEMAS.items():
        if frames[name].columns.tolist() != columns:
            raise ValueError(f"Unexpected blind evidence schema: {name}")
    cases = frames["cases.csv"]
    if (cases.empty or not cases.review_id.is_unique or len(cases) != manifest["case_count"]
            or not cases.review_id.str.fullmatch(r"BR[0-9]{4,}").all()
            or cases[REVIEW_COLUMNS].ne("").any().any()):
        raise ValueError("Expected unique pseudonyms and entirely blank source labels")
    for name in ["payment_records.csv", "item_records.csv"]:
        if not set(frames[name].review_id).issubset(set(cases.review_id)):
            raise ValueError("Evidence belongs to a case outside this pilot")
    for name, count in [("payment_records.csv", "payment_record_count"), ("item_records.csv", "item_count")]:
        actual = frames[name].groupby("review_id").size().reindex(cases.review_id, fill_value=0).to_numpy()
        if not (actual == pd.to_numeric(cases[count]).to_numpy()).all():
            raise ValueError(f"Source record counts differ: {name}")
    return frames, digest(directory/"manifest.json")


def validate_criteria(value):
    if set(value) != CRITERIA_FIELDS or value.get("version") != VERSION or value.get("status") != "approved":
        raise ValueError("Label entry is blocked: a recorded approved criteria protocol is required")
    for key in CRITERIA_FIELDS - {"version", "status"}:
        field = value[key]
        if not isinstance(field, str) or not field.strip() or field.strip().lower() in {"todo", "tbd", "pending", "draft", "n/a"}:
            raise ValueError(f"Approved criteria must specify {key}")
    return value


def validate_progress(frame, review_ids):
    if frame.columns.tolist() != ["review_id", *REVIEW_COLUMNS] or frame.review_id.tolist() != list(review_ids):
        raise ValueError("Review progress IDs/schema changed")
    if frame.isna().any().any():
        raise ValueError("Progress must use empty strings for unanswered fields")
    fields = frame[REVIEW_COLUMNS].apply(lambda column: column.str.strip())
    complete = fields.reviewer_label.ne("")
    if fields.loc[~complete].ne("").any().any() or fields.loc[complete].eq("").any().any():
        raise ValueError("Partial review decisions are not allowed")
    for column, allowed in [("reviewer_label", LABELS), ("reviewer_confidence", CONFIDENCES), ("reviewer_reason", REASONS)]:
        if not frame.loc[complete, column].isin(allowed).all():
            raise ValueError(f"Unknown {column}")
    for value in frame.loc[complete, "reviewed_at_utc"]:
        try:
            recorded = datetime.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Invalid review audit timestamp") from exc
        if recorded.utcoffset() != timezone.utc.utcoffset(recorded):
            raise ValueError("Review audit timestamp must be timezone-aware UTC")
    return frame


def session_path(batch, reviewer_id):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", reviewer_id):
        raise ValueError("Reviewer ID must be 1-64 letters, digits, underscores or hyphens")
    root = Path(batch)/"sessions"
    path = root/reviewer_id
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Invalid reviewer session path")
    return path


def open_session(batch, reviewer_id, criteria_path):
    criteria_path = Path(criteria_path)
    before = digest(criteria_path)
    criteria = validate_criteria(read_json(criteria_path))
    if digest(criteria_path) != before:
        raise ValueError("Criteria changed while being read")
    frames, evidence_hash = load_evidence(batch)
    ids = frames["cases.csv"].review_id.tolist()
    directory = session_path(batch, reviewer_id)
    binding = {"version": VERSION, "reviewer_id": reviewer_id, "evidence_manifest_sha256": evidence_hash,
               "criteria_source_sha256": before}
    if not directory.exists():
        directory.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix=".review-session-", dir=directory.parent) as temp:
            staging = Path(temp)/"session"
            staging.mkdir()
            write_json(staging/"approved_criteria.json", criteria)
            metadata = {**binding, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "approved_criteria_sha256": digest(staging/"approved_criteria.json")}
            write_json(staging/"session.json", metadata)
            progress = frames["cases.csv"][["review_id", *REVIEW_COLUMNS]]
            progress.to_csv(staging/"review_progress.csv", index=False)
            if directory.exists():
                raise FileExistsError("Another process created this reviewer session")
            staging.rename(directory)
    metadata = read_json(directory/"session.json")
    if (any(metadata.get(key) != value for key, value in binding.items())
            or digest(directory/"approved_criteria.json") != metadata.get("approved_criteria_sha256")
            or read_json(directory/"approved_criteria.json") != criteria):
        raise ValueError("Session reviewer, criteria or evidence binding changed")
    path = directory/"review_progress.csv"
    expected_hash = digest(path)
    progress = validate_progress(read_strings(path), ids)
    if digest(path) != expected_hash:
        raise ValueError("Concurrent progress change; reopen the session")
    return frames, progress, directory, expected_hash, digest(directory/"session.json"), criteria


def save_progress(batch, directory, progress, expected_hash, session_hash):
    directory = Path(directory)
    lock = directory/".write.lock"
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Review write lock exists; another writer or interrupted save needs inspection") from exc
    os.close(descriptor)
    try:
        frames, evidence_hash = load_evidence(batch)
        metadata = read_json(directory/"session.json")
        if (digest(directory/"session.json") != session_hash or metadata["evidence_manifest_sha256"] != evidence_hash
                or digest(directory/"approved_criteria.json") != metadata["approved_criteria_sha256"]):
            raise ValueError("Session/evidence/criteria changed during review")
        validate_criteria(read_json(directory/"approved_criteria.json"))
        validate_progress(progress, frames["cases.csv"].review_id)
        path = directory/"review_progress.csv"
        if digest(path) != expected_hash:
            raise ValueError("Concurrent progress change; no labels were overwritten")
        with tempfile.TemporaryDirectory(prefix=".save-", dir=directory) as temp:
            staging = Path(temp)/"progress.csv"
            progress.to_csv(staging, index=False)
            validate_progress(read_strings(staging), frames["cases.csv"].review_id)
            if digest(path) != expected_hash:
                raise ValueError("Concurrent progress change; no labels were overwritten")
            staging.replace(path)
        return digest(path)
    finally:
        lock.unlink()


def show_case(frames, review_id, output=print):
    selected = frames["cases.csv"].loc[lambda value: value.review_id.eq(review_id)]
    if len(selected) != 1:
        raise ValueError("Unknown review ID")
    output(f"\nCase {review_id} | raw observations, not a model verdict")
    for column in CONTEXT_COLUMNS + FEATURE_COLUMNS:
        output(f"{column}: {selected.iloc[0][column] or '[missing]'}")
    for name in ["payment_records.csv", "item_records.csv"]:
        output(f"\n{name}\n{frames[name].loc[lambda value: value.review_id.eq(review_id)].to_string(index=False)}")


def choose(prompt, choices, input_fn, output):
    while True:
        output(" | ".join(f"{index}: {value}" for index, value in enumerate(choices, 1)))
        answer = input_fn(prompt).strip()
        if answer == "q":
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(choices):
            return choices[int(answer)-1]
        output("Choose a listed number, or q to stop without saving this decision.")


def run_review(batch, reviewer_id, criteria_path, input_fn=input, output=print):
    frames, progress, directory, expected_hash, session_hash, criteria = open_session(batch, reviewer_id, criteria_path)
    output(json.dumps(criteria, indent=2, ensure_ascii=False))
    output("Confidence is a human ordinal judgment, not an anomaly probability. q stops; s skips.")
    for index in progress.index[progress.reviewer_label.eq("")]:
        review_id = progress.at[index, "review_id"]
        show_case(frames, review_id, output)
        command = input_fn("Enter to review, s to skip, q to stop: ").strip()
        if command == "q":
            break
        if command != "":
            continue
        answers = []
        for prompt, options in [("Label: ", LABELS), ("Confidence: ", CONFIDENCES), ("Reason: ", REASONS)]:
            answer = choose(prompt, options, input_fn, output)
            if answer is None:
                return
            answers.append(answer)
        note = input_fn("Evidence and criterion supporting your decision (required, q stops): ").strip()
        if note == "q":
            return
        if not note:
            output("No evidence note provided; this case remains unanswered.")
            continue
        if input_fn("Type save to record this human decision: ").strip() != "save":
            output("Decision not saved.")
            continue
        progress.loc[index, REVIEW_COLUMNS] = [*answers, note, datetime.now(timezone.utc).isoformat()]
        expected_hash = save_progress(batch, directory, progress, expected_hash, session_hash)
        output(f"Saved {review_id}. Completed: {int(progress.reviewer_label.ne('').sum())}/{len(progress)}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, default=DEFAULT_BATCH)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--preview", metavar="REVIEW_ID")
    mode.add_argument("--review", action="store_true")
    parser.add_argument("--reviewer-id")
    parser.add_argument("--approved-criteria", type=Path)
    args = parser.parse_args()
    try:
        if args.review:
            if not args.reviewer_id or not args.approved_criteria:
                raise ValueError("--review requires --reviewer-id and --approved-criteria; draft labels remain blank")
            run_review(args.batch, args.reviewer_id, args.approved_criteria)
        else:
            frames, _ = load_evidence(args.batch)
            if args.preview:
                show_case(frames, args.preview)
            else:
                print(json.dumps({"cases": len(frames["cases.csv"]), "source_labels": "all_blank",
                                  "session_status": "not_inspected", "approval": "not_assessed",
                                  "note": "Read-only evidence check; no model quality result."}, indent=2))
    except (ValueError, OSError, KeyError, EOFError, KeyboardInterrupt) as exc:
        parser.exit(2, f"Review stopped: {exc}\n")


if __name__ == "__main__":
    main()
