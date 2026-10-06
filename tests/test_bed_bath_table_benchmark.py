"""Benchmark checks use real source cases and actual recorded labels only."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import confusion_matrix, f1_score, precision_score, recall_score


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import benchmark_bed_bath_table as b
import evaluate_bed_bath_table_benchmark as evaluation


class BedBathTableBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrices, cls.context, cls.parents = b.previous.load_inputs()
        cls.subsets, cls.ledger = b.inner_split(cls.matrices, cls.context)
        cls.frames, cls.bank, cls.design = b.build_benchmark(cls.matrices, cls.context)

    def test_inner_split_reconciles_disjoint_real_cases_and_event_cutoff(self):
        ids = set(self.matrices["train"].order_id)
        self.assertEqual(set(self.ledger.order_id), ids)
        self.assertTrue(self.ledger.order_id.is_unique)
        self.assertEqual(self.design["fit_orders"] + self.design["calibration_orders"] + self.design["inner_deferred_orders"], len(ids))
        self.assertEqual(self.design["fit_orders"], 3190)
        self.assertEqual(self.design["calibration_orders"], 1412)
        fit = self.context.set_index("order_id").loc[self.subsets["fit"].order_id]
        self.assertTrue(fit[b.previous.bed.audit.TIMES].lt(pd.Timestamp(b.FIT_END)).all().all())
        cal = self.context.set_index("order_id").loc[self.subsets["calibration"].order_id]
        self.assertTrue(cal.order_purchase_timestamp.ge(pd.Timestamp(b.FIT_END)).all())
        self.assertTrue(cal[b.previous.bed.audit.TIMES].lt(pd.Timestamp("2018-03-01")).all().all())
        for a, c in [("fit", "calibration"), ("fit", "validation"), ("calibration", "validation")]:
            self.assertTrue(set(self.subsets[a].order_id).isdisjoint(self.subsets[c].order_id))

    def test_grid_is_complete_unique_and_declared(self):
        candidates = self.frames["candidates.csv"]
        self.assertEqual(len(candidates), 120)
        self.assertTrue(candidates.candidate.is_unique)
        self.assertEqual(candidates.groupby("family").size().to_dict(), {
            "ensemble": 24, "iqr": 6, "isolation_forest": 32, "lof": 24, "mad": 6,
            "one_class_svm": 24, "upper_quantile": 4})
        self.assertEqual(set(self.bank["models"]), set(b.MODEL_NAMES))
        self.assertEqual(len(self.bank["models"]), 10)
        self.assertFalse(self.design["test_scored"])
        self.assertIsNone(self.design["selected_candidate"])

    def test_fit_statistics_and_scalers_use_fit_rows_only(self):
        x = self.subsets["fit"][b.FEATURES].to_numpy()
        for transform, stats in self.bank["statistics"].items():
            v = x if transform == "raw" else np.log1p(x)
            np.testing.assert_allclose(stats["median"], np.median(v, axis=0))
            np.testing.assert_allclose(stats["mad"], np.median(np.abs(v - np.median(v, axis=0)), axis=0))
            np.testing.assert_allclose(stats["iqr"], np.quantile(v, .75, axis=0) - np.quantile(v, .25, axis=0))
        for name, model in self.bank["models"].items():
            if "scale" in model.named_steps:
                np.testing.assert_allclose(model.named_steps["scale"].center_, np.median(np.log1p(x), axis=0))
        self.assertEqual(self.bank["fit_ids"], self.subsets["fit"].order_id.tolist())

    def test_no_novelty_scoring_on_fit_and_no_test_input(self):
        with self.assertRaisesRegex(ValueError, "must not use fit"):
            b.base_scores(self.bank, self.subsets["fit"].iloc[:1])
        with self.assertRaisesRegex(ValueError, "train and validation only"):
            b.inner_split({**self.matrices, "test": self.matrices["validation"]}, self.context)
        for name in ["calibration_scores.csv", "validation_scores.csv"]:
            self.assertTrue(set(self.frames[name].order_id).isdisjoint(self.bank["fit_ids"]))

    def test_statistical_scores_match_direct_formulas(self):
        x = self.subsets["validation"][b.FEATURES].to_numpy()
        scores = self.frames["validation_scores.csv"]
        for transform, stats in self.bank["statistics"].items():
            v = x if transform == "raw" else np.log1p(x)
            iqr = np.maximum(0, np.max((v - stats["q75"]) / stats["iqr"], axis=1))
            mad = np.maximum(0, np.max(.6744897501960817 * (v - stats["median"]) / stats["mad"], axis=1))
            np.testing.assert_allclose(scores[f"iqr_{transform}"], iqr)
            np.testing.assert_allclose(scores[f"mad_{transform}"], mad)
        for q in b.UPPER_QUANTILES:
            thresholds = np.quantile(self.subsets["fit"][b.FEATURES], q, axis=0)
            expected = (x > thresholds).any(axis=1)
            self.assertTrue(self.frames["validation_flags.csv"][f"upper_q{round(q*1000):03d}"].eq(expected).all())

    def test_all_score_thresholds_use_unseen_calibration_only(self):
        candidates = self.frames["candidates.csv"]
        cal = self.frames["calibration_scores.csv"]
        for row in candidates.loc[candidates.threshold_origin.eq("unseen_calibration_score_quantile")].itertuples(index=False):
            self.assertAlmostEqual(row.threshold, np.quantile(cal[row.score_column], 1-row.tail_fraction))
        for score_name, group in candidates.dropna(subset="tail_fraction").groupby("score_column"):
            ordered = group.sort_values("tail_fraction")
            self.assertTrue(ordered.threshold.diff().dropna().le(0).all())
            flags = self.frames["validation_flags.csv"]
            for a, c in zip(ordered.candidate, ordered.candidate.iloc[1:]):
                self.assertFalse((flags[a] & ~flags[c]).any())

    def test_ensemble_ranks_use_calibration_reference_and_fixed_weights(self):
        scores = self.frames["validation_scores.csv"]
        independent = {}
        for name, reference in self.bank["rank_references"].items():
            independent[name] = np.array([(reference <= value).mean() for value in scores[name]])
        ml = np.column_stack([independent[name] for name in b.ENSEMBLE_ML])
        np.testing.assert_allclose(scores.ensemble_rank_mean, ml.mean(axis=1))
        np.testing.assert_allclose(scores.ensemble_rank_median, np.median(ml, axis=1))
        np.testing.assert_allclose(scores.hybrid_rank_mean_mad, .5*ml.mean(axis=1)+.5*independent["mad_log1p"])
        for name in b.ENSEMBLE_NAMES:
            self.assertTrue(scores[name].between(0, 1).all())

    def test_validation_changes_cannot_refit_or_recalibrate(self):
        original_candidates = self.bank["candidates"].copy(deep=True)
        original_stats = {name: {key: value.copy() for key, value in stats.items()}
                          for name, stats in self.bank["statistics"].items()}
        subset = self.subsets["validation"].iloc[::3]
        actual = b.add_ensembles(b.base_scores(self.bank, subset), self.bank["rank_references"])
        expected = self.frames["validation_scores.csv"].loc[lambda f: f.order_id.isin(subset.order_id)].reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, expected)
        pd.testing.assert_frame_equal(original_candidates, self.bank["candidates"])
        for name, stats in original_stats.items():
            for key, values in stats.items():
                np.testing.assert_array_equal(self.bank["statistics"][name][key], values)

    def test_ties_are_not_forced_into_an_alert_percentage(self):
        candidates = self.frames["candidates.csv"].iloc[:1].copy()
        column = candidates.score_column.iloc[0]
        actual_score = self.frames["validation_scores.csv"].iloc[:1]
        candidates["threshold"] = actual_score[column].iloc[0]
        flags = b.apply_thresholds(actual_score, candidates)
        self.assertFalse(flags[candidates.candidate.iloc[0]].iloc[0])

    def test_warning_scope_and_monthly_counts_reconcile(self):
        flags = self.frames["validation_flags.csv"]
        self.assertEqual(len(flags), 1523)
        monthly = self.frames["validation_monthly_flags.csv"].set_index("candidate")
        for row in self.frames["validation_diagnostics.csv"].itertuples(index=False):
            self.assertEqual(row.validation_flags, int(flags[row.candidate].sum()))
            self.assertEqual(int(monthly.loc[row.candidate].sum()), row.validation_flags)
        self.assertEqual(len(self.frames["validation_scope_ledger.csv"]), 1853)
        short = self.frames["validation_short_warnings.csv"]
        for column in b.FEATURES:
            self.assertTrue(short.loc[short[column].eq(0), f"{column}_short_warning"].all())
        for frame in self.frames.values():
            self.assertNotIn("reviewer_label", frame.columns)
            self.assertNotIn("anomaly_probability", frame.columns)
            self.assertNotIn("anomaly_label", frame.columns)

    def test_refit_is_reproducible_with_reversed_input_order(self):
        bank = b.fit_bank(self.subsets["fit"].iloc[::-1])
        actual = b.base_scores(bank, self.subsets["calibration"].iloc[::-1])
        expected = self.frames["calibration_scores.csv"].drop(columns=b.ENSEMBLE_NAMES)
        pd.testing.assert_frame_equal(actual, expected)

    def test_real_repeated_row_is_rejected(self):
        duplicate = pd.concat([self.subsets["fit"], self.subsets["fit"].iloc[:1]])
        with self.assertRaisesRegex(ValueError, "unique"):
            b.fit_bank(duplicate)
        scores = self.frames["validation_scores.csv"]
        with self.assertRaisesRegex(ValueError, "Invalid case"):
            b.apply_thresholds(pd.concat([scores, scores.iloc[:1]]), self.frames["candidates.csv"])

    def test_empty_review_has_no_accuracy_or_rank(self):
        progress = evaluation.review.load_review(evaluation.DEFAULT_REVIEW)
        self.assertTrue(progress.reviewer_label.eq("").all())
        metrics, summary = evaluation.evaluate(self.frames["candidates.csv"], self.frames["validation_flags.csv"], progress)
        self.assertEqual(len(metrics), 120)
        self.assertEqual(summary["status"], "awaiting_human_review")
        self.assertEqual(summary["reviewed_cases"], 0)
        self.assertFalse(summary["ranking_available"])
        self.assertEqual(summary["best_observed_validation_candidates"], [])
        self.assertIsNone(summary["selected_candidate"])
        self.assertTrue(metrics[["precision", "recall", "f1", "accuracy", "balanced_accuracy", "validation_f1_rank"]].isna().all().all())

    def test_confusion_math_on_actual_lateness_not_anomaly_ground_truth(self):
        # Actual calendar lateness exercises binary metric math only. It is NOT saved or used as an anomaly label.
        context = self.context.set_index("order_id").loc[self.subsets["validation"].order_id]
        observed_late = context.order_delivered_customer_date.dt.normalize().gt(context.order_estimated_delivery_date.dt.normalize()).to_numpy(dtype=bool)
        predicted = self.frames["validation_flags.csv"].iloc[:, 1].to_numpy(dtype=bool)
        self.assertTrue(observed_late.any())
        self.assertTrue((~observed_late).any())
        metrics = evaluation.binary_metrics(observed_late, predicted)
        tn, fp, fn, tp = confusion_matrix(observed_late, predicted, labels=[False, True]).ravel()
        self.assertEqual((metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]), (tp, fp, fn, tn))
        self.assertAlmostEqual(metrics["precision"], precision_score(observed_late, predicted))
        self.assertAlmostEqual(metrics["recall"], recall_score(observed_late, predicted))
        self.assertAlmostEqual(metrics["f1"], f1_score(observed_late, predicted))
        self.assertLessEqual(metrics["precision_wilson95_low"], metrics["precision"])
        self.assertGreaterEqual(metrics["precision_wilson95_high"], metrics["precision"])

    def test_actual_old_labels_without_normals_cannot_rank(self):
        old = pd.read_csv(ROOT / "data/manual_review/final_candidate_manual_validation_labeled.csv", dtype={"case_id": "string"})
        self.assertTrue(old.groupby("case_id").reviewer_label.nunique().eq(1).all())
        progress = old[["case_id", "reviewer_label"]].drop_duplicates("case_id")
        # Only tests handling of the existing biased reference. It never enters the benchmark/evaluation outputs.
        baseline = pd.read_csv(ROOT / "data/anomaly/anomaly_results.csv", dtype={"case_id": "string"})
        names = ["rule_anomaly_flag", "isolation_forest_flag"]
        flags = baseline.loc[baseline.case_id.isin(progress.case_id), ["case_id", *names]].rename(columns={"case_id": "order_id"})
        flags[names] = flags[names].astype(bool)
        candidates = pd.DataFrame({"candidate": names, "family": ["historical_reference", "historical_reference"]})
        metrics, summary = evaluation.evaluate(candidates, flags, progress)
        self.assertEqual(summary["status"], "insufficient_definite_classes")
        self.assertEqual(summary["suspicious_cases"], 1)
        self.assertEqual(summary["definite_normal_cases"], 0)
        self.assertTrue(metrics.validation_f1_rank.isna().all())
        self.assertTrue(metrics.balanced_accuracy.isna().all())
        self.assertTrue(metrics.evaluated_cases.eq(70).all())
        with self.assertRaisesRegex(ValueError, "unique nonmissing"):
            evaluation.evaluate(candidates, flags, pd.concat([progress, progress.iloc[:1]]))

    def test_undefined_metric_denominators_remain_unavailable(self):
        old = pd.read_csv(ROOT / "data/manual_review/final_candidate_manual_validation_labeled.csv")
        known_positive = old.loc[old.reviewer_label.eq("Anomaly")].reviewer_label.eq("Anomaly").to_numpy(dtype=bool)
        metrics = evaluation.binary_metrics(known_positive, known_positive)
        self.assertEqual(metrics["precision"], 1)
        self.assertIsNone(metrics["specificity"])
        self.assertIsNone(metrics["balanced_accuracy"])
        empty = evaluation.binary_metrics(known_positive[:0], known_positive[:0])
        for column in ["precision", "recall", "f1", "accuracy", "balanced_accuracy"]:
            self.assertIsNone(empty[column])
        self.assertEqual(evaluation.wilson(0, 0), (None, None))

    def test_versioned_models_roundtrip_evaluator_and_hash_guards(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "benchmark"
            wrong = dict(self.parents, experiment_manifest_sha256="")
            with self.assertRaisesRegex(ValueError, "Experiment changed"):
                b.write_benchmark(self.frames, self.bank, self.design, wrong, output=output)
            b.write_benchmark(self.frames, self.bank, self.design, self.parents, output=output)
            manifest = evaluation.verify_benchmark(output)
            self.assertEqual(manifest["design"], self.design)
            loaded = joblib.load(output / "model_bank.joblib")
            scored = b.add_ensembles(b.base_scores(loaded, self.subsets["validation"]), loaded["rank_references"])
            actual = b.apply_thresholds(scored, loaded["candidates"])
            pd.testing.assert_frame_equal(actual, self.frames["validation_flags.csv"])
            candidates, flags, progress, hashes = evaluation.load_inputs(output)
            metrics, summary = evaluation.evaluate(candidates, flags, progress)
            evaluation_output = Path(temporary) / "evaluation"
            evaluation.write_evaluation(metrics, summary, hashes, benchmark_dir=output, output=evaluation_output)
            saved = json.loads((evaluation_output / "manifest.json").read_text())
            for filename, digest in saved["output_hashes"].items():
                self.assertEqual(b.previous.bed.audit.file_hash(evaluation_output / filename), digest)
            self.assertEqual(saved["review_summary"]["status"], "awaiting_human_review")
            with self.assertRaises(FileExistsError):
                evaluation.write_evaluation(metrics, summary, hashes, benchmark_dir=output, output=evaluation_output)
            with self.assertRaises(FileExistsError):
                b.write_benchmark(self.frames, self.bank, self.design, self.parents, output=output)
            actual_hash = b.previous.bed.audit.file_hash
            def changed(path):
                return "" if Path(path).name == "candidates.csv" else actual_hash(path)
            with patch.object(b.previous.bed.audit, "file_hash", side_effect=changed):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    evaluation.verify_benchmark(output)


if __name__ == "__main__":
    unittest.main()
