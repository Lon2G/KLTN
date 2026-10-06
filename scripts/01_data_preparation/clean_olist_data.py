"""Create a traceable Olist preparation snapshot; never impute anomaly evidence."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import tempfile

import numpy as np
import pandas as pd

import olist_import_audit as audit


DEFAULT_OUTPUT = audit.PROCESSED_DATA_DIR / "olist_clean_v1"
TEXT = {
    "orders": ["order_id", "customer_id", "order_status"],
    "order_items": ["order_id", "product_id", "seller_id"],
    "products": ["product_id", "product_category_name"],
    "customers": ["customer_id", "customer_unique_id", "customer_zip_code_prefix", "customer_city", "customer_state"],
    "sellers": ["seller_id", "seller_zip_code_prefix", "seller_city", "seller_state"],
    "order_payments": ["order_id", "payment_type"],
    "order_reviews": ["review_id", "order_id", "review_comment_title", "review_comment_message"],
    "geolocation": ["geolocation_zip_code_prefix", "geolocation_city", "geolocation_state"],
    "translation": ["product_category_name", "product_category_name_english"],
}
DATES = {
    "orders": audit.TIMES + ["order_estimated_delivery_date"],
    "order_items": ["shipping_limit_date"],
    "order_reviews": ["review_creation_date", "review_answer_timestamp"],
}
INTEGERS = {
    "order_items": ["order_item_id"],
    "order_payments": ["payment_sequential", "payment_installments"],
    "order_reviews": ["review_score"],
    "products": ["product_name_lenght", "product_description_lenght", "product_photos_qty",
                 "product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"],
}
FLOATS = {
    "order_items": ["price", "freight_value"],
    "order_payments": ["payment_value"],
    "geolocation": ["geolocation_lat", "geolocation_lng"],
}
STATUSES = {"delivered", "shipped", "canceled", "unavailable", "invoiced", "processing", "created", "approved"}
PHYSICAL = ["product_weight_g", "product_length_cm", "product_height_cm", "product_width_cm"]
FREE_TEXT = {"review_comment_title", "review_comment_message"}
ISSUE_COLUMNS = ["table", "source_record", "record_key", "field", "issue_code", "observed_value", "action"]


def normalize_table(name, source):
    expected = set(TEXT[name] + DATES.get(name, []) + INTEGERS.get(name, []) + FLOATS.get(name, []))
    if set(source.columns) != expected:
        raise ValueError(f"Unexpected schema in {name}: missing={expected - set(source)}, extra={set(source) - expected}")
    frame = source.copy()
    actions = []

    def record(column, operation, count):
        actions.append({"table": name, "column": column, "operation": operation, "affected_cells": int(count)})

    for column in frame:
        if column in DATES.get(name, []) and pd.api.types.is_datetime64_any_dtype(frame[column]):
            original = frame[column].dt.strftime("%Y-%m-%d %H:%M:%S").astype("string")
        else:
            original = frame[column].astype("string")
        cleaned = original if column in FREE_TEXT else original.str.strip()
        record(column, "trim_surrounding_whitespace", (original.fillna("") != cleaned.fillna("")).sum())
        record(column, "empty_string_to_missing", cleaned.eq("").sum())
        cleaned = cleaned.mask(cleaned.eq(""))
        if column in TEXT[name] and column not in FREE_TEXT:
            canonical = cleaned.str.normalize("NFC")
            canonical = canonical.str.upper() if column.endswith("_state") else canonical.str.lower()
            record(column, "canonical_case_and_unicode", (cleaned.fillna("") != canonical.fillna("")).sum())
            cleaned = canonical
        if "zip_code_prefix" in column:
            if (~cleaned.dropna().str.fullmatch(r"[0-9]{1,5}")).any():
                raise ValueError(f"Invalid ZIP prefix in {name}.{column}")
            padded = cleaned.str.zfill(5)
            record(column, "zip_prefix_pad_to_five_digits", (cleaned.fillna("") != padded.fillna("")).sum())
            cleaned = padded
        if column in TEXT[name] and column.endswith("_id"):
            if (~cleaned.dropna().str.fullmatch(r"[0-9a-f]{32}")).any():
                raise ValueError(f"Invalid identifier in {name}.{column}")
        if column in DATES.get(name, []):
            if (~cleaned.dropna().str.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")).any():
                raise ValueError(f"Invalid timestamp format in {name}.{column}")
            cleaned = pd.to_datetime(cleaned, format="%Y-%m-%d %H:%M:%S", errors="raise")
            record(column, "parse_timestamp_without_timezone_conversion", cleaned.notna().sum())
        elif column in INTEGERS.get(name, []) + FLOATS.get(name, []):
            cleaned = pd.to_numeric(cleaned, errors="raise")
            if not np.isfinite(cleaned.dropna().to_numpy(dtype=float)).all():
                raise ValueError(f"Non-finite numeric value in {name}.{column}")
            cleaned = cleaned.astype("Int64" if column in INTEGERS.get(name, []) else "Float64")
            record(column, "parse_numeric_without_imputation", cleaned.notna().sum())
        frame[column] = cleaned
    return frame, actions


def deduplicate_geolocation(frame):
    # Track original data-record positions, not physical CSV lines (text can contain newlines).
    positions = pd.Series(frame.index + 1, index=frame.index, name="source_record")
    first = positions.groupby([frame[c] for c in frame], sort=False, dropna=False).transform("min")
    duplicate = positions.ne(first)
    lineage = pd.DataFrame({"source_record": positions[duplicate], "kept_source_record": first[duplicate]})
    return frame.loc[~duplicate].copy(), lineage.reset_index(drop=True)


def build_quality_issues(tables, cases):
    parts = []

    def add(name, frame, mask, field, code, action="retain_for_review", values=None):
        selected = frame.loc[mask.fillna(False)]
        if selected.empty:
            return
        keys = audit.KEYS.get(name, ["geolocation_zip_code_prefix"])
        key_json = selected[keys].astype("string").apply(
            lambda row: json.dumps({c: None if pd.isna(v) else v for c, v in row.items()}), axis=1,
        )
        observed = selected[field].astype("string") if values is None else values.loc[selected.index].astype("string")
        parts.append(pd.DataFrame({
            "table": name, "source_record": selected.index + 1, "record_key": key_json,
            "field": field, "issue_code": code, "observed_value": observed, "action": action,
        }))

    for name, columns in DATES.items():
        for column in columns:
            add(name, tables[name], tables[name][column].isna(), column, "missing_timestamp", "retain_missing_no_imputation")
    products = tables["products"]
    for column in ["product_category_name", *INTEGERS["products"]]:
        add("products", products, products[column].isna(), column, "missing_product_attribute", "retain_missing_no_imputation")
    for name, columns in INTEGERS.items():
        for column in columns:
            add(name, tables[name], tables[name][column].lt(0), column, "negative_numeric_value")
    for name, columns in FLOATS.items():
        if name != "geolocation":
            for column in columns:
                add(name, tables[name], tables[name][column].lt(0), column, "negative_numeric_value")
    for column in PHYSICAL:
        add("products", products, products[column].eq(0), column, "zero_physical_measurement", "retain_exclude_this_measurement")
    payments = tables["order_payments"]
    for column in ["payment_value", "payment_installments"]:
        add("order_payments", payments, payments[column].eq(0), column, "zero_payment_field_needs_context")
    add("order_payments", payments, payments.payment_type.eq("not_defined"), "payment_type", "undefined_payment_type")
    reviews = tables["order_reviews"]
    add("order_reviews", reviews, ~reviews.review_score.between(1, 5) | reviews.review_score.isna(),
        "review_score", "invalid_review_score")
    add("order_reviews", reviews, reviews.review_answer_timestamp.lt(reviews.review_creation_date),
        "review_answer_timestamp", "review_answer_before_creation")
    for name, fk, parent, pk, blocking in audit.RELATIONS:
        if not blocking:
            source = tables[name]
            add(name, source, source[fk].notna() & ~source[fk].isin(tables[parent][pk]), fk,
                "unmatched_optional_reference", "retain_no_inferred_mapping")
    geo = tables["geolocation"]
    for column, limit in [("geolocation_lat", 90), ("geolocation_lng", 180)]:
        add("geolocation", geo, geo[column].abs().gt(limit) | geo[column].isna(), column,
            "invalid_earth_coordinate", "retain_exclude_this_coordinate")

    orders = tables["orders"]
    context = cases.set_index("order_id").reindex(orders.order_id).reset_index()
    context.index = orders.index
    for column in audit.DURATIONS:
        add("orders", orders, context[column].lt(0), column, "negative_transition_duration",
            "retain_temporal_evidence", context[column])
    add("orders", orders, context.has_reversed_recorded_milestones, "order_purchase_timestamp",
        "recorded_milestones_out_of_order", "retain_temporal_evidence")
    delivered = orders.order_status.eq("delivered")
    add("orders", orders, delivered & orders.order_delivered_customer_date.isna(), "order_status",
        "delivered_without_delivery_timestamp")
    add("orders", orders, ~delivered & orders.order_delivered_customer_date.notna(), "order_status",
        "non_delivered_status_with_delivery_timestamp")
    for column in ["item_count", "payment_record_count", "review_record_count"]:
        add("orders", orders, context[column].eq(0), column, "missing_child_records",
            "retain_missing_no_imputation", context[column])
    if not parts:
        return pd.DataFrame(columns=ISSUE_COLUMNS)
    return pd.concat(parts, ignore_index=True)[ISSUE_COLUMNS].sort_values(
        ["table", "source_record", "field", "issue_code"], kind="stable",
    ).reset_index(drop=True)


def build_order_quality(tables):
    cases, _, _ = audit.build_order_categories(tables)
    original_position = pd.Series(tables["orders"].index + 1, index=tables["orders"].order_id)
    cases.insert(0, "source_record", cases.order_id.map(original_position))
    cases["category_assignment_eligible"] = cases.category_scope.eq("single_category")
    cases["completed_timing_eligible"] = cases.delivered_complete_nondecreasing
    cases["single_category_timing_eligible"] = cases.category_assignment_eligible & cases.completed_timing_eligible
    cases["status_delivery_conflict"] = (
        cases.order_status.eq("delivered") != cases.order_delivered_customer_date.notna()
    )
    cases["temporal_review_required"] = (
        cases.missing_milestone_count.gt(0) | cases.has_reversed_recorded_milestones | cases.status_delivery_conflict
    )
    products = tables["products"]
    invalid_physical = products[PHYSICAL].isna().any(axis=1) | products[PHYSICAL].le(0).any(axis=1)
    bad_products = products.loc[invalid_physical, "product_id"]
    bad_orders = tables["order_items"].loc[tables["order_items"].product_id.isin(bad_products), "order_id"]
    cases["product_measurement_review_required"] = cases.order_id.isin(bad_orders)
    return cases


def build_event_log(orders):
    parts = []
    for rank, (column, activity) in enumerate(zip(audit.TIMES, ["Purchased", "Approved", "Handed to Carrier", "Delivered"])):
        part = orders.loc[orders[column].notna(), ["order_id", column]].rename(
            columns={"order_id": "case_id", column: "timestamp"},
        )
        part["activity"], part["_rank"] = activity, rank
        parts.append(part)
    return pd.concat(parts, ignore_index=True).sort_values(
        ["case_id", "timestamp", "_rank"], kind="stable",
    )[["case_id", "activity", "timestamp"]].reset_index(drop=True)


def prepare_dataset(raw_dir=audit.RAW_DATA_DIR):
    tables, summaries, actions, sources = {}, [], [], {}
    for name, filename in audit.FILES.items():
        path = raw_dir / filename
        before_hash = audit.file_hash(path)
        source = pd.read_csv(path, dtype="string", keep_default_na=False)
        if audit.file_hash(path) != before_hash:
            raise ValueError(f"Input changed while reading: {path}")
        sources[filename] = {"sha256": before_hash, "rows": len(source)}
        frame, table_actions = normalize_table(name, source)
        raw_duplicates = int(source.duplicated().sum())
        if name == "geolocation":
            frame, duplicates = deduplicate_geolocation(frame)
        tables[name] = frame
        actions.extend(table_actions)
        summaries.append({"table": name, "input_rows": len(source), "output_rows": len(frame),
                          "raw_exact_duplicate_excess_rows": raw_duplicates,
                          "removed_after_normalization": len(source) - len(frame),
                          "remaining_missing_cells": int(frame.isna().sum().sum())})
    if tables["orders"].order_status.isna().any() or not set(tables["orders"].order_status).issubset(STATUSES):
        raise ValueError("Unknown or missing order status; update the explicit status policy before processing")
    relations = audit.validate_core(tables)
    cases = build_order_quality(tables)
    events = build_event_log(tables["orders"])
    issues = build_quality_issues(tables, cases)
    return {"tables": tables, "summary": pd.DataFrame(summaries), "actions": pd.DataFrame(actions),
            "sources": sources, "duplicates": duplicates, "relations": relations,
            "cases": cases, "events": events, "issues": issues}


def render_readme(bundle):
    summary, cases, issues = bundle["summary"], bundle["cases"], bundle["issues"]
    counts = issues.groupby(["table", "issue_code"]).size().reset_index(name="issue_entries")
    return "\n\n".join([
        "# Olist standardized snapshot v1",
        "## Meaning of clean\nStructurally standardized and traceable, NOT guaranteed error-free business data or a normal-only sample. All observed orders/items/payments/reviews/products/customers/sellers are retained. The only removed records are identical geolocation observations after deterministic normalization. This snapshot does not select an industry, change old pipeline outputs, train a model or create human/anomaly labels.",
        "## Table reconciliation\n" + audit.markdown_table(summary),
        "## Fixed normalization policy\nEmpty fields become missing values, never zero. Non-free-text fields are trimmed and Unicode-NFC normalized. IDs/categories/cities are lowercase, state codes uppercase, and numeric ZIP prefixes are stored as five-character strings. Review text is preserved verbatim, including its whitespace and accents. Dates must match YYYY-MM-DD HH:MM:SS and are parsed without timezone conversion. No extraction date or timezone is inferred. Numeric columns use explicit nullable integer/float types without filling, clipping or winsorizing. Original column names, including source spelling, are retained. CSV consumers must apply the logical dtypes recorded in manifest.json, especially string ZIP prefixes and IDs.",
        "Exact geolocation duplicates are removed only from this snapshot. audit/geolocation_duplicates.csv maps every removed source record to its retained source record. source_record is 1-based data-record position excluding the header, NOT a physical CSV line number. Index positions are captured before normalization. Multiple distinct coordinates/city spellings for the same ZIP remain separate; no centroid, city correction, geocoding or country-border validation is performed. Geolocation is not joined directly to orders.",
        "## Remaining issues\n" + audit.markdown_table(counts),
        "Issue entries are not distinct cases and can overlap. audit/quality_issues.csv contains source positions, record keys, observed values and actions. Missing optional review comments are not errors. Zero freight is retained without an error flag. Zero product dimensions/weight and zero payment fields are retained with context-specific review flags. Unknown product categories and missing translations stay unknown. ZIP references that do not match the geolocation table remain unmatched. No substitute observations are invented.",
        "## Order and event coverage\n"
        f"Orders retained: {len(cases):,}. Events from actual nonmissing milestones: {len(bundle['events']):,}. "
        f"Orders with missing milestones: {int(cases.missing_milestone_count.gt(0).sum()):,}. "
        f"Orders with recorded milestone reversals: {int(cases.has_reversed_recorded_milestones.sum()):,}. "
        f"Orders requiring temporal/status review: {int(cases.temporal_review_required.sum()):,}.",
        "event_log.csv uses order_id as case_id, emits no synthetic events and keeps every recorded timestamp. Equal timestamps use the fixed reference activity order only for stable display, not proof of causal order. Missing timestamps produce no event and remain visible in order_quality.csv. Reversed and zero durations are preserved.",
        "## Feature-specific eligibility\norder_quality.csv retains ALL orders and makes eligibility explicit. category_assignment_eligible requires at least one item, exactly one known category and no unknown-category items. completed_timing_eligible requires delivered status, all four actual milestones and nondecreasing timestamps. single_category_timing_eligible requires both. These flags are NOT normal/anomaly labels and NOT a final training split. Long durations, zero durations and other potential anomalies remain included when the required fields exist. product_measurement_review_required is independent and does not silently exclude an order from timing analysis. Missing data and reversals stay available for process/anomaly analysis even when ineligible for completed-duration comparisons.",
        "## Fair comparisons later\nApply the SAME eligibility rules to every category and show both all-order counts and excluded counts with reasons. Compare matched periods and account for status/seller/region composition when appropriate. Complete-case filtering can introduce selection bias; this snapshot cannot guarantee unbiased comparisons. No ranking is recomputed here. Fit learned imputation/scaling/thresholds only on future training data, never on the entire snapshot. The source extraction cutoff and true business causes of missing/reversed timestamps remain unknown.",
        "## Files and reproducibility\nThe nine named source-table CSVs contain standardized observations. order_quality.csv and event_log.csv contain derived order/event views. audit/ contains actions, row reconciliation, issues, duplicate lineage and relationship checks. manifest.json records raw/output hashes, code hashes, runtime versions and logical schemas. A completed manifest is required before consuming a snapshot. Existing output directories are never overwritten.",
        "Run `.venv/bin/python scripts/01_data_preparation/clean_olist_data.py` from the project root for the default snapshot. For a new version, pass `--output data/processed/olist_clean_v2`. The previous baseline scripts still point to their old inputs; downstream migration and category comparison are deliberately deferred.",
    ]) + "\n"


def write_snapshot(bundle, output_dir=DEFAULT_OUTPUT, raw_dir=audit.RAW_DATA_DIR):
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"Snapshot already exists, choose a new version: {output_dir}")
    for filename, source in bundle["sources"].items():
        if audit.file_hash(raw_dir / filename) != source["sha256"]:
            raise ValueError(f"Raw source changed since preparation: {filename}")
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output_dir.name}-", dir=output_dir.parent) as temporary:
        staging = Path(temporary)
        (staging / "audit").mkdir()
        frames = {audit.FILES[name]: frame for name, frame in bundle["tables"].items()}
        frames.update({"order_quality.csv": bundle["cases"], "event_log.csv": bundle["events"],
                       "audit/table_reconciliation.csv": bundle["summary"],
                       "audit/cleaning_actions.csv": bundle["actions"],
                       "audit/quality_issues.csv": bundle["issues"],
                       "audit/geolocation_duplicates.csv": bundle["duplicates"],
                       "audit/relationships.csv": bundle["relations"]})
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "README.md").write_text(render_readme(bundle), encoding="utf-8")
        manifest = {
            "status": "complete", "policy_version": "olist_standardization_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "python_version": sys.version.split()[0], "pandas_version": pd.__version__,
            "source_directory": str(Path(raw_dir).resolve()), "sources": bundle["sources"],
            "code_hashes": {path.name: audit.file_hash(path) for path in [Path(__file__), Path(audit.__file__)]},
            "output_hashes": {str(p.relative_to(staging)): audit.file_hash(p) for p in sorted(staging.rglob("*")) if p.is_file()},
            "schemas": {filename: {c: str(t) for c, t in frame.dtypes.items()} for filename, frame in frames.items()},
            "all_orders_preserved": len(bundle["cases"]) == bundle["sources"][audit.FILES["orders"]]["rows"],
            "source_event_count": int(bundle["tables"]["orders"][audit.TIMES].notna().sum().sum()),
            "output_event_count": len(bundle["events"]), "no_imputation": True, "no_outlier_removal": True,
        }
        if not manifest["all_orders_preserved"] or manifest["source_event_count"] != manifest["output_event_count"]:
            raise ValueError("Order/event reconciliation failed")
        for filename, source in bundle["sources"].items():
            if audit.file_hash(raw_dir / filename) != source["sha256"]:
                raise ValueError(f"Raw source changed during export: {filename}")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output_dir.exists():
            raise FileExistsError(f"Snapshot appeared during export: {output_dir}")
        staging.rename(output_dir)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Snapshot already exists, choose a new version: {args.output}")
    print("Validating and standardizing all nine raw tables...", flush=True)
    bundle = prepare_dataset()
    print("Exporting a separate snapshot with lineage and quality flags...", flush=True)
    write_snapshot(bundle, args.output)
    print(bundle["summary"].to_string(index=False))
    print(f"Orders: {len(bundle['cases']):,}; actual events: {len(bundle['events']):,}")
    print(f"Snapshot: {args.output.resolve()}")


if __name__ == "__main__":
    main()
