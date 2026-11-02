"""Temporal diagnostics on frozen real Olist cases; perturbations are test-only."""

from copy import deepcopy
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
import select_temporal_delivery_candidate as temporal


class TemporalCandidateSelectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(joblib, "load", side_effect=AssertionError("No model deserialization")), \
                patch.object(temporal.previous.product, "build_tables", side_effect=AssertionError("No new scores")):
            cls.inputs, cls.source = temporal.load_inputs()
            cls.frames, cls.summary, cls.selection = temporal.build_tables(cls.inputs)
        cls.cases = cls.frames["paired_cases.csv"]
        cls.periods = cls.frames["period_metrics.csv"]
        cls.context = temporal.previous.PRODUCT_CANDIDATE

    def test_identical_real_cohort_and_temporal_partition(self):
        self.assertEqual((self.summary["orders"], self.summary["late"]), (1307, 138))
        self.assertEqual(self.summary["late_counts_by_month"], {"2018-03": 121, "2018-04": 17, "2018-05": 0})
        self.assertEqual(self.summary["eligible_selection_months"], ["2018-03", "2018-04"])
        for candidate in temporal.CANDIDATES:
            for kind in ["month", "half_month"]:
                group = self.periods.loc[self.periods.candidate.eq(candidate) & self.periods.period_kind.eq(kind)]
                self.assertEqual(int(group.orders.sum()), 1307)
                self.assertEqual(int(group.late.sum()), 138)
                pooled = self.periods.loc[self.periods.candidate.eq(candidate) & self.periods.period_kind.eq("pooled")].iloc[0]
                for name in ["tp", "fp", "fn", "tn", "alerts"]:
                    self.assertEqual(group[name].sum(), pooled[name])

    def test_expected_monthly_confusions_and_selected_candidate(self):
        observed = self.periods.loc[self.periods.period_kind.eq("month")].set_index(["period", "candidate"])
        expected = {("2018-03", "upper_q950"): [107, 63, 14, 388],
                    ("2018-03", self.context): [91, 30, 30, 421],
                    ("2018-04", "upper_q950"): [14, 49, 3, 413],
                    ("2018-04", self.context): [12, 27, 5, 435],
                    ("2018-05", "upper_q950"): [0, 8, 0, 248],
                    ("2018-05", self.context): [0, 6, 0, 250]}
        for key, values in expected.items():
            self.assertEqual(observed.loc[key, ["tp", "fp", "fn", "tn"]].tolist(), values)
        self.assertEqual(self.selection["selected_candidate"], self.context)
        self.assertEqual(self.selection["status"], "provisional_validation_choice")
        self.assertFalse(self.selection["production_activated"])
        self.assertEqual(self.selection["threshold"], 0.)

    def test_single_class_may_is_not_fake_perfect_or_bad_auc(self):
        may = self.periods.loc[self.periods.period_kind.eq("month") & self.periods.period.eq("2018-05")]
        self.assertTrue(may[["roc_auc", "average_precision", "recall", "f1_for_selection"]].isna().all().all())
        self.assertTrue(may.f1_raw.eq(0).all())
        self.assertTrue(may.metric_status.eq("no_late_orders").all())
        self.assertEqual(may.alerts.tolist(), [8, 6])
        # Only existing nonlate, unflagged observations: undefined is not 1.
        subset = self.cases.loc[~self.cases[temporal.TARGET] & ~self.cases[self.context+"_flag"]]
        m = temporal.metrics(subset, self.context)
        for key in ["precision", "recall", "f1_raw", "roc_auc", "average_precision"]:
            self.assertTrue(pd.isna(m[key]), key)

    def test_macro_f1_is_equal_month_weight_not_pooled_f1(self):
        table = self.frames["candidate_stability.csv"].set_index("candidate")
        for candidate in temporal.CANDIDATES:
            eligible = self.periods.loc[self.periods.candidate.eq(candidate) & self.periods.period_kind.eq("month") & self.periods.both_classes]
            expected = np.mean([2*r.tp/(2*r.tp+r.fp+r.fn) for r in eligible.itertuples(index=False)])
            self.assertAlmostEqual(table.loc[candidate, "macro_monthly_f1"], expected, places=14)
            self.assertNotAlmostEqual(expected, table.loc[candidate, "pooled_f1"], places=4)
        self.assertAlmostEqual(table.loc[self.context, "macro_monthly_f1"], (182/242+24/56)/2, places=14)

    def test_auc_matches_actual_pairwise_ordering_per_month(self):
        for candidate, score in temporal.CANDIDATES.items():
            for month in self.summary["eligible_selection_months"]:
                rows = self.cases.loc[self.cases.purchase_month.eq(month)]
                positive = rows.loc[rows[temporal.TARGET], score].to_numpy()
                negative = rows.loc[~rows[temporal.TARGET], score].to_numpy()
                delta = positive[:, None]-negative[None, :]
                expected = ((delta > 0).sum()+.5*(delta == 0).sum())/delta.size
                actual = self.periods.loc[self.periods.candidate.eq(candidate) & self.periods.period_kind.eq("month") & self.periods.period.eq(month), "roc_auc"].item()
                self.assertAlmostEqual(actual, expected, places=14)

    def test_actual_reversal_week_is_not_hidden(self):
        self.assertEqual(self.summary["week_deletions"], 12)
        self.assertEqual(self.summary["week_deletion_winners"], {self.context: 11, "upper_q950": 1})
        self.assertEqual(self.summary["relevant_week_deletions"], 10)
        self.assertEqual(self.summary["relevant_week_deletion_winners"], {self.context: 9, "upper_q950": 1})
        self.assertFalse(self.summary["all_sensitivity_checks_agree"])
        reversals = self.summary["week_selection_reversals"]
        self.assertEqual(len(reversals), 1)
        self.assertEqual(reversals[0]["removed_period"], "2018-04-02")
        self.assertGreater(reversals[0]["q95_macro_f1"], reversals[0]["context_macro_f1"])
        self.assertLess(reversals[0]["q95_macro_f1"]-reversals[0]["context_macro_f1"], .001)

    def test_deletions_remove_whole_real_weeks(self):
        rows = self.frames["deletion_sensitivity.csv"].query("removed_kind == 'week'")
        self.assertEqual(rows.removed_orders.sum(), len(self.cases))
        for r in rows.itertuples(index=False):
            actual = int(self.cases.purchase_week.eq(r.removed_period).sum())
            self.assertEqual(r.removed_orders, actual)
            self.assertEqual(r.remaining_orders+actual, len(self.cases))

    def test_month_without_a_class_cannot_be_silently_dropped_after_deletion(self):
        subset = self.cases.loc[~(self.cases.purchase_month.eq("2018-04") & self.cases[temporal.TARGET])]
        ranked, winners = temporal.rank_candidates(temporal.period_metrics(subset), ["2018-03", "2018-04"])
        self.assertTrue(ranked.empty)
        self.assertEqual(winners, [])

    def test_tied_metrics_keep_both_candidates_without_arbitrary_id_selection(self):
        # Clone actual metric rows only to exercise the tie path; never published as research.
        rows = self.periods.loc[self.periods.candidate.eq("upper_q950")].copy()
        tied = pd.concat([rows, rows.assign(candidate=self.context)], ignore_index=True)
        _, winners = temporal.rank_candidates(tied, ["2018-03", "2018-04"])
        self.assertEqual(winners, sorted(temporal.CANDIDATES))

    def test_paired_case_disagreements_are_reconciled(self):
        both = self.frames["paired_comparisons.csv"].query("period == 'all'").set_index("outcome_group")
        self.assertEqual(both.loc["all", ["both_flag", "q95_only", "context_only", "neither_flag"]].tolist(), [150, 91, 16, 1050])
        self.assertEqual(both.loc["late", ["both_flag", "q95_only", "context_only", "neither_flag"]].tolist(), [92, 29, 11, 6])
        disagreements = self.frames["disagreement_cases.csv"]
        self.assertEqual(len(disagreements), 107)
        self.assertTrue(disagreements.order_id.is_unique)
        self.assertTrue(disagreements["upper_q950_flag"].ne(disagreements[self.context+"_flag"]).all())

    def test_cost_preferences_can_change_the_winner(self):
        crossover = self.summary["fn_to_fp_cost_crossover"]
        self.assertAlmostEqual(crossover, 57/18)
        self.assertAlmostEqual(120+crossover*17, 63+crossover*35)
        costs = self.frames["cost_sensitivity.csv"]
        self.assertEqual(costs.loc[costs.fn_to_fp_cost_ratio.eq(1) & costs.lowest_cost, "candidate"].tolist(), [self.context])
        self.assertEqual(costs.loc[costs.fn_to_fp_cost_ratio.eq(5) & costs.lowest_cost, "candidate"].tolist(), ["upper_q950"])

    def test_coverage_and_censoring_limitations_are_visible(self):
        coverage = self.frames["monthly_coverage.csv"].set_index("month")
        self.assertEqual(coverage.public_source_orders.sum(), 1621)
        self.assertEqual(coverage.excluded_public_orders.sum(), 314)
        may = coverage.loc["2018-05"]
        self.assertEqual((may.public_source_orders, may.compared_orders, may.excluded_unknown_outcome), (447, 256, 181))
        self.assertTrue(may.observation_cutoff_before_month_end)
        self.assertLess(may.followup_median_days, coverage.loc["2018-03", "followup_median_days"])

    def test_keep_no_test_training_labels_or_activation_claims(self):
        for key in ["test_scored", "models_loaded", "models_retrained", "thresholds_optimized", "production_activated",
                    "new_reference_labels", "confirmed_bottleneck_accuracy", "statistical_significance_claimed",
                    "independent_cross_validation", "near_real_time_validated"]:
            self.assertFalse(self.summary[key], key)

    def test_reordering_is_deterministic_and_does_not_mutate_sources(self):
        original = deepcopy(self.inputs)
        shuffled = {k: v.iloc[::-1].reset_index(drop=True) if isinstance(v, pd.DataFrame) else v for k, v in self.inputs.items()}
        frames, summary, selection = temporal.build_tables(shuffled)
        self.assertEqual(summary, self.summary)
        self.assertEqual(selection, self.selection)
        for name in frames:
            pd.testing.assert_frame_equal(frames[name], self.frames[name])
        for name, frame in original.items():
            if isinstance(frame, pd.DataFrame):
                pd.testing.assert_frame_equal(self.inputs[name], frame)

    def test_case_mismatch_duplicate_and_nonfinite_scores_are_rejected(self):
        bad = deepcopy(self.inputs)
        bad["scores"] = bad["scores"].iloc[1:]
        with self.assertRaisesRegex(ValueError, "complete case IDs"):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["scores"] = pd.concat([bad["scores"], bad["scores"].iloc[:1]], ignore_index=True)
        with self.assertRaises(ValueError):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["scores"].loc[0, "upper_q950"] = np.nan
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            temporal.prepare_cases(bad)

    def test_altered_target_date_threshold_or_flag_is_rejected(self):
        bad = deepcopy(self.inputs)
        bad["outcomes"].loc[0, temporal.TARGET] = not bad["outcomes"].loc[0, temporal.TARGET]
        with self.assertRaisesRegex(ValueError, "Target differs"):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["outcomes"].loc[0, "order_purchase_timestamp"] += pd.Timedelta(seconds=1)
        with self.assertRaisesRegex(ValueError, "observations differ"):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["definitions"].loc[bad["definitions"].candidate.eq("upper_q950"), "threshold"] = 1
        with self.assertRaisesRegex(ValueError, "threshold changed"):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["flags"].loc[0, "upper_q950"] = "True" if bad["flags"].loc[0, "upper_q950"] == "False" else "False"
        with self.assertRaisesRegex(ValueError, "flag mismatch"):
            temporal.prepare_cases(bad)

    def test_test_split_and_unobserved_endpoints_are_rejected(self):
        bad = deepcopy(self.inputs)
        bad["outcomes"].loc[0, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "validation dates"):
            temporal.prepare_cases(bad)
        bad = deepcopy(self.inputs)
        bad["outcomes"].loc[0, "visible_before_exclusive"] = bad["outcomes"].loc[0, "order_delivered_customer_date"]
        with self.assertRaisesRegex(ValueError, "observation cutoff"):
            temporal.prepare_cases(bad)

    def test_publication_roundtrip_hashes_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)/"selection"
            with patch.object(temporal, "load_inputs", return_value=(self.inputs, self.source)):
                summary = temporal.run(output)
            self.assertEqual(summary, self.summary)
            manifest = temporal.review.read_json(output/"manifest.json")
            temporal.shared.verify_files(output, manifest["output_hashes"])
            self.assertEqual(temporal.review.read_json(output/"selection.json"), self.selection)
            saved = pd.read_csv(output/"candidate_stability.csv", float_precision="round_trip")
            np.testing.assert_array_equal(saved.macro_monthly_f1.to_numpy(), self.frames["candidate_stability.csv"].macro_monthly_f1.to_numpy())
            report = (output/"bao_cao_on_dinh.md").read_text()
            self.assertIn("2018-04-02", report)
            self.assertIn("KHÔNG phải", report)
            with self.assertRaises(FileExistsError):
                temporal.run(output)


if __name__ == "__main__":
    unittest.main()
