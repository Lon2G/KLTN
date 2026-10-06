"""Dataset-bound training tests use real Olist observations, not invented cases or labels."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import train_imported_process as training
import validate_saved_process_model as replay


SNAPSHOT = ROOT / "data/imported/olist_bed_bath_table_import_v1"
PLAN = ROOT / "templates/process_training_v1/olist_bed_bath_table.json"


class ImportedProcessTrainingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.design, cls.plan, cls.identity, cls.provenance = training.load_training_inputs(SNAPSHOT, PLAN)
        cls.subsets, cls.context, cls.ledger, cls.inner_ledger = cls.design
        cls.orders, cls.import_manifest = training.importer.load_import_snapshot(SNAPSHOT)
        # Training must fit actual observations, not load previously fitted Olist models.
        with patch.object(training.joblib, "load", side_effect=AssertionError("No old fitted model may be loaded while training")):
            cls.frames, cls.bundle, cls.summary = training.train_dataset(cls.design, cls.plan, cls.identity, cls.provenance)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.model_dir = Path(cls.temporary.name) / "model"
        cls.saved_summary = training.write_training(cls.frames, cls.bundle, cls.summary, cls.model_dir)

    def test_every_imported_case_reconciles_and_observations_are_preserved(self):
        self.assertEqual(len(self.ledger), 7333)
        self.assertTrue(self.ledger.order_id.is_unique)
        self.assertEqual(set(self.ledger.order_id), set(self.orders.order_id))
        self.assertTrue(self.ledger.scope_reason.eq("development").all())
        observed = ["order_id", "source_record", "order_status", "product_category_name", *training.TIMES,
                    "order_estimated_delivery_date", *training.importer.usage.DURATIONS]
        pd.testing.assert_frame_equal(self.context[observed], self.orders[observed], check_dtype=False)
        self.assertEqual(self.context.inner_role.value_counts().to_dict(), {
            "fit": 3190, "validation": 1523, "calibration": 1412, "process_only": 732, "deferred_at_inner_cutoff": 476})

    def test_fit_calibration_validation_are_disjoint_and_chronological(self):
        roles = {name: set(frame.order_id) for name, frame in self.subsets.items()}
        for left, right in [("fit", "calibration"), ("fit", "validation"), ("calibration", "validation")]:
            self.assertTrue(roles[left].isdisjoint(roles[right]))
        indexed = self.context.set_index("order_id")
        fit = indexed.loc[self.subsets["fit"].order_id]
        self.assertTrue(fit[training.TIMES].lt(pd.Timestamp(self.plan["fit_end_exclusive"])).all().all())
        cal = indexed.loc[self.subsets["calibration"].order_id]
        self.assertTrue(cal.order_purchase_timestamp.ge(pd.Timestamp(self.plan["fit_end_exclusive"])).all())
        self.assertTrue(cal[training.TIMES].lt(pd.Timestamp(self.plan["train_end_exclusive"])).all().all())
        val = indexed.loc[self.subsets["validation"].order_id]
        self.assertTrue(val.order_purchase_timestamp.ge(pd.Timestamp(self.plan["train_end_exclusive"])).all())
        self.assertTrue(val[training.TIMES].lt(pd.Timestamp(self.plan["validation_end_exclusive"])).all().all())

    def test_profile_uses_only_allowlisted_features_and_no_labels(self):
        self.assertEqual(len(self.bundle["bank"]["models"]), 10)
        self.assertEqual(len(self.frames["candidates.csv"]), 120)
        self.assertEqual(len(self.frames["validation_scores.csv"].columns)-1, 21)
        for frame in self.subsets.values():
            self.assertEqual(frame.columns.tolist(), ["order_id", *training.FEATURES])
            self.assertFalse(frame[training.FEATURES].lt(0).any().any())
            self.assertFalse(frame[training.FEATURES].isna().any().any())
        for model in self.bundle["bank"]["models"].values():
            first_step = model.steps[0][1]
            feature_owner = model.named_steps["detector"] if isinstance(first_step, str) else first_step
            self.assertEqual(feature_owner.feature_names_in_.tolist(), training.FEATURES)
        for name in ["accuracy_computed", "human_labels_created", "anomaly_probabilities_computed", "test_scored", "previous_fitted_model_reused"]:
            self.assertFalse(self.summary[name])
        self.assertIsNone(self.summary["selected_candidate"])

    def test_fit_statistics_and_score_thresholds_use_separate_real_populations(self):
        values = self.subsets["fit"][training.FEATURES].to_numpy()
        for transform, statistics in self.bundle["bank"]["statistics"].items():
            actual = np.log1p(values) if transform == "log1p" else values
            np.testing.assert_allclose(statistics["median"], np.median(actual, axis=0))
            np.testing.assert_allclose(statistics["iqr"], np.quantile(actual, .75, axis=0)-np.quantile(actual, .25, axis=0))
        for row in self.frames["candidates.csv"].dropna(subset="tail_fraction").itertuples(index=False):
            expected = np.quantile(self.frames["calibration_scores.csv"][row.score_column], 1-row.tail_fraction)
            self.assertAlmostEqual(row.threshold, expected)

    def test_new_training_reproduces_frozen_benchmark_without_reusing_models(self):
        previous = ROOT / "data/experiments/bed_bath_table_benchmark_v1"
        candidates = self.frames["candidates.csv"]
        expected = pd.read_csv(previous/"validation_flags.csv", dtype={"order_id": "string", **{name: "bool" for name in candidates.candidate}})
        pd.testing.assert_frame_equal(self.frames["validation_flags.csv"], expected, check_dtype=False, check_exact=True)
        saved_candidates = pd.read_csv(previous/"candidates.csv", float_precision="round_trip")
        pd.testing.assert_frame_equal(candidates, saved_candidates, check_dtype=False, rtol=1e-12, atol=1e-12)
        old_actions = pd.read_csv(ROOT / "data/experiments/bed_bath_table_auto_validation_v1/case_results.csv", dtype={"order_id": "string"})
        fields = ["order_id", "auto_action", "reason_codes_json"]
        pd.testing.assert_frame_equal(self.frames["auto_case_results.csv"][fields], old_actions[fields], check_dtype=False)

    def test_incomplete_and_deferred_cases_remain_without_novelty_fit_scoring(self):
        cases = self.frames["auto_case_results.csv"]
        self.assertEqual(len(cases), 7333)
        self.assertEqual(int(cases.model_signals_available.sum()), 2935)
        self.assertTrue(cases.loc[cases.inner_role.isin(["fit", "deferred_at_inner_cutoff", "process_only"]), "model_candidate_signal"].isna().all())
        self.assertEqual(self.summary["validation_actions"], {
            "no_signal_in_available_checks": 750, "inspect_timing_warning": 570, "insufficient_context": 279,
            "inspect_model_signal": 203, "verify_source_record": 51})
        with self.assertRaisesRegex(ValueError, "must not use fit"):
            training.score_bundle(self.bundle, self.subsets["fit"].iloc[:1], self.identity)

    def test_validation_scoring_cannot_change_thresholds_or_statistics(self):
        before = self.bundle["bank"]["candidates"].copy(deep=True)
        with patch.object(training.engine, "fit_bank", side_effect=AssertionError("Must not fit during scoring")):
            scores, flags = training.score_bundle(self.bundle, self.subsets["validation"].iloc[::4], self.identity)
        pd.testing.assert_frame_equal(self.bundle["bank"]["candidates"], before)
        expected = self.frames["validation_flags.csv"].iloc[::4].reset_index(drop=True)
        pd.testing.assert_frame_equal(flags, expected)
        self.assertEqual(scores.order_id.tolist(), expected.order_id.tolist())

    def test_plan_identity_and_model_identity_cannot_cross_datasets(self):
        for field, value in [("marketplace_id", "shopee"), ("dataset_id", "different_import"), ("primary_category", "bed_bath_table")]:
            # Deliberately incorrect metadata tests rejection; no other-platform observations are invented.
            plan = {**self.plan, field: value}
            with self.assertRaisesRegex(ValueError, "identity mismatch"):
                training.build_design(self.orders, plan, self.identity)
            with patch.object(training.engine, "base_scores", side_effect=AssertionError("Reject identity before scoring")):
                with self.assertRaisesRegex(ValueError, "identity mismatch"):
                    training.score_bundle(self.bundle, self.subsets["validation"], {**self.identity, field: value})

    def test_existing_split_and_cutoff_cannot_be_silently_rewritten(self):
        changed = {**self.plan, "train_end_exclusive": "2018-03-02 00:00:00"}
        with self.assertRaisesRegex(ValueError, "disagrees"):
            training.build_design(self.orders, changed, self.identity)
        # A reserved experiment-role marker is metadata only, not an invented event or human label.
        reserved = self.orders.copy()
        reserved.loc[0, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "reserved test"):
            training.build_design(reserved, self.plan, self.identity)

    def test_declared_plan_can_assign_unassigned_real_import_without_editing_source(self):
        source = pd.read_csv(SNAPSHOT/"source.csv", dtype="string", keep_default_na=False)
        config = deepcopy(self.import_manifest["config"])
        config["columns"]["split"] = config["columns"]["event_time_cutoff_exclusive"] = None
        config["dataset_scope"] = "unassigned"
        frames, _ = training.importer.build_import(source, config)
        unassigned = frames["orders.csv"]
        subsets, context, _, _ = training.build_design(unassigned, self.plan, self.identity)
        self.assertTrue(unassigned.split.isna().all())
        self.assertTrue(context.imported_split.isna().all())
        for name in self.subsets:
            pd.testing.assert_frame_equal(subsets[name], self.subsets[name])

    def test_fit_boundary_is_a_per_run_parameter_and_equality_is_exclusive(self):
        observed = self.context.loc[self.context.inner_role.eq("fit")
                                    & self.context.order_delivered_customer_date.ge(pd.Timestamp("2017-11-01"))
                                    & self.context.total_cycle_time_days.gt(0)].iloc[0]
        plan = {**self.plan, "fit_end_exclusive": observed.order_delivered_customer_date.strftime("%Y-%m-%d %H:%M:%S")}
        subsets, context, _, _ = training.build_design(self.orders, plan, self.identity)
        target = context.set_index("order_id").loc[observed.order_id]
        self.assertEqual(target.inner_role, "deferred_at_inner_cutoff")
        self.assertTrue(target.timing_input_eligible)
        self.assertNotIn(observed.order_id, subsets["fit"].order_id.tolist())

    def test_insufficient_real_fit_data_fails_without_padding_or_imputation(self):
        removed = self.subsets["fit"].order_id.iloc[500:]
        subset = self.orders.loc[~self.orders.order_id.isin(removed)]
        with self.assertRaisesRegex(ValueError, "at least 512 fit"):
            training.build_design(subset, self.plan, self.identity)

    def test_invalid_plan_and_duplicate_actual_row_fail(self):
        template = json.loads((ROOT/"templates/process_training_v1/training_config.json").read_text())
        with self.assertRaises(ValueError):
            training.validate_plan(template)
        for plan in [{**self.plan, "fit_end_exclusive": self.plan["train_end_exclusive"]},
                     {**self.plan, "fit_end_exclusive": "2017-12-01T00:00:00Z"},
                     {**self.plan, "method_profile": "undeclared_profile"}]:
            with self.assertRaises(ValueError):
                training.validate_plan(plan)
        with self.assertRaisesRegex(ValueError, "unique"):
            training.build_design(pd.concat([self.orders, self.orders.iloc[:1]]), self.plan, self.identity)

    def test_reload_reproduces_all_flags_without_any_fitting(self):
        with patch.object(training.engine, "fit_bank", side_effect=AssertionError("No fitting on reload")):
            frames, summary, hashes = replay.reproduce_validation(self.model_dir, SNAPSHOT)
        self.assertEqual(summary["validation_orders_scored"], 1523)
        self.assertEqual(summary["candidate_configurations"], 120)
        self.assertTrue(summary["candidate_flags_match_exactly"])
        self.assertFalse(summary["refitting_performed"])
        pd.testing.assert_frame_equal(frames["validation_flags.csv"], self.frames["validation_flags.csv"])
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/"replay"
            replay.write_validation(frames, summary, hashes, self.model_dir, SNAPSHOT, output)
            manifest = json.loads((output/"manifest.json").read_text())
            for name, digest in manifest["output_hashes"].items():
                self.assertEqual(training.importer.audit.file_hash(output/name), digest)
            with self.assertRaises(FileExistsError):
                replay.write_validation(frames, summary, hashes, self.model_dir, SNAPSHOT, output)

    def test_bundle_publication_hashes_and_no_overwrite(self):
        _, manifest = training.load_model_bundle(self.model_dir)
        self.assertTrue(self.saved_summary["saved_model_roundtrip_verified"])
        self.assertEqual(manifest["identity"], self.identity)
        for name, digest in manifest["output_hashes"].items():
            self.assertEqual(training.importer.audit.file_hash(self.model_dir/name), digest)
        with self.assertRaises(FileExistsError):
            training.write_training(self.frames, self.bundle, self.summary, self.model_dir)
        with tempfile.TemporaryDirectory() as temporary:
            stale = {**self.bundle, "provenance": {**self.provenance, "plan_sha256": "stale"}}
            with self.assertRaisesRegex(ValueError, "source/config changed"):
                training.write_training(self.frames, stale, self.summary, Path(temporary)/"stale")

    def test_corrupt_artifact_is_rejected_before_deserializing(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)/"model"
            shutil.copytree(self.model_dir, copied)
            target = copied/"validation_flags.csv"
            target.write_bytes(target.read_bytes()+b"\n")
            with patch.object(training.joblib, "load", side_effect=AssertionError("Do not deserialize after hash failure")):
                with self.assertRaisesRegex(ValueError, "hash mismatch"):
                    training.load_model_bundle(copied)

    def test_wrong_runtime_or_engine_is_rejected_before_deserializing(self):
        with tempfile.TemporaryDirectory() as temporary:
            copied = Path(temporary)/"model"
            shutil.copytree(self.model_dir, copied)
            original = json.loads((copied/"manifest.json").read_text())
            for field, replacement, message in [("engine_hashes", {}, "engine differs"),
                                                 ("runtime", {**original["runtime"], "sklearn": "incompatible"}, "runtime")]:
                (copied/"manifest.json").write_text(json.dumps({**original, field: replacement}))
                with patch.object(training.joblib, "load", side_effect=AssertionError("Reject incompatible engine before pickle")):
                    with self.assertRaisesRegex(ValueError, message):
                        training.load_model_bundle(copied)


if __name__ == "__main__":
    unittest.main()
