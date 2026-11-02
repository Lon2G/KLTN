"""Real frozen Olist evidence; mutations below are test-only fault injections."""

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import joblib
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/"scripts/01_data_preparation"), str(ROOT/"scripts/03_anomaly_detection")]
import analyze_holdout_process_evidence as evidence


class HoldoutProcessEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = Path(os.environ.get("KLTN_HOLDOUT_EVIDENCE_TEST_SNAPSHOT", evidence.DEFAULT_OUTPUT))
        cls.manifest = evidence.load_snapshot(cls.directory)
        cls.inputs, cls.source = evidence.load_inputs()
        cls.frames = {name: evidence.shared.read_table(cls.directory, name, cls.manifest) for name in evidence.TABLES}
        cls.orders, cls.stages = [cls.frames[n] for n in ["order_evidence.csv", "stage_evidence.csv"]]
        cls.reviews, cls.groups = [cls.frames[n] for n in ["review_evidence.csv", "context_groups.csv"]]
        cls.summary = cls.manifest["summary"]

    def test_exact_original_population_and_disagreement_selection(self):
        self.assertEqual(len(self.orders), 1691)
        self.assertEqual(len(self.stages), 5073)
        self.assertTrue(self.stages.groupby("order_id").size().eq(3).all())
        detailed = self.frames["disagreement_orders.csv"]
        expected = self.inputs["order_ledger.csv"].loc[lambda f: f.calendar_proxy_confusion.isin(["FP", "FN"])]
        self.assertEqual(set(detailed.order_id), set(expected.order_id))
        self.assertEqual(detailed.calendar_proxy_confusion.value_counts().to_dict(), {"FP": 59, "FN": 24})
        for name in ["disagreement_stages.csv", "disagreement_reviews.csv"]:
            self.assertTrue(set(self.frames[name].order_id).issubset(detailed.order_id))

    def test_every_original_order_and_stage_column_is_unchanged(self):
        for source_name, actual, keys in [("order_ledger.csv", self.orders, ["order_id"]),
                                         ("stage_scores.csv", self.stages, ["order_id", "stage"])]:
            original = self.inputs[source_name].sort_values(keys).reset_index(drop=True)
            found = actual.sort_values(keys).reset_index(drop=True)[original.columns]
            pd.testing.assert_frame_equal(original, found, check_dtype=False, check_exact=True)
        self.assertEqual(self.summary["parent_metrics_preserved"], self.inputs["parent_summary"]["pooled_metrics"])

    def test_reference_boundary_is_from_frozen_train_members_and_not_test(self):
        reference = self.inputs["reference"]
        for group in self.inputs["reference_groups.csv"].itertuples(index=False):
            ids = json.loads(group.order_ids_json)
            self.assertFalse(set(ids) & set(self.orders.order_id))
            values = reference.loc[reference.stage.eq(group.stage) & reference.order_id.isin(ids)].sort_values("order_id").duration_days.to_numpy(float)
            rows = self.stages.loc[self.stages.selected_reference_id.eq(group.reference_id)]
            boundary = np.sort(values)[(19*len(values))//20]
            self.assertTrue(rows.frozen_upper_tail_boundary_days.eq(boundary).all())
            self.assertEqual(values.mean(), group.mean_days)
            for duration in [np.nextafter(boundary, -np.inf), boundary, np.nextafter(boundary, np.inf)]:
                old_rule = duration > max(0, values.mean()) and 20*(values <= duration).sum() > 19*len(values)
                explanation = duration >= boundary and duration > max(0, values.mean())
                self.assertEqual(old_rule, explanation)

    def test_above_mean_is_not_automatically_warning(self):
        rows = self.stages.loc[self.stages.stage_observation.eq("above_mean_without_tail_warning")]
        self.assertGreater(len(rows), 0)
        self.assertTrue(rows.difference_from_reference_mean_days.gt(0).all())
        self.assertFalse(rows.long_stage_warning.any())
        self.assertTrue(rows.delay_score_0_100.eq(0).all())
        self.assertEqual(self.summary["late_without_warning_but_above_mean"], 21)
        self.assertEqual(self.summary["late_without_warning_all_stages_at_or_below_mean"], 3)

    def test_early_warnings_and_unknowns_remain_separate(self):
        early = self.orders.loc[self.orders.performance_state.eq("early") & self.orders[evidence.parent.SCORE].gt(0)]
        self.assertEqual(len(early), 58)
        self.assertTrue(early.long_warning_stages.gt(0).all())
        unknown = self.stages.loc[~self.stages.assessable]
        self.assertTrue(unknown.long_stage_warning.isna().all())
        self.assertTrue(unknown.stage_observation.eq("unassessed").all())
        self.assertTrue(unknown.difference_from_tail_boundary_days.isna().all())
        self.assertTrue(self.orders.promise_basis_status.eq("unverified_source_method_not_provided").all())

    def test_raw_review_payload_and_all_source_records_preserved(self):
        source = self.inputs["reviews"].sort_values("clean_source_record").reset_index(drop=True)
        actual = self.reviews.sort_values("clean_source_record").reset_index(drop=True)[source.columns]
        pd.testing.assert_frame_equal(source, actual, check_dtype=False, check_exact=True)
        self.assertEqual(len(actual), 1681)
        self.assertEqual(actual.order_id.nunique(), 1680)
        self.assertEqual(actual.clean_source_record.nunique(), len(actual))
        for field in ["review_score_min", "review_score_max"]:
            self.assertEqual(str(self.orders[field].dtype), "Int64")

    def test_multiple_reviews_not_counted_as_multiple_orders(self):
        counts = self.reviews.groupby("order_id").size()
        multiple = counts.loc[counts.gt(1)]
        self.assertEqual(len(multiple), 1)
        oid = multiple.index[0]
        row = self.orders.set_index("order_id").loc[oid]
        self.assertEqual(row.review_records, 2)
        self.assertEqual(len(json.loads(row.review_source_records_json)), 2)
        self.assertEqual(self.orders.order_id.eq(oid).sum(), 1)
        overall = self.frames["review_comparison.csv"].loc[lambda f: f.dimension.eq("overall")].iloc[0]
        self.assertEqual(overall.orders_with_review, 1680)

    def test_missing_review_or_text_never_becomes_negative_label(self):
        missing = self.orders.loc[self.orders.review_records.eq(0)]
        self.assertEqual(len(missing), 11)
        self.assertTrue(missing.review_evidence_status.eq("no_source_review").all())
        self.assertTrue(missing.any_low_score_1_2.isna().all())
        no_text = self.orders.loc[~self.orders.has_usable_text]
        self.assertTrue(no_text.any_delay_or_nonreceipt_wording.isna().all())
        self.assertGreater(len(no_text.loc[no_text.review_records.gt(0)]), 0)

    def test_review_before_delivery_is_preserved_as_retrospective_statement(self):
        rows = self.reviews.loc[self.reviews.answered_before_recorded_delivery.fillna(False)]
        self.assertGreater(len(rows), 0)
        self.assertTrue(rows.valid_review_dates.all())
        self.assertTrue(rows.evidence_status.eq("retrospective_customer_statement_not_verified_cause_or_label").all())

    def test_invalid_missing_review_dates_withheld_not_deleted(self):
        source = self.inputs["reviews"].iloc[:2].copy()
        source.loc[source.index[0], "review_answer_timestamp"] = source.iloc[0].review_creation_date-pd.Timedelta(days=1)
        source.loc[source.index[1], "review_creation_date"] = pd.NaT
        result = evidence.wording.enrich_reviews(source, self.inputs["order_ledger.csv"])
        self.assertEqual(len(result), 2)
        self.assertFalse(result.valid_review_dates.any())
        self.assertFalse(result.score_usable.any())
        self.assertFalse(result.text_usable.any())
        summary = evidence.wording.aggregate_reviews(result, result.order_id.unique())
        self.assertTrue(summary.any_low_score_1_2.isna().all())

    def test_no_reviews_empty_source_supported_without_inventing_evidence(self):
        result = evidence.wording.enrich_reviews(self.inputs["reviews"].iloc[:0], self.inputs["order_ledger.csv"])
        summary = evidence.wording.aggregate_reviews(result, self.orders.order_id)
        self.assertEqual(len(summary), 1691)
        self.assertTrue(summary.review_records.eq(0).all())
        self.assertTrue(summary.any_delay_or_nonreceipt_wording.isna().all())

    def test_literal_screen_records_mixed_wording_not_customer_labels(self):
        actual = self.reviews.loc[self.reviews.order_id.eq("bf6a2df6b944199274254f8bb0159ec3")].iloc[0]
        screen = evidence.wording.screen(actual.review_comment_title, actual.review_comment_message)
        self.assertTrue(screen["delay_wording"])
        self.assertTrue(screen["on_time_or_early_wording"])
        self.assertTrue(screen["mixed_wording"])
        self.assertEqual(actual.review_score, 5)
        # Deliberately modified text tests negation handling only; never exported as research evidence.
        negated = evidence.wording.screen(None, "sem atraso")
        self.assertTrue(negated["negated_delay_wording"])
        self.assertTrue(negated["mixed_wording"])
        self.assertEqual(json.loads(evidence.wording.screen(None, None)["literal_matches_json"]), [])

    def test_review_duplicates_and_out_of_scope_ids_rejected(self):
        source = self.inputs["reviews"].iloc[:1].copy()
        with self.assertRaisesRegex(ValueError, "distinct source"):
            evidence.wording.enrich_reviews(pd.concat([source, source]), self.inputs["order_ledger.csv"])
        source["order_id"] = self.inputs["reference"].iloc[0].order_id
        with self.assertRaisesRegex(ValueError, "outside"):
            evidence.wording.enrich_reviews(source, self.inputs["order_ledger.csv"])

    def test_review_comparison_denominators_are_distinct_valid_orders(self):
        for row in self.frames["review_comparison.csv"].loc[lambda f: f.dimension.eq("calendar_proxy_confusion")].itertuples(index=False):
            orders = self.orders.loc[self.orders.calendar_proxy_confusion.eq(row.group_value)]
            scored, text = orders.loc[orders.usable_score_records.gt(0)], orders.loc[orders.has_usable_text]
            self.assertEqual(row.orders, len(orders))
            self.assertEqual(row.orders_with_usable_text, len(text))
            self.assertEqual(row.orders_with_usable_score, len(scored))
            self.assertAlmostEqual(row.low_score_rate_among_usable_scores, scored.any_low_score_1_2.mean())
            self.assertAlmostEqual(row.wording_rate_among_usable_text, text.any_delay_or_nonreceipt_wording.mean())

    def test_partial_scoring_not_reported_as_clear_negative(self):
        rows = self.orders.loc[self.orders.scored_stages.eq(3) & self.orders[evidence.parent.SCORE].eq(0)].iloc[:1].copy()
        rows["scored_stages"] = 2
        found = evidence.review_comparison(rows).loc[lambda f: f.dimension.eq("stage_warning_status")]
        self.assertEqual(found.group_value.tolist(), ["unassessed_or_incomplete"])

    def test_all_groups_including_unsupported_and_multi_seller_scope(self):
        self.assertEqual(len(self.groups), 490)
        self.assertGreater(self.groups.support_status.eq("insufficient_common_support").sum(), 0)
        for dimension in ["overall", "customer_state", "distance_band", "stage_start_month"]:
            self.assertTrue(self.groups.loc[self.groups.dimension.eq(dimension)].groupby("stage").total_orders.sum().eq(1691).all())
        seller_groups = self.groups.loc[self.groups.dimension.eq("single_seller_id")]
        self.assertTrue(seller_groups.groupby("stage").total_orders.sum().eq(1647).all())
        members = self.frames["group_membership.csv"].loc[lambda f: f.group_id.isin(seller_groups.group_id)]
        scopes = self.orders.set_index("order_id").seller_attribution_scope
        self.assertTrue(members.order_id.map(scopes).eq("single_seller_order").all())

    def test_matched_membership_is_disjoint_complete_and_same_context(self):
        members = self.frames["group_membership.csv"]
        self.assertFalse(members.duplicated(["group_id", "order_id"]).any())
        stages = self.stages.set_index(["order_id", "stage"])
        for row in self.frames["matched_cells.csv"].loc[lambda f: f.included].itertuples(index=False):
            matched = members.loc[members.group_id.eq(row.group_id) & members.cell_id.eq(row.cell_id)]
            target = matched.loc[matched.role.eq("target")]
            comparator = matched.loc[matched.role.eq("comparator")]
            self.assertEqual(len(target), row.target_orders)
            self.assertEqual(len(comparator), row.comparator_orders)
            self.assertGreaterEqual(len(target), 5)
            self.assertGreaterEqual(len(comparator), 5)
            self.assertFalse(set(target.order_id) & set(comparator.order_id))
            data = stages.loc[pd.MultiIndex.from_frame(matched[["order_id", "stage"]])]
            self.assertTrue(data.assessable.all())
            for field, value in json.loads(row.match_context_json).items():
                self.assertTrue(data[field].astype(str).eq(value).all())

    def test_matched_comparator_weighting_and_support_guards(self):
        cells = self.frames["matched_cells.csv"].loc[lambda f: f.included]
        for row in self.groups.itertuples(index=False):
            part = cells.loc[cells.group_id.eq(row.group_id)]
            if part.empty:
                self.assertFalse(row.potential_concentration_for_investigation)
                continue
            n = part.target_orders.sum()
            target = part.target_signals.sum()/n
            control = ((part.target_orders/n)*(part.comparator_signals/part.comparator_orders)).sum()
            self.assertEqual(n, row.matched_target_orders)
            self.assertAlmostEqual(target, row.matched_target_rate)
            self.assertAlmostEqual(control, row.matched_comparator_rate)
            self.assertAlmostEqual(target-control, row.matched_rate_difference)
            self.assertAlmostEqual(n/row.assessed_orders, row.target_match_coverage)
            supported = n >= 30 and part.comparator_orders.sum() >= 30 and n/row.assessed_orders >= .8
            self.assertEqual(supported, row.support_status == "supported_exploratory_comparison")
            self.assertEqual(bool(supported and part.target_signals.sum() >= 5 and target > control), row.potential_concentration_for_investigation)

    def test_actions_are_conditional_and_not_verified_causes(self):
        actions = self.frames["investigation_actions.csv"]
        self.assertEqual(set(actions.group_id), set(self.groups.loc[self.groups.potential_concentration_for_investigation, "group_id"]))
        self.assertTrue(actions.cause_status.eq("unverified").all())
        self.assertFalse(actions.intervention_effect_measured.any())
        self.assertFalse(self.frames["evaluation_contract.csv"].independent_bottleneck_accuracy.any())
        for key in ["raw_data_changed", "models_retrained", "thresholds_changed", "new_reference_labels", "synthetic_data_used",
                    "independent_bottleneck_accuracy_computed", "review_classifier_validated", "causes_verified", "near_real_time_validated", "production_activated"]:
            self.assertFalse(self.summary[key], key)
        self.assertIsNone(self.summary["source_extraction_cutoff"])

    def test_modified_inputs_fail_closed(self):
        for field, value in [("split", "train"), ("calendar_proxy_confusion", "FN")]:
            inputs = self.inputs.copy()
            inputs["order_ledger.csv"] = self.inputs["order_ledger.csv"].copy()
            index = inputs["order_ledger.csv"].loc[lambda f: f.calendar_proxy_confusion.eq("TN")].index[0]
            inputs["order_ledger.csv"].loc[index, field] = value
            with self.assertRaises(ValueError):
                evidence.validate_inputs(inputs)
        inputs = self.inputs.copy()
        inputs["reference_groups.csv"] = self.inputs["reference_groups.csv"].copy()
        inputs["reference_groups.csv"].loc[0, "n"] += 1
        with self.assertRaisesRegex(ValueError, "reference membership"):
            evidence.stage_evidence(inputs)

    def test_reproduction_shuffled_inputs_without_fit_or_labeling(self):
        inputs = {name: value.sample(frac=1, random_state=17).reset_index(drop=True) if isinstance(value, pd.DataFrame) else deepcopy(value)
                  for name, value in self.inputs.items()}
        with patch.object(joblib, "load", side_effect=AssertionError("No model load")), \
                patch.object(evidence.parent.frozen, "score_stages", side_effect=AssertionError("No rescoring")), \
                patch.object(evidence.parent.contextual, "fit_entry", side_effect=AssertionError("No model fit")):
            tables, summary = evidence.build_tables(inputs)
        self.assertEqual(summary, self.summary)
        for name, actual in tables.items():
            pd.testing.assert_frame_equal(actual, self.frames[name], check_dtype=False, check_exact=True, obj=name)

    def test_datetimes_roundtrip_and_report_contains_every_case_once(self):
        for frame, fields in [(self.orders, ["order_estimated_delivery_date", "order_purchase_timestamp"]),
                              (self.reviews, ["review_creation_date", "review_answer_timestamp"]),
                              (self.stages, ["start_timestamp", "end_timestamp", "selected_reference_latest_end"])]:
            for field in fields:
                self.assertTrue(pd.api.types.is_datetime64_any_dtype(frame[field]), field)
        report = (self.directory/"chi_tiet_83_don.md").read_text()
        for oid in self.frames["disagreement_orders.csv"].order_id:
            self.assertEqual(report.count("## "+oid), 1)

    def test_snapshot_integrity_and_no_overwrite(self):
        with self.assertRaises(FileExistsError):
            evidence.run(self.directory)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            manifest = deepcopy(self.manifest)
            evidence.review.write_json(path/"manifest.json", manifest)
            with self.assertRaises((ValueError, FileNotFoundError)):
                evidence.load_snapshot(path)
        with patch.object(evidence, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "code or protocol changed"):
                evidence.verify_sources(self.source)


if __name__ == "__main__":
    unittest.main()
