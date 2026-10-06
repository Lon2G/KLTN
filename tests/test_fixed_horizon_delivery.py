"""Fixed-window evaluation invariants using actual saved Olist orders only."""

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
import evaluate_fixed_horizon_delivery as evaluation


class FixedHorizonDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(evaluation.experiment, "fit_entry", side_effect=AssertionError("Refitting forbidden")), \
                patch.object(evaluation.experiment, "calibrate_entry", side_effect=AssertionError("Recalibration forbidden")):
            cls.inputs, cls.provenance = evaluation.load_inputs()
            before = joblib.hash(cls.inputs["bundle"])
            cls.frames, cls.summary = evaluation.run_evaluation(cls.inputs)
            cls.bundle_unchanged = before == joblib.hash(cls.inputs["bundle"])
        cls.frame = cls.frames["prediction_inputs.csv"]
        cls.forecasts = cls.frames["frozen_predictions.csv"]
        cls.outcomes = cls.frames["window_outcomes.csv"]
        cls.scores = cls.frames["horizon_scores.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"fixed_horizon"
        cls.saved_summary = evaluation.write_evaluation(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.manifest = evaluation.load_snapshot(cls.output)

    def test_actual_population_and_all_horizons_retained(self):
        expected = set(self.inputs["ledger"].loc[lambda x: x.split.eq("validation"), "order_id"])
        self.assertEqual(len(expected), 1853)
        self.assertEqual(set(self.frame.order_id), expected)
        self.assertEqual(len(self.outcomes), 5559)
        self.assertFalse(self.outcomes.duplicated(["order_id", "horizon_days"]).any())
        for _, group in self.outcomes.groupby("horizon_days"):
            self.assertEqual(set(group.order_id), expected)
        self.assertEqual(self.saved_summary["reserved_test_orders_excluded"], 1691)

    def test_no_test_features_or_manual_labels_read(self):
        for path in self.read_paths:
            self.assertFalse("manual_review" in path or "test_timing" in path, path)
        self.assertFalse(self.inputs["ledger"].split.eq("test").any())
        for key in ["training_performed", "thresholds_changed", "model_state_changed", "missing_delivery_imputed",
                    "synthetic_data_used", "human_labels_created", "test_scored", "operational_winner_selected",
                    "anomaly_accuracy_computed", "real_time_validated"]:
            self.assertFalse(self.summary[key], key)

    def test_predictors_exclude_delivery_outcome_and_final_status(self):
        forbidden = {"order_delivered_customer_date", "order_status", evaluation.experiment.TARGET,
                     "calendar_late_vs_promise", "review_score", "label"}
        self.assertFalse(forbidden.intersection(self.frame.columns))
        for entry in self.inputs["bundle"]["bank"].values():
            self.assertFalse(forbidden.intersection(entry.get("numeric", [])+entry.get("categorical", [])))

    def test_precursor_values_match_actual_timestamps(self):
        for name, start, end in [("purchased_to_approved_days", "order_purchase_timestamp", "order_approved_at"),
                                 ("approved_to_carrier_days", "order_approved_at", "order_delivered_carrier_date")]:
            actual = (self.frame[end]-self.frame[start]).dt.total_seconds()/86400
            known = actual.notna() & self.frame[name].notna()
            np.testing.assert_allclose(self.frame.loc[known, name], actual.loc[known], rtol=1e-12, atol=1e-12)
        ready = self.frame.loc[self.frame.context_ready]
        self.assertTrue(ready.order_approved_at.ge(ready.order_purchase_timestamp).all())
        self.assertTrue(ready.order_approved_at.le(ready.order_delivered_carrier_date).all())
        self.assertTrue(ready.order_delivered_carrier_date.lt(ready.event_time_cutoff_exclusive).all())
        values = ready[evaluation.experiment.NUMERIC].to_numpy(dtype=float)
        self.assertTrue((np.isfinite(values) & (values >= 0)).all())

    def test_forecast_routes_retain_unavailable_and_fallback_orders(self):
        self.assertEqual(self.frame.forecast_route.value_counts().to_dict(), {
            "paired_geographic_context": 1687, "no_distance_fallback": 64,
            "stage_not_ready": 57, "global_fallback_only": 45})
        self.assertEqual(set(self.forecasts.order_id), set(self.frame.loc[self.frame.stage_ready, "order_id"]))
        self.assertEqual(self.forecasts.order_id.nunique(), 1796)
        self.assertTrue(self.forecasts[evaluation.FORECAST_COLUMNS].notna().all().all())

    def test_extended_predictions_are_real_previously_unscored_orders(self):
        added = self.forecasts.loc[~self.forecasts.formerly_scored_validation]
        expected = self.frame.loc[self.frame.stage_ready & ~self.frame.formerly_scored_validation]
        self.assertEqual(added.order_id.nunique(), 273)
        self.assertEqual(set(added.order_id), set(expected.order_id))
        self.assertTrue(set(added.order_id).issubset(self.inputs["ledger"].order_id))
        self.assertTrue(expected.inner_role.ne("validation").all())

    def test_all_paired_models_use_identical_actual_orders(self):
        expected = set(self.frame.loc[self.frame.geographic_context_ready, "order_id"])
        for name in evaluation.PAIRED_NAMES:
            rows = self.forecasts.loc[self.forecasts.model.eq(name)]
            self.assertEqual(set(rows.order_id), expected)
            self.assertFalse(rows.order_id.duplicated().any())
        geographic = self.frame.loc[self.frame.geographic_context_ready]
        self.assertTrue(geographic.all_pairs_quality_eligible.all())
        self.assertTrue(np.isfinite(geographic[evaluation.experiment.DISTANCES].to_numpy(dtype=float)).all())

    def test_fallback_denominators_are_explicit_and_different(self):
        fallback = self.frame.stage_ready & ~self.frame.geographic_context_ready
        global_rows = self.forecasts.loc[self.forecasts.model.eq("fallback_global_quantile")]
        context_rows = self.forecasts.loc[self.forecasts.model.eq("fallback_no_distance_d2_l30")]
        self.assertEqual(set(global_rows.order_id), set(self.frame.loc[fallback, "order_id"]))
        self.assertEqual(set(context_rows.order_id), set(self.frame.loc[fallback & self.frame.context_ready, "order_id"]))
        self.assertEqual(len(global_rows), 109)
        self.assertEqual(len(context_rows), 64)
        self.assertTrue(global_rows.population.eq("fallback_all_stage_ready").all())
        self.assertTrue(context_rows.population.eq("fallback_numeric_context_ready").all())

    def test_all_old_validation_forecasts_replay_exactly(self):
        audit = evaluation.verify_replay(self.forecasts, self.inputs["predicted"])
        self.assertEqual(audit.replayed_validation_orders.sum(), 16294)
        self.assertTrue(audit.max_absolute_prediction_difference.eq(0).all())
        self.assertTrue(audit.all_predictions_identical.all())
        self.assertEqual(len(audit), 13)
        missing = self.forecasts.loc[self.forecasts.model.ne(evaluation.diagnostics.FOCAL)]
        with self.assertRaisesRegex(ValueError, "Previously scored"):
            evaluation.verify_replay(missing, self.inputs["predicted"])

    def test_model_state_and_calibration_unchanged(self):
        self.assertTrue(self.bundle_unchanged)
        audit = self.frames["model_state_audit.csv"].set_index("model")
        self.assertFalse(audit.refitted.any() or audit.recalibrated.any())
        original = self.inputs["adjustments"].set_index("model")
        for name in audit.index:
            self.assertEqual(audit.at[name, "fit_orders_unchanged"], original.at[name, "fit_orders"])
            self.assertEqual(audit.at[name, "calibration_orders_unchanged"], original.at[name, "calibration_orders"])
            self.assertEqual(audit.at[name, "lower_shift_days_unchanged"], original.at[name, "lower_shift_days"])
            self.assertEqual(audit.at[name, "upper_shift_days_unchanged"], original.at[name, "upper_shift_days"])

    def test_identity_runtime_population_and_offset_guards(self):
        original = self.inputs["bundle"]

        def validate(bundle, manifest=None):
            evaluation.validate_bundle(bundle, self.inputs["manifest"] if manifest is None else manifest,
                                       self.inputs["ledger"], self.inputs["adjustments"], self.inputs["plan"])

        changed = deepcopy(original)
        changed["identity"]["marketplace_id"] = "different_marketplace"
        with self.assertRaisesRegex(ValueError, "identity/protocol"):
            validate(changed)
        runtime = deepcopy(self.inputs["manifest"])
        runtime["runtime"]["scikit_learn"] = "different_runtime"
        with self.assertRaisesRegex(ValueError, "runtime"):
            validate(original, runtime)
        changed = deepcopy(original)
        changed["bank"][evaluation.diagnostics.FOCAL]["fit_ids"].pop()
        with self.assertRaisesRegex(ValueError, "population"):
            validate(changed)
        changed = deepcopy(original)
        changed["bank"][evaluation.diagnostics.FOCAL]["lower_shift"] = 0
        with self.assertRaisesRegex(ValueError, "offsets"):
            validate(changed)

    def test_forecasting_rejects_other_marketplace_and_training_scope(self):
        other = {**evaluation.geo.business.IDENTITY, "marketplace_id": "different_marketplace"}
        with self.assertRaisesRegex(ValueError, "marketplace/category"):
            evaluation.score_frozen(self.inputs["bundle"], self.frame, other)
        training = self.inputs["ledger"].loc[lambda x: x.split.eq("train")]
        with self.assertRaisesRegex(ValueError, "development-validation"):
            evaluation.score_frozen(self.inputs["bundle"], training, evaluation.geo.business.IDENTITY)

    def test_maturity_uses_strict_cutoff_independent_of_fast_delivery(self):
        expected = self.outcomes.available_followup_days.notna() & self.outcomes.horizon_endpoint.lt(self.outcomes.event_time_cutoff_exclusive)
        np.testing.assert_array_equal(self.outcomes.full_followup_window, expected)
        prior = self.inputs["followup"]
        immature_delivered = prior.loc[~prior.full_followup_window & prior.observed_duration_days.notna()]
        self.assertFalse(immature_delivered.empty)
        selected = self.outcomes.set_index(["order_id", "horizon_days"]).loc[
            pd.MultiIndex.from_frame(immature_delivered[["order_id", "horizon_days"]])]
        for policy in evaluation.POLICIES:
            self.assertTrue(selected[policy+"_target_days"].isna().all())

    def test_primary_has_no_post_horizon_delivery_evidence(self):
        known = self.outcomes.horizon_only_target_days.notna()
        rows = self.outcomes.loc[known]
        self.assertTrue(rows.full_followup_window.all())
        self.assertTrue(rows.horizon_visible_delivery_timestamp.le(rows.horizon_endpoint).all())
        self.assertTrue(rows.horizon_visible_delivery_timestamp.lt(rows.event_time_cutoff_exclusive).all())
        duration = (rows.horizon_visible_delivery_timestamp-rows.visible_carrier_timestamp).dt.total_seconds()/86400
        np.testing.assert_allclose(rows.horizon_only_target_days, duration)
        after = self.outcomes.loc[self.outcomes.followup_state.eq("delivery_recorded_after_horizon_before_cutoff")]
        self.assertFalse(after.empty)
        self.assertTrue(after.horizon_only_target_days.isna().all())
        self.assertTrue(after.horizon_visible_delivery_timestamp.isna().all())

    def test_sensitivity_only_caps_actual_pre_cutoff_deliveries(self):
        source = self.inputs["followup"].set_index(["order_id", "horizon_days"])
        rows = self.outcomes.set_index(["order_id", "horizon_days"])
        expected = np.minimum(source.observed_duration_days, source.index.get_level_values("horizon_days"))
        expected = expected.where(source.full_followup_window & source.observed_duration_days.notna())
        pd.testing.assert_series_equal(rows.cutoff_verified_sensitivity_target_days, expected, check_names=False)
        absent = source.visible_delivery_timestamp.isna()
        self.assertTrue(rows.loc[absent, "cutoff_verified_sensitivity_target_days"].isna().all())

    def test_unknown_targets_and_errors_are_never_imputed(self):
        for policy in evaluation.POLICIES:
            unknown = self.scores.loc[~self.scores[policy+"_outcome_known"]]
            self.assertFalse(unknown.empty)
            self.assertTrue(unknown[policy+"_target_days"].isna().all())
            self.assertTrue(unknown[policy+"_absolute_error_days"].isna().all())
            self.assertTrue(unknown[policy+"_error_lower_bound_days"].eq(0).all())
            expected = np.maximum(unknown.capped_prediction_days, unknown.horizon_days-unknown.capped_prediction_days)
            np.testing.assert_array_equal(unknown[policy+"_error_upper_bound_days"], expected)

    def test_known_capped_error_and_bounds_are_exact(self):
        scores = self.scores
        np.testing.assert_array_equal(scores.capped_prediction_days, np.minimum(scores.calibrated_median_days, scores.horizon_days))
        self.assertTrue(scores.capped_prediction_days.between(0, scores.horizon_days).all())
        for policy in evaluation.POLICIES:
            known = scores.loc[scores[policy+"_outcome_known"]]
            error = (known[policy+"_target_days"]-known.capped_prediction_days).abs()
            for field in ["absolute_error_days", "error_lower_bound_days", "error_upper_bound_days"]:
                np.testing.assert_array_equal(known[policy+"_"+field], error)
            lower = scores[policy+"_error_lower_bound_days"]
            upper = scores[policy+"_error_upper_bound_days"]
            self.assertTrue((lower.ge(0) & upper.ge(lower) & upper.le(scores.horizon_days)).all())

    def test_monthly_metrics_count_unresolved_in_total_denominator(self):
        metrics = self.frames["window_metrics.csv"]
        self.assertEqual(len(metrics), 13*3*3*2)
        for row in metrics.itertuples(index=False):
            frame = self.scores.loc[self.scores.model.eq(row.model) & self.scores.horizon_days.eq(row.horizon_days)
                                    & self.scores.purchase_month.eq(row.purchase_month)]
            self.assertEqual(row.scored_mature_orders, len(frame))
            self.assertEqual(row.known_outcome_orders+row.unresolved_outcome_orders, len(frame))
            if frame.empty:
                self.assertTrue(np.isnan(row.all_scored_mae_lower_bound_days))
                continue
            prefix = row.evidence_policy+"_"
            self.assertEqual(row.known_outcome_orders, frame[prefix+"outcome_known"].sum())
            self.assertAlmostEqual(row.all_scored_mae_lower_bound_days, frame[prefix+"error_lower_bound_days"].sum()/len(frame))
            self.assertAlmostEqual(row.all_scored_mae_upper_bound_days, frame[prefix+"error_upper_bound_days"].sum()/len(frame))
            self.assertAlmostEqual(row.observed_only_capped_mae_days, frame[prefix+"absolute_error_days"].mean())

    def test_empty_may_long_windows_are_missing_not_zero_error(self):
        rows = self.frames["window_metrics.csv"].loc[lambda x: x.purchase_month.eq("2018-05") & x.horizon_days.isin([30, 45])]
        self.assertEqual(len(rows), 13*2*2)
        self.assertTrue(rows.scored_mature_orders.eq(0).all())
        self.assertTrue(rows[["known_outcome_fraction", "observed_only_capped_mae_days", "all_scored_mae_lower_bound_days",
                              "all_scored_mae_upper_bound_days"]].isna().all().all())
        self.assertTrue(rows.status.eq("no_mature_cases").all())

    def test_coverage_conserves_all_orders_and_evidence_states(self):
        for row in self.frames["window_coverage.csv"].itertuples(index=False):
            self.assertEqual(row.validation_purchases, row.mature_stage_orders+row.insufficient_followup_orders
                             +row.invalid_visible_stage_orders+row.carrier_not_recorded_before_cutoff_orders)
            self.assertEqual(row.mature_stage_orders, row.paired_context_orders+row.fallback_stage_orders)
            for policy in evaluation.POLICIES:
                self.assertEqual(row.mature_stage_orders, getattr(row, policy+"_known_stage_outcomes")
                                 +getattr(row, policy+"_unresolved_stage_outcomes"))

    def test_paired_bounds_use_common_outcomes_and_exact_mathematics(self):
        comparisons = self.frames["paired_feature_comparisons.csv"]
        self.assertEqual(len(comparisons), 4*3*3*2)
        self.assertFalse(comparisons.is_confidence_interval.any() or comparisons.winner_selected.any())
        for row in comparisons.itertuples(index=False):
            scope = self.scores.horizon_days.eq(row.horizon_days) & self.scores.purchase_month.eq(row.purchase_month)
            geographic = self.scores.loc[scope & self.scores.model.eq(f"gbr_geographic_d{row.depth}_l{row.leaf}")].set_index("order_id").sort_index()
            baseline = self.scores.loc[scope & self.scores.model.eq(f"gbr_no_distance_d{row.depth}_l{row.leaf}")].set_index("order_id").sort_index()
            self.assertTrue(geographic.index.equals(baseline.index))
            if geographic.empty:
                self.assertEqual(row.logical_bound_direction, "no_mature_cases")
                continue
            p = geographic.capped_prediction_days
            q = baseline.capped_prediction_days
            target = geographic[row.evidence_policy+"_target_days"]
            pd.testing.assert_series_equal(target, baseline[row.evidence_policy+"_target_days"])
            # Endpoint differences give exact attainable bounds for a common unknown target in [0, H].
            at_zero = p-q
            at_horizon = (row.horizon_days-p)-(row.horizon_days-q)
            delta = (target-p).abs()-(target-q).abs()
            lower = delta.where(target.notna(), np.minimum(at_zero, at_horizon)).mean()
            upper = delta.where(target.notna(), np.maximum(at_zero, at_horizon)).mean()
            self.assertAlmostEqual(row.all_paired_difference_lower_bound_days, lower)
            self.assertAlmostEqual(row.all_paired_difference_upper_bound_days, upper)
            direction = "negative_only" if upper < 0 else "positive_only" if lower > 0 else "overlaps_zero"
            self.assertEqual(row.logical_bound_direction, direction)

    def test_sensitivity_can_only_narrow_primary_missing_outcome_bounds(self):
        self.assertTrue(self.scores.cutoff_verified_sensitivity_error_lower_bound_days.ge(self.scores.horizon_only_error_lower_bound_days).all())
        self.assertTrue(self.scores.cutoff_verified_sensitivity_error_upper_bound_days.le(self.scores.horizon_only_error_upper_bound_days).all())

    def test_duplicate_and_missing_windows_are_rejected(self):
        changed = {**self.inputs, "followup": pd.concat([self.inputs["followup"], self.inputs["followup"].iloc[:1]], ignore_index=True)}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            evaluation.window_outcomes(changed, self.frame)
        changed = {**self.inputs, "followup": self.inputs["followup"].iloc[1:]}
        with self.assertRaisesRegex(ValueError, "Every validation order"):
            evaluation.window_outcomes(changed, self.frame)

    def test_saved_tables_roundtrip_exactly_and_sources_verify(self):
        for name, expected in self.frames.items():
            actual = evaluation.geo.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        evaluation.verify_sources(self.provenance)
        self.assertEqual(len(list(self.output.iterdir())), 13)

    def test_overwrite_and_modified_artifact_fail(self):
        with self.assertRaises(FileExistsError):
            evaluation.write_evaluation(self.frames, self.summary, self.provenance, self.output)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"copy"
            shutil.copytree(self.output, target)
            (target/"README.md").write_text("Temporary integrity-test modification\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                evaluation.load_snapshot(target)

    def test_failed_verification_leaves_no_partial_publication(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"not_published"
            with patch.object(evaluation, "verify_sources", side_effect=[None, ValueError("Source changed")]):
                with self.assertRaisesRegex(ValueError, "Source changed"):
                    evaluation.write_evaluation(self.frames, self.summary, self.provenance, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_changed_code_provenance_is_rejected(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "source code changed"):
            evaluation.verify_sources(changed)


if __name__ == "__main__":
    unittest.main()
