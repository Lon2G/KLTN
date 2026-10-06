"""Paired ablation tests use real Olist observations, never invented thesis labels."""

from copy import deepcopy
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
import train_business_ablation as ablation


class BusinessAblationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original_read = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original_read(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
             patch.object(ablation.joblib, "load", side_effect=AssertionError("Do not load previous fitted weights")):
            cls.inputs = ablation.load_inputs()
            cls.frames, cls.bundle, cls.summary = ablation.train_study(*cls.inputs)
        cls.subsets, cls.ledger, cls.plan, cls.provenance = cls.inputs
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"study"
        cls.saved_summary = ablation.write_study(cls.frames, cls.bundle, cls.summary, cls.output)
        cls.restored, cls.manifest = ablation.load_study(cls.output)

    def test_real_scope_counts_and_honest_claims(self):
        summary = self.summary
        self.assertEqual(summary["development_orders"], 7333)
        self.assertEqual(summary["common_orders"], {"fit": 3190, "calibration": 1412, "validation": 1523})
        self.assertEqual(summary["feature_counts"], {"timing_only": 3, "business_only": 9, "combined": 12})
        self.assertEqual(summary["validation_all_orders"], 1853)
        self.assertEqual(summary["validation_unscored_orders"], 330)
        self.assertEqual(summary["analysis_role_counts"]["process_only"], 732)
        self.assertEqual(summary["analysis_role_counts"]["deferred_at_inner_cutoff"], 476)
        self.assertEqual(summary["fresh_ml_fits"], 30)
        self.assertEqual(summary["profile_candidate_results"], 288)
        self.assertEqual(summary["reserved_test_orders_excluded"], 1691)
        self.assertTrue(summary["training_performed"])
        for key in ["previous_fitted_weights_loaded", "test_scored", "human_labels_created", "fraud_labels_created",
                    "accuracy_computed", "anomaly_probabilities_computed", "imputation_performed", "online_validated"]:
            self.assertFalse(summary[key])
        self.assertIsNone(summary["selected_profile"])
        self.assertIsNone(summary["selected_candidate"])

    def test_identical_populations_disjoint_roles_and_temporal_boundaries(self):
        roles = self.bundle["case_ids"]
        self.assertFalse(set(roles["fit"]) & set(roles["calibration"]))
        self.assertFalse(set(roles["fit"]+roles["calibration"]) & set(roles["validation"]))
        for role in ablation.ROLES:
            for profile in ablation.PROFILES:
                frame = self.frames[f"{profile}_{role}_features.csv"]
                self.assertEqual(frame.order_id.tolist(), roles[role])
                self.assertEqual(frame.columns.tolist(), ["order_id", *ablation.PROFILES[profile]])
        self.assertEqual(len(self.ledger), 7333)
        self.assertTrue(self.ledger.order_id.is_unique)
        indexed = self.ledger.set_index("order_id")
        self.assertTrue(indexed.loc[roles["fit"], "order_purchase_timestamp"].lt(pd.Timestamp("2017-12-01")).all())
        self.assertTrue(indexed.loc[roles["calibration"], "order_purchase_timestamp"].ge(pd.Timestamp("2017-12-01")).all())
        self.assertTrue(indexed.loc[roles["validation"], "order_purchase_timestamp"].ge(pd.Timestamp("2018-03-01")).all())

    def test_no_test_matrix_or_manual_reference_parsed(self):
        self.assertFalse(any("test_timing" in path or "manual" in path for path in self.read_paths))
        self.assertFalse(any(path.endswith(".joblib") for path in self.read_paths))
        ledger = pd.read_csv(Path(self.provenance["feature_sources"]["reservation_directory"])/"cohort_cases.csv", usecols=["order_id", "split"])
        reserved = set(ledger.loc[ledger.split.eq("test"), "order_id"])
        for frame in self.frames.values():
            if "order_id" in frame:
                self.assertFalse(set(frame.order_id) & reserved)

    def test_exact_model_grid_is_shared_across_all_feature_profiles(self):
        for profile, bank in self.bundle["banks"].items():
            self.assertEqual(list(bank["models"]), ablation.training.engine.MODEL_NAMES)
            for name, model in bank["models"].items():
                detector = model.named_steps["detector"]
                self.assertEqual(detector.n_features_in_, len(ablation.PROFILES[profile]))
                if name.startswith("if_"):
                    self.assertEqual(detector.n_estimators, 300)
                    self.assertEqual(detector.random_state, 42)
                    self.assertEqual(detector.max_samples, int(name.rsplit("_", 1)[1]))
                elif name.startswith("lof_"):
                    self.assertTrue(detector.novelty)
                    self.assertEqual(detector.n_neighbors, int(name.rsplit("_", 1)[1]))
                else:
                    self.assertEqual(detector.kernel, "rbf")
                    self.assertEqual(detector.fit_status_, 0)
                    self.assertEqual(detector.gamma, "scale")

    def test_scalers_learn_only_real_fit_values_and_zero_iqr_is_explicit(self):
        diagnostics = self.frames["fit_preprocessing.csv"]
        self.assertEqual(diagnostics.loc[diagnostics.profile.eq("business_only"), "zero_iqr"].sum(), 5)
        self.assertTrue(diagnostics.loc[diagnostics.zero_iqr, "actual_robust_scale"].eq(1).all())
        for profile, bank in self.bundle["banks"].items():
            values = np.log1p(self.frames[f"{profile}_fit_features.csv"][bank["features"]].to_numpy(dtype=float))
            q25, median, q75 = np.quantile(values, [.25, .5, .75], axis=0)
            expected_scale = q75-q25
            expected_scale[expected_scale < 10*np.finfo(expected_scale.dtype).eps] = 1
            for name, model in bank["models"].items():
                if "scale" in model.named_steps:
                    np.testing.assert_allclose(model.named_steps["scale"].center_, median)
                    np.testing.assert_allclose(model.named_steps["scale"].scale_, expected_scale)

    def test_thresholds_are_calibration_quantiles_not_fit_or_validation_quantiles(self):
        for profile, bank in self.bundle["banks"].items():
            calibration = self.frames[f"{profile}_calibration_scores.csv"]
            candidates = bank["candidates"]
            self.assertEqual(len(candidates), 96)
            self.assertTrue(candidates.candidate.is_unique)
            self.assertEqual(candidates.groupby("score_column").size().unique().tolist(), [8])
            for row in candidates.itertuples(index=False):
                expected = np.quantile(calibration[row.score_column], 1-row.tail_fraction, method="linear")
                self.assertEqual(row.threshold, expected)
                for role in ["calibration", "validation"]:
                    flags = self.frames[f"{profile}_{role}_flags.csv"][row.candidate]
                    scores = self.frames[f"{profile}_{role}_scores.csv"][row.score_column]
                    pd.testing.assert_series_equal(flags, scores.gt(expected), check_names=False)

    def test_empirical_rank_ensemble_formulas_use_calibration_references(self):
        for profile, bank in self.bundle["banks"].items():
            calibration = self.frames[f"{profile}_calibration_scores.csv"]
            for name in ablation.training.engine.ENSEMBLE_ML:
                np.testing.assert_array_equal(bank["rank_references"][name], np.sort(calibration[name]))
            for role in ["calibration", "validation"]:
                scores = self.frames[f"{profile}_{role}_scores.csv"]
                ranks = np.column_stack([np.searchsorted(bank["rank_references"][name], scores[name], side="right")/len(calibration)
                                         for name in ablation.training.engine.ENSEMBLE_ML])
                np.testing.assert_allclose(scores.ensemble_rank_mean, ranks.mean(axis=1))
                np.testing.assert_allclose(scores.ensemble_rank_median, np.median(ranks, axis=1))

    def test_score_sign_and_no_novelty_scoring_on_fit(self):
        for profile, bank in self.bundle["banks"].items():
            observed = self.frames[f"{profile}_validation_features.csv"]
            scores = self.frames[f"{profile}_validation_scores.csv"]
            for name, model in bank["models"].items():
                np.testing.assert_allclose(scores[name], -model.score_samples(observed[bank["features"]]), rtol=1e-12, atol=1e-12)
            with self.assertRaisesRegex(ValueError, "fit cases"):
                ablation.score_bundle(self.bundle, profile, self.frames[f"{profile}_fit_features.csv"], self.bundle["identity"])

    def test_fresh_timing_refit_reproduces_original_overlapping_baseline(self):
        self.assertTrue(self.summary["preserved_timing_baseline_reproduced"])
        self.assertTrue(self.frames["preserved_baseline_checks.csv"].status.eq("matched").all())
        candidates = self.frames["timing_only_candidates.csv"].candidate.tolist()
        original = pd.read_csv(ablation.DEFAULT_BASELINE/"validation_flags.csv")[["order_id", *candidates]]
        pd.testing.assert_frame_equal(self.frames["timing_only_validation_flags.csv"], original, check_dtype=False, check_exact=True)

    def test_pairwise_counts_and_jaccard_match_actual_case_sets(self):
        self.assertEqual(len(self.frames["profile_pairwise_agreement.csv"]), 288)
        for row in self.frames["profile_pairwise_agreement.csv"].itertuples(index=False):
            left, right = (self.frames[f"{profile}_validation_flags.csv"] for profile in [row.left_profile, row.right_profile])
            left = set(left.loc[left[row.candidate], "order_id"])
            right = set(right.loc[right[row.candidate], "order_id"])
            self.assertEqual(row.both_flag, len(left & right))
            self.assertEqual(row.left_only, len(left-right))
            self.assertEqual(row.right_only, len(right-left))
            self.assertEqual(row.neither_flag, 1523-len(left | right))
            self.assertEqual(row.changed_decisions, len(left ^ right))
            self.assertAlmostEqual(row.changed_fraction, len(left ^ right)/1523)
            if left | right:
                self.assertAlmostEqual(row.jaccard_overlap, len(left & right)/len(left | right))
            else:
                self.assertTrue(pd.isna(row.jaccard_overlap))

    def test_changed_case_evidence_contains_exact_candidate_names_not_new_labels(self):
        flags = {profile: self.frames[f"{profile}_validation_flags.csv"].set_index("order_id") for profile in ablation.PROFILES}
        changes = self.frames["validation_decision_changes.csv"]
        self.assertFalse(changes.duplicated(["order_id", "left_profile", "right_profile"]).any())
        for row in changes.iloc[::17].itertuples(index=False):
            left, right = flags[row.left_profile].loc[row.order_id], flags[row.right_profile].loc[row.order_id]
            names = left.index[left.ne(right)].tolist()
            self.assertEqual(json.loads(row.changed_candidate_names_json), names)
            self.assertEqual(row.changed_candidates, len(names))
        self.assertNotIn("anomaly_label", changes)

    def test_monthly_and_overall_workload_reconcile_on_scored_denominators(self):
        coverage = self.frames["monthly_coverage.csv"]
        self.assertEqual(coverage.all_orders.sum(), 1853)
        self.assertEqual(coverage.scored_orders.sum(), 1523)
        self.assertEqual(coverage.unscored_orders.sum(), 330)
        self.assertEqual(coverage.scored_orders.tolist(), [642, 536, 345])
        metrics = self.frames["validation_candidate_metrics.csv"].set_index(["profile", "candidate"])
        for key, rows in self.frames["validation_monthly_flags.csv"].groupby(["profile", "candidate"]):
            self.assertEqual(rows.flagged_orders.sum(), metrics.at[key, "flagged_orders"])
            self.assertAlmostEqual(np.average(rows.flag_fraction_of_scored, weights=rows.scored_orders), metrics.at[key, "flag_fraction_of_scored"])

    def test_refitted_models_are_order_independent_on_real_fit_and_calibration(self):
        profile = "business_only"
        fit = self.frames[f"{profile}_fit_features.csv"]
        bank = ablation.fit_bank(fit.iloc[::-1], ablation.PROFILES[profile])
        actual = ablation.base_scores(bank, self.frames[f"{profile}_calibration_features.csv"].iloc[::-1])
        expected = self.frames[f"{profile}_calibration_scores.csv"][["order_id", *ablation.training.engine.MODEL_NAMES]]
        pd.testing.assert_frame_equal(actual, expected, check_exact=True)

    def test_prediction_is_frozen_without_refit_or_threshold_updates(self):
        candidates = {name: bank["candidates"].copy(deep=True) for name, bank in self.restored["banks"].items()}
        with patch.object(ablation.IsolationForest, "fit", side_effect=AssertionError("No refit")), \
             patch.object(ablation.LocalOutlierFactor, "fit", side_effect=AssertionError("No refit")), \
             patch.object(ablation.OneClassSVM, "fit", side_effect=AssertionError("No refit")):
            for profile in ablation.PROFILES:
                frame = self.frames[f"{profile}_validation_features.csv"].iloc[::3]
                scores, flags = ablation.score_bundle(self.restored, profile, frame.iloc[::-1], self.restored["identity"])
                expected = self.frames[f"{profile}_validation_scores.csv"].iloc[::3].reset_index(drop=True)
                pd.testing.assert_frame_equal(scores, expected, rtol=1e-12, atol=1e-12)
                pd.testing.assert_frame_equal(flags, self.frames[f"{profile}_validation_flags.csv"].iloc[::3].reset_index(drop=True))
        for name in ablation.PROFILES:
            pd.testing.assert_frame_equal(self.restored["banks"][name]["candidates"], candidates[name])

    def test_invalid_identity_schema_empty_duplicate_and_too_small_fit_fail(self):
        frame = self.frames["business_only_validation_features.csv"]
        identity = {**self.bundle["identity"], "marketplace_id": "different_marketplace"}
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            ablation.score_bundle(self.bundle, "business_only", frame, identity)
        for invalid in [frame.iloc[:0], frame.drop(columns="item_count"), pd.concat([frame, frame.iloc[:1]])]:
            with self.assertRaises(ValueError):
                ablation.score_bundle(self.bundle, "business_only", invalid, self.bundle["identity"])
        with self.assertRaisesRegex(ValueError, "512"):
            ablation.fit_bank(self.frames["business_only_fit_features.csv"].iloc[:511], ablation.BUSINESS_FEATURES)

    def test_protocol_is_declared_and_refuses_adaptive_configuration_changes(self):
        self.assertEqual(json.loads(ablation.DEFAULT_PROTOCOL.read_text()), ablation.protocol())
        path = Path(self.temporary.name)/"different_protocol.json"
        changed = deepcopy(ablation.protocol())
        changed["tail_fractions"] = changed["tail_fractions"][:1]
        path.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "protocol differs"):
            ablation.load_inputs(protocol_path=path)

    def test_profile_comparison_rejects_different_case_sets_and_missing_flags(self):
        candidates = {profile: self.frames[f"{profile}_candidates.csv"] for profile in ablation.PROFILES}
        flags = {profile: self.frames[f"{profile}_validation_flags.csv"] for profile in ablation.PROFILES}
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            ablation.compare_profiles(candidates, {**flags, "business_only": flags["business_only"].iloc[1:]}, self.ledger)
        with self.assertRaisesRegex(ValueError, "boolean"):
            ablation.compare_profiles(candidates, {**flags, "business_only": flags["business_only"].iloc[:, :-1]}, self.ledger)

    def test_model_roundtrip_all_tables_and_hashes(self):
        self.assertEqual(self.saved_summary["serialized_tables_verified"], 34)
        self.assertTrue(self.saved_summary["saved_model_roundtrip_verified"])
        self.assertEqual(len(list(self.output.iterdir())), 41)
        ablation.verify_files(self.output, self.manifest)
        for name, expected in self.frames.items():
            actual = ablation.business.usage.read_snapshot_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
        checks = pd.read_csv(self.output/"reload_checks.csv")
        self.assertEqual(len(checks), 6)
        self.assertTrue(checks.scores_match.all())
        self.assertTrue(checks.flags_match_exactly.all())
        self.assertFalse(checks.refitting_performed.any())
        self.assertEqual(self.restored["provenance"], self.provenance)

    def test_modified_model_or_runtime_rejected_before_deserialization(self):
        broken = Path(self.temporary.name)/"broken"
        shutil.copytree(self.output, broken)
        (broken/"model_banks.joblib").write_bytes(b"interrupted write")
        with patch.object(ablation.joblib, "load", side_effect=AssertionError("Must check hashes first")):
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                ablation.load_study(broken)
            with patch.object(ablation.training, "runtime", return_value={}):
                with self.assertRaisesRegex(ValueError, "runtime differs"):
                    ablation.load_study(self.output)

    def test_no_overwrite_or_publication_after_source_or_reload_failure(self):
        with self.assertRaises(FileExistsError):
            ablation.write_study(self.frames, self.bundle, self.summary, self.output)
        changed = {**self.provenance, "features_manifest_sha256": "changed metadata"}
        with self.assertRaisesRegex(ValueError, "manifest changed"):
            ablation.verify_sources(changed)
        target = Path(self.temporary.name)/"not_published"
        with patch.object(ablation, "load_study", side_effect=ValueError("reload verification failed")):
            with self.assertRaisesRegex(ValueError, "reload verification failed"):
                ablation.write_study(self.frames, self.bundle, self.summary, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(".not_published-*")))


if __name__ == "__main__":
    unittest.main()
