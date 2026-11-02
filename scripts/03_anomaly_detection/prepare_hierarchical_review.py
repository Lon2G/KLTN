"""Concrete blind review rubric and guarded provisional notes; never auto-label."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import review_business_cases as review


ROOT = review.ROOT
VERSION = "hierarchical_review_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_hierarchical_review_v1"
DEFAULT_JOURNAL = ROOT/"data/manual_review/hierarchical_review_notes_v1/journal.json"
TEMPLATE = ROOT/"templates/hierarchical_review_v1"
DOCUMENTS = ["README.md", "reviewer_guide.md", "criteria.json", "evaluation_plan.json"]
TIMES = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date"]
SCOPES = {
    "process_timing": {
        "cases.csv": ["order_status", *TIMES, "order_estimated_delivery_date", "purchased_to_approved_days",
                      "approved_to_carrier_days", "carrier_to_delivered_days", "total_cycle_time_days"]},
    "payment": {
        "cases.csv": ["payment_value_sum", "payment_record_count", "payment_types_json", "payment_type_count",
                      "payment_installments_max", "payment_sequential_max", "item_price_sum", "freight_sum",
                      "items_plus_freight_sum", "payment_minus_items_and_freight"],
        "payment_records.csv": review.PAYMENT_COLUMNS,
        "item_records.csv": ["order_item_id", "price", "freight_value"]},
    "order_contents": {
        "cases.csv": ["item_count", "product_count", "seller_count", "item_price_sum", "freight_sum",
                      "item_price_max", "item_price_mean", "freight_to_item_price_ratio"],
        "item_records.csv": review.ITEM_COLUMNS},
}
REQUIRED_NORMAL = {
    "process_timing": ["cases.csv."+field for field in TIMES+[
        "order_estimated_delivery_date", "purchased_to_approved_days", "approved_to_carrier_days", "carrier_to_delivered_days"]],
    "payment": ["cases.csv."+field for field in ["payment_value_sum", "payment_record_count", "items_plus_freight_sum"]]
               + ["payment_records.csv."+field for field in review.PAYMENT_COLUMNS]
               + ["item_records.csv.price", "item_records.csv.freight_value"],
    "order_contents": ["cases.csv."+field for field in ["item_count", "product_count", "seller_count", "item_price_sum", "freight_sum"]]
                      + ["item_records.csv."+field for field in review.ITEM_COLUMNS],
}
PACKET_FILES = {f"reviewer_packet/reviewer/{name}" for name in [*review.SCHEMAS, "README.md", "criteria_draft.json", "manifest.json"]}
OUTPUT_FILES = PACKET_FILES | {"README.md", "evaluation_plan.json", "summary.json", "reviewer_packet/README.md",
                              "reviewer_packet/criteria.json", "reviewer_packet/review_tasks.json", "reviewer_packet/note_template.json"}


def note_template(review_id="", scope=""):
    return {"review_id": review_id, "analysis_scope": scope, "review_status": "not_reviewed",
            "parent_label": "", "anomaly_subtype": "", "reason_codes": [], "evidence_fields": [], "reason": "",
            "verification_reference": "", "comparison_reference": "", "confidence": "", "missing_evidence": "",
            "proposed_use_action": ""}


def code_hashes():
    return {str(path.relative_to(ROOT)): review.digest(path)
            for path in [Path(__file__), Path(review.__file__), *[TEMPLATE/name for name in DOCUMENTS]]}


def verify_files(directory, hashes):
    directory = Path(directory).resolve()
    for name, expected in hashes.items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or review.digest(path) != expected:
            raise ValueError(f"Review protocol file changed: {name}")


def load_inputs(batch=review.DEFAULT_BATCH):
    batch = Path(batch).resolve()
    frames, evidence_hash = review.load_evidence(batch)
    cases = frames["cases.csv"]
    if len(cases) != 120 or not cases.product_category_name.eq("cama_mesa_banho").all():
        raise ValueError("Expected original 120-case bed_bath_table pilot")
    provenance = {"batch_directory": str(batch), "reviewer_manifest_sha256": evidence_hash, "code_hashes": code_hashes()}
    verify_sources(provenance)
    return frames, provenance


def verify_sources(provenance):
    batch = Path(provenance["batch_directory"])
    if review.digest(batch/"reviewer/manifest.json") != provenance["reviewer_manifest_sha256"]:
        raise ValueError("Original blind evidence manifest changed")
    review.load_evidence(batch)
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Hierarchical review code/rubric changed; use a new version")


def tasks_for(frames):
    return [note_template(review_id, scope) for review_id in frames["cases.csv"].review_id for scope in SCOPES]


def destination_guard(path, provenance):
    path = Path(path).resolve()
    protected = [ROOT/name for name in ["scripts", "templates", "tests", "models", ".git", "data/raw", "data/processed",
                                       "data/imported", "data/import_sources"]]
    protected.append(Path(provenance["batch_directory"]))
    if any(path == root or path.is_relative_to(root) or root.is_relative_to(path) for root in protected):
        raise ValueError("Destination overlaps a protected source/code/pilot directory")
    if any((parent/"manifest.json").exists() for parent in path.parents):
        raise ValueError("Destination is inside an immutable snapshot")
    return path


def prepare(output=DEFAULT_OUTPUT, batch=review.DEFAULT_BATCH):
    frames, provenance = load_inputs(batch)
    output = destination_guard(output, provenance)
    if output.exists():
        raise FileExistsError("Refusing to overwrite hierarchical review package")
    summary = {"status": "prepared_technical_draft_no_assessments", "pilot_orders": len(frames["cases.csv"]),
               "payment_records": len(frames["payment_records.csv"]), "item_records": len(frames["item_records.csv"]),
               "analysis_scopes": list(SCOPES), "review_tasks": len(tasks_for(frames)), "assessed_tasks": 0,
               "unreviewed_tasks": len(tasks_for(frames)), "checklist_rules": len(review.read_json(TEMPLATE/"criteria.json")["rules"]),
               "original_blind_packet_byte_identical": True, "provisional_note_entry_supported": True,
               **{key: False for key in ["reference_labels_created", "advisor_approval_inferred", "old_labels_converted",
                                         "new_cases_sampled", "model_outputs_read", "reviewers_assigned", "training_performed",
                                         "test_scored", "accuracy_computed", "split_changed", "training_exclusions_applied"]}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".hierarchical-review-", dir=output.parent) as temporary:
        staging = Path(temporary)/"snapshot"
        target = staging/"reviewer_packet/reviewer"
        target.mkdir(parents=True)
        original = review.read_json(Path(batch)/"reviewer/manifest.json")
        for name in [*original["output_hashes"], "manifest.json"]:
            shutil.copyfile(Path(batch)/"reviewer"/name, target/name)
        review.load_evidence(staging/"reviewer_packet")
        for source, name in [("README.md", "README.md"), ("reviewer_guide.md", "reviewer_packet/README.md"),
                             ("criteria.json", "reviewer_packet/criteria.json"), ("evaluation_plan.json", "evaluation_plan.json")]:
            shutil.copyfile(TEMPLATE/source, staging/name)
        review.write_json(staging/"reviewer_packet/review_tasks.json", tasks_for(frames))
        review.write_json(staging/"reviewer_packet/note_template.json", note_template())
        review.write_json(staging/"summary.json", summary)
        manifest = {"version": VERSION, "status": "complete", "lifecycle": "technical_draft_not_reference_ready",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": provenance, "summary": summary,
                    "output_hashes": {str(path.relative_to(staging)): review.digest(path)
                                      for path in sorted(staging.rglob("*")) if path.is_file()}}
        review.write_json(staging/"manifest.json", manifest)
        verify_sources(provenance)
        load_package(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_package(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or manifest.get("lifecycle") != "technical_draft_not_reference_ready"
            or set(manifest.get("output_hashes", {})) != OUTPUT_FILES):
        raise ValueError("Invalid hierarchical review package")
    verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    frames, evidence_hash = review.load_evidence(directory/"reviewer_packet")
    if evidence_hash != manifest["provenance"]["reviewer_manifest_sha256"]:
        raise ValueError("Copied blind evidence binding differs")
    if (review.read_json(directory/"summary.json") != manifest["summary"]
            or review.read_json(directory/"reviewer_packet/criteria.json") != review.read_json(TEMPLATE/"criteria.json")
            or review.read_json(directory/"evaluation_plan.json") != review.read_json(TEMPLATE/"evaluation_plan.json")
            or review.read_json(directory/"reviewer_packet/review_tasks.json") != tasks_for(frames)
            or review.read_json(directory/"reviewer_packet/note_template.json") != note_template()):
        raise ValueError("Rubric, plan or unassessed tasks differ")
    return frames, manifest


def case_frames(frames, review_id):
    if not isinstance(review_id, str) or review_id not in set(frames["cases.csv"].review_id):
        raise ValueError("Unknown blind review ID; raw order IDs are not accepted")
    return {name: frame.loc[frame.review_id.eq(review_id)] for name, frame in frames.items()}


def scope_values(selected, scope):
    if scope not in SCOPES:
        raise ValueError("Unknown analysis scope")
    return {f"{table}.{field}": selected[table][field].tolist()
            for table, fields in SCOPES[scope].items() for field in fields}


def meaningful(value):
    return isinstance(value, str) and value.strip().lower() not in {"", "todo", "tbd", "unknown", "n/a", "pending"}


def validate_note(frames, note):
    if not isinstance(note, dict) or set(note) != set(note_template()):
        raise ValueError("Unexpected assessment fields")
    for name, value in note.items():
        if name not in {"reason_codes", "evidence_fields"} and not isinstance(value, str):
            raise ValueError("Assessment fields must be text")
    selected = case_frames(frames, note["review_id"])
    values = scope_values(selected, note["analysis_scope"])
    scope = note["analysis_scope"]
    rules = {rule["code"] for rule in review.read_json(TEMPLATE/"criteria.json")["rules"] if rule["scope"] == scope}
    for field, allowed in [("reason_codes", rules), ("evidence_fields", set(values))]:
        entries = note[field]
        if (not isinstance(entries, list) or not entries or not all(isinstance(x, str) for x in entries)
                or len(set(entries)) != len(entries) or not set(entries).issubset(allowed)):
            raise ValueError(f"Cite distinct allowed {field}; no model outputs as evidence")
    if not meaningful(note["reason"]) or note["confidence"] not in {"low", "medium", "high"}:
        raise ValueError("An explicit reason and ordinal confidence are required")
    parent, subtype, action = note["parent_label"], note["anomaly_subtype"], note["proposed_use_action"]
    if note["review_status"] == "unresolved":
        if parent or subtype or action != "pending" or not meaningful(note["missing_evidence"]):
            raise ValueError("Unresolved evidence needs a missing-evidence reason, blank labels and pending action")
    elif note["review_status"] == "completed":
        if parent == "no_supported_anomaly":
            if subtype or note["missing_evidence"].strip() or action not in {"retain", "pending"}:
                raise ValueError("No-supported-anomaly assessment cannot contain subtype, missing evidence or exclusion")
            if set(note["reason_codes"]) != rules:
                raise ValueError("A negative assessment must acknowledge all scope checks")
            if not meaningful(note["comparison_reference"]):
                raise ValueError("A negative assessment also needs a declared comparison reference")
            if any(not values[field] or any(not value.strip() for value in values[field]) for field in REQUIRED_NORMAL[scope]):
                raise ValueError("Required source evidence is missing; do not declare the scope normal")
        elif parent == "anomaly":
            if subtype not in {"data_error", "valid_deviation", "unclassified"} or not meaningful(note["verification_reference"]):
                raise ValueError("An anomaly needs a defined subtype and explicit verification reference")
            if subtype == "data_error" and action not in {"exclude_from_named_analysis", "pending"}:
                raise ValueError("Confirmed-invalid inputs cannot be proposed for numeric retention")
            if subtype == "valid_deviation" and (action not in {"retain", "pending"}
                    or not meaningful(note["comparison_reference"]) or note["missing_evidence"].strip()):
                raise ValueError("Valid deviation requires comparison support, no missing evidence and no automatic exclusion")
            if subtype == "unclassified" and (action != "pending" or not meaningful(note["missing_evidence"])):
                raise ValueError("Unknown subtype must retain pending action and describe missing evidence")
        else:
            raise ValueError("Unknown parent label; legacy labels cannot be converted automatically")
    else:
        raise ValueError("Only explicit completed or unresolved assessments may be submitted")
    return {field: values[field] for field in note["evidence_fields"]}


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def journal_binding(directory, manifest):
    return {"version": VERSION, "package_manifest_sha256": review.digest(Path(directory)/"manifest.json"),
            "reviewer_evidence_manifest_sha256": manifest["provenance"]["reviewer_manifest_sha256"],
            "role": "provisional_human_notes_not_approved_reference_labels_not_applied"}


def validate_reviewer(reviewer_id):
    if not isinstance(reviewer_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", reviewer_id):
        raise ValueError("An explicit reviewer ID is required")


def load_journal(path=DEFAULT_JOURNAL, directory=DEFAULT_OUTPUT):
    frames, manifest = load_package(directory)
    path = Path(path)
    binding = journal_binding(directory, manifest)
    if not path.exists():
        return {**binding, "events": []}, None
    before = review.digest(path)
    journal = review.read_json(path)
    if set(journal) != set(binding) | {"events"} or any(journal.get(key) != value for key, value in binding.items()):
        raise ValueError("Journal package/protocol binding differs")
    if not isinstance(journal["events"], list):
        raise ValueError("Journal events must be a list")
    previous = ""
    fields = {"revision", "previous_sha256", "reviewer_id", "recorded_at_utc", "assessment", "source_values", "sha256"}
    for number, event in enumerate(journal["events"], 1):
        if not isinstance(event, dict) or set(event) != fields:
            raise ValueError("Unexpected journal event fields")
        if (event["revision"] != number or event["previous_sha256"] != previous
                or event["sha256"] != payload_hash({key: value for key, value in event.items() if key != "sha256"})):
            raise ValueError("Journal revision/hash chain changed")
        validate_reviewer(event["reviewer_id"])
        if datetime.fromisoformat(event["recorded_at_utc"]).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("Journal recording time must be timezone-aware UTC")
        if event["source_values"] != validate_note(frames, event["assessment"]):
            raise ValueError("Journal source evidence changed")
        previous = event["sha256"]
    if review.digest(path) != before:
        raise ValueError("Concurrent journal change; reload")
    return journal, before


def append_note(note, reviewer_id, expected_hash, path=DEFAULT_JOURNAL, directory=DEFAULT_OUTPUT):
    validate_reviewer(reviewer_id)
    frames, manifest = load_package(directory)
    values = validate_note(frames, note)
    path = destination_guard(path, manifest["provenance"])
    if path.is_relative_to(Path(directory).resolve()):
        raise ValueError("Journal must remain outside the immutable review package")
    journal, current_hash = load_journal(path, directory)
    if current_hash != expected_hash:
        raise ValueError("Stale journal; reload before recording")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name+".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Journal write lock exists; inspect before retrying") from exc
    os.close(descriptor)
    try:
        if (review.digest(path) if path.exists() else None) != expected_hash:
            raise ValueError("Concurrent journal change; no note overwritten")
        event = {"revision": len(journal["events"])+1,
                 "previous_sha256": journal["events"][-1]["sha256"] if journal["events"] else "",
                 "reviewer_id": reviewer_id, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                 "assessment": note, "source_values": values}
        event["sha256"] = payload_hash(event)
        journal["events"].append(event)
        with tempfile.TemporaryDirectory(prefix=".hierarchical-note-", dir=path.parent) as temporary:
            staging = Path(temporary)/"journal.json"
            review.write_json(staging, journal)
            load_journal(staging, directory)
            if (review.digest(path) if path.exists() else None) != expected_hash:
                raise ValueError("Concurrent journal change; no note overwritten")
            staging.replace(path)
        return event
    finally:
        lock.unlink()


def preview(directory, review_id):
    frames, _ = load_package(directory)
    selected = case_frames(frames, review_id)
    return {"review_id": review_id, "role": "blinded_evidence_for_provisional_review_not_model_verdict",
            "scope_source_fields": {scope: scope_values(selected, scope) for scope in SCOPES},
            "note_templates": [note_template(review_id, scope) for scope in SCOPES],
            "unsupported_targets": review.read_json(TEMPLATE/"criteria.json")["unsupported_targets"]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prepare", action="store_true")
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--preview")
    mode.add_argument("--check-note", type=Path)
    mode.add_argument("--record-note", type=Path)
    mode.add_argument("--history", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--reviewer-id")
    args = parser.parse_args(argv)
    try:
        if args.reviewer_id and not args.record_note:
            raise ValueError("--reviewer-id is only used with --record-note")
        if args.prepare:
            result = prepare(args.output)
        elif args.summary:
            result = load_package(args.output)[1]["summary"]
        elif args.preview:
            result = preview(args.output, args.preview)
        elif args.history:
            result = load_journal(args.journal, args.output)[0]
        else:
            path = args.check_note or args.record_note
            before = review.digest(path)
            note = review.read_json(path)
            if review.digest(path) != before:
                raise ValueError("Input note changed while read")
            if args.check_note:
                values = validate_note(load_package(args.output)[0], note)
                result = {"syntax_valid": True, "verification_authenticated": False, "reference_ready": False,
                          "source_values": values, "saved": False}
            else:
                _, expected = load_journal(args.journal, args.output)
                result = append_note(note, args.reviewer_id, expected, args.journal, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Hierarchical review stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
