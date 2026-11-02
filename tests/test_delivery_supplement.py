"""Supplementary case selection checked against the existing real Olist data."""

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
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import prepare_delivery_supplement as supplement


class DeliverySupplementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("No model weights")), \
                patch.object(supplement.performance.inspection.integration.delivery, "fit_entry", side_effect=AssertionError("No training")):
            cls.inputs, cls.provenance = supplement.load_inputs()
            cls.frames, cls.summary = supplement.build_tables(cls.inputs)
        cls.cases = cls.frames["case_evidence.csv"]
        cls.stages = cls.frames["stage_comparisons.csv"]
        cls.audit = cls.frames["candidate_audit.csv"]
        cls.reference = cls.frames["reference_members.csv"]
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"supplement"
        supplement.write_snapshot(cls.frames, cls.summary, cls.provenance, cls.output)

    def test_exact_sample_and_non_overlapping_groups(self):
        self.assertEqual(len(self.cases), 40)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertTrue(self.cases.review_id.is_unique)
        self.assertEqual(self.cases.selection_group.value_counts().to_dict(), {key: 10 for key in supplement.GROUP_NAMES})
        self.assertFalse(any(self.summary["group_shortfalls"].values()))
        self.assertEqual(self.audit.selected.sum(), 40)

    def test_real_category_validation_only_and_entire_pilot_excluded(self):
        performance = supplement.performance
        source = self.inputs["case_evidence.csv"].set_index("order_id")
        self.assertTrue(source.loc[self.cases.order_id].product_category_name.eq("cama_mesa_banho").all())
        self.assertTrue(self.cases.split.eq("validation").all())
        self.assertTrue(set(self.cases.order_id).issubset(self.inputs["order_performance.csv"].order_id))
        batch = Path(self.provenance["inspection"]["pilot_directory"])
        pm = supplement.review.read_json(batch/"analyst_only/manifest.json")
        protected = supplement.shared.read_table(batch, "analyst_only/selection_key.csv", pm)
        self.assertEqual(len(protected), 120)
        self.assertFalse(self.audit.order_id.isin(protected.order_id).any())
        self.assertFalse(self.reference.order_id.isin(protected.order_id).any())
        boundary, _ = performance.load_boundary()
        self.assertTrue(self.cases[performance.TIMES[0]].lt(boundary).all())

    def test_no_new_fit_or_final_test_feature_read(self):
        for path in self.reads:
            self.assertNotIn("test_timing", path)
            self.assertNotIn("partition_plan.csv", path)
        for field in ["model_trained", "detector_threshold_changed", "model_selected", "test_scored", "split_activated",
                      "anomaly_accuracy_computed", "human_review_performed", "independent_reference_ready", "external_data_imported"]:
            self.assertFalse(self.summary[field])

    def test_candidate_milestones_visible_and_consistent(self):
        for field in supplement.performance.TIMES:
            self.assertTrue(self.cases[field].notna().all())
            self.assertTrue(self.cases[field].lt(self.cases.visible_before_exclusive).all())
        self.assertFalse(self.cases.quality_conflict.any())
        self.assertTrue(self.cases.performance_eligible.all())
        self.assertTrue(self.cases.post_cutoff_milestones.eq(0).all())
        for row in self.stages.itertuples(index=False):
            self.assertEqual(row.duration_days, (row.end_timestamp-row.start_timestamp).total_seconds()/86400)

    def test_training_reference_is_earlier_and_disjoint(self):
        self.assertTrue(self.reference.split.eq("train").all())
        self.assertTrue(self.reference.end_timestamp.lt(pd.Timestamp(self.summary["reference_end_before"])).all())
        self.assertTrue(self.reference.end_timestamp.lt(self.reference.visible_before_exclusive).all())
        self.assertFalse(self.reference.order_id.isin(self.audit.order_id).any())
        self.assertTrue(self.reference.duration_eligible.all())
        self.assertFalse(self.reference.duplicated(["order_id", "stage"]).any())

    def test_context_statistics_recomputed_independently(self):
        for row in self.stages.itertuples(index=False):
            context = supplement.json.loads(row.reference_context_json)
            if not row.reference_n:
                self.assertTrue(pd.isna(row.empirical_percentile))
                continue
            ref = self.reference.loc[self.reference.stage.eq(row.stage)]
            for field, value in context.items():
                ref = ref.loc[ref[field].eq(value)]
            if "distance_band" in context:
                ref = ref.loc[ref.all_pairs_quality_eligible]
            values = ref.duration_days.to_numpy()
            self.assertEqual(len(values), row.reference_n)
            self.assertGreaterEqual(len(values), supplement.protocol()["minimum_reference_orders"])
            self.assertEqual(float(np.median(values)), row.reference_median_days)
            self.assertEqual(float(np.quantile(values, .95)), row.reference_p95_days)
            self.assertEqual(float((values <= row.duration_days).mean()), row.empirical_percentile)

    def test_context_fallback_is_explicit_and_does_not_invent_distance(self):
        indexed = self.cases.set_index("order_id")
        for row in self.stages.itertuples(index=False):
            context = supplement.json.loads(row.reference_context_json)
            case = indexed.loc[row.order_id]
            self.assertEqual(row.reference_distance_matched, "distance_band" in context)
            for field, value in context.items():
                self.assertEqual(value, case[field])
            if row.reference_distance_matched:
                self.assertTrue(case.all_pairs_quality_eligible)
                self.assertNotEqual(case.distance_band, "unavailable")
        self.assertTrue(self.stages.reference_level.gt(0).any())

    def test_too_few_real_reference_rows_stay_unknown(self):
        comparisons = supplement.compare_stages(self.cases.head(1), self.inputs["stage_durations.csv"], self.reference.head(5))
        self.assertTrue(comparisons.reference_n.eq(0).all())
        self.assertTrue(comparisons.empirical_percentile.isna().all())
        self.assertTrue(comparisons.reference_median_days.isna().all())
        self.assertTrue(comparisons.reference_status.eq("insufficient_reference").all())
        self.assertFalse(comparisons.reference_distance_matched.any())

    def test_absent_saved_result_is_not_a_no_warning_case(self):
        missing = self.audit.loc[self.audit.saved_configurations_evaluated.eq(0)]
        self.assertGreater(len(missing), 0)
        self.assertTrue(missing.saved_long_warnings.isna().all())
        self.assertFalse(missing.eligible_comparison.any())
        self.assertFalse(missing.eligible_saved_model_disagreement.any())

    def test_same_target_model_disagreement_not_missing_or_cross_profile_vote(self):
        models = self.frames["saved_model_evidence.csv"]
        evaluated = models.loc[models.outcome_available_for_saved_comparison]
        actual = self.stages.loc[self.stages.stage.eq("transport")].set_index("order_id").duration_days
        np.testing.assert_allclose(evaluated[supplement.TARGET], evaluated.order_id.map(actual), atol=1e-12, rtol=0)
        for row in self.cases.itertuples(index=False):
            group = evaluated.loc[evaluated.order_id.eq(row.order_id)]
            self.assertEqual(row.saved_configurations_evaluated, len(group))
            if len(group):
                self.assertEqual(row.saved_long_warnings, int(group.long_warning.sum()))
            else:
                self.assertTrue(pd.isna(row.saved_long_warnings))
        selected = self.cases.loc[self.cases.selection_group.eq("saved_model_disagreement")]
        self.assertTrue(selected.saved_long_warnings.gt(0).all())
        self.assertTrue(selected.saved_long_warnings.lt(selected.saved_configurations_evaluated).all())

    def test_selection_is_reproducible_and_independent_of_row_order(self):
        fields = ["review_id", "order_id", "selection_group"]
        expected = self.cases[fields]
        pd.testing.assert_frame_equal(supplement.choose_cases(self.audit), expected, check_dtype=False, check_exact=True)
        pd.testing.assert_frame_equal(supplement.choose_cases(self.audit.iloc[::-1]), expected, check_dtype=False, check_exact=True)

    def test_no_fabrication_when_real_subset_has_too_few_candidates(self):
        subset = self.audit.iloc[:3]
        chosen = supplement.choose_cases(subset)
        self.assertLessEqual(len(chosen), 3)
        self.assertTrue(set(chosen.order_id).issubset(subset.order_id))
        empty = supplement.choose_cases(self.audit.iloc[:0])
        self.assertTrue(empty.empty)

    def test_groups_have_their_declared_selection_properties(self):
        for row in self.cases.itertuples(index=False):
            audit = self.audit.set_index("order_id").loc[row.order_id]
            self.assertTrue(audit["eligible_"+row.selection_group])
        late = self.cases.loc[self.cases.selection_group.eq("late_promise")]
        self.assertTrue(late.promise_delta_calendar_days.gt(0).all())
        long = self.cases.loc[self.cases.selection_group.eq("long_stage_on_time")]
        self.assertTrue(long.promise_delta_calendar_days.le(0).all())
        for order_id in long.order_id:
            group = self.stages.loc[self.stages.order_id.eq(order_id)]
            self.assertTrue((group.reference_distance_matched & group.empirical_percentile.gt(.95)).any())
        controls = self.cases.loc[self.cases.selection_group.eq("comparison")]
        self.assertTrue(controls.complete_saved_comparison.all())
        self.assertTrue(controls[["saved_long_warnings", "saved_short_warnings", "saved_zero_warnings"]].eq(0).all().all())

    def test_source_values_and_exact_money_preserved(self):
        original = self.inputs["case_evidence.csv"].set_index("order_id")
        actual = self.cases.set_index("order_id")[supplement.CONTEXT_FIELDS]
        expected = original.loc[actual.index, supplement.CONTEXT_FIELDS]
        pd.testing.assert_frame_equal(actual, expected, check_exact=True, check_index_type=False)
        payments = self.frames["payment_records.csv"]
        items = self.frames["item_records.csv"]
        for table, count in [(payments, "payment_record_count"), (items, "item_count")]:
            counts = table.groupby("order_id").size()
            np.testing.assert_array_equal(counts.reindex(self.cases.order_id), self.cases[count])
        totals = payments.groupby("order_id").payment_value_minor_units.sum(min_count=1)
        np.testing.assert_array_equal(totals.reindex(self.cases.order_id), self.cases.payment_value_sum_minor_units)

    def test_every_child_row_belongs_to_selected_real_case(self):
        for name in ["stage_comparisons.csv", "saved_model_evidence.csv", "payment_records.csv", "item_records.csv", "assessment_status.csv"]:
            frame = self.frames[name]
            self.assertTrue(set(frame.order_id).issubset(self.cases.order_id), name)
            mapping = self.cases.set_index("order_id").review_id
            self.assertTrue(frame.review_id.eq(frame.order_id.map(mapping)).all(), name)
        self.assertEqual(len(self.stages), 3*len(self.cases))

    def test_no_reference_labels_or_fake_certainty(self):
        status = self.frames["assessment_status.csv"]
        self.assertTrue(status.broad_anomaly_status.eq("unresolved").all())
        self.assertTrue(status.confirmed_bottleneck.eq("unresolved").all())
        self.assertTrue(status.data_error_or_valid_deviation.eq("unresolved").all())
        self.assertFalse(status.human_review_performed.any())
        self.assertFalse(status.independent_reference.any())
        self.assertEqual(self.summary["reference_labels_created"], 0)
        for frame in self.frames.values():
            self.assertFalse({"anomaly_probability", "reviewer_name", "advisor_approved", "final_verdict"}.intersection(frame.columns))

    def test_snapshot_roundtrip_and_report_reconstruction(self):
        manifest = supplement.load_snapshot(self.output)
        restored = {name: supplement.shared.read_table(self.output, name, manifest) for name in supplement.TABLES}
        for name, frame in self.frames.items():
            pd.testing.assert_frame_equal(restored[name], frame, check_dtype=False, check_exact=True)
        self.assertEqual((self.output/"bao_cao_doi_chieu.md").read_text(), supplement.render_report(restored, self.summary))

    def test_overwrite_and_protected_destinations_refused(self):
        with self.assertRaises(FileExistsError):
            supplement.write_snapshot(self.frames, self.summary, self.provenance, self.output)
        for output in [ROOT/"data/raw/supplement", supplement.SOURCE/"supplement", ROOT/"models/supplement"]:
            with self.assertRaises(ValueError):
                supplement.write_snapshot(self.frames, self.summary, self.provenance, output)

    def test_modified_payload_is_rejected(self):
        damaged = Path(self.temp.name)/"damaged"
        shutil.copytree(self.output, damaged)
        with (damaged/"case_evidence.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaises(ValueError):
            supplement.load_snapshot(damaged)


if __name__ == "__main__":
    unittest.main()
