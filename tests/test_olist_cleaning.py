"""Cleaning checks use imported observations only, never synthetic business records."""

import json
from pathlib import Path
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import clean_olist_data as clean


class OlistCleaningTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundle = clean.prepare_dataset()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name) / "snapshot"
        clean.write_snapshot(cls.bundle, cls.output)

    def test_only_identical_geolocation_records_are_removed(self):
        summary = self.bundle["summary"].set_index("table")
        other = summary.drop(index="geolocation")
        self.assertTrue(other.input_rows.eq(other.output_rows).all())
        self.assertEqual(summary.loc["geolocation", "removed_after_normalization"], len(self.bundle["duplicates"]))
        source = pd.read_csv(clean.audit.RAW_DATA_DIR / clean.audit.FILES["geolocation"], dtype="string", keep_default_na=False)
        normalized, _ = clean.normalize_table("geolocation", source)
        links = self.bundle["duplicates"]
        removed = normalized.loc[links.source_record.to_numpy() - 1].reset_index(drop=True)
        retained = normalized.loc[links.kept_source_record.to_numpy() - 1].reset_index(drop=True)
        pd.testing.assert_frame_equal(removed, retained)
        self.assertTrue(links.source_record.is_unique)
        self.assertTrue(links.kept_source_record.lt(links.source_record).all())
        self.assertFalse(self.bundle["tables"]["geolocation"].duplicated().any())

    def test_order_grain_and_actual_timestamps_are_preserved(self):
        orders = self.bundle["tables"]["orders"]
        raw = pd.read_csv(clean.audit.RAW_DATA_DIR / clean.audit.FILES["orders"], dtype="string")
        self.assertEqual(orders.order_id.tolist(), raw.order_id.tolist())
        self.assertEqual(len(self.bundle["cases"]), len(raw))
        self.assertTrue(self.bundle["cases"].order_id.is_unique)
        for column in clean.DATES["orders"]:
            pd.testing.assert_series_equal(orders[column], pd.to_datetime(raw[column]), check_dtype=False)
        cases = self.bundle["cases"].set_index("order_id")
        expected = pd.Series(range(1, len(raw) + 1), index=raw.order_id, name="source_record")
        pd.testing.assert_series_equal(cases.source_record.sort_index(), expected.sort_index(), check_dtype=False)

    def test_numeric_values_and_missing_values_are_not_filled_or_clipped(self):
        for name in set(clean.INTEGERS) | set(clean.FLOATS):
            raw = pd.read_csv(clean.audit.RAW_DATA_DIR / clean.audit.FILES[name])
            frame = self.bundle["tables"][name]
            for column in clean.INTEGERS.get(name, []) + clean.FLOATS.get(name, []):
                original = raw.loc[frame.index, column]
                np.testing.assert_allclose(frame[column].to_numpy(dtype=float, na_value=np.nan), original,
                                           equal_nan=True, rtol=1e-14, atol=1e-14)
        products = self.bundle["tables"]["products"]
        self.assertGreater(products.product_weight_g.eq(0).sum(), 0)
        self.assertGreater(products.product_category_name.isna().sum(), 0)

    def test_free_text_is_verbatim_and_zip_prefixes_are_strings(self):
        raw = pd.read_csv(clean.audit.RAW_DATA_DIR / clean.audit.FILES["order_reviews"], dtype="string", keep_default_na=False)
        for column in clean.FREE_TEXT:
            pd.testing.assert_series_equal(self.bundle["tables"]["order_reviews"][column].fillna(""), raw[column])
        for name in ["customers", "sellers", "geolocation"]:
            frame = self.bundle["tables"][name]
            column = next(c for c in frame if "zip_code_prefix" in c)
            self.assertTrue(frame[column].str.fullmatch(r"[0-9]{5}").all())

    def test_normalization_is_idempotent(self):
        for name, frame in self.bundle["tables"].items():
            normalized, _ = clean.normalize_table(name, frame)
            pd.testing.assert_frame_equal(normalized, frame)

    def test_event_log_contains_exactly_observed_milestones(self):
        actual = self.bundle["events"].sort_values(["case_id", "activity", "timestamp"]).reset_index(drop=True)
        existing = pd.read_csv(ROOT / "data/processed/event_log.csv", dtype={"case_id": "string", "activity": "string"})
        existing.timestamp = pd.to_datetime(existing.timestamp)
        existing = existing.sort_values(["case_id", "activity", "timestamp"]).reset_index(drop=True)
        pd.testing.assert_frame_equal(actual, existing[actual.columns], check_dtype=False)
        self.assertFalse(actual.duplicated(["case_id", "activity"]).any())
        self.assertEqual(len(actual), self.bundle["tables"]["orders"][clean.audit.TIMES].notna().sum().sum())

    def test_eligibility_is_not_a_normal_label_or_outlier_filter(self):
        cases = self.bundle["cases"]
        eligible = cases.completed_timing_eligible
        self.assertTrue(cases.loc[eligible, "order_status"].eq("delivered").all())
        self.assertTrue(cases.loc[eligible, "missing_milestone_count"].eq(0).all())
        self.assertFalse(cases.loc[eligible, "has_reversed_recorded_milestones"].any())
        self.assertTrue(cases.single_category_timing_eligible.eq(eligible & cases.category_assignment_eligible).all())
        self.assertGreater(cases.loc[eligible, "purchased_to_approved_days"].eq(0).sum(), 0)
        self.assertGreater(cases.loc[eligible, "total_cycle_time_days"].max(), 100)
        self.assertGreater(cases.negative_transition_count.gt(0).sum(), 0)
        self.assertTrue(cases.loc[cases.has_reversed_recorded_milestones, "temporal_review_required"].all())
        self.assertFalse(any("label" in column or "probability" in column for column in cases))

    def test_issue_keys_and_positions_link_to_real_records(self):
        issues = self.bundle["issues"]
        for name, subset in issues.groupby("table"):
            frame = self.bundle["tables"][name]
            for row in subset.itertuples(index=False):
                record = frame.loc[row.source_record - 1]
                for column, value in json.loads(row.record_key).items():
                    self.assertEqual(None if pd.isna(record[column]) else str(record[column]), value)
        self.assertIn("negative_transition_duration", set(issues.issue_code))
        self.assertIn("zero_physical_measurement", set(issues.issue_code))
        self.assertFalse(issues.loc[issues.field.eq("freight_value"), "observed_value"].isin(["0", "0.0"]).any())

    def test_snapshot_hashes_and_exported_types_are_reproducible(self):
        manifest = json.loads((self.output / "manifest.json").read_text())
        self.assertEqual(manifest["status"], "complete")
        self.assertTrue(manifest["all_orders_preserved"])
        self.assertEqual(manifest["source_event_count"], manifest["output_event_count"])
        for filename, expected in manifest["output_hashes"].items():
            self.assertEqual(clean.audit.file_hash(self.output / filename), expected)
        for filename, source in manifest["sources"].items():
            self.assertEqual(clean.audit.file_hash(clean.audit.RAW_DATA_DIR / filename), source["sha256"])
        for name in ["orders", "customers", "products", "order_reviews"]:
            exported = pd.read_csv(self.output / clean.audit.FILES[name], dtype="string", keep_default_na=False)
            loaded, _ = clean.normalize_table(name, exported)
            pd.testing.assert_frame_equal(loaded, self.bundle["tables"][name])

    def test_existing_snapshot_is_never_overwritten(self):
        before = clean.audit.file_hash(self.output / "manifest.json")
        with self.assertRaises(FileExistsError):
            clean.write_snapshot(self.bundle, self.output)
        self.assertEqual(before, clean.audit.file_hash(self.output / "manifest.json"))

    def test_missing_schema_and_repeated_real_keys_are_rejected(self):
        products = self.bundle["tables"]["products"].astype("string")
        with self.assertRaisesRegex(ValueError, "Unexpected schema"):
            clean.normalize_table("products", products.drop(columns="product_id"))
        tables = dict(self.bundle["tables"])
        tables["orders"] = pd.concat([tables["orders"], tables["orders"].iloc[:1]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate key in orders"):
            clean.audit.validate_core(tables)


if __name__ == "__main__":
    unittest.main()
