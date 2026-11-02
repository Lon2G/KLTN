"""Read-only case inspection of verified Olist evidence, excluding the blind pilot."""

import argparse
from contextlib import redirect_stdout
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import sys
import tempfile

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/"scripts/01_data_preparation"))
with redirect_stdout(sys.stderr):
    import integrate_process_evidence as integration

shared = integration.shared
review = integration.pilot.review
VERSION = "process_evidence_inspection_v1"
README = ROOT/"templates/evidence_inspection_v1/README.md"
INTEGRATED_TABLES = ["case_evidence.csv", "milestone_evidence.csv", "process_durations.csv",
                     "business_anchor_evidence.csv", "delivery_model_evidence.csv", "seller_pair_evidence.csv"]
SOURCE_TABLES = ["payment_source_evidence.csv", "item_source_evidence.csv"]
SIGNALS = {
    "visible_milestone_inversion": ("process_inverted_pairs", "Earlier reference steps have later recorded times; verify source chronology, not seller fault."),
    "incomplete_visible_trace": ("process_visible_events", "Not all four milestones are visible at the partition cutoff; this is not a cancellation or anomaly label."),
    "equal_time_order_unresolved": ("process_has_equal_timestamps", "Some milestone times are equal; displayed reference order is not observed causal order."),
    "geographic_context_withheld": ("geography_quality_reasons", "Geographic evidence did not meet the existing provisional quality screen."),
    "multi_seller_no_individual_outcome_attribution": ("seller_count", "The order has multiple sellers; its outcome is not assigned to every seller."),
    "limited_seller_history": ("history_route", "Available category-scoped seller history is limited or cannot be represented by one seller."),
    "no_prior_scoped_customer_purchase": ("customer_prior_orders_in_scope", "No earlier purchase is observed in this limited category history; not necessarily a new customer."),
    "observed_accounting_difference_not_fraud": ("payment_minus_items_and_freight_minor_units", "Observed payment total differs from items plus freight; the difference alone is not fraud."),
    "business_anchor_warning": ("business_profiles_with_warning", "At least one saved pilot-anchor profile exceeds its threshold; no winning model or causal feature is identified."),
    "delivery_long_interval_warning": ("delivery_models_long_warning", "Observed duration exceeds a saved model upper bound; not a confirmed bottleneck."),
    "delivery_short_interval_warning": ("delivery_models_short_warning", "A positive observed duration is below a saved lower bound; a warning, not an anomaly label."),
    "delivery_zero_duration_warning": ("delivery_models_zero_duration_warning", "A saved comparison reports zero duration; inspect actual timestamps."),
    "no_validation_model_result_here": ("inner_role", "No saved validation model result is shown for this order; absence is not a normal verdict."),
}
LIST_COLUMNS = ["order_id", "split", "order_purchase_timestamp", "process_visible_events", "process_replay_status",
                "business_profiles_with_warning", "delivery_models_forecast", "delivery_models_evaluated", "evidence_codes_json"]
LIMITS = [
    "Analyst-only evidence, not approved human labels, fraud findings or anomaly probabilities.",
    "The 120 frozen pilot cases are excluded from every inspection output; use the blind reviewer tool for those cases.",
    "Source snapshot status and payment/item fields are retrospective; event-time filtering does not prove live availability.",
    "No model is fitted, no threshold changes, and no final-test case is inspected. Missing means unknown, not zero or normal.",
    "Model counts are descriptive, not a vote, ranking, confidence or causal explanation. Display rounding does not change JSON values.",
]


def code_hashes():
    return {**integration.code_hashes(), **{str(path.relative_to(ROOT)): review.digest(path) for path in [Path(__file__), README]}}


