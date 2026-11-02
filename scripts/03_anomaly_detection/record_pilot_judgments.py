"""Record scoped pilot judgments with provenance; never manufacture reference labels."""

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

import review_pilot_signal_findings as findings

review, evidence = findings.review, findings.evidence
hierarchy = evidence.audit.hierarchy
ROOT = findings.ROOT
VERSION = "pilot_signal_judgments_v1"
DEFAULT_JOURNAL = ROOT/"data/manual_review/pilot_signal_judgments_v1/journal.json"
GUIDE = ROOT/"docs/pilot_signal_judgments.md"
SCOPES = ["delivery_promise", *evidence.signals.STAGES]
DECISIONS = {"needs_investigation", "no_supported_issue", "insufficient_evidence"}
ORIGINS = {"human_declared", "ai_assisted", "automated", "software_test"}
BASES = {"source_observations", "source_and_statistical_reference", "source_and_additional_evidence", "model_output_only"}
EXPOSURES = {"seen_model_outputs", "unknown"}
ORDER_FIELDS = [*evidence.performance.TIMES, evidence.performance.ESTIMATE, "visible_before_exclusive",
                "promise_delta_calendar_days", "performance_eligible", "source_quality_status", "promise_basis_status",
                "customer_state", "distance_band", "seller_attribution_scope", "all_pairs_quality_eligible",
                "geography_quality_reasons", "screened_distance_max_km", "item_count", "product_count"]
STAGE_FIELDS = ["start_timestamp", "end_timestamp", "duration_days", "duration_state", "duration_eligible",
                "assessment_status", "assessable", "reference_n", "reference_context_json", "reference_median_days",
                "difference_from_reference_days", "upper_boundary_days_inclusive", "long_stage_warning",
                "short_stage_note", "stage_plan_status", "limitation_vi"]


def note_template(review_id="", scope=""):
    return {"review_id": review_id, "scope": scope, "decision": "", "basis": "", "evidence_fields": [],
            "reason": "", "missing_evidence": "", "anomaly_subtype": "", "subtype_reason": "",
            "additional_evidence": []}


def load_context(source=findings.DEFAULT_OUTPUT):
    source = Path(source).resolve()
    manifest = findings.load_snapshot(source)
    frames, _ = findings.load_inputs(Path(manifest["provenance"]["source_directory"]))
    findings.validate_inputs(frames)
    binding = {"version": VERSION, "source_directory": str(source),
               "source_manifest_sha256": review.digest(source/"manifest.json"),
               "implementation_hashes": {str(p.relative_to(ROOT)): review.digest(p)
                                         for p in [Path(__file__), Path(hierarchy.__file__), GUIDE]},
               "role": "scoped_provisional_judgments_not_independent_reference_labels"}
    return {"frames": frames, "binding": binding}


def scalar(value):
    if pd.isna(value):
        return None
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    return value.item() if hasattr(value, "item") else value


def selected_order(context, review_id):
    ledger = context["frames"]["scope_ledger.csv"].set_index("review_id")
    if not isinstance(review_id, str) or review_id not in ledger.index:
        raise ValueError("Unknown pilot review ID")
    if not ledger.loc[review_id, "comparison_generated"]:
        raise ValueError("Held test order: outcomes and judgments are not available")
    return context["frames"]["order_explanations.csv"].set_index("review_id").loc[review_id]


def scope_evidence(context, review_id, scope):
    if scope not in SCOPES:
        raise ValueError("Unknown fulfillment scope; payment/fraud are not this review target")
    order = selected_order(context, review_id)
    values = {"order."+field: scalar(order[field]) for field in ORDER_FIELDS}
    if scope == "delivery_promise":
        assessable = bool(order.performance_eligible)
        required = ["order.order_delivered_customer_date", "order.order_estimated_delivery_date",
                    "order.promise_delta_calendar_days", "order.visible_before_exclusive"]
    else:
        rows = context["frames"]["stage_comparisons.csv"]
        stage = rows.loc[rows.review_id.eq(review_id) & rows.stage.eq(scope)].iloc[0]
        values.update({"stage."+field: scalar(stage[field]) for field in STAGE_FIELDS})
        assessable = bool(stage.assessable)
        required = ["stage.start_timestamp", "stage.end_timestamp", "stage.duration_days",
                    "stage.assessment_status", "stage.reference_n", "stage.reference_context_json",
                    "order.all_pairs_quality_eligible"]
    return values, assessable, required


