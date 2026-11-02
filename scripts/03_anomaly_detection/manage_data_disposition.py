"""Source-bound data-use evidence and separate, revisioned analyst assessments."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

import pandas as pd

import inspect_process_evidence as inspection

ROOT = inspection.ROOT
review = inspection.review
shared = inspection.shared
VERSION = "data_disposition_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_data_disposition_v1"
DEFAULT_JOURNAL = ROOT/"data/manual_review/analyst_disposition_notes_v1/journal.json"
TEMPLATE = ROOT/"templates/data_disposition_v1"
DOCUMENTS = ["README.md", "implementation_plan.md"]
SCOPES = {
    "process_evidence": ["process_visible_events", "process_missing_source_events", "process_post_cutoff_events",
                         "process_inverted_pairs", "process_has_equal_timestamps", "process_replay_status"],
    "completed_timing": ["timing_input_eligible", "timing_input_reason", "inner_role",
                         "purchased_to_approved_days", "approved_to_carrier_days", "carrier_to_delivered_days"],
    "payment_order_features": list(inspection.integration.ablation.BUSINESS_FEATURES),
    "geographic_context": ["all_pairs_quality_eligible", "geography_quality_reasons",
                           *inspection.integration.delivery.context.DISTANCES],
    "seller_outcome_context": ["seller_count", "history_route", "single_seller_prior_promise_comparable_orders",
                               "single_seller_prior_after_promise_fraction", "single_seller_prior_reviewed_orders",
                               "single_seller_prior_mean_review_score"],
}
QUALITY = ["not_assessed", "insufficient_evidence", "suspected_error", "confirmed_error", "no_error_identified"]
ATYPICALITY = ["not_assessed", "insufficient_evidence", "suspected_deviation", "valid_deviation", "no_supported_deviation"]
ACTIONS = ["pending", "retain", "exclude_from_named_analysis"]
NOTE_FIELDS = {"order_id", "analysis_scope", "data_quality_assessment", "atypicality_assessment", "proposed_use_action",
               "supporting_fields", "verification_reference", "reason"}
DECISIONS = {"data_quality_assessment": "not_assessed", "atypicality_assessment": "not_assessed", "proposed_use_action": "pending"}
TABLES = {"analysis_use_ledger.csv", "coverage.csv"}


def code_hashes():
    return {**inspection.code_hashes(), **{str(p.relative_to(ROOT)): review.digest(p)
                                         for p in [Path(__file__), *[TEMPLATE/name for name in DOCUMENTS]]}}


def verify_sources(provenance):
    inspection.verify_sources(provenance["inspection_sources"])
    if provenance["code_hashes"] != code_hashes():
        raise ValueError("Disposition code/protocol changed; create an explicit new version")


def scope_support(row, scope):
    values = {field: row[field] for field in SCOPES[scope]}
    reasons = []
    if scope == "process_evidence":
        state = "available" if row["process_visible_events"] == 4 else "partial"
        if row["process_missing_source_events"]:
            reasons.append("source_milestones_missing")
        if row["process_post_cutoff_events"]:
            reasons.append("events_not_visible_at_cutoff")
        if row["process_inverted_pairs"]:
            reasons.append("recorded_inversion_not_verified_entry_error")
        if row["process_has_equal_timestamps"]:
            reasons.append("equal_times_not_observed_causal_order")
    elif scope == "completed_timing":
        available = row["timing_input_eligible"] is True and row["inner_role"] in {"fit", "calibration", "validation"}
        state = "available" if available else "withheld"
        if not available:
            reasons.append("preserve_saved_timing_eligibility_and_inner_cutoff")
    elif scope == "payment_order_features":
        unavailable = [field for field, value in values.items() if value is None or value < 0]
        state = "withheld" if unavailable else "available"
        reasons += ["missing_or_negative:"+field for field in unavailable]
        reasons.append("retrospective_snapshot_not_verified_event_time_availability")
    elif scope == "geographic_context":
        available = row["all_pairs_quality_eligible"] is True and all(row[field] is not None for field in inspection.integration.delivery.context.DISTANCES)
        state = "available" if available else "withheld"
        reasons.append("inherited_provisional_zip_screen_not_verified_road_distance")
    else:
        single = row["seller_count"] == 1
        supported = all(row[field] is not None and row[field] > 0 for field in
                        ["single_seller_prior_promise_comparable_orders", "single_seller_prior_reviewed_orders"])
        complete = all(row[field] is not None for field in
                       ["single_seller_prior_after_promise_fraction", "single_seller_prior_mean_review_score"])
        state = "available" if single and supported and complete else "withheld"
        reasons.append("prior_category_scoped_history_not_causal_seller_fault")
        if not single:
            reasons.append("multi_seller_order_level_outcome_not_attributed")
        if not supported or not complete:
            reasons.append("required_prior_outcomes_unavailable_not_zero")
    action = {"available": "retain_evidence", "partial": "retain_partial_evidence", "withheld": "withhold_from_named_analysis"}[state]
    return state, action, values, reasons


def build_ledger(repository):
    rows = []
    cases = repository["frames"]["case_evidence.csv"].sort_values(["order_purchase_timestamp", "order_id"])
    for row in inspection.records(cases):
        for scope in SCOPES:
            state, action, values, reasons = scope_support(row, scope)
            rows.append({"order_id": row["order_id"], "split": row["split"], "inner_role": row["inner_role"],
                         "analysis_scope": scope, "support_state": state, "automatic_use_recommendation": action,
                         "source_fields_json": json.dumps(values, sort_keys=True, allow_nan=False),
                         "observation_reasons_json": json.dumps(reasons), "saved_signal_codes_json": row["evidence_codes_json"],
                         **DECISIONS, "raw_order_retained": True, "human_decision_applied": False})
    ledger = pd.DataFrame(rows)
    coverage = ledger.groupby(["split", "analysis_scope", "support_state"], as_index=False).size().rename(columns={"size": "orders"})
    summary = {"status": "prepared_unassessed_analyst_evidence", "source_orders": repository["source_orders"],
               "protected_pilot_orders": len(repository["protected_ids"]), "accessible_orders": len(cases),
               "analysis_scopes": len(SCOPES), "scope_rows": len(ledger), "unassessed_scope_rows": len(ledger),
               "scope_counts_overlap": True, "annotation_role": "analyst_model_exposed_not_independent_reference",
               **{key: False for key in ["source_records_deleted", "human_labels_created", "decisions_applied_to_training",
                                         "models_retrained", "thresholds_changed", "split_changed", "test_scored",
                                         "accuracy_computed", "advisor_approval_inferred", "near_real_time_validated"]}}
    return {"analysis_use_ledger.csv": ledger, "coverage.csv": coverage}, summary


def note_template():
    return {"order_id": "", "analysis_scope": "", **DECISIONS, "supporting_fields": [], "verification_reference": "", "reason": ""}


def destination_guard(path, repository):
    path = Path(path).resolve()
    forbidden = [ROOT/name for name in ["scripts", "templates", "tests", "models", ".git", "data/raw", "data/processed",
                                       "data/imported", "data/import_sources"]]
    forbidden += [Path(repository["provenance"][name]) for name in ["source_directory", "pilot_directory"]]
    if any(path.is_relative_to(parent.resolve()) for parent in forbidden):
        raise ValueError("Destination must not be inside protected source/code/model/pilot directories")
    if any((parent/"manifest.json").is_file() for parent in path.parents):
        raise ValueError("Destination must not be nested inside an existing immutable snapshot")
    return path


def prepare(repository, output=DEFAULT_OUTPUT):
    output = destination_guard(output, repository)
    if output.exists():
        raise FileExistsError(f"Disposition output already exists: {output}")
    provenance = {"inspection_sources": repository["provenance"], "code_hashes": code_hashes()}
    verify_sources(provenance)
    frames, summary = build_ledger(repository)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".disposition-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False)
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_exact=True)
        for name in DOCUMENTS:
            (staging/name).write_text((TEMPLATE/name).read_text())
        review.write_json(staging/"note_template.json", note_template())
        review.write_json(staging/"summary.json", summary)
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "schemas": schemas, "summary": summary,
                    "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())}}
        review.write_json(staging/"manifest.json", manifest)
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    required = TABLES | set(DOCUMENTS) | {"summary.json", "note_template.json"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != required or set(manifest.get("schemas", {})) != TABLES):
        raise ValueError("Expected complete disposition snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"summary.json") != manifest["summary"] or review.read_json(directory/"note_template.json") != note_template():
        raise ValueError("Disposition summary/template changed")
    return manifest


def get_row(repository, order_id, scope):
    if scope not in SCOPES:
        raise ValueError("Unknown analysis scope")
    # Reuse the existing guard; protected pilot IDs never reach analyst annotations.
    return inspection.inspect_case(repository, order_id)["case"]


def validate_note(repository, note):
    if not isinstance(note, dict) or set(note) != NOTE_FIELDS:
        raise ValueError("Unexpected note fields; no binary label, approval or probability is accepted")
    for field in NOTE_FIELDS - {"supporting_fields"}:
        if not isinstance(note[field], str):
            raise ValueError("Note fields must be text")
    row = get_row(repository, note["order_id"], note["analysis_scope"])
    for field, choices in [("data_quality_assessment", QUALITY), ("atypicality_assessment", ATYPICALITY), ("proposed_use_action", ACTIONS)]:
        if note[field] not in choices:
            raise ValueError("Unknown assessment/action")
    if not note["reason"].strip():
        raise ValueError("An explicit reason is required; no default human judgment")
    fields = note["supporting_fields"]
    if (not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields)
            or len(set(fields)) != len(fields) or not set(fields).issubset(SCOPES[note["analysis_scope"]])):
        raise ValueError("Cite distinct source fields in the selected scope, never model scores as verification")
    confirmed_error = note["data_quality_assessment"] == "confirmed_error"
    valid_deviation = note["atypicality_assessment"] == "valid_deviation"
    if (confirmed_error or valid_deviation) and not note["verification_reference"].strip():
        raise ValueError("Confirmed error/valid deviation requires an explicit verification reference")
    if valid_deviation and note["data_quality_assessment"] != "no_error_identified":
        raise ValueError("A valid deviation needs a consistent no-error assessment in this scope")
    state, _, _, _ = scope_support(row, note["analysis_scope"])
    if note["proposed_use_action"] == "retain" and state == "withheld":
        raise ValueError("A note cannot override missing evidence or frozen eligibility; a new source/protocol is required")
    if confirmed_error and note["proposed_use_action"] == "retain" and note["analysis_scope"] != "process_evidence":
        raise ValueError("Do not retain confirmed-invalid inputs for numeric analysis")
    return {field: row[field] for field in fields}


def payload_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def journal_binding(directory):
    manifest = load_snapshot(directory)
    return {"version": VERSION, "disposition_manifest_sha256": review.digest(Path(directory)/"manifest.json"),
            "source_manifest_sha256": manifest["provenance"]["inspection_sources"]["source_manifest_sha256"],
            "role": "analyst_proposals_not_applied_not_independent_labels"}


def load_journal(path, directory, repository):
    path = Path(path)
    binding = journal_binding(directory)
    manifest = load_snapshot(directory)
    if manifest["provenance"]["inspection_sources"] != repository["provenance"]:
        raise ValueError("Repository and disposition snapshot differ")
    if not path.exists():
        return {**binding, "events": []}, None
    before = review.digest(path)
    journal = review.read_json(path)
    if set(journal) != set(binding) | {"events"} or any(journal.get(key) != value for key, value in binding.items()):
        raise ValueError("Journal source/version binding changed")
    if not isinstance(journal["events"], list):
        raise ValueError("Journal events must be a list")
    previous = ""
    for index, event in enumerate(journal["events"], 1):
        expected_fields = {"revision", "previous_sha256", "reviewer_id", "recorded_at_utc", "assessment", "source_values", "sha256"}
        if not isinstance(event, dict) or set(event) != expected_fields:
            raise ValueError("Unexpected journal event")
        if event["revision"] != index or event["previous_sha256"] != previous or event["sha256"] != payload_hash({k: v for k, v in event.items() if k != "sha256"}):
            raise ValueError("Journal revision/hash chain changed")
        if not isinstance(event["reviewer_id"], str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", event["reviewer_id"]):
            raise ValueError("Invalid reviewer identity")
        recorded = datetime.fromisoformat(event["recorded_at_utc"])
        if recorded.utcoffset() != timezone.utc.utcoffset(recorded):
            raise ValueError("Review timestamp must be UTC, not an invented source event time")
        if event["source_values"] != validate_note(repository, event["assessment"]):
            raise ValueError("Journal source values changed")
        previous = event["sha256"]
    if review.digest(path) != before:
        raise ValueError("Concurrent journal change; reload before recording")
    return journal, before


def append_note(repository, directory, path, reviewer_id, note, expected_hash):
    if not isinstance(reviewer_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", reviewer_id):
        raise ValueError("An explicit reviewer ID is required")
    values = validate_note(repository, note)
    path = Path(path).resolve()
    if path.is_relative_to((ROOT/"data/experiments").resolve()) or path.is_relative_to(Path(directory).resolve()):
        raise ValueError("Keep analyst notes outside immutable experiment snapshots")
    path = destination_guard(path, repository)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name+".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Journal is locked; inspect another writer or interrupted save") from exc
    os.close(descriptor)
    try:
        journal, actual_hash = load_journal(path, directory, repository)
        if actual_hash != expected_hash:
            raise ValueError("Stale journal revision; no earlier notes overwritten")
        events = journal["events"]
        event = {"revision": len(events)+1, "previous_sha256": events[-1]["sha256"] if events else "",
                 "reviewer_id": reviewer_id, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                 "assessment": note, "source_values": values}
        event["sha256"] = payload_hash(event)
        events.append(event)
        with tempfile.TemporaryDirectory(prefix=".disposition-note-", dir=path.parent) as temp:
            staging = Path(temp)/"journal.json"
            review.write_json(staging, journal)
            load_journal(staging, directory, repository)
            if (review.digest(path) if path.exists() else None) != expected_hash:
                raise ValueError("Journal changed during save")
            staging.replace(path)
        return event
    finally:
        lock.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--prepare", action="store_true")
    modes.add_argument("--summary", action="store_true")
    modes.add_argument("--history", action="store_true")
    modes.add_argument("--order-id")
    modes.add_argument("--record-note", type=Path)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--reviewer-id")
    args = parser.parse_args(argv)
    try:
        if args.record_note and not args.reviewer_id:
            raise ValueError("--record-note requires an explicit --reviewer-id")
        if args.reviewer_id and not args.record_note:
            raise ValueError("--reviewer-id is only used with --record-note")
        if args.prepare and args.snapshot.exists():
            raise FileExistsError("Snapshot already exists; create a new version")
        if args.summary:
            result = load_snapshot(args.snapshot)["summary"]
        else:
            repository = inspection.load_repository()
            if args.prepare:
                result = prepare(repository, args.snapshot)
            elif args.history:
                result, _ = load_journal(args.journal, args.snapshot, repository)
            elif args.record_note:
                note = review.read_json(args.record_note)
                _, expected = load_journal(args.journal, args.snapshot, repository)
                result = append_note(repository, args.snapshot, args.journal, args.reviewer_id, note, expected)
            else:
                manifest = load_snapshot(args.snapshot)
                if manifest["provenance"]["inspection_sources"] != repository["provenance"]:
                    raise ValueError("Repository and snapshot differ")
                get_row(repository, args.order_id, "process_evidence")
                frame = shared.read_table(args.snapshot, "analysis_use_ledger.csv", manifest)
                result = {"role": "analyst_model_exposed_not_ground_truth", "rows": inspection.records(frame.loc[frame.order_id.eq(args.order_id)])}
        print(json.dumps(result, indent=2, allow_nan=False))
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Disposition stopped: {exc}\n")


if __name__ == "__main__":
    main()
