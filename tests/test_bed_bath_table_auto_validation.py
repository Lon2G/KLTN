"""Auto Validation checks on actual Olist records, never fabricated review labels."""

from itertools import combinations
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/03_anomaly_detection"))
import auto_validate_bed_bath_table as a


class BedBathTableAutoValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        (cls.context, cls.ledger, cls.thresholds, cls.candidates, cls.flags,
         cls.old, cls.hashes, cls.source) = a.load_inputs()
        cls.frames, cls.summary = a.build_outputs(cls.context, cls.ledger, cls.thresholds,
                                                  cls.candidates, cls.flags, cls.old)
        cls.cases = cls.frames["case_results.csv"]
        cls.evidence = cls.frames["rule_evidence.csv"]

    def test_scope_reconciles_and_original_facts_are_unchanged(self):
        self.assertEqual(len(self.cases), 7333)
        self.assertEqual(self.cases.split.value_counts().to_dict(), {"train": 5480, "validation": 1853})
        expected = self.context.sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.cases[expected.columns], expected, check_dtype=False)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertEqual(int(self.cases.timing_rules_available.sum()), 6601)
        self.assertEqual(int(self.cases.model_signals_available.sum()), 2935)
        self.assertEqual(self.frames["action_summary.csv"].case_count.sum(), len(self.cases))
        self.assertFalse(self.summary["test_scored"])

    def test_loading_never_parses_test_matrix_or_optional_blind_review(self):
        original = pd.read_csv

        def guarded(path, *args, **kwargs):
            forbidden = ["test_timing_features.csv", "review_cases_blind.csv", "review_progress.csv"]
            self.assertFalse(any(name in str(path) for name in forbidden))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=guarded):
            loaded = a.load_inputs()
        self.assertEqual(set(loaded[0].order_id), set(self.cases.order_id))

    def test_thresholds_match_actual_inner_fit_and_not_validation(self):
        fit_ids = self.ledger.loc[self.ledger.inner_role.eq("fit"), "order_id"]
        fit = self.context.set_index("order_id").loc[fit_ids]
        self.assertEqual(len(fit), 3190)
        self.assertTrue(fit[a.TIMES].lt(pd.Timestamp(a.benchmark.FIT_END)).all().all())
        for row in self.thresholds.itertuples(index=False):
            q01, q25, q75 = np.quantile(fit[row.feature], [.01, .25, .75])
            self.assertAlmostEqual(row.q01_days, q01)
            self.assertAlmostEqual(row.long_threshold_days, q75 + 1.5*(q75-q25))
            self.assertAlmostEqual(row.severe_long_threshold_days, q75 + 3*(q75-q25))

    def test_all_reversed_pairs_have_exact_observed_evidence(self):
        expected = []
        for row in self.context.to_dict("records"):
            for start, end in combinations(a.TIMES, 2):
                if pd.notna(row[start]) and pd.notna(row[end]) and row[end] < row[start]:
                    expected.append((row["order_id"], f"{start} -> {end}", (row[end]-row[start]).total_seconds()/86400))
        actual = self.evidence.loc[self.evidence.reason_code.eq("RECORDED_TIMELINE_REVERSAL")]
        self.assertEqual(set(actual[["order_id", "feature", "observed_days"]].itertuples(index=False, name=None)), set(expected))
        self.assertEqual(actual.order_id.nunique(), 59)
        self.assertTrue(self.cases.source_record_issue.eq(self.cases.source_verification_priority).all())
        self.assertTrue(self.cases.loc[self.cases.source_record_issue, "auto_action"].eq("verify_source_record").all())

    def test_status_context_does_not_turn_missing_events_into_anomalies(self):
        incomplete = self.cases.usage_group.isin(["canceled_or_unavailable_incomplete", "nonfinal_status_cutoff_unknown"])
        self.assertEqual(int(incomplete.sum()), 118)
        self.assertTrue(self.cases.loc[incomplete, "auto_action"].eq("insufficient_context").all())
        self.assertFalse(self.cases.loc[incomplete, "source_record_issue"].any())
        self.assertFalse(self.cases.ongoing_age_computable.any())
        for row in self.cases.loc[incomplete].itertuples(index=False):
            codes = json.loads(row.reason_codes_json)
            code = "INCOMPLETE_CLOSED_STATUS" if row.order_status in ["canceled", "unavailable"] else "NONFINAL_CUTOFF_UNKNOWN"
            self.assertIn(code, codes)

    def test_unavailable_flags_are_blank_and_late_events_not_compared(self):
        excluded = ~self.cases.timing_rules_available
        self.assertEqual(int(excluded.sum()), 732)
        self.assertTrue(self.cases.loc[excluded, a.TIMING_FLAGS].isna().all().all())
        late = self.cases.timing_input_reason.eq("completion_not_before_partition_cutoff")
        self.assertEqual(int(late.sum()), 555)
        self.assertTrue(self.cases.loc[late, "auto_action"].eq("insufficient_context").all())
        self.assertFalse(self.evidence.loc[self.evidence.reason_code.isin(["LONG_DURATION", "SHORT_DURATION_WARNING", "ZERO_DURATION_WARNING"]), "order_id"].isin(self.cases.loc[excluded, "order_id"]).any())
        self.assertTrue(self.cases.loc[~self.cases.model_signals_available, "model_candidate_signal"].isna().all())

    def test_timing_rules_and_zero_short_semantics_match_formulas(self):
        cases = self.cases.loc[self.cases.timing_rules_available]
        thresholds = self.thresholds.set_index("feature").loc[a.FEATURES]
        values = cases[a.FEATURES].to_numpy()
        expected_long = (values > thresholds.long_threshold_days.to_numpy()).any(axis=1)
        expected_severe = (values > thresholds.severe_long_threshold_days.to_numpy()).any(axis=1)
        expected_short = ((values < thresholds.q01_days.to_numpy()) | (values == 0)).any(axis=1)
        np.testing.assert_array_equal(cases.long_duration_warning, expected_long)
        np.testing.assert_array_equal(cases.severe_long_duration_warning, expected_severe)
        np.testing.assert_array_equal(cases.short_duration_warning, expected_short)
        self.assertGreater(int(cases.zero_duration_warning.sum()), 0)
        self.assertFalse((cases.severe_long_duration_warning & ~cases.long_duration_warning).any())
        short_only = cases.short_duration_warning & ~cases.long_duration_warning & ~cases.late_delivery_warning
        self.assertGreater(int(short_only.sum()), 0)
        self.assertTrue(cases.loc[short_only, "auto_action"].eq("inspect_timing_warning").all())
        self.assertNotIn("anomaly_label", self.cases.columns)
        for row in self.evidence.loc[self.evidence.reason_code.eq("SHORT_DURATION_WARNING")].itertuples(index=False):
            self.assertGreater(row.observed_days, 0)
            self.assertLess(row.observed_days, row.threshold_days)

    def test_calendar_lateness_is_not_clock_time_lateness(self):
        rows = self.cases.loc[self.cases.timing_rules_available]
        dates = (rows.order_delivered_customer_date.dt.normalize()-rows.order_estimated_delivery_date.dt.normalize()).dt.days
        np.testing.assert_array_equal(rows.late_calendar_days, dates)
        np.testing.assert_array_equal(rows.late_delivery_warning, dates.gt(0))
        same_day = dates.eq(0)
        self.assertGreater(int(same_day.sum()), 0)
        self.assertFalse(rows.loc[same_day, "late_delivery_warning"].any())

    def test_model_signals_reuse_exact_candidates_without_scoring_fit(self):
        flags = pd.concat(self.flags.values()).set_index("order_id")
        rows = self.cases.set_index("order_id").loc[flags.index]
        names = self.candidates.loc[self.candidates.family.isin(a.ML_FAMILIES), "candidate"]
        np.testing.assert_array_equal(rows.model_candidate_signal, flags[names].any(axis=1))
        fit = self.cases.benchmark_role.eq("fit")
        self.assertEqual(int(fit.sum()), 3190)
        self.assertFalse(self.cases.loc[fit, "model_signals_available"].any())
        for row in rows.itertuples():
            original = flags.loc[row.Index]
            self.assertEqual(json.loads(row.positive_candidate_ids_json), original.index[original].tolist())
            support = json.loads(row.candidate_support_by_family_json)
            self.assertEqual(sum(count["evaluated"] for count in support.values()), 120)
            self.assertEqual(sum(count["flagged"] for count in support.values()), int(original.sum()))

    def test_rule_overlap_is_not_an_accuracy_metric(self):
        overlap = self.frames["detector_rule_overlap.csv"]
        self.assertEqual(len(overlap), 120*3)
        self.assertTrue(overlap.compared_validation_cases.eq(1523).all())
        cells = overlap[["both_flag", "detector_only", "rule_only", "neither_flag"]]
        self.assertTrue(cells.sum(axis=1).eq(overlap.compared_validation_cases).all())
        for forbidden in ["accuracy", "precision", "recall", "f1", "anomaly_probability", "reviewer_label"]:
            self.assertNotIn(forbidden, overlap.columns)
            self.assertNotIn(forbidden, self.cases.columns)
        self.assertIsNone(self.summary["selected_candidate"])
        self.assertFalse(self.summary["accuracy_computed"])
        self.assertFalse(self.summary["anomaly_probabilities_computed"])

    def test_existing_manual_reference_is_deduplicated_without_relabeling(self):
        reference = self.frames["human_reference_comparison.csv"]
        self.assertEqual(len(reference), 71)
        self.assertEqual(int(reference.reference_row_count.sum()), 100)
        self.assertEqual(int(reference.in_development_scope.sum()), 5)
        original = self.old.groupby("case_id").reviewer_label.first()
        self.assertEqual(reference.set_index("order_id").human_reference_label.to_dict(), original.to_dict())
        for row in reference.itertuples(index=False):
            source = self.old.loc[self.old.case_id.eq(row.order_id)]
            for field in ["reviewer_confidence", "reviewer_reason", "reviewer_notes"]:
                self.assertEqual(json.loads(getattr(row, f"human_{field}_values_json")), sorted(source[field].unique().tolist()))
        self.assertTrue(reference.loc[~reference.in_development_scope, "auto_action"].isna().all())
        self.assertFalse(self.summary["human_labels_created"])

    def test_monthly_counts_and_reason_lists_reconcile(self):
        monthly = self.frames["monthly_warning_summary.csv"]
        self.assertEqual(int(monthly.all_cases.sum()), len(self.cases))
        self.assertEqual(int(monthly.model_available.sum()), 2935)
        np.testing.assert_allclose(monthly.long_warning_fraction_of_timing_available,
                                   monthly.long_warnings/monthly.timing_available)
        for row in self.cases.itertuples(index=False):
            actual = self.evidence.loc[self.evidence.order_id.eq(row.order_id), "reason_code"].unique().tolist()
            self.assertEqual(json.loads(row.reason_codes_json), actual)
        self.assertTrue(set(self.frames["real_case_examples.csv"].order_id).issubset(self.context.order_id))

    def test_case_order_does_not_change_results(self):
        cases, evidence = a.validate_cases(self.context.iloc[::-1], self.ledger, self.thresholds, self.candidates, self.flags)
        pd.testing.assert_frame_equal(cases, self.cases)
        pd.testing.assert_frame_equal(evidence, self.evidence)

    def test_repeated_real_record_is_rejected(self):
        duplicated = pd.concat([self.context, self.context.iloc[:1]])
        with self.assertRaisesRegex(ValueError, "unique"):
            a.validate_context(duplicated)

    def test_real_status_conflicts_and_missing_delivered_milestones(self):
        # These rare branches use actual Olist cases outside the primary category, only in this unit test.
        usage = a.previous.bed.usage
        manifest = usage.verify_snapshot(usage.clean.DEFAULT_OUTPUT)
        observed = usage.read_snapshot_table(usage.clean.DEFAULT_OUTPUT, "order_quality.csv", manifest)
        source = usage.build_case_policy(observed)
        in_period = source.order_purchase_timestamp.ge(pd.Timestamp("2017-03-01")) & source.order_purchase_timestamp.lt(pd.Timestamp("2018-06-01"))
        missing_delivered = source.order_status.eq("delivered") & source.missing_milestone_count.gt(0)
        selected = source.loc[in_period & (source.status_delivery_conflict | missing_delivered)].copy()
        self.assertGreater(len(selected), 0)
        selected["split"] = np.where(selected.order_purchase_timestamp.lt(pd.Timestamp("2018-03-01")), "train", "validation")
        selected["event_time_cutoff_exclusive"] = pd.to_datetime(np.where(selected.split.eq("train"), "2018-03-01", "2018-06-01"))
        selected["timing_input_eligible"] = False
        selected["timing_input_reason"] = "timing_ineligible:" + selected.usage_group
        flags = {name: values.iloc[:0] for name, values in self.flags.items()}
        cases, evidence = a.validate_cases(selected, self.ledger.iloc[:0], self.thresholds, self.candidates, flags)
        self.assertTrue(cases.auto_action.eq("verify_source_record").all())
        self.assertEqual(set(evidence.loc[evidence.reason_code.eq("STATUS_DELIVERY_CONFLICT"), "order_id"]),
                         set(selected.loc[selected.status_delivery_conflict, "order_id"]))
        self.assertEqual(set(evidence.loc[evidence.reason_code.eq("DELIVERED_MISSING_MILESTONE"), "order_id"]),
                         set(selected.loc[selected.order_status.eq("delivered") & selected.missing_milestone_count.gt(0), "order_id"]))
        self.assertTrue(cases[a.TIMING_FLAGS].isna().all().all())

    def test_stale_parent_hashes_prevent_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "auto"
            with self.assertRaisesRegex(ValueError, "Inputs changed"):
                a.write_outputs(self.frames, self.summary, {**self.hashes, "human_reference": "stale"}, self.source,
                                a.benchmark.DEFAULT_OUTPUT, a.DEFAULT_REFERENCE, output)
            self.assertFalse(output.exists())

    def test_publication_is_versioned_hashed_and_preserves_inputs(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "auto"
            a.write_outputs(self.frames, self.summary, self.hashes, self.source,
                            a.benchmark.DEFAULT_OUTPUT, a.DEFAULT_REFERENCE, output)
            manifest = json.loads((output / "manifest.json").read_text())
            self.assertEqual(manifest["source_hashes"], self.hashes)
            for name, digest in manifest["output_hashes"].items():
                self.assertEqual(a.audit.file_hash(output / name), digest)
            restored = pd.read_csv(output / "case_results.csv", dtype={name: "boolean" for name in a.TIMING_FLAGS})
            self.assertTrue(restored.loc[~restored.timing_rules_available, a.TIMING_FLAGS].isna().all().all())
            with self.assertRaises(FileExistsError):
                a.write_outputs(self.frames, self.summary, self.hashes, self.source,
                                a.benchmark.DEFAULT_OUTPUT, a.DEFAULT_REFERENCE, output)
            self.assertEqual(a.input_hashes(a.benchmark.DEFAULT_OUTPUT, a.DEFAULT_REFERENCE, self.source), self.hashes)


if __name__ == "__main__":
    unittest.main()