def preview(context, review_id, scope=None):
    selected_order(context, review_id)
    scopes = SCOPES if scope is None else [scope]
    return {"review_id": review_id, "mode": "analyst_assisted_not_blind",
            "source_manifest_sha256": context["binding"]["source_manifest_sha256"],
            "warning": "Displayed statistics/flags are not independent truth. Do not merely copy the flag as a judgment.",
            "scope_evidence": {name: scope_evidence(context, review_id, name)[0] for name in scopes},
            "notes": [note_template(review_id, name) for name in scopes]}


def validate_additional(entries):
    if not isinstance(entries, list):
        raise ValueError("Additional evidence must be an explicit list of local source references")
    seen = set()
    for entry in entries:
        if (not isinstance(entry, dict) or set(entry) != {"path", "sha256", "locator", "supports"}
                or not all(isinstance(value, str) for value in entry.values())):
            raise ValueError("Each additional source needs path, sha256, locator and supports")
        path = Path(entry["path"])
        if (not path.is_absolute() or not path.is_file() or review.digest(path) != entry["sha256"]
                or not hierarchy.meaningful(entry["locator"]) or not hierarchy.meaningful(entry["supports"])):
            raise ValueError("Additional evidence file/hash/locator is missing or changed")
        key = (str(path.resolve()), entry["locator"])
        if key in seen:
            raise ValueError("Duplicate additional evidence reference")
        seen.add(key)


def validate_note(context, note):
    if not isinstance(note, dict) or set(note) != set(note_template()):
        raise ValueError("Unexpected note fields; no metrics, approval or confidence probability may be injected")
    if any(not isinstance(value, str) for key, value in note.items() if key not in {"evidence_fields", "additional_evidence"}):
        raise ValueError("Note fields must be text except evidence lists")
    values, assessable, required = scope_evidence(context, note["review_id"], note["scope"])
    fields = note["evidence_fields"]
    if (not isinstance(fields, list) or not fields or not all(isinstance(field, str) for field in fields)
            or len(set(fields)) != len(fields) or not set(fields).issubset(values)):
        raise ValueError("Cite distinct fields actually present in this scope")
    if note["decision"] not in DECISIONS or note["basis"] not in BASES or not hierarchy.meaningful(note["reason"]):
        raise ValueError("An explicit scoped decision, evidence basis and reason are required")
    validate_additional(note["additional_evidence"])
    if note["basis"] == "source_and_additional_evidence" and not note["additional_evidence"]:
        raise ValueError("Declared additional evidence requires an actual bound source")
    if note["decision"] == "insufficient_evidence":
        if not hierarchy.meaningful(note["missing_evidence"]) or note["anomaly_subtype"] or note["subtype_reason"]:
            raise ValueError("Insufficient evidence needs a reason for uncertainty and no subtype")
    else:
        # The frozen observation window and context cannot be bypassed by attaching later records.
        if not assessable:
            raise ValueError("Unassessable scope stays unresolved in this version; do not force a positive or negative")
        if not set(required).issubset(fields):
            raise ValueError("A resolved judgment must cite endpoints and the scope's required context")
        if note["missing_evidence"].strip():
            raise ValueError("Use insufficient_evidence when required decision evidence is missing")
        if note["decision"] == "no_supported_issue":
            if note["anomaly_subtype"] or note["subtype_reason"]:
                raise ValueError("No-supported-issue is scoped only and cannot contain an anomaly subtype")
        else:
            if note["anomaly_subtype"] not in {"unclassified", "data_error", "valid_deviation"}:
                raise ValueError("Use unclassified unless a subtype has separately documented support")
            if note["anomaly_subtype"] != "unclassified" and (not note["additional_evidence"]
                    or note["basis"] != "source_and_additional_evidence" or not hierarchy.meaningful(note["subtype_reason"])):
                raise ValueError("A subtype needs additional evidence and justification, not merely a model flag")
    return {field: values[field] for field in fields}


def validate_actor(reviewer_id, origin, exposure, human_confirmed):
    hierarchy.validate_reviewer(reviewer_id)
    if origin not in ORIGINS or exposure not in EXPOSURES or type(human_confirmed) is not bool:
        raise ValueError("Declare the actual judgment origin and model exposure; this flow is not blind")
    if human_confirmed != (origin == "human_declared"):
        raise ValueError("Human origin requires explicit confirmation; AI/software notes cannot claim a human decision")