def verify_sources(provenance):
    source, batch = Path(provenance["source_directory"]), Path(provenance["pilot_directory"])
    if review.digest(source/"manifest.json") != provenance["source_manifest_sha256"]:
        raise ValueError("Inspection source manifest changed")
    manifest = integration.load_snapshot(source)
    integration.verify_sources(manifest["provenance"])
    if review.digest(batch/"analyst_only/manifest.json") != provenance["pilot_manifest_sha256"]:
        raise ValueError("Protected pilot manifest changed")
    pm = shared.read_json(batch/"analyst_only/manifest.json")
    if pm.get("version") != review.VERSION or pm.get("status") != "complete":
        raise ValueError("Expected the frozen pilot protection ledger")
    if pm["provenance"]["study_manifest_sha256"] != manifest["provenance"]["snapshots"]["ablation"]["manifest_sha256"]:
        raise ValueError("Pilot and integrated evidence lineage differ")
    shared.verify_files(batch, pm["output_hashes"])
    integration.pilot.verify_sources(pm["provenance"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Inspection code/protocol changed")


def load_repository(source=integration.DEFAULT_OUTPUT, batch=review.DEFAULT_BATCH):
    source, batch = Path(source).resolve(), Path(batch).resolve()
    manifest = integration.load_snapshot(source)
    provenance = {"source_directory": str(source), "source_manifest_sha256": review.digest(source/"manifest.json"),
                  "pilot_directory": str(batch), "pilot_manifest_sha256": review.digest(batch/"analyst_only/manifest.json"),
                  "code_hashes": code_hashes()}
    verify_sources(provenance)
    pm = shared.read_json(batch/"analyst_only/manifest.json")
    key = shared.read_table(batch, "analyst_only/selection_key.csv", pm)
    integration.business.require_key(key, ["order_id"], "protected pilot")
    if len(key) != integration.pilot.protocol()["sample_size"]:
        raise ValueError("Frozen pilot protection count differs")
    frames = {name: shared.read_table(source, name, manifest) for name in INTEGRATED_TABLES}
    parent = Path(manifest["provenance"]["snapshots"]["business"]["directory"])
    bm = shared.read_json(parent/"manifest.json")
    frames.update({name: shared.read_table(parent, name, bm) for name in SOURCE_TABLES})
    cases = frames["case_evidence.csv"]
    protected = frozenset(key.order_id)
    if (not protected.issubset(cases.order_id) or not cases.loc[cases.order_id.isin(protected), "split"].eq("validation").all()
            or not cases.split.isin(["train", "validation"]).all()):
        raise ValueError("Invalid development/pilot protection scope")
    # Remove protected cases from every public table before any query or rendering.
    public = {name: frame.loc[~frame.order_id.isin(protected)].reset_index(drop=True).copy() for name, frame in frames.items()}
    validate_public_tables(public)
    verify_sources(provenance)
    return {"frames": public, "protected_ids": protected, "source_orders": len(cases), "provenance": provenance}


def validate_public_tables(frames):
    cases = frames["case_evidence.csv"]
    integration.business.require_key(cases, ["order_id"], "inspection cases")
    indexed = cases.set_index("order_id")
    keys = {"milestone_evidence.csv": ["order_id", "activity"], "process_durations.csv": ["order_id", "start_activity", "end_activity"],
            "business_anchor_evidence.csv": ["order_id", "profile"], "delivery_model_evidence.csv": ["order_id", "model"],
            "seller_pair_evidence.csv": ["order_id", "seller_id"], "payment_source_evidence.csv": ["clean_source_record"],
            "item_source_evidence.csv": ["clean_source_record"]}
    for name, fields in keys.items():
        frame = frames[name]
        integration.business.require_key(frame, fields, name)
        if not set(frame.order_id).issubset(indexed.index):
            raise ValueError("Child evidence outside inspection scope")
    for name in ["milestone_evidence.csv", "process_durations.csv"]:
        counts = frames[name].groupby("order_id").size().reindex(indexed.index, fill_value=0)
        if not counts.eq(4).all():
            raise ValueError("Each case requires four milestone and duration evidence rows")
    for name, count, amount_fields in [
        ("payment_source_evidence.csv", "payment_record_count", [("payment_value_minor_units", "payment_value_sum_minor_units")]),
        ("item_source_evidence.csv", "item_count", [("price_minor_units", "item_price_sum_minor_units"), ("freight_minor_units", "freight_sum_minor_units")])]:
        group = frames[name].groupby("order_id")
        np.testing.assert_array_equal(group.size().reindex(indexed.index, fill_value=0), indexed[count])
        for source_field, aggregate in amount_fields:
            actual = group[source_field].sum(min_count=1).reindex(indexed.index)
            actual = actual.mask(group[source_field].count().reindex(indexed.index).ne(group.size().reindex(indexed.index)))
            pd.testing.assert_series_equal(actual, indexed[aggregate], check_names=False, check_dtype=False, check_index_type=False, check_exact=True)
    for value in cases.evidence_codes_json:
        codes = json.loads(value)
        if not isinstance(codes, list) or len(codes) != len(set(codes)) or not set(codes).issubset(SIGNALS):
            raise ValueError("Unknown or repeated evidence explanation code")


def json_value(value):
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, pd.Timestamp):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, np.generic):
        return json_value(value.item())
    if isinstance(value, float):
        if math.isnan(value):
            return None
        if not math.isfinite(value):
            raise ValueError("Nonfinite evidence cannot be exported")
    return value


