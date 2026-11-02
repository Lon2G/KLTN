"""Checks on real frozen Olist observations; fault injections are test-only."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import evaluate_selected_holdout as holdout


class SelectedHoldoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(os.environ.get("KLTN_HOLDOUT_TEST_SNAPSHOT", holdout.DEFAULT_OUTPUT))
        cls.manifest = holdout.review.read_json(cls.directory/"manifest.json")
        holdout.shared.verify_files(cls.directory, cls.manifest["output_hashes"])
        holdout.verify_sources(cls.manifest["provenance"])
        cls.frames = {name: holdout.shared.read_table(cls.directory, name, cls.manifest) for name in holdout.TABLES}
        cls.ledger, cls.stages = cls.frames["order_ledger.csv"], cls.frames["stage_scores.csv"]
        pm = holdout.review.read_json(holdout.product.DEFAULT_OUTPUT/"manifest.json")
        cls.reference = holdout.shared.read_table(holdout.product.DEFAULT_OUTPUT, "train_stage_reference.csv", pm)
        cls.summary = cls.manifest["summary"]

    def test_original_test_not_proposed_80_20_repartition(self):
        partition = pd.read_csv(holdout.COHORT/"cohort_cases.csv", usecols=["order_id", "split"], dtype="string")
        expected = set(partition.loc[partition.split.eq("test"), "order_id"])
        self.assertEqual(len(expected), 1691)
        self.assertEqual(set(self.ledger.order_id), expected)
        self.assertEqual(len(self.ledger), len(expected))
        self.assertFalse(expected & set(self.reference.order_id))
        self.assertTrue(self.ledger.order_purchase_timestamp.ge("2018-06-01").all())
        self.assertTrue(self.ledger.order_purchase_timestamp.lt("2018-09-01").all())

    def test_pretest_exact_development_parity_was_required(self):
        self.assertEqual(self.summary["pre_test_parity"], {"validation_orders": 1739,
            "validation_stage_scores": 5217, "development_geographic_orders": 7333, "exact_parity": True})

    def test_calendar_target_never_assigns_unknown_negative(self):
        orders = self.ledger
        delta = (orders.order_delivered_customer_date.dt.normalize()-orders.order_estimated_delivery_date.dt.normalize()).dt.days
        valid = delta.notna() & ~orders.quality_conflict
        self.assertTrue(orders[holdout.TARGET].loc[~valid].isna().all())
        self.assertTrue(orders[holdout.TARGET].loc[valid].eq(delta.loc[valid].gt(0)).all())
        self.assertEqual(int(valid.sum()), int(orders.performance_eligible.sum()))

    def test_midnight_date_serialization_preserves_datetime(self):
        for name in [*holdout.performance.TIMES, holdout.performance.ESTIMATE]:
            self.assertTrue(pd.api.types.is_datetime64_any_dtype(self.ledger[name]), name)
        self.assertTrue(self.ledger.order_estimated_delivery_date.dt.hour.eq(0).all())

    def test_metrics_only_on_complete_scores_and_valid_outcome(self):
        orders, stages = self.ledger, self.stages
        self.assertTrue(stages.groupby("order_id").size().eq(3).all())
        count = orders.order_id.map(stages.groupby("order_id").delay_score_0_100.count())
        expected = count.eq(3) & orders.performance_eligible
        self.assertTrue(orders.metric_eligible.eq(expected).all())
        self.assertTrue(orders[holdout.SCORE].isna().eq(count.eq(0)).all())
        self.assertTrue(orders.loc[count.eq(0), holdout.CANDIDATE+"_flag"].isna().all())
        self.assertEqual(int(expected.sum()), self.summary["evaluated_orders"])

    def test_score_algebra_from_actual_reference_members(self):
        groups = self.frames["reference_groups.csv"].set_index("reference_id")
        for gid, rows in self.stages.loc[self.stages.assessable].groupby("selected_reference_id"):
            group = groups.loc[gid]
            ids = json.loads(group.order_ids_json)
            values = self.reference.loc[self.reference.stage.eq(group.stage) & self.reference.order_id.isin(ids)].sort_values("order_id").duration_days.to_numpy(float)
            self.assertEqual(len(ids), len(values))
            self.assertGreaterEqual(len(values), 30)
            self.assertEqual(len(ids), len(set(ids)))
            self.assertFalse(set(ids) & set(self.ledger.order_id))
            self.assertEqual(values.mean(), group.mean_days)
            self.assertIn("distance_band", json.loads(group.context_json))
            for row in rows.itertuples(index=False):
                count = int((values <= row.duration_days).sum())
                expected = (100*(20*count-19*len(values))/len(values)
                            if row.duration_days > max(0, values.mean()) and 20*count > 19*len(values) else 0.)
                self.assertEqual(row.delay_score_0_100, expected)
                self.assertEqual(row.difference_from_reference_mean_days, row.duration_days-values.mean())

    def test_order_maximum_and_fixed_threshold(self):
        actual = self.ledger.set_index("order_id")
        maximum = self.stages.groupby("order_id").delay_score_0_100.max()
        np.testing.assert_array_equal(actual[holdout.SCORE], maximum.reindex(actual.index))
        complete = actual.loc[actual.scored_stages.eq(3)]
        self.assertTrue(complete[holdout.CANDIDATE+"_flag"].eq(complete[holdout.SCORE].gt(0)).all())
        selected = holdout.review.read_json(self.directory/"selection.json")
        holdout.require_selection(selected)
        self.assertEqual(selected, holdout.review.read_json(holdout.temporal.DEFAULT_OUTPUT/"selection.json"))

    def test_stage_duration_uses_original_endpoints(self):
        source = self.ledger.set_index("order_id")
        for left, right, stage in holdout.performance.STAGES[:3]:
            rows = self.stages.loc[self.stages.stage.eq(stage)].set_index("order_id")
            original = source.loc[rows.index]
            duration = (original[right]-original[left]).dt.total_seconds()/86400
            np.testing.assert_array_equal(rows.duration_days.to_numpy(float), duration.to_numpy(float))
            pd.testing.assert_series_equal(rows.start_timestamp.reset_index(drop=True), original[left].reset_index(drop=True),
                                           check_names=False, check_exact=True)
            pd.testing.assert_series_equal(rows.end_timestamp.reset_index(drop=True), original[right].reset_index(drop=True),
                                           check_names=False, check_exact=True)

    def test_geography_uses_saved_not_test_fitted_limits(self):
        qm = holdout.review.read_json(holdout.contextual.DEFAULT_OUTPUT/"manifest.json")
        thresholds = holdout.shared.read_table(holdout.contextual.DEFAULT_OUTPUT, "quality_thresholds.csv", qm).set_index("diagnostic")
        zips = self.frames["zip_quality.csv"]
        for diagnostic in holdout.frozen.quality.SPREADS:
            actual = zips["high_"+diagnostic]
            expected = zips[diagnostic].gt(thresholds.loc[diagnostic, "threshold_km"])
            self.assertTrue(actual.eq(expected).all())
        pairs = self.frames["pair_quality.csv"]
        expected = pairs.groupby("order_id").pair_quality_eligible.all()
        actual = self.ledger.set_index("order_id").all_pairs_quality_eligible
        self.assertTrue(actual.eq(expected.reindex(actual.index)).all())

    def test_monthly_confusions_reconcile_and_metrics_are_correct(self):
        periods = self.frames["period_metrics.csv"].set_index("period")
        pooled = periods.loc["all"]
        for col in ["orders", "late", "not_late", "tp", "fp", "fn", "tn", "alerts"]:
            self.assertEqual(periods.drop(index="all")[col].sum(), pooled[col])
        for label in ["TP", "FP", "FN", "TN"]:
            self.assertEqual(int(self.ledger.calendar_proxy_confusion.eq(label).sum()), pooled[label.lower()])
        for period, metric in periods.iterrows():
            cases = self.ledger.loc[self.ledger.metric_eligible]
            if period != "all":
                cases = cases.loc[cases.purchase_month.eq(period)]
            y, predicted = cases[holdout.TARGET].to_numpy(bool), cases[holdout.SCORE].gt(0).to_numpy(bool)
            confusion = [int((y & predicted).sum()), int((~y & predicted).sum()), int((y & ~predicted).sum()), int((~y & ~predicted).sum())]
            self.assertEqual(metric[["tp", "fp", "fn", "tn"]].tolist(), confusion)
            if 2*metric.tp+metric.fp+metric.fn:
                self.assertAlmostEqual(metric.f1_raw, 2*metric.tp/(2*metric.tp+metric.fp+metric.fn), places=14)

    def test_auc_direct_pairwise_ranking(self):
        cases = self.ledger.loc[self.ledger.metric_eligible]
        positive = cases.loc[cases[holdout.TARGET], holdout.SCORE].to_numpy(float)
        negative = cases.loc[~cases[holdout.TARGET], holdout.SCORE].to_numpy(float)
        delta = positive[:, None]-negative[None, :]
        expected = ((delta > 0).sum()+.5*(delta == 0).sum())/delta.size
        actual = self.frames["period_metrics.csv"].set_index("period").loc["all", "roc_auc"]
        self.assertAlmostEqual(actual, expected, places=14)

    def test_single_class_metrics_remain_undefined(self):
        cases = self.ledger.loc[self.ledger.metric_eligible & self.ledger[holdout.TARGET].eq(False)]
        metric = holdout.temporal.metrics(cases, holdout.CANDIDATE)
        for field in ["roc_auc", "average_precision", "recall", "f1_for_selection"]:
            self.assertTrue(pd.isna(metric[field]), field)

    def test_coverage_counts_and_primary_reason_reconcile(self):
        coverage = self.frames["monthly_coverage.csv"].set_index("period")
        for col in ["total_orders", "evaluated_orders", "excluded_orders", "quality_conflicts", "missing_stage_endpoints",
                    "geographic_quality_withheld", "known_late_total", "known_late_excluded", "unknown_or_conflicted_outcome"]:
            self.assertEqual(coverage.drop(index="all")[col].sum(), coverage.loc["all", col])
        self.assertEqual(sum(self.summary["exclusion_reasons"].values()), len(self.ledger))
        self.assertTrue(self.ledger.exclusion_reason.eq("included").eq(self.ledger.metric_eligible).all())

    def test_early_delivery_does_not_erase_stage_warnings(self):
        early = self.ledger.loc[self.ledger.performance_state.eq("early") & self.ledger[holdout.SCORE].gt(0)]
        self.assertGreater(len(early), 0)
        self.assertTrue(early[holdout.CANDIDATE+"_flag"].all())
        self.assertTrue(early.warning_stages_json.map(json.loads).map(bool).all())
        self.assertEqual(len(early), self.summary["early_orders_with_stage_warning"])

    def test_faster_stages_are_evidence_not_forced_by_delivery_status(self):
        for row in self.ledger.itertuples(index=False):
            stages = self.stages.loc[self.stages.order_id.eq(row.order_id)]
            expected = stages.loc[stages.difference_from_reference_mean_days.lt(0), "stage"].tolist()
            self.assertEqual(json.loads(row.faster_stages_json), expected)
            if row.scored_stages == 3 and not expected:
                self.assertEqual(row.faster_stage_evidence_status, "no_observed_stage_below_reference_mean")

    def test_no_fabricated_cutoff_cause_label_or_production_claim(self):
        self.assertTrue(self.ledger.visible_before_exclusive.isna().all())
        self.assertTrue(self.ledger.source_extraction_cutoff_status.eq("unknown_not_inferred").all())
        self.assertTrue(self.ledger.promise_basis_status.eq("unverified_source_method_not_provided").all())
        self.assertIsNone(self.summary["source_extraction_cutoff"])
        for key in ["models_retrained", "thresholds_tuned", "synthetic_data_used", "new_reference_labels", "selection_changed",
                    "production_activated", "full_cohort_followup_verified", "near_real_time_validated", "confirmed_bottleneck_accuracy",
                    "causes_verified", "untouched_external_benchmark"]:
            self.assertFalse(self.summary[key], key)

    def test_recalculation_without_fit_or_model_load(self):
        original = self.ledger.copy(deep=True)
        with patch.object(joblib, "load", side_effect=AssertionError("No model load")), \
                patch.object(holdout.frozen.quality, "assess", side_effect=AssertionError("No quality refit")), \
                patch.object(holdout.contextual, "fit_entry", side_effect=AssertionError("No model fit")):
            frames, _ = holdout.evaluate(self.ledger, self.reference)
        for field in [holdout.SCORE, holdout.TARGET, "metric_eligible", "exclusion_reason"]:
            pd.testing.assert_series_equal(frames["order_ledger.csv"][field], self.ledger[field], check_dtype=False, check_exact=True)
        pd.testing.assert_frame_equal(self.ledger, original)

    def test_wrong_split_dates_and_duplicates_rejected(self):
        row = self.ledger.iloc[:1].copy()
        for field, value in [("split", "validation"), ("order_purchase_timestamp", pd.Timestamp("2018-05-31"))]:
            bad = row.copy()
            bad[field] = value
            with self.assertRaisesRegex(ValueError, "original test"):
                holdout.observations(bad)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            holdout.observations(pd.concat([row, row]))

    def test_candidate_cannot_enter_own_reference(self):
        bad = self.reference.copy()
        bad.loc[bad.index[0], "order_id"] = self.stages.iloc[0].order_id
        with self.assertRaisesRegex(ValueError, "disjoint"):
            holdout.frozen.score_stages(self.stages, bad)

    def test_unknown_and_conflicted_cases_remain_unscored(self):
        rows = self.ledger.loc[~self.ledger.performance_eligible]
        self.assertGreater(len(rows), 0)
        frames, summary = holdout.evaluate(rows, self.reference)
        self.assertEqual(summary["evaluated_orders"], 0)
        self.assertTrue(frames["order_ledger.csv"][holdout.SCORE].isna().all())
        self.assertTrue(frames["period_metrics.csv"].roc_auc.isna().all())

    def test_threshold_change_and_missing_frozen_limits_rejected(self):
        selection = holdout.review.read_json(self.directory/"selection.json")
        bad = deepcopy(selection)
        bad["threshold"] = 20
        with self.assertRaisesRegex(ValueError, "selection changed"):
            holdout.require_selection(bad)
        qm = holdout.review.read_json(holdout.contextual.DEFAULT_OUTPUT/"manifest.json")
        thresholds = holdout.shared.read_table(holdout.contextual.DEFAULT_OUTPUT, "quality_thresholds.csv", qm)
        with self.assertRaisesRegex(ValueError, "all frozen"):
            holdout.frozen.geography(self.frames["zip_quality.csv"], self.frames["coordinate_source_evidence.csv"],
                self.frames["pair_quality.csv"], self.frames["pair_quality.csv"][["order_id", "seller_id", "customer_state", "seller_state"]], thresholds.iloc[:1])

    def test_no_overwrite_and_changed_source_code_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileExistsError):
                holdout.run(Path(tmp))
        with self.assertRaisesRegex(ValueError, "implementation"):
            holdout.verify_sources({"snapshots": {}, "code_hashes": {}})


if __name__ == "__main__":
    unittest.main()
