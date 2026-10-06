"""Integration checks using real imported cases; tests write no data or labels."""

import importlib.util
from pathlib import Path
import unittest

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "auto_validation_evaluation",
    ROOT / "scripts/03_anomaly_detection/auto_validation_evaluation.py",
)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


class EvaluationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = pd.read_csv(evaluation.BASELINE_FILE, dtype={"case_id": "string"})
        cls.auto = pd.read_csv(evaluation.AUTO_FILE, dtype={"case_id": "string"})
        cls.comparison = evaluation.build_comparison(cls.baseline, cls.auto)

    def test_case_alignment_is_independent_of_row_order(self):
        comparison = evaluation.build_comparison(self.baseline, self.auto.iloc[::-1])
        pd.testing.assert_frame_equal(self.comparison, comparison)
        pd.testing.assert_frame_equal(
            comparison[self.baseline.columns], self.baseline,
        )

    def test_duplicate_case_is_rejected(self):
        repeated = pd.concat([self.baseline, self.baseline.iloc[[0]]], ignore_index=True)
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            evaluation.build_comparison(repeated, self.auto)

    def test_missing_case_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "case_id sets differ"):
            evaluation.build_comparison(self.baseline, self.auto.iloc[1:])

    def test_missing_required_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing column"):
            evaluation.build_comparison(self.baseline, self.auto.drop(columns="auto_long_duration_count"))

    def test_incomplete_baseline_snapshot_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing column"):
            evaluation.build_comparison(self.baseline, self.auto.drop(columns="isolation_anomaly_score"))

    def test_misaligned_snapshot_is_rejected(self):
        # Rearrange existing values only in memory to simulate a broken upstream join.
        misaligned = self.auto.copy()
        misaligned["isolation_anomaly_score"] = misaligned["isolation_anomaly_score"].iloc[::-1].to_numpy()
        with self.assertRaisesRegex(ValueError, "different baseline snapshot"):
            evaluation.build_comparison(self.baseline, misaligned)

    def test_cross_tab_and_variant_totals_reconcile(self):
        cross = evaluation.build_cross_tab(self.comparison)
        variants = evaluation.summarize_groups(self.comparison, ["variant"])
        self.assertEqual(len(cross), 9)
        self.assertEqual(cross.case_count.sum(), len(self.baseline))
        self.assertEqual(variants.case_count.sum(), len(self.baseline))
        for label in evaluation.AUTO_LABELS:
            expected = self.auto.auto_validation_label.eq(label).sum()
            self.assertEqual(cross.loc[cross.auto_validation_label.eq(label), "case_count"].sum(), expected)
            self.assertEqual(variants[f"auto_{label.lower()}_cases"].sum(), expected)
        self.assertEqual(variants.baseline_anomaly_cases.sum(), self.baseline.anomaly_flag.sum())
        self.assertEqual(variants.label_disagreement_cases.sum(), (~self.comparison.aligned_label_match).sum())

    def test_detector_overlaps_use_case_sets(self):
        summary = evaluation.build_detector_comparison(self.comparison)
        auto_ids = set(self.auto.loc[self.auto.auto_validation_label.eq("Anomaly"), "case_id"])
        for detector, column in evaluation.DETECTORS.items():
            row = summary.loc[summary.detector.eq(detector)].iloc[0]
            detector_ids = set(self.baseline.loc[self.baseline[column].eq(1), "case_id"])
            self.assertEqual(row.both_anomaly, len(detector_ids & auto_ids))
            self.assertEqual(row.detector_only_anomaly, len(detector_ids - auto_ids))
            self.assertEqual(row.auto_only_anomaly, len(auto_ids - detector_ids))
            self.assertEqual(row.neither_anomaly, len(self.baseline) - len(detector_ids | auto_ids))

    def test_evidence_counts_preserve_suppressed_explanation_tags(self):
        summary = evaluation.build_evidence_summary(self.comparison).set_index(["evidence_source", "evidence"])
        signals = {
            "any_long_duration": "auto_long_duration_count",
            "severe_long_duration": "auto_severe_long_duration_count",
            "near_long_duration_review": "auto_near_long_duration_review_count",
        }
        for signal, column in signals.items():
            self.assertEqual(summary.loc[("computed_signal", signal), "case_count"], self.auto[column].gt(0).sum())
        for tag in evaluation.EVIDENCE_TAGS:
            expected = sum(tag in {part.strip() for part in value.split(";")}
                           for value in self.auto.auto_validation_evidence)
            self.assertEqual(summary.loc[("recorded_tag", tag), "case_count"], expected)
        for row in summary.itertuples():
            self.assertEqual(row.case_count, row.auto_normal_cases + row.auto_suspicious_cases + row.auto_anomaly_cases)

    def test_disagreements_reconcile_and_empty_groups_are_supported(self):
        summary = evaluation.build_disagreement_summary(self.comparison)
        self.assertEqual(summary.case_count.sum(), (~self.comparison.aligned_label_match).sum())
        agreement_only = self.comparison.loc[self.comparison.aligned_label_match]
        self.assertTrue(evaluation.build_disagreement_summary(agreement_only).empty)
        normal = self.comparison.loc[self.comparison.anomaly_vote_count.eq(0)
                                     & self.comparison.auto_validation_label.eq("Normal")]
        self.assertEqual(evaluation.build_cross_tab(normal).case_count.sum(), len(normal))
        self.assertTrue(evaluation.build_detector_comparison(normal).anomaly_jaccard.isna().all())


if __name__ == "__main__":
    unittest.main()
