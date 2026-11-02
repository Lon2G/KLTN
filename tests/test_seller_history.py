"""Seller-history invariants checked against real saved Olist observations."""

from copy import deepcopy
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
sys.path.insert(0, str(ROOT/"scripts/01_data_preparation"))
import build_seller_history as seller


class SellerHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append((str(path), kwargs.get("usecols")))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), patch.object(joblib, "load", side_effect=AssertionError("No model loading")):
            cls.inputs, cls.provenance = seller.load_inputs()
            cls.frames, cls.summary = seller.build_history(cls.inputs)
        cls.pairs = cls.frames["seller_order_history.csv"]
        cls.orders = cls.frames["order_seller_history.csv"]
        cls.source = cls.frames["order_source_evidence.csv"]
        cls.reviews = cls.frames["review_source_evidence.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"seller_history"
        cls.saved = seller.write_history(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.manifest = seller.load_snapshot(cls.output)

    def test_real_populations_and_keys_retained(self):
        self.assertEqual(len(self.orders), 7333)
        self.assertEqual(len(self.pairs), 7491)
        self.assertEqual(self.pairs.seller_id.nunique(), 157)
        self.assertEqual(len(self.reviews), 7351)
        self.assertTrue(self.orders.order_id.is_unique)
        self.assertFalse(self.pairs.duplicated(["order_id", "seller_id"]).any())
        self.assertEqual(set(self.orders.order_id), set(self.inputs["context"].order_id))
        self.assertEqual(set(zip(self.pairs.order_id, self.pairs.seller_id)), set(zip(self.inputs["links"].order_id, self.inputs["links"].seller_id)))

    def test_no_test_features_human_labels_or_models_read(self):
        ledgers = [(path, cols) for path, cols in self.reads if Path(path).name == "cohort_cases.csv"]
        self.assertEqual(len(ledgers), 1)
        self.assertEqual(ledgers[0][1], ["order_id", "split"])
        self.assertFalse(any("manual_review" in path or "test_timing" in path or "models/" in path for path, _ in self.reads))
        reserved = set(self.inputs["reservations"].loc[lambda x: x.split.eq("test"), "order_id"])
        self.assertEqual(self.saved["reserved_test_orders_excluded"], 1691)
        for name, frame in self.frames.items():
            if "order_id" in frame:
                self.assertFalse(reserved.intersection(frame.order_id), name)

    def test_observed_source_values_preserved_exactly(self):
        columns = ["order_id", "split", "clean_order_record", *seller.TIMES, "order_estimated_delivery_date"]
        expected = self.inputs["context"][columns].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.source[columns], expected, check_exact=True)
        columns = ["clean_source_record", "order_id", "review_id", "review_score", "review_creation_date", "review_answer_timestamp"]
        expected = self.inputs["reviews"][columns].sort_values("clean_source_record").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.reviews[columns], expected, check_exact=True)

    def test_purchase_counts_windows_and_recency_by_independent_search(self):
        for _, group in self.pairs.groupby("seller_id"):
            times = np.sort(group.as_of_timestamp.to_numpy(dtype="datetime64[us]"))
            queries = group.as_of_timestamp.to_numpy(dtype="datetime64[us]")
            before = np.searchsorted(times, queries, side="left")
            window_start = np.searchsorted(times, queries-np.timedelta64(30, "D"), side="left")
            np.testing.assert_array_equal(group.prior_orders, before)
            np.testing.assert_array_equal(group.prior_orders_30d, before-window_start)
            present = before > 0
            gaps = (queries[present]-times[before[present]-1])/np.timedelta64(1, "D")
            np.testing.assert_allclose(group.loc[present, "days_since_last_prior_purchase"], gaps, rtol=1e-12, atol=1e-12)
            self.assertTrue(group.loc[~present, "days_since_last_prior_purchase"].isna().all())

    def test_equal_time_purchases_never_enter_each_others_history(self):
        ties = self.pairs.loc[self.pairs.duplicated(["seller_id", "as_of_timestamp"], keep=False)]
        self.assertEqual(len(ties), 20)
        for _, group in ties.groupby(["seller_id", "as_of_timestamp"]):
            for name in [*seller.COUNT_COLUMNS, *seller.FLOAT_COLUMNS, *seller.TIME_COLUMNS]:
                self.assertEqual(group[name].nunique(dropna=False), 1, name)

    def test_every_contributing_event_is_strictly_earlier(self):
        for column in seller.TIME_COLUMNS:
            present = self.pairs[column].notna()
            self.assertTrue(self.pairs.loc[present, column].lt(self.pairs.loc[present, "as_of_timestamp"]).all())
        query_times = self.inputs["context"].set_index("order_id").order_purchase_timestamp
        np.testing.assert_array_equal(self.pairs.as_of_timestamp, self.pairs.order_id.map(query_times))

    def test_delivery_denominators_independently_recomputed(self):
        joined = self.inputs["links"][["order_id", "seller_id"]].merge(self.source, on="order_id", validate="many_to_one")
        for sid, history in joined.groupby("seller_id"):
            query = self.pairs.loc[self.pairs.seller_id.eq(sid)]
            purchased = history.order_purchase_timestamp.to_numpy(dtype="datetime64[us]")
            delivered = history.order_delivered_customer_date.to_numpy(dtype="datetime64[us]")
            one_seller = history.seller_count.eq(1).to_numpy()
            complete = history.recorded_completion_chronology_valid.to_numpy()
            promise = history.recorded_promise_comparable.to_numpy()
            late = history.calendar_delay_days.gt(0).to_numpy()
            for row in query.itertuples(index=False):
                before = purchased < np.datetime64(row.as_of_timestamp)
                eligible = before & one_seller & complete & (delivered < np.datetime64(row.as_of_timestamp))
                denominator = int((eligible & promise).sum())
                numerator = int((eligible & promise & late).sum())
                self.assertEqual(row.prior_single_seller_orders, (before & one_seller).sum())
                self.assertEqual(row.prior_multi_seller_orders, (before & ~one_seller).sum())
                self.assertEqual(row.prior_observed_completed_orders, eligible.sum())
                self.assertEqual(row.prior_promise_comparable_orders, denominator)
                self.assertEqual(row.prior_after_promise_orders, numerator)
                if denominator:
                    self.assertEqual(row.prior_after_promise_fraction, numerator/denominator)
                else:
                    self.assertTrue(np.isnan(row.prior_after_promise_fraction))

    def test_same_calendar_date_is_not_midnight_lateness(self):
        source = self.source
        same_date = source.recorded_completion_chronology_valid & source.order_delivered_customer_date.dt.normalize().eq(source.order_estimated_delivery_date.dt.normalize())
        later_clock = source.order_delivered_customer_date.gt(source.order_estimated_delivery_date)
        examples = source.loc[same_date & later_clock]
        self.assertGreater(len(examples), 0)
        self.assertTrue(examples.calendar_delay_days.eq(0).all())
        usable = source.loc[source.recorded_completion_chronology_valid & source.recorded_promise_comparable]
        expected = (usable.order_delivered_customer_date.dt.normalize()-usable.order_estimated_delivery_date.dt.normalize()).dt.days
        np.testing.assert_array_equal(usable.calendar_delay_days, expected)

    def test_no_final_status_or_own_outcome_predictors(self):
        prohibited = {"order_status", "review_score", "order_delivered_customer_date", "calendar_delay_days", "is_anomaly", "is_fraud"}
        self.assertFalse(prohibited.intersection(self.pairs.columns))
        self.assertFalse(prohibited.intersection(self.orders.columns))
        without_status = {**self.inputs, "context": self.inputs["context"].drop(columns="order_status")}
        orders, reviews = seller.build_source_evidence(without_status)
        pd.testing.assert_frame_equal(orders, self.source, check_exact=True)
        pd.testing.assert_frame_equal(reviews, self.reviews, check_exact=True)

    def test_review_counts_are_order_level_and_cover_all_scores(self):
        self.assertEqual(self.inputs["reviews"].order_id.duplicated().sum(), 108)
        columns = [f"prior_review_score_{score}_orders" for score in range(1, 6)]
        np.testing.assert_array_equal(self.pairs[columns].sum(axis=1), self.pairs.prior_reviewed_orders)
        known = self.pairs.prior_reviewed_orders.gt(0)
        expected_mean = sum(self.pairs[f"prior_review_score_{score}_orders"]*score for score in range(1, 6))/self.pairs.prior_reviewed_orders
        np.testing.assert_array_equal(self.pairs.loc[known, "prior_mean_review_score"], expected_mean.loc[known])
        for score in range(1, 6):
            expected = self.pairs.loc[known, f"prior_review_score_{score}_orders"]/self.pairs.loc[known, "prior_reviewed_orders"]
            np.testing.assert_array_equal(self.pairs.loc[known, f"prior_review_score_{score}_fraction"], expected)

    def test_latest_review_selected_only_from_visible_answers(self):
        duplicated = self.reviews.loc[self.reviews.recorded_review_valid & self.reviews.order_id.duplicated(keep=False)]
        checked = 0
        for _, group in duplicated.groupby("order_id"):
            if len(group) != 2 or group.review_answer_timestamp.nunique() != 2:
                continue
            ordered = group.sort_values("review_answer_timestamp")
            # The actual second answer timestamp is the strict boundary: the first remains visible.
            selected, ambiguous = seller.latest_visible_reviews(group, ordered.iloc[1].review_answer_timestamp)
            self.assertEqual(len(selected), 1)
            self.assertEqual(selected.iloc[0].clean_source_record, ordered.iloc[0].clean_source_record)
            self.assertEqual(ambiguous, 0)
            checked += 1
        self.assertGreater(checked, 0)

    def test_review_chronology_gate_recomputed_from_observed_values(self):
        r = self.reviews
        expected = (r.review_score.between(1, 5) & r.review_creation_date.ge(r.order_purchase_timestamp)
                    & r.review_answer_timestamp.ge(r.review_creation_date)).fillna(False)
        np.testing.assert_array_equal(r.recorded_review_valid, expected)
        last = self.pairs.as_of_timestamp.max()
        selected, _ = seller.latest_visible_reviews(r, last)
        self.assertTrue(selected.recorded_review_valid.all())
        self.assertTrue(selected.review_answer_timestamp.lt(last).all())
        self.assertTrue(selected.order_id.is_unique)

    def test_representative_review_histories_by_independent_record_selection(self):
        links = self.inputs["links"]
        sampled = self.pairs.groupby("seller_id", sort=True).tail(1)
        for row in sampled.itertuples(index=False):
            ids = set(links.loc[links.seller_id.eq(row.seller_id) & links.order_seller_count.eq(1), "order_id"])
            eligible = self.reviews.loc[self.reviews.order_id.isin(ids)]
            answers = {}
            for record in eligible.itertuples(index=False):
                if (pd.isna(record.review_score) or not 1 <= record.review_score <= 5
                        or not record.order_purchase_timestamp <= record.review_creation_date <= record.review_answer_timestamp < row.as_of_timestamp):
                    continue
                answers.setdefault(record.order_id, []).append(record)
            values, ambiguous = [], 0
            for records in answers.values():
                latest_time = max(record.review_answer_timestamp for record in records)
                scores = {record.review_score for record in records if record.review_answer_timestamp == latest_time}
                if len(scores) == 1:
                    values.append(scores.pop())
                else:
                    ambiguous += 1
            self.assertEqual(row.prior_reviewed_orders, len(values))
            self.assertEqual(row.prior_ambiguous_latest_review_orders, ambiguous)
            if values:
                self.assertEqual(row.prior_mean_review_score, sum(values)/len(values))

    def test_future_purchases_and_answers_cannot_change_earlier_queries(self):
        largest = self.pairs.seller_id.value_counts().head(5).index
        for sid in largest:
            history = self.pairs.loc[self.pairs.seller_id.eq(sid)].sort_values("as_of_timestamp")
            row = history.iloc[len(history)//2]
            query = self.inputs["links"].loc[lambda x: x.order_id.eq(row.order_id) & x.seller_id.eq(sid),
                                            ["order_id", "seller_id", "split", "order_purchase_timestamp"]]
            old_orders = self.source.loc[self.source.order_purchase_timestamp.lt(row.as_of_timestamp)]
            old_reviews = self.reviews.loc[self.reviews.review_answer_timestamp.lt(row.as_of_timestamp)]
            actual = seller.seller_histories(query, self.inputs["links"], old_orders, old_reviews)
            expected = self.pairs.loc[self.pairs.order_id.eq(row.order_id) & self.pairs.seller_id.eq(sid)].reset_index(drop=True)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)

    def test_missing_histories_are_not_zero_rates_or_completed_orders(self):
        no_history = self.pairs.loc[self.pairs.prior_orders.eq(0)]
        self.assertGreater(len(no_history), 0)
        self.assertTrue(no_history[seller.COUNT_COLUMNS].eq(0).all().all())
        self.assertTrue(no_history[seller.FLOAT_COLUMNS+seller.TIME_COLUMNS].isna().all().all())
        unavailable = self.pairs.prior_promise_comparable_orders.eq(0)
        self.assertTrue(self.pairs.loc[unavailable, "prior_after_promise_fraction"].isna().all())
        unavailable = self.pairs.prior_reviewed_orders.eq(0)
        fields = ["prior_mean_review_score", *[f"prior_review_score_{n}_fraction" for n in range(1, 6)]]
        self.assertTrue(self.pairs.loc[unavailable, fields].isna().all().all())

    def test_current_multi_seller_orders_do_not_receive_arbitrary_rates(self):
        multi = self.orders.loc[self.orders.seller_count.gt(1)]
        self.assertEqual(len(multi), 149)
        fields = [column for column in self.orders if column.startswith("single_seller_")]
        self.assertTrue(multi[fields].isna().all().all())
        self.assertTrue(multi.history_route.eq("multi_seller_use_pair_context").all())
        self.assertTrue(multi.min_seller_prior_orders.le(multi.max_seller_prior_orders).all())

    def test_counts_reconcile_without_double_counting_missing_history(self):
        frame = self.pairs
        np.testing.assert_array_equal(frame.prior_orders, frame.prior_single_seller_orders+frame.prior_multi_seller_orders)
        np.testing.assert_array_equal(frame.prior_single_seller_orders, frame.prior_observed_completed_orders+frame.prior_completion_history_unavailable_orders)
        np.testing.assert_array_equal(frame.prior_single_seller_orders, frame.prior_reviewed_orders+frame.prior_review_history_unavailable_orders)
        self.assertTrue(frame.prior_promise_comparable_orders.le(frame.prior_observed_completed_orders).all())
        self.assertTrue(frame.prior_after_promise_orders.le(frame.prior_promise_comparable_orders).all())
        self.assertTrue(frame.prior_ambiguous_latest_review_orders.le(frame.prior_review_history_unavailable_orders).all())

    def test_coverage_and_support_bins_reconcile(self):
        coverage = self.frames["coverage_by_month.csv"]
        self.assertEqual(coverage.orders.sum(), 7333)
        self.assertEqual(coverage.multi_seller_orders.sum(), 149)
        self.assertEqual(coverage.groupby("split").orders.sum().to_dict(), {"train": 5480, "validation": 1853})
        for (split, _), group in self.frames["history_support_summary.csv"].groupby(["split", "history_measure"]):
            self.assertEqual(group.order_seller_pairs.sum(), self.pairs.split.eq(split).sum())

    def test_no_cancellation_anomaly_probability_or_training_claims(self):
        for name in ["cancellation_rate_computed", "low_review_cutoff_selected", "synthetic_data_used", "human_labels_created",
                     "anomaly_labels_created", "seller_fault_established", "training_performed", "existing_models_changed",
                     "thresholds_changed", "test_features_built", "test_scored", "accuracy_computed", "real_time_validated"]:
            self.assertFalse(self.summary[name], name)
        self.assertFalse(seller.protocol()["cancellation_rate_available"])
        self.assertFalse(any("cancel" in column or "anomaly" in column or "probability" in column for column in self.pairs))

    def test_duplicate_links_and_scope_mismatch_rejected(self):
        changed = {**self.inputs, "links": pd.concat([self.inputs["links"], self.inputs["links"].iloc[:1]], ignore_index=True)}
        with self.assertRaisesRegex(ValueError, "duplicate"):
            seller.validate_inputs(changed)
        changed = {**self.inputs, "context": self.inputs["context"].iloc[1:]}
        with self.assertRaisesRegex(ValueError, "scope differs"):
            seller.validate_inputs(changed)

    def test_exact_saved_roundtrip_and_source_bindings(self):
        for name, expected in self.frames.items():
            actual = seller.shared.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        seller.verify_sources(self.provenance)
        self.assertEqual(len(list(self.output.iterdir())), 10)

    def test_overwrite_and_tampering_rejected(self):
        with self.assertRaises(FileExistsError):
            seller.write_history(self.frames, self.summary, self.provenance, self.output)
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"copy"
            shutil.copytree(self.output, target)
            (target/"README.md").write_text("Temporary integrity-test modification\n")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                seller.load_snapshot(target)

    def test_failed_verification_leaves_no_partial_output(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)/"unpublished"
            with patch.object(seller, "verify_sources", side_effect=[None, ValueError("Source changed")]):
                with self.assertRaisesRegex(ValueError, "Source changed"):
                    seller.write_history(self.frames, self.summary, self.provenance, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temp).iterdir()), [])

    def test_changed_code_binding_rejected(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code or protocol changed"):
            seller.verify_sources(changed)


if __name__ == "__main__":
    unittest.main()
