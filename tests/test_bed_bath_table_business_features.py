"""Feature checks use real imported Olist rows, not fabricated thesis observations."""

from decimal import Decimal
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
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import build_bed_bath_table_business_features as business


class BusinessFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original_read = pd.read_csv

        def record(path, *args, **kwargs):
            cls.reads.append((str(path), kwargs.get("usecols")))
            return original_read(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=record):
            cls.orders, cls.sources, cls.provenance = business.load_inputs()
        cls.frames, cls.summary = business.build_features(cls.orders, cls.sources)
        cls.summary["reserved_test_orders_excluded"] = cls.provenance["reserved_test_orders_excluded"]
        cls.features = cls.frames["order_features.csv"].set_index("order_id")
        cls.context = cls.frames["order_context.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "snapshot"
        business.write_features(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.restored, cls.manifest = business.load_feature_snapshot(cls.output)

    def test_real_population_and_separate_missing_timing_coverage(self):
        self.assertEqual(self.summary["development_orders"], 7333)
        self.assertEqual(self.summary["split_orders"], {"train": 5480, "validation": 1853})
        self.assertEqual(self.summary["payment_records"], 7814)
        self.assertEqual(self.summary["item_records"], 8642)
        self.assertEqual(self.summary["orders_with_timing_features"], 6601)
        self.assertEqual(self.summary["orders_without_timing_features_retained"], 732)
        self.assertTrue(self.features.index.is_unique)
        self.assertSetEqual(set(self.features.index), set(self.orders.order_id))
        excluded = self.features.loc[~self.features.timing_input_eligible]
        self.assertTrue(excluded[business.audit.DURATIONS].isna().all().all())
        self.assertTrue(excluded.payment_value_sum.notna().all())
        expected = self.orders.set_index("order_id").sort_index()
        observed = self.context.set_index("order_id").sort_index()
        pd.testing.assert_frame_equal(observed[expected.columns], expected)

    def test_test_reservation_ledger_is_identifiers_only_and_no_review_labels_read(self):
        ledgers = [(path, cols) for path, cols in self.reads if Path(path).name == "cohort_cases.csv"]
        self.assertEqual(len(ledgers), 1)
        self.assertEqual(ledgers[0][1], ["order_id", "split"])
        self.assertFalse(any("test_timing" in path or "manual_validation" in path or "models/" in path for path, _ in self.reads))
        self.assertEqual(self.provenance["reserved_test_orders_excluded"], 1691)
        ledger = pd.read_csv(Path(self.provenance["reservation_directory"])/"cohort_cases.csv", usecols=["order_id", "split"])
        reserved = set(ledger.loc[ledger.split.eq("test"), "order_id"])
        for name, frame in self.frames.items():
            if "order_id" in frame:
                self.assertFalse(set(frame.order_id) & reserved, name)

    def test_exact_source_amount_sums_without_installment_multiplication(self):
        expected = {}
        for row in self.sources["order_payments"].itertuples(index=False):
            expected[row.order_id] = expected.get(row.order_id, 0) + int(Decimal(str(row.payment_value))*100)
        for order_id, value in expected.items():
            self.assertEqual(self.features.at[order_id, "payment_value_sum_minor_units"], value)
            self.assertAlmostEqual(self.features.at[order_id, "payment_value_sum"], value/100)
        source = self.sources["order_payments"]
        multiplied = (source.payment_value*source.payment_installments).groupby(source.order_id).sum()
        self.assertTrue((multiplied-self.features.payment_value_sum.reindex(multiplied.index)).abs().gt(1).any())

    def test_item_amounts_and_signed_reconciliation_are_exact(self):
        amounts = {}
        for row in self.sources["order_items"].itertuples(index=False):
            price, freight = amounts.get(row.order_id, (0, 0))
            amounts[row.order_id] = (price+int(Decimal(str(row.price))*100), freight+int(Decimal(str(row.freight_value))*100))
        for order_id, (price, freight) in amounts.items():
            row = self.features.loc[order_id]
            self.assertEqual(row.item_price_sum_minor_units, price)
            self.assertEqual(row.freight_sum_minor_units, freight)
            self.assertEqual(row.payment_minus_items_and_freight_minor_units, row.payment_value_sum_minor_units-price-freight)
        self.assertEqual(self.summary["orders_with_nonzero_payment_item_difference"], 57)
        self.assertNotIn("is_anomaly", self.features)
        self.assertNotIn("is_fraud", self.features)

    def test_many_to_many_join_does_not_multiply_payments_or_items(self):
        ids = self.features.index[self.features.payment_record_count.gt(1) & self.features.item_count.gt(1)]
        self.assertGreater(len(ids), 0)
        order_id = ids[0]
        payments = self.sources["order_payments"].query("order_id == @order_id")
        items = self.sources["order_items"].query("order_id == @order_id")
        naive = payments.merge(items, on="order_id")
        self.assertEqual(len(naive), len(payments)*len(items))
        row = self.features.loc[order_id]
        self.assertAlmostEqual(naive.payment_value.sum(), row.payment_value_sum*len(items))
        self.assertAlmostEqual(naive.price.sum(), row.item_price_sum*len(payments))
        self.assertEqual(self.summary["orders_with_multiple_payment_records"], 279)

    def test_payment_fields_and_source_record_provenance_preserved(self):
        columns = self.sources["order_payments"].columns
        actual = self.frames["payment_source_evidence.csv"][columns]
        expected = self.sources["order_payments"].sort_values(["order_id", "payment_sequential"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, expected)
        self.assertTrue(actual.clean_source_record.is_unique)
        self.assertTrue(actual.clean_source_record.ge(1).all())
        for order_id, rows in actual.groupby("order_id"):
            feature = self.features.loc[order_id]
            self.assertEqual(feature.payment_record_count, len(rows))
            self.assertEqual(feature.payment_installments_max, rows.payment_installments.max())
            self.assertEqual(feature.payment_sequential_max, rows.payment_sequential.max())
            self.assertEqual(json.loads(feature.payment_types_json), sorted(rows.payment_type.unique()))
            self.assertEqual(feature.payment_type_count, rows.payment_type.nunique())

    def test_missing_child_records_are_not_zero_money(self):
        # Removing real child rows exercises a guard; this is not a study dataset.
        order_id = self.orders.order_id.iloc[0]
        sources = {**self.sources, "order_payments": self.sources["order_payments"].loc[lambda frame: frame.order_id.ne(order_id)]}
        frames, _ = business.build_features(self.orders, sources)
        row = frames["order_features.csv"].set_index("order_id").loc[order_id]
        self.assertEqual(row.payment_record_count, 0)
        self.assertFalse(row.has_payment_records)
        self.assertFalse(row.payment_values_complete)
        self.assertTrue(pd.isna(row.payment_value_sum))
        self.assertTrue(pd.isna(row.payment_minus_items_and_freight))
        self.assertTrue(pd.notna(row.items_plus_freight_sum))
        for source_name, rollup in [("order_payments", business.roll_up_payments), ("order_items", business.roll_up_items)]:
            aggregate, evidence = rollup(self.sources[source_name].iloc[:0])
            self.assertTrue(aggregate.empty)
            self.assertTrue(evidence.empty)

    def test_real_zero_freight_and_large_values_not_trimmed(self):
        items = self.sources["order_items"]
        actual = self.frames["item_source_evidence.csv"]
        self.assertEqual(actual.freight_value.eq(0).sum(), items.freight_value.eq(0).sum())
        self.assertEqual(actual.price.max(), items.price.max())
        self.assertEqual(self.features.item_price_max.max(), items.price.max())
        self.assertEqual(self.frames["payment_source_evidence.csv"].payment_value.max(), self.sources["order_payments"].payment_value.max())

    def test_missing_partial_sums_do_not_ignore_missing_observations(self):
        # Existing incomplete process observations exercise the generic helper
        # without inserting invented missing values into payment observations.
        observed = self.context.melt(id_vars="order_id", value_vars=business.audit.DURATIONS, value_name="duration")
        counts = observed.groupby("order_id").duration.count()
        self.assertTrue((counts.gt(0) & counts.lt(3)).any())
        actual = business.complete_group_sum(observed, "duration")
        for order_id, group in observed.groupby("order_id"):
            if group.duration.isna().any():
                self.assertTrue(pd.isna(actual.loc[order_id]))
            else:
                self.assertAlmostEqual(actual.loc[order_id], group.duration.sum())

    def test_subcent_precision_is_rejected_instead_of_rounded(self):
        # Real geographic observations have non-monetary precision and must not
        # pass the money converter. They never become payment observations.
        values = self.sources["geolocation"].geolocation_lat.dropna().head(20)
        with self.assertRaisesRegex(ValueError, "two-decimal"):
            business.minor_units(values)

    def test_customer_history_uses_unique_id_and_strictly_earlier_orders(self):
        self.assertEqual(self.summary["unique_customers_in_scope"], 7106)
        self.assertEqual(self.summary["orders_with_prior_scoped_purchases"], 200)
        for _, group in self.context.groupby("customer_unique_id"):
            for row in group.itertuples(index=False):
                prior = group.loc[group.order_purchase_timestamp.lt(row.order_purchase_timestamp)]
                feature = self.features.loc[row.order_id]
                self.assertEqual(feature.customer_prior_orders_in_scope, prior.order_id.nunique())
                if prior.empty:
                    self.assertTrue(pd.isna(feature.customer_days_since_prior_purchase))
                else:
                    expected = (row.order_purchase_timestamp-prior.order_purchase_timestamp.max()).total_seconds()/86400
                    self.assertAlmostEqual(feature.customer_days_since_prior_purchase, expected)
        self.assertTrue(self.context.customer_id.is_unique)

    def test_customer_history_unchanged_when_future_orders_removed(self):
        cutoff = self.context.order_purchase_timestamp.median()
        earlier = self.context.loc[self.context.order_purchase_timestamp.lt(cutoff)]
        actual = business.customer_history(earlier).set_index("order_id").sort_index()
        expected = self.features.loc[actual.index, actual.columns]
        pd.testing.assert_frame_equal(actual, expected)

    def test_seller_links_do_not_arbitrarily_assign_multi_seller_reviews(self):
        pairs = self.frames["seller_order_links.csv"]
        expected = self.sources["order_items"][["order_id", "seller_id"]].drop_duplicates().sort_values(["order_id", "seller_id"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(pairs[expected.columns], expected)
        self.assertEqual(self.summary["orders_with_multiple_sellers"], 149)
        self.assertTrue(pairs.review_is_order_level_not_seller_attributed.all())
        seller_counts = pairs.groupby("order_id").seller_id.size()
        np.testing.assert_array_equal(seller_counts, self.features.seller_count.reindex(seller_counts.index))
        support = self.frames["seller_source_support.csv"]
        self.assertFalse(any("rate" in column or "anomaly" in column for column in support))
        self.assertEqual(support.linked_orders.sum(), len(pairs))

    def test_reviews_are_observed_evidence_not_auto_labels_or_purchase_time_features(self):
        reviews = self.frames["review_source_evidence.csv"]
        valid = (reviews.review_score.between(1, 5) & reviews.review_creation_date.ge(reviews.order_purchase_timestamp)
                 & reviews.review_answer_timestamp.ge(reviews.review_creation_date)
                 & reviews.review_answer_timestamp.lt(reviews.event_time_cutoff_exclusive)).fillna(False)
        pd.testing.assert_series_equal(reviews.usable_answer_before_partition_cutoff, valid, check_names=False, check_dtype=False)
        self.assertEqual(len(reviews), 7351)
        self.assertEqual(valid.sum(), 6709)
        self.assertFalse(any("review_score" in column or "seller_late" in column for column in self.features))

    def test_geo_support_uses_observed_valid_coordinates_without_invented_distances(self):
        source = self.sources["geolocation"]
        valid = (source.geolocation_lat.between(-90, 90) & source.geolocation_lng.between(-180, 180)).fillna(False)
        expected = valid.groupby(source.geolocation_zip_code_prefix).sum()
        actual = self.frames["geolocation_zip_support.csv"].set_index("geolocation_zip_code_prefix")
        np.testing.assert_array_equal(actual.valid_coordinate_rows.reindex(expected.index), expected)
        self.assertEqual(actual.recorded_coordinate_rows.sum(), len(source))
        self.assertEqual(self.summary["orders_with_all_seller_zip_sources"], 7319)
        self.assertFalse(any("distance" in column for column in self.features))

    def test_all_six_groups_and_twenty_candidates_have_explicit_limits(self):
        coverage = self.frames["advisor_group_coverage.csv"]
        self.assertEqual(coverage.advisor_group.tolist(), list(range(1, 7)))
        self.assertTrue(coverage.support_denominator_orders.eq(7333).all())
        self.assertTrue(coverage.implementation_status.str.endswith("not_scored").all())
        dictionary = self.frames["feature_dictionary.csv"]
        self.assertEqual(dictionary.column.tolist(), self.frames["order_features.csv"].columns.tolist())
        self.assertFalse(dictionary.online_ready.any())
        self.assertEqual(dictionary.role.eq("candidate_feature").sum(), 20)
        indexed = dictionary.set_index("column")
        for column in ["order_id", "split", "marketplace_id", "timing_input_eligible"]:
            self.assertEqual(indexed.at[column, "role"], "audit_not_predictor")
        for key in ["training_performed", "thresholds_learned", "test_features_built", "test_scored", "human_labels_created",
                    "fraud_labels_created", "anomaly_labels_created", "accuracy_computed", "online_ready", "imputation_performed", "existing_models_changed"]:
            self.assertFalse(self.summary[key], key)

    def test_source_order_does_not_change_features(self):
        sources = {name: frame.iloc[::-1] for name, frame in self.sources.items()}
        actual, summary = business.build_features(self.orders.iloc[::-1], sources)
        self.assertEqual(summary["split_orders"], self.summary["split_orders"])
        for name in self.frames:
            pd.testing.assert_frame_equal(actual[name], self.frames[name])

    def test_duplicate_sources_missing_links_and_category_mismatch_fail(self):
        for name in ["orders", "order_payments", "order_items", "customers", "products", "sellers", "order_reviews"]:
            sources = {**self.sources, name: pd.concat([self.sources[name], self.sources[name].iloc[:1]])}
            with self.assertRaisesRegex(ValueError, "duplicate key"):
                business.validate_sources(self.orders, sources)
        sources = {**self.sources, "sellers": self.sources["sellers"].iloc[:0]}
        with self.assertRaisesRegex(ValueError, "Missing core relationship"):
            business.validate_sources(self.orders, sources)
        sources = {**self.sources, "order_items": self.sources["order_items"].iloc[:0]}
        with self.assertRaisesRegex(ValueError, "single-category cohort"):
            business.validate_sources(self.orders, sources)

    def test_identity_reservation_relabel_and_different_scope_rejected(self):
        reservations = pd.read_csv(Path(self.provenance["reservation_directory"])/"cohort_cases.csv", usecols=["order_id", "split"])
        business.require_scope(self.orders, reservations, business.IDENTITY)
        with self.assertRaisesRegex(ValueError, "identity"):
            business.require_scope(self.orders, reservations, {**business.IDENTITY, "marketplace_id": "different_platform"})
        with self.assertRaisesRegex(ValueError, "scope differs"):
            business.require_scope(self.orders.iloc[1:], reservations, business.IDENTITY)
        relabeled = self.orders.copy()
        relabeled.loc[0, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "scope differs"):
            business.require_scope(relabeled, reservations, business.IDENTITY)

    def test_saved_snapshot_all_tables_hashes_and_typed_roundtrip(self):
        pd.testing.assert_frame_equal(self.restored, self.frames["order_features.csv"], check_dtype=False, rtol=1e-12, atol=1e-12)
        self.assertEqual(set(self.manifest["schemas"]), business.TABLES)
        files = {path.name for path in self.output.iterdir()}
        self.assertEqual(set(self.manifest["output_hashes"]), files-{"manifest.json"})
        self.assertEqual(len(files), 14)
        for name, digest in self.manifest["output_hashes"].items():
            self.assertEqual(business.audit.file_hash(self.output/name), digest)
        for name, expected in self.frames.items():
            restored = business.usage.read_snapshot_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(restored, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
        self.assertEqual(self.manifest["provenance"], self.provenance)

    def test_refuses_overwrite_changed_source_or_modified_output(self):
        with self.assertRaises(FileExistsError):
            business.write_features(self.frames, self.summary, self.provenance, self.output)
        changed = {**self.provenance, "clean_manifest_sha256": "changed metadata"}
        with self.assertRaisesRegex(ValueError, "source manifest changed"):
            business.verify_sources(changed)
        broken = Path(self.temporary.name)/"broken"
        shutil.copytree(self.output, broken)
        (broken/"summary.json").write_text("{}\n")
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            business.load_feature_snapshot(broken)

    def test_failed_roundtrip_never_publishes_partial_directory(self):
        target = Path(self.temporary.name)/"not_published"
        with patch.object(business.usage, "read_snapshot_table", side_effect=ValueError("roundtrip failed")):
            with self.assertRaisesRegex(ValueError, "roundtrip failed"):
                business.write_features(self.frames, self.summary, self.provenance, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(".not_published-*")))


if __name__ == "__main__":
    unittest.main()
