"""Chronological experiment checks use only real observations from frozen snapshots."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import prepare_bed_bath_table_experiment as bed


class BedBathTableExperimentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, _, cls.membership, cls.base_parents = bed.compare.load_inputs()
        comparison = bed.read_comparison(bed.compare.DEFAULT_OUTPUT, cls.base_parents)
        cls.window = comparison["design"]["window"]
        cls.reviewed, exposure = bed.review_exposure(bed.MANUAL_REVIEW_DIR)
        cls.parents = {**cls.base_parents,
                       "comparison_manifest_sha256": bed.audit.file_hash(bed.compare.DEFAULT_OUTPUT / "manifest.json"),
                       "comparison_output_hashes": comparison["output_hashes"], "review_exposure": exposure}
        cls.frames, cls.design = bed.build_experiment(cls.cases, cls.membership, cls.window, cls.reviewed)
        cls.cohort = cls.frames["cohort_cases.csv"]

    def test_category_scope_reconciles_all_known_membership(self):
        ledger = self.frames["category_membership_ledger.csv"]
        expected = set(self.membership.loc[self.membership.product_category_name.eq(bed.CATEGORY), "order_id"])
        self.assertEqual(set(ledger.order_id), expected)
        self.assertTrue(ledger.order_id.is_unique)
        self.assertEqual(len(ledger), self.frames["scope_summary.csv"].orders.sum())
        self.assertEqual(set(ledger.loc[ledger.in_primary_cohort, "order_id"]), set(self.cohort.order_id))
        self.assertTrue(ledger.loc[~ledger.in_primary_cohort, "split"].isna().all())
        self.assertTrue(self.cohort.category_assignment_eligible.all())
        self.assertTrue(self.cohort.single_category_name.eq(bed.CATEGORY).all())
        self.assertTrue(self.cohort.single_category_name_english.eq(bed.CATEGORY_ENGLISH).all())
        self.assertEqual(len(self.cohort), 9024)

    def test_observations_and_problem_cases_are_unchanged(self):
        source = self.cases.set_index("order_id").loc[self.cohort.order_id]
        actual = self.cohort.set_index("order_id")[source.columns]
        pd.testing.assert_frame_equal(actual, source)
        self.assertEqual(int((~self.cohort.completed_timing_eligible).sum()), 273)
        self.assertGreater(self.cohort.has_reversed_recorded_milestones.sum(), 0)
        self.assertGreater(self.cohort.missing_milestone_count.gt(0).sum(), 0)
        self.assertGreater(self.cohort.total_cycle_time_days.max(), 100)
        self.assertGreater(self.cohort.purchased_to_approved_days.eq(0).sum(), 0)
        self.assertGreater(self.cohort.order_status.ne("delivered").sum(), 0)

    def test_split_is_disjoint_complete_and_purchase_based(self):
        ids = []
        for partition in self.design["partitions"]:
            expected = self.cohort.loc[
                self.cohort.order_purchase_timestamp.ge(pd.Timestamp(partition["purchase_start_inclusive"]))
                & self.cohort.order_purchase_timestamp.lt(pd.Timestamp(partition["purchase_end_exclusive"]))]
            actual = self.cohort.loc[self.cohort.split.eq(partition["split"])]
            self.assertEqual(set(actual.order_id), set(expected.order_id))
            self.assertGreater(len(actual), 0)
            ids.extend(actual.order_id)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(set(ids), set(self.cohort.order_id))
        self.assertTrue(self.cohort.order_id.is_unique)

    def test_timing_matrices_respect_event_cutoffs_without_reassignment(self):
        for name in bed.SPLITS:
            group = self.cohort.loc[self.cohort.split.eq(name)]
            expected = group.completed_timing_eligible.copy()
            if name != "test":
                expected &= group[bed.audit.TIMES].lt(group.event_time_cutoff_exclusive, axis=0).all(axis=1)
                crossed = group.completed_timing_eligible & group.has_event_at_or_after_cutoff
                self.assertGreater(crossed.sum(), 0)
                self.assertFalse(group.loc[crossed, "timing_input_eligible"].any())
                self.assertTrue(group.loc[crossed, "timing_input_reason"].eq("completion_not_before_partition_cutoff").all())
            actual = self.frames[f"{name}_timing_features.csv"]
            self.assertEqual(actual.columns.tolist(), ["order_id", *bed.FEATURES])
            self.assertEqual(set(actual.order_id), set(group.loc[expected, "order_id"]))
            self.assertFalse(actual[bed.FEATURES].isna().any().any())
            self.assertFalse(actual[bed.FEATURES].lt(0).any().any())
            self.assertTrue(actual.order_id.is_unique)

    def test_exact_observed_timestamp_boundary_is_exclusive(self):
        eligible = self.cohort.loc[
            self.cohort.completed_timing_eligible
            & self.cohort.order_delivered_customer_date.ge(pd.Timestamp("2018-01-01"))
            & self.cohort.order_delivered_customer_date.lt(pd.Timestamp(bed.TRAIN_END))
            & self.cohort.total_cycle_time_days.gt(0)]
        observed = eligible.iloc[0]
        frames, _ = bed.build_experiment(self.cases, self.membership, self.window, self.reviewed,
                                         train_end=str(observed.order_delivered_customer_date))
        case = frames["cohort_cases.csv"].set_index("order_id").loc[observed.order_id]
        self.assertEqual(case.split, "train")
        self.assertTrue(case.has_event_at_or_after_cutoff)
        self.assertFalse(case.timing_input_eligible)
        self.assertNotIn(observed.order_id, set(frames["train_timing_features.csv"].order_id))

    def test_purchase_at_boundary_starts_next_split(self):
        actual_purchase = self.cohort.loc[
            self.cohort.order_purchase_timestamp.ge(pd.Timestamp("2018-01-01"))
            & self.cohort.order_purchase_timestamp.lt(pd.Timestamp(bed.TRAIN_END))].iloc[0]
        frames, _ = bed.build_experiment(self.cases, self.membership, self.window, self.reviewed,
                                         train_end=str(actual_purchase.order_purchase_timestamp))
        case = frames["cohort_cases.csv"].set_index("order_id").loc[actual_purchase.order_id]
        self.assertEqual(case.split, "validation")

    def test_test_cutoff_is_unknown_not_purchase_end(self):
        cases = self.cohort.loc[self.cohort.split.eq("test")]
        events = self.frames["event_log.csv"].loc[lambda f: f.split.eq("test")]
        self.assertTrue(cases.event_time_cutoff_exclusive.isna().all())
        self.assertTrue(cases.has_event_at_or_after_cutoff.isna().all())
        self.assertTrue(events.timestamp_before_partition_cutoff.isna().all())
        self.assertTrue(cases.timing_input_eligible.eq(cases.completed_timing_eligible).all())
        self.assertIsNone(self.design["source_extraction_cutoff"])
        self.assertFalse(self.cohort.ongoing_age_computable.any())

    def test_events_match_frozen_real_log_and_keep_future_evidence(self):
        manifest = bed.usage.verify_snapshot(bed.usage.clean.DEFAULT_OUTPUT)
        source = bed.usage.read_snapshot_table(bed.usage.clean.DEFAULT_OUTPUT, "event_log.csv", manifest)
        expected = source.loc[source.case_id.isin(self.cohort.order_id)].reset_index(drop=True)
        events = self.frames["event_log.csv"]
        pd.testing.assert_frame_equal(events[expected.columns], expected)
        self.assertEqual(len(events), self.cohort[bed.audit.TIMES].notna().sum().sum())
        self.assertEqual(set(events.case_id), set(self.cohort.order_id))
        bounded = events.loc[events.event_time_cutoff_exclusive.notna()]
        self.assertGreater((~bounded.timestamp_before_partition_cutoff).sum(), 0)
        self.assertTrue(bounded.timestamp_before_partition_cutoff.eq(bounded.timestamp.lt(bounded.event_time_cutoff_exclusive)).all())

    def test_feature_values_are_direct_durations_not_fitted_statistics(self):
        indexed = self.cohort.set_index("order_id")
        for name in bed.SPLITS:
            frame = self.frames[f"{name}_timing_features.csv"].set_index("order_id")
            cases = indexed.loc[frame.index]
            for feature, start, end in zip(bed.FEATURES, bed.audit.TIMES, bed.audit.TIMES[1:]):
                expected = (cases[end] - cases[start]).dt.total_seconds() / 86400
                pd.testing.assert_series_equal(frame[feature], expected, check_names=False)
            self.assertNotIn("total_cycle_time_days", frame.columns)
        self.assertEqual(bed.feature_contract()["model_feature_columns"], bed.FEATURES)
        self.assertFalse(bed.feature_contract()["training_performed"])

    def test_summary_denominators_and_review_exposure(self):
        summary = self.frames["split_summary.csv"]
        self.assertEqual(summary.cohort_orders.sum(), len(self.cohort))
        self.assertEqual(self.frames["monthly_counts.csv"].cohort_orders.sum(), len(self.cohort))
        self.assertEqual(self.frames["usage_by_split.csv"].orders.sum(), len(self.cohort))
        self.assertTrue(summary.cohort_orders.eq(summary.completed_timing_orders_in_snapshot + summary.timing_ineligible_orders).all())
        self.assertTrue(summary.completed_timing_orders_in_snapshot.eq(summary.timing_input_orders + summary.completed_orders_crossing_cutoff.fillna(0)).all())
        self.assertTrue(self.cohort.previously_human_reviewed.eq(self.cohort.order_id.isin(self.reviewed)).all())
        self.assertTrue(self.design["category_selection_was_exploratory"])
        self.assertGreater(self.cohort.previously_human_reviewed.sum(), 0)
        for frame in self.frames.values():
            self.assertNotIn("reviewer_label", frame.columns)
            self.assertNotIn("anomaly_label", frame.columns)

    def test_row_order_and_review_flags_do_not_change_assignment(self):
        frames, design = bed.build_experiment(self.cases.iloc[::-1], self.membership.iloc[::-1],
                                              self.window, self.reviewed)
        self.assertEqual(design, self.design)
        for filename in frames:
            pd.testing.assert_frame_equal(frames[filename], self.frames[filename])
        no_labels, _ = bed.build_experiment(self.cases, self.membership, self.window, set())
        for name in bed.SPLITS:
            pd.testing.assert_frame_equal(no_labels[f"{name}_timing_features.csv"], self.frames[f"{name}_timing_features.csv"])

    def test_invalid_boundaries_and_duplicate_real_case_fail(self):
        with self.assertRaisesRegex(ValueError, "start < train end"):
            bed.split_design(self.window, train_end=bed.VALIDATION_END)
        with self.assertRaisesRegex(ValueError, "timezone-naive"):
            bed.split_design(self.window, train_end="2018-03-01T00:00:00Z")
        with self.assertRaisesRegex(ValueError, "nonmissing and unique"):
            bed.build_experiment(pd.concat([self.cases, self.cases.iloc[:1]]), self.membership, self.window, self.reviewed)

    def test_versioned_export_hashes_schemas_and_source_guard(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "experiment"
            bad_parents = dict(self.parents, comparison_manifest_sha256="")
            with self.assertRaisesRegex(ValueError, "Source changed"):
                bed.write_experiment(self.frames, self.design, bad_parents, output=output)
            self.assertFalse(output.exists())
            bed.write_experiment(self.frames, self.design, self.parents, output=output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["design"], self.design)
            self.assertEqual(manifest["parents"], self.parents)
            self.assertFalse(manifest["model_training_performed"])
            self.assertFalse(manifest["human_labels_created"])
            for filename, digest in manifest["output_hashes"].items():
                self.assertEqual(bed.audit.file_hash(output / filename), digest)
            for filename in self.frames:
                loaded = bed.usage.read_snapshot_table(output, filename, manifest)
                self.assertEqual(loaded.shape, self.frames[filename].shape)
                self.assertEqual(loaded.columns.tolist(), self.frames[filename].columns.tolist())
            with self.assertRaises(FileExistsError):
                bed.write_experiment(self.frames, self.design, self.parents, output=output)


if __name__ == "__main__":
    unittest.main()
