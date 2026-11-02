"""Planning checks on real Olist metadata; corrupt copies test rejection only."""

from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/01_data_preparation"))
import plan_temporal_evaluation as planning


class TemporalEvaluationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with patch.object(joblib, "load", side_effect=AssertionError("Do not deserialize models")), \
                patch.object(planning.review, "open_session", side_effect=AssertionError("Do not open review")):
            cls.inputs, cls.provenance = planning.load_inputs()
            cls.frames, cls.summary = planning.build_plan(cls.inputs)
            cls.temporary = tempfile.TemporaryDirectory()
            cls.addClassCleanup(cls.temporary.cleanup)
            cls.root = Path(cls.temporary.name)
            cls.snapshot = cls.root/"plan"
            planning.prepare(cls.snapshot)
        cls.cohort = cls.inputs["cohort"]
        cls.ledger = cls.frames["analyst_only/partition_plan.csv"]

    def test_real_cohort_counts_and_boundary(self):
        self.assertEqual(self.summary["cohort_orders"], 9024)
        self.assertEqual(self.summary["proposed_development_orders"], 7219)
        self.assertEqual(self.summary["proposed_test_orders"], 1805)
        self.assertEqual(self.summary["boundary_purchase_timestamp_inclusive_for_test"], "2018-05-24 11:07:21")
        self.assertEqual(self.summary["extra_test_orders_due_to_timestamp_ties"], 0)
        self.assertAlmostEqual(self.summary["proposed_test_fraction"], 1805/9024)

    def test_no_orders_lost_duplicated_or_invented(self):
        self.assertTrue(self.ledger.order_id.is_unique)
        self.assertEqual(set(self.ledger.order_id), set(self.cohort.order_id))
        recovered = self.ledger.rename(columns={"old_split": "split"})[planning.METADATA]
        pd.testing.assert_frame_equal(recovered.sort_values("order_id").reset_index(drop=True),
                                      self.cohort.sort_values("order_id").reset_index(drop=True), check_exact=True)

    def test_strict_chronology_and_inclusive_test_boundary(self):
        development = self.ledger.loc[self.ledger.proposed_role.eq("development")]
        test = self.ledger.loc[self.ledger.proposed_role.eq("test")]
        self.assertLess(development[planning.TIME].max(), test[planning.TIME].min())
        self.assertTrue(self.ledger.groupby(planning.TIME).proposed_role.nunique().eq(1).all())
        self.assertEqual(int(self.ledger[planning.TIME].eq(test[planning.TIME].min()).sum()), 1)

    def test_deterministic_row_order_independent(self):
        frames, summary = planning.build_plan({**self.inputs, "cohort": self.cohort.iloc[::-1]})
        self.assertEqual(summary, self.summary)
        for name in frames:
            pd.testing.assert_frame_equal(frames[name], self.frames[name], check_exact=True)

    def test_assignment_does_not_depend_on_old_role_or_review_marker(self):
        changed = self.cohort.copy()
        changed["split"] = "train"
        changed["previously_human_reviewed"] = ~changed.previously_human_reviewed
        ledger, boundary, target = planning.assign_outer(changed)
        pd.testing.assert_series_equal(ledger.proposed_role, self.ledger.proposed_role)
        self.assertEqual(str(boundary), self.summary["boundary_purchase_timestamp_inclusive_for_test"])
        self.assertEqual(target, 1805)

    def tied_real_subset(self):
        counts = self.cohort.groupby(planning.TIME).size()
        timestamp = counts.index[counts.gt(1)][0]
        earlier = self.cohort.loc[self.cohort[planning.TIME].lt(timestamp)].head(1)
        tied = self.cohort.loc[self.cohort[planning.TIME].eq(timestamp)]
        self.assertFalse(earlier.empty)
        return earlier, tied

    def test_real_equal_timestamp_group_not_split_for_ratio(self):
        earlier, tied = self.tied_real_subset()
        subset = pd.concat([earlier, tied], ignore_index=True)
        ledger, boundary, target = planning.assign_outer(subset)
        self.assertEqual(boundary, tied.iloc[0][planning.TIME])
        self.assertEqual(int(ledger.proposed_role.eq("test").sum()), len(tied))
        self.assertGreater(len(tied), target)

    def test_all_tied_real_purchases_rejected_without_fabricating_cutoff(self):
        _, tied = self.tied_real_subset()
        with self.assertRaisesRegex(ValueError, "no development"):
            planning.assign_outer(tied)

    def test_invalid_metadata_rejected(self):
        examples = []
        duplicated = self.cohort.copy()
        duplicated.loc[1, "order_id"] = duplicated.loc[0, "order_id"]
        examples.append(duplicated)
        missing = self.cohort.copy()
        missing.loc[0, planning.TIME] = pd.NaT
        examples.append(missing)
        bad_split = self.cohort.copy()
        bad_split.loc[0, "split"] = "arbitrary"
        examples.append(bad_split)
        tz_aware = self.cohort.copy()
        tz_aware[planning.TIME] = tz_aware[planning.TIME].dt.tz_localize("UTC")
        examples.extend([tz_aware, self.cohort.iloc[:0], self.cohort.iloc[:1], self.cohort.assign(unexpected_outcome=0)])
        for frame in examples:
            with self.subTest(rows=len(frame), columns=len(frame.columns)), self.assertRaises(ValueError):
                planning.assign_outer(frame)

    def test_real_migration_and_reserved_test_preserved(self):
        self.assertEqual(self.summary["old_reserved_test_retained_in_proposed_test"], 1691)
        self.assertEqual(self.summary["old_reserved_test_moved_to_development"], 0)
        self.assertEqual(self.summary["old_development_moved_by_split"], {"validation": 114})
        test = self.ledger.loc[self.ledger.proposed_role.eq("test")]
        self.assertEqual(test.test_exposure_stratum.value_counts().to_dict(), {"old_reserved_test": 1691, "reused_old_development": 114})

    def test_exposure_overlap_is_explicit_not_accuracy(self):
        exposures = self.summary["proposed_test_recorded_exposures"]
        self.assertEqual(exposures["old_development_membership"], 114)
        self.assertEqual(exposures["business_ablation_validation_membership"], 5)
        self.assertEqual(exposures["seller_delivery_saved_forecast"], 62)
        self.assertEqual(exposures["seller_delivery_observed_validation_evaluation"], 5)
        self.assertEqual(exposures["protected_blind_pilot_membership_not_a_completed_review"], 2)
        self.assertTrue(self.summary["recorded_exposure_counts_overlap"])
        self.assertFalse(self.summary["test_is_never_seen_independent_data"])
        self.assertFalse(self.summary["exposure_audit_exhaustive"])

    def test_original_pilot_stays_whole_no_replacement_or_pseudonym_mapping(self):
        protected = self.inputs["exposure_sets"]["protected_blind_pilot_membership_not_a_completed_review"]
        self.assertEqual(len(protected), 120)
        self.assertTrue(protected.issubset(self.ledger.order_id))
        self.assertEqual(self.ledger.columns.tolist(), ["order_id", planning.TIME, "old_split", "previously_human_reviewed",
                                                       "proposed_role", "test_exposure_stratum"])
        for frame in self.frames.values():
            self.assertNotIn("review_id", frame.columns)
            self.assertNotIn("protected_pilot", frame.columns)
        for name in ["README.md", "summary.json", "protocol.json", "split_counts.csv", "migration_counts.csv", "exposure_counts.csv", "manifest.json"]:
            text = (self.snapshot/name).read_text()
            self.assertFalse(any(order_id in text for order_id in protected), name)

    def test_aggregate_counts_reconcile(self):
        self.assertEqual(self.frames["split_counts.csv"].orders.sum(), 9024)
        self.assertEqual(self.frames["migration_counts.csv"].orders.sum(), 9024)
        for row in self.frames["exposure_counts.csv"].itertuples():
            ids = self.inputs["exposure_sets"][row.recorded_exposure]
            expected = self.ledger.loc[self.ledger.proposed_role.eq(row.proposed_role)].order_id.isin(ids).sum()
            self.assertEqual(row.orders, expected)

    def test_only_allowlisted_metadata_columns_parsed_no_models_loaded(self):
        read_csv = pd.read_csv
        reads = []

        def guard(path, **kwargs):
            self.assertIn("usecols", kwargs)
            matched = [(key, name) for key, name in planning.READ_COLUMNS
                       if Path(path) == Path(self.provenance["snapshots"][key]["directory"])/name]
            self.assertEqual(len(matched), 1)
            self.assertEqual(kwargs["usecols"], planning.READ_COLUMNS[matched[0]])
            reads.append(matched[0])
            return read_csv(path, **kwargs)

        with patch.object(pd, "read_csv", side_effect=guard), patch.object(joblib, "load", side_effect=AssertionError("No models")):
            inputs, _ = planning.load_inputs()
            planning.build_plan(inputs)
        self.assertEqual(reads, list(planning.READ_COLUMNS))

    def test_activity_inventory_hashes_without_claiming_human_labels(self):
        inventory = self.provenance["activity_inventory"]
        self.assertIn("outputs/inspections/order_evidence_example_v1/result.json", inventory)
        self.assertTrue(all(len(value) == 64 for value in inventory.values()))
        self.assertFalse(self.summary["human_labels_created"])

    def test_unexpected_exposure_id_rejected(self):
        exposures = {**self.inputs["exposure_sets"], "bad_scope": {"outside_cohort_software_rejection_test"}}
        with self.assertRaisesRegex(ValueError, "outside frozen cohort"):
            planning.build_plan({**self.inputs, "exposure_sets": exposures})

    def test_no_training_or_activation_claims(self):
        self.assertEqual(planning.protocol()["lifecycle"], "proposal_not_activated")
        for key in ["split_activated", "training_performed", "model_weights_loaded", "test_feature_values_parsed", "test_scored",
                    "accuracy_computed", "human_labels_created", "synthetic_observations_used", "pilot_changed", "near_real_time_validated"]:
            self.assertIs(self.summary[key], False)

    def test_snapshot_verifies_and_roundtrips(self):
        manifest = planning.load_snapshot(self.snapshot)
        self.assertEqual(manifest["summary"], self.summary)
        self.assertEqual(planning.review.read_json(self.snapshot/"protocol.json"), planning.protocol())
        for name in planning.TABLES:
            pd.testing.assert_frame_equal(planning.shared.read_table(self.snapshot, name, manifest), self.frames[name], check_exact=True)

    def test_existing_snapshot_cannot_be_overwritten(self):
        before = {path: planning.review.digest(path) for path in self.snapshot.rglob("*") if path.is_file()}
        with self.assertRaises(FileExistsError):
            planning.prepare(self.snapshot)
        self.assertEqual(before, {path: planning.review.digest(path) for path in before})

    def test_protected_or_nested_destination_rejected(self):
        for destination in [ROOT/"data/raw/new_plan", ROOT/"data/manual_review/new_plan", ROOT/"scripts/new_plan", self.snapshot/"nested"]:
            with self.subTest(destination=destination), self.assertRaises(ValueError):
                planning.destination_guard(destination, self.provenance)

    def test_source_manifest_change_rejected(self):
        changed = deepcopy(self.provenance)
        changed["snapshots"]["cohort"]["manifest_sha256"] = "0"*64
        with self.assertRaisesRegex(ValueError, "Source manifest changed"):
            planning.verify_sources(changed)

    def test_source_output_change_rejected(self):
        changed = deepcopy(self.provenance)
        changed["snapshots"]["cohort"]["output_hashes"]["cohort_cases.csv"] = "0"*64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            planning.verify_sources(changed)

    def test_changed_review_activity_invalidates_current_audit(self):
        with patch.object(planning, "activity_inventory", return_value={}):
            with self.assertRaisesRegex(ValueError, "review/inspection activity changed"):
                planning.verify_sources(self.provenance)

    def test_changed_code_invalidates_reproduction(self):
        with patch.object(planning, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "code or protocol changed"):
                planning.verify_sources(self.provenance)

    def test_corrupted_saved_outputs_rejected(self):
        directory = self.root/"tampered_plan"
        shutil.copytree(self.snapshot, directory)
        (directory/"summary.json").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            planning.load_snapshot(directory)

    def test_cli_summary_json(self):
        result = subprocess.run([sys.executable, str(Path(planning.__file__)), "--summary", "--output", str(self.snapshot)],
                                text=True, capture_output=True, env={**os.environ, "MPLCONFIGDIR": "/private/tmp/kltn-matplotlib"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), self.summary)

    def test_cli_has_no_activation_or_test_scoring_switch(self):
        for switch in ["--activate", "--score-test"]:
            result = subprocess.run([sys.executable, str(Path(planning.__file__)), switch], text=True, capture_output=True)
            self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
