"""Read-only inspection tests on real stored Olist observations, no fabricated cases."""

from copy import deepcopy
import json
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import inspect_process_evidence as inspection


class EvidenceInspectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append((str(path), kwargs.get("usecols")))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(inspection.integration, "build_integration", side_effect=AssertionError("No recomputation")), \
                patch.object(inspection.review, "open_session", side_effect=AssertionError("No review sessions")):
            cls.repository = inspection.load_repository()
        cls.frames = cls.repository["frames"]
        cls.cases = cls.frames["case_evidence.csv"]
        cls.summary = inspection.query(cls.repository)
        cls.page = inspection.query(cls.repository, limit=10)
        cls.order_id = inspection.select_cases(cls.repository, signal="delivery_long_interval_warning").iloc[0].order_id
        cls.detail = inspection.inspect_case(cls.repository, cls.order_id)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.root = Path(cls.temporary.name)
        cls.exported = inspection.export_result(cls.repository, cls.detail, cls.root/"case")

    def test_population_and_blind_protection_reconcile(self):
        self.assertEqual(self.repository["source_orders"], 7333)
        self.assertEqual(len(self.repository["protected_ids"]), 120)
        self.assertEqual(len(self.cases), 7213)
        self.assertEqual(self.cases.split.value_counts().to_dict(), {"train": 5480, "validation": 1733})
        self.assertTrue(self.cases.order_id.is_unique)

    def test_protected_ids_absent_from_every_public_table(self):
        for name, frame in self.frames.items():
            self.assertFalse(set(frame.order_id) & self.repository["protected_ids"], name)
            self.assertTrue(set(frame.order_id).issubset(self.cases.order_id), name)

    def test_direct_protected_lookup_blocks_without_exposing_flags_or_mapping(self):
        for order_id in sorted(self.repository["protected_ids"]):
            with self.assertRaisesRegex(ValueError, "reserved for blinded review") as caught:
                inspection.inspect_case(self.repository, order_id)
            self.assertNotIn(order_id, str(caught.exception))

    def test_reserved_test_id_is_not_accessible(self):
        ledger = pd.read_csv(ROOT/"data/experiments/bed_bath_table_v1/cohort_cases.csv", usecols=["order_id", "split"], dtype="string")
        order_id = ledger.loc[ledger.split.eq("test"), "order_id"].iloc[0]
        with self.assertRaisesRegex(ValueError, "not in the accessible development scope"):
            inspection.inspect_case(self.repository, order_id)
        for path, columns in self.reads:
            self.assertFalse("test_timing" in path, path)
            if path.endswith("cohort_cases.csv"):
                self.assertEqual(columns, ["order_id", "split"])

    def test_default_summary_is_validation_not_all_data(self):
        self.assertEqual(self.summary["matched_orders"], 1733)
        self.assertEqual(self.summary["query"]["split"], "validation")
        self.assertEqual(self.summary["coverage"], {"business_scored": 1403, "business_not_scored": 330,
                                                   "delivery_observed_outcome": 1268, "delivery_forecast_only": 200, "delivery_no_forecast": 265})
        self.assertNotIn("rows", self.summary)

    def test_all_and_training_scope_counts(self):
        self.assertEqual(inspection.query(self.repository, split="all")["matched_orders"], 7213)
        training = inspection.query(self.repository, split="train")
        self.assertEqual(training["matched_orders"], 5480)
        self.assertEqual(training["coverage"]["business_scored"], 0)
        self.assertEqual(training["coverage"]["delivery_no_forecast"], 5480)

    def test_filter_intersection_matches_actual_values(self):
        actual = inspection.select_cases(self.repository, month="2018-03", signal="delivery_long_interval_warning", delivery_state="observed_outcome")
        expected = self.cases.loc[self.cases.split.eq("validation") & self.cases.order_purchase_timestamp.dt.strftime("%Y-%m").eq("2018-03")
                                  & self.cases.delivery_models_long_warning.gt(0) & self.cases.delivery_models_evaluated.notna()]
        self.assertFalse(actual.empty)
        self.assertEqual(set(actual.order_id), set(expected.order_id))

    def test_signal_filters_match_saved_codes_not_new_thresholds(self):
        for code in inspection.SIGNALS:
            actual = inspection.select_cases(self.repository, split="all", signal=code)
            expected = self.cases.loc[self.cases.evidence_codes_json.map(lambda value: code in json.loads(value))]
            self.assertEqual(set(actual.order_id), set(expected.order_id), code)

    def test_three_delivery_states_partition_query_population(self):
        groups = [set(inspection.select_cases(self.repository, delivery_state=state).order_id)
                  for state in ["observed_outcome", "forecast_only", "no_forecast"]]
        self.assertEqual(set.union(*groups), set(self.cases.loc[self.cases.split.eq("validation"), "order_id"]))
        for i, left in enumerate(groups):
            for right in groups[i+1:]:
                self.assertFalse(left & right)

    def test_stable_pagination_and_summary_denominators(self):
        page1 = inspection.query(self.repository, limit=10)
        page2 = inspection.query(self.repository, limit=10, offset=10)
        expected = inspection.select_cases(self.repository)
        self.assertEqual([row["order_id"] for row in page1["rows"]+page2["rows"]], expected.order_id.iloc[:20].tolist())
        self.assertEqual(page1["coverage"], page2["coverage"])
        self.assertEqual(page1["matched_orders"], 1733)
        self.assertTrue(page1["has_more"])

    def test_empty_results_have_no_fabricated_rows(self):
        result = inspection.query(self.repository, split="train", month="2018-03", limit=10)
        self.assertEqual(result["matched_orders"], 0)
        self.assertEqual(result["rows"], [])
        self.assertFalse(result["has_more"])
        self.assertTrue(all(value == 0 for value in result["coverage"].values()))
        result = inspection.query(self.repository, offset=2000, limit=10)
        self.assertEqual(result["matched_orders"], 1733)
        self.assertEqual(result["rows"], [])
        self.assertFalse(result["has_more"])

    def test_invalid_filters_and_page_sizes_rejected(self):
        for kwargs in [{"split": "test"}, {"month": "2018-13"}, {"month": "2018-3"}, {"signal": "fraud"},
                       {"delivery_state": "Normal"}, {"limit": 0}, {"limit": 201}, {"offset": -1}]:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                inspection.query(self.repository, **kwargs)
        with self.assertRaisesRegex(ValueError, "32-character"):
            inspection.inspect_case(self.repository, self.order_id[:10])

    def test_detail_has_one_actual_order_and_exact_child_records(self):
        self.assertEqual(self.detail["case"]["order_id"], self.order_id)
        expected = self.cases.loc[self.cases.order_id.eq(self.order_id)]
        self.assertEqual(self.detail["case"], inspection.records(expected)[0])
        for name, frame in self.frames.items():
            if name != "case_evidence.csv":
                self.assertEqual(self.detail["tables"][name], inspection.records(frame.loc[frame.order_id.eq(self.order_id)]))
                self.assertTrue(all(row["order_id"] == self.order_id for row in self.detail["tables"][name]))

    def test_source_payment_and_item_counts_and_minor_unit_sums_exact(self):
        for name, count, fields in [("payment_source_evidence.csv", "payment_record_count", [("payment_value_minor_units", "payment_value_sum_minor_units")]),
                                    ("item_source_evidence.csv", "item_count", [("price_minor_units", "item_price_sum_minor_units"), ("freight_minor_units", "freight_sum_minor_units")])]:
            rows = self.detail["tables"][name]
            self.assertEqual(len(rows), self.detail["case"][count])
            self.assertEqual(len({row["clean_source_record"] for row in rows}), len(rows))
            for source, aggregate in fields:
                self.assertEqual(sum(row[source] for row in rows), self.detail["case"][aggregate])

    def test_milestones_and_durations_preserve_cutoff_states(self):
        rows = self.cases.loc[self.cases.process_post_cutoff_events.gt(0)]
        self.assertFalse(rows.empty)
        result = inspection.inspect_case(self.repository, rows.iloc[0].order_id)
        cutoff = pd.Timestamp(result["case"]["event_time_cutoff_exclusive"])
        for milestone in result["tables"]["milestone_evidence.csv"]:
            if milestone["evidence_state"] == "visible":
                self.assertLess(pd.Timestamp(milestone["recorded_timestamp"]), cutoff)
            elif milestone["evidence_state"] == "at_or_after_cutoff":
                self.assertGreaterEqual(pd.Timestamp(milestone["recorded_timestamp"]), cutoff)
            else:
                self.assertIsNone(milestone["recorded_timestamp"])
        for duration in result["tables"]["process_durations.csv"]:
            if duration["duration_state"] == "endpoint_not_visible":
                self.assertIsNone(duration["duration_days"])
            else:
                expected = (pd.Timestamp(duration["end_timestamp"])-pd.Timestamp(duration["start_timestamp"])).total_seconds()/86400
                self.assertEqual(duration["duration_days"], expected)

    def test_saved_scores_thresholds_and_all_model_configurations_preserved(self):
        result = self.detail
        self.assertEqual(len(result["tables"]["business_anchor_evidence.csv"]), 3)
        self.assertEqual(len(result["tables"]["delivery_model_evidence.csv"]), 12)
        for row in result["tables"]["business_anchor_evidence.csv"]:
            self.assertEqual(row["warning"], row["score"] > row["threshold"])
        for row in result["tables"]["delivery_model_evidence.csv"]:
            duration = row[inspection.integration.delivery.context.TARGET]
            self.assertEqual(row["long_warning"], duration > row["calibrated_upper_days"])
        self.assertIn("pilot display anchor", inspection.render_markdown(result).lower())

    def test_unknown_outcomes_serialize_null_not_false_or_normal(self):
        order_id = inspection.select_cases(self.repository, delivery_state="forecast_only").iloc[0].order_id
        result = inspection.inspect_case(self.repository, order_id)
        self.assertEqual(result["availability"]["delivery"], "saved_forecast_outcome_unknown")
        for row in result["tables"]["delivery_model_evidence.csv"]:
            self.assertFalse(row["outcome_available_for_saved_comparison"])
            for field in [inspection.integration.delivery.context.TARGET, "long_warning", "short_warning", "zero_duration_warning"]:
                self.assertIsNone(row[field])
        self.assertIn("[unknown/not available]", inspection.render_markdown(result))

    def test_unscored_training_case_has_empty_models_not_negative_flags(self):
        result = inspection.inspect_case(self.repository, self.cases.loc[self.cases.split.eq("train"), "order_id"].iloc[0])
        self.assertEqual(result["availability"], {"business": "not_scored_here", "delivery": "no_saved_forecast"})
        self.assertEqual(result["tables"]["business_anchor_evidence.csv"], [])
        self.assertEqual(result["tables"]["delivery_model_evidence.csv"], [])
        self.assertIsNone(result["case"]["business_profiles_with_warning"])

    def test_explanation_values_come_from_saved_fields(self):
        for note in self.detail["evidence_codes"]:
            self.assertEqual(note["observed_value"], self.detail["case"][note["field"]])
            self.assertEqual(note["meaning"], inspection.SIGNALS[note["code"]][1])
        self.assertEqual([note["code"] for note in self.detail["evidence_codes"]], json.loads(self.detail["case"]["evidence_codes_json"]))

    def test_json_is_strict_lossless_and_units_are_explicit(self):
        serialized = json.dumps(self.detail, allow_nan=False)
        self.assertEqual(json.loads(serialized), self.detail)
        self.assertEqual(self.detail["identity"], inspection.integration.business.IDENTITY)
        self.assertEqual(self.detail["units"]["durations"], "elapsed_days")
        self.assertNotIn(": NaN", serialized)
        self.assertNotIn(": Infinity", serialized)

    def test_protected_ids_never_appear_in_exports_or_query_results(self):
        serialized = json.dumps([self.summary, self.detail, inspection.query(self.repository, split="all", limit=200)])
        for order_id in self.repository["protected_ids"]:
            self.assertNotIn(order_id, serialized)
        actual = (self.exported/"README.md").read_text()+(self.exported/"result.json").read_text()
        for order_id in self.repository["protected_ids"]:
            self.assertNotIn(order_id, actual)

    def test_export_roundtrip_hashes_and_preview_text(self):
        result, manifest = inspection.load_export(self.exported)
        self.assertEqual(result, self.detail)
        self.assertEqual(manifest["provenance"], self.repository["provenance"])
        self.assertEqual((self.exported/"README.md").read_text(), inspection.render_markdown(result))
        self.assertEqual(set(path.name for path in self.exported.iterdir()), {"README.md", "result.json", "manifest.json"})

    def test_summary_and_list_export_roundtrip(self):
        for name, result in [("summary", self.summary), ("page", self.page)]:
            path = inspection.export_result(self.repository, result, self.root/name)
            self.assertEqual(inspection.load_export(path)[0], result)

    def test_export_rejects_altered_values_and_source_directory(self):
        changed = deepcopy(self.detail)
        changed["availability"]["delivery"] = "Normal"
        with self.assertRaisesRegex(ValueError, "exactly match"):
            inspection.export_result(self.repository, changed, self.root/"altered")
        with self.assertRaisesRegex(ValueError, "never inside source"):
            inspection.export_result(self.repository, self.detail, inspection.integration.DEFAULT_OUTPUT/"inspection")
        self.assertFalse((self.root/"altered").exists())

    def test_existing_export_and_tampered_export_are_refused(self):
        with self.assertRaises(FileExistsError):
            inspection.export_result(self.repository, self.detail, self.exported)
        copied = self.root/"tampered"
        shutil.copytree(self.exported, copied)
        with (copied/"result.json").open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            inspection.load_export(copied)

    def test_changed_code_and_failed_publication_leave_no_partial_output(self):
        provenance = deepcopy(self.repository["provenance"])
        provenance["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code/protocol"):
            inspection.verify_sources(provenance)
        output = self.root/"incomplete"
        with patch.object(inspection, "verify_sources", side_effect=[None, ValueError("Source changed")]):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                inspection.export_result(self.repository, self.detail, output)
        self.assertFalse(output.exists())
        self.assertFalse(list(self.root.glob(".inspection-*")))

    def test_missing_or_duplicate_real_child_rows_are_rejected(self):
        name = "payment_source_evidence.csv"
        with self.assertRaises(AssertionError):
            inspection.validate_public_tables({**self.frames, name: self.frames[name].iloc[1:]})
        with self.assertRaises(ValueError):
            inspection.validate_public_tables({**self.frames, name: pd.concat([self.frames[name], self.frames[name].iloc[:1]], ignore_index=True)})

    def test_cli_json_stdout_is_parseable_and_filters_apply(self):
        run = subprocess.run([sys.executable, str(Path(inspection.__file__)), "--list", "--month", "2018-03", "--limit", "2", "--format", "json"],
                             cwd=ROOT, env={**os.environ, "MPLCONFIGDIR": str(self.root/"matplotlib")}, capture_output=True, text=True, check=True)
        actual = json.loads(run.stdout)
        self.assertEqual(actual, inspection.query(self.repository, month="2018-03", limit=2))

    def test_cli_blocks_protected_case_and_conflicting_filters(self):
        order_id = sorted(self.repository["protected_ids"])[0]
        run = subprocess.run([sys.executable, str(Path(inspection.__file__)), "--order-id", order_id, "--format", "json"],
                             cwd=ROOT, env={**os.environ, "MPLCONFIGDIR": str(self.root/"matplotlib")}, capture_output=True, text=True)
        self.assertEqual(run.returncode, 2)
        self.assertEqual(run.stdout, "")
        self.assertIn("reserved for blinded review", run.stderr)
        with patch.object(inspection, "load_repository", side_effect=AssertionError("Invalid arguments must stop before data loading")):
            with self.assertRaises(SystemExit) as caught:
                inspection.main(["--order-id", self.order_id, "--split", "validation"])
            self.assertEqual(caught.exception.code, 2)

    def test_readable_case_report_contains_key_sections_and_limits(self):
        rendered = inspection.render_markdown(self.detail)
        for heading in ["Process View", "Milestones", "Observed Durations", "Source Payments", "Source Items", "Prior Customer Context",
                        "Geographic Context", "Prior Seller History", "Saved Business Anchor", "Saved Delivery Models", "Source References", "Limits"]:
            self.assertIn("## "+heading, rendered)
        self.assertIn("not status known at cutoff", rendered)
        self.assertIn("not an anomaly probability", rendered)
        self.assertNotIn("final_label", self.detail)


if __name__ == "__main__":
    unittest.main()
