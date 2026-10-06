"""Checks against real imported Olist records, with no generated observations/labels."""

from pathlib import Path
import sys
import unittest
import json

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import olist_import_audit as audit


class OlistImportAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tables, cls.inventory = audit.load_tables()
        cls.cases, cls.items, cls.membership = audit.build_order_categories(cls.tables)
        cls.profile = audit.profile_categories(cls.cases, cls.items, cls.membership, cls.tables["translation"])

    def test_raw_order_values_and_grain_preserved(self):
        raw = self.tables["orders"].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.cases[raw.columns], raw)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertEqual(len(self.cases), len(raw))

    def test_counts_do_not_multiply_child_records(self):
        self.assertEqual(self.cases.item_count.sum(), len(self.tables["order_items"]))
        self.assertEqual(self.cases.payment_record_count.sum(), len(self.tables["order_payments"]))
        self.assertEqual(self.cases.review_record_count.sum(), len(self.tables["order_reviews"]))
        self.assertEqual(self.cases.missing_category_item_count.sum(), self.items.product_category_name.isna().sum())

    def test_categories_match_all_actual_order_items(self):
        expected = self.items.dropna(subset=["product_category_name"]).groupby("order_id").product_category_name.agg(set)
        for row in self.cases.itertuples(index=False):
            self.assertEqual(set(json.loads(row.categories_json)), expected.get(row.order_id, set()))
        single = self.cases.category_scope.eq("single_category")
        self.assertTrue(self.cases.loc[single, "known_category_count"].eq(1).all())
        self.assertTrue(self.cases.loc[single, "missing_category_item_count"].eq(0).all())
        self.assertTrue(self.cases.loc[~single, "single_category_name"].isna().all())
        self.assertEqual(self.profile.single_category_orders.sum(), single.sum())
        self.assertEqual(self.profile.orders_with_category.sum(), len(self.membership))

    def test_missing_data_and_reversals_are_not_repaired(self):
        parsed = self.cases[audit.TIMES].apply(pd.to_datetime, errors="raise")
        self.assertTrue(self.cases.missing_milestone_count.eq(parsed.isna().sum(axis=1)).all())
        for start, end, column in zip(audit.TIMES, audit.TIMES[1:], audit.DURATIONS):
            expected = (parsed[end] - parsed[start]).dt.total_seconds() / 86400
            np.testing.assert_allclose(self.cases[column], expected, equal_nan=True)
        self.assertTrue(self.cases.negative_transition_count.gt(0).any())
        self.assertTrue(self.cases.missing_milestone_count.gt(0).any())

    def test_timing_cohort_and_category_statistics_reconcile(self):
        eligible = self.cases.loc[self.cases.delivered_complete_nondecreasing]
        self.assertTrue(eligible.order_status.eq("delivered").all())
        self.assertTrue(eligible.missing_milestone_count.eq(0).all())
        self.assertTrue(eligible[audit.DURATIONS].ge(0).all().all())
        for row in self.profile.itertuples(index=False):
            subset = eligible.loc[eligible.single_category_name.eq(row.product_category_name)]
            self.assertEqual(row.delivered_complete_nondecreasing_orders, len(subset))
            if len(subset):
                self.assertAlmostEqual(row.eligible_total_days_median, subset.total_cycle_time_days.median())
                self.assertAlmostEqual(row.eligible_total_days_p95, subset.total_cycle_time_days.quantile(.95))

    def test_translations_are_source_only(self):
        expected = self.tables["translation"].set_index("product_category_name").product_category_name_english
        actual = self.profile.set_index("product_category_name").product_category_name_english
        pd.testing.assert_series_equal(actual, expected.reindex(actual.index), check_names=False)

    def test_core_relations_pass_and_optional_gaps_stay_visible(self):
        relations = audit.validate_core(self.tables)
        self.assertTrue(relations.loc[relations.blocking, "unmatched_nonnull_rows"].eq(0).all())
        self.assertTrue(relations.loc[relations.parent_table.eq("translation"), "unmatched_nonnull_rows"].gt(0).all())
        self.assertGreater(self.inventory.loc[self.inventory.table.eq("geolocation"), "exact_duplicate_excess_rows"].iloc[0], 0)

    def test_repeated_real_primary_key_fails_without_deduplication(self):
        tables = dict(self.tables)
        tables["orders"] = pd.concat([tables["orders"], tables["orders"].iloc[:1]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate key in orders"):
            audit.build_order_categories(tables)

    def test_removed_real_parent_fails_instead_of_losing_items(self):
        tables = dict(self.tables)
        product = tables["order_items"].product_id.iloc[0]
        tables["products"] = tables["products"].loc[~tables["products"].product_id.eq(product)]
        with self.assertRaisesRegex(ValueError, "Invalid core foreign keys"):
            audit.build_order_categories(tables)


if __name__ == "__main__":
    unittest.main()
