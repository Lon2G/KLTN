"""Usage rules tested against real imported observations, without generated labels."""

import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import apply_olist_usage_policy as usage


class OlistUsagePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames, cls.parent_hash = usage.prepare_policy()
        cls.snapshot = usage.clean.DEFAULT_OUTPUT
        cls.manifest = usage.verify_snapshot(cls.snapshot)
        cls.source = usage.read_snapshot_table(cls.snapshot, "order_quality.csv", cls.manifest)
        cls.cases = cls.frames["case_usage_policy.csv"]

    def test_every_source_case_and_observation_is_preserved(self):
        original = self.source.sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.cases[original.columns], original)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertTrue(self.cases.process_evidence_retained.all())
        self.assertFalse(self.cases.ongoing_age_computable.any())

    def test_exclusive_groups_reconcile_without_hiding_overlaps(self):
        summary = self.frames["usage_group_summary.csv"]
        self.assertEqual(summary.case_count.sum(), len(self.source))
        self.assertTrue(self.cases.usage_group.isin(usage.GROUPS).all())
        for row in self.cases.itertuples(index=False):
            reasons = set(json.loads(row.timing_exclusion_reasons))
            self.assertEqual("missing_milestones" in reasons, row.missing_milestone_count > 0)
            self.assertEqual("reversed_recorded_timeline" in reasons, row.has_reversed_recorded_milestones)
            self.assertEqual("not_delivered_status" in reasons, row.order_status != "delivered")

    def test_status_context_does_not_label_incomplete_orders(self):
        cases = self.cases
        closed = cases.usage_group.eq("canceled_or_unavailable_incomplete")
        self.assertTrue(cases.loc[closed, "order_status"].isin(["canceled", "unavailable"]).all())
        nonfinal = cases.usage_group.eq("nonfinal_status_cutoff_unknown")
        self.assertTrue(cases.loc[nonfinal, "missing_milestone_count"].gt(0).all())
        self.assertFalse(cases.loc[closed | nonfinal, "source_verification_priority"].any())
        self.assertFalse(any("label" in c or "probability" in c for c in cases))
        self.assertTrue(cases.loc[cases.status_delivery_conflict, "usage_group"].eq("status_delivery_conflict").all())

    def test_completed_view_has_no_negative_or_missing_duration_but_keeps_extremes(self):
        timing = self.frames["completed_timing_cases.csv"]
        self.assertEqual(set(timing.order_id), set(self.source.loc[self.source.completed_timing_eligible, "order_id"]))
        self.assertTrue(timing[usage.DURATIONS].notna().all().all())
        self.assertTrue(timing[usage.DURATIONS].ge(0).all().all())
        self.assertGreater(timing.purchased_to_approved_days.eq(0).sum(), 0)
        self.assertGreater(timing.total_cycle_time_days.max(), 100)
        self.assertGreater(self.cases.negative_transition_count.gt(0).sum(), 0)

    def test_pairwise_observations_never_override_completed_case_rule(self):
        for column in usage.DURATIONS:
            value = self.cases[column]
            self.assertTrue(self.cases[f"{column}_nonnegative_observed"].eq(value.notna() & value.ge(0)).all())
            self.assertTrue(self.cases.loc[value.lt(0), f"{column}_state"].eq("negative_observed").all())
            self.assertTrue(self.cases.loc[value.isna(), f"{column}_state"].eq("missing_endpoint").all())
        incomplete = self.cases.missing_milestone_count.gt(0)
        self.assertTrue(self.cases.loc[incomplete, "purchased_to_approved_days_nonnegative_observed"].any())
        self.assertFalse(self.cases.loc[incomplete, "completed_timing_eligible"].any())

    def test_physical_features_mask_only_invalid_field_values(self):
        products = usage.read_snapshot_table(self.snapshot, usage.audit.FILES["products"], self.manifest)
        policy = self.frames["product_measurement_policy.csv"]
        self.assertEqual(policy.product_id.tolist(), products.product_id.tolist())
        for column in usage.clean.PHYSICAL:
            pd.testing.assert_series_equal(policy[column], products[column])
            valid = products[column].notna() & products[column].gt(0)
            self.assertTrue(policy[f"{column}_usable"].eq(valid).all())
            self.assertTrue(policy.loc[~valid, f"{column}_feature"].isna().all())
            np.testing.assert_array_equal(policy.loc[valid, f"{column}_feature"], products.loc[valid, column])

    def test_issue_actions_and_examples_are_traceable_to_inputs(self):
        source_issues = usage.read_snapshot_table(self.snapshot, "audit/quality_issues.csv", self.manifest)
        actions = self.frames["quality_issue_actions.csv"]
        pd.testing.assert_frame_equal(actions[source_issues.columns], source_issues)
        self.assertFalse(actions.usage_action.isna().any())
        examples = self.frames["real_case_examples.csv"]
        self.assertTrue(examples.order_id.is_unique)
        self.assertTrue(examples.order_id.isin(self.source.order_id).all())
        expected = self.cases.set_index("order_id").loc[examples.order_id, examples.columns.drop("order_id")].reset_index()
        pd.testing.assert_frame_equal(examples, expected)

    def test_policy_is_independent_of_input_row_order(self):
        reversed_policy = usage.build_case_policy(self.source.iloc[::-1])
        pd.testing.assert_frame_equal(reversed_policy, self.cases)

    def test_publication_is_versioned_and_all_output_hashes_match(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "policy"
            usage.write_policy(self.frames, self.parent_hash, self.snapshot, output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["source_manifest_sha256"], self.parent_hash)
            for filename, digest in manifest["output_hashes"].items():
                self.assertEqual(usage.audit.file_hash(output / filename), digest)
            with self.assertRaises(FileExistsError):
                usage.write_policy(self.frames, self.parent_hash, self.snapshot, output)

    def test_missing_or_modified_snapshot_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            snapshot = Path(temporary)
            shutil.copyfile(self.snapshot / "manifest.json", snapshot / "manifest.json")
            with self.assertRaisesRegex(ValueError, "Invalid snapshot file"):
                usage.verify_snapshot(snapshot)
            shutil.copyfile(self.snapshot / "README.md", snapshot / "README.md")
            with (snapshot / "README.md").open("a") as handle:
                handle.write("\n")
            with self.assertRaisesRegex(ValueError, "Snapshot hash mismatch"):
                usage.verify_snapshot(snapshot)


if __name__ == "__main__":
    unittest.main()
