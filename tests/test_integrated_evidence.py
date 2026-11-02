"""Integration invariants on saved real Olist observations only."""

from collections import Counter
from copy import deepcopy
from itertools import combinations
from pathlib import Path
import json
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
import integrate_process_evidence as integration


class IntegratedEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reads = []
        original = pd.read_csv

        def read(path, *args, **kwargs):
            cls.reads.append(str(path))
            return original(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read), \
                patch.object(joblib, "load", side_effect=AssertionError("Model loading forbidden")), \
                patch.object(integration.delivery, "fit_entry", side_effect=AssertionError("Training forbidden")):
            cls.inputs, cls.provenance = integration.load_inputs()
            cls.frames, cls.summary = integration.build_integration(cls.inputs)
        cls.cases = cls.frames["case_evidence.csv"]
        cls.process = cls.frames["process_cases.csv"]
        cls.milestones = cls.frames["milestone_evidence.csv"]
        cls.events = cls.frames["visible_events.csv"]
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"integration"
        cls.saved = integration.write_integration(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.manifest = integration.load_snapshot(cls.output)

    def test_every_real_development_order_retained_once(self):
        self.assertEqual(len(self.cases), 7333)
        self.assertTrue(self.cases.order_id.is_unique)
        self.assertEqual(self.cases.split.value_counts().to_dict(), {"train": 5480, "validation": 1853})
        self.assertEqual(set(self.cases.order_id), set(self.inputs["context"].order_id))
        self.assertEqual(self.saved["reserved_test_orders_excluded"], 1691)
        self.assertEqual(self.frames["coverage.csv"].orders.sum(), 7333)

    def test_no_test_matrix_labels_or_old_global_event_log_read(self):
        for path in self.reads:
            self.assertFalse("test_timing" in path or "data/manual_review" in path or "review_progress" in path, path)
            self.assertFalse(path.endswith("data/processed/event_log.csv"), path)
        self.assertNotIn("test", set(self.cases.split))

    def test_original_context_values_preserved_exactly(self):
        original = self.inputs["context"].sort_values("order_id").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.cases[original.columns], original, check_dtype=False, check_exact=True)

    def test_original_business_and_geographic_values_preserved(self):
        actual = self.cases.set_index("order_id")
        for source_name, fields in [("features", ["payment_value_sum_minor_units", "item_price_sum_minor_units", "freight_sum_minor_units",
                                                 "customer_prior_orders_in_scope", "customer_days_since_prior_purchase"]),
                                    ("ledger", [*integration.delivery.context.DISTANCES, "geography_quality_reasons", "all_pairs_quality_eligible"])]:
            expected = self.inputs[source_name].set_index("order_id").loc[actual.index, fields]
            pd.testing.assert_frame_equal(actual[fields], expected, check_dtype=False, check_index_type=False, check_exact=True)

    def test_four_source_milestone_records_per_order_without_invention(self):
        self.assertEqual(len(self.milestones), 4*7333)
        self.assertFalse(self.milestones.duplicated(["order_id", "activity"]).any())
        source = self.inputs["context"].set_index("order_id")
        for field, group in self.milestones.groupby("source_column"):
            expected = source.loc[group.order_id, field].reset_index(drop=True)
            pd.testing.assert_series_equal(group.recorded_timestamp.reset_index(drop=True), expected, check_names=False, check_exact=True)
            np.testing.assert_array_equal(group.clean_order_record, source.loc[group.order_id, "clean_order_record"])

    def test_visible_events_use_strict_original_cutoff(self):
        self.assertEqual(len(self.events), 28510)
        self.assertTrue(self.events.recorded_timestamp.notna().all())
        self.assertTrue(self.events.recorded_timestamp.lt(self.events.event_time_cutoff_exclusive).all())
        expected = self.milestones.loc[self.milestones.recorded_timestamp.notna()
                                       & self.milestones.recorded_timestamp.lt(self.milestones.event_time_cutoff_exclusive)]
        self.assertEqual(set(zip(self.events.order_id, self.events.activity)), set(zip(expected.order_id, expected.activity)))

    def test_missing_and_post_cutoff_events_are_distinct(self):
        missing = self.milestones.loc[self.milestones.evidence_state.eq("source_missing")]
        later = self.milestones.loc[self.milestones.evidence_state.eq("at_or_after_cutoff")]
        self.assertEqual(len(missing), 144)
        self.assertEqual(len(later), 678)
        self.assertTrue(missing.recorded_timestamp.isna().all())
        self.assertTrue(later.recorded_timestamp.ge(later.event_time_cutoff_exclusive).all())
        self.assertTrue((self.process.process_visible_events+self.process.process_missing_source_events+self.process.process_post_cutoff_events).eq(4).all())

    def test_all_pair_chronology_inversions_independently_recomputed(self):
        source = self.inputs["context"].set_index("order_id")
        expected = pd.Series(0, index=source.index)
        for first, second in combinations(integration.TIMES, 2):
            left, right = source[first], source[second]
            expected += (left.lt(source.event_time_cutoff_exclusive) & right.lt(source.event_time_cutoff_exclusive) & right.lt(left)).astype(int)
        actual = self.process.set_index("order_id").process_inverted_pairs.reindex(expected.index)
        np.testing.assert_array_equal(actual, expected)
        self.assertEqual(actual.gt(0).sum(), 59)

    def test_incomplete_or_tied_traces_have_no_fitness_verdict(self):
        self.assertEqual(self.process.process_replay_status.value_counts().to_dict(), {
            "complete_distinct_trace_replayed": 6542, "incomplete_observed_trace_not_scored": 678,
            "equal_time_order_unresolved_not_scored": 113})
        withheld = self.process.loc[self.process.process_visible_events.lt(4) | self.process.process_has_equal_timestamps]
        self.assertTrue(withheld.process_trace_fitness.isna().all())
        self.assertTrue(withheld.process_reference_is_fit.isna().all())
        self.assertEqual(self.process.process_has_equal_timestamps.sum(), 118)

    def test_pm4py_reference_fitness_is_not_anomaly_probability(self):
        scored = self.process.loc[self.process.process_replay_status.eq("complete_distinct_trace_replayed")]
        self.assertTrue(scored.process_trace_fitness.between(0, 1).all())
        ordinary = scored.process_variant.eq(" -> ".join(integration.ACTIVITIES))
        self.assertTrue(scored.loc[ordinary, "process_trace_fitness"].eq(1).all())
        self.assertTrue(scored.loc[ordinary, "process_reference_is_fit"].all())
        self.assertTrue(scored.loc[~ordinary, "process_reference_is_fit"].eq(False).all())
        self.assertNotIn("anomaly_label", self.process.columns)

    def test_process_result_is_independent_of_input_row_order(self):
        shuffled = self.inputs["context"].sample(frac=1, random_state=71)
        actual = integration.process_evidence(shuffled)
        for name, expected in actual.items():
            pd.testing.assert_frame_equal(expected, self.frames[name], check_exact=True)

    def test_signed_stage_durations_preserved_and_unknown_not_zero(self):
        durations = self.frames["process_durations.csv"]
        self.assertEqual(len(durations), 4*7333)
        expected = (durations.end_timestamp-durations.start_timestamp).dt.total_seconds()/86400
        np.testing.assert_allclose(durations.duration_days, expected, rtol=0, atol=0, equal_nan=True)
        missing = durations.duration_state.eq("endpoint_not_visible")
        self.assertTrue(durations.loc[missing, "duration_days"].isna().all())
        self.assertTrue(durations.loc[durations.duration_state.eq("negative_recorded_duration"), "duration_days"].lt(0).all())
        self.assertTrue(durations.loc[durations.duration_state.eq("zero_recorded_duration"), "duration_days"].eq(0).all())
        self.assertNotIn("threshold_days", durations.columns)

    def test_variants_cover_all_cases_and_dfg_matches_observed_adjacencies(self):
        self.assertEqual(self.frames["process_variants.csv"].orders.sum(), 7333)
        tied = set(self.process.loc[self.process.process_has_equal_timestamps, "order_id"])
        expected = Counter()
        for (split, order_id), rows in self.events.groupby(["split", "order_id"]):
            if order_id not in tied:
                sequence = rows.activity.tolist()
                expected.update((split, first, second) for first, second in zip(sequence, sequence[1:]))
        actual = {(row.split, row.start_activity, row.end_activity): row.observed_connections
                  for row in self.frames["process_dfg.csv"].itertuples(index=False)}
        self.assertEqual(dict(expected), actual)

    def test_business_anchor_scores_thresholds_flags_match_saved_study(self):
        table = self.frames["business_anchor_evidence.csv"]
        self.assertEqual(len(table), 3*1523)
        for profile, source in self.inputs["business_tables"].items():
            actual = table.loc[table.profile.eq(profile)].set_index("order_id")
            candidate = source["candidates"].loc[lambda x: x.candidate.eq(integration.ANCHOR)].iloc[0]
            expected = source["scores"].set_index("order_id").loc[actual.index, candidate.score_column]
            np.testing.assert_array_equal(actual.score, expected)
            self.assertTrue(actual.threshold.eq(candidate.threshold).all())
            np.testing.assert_array_equal(actual.warning, source["flags"].set_index("order_id").loc[actual.index, integration.ANCHOR])

    def test_delivery_forecasts_preserve_all_models_and_exact_saved_values(self):
        actual = self.frames["delivery_model_evidence.csv"]
        self.assertEqual(len(actual), 12*1576)
        self.assertEqual(actual.model.nunique(), 12)
        expected = self.inputs["forecasts"]
        pd.testing.assert_frame_equal(actual[expected.columns], expected, check_dtype=False, check_exact=True)

    def test_unobserved_delivery_outcomes_are_not_negative_warnings(self):
        table = self.frames["delivery_model_evidence.csv"]
        unknown = table.loc[~table.outcome_available_for_saved_comparison]
        self.assertEqual(unknown.order_id.nunique(), 200)
        for field in [integration.delivery.context.TARGET, "long_warning", "short_warning", "zero_duration_warning"]:
            self.assertTrue(unknown[field].isna().all())
        known = table.loc[table.outcome_available_for_saved_comparison]
        self.assertEqual(known.order_id.nunique(), 1376)
        np.testing.assert_array_equal(known.long_warning, known[integration.delivery.context.TARGET].gt(known.calibrated_upper_days))

    def test_training_cases_not_scored_here_and_absence_not_normal(self):
        train = self.cases.loc[self.cases.split.eq("train")]
        fields = ["business_profiles_scored", "business_profiles_with_warning", "delivery_models_forecast", "delivery_models_evaluated"]
        self.assertTrue(train[fields].isna().all().all())
        self.assertTrue(train.evidence_codes_json.map(lambda x: "no_validation_model_result_here" in json.loads(x)).all())
        self.assertFalse({"final_label", "anomaly_probability", "priority_score"}.intersection(self.cases.columns))

    def test_per_case_counts_reconcile_without_duplicate_order_rows(self):
        indexed = self.cases.set_index("order_id")
        anchors = self.frames["business_anchor_evidence.csv"]
        counts = anchors.groupby("order_id").warning.sum()
        np.testing.assert_array_equal(indexed.loc[counts.index, "business_profiles_with_warning"], counts)
        forecasts = self.frames["delivery_model_evidence.csv"]
        known = forecasts.loc[forecasts.outcome_available_for_saved_comparison]
        for field in ["long_warning", "short_warning", "zero_duration_warning"]:
            counts = known.groupby("order_id")[field].sum()
            np.testing.assert_array_equal(indexed.loc[counts.index, "delivery_models_"+field], counts)

    def test_seller_pairs_and_purchase_time_boundaries_unchanged(self):
        actual = self.frames["seller_pair_evidence.csv"]
        self.assertEqual(len(actual), 7491)
        pd.testing.assert_frame_equal(actual, self.inputs["seller_pairs"], check_exact=True)
        for field in integration.delivery.seller.TIME_COLUMNS:
            present = actual[field].notna()
            self.assertTrue(actual.loc[present, field].lt(actual.loc[present, "as_of_timestamp"]).all())

    def test_validation_scope_mismatch_and_duplicate_keys_rejected(self):
        inputs = {**self.inputs, "features": self.inputs["features"].iloc[1:]}
        with self.assertRaisesRegex(ValueError, "scope"):
            integration.validate_inputs(inputs)
        inputs = {**self.inputs, "context": pd.concat([self.inputs["context"], self.inputs["context"].iloc[:1]], ignore_index=True)}
        with self.assertRaises(ValueError):
            integration.validate_inputs(inputs)

    def test_exact_csv_roundtrip_and_complete_snapshot(self):
        self.assertEqual(len(list(self.output.iterdir())), 16)
        for name, expected in self.frames.items():
            actual = integration.shared.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)

    def test_output_overwrite_and_tampering_refused(self):
        with self.assertRaises(FileExistsError):
            integration.write_integration(self.frames, self.summary, self.provenance, self.output)
        target = Path(self.temporary.name)/"tampered"
        shutil.copytree(self.output, target)
        with (target/"summary.json").open("a") as stream:
            stream.write("\n")
        with self.assertRaises(ValueError):
            integration.load_snapshot(target)

    def test_changed_source_code_and_partial_publication_refused(self):
        changed = deepcopy(self.provenance)
        changed["code_hashes"] = {}
        with self.assertRaisesRegex(ValueError, "code/protocol"):
            integration.verify_sources(changed)
        target = Path(self.temporary.name)/"incomplete"
        with patch.object(integration, "verify_sources", side_effect=[None, ValueError("Source changed")]):
            with self.assertRaisesRegex(ValueError, "Source changed"):
                integration.write_integration(self.frames, self.summary, self.provenance, target)
        self.assertFalse(target.exists())
        self.assertFalse(list(target.parent.glob(".integrated-evidence-*")))

    def test_no_unsupported_research_or_operational_claims(self):
        for field in ["models_trained", "model_weights_loaded", "thresholds_changed", "test_scored", "human_labels_created",
                      "synthetic_data_used", "probabilities_computed", "accuracy_computed", "winner_selected", "real_time_validated"]:
            self.assertFalse(self.summary[field], field)
        self.assertEqual(len(self.frames["evidence_catalog.csv"]), 7)


if __name__ == "__main__":
    unittest.main()
