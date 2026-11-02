"""Audit the real frozen pilot without fabricated research rows or labels."""

from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import audit_review_pilot as audit


class ReviewPilotAuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = audit.inventory(audit.review.DEFAULT_BATCH)
        with patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(audit.readiness.integration.delivery, "fit_entry", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = audit.load_inputs()
            cls.rows, cls.summary = audit.build_audit(cls.inputs)
        cls.by_id = {row["review_id"]: row for row in cls.rows}

    def test_all_original_ids_and_source_rows_verified(self):
        self.assertEqual([r["review_id"] for r in self.rows], self.inputs["key"].review_id.tolist())
        self.assertEqual([r["order_id"] for r in self.rows], self.inputs["key"].order_id.tolist())
        self.assertEqual(len(self.rows), 120)
        self.assertEqual(len(set(r["order_id"] for r in self.rows)), 120)
        self.assertEqual(self.summary["source_checks"], {"orders": 120, "payment_records.csv": 135, "item_records.csv": 171})
        for row in self.rows:
            self.assertTrue(row["source_row_verified"])
            self.assertEqual(len(row["pilot_evidence_records"]["cases.csv"]), 1)

    def test_two_proposed_test_orders_have_no_derived_outcomes(self):
        self.assertEqual(self.summary["held_review_ids"], ["BR0043", "BR0119"])
        key = self.inputs["key"].set_index("review_id")
        self.assertEqual(key.loc["BR0119", "order_purchase_timestamp"], self.inputs["boundary"])
        for rid in self.summary["held_review_ids"]:
            row = self.by_id[rid]
            self.assertEqual(row["delivery_state"], "proposed_test_hold")
            self.assertIsNone(row["business"])
            self.assertIsNone(row["promise_delta_calendar_days"])
            self.assertEqual(row["stage_durations_days"], {})

    def test_post_cutoff_delivery_is_unknown_not_zero_or_normal(self):
        self.assertEqual(len(self.summary["post_cutoff_review_ids"]), 10)
        for rid in self.summary["post_cutoff_review_ids"]:
            row = self.by_id[rid]
            self.assertEqual(row["delivery_state"], "unknown")
            self.assertIsNone(row["promise_delta_calendar_days"])
            self.assertIsNone(row["stage_durations_days"]["transport"])
            self.assertIsNone(row["stage_durations_days"]["total_cycle"])
            self.assertGreater(row["stage_durations_days"]["approval"], 0)
            self.assertGreater(row["stage_durations_days"]["preparation_handoff"], 0)

    def test_visible_duration_calculations_and_calendar_counts(self):
        self.assertEqual(self.summary["delivery_states"], {"early": 90, "late": 12, "unknown": 10, "on_date": 6, "proposed_test_hold": 2})
        source = self.inputs["blind"]["cases.csv"].set_index("review_id")
        for row in self.rows:
            for (start, end, stage) in audit.performance.STAGES:
                duration = row["stage_durations_days"].get(stage)
                if duration is not None:
                    record = source.loc[row["review_id"]]
                    expected = (pd.Timestamp(record[end])-pd.Timestamp(record[start])).total_seconds()/86400
                    self.assertEqual(duration, expected)
        self.assertEqual(self.summary["aggregate_mismatch_cases"], 0)
        self.assertEqual(self.summary["visible_inversion_cases"], 0)

    def test_exact_money_discrepancies_not_rounded_away(self):
        self.assertEqual(self.summary["payment_difference_cases"], [
            {"review_id": "BR0051", "difference_minor_units": 3944},
            {"review_id": "BR0060", "difference_minor_units": 1}])
        self.assertEqual(self.summary["money_matched_cases"], 116)
        for rid in ["BR0051", "BR0060"]:
            row = self.by_id[rid]
            b = row["business"]
            self.assertEqual(b["payment_total_minor_units"]-b["items_plus_freight_minor_units"], b["payment_difference_minor_units"])
            self.assertEqual(b["aggregate_mismatches"], [])
            self.assertIsNone(row["anomaly_label"])
        self.assertEqual(audit.annotations.minor_units("0.01"), 1)

    def test_all_amounts_and_keys_complete_in_audited_scope(self):
        for field in ["negative_amounts", "missing_amounts", "duplicate_item_cases", "duplicate_payment_sequence_cases", "money_unknown_cases"]:
            self.assertEqual(self.summary[field], 0)

    def test_reference_absence_does_not_become_negative_warning(self):
        for row in self.rows:
            self.assertIsNone(row["long_stage_warning"])
            self.assertIsNone(row["faster_stage_observed"])
            self.assertEqual(row["stage_reference_status"], "not_scored_protected_pilot")
            self.assertEqual(row["promise_basis_status"], "unverified_source_method_not_provided")
        self.assertEqual(self.summary["pilot_cases_in_current_explanations"], 0)

    def test_context_and_sample_scope_are_explicit(self):
        self.assertEqual(self.summary["geography_ineligible_review_ids"], ["BR0025", "BR0051", "BR0091"])
        self.assertEqual(sum(self.summary["seller_history_routes"].values()), 118)
        self.assertEqual(self.summary["original_sampling_population"], 1522)
        self.assertEqual(self.summary["sampling_weight_sum"], 1522)
        self.assertEqual(self.summary["sample_by_anchor_group"], {"all_flag": 11, "disagreement": 55, "none_flag": 54})

    def test_no_labels_or_scoring_claims(self):
        self.assertEqual(self.summary["previous_rule_annotation_cases"], 20)
        self.assertEqual(self.summary["recorded_pilot_session_labels"], 0)
        self.assertEqual(self.summary["hierarchical_journal_events"], 0)
        for field in ["labels_created", "model_fitted", "model_scored", "test_scored", "split_activated", "accuracy_computed", "auc_computed", "new_cases_sampled", "source_edited"]:
            self.assertFalse(self.summary[field])
        self.assertTrue(all(r["anomaly_label"] is None and not r["independent_reference"] for r in self.rows))

    def test_no_source_mutation(self):
        self.assertEqual(self.before, audit.inventory(audit.review.DEFAULT_BATCH))
        audit.verify_unchanged(self.provenance)

    def test_overlapping_public_explanation_rejected(self):
        inputs = {**self.inputs, "explained_ids": {self.rows[0]["order_id"]}}
        with self.assertRaisesRegex(ValueError, "overlaps"):
            audit.build_audit(inputs)

    def test_removing_real_source_row_fails_lineage_check(self):
        inputs = deepcopy(self.inputs)
        inputs["blind"]["payment_records.csv"] = inputs["blind"]["payment_records.csv"].iloc[1:]
        _, provenance = audit.readiness.load_inputs()
        with self.assertRaisesRegex(ValueError, "source locators"):
            audit.source_checks(inputs, provenance)

    def test_report_has_all_120_ids_and_unknowns(self):
        text = audit.report(self.rows, self.summary)
        for row in self.rows:
            self.assertIn("| "+row["review_id"]+" |", text)
        self.assertIn("39.44", text)
        self.assertIn("0.01", text)
        json.dumps(self.rows, allow_nan=False)
        json.dumps(self.summary, allow_nan=False)

    def test_snapshot_roundtrip_refuses_overwrite_or_tamper(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/"audit"
            with patch.object(audit, "load_inputs", return_value=(self.inputs, self.provenance)):
                audit.run(output)
                rows, summary = audit.load_snapshot(output)
                self.assertEqual(rows, self.rows)
                self.assertEqual(summary, self.summary)
                with self.assertRaises(FileExistsError):
                    audit.run(output)
            # Damage only a disposable copy of actual output to test integrity checks.
            (output/"summary.json").write_text("{}\n")
            with self.assertRaises(ValueError):
                audit.load_snapshot(output)

    def test_destination_cannot_overlap_original_sources(self):
        for path in [audit.review.DEFAULT_BATCH, ROOT/"data/raw", audit.explanation.DEFAULT_OUTPUT/"new_audit"]:
            with self.assertRaises(ValueError):
                audit.run(path)


if __name__ == "__main__":
    unittest.main()
