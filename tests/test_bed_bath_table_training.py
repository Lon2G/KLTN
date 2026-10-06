"""Train-only models and review preparation tested against actual frozen Olist cases."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import train_bed_bath_table as training
import status_context_manual_review as review
from order_status_context_audit import BLIND_COLUMNS, REVIEW_COLUMNS


class BedBathTableTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.matrices, cls.context, cls.parents = training.load_inputs()
        cls.reviewed, cls.review_sources = training.current_review_exposure()
        cls.frames, cls.models, cls.review_design = training.build_training(cls.matrices, cls.context, cls.reviewed)

    def test_loader_never_parses_test_matrix_and_only_returns_training_periods(self):
        reader = training.bed.usage.read_snapshot_table
        with patch.object(training.bed.usage, "read_snapshot_table", wraps=reader) as tracked:
            matrices, context, _ = training.load_inputs()
        filenames = [call.args[1] for call in tracked.call_args_list]
        self.assertEqual(filenames, ["cohort_cases.csv", "train_timing_features.csv", "validation_timing_features.csv"])
        self.assertEqual(set(matrices), {"train", "validation"})
        self.assertEqual(set(context.split), {"train", "validation"})
        self.assertTrue(set(matrices["train"].order_id).isdisjoint(matrices["validation"].order_id))
        with self.assertRaisesRegex(ValueError, "not test"):
            training.build_training({**matrices, "test": matrices["validation"]}, context, self.reviewed)

    def test_statistics_equal_independent_train_quantiles(self):
        expected_train = self.matrices["train"]
        stats = self.frames["train_statistics.csv"].set_index("feature")
        for feature in training.FEATURES:
            values = expected_train[feature].to_numpy()
            q01, q25, median, q75, q99 = np.quantile(values, [.01, .25, .5, .75, .99], method="linear")
            self.assertAlmostEqual(stats.at[feature, "q01_days"], q01)
            self.assertAlmostEqual(stats.at[feature, "q25_days"], q25)
            self.assertAlmostEqual(stats.at[feature, "median_days"], median)
            self.assertAlmostEqual(stats.at[feature, "q75_days"], q75)
            self.assertAlmostEqual(stats.at[feature, "q99_days"], q99)
            self.assertAlmostEqual(stats.at[feature, "long_iqr_threshold_days"], q75 + 1.5 * (q75 - q25))
            self.assertEqual(stats.at[feature, "zero_train_orders"], int((values == 0).sum()))
            self.assertEqual(stats.at[feature, "train_orders"], len(expected_train))

    def test_saved_fit_provenance_and_forest_parameters(self):
        train = self.matrices["train"]
        for transform, bundle in self.models.items():
            self.assertEqual(bundle["train_order_ids"], train.order_id.tolist())
            self.assertEqual(bundle["features"], training.FEATURES)
            estimator = bundle["pipeline"].named_steps["isolation_forest"]
            self.assertEqual(estimator.n_estimators, 300)
            self.assertEqual(estimator.max_samples_, 256)
            self.assertEqual(estimator.random_state, 42)
            self.assertEqual(estimator.n_features_in_, 3)
            if transform == "log1p":
                transformed = bundle["pipeline"].named_steps["duration_transform"].transform(train[training.FEATURES])
                np.testing.assert_allclose(transformed, np.log1p(train[training.FEATURES]), rtol=1e-14)

    def test_thresholds_are_train_score_quantiles_not_validation_refits(self):
        for row in self.frames["score_thresholds.csv"].itertuples(index=False):
            train_scores = self.frames["train_scores.csv"][f"if_{row.transform}_score"]
            expected = np.quantile(train_scores, 1 - row.train_tail_fraction, method="linear")
            self.assertAlmostEqual(row.score_threshold, expected)
            self.assertEqual(row.train_flagged_orders, int(train_scores.gt(expected).sum()))
            val = self.frames["validation_scores.csv"]
            self.assertTrue(val[row.candidate].eq(val[f"if_{row.transform}_score"].gt(expected)).all())
        for transform in training.TRANSFORMS:
            names = [training.candidate_id(transform, fraction) for fraction in training.TAIL_FRACTIONS]
            scored = self.frames["validation_scores.csv"]
            for fewer, more in zip(names, names[1:]):
                self.assertFalse((scored[fewer] & ~scored[more]).any())

    def test_changing_validation_subset_does_not_change_training(self):
        reduced = {"train": self.matrices["train"], "validation": self.matrices["validation"].iloc[::2]}
        frames, models, _ = training.build_training(reduced, self.context, self.reviewed)
        for filename in ["train_statistics.csv", "score_thresholds.csv", "train_scores.csv"]:
            pd.testing.assert_frame_equal(frames[filename], self.frames[filename])
        for name in training.TRANSFORMS:
            self.assertEqual(models[name]["train_order_ids"], self.models[name]["train_order_ids"])
        self.assertEqual(len(frames["validation_scores.csv"]), len(reduced["validation"]))

    def test_row_order_is_deterministic_for_fit_and_review(self):
        matrices = {name: frame.iloc[::-1] for name, frame in self.matrices.items()}
        frames, _, design = training.build_training(matrices, self.context.iloc[::-1], self.reviewed)
        self.assertEqual(design, self.review_design)
        for name in self.frames:
            pd.testing.assert_frame_equal(frames[name], self.frames[name])

    def test_warning_rules_keep_zero_and_do_not_create_anomaly_labels(self):
        scores = self.frames["train_scores.csv"]
        stats = self.frames["train_statistics.csv"].set_index("feature")
        self.assertGreater(scores[training.FEATURES].eq(0).sum().sum(), 0)
        for feature in training.FEATURES:
            expected_long = scores[feature].gt(stats.at[feature, "long_iqr_threshold_days"])
            expected_short = scores[feature].lt(stats.at[feature, "q01_days"]) | scores[feature].eq(0)
            self.assertTrue(scores[f"{feature}_long_warning"].eq(expected_long).all())
            self.assertTrue(scores[f"{feature}_short_warning"].eq(expected_short).all())
            self.assertTrue(scores.loc[scores[feature].eq(0), f"{feature}_short_warning"].all())
        for name in ["train_scores.csv", "validation_scores.csv", "validation_process_audit.csv"]:
            self.assertNotIn("anomaly_label", self.frames[name].columns)
            self.assertNotIn("anomaly_probability", self.frames[name].columns)
            self.assertNotIn("reviewer_label", self.frames[name].columns)

    def test_threshold_equality_is_not_flagged(self):
        statistics = self.frames["train_statistics.csv"].copy()
        actual = self.matrices["train"].iloc[:1].copy()
        # Use a real observed duration as a configurable test boundary, never invent a case.
        feature = training.FEATURES[0]
        statistics.loc[statistics.feature.eq(feature), "long_iqr_threshold_days"] = actual[feature].iloc[0]
        score = training.statistical_signals(actual, statistics)
        self.assertFalse(score[f"{feature}_long_warning"].iloc[0])
        bundle = dict(self.models["raw"])
        value = -bundle["pipeline"].score_samples(actual[training.FEATURES])[0]
        bundle["score_thresholds"] = {"boundary_check": value}
        score = training.score_candidates(actual, {"raw": bundle}, statistics)
        self.assertFalse(score.boundary_check.iloc[0])

    def test_comparison_and_overlap_match_case_sets(self):
        val, train = self.frames["validation_scores.csv"], self.frames["train_scores.csv"]
        for row in self.frames["validation_comparison.csv"].itertuples(index=False):
            self.assertEqual(row.validation_orders, len(val))
            self.assertEqual(row.train_orders, len(train))
            self.assertEqual(row.validation_flagged_orders, int(val[row.candidate].sum()))
            self.assertEqual(row.train_flagged_orders, int(train[row.candidate].sum()))
            self.assertAlmostEqual(row.validation_flagged_fraction, val[row.candidate].mean())
        for row in self.frames["validation_overlap.csv"].itertuples(index=False):
            a = set(val.loc[val[row.first_candidate], "order_id"])
            b = set(val.loc[val[row.second_candidate], "order_id"])
            self.assertEqual(row.intersection, len(a & b))
            self.assertEqual(row.union, len(a | b))
            if a | b:
                self.assertAlmostEqual(row.jaccard, len(a & b) / len(a | b))

    def test_all_validation_process_cases_remain_without_imputation(self):
        source = self.context.loc[self.context.split.eq("validation")].set_index("order_id").sort_index()
        actual = self.frames["validation_process_audit.csv"].set_index("order_id").sort_index()
        self.assertEqual(len(actual), 1853)
        self.assertEqual(set(actual.index), set(source.index))
        pd.testing.assert_frame_equal(actual[training.bed.audit.TIMES], source[training.bed.audit.TIMES])
        self.assertGreater(actual.has_reversed_recorded_milestones.sum(), 0)
        self.assertGreater(actual.missing_milestone_count.gt(0).sum(), 0)
        self.assertEqual(self.frames["validation_usage_summary.csv"].orders.sum(), len(actual))

    def test_blind_sample_uses_real_unreviewed_validation_cases_only(self):
        blind = self.frames["review/review_cases_blind.csv"]
        self.assertEqual(blind.columns.tolist(), BLIND_COLUMNS)
        self.assertEqual(len(blind), 120)
        self.assertTrue(blind.case_id.is_unique)
        self.assertTrue(blind[REVIEW_COLUMNS].eq("").all().all())
        self.assertTrue(set(blind.case_id).issubset(self.matrices["validation"].order_id))
        self.assertTrue(set(blind.case_id).isdisjoint(self.reviewed))
        source = self.context.set_index("order_id").loc[blind.case_id]
        for column in training.bed.audit.TIMES:
            np.testing.assert_array_equal(blind[column].to_numpy(), source[column].to_numpy())
        design = self.review_design
        self.assertAlmostEqual(design["inclusion_probability"], design["sample_orders"] / design["eligible_orders"])
        self.assertAlmostEqual(design["sampling_weight"], design["eligible_orders"] / design["sample_orders"])
        with self.assertRaises(ValueError):
            training.prepare_blind_review(self.context, self.matrices["validation"], self.reviewed, size=0)

    def test_real_invalid_rows_and_wrong_schema_are_rejected(self):
        frame = self.matrices["train"]
        with self.assertRaisesRegex(ValueError, "unique"):
            training.validate_features(pd.concat([frame, frame.iloc[:1]]))
        with self.assertRaisesRegex(ValueError, "allowlist"):
            training.validate_features(frame[list(reversed(frame.columns))])
        reversed_cases = self.context.loc[self.context.has_reversed_recorded_milestones, ["order_id", *training.FEATURES]]
        self.assertFalse(reversed_cases.empty)
        with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
            training.validate_features(reversed_cases)
        missing = self.context.loc[self.context.missing_milestone_count.gt(0), ["order_id", *training.FEATURES]]
        with self.assertRaisesRegex(ValueError, "finite and nonnegative"):
            training.validate_features(missing)

    def test_saved_models_roundtrip_blind_cli_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "training"
            wrong = dict(self.parents, experiment_manifest_sha256="")
            with self.assertRaisesRegex(ValueError, "Source changed"):
                training.write_training(self.frames, self.models, self.review_design, wrong, self.review_sources, output=output)
            self.assertFalse(output.exists())
            training.write_training(self.frames, self.models, self.review_design, self.parents, self.review_sources, output=output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertFalse(manifest["test_scored"])
            self.assertFalse(manifest["human_labels_created"])
            self.assertIsNone(manifest["selected_candidate"])
            self.assertEqual(manifest["fit_split"], "train")
            for filename, digest in manifest["output_hashes"].items():
                self.assertEqual(training.bed.audit.file_hash(output / filename), digest)
            loaded = {name: joblib.load(output / f"models/if_{name}.joblib") for name in training.TRANSFORMS}
            actual = training.score_candidates(self.matrices["validation"], loaded, self.frames["train_statistics.csv"])
            pd.testing.assert_frame_equal(actual, self.frames["validation_scores.csv"])
            batch = output / "review"
            original = review.load_review(batch)
            self.assertTrue(original.reviewer_label.eq("").all())
            with patch("builtins.input", return_value="q"), patch("builtins.print"):
                review.run_review(batch)
            progress = review.load_review(batch)
            self.assertTrue(progress.reviewer_label.eq("").all())
            self.assertTrue(progress.reviewed_at_utc.eq("").all())
            with self.assertRaises(FileExistsError):
                training.write_training(self.frames, self.models, self.review_design, self.parents, self.review_sources, output=output)


if __name__ == "__main__":
    unittest.main()
