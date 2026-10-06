"""Descriptive comparison checks use only real observations from frozen Olist snapshots."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import compare_olist_categories as compare


class OlistCategoryComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases, cls.pairs, cls.membership, cls.parents = compare.load_inputs()
        cls.frames, cls.design = compare.build_comparison(cls.cases, cls.pairs, cls.membership)
        cls.ledger = cls.frames["selection_ledger.csv"]
        cls.main = cls.cases.loc[cls.ledger.included]

    def test_all_source_orders_and_exclusions_reconcile(self):
        self.assertEqual(self.ledger.order_id.tolist(), self.cases.order_id.tolist())
        self.assertTrue(self.ledger.order_id.is_unique)
        self.assertEqual(self.frames["selection_summary.csv"].orders.sum(), len(self.cases))
        self.assertTrue(self.ledger.included.eq(self.ledger.disposition.eq("included")).all())
        self.assertFalse(self.ledger.loc[~self.ledger.category_eligible, "included"].any())
        self.assertFalse(self.ledger.loc[~self.ledger.completed_timing_eligible, "included"].any())
        self.assertTrue(self.ledger.loc[~self.ledger.category_eligible, "disposition"].str.startswith("category_scope:").all())

    def test_gates_and_window_use_coverage_not_duration_outcomes(self):
        columns = ["category_assignment_eligible", compare.CAT, compare.MONTH, "order_purchase_timestamp"]
        gate, monthly, window = compare.select_window(self.cases[columns], compare.DEFAULT_RULES)
        self.assertEqual(window, self.design["window"])
        chosen = monthly.loc[monthly.candidate & monthly.in_common_window]
        self.assertTrue(chosen.supported_month.all())
        self.assertEqual(chosen.groupby(compare.CAT).size().nunique(), 1)
        self.assertTrue(chosen.groupby(compare.CAT).size().eq(window["month_count"]).all())
        self.assertTrue(gate.loc[gate.candidate, "single_category_orders"].ge(2000).all())
        self.assertTrue(gate.loc[gate.candidate, "supported_months"].ge(12).all())
        self.assertGreater((~gate.candidate).sum(), 0)

    def test_common_period_is_purchase_based_and_does_not_truncate_deliveries(self):
        window = self.design["window"]
        start, end = pd.Timestamp(window["start_inclusive"]), pd.Timestamp(window["end_exclusive"])
        expected = self.cases.order_purchase_timestamp.ge(start) & self.cases.order_purchase_timestamp.lt(end)
        self.assertTrue(self.ledger.in_common_window.eq(expected).all())
        self.assertTrue(self.main.order_purchase_timestamp.ge(start).all())
        self.assertTrue(self.main.order_purchase_timestamp.lt(end).all())
        self.assertGreater(self.main.order_delivered_customer_date.ge(end).sum(), 0)

    def test_category_and_period_denominators_are_explicit(self):
        overview = self.frames["category_overview.csv"].set_index(compare.CAT)
        groups = self.frames["period_usage_groups.csv"].groupby(compare.CAT).orders.sum()
        self.assertEqual(overview.single_category_orders.sum(), self.cases.category_assignment_eligible.sum())
        self.assertEqual(overview.orders_with_category_nonexclusive.sum(), len(self.membership))
        self.assertTrue(overview.period_single_category_orders.eq(overview.period_timing_eligible_orders + overview.period_timing_excluded_orders).all())
        pd.testing.assert_series_equal(overview.period_single_category_orders.sort_index(), groups.reindex(overview.index, fill_value=0).sort_index(), check_names=False)
        self.assertEqual(overview.comparison_orders.sum(), len(self.main))
        self.assertTrue(overview.loc[~overview.candidate, "comparison_orders"].eq(0).all())

    def test_duration_statistics_match_actual_observations_without_outlier_filter(self):
        for row in self.frames["duration_summary.csv"].itertuples(index=False):
            data = self.main.loc[self.main[compare.CAT].eq(row.single_category_name), row.duration].to_numpy()
            self.assertEqual(len(data), row.orders)
            self.assertAlmostEqual(row.mean_days, np.mean(data))
            self.assertAlmostEqual(row.median_days, np.median(data))
            self.assertAlmostEqual(row.p95_days, np.quantile(data, .95))
            self.assertEqual(row.zero_duration_orders, int((data == 0).sum()))
        self.assertGreater(self.main.total_cycle_time_days.max(), 100)
        self.assertGreater(self.main.purchased_to_approved_days.eq(0).sum(), 0)

    def test_seller_enrichment_never_assigns_an_arbitrary_seller(self):
        self.assertTrue(self.cases.loc[self.cases.seller_count.ne(1), "single_seller_id"].isna().all())
        expected = self.pairs.groupby("order_id").seller_id.nunique()
        actual = self.cases.set_index("order_id").seller_count
        pd.testing.assert_series_equal(actual, expected.reindex(actual.index, fill_value=0), check_names=False)
        for row in self.frames["seller_composition.csv"].itertuples(index=False):
            group = self.main.loc[self.main[compare.CAT].eq(row.single_category_name)]
            single = group.loc[group.seller_count.eq(1)]
            self.assertEqual(row.single_seller_orders + row.multi_seller_orders, len(group))
            self.assertAlmostEqual(row.largest_seller_share_among_single_seller,
                                   single.single_seller_id.value_counts().max() / len(single))

    def test_region_weights_and_means_match_independent_case_weighting(self):
        strata = self.frames["common_region_month_strata.csv"]
        self.assertAlmostEqual(strata.reference_weight.sum(), 1)
        self.assertTrue(strata.minimum_category_orders.ge(compare.DEFAULT_RULES["min_stratum_orders"]).all())
        data = self.main.merge(strata[[compare.MONTH, "customer_state", "reference_weight"]],
                               on=[compare.MONTH, "customer_state"], how="inner", validate="many_to_one")
        n = data.groupby([compare.CAT, compare.MONTH, "customer_state"]).order_id.transform("size")
        data["weighted_days"] = data.total_cycle_time_days * data.reference_weight / n
        for row in self.frames["region_standardization.csv"].itertuples(index=False):
            group = data.loc[data[compare.CAT].eq(row.single_category_name)]
            self.assertEqual(row.supported_orders, len(group))
            self.assertAlmostEqual(row.standardized_mean_total_days, group.weighted_days.sum())
            self.assertAlmostEqual(row.raw_mean_on_supported_strata_days, group.total_cycle_time_days.mean())
            self.assertGreater(row.supported_fraction, 0)
            self.assertLessEqual(row.supported_fraction, 1)

    def test_empty_common_support_is_unavailable_not_zero(self):
        region, common, cells = compare.region_sensitivity(self.main, self.design["window"]["candidates"], len(self.main) + 1)
        self.assertTrue(common.empty)
        self.assertTrue(region.standardized_mean_total_days.isna().all())
        self.assertTrue(region.supported_orders.eq(0).all())
        self.assertFalse(cells.in_common_support.any())

    def test_tail_sensitivity_changes_only_the_purchase_window(self):
        tail = self.frames["tail_month_sensitivity.csv"]
        last = self.design["window"]["last_month"]
        for category, group in tail.groupby(compare.CAT):
            base = group.loc[group.scenario.eq("common_window")].iloc[0]
            trimmed = group.loc[group.scenario.eq("omit_last_purchase_month")].iloc[0]
            actual_last = self.main.loc[self.main[compare.CAT].eq(category) & self.main.purchase_month.eq(last)]
            self.assertEqual(base.timing_eligible_orders - trimmed.timing_eligible_orders, len(actual_last))
            self.assertEqual(base.timing_excluded_orders + base.timing_eligible_orders, base.all_single_category_orders)

    def test_row_order_does_not_change_results(self):
        frames, design = compare.build_comparison(self.cases.iloc[::-1], self.pairs.iloc[::-1], self.membership.iloc[::-1])
        self.assertEqual(design, self.design)
        for filename in frames:
            pd.testing.assert_frame_equal(frames[filename], self.frames[filename])

    def test_impossible_coverage_rules_fail_instead_of_silent_fallback(self):
        rules = dict(compare.DEFAULT_RULES, min_category_orders=len(self.cases) + 1)
        with self.assertRaisesRegex(ValueError, "Fewer than two"):
            compare.select_window(self.cases, rules)
        with self.assertRaisesRegex(ValueError, "positive integers"):
            compare.select_window(self.cases, dict(compare.DEFAULT_RULES, min_month_orders=0))

    def test_versioned_export_and_parent_output_hashes(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "comparison"
            compare.write_comparison(self.frames, self.design, self.parents, output=output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["parents"], self.parents)
            self.assertEqual(manifest["design"], self.design)
            self.assertFalse(manifest["primary_category_selected"])
            self.assertFalse(manifest["model_training_performed"])
            for filename, digest in manifest["output_hashes"].items():
                self.assertEqual(compare.audit.file_hash(output / filename), digest)
            with self.assertRaises(FileExistsError):
                compare.write_comparison(self.frames, self.design, self.parents, output=output)


if __name__ == "__main__":
    unittest.main()
