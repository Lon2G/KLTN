"""Real Olist evidence tests; temporary software-test notes are not research labels."""

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
import manage_data_disposition as disposition


class DataDispositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(joblib, "load", side_effect=AssertionError("Do not load model weights")), \
                patch.object(disposition.review, "open_session", side_effect=AssertionError("Do not start blind review")):
            cls.repository = disposition.inspection.load_repository()
            cls.frames, cls.summary = disposition.build_ledger(cls.repository)
        cls.cases = cls.repository["frames"]["case_evidence.csv"]
        cls.ledger = cls.frames["analysis_use_ledger.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.snapshot = cls.root/"snapshot"
        disposition.prepare(cls.repository, cls.snapshot)
        cls.order_id = cls.cases.sort_values(["order_purchase_timestamp", "order_id"]).iloc[0].order_id

    def software_test_note(self, order_id=None, scope="process_evidence", **kwargs):
        return {**disposition.note_template(), "order_id": order_id or self.order_id, "analysis_scope": scope,
                "supporting_fields": [disposition.SCOPES[scope][0]],
                "reason": "Temporary software validation only; no human judgment or verification performed.", **kwargs}

    def test_scope_and_protection_counts(self):
        self.assertEqual(self.summary["source_orders"], 7333)
        self.assertEqual(self.summary["accessible_orders"], 7213)
        self.assertEqual(self.summary["protected_pilot_orders"], 120)
        self.assertEqual(len(self.ledger), 36065)
        self.assertFalse(self.ledger.duplicated(["order_id", "analysis_scope"]).any())
        self.assertTrue(self.ledger.groupby("order_id").size().eq(5).all())
        self.assertEqual(set(self.ledger.order_id), set(self.cases.order_id))
        self.assertFalse(set(self.ledger.order_id) & self.repository["protected_ids"])

    def test_every_human_field_unassessed_and_no_source_deleted(self):
        for column, default in disposition.DECISIONS.items():
            self.assertTrue(self.ledger[column].eq(default).all())
        self.assertTrue(self.ledger.raw_order_retained.all())
        self.assertFalse(self.ledger.human_decision_applied.any())
        self.assertFalse(self.summary["human_labels_created"])
        self.assertFalse(self.summary["advisor_approval_inferred"])
        self.assertFalse(self.summary["split_changed"])

    def test_exact_source_values_and_saved_codes_not_labels(self):
        source = self.cases.set_index("order_id")
        for scope in disposition.SCOPES:
            rows = self.ledger.loc[self.ledger.analysis_scope.eq(scope)]
            for row in rows.itertuples():
                expected = disposition.inspection.json_value(source.loc[row.order_id, disposition.SCOPES[scope]].to_dict())
                self.assertEqual(json.loads(row.source_fields_json), expected)
                self.assertEqual(row.saved_signal_codes_json, source.at[row.order_id, "evidence_codes_json"])

    def test_partial_and_inverted_processes_retained_without_error_verdict(self):
        partial = self.ledger.loc[self.ledger.support_state.eq("partial")]
        self.assertEqual(len(partial), 678)
        self.assertTrue(partial.automatic_use_recommendation.eq("retain_partial_evidence").all())
        ids = set(self.cases.loc[self.cases.process_inverted_pairs.gt(0), "order_id"])
        rows = self.ledger.loc[self.ledger.order_id.isin(ids) & self.ledger.analysis_scope.eq("process_evidence")]
        self.assertFalse(rows.empty)
        self.assertTrue(rows.observation_reasons_json.str.contains("not_verified_entry_error").all())
        self.assertTrue(rows.data_quality_assessment.eq("not_assessed").all())

    def test_timing_preserves_inner_cutoff_and_does_not_reassign_cases(self):
        actual = self.ledger.loc[self.ledger.analysis_scope.eq("completed_timing") & self.ledger.support_state.eq("available")]
        expected = self.cases.loc[self.cases.timing_input_eligible & self.cases.inner_role.isin(["fit", "calibration", "validation"])]
        self.assertEqual(set(actual.order_id), set(expected.order_id))
        self.assertEqual(len(actual), 6005)
        self.assertFalse(set(actual.order_id) & set(self.cases.loc[self.cases.inner_role.eq("deferred_at_inner_cutoff"), "order_id"]))

    def test_payment_eligibility_is_not_timing_eligibility(self):
        timing = self.ledger.loc[self.ledger.analysis_scope.eq("completed_timing") & self.ledger.support_state.eq("withheld")]
        payments = self.ledger.loc[self.ledger.analysis_scope.eq("payment_order_features")]
        self.assertEqual(len(payments), 7213)
        self.assertTrue(payments.support_state.eq("available").all())
        self.assertTrue(set(timing.order_id).issubset(payments.order_id))
        self.assertTrue(payments.observation_reasons_json.str.contains("retrospective").all())

    def test_existing_model_warning_does_not_automatically_exclude_or_label(self):
        ids = set(self.cases.loc[self.cases.business_profiles_with_warning.gt(0), "order_id"])
        rows = self.ledger.loc[self.ledger.order_id.isin(ids) & self.ledger.analysis_scope.eq("payment_order_features")]
        self.assertFalse(rows.empty)
        self.assertTrue(rows.support_state.eq("available").all())
        self.assertTrue(rows.proposed_use_action.eq("pending").all())
        self.assertTrue(rows.atypicality_assessment.eq("not_assessed").all())

    def test_geography_withheld_is_not_confirmed_bad_coordinate(self):
        rows = self.ledger.loc[self.ledger.analysis_scope.eq("geographic_context") & self.ledger.support_state.eq("withheld")]
        self.assertEqual(len(rows), 296)
        self.assertTrue(rows.data_quality_assessment.eq("not_assessed").all())
        for row in rows.itertuples():
            self.assertIn("inherited_provisional_zip_screen_not_verified_road_distance", json.loads(row.observation_reasons_json))

    def test_seller_multi_and_missing_history_are_not_zero_filled(self):
        ids = set(self.cases.loc[self.cases.seller_count.gt(1), "order_id"])
        rows = self.ledger.loc[self.ledger.analysis_scope.eq("seller_outcome_context") & self.ledger.order_id.isin(ids)]
        self.assertEqual(len(rows), 144)
        self.assertTrue(rows.support_state.eq("withheld").all())
        for values in rows.source_fields_json:
            self.assertIsNone(json.loads(values)["single_seller_prior_after_promise_fraction"])

    def test_coverage_reconciles_each_split_scope(self):
        totals = self.frames["coverage.csv"].groupby(["split", "analysis_scope"]).orders.sum()
        for (split, scope), count in totals.items():
            self.assertEqual(count, 5480 if split == "train" else 1733)
        self.assertTrue(self.summary["scope_counts_overlap"])

    def test_result_independent_of_source_row_order(self):
        repository = {**self.repository, "frames": {**self.repository["frames"], "case_evidence.csv": self.cases.iloc[::-1]}}
        frames, summary = disposition.build_ledger(repository)
        self.assertEqual(summary, self.summary)
        for name in frames:
            pd.testing.assert_frame_equal(frames[name], self.frames[name], check_exact=True)

    def test_snapshot_roundtrip_and_no_journal_created(self):
        manifest = disposition.load_snapshot(self.snapshot)
        self.assertEqual(manifest["summary"], self.summary)
        for name in disposition.TABLES:
            pd.testing.assert_frame_equal(disposition.shared.read_table(self.snapshot, name, manifest), self.frames[name], check_exact=True)
        self.assertEqual(disposition.review.read_json(self.snapshot/"note_template.json"), disposition.note_template())
        self.assertFalse((self.snapshot/"journal.json").exists())

    def test_blank_template_cannot_be_submitted(self):
        with self.assertRaises(ValueError):
            disposition.validate_note(self.repository, disposition.note_template())

    def test_protected_case_annotations_blocked_for_all_original_ids(self):
        for order_id in self.repository["protected_ids"]:
            with self.assertRaisesRegex(ValueError, "reserved for blinded review"):
                disposition.validate_note(self.repository, self.software_test_note(order_id))

    def test_final_test_id_blocked_without_feature_loading(self):
        reserved = pd.read_csv(ROOT/"data/experiments/bed_bath_table_v1/cohort_cases.csv", usecols=["order_id", "split"], dtype="string")
        order_id = reserved.loc[reserved.split.eq("test"), "order_id"].iloc[0]
        with self.assertRaisesRegex(ValueError, "not in the accessible development scope"):
            disposition.validate_note(self.repository, self.software_test_note(order_id))

    def test_note_requires_explicit_fields_and_no_model_score_verification(self):
        for changes in [{"reason": " "}, {"supporting_fields": []}, {"supporting_fields": ["business_profiles_with_warning"]},
                        {"supporting_fields": ["process_visible_events"]*2}, {"proposed_use_action": "delete_raw"},
                        {"data_quality_assessment": "Normal"}, {"analysis_scope": "all_data"}, {"reason": None}]:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                disposition.validate_note(self.repository, self.software_test_note(**changes))
        with self.assertRaisesRegex(ValueError, "Unexpected note"):
            disposition.validate_note(self.repository, {**self.software_test_note(), "probability": 1})

    def test_confirmed_claims_require_verification_and_consistent_dimensions(self):
        for changes in [{"data_quality_assessment": "confirmed_error"},
                        {"atypicality_assessment": "valid_deviation", "data_quality_assessment": "no_error_identified"}]:
            with self.assertRaisesRegex(ValueError, "verification reference"):
                disposition.validate_note(self.repository, self.software_test_note(**changes))
        note = self.software_test_note(data_quality_assessment="confirmed_error", atypicality_assessment="valid_deviation",
                                       verification_reference="Software contract rejection test only, not a verification.")
        with self.assertRaisesRegex(ValueError, "consistent no-error"):
            disposition.validate_note(self.repository, note)

    def test_notes_cannot_override_withheld_inputs(self):
        for scope in ["completed_timing", "geographic_context", "seller_outcome_context"]:
            order_id = self.ledger.loc[self.ledger.analysis_scope.eq(scope) & self.ledger.support_state.eq("withheld"), "order_id"].iloc[0]
            with self.assertRaisesRegex(ValueError, "cannot override"):
                disposition.validate_note(self.repository, self.software_test_note(order_id, scope, proposed_use_action="retain"))

    def test_invalid_note_does_not_create_journal(self):
        path = self.root/"invalid-note/journal.json"
        with self.assertRaises(ValueError):
            disposition.append_note(self.repository, self.snapshot, path, "software_test", disposition.note_template(), None)
        self.assertFalse(path.parent.exists())

    def test_empty_journal_read_has_no_side_effect(self):
        path = self.root/"empty/journal.json"
        journal, digest = disposition.load_journal(path, self.snapshot, self.repository)
        self.assertEqual(journal["events"], [])
        self.assertIsNone(digest)
        self.assertFalse(path.parent.exists())

    def test_journal_append_revisions_preserve_unassessed_test_notes(self):
        path = self.root/"revisions/journal.json"
        first = disposition.append_note(self.repository, self.snapshot, path, "software_test_a", self.software_test_note(), None)
        _, digest = disposition.load_journal(path, self.snapshot, self.repository)
        second = disposition.append_note(self.repository, self.snapshot, path, "software_test_b", self.software_test_note(reason="Second temporary technical revision; still no human judgment."), digest)
        journal, _ = disposition.load_journal(path, self.snapshot, self.repository)
        self.assertEqual(journal["events"], [first, second])
        self.assertEqual(second["previous_sha256"], first["sha256"])
        self.assertEqual(second["revision"], 2)
        self.assertIn("not_independent_labels", journal["role"])
        for event in journal["events"]:
            self.assertEqual(event["source_values"], disposition.validate_note(self.repository, event["assessment"]))
            for field, value in disposition.DECISIONS.items():
                self.assertEqual(event["assessment"][field], value)

    def test_stale_writer_and_lock_do_not_overwrite_notes(self):
        path = self.root/"concurrent/journal.json"
        disposition.append_note(self.repository, self.snapshot, path, "software_test", self.software_test_note(), None)
        before = path.read_bytes()
        with self.assertRaisesRegex(ValueError, "Stale"):
            disposition.append_note(self.repository, self.snapshot, path, "software_test", self.software_test_note(), None)
        self.assertEqual(path.read_bytes(), before)
        path.with_name("journal.json.lock").touch()
        with self.assertRaisesRegex(ValueError, "locked"):
            disposition.append_note(self.repository, self.snapshot, path, "software_test", self.software_test_note(), disposition.review.digest(path))
        self.assertEqual(path.read_bytes(), before)

    def test_journal_tamper_and_binding_change_rejected(self):
        path = self.root/"tamper/journal.json"
        disposition.append_note(self.repository, self.snapshot, path, "software_test", self.software_test_note(), None)
        journal = disposition.review.read_json(path)
        changed = deepcopy(journal)
        changed["events"][0]["assessment"]["reason"] = "Changed temporary technical note"
        disposition.review.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "hash chain"):
            disposition.load_journal(path, self.snapshot, self.repository)
        changed = deepcopy(journal)
        changed["disposition_manifest_sha256"] = ""
        disposition.review.write_json(path, changed)
        with self.assertRaisesRegex(ValueError, "binding changed"):
            disposition.load_journal(path, self.snapshot, self.repository)

    def test_snapshot_no_overwrite_tamper_or_partial_publication(self):
        with self.assertRaises(FileExistsError):
            disposition.prepare(self.repository, self.snapshot)
        copied = self.root/"tampered-snapshot"
        shutil.copytree(self.snapshot, copied)
        with (copied/"summary.json").open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            disposition.load_snapshot(copied)
        output = self.root/"partial-snapshot"
        with patch.object(disposition, "verify_sources", side_effect=[None, ValueError("Source changed")]):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                disposition.prepare(self.repository, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".disposition-*")))

    def test_protected_destinations_and_identity_rejected(self):
        with self.assertRaisesRegex(ValueError, "protected source"):
            disposition.prepare(self.repository, ROOT/"data/raw/disposition")
        with self.assertRaisesRegex(ValueError, "outside immutable"):
            disposition.append_note(self.repository, self.snapshot, self.snapshot/"journal.json", "software_test", self.software_test_note(), None)
        with self.assertRaisesRegex(ValueError, "reviewer ID"):
            disposition.append_note(self.repository, self.snapshot, self.root/"identity/journal.json", "../reviewer", self.software_test_note(), None)
        with self.assertRaisesRegex(ValueError, "nested inside"):
            disposition.prepare(self.repository, self.snapshot/"nested-version")

    def test_cli_summary_json_and_case_mode(self):
        base = [sys.executable, str(Path(disposition.__file__)), "--snapshot", str(self.snapshot)]
        env = {**os.environ, "MPLCONFIGDIR": str(self.root/"matplotlib")}
        result = subprocess.run([*base, "--summary"], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout), self.summary)
        result = subprocess.run([*base, "--order-id", self.order_id], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        rows = json.loads(result.stdout)["rows"]
        self.assertEqual(len(rows), 5)
        self.assertTrue(all(row["order_id"] == self.order_id for row in rows))
        journal = self.root/"cli-history/journal.json"
        result = subprocess.run([*base, "--history", "--journal", str(journal)], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["events"], [])
        self.assertFalse(journal.parent.exists())

    def test_code_change_refused_and_plan_has_no_false_completion(self):
        provenance = deepcopy(disposition.load_snapshot(self.snapshot)["provenance"])
        provenance["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code/protocol changed"):
            disposition.verify_sources(provenance)
        plan = (self.snapshot/"implementation_plan.md").read_text()
        self.assertIn("DOES NOT REASSIGN", plan)
        self.assertIn("not exact 80/20", plan)
        self.assertFalse(self.summary["near_real_time_validated"])


if __name__ == "__main__":
    unittest.main()
