"""Import contracts tested with actual records and serialization/configuration errors only."""

from copy import deepcopy
import csv
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import export_olist_import_source as export
import import_process_data as importer


class ProcessImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.orders, cls.lineage, cls.config, cls.provenance = export.prepare_source()
        cls.source = pd.read_csv(io.StringIO(cls.orders.to_csv(index=False, date_format="%Y-%m-%d %H:%M:%S")),
                                 dtype="string", keep_default_na=False)
        cls.frames, cls.summary = importer.build_import(cls.source, cls.config)
        manifest = export.verify_source(export.bed.DEFAULT_OUTPUT)
        cls.cohort = export.bed.usage.read_snapshot_table(export.bed.DEFAULT_OUTPUT, "cohort_cases.csv", manifest)
        cls.expected = cls.cohort.loc[cls.cohort.split.isin(["train", "validation"])].set_index("order_id")

    def test_real_export_has_lineage_and_excludes_reserved_test(self):
        self.assertEqual(len(self.orders), 7333)
        self.assertEqual(self.provenance["reserved_test_records_excluded"], 1691)
        self.assertEqual(self.orders.order_id.tolist(), self.lineage.order_id.tolist())
        self.assertEqual(self.lineage.import_source_record.tolist(), list(range(1, 7334)))
        traced = self.cohort.iloc[self.lineage.upstream_cohort_record.to_numpy()-1]
        self.assertEqual(traced.order_id.tolist(), self.orders.order_id.tolist())
        self.assertEqual(traced.source_record.tolist(), self.lineage.original_raw_source_record.tolist())
        self.assertTrue(set(self.orders.order_id).isdisjoint(self.cohort.loc[self.cohort.split.eq("test"), "order_id"]))

    def test_templates_contain_no_fabricated_orders_or_filled_metadata(self):
        template = pd.read_csv(ROOT / "templates/process_import_v1/orders.csv", dtype="string")
        self.assertTrue(template.empty)
        config = json.loads(export.TEMPLATE.read_text())
        for name in ["dataset_id", "marketplace_id", "primary_category"]:
            self.assertIsNone(config[name])
        with self.assertRaisesRegex(ValueError, "dataset_id"):
            importer.validate_config(config)

    def test_roundtrip_preserves_every_order_timestamp_duration_and_cutoff(self):
        actual = self.frames["orders.csv"].set_index("order_id").loc[self.expected.index]
        self.assertEqual(len(actual), len(self.expected))
        for column in ["order_status", *export.bed.audit.TIMES, "order_estimated_delivery_date", "split", "event_time_cutoff_exclusive",
                       *export.bed.usage.DURATIONS, "usage_group", "completed_timing_eligible", "timing_input_eligible", "timing_input_reason"]:
            pd.testing.assert_series_equal(actual[column], self.expected[column], check_dtype=False)
        self.assertEqual(self.summary["quarantined_records"], 0)
        self.assertEqual(self.summary["timing_input_eligible"], 6601)
        self.assertEqual(self.summary["normalization_changes"], 0)
        self.assertTrue(self.summary["ready_for_pipeline"])
        self.assertTrue(actual.index.str.startswith("0").any())
        self.assertTrue(actual.source_record.is_unique)

    def test_actual_events_are_not_reconstructed_and_future_events_stay_visible(self):
        actual = self.frames["event_log.csv"]
        expected = export.bed.usage.clean.build_event_log(self.expected.reset_index())
        pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False)
        self.assertEqual(len(actual), 29188)
        self.assertGreater(int((~actual.timestamp_before_partition_cutoff).sum()), 0)
        self.assertEqual(len(actual), int(self.orders[export.bed.audit.TIMES].notna().sum().sum()))

    def test_quality_evidence_is_preserved_not_quarantined_or_labeled(self):
        orders = self.frames["orders.csv"]
        issues = self.frames["issues.csv"]
        self.assertEqual(int(orders.has_reversed_recorded_milestones.sum()), 59)
        self.assertGreater(int(orders.purchased_to_approved_days.eq(0).sum()), 0)
        self.assertFalse(orders.loc[orders.has_reversed_recorded_milestones, "completed_timing_eligible"].any())
        self.assertEqual(issues.loc[issues.issue_code.eq("incomplete_status_context"), "source_record"].nunique(), 118)
        self.assertEqual(issues.loc[issues.issue_code.eq("completion_crosses_cutoff"), "source_record"].nunique(), 555)
        self.assertFalse(issues.severity.eq("error").any())
        self.assertFalse(orders.ongoing_age_computable.any())
        for forbidden in ["reviewer_label", "anomaly_label", "anomaly_probability"]:
            self.assertNotIn(forbidden, orders.columns)

    def test_duplicate_actual_record_quarantines_every_member(self):
        repeated = pd.concat([self.source.iloc[:3], self.source.iloc[:1]], ignore_index=True)
        frames, summary = importer.build_import(repeated, self.config)
        self.assertEqual(summary["quarantined_records"], 2)
        self.assertEqual(summary["accepted_records"], 2)
        self.assertFalse(summary["ready_for_pipeline"])
        self.assertEqual(frames["duplicate_groups.csv"].kind.iloc[0], "duplicate_id_identical_rows")
        self.assertNotIn(self.source.order_id.iloc[0], frames["orders.csv"].order_id.tolist())
        for row in frames["quarantined_rows.csv"].itertuples(index=False):
            self.assertEqual(json.loads(row.raw_row_json), repeated.iloc[row.source_record-1].to_dict())

    def test_actual_duplicate_candidate_payloads_are_distinguished(self):
        # Historical candidate rows genuinely repeat IDs across different contamination settings.
        reviewed = pd.read_csv(ROOT / "data/manual_review/final_candidate_manual_validation_labeled.csv", dtype="string", keep_default_na=False)
        reviewed = reviewed.loc[reviewed.case_id.isin(self.source.order_id)]
        repeated_id = reviewed.loc[reviewed.case_id.duplicated(keep=False), "case_id"].iloc[0]
        conditions = reviewed.loc[reviewed.case_id.eq(repeated_id), ["case_id", "contamination"]]
        real_rows = self.source.loc[self.source.order_id.eq(repeated_id)].merge(conditions, left_on="order_id", right_on="case_id", validate="one_to_many")
        frames, summary = importer.build_import(real_rows, self.config)
        self.assertEqual(summary["accepted_records"], 0)
        self.assertEqual(summary["quarantined_records"], len(real_rows))
        self.assertEqual(frames["duplicate_groups.csv"].kind.iloc[0], "duplicate_id_conflicting_rows")
        self.assertIn("contamination", json.loads(frames["duplicate_groups.csv"].conflicting_columns_json.iloc[0]))
        self.assertTrue(frames["event_log.csv"].empty)

    def test_whitespace_normalization_is_logged_and_collisions_never_merged(self):
        # Serialization whitespace only; these are copies of the same actual source order.
        rows = pd.concat([self.source.iloc[:1], self.source.iloc[:1]], ignore_index=True)
        rows.loc[1, "order_id"] = " " + rows.loc[1, "order_id"] + " "
        frames, summary = importer.build_import(rows, self.config)
        self.assertEqual(summary["quarantined_records"], 2)
        self.assertTrue(frames["issues.csv"].issue_code.eq("normalized_id_collision").any())
        self.assertEqual(len(frames["normalization_changes.csv"]), 1)

    def test_unknown_status_mapping_is_a_blocking_error_not_a_guess(self):
        config = deepcopy(self.config)
        actual_status = self.source.order_status.iloc[0]
        del config["status_mapping"][actual_status]
        frames, summary = importer.build_import(self.source.iloc[:1], config)
        self.assertFalse(summary["ready_for_pipeline"])
        self.assertEqual(summary["quarantined_records"], 1)
        self.assertTrue(frames["issues.csv"].issue_code.eq("unmapped_status").any())

    def test_wrong_declared_date_format_does_not_silently_infer_dates(self):
        config = deepcopy(self.config)
        config["timestamp_format"] = "%d/%m/%Y %H:%M:%S"
        frames, summary = importer.build_import(self.source.iloc[:2], config)
        self.assertEqual(summary["quarantined_records"], 2)
        self.assertTrue(frames["issues.csv"].issue_code.eq("invalid_timestamp").any())
        self.assertTrue(frames["orders.csv"].empty)

    def test_alternative_serialization_keeps_the_same_real_instants(self):
        data = self.source.iloc[:20].copy()
        config = deepcopy(self.config)
        config["timestamp_format"] = "%d/%m/%Y %H:%M:%S"
        config["estimated_delivery_format"] = "%Y-%m-%d"
        for column in [*export.bed.audit.TIMES, "event_time_cutoff_exclusive"]:
            data[column] = pd.to_datetime(data[column].replace("", pd.NA), format="%Y-%m-%d %H:%M:%S").dt.strftime(config["timestamp_format"]).fillna("").astype("string")
        data["order_estimated_delivery_date"] = self.orders.order_estimated_delivery_date.iloc[:20].dt.strftime("%Y-%m-%d").astype("string")
        frames, summary = importer.build_import(data, config)
        self.assertTrue(summary["ready_for_pipeline"])
        actual = frames["orders.csv"].set_index("order_id")
        for field in [*export.bed.audit.TIMES, "order_estimated_delivery_date", "event_time_cutoff_exclusive"]:
            pd.testing.assert_series_equal(actual[field], self.expected.loc[actual.index, field], check_dtype=False)

    def test_missing_actual_later_milestone_is_accepted_but_missing_identity_is_not(self):
        incomplete = self.source.loc[self.source.order_delivered_carrier_date.eq("")].iloc[:1]
        self.assertFalse(incomplete.empty)
        frames, summary = importer.build_import(incomplete, self.config)
        self.assertTrue(summary["ready_for_pipeline"])
        self.assertTrue(frames["orders.csv"].order_delivered_carrier_date.isna().all())
        config = deepcopy(self.config)
        # Deliberately wrong column mapping tests an absent key using real blank source cells.
        config["columns"]["order_id"], config["columns"]["order_delivered_carrier_date"] = "order_delivered_carrier_date", "order_id"
        frames, summary = importer.build_import(incomplete, config)
        self.assertFalse(summary["ready_for_pipeline"])
        self.assertTrue(frames["issues.csv"].query("field == 'order_id'").issue_code.eq("missing_required_value").any())

    def test_no_split_is_invented_and_unmapped_columns_are_not_predictors(self):
        config = deepcopy(self.config)
        config["columns"]["split"] = None
        config["columns"]["event_time_cutoff_exclusive"] = None
        config["dataset_scope"] = "unassigned"
        frames, summary = importer.build_import(self.source, config)
        self.assertTrue(summary["ready_for_pipeline"])
        self.assertTrue(frames["orders.csv"].split.isna().all())
        self.assertFalse(frames["orders.csv"].timing_input_eligible.any())
        self.assertTrue(frames["orders.csv"].timing_input_reason.eq("chronological_split_required").all())

    def test_absent_selected_category_blocks_scope_without_relabeling_orders(self):
        config = deepcopy(self.config)
        # An actual other Olist category from the real products table, not an invented label.
        products = pd.read_csv(ROOT / "data/raw/olist_products_dataset.csv", usecols=["product_category_name"], dtype="string")
        config["primary_category"] = products.loc[products.product_category_name.notna() & products.product_category_name.ne(config["primary_category"]), "product_category_name"].iloc[0]
        frames, summary = importer.build_import(self.source.iloc[:2], config)
        self.assertFalse(summary["ready_for_pipeline"])
        self.assertEqual(summary["scope_errors"], ["declared_primary_category_absent"])
        self.assertEqual(summary["accepted_records"], 2)
        self.assertTrue(frames["orders.csv"].product_category_name.eq(self.config["primary_category"]).all())

    def test_recorded_completion_at_cutoff_is_excluded_and_missing_cutoff_stays_unknown(self):
        eligible_id = self.expected.loc[self.expected.timing_input_eligible & self.expected.total_cycle_time_days.gt(0)].index[0]
        row = self.source.loc[self.source.order_id.eq(eligible_id)].reset_index(drop=True).copy()
        # The boundary is an actual observed instant used as a configuration value, never a new event.
        row.loc[0, "event_time_cutoff_exclusive"] = row.loc[0, "order_delivered_customer_date"]
        frames, summary = importer.build_import(row, self.config)
        self.assertTrue(summary["ready_for_pipeline"])
        self.assertFalse(frames["orders.csv"].timing_input_eligible.any())
        self.assertEqual(frames["orders.csv"].timing_input_reason.iloc[0], "completion_not_before_partition_cutoff")
        row.loc[0, "event_time_cutoff_exclusive"] = ""
        frames, summary = importer.build_import(row, self.config)
        self.assertTrue(summary["ready_for_pipeline"])
        self.assertFalse(frames["orders.csv"].timing_input_eligible.any())
        self.assertEqual(frames["orders.csv"].timing_input_reason.iloc[0], "partition_cutoff_required")
        row.loc[0, "event_time_cutoff_exclusive"] = row.loc[0, "order_purchase_timestamp"]
        frames, summary = importer.build_import(row, self.config)
        self.assertFalse(summary["ready_for_pipeline"])
        self.assertTrue(frames["issues.csv"].issue_code.eq("purchase_outside_partition_cutoff").any())

    def test_actual_test_partition_is_rejected_from_development_and_never_training_eligible(self):
        # Only tests partition admission on real rows; no model, threshold or outcome evaluation is run.
        selected = self.cohort.loc[self.cohort.split.eq("test")].iloc[:1]
        columns = ["single_category_name" if column == "product_category_name" else column for column in self.orders.columns]
        records = selected[columns].rename(columns={"single_category_name": "product_category_name"})
        source = pd.read_csv(io.StringIO(records.to_csv(index=False, date_format="%Y-%m-%d %H:%M:%S")), dtype="string", keep_default_na=False)
        frames, summary = importer.build_import(source, self.config)
        self.assertEqual(summary["quarantined_records"], 1)
        self.assertTrue(frames["issues.csv"].issue_code.eq("test_in_development_source").any())
        config = deepcopy(self.config)
        config["dataset_scope"] = "unassigned"
        frames, summary = importer.build_import(source, config)
        self.assertTrue(summary["ready_for_pipeline"])
        self.assertFalse(frames["orders.csv"].timing_input_eligible.any())
        self.assertEqual(frames["orders.csv"].timing_input_reason.iloc[0], "reserved_test_not_used")

    def test_config_rejects_ambiguous_mapping_unknown_keys_and_timezone_guessing(self):
        for modify in [lambda c: c.update(extra=True),
                       lambda c: c["columns"].update(order_id="order_status"),
                       lambda c: c["columns"].update(split=None),
                       lambda c: c.update(timestamp_basis="utc_offset"),
                       lambda c: c["status_mapping"].update(delivered=[]),
                       lambda c: c.update(delimiter=";;")]:
            config = deepcopy(self.config)
            modify(config)
            with self.assertRaises(ValueError):
                importer.validate_config(config)
        with self.assertRaisesRegex(ValueError, "Duplicate JSON key"):
            importer.unique_json_keys([("columns", self.config["columns"]), ("columns", self.config["columns"])])

    def test_strict_csv_rejects_duplicate_headers_ragged_rows_and_empty_template(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "source.csv"
            for header, values in [(list(self.source.columns)+[self.source.columns[0]], self.source.iloc[0].tolist()+[self.source.iloc[0, 0]]),
                                   (list(self.source.columns), self.source.iloc[0].tolist()[:-1]),
                                   (list(self.source.columns), None)]:
                with path.open("w", newline="") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(header)
                    if values is not None:
                        writer.writerow(values)
                with self.assertRaises(ValueError):
                    importer.read_csv_strict(path, self.config)

    def test_column_aliases_bom_and_explicit_delimiter_preserve_values(self):
        config = deepcopy(self.config)
        config["delimiter"] = ";"
        config["columns"]["order_id"] = "case_id"
        renamed = self.source.iloc[:3].rename(columns={"order_id": "case_id"})
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/"real.csv"
            renamed.to_csv(path, index=False, sep=";", encoding="utf-8-sig")
            parsed = importer.read_csv_strict(path, config)
            pd.testing.assert_frame_equal(parsed, renamed)
            frames, summary = importer.build_import(parsed, config)
            self.assertTrue(summary["ready_for_pipeline"])
            self.assertEqual(frames["orders.csv"].order_id.tolist(), self.source.order_id.iloc[:3].tolist())

    def test_missing_mapped_column_stops_before_partial_import(self):
        with self.assertRaisesRegex(ValueError, "Missing mapped"):
            importer.build_import(self.source.drop(columns="order_id"), self.config)

    def test_input_record_order_does_not_change_observations(self):
        frames, _ = importer.build_import(self.source.iloc[::-1], self.config)
        pd.testing.assert_frame_equal(frames["orders.csv"].drop(columns="source_record"),
                                      self.frames["orders.csv"].drop(columns="source_record"))
        self.assertEqual(frames["orders.csv"].source_record.tolist(), list(range(7333, 0, -1)))

    def test_export_never_parses_test_feature_matrix(self):
        original = pd.read_csv

        def guarded(path, *args, **kwargs):
            self.assertNotIn("test_timing_features.csv", str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=guarded):
            exported, _, _, _ = export.prepare_source()
        pd.testing.assert_frame_equal(exported, self.orders)

    def test_real_disk_roundtrip_hashes_loader_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as temporary:
            package, snapshot = Path(temporary)/"package", Path(temporary)/"snapshot"
            export.write_source(self.orders, self.lineage, self.config, self.provenance, package)
            frames, summary, config, provenance = importer.prepare_import(package/"orders.csv", package/"import_config.json")
            importer.write_import(frames, summary, config, provenance, snapshot)
            orders, manifest = importer.load_import_snapshot(snapshot)
            pd.testing.assert_frame_equal(orders, self.frames["orders.csv"], check_dtype=False)
            for name, digest in manifest["output_hashes"].items():
                self.assertEqual(importer.audit.file_hash(snapshot/name), digest)
            self.assertEqual((package/"orders.csv").read_bytes(), (snapshot/"source.csv").read_bytes())
            with self.assertRaises(FileExistsError):
                export.write_source(self.orders, self.lineage, self.config, self.provenance, package)
            with self.assertRaises(FileExistsError):
                importer.write_import(frames, summary, config, provenance, snapshot)
            with self.assertRaisesRegex(ValueError, "Source changed"):
                importer.write_import(frames, summary, config, {**provenance, "sha256": {**provenance["sha256"], "input_csv": "stale"}}, Path(temporary)/"blocked")

    def test_blocking_import_is_auditable_and_loader_cannot_bypass_it(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = pd.concat([self.source.iloc[:1], self.source.iloc[:1]], ignore_index=True)
            source.to_csv(root/"input.csv", index=False)
            (root/"config.json").write_text(json.dumps(self.config))
            result = subprocess.run([sys.executable, str(ROOT/"scripts/01_data_preparation/import_process_data.py"),
                                     "--input", str(root/"input.csv"), "--config", str(root/"config.json"),
                                     "--output", str(root/"snapshot")], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2)
            self.assertTrue((root/"snapshot/quarantined_rows.csv").is_file())
            with self.assertRaisesRegex(ValueError, "blocking row/scope"):
                importer.load_import_snapshot(root/"snapshot")


if __name__ == "__main__":
    unittest.main()
