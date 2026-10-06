"""Integration checks on real Olist development records; no fabricated cases."""

from copy import deepcopy
import json
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
import experiment_contextual_delivery as experiment


class ContextualDeliveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read):
            cls.inputs, cls.plan, cls.provenance = experiment.load_inputs()
            cls.frames, cls.bundle, cls.summary = experiment.run_experiment(cls.inputs, cls.plan, lambda x: None)
        cls.ledger = cls.frames["analysis_ledger.csv"]
        cls.predicted = cls.frames["predictions.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"experiment"
        cls.saved_summary = experiment.write_experiment(cls.frames, cls.bundle, cls.summary, cls.provenance, cls.output)
        cls.restored, cls.manifest = experiment.load_snapshot(cls.output)

    def test_real_population_reconciles_without_deletion(self):
        self.assertEqual(len(self.ledger), 7333)
        self.assertEqual(set(self.ledger.order_id), set(self.inputs["features"].order_id))
        self.assertEqual(self.ledger.inner_role.value_counts().to_dict(),
                         {"fit": 3190, "validation": 1523, "calibration": 1412, "process_only": 732, "deferred_at_inner_cutoff": 476})
        self.assertEqual(self.summary["paired_role_counts"], {"fit": 3039, "calibration": 1381, "validation": 1472})
        self.assertEqual(self.summary["quality_eligible_orders"], 7034)
        self.assertEqual(self.summary["quality_withheld_orders"], 299)
        self.assertEqual(self.saved_summary["reserved_test_orders_excluded"], 1691)
        self.assertEqual(self.summary["fitted_quantile_regressors"], 27)

    def test_no_test_features_review_labels_or_old_model_scores_read(self):
        self.assertFalse(any("manual_review" in path or "test_timing" in path or "validation_scores" in path
                             or "validation_flags" in path or "models/" in path for path in self.read_paths))
        self.assertFalse(self.ledger.split.eq("test").any())
        self.assertEqual(set(self.predicted.inner_role), {"calibration", "validation"})

    def test_no_research_claims_or_labels_invented(self):
        for key in ["raw_data_changed", "synthetic_data_used", "test_scored", "human_labels_created",
                    "anomaly_accuracy_computed", "operational_winner_selected", "real_time_validated",
                    "causal_bottlenecks_proven", "geographic_locations_verified"]:
            self.assertFalse(self.summary[key], key)
        self.assertTrue(self.summary["quality_screen_provisional"])

    def test_fit_calibration_validation_disjoint_and_observed_in_time(self):
        for name, entry in self.bundle["bank"].items():
            fit = self.ledger.set_index("order_id").loc[entry["fit_ids"]]
            calibration = self.ledger.set_index("order_id").loc[entry["calibration_ids"]]
            self.assertTrue(fit.inner_role.eq("fit").all(), name)
            self.assertTrue(fit.order_delivered_customer_date.lt(pd.Timestamp(self.plan["fit_end_exclusive"])).all())
            self.assertTrue(calibration.order_purchase_timestamp.ge(pd.Timestamp(self.plan["fit_end_exclusive"])).all())
            self.assertTrue(calibration.order_delivered_customer_date.lt(pd.Timestamp(self.plan["train_end_exclusive"])).all())
            self.assertFalse(set(entry["fit_ids"]) & set(entry["calibration_ids"]))
            self.assertFalse(set(entry["fit_ids"]+entry["calibration_ids"]) & set(self.bundle["validation_ids"]))

    def test_target_exactly_matches_actual_timestamps(self):
        usable = self.ledger.loc[self.ledger.inner_role.isin(experiment.ROLES)]
        actual = (usable.order_delivered_customer_date-usable.order_delivered_carrier_date).dt.total_seconds()/86400
        np.testing.assert_allclose(usable[experiment.TARGET], actual, rtol=1e-13)
        self.assertTrue(actual.ge(0).all())

    def test_quality_does_not_change_source_coordinates_or_representatives(self):
        zips = self.frames["zip_quality.csv"]
        pd.testing.assert_frame_equal(zips[self.inputs["zips"].columns], self.inputs["zips"].sort_values(experiment.geo.ZIP).reset_index(drop=True), check_exact=True)
        evidence = self.frames["outside_envelope_evidence.csv"]
        self.assertEqual(len(evidence), 2)
        source = self.inputs["evidence"].set_index("clean_source_record").loc[evidence.clean_source_record].reset_index()
        pd.testing.assert_frame_equal(evidence[source.columns], source, check_exact=True)
        self.assertTrue(zips.set_index(experiment.geo.ZIP).loc[["57319", "83810"], "zip_quality_eligible"].eq(False).all())

    def test_quality_threshold_formula_and_fit_zip_population(self):
        zips = self.frames["zip_quality.csv"]
        reference = zips.loc[zips.used_for_quality_threshold_fit]
        self.assertEqual(len(reference), 2486)
        fit_ids = set(self.ledger.loc[self.ledger.inner_role.eq("fit"), "order_id"])
        pairs = self.inputs["pairs"].loc[self.inputs["pairs"].order_id.isin(fit_ids)]
        allowed = set(pairs.customer_zip_code_prefix) | set(pairs.seller_zip_code_prefix)
        self.assertTrue(set(reference[experiment.geo.ZIP]).issubset(allowed))
        for row in self.frames["quality_thresholds.csv"].itertuples(index=False):
            q25, q75 = np.quantile(np.log1p(reference[row.diagnostic]), [.25, .75])
            self.assertAlmostEqual(row.threshold_km, np.expm1(q75+3*(q75-q25)), places=11)

    def test_unlinked_validation_zips_cannot_change_quality_thresholds(self):
        fit_ids = set(self.ledger.loc[self.ledger.inner_role.eq("fit"), "order_id"])
        pairs = self.inputs["pairs"].loc[self.inputs["pairs"].order_id.isin(fit_ids)]
        zips = set(pairs.customer_zip_code_prefix) | set(pairs.seller_zip_code_prefix)
        subset = experiment.quality.assess(
            self.inputs["zips"].loc[self.inputs["zips"][experiment.geo.ZIP].isin(zips)],
            self.inputs["evidence"].loc[self.inputs["evidence"][experiment.geo.ZIP].isin(zips)], pairs,
            fit_ids, self.inputs["states"].loc[self.inputs["states"].order_id.isin(fit_ids)])
        pd.testing.assert_frame_equal(subset["quality_thresholds.csv"], self.frames["quality_thresholds.csv"], check_exact=True)

    def test_every_pair_must_pass_no_partial_or_zero_imputation(self):
        pairs = self.frames["pair_quality.csv"]
        orders = self.frames["order_quality.csv"].set_index("order_id")
        pd.testing.assert_series_equal(orders.all_pairs_quality_eligible, pairs.groupby("order_id").pair_quality_eligible.all(), check_names=False)
        self.assertTrue(orders.loc[~orders.all_pairs_quality_eligible, experiment.DISTANCES].isna().all().all())
        usable = pairs.loc[pairs.pair_quality_eligible]
        self.assertTrue(usable.customer_state_matches.all() and usable.seller_state_matches.all())
        self.assertTrue(usable.customer_zip_quality_eligible.all() and usable.seller_zip_quality_eligible.all())

    def test_geographic_distances_retain_original_values_and_item_weights(self):
        actual = self.ledger.loc[self.ledger.paired_eligible].set_index("order_id")
        source = self.inputs["geographic_features"].set_index("order_id").loc[actual.index]
        for new, old in zip(experiment.DISTANCES, experiment.geo.CANDIDATES):
            np.testing.assert_array_equal(actual[new], source[old])

    def test_paired_candidates_have_identical_orders_and_fit_calibration_sets(self):
        paired = self.predicted.loc[self.predicted.population.eq("paired_geography_eligible")]
        self.assertEqual(paired.model.nunique(), 11)
        for role in experiment.ROLES[1:]:
            expected = set(self.ledger.loc[self.ledger.inner_role.eq(role) & self.ledger.paired_eligible, "order_id"])
            for _, frame in paired.loc[paired.inner_role.eq(role)].groupby("model"):
                self.assertEqual(set(frame.order_id), expected)
        for name in paired.model.unique():
            for role, field in [("fit", "fit_ids"), ("calibration", "calibration_ids")]:
                self.assertEqual(set(self.bundle["bank"][name][field]),
                                 set(self.ledger.loc[self.ledger.inner_role.eq(role) & self.ledger.paired_eligible, "order_id"]))

    def test_fallback_report_is_separate_and_all_timing_cases_are_covered(self):
        for role in experiment.ROLES[1:]:
            actual = self.predicted.loc[self.predicted.inner_role.eq(role)]
            expected = self.ledger.loc[self.ledger.inner_role.eq(role)]
            self.assertEqual(set(actual.order_id), set(expected.order_id))
            paired = actual.loc[actual.population.eq("paired_geography_eligible")]
            fallback = actual.loc[actual.population.eq("outside_paired_geography_population")]
            self.assertFalse(set(paired.order_id) & set(fallback.order_id))
        fallback = self.bundle["bank"]["fallback_no_distance_d2_l30"]
        self.assertEqual(fallback["fit_count"], 3190)
        self.assertEqual(len(fallback["calibration_ids"]), 1412)

    def test_predictors_exclude_outcomes_and_encoders_fit_only_on_fit(self):
        forbidden = {experiment.TARGET, "order_id", "order_status", "calendar_late_vs_promise", "order_delivered_customer_date", "review_score"}
        for entry in self.bundle["bank"].values():
            if entry["kind"] != "gbr":
                continue
            columns = entry["numeric"]+entry["categorical"]
            self.assertFalse(set(columns) & forbidden)
            fit = self.ledger.set_index("order_id").loc[entry["fit_ids"]]
            if entry["geographic"]:
                encoder = entry["models"][0].named_steps["encode"].named_transformers_["categorical"]
                for name, categories in zip(entry["categorical"], encoder.categories_):
                    self.assertEqual(set(categories), set(fit[name]))
            else:
                self.assertEqual(columns, experiment.NUMERIC)

    def test_prediction_does_not_need_or_consult_actual_delivery_outcome(self):
        entry = self.bundle["bank"]["gbr_geographic_d2_l30"]
        frame = self.ledger.loc[self.ledger.inner_role.eq("validation") & self.ledger.paired_eligible]
        minimal = frame[["order_id", *entry["numeric"], *entry["categorical"]]]
        np.testing.assert_array_equal(experiment.predict_entry(entry, frame)[1], experiment.predict_entry(entry, minimal)[1])

    def test_training_and_calibration_role_guards(self):
        fit = self.ledger.loc[self.ledger.inner_role.eq("fit") & self.ledger.paired_eligible]
        validation = self.ledger.loc[self.ledger.inner_role.eq("validation") & self.ledger.paired_eligible]
        with self.assertRaisesRegex(ValueError, "Only declared fit"):
            experiment.fit_entry(validation, "global")
        entry = self.bundle["bank"]["global_quantile"]
        with self.assertRaisesRegex(ValueError, "fitted orders"):
            experiment.predict_entry(entry, fit)
        with self.assertRaisesRegex(ValueError, "calibration"):
            experiment.calibrate_entry(deepcopy(entry), validation)

    def test_calibration_shifts_computed_only_from_calibration_residuals(self):
        for entry in self.bundle["bank"].values():
            frame = self.ledger.set_index("order_id").loc[entry["calibration_ids"]].reset_index()
            raw = experiment.predict_entry(entry, frame)[0]
            if entry["kind"] != "legacy":
                y = frame[experiment.TARGET].to_numpy(dtype=float)
                self.assertAlmostEqual(entry["lower_shift"], np.quantile(y-raw[:, 0], .05), places=12)
                self.assertAlmostEqual(entry["upper_shift"], np.quantile(y-raw[:, 2], .95), places=12)

    def test_peer_group_support_and_fallback_are_fit_only(self):
        entry = self.bundle["bank"]["peer_quantile"]
        fit = self.ledger.set_index("order_id").loc[entry["fit_ids"]]
        for key, group in entry["groups"].items():
            actual = fit.loc[fit.distance_band.eq(key[-1])]
            if key[0] == 2:
                actual = actual.loc[actual.customer_state.eq(key[1])]
            self.assertEqual(len(actual), group["count"])
            self.assertGreaterEqual(group["count"], 50)
            np.testing.assert_array_equal(group["values"], np.quantile(actual[experiment.TARGET], experiment.QUANTILES))
        predicted = self.predicted.loc[self.predicted.model.eq("peer_quantile")]
        self.assertTrue(predicted.reference_fit_support.ge(50).all())
        self.assertTrue(set(predicted.reference_route).issubset({"state_distance_band", "distance_band", "global"}))

    def test_intervals_ordered_finite_nonnegative_and_warning_arithmetic(self):
        p = self.predicted
        for view in ["raw", "calibrated"]:
            values = p[[f"{view}_{name}_days" for name in ["lower", "median", "upper"]]].to_numpy(dtype=float)
            self.assertTrue(np.isfinite(values).all())
            self.assertTrue((np.diff(values, axis=1) >= 0).all())
            self.assertTrue((values >= 0).all())
        np.testing.assert_array_equal(p.long_warning, p[experiment.TARGET] > p.calibrated_upper_days)
        np.testing.assert_array_equal(p.short_warning, (p[experiment.TARGET] > 0) & (p[experiment.TARGET] < p.calibrated_lower_days))
        np.testing.assert_allclose(p.excess_above_upper_days, np.maximum(0, p[experiment.TARGET]-p.calibrated_upper_days))

    def test_metrics_independently_recomputed_and_legacy_not_nominal(self):
        for row in self.frames["metrics.csv"].itertuples(index=False):
            p = self.predicted.loc[self.predicted.model.eq(row.model) & self.predicted.inner_role.eq(row.role)]
            y = p[experiment.TARGET].to_numpy(dtype=float)
            lo, med, hi = (p[f"{row.view}_{name}_days"].to_numpy() for name in ["lower", "median", "upper"])
            self.assertEqual(row.orders, len(p))
            self.assertAlmostEqual(row.median_mae_days, np.abs(y-med).mean())
            self.assertAlmostEqual(row.coverage, ((y >= lo) & (y <= hi)).mean())
            if row.model != "legacy_iqr":
                delta = y-hi
                self.assertAlmostEqual(row.q95_pinball, np.maximum(.95*delta, -.05*delta).mean())
                score = (hi-lo)+20*np.maximum(lo-y, 0)+20*np.maximum(y-hi, 0)
                self.assertAlmostEqual(row.interval_score_alpha010, score.mean())
            else:
                self.assertTrue(np.isnan(row.nominal_coverage) and np.isnan(row.q95_pinball) and np.isnan(row.interval_score_alpha010))

    def test_multi_seller_outcomes_not_attributed_to_each_seller(self):
        reports = self.frames["warning_summaries.csv"]
        for (population, name), summary in reports.loc[reports.dimension.eq("single_seller_id")].groupby(["population", "model"]):
            p = self.predicted.loc[self.predicted.population.eq(population) & self.predicted.model.eq(name) & self.predicted.inner_role.eq("validation")]
            self.assertEqual(summary.scored_orders.sum(), p.seller_count.eq(1).sum())
            self.assertTrue(summary.multi_seller_excluded_from_seller_attribution.eq(p.seller_count.gt(1).sum()).all())
            self.assertFalse(summary.group.eq("multi_seller_not_attributed").any())
            self.assertTrue(summary.sufficient_support.eq(summary.scored_orders.ge(30)).all())

    def test_saved_frames_and_model_predictions_roundtrip_exactly(self):
        for name, frame in self.frames.items():
            actual = experiment.geo.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, frame, check_dtype=False, check_exact=True)
        bundle = joblib.load(self.output/"model_bundle.joblib")
        self.assertEqual(bundle["identity"], experiment.geo.business.IDENTITY)
        self.assertEqual(bundle["protocol"], experiment.protocol())

    def test_output_overwrite_and_tampering_are_rejected(self):
        with self.assertRaises(FileExistsError):
            experiment.write_experiment(self.frames, self.bundle, self.summary, self.provenance, self.output)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"copy"
            shutil.copytree(self.output, target)
            (target/"README.md").write_text("Changed temporary report for integrity test\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                experiment.load_snapshot(target)

    def test_changed_code_provenance_stops_publication(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "source code changed"):
            experiment.verify_sources(changed)


if __name__ == "__main__":
    unittest.main()
