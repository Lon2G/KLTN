"""Real pilot evidence: policy consistency is not independent anomaly accuracy."""

from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import review_pilot_signal_findings as findings


class PilotSignalFindingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {**findings.evidence.audit.inventory(findings.review.DEFAULT_BATCH),
                      **findings.evidence.audit.inventory(findings.evidence.DEFAULT_OUTPUT)}
        with patch.object(joblib, "load", side_effect=AssertionError("No model loading")), \
                patch.object(findings.evidence.audit.readiness.integration.delivery, "fit_entry", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.provenance = findings.load_inputs()
            cls.frames, cls.summary = findings.build_tables(cls.inputs)
        cls.cases = cls.frames["case_findings.csv"].set_index("review_id")
        cls.margins = cls.frames["stage_margins.csv"]
        cls.actions = cls.frames["verification_actions.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"findings"
        findings.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_120_original_orders_in_exhaustive_exclusive_groups(self):
        self.assertEqual(self.cases.index.tolist(), self.inputs["scope_ledger.csv"].review_id.tolist())
        self.assertEqual(len(self.cases), 120)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertEqual(self.summary["group_counts"], {
            "no_primary_signal": 73, "primary_signal": 31, "insufficient_evidence": 14, "proposed_test_hold": 2})
        self.assertEqual(int(self.frames["group_summary.csv"].orders.sum()), 120)

    def test_late_only_retained_without_inventing_bottleneck(self):
        self.assertEqual(self.summary["late_only_ids"], ["BR0059"])
        row = self.cases.loc["BR0059"]
        self.assertEqual(row.days_late, 5)
        self.assertTrue(row.calendar_late_signal)
        self.assertFalse(row.long_stage_warning)
        self.assertEqual(row.slower_stage_count, 3)
        self.assertEqual(row.finding_group, "primary_signal")
        actions = self.actions.loc[self.actions.review_id.eq("BR0059")]
        self.assertEqual(actions.action_code.tolist(), ["verify_late_promise"])

    def test_slower_than_median_does_not_automatically_become_warning(self):
        self.assertEqual(len(self.summary["no_primary_but_slower_stage_ids"]), 65)
        self.assertEqual(self.summary["slow_but_not_long_stage_rows"], 132)
        row = self.cases.loc["BR0001"]
        self.assertEqual(row.slower_stage_count, 3)
        self.assertEqual(row.finding_group, "no_primary_signal")
        self.assertFalse(row.long_stage_warning)
        self.assertTrue(pd.isna(row.human_label))

    def test_short_only_is_secondary_and_never_ground_truth(self):
        self.assertEqual(len(self.summary["short_note_without_primary_ids"]), 10)
        selected = self.cases.loc[self.summary["short_note_without_primary_ids"]]
        self.assertTrue(selected.finding_group.eq("no_primary_signal").all())
        self.assertTrue(selected.short_stage_note.all())
        self.assertFalse(selected.long_stage_warning.any())
        self.assertTrue(selected.human_label.isna().all())

    def test_unknown_and_held_orders_not_counted_as_negatives(self):
        unknown = self.cases.loc[self.cases.finding_group.eq("insufficient_evidence")]
        self.assertEqual(len(unknown), 14)
        self.assertTrue(unknown.long_stage_warning.isna().all())
        held = self.cases.loc[self.cases.finding_group.eq("proposed_test_hold")]
        self.assertEqual(held.index.tolist(), ["BR0043", "BR0119"])
        for field in ["days_late", "calendar_state", "calendar_late_signal", "long_stage_warning", "payment_difference_minor_units"]:
            self.assertTrue(held[field].isna().all())
        self.assertFalse(self.actions.review_id.isin(held.index).any())
        self.assertFalse(self.margins.review_id.isin(held.index).any())

    def test_early_slow_stage_keeps_its_investigation_action(self):
        row = self.cases.loc["BR0079"]
        self.assertEqual(row.calendar_state, "early")
        self.assertTrue(row.long_stage_warning)
        actions = self.actions.loc[self.actions.review_id.eq("BR0079")]
        self.assertEqual(actions.scope.tolist(), ["preparation_handoff"])
        self.assertEqual(actions.action_code.tolist(), ["verify_long_stage"])
        self.assertTrue(actions.cause_status.eq("not_established").all())
        self.assertTrue(actions.action_status.eq("proposed_not_executed").all())

    def test_signed_margin_is_exact_and_not_a_confidence(self):
        self.assertEqual(len(self.margins), 354)
        expected = (self.margins.duration_days-self.margins.upper_boundary_days_inclusive).where(self.margins.assessable)
        pd.testing.assert_series_equal(expected, self.margins.signed_margin_to_upper_days, check_names=False, check_exact=True)
        self.assertEqual(int(self.margins.signed_margin_to_upper_days.isna().sum()), 42)
        above = self.margins.loc[self.margins.long_stage_warning.fillna(False)]
        closest = above.sort_values("signed_margin_to_upper_days").iloc[0]
        self.assertEqual(closest.review_id, "BR0077")
        self.assertAlmostEqual(closest.signed_margin_to_upper_days, 0.09539351851851663)
        self.assertNotIn("confidence", self.margins.columns)
        self.assertNotIn("probability", self.margins.columns)

    def test_conditional_actions_counts_and_real_locators(self):
        self.assertEqual(len(self.actions), 58)
        self.assertEqual(self.summary["action_counts"], {
            "verify_long_stage": 30, "resolve_missing_evidence": 14, "verify_late_promise": 12, "verify_payment_reconciliation": 2})
        parent = Path(self.provenance["source_directory"])
        for row in self.actions.itertuples(index=False):
            self.assertTrue((parent/row.source_locator.split(":", 1)[0]).is_file())
            self.assertIn("review_id="+row.review_id, row.source_locator)
            self.assertEqual(row.action_status, "proposed_not_executed")
            self.assertFalse(row.independent_reference)

    def test_money_discrepancy_not_erased_or_called_data_error(self):
        self.assertEqual(self.cases.loc["BR0051", "payment_difference_minor_units"], 3944)
        self.assertEqual(self.cases.loc["BR0060", "payment_difference_minor_units"], 1)
        selected = self.actions.loc[self.actions.action_code.eq("verify_payment_reconciliation")]
        self.assertEqual(selected.review_id.tolist(), ["BR0051", "BR0060"])
        self.assertTrue(self.cases.anomaly_subtype.isna().all())

    def test_no_fabricated_judgments_metrics_or_selection(self):
        self.assertTrue(self.cases.human_label.isna().all())
        self.assertFalse(self.cases.independent_reference.any())
        self.assertTrue(self.cases.independent_judgment_status.eq("not_provided_by_this_analysis").all())
        self.assertEqual(self.summary["human_labels_created"], 0)
        for field in ["independent_reference_ready", "accuracy_computed", "auc_computed", "threshold_changed",
                      "model_trained", "model_selected", "test_scored", "new_cases_sampled", "intervention_executed"]:
            self.assertFalse(self.summary[field])

    def test_real_case_flag_or_description_corruption_is_rejected(self):
        inputs = {name: frame.copy(deep=True) for name, frame in self.inputs.items()}
        orders = inputs["order_explanations.csv"]
        index = orders.index[orders.review_id.eq("BR0079")][0]
        orders.at[index, "long_stage_warning"] = False
        with self.assertRaises((ValueError, AssertionError)):
            findings.build_tables(inputs)
        inputs = {name: frame.copy(deep=True) for name, frame in self.inputs.items()}
        orders = inputs["order_explanations.csv"]
        orders.at[index, "order_explanation_vi"] = orders.loc[orders.review_id.eq("BR0001"), "order_explanation_vi"].iloc[0]
        with self.assertRaises((ValueError, AssertionError)):
            findings.build_tables(inputs)

    def test_report_all_ids_and_non_accuracy_limit(self):
        report = findings.report(self.frames, self.summary)
        for rid in self.cases.index:
            self.assertIn("| "+rid+" |", report)
        self.assertIn("không chứng minh độ chính xác", report)
        self.assertIn("Không cần chốt lại", report)
        self.assertIn("BR0059", report)
        self.assertIn("BR0077", report)

    def test_roundtrip_protected_sources_and_no_overwrite(self):
        manifest = findings.load_snapshot(self.output)
        for name, expected in self.frames.items():
            actual = findings.shared.read_table(self.output, "analyst_only/"+name, manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        for name, digest in self.before.items():
            self.assertEqual(findings.review.digest(ROOT/name), digest)
        with self.assertRaises(FileExistsError):
            findings.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        with self.assertRaises(ValueError):
            findings.write_snapshot(self.frames, self.summary, self.provenance, findings.review.DEFAULT_BATCH)

    def test_output_tampering_rejected_in_disposable_copy(self):
        import shutil
        with tempfile.TemporaryDirectory() as temp:
            copy = Path(temp)/"copy"
            shutil.copytree(self.output, copy)
            (copy/"summary.json").write_text("{}\n")
            with self.assertRaises(ValueError):
                findings.load_snapshot(copy)


if __name__ == "__main__":
    unittest.main()
