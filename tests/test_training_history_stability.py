"""History experiments use real imported Olist observations, never fabricated cases."""

import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import evaluate_training_history_stability as history


SNAPSHOT = ROOT / "data/imported/olist_bed_bath_table_import_v1"
PLANS = [ROOT / f"templates/process_history_stability_v1/fit_{months}m.json" for months in [9, 6, 3]]
BASELINE = ROOT / "models/datasets/olist_bed_bath_table_training_v1"


class TrainingHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = history.load_inputs(SNAPSHOT, PLANS)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "study"
        cls.read_paths, cls.model_load_paths = [], []
        original_read, original_load = pd.read_csv, history.training.joblib.load

        def read(path, *args, **kwargs):
            cls.read_paths.append(str(path))
            return original_read(path, *args, **kwargs)

        def load(path, *args, **kwargs):
            cls.model_load_paths.append(str(path))
            return original_load(path, *args, **kwargs)

        with patch.object(history.training.engine, "fit_bank", wraps=history.training.engine.fit_bank) as fit, \
             patch.object(pd, "read_csv", side_effect=read), \
             patch.object(history.training.joblib, "load", side_effect=load):
            cls.summary = history.run_study(SNAPSHOT, PLANS, cls.output)
            cls.fit_calls = fit.call_count
        cls.manifest = json.loads((cls.output / "manifest.json").read_text())
        cls.tables = {path.name: pd.read_csv(path, float_precision="round_trip") for path in cls.output.glob("*.csv")}
        cls.candidates, cls.flags = None, {}
        for run_id in cls.summary["run_ids"]:
            directory = cls.output / "runs" / run_id
            candidates = pd.read_csv(directory / "candidates.csv", float_precision="round_trip")
            cls.candidates = candidates if cls.candidates is None else cls.candidates
            cls.flags[run_id] = history.comparison.read_flags(directory / "validation_flags.csv", candidates)

    def test_real_populations_and_honest_experiment_scope(self):
        self.assertEqual(self.summary["fit_orders_by_run"], {"olist_history_9m_v1": 3190, "olist_history_6m_v1": 2413, "olist_history_3m_v1": 1181})
        self.assertEqual(self.summary["common_calibration_orders"], 1412)
        self.assertEqual(self.summary["common_validation_all_orders"], 1853)
        self.assertEqual(self.summary["common_validation_scored_orders"], 1523)
        self.assertEqual(self.summary["common_validation_unscored_orders"], 330)
        self.assertEqual(self.summary["fresh_ml_fits"], 30)
        self.assertEqual(self.summary["unique_candidate_configurations"], 120)
        self.assertEqual(self.summary["window_configuration_results"], 360)
        self.assertEqual(self.fit_calls, 3)
        self.assertIsNone(self.summary["selected_candidate"])
        for key in ["accuracy_computed", "anomaly_probabilities_computed", "human_labels_created", "test_scored",
                    "existing_fitted_model_reused", "independent_new_data_evaluation", "rolling_origin_backtest"]:
            self.assertFalse(self.summary[key])

    def test_fit_nesting_identical_calibration_validation_and_real_event_cutoffs(self):
        previous = None
        for design, plan, _, _ in self.inputs:
            subsets, context, ledger, _ = design
            ids = set(subsets["fit"].order_id)
            if previous is not None:
                self.assertLess(ids, previous)
            previous = ids
            self.assertEqual(len(ledger), 7333)
            self.assertTrue(ledger.order_id.is_unique)
            for role, end in [("fit", "fit_end_exclusive"), ("calibration", "train_end_exclusive"), ("validation", "validation_end_exclusive")]:
                selected = context.loc[context.inner_role.eq(role)]
                self.assertTrue(selected[history.training.TIMES].lt(pd.Timestamp(plan[end])).all().all())
            for role in ["calibration", "validation"]:
                pd.testing.assert_frame_equal(subsets[role], self.inputs[0][0][0][role])
        self.assertEqual(self.tables["window_summary.csv"].outside_scope_orders.tolist(), [0, 796, 2056])

    def test_no_test_or_review_parsing_and_no_previous_model_loaded(self):
        self.assertFalse(any("test_timing" in path or "manual" in path or "review" in path for path in self.read_paths))
        self.assertGreater(len(self.model_load_paths), 0)
        self.assertTrue(all(Path(path).is_relative_to(Path(self.temporary.name)) for path in self.model_load_paths))
        self.assertFalse(any(str(BASELINE) in path for path in self.model_load_paths))

    def test_nine_month_refit_reproduces_existing_baseline_without_loading_weights(self):
        directory = self.output / "runs" / "olist_history_9m_v1"
        for name in ["candidates.csv", "validation_flags.csv", "validation_scores.csv", "timing_thresholds.csv"]:
            expected = pd.read_csv(BASELINE / name, float_precision="round_trip")
            actual = pd.read_csv(directory / name, float_precision="round_trip")
            pd.testing.assert_frame_equal(actual, expected, check_exact=True)

    def test_recursive_hashes_and_reloading_after_atomic_publication(self):
        hashes = self.manifest["output_hashes"]
        files = {str(path.relative_to(self.output)) for path in self.output.rglob("*") if path.is_file()}
        self.assertEqual(set(hashes), files-{"manifest.json"})
        for name, digest in hashes.items():
            self.assertEqual(history.training.importer.audit.file_hash(self.output/name), digest)
        checks = self.tables["reload_checks.csv"]
        self.assertTrue(checks.candidate_flags_match_exactly.all())
        self.assertTrue(checks.scores_match_with_tolerance.all())
        self.assertFalse(checks.refitting_performed.any())
        for run_id in self.summary["run_ids"]:
            bundle, _ = history.training.load_model_bundle(self.output/"runs"/run_id)
            history.training.verify_sources(bundle["provenance"])
            self.assertTrue(Path(bundle["provenance"]["plan_path"]).is_file())

    def test_pairwise_counts_jaccard_and_change_fraction_match_actual_sets(self):
        table = self.tables["window_pairwise_agreement.csv"]
        self.assertEqual(len(table), 360)
        self.assertFalse(table.duplicated(["candidate", "left_run_id", "right_run_id"]).any())
        for row in table.itertuples(index=False):
            left_frame, right_frame = self.flags[row.left_run_id], self.flags[row.right_run_id]
            left = set(left_frame.loc[left_frame[row.candidate], "order_id"])
            right = set(right_frame.loc[right_frame[row.candidate], "order_id"])
            self.assertEqual(row.both_flag, len(left & right))
            self.assertEqual(row.left_only, len(left-right))
            self.assertEqual(row.right_only, len(right-left))
            self.assertEqual(row.neither_flag, 1523-len(left | right))
            self.assertEqual(row.changed_decisions, len(left ^ right))
            self.assertAlmostEqual(row.changed_decision_fraction, len(left ^ right)/1523)
            if left | right:
                self.assertAlmostEqual(row.jaccard_overlap, len(left & right)/len(left | right))
            else:
                self.assertTrue(pd.isna(row.jaccard_overlap))

    def test_change_evidence_and_candidate_ranges_reconcile_without_accuracy_claim(self):
        changes = self.tables["changed_case_decisions.csv"]
        indexed = {run_id: frame.set_index("order_id") for run_id, frame in self.flags.items()}
        self.assertFalse(changes.duplicated(["order_id", "candidate"]).any())
        self.assertEqual(len(changes), self.summary["changed_case_candidate_pairs"])
        self.assertEqual(changes.order_id.nunique(), self.summary["unique_cases_with_any_candidate_change"])
        for row in changes.iloc[::37].itertuples(index=False):
            expected = [run_id for run_id, frame in indexed.items() if frame.at[row.order_id, row.candidate]]
            self.assertEqual(json.loads(row.flagged_run_ids_json), expected)
            self.assertEqual(row.windows_flagged, len(expected))
            self.assertIn(row.windows_flagged, [1, 2])
        for row in self.tables["candidate_stability.csv"].itertuples(index=False):
            counts = [int(frame[row.candidate].sum()) for frame in self.flags.values()]
            self.assertEqual(row.min_flagged_cases, min(counts))
            self.assertEqual(row.max_flagged_cases, max(counts))
            self.assertAlmostEqual(row.flag_rate_range_pp, 100*(max(counts)-min(counts))/1523)
            self.assertEqual(row.cases_with_changed_decision, int(changes.candidate.eq(row.candidate).sum()))

    def test_monthly_coverage_and_rates_use_same_population(self):
        coverage = self.tables["monthly_coverage.csv"]
        metrics = self.tables["candidate_window_metrics.csv"].set_index(["run_id", "candidate"])
        for _, group in coverage.groupby("run_id"):
            self.assertEqual(group.all_cases.tolist(), [660, 598, 595])
            self.assertEqual(group.scored_cases.tolist(), [642, 536, 345])
        for key, group in self.tables["monthly_candidate_rates.csv"].groupby(["run_id", "candidate"]):
            self.assertEqual(group.flagged_cases.sum(), metrics.at[key, "validation_flags"])
            self.assertAlmostEqual(np.average(group.flag_fraction_of_scored, weights=group.scored_cases),
                                   metrics.at[key, "validation_flag_fraction"])

    def test_physical_day_thresholds_change_despite_fixed_iqr_multipliers(self):
        thresholds = self.tables["timing_thresholds_by_window.csv"]
        self.assertEqual(len(thresholds), 9)
        np.testing.assert_allclose(thresholds.long_threshold_days, thresholds.q75_days+1.5*thresholds.iqr_days)
        self.assertTrue(thresholds.groupby("feature").long_threshold_days.nunique().gt(1).all())
        candidates = self.tables["candidate_window_metrics.csv"]
        iqr = candidates.loc[candidates.score_column.str.startswith("iqr_")]
        self.assertTrue(iqr.groupby("candidate").threshold.nunique().eq(1).all())

    def test_common_unflagged_real_cases_have_undefined_jaccard_not_perfect_score(self):
        quiet = None
        for frame in self.flags.values():
            ids = set(frame.loc[~frame[self.candidates.candidate].any(axis=1), "order_id"])
            quiet = ids if quiet is None else quiet & ids
        self.assertTrue(quiet)
        subsets = {run_id: frame.loc[frame.order_id.isin(quiet)] for run_id, frame in self.flags.items()}
        tables = history.compare_windows(self.candidates, subsets)
        self.assertTrue(tables["window_pairwise_agreement.csv"].jaccard_overlap.isna().all())
        self.assertTrue(tables["candidate_stability.csv"].cases_with_changed_decision.eq(0).all())
        self.assertTrue(tables["changed_case_decisions.csv"].empty)

    def test_case_reordering_is_harmless_but_missing_duplicate_empty_cases_fail(self):
        reordered = {run_id: frame.iloc[::-1] for run_id, frame in self.flags.items()}
        expected = history.compare_windows(self.candidates, self.flags)
        actual = history.compare_windows(self.candidates, reordered)
        for name in expected:
            pd.testing.assert_frame_equal(actual[name], expected[name])
        first = next(iter(self.flags))
        with self.assertRaisesRegex(ValueError, "IDs differ"):
            history.compare_windows(self.candidates, {**self.flags, first: self.flags[first].iloc[1:]})
        with self.assertRaisesRegex(ValueError, "unique"):
            history.compare_windows(self.candidates, {**self.flags, first: pd.concat([self.flags[first], self.flags[first].iloc[:1]])})
        with self.assertRaisesRegex(ValueError, "No scored"):
            history.compare_windows(self.candidates, {name: frame.iloc[:0] for name, frame in self.flags.items()})

    def test_plan_mismatches_rejected_before_fitting(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            history.validate_designs(self.inputs[:1])
        with self.assertRaisesRegex(ValueError, "longest to shortest"):
            history.validate_designs(self.inputs[::-1])
        with self.assertRaisesRegex(ValueError, "distinct"):
            history.validate_designs([self.inputs[0], self.inputs[0]])
        # Change configuration metadata only, never order observations or labels.
        for field, value in [("fit_end_exclusive", "2017-11-01 00:00:00"), ("marketplace_id", "different_platform")]:
            inputs = list(self.inputs)
            design, plan, identity, provenance = inputs[1]
            inputs[1] = (design, {**plan, field: value}, identity, provenance)
            with self.assertRaisesRegex(ValueError, "share identity"):
                history.validate_designs(inputs)

    def test_changed_calibration_features_and_non_nested_fit_fail(self):
        for role, expression in [("calibration", "Common calibration"), ("fit", "strictly nested")]:
            inputs = copy.deepcopy(self.inputs)
            subsets = inputs[1][0][0]
            subsets[role] = subsets[role].iloc[1:] if role == "calibration" else inputs[0][0][0]["fit"]
            with self.assertRaisesRegex(ValueError, expression):
                history.validate_designs(inputs)

    def test_insufficient_real_fit_data_fails_without_padding(self):
        orders, manifest = history.training.importer.load_import_snapshot(SNAPSHOT)
        plan = {**self.inputs[0][1], "purchase_start_inclusive": "2017-11-25 00:00:00"}
        with self.assertRaisesRegex(ValueError, "at least 512"):
            history.training.build_design(orders, plan, history.training.identity_from_config(manifest["config"]))

    def test_existing_output_refused_before_loading_or_fitting(self):
        with patch.object(history, "load_inputs", side_effect=AssertionError("Must stop before source loading")):
            with self.assertRaises(FileExistsError):
                history.run_study(SNAPSHOT, PLANS, self.output)

    def test_source_change_aborts_atomic_publication(self):
        output = Path(self.temporary.name) / "aborted_study"
        with patch.object(history.training, "verify_sources", side_effect=ValueError("Training source/config changed")), \
             patch.object(history.training.engine, "fit_bank", side_effect=AssertionError("Must not fit changed inputs")):
            with self.assertRaisesRegex(ValueError, "source/config changed"):
                history.run_study(SNAPSHOT, PLANS, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