def records(frame):
    return json_value(frame.to_dict(orient="records"))


def envelope(repository, kind):
    return {"version": VERSION, "kind": kind, "identity": integration.business.IDENTITY.copy(),
            "units": {"durations": "elapsed_days", "distance": "kilometers_ZIP_representative", "money": "source_currency_unconverted"},
            "source_manifest_sha256": repository["provenance"]["source_manifest_sha256"],
            "pilot_manifest_sha256": repository["provenance"]["pilot_manifest_sha256"],
            "source_orders": repository["source_orders"], "protected_pilot_orders": len(repository["protected_ids"]),
            "accessible_orders": len(repository["frames"]["case_evidence.csv"]), "limits": LIMITS.copy()}


def select_cases(repository, split="validation", month=None, signal=None, delivery_state="any"):
    if split not in ["all", "train", "validation"] or delivery_state not in ["any", "observed_outcome", "forecast_only", "no_forecast"]:
        raise ValueError("Unsupported inspection scope")
    if signal is not None and signal not in SIGNALS:
        raise ValueError("Unknown evidence signal")
    if month is not None:
        if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
            raise ValueError("Month must be YYYY-MM")
        pd.Period(month, freq="M")
    frame = repository["frames"]["case_evidence.csv"]
    selected = pd.Series(True, index=frame.index)
    if split != "all":
        selected &= frame.split.eq(split)
    if month is not None:
        selected &= frame.order_purchase_timestamp.dt.strftime("%Y-%m").eq(month)
    if signal is not None:
        selected &= frame.evidence_codes_json.map(lambda value: signal in json.loads(value))
    if delivery_state == "observed_outcome":
        selected &= frame.delivery_models_evaluated.notna()
    elif delivery_state == "forecast_only":
        selected &= frame.delivery_models_forecast.notna() & frame.delivery_models_evaluated.isna()
    elif delivery_state == "no_forecast":
        selected &= frame.delivery_models_forecast.isna()
    return frame.loc[selected].sort_values(["order_purchase_timestamp", "order_id"]).reset_index(drop=True)


