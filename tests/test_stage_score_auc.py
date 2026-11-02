"""AUC checks on existing Olist observations; no synthetic research labels."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from sklearn.metrics import auc

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import evaluate_stage_score_auc as evaluation


class StageScoreAUCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        p = evaluation.product
        manifest = p.load_snapshot()
        cls.orders = p.shared.read_table(p.DEFAULT_OUTPUT, "order_scores.csv", manifest)
        cls.frames, cls.summary = evaluation.evaluate(cls.orders)

    def test_scope_and_missingness_are_explicit(self):
        s = self.summary
        self.assertEqual((s["source_orders"], s["evaluated_orders"], s["late"], s["not_late"]), (1739, 1411, 150, 1261))
        self.assertEqual(s["exclusion_counts"], {"missing_target_and_score": 272, "missing_score": 56})
        self.assertEqual(s["excluded_known_late"], 11)
        self.assertFalse(self.orders.review_id.isin(["BR0043", "BR0119"]).any())
        self.assertFalse(s["held_out_test_scored"])
        self.assertFalse(s["bottleneck_auc_computed"])
        self.assertFalse(s["model_selected"])

    def test_auc_matches_real_pairwise_ranking_with_half_credit_for_ties(self):
        frame = self.frames["evaluation_orders.csv"].query("evaluation_status == 'included'")
        positive = frame.loc[frame.calendar_late_signal, evaluation.SCORE].to_numpy()
        negative = frame.loc[~frame.calendar_late_signal, evaluation.SCORE].to_numpy()
        difference = positive[:, None]-negative[None, :]
        pairwise = ((difference > 0).sum()+.5*(difference == 0).sum())/difference.size
        self.assertAlmostEqual(self.summary["roc_auc"], pairwise, places=14)
        self.assertAlmostEqual(pairwise, 0.8507137192704204, places=14)
        curve = self.frames["roc_curve.csv"]
        self.assertAlmostEqual(auc(curve.false_positive_rate, curve.true_positive_rate), pairwise)

    def test_average_precision_is_not_trapezoid_pr_area(self):
        curve = self.frames["precision_recall_curve.csv"]
        ap = -np.sum(np.diff(curve.recall)*curve.precision.to_numpy()[:-1])
        self.assertAlmostEqual(ap, self.summary["average_precision"])
        self.assertAlmostEqual(ap, 0.4905015264645788)
        self.assertNotAlmostEqual(ap, auc(curve.recall, curve.precision), places=4)

    def test_old_threshold_is_not_optimized(self):
        counts = self.summary["fixed_existing_warning"]
        self.assertEqual([counts[name] for name in ["true_positive", "false_positive", "false_negative", "true_negative"]],
                         [114, 82, 36, 1179])
        self.assertEqual(self.summary["zero_scores"], 1215)
        self.assertFalse(self.summary["thresholds_optimized"])

    def test_keeps_early_delivery_warning_and_does_not_mutate_inputs(self):
        before = self.orders.copy(deep=True)
        frames, _ = evaluation.evaluate(self.orders)
        pd.testing.assert_frame_equal(before, self.orders)
        row = frames["evaluation_orders.csv"].set_index("review_id").loc["BR0079"]
        self.assertFalse(row.calendar_late_signal)
        self.assertGreater(row.max_stage_delay_score, 0)
        self.assertEqual(row.evaluation_status, "included")

    def test_order_does_not_affect_metrics(self):
        _, summary = evaluation.evaluate(self.orders.iloc[::-1])
        self.assertEqual(summary, self.summary)

    def test_one_class_subset_does_not_produce_fake_auc(self):
        with self.assertRaisesRegex(ValueError, "both late and non-late"):
            evaluation.evaluate(self.orders.loc[self.orders.performance_state.eq("late")])

    def test_duplicates_are_rejected_not_double_counted(self):
        with self.assertRaises(ValueError):
            evaluation.evaluate(pd.concat([self.orders, self.orders.iloc[:1]], ignore_index=True))

    def test_missing_target_cannot_be_reinterpreted_as_negative(self):
        corrupted = self.orders.copy()
        corrupted[evaluation.TARGET] = corrupted[evaluation.TARGET].fillna(False)
        with self.assertRaisesRegex(ValueError, "unknown is not negative"):
            evaluation.evaluate(corrupted)

    def test_output_never_overwrites_existing_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                evaluation.run(Path(tmp))

    def test_real_snapshot_roundtrip_and_provenance(self):
        p = evaluation.product
        with tempfile.TemporaryDirectory() as tmp:
            destination = Path(tmp)/"auc"
            with patch.object(p, "build_tables", side_effect=AssertionError("No score rebuilding")):
                summary = evaluation.run(destination)
            self.assertEqual(summary, self.summary)
            manifest = p.review.read_json(destination/"manifest.json")
            p.shared.verify_files(destination, manifest["output_hashes"])
            self.assertEqual(manifest["source_manifest_sha256"], p.review.digest(p.DEFAULT_OUTPUT/"manifest.json"))
            ledger = pd.read_csv(destination/"evaluation_orders.csv")
            self.assertEqual(len(ledger), 1739)
            self.assertEqual(int(ledger.max_stage_delay_score.isna().sum()), 328)


if __name__ == "__main__":
    unittest.main()
