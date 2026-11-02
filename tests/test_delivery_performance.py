"""Delivery performance invariants using the saved real Olist observations."""

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
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import build_delivery_performance as performance


class DeliveryPerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(performance.inspection.integration.delivery, "fit_entry", side_effect=AssertionError("No training")):
            cls.repository, cls.boundary, cls.provenance = performance.load_inputs()
            cls.frames, cls.summary = performance.build_tables(cls.repository, cls.boundary)
        cls.orders = cls.frames["order_performance.csv"]
        cls.durations = cls.frames["stage_durations.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"performance"
        performance.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_all_accessible_orders_reconcile_without_pilot_or_test(self):
        self.assertEqual(len(self.orders), 7101)
        self.assertTrue(self.orders.order_id.is_unique)
        self.assertFalse(self.orders.order_id.isin(self.repository["protected_ids"]).any())
        self.assertTrue(self.orders.order_purchase_timestamp.lt(self.boundary).all())
        self.assertEqual(7101+120+112, self.summary["source_development_orders"])
        self.assertEqual(set(self.orders.split), {"train", "validation"})
        for name, frame in self.frames.items():
            if "order_id" in frame:
                self.assertTrue(set(frame.order_id).issubset(self.orders.order_id), name)

    def test_no_final_test_feature_values_or_model_weights_loaded(self):
        for path in self.reads:
            self.assertNotIn("test_timing", path)
            self.assertNotIn("partition_plan.csv", path)
            self.assertNotIn("review_progress", path)
        for key in ["model_trained", "threshold_changed", "human_labels_created", "test_scored", "split_activated", "anomaly_accuracy_computed"]:
            self.assertFalse(self.summary[key])

    def test_calendar_delta_recomputed_from_real_timestamps(self):
        frame = self.orders
        expected = (frame.order_delivered_customer_date.dt.normalize()-frame.order_estimated_delivery_date.dt.normalize()).dt.days
        np.testing.assert_allclose(frame.promise_delta_calendar_days.to_numpy(dtype=float, na_value=np.nan), expected, equal_nan=True)
        self.assertTrue(frame.loc[frame.delivery_timing_observation.eq("early"), "promise_delta_calendar_days"].lt(0).all())
        self.assertTrue(frame.loc[frame.delivery_timing_observation.eq("late"), "promise_delta_calendar_days"].gt(0).all())

    def test_same_date_after_midnight_is_on_date_not_late(self):
        same_day = self.orders.loc[self.orders.performance_state.eq("on_date")]
        self.assertEqual(len(same_day), 65)
        self.assertTrue(same_day.order_delivered_customer_date.gt(same_day.order_estimated_delivery_date).all())
        self.assertTrue(same_day.promise_delta_calendar_days.eq(0).all())
        self.assertTrue(same_day.days_late.eq(0).all())

    def test_unknown_delivery_is_not_zero_or_normal(self):
        unknown = self.orders.loc[self.orders.delivery_timing_observation.eq("unknown")]
        self.assertGreater(len(unknown), 0)
        self.assertTrue(unknown.promise_delta_calendar_days.isna().all())
        self.assertTrue(unknown.days_early.isna().all())
        self.assertTrue(unknown.days_late.isna().all())
        self.assertFalse(unknown.performance_eligible.any())
        self.assertEqual(set(unknown.delivery_visibility), {"source_missing", "at_or_after_cutoff"})

    def test_all_exported_actual_events_strictly_before_both_cutoffs(self):
        source = self.repository["frames"]["case_evidence.csv"].set_index("order_id")
        for field in performance.TIMES:
            visible = self.orders[field].notna()
            self.assertTrue(self.orders.loc[visible, field].lt(self.orders.loc[visible, "visible_before_exclusive"]).all())
            expected = source.loc[self.orders.order_id, field].reset_index(drop=True)
            expected = expected.where(expected.lt(self.orders.visible_before_exclusive))
            pd.testing.assert_series_equal(self.orders[field], expected, check_names=False, check_exact=True)

    def test_source_conflicts_retained_but_not_in_performance_denominator(self):
        conflict = self.orders.loc[self.orders.quality_conflict]
        self.assertEqual(len(conflict), 59)
        self.assertTrue(conflict.performance_state.eq("excluded_source_conflict").all())
        self.assertFalse(conflict.performance_eligible.any())
        self.assertTrue(conflict.delivery_timing_observation.isin(["early", "on_date", "late", "unknown"]).all())
        self.assertFalse(self.durations.loc[self.durations.quality_conflict, "duration_eligible"].any())

    def test_original_estimated_dates_are_preserved(self):
        source = self.repository["frames"]["case_evidence.csv"].set_index("order_id")
        expected = source.loc[self.orders.order_id, performance.ESTIMATE].reset_index(drop=True)
        pd.testing.assert_series_equal(self.orders[performance.ESTIMATE], expected, check_exact=True)
        self.assertEqual(self.summary["estimated_date_non_midnight_records"], 0)

    def test_durations_keep_negative_zero_and_missing_without_imputation(self):
        frame = self.durations
        self.assertEqual(len(frame), 4*len(self.orders))
        self.assertFalse(frame.duplicated(["order_id", "stage"]).any())
        actual = (frame.end_timestamp-frame.start_timestamp).dt.total_seconds()/86400
        np.testing.assert_allclose(frame.duration_days, actual, rtol=0, atol=0, equal_nan=True)
        for state in ["negative_recorded_duration", "zero_recorded_duration", "endpoint_not_visible", "positive_observed"]:
            self.assertGreater(int(frame.duration_state.eq(state).sum()), 0)
        self.assertFalse(frame.loc[frame.duration_days.isna() | frame.duration_days.lt(0), "duration_eligible"].any())

    def test_every_state_and_rate_reconciles(self):
        summary = self.frames["overview.csv"]
        self.assertEqual(summary.orders.sum(), len(self.orders))
        self.assertEqual(summary.comparable_orders.sum(), 6437)
        for row in summary.itertuples(index=False):
            self.assertEqual(sum(getattr(row, state+"_orders") for state in performance.STATES), row.orders)
            self.assertAlmostEqual(row.early_rate+row.on_date_rate+row.late_rate, 1)
            self.assertAlmostEqual(row.early_rate, row.early_orders/row.comparable_orders)

    def test_entity_links_remove_repeated_item_orders_not_distinct_products(self):
        items = self.repository["frames"]["item_source_evidence.csv"]
        items = items.loc[items.order_id.isin(self.orders.order_id)]
        for entity in ["product", "seller"]:
            link = self.frames[entity+"_order_links.csv"]
            self.assertFalse(link.duplicated(["order_id", entity+"_id"]).any())
            self.assertEqual(set(map(tuple, link.values)), set(map(tuple, items[["order_id", entity+"_id"]].values)))
        self.assertGreater(len(items), len(self.frames["product_order_links.csv"]))
        self.assertGreater(len(self.frames["product_order_links.csv"]), len(self.orders))

    def test_seller_single_and_multi_order_associations_remain_separate(self):
        table = self.frames["seller_summary.csv"]
        self.assertEqual(set(table.seller_attribution_scope), {"single_seller_order", "multi_seller_order"})
        self.assertTrue(table.loc[table.seller_attribution_scope.eq("single_seller_order"), "multi_seller_orders"].eq(0).all())
        self.assertTrue(table.loc[table.seller_attribution_scope.eq("multi_seller_order"), "single_seller_orders"].eq(0).all())

    def test_group_rates_use_real_eligible_denominators(self):
        for name in ["product_summary.csv", "seller_summary.csv", "context_summary.csv"]:
            table = self.frames[name]
            self.assertTrue(table.comparable_orders.le(table.orders).all())
            zero = table.comparable_orders.eq(0)
            self.assertTrue(table.loc[zero, ["early_rate", "on_date_rate", "late_rate"]].isna().all().all())
            self.assertTrue((table.early_orders+table.on_date_orders+table.late_orders).eq(table.comparable_orders).all())

    def test_context_groups_reconcile_without_claiming_causality(self):
        self.assertEqual(self.frames["context_summary.csv"].orders.sum(), len(self.orders))
        self.assertEqual(self.frames["stage_context_summary.csv"].orders.sum(), len(self.durations))
        self.assertFalse(self.summary["causal_bottleneck_identified"])

    def test_stage_summary_medians_match_independent_real_groups(self):
        for row in self.frames["stage_summary.csv"].itertuples(index=False):
            group = self.durations.loc[self.durations.split.eq(row.split) & self.durations.performance_state.eq(row.performance_state)
                                       & self.durations.stage.eq(row.stage)]
            valid = group.loc[group.duration_eligible, "duration_days"]
            self.assertEqual(row.duration_eligible_orders, len(valid))
            if len(valid):
                self.assertEqual(row.median_duration_days, np.median(valid.to_numpy()))
            else:
                self.assertTrue(pd.isna(row.median_duration_days))

    def test_early_delivery_never_generates_anomaly_label(self):
        forbidden = {"anomaly_label", "normal_label", "fraud", "stage_deadline", "anomaly_probability", "final_verdict"}
        for frame in self.frames.values():
            self.assertFalse(forbidden.intersection(frame.columns))
        self.assertIn("not_anomaly", performance.protocol()["early_delivery"])

    def test_historical_signals_are_separate_and_future_outcome_signals_withheld(self):
        saved = self.frames["saved_signals.csv"]
        original = self.repository["frames"]["case_evidence.csv"].set_index("order_id")
        withheld = saved.saved_signals_visibility.eq("withheld_post_cutoff_observations")
        self.assertGreater(int(withheld.sum()), 0)
        signal_fields = [c for c in saved if c not in {"order_id", "historical_event_time_cutoff_exclusive", "saved_signals_visibility"}]
        self.assertTrue(saved.loc[withheld, signal_fields].isna().all().all())
        expected = original.loc[saved.loc[~withheld, "order_id"], signal_fields].reset_index(drop=True)
        pd.testing.assert_frame_equal(saved.loc[~withheld, signal_fields].reset_index(drop=True), expected, check_dtype=False, check_exact=True)
        self.assertNotIn("delivery_models_long_warning", self.orders.columns)

    def test_serialized_outputs_verify_and_round_trip(self):
        manifest = performance.load_snapshot(self.output)
        self.assertEqual(manifest["summary"], self.summary)
        for name, frame in self.frames.items():
            actual = performance.shared.read_table(self.output, name, manifest)
            pd.testing.assert_frame_equal(actual, frame, check_dtype=False, check_exact=True)

    def test_existing_snapshot_and_protected_destinations_refused(self):
        with self.assertRaises(FileExistsError):
            performance.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [ROOT, ROOT/"data/raw/new", ROOT/"models/new", performance.inspection.review.DEFAULT_BATCH/"new"]:
            with self.assertRaises(ValueError):
                performance.destination_guard(path, self.provenance)

    def test_tampering_detected_without_touching_real_snapshot(self):
        target = Path(self.temp.name)/"tampered"
        shutil.copytree(self.output, target)
        with (target/"overview.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            performance.load_snapshot(target)

    def test_duplicate_orders_fail_closed(self):
        source = self.repository["frames"]["case_evidence.csv"].head(1)
        with self.assertRaises(ValueError):
            performance.order_performance(pd.concat([source, source]), self.boundary)


if __name__ == "__main__":
    unittest.main()
