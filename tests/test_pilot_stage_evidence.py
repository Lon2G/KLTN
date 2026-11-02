"""Pilot comparisons checked on real Olist cases and frozen train observations."""

from pathlib import Path
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import prepare_pilot_stage_evidence as evidence


class PilotStageEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {**evidence.audit.inventory(evidence.review.DEFAULT_BATCH),
                      **evidence.audit.inventory(evidence.audit.DEFAULT_OUTPUT),
                      **evidence.audit.inventory(evidence.explanation.DEFAULT_OUTPUT),
                      **evidence.audit.inventory(evidence.signals.DEFAULT_OUTPUT)}
        with patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(evidence.audit.readiness.integration.delivery, "fit_entry", side_effect=AssertionError("No training")):
            cls.inputs, cls.provenance = evidence.load_inputs()
            cls.frames, cls.summary = evidence.build_tables(cls.inputs)
        cls.orders = cls.frames["order_explanations.csv"].set_index("review_id")
        cls.stages = cls.frames["stage_comparisons.csv"]
        cls.ledger = cls.frames["scope_ledger.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"evidence"
        evidence.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_original_120_ids_and_118_plus_two_scope(self):
        key = self.inputs["pilot"]["key"]
        self.assertEqual(self.ledger.review_id.tolist(), key.review_id.tolist())
        self.assertEqual(self.ledger.order_id.tolist(), key.order_id.tolist())
        self.assertEqual(len(self.ledger), 120)
        self.assertEqual(len(self.orders), 118)
        self.assertEqual(len(self.stages), 354)
        self.assertEqual(self.summary["held_review_ids"], ["BR0043", "BR0119"])
        held = self.ledger.loc[~self.ledger.comparison_generated]
        self.assertTrue(held.assessed_stage_count.isna().all())
        for table in ["order_explanations.csv", "stage_comparisons.csv", "signal_evidence.csv"]:
            self.assertFalse(self.frames[table].review_id.isin(held.review_id).any())
        self.assertTrue(self.orders.order_purchase_timestamp.lt(self.inputs["pilot"]["boundary"]).all())

    def test_three_stages_per_order_and_exact_observed_durations(self):
        self.assertTrue(self.stages.groupby("order_id").stage.agg(set).map(lambda s: s == set(evidence.signals.STAGES)).all())
        self.assertFalse(self.stages.duplicated(["order_id", "stage"]).any())
        actual = (self.stages.end_timestamp-self.stages.start_timestamp).dt.total_seconds()/86400
        pd.testing.assert_series_equal(actual, self.stages.duration_days, check_names=False, check_exact=True)
        for field in ["start_timestamp", "end_timestamp"]:
            available = self.stages[field].notna()
            self.assertTrue(self.stages.loc[available, field].lt(self.stages.loc[available, "visible_before_exclusive"]).all())

    def test_train_reference_is_earlier_disjoint_and_unchanged(self):
        ref = self.inputs["train_reference"]
        self.assertEqual(len(ref), 15931)
        self.assertEqual(ref.order_id.nunique(), 5461)
        self.assertTrue(ref.split.eq("train").all())
        self.assertTrue(ref.end_timestamp.lt(self.inputs["reference_end_before"]).all())
        self.assertFalse(ref.order_id.isin(self.ledger.order_id).any())
        frozen, _ = evidence.signals.load_inputs()
        pd.testing.assert_frame_equal(ref, frozen["train_reference"], check_exact=True)

    def test_reconstruct_group_median_rank_and_day_threshold(self):
        ref = self.inputs["train_reference"]
        for group in self.frames["reference_groups.csv"].itertuples(index=False):
            context = json.loads(group.reference_context_json)
            selected = ref.loc[ref.stage.eq(group.stage)]
            for field, value in context.items():
                selected = selected.loc[selected[field].eq(value)]
            if "distance_band" in context:
                selected = selected.loc[selected.all_pairs_quality_eligible]
            values = np.sort(selected.duration_days.to_numpy())
            self.assertEqual(len(values), group.reference_n)
            self.assertEqual(float(np.quantile(values, .5)), group.reference_median_days)
            lower, upper = evidence.signals.exact_boundaries(values)
            self.assertEqual(lower, group.lower_boundary_days_exclusive)
            self.assertEqual(upper, group.upper_boundary_days_inclusive)
            rows = self.stages.loc[self.stages.reference_group_id.eq(group.reference_group_id).fillna(False)]
            np.testing.assert_array_equal(np.searchsorted(values, rows.duration_days, side="right")/len(values), rows.empirical_percentile)

    def test_context_required_and_104_orders_assessed(self):
        self.assertEqual(self.summary["speed_complete_orders"], 104)
        self.assertEqual(self.summary["speed_incomplete_orders"], 14)
        self.assertEqual(int(self.stages.assessable.sum()), 312)
        for row in self.stages.loc[self.stages.assessable].itertuples(index=False):
            self.assertGreaterEqual(row.reference_n, 30)
            self.assertIn("distance_band", json.loads(row.reference_context_json))
        self.assertEqual(self.summary["geography_quality_ineligible_ids"], ["BR0025", "BR0051", "BR0091"])
        self.assertEqual(self.summary["complete_timing_but_no_context_ids"], ["BR0025", "BR0051", "BR0091", "BR0115"])

    def test_small_distance_group_is_not_replaced_by_global_warning(self):
        case = self.orders.loc["BR0115"]
        self.assertTrue(case.all_pairs_quality_eligible)
        self.assertEqual(case.performance_state, "early")
        ref = self.inputs["train_reference"]
        for stage in evidence.signals.STAGES:
            peers = ref.loc[ref.stage.eq(stage) & ref.all_pairs_quality_eligible & ref.distance_band.eq(case.distance_band)
                            & ref.seller_attribution_scope.eq(case.seller_attribution_scope)]
            self.assertLess(len(peers), 30)
        rows = self.stages.loc[self.stages.review_id.eq("BR0115")]
        self.assertTrue(rows.long_stage_warning.isna().all())
        self.assertTrue(rows.comparison_direction.eq("unavailable").all())

    def test_missing_final_milestone_retains_complete_case_policy(self):
        self.assertEqual(self.stages.assessment_status.value_counts().to_dict(), {
            "assessable": 312, "parent_complete_case_policy": 20, "context_reference_unavailable": 12, "endpoint_not_visible": 10})
        rows = self.stages.loc[self.stages.review_id.eq("BR0013")]
        self.assertEqual(int(rows.duration_days.notna().sum()), 2)
        self.assertTrue(rows.long_stage_warning.isna().all())
        self.assertTrue(rows.difference_from_reference_days.isna().all())
        self.assertEqual(self.orders.loc["BR0013", "performance_state"], "unknown")

    def test_faster_slower_is_distinct_from_threshold_flag(self):
        valid = self.stages.speed_comparable
        expected = (self.stages.duration_days-self.stages.reference_median_days).where(valid)
        pd.testing.assert_series_equal(expected, self.stages.difference_from_reference_days, check_names=False, check_exact=True)
        self.assertEqual(self.stages.comparison_direction.value_counts().to_dict(), {"slower": 162, "faster": 150, "unavailable": 42})
        self.assertGreater(int((self.stages.comparison_direction.eq("slower") & self.stages.long_stage_warning.eq(False).fillna(False)).sum()), 0)
        scored = self.stages.loc[self.stages.assessable]
        self.assertTrue(scored.long_stage_warning.eq(scored.duration_days.gt(0) & scored.empirical_percentile.gt(.95)).all())

    def test_early_orders_keep_warnings_and_no_fast_is_not_forced(self):
        self.assertEqual(self.summary["early_with_long_stage_warning"], 16)
        self.assertEqual(self.summary["early_explanation_counts"], {
            "early_with_faster_stage": 78, "early_without_faster_stage": 9, "early_insufficient_stage_evidence": 3})
        row = self.orders.loc["BR0079"]
        self.assertEqual(row.days_early, 13)
        self.assertTrue(row.long_stage_warning)
        self.assertFalse(row.faster_stage_observed)
        self.assertEqual(json.loads(row.long_stage_warning_stages_json), ["preparation_handoff"])
        self.assertIn("Không có công đoạn nào nhanh hơn", row.order_explanation_vi)
        self.assertIn("Giữ cảnh báo công đoạn kéo dài", row.order_explanation_vi)

    def test_nullable_flags_and_no_normal_or_human_labels(self):
        unknown = self.stages.loc[~self.stages.assessable]
        for field in ["long_stage_warning", "short_stage_note", "difference_from_reference_days"]:
            self.assertTrue(unknown[field].isna().all())
        self.assertTrue(self.ledger.anomaly_label.isna().all())
        self.assertFalse(self.ledger.human_review_performed.any())
        self.assertFalse(self.ledger.independent_reference.any())
        self.assertEqual(self.summary["long_warning_orders"], 30)
        self.assertFalse(self.summary["independent_reference_ready"])

    def test_promise_and_money_remain_source_facts(self):
        self.assertTrue(self.ledger.promise_basis_status.eq("unverified_source_method_not_provided").all())
        self.assertEqual(self.orders.loc["BR0051", "payment_minus_items_and_freight_minor_units"], 3944)
        self.assertEqual(self.orders.loc["BR0060", "payment_minus_items_and_freight_minor_units"], 1)
        self.assertFalse(self.summary["promise_dates_changed"])
        self.assertFalse(self.summary["threshold_changed"])
        self.assertFalse(self.summary["model_trained"])
        self.assertFalse(self.summary["test_scored"])
        self.assertFalse(self.summary["auc_computed"])

    def test_safety_exposure_and_actual_record_locators(self):
        notice = evidence.exposure_notice(self.frames)
        self.assertEqual(len(notice["generated_comparison_review_ids"]), 118)
        self.assertEqual(notice["metadata_only_review_ids"], ["BR0043", "BR0119"])
        self.assertEqual(len(notice["previous_automatic_annotation_review_ids"]), 20)
        self.assertEqual(notice["review_mode"], "analyst_assisted_not_blind")
        self.assertTrue(self.frames["signal_evidence.csv"].evidence_locator.str.startswith("analyst_only/").all())
        self.assertFalse(self.frames["signal_evidence.csv"].independent_reference.any())

    def test_all_rows_roundtrip_and_source_snapshots_unchanged(self):
        manifest = evidence.load_snapshot(self.output)
        for name, expected in self.frames.items():
            actual = evidence.shared.read_table(self.output, "analyst_only/"+name, manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        for name, digest in self.before.items():
            self.assertEqual(evidence.review.digest(ROOT/name), digest)
        evidence.audit.load_snapshot()
        evidence.explanation.load_snapshot()

    def test_unchanged_public_reference_groups_when_reused(self):
        manifest = evidence.signals.load_snapshot()
        original = evidence.shared.read_table(evidence.signals.DEFAULT_OUTPUT, "reference_groups.csv", manifest).set_index("reference_group_id")
        current = self.frames["reference_groups.csv"].set_index("reference_group_id")
        common = current.index.intersection(original.index)
        self.assertGreater(len(common), 0)
        pd.testing.assert_frame_equal(current.loc[common], original.loc[common], check_dtype=False, check_exact=True)

    def test_duplicate_reference_and_public_pilot_overlap_rejected(self):
        duplicated = pd.concat([self.inputs["train_reference"], self.inputs["train_reference"].iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            evidence.build_tables({**self.inputs, "train_reference": duplicated})
        pilot = {**self.inputs["pilot"], "explained_ids": {self.ledger.order_id.iloc[0]}}
        with self.assertRaisesRegex(ValueError, "separate"):
            evidence.build_tables({**self.inputs, "pilot": pilot})

    def test_no_overwrite_or_write_into_protected_sources(self):
        with self.assertRaises(FileExistsError):
            evidence.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for directory in [evidence.review.DEFAULT_BATCH, ROOT/"data/raw", evidence.audit.DEFAULT_OUTPUT/"new"]:
            with self.assertRaises(ValueError):
                evidence.write_snapshot(self.frames, self.summary, self.provenance, directory)

    def test_report_covers_all_120_and_discloses_limits(self):
        details = evidence.render_cases(self.frames)
        for rid in self.ledger.review_id:
            self.assertIn("## "+rid+"\n", details)
        report = evidence.render_report(self.frames, self.summary)
        self.assertIn("không phải", report)
        self.assertIn("không phải xác suất", report)
        self.assertIn("BR0115", report)
        self.assertIn("không cộng", report)


if __name__ == "__main__":
    unittest.main()
