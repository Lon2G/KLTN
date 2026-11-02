"""Criteria resolution on real saved Olist rows; no fabricated research labels."""

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
import resolve_evidence_criteria as criteria


class EvidenceCriteriaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(criteria.performance.inspection.integration.delivery, "fit_entry", side_effect=AssertionError("No training")):
            cls.inputs, cls.provenance = criteria.load_inputs()
            cls.frames, cls.summary = criteria.build_tables(cls.inputs)
        cls.cases = cls.frames["case_decisions.csv"]
        cls.stages = cls.frames["stage_evidence.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"criteria"
        criteria.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_all_guarded_validation_not_only_selected_forty(self):
        expected = self.inputs["order_performance.csv"].loc[lambda x: x.split.eq("validation")]
        self.assertEqual(set(self.cases.order_id), set(expected.order_id))
        self.assertEqual(len(self.cases), 1621)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertTrue(self.cases.split.eq("validation").all())
        self.assertEqual(self.summary["complete_timing_candidates"], 1359)

    def test_source_dates_and_money_are_preserved_exactly(self):
        source = self.inputs["order_performance.csv"].set_index("order_id")
        fields = [*criteria.performance.TIMES, criteria.performance.ESTIMATE, "visible_before_exclusive", "promise_delta_calendar_days"]
        actual = self.cases.set_index("order_id")
        pd.testing.assert_frame_equal(actual[fields], source.loc[actual.index, fields], check_exact=True, check_index_type=False)
        original = self.inputs["case_evidence.csv"].set_index("order_id")
        pd.testing.assert_series_equal(actual.payment_minus_items_and_freight_minor_units,
                                       original.loc[actual.index, "payment_minus_items_and_freight_minor_units"],
                                       check_exact=True, check_index_type=False)

    def test_date_criterion_resolves_real_same_day_cases(self):
        same = self.cases.loc[self.cases.performance_state.eq("on_date")]
        self.assertEqual(len(same), 17)
        self.assertTrue(same.order_delivered_customer_date.gt(same.order_estimated_delivery_date).all())
        self.assertTrue(same.promise_delta_calendar_days.eq(0).all())
        self.assertEqual(self.summary["same_day_misclassified_by_timestamp_rule"], 17)
        self.assertEqual(self.summary["calendar_late_orders"], 149)

    def test_missing_or_invalid_sources_are_never_complete_or_normal(self):
        invalid = self.cases.quality_conflict
        missing = self.cases[criteria.performance.TIMES].isna().any(axis=1)
        self.assertGreater(int(invalid.sum()), 0)
        self.assertGreater(int(missing.sum()), 0)
        self.assertFalse(self.cases.loc[invalid | missing, "context_timing_assessable"].any())
        self.assertTrue(self.cases.loc[invalid, "source_quality_status"].eq("recorded_conflict_requires_verification").all())
        self.assertTrue(self.cases.loc[invalid | missing, "context_long_warning"].isna().all())

    def test_no_future_milestones_enter_stage_comparison(self):
        cutoffs = self.cases.set_index("order_id").visible_before_exclusive
        self.assertTrue(self.stages.end_timestamp.lt(self.stages.order_id.map(cutoffs)).all())
        self.assertTrue(self.stages.start_timestamp.lt(self.stages.order_id.map(cutoffs)).all())
        self.assertTrue(self.stages.duration_days.ge(0).all())
        self.assertFalse(self.stages.duplicated(["order_id", "stage"]).any())

    def test_global_rank_independently_recomputed_from_train(self):
        reference, _ = criteria.supplement.stage_references(self.inputs)
        for name in criteria.supplement.STAGES:
            values = reference.loc[reference.stage.eq(name), "duration_days"].to_numpy()
            sample = self.stages.loc[self.stages.stage.eq(name)].iloc[::97]
            for row in sample.itertuples(index=False):
                self.assertEqual(row.global_reference_n, len(values))
                self.assertEqual(row.global_empirical_percentile, np.count_nonzero(values <= row.duration_days)/len(values))

    def test_context_warning_requires_support_and_real_distance(self):
        selected = self.stages.loc[self.stages.context_warning_assessable]
        self.assertTrue(selected.reference_distance_matched.all())
        self.assertTrue(selected.reference_n.ge(30).all())
        missing = self.stages.loc[~self.stages.context_warning_assessable]
        self.assertGreater(len(missing), 0)
        self.assertFalse(missing.reference_distance_matched.all())

    def test_threshold_comparison_uses_identical_case_denominators(self):
        table = self.frames["threshold_sensitivity.csv"]
        self.assertEqual(len(table), 8)
        self.assertEqual(table.common_orders.nunique(), 1)
        self.assertEqual(table.late_promise_orders.nunique(), 1)
        self.assertTrue((table.late_promise_with_warning+table.late_promise_without_warning).eq(table.late_promise_orders).all())
        self.assertTrue((table.late_promise_with_warning+table.meets_promise_with_warning).eq(table.warning_orders).all())
        for _, group in table.groupby("method"):
            self.assertTrue(group.sort_values("percentile").warning_orders.diff().dropna().le(0).all())

    def test_disagreement_rows_are_real_differences_not_error_labels(self):
        disagreements = self.frames["threshold_disagreements.csv"]
        self.assertGreater(len(disagreements), 0)
        self.assertTrue(disagreements.global_warning.ne(disagreements.context_warning).all())
        self.assertTrue(disagreements.correct_detector.eq("unresolved").all())
        self.assertTrue(set(disagreements.order_id).issubset(self.cases.order_id))
        for q, rows in disagreements.groupby("percentile"):
            metric = self.frames["threshold_sensitivity.csv"].loc[lambda x: x.percentile.eq(q)].iloc[0]
            self.assertEqual(len(rows), metric.context_only_warnings+metric.global_only_warnings)

    def test_models_are_compared_on_exact_same_observations(self):
        table = self.frames["saved_model_comparison.csv"]
        self.assertEqual(set(table.model), criteria.supplement.EXPECTED_MODELS)
        self.assertEqual(table.common_orders.nunique(), 1)
        self.assertEqual(table.common_orders.iloc[0], 1229)
        self.assertEqual(table.late_promise_orders.nunique(), 1)
        models = self.inputs["delivery_model_evidence.csv"]
        common_ids = self.cases.loc[self.cases.complete_saved_comparison.fillna(False), "order_id"]
        for row in table.itertuples(index=False):
            source = models.loc[models.order_id.isin(common_ids) & models.model.eq(row.model)]
            self.assertEqual(set(source.order_id), set(common_ids))
            self.assertEqual(row.long_warning_orders, int(source.long_warning.sum()))

    def test_short_positive_duration_not_error_or_data_removal(self):
        short = self.cases.loc[self.cases.context_short_warning.fillna(False)]
        self.assertGreater(len(short), 0)
        self.assertTrue(short.broad_anomaly_status.eq("unresolved").all())
        self.assertTrue(short.cause_or_seller_fault.eq("unresolved").all())
        self.assertNotIn("remove_from_training", self.cases)

    def test_partial_context_does_not_become_complete_no_warning(self):
        partial = self.cases.loc[~self.cases.context_timing_assessable]
        self.assertFalse(partial.timing_signal_status.eq("no_warning_under_working_reference").any())
        self.assertTrue(partial.timing_signal_status.isin(["not_assessable", "partial_context_warning_other_stages_unassessed"]).all())

    def test_policy_selected_but_no_fake_accuracy_or_detector_winner(self):
        self.assertEqual(self.summary["selected_policy"], criteria.protocol()["selected_policy"])
        self.assertIsNone(self.summary["selected_detector"])
        self.assertIsNone(self.summary["optimized_detector_threshold"])
        self.assertFalse(self.summary["business_accuracy_known"])
        self.assertFalse(self.frames["saved_model_comparison.csv"].selected_as_best_detector.any())
        self.assertTrue(self.cases.human_reference_label.isna().all())
        self.assertTrue(self.cases.broad_anomaly_status.eq("unresolved").all())
        self.assertFalse(self.summary["policy_is_unbiased_or_globally_optimal"])

    def test_missing_result_is_not_zero_warning_and_no_test_or_fit(self):
        unavailable = self.cases.saved_configurations_evaluated.isna() | self.cases.saved_configurations_evaluated.eq(0)
        self.assertGreater(int(unavailable.sum()), 0)
        self.assertTrue(self.cases.loc[unavailable, "saved_long_warnings"].isna().all())
        for path in self.reads:
            self.assertNotIn("test_timing", path)
            self.assertNotIn("partition_plan.csv", path)
        for field in ["model_trained", "detector_threshold_changed", "test_scored", "split_activated", "near_real_time_validated"]:
            self.assertFalse(self.summary[field])

    def test_contract_checks_and_blank_human_labels_survive_roundtrip(self):
        self.assertTrue(self.frames["contract_checks.csv"].passed.all())
        self.assertEqual(self.summary["contract_violations"], 0)
        manifest = criteria.load_snapshot(self.output)
        restored = {name: criteria.shared.read_table(self.output, name, manifest) for name in criteria.TABLES}
        for name, frame in self.frames.items():
            pd.testing.assert_frame_equal(restored[name], frame, check_dtype=False, check_exact=True)
        self.assertTrue(restored["case_decisions.csv"].human_reference_label.isna().all())
        self.assertEqual((self.output/"bao_cao_tieu_chi.md").read_text(), criteria.render_report(restored, self.summary))

    def test_snapshot_overwrite_protected_path_and_tamper_rejected(self):
        with self.assertRaises(FileExistsError):
            criteria.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        with self.assertRaises(ValueError):
            criteria.write_snapshot(self.frames, self.summary, self.provenance, ROOT/"data/raw/criteria")
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"case_decisions.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            criteria.load_snapshot(damaged)


if __name__ == "__main__":
    unittest.main()
