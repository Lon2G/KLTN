"""Reconstruct scores from actual frozen Olist observations; no invented research cases."""

import json
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
import build_product_stage_scores as product


class ProductStageScoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.before = {**product.pilot.audit.inventory(product.pilot.DEFAULT_OUTPUT),
                      **product.pilot.audit.inventory(product.signals.DEFAULT_OUTPUT),
                      **product.pilot.audit.inventory(product.review.DEFAULT_BATCH)}
        with patch.object(joblib, "load", side_effect=AssertionError("No models loaded")), \
                patch.object(product.pilot.audit.readiness.integration.delivery, "fit_entry", side_effect=AssertionError("No fitting")):
            cls.inputs, cls.source = product.load_inputs()
            cls.frames, cls.summary = product.build_tables(cls.inputs)
        cls.stages = cls.frames["stage_scores.csv"]
        cls.orders = cls.frames["order_scores.csv"]
        cls.reference = cls.frames["train_stage_reference.csv"]
        cls.groups = cls.frames["selected_reference_groups.csv"].set_index("reference_id")
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.output = Path(cls.temp.name)/"snapshot"
        product.write_snapshot(cls.frames, cls.summary, cls.source, cls.output)

    def test_train_validation_scope_no_self_reference_or_held_test(self):
        self.assertEqual(len(self.orders), 1739)
        self.assertEqual(len(self.stages), 5217)
        self.assertEqual(int(self.orders.review_id.notna().sum()), 118)
        self.assertEqual(self.reference.order_id.nunique(), 5461)
        self.assertEqual(len(self.reference), 15931)
        self.assertTrue(self.reference.split.eq("train").all())
        self.assertTrue(self.reference.end_timestamp.lt(self.inputs["reference_end_before"]).all())
        self.assertFalse(self.reference.order_id.isin(self.orders.order_id).any())
        self.assertFalse(self.orders.review_id.isin(["BR0043", "BR0119"]).any())
        held = self.frames["pilot_scope.csv"].loc[lambda f: ~f.comparison_generated]
        self.assertEqual(held.review_id.tolist(), ["BR0043", "BR0119"])
        self.assertFalse(self.frames["order_product_links.csv"].order_id.isin(held.order_id).any())

    def test_product_mean_is_order_weighted_not_item_weighted(self):
        profiles = self.frames["product_stage_statistics.csv"].set_index(["product_id", "stage"])
        observed = self.reference.loc[self.reference.product_attributable]
        self.assertFalse(observed.duplicated(["order_id", "stage"]).any())
        for key, group in observed.groupby(["product_id", "stage"]):
            profile = profiles.loc[key]
            values = group.duration_days.to_numpy(float)
            self.assertEqual(profile.reference_n, len(group))
            self.assertEqual(profile.mean_days, values.mean())
            self.assertEqual(profile.median_days, np.median(values))
            if len(group) > 1:
                self.assertEqual(profile.sd_days, values.std(ddof=1))
            else:
                self.assertTrue(pd.isna(profile.sd_days))
        known = profiles.loc[("99a4788cb24856965c36a24e339b6058", "preparation_handoff")]
        self.assertEqual(known.reference_n, 335)
        self.assertAlmostEqual(known.mean_days, 2.3163, places=5)
        self.assertTrue(observed.item_count.gt(1).any())

    def test_sparse_and_unseen_products_never_get_fake_means(self):
        profiles = self.frames["product_stage_statistics.csv"]
        self.assertEqual(int(profiles.loc[profiles.reference_n.gt(0), "product_id"].nunique()), 1679)
        self.assertEqual(int(profiles.reference_n.ge(30).sum()), 44)
        empty = profiles.loc[profiles.reference_n.eq(0)]
        self.assertGreater(len(empty), 0)
        self.assertTrue(empty[["mean_days", "median_days", "sd_days"]].isna().all().all())
        self.assertTrue(empty.mean_support.eq("none").all())
        sparse = profiles.loc[profiles.reference_n.between(1, 29)]
        self.assertTrue(sparse.mean_support.eq("sparse_descriptive_only").all())

    def test_br0079_uses_disclosed_fallback_not_fabricated_product_history(self):
        rows = self.stages.loc[self.stages.review_id.eq("BR0079")]
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows.product_reference_n.eq(0).all())
        self.assertTrue(rows.product_mean_days.isna().all())
        self.assertTrue(rows.difference_from_product_mean_days.isna().all())
        self.assertTrue(rows.selected_reference_scope.eq("category_context_fallback").all())
        preparation = rows.set_index("stage").loc["preparation_handoff"]
        self.assertTrue(preparation.long_stage_warning)
        self.assertGreater(preparation.delay_score_0_100, 0)
        order = self.orders.set_index("review_id").loc["BR0079"]
        self.assertEqual(order.performance_state, "early")
        self.assertEqual(order.days_early, 13)
        self.assertTrue(order.long_stage_warning)

    def test_selected_groups_reconstruct_exact_context_and_support(self):
        for gid, group in self.groups.iterrows():
            ids = json.loads(group.order_ids_json)
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(group.n, len(ids))
            self.assertGreaterEqual(group.n, 30)
            selected = self.reference.loc[self.reference.stage.eq(group.stage) & self.reference.order_id.isin(ids)]
            self.assertEqual(len(selected), group.n)
            self.assertTrue(selected.all_pairs_quality_eligible.all())
            context = json.loads(group.context_json)
            self.assertIn("distance_band", context)
            for field, value in context.items():
                self.assertTrue(selected[field].eq(value).all())
            if group.scope == "product_context":
                self.assertIn("item_count", context)
                self.assertTrue(selected.product_attributable.all())
                self.assertEqual(selected.product_id.nunique(), 1)
            self.assertEqual(group.mean_days, selected.sort_values("order_id").duration_days.to_numpy().mean())
            self.assertEqual(group.latest_end, selected.end_timestamp.max())
            self.assertFalse(selected.order_id.isin(self.orders.order_id).any())

    def test_every_stage_score_recomputes_from_its_actual_references(self):
        cache = {}
        for gid, group in self.groups.iterrows():
            ids = json.loads(group.order_ids_json)
            cache[gid] = self.reference.loc[self.reference.stage.eq(group.stage) & self.reference.order_id.isin(ids)].sort_values("order_id").duration_days.to_numpy(float)
        for row in self.stages.loc[self.stages.delay_score_0_100.notna()].itertuples(index=False):
            values = cache[row.selected_reference_id]
            score, severity, rank = product.tail_score(row.duration_days, values)
            self.assertEqual(row.delay_score_0_100, score)
            self.assertEqual(row.severity, severity)
            self.assertEqual(row.reference_percentile_0_100, 100*rank)
            self.assertEqual(row.difference_from_reference_mean_days, row.duration_days-values.mean())
            self.assertEqual(row.long_stage_warning, score > 0)
            self.assertGreaterEqual(score, 0)
            self.assertLessEqual(score, 100)
            if values.mean() > 0:
                self.assertEqual(row.relative_difference_pct, 100*(row.duration_days-values.mean())/values.mean())
            else:
                self.assertTrue(pd.isna(row.relative_difference_pct))

    def test_score_boundary_rules_on_real_distinct_observations(self):
        # Boundary checks reuse observed durations, not generated Olist records.
        values = np.unique(self.reference.loc[self.reference.stage.eq("transport"), "duration_days"].to_numpy(float))[:100]
        self.assertEqual(len(values), 100)
        for index, score, severity in [(94, 0, "none"), (95, 20, "low"), (97, 60, "medium"),
                                       (98, 80, "medium"), (99, 100, "high")]:
            actual, level, _ = product.tail_score(values[index], values)
            self.assertEqual(actual, score)
            self.assertEqual(level, severity)
        for value in values[:50]:
            self.assertEqual(product.tail_score(value, values)[0], 0)
        with self.assertRaises(ValueError):
            product.tail_score(values[-1], values[:29])

    def test_missing_and_bad_geo_are_not_zero_or_normal(self):
        missing = self.stages.loc[self.stages.selected_reference_scope.eq("unavailable")]
        self.assertGreater(len(missing), 0)
        self.assertTrue(missing.delay_score_0_100.isna().all())
        self.assertTrue(missing.long_stage_warning.isna().all())
        self.assertTrue(missing.severity.eq("unassessed").all())
        for rid in ["BR0013", "BR0025", "BR0051", "BR0091", "BR0115"]:
            self.assertTrue(self.stages.loc[self.stages.review_id.eq(rid), "delay_score_0_100"].isna().all())
            self.assertTrue(pd.isna(self.orders.set_index("review_id").loc[rid, "long_stage_warning"]))

    def test_multi_product_or_seller_is_not_attributed_to_one_product(self):
        mixed = self.stages.loc[~self.stages.product_attributable]
        self.assertGreater(len(mixed), 0)
        self.assertFalse(mixed.selected_reference_scope.eq("product_context").any())
        self.assertTrue(mixed.product_mean_days.isna().all())
        self.assertTrue(mixed.difference_from_product_mean_days.isna().all())
        self.assertTrue(mixed.product_mean_comparison_status.eq("not_attributable_to_one_product").all())

    def test_order_aggregation_keeps_stage_and_promise_separate(self):
        original = self.inputs["orders"].set_index("order_id")
        for row in self.orders.itertuples(index=False):
            selected = self.stages.loc[self.stages.order_id.eq(row.order_id)]
            available = selected.delay_score_0_100.notna()
            self.assertEqual(row.scored_stages, int(available.sum()))
            if available.any():
                self.assertEqual(row.max_stage_delay_score, selected.delay_score_0_100.max())
            else:
                self.assertTrue(pd.isna(row.max_stage_delay_score))
            self.assertEqual(row.performance_state, original.loc[row.order_id, "performance_state"])
            warned = selected.loc[selected.long_stage_warning.fillna(False), "stage"].tolist()
            self.assertEqual(json.loads(row.attention_stages_json), warned)
            if warned:
                self.assertTrue(row.long_stage_warning)
            self.assertFalse(row.independent_reference)
            self.assertEqual(row.promise_basis_status, "unverified_source_method_not_provided")

    def test_small_group_tail_resolution_is_disclosed(self):
        scored = self.stages.loc[self.stages.selected_reference_n.notna()]
        np.testing.assert_array_equal(scored.nominal_tail_score_step.to_numpy(),
                                      (2000/scored.selected_reference_n).to_numpy(float))
        small = scored.loc[scored.selected_reference_n.lt(100)]
        self.assertGreater(len(small), 0)
        self.assertTrue(small.tail_resolution.eq("limited_n_below_100").all())

    def test_source_duplication_self_inclusion_and_future_end_rejected(self):
        for change in ["duplicate_item", "self", "future"]:
            inputs = {key: value.copy(deep=True) if isinstance(value, pd.DataFrame) else value for key, value in self.inputs.items()}
            if change == "duplicate_item":
                inputs["items"] = pd.concat([inputs["items"], inputs["items"].iloc[:1]], ignore_index=True)
            elif change == "self":
                inputs["reference"].loc[0, "order_id"] = inputs["orders"].order_id.iloc[0]
            else:
                inputs["reference"].loc[0, "end_timestamp"] = inputs["reference_end_before"]
            with self.subTest(change=change), self.assertRaises(ValueError):
                product.build_tables(inputs)

    def test_roundtrip_preserves_all_values_and_originals(self):
        manifest = product.load_snapshot(self.output)
        for name, frame in self.frames.items():
            pd.testing.assert_frame_equal(product.shared.read_table(self.output, name, manifest), frame,
                                          check_dtype=False, check_exact=True)
        for name, digest in self.before.items():
            self.assertEqual(product.review.digest(ROOT/name), digest)
        with self.assertRaises(FileExistsError):
            product.write_snapshot(self.frames, self.summary, self.source, self.output)
        with self.assertRaises(ValueError):
            product.write_snapshot(self.frames, self.summary, self.source, product.review.DEFAULT_BATCH)

    def test_tampering_rejected_in_disposable_copy(self):
        copy = Path(self.temp.name)/"tampered"
        shutil.copytree(self.output, copy)
        (copy/"summary.json").write_text("{}\n")
        with self.assertRaises(ValueError):
            product.load_snapshot(copy)

    def test_no_accuracy_labels_optimality_or_confirmed_cause(self):
        for key in ["held_out_test_scored", "new_labels_created", "accuracy_computed", "auc_computed",
                    "optimal_threshold_claimed", "cause_verified", "models_retrained"]:
            self.assertFalse(self.summary[key])
        report = product.render_report(self.frames, self.summary)
        self.assertIn("không phải xác suất", report)
        self.assertIn("chưa đủ dữ liệu", report.lower())
        self.assertIn("fallback", report)
        self.assertIn("BR0079", report)
        self.assertIn("không phải cải thiện độ chính xác", report)


if __name__ == "__main__":
    unittest.main()
