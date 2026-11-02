"""Real-source, blind 20-order preparation; no research labels are generated."""

from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import prepare_delivery_review20 as preparation


class DeliveryReview20Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")):
            cls.original, cls.key, cls.boundary, cls.provenance = preparation.load_inputs()
            cls.frames, cls.selected, cls.audit = preparation.select_subset(cls.original, cls.key, cls.boundary)
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"review20"
        cls.original_hashes = {str(p): preparation.review.digest(p) for p in preparation.review.DEFAULT_BATCH.rglob("*") if p.is_file()}
        cls.summary = preparation.prepare(cls.output)

    def test_twenty_unique_real_orders_and_original_pseudonyms(self):
        self.assertEqual(len(self.frames["cases.csv"]), 20)
        self.assertTrue(self.selected.order_id.is_unique)
        self.assertTrue(self.selected.review_id.is_unique)
        self.assertTrue(set(self.selected.review_id).issubset(self.original["cases.csv"].review_id))
        self.assertEqual(self.audit["selected_by_sampling_group"], {"any_anchor_flag": 10, "none_anchor_flag": 10})

    def test_source_values_byte_strings_preserved_and_source_counts_reconcile(self):
        for name, frame in self.frames.items():
            original = self.original[name]
            expected = original.loc[original.review_id.isin(self.selected.review_id)].reset_index(drop=True)
            pd.testing.assert_frame_equal(frame, expected, check_exact=True)
        loaded, _ = preparation.review.load_evidence(self.output/"reviewer_packet")
        for name, frame in loaded.items():
            pd.testing.assert_frame_equal(frame, self.frames[name], check_exact=True)

    def test_original_pilot_files_unchanged(self):
        for name, before in self.original_hashes.items():
            self.assertEqual(preparation.review.digest(name), before)

    def test_no_actual_milestone_or_purchase_crosses_proposed_boundary(self):
        for field in preparation.hierarchy.TIMES:
            dates = pd.to_datetime(self.frames["cases.csv"][field], format="%Y-%m-%d %H:%M:%S", errors="raise")
            self.assertTrue(dates.notna().all())
            self.assertTrue(dates.lt(self.boundary).all())
        self.assertGreater(self.audit["excluded_purchase_or_actual_milestone_on_or_after_boundary"], 0)

    def test_selection_is_reproducible_and_display_order_stays_original(self):
        actual, key, audit = preparation.select_subset(self.original, self.key, self.boundary)
        pd.testing.assert_frame_equal(key, self.selected, check_exact=True)
        self.assertEqual(audit, self.audit)
        for name, frame in actual.items():
            pd.testing.assert_frame_equal(frame, self.frames[name], check_exact=True)
        expected_order = self.original["cases.csv"].loc[lambda f: f.review_id.isin(key.review_id), "review_id"].tolist()
        self.assertEqual(self.frames["cases.csv"].review_id.tolist(), expected_order)

    def test_no_scores_model_weights_or_test_feature_values_parsed(self):
        for path in self.reads:
            for forbidden in ["validation_scores.csv", "validation_flags.csv", "predictions.csv", "test_timing", "frozen_forecasts.csv"]:
                self.assertNotIn(forbidden, path)

    def test_reviewer_packet_has_no_selection_mapping_flags_or_model_results(self):
        folder = self.output/"reviewer_packet"
        text = "\n".join(p.read_text() for p in folder.rglob("*") if p.is_file())
        for order_id in self.key.order_id:
            self.assertNotIn(order_id, text)
        for token in ["ensemble_rank_mean_tail050", "sampling_group", "agreement_group", "selection_hash",
                      "timing_only_flag", "business_only_flag", "combined_flag", "inclusion_probability"]:
            self.assertNotIn(token, text)
        for name, columns in preparation.review.SCHEMAS.items():
            self.assertEqual(self.frames[name].columns.tolist(), columns)

    def test_sixty_scoped_notes_are_entirely_unassessed(self):
        tasks = preparation.review.read_json(self.output/"reviewer_packet/assessment_template.json")
        self.assertEqual(len(tasks), 60)
        self.assertEqual(len({(t["review_id"], t["analysis_scope"]) for t in tasks}), 60)
        for task in tasks:
            self.assertEqual(task, preparation.hierarchy.note_template(task["review_id"], task["analysis_scope"]))
        self.assertTrue(self.frames["cases.csv"][preparation.review.REVIEW_COLUMNS].eq("").all().all())
        self.assertEqual(self.summary["completed_assessments"], 0)

    def test_casebook_contains_only_selected_real_cases_and_no_completed_assessments(self):
        text = (self.output/"reviewer_packet/20_don_can_ra_soat.md").read_text()
        self.assertEqual(text.count("## BR"), 20)
        self.assertEqual(text.count("Quy trình/thời gian: chưa đánh giá."), 20)
        for review_id in self.selected.review_id:
            self.assertIn("## "+review_id, text)
        self.assertEqual(text, preparation.casebook(self.frames))

    def test_clarification_does_not_approve_old_or_new_criteria(self):
        criteria = preparation.criteria()
        self.assertEqual(criteria["approval"], {"approved_by": "", "approval_reference": ""})
        self.assertIn("not_advisor_approved", criteria["status"])
        self.assertIn("not an anomaly", criteria["delivery_performance_policy"]["early"])
        self.assertEqual([r["code"] for r in criteria["rules"]], ["T01", "T02", "T03", "T04", "P01", "P02", "P03", "O01", "O02"])
        with self.assertRaises(ValueError):
            preparation.review.validate_criteria(preparation.review.read_json(self.output/"reviewer_packet/reviewer/criteria_draft.json"))

    def test_no_reference_accuracy_training_or_session_claims(self):
        for name in ["human_labels_created", "reviewer_session_started", "advisor_approval_inferred", "reference_ready",
                     "model_trained", "threshold_changed", "test_scored", "accuracy_computed"]:
            self.assertFalse(self.summary[name])
        self.assertFalse(self.audit["selection_is_accuracy_reference"])
        self.assertFalse(list(self.output.rglob("journal.json")))

    def test_blank_note_cannot_be_validated_as_completed_human_work(self):
        note = preparation.hierarchy.note_template(self.selected.iloc[0].review_id, "process_timing")
        with self.assertRaises(ValueError):
            preparation.hierarchy.validate_note(self.frames, note)

    def test_insufficient_real_candidates_fail_without_padding(self):
        with self.assertRaises(ValueError):
            preparation.select_subset(self.original, self.key.head(1), self.boundary)

    def test_package_hashes_and_reconstruction_verify(self):
        frames, manifest = preparation.load_package(self.output)
        self.assertEqual(manifest["summary"], self.summary)
        for name, frame in frames.items():
            pd.testing.assert_frame_equal(frame, self.frames[name], check_exact=True)

    def test_existing_output_is_not_overwritten(self):
        with self.assertRaises(FileExistsError):
            preparation.prepare(self.output)

    def test_tampered_guide_fails_verification(self):
        target = Path(self.temp.name)/"tampered"
        shutil.copytree(self.output, target)
        with (target/"reviewer_packet/README.md").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            preparation.load_package(target)


if __name__ == "__main__":
    unittest.main()
