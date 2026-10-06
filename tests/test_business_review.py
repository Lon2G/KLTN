"""Real Olist pilot tests; no invented orders, approvals or research judgments."""

from copy import deepcopy
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/03_anomaly_detection"))
import prepare_business_review as prepare
import review_business_cases as review


class BusinessReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original_read = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original_read(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
             patch.object(prepare.ablation.joblib, "load", side_effect=AssertionError("No model deserialization")), \
             patch.object(prepare.ablation, "fit_bank", side_effect=AssertionError("No training")), \
             patch.object(prepare.ablation, "base_scores", side_effect=AssertionError("No model scoring")):
            cls.population, cls.evidence, cls.provenance = prepare.load_inputs()
            cls.frames = prepare.build_frames(cls.population, cls.evidence)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.batch = Path(cls.temporary.name)/"pilot"
        cls.summary = prepare.write_batch(cls.frames, cls.provenance, cls.batch)

    def clone(self, root):
        destination = Path(root)/"pilot"
        shutil.copytree(self.batch, destination)
        return destination

    def test_scope_and_no_ground_truth_or_test_claims(self):
        self.assertEqual(self.summary["common_validation_cases"], 1523)
        self.assertEqual(self.summary["excluded_previously_reviewed"], 1)
        self.assertEqual(self.summary["eligible_cases"], 1522)
        self.assertEqual(self.summary["sample_cases"], 120)
        self.assertEqual(self.summary["payment_records"], 135)
        self.assertEqual(self.summary["item_records"], 171)
        for field in ["human_labels_created", "models_fitted", "models_deserialized", "test_scored", "accuracy_computed", "approved_criteria_available"]:
            self.assertFalse(self.summary[field])
        self.assertFalse(any("test_timing" in path or path.endswith(".joblib") for path in self.read_paths))
        self.assertTrue(self.population.split.eq("validation").all())
        self.assertEqual(set(self.population.purchase_month), {"2018-03", "2018-04", "2018-05"})

    def test_sampling_unique_real_ids_excludes_recorded_labels(self):
        key = self.frames["analyst_only/selection_key.csv"]
        self.assertTrue(key.order_id.is_unique)
        self.assertTrue(key.review_id.is_unique)
        self.assertTrue(key.order_id.isin(self.population.order_id).all())
        self.assertFalse(key.order_id.isin(self.provenance["reviewed_ids"]).any())
        self.assertFalse(key.previously_human_reviewed.any())
        self.assertEqual(set(key.agreement_group), {"all_flag", "disagreement", "none_flag"})

    def test_sampling_order_invariance_weights_and_all_strata_covered(self):
        key, strata = prepare.select_sample(self.population.iloc[::-1])
        pd.testing.assert_frame_equal(key, self.frames["analyst_only/selection_key.csv"])
        pd.testing.assert_frame_equal(strata, self.frames["analyst_only/sampling_strata.csv"])
        np.testing.assert_allclose(strata.inclusion_probability, strata.sample_cases/strata.eligible_cases)
        self.assertAlmostEqual(key.sampling_weight.sum(), strata.eligible_cases.sum())
        self.assertTrue(strata.loc[strata.eligible_cases.gt(0), "sample_cases"].gt(0).all())
        counts = key.groupby(prepare.STRATA).size()
        pd.testing.assert_series_equal(counts, strata.set_index(prepare.STRATA).sample_cases, check_names=False)
        self.assertTrue(strata.sample_cases.le(strata.eligible_cases).all())

    def test_sample_size_duplicate_missing_and_no_available_cases_are_rejected(self):
        for size in [0, 1, 1523]:
            with self.subTest(size=size), self.assertRaises(ValueError):
                prepare.select_sample(self.population, size=size)
        with self.assertRaises(ValueError):
            prepare.select_sample(pd.concat([self.population, self.population.iloc[:1]]))
        missing = self.population.copy()
        missing.loc[0, "purchase_month"] = pd.NA
        with self.assertRaises(ValueError):
            prepare.select_sample(missing)
        all_reviewed = self.population.copy()
        all_reviewed["previously_human_reviewed"] = True
        with self.assertRaises(ValueError):
            prepare.select_sample(all_reviewed)

    def test_flags_are_stratification_only_and_match_saved_anchor(self):
        manifest = review.read_json(prepare.ablation.DEFAULT_OUTPUT/"manifest.json")
        for profile in prepare.ablation.PROFILES:
            flags = prepare.ablation.business.usage.read_snapshot_table(prepare.ablation.DEFAULT_OUTPUT, f"{profile}_validation_flags.csv", manifest)
            pd.testing.assert_series_equal(self.population[f"{profile}_flag"], flags[prepare.protocol()["anchor_candidate"]], check_names=False)
        counts = self.population[[f"{name}_flag" for name in prepare.ablation.PROFILES]].sum(axis=1)
        self.assertTrue(self.population.loc[counts.eq(0), "agreement_group"].eq("none_flag").all())
        self.assertTrue(self.population.loc[counts.eq(3), "agreement_group"].eq("all_flag").all())
        self.assertIn("not_selected_best_model", prepare.protocol()["anchor_status"])

    def test_exact_observed_facts_no_fake_timestamps_or_money(self):
        key = self.frames["analyst_only/selection_key.csv"]
        cases = self.frames["reviewer/cases.csv"]
        for source, columns in [("context", review.CONTEXT_COLUMNS), ("features", review.FEATURE_COLUMNS)]:
            expected = self.evidence[source].set_index("order_id").loc[key.order_id, columns].reset_index(drop=True)
            pd.testing.assert_frame_equal(cases[columns], expected)
        self.assertTrue(cases[review.REVIEW_COLUMNS].isna().all().all())
        self.assertEqual(cases.review_id.tolist(), key.review_id.tolist())

    def test_child_evidence_exact_lineage_and_no_cartesian_join(self):
        key = self.frames["analyst_only/selection_key.csv"]
        lineage = self.frames["analyst_only/source_lineage.csv"]
        cases = self.frames["reviewer/cases.csv"].set_index("review_id")
        for source, filename, columns, count_column in [
            ("payments", "payment_records.csv", review.PAYMENT_COLUMNS, "payment_record_count"),
            ("items", "item_records.csv", review.ITEM_COLUMNS, "item_count")]:
            frame = self.frames[f"reviewer/{filename}"]
            trace = lineage.loc[lineage.reviewer_table.eq(filename)]
            actual_source = self.evidence[source].set_index("clean_source_record").loc[trace.clean_source_record]
            pd.testing.assert_frame_equal(frame[columns].reset_index(drop=True), actual_source[columns].reset_index(drop=True))
            self.assertEqual(trace.order_id.tolist(), actual_source.order_id.tolist())
            self.assertEqual(trace.reviewer_record.tolist(), list(range(1, len(frame)+1)))
            self.assertEqual(frame.review_id.tolist(), trace.review_id.tolist())
            pd.testing.assert_series_equal(frame.groupby("review_id").size().sort_index(), cases[count_column].sort_index(), check_names=False, check_dtype=False)
            self.assertEqual(len(frame), int(self.evidence[source].order_id.isin(key.order_id).sum()))
        sums = self.frames["reviewer/payment_records.csv"].groupby("review_id").payment_value.sum()
        np.testing.assert_allclose(sums.sort_index(), cases.payment_value_sum.sort_index())

    def test_blind_allowlists_and_no_analyst_reads(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = self.clone(temporary)
            shutil.rmtree(batch/"analyst_only")
            frames, _ = review.load_evidence(batch)
            for name, columns in review.SCHEMAS.items():
                self.assertEqual(frames[name].columns.tolist(), columns)
                self.assertFalse({"order_id", "case_id", "sampling_weight", "agreement_group", "split", "score"} & set(frames[name].columns))
            output = []
            review.show_case(frames, "BR0001", output.append)
            text = "\n".join(output)
            self.assertNotIn("sampling_weight", text)
            self.assertNotIn("ensemble_rank_mean", text)
            self.assertNotIn("agreement_group", text)
            with self.assertRaisesRegex(ValueError, "Unknown review"):
                review.show_case(frames, "BR0000", output.append)

    def test_all_immutable_files_hash_and_csv_roundtrip(self):
        manifest = review.read_json(self.batch/"analyst_only/manifest.json")
        for name, value in manifest["output_hashes"].items():
            self.assertEqual(review.digest(self.batch/name), value)
        for name, expected in self.frames.items():
            actual = prepare.ablation.business.usage.read_snapshot_table(self.batch, name, manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
        self.assertFalse((self.batch/"sessions").exists())

    def test_frozen_pilot_cannot_be_overwritten(self):
        before = review.digest(self.batch/"analyst_only/manifest.json")
        with self.assertRaises(FileExistsError):
            prepare.write_batch(self.frames, self.provenance, self.batch)
        self.assertEqual(before, review.digest(self.batch/"analyst_only/manifest.json"))

    def test_changed_evidence_and_duplicate_json_keys_are_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch = self.clone(temporary)
            path = batch/"reviewer/cases.csv"
            cases = review.read_strings(path)
            cases.iloc[::-1].to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Blind evidence changed"):
                review.load_evidence(batch)
        with self.assertRaisesRegex(ValueError, "Duplicate JSON"):
            review.unique_keys([("status", "draft"), ("status", "draft")])

    def test_draft_blocks_entry_without_creating_session(self):
        criteria = self.batch/"reviewer/criteria_draft.json"
        with self.assertRaisesRegex(ValueError, "approved criteria"):
            review.open_session(self.batch, "reviewer_1", criteria)
        self.assertFalse((self.batch/"sessions").exists())
        draft = review.read_json(criteria)
        self.assertEqual(draft["status"], "draft_pending_advisor")
        for field in review.CRITERIA_FIELDS - {"status", "version"}:
            self.assertEqual(draft[field], "")
        incomplete = {**draft, "status": "approved"}
        with self.assertRaisesRegex(ValueError, "must specify"):
            review.validate_criteria(incomplete)

    def test_progress_validation_blank_ids_partial_and_unknown_codes(self):
        frames, _ = review.load_evidence(self.batch)
        progress = frames["cases.csv"][["review_id", *review.REVIEW_COLUMNS]].copy()
        review.validate_progress(progress, frames["cases.csv"].review_id)
        with self.assertRaisesRegex(ValueError, "IDs/schema"):
            review.validate_progress(progress.iloc[::-1], frames["cases.csv"].review_id)
        # An invalid administrative note tests partial-row rejection, not an invented judgment.
        progress.loc[0, "reviewer_notes"] = "Incomplete row must not be accepted"
        with self.assertRaisesRegex(ValueError, "Partial review"):
            review.validate_progress(progress, frames["cases.csv"].review_id)

    def test_invalid_reviewer_path_rejected(self):
        for value in ["", "../reviewer", "/tmp/reviewer", "x"*65]:
            with self.subTest(value=value), self.assertRaises(ValueError):
                review.session_path(self.batch, value)

    def test_blank_session_quit_resume_and_separate_reviewers(self):
        # Storage behavior is isolated by mocking only the approval gate. The real
        # pending draft stays pending; no approval declaration or judgment is invented.
        with tempfile.TemporaryDirectory() as temporary, patch.object(review, "validate_criteria", side_effect=lambda value: value):
            batch = self.clone(temporary)
            criteria = batch/"reviewer/criteria_draft.json"
            output = []
            review.run_review(batch, "reviewer_1", criteria, input_fn=lambda _: "q", output=output.append)
            first = review.open_session(batch, "reviewer_1", criteria)
            second = review.open_session(batch, "reviewer_1", criteria)
            pd.testing.assert_frame_equal(first[1], second[1])
            self.assertTrue(first[1][review.REVIEW_COLUMNS].eq("").all().all())
            other = review.open_session(batch, "reviewer_2", criteria)
            self.assertNotEqual(first[2], other[2])
            self.assertEqual(review.read_json(first[2]/"approved_criteria.json")["status"], "draft_pending_advisor")
            self.assertNotIn("agreement_group", "\n".join(output))

    def test_atomic_blank_progress_save_lock_and_stale_writer(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(review, "validate_criteria", side_effect=lambda value: value):
            batch = self.clone(temporary)
            frames, progress, directory, before, session_hash, _ = review.open_session(batch, "reviewer_1", batch/"reviewer/criteria_draft.json")
            after = review.save_progress(batch, directory, progress, before, session_hash)
            self.assertEqual(after, before)
            self.assertFalse((directory/".write.lock").exists())
            with self.assertRaisesRegex(ValueError, "Concurrent progress"):
                review.save_progress(batch, directory, progress, "stale_revision", session_hash)
            self.assertEqual(review.digest(directory/"review_progress.csv"), before)
            (directory/".write.lock").touch()
            with self.assertRaisesRegex(ValueError, "write lock"):
                review.save_progress(batch, directory, progress, before, session_hash)
            self.assertTrue((directory/".write.lock").exists())

    def test_changed_session_binding_cannot_resume_or_save(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(review, "validate_criteria", side_effect=lambda value: value):
            batch = self.clone(temporary)
            criteria = batch/"reviewer/criteria_draft.json"
            _, progress, directory, before, session_hash, _ = review.open_session(batch, "reviewer_1", criteria)
            metadata = review.read_json(directory/"session.json")
            metadata["reviewer_id"] = "reviewer_2"
            review.write_json(directory/"session.json", metadata)
            with self.assertRaisesRegex(ValueError, "binding changed"):
                review.open_session(batch, "reviewer_1", criteria)
            with self.assertRaisesRegex(ValueError, "changed during review"):
                review.save_progress(batch, directory, progress, before, session_hash)
            self.assertEqual(review.digest(directory/"review_progress.csv"), before)
            self.assertFalse((directory/".write.lock").exists())

    def test_atomic_publication_discards_failed_staging(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"pilot"
            with patch.object(prepare, "verify_sources", side_effect=[None, ValueError("source changed")]):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    prepare.write_batch(self.frames, self.provenance, path)
            self.assertFalse(path.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_source_and_code_binding_changes_are_rejected(self):
        altered = deepcopy(self.provenance)
        altered["study_manifest_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            prepare.verify_sources(altered)
        with patch.object(prepare, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "implementation or protocol changed"):
                prepare.verify_sources(self.provenance)


if __name__ == "__main__":
    unittest.main()