def query(repository, split="validation", month=None, signal=None, delivery_state="any", limit=None, offset=0):
    if type(offset) is not int or offset < 0 or (limit is not None and (type(limit) is not int or not 1 <= limit <= 200)):
        raise ValueError("Offset must be nonnegative and page size 1..200")
    frame = select_cases(repository, split, month, signal, delivery_state)
    result = envelope(repository, "list" if limit is not None else "summary")
    result["query"] = {"split": split, "month": month, "signal": signal, "delivery_state": delivery_state,
                       "sort": "purchase_timestamp_then_order_id_ascending; not_priority", "limit": limit, "offset": offset}
    result["matched_orders"] = len(frame)
    result["coverage"] = {"business_scored": int(frame.business_profiles_scored.notna().sum()),
                          "business_not_scored": int(frame.business_profiles_scored.isna().sum()),
                          "delivery_observed_outcome": int(frame.delivery_models_evaluated.notna().sum()),
                          "delivery_forecast_only": int((frame.delivery_models_forecast.notna() & frame.delivery_models_evaluated.isna()).sum()),
                          "delivery_no_forecast": int(frame.delivery_models_forecast.isna().sum())}
    result["process_states"] = {str(key): int(value) for key, value in frame.process_replay_status.value_counts().sort_index().items()}
    result["evidence_code_counts"] = {code: sum(code in json.loads(value) for value in frame.evidence_codes_json) for code in SIGNALS}
    if limit is not None:
        result["rows"] = records(frame.iloc[offset:offset+limit][LIST_COLUMNS])
        result["returned_orders"] = len(result["rows"])
        result["has_more"] = offset+len(result["rows"]) < len(frame)
    return result


def inspect_case(repository, order_id):
    if not isinstance(order_id, str) or not re.fullmatch(r"[0-9a-f]{32}", order_id):
        raise ValueError("Use the exact 32-character order_id shown by --list")
    if order_id in repository["protected_ids"]:
        raise ValueError("Case reserved for blinded review; analyst inspection blocked. Use review_business_cases.py without model output.")
    frames = repository["frames"]
    selected = frames["case_evidence.csv"].loc[lambda frame: frame.order_id.eq(order_id)]
    if len(selected) != 1:
        raise ValueError("Order is not in the accessible development scope; no test or external case lookup")
    result = envelope(repository, "case")
    result["case"] = records(selected)[0]
    row = result["case"]
    result["evidence_codes"] = [{"code": code, "field": SIGNALS[code][0], "observed_value": row[SIGNALS[code][0]],
                                 "meaning": SIGNALS[code][1]} for code in json.loads(row["evidence_codes_json"])]
    result["availability"] = {"business": "saved_anchor_results" if row["business_profiles_scored"] is not None else "not_scored_here",
                              "delivery": "saved_forecast_and_observed_outcome" if row["delivery_models_evaluated"] is not None
                              else "saved_forecast_outcome_unknown" if row["delivery_models_forecast"] is not None else "no_saved_forecast"}
    result["tables"] = {name: records(frame.loc[frame.order_id.eq(order_id)]) for name, frame in frames.items() if name != "case_evidence.csv"}
    result["source_references"] = {
        "integrated_snapshot": repository["provenance"]["source_directory"],
        "milestones": "clean_order_record + source_column in milestone_evidence.csv",
        "payment_and_items": "clean_source_record in corresponding clean Olist table; amounts in original currency units",
        "history": "seller_id + order_id + as_of_timestamp; only prior scoped recorded evidence",
        "models": "order_id + profile/candidate or model; unchanged saved experiment results"}
    return result


def display(value):
    if value is None:
        return "[unknown/not available]"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float):
        return format(value, ".8g")
    if isinstance(value, (dict, list)):
        value = json.dumps(value, ensure_ascii=True)
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def table(rows, columns):
    if not rows:
        return "No saved evidence rows. This is not a normal verdict."
    return "\n".join(["| "+" | ".join(columns)+" |", "| "+" | ".join(["---"]*len(columns))+" |",
                      *["| "+" | ".join(display(row[column]) for column in columns)+" |" for row in rows]])


