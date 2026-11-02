"""Deterministic annotations from actual pilot evidence, not fabricated labels."""

from decimal import Decimal
from pathlib import Path
import json
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/03_anomaly_detection"))
import annotate_delivery_review20 as annotations


class RuleAnnotations20Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        cls.before = {str(p): annotations.review.digest(p) for root in [annotations.SOURCE, annotations.review.DEFAULT_BATCH]
                      for p in root.rglob("*") if p.is_file()}
        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("Model loading forbidden")):
            cls.source, cls.provenance = annotations.load_inputs()
            cls.frames, cls.summary = annotations.build_annotations(cls.source)
        cls.cases = cls.frames["case_annotations.csv"].set_index("review_id")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"annotations"
        annotations.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_all_twenty_actual_review_ids_are_annotated_once(self):
        self.assertEqual(len(self.cases), 20)
        self.assertTrue(self.cases.index.is_unique)
        self.assertEqual(self.cases.index.tolist(), self.source["cases.csv"].review_id.tolist())
        self.assertEqual(len(self.frames["rule_checks.csv"]), 180)
        self.assertEqual(len(self.frames["scope_annotations.csv"]), 60)

    def test_no_analyst_key_scores_or_external_test_parsed(self):
        for path in self.reads:
            for forbidden in ["analyst_only", "selection_key", "scores", "flags", "test_timing", "predictions"]:
                self.assertNotIn(forbidden, path)
        self.assertEqual({Path(path).name for path in self.reads}, set(annotations.review.SCHEMAS))

    def test_actual_delivery_labels_recompute_independently(self):
        source = self.source["cases.csv"].set_index("review_id")
        delivered = pd.to_datetime(source.order_delivered_customer_date)
        promised = pd.to_datetime(source.order_estimated_delivery_date)
        delta = (delivered.dt.normalize()-promised.dt.normalize()).dt.days
        self.assertEqual(self.cases.promise_delta_calendar_days.tolist(), delta.tolist())
        self.assertEqual(self.summary["delivery_label_counts"], {"early": 18, "on_date": 2})
        self.assertTrue(self.cases.calendar_promise_assessment.eq("meets_recorded_date_promise").all())

    def test_same_day_cases_after_midnight_are_not_late(self):
        self.assertEqual(self.cases.loc[self.cases.delivery_label.eq("on_date")].index.tolist(), ["BR0019", "BR0080"])
        self.assertTrue(self.cases.loc[["BR0019", "BR0080"], "promise_delta_calendar_days"].eq(0).all())

    def test_one_cent_difference_not_rounded_away_or_declared_fraud(self):
        row = self.cases.loc["BR0060"]
        self.assertEqual(row.payment_total_minor_units, 39605)
        self.assertEqual(row.items_plus_freight_minor_units, 39604)
        self.assertEqual(row.payment_difference_minor_units, 1)
        self.assertEqual(row.payment_reconciliation, "needs_verification")
        self.assertEqual(row.source_check_status, "needs_verification")
        self.assertEqual(row.broad_anomaly_status, "unresolved")

    def test_payment_totals_match_decimal_source_rows(self):
        for rid, rows in self.source["payment_records.csv"].groupby("review_id", sort=False):
            total = sum(Decimal(value) for value in rows.payment_value)
            self.assertEqual(self.cases.loc[rid, "payment_total_minor_units"], int(total*100))
        self.assertEqual(self.summary["payment_reconciliation_counts"], {"matched_exactly": 19, "needs_verification": 1})

    def test_stage_durations_recompute_from_original_source_only(self):
        source = self.source["cases.csv"].set_index("review_id")
        for left, right, name in [(0, 1, "approval_days"), (1, 2, "preparation_days"), (2, 3, "transport_days")]:
            expected = (pd.to_datetime(source[annotations.DATES[right]])-pd.to_datetime(source[annotations.DATES[left]])).dt.total_seconds()/86400
            pd.testing.assert_series_equal(self.cases[name], expected, check_names=False, check_dtype=False,
                                           check_index_type=False, check_exact=True)
        self.assertTrue(self.cases.recorded_inversion_pairs.eq(0).all())

    def test_each_original_rule_runs_once_per_case_with_traceable_source_lines(self):
        checks = self.frames["rule_checks.csv"]
        self.assertFalse(checks.duplicated(["review_id", "rule_code"]).any())
        for rid, rows in checks.groupby("review_id"):
            self.assertEqual(set(rows.rule_code), {"T01", "T02", "T03", "T04", "P01", "P02", "P03", "O01", "O02"})
            for row in rows.itertuples(index=False):
                observations = json.loads(row.observations_json)
                self.assertIsInstance(observations, dict)
                for reference in json.loads(row.source_references_json):
                    table = Path(reference["file"]).name
                    source = self.source[table]
                    self.assertTrue(set(reference["columns"]).issubset(source.columns))
                    for line in reference["csv_line_numbers"]:
                        self.assertEqual(source.iloc[line-2].review_id, rid)

    def test_no_normal_fraud_or_verified_legitimate_labels_are_invented(self):
        scopes = self.frames["scope_annotations.csv"]
        self.assertTrue(scopes.assessment_status.eq("unresolved").all())
        self.assertTrue(scopes.parent_label.eq("").all())
        self.assertTrue(scopes.anomaly_subtype.eq("").all())
        self.assertTrue(scopes.human_reviewer_id.eq("").all())
        self.assertTrue(scopes.confidence_probability.eq("").all())
        self.assertFalse(scopes.independent_reference.any())
        for column in ["normal_label", "fraud", "anomaly_probability", "confirmed_anomaly"]:
            self.assertNotIn(column, self.cases.columns)

    def test_multiple_payment_rows_are_not_declared_failed_attempts(self):
        multiple = self.cases.loc[self.cases.payment_records.gt(1)].index
        self.assertEqual(len(multiple), 4)
        checks = self.frames["rule_checks.csv"]
        self.assertTrue(checks.loc[checks.rule_code.eq("P02"), "outcome"].eq("observed").all())

    def test_context_dependent_checks_stay_unresolved_without_thresholds(self):
        checks = self.frames["rule_checks.csv"]
        self.assertTrue(checks.loc[checks.rule_code.isin(["T03", "P03", "O02"]), "outcome"].eq("not_assessable").all())
        self.assertEqual(self.summary["unresolved_broad_anomaly_scopes"], 60)

    def test_original_human_templates_and_both_packets_unchanged(self):
        for name, digest in self.before.items():
            self.assertEqual(annotations.review.digest(name), digest)
        self.assertTrue(self.source["cases.csv"][annotations.review.REVIEW_COLUMNS].eq("").all().all())
        self.assertFalse(list(self.output.rglob("journal.json")))

    def test_exposure_notice_prevents_claiming_future_unassisted_blind_review(self):
        notice = annotations.review.read_json(self.output/"exposure_notice.json")
        self.assertEqual(notice["review_ids"], self.cases.index.tolist())
        self.assertFalse(notice["future_unassisted_blind_reference"])
        self.assertFalse(notice["human_review_performed"])
        self.assertTrue(notice["automatic_annotations_are_not_independent_ground_truth"])

    def test_no_training_accuracy_approval_or_human_review_claims(self):
        for key in ["human_review_performed", "independent_reference_ready", "anomaly_accuracy_computed", "models_loaded",
                    "models_trained", "model_flags_used", "test_scored", "advisor_approval_inferred"]:
            self.assertFalse(self.summary[key])
        self.assertEqual(self.summary["human_labels_created"], 0)

    def test_snapshot_reconstructs_all_tables_and_markdown(self):
        self.assertEqual(annotations.load_snapshot(self.output)["summary"], self.summary)
        report = (self.output/"20_case_da_doi_chieu.md").read_text()
        self.assertEqual(report.count("### BR"), 20)
        self.assertEqual(report, annotations.markdown_report(self.frames, self.summary))

    def test_existing_output_and_protected_directories_refused(self):
        with self.assertRaises(FileExistsError):
            annotations.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for path in [annotations.SOURCE/"annotations", ROOT/"data/raw/annotations"]:
            with self.assertRaises(ValueError):
                annotations.write_snapshot(self.frames, self.summary, self.provenance, path)

    def test_tampering_is_rejected_in_disposable_copy(self):
        target = Path(self.temp.name)/"tampered"
        shutil.copytree(self.output, target)
        with (target/"case_annotations.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            annotations.load_snapshot(target)

    def test_blank_amount_is_not_zero_and_fractional_minor_units_not_rounded(self):
        # Parser guards only; these values never enter a research output.
        self.assertIsNone(annotations.minor_units(""))
        self.assertIsNone(annotations.complete_sum([]))
        self.assertIsNone(annotations.complete_sum([None]))
        for text in ["NaN", "Infinity", "0.001", "not-an-amount"]:
            with self.assertRaises(ValueError):
                annotations.minor_units(text)


if __name__ == "__main__":
    unittest.main()
