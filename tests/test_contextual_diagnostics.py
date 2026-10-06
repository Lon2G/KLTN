"""Diagnostic invariants checked on actual saved Olist orders and predictions."""

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
import diagnose_contextual_delivery as diagnostics


class ContextualDiagnosticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading allowed")), \
                patch.object(diagnostics.experiment, "fit_entry", side_effect=AssertionError("No refitting allowed")), \
                patch.object(diagnostics.experiment, "calibrate_entry", side_effect=AssertionError("No recalibration allowed")):
            cls.ledger, cls.predicted, cls.provenance = diagnostics.load_inputs()
            cls.frames, cls.summary = diagnostics.build_diagnostics(cls.ledger, cls.predicted)
        cls.cases = cls.frames["analyst_case_diagnostics.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"diagnostics"
        cls.saved_summary = diagnostics.write_diagnostics(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.manifest = diagnostics.load_snapshot(cls.output)

    def test_actual_populations_and_all_candidates_retained(self):
        self.assertEqual(self.summary["development_orders_retained_in_parent"], 7333)
        self.assertEqual(self.summary["validation_purchase_orders_audited"], 1853)
        self.assertEqual(self.summary["paired_validation_orders"], 1472)
        self.assertEqual(self.summary["fit_support_reference_orders"], 3039)
        self.assertEqual(self.summary["paired_candidates_retained"], 11)
        self.assertEqual(self.saved_summary["reserved_test_orders_excluded"], 1691)
        self.assertEqual(len(self.cases), len(self.predicted))
        self.assertEqual(set(self.cases.model), set(self.predicted.model))

    def test_no_test_features_or_manual_review_sessions_read(self):
        for path in self.read_paths:
            self.assertFalse("manual_review" in path or "test_timing" in path or "model_bundle" in path, path)
        self.assertFalse(self.ledger.split.eq("test").any())
        for key in ["training_performed", "thresholds_changed", "model_bundle_deserialized", "test_scored",
                    "human_labels_created", "human_review_started", "anomaly_accuracy_computed",
                    "causal_explanation_proven", "synthetic_data_used"]:
            self.assertFalse(self.summary[key], key)

    def test_all_saved_prediction_values_preserved_exactly(self):
        keys = ["model", "inner_role", "order_id"]
        expected = self.predicted.sort_values(keys).reset_index(drop=True)
        actual = self.cases.sort_values(keys).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual[expected.columns], expected, check_exact=True)
        self.assertTrue(self.cases.audience.eq("analyst_only_not_blinded_review").all())

    def test_actual_target_and_error_arithmetic(self):
        case = self.cases
        actual = (case.order_delivered_customer_date-case.order_delivered_carrier_date).dt.total_seconds()/86400
        np.testing.assert_allclose(case[diagnostics.TARGET], actual, rtol=1e-13)
        np.testing.assert_allclose(case.signed_error_days, actual-case.calibrated_median_days, rtol=1e-12, atol=1e-12)
        np.testing.assert_array_equal(case.absolute_error_days, case.signed_error_days.abs())
        self.assertTrue(case.order_delivered_customer_date.lt(case.event_time_cutoff_exclusive).all())

    def test_fit_references_use_only_paired_fit_orders(self):
        fit = self.ledger.loc[self.ledger.inner_role.eq("fit") & self.ledger.paired_eligible]
        for row in self.frames["fit_numeric_references.csv"].itertuples(index=False):
            values = fit[row.feature].to_numpy(dtype=float)
            self.assertEqual(row.fit_orders, len(fit))
            np.testing.assert_array_equal([row.fit_min, row.fit_q01, row.fit_median, row.fit_q99, row.fit_max],
                                          np.quantile(values, [0, .01, .5, .99, 1]))

    def test_removing_validation_does_not_change_fit_reference(self):
        subset = self.ledger.loc[self.ledger.split.ne("validation")]
        _, reference, _ = diagnostics.fit_support(subset)
        pd.testing.assert_frame_equal(reference, self.frames["fit_numeric_references.csv"], check_exact=True)

    def test_categorical_support_reconciles_with_actual_fit_counts(self):
        fit = self.ledger.loc[self.ledger.inner_role.eq("fit") & self.ledger.paired_eligible].copy()
        fit["state_distance_group"] = diagnostics.state_distance_keys(fit)
        actual = self.frames["case_fit_support.csv"]
        held = self.ledger.set_index("order_id").loc[actual.order_id].reset_index()
        held["state_distance_group"] = diagnostics.state_distance_keys(held)
        for column in ["customer_state", "seller_states_json", "distance_band", "state_distance_group"]:
            expected = held[column].map(fit[column].value_counts()).fillna(0).astype(int)
            np.testing.assert_array_equal(actual[column+"_fit_orders"], expected)

    def test_missing_distance_is_unavailable_not_unseen_zero_distance(self):
        support = self.frames["case_fit_support.csv"]
        absent = support.loc[~support.distance_context_available]
        self.assertFalse(absent.empty)
        self.assertTrue(absent.fit_support_group.eq("distance_context_unavailable").all())
        ledger = self.ledger.set_index("order_id").loc[absent.order_id]
        self.assertTrue(ledger[diagnostics.experiment.DISTANCES].isna().all().all())

    def test_monthly_metrics_and_top_error_concentration_reconcile(self):
        for row in self.frames["monthly_model_diagnostics.csv"].itertuples(index=False):
            group = self.cases.loc[self.cases.model.eq(row.model) & self.cases.inner_role.eq(row.role)
                                   & self.cases.purchase_month.eq(row.purchase_month)]
            self.assertEqual(row.orders, len(group))
            self.assertAlmostEqual(row.median_mae_days, group.absolute_error_days.mean())
            self.assertAlmostEqual(row.mean_signed_error_days, group.signed_error_days.mean())
            self.assertAlmostEqual(row.coverage, group.covered_by_interval.mean())
            self.assertEqual(row.top_error_orders, int(np.ceil(len(group)/10)))
            expected = group.absolute_error_days.nlargest(row.top_error_orders).sum()/group.absolute_error_days.sum()
            self.assertAlmostEqual(row.top_10pct_share_of_absolute_error, expected)

    def test_all_feature_pairs_compare_identical_order_ids(self):
        effects = self.frames["paired_geographic_effects.csv"]
        all_rows = effects.loc[effects.dimension.eq("all_validation")]
        self.assertEqual(len(all_rows), 4)
        self.assertTrue(all_rows.orders.eq(1472).all())
        for row in effects.itertuples(index=False):
            self.assertEqual(row.orders, row.orders_with_lower_absolute_error+row.orders_with_higher_absolute_error+row.orders_with_equal_absolute_error)
            self.assertAlmostEqual(row.geographic_minus_no_distance_mae_days, row.geographic_mae_days-row.no_distance_mae_days)

    def test_composition_identity_and_exact_common_group_counts(self):
        summary = self.frames["composition_summary.csv"]
        details = self.frames["composition_details.csv"]
        self.assertEqual(len(summary), 11*3*3)
        for row in summary.itertuples(index=False):
            group = details.loc[details.model.eq(row.model) & details.dimension.eq(row.dimension) & details.quantity.eq(row.quantity)]
            self.assertEqual(row.common_groups, len(group))
            if group.empty:
                self.assertTrue(np.isnan(row.gap))
                continue
            self.assertTrue(group.period_A_orders.ge(20).all() and group.period_B_orders.ge(20).all())
            self.assertEqual(row.period_A_kept_orders, group.period_A_orders.sum())
            self.assertEqual(row.period_B_kept_orders, group.period_B_orders.sum())
            self.assertEqual(row.period_A_total_orders, 617)
            self.assertEqual(row.period_B_total_orders, 855)
            self.assertAlmostEqual(group.weight_A.sum(), 1)
            self.assertAlmostEqual(group.weight_B.sum(), 1)
            self.assertAlmostEqual(row.gap, (group.weight_A*group.mean_A).sum()-(group.weight_B*group.mean_B).sum())
            self.assertAlmostEqual(row.gap, row.composition_component+row.within_group_component)
            self.assertLessEqual(row.period_A_retained_fraction, 1)
            self.assertLessEqual(row.period_B_retained_fraction, 1)

    def test_composition_is_stable_under_input_row_order(self):
        details, summary = diagnostics.decompose(self.cases.iloc[::-1])
        pd.testing.assert_frame_equal(details, self.frames["composition_details.csv"], check_exact=False, rtol=1e-12, atol=1e-12)
        pd.testing.assert_frame_equal(summary, self.frames["composition_summary.csv"], check_exact=False, rtol=1e-12, atol=1e-12)

    def test_observation_coverage_includes_all_validation_orders(self):
        coverage = self.frames["observation_coverage.csv"].set_index("purchase_month")
        self.assertEqual(coverage.all_orders.to_dict(), {"2018-03": 660, "2018-04": 598, "2018-05": 595})
        self.assertEqual(coverage.completion_not_before_cutoff_orders.to_dict(), {"2018-03": 0, "2018-04": 10, "2018-05": 238})
        self.assertTrue((coverage.paired_scored_orders+coverage.fallback_scored_orders+coverage.timing_ineligible_orders).eq(coverage.all_orders).all())
        reasons = self.frames["timing_exclusion_reasons.csv"]
        self.assertEqual(reasons.orders.sum(), 1853)

    def test_followup_keeps_every_order_once_for_every_horizon(self):
        rows = self.frames["followup_case_audit.csv"]
        expected = set(self.ledger.loc[self.ledger.split.eq("validation"), "order_id"])
        self.assertEqual(len(rows), 1853*3)
        self.assertFalse(rows.duplicated(["order_id", "horizon_days"]).any())
        for _, group in rows.groupby("horizon_days"):
            self.assertEqual(set(group.order_id), expected)

    def test_post_cutoff_timestamps_are_masked_before_observation_audit(self):
        rows = self.frames["followup_case_audit.csv"]
        indexed = self.ledger.set_index("order_id").loc[rows.order_id].reset_index()
        for original, visible in [("order_delivered_carrier_date", "visible_carrier_timestamp"),
                                  ("order_delivered_customer_date", "visible_delivery_timestamp")]:
            expected = indexed[original].where(indexed[original].lt(indexed.event_time_cutoff_exclusive))
            pd.testing.assert_series_equal(rows[visible], expected, check_names=False)
        self.assertTrue(rows.loc[rows.visible_delivery_timestamp.isna(), "observed_duration_days"].isna().all())

    def test_maturity_is_independent_of_early_delivery_outcome(self):
        rows = self.frames["followup_case_audit.csv"]
        valid = rows.available_followup_days.notna()
        expected = valid & rows.available_followup_days.gt(rows.horizon_days)
        np.testing.assert_array_equal(rows.full_followup_window, expected)
        immature_but_delivered = rows.loc[~rows.full_followup_window & rows.observed_duration_days.notna()]
        self.assertFalse(immature_but_delivered.empty)
        self.assertTrue(immature_but_delivered.delivery_recorded_within_horizon.isna().all())

    def test_fixed_horizon_states_use_observed_durations_without_imputation(self):
        rows = self.frames["followup_case_audit.csv"]
        mature = rows.loc[rows.full_followup_window]
        delivered = mature.visible_delivery_timestamp.notna()
        expected = delivered & mature.observed_duration_days.le(mature.horizon_days)
        np.testing.assert_array_equal(mature.delivery_recorded_within_horizon, expected)
        missing = mature.loc[~delivered]
        self.assertTrue(missing.observed_duration_days.isna().all())
        self.assertTrue(missing.followup_state.eq("no_delivery_recorded_before_cutoff").all())
        self.assertTrue(rows.loc[~rows.full_followup_window, "delivery_recorded_within_horizon"].isna().all())

    def test_followup_summary_denominators_and_unavailable_rates(self):
        rows = self.frames["followup_case_audit.csv"]
        for row in self.frames["followup_summary.csv"].itertuples(index=False):
            group = rows.loc[rows.horizon_days.eq(row.horizon_days) & rows.purchase_month.eq(row.purchase_month)]
            self.assertEqual(row.all_orders, len(group))
            self.assertEqual(row.mature_stage_orders, group.full_followup_window.sum())
            self.assertEqual(row.mature_stage_orders, row.delivery_recorded_within_horizon_orders
                             + row.recorded_after_horizon_before_cutoff_orders+row.no_delivery_recorded_before_cutoff_orders)
            self.assertEqual(row.all_orders, row.mature_stage_orders+row.insufficient_followup_orders
                             +row.carrier_not_recorded_before_cutoff_orders+row.invalid_visible_stage_chronology_orders)
            if row.mature_stage_orders:
                self.assertAlmostEqual(row.delivery_recorded_within_horizon_fraction, row.delivery_recorded_within_horizon_orders/row.mature_stage_orders)
            else:
                self.assertTrue(np.isnan(row.delivery_recorded_within_horizon_fraction))

    def test_prediction_scope_duplicates_and_missing_role_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            diagnostics.validate_inputs(self.ledger, pd.concat([self.predicted, self.predicted.iloc[:1]], ignore_index=True))
        missing = self.predicted.loc[~(self.predicted.model.eq(diagnostics.FOCAL) & self.predicted.inner_role.eq("validation"))]
        with self.assertRaisesRegex(ValueError, "both held-out roles"):
            diagnostics.validate_inputs(self.ledger, missing)
        fallback = self.predicted.loc[~(self.predicted.model.eq("fallback_global_quantile") & self.predicted.inner_role.eq("validation"))]
        with self.assertRaisesRegex(ValueError, "fallback role coverage"):
            diagnostics.validate_inputs(self.ledger, fallback)
        with self.assertRaisesRegex(ValueError, "outside development"):
            diagnostics.validate_inputs(self.ledger.iloc[:100], self.predicted)

    def test_saved_tables_exactly_roundtrip_and_sources_verify(self):
        for name, expected in self.frames.items():
            actual = diagnostics.experiment.geo.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        diagnostics.verify_sources(self.provenance)

    def test_overwrite_and_modified_artifact_fail(self):
        with self.assertRaises(FileExistsError):
            diagnostics.write_diagnostics(self.frames, self.summary, self.provenance, self.output)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"copy"
            shutil.copytree(self.output, target)
            (target/"README.md").write_text("Temporary integrity-test modification\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                diagnostics.load_snapshot(target)

    def test_failed_verification_does_not_publish_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"not_published"
            with patch.object(diagnostics, "verify_sources", side_effect=[None, ValueError("Source changed")]):
                with self.assertRaisesRegex(ValueError, "Source changed"):
                    diagnostics.write_diagnostics(self.frames, self.summary, self.provenance, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_changed_code_provenance_rejected(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code or protocol changed"):
            diagnostics.verify_sources(changed)


if __name__ == "__main__":
    unittest.main()
