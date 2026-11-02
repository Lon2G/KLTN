"""Explanation contracts using frozen real Olist observations, not generated data."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import explain_process_performance as explanation


class ProcessPerformanceExplanationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        cls.verification_reads = []
        original = pd.read_csv
        verify_sources = explanation.verify_sources
        verifying = False

        def verify(provenance):
            nonlocal verifying
            verifying = True
            try:
                return verify_sources(provenance)
            finally:
                verifying = False

        def read(path, *args, **kwargs):
            (cls.verification_reads if verifying else cls.reads).append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(explanation, "verify_sources", side_effect=verify), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(explanation.signals.performance.inspection.integration.delivery,
                             "fit_entry", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = explanation.load_inputs()
            cls.frames, cls.summary = explanation.build_tables(cls.inputs)
        cls.orders = cls.frames["order_explanations.csv"]
        cls.stages = cls.frames["stage_comparisons.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"explanations"
        explanation.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_all_real_validation_orders_and_source_columns_unchanged(self):
        self.assertEqual(len(self.orders), 1621)
        self.assertEqual(len(self.stages), 4863)
        for old, new, keys in [("order_signals.csv", "order_explanations.csv", ["order_id"]),
                               ("stage_signals.csv", "stage_comparisons.csv", ["order_id", "stage"])]:
            source = self.inputs[old].sort_values(keys).reset_index(drop=True)
            pd.testing.assert_frame_equal(source, self.frames[new][source.columns], check_exact=True)

    def test_promise_basis_unverified_is_not_random_or_invalid(self):
        self.assertTrue(self.orders.promise_basis_status.eq("unverified_source_method_not_provided").all())
        self.assertTrue(self.orders.promise_basis_vi.eq("Chưa xác minh được cơ sở ngày hẹn").all())
        self.assertTrue(self.orders.promise_basis_evidence_status.eq("source_method_documentation_not_provided").all())
        self.assertTrue(self.orders.order_explanation_vi.str.contains("không kết luận ngày hẹn được đặt ngẫu nhiên", regex=False).all())
        self.assertFalse(self.summary["promise_dates_changed"])

    def test_exact_signed_difference_separate_from_warning(self):
        comparable = self.stages.speed_comparable
        expected = (self.stages.duration_days-self.stages.reference_median_days).where(comparable)
        pd.testing.assert_series_equal(self.stages.difference_from_reference_days, expected, check_names=False, check_exact=True)
        self.assertTrue(self.stages.loc[comparable, "reference_type"].eq("train_peer_median").all())
        self.assertTrue(self.stages.stage_plan_status.eq("not_provided_in_frozen_source").all())
        for sign, direction in [(expected.lt(0), "faster"), (expected.gt(0), "slower"), (expected.eq(0), "equal")]:
            self.assertTrue(self.stages.loc[comparable & sign, "comparison_direction"].eq(direction).all())
        slower_without_warning = self.stages.comparison_direction.eq("slower") & self.stages.long_stage_warning.eq(False).fillna(False)
        self.assertGreater(int(slower_without_warning.sum()), 0)
        self.assertTrue(self.stages.loc[slower_without_warning, "comparison_explanation_vi"].str.contains("Không vượt ngưỡng", regex=False).all())

    def test_known_early_order_without_faster_stage_retains_long_warning(self):
        oid = "0f916489801ab47804cc7da10fe0dd6d"
        case = self.orders.set_index("order_id").loc[oid]
        self.assertEqual(case.performance_state, "early")
        self.assertEqual(case.days_early, 8)
        self.assertEqual(case.early_delivery_explanation_status, "early_without_faster_stage")
        self.assertFalse(case.faster_stage_observed)
        self.assertTrue(case.long_stage_warning)
        self.assertEqual(case.primary_attention_status, "signal_present")
        self.assertEqual(case.slower_stage_count, 3)
        self.assertIn("Không có công đoạn nào nhanh hơn", case.order_explanation_vi)
        self.assertIn("Giữ cảnh báo công đoạn kéo dài", case.order_explanation_vi)
        stages = self.stages.loc[self.stages.order_id.eq(oid)].set_index("stage")
        self.assertAlmostEqual(stages.loc["preparation_handoff", "difference_from_reference_days"], 7.031927083333333)
        self.assertTrue(stages.loc["preparation_handoff", "long_stage_warning"])
        self.assertFalse(stages.loc["approval", "long_stage_warning"])
        self.assertFalse(stages.loc["transport", "long_stage_warning"])

    def test_known_early_order_with_three_faster_stages(self):
        oid = "009905140e9f8cc35d5be897937381db"
        case = self.orders.set_index("order_id").loc[oid]
        self.assertEqual(case.days_early, 14)
        self.assertEqual(case.early_delivery_explanation_status, "early_with_faster_stage")
        self.assertTrue(case.faster_stage_observed)
        self.assertEqual(case.faster_stage_count, 3)
        self.assertEqual(set(json.loads(case.faster_stages_json)), set(explanation.signals.STAGES))
        self.assertFalse(case.long_stage_warning)
        stages = self.stages.loc[self.stages.order_id.eq(oid)]
        self.assertTrue(stages.difference_from_reference_days.lt(0).all())
        self.assertNotAlmostEqual(-stages.difference_from_reference_days.sum(), case.days_early)
        self.assertIn("không cộng thành số ngày sớm/trễ", case.order_explanation_vi)

    def test_early_counts_and_warning_preservation(self):
        self.assertEqual(self.summary["early_orders"], 1193)
        self.assertEqual(self.summary["early_explanation_counts"], {
            "early_with_faster_stage": 1063, "early_without_faster_stage": 90,
            "early_insufficient_stage_evidence": 40})
        self.assertEqual(self.summary["early_with_long_stage_warning"], 61)
        self.assertEqual(self.summary["early_long_without_faster_stage"], 14)
        self.assertEqual(int(self.orders.long_stage_warning.sum()), 165)
        self.assertEqual(int(self.stages.long_stage_warning.sum()), 170)

    def test_unavailable_stages_remain_unknown_with_explanation(self):
        unknown = self.stages.loc[~self.stages.assessable]
        self.assertEqual(len(unknown), 942)
        self.assertFalse(unknown.speed_comparable.any())
        self.assertTrue(unknown.difference_from_reference_days.isna().all())
        self.assertTrue(unknown.comparison_direction.eq("unavailable").all())
        self.assertTrue(unknown.long_stage_warning.isna().all())
        self.assertTrue(unknown.short_stage_note.isna().all())
        self.assertTrue(unknown.reference_type.eq("unavailable").all())
        self.assertTrue(unknown.comparison_explanation_vi.str.startswith("Chưa so sánh nhanh/chậm.").all())
        orders = self.orders.loc[~self.orders.speed_comparison_complete]
        self.assertEqual(len(orders), 314)
        self.assertTrue(orders.faster_stage_observed.isna().all())
        self.assertTrue(orders.order_explanation_vi.str.contains("Chưa đủ bằng chứng", regex=False).all())

    def test_early_unknown_is_not_claimed_to_have_no_fast_stage(self):
        cases = self.orders.loc[self.orders.early_delivery_explanation_status.eq("early_insufficient_stage_evidence")]
        self.assertEqual(len(cases), 40)
        self.assertTrue(cases.faster_stage_observed.isna().all())
        self.assertFalse(cases.order_explanation_vi.str.contains("Không có công đoạn nào nhanh hơn", regex=False).any())

    def test_partial_real_observation_cannot_prove_no_faster_stage(self):
        # Withhold real rows in memory only; no fabricated research observation.
        oid = "0f916489801ab47804cc7da10fe0dd6d"
        source = self.inputs["order_signals.csv"].loc[lambda x: x.order_id.eq(oid)]
        subset = self.stages.loc[self.stages.order_id.eq(oid) & self.stages.stage.ne("transport")]
        case = explanation.order_explanations(source, subset).iloc[0]
        self.assertEqual(case.speed_compared_stage_count, 2)
        self.assertFalse(case.speed_comparison_complete)
        self.assertTrue(pd.isna(case.faster_stage_observed))
        self.assertEqual(case.early_delivery_explanation_status, "early_insufficient_stage_evidence")
        self.assertTrue(case.long_stage_warning)
        self.assertIn("2/3 công đoạn", case.order_explanation_vi)

    def test_partial_real_observation_retains_observed_faster_stage(self):
        oid = "009905140e9f8cc35d5be897937381db"
        source = self.inputs["order_signals.csv"].loc[lambda x: x.order_id.eq(oid)]
        subset = self.stages.loc[self.stages.order_id.eq(oid) & self.stages.stage.eq("approval")]
        case = explanation.order_explanations(source, subset).iloc[0]
        self.assertTrue(case.faster_stage_observed)
        self.assertFalse(case.speed_comparison_complete)
        self.assertEqual(case.faster_stage_count, 1)
        self.assertEqual(case.early_delivery_explanation_status, "early_with_faster_stage_partial_comparison")
        self.assertIn("1/3 công đoạn", case.order_explanation_vi)

    def test_missing_or_duplicate_real_stage_input_rejected(self):
        for stages in [self.inputs["stage_signals.csv"].iloc[1:],
                       pd.concat([self.inputs["stage_signals.csv"], self.inputs["stage_signals.csv"].iloc[:1]], ignore_index=True)]:
            with self.subTest(rows=len(stages)), self.assertRaises(ValueError):
                explanation.build_tables({**self.inputs, "stage_signals.csv": stages})

    def test_reject_mismatched_order_metadata_and_reversed_stage_warning(self):
        damaged = self.inputs["stage_signals.csv"].copy()
        damaged.loc[damaged.index[0], "performance_state"] = "inconsistent"
        with self.assertRaises(AssertionError):
            explanation.build_tables({**self.inputs, "stage_signals.csv": damaged})
        damaged = self.inputs["stage_signals.csv"].copy()
        index = damaged.index[damaged.assessable][0]
        damaged.loc[index, "long_stage_warning"] = not damaged.loc[index, "long_stage_warning"]
        with self.assertRaisesRegex(ValueError, "Frozen long-stage flags changed"):
            explanation.build_tables({**self.inputs, "stage_signals.csv": damaged})

    def test_reject_event_at_cutoff_and_changed_duration(self):
        damaged = self.inputs["stage_signals.csv"].copy()
        index = damaged.index[damaged.assessable][0]
        damaged.loc[index, "end_timestamp"] = damaged.loc[index, "visible_before_exclusive"]
        with self.assertRaisesRegex(ValueError, "observation cutoff"):
            explanation.build_tables({**self.inputs, "stage_signals.csv": damaged})
        damaged = self.inputs["stage_signals.csv"].copy()
        damaged.loc[index, "duration_days"] += 1
        with self.assertRaises(AssertionError):
            explanation.build_tables({**self.inputs, "stage_signals.csv": damaged})

    def test_no_test_reads_model_fitting_labels_or_accuracy(self):
        # Ancestor audit validation reads are not inputs to the explanation builder.
        self.assertEqual({Path(path).name for path in self.reads}, {"order_signals.csv", "stage_signals.csv"})
        self.assertEqual(set(self.inputs), {"order_signals.csv", "stage_signals.csv"})
        self.assertGreater(len(self.verification_reads), 0)
        for path in [*self.reads, *self.verification_reads]:
            self.assertNotIn("test_timing", path)
            self.assertNotIn("partition_plan.csv", path)
        self.assertEqual(self.summary["reference_labels_created"], 0)
        self.assertIsNone(self.summary["selected_ml_detector"])
        for key in ["model_trained", "threshold_changed", "test_scored", "promise_dates_changed",
                    "anomaly_accuracy_computed", "near_real_time_validated"]:
            self.assertFalse(self.summary[key])
        self.assertNotIn("remove_from_training", self.orders)
        self.assertNotIn("human_reference_label", self.orders)

    def test_roundtrip_preserves_missing_flags_and_reproducible_report(self):
        manifest = explanation.load_snapshot(self.output)
        restored = {name: explanation.shared.read_table(self.output, name, manifest) for name in explanation.TABLES}
        for name in explanation.TABLES:
            pd.testing.assert_frame_equal(restored[name], self.frames[name], check_dtype=False, check_exact=True)
        self.assertEqual((self.output/"bao_cao_dien_giai.md").read_text(), explanation.render_report(restored, self.summary))
        for oid in ["0f916489801ab47804cc7da10fe0dd6d", "009905140e9f8cc35d5be897937381db"]:
            report = explanation.render_case(restored, oid)
            self.assertIn(oid, report)
            self.assertIn("Chưa xác minh được cơ sở ngày hẹn", report)
        unknown = self.orders.loc[self.orders.early_delivery_explanation_status.eq("early_insufficient_stage_evidence"), "order_id"].iloc[0]
        self.assertIn("chưa đánh giá", explanation.render_case(restored, unknown))
        with self.assertRaises(ValueError):
            explanation.render_case(restored, "outside_snapshot")

    def test_protected_nested_paths_and_overwrite_rejected(self):
        with self.assertRaises(FileExistsError):
            explanation.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [ROOT/"data/raw/explanations", ROOT/"models/explanations", explanation.signals.DEFAULT_OUTPUT/"nested"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                explanation.write_snapshot(self.frames, self.summary, self.provenance, path)

    def test_changed_saved_table_or_source_manifest_rejected(self):
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"stage_comparisons.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            explanation.load_snapshot(damaged)
        provenance = {**self.provenance, "signals_manifest_sha256": "tampered"}
        with self.assertRaisesRegex(ValueError, "Frozen signal source changed"):
            explanation.verify_sources(provenance)
        with patch.object(explanation, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "code/protocol changed"):
                explanation.load_snapshot(self.output)


if __name__ == "__main__":
    unittest.main()
