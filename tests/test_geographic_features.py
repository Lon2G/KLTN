"""Geographic checks use actual Olist coordinates and orders, never synthetic cases."""

from copy import deepcopy
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/01_data_preparation"))
import build_geographic_features as geo


class GeographicFeatureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.read_paths = []
        original_read = pd.read_csv

        def read(path, *args, **kwargs):
            cls.read_paths.append((str(path), kwargs.get("usecols")))
            return original_read(path, *args, **kwargs)

        with patch.object(pd, "read_csv", side_effect=read):
            cls.inputs, cls.provenance = geo.load_inputs()
            cls.frames, cls.summary = geo.build_features(cls.inputs)
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.output = Path(cls.temporary.name)/"geography"
        cls.saved_summary = geo.write_features(cls.frames, cls.summary, cls.provenance, cls.output)
        cls.restored, cls.manifest = geo.load_snapshot(cls.output)
        cls.orders = cls.frames["order_geographic_features.csv"]
        cls.pairs = cls.frames["seller_customer_distances.csv"]
        cls.zips = cls.frames["zip_representatives.csv"]
        cls.evidence = cls.frames["coordinate_source_evidence.csv"]

    def test_scope_counts_and_no_model_or_accuracy_claims(self):
        expected = {"development_orders": 7333, "distinct_order_seller_pairs": 7491, "source_coordinate_rows": 368922,
                    "requested_zips": 4674, "zips_with_representatives": 4663, "zips_without_source_rows": 11,
                    "all_sellers_geocoded_orders": 7319, "missing_geography_orders_retained": 14, "multiseller_orders": 149}
        for key, value in expected.items():
            self.assertEqual(self.summary[key], value, key)
        self.assertEqual(self.summary["split_orders"], {"train": 5480, "validation": 1853})
        self.assertEqual(self.saved_summary["reserved_test_orders_excluded"], 1691)
        for key in ["training_performed", "thresholds_learned", "test_features_built", "test_scored", "human_labels_created",
                    "anomaly_labels_created", "fraud_labels_created", "accuracy_computed", "online_ready", "imputation_performed",
                    "routes_or_exact_addresses_inferred", "representative_policy_selected_by_accuracy"]:
            self.assertFalse(self.summary[key], key)

    def test_no_reserved_features_models_or_human_review_labels_parsed(self):
        ledger_reads = [(path, columns) for path, columns in self.read_paths if Path(path).name == "cohort_cases.csv"]
        self.assertEqual(len(ledger_reads), 1)
        self.assertEqual(ledger_reads[0][1], ["order_id", "split"])
        self.assertFalse(any("test_timing" in path or "manual_review" in path or "models/" in path or "validation_flags" in path
                             for path, _ in self.read_paths))
        ledger = pd.read_csv(Path(self.provenance["business_sources"]["reservation_directory"])/"cohort_cases.csv", usecols=["order_id", "split"])
        reserved = set(ledger.loc[ledger.split.eq("test"), "order_id"])
        for frame in self.frames.values():
            if "order_id" in frame:
                self.assertFalse(set(frame.order_id) & reserved)

    def test_source_rows_and_real_extreme_coordinates_retained_exactly(self):
        source = self.inputs["coordinates"].sort_values("clean_source_record").reset_index(drop=True)
        pd.testing.assert_frame_equal(self.evidence[source.columns], source, check_exact=True)
        self.assertTrue(source.geolocation_lat.gt(40).any())
        self.assertEqual(int(self.evidence.geolocation_lat.gt(40).sum()), int(source.geolocation_lat.gt(40).sum()))
        self.assertTrue(self.evidence.valid_coordinate_pair.all())
        self.assertEqual(int(self.evidence.used_unique_coordinate.sum()), len(source.drop_duplicates([geo.ZIP, *geo.COORDS])))
        self.assertTrue(self.evidence.clean_source_record.is_unique)

    def test_both_representatives_are_actual_source_points_in_same_zip(self):
        source = self.evidence.set_index("clean_source_record")
        available = self.zips.loc[self.zips.availability.eq("available")]
        for record, latitude, longitude in [("representative_source_record", "latitude", "longitude"),
                                             ("mean_anchor_source_record", "mean_anchor_latitude", "mean_anchor_longitude")]:
            matched = source.loc[available[record]]
            self.assertEqual(available[geo.ZIP].tolist(), matched[geo.ZIP].tolist())
            np.testing.assert_array_equal(available[latitude], matched.geolocation_lat)
            np.testing.assert_array_equal(available[longitude], matched.geolocation_lng)
        missing = self.zips.loc[self.zips.availability.ne("available")]
        self.assertTrue(missing[["representative_source_record", "latitude", "longitude"]].isna().all().all())

    def test_nearest_median_mean_selection_and_spread_match_library(self):
        groups = self.evidence.loc[self.evidence.used_unique_coordinate].groupby(geo.ZIP)
        for row in self.zips.loc[self.zips.availability.eq("available")].iloc[::29].itertuples(index=False):
            points = groups.get_group(getattr(row, geo.ZIP)).sort_values([*geo.COORDS, "clean_source_record"])
            values = points[geo.COORDS].to_numpy(dtype=float)
            angles = np.radians(values)
            anchors = np.radians([np.median(values, axis=0), np.mean(values, axis=0)])
            primary, alternative = geo.haversine_distances(angles, anchors).argmin(axis=0)
            self.assertEqual(row.representative_source_record, points.iloc[primary].clean_source_record)
            self.assertEqual(row.mean_anchor_source_record, points.iloc[alternative].clean_source_record)
            spread = geo.haversine_distances(angles, angles[primary:primary+1]).ravel()*geo.RADIUS_KM
            np.testing.assert_allclose([row.spread_p50_km, row.spread_p90_km, row.spread_max_km], np.quantile(spread, [.5, .9, 1]))

    def test_zip_selection_is_input_order_independent_and_one_point_is_zero_spread(self):
        repeated, evidence = geo.representatives(self.inputs["coordinates"].iloc[::-1], self.inputs["requested_zips"][::-1])
        pd.testing.assert_frame_equal(repeated, self.zips, check_exact=True)
        pd.testing.assert_frame_equal(evidence, self.evidence, check_exact=True)
        single = repeated.loc[repeated.unique_valid_points.eq(1)]
        self.assertFalse(single.empty)
        self.assertTrue(single[["spread_max_km", "representative_sensitivity_km", "median_anchor_offset_km"]].eq(0).all().all())

    def test_distance_agrees_with_independent_spherical_vector_geometry(self):
        valid = self.pairs.loc[self.pairs.distance_available]
        def vectors(values):
            lat, lon = np.radians(values).T
            return np.column_stack([np.cos(lat)*np.cos(lon), np.cos(lat)*np.sin(lon), np.sin(lat)])
        seller = valid[["seller_latitude", "seller_longitude"]].to_numpy()
        customer = valid[["customer_latitude", "customer_longitude"]].to_numpy()
        left, right = vectors(seller), vectors(customer)
        expected = np.arctan2(np.linalg.norm(np.cross(left, right), axis=1), np.sum(left*right, axis=1))*geo.RADIUS_KM
        np.testing.assert_allclose(valid.distance_km, expected, rtol=1e-11, atol=1e-9)
        np.testing.assert_array_equal(geo.pair_distances(seller, seller), np.zeros(len(seller)))
        np.testing.assert_allclose(geo.pair_distances(seller, customer), geo.pair_distances(customer, seller), rtol=1e-13)
        self.assertTrue(valid.distance_km.between(0, np.pi*geo.RADIUS_KM).all())

    def test_distance_api_handles_empty_pairs_and_rejects_missing_coordinates(self):
        observed = self.pairs.loc[self.pairs.distance_available, ["seller_latitude", "seller_longitude"]].to_numpy()
        self.assertEqual(geo.pair_distances(observed[:0], observed[:0]).shape, (0,))
        with self.assertRaises(ValueError):
            geo.pair_distances(observed[:1], observed[:2])
        with self.assertRaises(ValueError):
            geo.pair_distances(observed.ravel(), observed.ravel())
        missing = self.pairs.loc[~self.pairs.distance_available, ["customer_latitude", "customer_longitude"]].to_numpy()
        self.assertTrue(np.isnan(missing).any())
        with self.assertRaisesRegex(ValueError, "finite coordinates"):
            geo.pair_distances(missing, missing)

    def test_multi_seller_counts_and_item_weighting_use_exact_observed_items(self):
        source = self.inputs["items"].groupby(["order_id", "seller_id"]).size()
        pd.testing.assert_series_equal(self.pairs.set_index(["order_id", "seller_id"]).seller_item_count, source, check_names=False, check_dtype=False)
        grouped = self.pairs.groupby("order_id")
        indexed = self.orders.set_index("order_id")
        expected_mean = self.pairs.distance_km.mul(self.pairs.seller_item_count).groupby(self.pairs.order_id).sum(min_count=1)/grouped.seller_item_count.sum()
        expected_mean = expected_mean.where(indexed.all_sellers_geocoded)
        np.testing.assert_allclose(indexed[geo.CANDIDATES[1]], expected_mean, equal_nan=True)
        np.testing.assert_allclose(indexed[geo.CANDIDATES[0]], grouped.distance_km.max().where(indexed.all_sellers_geocoded), equal_nan=True)
        self.assertEqual(int(self.pairs.seller_item_count.sum()), 8642)
        self.assertEqual(int(indexed.seller_count.gt(1).sum()), 149)

    def test_missing_or_partial_geography_is_never_zero_or_partial_order_mean(self):
        missing = self.orders.loc[~self.orders.all_sellers_geocoded]
        self.assertEqual(len(missing), 14)
        self.assertTrue(missing[geo.CANDIDATES].isna().all().all())
        self.assertTrue(self.pairs.loc[~self.pairs.distance_available, "distance_km"].isna().all())
        self.assertTrue(missing.distance_unavailable_reasons.ne("none").all())
        # Remove one observed seller ZIP's support in a temporary derived view.
        # This checks missingness handling without inventing any coordinate or order.
        multi = self.pairs.loc[self.pairs.order_id.isin(self.orders.loc[self.orders.seller_count.gt(1), "order_id"])]
        target = multi.loc[multi.distance_available].iloc[0]
        unavailable = self.zips.loc[self.zips[geo.ZIP].ne(target.seller_zip_code_prefix)]
        pairs = geo.seller_distances(self.inputs, unavailable)
        result = geo.roll_up_orders(self.inputs, pairs).set_index("order_id")
        self.assertTrue(result.loc[target.order_id, geo.CANDIDATES].isna().all())
        self.assertFalse(result.loc[target.order_id, "all_sellers_geocoded"])

    def test_source_envelopes_bound_supplied_points_not_actual_routes(self):
        unique = self.evidence.loc[self.evidence.used_unique_coordinate].groupby(geo.ZIP)
        for row in self.pairs.loc[self.pairs.distance_available].iloc[::487].itertuples(index=False):
            seller = unique.get_group(row.seller_zip_code_prefix)[geo.COORDS].to_numpy(dtype=float)
            customer = unique.get_group(row.customer_zip_code_prefix)[geo.COORDS].to_numpy(dtype=float)
            observed = geo.haversine_distances(np.radians(seller), np.radians(customer))*geo.RADIUS_KM
            self.assertGreaterEqual(observed.min()+1e-8, row.source_envelope_lower_km)
            self.assertLessEqual(observed.max()-1e-8, row.source_envelope_upper_km)
        self.assertTrue(self.pairs.loc[~self.pairs.distance_available, ["source_envelope_lower_km", "source_envelope_upper_km"]].isna().all().all())

    def test_sensitivity_is_actual_policy_difference_and_extremes_not_removed(self):
        np.testing.assert_allclose(self.pairs.distance_sensitivity_km,
                                   (self.pairs.distance_km-self.pairs.mean_anchor_distance_km).abs(), equal_nan=True)
        self.assertGreater(self.orders.distance_max_sensitivity_km.max(), 1000)
        self.assertGreater(self.orders[geo.CANDIDATES[0]].max(), 9000)
        self.assertGreater(self.zips.spread_max_km.max(), 9000)
        self.assertFalse(self.summary["representative_policy_selected_by_accuracy"])
        self.assertFalse(self.summary["geographic_source_quality_verified"])
        self.assertFalse(self.summary["ready_for_model_training"])

    def test_monthly_coverage_and_observed_context_denominators_reconcile(self):
        coverage = self.frames["coverage_by_month.csv"]
        self.assertEqual(int(coverage.orders.sum()), 7333)
        self.assertEqual(int(coverage.all_sellers_geocoded.sum()), 7319)
        self.assertEqual(int(coverage.missing_geography_orders.sum()), 14)
        self.assertEqual(int(coverage.timing_eligible_orders.sum()), 6601)
        context = self.frames["order_context.csv"]
        summary = self.frames["distance_context_summary.csv"]
        self.assertEqual(int(summary.orders.sum()), 7333)
        self.assertEqual(int(summary.delivery_duration_observed_orders.sum()), 6601)
        self.assertEqual(int(context.distance_band_km.eq("unavailable").sum()), 14)
        original = self.inputs["features"].set_index("order_id")
        columns = ["freight_sum", "item_price_sum", *geo.business.audit.DURATIONS]
        pd.testing.assert_frame_equal(context.set_index("order_id")[columns].sort_index(), original[columns].sort_index(), check_exact=True)
        self.assertEqual(int(context.loc[context.timing_input_eligible & context.all_sellers_geocoded].shape[0]), int(coverage.geography_and_timing_available.sum()))

    def test_correlations_are_pairwise_observed_and_not_automated_labels(self):
        context = self.frames["order_context.csv"]
        for row in self.frames["associations_by_split.csv"].itertuples(index=False):
            subset = context.loc[context.split.eq(row.split)]
            if row.context_variable == "carrier_to_delivered_days":
                subset = subset.loc[subset.timing_input_eligible]
            subset = subset[[row.distance_feature, row.context_variable]].dropna()
            self.assertEqual(row.paired_orders, len(subset))
            self.assertAlmostEqual(row.spearman_rho, subset.corr(method="spearman").iloc[0, 1], places=12)
            self.assertIn("not_causation_or_anomaly_accuracy", row.interpretation)
        self.assertFalse(any("label" in column or "anomaly_flag" in column for frame in self.frames.values() for column in frame.columns))

    def test_exact_roundtrip_manifests_and_candidate_allowlist(self):
        for name, expected in self.frames.items():
            actual = geo.read_table(self.output, name, self.manifest)
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        pd.testing.assert_frame_equal(self.restored, self.orders, check_dtype=False, check_exact=True)
        geo.verify_files(self.output, self.manifest["output_hashes"])
        dictionary = self.frames["feature_dictionary.csv"]
        self.assertEqual(dictionary.loc[dictionary.role.eq("candidate_feature"), "column"].tolist(), geo.CANDIDATES)
        self.assertFalse(dictionary.online_ready.any())
        self.assertTrue(self.frames["source_reconciliation.csv"].source_count.eq(self.frames["source_reconciliation.csv"].retained_count).all())

    def test_changed_scope_duplicate_sources_and_wrong_identity_rejected(self):
        duplicate = {**self.inputs, "coordinates": pd.concat([self.inputs["coordinates"], self.inputs["coordinates"].iloc[:1]])}
        with self.assertRaisesRegex(ValueError, "duplicate key"):
            geo.validate_inputs(duplicate)
        missing_pair = {**self.inputs, "links": self.inputs["links"].iloc[1:]}
        with self.assertRaises((ValueError, AssertionError)):
            geo.validate_inputs(missing_pair)
        identity = self.inputs["features"].copy()
        identity["marketplace_id"] = ""
        with self.assertRaisesRegex(ValueError, "identity differs"):
            geo.validate_inputs({**self.inputs, "features": identity})
        scope = self.inputs["features"].copy()
        scope.loc[0, "split"] = "test"
        with self.assertRaisesRegex(ValueError, "development scope"):
            geo.validate_inputs({**self.inputs, "features": scope})

    def test_feature_results_do_not_depend_on_item_or_order_input_order(self):
        reordered = {name: value.iloc[::-1].reset_index(drop=True) if isinstance(value, pd.DataFrame) else value[::-1]
                     for name, value in self.inputs.items()}
        pairs = geo.seller_distances(reordered, self.zips)
        pd.testing.assert_frame_equal(pairs, self.pairs, check_exact=True)
        pd.testing.assert_frame_equal(geo.roll_up_orders(reordered, pairs), self.orders, check_exact=True)

    def test_temporal_outcomes_cannot_choose_representatives_or_change_distances(self):
        earlier = {**self.inputs, "links": self.inputs["links"].loc[self.inputs["links"].split.eq("train")],
                   "items": self.inputs["items"].loc[self.inputs["items"].order_id.isin(self.orders.loc[self.orders.split.eq("train"), "order_id"])]}
        requested = sorted(set(earlier["links"].seller_zip_code_prefix) | set(earlier["links"].customer_zip_code_prefix))
        selected = self.inputs["coordinates"].loc[self.inputs["coordinates"][geo.ZIP].isin(requested)]
        zips, _ = geo.representatives(selected, requested)
        pd.testing.assert_frame_equal(zips, self.zips.loc[self.zips[geo.ZIP].isin(requested)].reset_index(drop=True), check_exact=True)
        pairs = geo.seller_distances(earlier, zips)
        pd.testing.assert_frame_equal(pairs, self.pairs.loc[self.pairs.order_id.isin(earlier["links"].order_id)].reset_index(drop=True), check_exact=True)

    def test_snapshot_overwrite_changed_parent_and_modified_output_are_rejected(self):
        with self.assertRaises(FileExistsError):
            geo.write_features(self.frames, self.summary, self.provenance, self.output)
        changed = deepcopy(self.provenance)
        changed["business_manifest_sha256"] = "changed"
        with self.assertRaisesRegex(ValueError, "parent manifest changed"):
            geo.verify_sources(changed)
        with tempfile.TemporaryDirectory() as temporary:
            copy = Path(temporary)/"snapshot"
            shutil.copytree(self.output, copy)
            self.orders.iloc[::-1].to_csv(copy/"order_geographic_features.csv", index=False)
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                geo.load_snapshot(copy)

    def test_atomic_publication_rolls_back_when_source_changes(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/"snapshot"
            with patch.object(geo, "verify_sources", side_effect=[None, ValueError("source changed")]):
                with self.assertRaisesRegex(ValueError, "source changed"):
                    geo.write_features(self.frames, self.summary, self.provenance, target)
            self.assertFalse(target.exists())
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_protocol_and_code_changes_fail_instead_of_silent_policy_selection(self):
        self.assertEqual(geo.read_json(geo.TEMPLATE/"protocol.json"), geo.protocol())
        with patch.object(geo, "code_hashes", return_value={}):
            with self.assertRaisesRegex(ValueError, "code or protocol changed"):
                geo.verify_sources(self.provenance)
        self.assertEqual(geo.quantile_summary(self.orders.loc[~self.orders.all_sellers_geocoded, geo.CANDIDATES[0]], [.5]), {"0.5": None})
        json.dumps(geo.quantile_summary(pd.Series(dtype=float), [0, 1]), allow_nan=False)


if __name__ == "__main__":
    unittest.main()