def render_markdown(result):
    lines = ["# Process Evidence Inspection", "", "Analyst-only, read-only. No human labels or probabilities.",
             f"Source orders: {result['source_orders']}; blind pilot protected: {result['protected_pilot_orders']}; accessible: {result['accessible_orders']}.", ""]
    if result["kind"] in ["summary", "list"]:
        lines += ["## Query", json.dumps(result["query"], ensure_ascii=True), f"Matched orders: {result['matched_orders']}",
                  "## Coverage", table([{"state": key, "orders": value} for key, value in result["coverage"].items()], ["state", "orders"]),
                  "## Process States", table([{"state": key, "orders": value} for key, value in result["process_states"].items()], ["state", "orders"]),
                  "## Signals and Limitations", "Counts can overlap; these are not anomaly prevalence or accuracy.",
                  table([{"code": key, "orders": value} for key, value in result["evidence_code_counts"].items()], ["code", "orders"])]
        if result["kind"] == "list":
            lines += ["## Orders", table(result["rows"], LIST_COLUMNS), f"More rows: {result['has_more']}"]
    else:
        row, tables = result["case"], result["tables"]
        lines += [f"## Order {row['order_id']}", f"Split: {row['split']}; process cutoff (exclusive): {row['event_time_cutoff_exclusive']}",
                  f"Recorded SNAPSHOT status: {row['order_status']} (not status known at cutoff).",
                  "## Process View", table([row], ["process_variant", "process_replay_status", "process_visible_events", "process_trace_fitness", "process_reference_is_fit"]),
                  "Reference fitness is a process replay diagnostic, not an anomaly probability or a Normal label.",
                  "## Availability", json.dumps(result["availability"]),
                  "## Evidence Notes", table(result["evidence_codes"], ["code", "field", "observed_value", "meaning"]),
                  "## Milestones", "Only visible rows enter the cutoff-bound process view; other source times are retained as audit evidence.",
                  table(tables["milestone_evidence.csv"], ["activity", "recorded_timestamp", "evidence_state", "clean_order_record", "source_column"]),
                  "## Observed Durations", "Unit: elapsed days. Unknown/zero/negative are distinct; no new threshold is fitted.",
                  table(tables["process_durations.csv"], ["start_activity", "end_activity", "duration_days", "duration_state"]),
                  "## Source Payments", "Installments/sequences are not failed payment attempts; original currency, no conversion.",
                  table(tables["payment_source_evidence.csv"], ["clean_source_record", "payment_sequential", "payment_type", "payment_installments", "payment_value"]),
                  "## Source Items", table(tables["item_source_evidence.csv"], ["clean_source_record", "order_item_id", "product_id", "seller_id", "price", "freight_value"]),
                  "## Prior Customer Context", table([row], ["customer_prior_orders_in_scope", "customer_days_since_prior_purchase"]),
                  "Category-scoped recorded history only; not lifetime behavior or a customer anomaly detector.",
                  "## Geographic Context", table([{key: row[key] for key in ["customer_state", "seller_states_json", *integration.delivery.context.DISTANCES, "geography_quality_reasons"]}],
                                                   ["customer_state", "seller_states_json", *integration.delivery.context.DISTANCES, "geography_quality_reasons"]),
                  "ZIP representative straight-line distances, not verified road routes.",
                  "## Prior Seller History", "Conditional observed history, not lifetime rates or causal seller attribution.",
                  table(tables["seller_pair_evidence.csv"], ["seller_id", "as_of_timestamp", "prior_orders", "prior_promise_comparable_orders", "prior_after_promise_fraction", "prior_reviewed_orders", "prior_mean_review_score"]),
                  "## Saved Business Anchor", "Pilot display anchor only; no winning model or feature-causal explanation.",
                  table(tables["business_anchor_evidence.csv"], ["profile", "candidate", "score", "threshold", "warning"]),
                  "## Saved Delivery Models", "All available configurations. Unknown warning means no eligible saved comparison, not no warning.",
                  table(tables["delivery_model_evidence.csv"], ["model", "calibrated_lower_days", "calibrated_median_days", "calibrated_upper_days",
                                                               integration.delivery.context.TARGET, "long_warning", "short_warning", "zero_duration_warning"]),
                  "## Source References", table([{"evidence": key, "reference": value} for key, value in result["source_references"].items()], ["evidence", "reference"])]
    lines += ["## Limits", *["- "+text for text in result["limits"]], "", "Source manifest SHA256: "+result["source_manifest_sha256"]]
    return "\n\n".join(lines)+"\n"


