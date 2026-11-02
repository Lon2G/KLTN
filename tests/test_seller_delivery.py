"""Paired seller ablation checks on real Olist records; no generated orders."""

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
sys.path.insert(0, str(ROOT/"scripts/01_data_preparation"))
sys.path.insert(0, str(ROOT/"scripts/03_anomaly_detection"))
import experiment_seller_delivery as experiment


class SellerDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("Do not load previous models")):
            cls.inputs, cls.provenance = experiment.load_inputs()
            cls.frames, cls.bundle, cls.summary = experiment.run_experiment(cls.inputs, lambda _: None)
        cls.ledger = cls.frames["analysis_ledger.csv"]
        cls.predicted = cls.frames["predictions.csv"]
        cls.forecast_inputs = cls.frames["forecast_inputs.csv"]
        cls.forecasts = cls.frames["frozen_forecasts.csv"]
        cls.scores = cls.frames["horizon_scores.csv"]
        cls.selected = {role: cls.ledger.loc[cls.ledger.inner_role.eq(role) & cls.ledger.common_eligible]
                        for role in experiment.context.ROLES}
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"seller_ablation"
        cls.saved_summary = experiment.write_experiment(cls.frames, cls.bundle, cls.summary, cls.provenance, cls.output)
        cls.manifest = experiment.load_snapshot(cls.output)

    def test_real_population_retained_and_common_scope_explicit(self):
        self.assertEqual(len(self.ledger), 7333)
        self.assertEqual(len(self.forecast_inputs), 1853)
        self.assertEqual(self.summary["common_role_counts"], {"fit": 2753, "calibration": 1316, "validation": 1376})
        expected = self.ledger.paired_eligible & self.ledger.seller_count.eq(1) & self.ledger.history_ready
        np.testing.assert_array_equal(self.ledger.common_eligible, expected)
        self.assertEqual(self.frames["scope_coverage.csv"].orders.sum(), 7333)
        self.assertEqual(self.saved_summary["reserved_test_orders_excluded"], 1691)

    def test_existing_ledger_values_preserved_exactly(self):
        source = self.inputs["ledger"].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.ledger[source.columns], source, check_dtype=False, check_exact=True)
        source = self.inputs["forecast_inputs"].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.forecast_inputs[source.columns], source, check_dtype=False, check_exact=True)

    def test_no_test_features_review_labels_or_previous_weights_used(self):
        for path in self.read_paths:
            self.assertFalse("manual_review" in path or "test_timing" in path or "models/" in path, path)
        self.assertEqual(set(self.ledger.split), {"train", "validation"})
        self.assertFalse(self.predicted.inner_role.eq("test").any())

    def test_history_values_come_from_the_actual_single_seller_pair(self):
        one = self.ledger.loc[self.ledger.seller_count.eq(1)].set_index("order_id")
        source = self.inputs["history"].loc[lambda x: x.order_id.isin(one.index)].set_index("order_id").loc[one.index]
        fields = experiment.ACTIVITY+experiment.OUTCOMES+experiment.seller.TIME_COLUMNS
        pd.testing.assert_frame_equal(one[fields], source[fields], check_dtype=False, check_index_type=False, check_exact=True)
        self.assertTrue(source.seller_id.eq(one.single_seller_id).all())

    def test_unavailable_history_is_not_imputed_or_used(self):
        multiple = self.ledger.loc[self.ledger.seller_count.gt(1)]
        self.assertEqual(len(multiple), 149)
        self.assertTrue(multiple[experiment.ACTIVITY+experiment.OUTCOMES].isna().all().all())
        self.assertFalse(multiple.common_eligible.any())
        absent = self.ledger.loc[self.ledger.seller_count.eq(1) & ~self.ledger.history_ready]
        self.assertFalse(absent.empty)
        self.assertFalse(absent.common_eligible.any())
        self.assertTrue(absent[experiment.ACTIVITY+experiment.OUTCOMES].isna().any(axis=1).all())
        used = self.ledger.loc[self.ledger.common_eligible]
        self.assertTrue(used.prior_promise_comparable_orders.gt(0).all())
        self.assertTrue(used.prior_reviewed_orders.gt(0).all())

    def test_each_history_event_strictly_precedes_current_purchase(self):
        for column in experiment.seller.TIME_COLUMNS:
            observed = self.ledger[column].notna()
            self.assertTrue(self.ledger.loc[observed, column].lt(self.ledger.loc[observed, "order_purchase_timestamp"]).all())
        history = self.inputs["history"]
        actual = self.ledger.set_index("order_id").order_purchase_timestamp
        np.testing.assert_array_equal(history.as_of_timestamp, history.order_id.map(actual))

    def test_same_fit_calibration_validation_ids_for_all_twelve_models(self):
        self.assertEqual(len(self.bundle["bank"]), 12)
        self.assertEqual(sum(len(x["models"]) for x in self.bundle["bank"].values()), 36)
        for entry in self.bundle["bank"].values():
            self.assertEqual(entry["fit_ids"], self.selected["fit"].order_id.tolist())
            self.assertEqual(entry["calibration_ids"], self.selected["calibration"].order_id.tolist())
        self.assertEqual(len(self.predicted), 12*(1316+1376))
        for (name, role), rows in self.predicted.groupby(["model", "inner_role"]):
            self.assertEqual(set(rows.order_id), set(self.selected[role].order_id), name)
            self.assertTrue(rows.order_id.is_unique)

    def test_time_roles_disjoint_and_targets_observed_before_cutoffs(self):
        fit, calibration, validation = [self.selected[role] for role in experiment.context.ROLES]
        self.assertFalse(set(fit.order_id) & set(calibration.order_id))
        self.assertFalse(set(fit.order_id) & set(validation.order_id))
        self.assertFalse(set(calibration.order_id) & set(validation.order_id))
        plan = self.inputs["plan"]
        for rows, cutoff in [(fit, "fit_end_exclusive"), (calibration, "train_end_exclusive"),
                             (validation, "validation_end_exclusive")]:
            self.assertTrue(rows.order_delivered_customer_date.lt(pd.Timestamp(plan[cutoff])).all())
        self.assertTrue(calibration.order_purchase_timestamp.ge(pd.Timestamp(plan["fit_end_exclusive"])).all())
        self.assertTrue(validation.order_purchase_timestamp.ge(pd.Timestamp(plan["train_end_exclusive"])).all())

    def test_target_is_real_carrier_to_customer_elapsed_days(self):
        for rows in self.selected.values():
            expected = (rows.order_delivered_customer_date-rows.order_delivered_carrier_date).dt.total_seconds()/86400
            np.testing.assert_allclose(rows[experiment.context.TARGET], expected, rtol=1e-13)

    def test_explicit_predictor_allowlist_excludes_current_outcomes_and_ids(self):
        forbidden = {experiment.context.TARGET, "order_id", "single_seller_id", "seller_id", "order_status",
                     "order_delivered_customer_date", "calendar_late_vs_promise", "review_score", "label"}
        contract = self.frames["feature_contract.csv"]
        for entry in self.bundle["bank"].values():
            columns = entry["numeric"]+entry["categorical"]
            self.assertFalse(forbidden.intersection(columns))
            expected = experiment.context.NUMERIC+experiment.context.DISTANCES+experiment.PROFILES[entry["profile"]]
            self.assertEqual(entry["numeric"], expected)
            self.assertEqual(entry["categorical"], experiment.context.CATEGORICAL)
            self.assertEqual(set(contract.loc[contract.profile.eq(entry["profile"]), "column"]), set(columns))
        self.assertNotIn("prior_review_score_5_fraction", experiment.OUTCOMES)
        self.assertFalse(forbidden.difference({"order_id", "single_seller_id"}).intersection(self.forecast_inputs.columns))

    def test_encoders_fit_only_on_fit_and_grid_unchanged_across_profiles(self):
        fit = self.selected["fit"]
        for name, profile, depth, leaf in experiment.MODELS:
            entry = self.bundle["bank"][name]
            self.assertEqual(entry["profile"], profile)
            for quantile, model in zip(experiment.context.QUANTILES, entry["models"]):
                regressor = model.named_steps["regressor"]
                self.assertEqual((regressor.loss, regressor.alpha, regressor.max_depth, regressor.min_samples_leaf,
                                  regressor.n_estimators, regressor.learning_rate, regressor.random_state),
                                 ("quantile", quantile, depth, leaf, 150, .05, 42))
                encoder = model.named_steps["encode"].named_transformers_["categorical"]
                for column, categories in zip(entry["categorical"], encoder.categories_):
                    self.assertEqual(set(categories), set(fit[column]))
                self.assertEqual(model.named_steps["encode"].transformers[0][1], "passthrough")

    def test_baseline_matches_independent_refit_with_parent_implementation(self):
        entry = experiment.context.fit_entry(self.selected["fit"], "gbr", geographic=True, depth=2, leaf=60)
        experiment.context.calibrate_entry(entry, self.selected["calibration"])
        actual = experiment.context.predict_entry(entry, self.selected["validation"])[1]
        baseline = self.bundle["bank"]["base_geo_d2_l60"]
        expected = experiment.context.predict_entry(baseline, self.selected["validation"])[1]
        np.testing.assert_array_equal(actual, expected)

    def test_residual_corrections_use_only_common_calibration(self):
        calibration = self.selected["calibration"]
        target = calibration[experiment.context.TARGET].to_numpy(dtype=float)
        for entry in self.bundle["bank"].values():
            raw = experiment.context.predict_entry(entry, calibration)[0]
            self.assertEqual(entry["lower_shift"], float(np.quantile(target-raw[:, 0], .05)))
            self.assertEqual(entry["upper_shift"], float(np.quantile(target-raw[:, 2], .95)))

    def test_fit_rejects_wrong_roles_configuration_and_missing_history(self):
        with self.assertRaisesRegex(ValueError, "common fit"):
            experiment.fit_entry(self.selected["calibration"], "plus_seller", 2, 30)
        outside = self.ledger.loc[self.ledger.inner_role.eq("fit") & ~self.ledger.common_eligible]
        self.assertFalse(outside.empty)
        with self.assertRaisesRegex(ValueError, "common fit"):
            experiment.fit_entry(outside, "base_geo", 2, 30)
        with self.assertRaisesRegex(ValueError, "Undeclared"):
            experiment.fit_entry(self.selected["fit"], "plus_seller", 9, 30)

    def test_scoring_rejects_training_ids_or_different_identity(self):
        with self.assertRaisesRegex(ValueError, "validation"):
            experiment.score_validation(self.bundle, self.selected["fit"], experiment.shared.business.IDENTITY)
        identity = {**experiment.shared.business.IDENTITY, "marketplace_id": "different_marketplace"}
        with self.assertRaisesRegex(ValueError, "identity/protocol"):
            experiment.score_validation(self.bundle, self.forecast_inputs, identity)
        with self.assertRaisesRegex(ValueError, "fitted orders"):
            experiment.context.predict_entry(self.bundle["bank"]["plus_seller_d2_l30"], self.selected["fit"])

    def test_expanded_forecasts_need_no_actual_delivery_outcome(self):
        self.assertNotIn(experiment.context.TARGET, self.forecast_inputs)
        self.assertNotIn("order_delivered_customer_date", self.forecast_inputs)
        self.assertEqual(self.forecasts.order_id.nunique(), 1576)
        self.assertEqual(len(self.forecasts), 12*1576)
        expected = set(self.forecast_inputs.loc[self.forecast_inputs.ablation_forecast_eligible, "order_id"])
        for _, rows in self.forecasts.groupby("model"):
            self.assertEqual(set(rows.order_id), expected)
        added = self.forecasts.loc[~self.forecasts.formerly_scored_validation]
        self.assertEqual(added.order_id.nunique(), 200)
        self.assertTrue(set(added.order_id).issubset(self.ledger.order_id))

    def test_completed_case_forecasts_replay_exactly(self):
        replay = self.frames["forecast_replay.csv"]
        self.assertEqual(replay.replayed_validation_orders.sum(), 12*1376)
        self.assertTrue(replay.all_predictions_identical.all())
        self.assertTrue(replay.max_absolute_prediction_difference.eq(0).all())

    def test_saved_model_roundtrip_and_inference_leave_weights_unchanged(self):
        restored = joblib.load(self.output/"model_bundle.joblib")
        before = joblib.hash(restored)
        actual = experiment.score_validation(restored, self.forecast_inputs, experiment.shared.business.IDENTITY)
        self.assertEqual(before, joblib.hash(restored))
        pd.testing.assert_frame_equal(actual, self.forecasts, check_exact=True)
        self.assertTrue(self.saved_summary["saved_model_roundtrip_verified"])

    def test_validation_metrics_independently_recomputed(self):
        metrics = self.frames["metrics.csv"].loc[lambda x: x.role.eq("validation") & x.view.eq("calibrated")].set_index("model")
        for name, rows in self.predicted.loc[lambda x: x.inner_role.eq("validation")].groupby("model"):
            y, low, median, high = [rows[column].to_numpy() for column in [experiment.context.TARGET, *experiment.windows.FORECAST_COLUMNS]]
            self.assertAlmostEqual(metrics.at[name, "median_mae_days"], np.abs(y-median).mean(), places=12)
            self.assertAlmostEqual(metrics.at[name, "coverage"], ((low <= y) & (y <= high)).mean(), places=12)
            self.assertAlmostEqual(metrics.at[name, "mean_width_days"], (high-low).mean(), places=12)
            score = high-low+20*np.maximum(low-y, 0)+20*np.maximum(y-high, 0)
            self.assertAlmostEqual(metrics.at[name, "interval_score_alpha010"], score.mean(), places=12)
            self.assertTrue((0 <= low).all() and (low <= median).all() and (median <= high).all())

    def test_paired_mae_differences_and_months_match_all_configurations(self):
        differences = self.frames["paired_differences.csv"]
        self.assertEqual(len(differences), 32)
        validation = self.predicted.loc[self.predicted.inner_role.eq("validation")]
        for row in differences.itertuples(index=False):
            rows = validation if row.purchase_month == "all" else validation.loc[validation.purchase_month.eq(row.purchase_month)]
            base, other = experiment.paired_rows(rows, row.depth, row.leaf, row.profile)
            delta = (other[experiment.context.TARGET]-other.calibrated_median_days).abs()-(base[experiment.context.TARGET]-base.calibrated_median_days).abs()
            self.assertEqual(row.paired_orders, len(delta))
            self.assertAlmostEqual(row.augmented_minus_base_mae_days, delta.mean(), places=12)
            self.assertEqual(row.orders_with_lower_error+row.orders_with_higher_error+row.orders_with_equal_error, len(delta))
        for _, rows in differences.groupby(["depth", "leaf", "profile"]):
            self.assertEqual(rows.loc[rows.purchase_month.ne("all"), "paired_orders"].sum(), 1376)

    def test_horizon_scores_only_use_mature_common_real_cases(self):
        outcomes = self.inputs["outcomes"]
        actual = self.scores[["order_id", "horizon_days"]].drop_duplicates()
        expected = outcomes.loc[outcomes.full_followup_window & outcomes.order_id.isin(self.forecasts.order_id), ["order_id", "horizon_days"]]
        self.assertEqual(set(map(tuple, actual.to_numpy())), set(map(tuple, expected.to_numpy())))
        self.assertFalse(self.scores.duplicated(["model", "order_id", "horizon_days"]).any())
        self.assertTrue(outcomes.loc[outcomes.full_followup_window, "horizon_endpoint"].lt(
            outcomes.loc[outcomes.full_followup_window, "event_time_cutoff_exclusive"]).all())

    def test_unknown_horizon_targets_remain_unknown_with_logical_bounds(self):
        np.testing.assert_array_equal(self.scores.capped_prediction_days,
                                      np.minimum(self.scores.calibrated_median_days, self.scores.horizon_days))
        for policy in experiment.windows.POLICIES:
            unknown = self.scores.loc[~self.scores[policy+"_outcome_known"]]
            self.assertFalse(unknown.empty)
            self.assertTrue(unknown[policy+"_target_days"].isna().all())
            self.assertTrue(unknown[policy+"_absolute_error_days"].isna().all())
            self.assertTrue(unknown[policy+"_error_lower_bound_days"].eq(0).all())
            np.testing.assert_array_equal(unknown[policy+"_error_upper_bound_days"],
                                          np.maximum(unknown.capped_prediction_days, unknown.horizon_days-unknown.capped_prediction_days))
            known = self.scores.loc[self.scores[policy+"_outcome_known"]]
            expected = (known[policy+"_target_days"]-known.capped_prediction_days).abs()
            for suffix in ["absolute_error_days", "error_lower_bound_days", "error_upper_bound_days"]:
                np.testing.assert_array_equal(known[policy+"_"+suffix], expected)

    def test_horizon_metric_denominators_and_empty_groups_are_explicit(self):
        metrics = self.frames["horizon_metrics.csv"]
        self.assertEqual(len(metrics), 216)
        for row in metrics.itertuples(index=False):
            rows = self.scores.loc[self.scores.model.eq(row.model) & self.scores.horizon_days.eq(row.horizon_days)
                                   & self.scores.purchase_month.eq(row.purchase_month)]
            self.assertEqual(row.scored_mature_orders, len(rows))
            self.assertEqual(row.known_outcome_orders+row.unresolved_outcome_orders, len(rows))
            if len(rows):
                self.assertAlmostEqual(row.all_scored_mae_lower_bound_days, rows[row.evidence_policy+"_error_lower_bound_days"].mean())
                self.assertAlmostEqual(row.all_scored_mae_upper_bound_days, rows[row.evidence_policy+"_error_upper_bound_days"].mean())
            else:
                self.assertTrue(np.isnan(row.all_scored_mae_lower_bound_days))
                self.assertTrue(np.isnan(row.observed_only_capped_mae_days))
        may = metrics.loc[metrics.purchase_month.eq("2018-05") & metrics.horizon_days.isin([30, 45])]
        self.assertEqual(len(may), 48)
        self.assertTrue(may.scored_mature_orders.eq(0).all())

    def test_paired_horizon_bounds_respect_shared_unknown_outcomes(self):
        comparisons = self.frames["horizon_paired_differences.csv"]
        self.assertEqual(len(comparisons), 144)
        self.assertFalse(comparisons.is_confidence_interval.any() or comparisons.winner_selected.any())
        for row in comparisons.itertuples(index=False):
            rows = self.scores.loc[self.scores.horizon_days.eq(row.horizon_days) & self.scores.purchase_month.eq(row.purchase_month)]
            base, other = experiment.paired_rows(rows, row.depth, row.leaf, row.profile)
            self.assertEqual(row.paired_mature_orders, len(base))
            if not len(base):
                self.assertTrue(np.isnan(row.augmented_minus_base_lower_bound_days))
                continue
            target = row.evidence_policy+"_target_days"
            pd.testing.assert_series_equal(base[target], other[target], check_exact=True)
            known = base[target].notna()
            delta = (other.capped_prediction_days-other[target]).abs()-(base.capped_prediction_days-base[target]).abs()
            distance = (other.capped_prediction_days-base.capped_prediction_days).abs()
            self.assertAlmostEqual(row.augmented_minus_base_lower_bound_days, delta.where(known, -distance).mean())
            self.assertAlmostEqual(row.augmented_minus_base_upper_bound_days, delta.where(known, distance).mean())

    def test_missing_scope_horizon_or_duplicate_keys_are_rejected(self):
        changed = {**self.inputs, "seller_orders": self.inputs["seller_orders"].iloc[1:]}
        with self.assertRaisesRegex(ValueError, "scope"):
            experiment.validate_inputs(changed)
        changed = {**self.inputs, "outcomes": self.inputs["outcomes"].loc[lambda x: x.horizon_days.ne(45)]}
        with self.assertRaisesRegex(ValueError, "horizon"):
            experiment.validate_inputs(changed)
        changed = {**self.inputs, "history": pd.concat([self.inputs["history"], self.inputs["history"].iloc[:1]], ignore_index=True)}
        with self.assertRaises(ValueError):
            experiment.validate_inputs(changed)

    def test_every_saved_csv_roundtrips_exactly(self):
        self.assertEqual(set(self.manifest["schemas"]), experiment.TABLES)
        self.assertEqual(len(list(self.output.iterdir())), 20)
        for name, expected in self.frames.items():
            actual = experiment.shared.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)

    def test_existing_output_and_changed_protocol_are_refused(self):
        with self.assertRaises(FileExistsError):
            experiment.write_experiment(self.frames, self.bundle, self.summary, self.provenance, self.output)
        changed = deepcopy(self.provenance)
        changed["code_hashes"][str(Path(experiment.__file__).relative_to(ROOT))] = "changed"
        with self.assertRaisesRegex(ValueError, "code/protocol"):
            experiment.verify_sources(changed)

    def test_tampered_copy_fails_hash_verification(self):
        target = Path(self.temporary.name)/"tampered"
        shutil.copytree(self.output, target)
        with (target/"summary.json").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            experiment.load_snapshot(target)

    def test_failed_model_publication_does_not_leave_partial_snapshot(self):
        target = Path(self.temporary.name)/"incomplete"
        with patch.object(joblib, "dump", side_effect=OSError("Publication failure")):
            with self.assertRaisesRegex(OSError, "Publication failure"):
                experiment.write_experiment(self.frames, self.bundle, self.summary, self.provenance, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(".seller-ablation-*")))

    def test_no_unsupported_scientific_claims_or_labels(self):
        for key in ["previous_models_changed", "previous_thresholds_changed", "imputation_performed", "synthetic_data_used",
                    "human_labels_created", "test_scored", "anomaly_accuracy_computed", "real_time_validated",
                    "operational_winner_selected", "causal_seller_effect_proven", "independent_new_data_evaluation"]:
            self.assertFalse(self.summary[key], key)
        self.assertTrue(self.summary["baseline_refitted_on_identical_subset"])
        self.assertEqual(self.bundle["protocol"], experiment.protocol())


if __name__ == "__main__":
    unittest.main()
