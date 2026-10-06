"""Build real development-only business features and audit six proposed anomaly groups."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import apply_olist_usage_policy as usage
import export_olist_import_source as exporter
import import_process_data as importer


audit = usage.audit
VERSION = "bed_bath_table_business_features_v1"
DEFAULT_IMPORT = audit.ROOT / "data/imported/olist_bed_bath_table_import_v1"
DEFAULT_OUTPUT = audit.ROOT / "data/experiments/bed_bath_table_business_features_v1"
IDENTITY = {"dataset_id": "olist_bed_bath_table_development_v1", "marketplace_id": "olist",
            "primary_category": "cama_mesa_banho", "timestamp_basis": "source_wall_clock_naive"}
MONEY = "source_currency_units; no currency conversion"
TABLES = {"order_features.csv", "order_context.csv", "payment_source_evidence.csv", "item_source_evidence.csv",
          "advisor_group_coverage.csv", "feature_dictionary.csv", "source_reconciliation.csv",
          "seller_order_links.csv", "seller_source_support.csv", "review_source_evidence.csv", "geolocation_zip_support.csv"}


def code_hashes():
    paths = [Path(__file__), Path(usage.__file__), Path(usage.clean.__file__), Path(audit.__file__),
             Path(importer.__file__), Path(exporter.__file__)]
    return {str(path.relative_to(audit.ROOT)): audit.file_hash(path) for path in paths}


def read_selected(snapshot, name, manifest, key, allowed):
    filename = audit.FILES[name]
    schema = manifest["schemas"][filename]
    dates = [column for column, dtype in schema.items() if dtype.startswith("datetime64")]
    dtypes = {column: "string" if column in dates else dtype for column, dtype in schema.items()}
    parts = []
    for chunk in pd.read_csv(Path(snapshot)/filename, dtype=dtypes, keep_default_na=False,
                             na_values=[""], chunksize=50000, float_precision="round_trip"):
        if chunk.columns.tolist() != list(schema):
            raise ValueError(f"Clean source schema differs: {name}")
        selected = chunk.loc[chunk[key].isin(allowed)].copy()
        selected.insert(0, "clean_source_record", selected.index+1)
        parts.append(selected)
    frame = pd.concat(parts, ignore_index=True)
    for column in dates:
        frame[column] = pd.to_datetime(frame[column], format="%Y-%m-%d %H:%M:%S", errors="raise")
    return frame


def require_key(frame, columns, name):
    if frame[columns].isna().any().any() or frame.duplicated(columns).any():
        raise ValueError(f"Missing or duplicate key in {name}: {columns}")


def require_scope(orders, reservations, config):
    if {key: config[key] for key in IDENTITY} != IDENTITY:
        raise ValueError("This Olist feature profile requires the frozen bed_bath_table development identity")
    require_key(orders, ["order_id"], "imported orders")
    require_key(reservations, ["order_id"], "reservation ledger")
    if not reservations.split.isin(["train", "validation", "test"]).all():
        raise ValueError("Invalid reservation partitions")
    expected = reservations.loc[reservations.split.isin(["train", "validation"])].set_index("order_id").split
    if set(orders.order_id) != set(expected.index) or not orders.set_index("order_id").split.eq(expected.reindex(orders.order_id)).all():
        raise ValueError("Development scope differs from frozen reservations; no reserved test or relabeled cases allowed")
    if not orders.product_category_name.eq(IDENTITY["primary_category"]).all():
        raise ValueError("Imported category differs from the study")


def verify_sources(provenance):
    checks = [(Path(provenance["clean_directory"]), provenance["clean_manifest_sha256"], provenance["clean_output_hashes"]),
              (Path(provenance["import_directory"]), provenance["import_manifest_sha256"], provenance["import_output_hashes"]),
              (Path(provenance["reservation_directory"]), provenance["reservation_manifest_sha256"], provenance["reservation_output_hashes"])]
    for directory, manifest_hash, hashes in checks:
        if audit.file_hash(directory/"manifest.json") != manifest_hash:
            raise ValueError(f"Business-feature source manifest changed: {directory}")
        for name, digest in hashes.items():
            path = (directory/name).resolve()
            if not path.is_relative_to(directory.resolve()) or not path.is_file() or audit.file_hash(path) != digest:
                raise ValueError(f"Business-feature source changed: {directory/name}")
    if provenance["code_hashes"] != code_hashes():
        raise ValueError("Business-feature code changed during execution")


def load_inputs(clean_dir=usage.clean.DEFAULT_OUTPUT, import_dir=DEFAULT_IMPORT, reservation_dir=exporter.bed.DEFAULT_OUTPUT):
    clean_dir, import_dir, reservation_dir = map(Path, [clean_dir, import_dir, reservation_dir])
    before = [audit.file_hash(path/"manifest.json") for path in [clean_dir, import_dir, reservation_dir]]
    clean_manifest = usage.verify_snapshot(clean_dir)
    orders, imported = importer.load_import_snapshot(import_dir)
    reservation_manifest = exporter.verify_source(reservation_dir)
    # Only identifiers and partition markers are parsed from the reserved cohort.
    reservations = pd.read_csv(reservation_dir/"cohort_cases.csv", usecols=["order_id", "split"], dtype="string", keep_default_na=False)
    require_scope(orders, reservations, imported["config"])
    ids = set(orders.order_id)
    tables = {name: read_selected(clean_dir, name, clean_manifest, "order_id", ids)
              for name in ["orders", "order_items", "order_payments", "order_reviews"]}
    for name, key, allowed in [("customers", "customer_id", set(tables["orders"].customer_id)),
                               ("products", "product_id", set(tables["order_items"].product_id)),
                               ("sellers", "seller_id", set(tables["order_items"].seller_id))]:
        tables[name] = read_selected(clean_dir, name, clean_manifest, key, allowed)
    zips = set(tables["customers"].customer_zip_code_prefix.dropna()) | set(tables["sellers"].seller_zip_code_prefix.dropna())
    tables["geolocation"] = read_selected(clean_dir, "geolocation", clean_manifest, "geolocation_zip_code_prefix", zips)
    provenance = {"clean_directory": str(clean_dir.resolve()), "clean_manifest_sha256": before[0],
                  "clean_output_hashes": clean_manifest["output_hashes"], "raw_source_hashes": clean_manifest["sources"],
                  "import_directory": str(import_dir.resolve()), "import_manifest_sha256": before[1],
                  "import_output_hashes": imported["output_hashes"], "reservation_directory": str(reservation_dir.resolve()),
                  "reservation_manifest_sha256": before[2], "reservation_output_hashes": reservation_manifest["output_hashes"],
                  "reserved_test_orders_excluded": int(reservations.split.eq("test").sum()), "code_hashes": code_hashes()}
    verify_sources(provenance)
    validate_sources(orders, tables)
    return orders.sort_values("order_id").reset_index(drop=True), tables, provenance


def validate_sources(orders, tables):
    require_key(orders, ["order_id"], "development orders")
    ids = set(orders.order_id)
    for name in ["orders", "order_items", "order_payments", "order_reviews", "customers", "products", "sellers"]:
        require_key(tables[name], audit.KEYS[name], name)
    for name in ["order_items", "order_payments", "order_reviews"]:
        if not set(tables[name].order_id).issubset(ids):
            raise ValueError(f"Child table outside development scope: {name}")
    raw = tables["orders"].set_index("order_id").sort_index()
    if set(raw.index) != ids:
        raise ValueError("Clean/imported order identities differ")
    imported = orders.set_index("order_id").sort_index()
    columns = ["order_status", *audit.TIMES, "order_estimated_delivery_date"]
    try:
        pd.testing.assert_frame_equal(raw[columns], imported[columns], check_dtype=False, check_exact=True)
    except AssertionError as exc:
        raise ValueError("Clean/imported order observations differ") from exc
    for child, foreign, parent, key in [("orders", "customer_id", "customers", "customer_id"),
                                        ("order_items", "product_id", "products", "product_id"),
                                        ("order_items", "seller_id", "sellers", "seller_id")]:
        if tables[child][foreign].isna().any() or not tables[child][foreign].isin(tables[parent][key]).all():
            raise ValueError(f"Missing core relationship: {child}.{foreign}")
    items = tables["order_items"].merge(tables["products"][["product_id", "product_category_name"]], on="product_id", validate="many_to_one")
    if set(items.order_id) != ids or not items.product_category_name.eq(IDENTITY["primary_category"]).all():
        raise ValueError("Actual items do not reconcile with the single-category cohort")


def minor_units(series):
    result = []
    for value in series:
        if pd.isna(value):
            result.append(pd.NA)
            continue
        amount = Decimal(str(value))*100
        if not amount.is_finite() or amount != amount.to_integral_value():
            raise ValueError("Money requires exact two-decimal source precision; no silent rounding")
        result.append(int(amount))
    return pd.Series(result, index=series.index, dtype="Int64")


def complete_group_sum(frame, column):
    group = frame.groupby("order_id", sort=True)[column]
    return group.sum(min_count=1).where(group.count().eq(frame.groupby("order_id").size()))


def roll_up_payments(payments):
    frame = payments.copy()
    frame["payment_value_minor_units"] = minor_units(frame.payment_value)
    grouped = frame.groupby("order_id", sort=True)
    result = grouped.size().rename("payment_record_count").to_frame()
    result["payment_value_sum_minor_units"] = complete_group_sum(frame, "payment_value_minor_units")
    result["payment_value_sum"] = result.payment_value_sum_minor_units/100
    result["payment_type_count"] = grouped.payment_type.nunique().where(grouped.payment_type.count().eq(result.payment_record_count))
    result["payment_types_json"] = grouped.payment_type.agg(lambda values: json.dumps(sorted(set(values.dropna()))))
    result["payment_installments_max"] = grouped.payment_installments.max().where(grouped.payment_installments.count().eq(result.payment_record_count))
    result["payment_sequential_max"] = grouped.payment_sequential.max()
    for column in ["payment_value", "payment_installments", "payment_type"]:
        result[f"{column}_missing_records"] = result.payment_record_count-grouped[column].count()
    for column in ["payment_value", "payment_installments"]:
        for condition, mask in [("zero", frame[column].eq(0)), ("negative", frame[column].lt(0))]:
            result[f"{column}_{condition}_records"] = mask.fillna(False).groupby(frame.order_id).sum().astype("Int64")
    result["undefined_payment_type_records"] = frame.payment_type.eq("not_defined").fillna(False).groupby(frame.order_id).sum().astype("Int64")
    return result.reset_index(), frame


def roll_up_items(items):
    frame = items.copy()
    frame["price_minor_units"] = minor_units(frame.price)
    frame["freight_minor_units"] = minor_units(frame.freight_value)
    grouped = frame.groupby("order_id", sort=True)
    result = grouped.agg(item_count=("order_item_id", "size"), product_count=("product_id", "nunique"), seller_count=("seller_id", "nunique"))
    for source, prefix in [("price_minor_units", "item_price"), ("freight_minor_units", "freight")]:
        result[f"{prefix}_sum_minor_units"] = complete_group_sum(frame, source)
        result[f"{prefix}_sum"] = result[f"{prefix}_sum_minor_units"]/100
    result["items_plus_freight_minor_units"] = result.item_price_sum_minor_units+result.freight_sum_minor_units
    result["items_plus_freight_sum"] = result.items_plus_freight_minor_units/100
    result["item_price_max"] = grouped.price.max().where(grouped.price.count().eq(result.item_count))
    result["item_price_mean"] = result.item_price_sum/result.item_count
    result["freight_to_item_price_ratio"] = result.freight_sum/result.item_price_sum.where(result.item_price_sum.gt(0))
    result["item_missing_price_records"] = result.item_count-grouped.price.count()
    result["item_missing_freight_records"] = result.item_count-grouped.freight_value.count()
    result["item_negative_amount_records"] = (frame.price.lt(0) | frame.freight_value.lt(0)).fillna(False).groupby(frame.order_id).sum().astype("Int64")
    return result.reset_index(), frame


def customer_history(context):
    result = context[["order_id", "customer_unique_id", "order_purchase_timestamp"]].copy()
    result["customer_prior_orders_in_scope"] = pd.Series(0, index=result.index, dtype="Int64")
    result["customer_days_since_prior_purchase"] = pd.Series(pd.NA, index=result.index, dtype="Float64")
    for _, group in result.groupby("customer_unique_id", sort=False):
        times = group.order_purchase_timestamp.to_numpy(dtype="datetime64[ns]")
        sorted_times = np.sort(times)
        counts = np.searchsorted(sorted_times, times, side="left")
        result.loc[group.index, "customer_prior_orders_in_scope"] = counts
        valid = counts > 0
        gaps = (times[valid]-sorted_times[counts[valid]-1])/np.timedelta64(1, "D")
        result.loc[group.index[valid], "customer_days_since_prior_purchase"] = gaps
    return result[["order_id", "customer_prior_orders_in_scope", "customer_days_since_prior_purchase"]]


def source_support(context, tables):
    geo = tables["geolocation"].copy()
    valid = geo.geolocation_lat.between(-90, 90) & geo.geolocation_lng.between(-180, 180)
    geo["valid_coordinate_pair"] = valid.fillna(False)
    zip_support = geo.groupby("geolocation_zip_code_prefix").agg(
        recorded_coordinate_rows=("clean_source_record", "size"), valid_coordinate_rows=("valid_coordinate_pair", "sum")).reset_index()
    valid_zips = set(zip_support.loc[zip_support.valid_coordinate_rows.gt(0), "geolocation_zip_code_prefix"])
    pairs = tables["order_items"][["order_id", "seller_id"]].drop_duplicates().merge(
        context[["order_id", "split", "order_purchase_timestamp", "customer_zip_code_prefix", "event_time_cutoff_exclusive"]], on="order_id", validate="many_to_one")
    pairs = pairs.merge(tables["sellers"][["seller_id", "seller_zip_code_prefix"]], on="seller_id", validate="many_to_one")
    pairs["customer_zip_has_valid_coordinates"] = pairs.customer_zip_code_prefix.isin(valid_zips)
    pairs["seller_zip_has_valid_coordinates"] = pairs.seller_zip_code_prefix.isin(valid_zips)
    pairs["both_zip_sources_available"] = pairs.customer_zip_has_valid_coordinates & pairs.seller_zip_has_valid_coordinates
    reviews = tables["order_reviews"].merge(context[["order_id", "order_purchase_timestamp", "event_time_cutoff_exclusive"]], on="order_id", validate="many_to_one")
    reviews["valid_review_score"] = reviews.review_score.between(1, 5).fillna(False)
    reviews["review_dates_consistent"] = (reviews.review_creation_date.ge(reviews.order_purchase_timestamp)
                                          & reviews.review_answer_timestamp.ge(reviews.review_creation_date)).fillna(False)
    reviews["answer_before_partition_cutoff"] = reviews.review_answer_timestamp.lt(reviews.event_time_cutoff_exclusive).fillna(False)
    reviews["usable_answer_before_partition_cutoff"] = reviews.valid_review_score & reviews.review_dates_consistent & reviews.answer_before_partition_cutoff
    review_ids = set(reviews.loc[reviews.usable_answer_before_partition_cutoff, "order_id"])
    pairs["has_usable_order_review_before_cutoff"] = pairs.order_id.isin(review_ids)
    pairs["order_seller_count"] = pairs.groupby("order_id").seller_id.transform("size")
    pairs["review_is_order_level_not_seller_attributed"] = True
    seller_support = pairs.groupby(["split", "seller_id"]).agg(
        linked_orders=("order_id", "nunique"), orders_with_review_before_cutoff=("has_usable_order_review_before_cutoff", "sum"),
        orders_with_both_zip_sources=("both_zip_sources_available", "sum")).reset_index()
    return {"seller_order_links.csv": pairs.sort_values(["order_id", "seller_id"]).reset_index(drop=True),
            "seller_source_support.csv": seller_support, "review_source_evidence.csv": reviews.sort_values(["order_id", "review_id"]).reset_index(drop=True),
            "geolocation_zip_support.csv": zip_support}


def feature_dictionary(features):
    definitions = {
        "payment_record_count": ("payment", "count", "Count observed payment rows per order; not failed attempts or installment count."),
        "payment_value_sum": ("transaction_value", MONEY, "Sum payment_value once per source payment row; blank if no rows or any value missing. Do not multiply by installments."),
        "payment_type_count": ("payment", "count", "Distinct observed payment_type codes; not_defined remains a code, not an inferred method."),
        "payment_installments_max": ("payment", "installments", "Maximum installments over payment rows; blank if any installment count is missing."),
        "payment_sequential_max": ("payment", "sequence index", "Maximum recorded payment sequence index; not a retry count or timestamp."),
        "item_count": ("order", "count", "Count actual order_item_id rows; not unique product count."),
        "product_count": ("order", "count", "Distinct product IDs within the order."),
        "seller_count": ("order", "count", "Distinct seller IDs; never choose one arbitrary seller for a multi-seller order."),
        "item_price_sum": ("order", MONEY, "Sum item price once per item; blank if any source price is missing."),
        "freight_sum": ("order", MONEY, "Sum observed item freight once per item; zero freight is preserved."),
        "items_plus_freight_sum": ("order", MONEY, "item_price_sum + freight_sum; never used to fill missing payments."),
        "item_price_max": ("order", MONEY, "Maximum item price; blank if any item price is missing."),
        "item_price_mean": ("order", MONEY, "item_price_sum / item_count when available."),
        "freight_to_item_price_ratio": ("order", "ratio", "freight_sum / item_price_sum; blank if denominator is missing or <= 0."),
        "payment_minus_items_and_freight": ("transaction_value", MONEY, "Payment total minus item and freight totals, calculated in exact minor units; discrepancy is not a fraud label."),
        "customer_prior_orders_in_scope": ("customer", "orders", "Count distinct development/category orders with same customer_unique_id and strictly earlier purchase time; ties excluded."),
        "customer_days_since_prior_purchase": ("customer", "days", "Days since most recent strictly earlier purchase in this development/category scope; blank without history."),
    }
    for name in audit.DURATIONS:
        definitions[name] = ("order", "days", "Reuse imported duration only when timing_input_eligible; all original timestamps/durations stay in order_context.csv.")
    rows = []
    for name in features:
        group, unit, formula = definitions.get(name, ("audit", "metadata", "Observed lineage, availability or source-quality metadata; not an automatic predictor."))
        if name in audit.DURATIONS:
            availability = "completed_milestones_before_partition_cutoff"
        elif name.startswith("customer_prior_") or name == "customer_days_since_prior_purchase":
            availability = "strictly_prior_purchase_events_in_scope; source arrival times unknown"
        elif group == "audit":
            availability = "context_only"
        else:
            availability = "retrospective_snapshot; item/payment record availability times not supplied"
        rows.append({"column": name, "group": group, "unit": unit, "definition": formula,
                     "role": "candidate_feature" if name in definitions else "audit_not_predictor",
                     "availability": availability, "online_ready": False,
                     "missing_policy": "Keep missing; zero counts only mean zero observed child/history records."})
    return pd.DataFrame(rows)


def build_features(orders, tables):
    validate_sources(orders, tables)
    context = orders.sort_values("order_id").reset_index(drop=True).copy()
    links = tables["orders"][["order_id", "customer_id", "clean_source_record"]].rename(columns={"clean_source_record": "clean_order_record"})
    context = context.merge(links, on="order_id", validate="one_to_one").merge(
        tables["customers"][["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_state"]], on="customer_id", validate="many_to_one")
    if context.customer_unique_id.isna().any():
        raise ValueError("Missing customer identity; do not invent a history group")
    payments, payment_evidence = roll_up_payments(tables["order_payments"])
    items, item_evidence = roll_up_items(tables["order_items"])
    features = context[["order_id", "dataset_id", "marketplace_id", "product_category_name", "split",
                        "order_purchase_timestamp", "event_time_cutoff_exclusive", "timing_input_eligible"]].merge(
        payments, on="order_id", how="left", validate="one_to_one").merge(items, on="order_id", how="left", validate="one_to_one")
    for count in ["payment_record_count", "item_count", "product_count", "seller_count"]:
        features[count] = features[count].fillna(0).astype("Int64")
    features["has_payment_records"] = features.payment_record_count.gt(0)
    features["payment_values_complete"] = features.has_payment_records & features.payment_value_sum.notna()
    features["payment_values_nonnegative"] = features.payment_values_complete & features.payment_value_negative_records.eq(0).fillna(False)
    features["item_values_complete"] = features.item_count.gt(0) & features.items_plus_freight_sum.notna()
    features["payment_minus_items_and_freight_minor_units"] = features.payment_value_sum_minor_units-features.items_plus_freight_minor_units
    features["payment_minus_items_and_freight"] = features.payment_minus_items_and_freight_minor_units/100
    features["business_fields_temporal_status"] = "retrospective_snapshot_availability_unknown"
    features = features.merge(customer_history(context), on="order_id", validate="one_to_one")
    for name in audit.DURATIONS:
        features[name] = context.set_index("order_id")[name].reindex(features.order_id).to_numpy()
        features.loc[~features.timing_input_eligible, name] = np.nan
    if len(features) != len(orders) or not features.order_id.is_unique or set(features.order_id) != set(orders.order_id):
        raise ValueError("Feature enrichment changed order grain")
    supports = source_support(context, tables)
    pairs, reviews = supports["seller_order_links.csv"], supports["review_source_evidence.csv"]
    coverage = [
        (1, "transaction_value", "features_built_not_scored", int(features.payment_values_complete.sum()),
         "payment_value_sum; payment_minus_items_and_freight", "High value requires a fitted peer reference; payment timing unknown."),
        (2, "payment", "features_built_not_scored", int(features.has_payment_records.sum()),
         "payment_type_count; payment_installments_max; payment_sequential_max; payment_record_count", "Multiple rows can be legitimate split payments; no failed-attempt events."),
        (3, "customer", "partial_history_features_built_not_scored", int(features.customer_prior_orders_in_scope.gt(0).sum()),
         "customer_prior_orders_in_scope; customer_days_since_prior_purchase", "History is left-truncated and category/development-only; no peer-based customer anomaly model yet."),
        (4, "seller", "source_links_audited_not_scored", int(pairs.order_id.nunique()),
         "seller_order_links.csv; seller_source_support.csv; review_source_evidence.csv", "Cancellation timestamp unavailable; ratings are order-level. Historical seller-rate model needs an explicit as-of policy."),
        (5, "order", "features_built_not_scored", int(features.item_values_complete.sum()),
         "item/product/seller counts; price/freight; existing eligible durations", "Item snapshot fields lack arrival timestamps; duration eligibility is separate from monetary availability."),
        (6, "geography", "zip_sources_audited_not_scored", int(pairs.groupby("order_id").both_zip_sources_available.all().sum()),
         "geolocation_zip_support.csv; seller_order_links.csv", "Multiple coordinates per ZIP; no representative coordinate, route distance or geographic anomaly model is invented."),
    ]
    coverage = pd.DataFrame(coverage, columns=["advisor_group", "group", "implementation_status", "orders_with_stated_support", "implemented_evidence", "limitation"])
    coverage["support_denominator_orders"] = len(orders)
    coverage["support_definition"] = ["All observed payment values present", "At least one observed payment row", "At least one strictly earlier scoped purchase",
                                      "At least one linked seller", "All observed item prices and freight present", "Both ZIP sources available for every linked seller"]
    counts = pd.DataFrame([{"table": name, "retained_source_rows": len(frame),
                           "distinct_orders": int(frame.order_id.nunique()) if "order_id" in frame else pd.NA}
                          for name, frame in tables.items()]).astype({"distinct_orders": "Int64"})
    frames = {"order_features.csv": features, "order_context.csv": context,
              "payment_source_evidence.csv": payment_evidence.sort_values(["order_id", "payment_sequential"]).reset_index(drop=True),
              "item_source_evidence.csv": item_evidence.sort_values(["order_id", "order_item_id"]).reset_index(drop=True),
              "advisor_group_coverage.csv": coverage, "feature_dictionary.csv": feature_dictionary(features),
              "source_reconciliation.csv": counts, **supports}
    summary = {"identity": IDENTITY, "development_orders": len(features), "split_orders": features.split.value_counts().to_dict(),
               "payment_records": len(payment_evidence), "item_records": len(item_evidence),
               "orders_without_payment_records": int((~features.has_payment_records).sum()),
               "orders_with_multiple_payment_records": int(features.payment_record_count.gt(1).sum()),
               "orders_with_multiple_sellers": int(features.seller_count.gt(1).sum()),
               "orders_with_zero_payment_value_records": int(features.payment_value_zero_records.gt(0).sum()),
               "orders_with_zero_installment_records": int(features.payment_installments_zero_records.gt(0).sum()),
               "orders_with_nonzero_payment_item_difference": int(features.payment_minus_items_and_freight_minor_units.ne(0).fillna(False).sum()),
               "unique_customers_in_scope": int(context.customer_unique_id.nunique()),
               "orders_with_prior_scoped_purchases": int(features.customer_prior_orders_in_scope.gt(0).sum()),
               "orders_with_timing_features": int(features.timing_input_eligible.sum()),
               "orders_without_timing_features_retained": int((~features.timing_input_eligible).sum()),
               "orders_with_all_seller_zip_sources": int(pairs.groupby("order_id").both_zip_sources_available.all().sum()),
               "review_records": len(reviews), "review_records_usable_before_partition_cutoff": int(reviews.usable_answer_before_partition_cutoff.sum()),
               "candidate_feature_columns": frames["feature_dictionary.csv"].query("role == 'candidate_feature'").column.tolist(),
               "advisor_groups_audited": 6, "training_performed": False, "thresholds_learned": False,
               "test_features_built": False, "test_scored": False, "human_labels_created": False,
               "fraud_labels_created": False, "anomaly_labels_created": False, "accuracy_computed": False,
               "online_ready": False, "imputation_performed": False, "existing_models_changed": False}
    return frames, summary


def render_readme(summary, frames):
    return "\n\n".join([
        "# Business features following the six advisor groups",
        "## Actual output\n```json\n" + json.dumps(summary, indent=2) + "\n```",
        "## Scope\nOnly the exact frozen Olist bed_bath_table development orders are enriched. "
        "The reservation ledger is parsed for IDs and split markers only. Clean source tables are filtered to these orders "
        "and their linked products/customers/sellers before feature computation. No test feature matrix or human-review labels "
        "are parsed. Original raw/clean data, models, thresholds and reviews remain unchanged. No synthetic records are generated.",
        "## Six groups\n" + audit.markdown_table(frames["advisor_group_coverage.csv"]),
        "## Joins and money\nPayments and items are aggregated independently before one-to-one order joins. "
        "Each observed payment amount is counted exactly once, never multiplied by installments or repeated over item rows. "
        "Money is converted to integer minor units only after exact two-decimal validation; there is no silent rounding. "
        "Source currency is not converted. A missing child table produces zero OBSERVED row count, not a zero monetary total. "
        "A group with any missing monetary field keeps its aggregate missing instead of reporting a partial sum. "
        "Negative, zero and large observations remain evidence; a discrepancy is not automatically an error, anomaly or fraud.",
        "## Time and identities\nExisting timing features retain the imported eligibility mask; excluded cases remain in order_context.csv "
        "with every original timestamp and duration. Business snapshot features are not claimed to have been known at purchase or partition cutoff: "
        "there are no payment-event/record-arrival timestamps. Do not pass this whole table into the current three-duration training profile. "
        "A new training profile must explicitly choose features, transformations and a retrospective or event-availability protocol. "
        "customer_unique_id links purchases; customer_id is only the order/customer join key. Prior purchase counts use strictly earlier "
        "distinct orders in this selected category/development window, exclude simultaneous purchases and never include future orders. "
        "They do not describe lifetime history or prove online availability of all source records.",
        "## Seller and geography limits\nAll seller/order links are retained without arbitrary single-seller assignment. "
        "Ratings are order-level evidence, not labels attributed to every seller. Review usability at a partition cutoff is only "
        "a retrospective source audit, not a seller feature at the earlier purchase time. No cancellation timestamp is invented "
        "and no historical cancellation rate is produced. ZIP support counts actual valid coordinate pairs from the clean snapshot, "
        "without generating coordinates, routes, distances or automatically discarding valid but unusual coordinates. "
        "These two groups are audited, not finished anomaly detectors.",
        "## Semi-manual review and evaluation\nThese features supply evidence, not ground-truth labels. A later review protocol "
        "must define sampling of both flagged and unflagged cases, allowed evidence, group-specific criteria, reviewer identity, "
        "reason codes and uncertainty. Existing reviewed cases must not be relabeled or counted repeatedly. No extra human review "
        "is required to run this feature builder, but independent human evaluation cannot be replaced by copying detector flags. "
        "No fraud label, accuracy, calibrated probability or winning method is claimed here.",
        "## Files\norder_features.csv is one row per real order; feature_dictionary.csv defines every column, units and availability. "
        "Source evidence files retain original fields plus derived columns and 1-based clean_source_record positions excluding headers. "
        "These positions refer to standardized source tables, not arbitrary join row numbers. "
        "source_reconciliation.csv records retained rows; manifest.json binds source/code/output hashes and logical schemas. "
        "load_feature_snapshot() verifies all output hashes and applies the recorded schema. Use a new output directory for each revision.",
        "## Next step\nAgree the retrospective/online feature contract and semi-manual evaluation protocol, then compare a new "
        "business-feature model profile against the preserved timing baseline. Fit transformations and thresholds on training/calibration "
        "only. Seller behavior, geographic distances, multi-agent coordination and real-time replay remain separate implementation work.",
    ]) + "\n"


def write_features(frames, summary, provenance, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite business features: {output}")
    if set(frames) != TABLES:
        raise ValueError("Expected all business feature and evidence tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging/"summary.json").write_text(json.dumps(summary, indent=2)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames), encoding="utf-8")
        manifest = {"status": "complete", "feature_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "provenance": provenance, "pandas_version": pd.__version__, "numpy_version": np.__version__,
                    "schemas": {name: {column: str(dtype) for column, dtype in frame.dtypes.items()} for name, frame in frames.items()},
                    "output_hashes": {path.name: audit.file_hash(path) for path in sorted(staging.iterdir())}}
        verify_sources(provenance)
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        load_feature_snapshot(staging)
        for name, expected in frames.items():
            restored = usage.read_snapshot_table(staging, name, manifest)
            pd.testing.assert_frame_equal(restored, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
        if output.exists():
            raise FileExistsError(f"Business-feature output appeared during publication: {output}")
        staging.rename(output)


def load_feature_snapshot(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"manifest.json").read_text(), object_pairs_hook=importer.unique_json_keys)
    required = TABLES | {"summary.json", "README.md"}
    if manifest.get("status") != "complete" or manifest.get("feature_version") != VERSION or not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Expected a complete business-feature snapshot")
    if set(manifest.get("schemas", {})) != TABLES or manifest.get("summary", {}).get("identity") != IDENTITY:
        raise ValueError("Business-feature identity or schemas differ")
    for name, digest in manifest["output_hashes"].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or audit.file_hash(path) != digest:
            raise ValueError(f"Business-feature output hash mismatch: {name}")
    if json.loads((directory/"summary.json").read_text()) != manifest["summary"]:
        raise ValueError("Business-feature summary differs from manifest")
    features = usage.read_snapshot_table(directory, "order_features.csv", manifest)
    require_key(features, ["order_id"], "saved business features")
    if len(features) != manifest["summary"]["development_orders"] or not features.split.isin(["train", "validation"]).all():
        raise ValueError("Saved business-feature scope differs")
    return features, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", type=Path, default=usage.clean.DEFAULT_OUTPUT)
    parser.add_argument("--imported", type=Path, default=DEFAULT_IMPORT)
    parser.add_argument("--reservations", type=Path, default=exporter.bed.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    try:
        print("Verifying frozen sources and selecting real development records.", flush=True)
        orders, tables, provenance = load_inputs(args.clean, args.imported, args.reservations)
        print("Aggregating payments/items independently and auditing all six advisor groups.", flush=True)
        frames, summary = build_features(orders, tables)
        summary["reserved_test_orders_excluded"] = provenance["reserved_test_orders_excluded"]
        write_features(frames, summary, provenance, args.output)
        restored, _ = load_feature_snapshot(args.output)
        pd.testing.assert_frame_equal(restored, frames["order_features.csv"], check_dtype=False, rtol=1e-12, atol=1e-12)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Business-feature build stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