def export_result(repository, result, output):
    output = Path(output).resolve()
    forbidden = [ROOT/name for name in ["data", "models", "scripts", "templates", "tests", ".git"]]
    forbidden += [Path(repository["provenance"][key]) for key in ["source_directory", "pilot_directory"]]
    if any(output.is_relative_to(path.resolve()) for path in forbidden):
        raise ValueError("Export to a new outputs/inspections directory, never inside source data/code/model/review directories")
    if output.exists():
        raise FileExistsError(f"Inspection export exists: {output}")
    if result["kind"] == "case":
        expected = inspect_case(repository, result["case"]["order_id"])
    elif result["kind"] in ["summary", "list"]:
        args = {key: value for key, value in result["query"].items() if key != "sort"}
        expected = query(repository, **args)
    else:
        raise ValueError("Unknown inspection result kind")
    if result != expected:
        raise ValueError("Export must exactly match verified read-only evidence")
    verify_sources(repository["provenance"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".inspection-", dir=output.parent) as temp:
        staging = Path(temp)/"report"
        staging.mkdir()
        (staging/"result.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text(render_markdown(result))
        manifest = {"version": VERSION, "status": "complete", "kind": result["kind"],
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": repository["provenance"],
                    "output_hashes": {path.name: review.digest(path) for path in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(repository["provenance"])
        load_export(staging)
        if output.exists():
            raise FileExistsError("Export appeared during publication")
        staging.rename(output)
    return output


def load_export(directory):
    directory = Path(directory)
    manifest = shared.read_json(directory/"manifest.json")
    if manifest.get("version") != VERSION or manifest.get("status") != "complete" or set(manifest.get("output_hashes", {})) != {"result.json", "README.md"}:
        raise ValueError("Expected complete inspection export")
    shared.verify_files(directory, manifest["output_hashes"])
    result = shared.read_json(directory/"result.json")
    if (result.get("version") != VERSION or result.get("kind") != manifest["kind"]
            or result.get("source_manifest_sha256") != manifest["provenance"]["source_manifest_sha256"]
            or result.get("pilot_manifest_sha256") != manifest["provenance"]["pilot_manifest_sha256"]):
        raise ValueError("Inspection export binding differs")
    return result, manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--summary", action="store_true")
    modes.add_argument("--list", action="store_true")
    modes.add_argument("--order-id")
    parser.add_argument("--source", type=Path, default=integration.DEFAULT_OUTPUT)
    parser.add_argument("--pilot", type=Path, default=review.DEFAULT_BATCH)
    parser.add_argument("--split", choices=["all", "train", "validation"])
    parser.add_argument("--month")
    parser.add_argument("--signal", choices=list(SIGNALS))
    parser.add_argument("--delivery-state", choices=["any", "observed_outcome", "forecast_only", "no_forecast"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    try:
        if not 1 <= args.limit <= 200 or args.offset < 0:
            raise ValueError("Limit must be 1..200 and offset nonnegative")
        if args.order_id and any([args.split, args.month, args.signal, args.delivery_state, args.offset]):
            raise ValueError("Exact --order-id lookup cannot be combined with list filters or offset")
        if args.output and args.output.exists():
            raise FileExistsError(f"Inspection export exists: {args.output}")
        repository = load_repository(args.source, args.pilot)
        result = inspect_case(repository, args.order_id) if args.order_id else query(repository, args.split or "validation", args.month, args.signal,
                                                                                  args.delivery_state or "any", args.limit if args.list else None, args.offset)
        if args.output:
            export_result(repository, result, args.output)
        print(json.dumps(result, indent=2, allow_nan=False) if args.format == "json" else render_markdown(result))
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Inspection stopped: {exc}\n")


if __name__ == "__main__":
    main()
