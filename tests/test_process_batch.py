"""Batch checks use real Olist development observations; no generated process facts or labels."""

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
import score_process_batch as batch


MODEL = ROOT / "models/datasets/olist_bed_bath_table_training_v1"
SNAPSHOT = ROOT / "data/imported/olist_bed_bath_table_import_v1"
CONFIG = ROOT / "templates/process_batch_v1/olist_validation_replay.json"


class ProcessBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = batch.load_inputs(MODEL, SNAPSHOT, CONFIG)
        (cls.bundle, cls.context, cls.ledger, cls.reference, cls.reference_context,
         cls.score_columns, cls.identity, cls.config, cls.provenance) = cls.inputs
        cls.frames, cls.summary = batch.build_outputs(*cls.inputs[:-1])
        cls.orders, cls.import_manifest = batch.training.importer.load_import_snapshot(SNAPSHOT)
        _, cls.development, _, _ = batch.training.build_design(cls.orders, cls.bundle["plan"], cls.identity)
        cls.reserved = batch.load_reservations(cls.config, cls.development.order_id)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)

    def prepare(self, orders=None, config=None, identity=None):
        return batch.prepare_batch(self.orders if orders is None else orders, self.bundle, self.development,
                                   self.identity if identity is None else identity,
                                   self.config if config is None else config, self.reserved)

    def test_replay_counts_preserve_all_real_cases_and_do_not_score_test(self):
        self.assertEqual(self.summary["batch_cases"], 1853)
        self.assertEqual(self.summary["scored_cases"], 1523)
        self.assertEqual(self.summary["unscored_cases"], 330)
        self.assertEqual(self.summary["reference_timing_cases"], 1412)
        self.assertEqual(len(self.ledger), 7333)
        fields = ["order_id", "order_status", *batch.TIMES, *batch.FEATURES]
        original = self.orders.loc[self.orders.split.eq("validation"), fields].reset_index(drop=True)
        pd.testing.assert_frame_equal(self.frames["case_results.csv"][fields], original, check_dtype=False)
        self.assertFalse(set(self.frames["case_results.csv"].order_id) & self.reserved)
        for name in ["test_scored", "refitting_performed", "thresholds_updated", "human_labels_created",
                     "accuracy_computed", "anomaly_probabilities_computed", "independent_new_data_evaluation"]:
            self.assertFalse(self.summary[name])
        self.assertIsNone(self.summary["selected_candidate"])

    def test_replay_matches_saved_scores_flags_and_existing_auto_actions(self):
        scores = pd.read_csv(MODEL / "validation_scores.csv", dtype={"order_id": "string"}, float_precision="round_trip")
        flags = pd.read_csv(MODEL / "validation_flags.csv", dtype={"order_id": "string"})
        old = pd.read_csv(MODEL / "auto_case_results.csv", dtype={"order_id": "string"})
        pd.testing.assert_frame_equal(self.frames["candidate_scores.csv"], scores, check_dtype=False, rtol=1e-12, atol=1e-12)
        pd.testing.assert_frame_equal(self.frames["candidate_flags.csv"], flags, check_dtype=False, check_exact=True)
        fields = ["order_id", "auto_action", "reason_codes_json"]
        pd.testing.assert_frame_equal(self.frames["case_results.csv"][fields],
                                      old.loc[old.split.eq("validation"), fields].reset_index(drop=True), check_dtype=False)

    def test_scores_never_refit_or_change_frozen_candidates(self):
        before = self.bundle["bank"]["candidates"].copy(deep=True)
        with patch.object(batch.training.engine, "fit_bank", side_effect=AssertionError("No batch fitting")), \
             patch.object(batch.training.engine, "learn_thresholds", side_effect=AssertionError("No batch recalibration")):
            _, scores, flags = batch.score_cases(self.bundle, self.context, self.identity, self.score_columns)
        pd.testing.assert_frame_equal(scores, self.frames["candidate_scores.csv"])
        pd.testing.assert_frame_equal(flags, self.frames["candidate_flags.csv"])
        pd.testing.assert_frame_equal(before, self.bundle["bank"]["candidates"])

    def test_positive_evidence_reconciles_every_flag_and_threshold(self):
        flags = self.frames["candidate_flags.csv"].set_index("order_id")
        evidence = self.frames["positive_candidate_evidence.csv"]
        self.assertEqual(len(evidence), int(flags.to_numpy().sum()))
        self.assertFalse(evidence.duplicated(["order_id", "candidate"]).any())
        self.assertTrue(evidence.score_excess.gt(0).all())
        np.testing.assert_allclose(evidence.score_excess, evidence.score-evidence.threshold)
        candidates = self.bundle["bank"]["candidates"].set_index("candidate")
        scores = self.frames["candidate_scores.csv"].set_index("order_id")
        for row in evidence.iloc[::37].itertuples(index=False):
            self.assertTrue(flags.at[row.order_id, row.candidate])
            self.assertEqual(row.threshold, candidates.at[row.candidate, "threshold"])
            self.assertEqual(row.score, scores.at[row.order_id, row.score_column])

    def test_ks_distance_matches_direct_empirical_cdf_on_real_observations(self):
        diagnostics = self.frames["distribution_diagnostics.csv"].query("window == 'all_batch'").set_index("feature")
        for feature in batch.FEATURES:
            reference = np.sort(self.reference[feature].to_numpy())
            current = np.sort(self.frames["timing_features.csv"][feature].to_numpy())
            points = np.unique(np.concatenate([reference, current]))
            delta = np.abs(np.searchsorted(reference, points, side="right")/len(reference)
                           - np.searchsorted(current, points, side="right")/len(current))
            self.assertAlmostEqual(diagnostics.at[feature, "ks_distance"], delta.max())
            self.assertAlmostEqual(diagnostics.at[feature, "wasserstein_days"], np.sum(delta[:-1]*np.diff(points)))
            self.assertAlmostEqual(diagnostics.at[feature, "median_shift_days"], np.median(current)-np.median(reference))
            self.assertAlmostEqual(diagnostics.at[feature, "q90_shift_days"], np.quantile(current, .9)-np.quantile(reference, .9))

    def test_drift_policy_is_explicit_and_does_not_change_case_thresholds(self):
        current = self.frames["timing_features.csv"]
        identical = batch.drift_diagnostics(self.reference, self.reference, self.config, "same")
        self.assertTrue(identical.ks_distance.eq(0).all())
        self.assertTrue(identical.wasserstein_days.eq(0).all())
        self.assertTrue(identical.distribution_warning.eq(False).all())
        boundary = float(self.frames["distribution_diagnostics.csv"].iloc[0].ks_distance)
        config = {**self.config, "drift_ks_warning": boundary}
        result = batch.drift_diagnostics(self.reference, current, config, "boundary")
        self.assertTrue(result.distribution_warning.iloc[0])
        self.assertNotIn("pvalue", result.columns)

    def test_small_and_empty_real_subsets_have_no_drift_warning_conclusion(self):
        for subset in [self.reference.iloc[:1], self.reference.iloc[:0]]:
            result = batch.drift_diagnostics(self.reference, subset, self.config, "small")
            self.assertTrue(result.distribution_warning.isna().all())
            self.assertTrue(result.status.eq("insufficient_cases").all())
        empty = batch.drift_diagnostics(self.reference, self.reference.iloc[:0], self.config, "empty")
        self.assertTrue(empty.ks_distance.isna().all())

    def test_all_unscorable_real_batch_keeps_context_and_unavailable_rates(self):
        context = self.context.loc[~self.context.timing_input_eligible].copy()
        frames, summary = batch.build_outputs(self.bundle, context, self.ledger, self.reference,
                                              self.reference_context, self.score_columns, self.identity, self.config)
        self.assertEqual(summary["scored_cases"], 0)
        self.assertEqual(summary["batch_cases"], 330)
        self.assertTrue(frames["candidate_scores.csv"].empty)
        self.assertTrue(frames["positive_candidate_evidence.csv"].empty)
        self.assertTrue(frames["candidate_rates.csv"].flag_fraction_of_scored.isna().all())
        self.assertTrue(frames["case_results.csv"].model_candidate_signal.isna().all())
        self.assertTrue(frames["distribution_diagnostics.csv"].distribution_warning.isna().all())

    def test_monthly_denominators_and_fixed_cutoff_are_not_online_backtests(self):
        rates = self.frames["candidate_rates.csv"]
        monthly = rates.loc[rates.window.ne("all_batch")]
        summed = monthly.groupby("candidate")[["all_cases", "scored_cases", "flagged_cases"]].sum()
        all_rows = rates.loc[rates.window.eq("all_batch")].set_index("candidate")
        pd.testing.assert_frame_equal(summed, all_rows[summed.columns].sort_index())
        self.assertTrue(self.context.event_time_cutoff_exclusive.eq(pd.Timestamp("2018-06-01")).all())
        march = {**self.config, "purchase_end_exclusive": "2018-04-01 00:00:00"}
        context, _ = self.prepare(config=march)
        self.assertTrue(context.event_time_cutoff_exclusive.eq(pd.Timestamp("2018-06-01")).all())
        changed = {**march, "observation_cutoff_exclusive": "2018-04-01 00:00:00"}
        with self.assertRaisesRegex(ValueError, "fixed cutoff"):
            self.prepare(config=changed)

    def test_quality_reference_is_all_calibration_period_purchases(self):
        quality = self.frames["quality_summary.csv"]
        reference = quality.loc[quality.window.eq("reference_calibration_purchase_cohort")]
        self.assertTrue(reference.all_cases.eq(len(self.reference_context)).all())
        self.assertGreater(len(self.reference_context), len(self.reference))
        self.assertEqual(int(reference.loc[reference.indicator.str.startswith("status_"), "cases"].sum())
                         - int(reference.loc[reference.indicator.eq("status_delivery_conflict"), "cases"].sum()), len(self.reference_context))
        for window, group in self.frames["case_results.csv"].groupby("purchase_month"):
            measured = quality.loc[quality.window.eq(str(window))].set_index("indicator")
            self.assertEqual(measured.at["timing_eligible", "cases"], int(group.timing_input_eligible.sum()))
            self.assertEqual(measured.at["missing_actual_milestone", "cases"], int(group[batch.TIMES].isna().any(axis=1).sum()))

    def test_new_mode_refuses_old_development_and_replay_is_not_new(self):
        config = {**self.config, "mode": "new_batch", "cutoff_basis": "source_extract"}
        with self.assertRaisesRegex(ValueError, "overlap any prior development"):
            self.prepare(config=config)
        with self.assertRaisesRegex(ValueError, "identity mismatch"):
            self.prepare(identity={**self.identity, "marketplace_id": "different_source"})

    def test_reservations_parse_only_identity_markers_and_refuse_hash_changes(self):
        original = batch.pd.read_csv
        calls = []
        def read(*args, **kwargs):
            calls.append(kwargs.get("usecols"))
            return original(*args, **kwargs)
        with patch.object(batch.pd, "read_csv", side_effect=read):
            reserved = batch.load_reservations(self.config, self.development.order_id)
        self.assertEqual(len(reserved), 1691)
        self.assertEqual(calls, [["order_id", "split"]])
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            batch.load_reservations({**self.config, "reservation_ledger_sha256": "0"*64}, self.development.order_id)
        # Mark an existing real development ID reserved to test rejection without reading test observations.
        with self.assertRaisesRegex(ValueError, "Reserved test"):
            batch.prepare_batch(self.orders, self.bundle, self.development, self.identity, self.config,
                                {self.context.order_id.iloc[0]})

    def test_duplicate_missing_scope_and_invalid_template_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "unique"):
            self.prepare(orders=pd.concat([self.orders, self.orders.iloc[:1]]))
        with self.assertRaisesRegex(ValueError, "No batch cases"):
            self.prepare(config={**self.config, "purchase_start_inclusive": "2020-01-01 00:00:00",
                                 "purchase_end_exclusive": "2020-02-01 00:00:00", "observation_cutoff_exclusive": "2020-02-01 00:00:00"})
        template = json.loads((ROOT / "templates/process_batch_v1/new_batch_config.json").read_text())
        with self.assertRaises(ValueError):
            batch.validate_config(template)
        for config in [{**self.config, "drift_ks_warning": float("nan")}, {**self.config, "drift_min_cases": True},
                       {**self.config, "observation_cutoff_exclusive": "2018-06-01T00:00:00Z"}]:
            with self.assertRaises(ValueError):
                batch.validate_config(config)

    def test_replay_rejects_missing_real_cases_and_changed_scope(self):
        removed = self.orders.loc[~self.orders.order_id.eq(self.context.order_id.iloc[0])]
        with self.assertRaises(AssertionError):
            self.prepare(orders=removed)
        with self.assertRaisesRegex(ValueError, "original validation period"):
            self.prepare(config={**self.config, "purchase_start_inclusive": "2018-02-01 00:00:00"})

    def test_row_order_does_not_change_scores_or_drift(self):
        context, ledger = self.prepare(orders=self.orders.iloc[::-1])
        frames, _ = batch.build_outputs(self.bundle, context, ledger, self.reference.iloc[::-1],
                                        self.reference_context, self.score_columns, self.identity, self.config)
        for name in ["candidate_scores.csv", "candidate_flags.csv", "distribution_diagnostics.csv", "case_results.csv"]:
            pd.testing.assert_frame_equal(frames[name], self.frames[name])

    def test_versioned_publication_hashes_and_input_guards(self):
        output = self.root / "batch_results"
        batch.write_outputs(self.frames, self.summary, self.config, self.provenance, output)
        manifest = json.loads((output / "manifest.json").read_text())
        for name, digest in manifest["output_hashes"].items():
            self.assertEqual(batch.audit.file_hash(output/name), digest)
        with self.assertRaises(FileExistsError):
            batch.write_outputs(self.frames, self.summary, self.config, self.provenance, output)
        with self.assertRaisesRegex(ValueError, "input/code changed"):
            batch.write_outputs(self.frames, self.summary, self.config,
                                {**self.provenance, "config_sha256": "stale"}, self.root / "stale")


class NewBatchContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        source = pd.read_csv(SNAPSHOT / "source.csv", dtype="string", keep_default_na=False)
        imported_config = json.loads((SNAPSHOT / "source_config.json").read_text())
        imported_config["columns"]["split"] = imported_config["columns"]["event_time_cutoff_exclusive"] = None
        imported_config["dataset_scope"] = "unassigned"
        config_path = cls.root / "import_config.json"
        config_path.write_text(json.dumps(imported_config))

        def import_real_subset(frame, name):
            path = cls.root / f"{name}.csv"
            frame.to_csv(path, index=False)
            values = batch.training.importer.prepare_import(path, config_path)
            output = cls.root / name
            batch.training.importer.write_import(*values, output)
            return output

        training_source = source.loc[source.order_purchase_timestamp.lt("2018-03-01 00:00:00")]
        cls.earlier_import = import_real_subset(training_source, "earlier_import")
        plan = json.loads((MODEL / "training_plan.json").read_text())
        plan.update({"run_id": "earlier_real_development_contract_test", "fit_end_exclusive": "2017-09-01 00:00:00",
                     "train_end_exclusive": "2017-12-01 00:00:00", "validation_end_exclusive": "2018-03-01 00:00:00"})
        plan_path = cls.root / "earlier_plan.json"
        plan_path.write_text(json.dumps(plan))
        design, plan, identity, provenance = batch.training.load_training_inputs(cls.earlier_import, plan_path)
        frames, cls.bundle, summary = batch.training.train_dataset(design, plan, identity, provenance)
        cls.model = cls.root / "earlier_model"
        batch.training.write_training(frames, cls.bundle, summary, cls.model)

        # Contract-only declaration, not a claim of a historical source extraction at this date.
        # Subset real observations; do not invent/change event timestamps, durations, statuses or IDs.
        within = source[batch.TIMES].lt("2018-06-01 00:00:00").all(axis=1)
        later = source.loc[source.order_purchase_timestamp.ge("2018-03-01 00:00:00") & within]
        cls.later_import = import_real_subset(later, "later_real_development_import")
        cls.config = json.loads(CONFIG.read_text())
        cls.config.update({"batch_id": "new_batch_contract_test_real_development", "mode": "new_batch", "cutoff_basis": "source_extract"})
        cls.config_path = cls.root / "new_batch_config.json"
        cls.config_path.write_text(json.dumps(cls.config))
        cls.inputs = batch.load_inputs(cls.model, cls.later_import, cls.config_path)
        cls.orders, _ = batch.training.importer.load_import_snapshot(cls.later_import)
        _, cls.context, cls.ledger, cls.reference, cls.reference_context, cls.columns, cls.identity, _, _ = cls.inputs
        earlier, _ = batch.training.importer.load_import_snapshot(cls.earlier_import)
        _, cls.development, _, _ = batch.training.build_design(earlier, plan, identity)
        cls.reserved = batch.load_reservations(cls.config, cls.development.order_id)

    def prepare(self, config=None, orders=None):
        return batch.prepare_batch(self.orders if orders is None else orders, self.bundle, self.development,
                                   self.identity, self.config if config is None else config, self.reserved)

    def test_real_later_cases_use_saved_earlier_model_without_refitting(self):
        with patch.object(batch.training.engine, "fit_bank", side_effect=AssertionError("No fitting")), \
             patch.object(batch.training.engine, "learn_thresholds", side_effect=AssertionError("No recalibration")):
            frames, summary = batch.build_outputs(*self.inputs[:-1])
        self.assertGreater(summary["scored_cases"], 0)
        self.assertFalse(set(frames["case_results.csv"].order_id) & set(self.development.order_id))
        self.assertTrue(frames["case_results.csv"].split.isna().all())
        self.assertTrue(frames["case_results.csv"].application_mode.eq("new_batch").all())
        self.assertFalse(frames["rule_evidence.csv"].reason_code.eq("NONFINAL_CUTOFF_UNKNOWN").any())
        self.assertFalse(frames["case_results.csv"].usage_group.eq("nonfinal_status_cutoff_unknown").any())
        ongoing = frames["case_results.csv"].loc[lambda frame: frame.imported_usage_group.eq("nonfinal_status_cutoff_unknown")]
        self.assertGreater(len(ongoing), 0)
        self.assertTrue(ongoing.usage_group.eq("nonfinal_status_completed_model_unavailable").all())
        self.assertFalse(ongoing.ongoing_age_computable.any())
        self.assertTrue(frames["case_results.csv"].has_event_at_or_after_cutoff.eq(False).all())
        facts = ["order_id", "order_status", *batch.TIMES, *batch.FEATURES]
        pd.testing.assert_frame_equal(frames["case_results.csv"][facts], self.orders[facts], check_dtype=False)
        batch.write_outputs(frames, summary, self.config, self.inputs[-1], self.root / "new_batch_output")

    def test_declared_cutoff_cannot_truncate_observed_events(self):
        rows = self.orders.loc[self.orders.order_purchase_timestamp.lt(pd.Timestamp("2018-04-01"))]
        config = {**self.config, "purchase_end_exclusive": "2018-04-01 00:00:00", "observation_cutoff_exclusive": "2018-04-01 00:00:00"}
        self.assertTrue(rows[batch.TIMES].ge(pd.Timestamp("2018-04-01")).any().any())
        with self.assertRaisesRegex(ValueError, "reach/exceed"):
            self.prepare(config=config, orders=rows)

    def test_new_batch_requires_unassigned_exact_scope_and_post_development_period(self):
        assigned = self.orders.copy()
        assigned["split"] = "validation"
        with self.assertRaisesRegex(ValueError, "unassigned"):
            self.prepare(orders=assigned)
        with self.assertRaisesRegex(ValueError, "after the saved development"):
            self.prepare(config={**self.config, "purchase_start_inclusive": "2018-02-01 00:00:00"})
        with self.assertRaisesRegex(ValueError, "exactly the declared"):
            self.prepare(config={**self.config, "purchase_end_exclusive": "2018-04-01 00:00:00"})


if __name__ == "__main__":
    unittest.main()
