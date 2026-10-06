"""Traceable ZIP-distance features from real Olist development observations only."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics.pairwise import haversine_distances

import build_bed_bath_table_business_features as business


ROOT = business.audit.ROOT
VERSION = "bed_bath_table_geographic_features_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/bed_bath_table_geographic_features_v1"
TEMPLATE = ROOT/"templates/geographic_features_v1"
ZIP = "geolocation_zip_code_prefix"
COORDS = ["geolocation_lat", "geolocation_lng"]
RADIUS_KM = 6371.0
CANDIDATES = ["seller_customer_distance_max_km", "seller_customer_distance_item_weighted_mean_km"]
IDENTITY_COLUMNS = ["order_id", "dataset_id", "marketplace_id", "product_category_name", "split",
                    "order_purchase_timestamp", "event_time_cutoff_exclusive"]
LINK_COLUMNS = ["order_id", "seller_id", "customer_zip_code_prefix", "seller_zip_code_prefix"]
TABLES = {"order_geographic_features.csv", "order_context.csv", "seller_customer_distances.csv",
          "zip_representatives.csv", "coordinate_source_evidence.csv", "coverage_by_month.csv",
          "distance_context_summary.csv", "associations_by_split.csv", "feature_dictionary.csv", "source_reconciliation.csv"}


def protocol():
    return {"version": VERSION, "scope": "frozen_bed_bath_table_development_only",
            "coordinate_validity": "finite_latitude_in_minus90_90_and_longitude_in_minus180_180",
            "coordinate_weighting": "one_vote_per_distinct_valid_latitude_longitude_pair_per_zip",
            "primary_representative": "observed_point_nearest_coordinatewise_median_anchor",
            "sensitivity_representative": "observed_point_nearest_coordinatewise_mean_anchor",
            "tie_break": ["latitude_ascending", "longitude_ascending", "clean_source_record_ascending"],
            "distance_implementation": "sklearn.metrics.pairwise.haversine_distances", "earth_radius_km": RADIUS_KM,
            "order_candidate_features": CANDIDATES,
            "multi_seller_policy": "keep_each_distinct_order_seller_pair; order_aggregates_require_all_pairs",
            "missing_policy": "retain_order_and_leave_distance_missing; no_geocoding_or_imputation",
            "context_distance_band_edges_km": [0, 50, 200, 500, 1000, 2000],
            "context_distance_band_purpose": "fixed_descriptive_bands_not_anomaly_thresholds",
            "availability": "retrospective_zip_reference_snapshot; observation_and_arrival_dates_unknown",
            "thresholds_learned": False, "labels_created": False, "online_ready": False}


def read_json(path):
    return json.loads(Path(path).read_text(), object_pairs_hook=business.importer.unique_json_keys)


def read_table(directory, name, manifest):
    # Preserve floating-point source coordinates on CSV round trips.
    schema = manifest["schemas"][name]
    dates = [key for key, value in schema.items() if value.startswith("datetime64")]
    frame = pd.read_csv(Path(directory)/name, dtype={key: value for key, value in schema.items() if key not in dates},
                        parse_dates=dates, date_format="%Y-%m-%d %H:%M:%S", keep_default_na=False,
                        na_values=[""], float_precision="round_trip")
    if frame.columns.tolist() != list(schema):
        raise ValueError(f"Geographic table schema differs: {name}")
    return frame


def code_hashes():
    files = [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]
    return {str(path.relative_to(ROOT)): business.audit.file_hash(path) for path in files}


def verify_files(directory, hashes):
    directory = Path(directory).resolve()
    for name, expected in hashes.items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory) or not path.is_file() or business.audit.file_hash(path) != expected:
            raise ValueError(f"Geographic input/output hash mismatch: {directory/name}")


def verify_sources(provenance):
    parent = Path(provenance["business_directory"])
    if business.audit.file_hash(parent/"manifest.json") != provenance["business_manifest_sha256"]:
        raise ValueError("Business parent manifest changed")
    verify_files(parent, provenance["business_output_hashes"])
    business.verify_sources(provenance["business_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Geographic code or protocol changed during execution")


def load_inputs(parent=business.DEFAULT_OUTPUT):
    parent = Path(parent).resolve()
    before = business.audit.file_hash(parent/"manifest.json")
    _, manifest = business.load_feature_snapshot(parent)
    business.verify_sources(manifest["provenance"])
    if read_json(TEMPLATE/"protocol.json") != protocol():
        raise ValueError("Geographic protocol differs; use an explicit new version")
    features = read_table(parent, "order_features.csv", manifest)
    context = read_table(parent, "order_context.csv", manifest)
    items = read_table(parent, "item_source_evidence.csv", manifest)
    links = read_table(parent, "seller_order_links.csv", manifest)
    support = read_table(parent, "geolocation_zip_support.csv", manifest)
    reservation = Path(manifest["provenance"]["reservation_directory"])
    reservations = pd.read_csv(reservation/"cohort_cases.csv", usecols=["order_id", "split"], dtype="string", keep_default_na=False)
    business.require_scope(features, reservations, business.IDENTITY)
    requested = sorted(set(links.customer_zip_code_prefix.dropna()) | set(links.seller_zip_code_prefix.dropna()))
    clean = Path(manifest["provenance"]["clean_directory"])
    clean_manifest = read_json(clean/"manifest.json")
    coordinates = business.read_selected(clean, "geolocation", clean_manifest, ZIP, set(requested))
    inputs = {"features": features, "context": context, "items": items, "links": links,
              "zip_support": support, "coordinates": coordinates, "requested_zips": requested}
    validate_inputs(inputs)
    provenance = {"business_directory": str(parent), "business_manifest_sha256": before,
                  "business_output_hashes": manifest["output_hashes"], "business_sources": manifest["provenance"],
                  "code_hashes": code_hashes(), "reserved_test_orders_excluded": int(reservations.split.eq("test").sum())}
    verify_sources(provenance)
    return inputs, provenance


def valid_coordinates(frame):
    values = frame[COORDS].to_numpy(dtype=float, na_value=np.nan)
    return pd.Series(np.isfinite(values).all(axis=1) & (np.abs(values[:, 0]) <= 90) & (np.abs(values[:, 1]) <= 180), index=frame.index)


def validate_inputs(inputs):
    features, context, links, items, geo = (inputs[name] for name in ["features", "context", "links", "items", "coordinates"])
    for frame, columns, name in [(features, ["order_id"], "features"), (context, ["order_id"], "context"),
                                  (links, ["order_id", "seller_id"], "seller links"), (items, ["order_id", "order_item_id"], "items"),
                                  (geo, ["clean_source_record"], "coordinate source")]:
        business.require_key(frame, columns, name)
    if (not features.split.isin(["train", "validation"]).all() or features.empty
            or set(context.order_id) != set(features.order_id) or set(links.order_id) != set(features.order_id)):
        raise ValueError("Geographic inputs must preserve exact nonempty development scope")
    for name, value in [("dataset_id", business.IDENTITY["dataset_id"]), ("marketplace_id", business.IDENTITY["marketplace_id"]),
                        ("product_category_name", business.IDENTITY["primary_category"])]:
        if not features[name].eq(value).all():
            raise ValueError("Geographic input identity differs")
    pd.testing.assert_frame_equal(features.set_index("order_id")[IDENTITY_COLUMNS[1:]].sort_index(),
                                  context.set_index("order_id")[IDENTITY_COLUMNS[1:]].sort_index(), check_dtype=False)
    item_pairs = items[["order_id", "seller_id"]].drop_duplicates().sort_values(["order_id", "seller_id"]).reset_index(drop=True)
    pd.testing.assert_frame_equal(item_pairs, links[["order_id", "seller_id"]].sort_values(["order_id", "seller_id"]).reset_index(drop=True), check_dtype=False)
    expected_customer = context.set_index("order_id").customer_zip_code_prefix.reindex(links.order_id).reset_index(drop=True)
    pd.testing.assert_series_equal(links.customer_zip_code_prefix.reset_index(drop=True), expected_customer, check_names=False, check_dtype=False)
    if links.groupby("seller_id").seller_zip_code_prefix.nunique(dropna=False).gt(1).any():
        raise ValueError("A seller has inconsistent source ZIPs")
    requested = set(links.customer_zip_code_prefix.dropna()) | set(links.seller_zip_code_prefix.dropna())
    if set(inputs["requested_zips"]) != requested or not set(geo[ZIP]).issubset(requested):
        raise ValueError("Coordinates outside the linked development ZIP scope")
    support = geo.assign(valid=valid_coordinates(geo)).groupby(ZIP).agg(
        recorded_coordinate_rows=("clean_source_record", "size"), valid_coordinate_rows=("valid", "sum")).reset_index()
    pd.testing.assert_frame_equal(support, inputs["zip_support"].sort_values(ZIP).reset_index(drop=True), check_dtype=False)


def pair_distances(left, right):
    left, right = np.asarray(left, dtype=float), np.asarray(right, dtype=float)
    if left.ndim != 2 or left.shape[1] != 2 or left.shape != right.shape:
        raise ValueError("Expected equally sized paired latitude/longitude matrices")
    for values in [left, right]:
        if not np.isfinite(values).all() or (np.abs(values[:, 0]) > 90).any() or (np.abs(values[:, 1]) > 180).any():
            raise ValueError("Distances require finite coordinates within Earth bounds")
    result = np.empty(len(left), dtype=float)
    # Bounded matrices avoid allocating an all-orders squared distance matrix.
    for start in range(0, len(left), 256):
        stop = start+256
        result[start:stop] = np.diag(haversine_distances(np.radians(left[start:stop]), np.radians(right[start:stop])))*RADIUS_KM
    return result


def representatives(coordinates, requested_zips):
    business.require_key(coordinates, ["clean_source_record"], "coordinate source")
    evidence = coordinates.sort_values("clean_source_record").reset_index(drop=True).copy()
    evidence["valid_coordinate_pair"] = valid_coordinates(evidence)
    evidence["used_unique_coordinate"] = evidence.valid_coordinate_pair & ~evidence.duplicated([ZIP, *COORDS])
    grouped = evidence.groupby(ZIP, sort=True)
    rows = []
    numeric = ["latitude", "longitude", "mean_anchor_latitude", "mean_anchor_longitude",
               "median_anchor_offset_km", "spread_p50_km", "spread_p90_km", "spread_max_km", "representative_sensitivity_km"]
    for zip_code in sorted(set(requested_zips)):
        source = grouped.get_group(zip_code) if zip_code in grouped.groups else evidence.iloc[:0]
        unique = source.loc[source.used_unique_coordinate].sort_values([*COORDS, "clean_source_record"])
        row = {ZIP: zip_code, "recorded_coordinate_rows": len(source), "valid_coordinate_rows": int(source.valid_coordinate_pair.sum()),
               "invalid_coordinate_rows": int((~source.valid_coordinate_pair).sum()), "unique_valid_points": len(unique),
               "source_state_codes_json": json.dumps(sorted(set(source.geolocation_state.dropna()))),
               "source_state_count": int(source.geolocation_state.nunique()),
               "representative_source_record": pd.NA, "mean_anchor_source_record": pd.NA,
               "availability": "available" if len(unique) else "no_source_rows" if source.empty else "no_valid_coordinates",
               **{name: np.nan for name in numeric}}
        if len(unique):
            points = unique[COORDS].to_numpy(dtype=float)
            angles = np.radians(points)
            anchors = np.array([np.median(points, axis=0), np.mean(points, axis=0)])
            distances = haversine_distances(angles, np.radians(anchors))*RADIUS_KM
            primary, alternative = distances.argmin(axis=0)
            spread = haversine_distances(angles, angles[primary:primary+1]).ravel()*RADIUS_KM
            row.update(latitude=points[primary, 0], longitude=points[primary, 1],
                       mean_anchor_latitude=points[alternative, 0], mean_anchor_longitude=points[alternative, 1],
                       representative_source_record=int(unique.iloc[primary].clean_source_record),
                       mean_anchor_source_record=int(unique.iloc[alternative].clean_source_record),
                       median_anchor_offset_km=float(distances[primary, 0]), spread_p50_km=float(np.quantile(spread, .5)),
                       spread_p90_km=float(np.quantile(spread, .9)), spread_max_km=float(spread.max()),
                       representative_sensitivity_km=float(pair_distances(points[primary:primary+1], points[alternative:alternative+1])[0]))
        rows.append(row)
    result = pd.DataFrame(rows).astype({ZIP: "string", "representative_source_record": "Int64", "mean_anchor_source_record": "Int64"})
    return result, evidence


def seller_distances(inputs, zips):
    pairs = inputs["links"][LINK_COLUMNS].copy()
    counts = inputs["items"].groupby(["order_id", "seller_id"]).size().rename("seller_item_count").reset_index()
    pairs = pairs.merge(counts, on=["order_id", "seller_id"], validate="one_to_one", how="left")
    fields = [ZIP, "latitude", "longitude", "representative_source_record", "mean_anchor_latitude", "mean_anchor_longitude",
              "spread_max_km", "spread_p90_km", "availability", "unique_valid_points", "source_state_count"]
    for role in ["customer", "seller"]:
        renamed = zips[fields].rename(columns={name: f"{role}_{name}" for name in fields if name != ZIP})
        pairs = pairs.merge(renamed, left_on=f"{role}_zip_code_prefix", right_on=ZIP, validate="many_to_one", how="left").drop(columns=ZIP)
    pairs["distance_available"] = pairs.customer_availability.eq("available") & pairs.seller_availability.eq("available")
    pairs["same_zip"] = pairs.customer_zip_code_prefix.eq(pairs.seller_zip_code_prefix).fillna(False)
    for role in ["customer", "seller"]:
        pairs[f"{role}_availability"] = pairs[f"{role}_availability"].fillna("missing_zip")
    pairs["distance_unavailable_reason"] = [";".join(f"{role}:{getattr(row, role+'_availability')}" for role in ["customer", "seller"]
                                                    if getattr(row, role+'_availability') != "available") or "none" for row in pairs.itertuples(index=False)]
    mask = pairs.distance_available
    for prefix, output in [("", "distance_km"), ("mean_anchor_", "mean_anchor_distance_km")]:
        left = pairs.loc[mask, [f"seller_{prefix}latitude", f"seller_{prefix}longitude"]]
        right = pairs.loc[mask, [f"customer_{prefix}latitude", f"customer_{prefix}longitude"]]
        pairs[output] = np.nan
        pairs.loc[mask, output] = pair_distances(left, right)
    pairs["distance_sensitivity_km"] = (pairs.distance_km-pairs.mean_anchor_distance_km).abs()
    spread = pairs.customer_spread_max_km+pairs.seller_spread_max_km
    pairs["source_envelope_lower_km"] = (pairs.distance_km-spread).clip(lower=0)
    pairs["source_envelope_upper_km"] = (pairs.distance_km+spread).clip(upper=np.pi*RADIUS_KM)
    return pairs.sort_values(["order_id", "seller_id"]).reset_index(drop=True)


def roll_up_orders(inputs, pairs):
    features = inputs["features"].sort_values("order_id").reset_index(drop=True)
    grouped = pairs.groupby("order_id", sort=True)
    result = grouped.agg(seller_count=("seller_id", "size"), geocoded_seller_count=("distance_available", "sum"),
                         item_count=("seller_item_count", "sum"), same_zip_seller_count=("same_zip", "sum"))
    result["all_sellers_geocoded"] = result.seller_count.eq(result.geocoded_seller_count)
    for distance, prefix in [("distance_km", "seller_customer_distance"), ("mean_anchor_distance_km", "mean_anchor_distance")]:
        result[f"{prefix}_max_km"] = grouped[distance].max().where(result.all_sellers_geocoded)
        total = pairs[distance].mul(pairs.seller_item_count).groupby(pairs.order_id).sum(min_count=1)
        result[f"{prefix}_item_weighted_mean_km"] = (total/result.item_count).where(result.all_sellers_geocoded)
    result["distance_max_sensitivity_km"] = (result.seller_customer_distance_max_km-result.mean_anchor_distance_max_km).abs()
    result["distance_weighted_mean_sensitivity_km"] = (result.seller_customer_distance_item_weighted_mean_km-result.mean_anchor_distance_item_weighted_mean_km).abs()
    result["distance_unavailable_reasons"] = grouped.distance_unavailable_reason.agg(lambda values: ";".join(sorted(set(values)-{"none"})) or "none")
    result = features[IDENTITY_COLUMNS].merge(result.reset_index(), on="order_id", validate="one_to_one", how="left")
    pd.testing.assert_frame_equal(result[["order_id", "seller_count", "item_count"]], features[["order_id", "seller_count", "item_count"]], check_dtype=False)
    result["geographic_availability"] = protocol()["availability"]
    return result


def context_diagnostics(inputs, order_features):
    cols = ["order_id", "order_status", *business.audit.TIMES, "order_estimated_delivery_date", "timing_input_eligible"]
    context = order_features[IDENTITY_COLUMNS + ["all_sellers_geocoded", *CANDIDATES]].merge(
        inputs["context"][cols[0:1]+[name for name in cols[1:] if name not in IDENTITY_COLUMNS]], on="order_id", validate="one_to_one")
    context = context.merge(inputs["features"][["order_id", "freight_sum", "item_price_sum", *business.audit.DURATIONS]], on="order_id", validate="one_to_one")
    context["purchase_month"] = context.order_purchase_timestamp.dt.strftime("%Y-%m")
    bands = ["0_to_50", "50_to_200", "200_to_500", "500_to_1000", "1000_to_2000", "2000_plus"]
    context["distance_band_km"] = pd.cut(context[CANDIDATES[0]], [*protocol()["context_distance_band_edges_km"], np.inf],
                                          labels=bands, right=False).astype("string").fillna("unavailable")
    coverage, comparisons, associations = [], [], []
    for (split, month), frame in context.groupby(["split", "purchase_month"], sort=True):
        coverage.append({"split": split, "purchase_month": month, "orders": len(frame),
                         "all_sellers_geocoded": int(frame.all_sellers_geocoded.sum()),
                         "missing_geography_orders": int((~frame.all_sellers_geocoded).sum()),
                         "timing_eligible_orders": int(frame.timing_input_eligible.sum()),
                         "geography_and_timing_available": int((frame.all_sellers_geocoded & frame.timing_input_eligible).sum())})
    for (split, band), frame in context.groupby(["split", "distance_band_km"], sort=True):
        freight = frame.freight_sum.dropna()
        duration = frame.loc[frame.timing_input_eligible, "carrier_to_delivered_days"].dropna()
        comparisons.append({"split": split, "distance_band_km": band, "orders": len(frame),
                            "freight_observed_orders": len(freight), "freight_median": freight.median(),
                            "delivery_duration_observed_orders": len(duration), "carrier_to_delivered_days_median": duration.median(),
                            "carrier_to_delivered_days_p90": duration.quantile(.9)})
    for split, frame in context.groupby("split", sort=True):
        for x in CANDIDATES:
            for y in ["freight_sum", "carrier_to_delivered_days"]:
                usable = frame.loc[frame.timing_input_eligible] if y == "carrier_to_delivered_days" else frame
                usable = usable[[x, y]].dropna()
                supported = len(usable) >= 3 and usable[x].nunique() > 1 and usable[y].nunique() > 1
                associations.append({"split": split, "distance_feature": x, "context_variable": y, "paired_orders": len(usable),
                                     "spearman_rho": usable[x].corr(usable[y], method="spearman") if supported else np.nan,
                                     "interpretation": "descriptive_association_not_causation_or_anomaly_accuracy"})
    return {"order_context.csv": context, "coverage_by_month.csv": pd.DataFrame(coverage),
            "distance_context_summary.csv": pd.DataFrame(comparisons), "associations_by_split.csv": pd.DataFrame(associations)}


def feature_dictionary(features):
    formulas = {CANDIDATES[0]: "Maximum approximate seller-customer distance across every distinct seller of the order.",
                CANDIDATES[1]: "Sum(distance_km * observed seller item-row count) / total observed item-row count."}
    return pd.DataFrame([{"column": name, "role": "candidate_feature" if name in CANDIDATES else "audit_not_predictor",
                          "unit": "km" if name.endswith("_km") else "metadata_or_count",
                          "definition": formulas.get(name, "Identity, availability, source coverage or representative-policy sensitivity; not an automatic predictor."),
                          "missing_policy": "Both candidates missing unless all distinct sellers have coordinate support; zero distance is not missing.",
                          "availability": protocol()["availability"], "online_ready": False} for name in features])


def quantile_summary(values, quantiles):
    return {str(q): None if values.dropna().empty else float(values.quantile(q)) for q in quantiles}


def build_features(inputs):
    validate_inputs(inputs)
    zips, evidence = representatives(inputs["coordinates"], inputs["requested_zips"])
    pairs = seller_distances(inputs, zips)
    expected = inputs["links"].set_index(["order_id", "seller_id"]).both_zip_sources_available.sort_index()
    pd.testing.assert_series_equal(pairs.set_index(["order_id", "seller_id"]).distance_available.sort_index(), expected, check_names=False, check_dtype=False)
    features = roll_up_orders(inputs, pairs)
    frames = {"order_geographic_features.csv": features, "zip_representatives.csv": zips,
              "coordinate_source_evidence.csv": evidence, "seller_customer_distances.csv": pairs,
              "feature_dictionary.csv": feature_dictionary(features), **context_diagnostics(inputs, features)}
    frames["source_reconciliation.csv"] = pd.DataFrame([
        {"population": "development_orders", "source_count": len(inputs["features"]), "retained_count": len(features)},
        {"population": "distinct_order_seller_pairs", "source_count": len(inputs["links"]), "retained_count": len(pairs)},
        {"population": "observed_item_rows", "source_count": len(inputs["items"]), "retained_count": int(pairs.seller_item_count.sum())},
        {"population": "linked_zip_coordinate_rows", "source_count": len(inputs["coordinates"]), "retained_count": len(evidence)}])
    summary = {"identity": business.IDENTITY, "development_orders": len(features), "split_orders": features.split.value_counts().to_dict(),
               "distinct_order_seller_pairs": len(pairs), "source_coordinate_rows": len(evidence),
               "invalid_coordinate_rows_retained": int((~evidence.valid_coordinate_pair).sum()),
               "requested_zips": len(zips), "zips_with_representatives": int(zips.availability.eq("available").sum()),
               "zips_without_source_rows": int(zips.availability.eq("no_source_rows").sum()),
               "unique_coordinate_points_used": int(zips.unique_valid_points.sum()),
               "all_sellers_geocoded_orders": int(features.all_sellers_geocoded.sum()),
               "missing_geography_orders_retained": int((~features.all_sellers_geocoded).sum()),
               "multiseller_orders": int(features.seller_count.gt(1).sum()),
               "zero_approximate_max_distance_orders": int(features[CANDIDATES[0]].eq(0).sum()),
               "primary_mean_anchor_different_zips": int(zips.representative_sensitivity_km.gt(0).sum()),
               "distance_max_km_quantiles": quantile_summary(features[CANDIDATES[0]], [0, .5, .9, .99, 1]),
               "distance_max_sensitivity_km_quantiles": quantile_summary(features.distance_max_sensitivity_km, [.5, .9, .99, 1]),
               "candidate_features": CANDIDATES, "training_performed": False, "thresholds_learned": False,
               "test_features_built": False, "test_scored": False, "human_labels_created": False, "anomaly_labels_created": False,
               "fraud_labels_created": False, "accuracy_computed": False, "online_ready": False, "imputation_performed": False,
               "representatives_are_observed_source_points": True, "routes_or_exact_addresses_inferred": False,
               "representative_policy_selected_by_accuracy": False, "geographic_source_quality_verified": False,
               "ready_for_model_training": False}
    return frames, summary


def render_readme(summary, frames):
    return "\n\n".join(["# Geographic Feature Snapshot", "## Actual Output\n```json\n"+json.dumps(summary, indent=2)+"\n```",
                         (TEMPLATE/"README.md").read_text(),
                         "## Largest Supplied ZIP Spreads\nThese are source-dispersion diagnostics, not rejected ZIPs or anomaly labels.\n"+
                         business.audit.markdown_table(frames["zip_representatives.csv"].nlargest(10, "spread_max_km")[[ZIP, "unique_valid_points", "spread_p90_km", "spread_max_km", "representative_sensitivity_km"]]),
                         "## Context Associations\n"+business.audit.markdown_table(frames["associations_by_split.csv"])])+"\n"


def write_features(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite geographic snapshot: {output}")
    if set(frames) != TABLES:
        raise ValueError("Expected all geographic evidence and diagnostic tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".geographic-features-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
        for name, expected in frames.items():
            actual = read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, check_exact=True)
        summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            (staging/name).write_text(json.dumps(value, indent=2)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames))
        manifest = {"status": "complete", "feature_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "identity": business.IDENTITY, "provenance": provenance,
                    "runtime": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
                    "schemas": schemas, "output_hashes": {path.name: business.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        load_snapshot(staging)
        verify_sources(provenance)
        if output.exists():
            raise FileExistsError("Geographic output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = read_json(directory/"manifest.json")
    if (manifest.get("status") != "complete" or manifest.get("feature_version") != VERSION
            or manifest.get("identity") != business.IDENTITY or set(manifest.get("schemas", {})) != TABLES
            or not (TABLES | {"summary.json", "protocol.json", "README.md"}).issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected a complete geographic feature snapshot")
    verify_files(directory, manifest["output_hashes"])
    if read_json(directory/"summary.json") != manifest["summary"] or read_json(directory/"protocol.json") != protocol():
        raise ValueError("Geographic summary/protocol binding differs")
    features = read_table(directory, "order_geographic_features.csv", manifest)
    business.require_key(features, ["order_id"], "saved geographic features")
    if (len(features) != manifest["summary"]["development_orders"] or not features.split.isin(["train", "validation"]).all()
            or not features[CANDIDATES].notna().all(axis=1).eq(features.all_sellers_geocoded).all()):
        raise ValueError("Saved geographic scope or missingness differs")
    return features, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business-features", type=Path, default=business.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying development-only sources and linked ZIP observations.", flush=True)
        inputs, provenance = load_inputs(args.business_features)
        print("Selecting observed representatives and calculating approximate distances.", flush=True)
        frames, summary = build_features(inputs)
        summary = write_features(frames, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Geographic feature build stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
