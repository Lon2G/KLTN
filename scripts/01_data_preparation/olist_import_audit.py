"""Audit imported Olist tables and profile categories without altering raw data."""

import hashlib
import json
from pathlib import Path
import sys

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from project_paths import PROCESSED_DATA_DIR, RAW_DATA_DIR, REPORT_DIR


FILES = {name: f"olist_{name}_dataset.csv" for name in (
    "orders", "order_items", "products", "customers", "sellers",
    "order_payments", "order_reviews", "geolocation",
)}
FILES["translation"] = "product_category_name_translation.csv"
KEYS = {
    "orders": ["order_id"], "order_items": ["order_id", "order_item_id"],
    "products": ["product_id"], "customers": ["customer_id"],
    "sellers": ["seller_id"], "order_payments": ["order_id", "payment_sequential"],
    "order_reviews": ["review_id", "order_id"],
    "translation": ["product_category_name"],
}
RELATIONS = [
    ("orders", "customer_id", "customers", "customer_id", True),
    ("order_items", "order_id", "orders", "order_id", True),
    ("order_items", "product_id", "products", "product_id", True),
    ("order_items", "seller_id", "sellers", "seller_id", True),
    ("order_payments", "order_id", "orders", "order_id", True),
    ("order_reviews", "order_id", "orders", "order_id", True),
    ("products", "product_category_name", "translation", "product_category_name", False),
    ("customers", "customer_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix", False),
    ("sellers", "seller_zip_code_prefix", "geolocation", "geolocation_zip_code_prefix", False),
]
TIMES = ["order_purchase_timestamp", "order_approved_at",
         "order_delivered_carrier_date", "order_delivered_customer_date"]
DURATIONS = ["purchased_to_approved_days", "approved_to_carrier_days",
             "carrier_to_delivered_days"]


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_tables(raw_dir=RAW_DATA_DIR):
    tables, inventory = {}, []
    for name, filename in FILES.items():
        path = raw_dir / filename
        columns = pd.read_csv(path, nrows=0).columns
        string_columns = [c for c in columns if c.endswith("_id")
                          or "zip_code_prefix" in c or c.startswith("product_category_name")]
        frame = pd.read_csv(path, dtype={c: "string" for c in string_columns})
        tables[name] = frame
        keys = KEYS.get(name, [])
        inventory.append({
            "table": name, "file": filename, "rows": len(frame),
            "columns": len(columns), "sha256": file_hash(path),
            "exact_duplicate_excess_rows": int(frame.duplicated().sum()),
            "key_columns": json.dumps(keys),
            "null_key_rows": int(frame[keys].isna().any(axis=1).sum()) if keys else None,
            "duplicate_key_excess_rows": int(frame.duplicated(keys).sum()) if keys else None,
            "missing_by_column": json.dumps({c: int(n) for c, n in frame.isna().sum().items() if n}),
        })
    return tables, pd.DataFrame(inventory)


def audit_relations(tables):
    rows = []
    for child, fk, parent, pk, blocking in RELATIONS:
        source, target = tables[child][fk], tables[parent][pk]
        unmatched = source.notna() & ~source.isin(target.dropna())
        rows.append({
            "child_table": child, "foreign_key": fk, "parent_table": parent,
            "parent_key": pk, "blocking": blocking,
            "null_foreign_key_rows": int(source.isna().sum()),
            "unmatched_nonnull_rows": int(unmatched.sum()),
            "unmatched_nonnull_keys": int(source[unmatched].nunique()),
            "parent_keys_without_children": int((~target.dropna().drop_duplicates().isin(source)).sum()),
            "child_keys_with_multiple_rows": int(source.value_counts().gt(1).sum()),
            "parent_key_is_unique": bool(target.is_unique),
        })
    return pd.DataFrame(rows)


def validate_core(tables):
    for name, keys in KEYS.items():
        frame = tables[name]
        if frame[keys].isna().any().any() or frame.duplicated(keys).any():
            raise ValueError(f"Null or duplicate key in {name}: {keys}")
    relations = audit_relations(tables)
    bad = relations.blocking & (
        relations.null_foreign_key_rows.gt(0) | relations.unmatched_nonnull_rows.gt(0)
    )
    if bad.any():
        raise ValueError(f"Invalid core foreign keys: {relations.loc[bad].to_dict('records')}")
    return relations