def read_journal(context, path=DEFAULT_JOURNAL):
    path = Path(path)
    binding = context["binding"]
    if not path.exists():
        return {**binding, "events": []}, None
    before = review.digest(path)
    journal = review.read_json(path)
    if set(journal) != set(binding) | {"events"} or any(journal.get(key) != value for key, value in binding.items()):
        raise ValueError("Journal source/implementation binding changed")
    if not isinstance(journal["events"], list):
        raise ValueError("Journal events must be a list")
    previous = ""
    fields = {"revision", "previous_sha256", "reviewer_id", "origin", "model_exposure", "human_decision_confirmed",
              "recorded_at_utc", "assessment", "source_values", "supersedes_revision", "independent_reference", "sha256"}
    latest = {}
    for number, event in enumerate(journal["events"], 1):
        if not isinstance(event, dict) or set(event) != fields:
            raise ValueError("Unexpected journal event fields")
        if (type(event["revision"]) is not int or event["revision"] != number or event["previous_sha256"] != previous
                or event["sha256"] != hierarchy.payload_hash({key: value for key, value in event.items() if key != "sha256"})
                or event["independent_reference"] is not False):
            raise ValueError("Journal revision/hash chain or reference status changed")
        validate_actor(event["reviewer_id"], event["origin"], event["model_exposure"], event["human_decision_confirmed"])
        if datetime.fromisoformat(event["recorded_at_utc"]).utcoffset() != timezone.utc.utcoffset(None):
            raise ValueError("Recording time must be timezone-aware UTC")
        if event["source_values"] != validate_note(context, event["assessment"]):
            raise ValueError("Cited source values changed")
        key = event_key(event)
        if event["supersedes_revision"] != latest.get(key):
            raise ValueError("Revision must supersede only this reviewer's same-origin same-scope assessment")
        latest[key] = number
        previous = event["sha256"]
    if review.digest(path) != before:
        raise ValueError("Concurrent journal change; reload")
    return journal, before


def event_key(event):
    note = event["assessment"]
    return event["reviewer_id"], event["origin"], note["review_id"], note["scope"]


def append_note(note, reviewer_id, origin, exposure, human_confirmed, expected_hash,
                path=DEFAULT_JOURNAL, source=findings.DEFAULT_OUTPUT):
    validate_actor(reviewer_id, origin, exposure, human_confirmed)
    context = load_context(source)
    values = validate_note(context, note)
    if Path(path).is_symlink():
        raise ValueError("Journal cannot be a symbolic link")
    path = hierarchy.destination_guard(path, {"batch_directory": str(review.DEFAULT_BATCH)})
    journal, current = read_journal(context, path)
    if current != expected_hash:
        raise ValueError("Stale journal; inspect current status before recording")
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_name(path.name+".lock")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as exc:
        raise ValueError("Journal lock exists; inspect before retrying") from exc
    os.close(descriptor)
    try:
        event = {"revision": len(journal["events"])+1,
                 "previous_sha256": journal["events"][-1]["sha256"] if journal["events"] else "",
                 "reviewer_id": reviewer_id, "origin": origin, "model_exposure": exposure,
                 "human_decision_confirmed": human_confirmed, "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
                 "assessment": note, "source_values": values, "independent_reference": False}
        previous = [entry for entry in journal["events"] if event_key(entry) == event_key(event)]
        event["supersedes_revision"] = previous[-1]["revision"] if previous else None
        event["sha256"] = hierarchy.payload_hash(event)
        journal["events"].append(event)
        with tempfile.TemporaryDirectory(prefix=".pilot-judgment-", dir=path.parent) as temporary:
            staging = Path(temporary)/"journal.json"
            review.write_json(staging, journal)
            read_journal(context, staging)
            if load_context(source)["binding"] != context["binding"]:
                raise ValueError("Evidence or implementation changed during recording")
            if (review.digest(path) if path.exists() else None) != expected_hash:
                raise ValueError("Concurrent journal change; no judgment overwritten")
            staging.replace(path)
        return event
    finally:
        lock.unlink()


