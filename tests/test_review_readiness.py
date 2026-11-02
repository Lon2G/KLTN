"""Review preparation checks on the frozen real 120-case pilot, no label entry."""

from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import prepare_review_readiness as readiness


class ReviewReadinessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        with patch.object(joblib, "load", side_effect=AssertionError("Model loading forbidden")), \
                patch.object(readiness.review, "open_session", side_effect=AssertionError("No reviewer sessions")), \
                patch.object(readiness.pilot, "select_sample", side_effect=AssertionError("No resampling")):
            source, provenance = readiness.integration.load_inputs()
            frames, summary = readiness.integration.build_integration(source)
            readiness.integration.write_integration(frames, summary, provenance, cls.root/"integration")
            cls.inputs, cls.provenance = readiness.load_inputs(cls.root/"integration")
            cls.frames, cls.summary = readiness.build_readiness(cls.inputs)
            cls.output = cls.root/"readiness"
            cls.saved = readiness.write_readiness(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.manifest = readiness.load_snapshot(cls.output)
        cls.bridge = cls.frames["analyst_only/review_case_bridge.csv"]

    def test_original_120_cases_preserved_in_display_order(self):
        self.assertEqual(len(self.bridge), 120)
        self.assertEqual(self.bridge.review_id.tolist(), self.inputs["key"].review_id.tolist())
        self.assertEqual(self.bridge.order_id.tolist(), self.inputs["key"].order_id.tolist())
        self.assertTrue(self.bridge.review_id.is_unique and self.bridge.order_id.is_unique)
        self.assertEqual(self.saved["payment_records"], 135)
        self.assertEqual(self.saved["item_records"], 171)

    def test_every_original_selection_field_and_weight_preserved(self):
        original = self.inputs["key"]
        pd.testing.assert_frame_equal(self.bridge[original.columns], original, check_dtype=False, check_exact=True)
        pd.testing.assert_frame_equal(self.frames["analyst_only/sampling_strata.csv"], self.inputs["strata"], check_exact=True)
        self.assertEqual(self.saved["eligible_original_sampling_population"], 1522)
        self.assertAlmostEqual(self.bridge.sampling_weight.sum(), 1522, places=10)

    def test_bridge_points_only_to_real_validation_orders(self):
        source = self.inputs["cases"].set_index("order_id").loc[self.bridge.order_id]
        self.assertTrue(source.split.eq("validation").all())
        self.assertFalse(self.bridge.previously_human_reviewed.any())
        fields = [name for name in self.bridge if name not in self.inputs["key"]]
        pd.testing.assert_frame_equal(self.bridge[fields].reset_index(drop=True), source[fields].reset_index(drop=True),
                                      check_dtype=False, check_exact=True)

    def test_blind_packet_files_are_byte_identical_to_original(self):
        batch = Path(self.provenance["pilot_directory"])
        copied = self.output/"reviewer_packet/reviewer"
        original = readiness.review.read_json(batch/"reviewer/manifest.json")
        self.assertEqual(set(path.name for path in copied.iterdir()), set(original["output_hashes"]) | {"manifest.json"})
        for name in [*original["output_hashes"], "manifest.json"]:
            self.assertEqual(readiness.review.digest(copied/name), readiness.review.digest(batch/"reviewer"/name))
        self.assertTrue(self.saved["blind_packet_byte_identical"])

    def test_no_model_fields_or_order_mapping_leak_into_reviewer_tables(self):
        evidence, _ = readiness.review.load_evidence(self.output/"reviewer_packet")
        forbidden = {"order_id", "score", "threshold", "warning", "process_trace_fitness", "sampling_weight", "agreement_group",
                     "evidence_codes_json", "delivery_models_evaluated", "inclusion_probability"}
        for name, frame in evidence.items():
            self.assertFalse(forbidden.intersection(frame.columns), name)
            self.assertEqual(frame.columns.tolist(), readiness.review.SCHEMAS[name])
        self.assertFalse(list((self.output/"reviewer_packet").rglob("*bridge*")))

    def test_every_source_judgment_and_approval_field_remains_blank(self):
        evidence, _ = readiness.review.load_evidence(self.output/"reviewer_packet")
        self.assertTrue(evidence["cases.csv"][readiness.review.REVIEW_COLUMNS].eq("").all().all())
        criteria = readiness.review.read_json(self.output/"reviewer_packet/reviewer/criteria_draft.json")
        self.assertEqual(criteria["status"], "draft_pending_advisor")
        for field in readiness.review.CRITERIA_FIELDS-{"version", "status"}:
            self.assertEqual(criteria[field], "")
        with self.assertRaisesRegex(ValueError, "blocked"):
            readiness.review.validate_criteria(criteria)

    def test_no_review_session_started(self):
        self.assertFalse(list(self.output.rglob("sessions")))
        self.assertFalse(list(self.output.rglob("review_progress.csv")))
        self.assertEqual(self.manifest["canonical_review_batch"], str(readiness.review.DEFAULT_BATCH))

    def test_readiness_separates_technical_checks_from_human_decisions(self):
        checks = self.frames["analyst_only/readiness_checks.csv"]
        self.assertEqual(len(checks), 10)
        self.assertEqual(checks.blocks_label_entry.sum(), 5)
        self.assertTrue(checks.loc[checks.blocks_label_entry, "status"].isin(["pending_advisor", "missing"]).all())
        self.assertFalse(self.saved["ready_for_label_entry"])
        self.assertEqual(self.saved["status"], "prepared_pending_human_protocol")

    def test_review_coverage_does_not_imply_broader_target_validation(self):
        for summary_field, column in [("mapped_cases_with_business_results", "business_profiles_scored"),
                                       ("mapped_cases_with_delivery_forecasts", "delivery_models_forecast"),
                                       ("mapped_cases_with_saved_delivery_evaluation", "delivery_models_evaluated")]:
            self.assertEqual(self.summary[summary_field], self.bridge[column].notna().sum())
        guide = (self.output/"README.md").read_text()
        self.assertIn("New seller", guide)
        self.assertIn("NOT inserted", guide)

    def test_sampling_probabilities_independently_recomputed(self):
        for row in self.inputs["strata"].itertuples(index=False):
            selected = self.bridge.loc[self.bridge.purchase_month.eq(row.purchase_month) & self.bridge.agreement_group.eq(row.agreement_group)]
            self.assertEqual(len(selected), row.sample_cases)
            np.testing.assert_allclose(selected.inclusion_probability, row.sample_cases/row.eligible_cases)
            np.testing.assert_allclose(selected.sampling_weight, row.eligible_cases/row.sample_cases)

    def test_read_only_preview_works_without_analyst_files(self):
        copied = self.root/"blind_only"
        shutil.copytree(self.output/"reviewer_packet", copied)
        original = pd.read_csv
        paths = []

        def read(path, *args, **kwargs):
            paths.append(Path(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read):
            frames, _ = readiness.review.load_evidence(copied)
            output = []
            readiness.review.show_case(frames, frames["cases.csv"].iloc[0].review_id, output.append)
        self.assertTrue(output)
        self.assertTrue(all(path.is_relative_to(copied/"reviewer") for path in paths))

    def test_missing_order_or_changed_review_order_is_rejected(self):
        without = self.inputs["cases"].loc[lambda x: x.order_id.ne(self.inputs["key"].iloc[0].order_id)]
        with self.assertRaisesRegex(ValueError, "map one-to-one"):
            readiness.validate_inputs({**self.inputs, "cases": without})
        with self.assertRaisesRegex(ValueError, "display order"):
            readiness.validate_inputs({**self.inputs, "key": self.inputs["key"].iloc[::-1]})

    def test_exact_csv_roundtrip_and_all_files_hashed(self):
        for name, expected in self.frames.items():
            actual = readiness.shared.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        actual = {str(path.relative_to(self.output)) for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(actual, set(self.manifest["output_hashes"]) | {"manifest.json"})

    def test_overwrite_and_packet_tampering_refused(self):
        with self.assertRaises(FileExistsError):
            readiness.write_readiness(self.frames, self.summary, self.provenance, self.output)
        copied = self.root/"tampered"
        shutil.copytree(self.output, copied)
        with (copied/"reviewer_packet/reviewer/cases.csv").open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            readiness.load_snapshot(copied)

    def test_changed_code_and_source_fail_closed(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code/protocol"):
            readiness.verify_sources(changed)
        changed = deepcopy(self.provenance)
        changed["pilot_manifest_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "Pilot manifest"):
            readiness.verify_sources(changed)

    def test_failed_publication_does_not_leave_partial_packet(self):
        output = self.root/"incomplete"
        with patch.object(readiness, "verify_sources", side_effect=[None, ValueError("Source changed")]):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                readiness.write_readiness(self.frames, self.summary, self.provenance, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".review-readiness-*")))

    def test_no_labels_approvals_accuracy_or_new_cases_claimed(self):
        for field in ["labels_entered", "approval_created", "approved_criteria_available", "ready_for_label_entry",
                      "model_outputs_exposed_to_reviewers", "test_scored", "accuracy_computed", "new_cases_sampled"]:
            self.assertFalse(self.summary[field], field)


if __name__ == "__main__":
    unittest.main()
