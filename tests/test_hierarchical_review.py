"""Real blind Olist evidence; temporary unresolved test notes are not human labels."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/03_anomaly_detection"))
import prepare_hierarchical_review as hierarchy


class HierarchicalReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.package = cls.root/"package"
        with patch.object(joblib, "load", side_effect=AssertionError("Never deserialize models")), \
                patch.object(hierarchy.review, "open_session", side_effect=AssertionError("Do not bypass legacy approval")):
            cls.summary = hierarchy.prepare(cls.package)
            cls.frames, cls.manifest = hierarchy.load_package(cls.package)
        cls.first = cls.frames["cases.csv"].iloc[0].review_id

    def unresolved_test_note(self, scope="process_timing"):
        rule = next(rule for rule in hierarchy.review.read_json(hierarchy.TEMPLATE/"criteria.json")["rules"] if rule["scope"] == scope)
        table, fields = next(iter(hierarchy.SCOPES[scope].items()))
        return {**hierarchy.note_template(self.first, scope), "review_status": "unresolved", "reason_codes": [rule["code"]],
                "evidence_fields": [table+"."+fields[0]], "reason": "Temporary software contract test only; no human assessment.",
                "confidence": "low", "missing_evidence": "No human verification was performed in this technical test.",
                "proposed_use_action": "pending"}

    def test_real_source_counts_no_added_orders(self):
        self.assertEqual(self.summary["pilot_orders"], 120)
        self.assertEqual(self.summary["payment_records"], 135)
        self.assertEqual(self.summary["item_records"], 171)
        self.assertEqual(self.summary["review_tasks"], 360)
        self.assertEqual(self.summary["checklist_rules"], 9)

    def test_copied_source_packet_byte_identical(self):
        original = hierarchy.review.read_json(hierarchy.review.DEFAULT_BATCH/"reviewer/manifest.json")
        for name in [*original["output_hashes"], "manifest.json"]:
            self.assertEqual(hierarchy.review.digest(self.package/"reviewer_packet/reviewer"/name),
                             hierarchy.review.digest(hierarchy.review.DEFAULT_BATCH/"reviewer"/name))

    def test_tasks_preserve_original_order_and_are_unassessed(self):
        tasks = hierarchy.review.read_json(self.package/"reviewer_packet/review_tasks.json")
        self.assertEqual(tasks, hierarchy.tasks_for(self.frames))
        self.assertEqual(len({(row["review_id"], row["analysis_scope"]) for row in tasks}), 360)
        for row in tasks:
            self.assertEqual(row, hierarchy.note_template(row["review_id"], row["analysis_scope"]))
        self.assertEqual(self.summary["assessed_tasks"], 0)
        self.assertEqual(self.summary["unreviewed_tasks"], 360)

    def test_original_human_fields_and_approval_remain_blank(self):
        self.assertTrue(self.frames["cases.csv"][hierarchy.review.REVIEW_COLUMNS].eq("").all().all())
        criteria = hierarchy.review.read_json(self.package/"reviewer_packet/criteria.json")
        self.assertEqual(criteria["approval"], {"approved_by": "", "approval_reference": ""})
        old = hierarchy.review.read_json(self.package/"reviewer_packet/reviewer/criteria_draft.json")
        with self.assertRaisesRegex(ValueError, "blocked"):
            hierarchy.review.validate_criteria(old)

    def test_no_false_research_claims(self):
        for key in ["reference_labels_created", "advisor_approval_inferred", "old_labels_converted", "new_cases_sampled",
                    "model_outputs_read", "reviewers_assigned", "training_performed", "test_scored", "accuracy_computed",
                    "split_changed", "training_exclusions_applied"]:
            self.assertIs(self.summary[key], False)
        plan = hierarchy.review.read_json(self.package/"evaluation_plan.json")
        self.assertFalse(plan["winner_selected"])
        self.assertFalse(plan["accuracy_computed"])
        self.assertIn("114", plan["partitions"]["known_exposure"])

    def test_only_blind_evidence_read_not_analyst_keys_or_models(self):
        original_json = hierarchy.review.read_json
        original_strings = hierarchy.review.read_strings

        def guard_path(path):
            path = str(path)
            for forbidden in ["analyst_only", "cohort_cases", "model_bundle", "model_banks", "selection_key", "case_evidence"]:
                self.assertNotIn(forbidden, path)

        def json_reader(path):
            guard_path(path)
            return original_json(path)

        def csv_reader(path):
            guard_path(path)
            self.assertEqual(Path(path).parent.name, "reviewer")
            return original_strings(path)

        with patch.object(hierarchy.review, "read_json", side_effect=json_reader), \
                patch.object(hierarchy.review, "read_strings", side_effect=csv_reader), \
                patch.object(joblib, "load", side_effect=AssertionError("No models")):
            result = hierarchy.preview(self.package, self.first)
        self.assertEqual(result["review_id"], self.first)
        for scope, values in result["scope_source_fields"].items():
            self.assertEqual(set(values), {table+"."+field for table, fields in hierarchy.SCOPES[scope].items() for field in fields})

    def test_preview_exact_source_values_and_blank_notes(self):
        result = hierarchy.preview(self.package, self.first)
        selected = hierarchy.case_frames(self.frames, self.first)
        for scope, tables in hierarchy.SCOPES.items():
            for table, fields in tables.items():
                for field in fields:
                    self.assertEqual(result["scope_source_fields"][scope][table+"."+field], selected[table][field].tolist())
        self.assertTrue(all(row["review_status"] == "not_reviewed" for row in result["note_templates"]))

    def test_unknown_ids_and_raw_order_id_lookup_rejected(self):
        for review_id in ["BR9999", "0"*32, "", None]:
            with self.subTest(review_id=review_id), self.assertRaisesRegex(ValueError, "Unknown blind review"):
                hierarchy.preview(self.package, review_id)

    def test_blank_template_cannot_be_recorded(self):
        path = self.root/"invalid_blank/journal.json"
        with self.assertRaises(ValueError):
            hierarchy.append_note(hierarchy.note_template(), "software_test", None, path, self.package)
        self.assertFalse(path.parent.exists())

    def test_unresolved_note_validates_without_becoming_a_label(self):
        for scope in hierarchy.SCOPES:
            note = self.unresolved_test_note(scope)
            values = hierarchy.validate_note(self.frames, note)
            self.assertEqual(set(values), set(note["evidence_fields"]))
            self.assertEqual(note["parent_label"], "")
            self.assertEqual(note["anomaly_subtype"], "")
            self.assertEqual(note["proposed_use_action"], "pending")

    def test_unresolved_cannot_be_normal_or_exclusion(self):
        for changes in [{"parent_label": "no_supported_anomaly"}, {"anomaly_subtype": "data_error"},
                        {"proposed_use_action": "exclude_from_named_analysis"}, {"missing_evidence": ""}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "Unresolved"):
                hierarchy.validate_note(self.frames, {**self.unresolved_test_note(), **changes})

    def test_model_scores_wrong_scope_or_duplicate_fields_rejected(self):
        for changes in [{"evidence_fields": ["model.score"]}, {"evidence_fields": ["cases.csv.payment_value_sum"]},
                        {"reason_codes": ["P01"]}, {"reason_codes": ["T01", "T01"]}, {"evidence_fields": []},
                        {"evidence_fields": [["cases.csv.order_status"]]}, {"reason": " "}, {"confidence": "99%"},
                        {"analysis_scope": "geographic_anomaly"}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                hierarchy.validate_note(self.frames, {**self.unresolved_test_note(), **changes})

    def test_extra_label_probability_or_approval_fields_rejected(self):
        for name in ["order_id", "probability", "approved_by", "fraud", "reviewer_id"]:
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, "Unexpected assessment fields"):
                hierarchy.validate_note(self.frames, {**self.unresolved_test_note(), name: "not_an_actual_assessment"})

    def test_legacy_labels_not_automatically_converted(self):
        for label in hierarchy.review.LABELS:
            note = {**self.unresolved_test_note(), "review_status": "completed", "parent_label": label}
            with self.assertRaisesRegex(ValueError, "legacy labels"):
                hierarchy.validate_note(self.frames, note)

    def test_anomaly_requires_reference_and_known_subtype(self):
        for subtype in ["data_error", "valid_deviation", "unclassified", "fraud"]:
            note = {**self.unresolved_test_note(), "review_status": "completed", "parent_label": "anomaly", "anomaly_subtype": subtype}
            with self.assertRaisesRegex(ValueError, "subtype and explicit verification"):
                hierarchy.validate_note(self.frames, note)

    def test_valid_deviation_cannot_omit_comparison_or_delete_inputs(self):
        base = {**self.unresolved_test_note(), "review_status": "completed", "parent_label": "anomaly",
                "anomaly_subtype": "valid_deviation", "verification_reference": "Software rejection fixture; no verification."}
        for changes in [{}, {"comparison_reference": "Software fixture only", "proposed_use_action": "exclude_from_named_analysis"},
                        {"comparison_reference": "Software fixture only", "proposed_use_action": "retain"}]:
            with self.assertRaisesRegex(ValueError, "Valid deviation"):
                hierarchy.validate_note(self.frames, {**base, **changes})

    def test_confirmed_invalid_cannot_retain_numeric_input(self):
        note = {**self.unresolved_test_note(), "review_status": "completed", "parent_label": "anomaly", "anomaly_subtype": "data_error",
                "verification_reference": "Software rejection fixture only", "proposed_use_action": "retain"}
        with self.assertRaisesRegex(ValueError, "Confirmed-invalid"):
            hierarchy.validate_note(self.frames, note)

    def negative_rejection_fixture(self):
        return {**self.unresolved_test_note(), "review_status": "completed", "parent_label": "no_supported_anomaly",
                "missing_evidence": "", "reason_codes": ["T01", "T02", "T03", "T04"]}

    def test_negative_needs_all_checks_and_comparison_reference(self):
        note = self.negative_rejection_fixture()
        with self.assertRaisesRegex(ValueError, "comparison reference"):
            hierarchy.validate_note(self.frames, note)
        note["reason_codes"] = ["T01"]
        with self.assertRaisesRegex(ValueError, "all scope checks"):
            hierarchy.validate_note(self.frames, note)

    def test_missing_source_not_silently_normal(self):
        frames = {name: frame.copy() for name, frame in self.frames.items()}
        frames["cases.csv"].loc[frames["cases.csv"].review_id.eq(self.first), "order_delivered_customer_date"] = ""
        note = {**self.negative_rejection_fixture(), "comparison_reference": "Software rejection fixture only"}
        with self.assertRaisesRegex(ValueError, "Required source evidence is missing"):
            hierarchy.validate_note(frames, note)

    def test_empty_history_read_does_not_create_journal(self):
        path = self.root/"absent/journal.json"
        journal, digest = hierarchy.load_journal(path, self.package)
        self.assertEqual(journal["events"], [])
        self.assertIsNone(digest)
        self.assertFalse(path.parent.exists())

    def test_journal_appends_unresolved_technical_notes_with_source_binding(self):
        path = self.root/"revisions/journal.json"
        first = hierarchy.append_note(self.unresolved_test_note(), "software_test_a", None, path, self.package)
        _, digest = hierarchy.load_journal(path, self.package)
        second = hierarchy.append_note(self.unresolved_test_note(), "software_test_b", digest, path, self.package)
        journal, _ = hierarchy.load_journal(path, self.package)
        self.assertEqual(journal["events"], [first, second])
        self.assertEqual(second["previous_sha256"], first["sha256"])
        self.assertEqual(second["revision"], 2)
        self.assertIn("provisional", journal["role"])
        self.assertTrue(all(event["assessment"]["parent_label"] == "" for event in journal["events"]))

    def test_journal_stale_writer_and_lock_refused(self):
        path = self.root/"stale/journal.json"
        hierarchy.append_note(self.unresolved_test_note(), "software_test", None, path, self.package)
        before = hierarchy.review.digest(path)
        with self.assertRaisesRegex(ValueError, "Stale"):
            hierarchy.append_note(self.unresolved_test_note(), "software_test", None, path, self.package)
        lock = path.with_name(path.name+".lock")
        lock.touch()
        with self.assertRaisesRegex(ValueError, "lock exists"):
            hierarchy.append_note(self.unresolved_test_note(), "software_test", before, path, self.package)
        self.assertEqual(before, hierarchy.review.digest(path))
        lock.unlink()

    def test_journal_tampered_chain_and_source_values_rejected(self):
        path = self.root/"tamper/journal.json"
        hierarchy.append_note(self.unresolved_test_note(), "software_test", None, path, self.package)
        original = hierarchy.review.read_json(path)
        changed = deepcopy(original)
        changed["events"][0]["assessment"]["reason"] = "Modified technical note"
        hierarchy.review.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "hash chain"):
            hierarchy.load_journal(path, self.package)
        changed = deepcopy(original)
        changed["events"][0]["source_values"] = {}
        changed["events"][0]["sha256"] = hierarchy.payload_hash({k: v for k, v in changed["events"][0].items() if k != "sha256"})
        hierarchy.review.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "source evidence"):
            hierarchy.load_journal(path, self.package)

    def test_invalid_reviewer_or_protected_destination_refused(self):
        for reviewer in [None, "", "../person", "a b"]:
            with self.assertRaisesRegex(ValueError, "reviewer ID"):
                hierarchy.append_note(self.unresolved_test_note(), reviewer, None, self.root/"invalid/journal.json", self.package)
        for path in [self.package/"journal.json", hierarchy.review.DEFAULT_BATCH/"journal.json", ROOT/"data/raw/new.json"]:
            with self.assertRaises(ValueError):
                hierarchy.append_note(self.unresolved_test_note(), "software_test", None, path, self.package)

    def test_package_no_overwrite_or_corrupted_output(self):
        with self.assertRaises(FileExistsError):
            hierarchy.prepare(self.package)
        target = self.root/"bad_package"
        shutil.copytree(self.package, target)
        (target/"reviewer_packet/criteria.json").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "file changed"):
            hierarchy.load_package(target)

    def test_failed_publication_leaves_no_partial_package(self):
        target = self.root/"failed_package"
        with patch.object(hierarchy, "load_package", side_effect=ValueError("technical failure")):
            with self.assertRaisesRegex(ValueError, "technical failure"):
                hierarchy.prepare(target)
        self.assertFalse(target.exists())

    def test_changed_code_or_original_binding_refused(self):
        with patch.object(hierarchy, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "code/rubric changed"):
                hierarchy.load_package(self.package)
        provenance = deepcopy(self.manifest["provenance"])
        provenance["reviewer_manifest_sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            hierarchy.verify_sources(provenance)

    def test_cli_summary_preview_and_no_side_effect_history(self):
        for args in [["--summary"], ["--preview", self.first], ["--history", "--journal", str(self.root/"cli/journal.json")]]:
            result = subprocess.run([sys.executable, str(Path(hierarchy.__file__)), "--output", str(self.package), *args],
                                    text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIsInstance(json.loads(result.stdout), dict)
        self.assertFalse((self.root/"cli").exists())

    def test_cli_check_only_does_not_write_or_authenticate(self):
        path = self.root/"technical_note.json"
        hierarchy.review.write_json(path, self.unresolved_test_note())
        result = subprocess.run([sys.executable, str(Path(hierarchy.__file__)), "--output", str(self.package),
                                 "--check-note", str(path)], text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertTrue(status["syntax_valid"])
        self.assertFalse(status["saved"])
        self.assertFalse(status["verification_authenticated"])
        self.assertFalse(status["reference_ready"])


if __name__ == "__main__":
    unittest.main()