def progress(context, journal, journal_hash):
    ledger = context["frames"]["scope_ledger.csv"]
    active = ledger.loc[ledger.comparison_generated, "review_id"].tolist()
    latest = {event_key(event): event for event in journal["events"]}
    human = [event for event in latest.values() if event["origin"] == "human_declared"]
    groups = {}
    for event in human:
        note = event["assessment"]
        groups.setdefault((note["review_id"], note["scope"]), []).append(note["decision"])
    conflicts = sorted(key for key, decisions in groups.items() if len(set(decisions)) > 1)
    unresolved = sorted(key for key, decisions in groups.items() if "insufficient_evidence" in decisions)
    resolved = sorted(key for key, decisions in groups.items()
                      if len(set(decisions)) == 1 and decisions[0] != "insufficient_evidence")
    return {"version": VERSION, "pilot_orders": len(ledger), "active_orders": len(active),
            "held_review_ids": ledger.loc[~ledger.comparison_generated, "review_id"].tolist(),
            "scope_tasks": len(active)*len(SCOPES), "journal_sha256": journal_hash,
            "recorded_events": len(journal["events"]), "latest_notes_by_origin": {
                origin: sum(event["origin"] == origin for event in latest.values()) for origin in sorted(ORIGINS)},
            "human_reviewed_tasks": len(groups), "human_unreviewed_tasks": len(active)*len(SCOPES)-len(groups),
            "human_resolved_nonconflicting_tasks": len(resolved), "human_unresolved_tasks": len(unresolved),
            "human_conflicting_tasks": [list(key) for key in conflicts],
            "human_complete_orders": sum(all((rid, scope) in resolved for scope in SCOPES) for rid in active),
            "task_count_note": "Four separate scopes per order, not additional orders; unresolved/conflicting counts may overlap.",
            "unreviewed_order_ids": [rid for rid in active if not any((rid, scope) in groups for scope in SCOPES)],
            "independent_reference_ready": False, "identity_authenticated": False,
            "additional_evidence_semantics_authenticated": False, "model_selected": False, "accuracy_computed": False,
            "limitations": "Human origin is declared, not authenticated. Exposed pilot judgments are provisional, not automatic ground truth."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--status", action="store_true")
    mode.add_argument("--preview", metavar="REVIEW_ID")
    mode.add_argument("--template", metavar="REVIEW_ID")
    mode.add_argument("--check-note", type=Path)
    mode.add_argument("--record-note", type=Path)
    mode.add_argument("--history", action="store_true")
    parser.add_argument("--scope", choices=SCOPES)
    parser.add_argument("--source", type=Path, default=findings.DEFAULT_OUTPUT)
    parser.add_argument("--journal", type=Path, default=DEFAULT_JOURNAL)
    parser.add_argument("--reviewer-id")
    parser.add_argument("--origin", choices=sorted(ORIGINS))
    parser.add_argument("--model-exposure", choices=sorted(EXPOSURES))
    parser.add_argument("--human-decision-confirmed", action="store_true")
    parser.add_argument("--expected-journal-sha256", help="Use 'absent' only when no journal exists")
    args = parser.parse_args(argv)
    try:
        if args.scope and not (args.preview or args.template):
            raise ValueError("--scope is only used with --preview or --template")
        actor_args = [args.reviewer_id, args.origin, args.model_exposure, args.human_decision_confirmed,
                      args.expected_journal_sha256]
        if any(actor_args) and not args.record_note:
            raise ValueError("Actor and journal-version arguments are only used with --record-note")
        context = load_context(args.source)
        if args.preview:
            result = preview(context, args.preview, args.scope)
        elif args.template:
            if not args.scope:
                raise ValueError("A note template needs --scope")
            scope_evidence(context, args.template, args.scope)
            result = note_template(args.template, args.scope)
        elif args.status or args.history:
            journal, before = read_journal(context, args.journal)
            result = progress(context, journal, before) if args.status else journal
        else:
            path = args.check_note or args.record_note
            before = review.digest(path)
            note = review.read_json(path)
            if review.digest(path) != before:
                raise ValueError("Input note changed while reading")
            values = validate_note(context, note)
            if args.check_note:
                result = {"syntax_valid": True, "saved": False, "independent_reference_ready": False,
                          "additional_evidence_semantics_authenticated": False, "source_values": values}
            else:
                if args.expected_journal_sha256 is None:
                    raise ValueError("Read --status and pass its journal hash (or 'absent') before recording")
                expected = None if args.expected_journal_sha256 == "absent" else args.expected_journal_sha256
                result = append_note(note, args.reviewer_id, args.origin, args.model_exposure,
                                     args.human_decision_confirmed, expected, args.journal, args.source)
        print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Pilot review stopped: {exc}\n")


if __name__ == "__main__":
    main()
