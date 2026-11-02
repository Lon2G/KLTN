"""Signal contracts on real Olist records; no fabricated research observations."""

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
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import build_process_signals as signals


class ProcessSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(signals.performance.inspection.integration.delivery, "fit_entry", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = signals.load_inputs()
            cls.frames, cls.summary = signals.build_tables(cls.inputs)
        cls.orders = cls.frames["order_signals.csv"]
        cls.stages = cls.frames["stage_signals.csv"]
        cls.evidence = cls.frames["signal_evidence.csv"]
        cls.groups = cls.frames["reference_groups.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"signals"
        signals.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_every_real_guarded_validation_order_and_three_stages_retained(self):
        self.assertEqual(len(self.orders), 1621)
        self.assertEqual(set(self.orders.order_id), set(self.inputs["case_decisions.csv"].order_id))
        self.assertTrue(self.orders.order_id.is_unique)
        self.assertTrue(self.orders.split.eq("validation").all())
        self.assertEqual(len(self.stages), 4863)
        self.assertFalse(self.stages.duplicated(["order_id", "stage"]).any())
        self.assertTrue(self.stages.groupby("order_id").stage.agg(set).map(lambda x: x == set(signals.STAGES)).all())

    def test_source_money_calendar_and_timestamps_preserved(self):
        source = self.inputs["case_decisions.csv"].set_index("order_id")
        actual = self.orders.set_index("order_id")
        fields = [*signals.performance.TIMES, signals.performance.ESTIMATE, "visible_before_exclusive",
                  "promise_delta_calendar_days", "performance_state", "payment_minus_items_and_freight_minor_units"]
        pd.testing.assert_frame_equal(actual[fields], source.loc[actual.index, fields], check_exact=True, check_index_type=False)
        comparable = self.orders.performance_eligible
        recomputed = self.orders.promise_delta_calendar_days.gt(0)
        self.assertTrue(self.orders.loc[comparable, "calendar_late_signal"].eq(recomputed[comparable]).all())
        self.assertTrue(self.orders.loc[~comparable, "calendar_late_signal"].isna().all())

    def test_visible_stage_durations_recomputed_not_imputed(self):
        expected = (self.stages.end_timestamp-self.stages.start_timestamp).dt.total_seconds()/86400
        pd.testing.assert_series_equal(self.stages.duration_days, expected, check_names=False, check_exact=True)
        for field in ["start_timestamp", "end_timestamp"]:
            visible = self.stages[field].notna()
            self.assertTrue(self.stages.loc[visible, field].lt(self.stages.loc[visible, "visible_before_exclusive"]).all())
        missing = self.stages.duration_days.isna()
        self.assertGreater(int(missing.sum()), 0)
        self.assertTrue(self.stages.loc[missing, "long_stage_warning"].isna().all())

    def test_same_day_and_early_are_not_calendar_alerts(self):
        same = self.orders.loc[self.orders.performance_state.eq("on_date")]
        self.assertEqual(len(same), 17)
        self.assertFalse(same.calendar_late_signal.any())
        early = self.orders.loc[self.orders.performance_state.eq("early")]
        self.assertEqual(len(early), 1193)
        self.assertFalse(early.calendar_late_signal.any())
        self.assertFalse(self.evidence.signal_code.isin(["early", "on_date"]).any())

    def test_stage_flags_match_existing_policy_exactly(self):
        parent = self.inputs["case_decisions.csv"].set_index("order_id")
        actual = self.orders.set_index("order_id")
        for new, old in [("long_stage_warning", "context_long_warning"), ("short_stage_note", "context_short_warning")]:
            pd.testing.assert_series_equal(actual[new], parent.loc[actual.index, old], check_names=False, check_index_type=False)
        self.assertEqual(int(self.orders.long_stage_warning.fillna(False).sum()), 165)
        self.assertEqual(int(self.orders.short_stage_note.fillna(False).sum()), 178)
        self.assertFalse(self.summary["threshold_changed"])

    def test_exact_day_boundaries_reproduce_ranks_on_real_peer_values_including_ties(self):
        reference = self.inputs["train_reference"]
        tied_groups = 0
        different_p95 = 0
        for row in self.groups.itertuples(index=False):
            context = json.loads(row.reference_context_json)
            mask = reference.stage.eq(row.stage)
            for key, value in context.items():
                mask &= reference[key].eq(value)
            if "distance_band" in context:
                mask &= reference.all_pairs_quality_eligible
            peers = reference.loc[mask]
            values = np.sort(peers.duration_days.to_numpy())
            self.assertEqual(len(values), row.reference_n)
            rank = np.searchsorted(values, values, side="right")/len(values)
            np.testing.assert_array_equal(rank > .95, values >= row.upper_boundary_days_inclusive)
            np.testing.assert_array_equal(rank < .05, values < row.lower_boundary_days_exclusive)
            tied_groups += int(len(np.unique(values)) < len(values))
            different_p95 += int(row.descriptive_p95_days != row.upper_boundary_days_inclusive)
        self.assertGreater(tied_groups, 0)
        self.assertGreater(different_p95, 0)

    def test_references_train_only_and_not_candidate_self_inclusion(self):
        ref = self.inputs["train_reference"]
        self.assertTrue(ref.split.eq("train").all())
        self.assertFalse(set(ref.order_id) & set(self.orders.order_id))
        boundary = self.orders.order_purchase_timestamp.min()
        self.assertTrue(ref.end_timestamp.lt(boundary).all())
        self.assertTrue(self.groups.reference_latest_end_timestamp.lt(boundary).all())
        self.assertTrue(self.stages.loc[self.stages.assessable, "reference_n"].ge(30).all())
        for encoded in self.stages.loc[self.stages.assessable, "reference_context_json"]:
            self.assertIn("distance_band", json.loads(encoded))

    def test_insufficient_context_is_not_negative_prediction(self):
        unavailable = self.stages.loc[~self.stages.assessable]
        self.assertEqual(len(unavailable), 942)
        self.assertTrue(unavailable.long_stage_warning.isna().all())
        self.assertTrue(unavailable.short_stage_note.isna().all())
        self.assertTrue(unavailable.stage_signal.eq("not_assessable").all())
        self.assertTrue(unavailable.limitation_vi.str.len().gt(0).all())
        self.assertEqual(int(self.orders.assessment_coverage.eq("unavailable").sum()), 314)

    def test_valid_duration_can_still_be_outside_parent_complete_case_policy(self):
        excluded = self.stages.loc[self.stages.assessment_status.eq("parent_complete_case_policy")]
        self.assertGreater(len(excluded), 0)
        self.assertTrue(excluded.duration_days.notna().all())
        self.assertTrue(excluded.long_stage_warning.isna().all())
        self.assertFalse(excluded.comparison_available.any())

    def test_known_early_case_localizes_preparation_without_cause(self):
        oid = "0f916489801ab47804cc7da10fe0dd6d"
        case = self.orders.set_index("order_id").loc[oid]
        self.assertEqual(case.performance_state, "early")
        self.assertEqual(case.days_early, 8)
        self.assertEqual(case.primary_attention_status, "signal_present")
        self.assertEqual(json.loads(case.long_stage_warning_stages_json), ["preparation_handoff"])
        stage = self.stages.loc[self.stages.order_id.eq(oid) & self.stages.stage.eq("preparation_handoff")].iloc[0]
        self.assertAlmostEqual(stage.duration_days, 8.944155092592593)
        self.assertEqual(stage.reference_n, 648)
        self.assertGreater(stage.duration_days, stage.upper_boundary_days_inclusive)
        self.assertFalse(case.cause_required_for_detection)

    def test_short_only_is_secondary_not_primary_anomaly(self):
        short_only = (self.orders.short_stage_note.fillna(False) & ~self.orders.long_stage_warning.fillna(False)
                      & self.orders.performance_state.isin(["early", "on_date"]))
        self.assertGreater(int(short_only.sum()), 0)
        self.assertTrue(self.orders.loc[short_only, "primary_attention_status"].eq("no_primary_signal_under_policy").all())
        self.assertTrue(self.evidence.loc[self.evidence.signal_code.eq("short_stage"), "attention_level"].eq("secondary").all())
        self.assertNotIn("remove_from_training", self.orders)

    def test_signal_union_and_evidence_are_not_double_counted(self):
        primary = self.evidence.loc[self.evidence.attention_level.eq("primary")]
        selected = self.orders.loc[self.orders.primary_attention_status.eq("signal_present"), "order_id"]
        self.assertEqual(set(primary.order_id), set(selected))
        self.assertEqual(len(selected), 211)
        self.assertFalse(self.evidence.duplicated(["order_id", "stage", "signal_code"]).any())
        self.assertEqual(int(self.evidence.signal_code.eq("long_stage").sum()), 170)
        self.assertEqual(int(self.evidence.signal_code.eq("calendar_late").sum()), 149)

    def test_late_signal_can_survive_unavailable_stage_comparison(self):
        cases = self.orders.loc[self.orders.calendar_late_signal.fillna(False) & self.orders.assessment_coverage.eq("unavailable")]
        self.assertEqual(len(cases), 11)
        self.assertTrue(cases.primary_attention_status.eq("signal_present").all())
        self.assertTrue(cases.long_stage_warning.isna().all())

    def test_partial_observation_aggregation_uses_three_valued_flags(self):
        # Withhold real stage rows in memory only; no fabricated case or output.
        source = self.inputs["case_decisions.csv"]
        oid = "0f916489801ab47804cc7da10fe0dd6d"
        real = self.stages.loc[self.stages.order_id.eq(oid)]
        partial = real.loc[real.stage.ne("transport")]
        case = signals.order_signals(source.loc[source.order_id.eq(oid)], partial).iloc[0]
        self.assertTrue(case.long_stage_warning)
        self.assertEqual(case.assessment_coverage, "partial")
        self.assertEqual(case.primary_attention_status, "signal_present")
        no_observed_long = real.loc[real.stage.ne("preparation_handoff")]
        unknown = signals.order_signals(source.loc[source.order_id.eq(oid)], no_observed_long).iloc[0]
        self.assertTrue(pd.isna(unknown.long_stage_warning))
        self.assertEqual(unknown.primary_attention_status, "incomplete_assessment_no_primary_signal_observed")

    def test_root_cause_and_broad_labels_are_not_detection_inputs(self):
        alternate = dict(self.inputs)
        alternate["case_decisions.csv"] = alternate["case_decisions.csv"].drop(
            columns=["cause_or_seller_fault", "broad_anomaly_status", "human_reference_label"])
        frames, summary = signals.build_tables(alternate)
        self.assertEqual(summary, self.summary)
        for name in signals.TABLES:
            pd.testing.assert_frame_equal(frames[name], self.frames[name], check_exact=True)
        for name in ["order_signals.csv", "stage_signals.csv", "signal_evidence.csv"]:
            self.assertNotIn("cause_or_seller_fault", self.frames[name])
            self.assertNotIn("broad_anomaly_status", self.frames[name])

    def test_no_test_scoring_model_loading_training_labels_or_accuracy(self):
        for path in self.reads:
            self.assertNotIn("test_timing", path)
            self.assertNotIn("partition_plan.csv", path)
        self.assertEqual(self.summary["reference_labels_created"], 0)
        self.assertIsNone(self.summary["selected_ml_detector"])
        for key in ["model_trained", "test_scored", "split_activated", "anomaly_accuracy_computed", "near_real_time_validated", "cause_required_for_detection"]:
            self.assertFalse(self.summary[key])
        contract = self.frames["evaluation_contract.csv"].set_index("evaluation_scope")
        self.assertEqual(contract.loc["independent_signal_assessment", "status"], "not_scored_here")
        self.assertFalse(contract.root_cause_required_for_signal_reference.any())

    def test_roundtrip_preserves_nullable_flags_and_reproducible_report(self):
        manifest = signals.load_snapshot(self.output)
        restored = {name: signals.shared.read_table(self.output, name, manifest) for name in signals.TABLES}
        for name in signals.TABLES:
            pd.testing.assert_frame_equal(restored[name], self.frames[name], check_dtype=False, check_exact=True)
        self.assertEqual((self.output/"bao_cao_dau_hieu.md").read_text(), signals.render_report(restored, self.summary))
        self.assertIn("0f916489801ab47804cc7da10fe0dd6d", signals.render_case(restored, "0f916489801ab47804cc7da10fe0dd6d"))
        with self.assertRaises(ValueError):
            signals.render_case(restored, "outside_snapshot")

    def test_mismatched_reference_metadata_fails_instead_of_inventing_threshold(self):
        damaged = self.inputs["stage_evidence.csv"].copy()
        damaged.loc[damaged.index[0], "reference_n"] += 1
        with self.assertRaises(ValueError):
            signals.reference_groups(damaged, self.inputs["train_reference"])

    def test_existing_protected_nested_paths_and_tamper_are_rejected(self):
        with self.assertRaises(FileExistsError):
            signals.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [ROOT/"data/raw/signals", signals.criteria.DEFAULT_OUTPUT/"nested"]:
            with self.assertRaises(ValueError):
                signals.write_snapshot(self.frames, self.summary, self.provenance, path)
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"stage_signals.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            signals.load_snapshot(damaged)


if __name__ == "__main__":
    unittest.main()
