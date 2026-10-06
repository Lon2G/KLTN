"""Frozen-candidate comparisons use real saved development flags, not invented labels."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import compare_process_candidates as comparison


BATCH = ROOT / "data/experiments/olist_validation_batches_comparison_input_v1"


class CandidateComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.candidates, cls.cases, cls.flags, cls.calibration, cls.provenance = comparison.load_inputs(BATCH)
        cls.frames, cls.summary = comparison.build_outputs(cls.candidates, cls.cases, cls.flags, cls.calibration)

    def compare_subset(self, cases, flags):
        return comparison.build_outputs(self.candidates, cases, flags, self.calibration)

    def test_actual_populations_and_no_accuracy_winner_or_test(self):
        self.assertEqual(self.summary["candidate_configurations"], 120)
        self.assertEqual(self.summary["score_columns"], 21)
        self.assertEqual(self.summary["families"], 7)
        self.assertEqual(self.summary["validation_cases"], 1853)
        self.assertEqual(self.summary["scored_cases"], 1523)
        self.assertEqual(self.summary["unscored_cases"], 330)
        self.assertEqual(self.summary["calibration_cases"], 1412)
        self.assertEqual(self.summary["pairwise_comparisons"], 7140)
        self.assertIsNone(self.summary["selected_candidate"])
        for key in ["accuracy_computed", "accuracy_ranking_available", "anomaly_probabilities_computed", "human_labels_created",
                    "test_scored", "training_performed", "thresholds_updated", "model_deserialized", "independent_new_data_evaluation"]:
            self.assertFalse(self.summary[key])

    def test_every_candidate_count_matches_frozen_flags_and_calibration(self):
        table = self.frames["candidate_comparison.csv"].set_index("candidate")
        self.assertEqual(table.index.tolist(), sorted(self.candidates.candidate))
        for name in self.candidates.candidate:
            self.assertEqual(table.at[name, "flagged_cases"], int(self.flags[name].sum()))
            self.assertEqual(table.at[name, "calibration_flags"], int(self.calibration[name].sum()))
            self.assertAlmostEqual(table.at[name, "flag_fraction_of_scored"], self.flags[name].mean())
            self.assertAlmostEqual(table.at[name, "validation_minus_calibration_pp"],
                                   100*(self.flags[name].mean()-self.calibration[name].mean()))
        expected = pd.read_csv(BATCH / "candidate_rates.csv").query("window == 'all_batch'").set_index("candidate")
        pd.testing.assert_series_equal(table.flagged_cases, expected.flagged_cases.sort_index(), check_names=False)

    def test_monthly_denominators_and_overall_rate_reconcile(self):
        monthly = self.frames["monthly_candidate_rates.csv"]
        coverage = self.frames["monthly_coverage.csv"]
        self.assertEqual(coverage.all_cases.tolist(), [660, 598, 595])
        self.assertEqual(coverage.scored_cases.tolist(), [642, 536, 345])
        self.assertEqual(int(coverage.unscored_cases.sum()), 330)
        indexed = self.frames["candidate_comparison.csv"].set_index("candidate")
        for name, group in monthly.groupby("candidate"):
            self.assertEqual(group.flagged_cases.sum(), indexed.at[name, "flagged_cases"])
            self.assertAlmostEqual(np.average(group.flag_fraction_of_scored, weights=group.scored_cases),
                                   indexed.at[name, "flag_fraction_of_scored"])
            np.testing.assert_allclose(group.flag_fraction_of_scored, group.flagged_cases/group.scored_cases)
            np.testing.assert_allclose(group.flag_fraction_of_all_cases, group.flagged_cases/group.all_cases)

    def test_variability_formulas_are_percentage_points_with_scored_case_weights(self):
        table = self.frames["candidate_comparison.csv"].set_index("candidate")
        for name, group in self.frames["monthly_candidate_rates.csv"].groupby("candidate"):
            values = group.flag_fraction_of_scored.to_numpy()
            weights = group.scored_cases.to_numpy()
            mean = np.sum(values*weights)/weights.sum()
            deviation = np.sqrt(np.sum(weights*(values-mean)**2)/weights.sum())
            self.assertAlmostEqual(table.at[name, "monthly_rate_weighted_sd_pp"], 100*deviation)
            self.assertAlmostEqual(table.at[name, "monthly_rate_range_pp"], 100*(values.max()-values.min()))

    def test_pairwise_integer_counts_and_jaccard_match_actual_sets(self):
        pairs = self.frames["pairwise_overlap.csv"]
        self.assertEqual(len(pairs), 120*119//2)
        self.assertFalse(pairs.duplicated(["left_candidate", "right_candidate"]).any())
        self.assertTrue(pairs.left_candidate.lt(pairs.right_candidate).all())
        self.assertTrue((pairs.both_flag+pairs.left_only+pairs.right_only+pairs.neither_flag).eq(1523).all())
        self.assertGreater(pairs.both_flag.max(), 255)
        for row in pairs.iloc[::43].itertuples(index=False):
            left = set(self.flags.loc[self.flags[row.left_candidate], "order_id"])
            right = set(self.flags.loc[self.flags[row.right_candidate], "order_id"])
            self.assertEqual(row.both_flag, len(left & right))
            self.assertEqual(row.union_flags, len(left | right))
            self.assertEqual(row.left_only, len(left-right))
            self.assertEqual(row.right_only, len(right-left))
            self.assertAlmostEqual(row.jaccard_overlap, len(left & right)/len(left | right))

    def test_equivalence_groups_are_actual_flag_vectors_not_model_equivalence(self):
        groups = self.frames["observed_equivalence_groups.csv"]
        membership = self.frames["candidate_comparison.csv"].set_index("candidate").observed_equivalence_group
        self.assertEqual(len(groups), 118)
        self.assertEqual(int(groups.candidate_count.sum()), 120)
        self.assertEqual(int(groups.candidate_count.gt(1).sum()), 2)
        for row in groups.itertuples(index=False):
            names = json.loads(row.members_json)
            self.assertEqual(len(names), row.candidate_count)
            for name in names:
                pd.testing.assert_series_equal(self.flags[name], self.flags[names[0]], check_names=False)
                self.assertEqual(membership[name], row.group_id)
            self.assertEqual(row.flagged_cases, int(self.flags[names[0]].sum()))

    def test_threshold_sensitivity_is_nested_for_the_same_score_only(self):
        sensitivity = self.frames["threshold_sensitivity.csv"]
        self.assertEqual(len(sensitivity), 99)
        definitions = self.candidates.set_index("candidate")
        for row in sensitivity.itertuples(index=False):
            self.assertEqual(definitions.at[row.looser_candidate, "score_column"], row.score_column)
            self.assertEqual(definitions.at[row.stricter_candidate, "score_column"], row.score_column)
            self.assertGreaterEqual(row.stricter_threshold, row.looser_threshold)
            for prefix, frame in [("validation", self.flags), ("calibration", self.calibration)]:
                loose = set(frame.loc[frame[row.looser_candidate], "order_id"])
                strict = set(frame.loc[frame[row.stricter_candidate], "order_id"])
                self.assertTrue(strict.issubset(loose))
                self.assertEqual(getattr(row, prefix+"_removed_flags"), len(loose-strict))
                self.assertAlmostEqual(getattr(row, prefix+"_decrease_pp"), 100*len(loose-strict)/len(frame))

    def test_invalid_monotonic_flags_are_rejected_without_retuning(self):
        pair = self.frames["threshold_sensitivity.csv"].query("validation_removed_flags > 0").iloc[0]
        swapped = self.flags.copy()
        # Deliberately misassociate two real saved result columns; no experimental output is published.
        swapped[pair.looser_candidate] = self.flags[pair.stricter_candidate]
        swapped[pair.stricter_candidate] = self.flags[pair.looser_candidate]
        with self.assertRaisesRegex(ValueError, "Higher cutoff"):
            comparison.threshold_sensitivity(self.candidates, swapped, self.calibration)

    def test_rule_overlap_uses_only_applicable_real_signals_not_truth_labels(self):
        table = self.frames["candidate_rule_overlap.csv"]
        self.assertEqual(len(table), 120*5)
        cases = self.cases.set_index("order_id").loc[self.flags.order_id]
        for row in table.iloc[::17].itertuples(index=False):
            known = cases[row.rule].notna().to_numpy()
            rule = cases[row.rule].fillna(False).to_numpy(dtype=bool)[known]
            candidate = self.flags[row.candidate].to_numpy(dtype=bool)[known]
            self.assertEqual(row.compared_cases, int(known.sum()))
            self.assertEqual(row.both_flag, int((rule & candidate).sum()))
            self.assertEqual(row.candidate_only+row.rule_only+row.both_flag+row.neither_flag, row.compared_cases)
        self.assertFalse(any(word in table.columns for word in ["accuracy", "precision", "recall", "f1"]))

    def test_zero_flags_on_real_subset_are_not_a_perfect_jaccard_or_winner(self):
        flags = self.flags.loc[~self.flags[self.candidates.candidate].any(axis=1)]
        self.assertGreater(len(flags), 0)
        cases = self.cases.loc[self.cases.order_id.isin(flags.order_id)]
        frames, summary = self.compare_subset(cases, flags)
        self.assertTrue(frames["pairwise_overlap.csv"].jaccard_overlap.isna().all())
        self.assertTrue(frames["pairwise_overlap.csv"].identical_observed_flags.eq(True).all())
        self.assertEqual(summary["distinct_observed_flag_sets"], 1)
        self.assertEqual(summary["candidates_without_flags"], 120)
        self.assertIsNone(summary["selected_candidate"])

    def test_no_scored_cases_remain_unavailable_not_equivalent_or_stable(self):
        cases = self.cases.loc[~self.cases.timing_input_eligible]
        frames, summary = self.compare_subset(cases, self.flags.iloc[:0])
        self.assertEqual(summary["scored_cases"], 0)
        self.assertIsNone(summary["distinct_observed_flag_sets"])
        self.assertTrue(frames["observed_equivalence_groups.csv"].empty)
        self.assertTrue(frames["candidate_comparison.csv"].flag_fraction_of_scored.isna().all())
        self.assertTrue(frames["candidate_comparison.csv"].monthly_rate_range_pp.isna().all())
        self.assertTrue(frames["pairwise_overlap.csv"].identical_observed_flags.isna().all())
        self.assertTrue(frames["threshold_sensitivity.csv"].validation_observed_plateau.isna().all())

    def test_one_scored_month_has_no_monthly_variability_claim(self):
        cases = self.cases.loc[self.cases.purchase_month.eq("2018-03")]
        flags = self.flags.loc[self.flags.order_id.isin(cases.order_id)]
        frames, _ = self.compare_subset(cases, flags)
        table = frames["candidate_comparison.csv"]
        self.assertTrue(table.monthly_rate_range_pp.isna().all())
        self.assertTrue(table.monthly_rate_weighted_sd_pp.isna().all())
        self.assertTrue(table.months_with_scored_cases.eq(1).all())

    def test_month_without_scored_cases_is_not_assigned_zero_flag_rate(self):
        cases = self.cases.loc[self.cases.purchase_month.eq("2018-03")
                               | (self.cases.purchase_month.eq("2018-05") & ~self.cases.timing_input_eligible)]
        flags = self.flags.loc[self.flags.order_id.isin(cases.order_id)]
        frames, _ = self.compare_subset(cases, flags)
        may = frames["monthly_candidate_rates.csv"].query("purchase_month == '2018-05'")
        self.assertTrue(may.flag_fraction_of_scored.isna().all())
        self.assertTrue(may.scored_cases.eq(0).all())
        self.assertTrue(frames["candidate_comparison.csv"].months_without_scored_cases.eq(1).all())

    def test_row_and_candidate_order_do_not_change_comparisons(self):
        candidates = self.candidates.iloc[::-1].reset_index(drop=True)
        flags = self.flags.iloc[::-1][["order_id", *candidates.candidate]]
        calibration = self.calibration.iloc[::-1][["order_id", *candidates.candidate]]
        frames, _ = comparison.build_outputs(candidates, self.cases.iloc[::-1], flags, calibration)
        for name, expected in self.frames.items():
            pd.testing.assert_frame_equal(frames[name], expected)

    def test_loader_never_deserializes_models_refits_or_parses_test_and_review_files(self):
        training = comparison.batch.training
        original_read = pd.read_csv
        paths = []
        def read(path, *args, **kwargs):
            paths.append(str(path))
            return original_read(path, *args, **kwargs)
        with patch.object(training.joblib, "load", side_effect=AssertionError("No model deserialization")), \
             patch.object(training.engine, "fit_bank", side_effect=AssertionError("No fitting")), \
             patch.object(training.engine, "learn_thresholds", side_effect=AssertionError("No recalibration")), \
             patch.object(pd, "read_csv", side_effect=read):
            *inputs, _ = comparison.load_inputs(BATCH)
            frames, summary = comparison.build_outputs(*inputs)
        self.assertEqual(summary, self.summary)
        self.assertFalse(any("test_timing" in path or "manual" in path or "review" in path for path in paths))
        pd.testing.assert_frame_equal(frames["candidate_comparison.csv"], self.frames["candidate_comparison.csv"])

    def test_scope_duplicates_overlap_and_wrong_month_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.compare_subset(pd.concat([self.cases, self.cases.iloc[:1]]), self.flags)
        with self.assertRaisesRegex(ValueError, "scope mismatch"):
            self.compare_subset(self.cases, self.flags.iloc[1:])
        with self.assertRaisesRegex(ValueError, "overlap"):
            comparison.validate_inputs(self.candidates, self.cases, self.flags, pd.concat([self.calibration, self.flags.iloc[:1]]))
        changed = self.cases.copy()
        changed.loc[0, "purchase_month"] = "wrong_month_marker"
        with self.assertRaisesRegex(ValueError, "metadata disagrees"):
            self.compare_subset(changed, self.flags)

    def test_strict_flag_reader_refuses_unavailable_scored_flags(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"flags.csv"
            frame = self.flags.iloc[:3].astype("string")
            frame.iloc[0, 1] = ""
            frame.to_csv(path, index=False)
            with self.assertRaisesRegex(ValueError, "Invalid or unavailable flag"):
                comparison.read_flags(path, self.candidates)

    def test_score_flag_disagreement_is_not_accepted_as_new_threshold_result(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/"scores.csv"
            scores = pd.read_csv(BATCH/"candidate_scores.csv", dtype={"order_id": "string"}, float_precision="round_trip")
            scores.to_csv(path, index=False)
            reversed_association = self.flags.copy()
            row = self.frames["threshold_sensitivity.csv"].query("validation_removed_flags > 0").iloc[0]
            reversed_association[row.looser_candidate] = self.flags[row.stricter_candidate]
            with self.assertRaises(AssertionError):
                comparison.verify_score_flags(path, reversed_association, self.candidates)

    def test_modified_document_or_numeric_output_fails_hash_verification(self):
        for name in ["README.md", "candidate_flags.csv"]:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)/"batch"
                shutil.copytree(BATCH, root)
                target = root/name
                target.write_bytes(target.read_bytes()+b"\n")
                with self.assertRaisesRegex(ValueError, "hash/path mismatch"):
                    comparison.load_inputs(root)

    def test_versioned_publication_hashes_and_stale_source_guard(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)/"comparison"
            comparison.write_outputs(self.frames, self.summary, self.provenance, root)
            manifest = json.loads((root/"manifest.json").read_text())
            for name, digest in manifest["output_hashes"].items():
                self.assertEqual(comparison.batch.audit.file_hash(root/name), digest)
            with self.assertRaises(FileExistsError):
                comparison.write_outputs(self.frames, self.summary, self.provenance, root)
            with self.assertRaisesRegex(ValueError, "input/code changed"):
                comparison.write_outputs(self.frames, self.summary, {**self.provenance, "code_sha256": "stale"}, Path(directory)/"stale")


if __name__ == "__main__":
    unittest.main()