def build_order_categories(tables):
    validate_core(tables)
    items = tables["order_items"].merge(
        tables["products"][["product_id", "product_category_name"]],
        on="product_id", how="left", validate="many_to_one",
    )
    grouped = items.groupby("order_id", sort=True)
    counts = grouped.agg(item_count=("product_id", "size"),
                         product_count=("product_id", "nunique"),
                         seller_count=("seller_id", "nunique"),
                         known_category_count=("product_category_name", "nunique"),
                         categorized_item_count=("product_category_name", "count"))
    counts["missing_category_item_count"] = counts.item_count - counts.categorized_item_count
    counts = counts.drop(columns="categorized_item_count")
    membership = items[["order_id", "product_category_name"]].dropna().drop_duplicates()
    lists = membership.groupby("order_id").product_category_name.agg(lambda s: json.dumps(sorted(s)))
    cases = tables["orders"].copy().merge(counts, on="order_id", how="left", validate="one_to_one")
    cases[counts.columns] = cases[counts.columns].fillna(0).astype("int64")
    cases["categories_json"] = cases.order_id.map(lists).fillna("[]")
    cases["category_scope"] = "single_category"
    cases.loc[cases.known_category_count.gt(1), "category_scope"] = "mixed_category"
    cases.loc[cases.missing_category_item_count.gt(0), "category_scope"] = "partially_unknown"
    cases.loc[cases.known_category_count.eq(0), "category_scope"] = "unknown_only"
    cases.loc[cases.item_count.eq(0), "category_scope"] = "no_items"
    single_ids = cases.loc[cases.category_scope.eq("single_category"), "order_id"]
    single = membership.loc[membership.order_id.isin(single_ids)]
    cases = cases.merge(single.rename(columns={"product_category_name": "single_category_name"}),
                        on="order_id", how="left", validate="one_to_one")
    translated = tables["translation"].rename(columns={
        "product_category_name": "single_category_name",
        "product_category_name_english": "single_category_name_english",
    })
    cases = cases.merge(translated, on="single_category_name", how="left", validate="many_to_one")
    for table, column in [("order_payments", "payment_record_count"), ("order_reviews", "review_record_count")]:
        cases[column] = cases.order_id.map(tables[table].groupby("order_id").size()).fillna(0).astype("int64")

    # Derived times are separate from the original timestamp strings in the export.
    parsed = cases[TIMES + ["order_estimated_delivery_date"]].apply(pd.to_datetime, errors="raise")
    cases["missing_milestone_count"] = parsed[TIMES].isna().sum(axis=1)
    for start, end, column in zip(TIMES, TIMES[1:], DURATIONS):
        cases[column] = (parsed[end] - parsed[start]).dt.total_seconds() / 86400
    cases["total_cycle_time_days"] = (parsed[TIMES[-1]] - parsed[TIMES[0]]).dt.total_seconds() / 86400
    cases["negative_transition_count"] = cases[DURATIONS].lt(0).sum(axis=1)
    cases["has_reversed_recorded_milestones"] = False
    for position, start in enumerate(TIMES):
        for end in TIMES[position + 1:]:
            cases["has_reversed_recorded_milestones"] |= parsed[end].lt(parsed[start])
    cases["delivered_complete_nondecreasing"] = (
        cases.order_status.eq("delivered") & cases.missing_milestone_count.eq(0)
        & ~cases.has_reversed_recorded_milestones
    )
    cases["purchase_month"] = parsed[TIMES[0]].dt.to_period("M").astype("string")
    if len(cases) != len(tables["orders"]) or not cases.order_id.is_unique:
        raise ValueError("Order grain changed during enrichment")
    return cases.sort_values("order_id").reset_index(drop=True), items, membership


def profile_categories(cases, items, membership, translation):
    rows = []
    single = cases.loc[cases.category_scope.eq("single_category")]
    for category, members in membership.groupby("product_category_name", sort=True):
        subset = single.loc[single.single_category_name.eq(category)]
        eligible = subset.loc[subset.delivered_complete_nondecreasing]
        dates = pd.to_datetime(subset.order_purchase_timestamp, errors="raise")
        rows.append({
            "product_category_name": category,
            "orders_with_category": len(members),
            "single_category_orders": len(subset),
            "mixed_or_partially_unknown_orders": len(members) - len(subset),
            "delivered_single_category_orders": int(subset.order_status.eq("delivered").sum()),
            "delivered_complete_nondecreasing_orders": len(eligible),
            "missing_timeline_single_category_orders": int(subset.missing_milestone_count.gt(0).sum()),
            "reversed_timeline_single_category_orders": int(subset.has_reversed_recorded_milestones.sum()),
            "multi_seller_single_category_orders": int(subset.seller_count.gt(1).sum()),
            "distinct_sellers_single_category": items.loc[items.order_id.isin(subset.order_id), "seller_id"].nunique(),
            "purchase_months_single_category": subset.purchase_month.nunique(),
            "first_purchase": dates.min(), "last_purchase": dates.max(),
            "eligible_total_days_median": eligible.total_cycle_time_days.median(),
            "eligible_total_days_p95": eligible.total_cycle_time_days.quantile(0.95),
        })
    result = pd.DataFrame(rows).merge(translation, on="product_category_name", how="left", validate="one_to_one")
    return result.sort_values(
        ["delivered_complete_nondecreasing_orders", "product_category_name"], ascending=[False, True],
    ).reset_index(drop=True)


def markdown_table(frame):
    columns = list(frame.columns)
    lines = ["| " + " | ".join(columns) + " |", "| " + " | ".join(["---"] * len(columns)) + " |"]
    for row in frame.itertuples(index=False, name=None):
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(lines)


