"""Real Olist inputs; disposable contract notes are not thesis observations or labels."""

from copy import deepcopy
from contextlib import redirect_stdout, redirect_stderr
import io
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import record_pilot_judgments as entry


class PilotJudgmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.root = Path(cls.temp.name)
        cls.before = {**entry.evidence.audit.inventory(entry.review.DEFAULT_BATCH),
                      **entry.evidence.audit.inventory(entry.evidence.DEFAULT_OUTPUT),
                      **entry.evidence.audit.inventory(entry.findings.DEFAULT_OUTPUT)}
        cls.default_journal_existed = entry.DEFAULT_JOURNAL.exists()
        with patch.object(joblib, "load", side_effect=AssertionError("No models")), \
                patch.object(entry.evidence.audit.readiness.integration.delivery, "fit_entry", side_effect=AssertionError("No fitting")):
            cls.context = entry.load_context()

    def note(self, rid="BR0001", scope="approval", decision="insufficient_evidence"):
        _, _, required = entry.scope_evidence(self.context, rid, scope)
        return {**entry.note_template(rid, scope), "decision": decision, "basis": "source_observations",
                "evidence_fields": required, "reason": "Software contract fixture only; not an actual human assessment.",
                "missing_evidence": "Human judgment has not been supplied in this disposable software fixture."
                                    if decision == "insufficient_evidence" else "",
                "anomaly_subtype": "unclassified" if decision == "needs_investigation" else ""}

    def append(self, note, path, expected=None, reviewer="contract_test"):
        return entry.append_note(note, reviewer, "software_test", "unknown", False, expected, path)

    def test_original_120_and_four_scopes_without_resampling(self):
        ledger = self.context["frames"]["scope_ledger.csv"]
        self.assertEqual(len(ledger), 120)
        self.assertEqual(int(ledger.comparison_generated.sum()), 118)
        self.assertEqual(len(entry.SCOPES), 4)
        journal, digest = entry.read_journal(self.context, self.root/"absent.json")
        status = entry.progress(self.context, journal, digest)
        self.assertEqual(status["scope_tasks"], 472)
        self.assertEqual(status["human_unreviewed_tasks"], 472)
        self.assertEqual(status["held_review_ids"], ["BR0043", "BR0119"])
        self.assertIsNone(digest)
        self.assertFalse((self.root/"absent.json").exists())

    def test_preview_uses_exact_current_evidence_and_blank_notes(self):
        result = entry.preview(self.context, "BR0079", "preparation_handoff")
        values = result["scope_evidence"]["preparation_handoff"]
        self.assertEqual(result["mode"], "analyst_assisted_not_blind")
        self.assertEqual(values["order.promise_delta_calendar_days"], -13)
        self.assertEqual(values["stage.duration_days"], 18.1828125)
        self.assertEqual(values["stage.reference_n"], 470)
        self.assertTrue(values["stage.long_stage_warning"])
        self.assertEqual(result["notes"], [entry.note_template("BR0079", "preparation_handoff")])
        self.assertNotIn("order.order_id", values)
        self.assertNotIn("order.payment_reconciliation", values)

    def test_held_unknown_and_raw_ids_are_rejected(self):
        raw_id = self.context["frames"]["scope_ledger.csv"].order_id.iloc[0]
        for rid in ["BR0043", "BR0119", "BR9999", raw_id, None]:
            with self.subTest(rid=rid), self.assertRaises(ValueError):
                entry.preview(self.context, rid)
        with self.assertRaisesRegex(ValueError, "Unknown fulfillment scope"):
            entry.preview(self.context, "BR0001", "fraud")

    def test_blank_extra_fields_invalid_fields_and_bad_types_rejected(self):
        with self.assertRaises(ValueError):
            entry.validate_note(self.context, entry.note_template("BR0001", "approval"))
        for change in [{"probability": 0.99}, {"reference_ready": True}, {"decision": "normal"},
                       {"reason": " "}, {"basis": ""}, {"evidence_fields": []},
                       {"evidence_fields": ["model.score"]}, {"evidence_fields": [["nested"]]},
                       {"evidence_fields": ["stage.duration_days", "stage.duration_days"]},
                       {"additional_evidence": {}}, {"review_id": 1}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                entry.validate_note(self.context, {**self.note(), **change})

    def test_unresolved_has_no_subtype_or_negative_imputation(self):
        note = self.note()
        values = entry.validate_note(self.context, note)
        self.assertIn("stage.duration_days", values)
        for change in [{"missing_evidence": ""}, {"anomaly_subtype": "data_error"}, {"subtype_reason": "guess"}]:
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "Insufficient"):
                entry.validate_note(self.context, {**note, **change})

    def test_unassessable_stage_cannot_be_forced_positive_or_negative(self):
        for rid, scope in [("BR0013", "transport"), ("BR0025", "approval"), ("BR0115", "transport")]:
            self.assertFalse(entry.scope_evidence(self.context, rid, scope)[1])
            entry.validate_note(self.context, self.note(rid, scope))
            for decision in ["needs_investigation", "no_supported_issue"]:
                with self.subTest(rid=rid, decision=decision), self.assertRaisesRegex(ValueError, "Unassessable"):
                    entry.validate_note(self.context, self.note(rid, scope, decision))

    def test_post_cutoff_delivery_stays_missing(self):
        values, assessable, _ = entry.scope_evidence(self.context, "BR0013", "delivery_promise")
        self.assertIsNone(values["order.order_delivered_customer_date"])
        self.assertFalse(assessable)
        with self.assertRaisesRegex(ValueError, "Unassessable"):
            entry.validate_note(self.context, self.note("BR0013", "delivery_promise", "no_supported_issue"))

    def test_calendar_scope_not_erased_by_missing_geographic_context(self):
        self.assertTrue(entry.scope_evidence(self.context, "BR0025", "delivery_promise")[1])
        self.assertFalse(entry.scope_evidence(self.context, "BR0025", "transport")[1])
        entry.validate_note(self.context, self.note("BR0025", "delivery_promise", "no_supported_issue"))

    def test_judgment_is_not_forced_to_copy_machine_flag(self):
        # Acceptance is a schema test, not an invented business judgment for BR0079.
        values = entry.validate_note(self.context, self.note("BR0079", "preparation_handoff", "no_supported_issue"))
        self.assertEqual(values["stage.duration_days"], 18.1828125)
        entry.validate_note(self.context, self.note("BR0001", "approval", "needs_investigation"))

    def test_resolved_requires_source_fields_but_not_root_cause(self):
        note = self.note("BR0059", "delivery_promise", "needs_investigation")
        values = entry.validate_note(self.context, note)
        self.assertEqual(values["order.promise_delta_calendar_days"], 5)
        self.assertEqual(note["anomaly_subtype"], "unclassified")
        for change in [{"evidence_fields": ["order.promise_delta_calendar_days"]}, {"missing_evidence": "missing required evidence"}]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                entry.validate_note(self.context, {**note, **change})

    def test_no_supported_issue_cannot_have_subtype(self):
        with self.assertRaisesRegex(ValueError, "scoped only"):
            entry.validate_note(self.context, {**self.note(decision="no_supported_issue"), "anomaly_subtype": "valid_deviation"})

    def additional_source(self, path=None):
        path = path or entry.evidence.explanation.DECISIONS
        return {"path": str(path), "sha256": entry.review.digest(path), "locator": "Q1-Q5",
                "supports": "Disposable software reference-binding test; not verification of a business fact."}

    def test_subtypes_need_documented_extra_support_not_automatic_labels(self):
        note = self.note(decision="needs_investigation")
        for subtype in ["data_error", "valid_deviation", "fraud", ""]:
            with self.subTest(subtype=subtype), self.assertRaises(ValueError):
                entry.validate_note(self.context, {**note, "anomaly_subtype": subtype})
        candidate = {**note, "basis": "source_and_additional_evidence", "additional_evidence": [self.additional_source()],
                     "anomaly_subtype": "valid_deviation", "subtype_reason": "Software schema contract only; not a thesis label."}
        entry.validate_note(self.context, candidate)
        with self.assertRaisesRegex(ValueError, "actual bound source"):
            entry.validate_note(self.context, {**candidate, "additional_evidence": []})

    def test_additional_reference_hash_locator_and_duplicates_checked(self):
        valid = self.additional_source()
        for refs in [[{**valid, "sha256": "incorrect"}], [{**valid, "locator": ""}],
                     [{**valid, "path": "relative.txt"}], [valid, valid], [{"path": valid["path"]}]]:
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                entry.validate_additional(refs)

    def test_actor_cannot_claim_blind_or_mix_ai_with_human_confirmation(self):
        entry.validate_actor("contract_test", "software_test", "unknown", False)
        for origin, exposure, confirmed in [("human_declared", "seen_model_outputs", False),
                                            ("ai_assisted", "unknown", True), ("automated", "unknown", True),
                                            ("human_declared", "unseen", True), ("other", "unknown", False)]:
            with self.subTest(origin=origin, exposure=exposure), self.assertRaises(ValueError):
                entry.validate_actor("contract_test", origin, exposure, confirmed)
        with self.assertRaises(ValueError):
            entry.validate_actor("", "software_test", "unknown", False)

    def test_append_revision_preserves_original_and_stale_write_rejected(self):
        path = self.root/"revision/journal.json"
        first = self.append(self.note(), path)
        old_bytes = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Stale"):
            self.append(self.note(), path)
        self.assertEqual(path.read_bytes(), old_bytes)
        _, digest = entry.read_journal(self.context, path)
        second = self.append({**self.note(), "reason": "Second disposable technical revision, no human assessment."}, path, digest)
        journal, digest = entry.read_journal(self.context, path)
        self.assertEqual(journal["events"][0], first)
        self.assertEqual(second["supersedes_revision"], 1)
        self.assertEqual(second["previous_sha256"], first["sha256"])
        self.assertEqual(entry.progress(self.context, journal, digest)["latest_notes_by_origin"]["software_test"], 1)
        self.assertFalse(path.with_name("journal.json.lock").exists())

    def test_different_reviewer_scope_and_origin_do_not_supersede(self):
        path = self.root/"separate/journal.json"
        first = self.append(self.note(), path)
        _, digest = entry.read_journal(self.context, path)
        second = self.append(self.note(scope="transport"), path, digest)
        _, digest = entry.read_journal(self.context, path)
        third = self.append(self.note(), path, digest, reviewer="other_contract_test")
        _, digest = entry.read_journal(self.context, path)
        fourth = entry.append_note(self.note(), "contract_test", "ai_assisted", "seen_model_outputs", False, digest, path)
        for event in [first, second, third, fourth]:
            self.assertIsNone(event["supersedes_revision"])
            self.assertFalse(event["independent_reference"])

    def test_tamper_and_changed_binding_rejected(self):
        path = self.root/"tamper/journal.json"
        self.append(self.note(), path)
        original = entry.review.read_json(path)
        edited = deepcopy(original)
        edited["events"][0]["assessment"]["decision"] = "no_supported_issue"
        entry.review.write_json(path, edited)
        with self.assertRaisesRegex(ValueError, "chain"):
            entry.read_journal(self.context, path)
        entry.review.write_json(path, original)
        different = deepcopy(self.context)
        different["binding"]["source_manifest_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "binding"):
            entry.read_journal(different, path)

    def test_recomputed_hash_cannot_change_source_values_or_reference_status(self):
        path = self.root/"source-values/journal.json"
        self.append(self.note(), path)
        original = entry.review.read_json(path)
        for change in ["source", "reference"]:
            journal = deepcopy(original)
            event = journal["events"][0]
            if change == "source":
                event["source_values"]["stage.duration_days"] = None
            else:
                event["independent_reference"] = True
            event["sha256"] = entry.hierarchy.payload_hash({k: v for k, v in event.items() if k != "sha256"})
            entry.review.write_json(path, journal)
            with self.subTest(change=change), self.assertRaises(ValueError):
                entry.read_journal(self.context, path)

    def test_changed_additional_document_does_not_reinterpret_old_note(self):
        attachment = self.root/"reference.md"
        shutil.copyfile(entry.evidence.explanation.DECISIONS, attachment)
        path = self.root/"attachment/journal.json"
        self.append({**self.note(), "basis": "source_and_additional_evidence",
                     "additional_evidence": [self.additional_source(attachment)]}, path)
        shutil.copyfile(entry.GUIDE, attachment)
        with self.assertRaisesRegex(ValueError, "changed"):
            entry.read_journal(self.context, path)

    def test_protected_source_and_existing_lock_cannot_be_written(self):
        for path in [entry.review.DEFAULT_BATCH/"judgments.json", entry.findings.DEFAULT_OUTPUT/"judgments.json",
                     ROOT/"scripts/never_write.json"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.append(self.note(), path)
            self.assertFalse(path.exists())
        path = self.root/"locked/journal.json"
        path.parent.mkdir()
        lock = path.with_name("journal.json.lock")
        lock.touch()
        with self.assertRaisesRegex(ValueError, "lock exists"):
            self.append(self.note(), path)
        self.assertTrue(lock.exists())
        self.assertFalse(path.exists())

    def test_progress_is_unique_by_reviewer_origin_case_scope_not_majority_vote(self):
        # In-memory metadata branches only. No fabricated human event is persisted.
        journal = {**self.context["binding"], "events": []}
        for reviewer, origin, decision, scope in [
                ("contract_A", "human_declared", "no_supported_issue", "approval"),
                ("contract_B", "human_declared", "needs_investigation", "approval"),
                ("contract_A", "human_declared", "insufficient_evidence", "transport"),
                ("contract_A", "ai_assisted", "needs_investigation", "delivery_promise")]:
            journal["events"].append({"reviewer_id": reviewer, "origin": origin,
                                      "assessment": self.note(scope=scope, decision=decision)})
        status = entry.progress(self.context, journal, None)
        self.assertEqual(status["human_reviewed_tasks"], 2)
        self.assertEqual(status["human_unreviewed_tasks"], 470)
        self.assertEqual(status["human_conflicting_tasks"], [["BR0001", "approval"]])
        self.assertEqual(status["human_unresolved_tasks"], 1)
        self.assertEqual(status["human_resolved_nonconflicting_tasks"], 0)
        self.assertEqual(status["human_complete_orders"], 0)
        self.assertFalse(status["independent_reference_ready"])
        self.assertFalse(status["accuracy_computed"])
        self.assertFalse(status["model_selected"])

    def test_cli_readonly_status_template_and_preview_and_invalid_options(self):
        for argv in [["--status"], ["--template", "BR0001", "--scope", "approval"],
                     ["--preview", "BR0059", "--scope", "delivery_promise"]]:
            output = io.StringIO()
            with redirect_stdout(output):
                entry.main([*argv, "--journal", str(self.root/"cli-absent.json")])
            self.assertIsInstance(json.loads(output.getvalue()), dict)
        for argv in [["--template", "BR0001"], ["--preview", "BR0043"],
                     ["--status", "--reviewer-id", "contract_test"], ["--status", "--scope", "approval"]]:
            with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
                entry.main(argv)
            self.assertEqual(error.exception.code, 2)
        self.assertFalse((self.root/"cli-absent.json").exists())

    def test_software_does_not_create_real_journal_or_modify_sources(self):
        self.assertEqual(entry.DEFAULT_JOURNAL.exists(), self.default_journal_existed)
        for name, digest in self.before.items():
            self.assertEqual(entry.review.digest(ROOT/name), digest)


if __name__ == "__main__":
    unittest.main()
