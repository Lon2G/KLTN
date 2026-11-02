"""Build retrospective, strictly prior seller context from real Olist orders."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import build_geographic_features as shared


business = shared.business
ROOT = business.audit.ROOT
VERSION = "olist_seller_history_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_seller_history_v1"
README = ROOT/"templates/seller_history_v1/README.md"
TIMES = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date", "order_delivered_customer_date"]
TABLES = {"seller_order_history.csv", "order_seller_history.csv", "order_source_evidence.csv",
          "review_source_evidence.csv", "coverage_by_month.csv", "history_support_summary.csv"}
COUNT_COLUMNS = ["prior_orders", "prior_orders_30d", "prior_single_seller_orders", "prior_multi_seller_orders",
                 "prior_observed_completed_orders", "prior_completion_history_unavailable_orders", "prior_promise_comparable_orders",
                 "prior_after_promise_orders", "prior_reviewed_orders", "prior_ambiguous_latest_review_orders",
                 "prior_review_history_unavailable_orders", *[f"prior_review_score_{score}_orders" for score in range(1, 6)]]
FLOAT_COLUMNS = ["days_since_last_prior_purchase", "prior_after_promise_fraction", "prior_mean_review_score",
                 *[f"prior_review_score_{score}_fraction" for score in range(1, 6)]]
TIME_COLUMNS = ["latest_prior_purchase_timestamp", "latest_history_delivery_timestamp", "latest_history_review_answer_timestamp"]


def protocol():
    return {"version": VERSION, "identity": business.IDENTITY, "query_time": "current_order_purchase_timestamp",
            "purchase_history": "same_seller_strictly_earlier_scoped_purchases; equal_times_excluded",
            "purchase_activity_window_days": 30, "window_bounds": "[as_of_minus_30_days, as_of)",
            "outcome_history": "prior_single_seller_orders_only; never_causal_seller_attribution",
            "completion": "all_four_recorded_milestones_nondecreasing; delivery_strictly_before_as_of; no_final_status_filter",
            "promise": "recorded_estimate_date_not_before_purchase_date; compare_calendar_dates_not_midnight_instants",
            "review": "valid_score_1_to_5; purchase<=creation<=answer<as_of; latest_visible_answer_per_order",
            "review_ties": "same_latest_time_and_same_score_count_once; conflicting_scores_withheld",
            "low_review_cutoff_selected": False, "cancellation_rate_available": False,
            "missing_policy": "zero_observed_counts; missing_rates_when_denominator_zero; no_imputation",
            "current_multi_seller_policy": "keep_pair_features; do_not_average_rates_or_select_a_seller_for_order",
            "history_scope": "bed_bath_table_development_only_left_truncated_not_lifetime_history",
            "observation_policy": "retrospective_event_time_proxy; source_arrival_and_revision_times_unknown",
            "validation_history_updates": "earlier_validation_events_allowed_after_their_recorded_times; no_model_refit",
            "thresholds_learned": False, "labels_created": False, "real_time_validated": False}


def code_hashes():
    return {**business.code_hashes(), **{str(path.relative_to(ROOT)): business.audit.file_hash(path)
                                       for path in [Path(__file__), Path(shared.__file__), README]}}


def verify_sources(provenance):
    parent = Path(provenance["business_directory"])
    if business.audit.file_hash(parent/"manifest.json") != provenance["business_manifest_sha256"]:
        raise ValueError("Seller-history parent manifest changed")
    shared.verify_files(parent, provenance["business_output_hashes"])
    business.verify_sources(provenance["business_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Seller-history code or protocol changed")


def validate_inputs(inputs):
    context, links, reviews = inputs["context"], inputs["links"], inputs["reviews"]
    business.require_key(context, ["order_id"], "seller-history orders")
    business.require_key(links, ["order_id", "seller_id"], "seller-history links")
    business.require_key(reviews, ["clean_source_record"], "seller-history review source records")
    business.require_scope(context, inputs["reservations"], business.IDENTITY)
    if (not context.dataset_id.eq(business.IDENTITY["dataset_id"]).all()
            or not context.marketplace_id.eq(business.IDENTITY["marketplace_id"]).all()):
        raise ValueError("Seller-history source identity differs")
    if (set(links.order_id) != set(context.order_id) or not set(reviews.order_id).issubset(context.order_id)
            or context.order_purchase_timestamp.isna().any()
            or not context.order_purchase_timestamp.lt(context.event_time_cutoff_exclusive).all()):
        raise ValueError("Seller-history scope or query time differs")
    expected = context.set_index("order_id")
    for frame in [links, reviews]:
        columns = ["order_purchase_timestamp", "event_time_cutoff_exclusive"]
        pd.testing.assert_frame_equal(frame.set_index("order_id")[columns], expected.loc[frame.order_id, columns], check_dtype=False)
    counts = links.groupby("order_id").size()
    if not links.order_seller_count.eq(links.order_id.map(counts)).all():
        raise ValueError("Seller multiplicity differs from distinct source links")


def load_inputs(parent=business.DEFAULT_OUTPUT):
    parent = Path(parent).resolve()
    before = business.audit.file_hash(parent/"manifest.json")
    _, manifest = business.load_feature_snapshot(parent)
    business.verify_sources(manifest["provenance"])
    inputs = {key: shared.read_table(parent, name, manifest) for key, name in [
        ("context", "order_context.csv"), ("links", "seller_order_links.csv"), ("reviews", "review_source_evidence.csv")]}
    reservation = Path(manifest["provenance"]["reservation_directory"])/"cohort_cases.csv"
    inputs["reservations"] = pd.read_csv(reservation, usecols=["order_id", "split"], dtype="string", keep_default_na=False)
    validate_inputs(inputs)
    provenance = {"business_directory": str(parent), "business_manifest_sha256": before,
                  "business_output_hashes": manifest["output_hashes"], "business_sources": manifest["provenance"],
                  "code_hashes": code_hashes(), "reserved_test_orders_excluded": int(inputs["reservations"].split.eq("test").sum())}
    verify_sources(provenance)
    return inputs, provenance


def build_source_evidence(inputs):
    context, links = inputs["context"], inputs["links"]
    columns = ["order_id", "split", "clean_order_record", *TIMES, "order_estimated_delivery_date"]
    orders = context[columns].sort_values("order_id").reset_index(drop=True).copy()
    orders["seller_count"] = orders.order_id.map(links.groupby("order_id").size()).astype("int64")
    orders["recorded_completion_chronology_valid"] = orders[TIMES].notna().all(axis=1)
    for first, second in zip(TIMES, TIMES[1:]):
        orders["recorded_completion_chronology_valid"] &= orders[second].ge(orders[first])
    orders["recorded_promise_comparable"] = orders.order_estimated_delivery_date.dt.normalize().ge(orders.order_purchase_timestamp.dt.normalize())
    orders["calendar_delay_days"] = (orders.order_delivered_customer_date.dt.normalize()-orders.order_estimated_delivery_date.dt.normalize()).dt.days
    orders.loc[~(orders.recorded_completion_chronology_valid & orders.recorded_promise_comparable), "calendar_delay_days"] = np.nan
    reviews = inputs["reviews"][["clean_source_record", "order_id", "review_id", "review_score",
                                 "review_creation_date", "review_answer_timestamp"]].merge(
        orders[["order_id", "order_purchase_timestamp", "seller_count"]], on="order_id", validate="many_to_one")
    reviews["recorded_review_valid"] = (reviews.review_score.between(1, 5) & reviews.review_creation_date.ge(reviews.order_purchase_timestamp)
                                        & reviews.review_answer_timestamp.ge(reviews.review_creation_date)).fillna(False).astype(bool)
    return orders, reviews.sort_values("clean_source_record").reset_index(drop=True)


def latest_visible_reviews(reviews, as_of):
    visible = reviews.loc[reviews.recorded_review_valid & reviews.order_purchase_timestamp.lt(as_of) & reviews.review_answer_timestamp.lt(as_of)]
    latest = visible.loc[visible.review_answer_timestamp.eq(visible.groupby("order_id").review_answer_timestamp.transform("max"))]
    conflicts = latest.groupby("order_id").review_score.nunique().gt(1)
    ambiguous = int(conflicts.sum())
    latest = latest.loc[~latest.order_id.isin(conflicts.index[conflicts])]
    return latest.sort_values("clean_source_record").drop_duplicates("order_id"), ambiguous


def seller_histories(queries, links, orders, reviews):
    business.require_key(queries, ["order_id", "seller_id"], "seller-history queries")
    pairs = links[["order_id", "seller_id"]].merge(orders, on="order_id", validate="many_to_one")
    single_links = links.loc[links.order_id.isin(orders.loc[orders.seller_count.eq(1), "order_id"]), ["order_id", "seller_id"]]
    review_pairs = reviews.loc[reviews.seller_count.eq(1)].merge(single_links, on="order_id", validate="many_to_one")
    history_groups = {key: value for key, value in pairs.groupby("seller_id", sort=False)}
    review_groups = {key: value for key, value in review_pairs.groupby("seller_id", sort=False)}
    result = []
    for query in queries.itertuples(index=False):
        as_of = query.order_purchase_timestamp
        history = history_groups[query.seller_id]
        prior = history.loc[history.order_purchase_timestamp.lt(as_of)]
        single = prior.loc[prior.seller_count.eq(1)]
        completed = single.loc[single.recorded_completion_chronology_valid & single.order_delivered_customer_date.lt(as_of)]
        comparable = completed.loc[completed.recorded_promise_comparable]
        latest, ambiguous = latest_visible_reviews(review_groups.get(query.seller_id, review_pairs.iloc[:0]), as_of)
        previous = prior.order_purchase_timestamp.max()
        row = {"order_id": query.order_id, "seller_id": query.seller_id, "split": query.split, "as_of_timestamp": as_of,
               "prior_orders": len(prior), "prior_orders_30d": int(prior.order_purchase_timestamp.ge(as_of-pd.Timedelta(days=30)).sum()),
               "prior_single_seller_orders": len(single), "prior_multi_seller_orders": len(prior)-len(single),
               "latest_prior_purchase_timestamp": previous,
               "days_since_last_prior_purchase": (as_of-previous).total_seconds()/86400 if pd.notna(previous) else np.nan,
               "prior_observed_completed_orders": len(completed), "prior_completion_history_unavailable_orders": len(single)-len(completed),
               "prior_promise_comparable_orders": len(comparable), "prior_after_promise_orders": int(comparable.calendar_delay_days.gt(0).sum()),
               "prior_after_promise_fraction": float(comparable.calendar_delay_days.gt(0).mean()) if len(comparable) else np.nan,
               "latest_history_delivery_timestamp": completed.order_delivered_customer_date.max(),
               "prior_reviewed_orders": len(latest), "prior_ambiguous_latest_review_orders": ambiguous,
               "prior_review_history_unavailable_orders": len(single)-len(latest),
               "prior_mean_review_score": float(latest.review_score.mean()) if len(latest) else np.nan,
               "latest_history_review_answer_timestamp": latest.review_answer_timestamp.max()}
        for score in range(1, 6):
            row[f"prior_review_score_{score}_orders"] = int(latest.review_score.eq(score).sum())
            row[f"prior_review_score_{score}_fraction"] = float(latest.review_score.eq(score).mean()) if len(latest) else np.nan
        result.append(row)
    columns = ["order_id", "seller_id", "split", "as_of_timestamp", *COUNT_COLUMNS, *FLOAT_COLUMNS, *TIME_COLUMNS]
    frame = pd.DataFrame(result, columns=columns)
    for column in ["as_of_timestamp", *TIME_COLUMNS]:
        frame[column] = pd.to_datetime(frame[column]).astype("datetime64[us]")
    frame = frame.astype({**{column: "int64" for column in COUNT_COLUMNS}, **{column: "float64" for column in FLOAT_COLUMNS}})
    return frame.sort_values(["order_id", "seller_id"]).reset_index(drop=True)


def order_histories(context, pairs):
    result = context[["order_id", "dataset_id", "marketplace_id", "product_category_name", "split", "order_purchase_timestamp"]].copy()
    support = pairs.groupby("order_id").agg(seller_count=("seller_id", "size"), min_seller_prior_orders=("prior_orders", "min"),
                                           max_seller_prior_orders=("prior_orders", "max"))
    support["sellers_without_prior_orders"] = pairs.prior_orders.eq(0).groupby(pairs.order_id).sum()
    result = result.merge(support, on="order_id", validate="one_to_one")
    single = pairs.loc[pairs.order_id.isin(support.index[support.seller_count.eq(1)])]
    fields = ["seller_id", "prior_orders", "prior_observed_completed_orders", "prior_promise_comparable_orders", "prior_after_promise_fraction",
              "prior_reviewed_orders", "prior_mean_review_score", *[f"prior_review_score_{score}_fraction" for score in range(1, 6)]]
    names = {key: "single_seller_"+key for key in fields}
    names["seller_id"] = "single_seller_id"
    result = result.merge(single[["order_id", *fields]].rename(columns=names), on="order_id", how="left", validate="one_to_one")
    result["history_route"] = "single_seller_no_prior_history"
    result.loc[result.single_seller_prior_orders.gt(0), "history_route"] = "single_seller_purchase_history_only"
    available = result.single_seller_prior_observed_completed_orders.gt(0) | result.single_seller_prior_reviewed_orders.gt(0)
    result.loc[available, "history_route"] = "single_seller_with_prior_outcomes"
    result.loc[result.seller_count.gt(1), "history_route"] = "multi_seller_use_pair_context"
    result["availability"] = "retrospective_event_time_proxy_not_verified_online"
    return result.sort_values("order_id").reset_index(drop=True)


def coverage_tables(orders, pairs):
    scoped = orders.assign(purchase_month=orders.order_purchase_timestamp.dt.strftime("%Y-%m"))
    rows = []
    for (split, month), frame in scoped.groupby(["split", "purchase_month"], sort=True):
        rows.append({"split": split, "purchase_month": month, "orders": len(frame),
                     "multi_seller_orders": int(frame.seller_count.gt(1).sum()),
                     "orders_with_any_seller_without_history": int(frame.sellers_without_prior_orders.gt(0).sum()),
                     "single_seller_with_prior_delivery_context": int(frame.single_seller_prior_promise_comparable_orders.gt(0).sum()),
                     "single_seller_with_prior_review_context": int(frame.single_seller_prior_reviewed_orders.gt(0).sum())})
    supports = []
    for split, frame in pairs.groupby("split", sort=True):
        for column in ["prior_orders", "prior_promise_comparable_orders", "prior_reviewed_orders"]:
            for label, lower, upper in [("0", 0, 0), ("1-4", 1, 4), ("5-19", 5, 19), ("20-49", 20, 49), ("50+", 50, None)]:
                mask = frame[column].ge(lower)
                if upper is not None:
                    mask &= frame[column].le(upper)
                supports.append({"split": split, "history_measure": column, "observed_support_bin": label, "order_seller_pairs": int(mask.sum())})
    return pd.DataFrame(rows), pd.DataFrame(supports)


def build_history(inputs):
    validate_inputs(inputs)
    orders, reviews = build_source_evidence(inputs)
    queries = inputs["links"][["order_id", "seller_id", "split", "order_purchase_timestamp"]]
    pairs = seller_histories(queries, inputs["links"], orders, reviews)
    order_features = order_histories(inputs["context"], pairs)
    coverage, supports = coverage_tables(order_features, pairs)
    frames = {"seller_order_history.csv": pairs, "order_seller_history.csv": order_features,
              "order_source_evidence.csv": orders, "review_source_evidence.csv": reviews,
              "coverage_by_month.csv": coverage, "history_support_summary.csv": supports}
    summary = {"development_orders": len(orders), "order_seller_pairs": len(pairs), "distinct_sellers": int(pairs.seller_id.nunique()),
               "source_review_rows": len(reviews), "split_orders": orders.split.value_counts().to_dict(),
               "multi_seller_orders_retained": int(orders.seller_count.gt(1).sum()),
               "history_routes": order_features.history_route.value_counts().to_dict(),
               "pairs_without_prior_orders": int(pairs.prior_orders.eq(0).sum()),
               "pairs_with_prior_delivery_context": int(pairs.prior_promise_comparable_orders.gt(0).sum()),
               "pairs_with_prior_review_context": int(pairs.prior_reviewed_orders.gt(0).sum()),
               "pairs_with_ambiguous_latest_review": int(pairs.prior_ambiguous_latest_review_orders.gt(0).sum()),
               "cancellation_rate_computed": False, "low_review_cutoff_selected": False, "synthetic_data_used": False,
               "human_labels_created": False, "anomaly_labels_created": False, "seller_fault_established": False,
               "training_performed": False, "existing_models_changed": False, "thresholds_changed": False,
               "test_features_built": False, "test_scored": False, "accuracy_computed": False, "real_time_validated": False,
               "interpretation": "strictly_prior_recorded_events_in_selected_scope; conditional_observed_rates_not_anomaly_probabilities"}
    return frames, summary


def write_history(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite seller history: {output}")
    if set(frames) != TABLES:
        raise ValueError("Missing seller-history evidence tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".seller-history-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {key: str(dtype) for key, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            (staging/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text("# Actual Seller History Results\n\n```json\n"+json.dumps(summary, indent=2)+"\n```\n\n"
                                       +business.audit.markdown_table(frames["coverage_by_month.csv"])+"\n\n"+README.read_text())
        manifest = {"status": "complete", "version": VERSION, "identity": business.IDENTITY,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "summary": summary, "provenance": provenance,
                    "schemas": schemas, "runtime": {"numpy": np.__version__, "pandas": pd.__version__},
                    "output_hashes": {path.name: business.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = shared.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete" or manifest.get("identity") != business.IDENTITY
            or set(manifest.get("schemas", {})) != TABLES
            or not (TABLES | {"summary.json", "protocol.json", "README.md"}).issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected complete seller-history snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    if shared.read_json(directory/"protocol.json") != protocol() or shared.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Seller-history protocol/summary binding differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--business", type=Path, default=business.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying real development sources; no models or human labels loaded.", flush=True)
        inputs, provenance = load_inputs(args.business)
        print("Computing seller history using only strictly earlier recorded events.", flush=True)
        frames, summary = build_history(inputs)
        summary = write_history(frames, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Seller-history preparation stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
