"""Frozen real Olist observations; corruption below is test-only fault injection."""

from copy import deepcopy
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.metrics import precision_recall_curve

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import compare_delivery_discrimination as comparison


class DeliveryDiscriminationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs, cls.source = comparison.load_inputs()
        cls.frames, cls.summary = comparison.evaluate(cls.inputs)

    def test_all_methods_see_identical_real_outcomes(self):
        s = self.summary
        self.assertEqual((s["compared_orders"], s["late"], s["not_late"]), (1307, 138, 1169))
        self.assertEqual((s["methods"], s["fixed_configurations"], s["families"]), (22, 121, 8))
        self.assertEqual((s["source_orders"], s["excluded_orders"]), (1739, 432))
        self.assertEqual(s["exclusion_counts"], {"context_signal_assessment_incomplete": 314,
                                               "outside_legacy_comparison_scope": 118})
        for key in ["models_loaded", "models_retrained", "test_scored", "thresholds_optimized", "model_activated",
                    "confirmed_bottleneck_accuracy_computed", "near_real_time_validated"]:
            self.assertFalse(s[key])
        for name in ["common_outcomes.csv", "common_scores.csv", "common_flags.csv"]:
            self.assertEqual(self.frames[name].order_id.tolist(), sorted(self.inputs["cases"].order_id))
        self.assertTrue(self.frames["method_metrics.csv"].orders.eq(1307).all())

    def test_all_auc_values_match_pairwise_definition_including_ties(self):
        y = self.frames["common_outcomes.csv"][comparison.TARGET].to_numpy(bool)
        scores = self.frames["common_scores.csv"]
        for row in self.frames["method_metrics.csv"].itertuples(index=False):
            values = scores[row.score_column].to_numpy(float)
            delta = values[y, None]-values[~y][None, :]
            expected = ((delta > 0).sum()+.5*(delta == 0).sum())/delta.size
            self.assertAlmostEqual(row.roc_auc, expected, places=14, msg=row.score_column)

    def test_all_average_precision_values_use_recall_weighting(self):
        y = self.frames["common_outcomes.csv"][comparison.TARGET].to_numpy(bool)
        for row in self.frames["method_metrics.csv"].itertuples(index=False):
            precision, recall, _ = precision_recall_curve(y, self.frames["common_scores.csv"][row.score_column])
            self.assertAlmostEqual(row.average_precision, -np.sum(np.diff(recall)*precision[:-1]), places=14)

    def test_fixed_thresholds_and_flags_are_unchanged(self):
        definitions = self.frames["candidate_definitions.csv"].set_index("candidate")
        for row in self.inputs["candidates"].itertuples(index=False):
            self.assertEqual(definitions.loc[row.candidate, "threshold"], row.threshold)
            self.assertEqual(definitions.loc[row.candidate, "score_column"], row.score_column)
        flags = self.frames["common_flags.csv"].set_index("order_id")
        pd.testing.assert_frame_equal(flags[self.inputs["flags"].columns.drop("order_id")],
                                      self.inputs["flags"].set_index("order_id").sort_index())
        for row in self.frames["fixed_threshold_metrics.csv"].itertuples(index=False):
            self.assertEqual(row.tp+row.fn, 138)
            self.assertEqual(row.fp+row.tn, 1169)
            self.assertEqual(row.alert_count, row.tp+row.fp)
            self.assertAlmostEqual(row.f1, 2*row.tp/(2*row.tp+row.fp+row.fn))

    def test_product_auc_is_recomputed_on_common_cases_not_copied(self):
        metrics = self.frames["method_metrics.csv"].set_index("score_column")
        self.assertNotAlmostEqual(metrics.loc[comparison.PRODUCT_SCORE, "roc_auc"], 0.8507137192704204, places=6)
        original = self.inputs["orders"].set_index("order_id")[comparison.PRODUCT_SCORE]
        scores = self.frames["common_scores.csv"].set_index("order_id")
        np.testing.assert_array_equal(scores[comparison.PRODUCT_SCORE].to_numpy(), original.loc[scores.index].to_numpy())

    def test_real_data_leaders_and_operating_tradeoff(self):
        self.assertEqual(self.summary["best_roc_auc_methods"], ["upper_q950"])
        self.assertEqual(self.summary["best_average_precision_methods"], ["upper_q950"])
        self.assertAlmostEqual(self.summary["best_roc_auc"], 0.919921027510197, places=14)
        self.assertAlmostEqual(self.summary["best_average_precision"], 0.7419761892529877, places=14)
        self.assertEqual(self.summary["best_fixed_f1_candidates"], [comparison.PRODUCT_CANDIDATE])
        metrics = self.frames["fixed_threshold_metrics.csv"].set_index("candidate")
        self.assertEqual(metrics.loc["upper_q950", ["tp", "fp", "fn", "tn"]].tolist(), [121, 120, 17, 1049])
        self.assertEqual(metrics.loc[comparison.PRODUCT_CANDIDATE, ["tp", "fp", "fn", "tn"]].tolist(), [103, 63, 35, 1106])

    def test_future_observation_and_test_split_are_rejected(self):
        bad = deepcopy(self.inputs)
        index = bad["cases"].index[0]
        bad["cases"].loc[index, "visible_before_exclusive"] = bad["cases"].loc[index, "order_delivered_customer_date"]
        with self.assertRaisesRegex(ValueError, "observation cutoffs"):
            comparison.evaluate(bad)
        bad = deepcopy(self.inputs)
        bad["cases"].loc[index, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "Only frozen validation"):
            comparison.evaluate(bad)

    def test_one_class_svm_orientation_is_not_flipped(self):
        old = self.inputs["scores"].set_index("order_id")
        actual = self.frames["common_scores.csv"].set_index("order_id")
        for name in [n for n in actual if n.startswith("ocsvm_")]:
            self.assertTrue(actual[name].lt(0).all())
            pd.testing.assert_series_equal(actual[name], old.loc[actual.index, name])

    def test_early_warning_remains_a_warning_not_a_new_negative_label(self):
        outcomes = self.frames["common_outcomes.csv"]
        flags = self.frames["common_flags.csv"][comparison.PRODUCT_CANDIDATE]
        self.assertGreater(int((outcomes.performance_state.eq("early") & flags).sum()), 0)
        self.assertFalse(outcomes.loc[outcomes.performance_state.eq("early"), comparison.TARGET].any())
        self.assertFalse(self.summary["independent_anomaly_labels_used"])

    def test_input_order_does_not_change_any_results_or_mutate_inputs(self):
        before = deepcopy(self.inputs)
        reordered = {k: v.iloc[::-1].reset_index(drop=True) if isinstance(v, (pd.Series, pd.DataFrame)) else v
                     for k, v in self.inputs.items()}
        frames, summary = comparison.evaluate(reordered)
        self.assertEqual(summary, self.summary)
        for key in frames:
            pd.testing.assert_frame_equal(frames[key], self.frames[key])
        for key, value in before.items():
            if isinstance(value, pd.DataFrame):
                pd.testing.assert_frame_equal(self.inputs[key], value)

    def test_missing_common_score_cannot_be_dropped_or_imputed(self):
        bad = deepcopy(self.inputs)
        case_id = bad["cases"].order_id.iloc[0]
        bad["orders"].loc[bad["orders"].order_id.eq(case_id), comparison.PRODUCT_SCORE] = np.nan
        with self.assertRaisesRegex(ValueError, "complete scores"):
            comparison.evaluate(bad)

    def test_nonfinite_detector_score_is_rejected(self):
        bad = deepcopy(self.inputs)
        bad["scores"].loc[bad["scores"].index[0], "if_raw_256"] = np.inf
        with self.assertRaisesRegex(ValueError, "nonfinite"):
            comparison.evaluate(bad)

    def test_unknown_target_cannot_be_made_negative(self):
        bad = deepcopy(self.inputs)
        bad["orders"][comparison.TARGET] = bad["orders"][comparison.TARGET].fillna(False)
        with self.assertRaisesRegex(ValueError, "Unknown target"):
            comparison.evaluate(bad)

    def test_disagreeing_source_labels_are_rejected(self):
        bad = deepcopy(self.inputs)
        index = bad["cases"].index[0]
        bad["cases"].loc[index, comparison.TARGET] = not bad["cases"].loc[index, comparison.TARGET]
        with self.assertRaisesRegex(ValueError, "targets disagree"):
            comparison.evaluate(bad)
        bad["cases"].loc[index, comparison.TARGET] = pd.NA
        with self.assertRaisesRegex(ValueError, "targets disagree"):
            comparison.evaluate(bad)

    def test_changed_stage_duration_is_rejected(self):
        bad = deepcopy(self.inputs)
        index = bad["stages"].index[bad["stages"].order_id.eq(bad["cases"].order_id.iloc[0])][0]
        bad["stages"].loc[index, "duration_days"] += 1
        with self.assertRaisesRegex(ValueError, "Stage durations"):
            comparison.evaluate(bad)

    def test_duplicate_or_missing_method_case_is_rejected(self):
        bad = deepcopy(self.inputs)
        bad["scores"] = pd.concat([bad["scores"], bad["scores"].iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            comparison.evaluate(bad)
        bad = deepcopy(self.inputs)
        bad["scores"] = bad["scores"].iloc[1:]
        with self.assertRaisesRegex(ValueError, "cohort IDs differ"):
            comparison.evaluate(bad)

    def test_training_overlap_is_rejected(self):
        bad = deepcopy(self.inputs)
        bad["fit_ids"].iloc[0] = bad["cases"].order_id.iloc[0]
        with self.assertRaisesRegex(ValueError, "overlap violation"):
            comparison.evaluate(bad)

    def test_threshold_change_is_rejected_by_original_flags(self):
        bad = deepcopy(self.inputs)
        bad["candidates"].loc[bad["candidates"].index[0], "threshold"] = 1e100
        with self.assertRaisesRegex(ValueError, "threshold flags differ"):
            comparison.evaluate(bad)

    def test_frontier_keeps_all_ties_and_no_dominated_rows(self):
        metrics = self.frames["method_metrics.csv"]
        for row in metrics.itertuples(index=False):
            dominates = metrics.roc_auc.ge(row.roc_auc) & metrics.average_precision.ge(row.average_precision)
            strict = metrics.roc_auc.gt(row.roc_auc) | metrics.average_precision.gt(row.average_precision)
            self.assertEqual(row.auc_ap_frontier, not (dominates & strict).any())
        tied = pd.concat([metrics, metrics.iloc[:1]], ignore_index=True)
        result = comparison.nondominated(tied, ["roc_auc", "average_precision"])
        self.assertEqual(result[0], result[-1])

    def test_publication_roundtrip_hashes_no_training_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/"comparison"
            with patch.object(comparison, "load_inputs", return_value=(self.inputs, self.source)), \
                    patch.object(comparison.product, "build_tables", side_effect=AssertionError("No rebuilding scores")), \
                    patch.object(comparison.detectors.previous.batch.training.engine, "fit_bank", side_effect=AssertionError("No fitting")):
                summary = comparison.run(output)
            self.assertEqual(summary, self.summary)
            manifest = comparison.review.read_json(output/"manifest.json")
            comparison.shared.verify_files(output, manifest["output_hashes"])
            saved = pd.read_csv(output/"method_metrics.csv", float_precision="round_trip")
            np.testing.assert_array_equal(saved[["roc_auc", "average_precision"]],
                                          self.frames["method_metrics.csv"][["roc_auc", "average_precision"]])
            with self.assertRaises(FileExistsError):
                comparison.run(output)
            report = (output/"bao_cao_so_sanh.md").read_text()
            self.assertIn("1.411", report)
            self.assertIn("chưa chứng minh", report)


if __name__ == "__main__":
    unittest.main()
