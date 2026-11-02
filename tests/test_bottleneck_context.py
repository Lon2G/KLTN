"""Context analysis checks using actual guarded Olist records, not fake research data."""

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
import analyze_bottleneck_context as context


class BottleneckContextTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(context.signals.performance.inspection.integration.delivery, "fit_entry",
                             side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = context.load_inputs()
            cls.frames, cls.summary = context.build_tables(cls.inputs)
        cls.stages = cls.frames["stage_context.csv"]
        cls.groups = cls.frames["context_groups.csv"]
        cls.cells = cls.frames["matched_cells.csv"]
        cls.members = cls.frames["group_membership.csv"]
        cls.actions = cls.frames["investigation_actions.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"context"
        context.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_original_real_scope_and_signals_preserved(self):
        expected = self.inputs["stage_signals.csv"].sort_values(["stage", "order_id"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(self.stages[expected.columns], expected, check_exact=True)
        self.assertEqual(self.summary["validation_orders"], 1621)
        self.assertEqual(len(self.stages), 4863)
        self.assertTrue(self.stages.split.eq("validation").all())
        self.assertEqual(int(self.stages.assessable.sum()), 3921)
        self.assertEqual(int(self.stages.long_stage_warning.sum()), 170)
        self.assertEqual(self.summary["long_stage_unique_orders"], 165)
        self.assertEqual(self.summary["calendar_late_orders"], 149)

    def test_stage_time_is_not_always_purchase_month(self):
        expected = self.stages.start_timestamp.dt.strftime("%Y-%m").astype("string").fillna(context.UNKNOWN)
        pd.testing.assert_series_equal(self.stages.stage_start_month, expected, check_names=False)
        known = self.stages.start_timestamp.notna()
        self.assertGreater(int((self.stages.loc[known, "stage_start_month"] != self.stages.loc[known, "purchase_month"]).sum()), 0)
        self.assertTrue(self.stages.loc[known, "days_from_start_to_cutoff"].gt(0).all())
        self.assertTrue(self.stages.loc[~known, "days_from_start_to_cutoff"].isna().all())

    def test_each_dimension_partitions_orders_per_stage(self):
        for (_, _), rows in self.groups.groupby(["stage", "dimension"]):
            self.assertEqual(int(rows.total_orders.sum()), 1621)
            self.assertEqual(int(rows.assessed_orders.sum()), 1307)
            self.assertEqual(int(rows.unassessed_orders.sum()), 314)
        overview = self.frames["stage_overview.csv"].set_index("stage")
        self.assertEqual(overview.long_stage_orders.to_dict(), {"approval": 41, "preparation_handoff": 17, "transport": 112})
        self.assertTrue(overview.calendar_comparable_orders.eq(1359).all())
        self.assertTrue(overview.calendar_late_orders.eq(149).all())

    def test_actual_denominators_not_all_orders_or_missing_as_negative(self):
        for row in self.groups.itertuples(index=False):
            self.assertEqual(row.total_orders, row.assessed_orders+row.unassessed_orders)
            self.assertEqual(row.unassessed_orders, row.source_conflict_orders+row.missing_endpoint_orders+
                             row.parent_policy_excluded_orders+row.context_reference_unavailable_orders)
            if row.assessed_orders:
                self.assertEqual(row.long_stage_rate, row.long_stage_orders/row.assessed_orders)
            else:
                self.assertTrue(pd.isna(row.long_stage_rate))
            if row.calendar_comparable_orders:
                self.assertEqual(row.calendar_late_rate, row.calendar_late_orders/row.calendar_comparable_orders)
            else:
                self.assertTrue(pd.isna(row.calendar_late_rate))
        unknown = self.stages.loc[~self.stages.assessable]
        self.assertTrue(unknown.long_stage_warning.isna().all())
        included = self.members.loc[self.members.included_in_comparison].merge(
            self.stages[["order_id", "stage", "assessable"]], on=["order_id", "stage"], validate="many_to_one")
        self.assertTrue(included.assessable.all())

    def test_membership_contains_every_target_and_no_target_in_controls(self):
        self.assertFalse(self.members.duplicated(["group_id", "order_id"]).any())
        for row in self.groups.itertuples(index=False):
            pool = self.stages.loc[self.stages.stage.eq(row.stage)]
            target = pool if row.dimension == "overall" else pool.loc[pool[row.dimension].fillna(context.UNKNOWN).eq(row.group_value)]
            members = self.members.loc[self.members.group_id.eq(row.group_id)]
            self.assertEqual(set(members.loc[members.role.eq("target"), "order_id"]), set(target.order_id))
            self.assertFalse(set(members.loc[members.role.eq("comparator"), "order_id"]) & set(target.order_id))
            self.assertTrue(set(members.order_id).issubset(pool.order_id))

    def test_matched_cells_match_exact_context_with_real_counts(self):
        member_groups = {key: group for key, group in self.members.groupby("cell_id", dropna=True)}
        group_index = self.groups.set_index("group_id")
        for row in self.cells.itertuples(index=False):
            members = member_groups[row.cell_id]
            data = members.merge(self.stages, on=["order_id", "stage"], validate="one_to_one")
            target = data.loc[data.role.eq("target")]
            self.assertEqual(len(target), row.target_orders)
            self.assertEqual(int(target.long_stage_warning.sum()), row.target_signals)
            fields = json.loads(row.match_context_json)
            for field, value in fields.items():
                self.assertTrue(data[field].fillna(context.UNKNOWN).eq(value).all())
            if row.included:
                peers = data.loc[data.role.eq("comparator")]
                self.assertEqual(len(peers), row.comparator_orders)
                self.assertEqual(int(peers.long_stage_warning.sum()), row.comparator_signals)
                self.assertGreaterEqual(row.target_orders, 5)
                self.assertGreaterEqual(row.comparator_orders, 5)
                self.assertTrue(members.included_in_comparison.all())
            else:
                self.assertFalse(members.included_in_comparison.any())
                self.assertTrue(pd.isna(row.target_weight))
            self.assertIn(group_index.loc[row.group_id, "stage"], context.signals.STAGES)

    def test_empty_real_comparator_does_not_become_zero_rate(self):
        target = self.stages.loc[self.stages.stage.eq("transport") & self.stages.customer_state.eq("MG")]
        stats, cells, members = context.match_group(target, target.iloc[:0],
                                                    ["distance_band", "seller_attribution_scope", "stage_start_month"], "empty-peer-check")
        self.assertEqual(stats["matched_target_orders"], 0)
        self.assertTrue(pd.isna(stats["matched_comparator_rate"]))
        self.assertFalse(cells.included.any())
        self.assertTrue(cells.target_weight.isna().all())
        self.assertTrue(all(row["role"] == "target" and not row["included_in_comparison"] for row in members))

    def test_no_assessable_real_target_keeps_counts_numeric_and_rates_unknown(self):
        unknown = self.stages.loc[~self.stages.assessable & self.stages.stage.eq("transport")]
        self.assertGreater(len(unknown), 0)
        stats, cells, members = context.match_group(unknown, self.stages.iloc[:0],
                                                    ["distance_band", "seller_attribution_scope", "stage_start_month"], "unknown-check")
        self.assertTrue(cells.empty)
        self.assertEqual(str(cells.target_orders.dtype), "int64")
        self.assertEqual(str(cells.included.dtype), "bool")
        self.assertEqual(members, [])
        self.assertTrue(pd.isna(stats["target_match_coverage"]))
        self.assertTrue(pd.isna(stats["matched_target_rate"]))

    def test_weighted_rates_reconcile_to_cells_not_pooled_rate(self):
        differs_from_naive_pool = False
        for row in self.groups.itertuples(index=False):
            cells = self.cells.loc[self.cells.group_id.eq(row.group_id) & self.cells.included]
            if not len(cells):
                self.assertTrue(pd.isna(row.matched_target_rate))
                self.assertTrue(pd.isna(row.matched_comparator_rate))
                self.assertEqual(row.matched_target_orders, 0)
                continue
            n = int(cells.target_orders.sum())
            self.assertEqual(row.matched_target_orders, n)
            self.assertEqual(row.matched_comparator_orders, int(cells.comparator_orders.sum()))
            self.assertAlmostEqual(float(cells.target_weight.sum()), 1)
            actual = sum(int(c.target_orders)/n * int(c.comparator_signals)/int(c.comparator_orders)
                         for c in cells.itertuples(index=False))
            self.assertAlmostEqual(actual, row.matched_comparator_rate)
            self.assertEqual(row.matched_target_rate, int(cells.target_signals.sum())/n)
            self.assertEqual(row.matched_rate_difference, row.matched_target_rate-row.matched_comparator_rate)
            self.assertEqual(row.target_match_coverage, n/row.assessed_orders)
            pooled = int(cells.comparator_signals.sum())/int(cells.comparator_orders.sum())
            differs_from_naive_pool |= not np.isclose(pooled, actual)
        self.assertTrue(differs_from_naive_pool)

    def test_partial_month_not_target_or_comparator_in_month_comparisons(self):
        month_groups = self.groups.loc[self.groups.dimension.eq("stage_start_month")]
        partial = month_groups.loc[month_groups.group_value.eq("2018-05")]
        self.assertEqual(len(partial), 3)
        self.assertFalse(partial.investigation_candidate.any())
        self.assertTrue(partial.comparison_status.eq("partial_calendar_month_descriptive_only").all())
        self.assertTrue(partial.matched_target_rate.isna().all())
        members = self.members.loc[self.members.group_id.isin(month_groups.group_id) & self.members.included_in_comparison]
        data = members.merge(self.stages, on=["order_id", "stage"], validate="many_to_one")
        self.assertFalse(data.start_month_contains_cutoff.any())
        complete = month_groups.loc[month_groups.group_value.isin(["2018-03", "2018-04"])]
        self.assertTrue(complete.comparator_assessed_orders_excluded_partial_month.gt(0).all())

    def test_state_comparisons_match_distance_month_scope_and_exclude_target_state(self):
        selected = self.groups.loc[self.groups.dimension.eq("customer_state")]
        joined = self.members.loc[self.members.group_id.isin(selected.group_id) & self.members.included_in_comparison].merge(
            self.stages, on=["order_id", "stage"], validate="many_to_one")
        for (_, _), group in joined.groupby(["group_id", "cell_id"]):
            for field in ["distance_band", "stage_start_month", "seller_attribution_scope"]:
                self.assertEqual(group[field].nunique(), 1)
            target_states = set(group.loc[group.role.eq("target"), "customer_state"])
            self.assertFalse(target_states & set(group.loc[group.role.eq("comparator"), "customer_state"]))

    def test_distance_and_overall_are_descriptive_not_fake_matched_controls(self):
        rows = self.groups.loc[self.groups.dimension.isin(["overall", "distance_band"])]
        self.assertTrue(rows.comparison_status.eq("descriptive_only").all())
        self.assertFalse(rows.investigation_candidate.any())
        self.assertTrue(rows.matched_comparator_rate.isna().all())
        self.assertFalse(self.actions.dimension.isin(["overall", "distance_band"]).any())

    def test_support_guards_and_small_samples_are_explicit(self):
        supported = self.groups.loc[self.groups.comparison_status.eq("supported_descriptive_comparison")]
        self.assertGreater(len(supported), 0)
        self.assertTrue(supported.matched_target_orders.ge(30).all())
        self.assertTrue(supported.matched_comparator_orders.ge(30).all())
        self.assertTrue(supported.target_match_coverage.ge(.8).all())
        expected = (self.groups.comparison_status.eq("supported_descriptive_comparison")
                    & self.groups.matched_target_signals.ge(5) & self.groups.matched_rate_difference.gt(0))
        pd.testing.assert_series_equal(expected, self.groups.investigation_candidate, check_names=False)
        insufficient = self.groups.loc[self.groups.comparison_status.ne("supported_descriptive_comparison")]
        self.assertFalse(insufficient.investigation_candidate.any())
        self.assertGreater(int(self.groups.assessed_orders.eq(0).sum()), 0)

    def test_known_real_transport_group_is_traceable(self):
        row = self.groups.loc[self.groups.stage.eq("transport") & self.groups.dimension.eq("customer_state")
                              & self.groups.group_value.eq("MG")].iloc[0]
        self.assertEqual(row.total_orders, 195)
        self.assertEqual(row.assessed_orders, 156)
        self.assertEqual(row.matched_target_orders, 141)
        self.assertEqual(row.matched_target_signals, 30)
        self.assertEqual(row.matched_comparator_orders, 775)
        self.assertAlmostEqual(row.matched_target_rate, 30/141)
        self.assertGreater(row.matched_rate_difference, .1)
        self.assertTrue(row.investigation_candidate)

    def test_actions_are_evidence_linked_hypotheses_not_findings_of_cause(self):
        expected = set(self.groups.loc[self.groups.investigation_candidate, "group_id"])
        self.assertEqual(set(self.actions.group_id), expected)
        self.assertTrue(self.actions.cause_status.eq("unverified_hypothesis").all())
        self.assertTrue(self.actions.action_status.eq("investigate_before_intervention").all())
        for field in context.ACTION_COLUMNS:
            self.assertFalse(self.actions[field].isna().any())
            self.assertTrue(self.actions[field].str.len().gt(0).all())
        self.assertTrue(self.actions.evidence_locator.eq("context_groups.csv:group_id="+self.actions.group_id).all())
        self.assertEqual(self.summary["causes_confirmed"], 0)
        self.assertEqual(self.summary["interventions_evaluated"], 0)

    def test_investigation_count_requires_matching_order_and_stage_not_order_only(self):
        selected = self.members.loc[self.members.group_id.isin(self.actions.group_id) & self.members.role.eq("target")
                                    & self.members.included_in_comparison, ["order_id", "stage"]].drop_duplicates()
        longs = self.stages.loc[self.stages.long_stage_warning.fillna(False), ["order_id", "stage"]]
        joined = selected.merge(longs, on=["order_id", "stage"], validate="one_to_one")
        self.assertEqual(self.summary["investigation_unique_orders"], joined.order_id.nunique())

    def test_no_model_training_labels_test_scoring_or_claimed_accuracy(self):
        # Source integrity checks revisit historical lineage, not analysis inputs.
        self.assertEqual(set(self.inputs), {"order_signals.csv", "stage_signals.csv"})
        self.assertTrue(all(frame.split.eq("validation").all() for frame in self.inputs.values()))
        self.assertFalse(any("test_timing" in path or "test_scores" in path or "partition_plan.csv" in path
                             for path in self.reads))
        self.assertEqual(self.summary["reference_labels_created"], 0)
        for name in ["model_trained", "threshold_changed", "test_scored", "independent_accuracy_computed",
                     "near_real_time_validated", "multi_agent_implemented"]:
            self.assertFalse(self.summary[name])
        self.assertTrue(self.groups.causal_status.eq("association_only_no_confirmed_operational_cause").all())

    def test_input_order_does_not_change_output(self):
        shuffled = {name: frame.sample(frac=1, random_state=42) for name, frame in self.inputs.items()}
        frames, summary = context.build_tables(shuffled)
        self.assertEqual(summary, self.summary)
        for name in context.TABLES:
            pd.testing.assert_frame_equal(frames[name], self.frames[name], check_exact=True)

    def test_invalid_scope_duplicate_keys_and_unknown_flags_fail(self):
        for kind in ["test", "duplicate", "missing_stage", "unknown_flag", "future"]:
            broken = {name: frame.copy() for name, frame in self.inputs.items()}
            if kind == "test":
                broken["order_signals.csv"].loc[0, "split"] = "test"
            elif kind == "duplicate":
                broken["order_signals.csv"] = pd.concat([broken["order_signals.csv"], broken["order_signals.csv"].iloc[:1]])
            elif kind == "missing_stage":
                broken["stage_signals.csv"] = broken["stage_signals.csv"].iloc[1:]
            elif kind == "unknown_flag":
                rows = broken["stage_signals.csv"]
                index = rows.loc[rows.assessable].index[0]
                rows.loc[index, "long_stage_warning"] = pd.NA
            else:
                rows = broken["stage_signals.csv"]
                rows.loc[0, "end_timestamp"] = rows.loc[0, "visible_before_exclusive"]
            with self.subTest(kind=kind), self.assertRaises((ValueError, AssertionError)):
                context.build_tables(broken)

    def test_snapshot_roundtrip_report_and_integrity(self):
        manifest = context.load_snapshot(self.output)
        restored = {name: context.shared.read_table(self.output, name, manifest) for name in context.TABLES}
        for name in context.TABLES:
            pd.testing.assert_frame_equal(restored[name], self.frames[name], check_dtype=False, check_exact=True)
        self.assertEqual((self.output/"bao_cao_boi_canh.md").read_text(), context.render_report(restored, self.summary))
        self.assertIn("chưa phải", (self.output/"bao_cao_boi_canh.md").read_text())
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"context_groups.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            context.load_snapshot(damaged)

    def test_existing_source_and_nested_destinations_are_rejected(self):
        with self.assertRaises(FileExistsError):
            context.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [ROOT/"data/raw/context", context.signals.DEFAULT_OUTPUT/"nested"]:
            with self.assertRaises(ValueError):
                context.write_snapshot(self.frames, self.summary, self.provenance, path)


if __name__ == "__main__":
    unittest.main()