def write_outputs(tables, inventory, relations, cases, profile):
    inventory.to_csv(PROCESSED_DATA_DIR / "olist_import_inventory.csv", index=False)
    relations.to_csv(PROCESSED_DATA_DIR / "olist_relationship_audit.csv", index=False)
    cases.to_csv(PROCESSED_DATA_DIR / "olist_order_category_context.csv", index=False)
    profile.to_csv(PROCESSED_DATA_DIR / "olist_category_profile.csv", index=False)
    scopes = cases.category_scope.value_counts().rename_axis("category_scope").reset_index(name="orders")
    missing_translation = profile.loc[profile.product_category_name_english.isna(),
                                      ["product_category_name", "orders_with_category"]]
    display = ["product_category_name", "product_category_name_english", "single_category_orders",
               "delivered_complete_nondecreasing_orders", "missing_timeline_single_category_orders",
               "reversed_timeline_single_category_orders", "distinct_sellers_single_category"]
    shortlist = pd.concat([profile.head(10), profile.loc[profile.product_category_name.eq("papelaria")]]).drop_duplicates()
    report = "\n\n".join([
        "# Olist import and category audit",
        "## Scope\nComputed only from the nine imported raw CSV files. No records, timestamps, translations or human labels are fabricated. Raw files, existing event logs, thresholds, models and manual reviews are not modified. This is a technical audit, not a thesis chapter or a model-quality evaluation.",
        "## Import inventory\n" + markdown_table(inventory[["file", "rows", "exact_duplicate_excess_rows", "duplicate_key_excess_rows"]]),
        "Hashes, missing values by column and tested keys are recorded in olist_import_inventory.csv. Duplicate counts are excess rows after the first occurrence. Geolocation has no asserted unique row key. All asserted primary/composite keys pass uniqueness and non-null checks.",
        "## Relationships\n" + markdown_table(relations),
        "Repeated order IDs in items, payments and reviews represent child records, not duplicate cases. The review key checked here is (review_id, order_id), not review_id alone. Missing child records are retained. Unmatched translations and ZIP prefixes are reported but do not invalidate order-to-product joins. Geolocation is not joined to orders: repeated ZIP prefixes would multiply rows without a separately defined aggregation policy.",
        "## Order-level scope\n" + markdown_table(scopes),
        f"All {len(cases):,} orders remain in olist_order_category_context.csv, one row per order_id (the event-log case_id). single_category means at least one item, exactly one known category and no item with an unknown category. partially_unknown can include one or several known categories plus unknown items. Missing categories and missing English translations remain missing. No arbitrary first-item category is assigned.",
        "## Category feasibility\n" + markdown_table(shortlist[display]),
        "Sorted by delivered orders with all four recorded milestones in nondecreasing order, then category name. This ordering measures available historical timing data, not industry suitability or model accuracy. Counts for orders_with_category are nonexclusive across categories. Single-category counts are disjoint. Full profiles include sellers, purchase-month coverage and median/P95 total days for the delivered-complete-nondecreasing subset. These whole-history descriptive percentiles are NOT training thresholds and must not be reused across a held-out time split.",
        "Complete/nondecreasing does not mean normal. Very long and zero-duration records are retained. Missing milestones and reversals are counted even when a case is outside the timing subset. Missing and reversed counts can overlap. Final cohort selection and handling of incomplete, canceled and open orders require explicit rules. Raw status and timestamps are preserved for that decision; the extraction cutoff is unknown.",
        "## Missing English translations\n" + markdown_table(missing_translation),
        f"Products missing their original category: {int(tables['products'].product_category_name.isna().sum()):,}. Their items and orders remain present. No category is inferred from prices or other proxies.",
        "## Next implementation boundary\nChoose one primary category from the feasibility table, then freeze a cohort definition and chronological train/validation/test split. Fit preprocessing, timing thresholds and detectors only on training data. Evaluate with independent human review that also samples normal/warning candidates. Do not treat rule labels as ground truth or anomaly scores as calibrated probabilities. Keep separate data/model versions for each platform and category. This audit has not retrained any model or demonstrated transfer to another marketplace.",
        "## Reproduce\nRun `.venv/bin/python scripts/01_data_preparation/olist_import_audit.py` from the project root. The command refreshes only its four named olist_*.csv audit/context outputs and this report. Tests use imported records and do not generate human labels.",
    ])
    (REPORT_DIR / "olist_import_category_audit.md").write_text(report + "\n", encoding="utf-8")


def main():
    tables, inventory = load_tables()
    relations = validate_core(tables)
    cases, items, membership = build_order_categories(tables)
    profile = profile_categories(cases, items, membership, tables["translation"])
    for row in inventory.itertuples(index=False):
        if file_hash(RAW_DATA_DIR / row.file) != row.sha256:
            raise ValueError(f"Raw input changed during audit: {row.file}")
    write_outputs(tables, inventory, relations, cases, profile)
    print(f"Audited {len(inventory)} files; {len(cases):,} unique orders; {len(profile)} observed categories.")
    print(cases.category_scope.value_counts().to_string())
    print(profile[["product_category_name_english", "single_category_orders", "delivered_complete_nondecreasing_orders"]].head(10).to_string(index=False))
    print(f"Report: {REPORT_DIR / 'olist_import_category_audit.md'}")


if __name__ == "__main__":
    main()
