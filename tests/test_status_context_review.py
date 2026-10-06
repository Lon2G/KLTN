"""Audit and sampling checks using imported cases only; no human labels are generated."""

import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))

import order_status_context_audit as audit
import prepare_status_context_review as prepare
import status_context_manual_review as review


class StatusContextReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = audit.load_context()
        cls.orders = pd.read_csv(audit.ORDERS_FILE, dtype={"order_id": "string"})
        cls.reviewed_ids, cls.sources = prepare.collect_reviewed_ids(
            list((ROOT / "data/manual_review").rglob("*.csv"))
        )
        cls.sample, cls.strata, cls.key = prepare.select_sample(cls.context, cls.reviewed_ids, 120, 42)
        cls.blind = audit.build_blind_cases(cls.sample)

    def write_batch(self, path):
        prepare.write_batch(path, self.sample, self.strata, self.key, self.reviewed_ids, self.sources, 42)

    def test_status_and_timestamps_are_exact_raw_values(self):
        source = self.orders.rename(columns={"order_id": "case_id"}).set_index("case_id")
        actual = self.context.set_index("case_id")
        columns = ["order_status", *audit.TIMESTAMP_COLUMNS]
        pd.testing.assert_frame_equal(actual[columns].sort_index(), source[columns].sort_index())
        self.assertTrue(actual.raw_missing_event_count.eq(source[list(audit.ACTIVITY_COLUMNS)].isna().sum(axis=1)).all())
        self.assertTrue(actual.recorded_event_count.add(actual.raw_missing_event_count).eq(4).all())

    def test_status_totals_and_predictions_reconcile(self):
        summary = audit.build_status_summary(self.context)
        self.assertEqual(summary.case_count.sum(), len(self.context))
        self.assertEqual(summary.missing_milestone_cases.sum(), self.context.missing_event_count.gt(0).sum())
        self.assertEqual(summary.auto_anomaly_cases.sum(), self.context.auto_validation_label.eq("Anomaly").sum())
        np.testing.assert_array_equal(
            summary[["auto_anomaly_cases", "auto_suspicious_cases", "auto_normal_cases"]].sum(axis=1),
            summary.case_count,
        )

    def test_missing_delivery_stays_missing_and_calendar_days_use_dates(self):
        frame = self.context
        missing = frame.order_delivered_customer_date.isna()
        self.assertTrue(frame.loc[missing, "delivery_vs_estimate_calendar_days"].isna().all())
        actual = pd.to_datetime(frame.order_delivered_customer_date).dt.normalize()
        estimate = pd.to_datetime(frame.order_estimated_delivery_date).dt.normalize()
        np.testing.assert_allclose(frame.delivery_vs_estimate_calendar_days, (actual - estimate).dt.days, equal_nan=True)

    def test_sampling_excludes_reviewed_cases_and_has_no_duplicates(self):
        self.assertEqual(len(self.sample), 120)
        self.assertTrue(self.sample.case_id.is_unique)
        self.assertTrue(self.sample.review_id.is_unique)
        self.assertFalse(self.sample.case_id.isin(self.reviewed_ids).any())
        self.assertTrue(self.sample.case_id.isin(self.context.case_id).all())

    def test_sampling_reproducibility_and_probabilities(self):
        sample, strata, key = prepare.select_sample(self.context.iloc[::-1], self.reviewed_ids, 120, 42)
        pd.testing.assert_frame_equal(sample, self.sample)
        pd.testing.assert_frame_equal(strata, self.strata)
        self.assertTrue(strata.loc[strata.eligible_cases.gt(0), "sample_cases"].gt(0).all())
        np.testing.assert_allclose(strata.inclusion_probability.dropna(),
                                   (strata.sample_cases / strata.eligible_cases).dropna())
        self.assertAlmostEqual(key.sampling_weight.sum(), strata.eligible_cases.sum())
        observed = key.groupby(prepare.STRATA_COLUMNS).size()
        expected = strata.set_index(prepare.STRATA_COLUMNS).sample_cases
        self.assertTrue(observed.eq(expected.loc[observed.index]).all())

    def test_sample_size_must_cover_all_eligible_strata(self):
        with self.assertRaisesRegex(ValueError, "cover every eligible stratum"):
            prepare.select_sample(self.context, self.reviewed_ids, 1, 42)

    def test_blind_allowlist_labels_and_actual_event_sequences(self):
        self.assertEqual(list(self.blind.columns), audit.BLIND_COLUMNS)
        self.assertTrue(self.blind[audit.REVIEW_COLUMNS].eq("").all().all())
        excluded = {"comparison_group", "anomaly_flag", "trace_fitness", "sampling_weight", "auto_validation_label"}
        self.assertFalse(excluded & set(self.blind.columns))
        log = pd.read_csv(ROOT / "data/processed/event_log.csv")
        log = log.loc[log.case_id.isin(self.blind.case_id)].copy()
        log.timestamp = pd.to_datetime(log.timestamp)
        for row in self.blind.itertuples(index=False):
            expected_events = set(zip(log.loc[log.case_id.eq(row.case_id), "activity"],
                                      log.loc[log.case_id.eq(row.case_id), "timestamp"]))
            actual_events = {(activity, pd.Timestamp(getattr(row, column)))
                             for column, activity in audit.ACTIVITY_COLUMNS.items()
                             if pd.notna(getattr(row, column))}
            self.assertEqual(expected_events, actual_events)
            self.assertEqual(len(actual_events), row.recorded_event_count)

    def test_existing_batch_is_not_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary) / "batch"
            self.write_batch(batch_dir)
            before = audit.file_sha256(batch_dir / "review_cases_blind.csv")
            with self.assertRaises(FileExistsError):
                self.write_batch(batch_dir)
            self.assertEqual(before, audit.file_sha256(batch_dir / "review_cases_blind.csv"))

    def test_review_can_quit_and_resume_without_creating_labels(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary) / "batch"
            self.write_batch(batch_dir)
            with patch("builtins.input", return_value="q"), contextlib.redirect_stdout(io.StringIO()) as output:
                review.run_review(batch_dir)
            progress = review.load_review(batch_dir)
            self.assertTrue(progress[audit.REVIEW_COLUMNS + ["reviewed_at_utc"]].eq("").all().all())
            self.assertNotIn("auto_validation_label", output.getvalue())
            self.assertNotIn("anomaly_vote_count", output.getvalue())
            self.assertFalse((batch_dir / "review_progress.csv.tmp").exists())

    def test_changed_blind_source_or_progress_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            batch_dir = Path(temporary) / "batch"
            self.write_batch(batch_dir)
            progress = review.load_review(batch_dir)
            review.save_progress(progress.iloc[::-1], batch_dir)
            with self.assertRaisesRegex(ValueError, "Progress changed"):
                review.load_review(batch_dir)
            self.blind.iloc[::-1].to_csv(batch_dir / "review_cases_blind.csv", index=False)
            with self.assertRaisesRegex(ValueError, "Blind input changed"):
                review.load_review(batch_dir)


if __name__ == "__main__":
    unittest.main()
