"""Compare saved detectors using real guarded Olist observations, not fake labels."""

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
import compare_signal_detectors as comparison


class SignalDetectorComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selected_reads = []
        original = comparison.read_selected

        def selected(path, allowed, *args, **kwargs):
            result = original(path, allowed, *args, **kwargs)
            cls.selected_reads.append((str(path), set(allowed), set(result.order_id)))
            return result

        with patch.object(comparison, "read_selected", side_effect=selected), \
                patch.object(joblib, "load", side_effect=AssertionError("No model deserialization")), \
                patch.object(comparison.previous.batch.training, "score_bundle", side_effect=AssertionError("No model scoring")), \
                patch.object(comparison.previous.batch.training.engine, "fit_bank", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = comparison.load_inputs()
            cls.frames, cls.summary = comparison.build_tables(cls.inputs)
        cls.cases = cls.frames["common_cases.csv"]
        cls.flags = cls.frames["common_flags.csv"]
        cls.scores = cls.frames["common_scores.csv"]
        cls.table = cls.frames["candidate_comparison.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"comparison"
        comparison.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_same_guarded_population_and_every_configuration_retained(self):
        scope = self.frames["comparison_scope.csv"]
        self.assertEqual(len(scope), 1621)
        self.assertEqual(len(self.cases), 1307)
        self.assertEqual(set(self.cases.order_id), set(scope.loc[scope.included_common, "order_id"]))
        self.assertTrue(self.cases.split.eq("validation").all())
        self.assertTrue(self.cases.assessment_coverage.eq("complete").all())
        self.assertEqual(len(self.table), 120)
        self.assertEqual(self.table.family.nunique(), 7)
        self.assertEqual(self.table.score_column.nunique(), 21)
        self.assertEqual(self.table.compared_orders.unique().tolist(), [1307])
        self.assertEqual(set(self.table.candidate), set(self.inputs["candidates"].candidate))

    def test_numeric_sidecars_and_case_timestamps_only_parsed_for_common_ids(self):
        allowed = set(self.cases.order_id)
        self.assertEqual(len(self.selected_reads), 6)
        for path, requested, returned in self.selected_reads:
            self.assertEqual(requested, allowed, path)
            self.assertEqual(returned, allowed, path)
            self.assertNotIn("test", Path(path).name)
        for field in comparison.signals.performance.TIMES:
            self.assertTrue(self.cases[field].lt(self.cases.visible_before_exclusive).all())

    def test_outside_scope_is_unknown_not_negative_and_late_denominators_explicit(self):
        scope = self.frames["comparison_scope.csv"]
        excluded = scope.loc[~scope.included_common]
        self.assertEqual(len(excluded), 314)
        self.assertFalse(set(excluded.order_id) & set(self.flags.order_id))
        self.assertEqual(int(excluded.performance_state.eq("late").sum()), 11)
        self.assertEqual(self.summary["calendar_late_orders"], 138)
        self.assertEqual(self.summary["primary_signal_orders"], 200)
        self.assertTrue(excluded.comparison_status.eq("context_signal_assessment_incomplete").all())

    def test_all_flags_reproduce_frozen_strict_score_threshold(self):
        candidates = self.frames["candidate_definitions.csv"]
        for row in candidates.itertuples(index=False):
            np.testing.assert_array_equal(self.flags[row.candidate], self.scores[row.score_column].gt(row.threshold))
        self.assertEqual(self.flags.order_id.tolist(), self.cases.order_id.tolist())

    def test_candidate_definitions_and_parameter_slice_do_not_change_thresholds(self):
        definitions = self.frames["candidate_definitions.csv"]
        original = self.inputs["candidates"].sort_values("candidate").reset_index(drop=True)
        pd.testing.assert_frame_equal(definitions[original.columns], original, check_exact=True)
        focus = definitions.loc[definitions.focus_parameter_slice]
        self.assertEqual(len(focus), 18)
        self.assertEqual(int(focus.tail_fraction.eq(.05).sum()), 13)
        self.assertEqual(set(focus.loc[focus.tail_fraction.isna(), "candidate"]), set(comparison.protocol()["focus_fixed_candidates"]))
        self.assertEqual(set(self.frames["focus_comparison.csv"].candidate), set(focus.candidate))

    def test_every_overlap_count_recomputed_from_real_order_sets(self):
        long = set(self.cases.loc[self.cases.long_stage_warning, "order_id"])
        primary = set(self.cases.loc[self.cases.long_stage_warning | self.cases.calendar_late_signal, "order_id"])
        short = set(self.cases.loc[self.cases.short_stage_note, "order_id"])
        self.assertEqual(len(long), 165)
        for row in self.table.itertuples(index=False):
            selected = set(self.flags.loc[self.flags[row.candidate], "order_id"])
            self.assertEqual(row.candidate_flags, len(selected))
            self.assertEqual(row.both_context_long, len(selected & long))
            self.assertEqual(row.candidate_without_context_long, len(selected-long))
            self.assertEqual(row.context_long_without_candidate, len(long-selected))
            self.assertEqual(row.neither_context_long, len(self.cases)-len(long | selected))
            self.assertAlmostEqual(row.context_long_jaccard, len(long & selected)/len(long | selected))
            self.assertEqual(row.additional_flags_beyond_primary, len(selected-primary))
            self.assertEqual(row.additional_flags_with_short_note, len((selected-primary) & short))
            self.assertEqual(row.hypothetical_primary_union_orders, len(selected | primary))

    def test_actual_focus_examples_are_tradeoffs_not_accuracy(self):
        table = self.table.set_index("candidate")
        self.assertEqual(table.loc["if_raw_256_tail050", "candidate_flags"], 53)
        self.assertEqual(table.loc["if_raw_256_tail050", "both_context_long"], 51)
        self.assertEqual(table.loc["upper_q950", "candidate_flags"], 241)
        self.assertEqual(table.loc["upper_q950", "both_context_long"], 149)
        self.assertTrue(self.table.interpretation.eq("rule_cooccurrence_not_independent_accuracy").all())
        for field in ["true_positive", "false_positive", "false_negative", "accuracy", "precision", "recall", "f1"]:
            self.assertNotIn(field, self.table)

    def test_disagreement_rows_have_real_frozen_scores_but_no_reference_labels(self):
        disagreements = self.frames["focus_disagreement_cases.csv"]
        self.assertFalse(disagreements.duplicated(["candidate", "order_id"]).any())
        self.assertTrue(disagreements.independent_reference_label.isna().all())
        self.assertTrue(disagreements.candidate_flag.ne(disagreements.long_stage_warning).all())
        self.assertTrue(disagreements.view_status.eq("model_exposed_analyst_evidence_not_blind_review").all())
        self.assertEqual(set(disagreements.candidate), set(self.frames["focus_comparison.csv"].candidate))
        table = self.table.set_index("candidate")
        for name, group in disagreements.groupby("candidate"):
            metric = table.loc[name]
            self.assertEqual(len(group), metric.candidate_without_context_long+metric.context_long_without_candidate)
            expected = self.scores.set_index("order_id").loc[group.order_id, metric.score_column].to_numpy()
            np.testing.assert_array_equal(group.score, expected)
            self.assertTrue(group.candidate_flag.eq(group.score.gt(group.threshold)).all())
        self.assertEqual(self.summary["focus_disagreement_unique_orders"], disagreements.order_id.nunique())

    def test_stage_table_is_cooccurrence_not_fabricated_localization(self):
        table = self.frames["stage_overlap.csv"]
        self.assertEqual(len(table), 360)
        stage = self.inputs["stages"]
        for row in table.iloc[::7].itertuples(index=False):
            reference = set(stage.loc[stage.stage.eq(row.stage) & stage.long_stage_warning, "order_id"])
            selected = set(self.flags.loc[self.flags[row.candidate], "order_id"])
            self.assertEqual(row.reference_stage_long_orders, len(reference))
            self.assertEqual(row.both, len(selected & reference))
            self.assertEqual(row.reference_only, len(reference-selected))
        self.assertTrue(table.interpretation.eq("cooccurrence_not_ML_attribution_or_causal_localization").all())

    def test_month_and_distance_denominators_reconcile_with_common_population(self):
        grouped = self.frames["group_comparison.csv"]
        summary = self.table.set_index("candidate")
        for (name, dimension), group in grouped.groupby(["candidate", "dimension"]):
            self.assertEqual(group.compared_orders.sum(), len(self.cases))
            self.assertEqual(group.candidate_flags.sum(), summary.loc[name, "candidate_flags"])
            self.assertEqual(group.both.sum(), summary.loc[name, "both_context_long"])
            self.assertEqual(group.context_long_orders.sum(), 165)
            np.testing.assert_allclose(group.candidate_rate, group.candidate_flags/group.compared_orders)
        self.assertEqual(set(grouped.dimension), {"purchase_month", "distance_band"})

    def test_group_counts_recomputed_independently(self):
        grouped = self.frames["group_comparison.csv"]
        for row in grouped.iloc[::41].itertuples(index=False):
            values = self.cases.order_purchase_timestamp.dt.strftime("%Y-%m") if row.dimension == "purchase_month" else self.cases.distance_band
            ids = set(self.cases.loc[values.eq(row.group), "order_id"])
            selected = set(self.flags.loc[self.flags[row.candidate] & self.flags.order_id.isin(ids), "order_id"])
            self.assertEqual(row.compared_orders, len(ids))
            self.assertEqual(row.candidate_flags, len(selected))

    def test_frontier_uses_both_objectives_and_retains_strict_ties(self):
        for row in self.table.itertuples(index=False):
            others = self.table
            dominates = ((others.both_context_long >= row.both_context_long)
                         & (others.candidate_without_context_long <= row.candidate_without_context_long)
                         & ((others.both_context_long > row.both_context_long)
                            | (others.candidate_without_context_long < row.candidate_without_context_long)))
            self.assertEqual(row.rule_tradeoff_nondominated, not dominates.any())
        frontier = self.frames["rule_tradeoff_frontier.csv"]
        self.assertEqual(len(frontier), 11)
        self.assertEqual(set(frontier.candidate), set(self.table.loc[self.table.rule_tradeoff_nondominated, "candidate"]))
        # Actual rows with equal counts must receive the same diagnostic status.
        self.assertTrue(self.table.groupby(["both_context_long", "candidate_without_context_long"]).rule_tradeoff_nondominated.nunique().eq(1).all())

    def test_family_ranges_reconcile_without_votes(self):
        families = self.frames["family_summary.csv"]
        self.assertEqual(families.configurations.sum(), 120)
        self.assertEqual(families.frontier_configurations.sum(), 11)
        for row in families.itertuples(index=False):
            group = self.table.loc[self.table.family.eq(row.family)]
            self.assertEqual(row.min_flags, group.candidate_flags.min())
            self.assertEqual(row.max_flags, group.candidate_flags.max())

    def test_empty_real_subsets_have_undefined_jaccard_not_perfect_agreement(self):
        empty = self.flags.iloc[:0, 1]
        result = comparison.intersections(empty, empty)
        self.assertTrue(np.isnan(result["jaccard"]))
        self.assertEqual(result["both"], 0)
        self.assertEqual(result["neither"], 0)

    def test_alignment_and_unavailable_reference_fail_closed(self):
        changed = dict(self.inputs)
        changed["flags"] = self.inputs["flags"].iloc[::-1].reset_index(drop=True)
        with self.assertRaises(ValueError):
            comparison.build_tables(changed)
        changed = dict(self.inputs)
        changed["cases"] = self.inputs["cases"].copy()
        changed["cases"].loc[0, "long_stage_warning"] = pd.NA
        with self.assertRaises(ValueError):
            comparison.build_tables(changed)

    def test_no_model_training_labels_test_scoring_or_causal_requirement(self):
        self.assertIsNone(self.summary["selected_ml_detector"])
        self.assertEqual(self.summary["reference_labels_created"], 0)
        for field in ["model_loaded", "training_performed", "thresholds_changed", "independent_accuracy_computed",
                      "test_scored", "near_real_time_validated", "primary_policy_changed", "root_cause_required"]:
            self.assertFalse(self.summary[field])

    def test_roundtrip_and_report_reconstruction(self):
        manifest = comparison.load_snapshot(self.output)
        restored = {name: comparison.shared.read_table(self.output, name, manifest) for name in comparison.TABLES}
        for name in comparison.TABLES:
            pd.testing.assert_frame_equal(restored[name], self.frames[name], check_dtype=False, check_exact=True)
        self.assertEqual((self.output/"bao_cao_so_sanh.md").read_text(), comparison.render_report(restored, self.summary))

    def test_existing_and_protected_destinations_and_tampering_rejected(self):
        with self.assertRaises(FileExistsError):
            comparison.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [ROOT/"data/raw/comparison", comparison.signals.DEFAULT_OUTPUT/"nested"]:
            with self.assertRaises(ValueError):
                comparison.write_snapshot(self.frames, self.summary, self.provenance, path)
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"common_flags.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            comparison.load_snapshot(damaged)


if __name__ == "__main__":
    unittest.main()
