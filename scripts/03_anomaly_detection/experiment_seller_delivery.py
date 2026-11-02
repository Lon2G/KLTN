"""Paired Olist delivery ablation with strictly prior seller-history features."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn

import build_seller_history as seller
import evaluate_fixed_horizon_delivery as windows


context = windows.experiment
shared = context.geo
ROOT = context.ROOT
VERSION = "seller_delivery_ablation_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_seller_delivery_v1"
README = ROOT/"templates/seller_delivery_v1/README.md"
ACTIVITY = ["prior_orders", "prior_orders_30d", "days_since_last_prior_purchase"]
OUTCOMES = ["prior_promise_comparable_orders", "prior_after_promise_fraction", "prior_reviewed_orders",
            *[f"prior_review_score_{n}_fraction" for n in range(1, 5)]]
PROFILES = {"base_geo": [], "plus_activity": ACTIVITY, "plus_seller": ACTIVITY+OUTCOMES}
CONFIGS = [(depth, leaf) for depth in [2, 3] for leaf in [30, 60]]
MODELS = [(f"{profile}_d{depth}_l{leaf}", profile, depth, leaf) for profile in PROFILES for depth, leaf in CONFIGS]
TABLES = {"analysis_ledger.csv", "forecast_inputs.csv", "feature_contract.csv", "scope_coverage.csv",
          "calibration_adjustments.csv", "predictions.csv", "metrics.csv", "validation_subgroups.csv",
          "paired_differences.csv", "frozen_forecasts.csv", "forecast_replay.csv",
          "horizon_scores.csv", "horizon_metrics.csv", "horizon_paired_differences.csv"}


def protocol():
    return {"version": VERSION, "profiles": PROFILES, "base_numeric": context.NUMERIC+context.DISTANCES,
            "categorical": context.CATEGORICAL, "gbr_grid": context.protocol()["gbr_grid"], "quantiles": context.QUANTILES,
            "target": context.TARGET, "history_time": "strictly_prior_to_current_purchase; fixed_seller_snapshot",
            "cohort": "identical_single_seller_geographic_and_complete_history_cases_for_all_profiles",
            "missing_policy": "no_imputation; require_activity_and_observed_delivery_and_review_history_for_every_profile",
            "review_score_5": "omitted_reference_fraction; all_five_preserved_in_source; no_low_score_cutoff",
            "fit_calibration_validation": "inherit_frozen_roles_and_event_cutoffs; refit_every_profile_on_common_subset",
            "geographic_screen": "reuse_parent_training_only_screen; no_relearning",
            "calibration": context.protocol()["calibration"], "horizons": windows.HORIZONS, "evidence_policies": windows.POLICIES,
            "incomplete_forecasts": "same_common_history_and_stage_ready_population_for_every_new_model",
            "comparison": "paired_descriptive; reused_validation; no_winner_or_significance_claim",
            "test_scored": False, "real_time_validated": False, "anomaly_accuracy_computed": False}


def code_hashes():
    return {**windows.code_hashes(), **seller.code_hashes(), **{str(p.relative_to(ROOT)): shared.business.audit.file_hash(p)
                                                            for p in [Path(__file__), README]}}


def verify_sources(provenance):
    for prefix in ["context", "seller", "windows"]:
        path = Path(provenance[prefix+"_directory"])
        if shared.business.audit.file_hash(path/"manifest.json") != provenance[prefix+"_manifest_sha256"]:
            raise ValueError(f"Seller ablation {prefix} manifest changed")
        shared.verify_files(path, provenance[prefix+"_output_hashes"])
    # Window provenance already binds and verifies the full contextual ancestor chain.
    windows.verify_sources(provenance["windows_sources"])
    seller.verify_sources(provenance["seller_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Seller ablation code/protocol changed")


def load_inputs(context_dir=context.DEFAULT_OUTPUT, seller_dir=seller.DEFAULT_OUTPUT, window_dir=windows.DEFAULT_OUTPUT):
    context_dir, seller_dir, window_dir = [Path(path).resolve() for path in [context_dir, seller_dir, window_dir]]
    ledger, cm = context.load_snapshot(context_dir)
    sm, wm = seller.load_snapshot(seller_dir), windows.load_snapshot(window_dir)
    diagnostic = wm["provenance"]["diagnostic_sources"]
    if (Path(diagnostic["parent_directory"]).resolve() != context_dir
            or diagnostic["parent_manifest_sha256"] != shared.business.audit.file_hash(context_dir/"manifest.json")
            or sm["provenance"]["business_manifest_sha256"] != cm["provenance"]["business_manifest_sha256"]):
        raise ValueError("Seller/context/window snapshots must share the same source lineage")
    manifests = {"context": cm, "seller": sm, "windows": wm}
    provenance = {"code_hashes": code_hashes(), "windows_sources": wm["provenance"], "seller_sources": sm["provenance"],
                  "reserved_test_orders_excluded": cm["summary"]["reserved_test_orders_excluded"]}
    for prefix, path in [("context", context_dir), ("seller", seller_dir), ("windows", window_dir)]:
        provenance.update({prefix+"_directory": str(path), prefix+"_manifest_sha256": shared.business.audit.file_hash(path/"manifest.json"),
                           prefix+"_output_hashes": manifests[prefix]["output_hashes"]})
    verify_sources(provenance)
    inputs = {"ledger": ledger, "history": shared.read_table(seller_dir, "seller_order_history.csv", sm),
              "seller_orders": shared.read_table(seller_dir, "order_seller_history.csv", sm),
              "forecast_inputs": shared.read_table(window_dir, "prediction_inputs.csv", wm),
              "outcomes": shared.read_table(window_dir, "window_outcomes.csv", wm),
              "plan": shared.read_json(context_dir/"training_plan.json")}
    validate_inputs(inputs)
    return inputs, provenance


def validate_inputs(inputs):
    ledger, orders, history, forecasts = (inputs[key] for key in ["ledger", "seller_orders", "history", "forecast_inputs"])
    shared.business.require_key(ledger, ["order_id"], "ablation ledger")
    shared.business.require_key(orders, ["order_id"], "ablation seller orders")
    shared.business.require_key(history, ["order_id", "seller_id"], "ablation seller pairs")
    shared.business.require_key(forecasts, ["order_id"], "ablation forecast inputs")
    if (not ledger.split.isin(["train", "validation"]).all() or set(ledger.order_id) != set(orders.order_id)
            or set(ledger.order_id) != set(history.order_id)
            or set(forecasts.order_id) != set(ledger.loc[ledger.split.eq("validation"), "order_id"])):
        raise ValueError("Seller ablation development/validation scope differs")
    indexed = ledger.set_index("order_id")
    columns = ["split", "order_purchase_timestamp", "seller_count"]
    pd.testing.assert_frame_equal(orders.set_index("order_id")[columns], indexed.loc[orders.order_id, columns],
                                  check_dtype=False, check_index_type=False, check_exact=True)
    if (not history.split.eq(history.order_id.map(indexed.split)).all()
            or not history.as_of_timestamp.eq(history.order_id.map(indexed.order_purchase_timestamp)).all()):
        raise ValueError("History must be measured at each actual purchase")
    for field in seller.TIME_COLUMNS:
        visible = history[field].notna()
        if not history.loc[visible, field].lt(history.loc[visible, "as_of_timestamp"]).all():
            raise ValueError("Future history evidence is not allowed")
    counts = history.groupby("order_id").size()
    if not ledger.seller_count.eq(ledger.order_id.map(counts)).all():
        raise ValueError("Seller multiplicity differs")
    one = history.loc[history.order_id.isin(ledger.loc[ledger.seller_count.eq(1), "order_id"])]
    if not one.seller_id.eq(one.order_id.map(indexed.single_seller_id)).all():
        raise ValueError("Single seller identity differs")
    eligible = history["prior_promise_comparable_orders"].gt(0) & history.prior_reviewed_orders.gt(0)
    rates = ["prior_after_promise_fraction", *[f"prior_review_score_{n}_fraction" for n in range(1, 6)]]
    values = history.loc[eligible, rates].to_numpy(dtype=float)
    if not (np.isfinite(values) & (values >= 0) & (values <= 1)).all():
        raise ValueError("Observed historical fractions must be finite and bounded")
    np.testing.assert_allclose(values[:, 1:].sum(axis=1), 1, rtol=1e-12, atol=1e-12)
    outcomes = inputs["outcomes"]
    shared.business.require_key(outcomes, ["order_id", "horizon_days"], "ablation windows")
    if set(outcomes.horizon_days) != set(windows.HORIZONS):
        raise ValueError("Unexpected horizon scope")
    for _, group in outcomes.groupby("horizon_days"):
        if set(group.order_id) != set(forecasts.order_id):
            raise ValueError("Every validation order requires every horizon")


def attach_history(frame, history):
    one = frame.loc[frame.seller_count.eq(1), "order_id"]
    columns = ["order_id", *ACTIVITY, *OUTCOMES, *seller.TIME_COLUMNS]
    selected = history.loc[history.order_id.isin(one), columns]
    result = frame.merge(selected, on="order_id", how="left", validate="one_to_one")
    values = result[ACTIVITY+OUTCOMES].to_numpy(dtype=float, na_value=np.nan)
    result["history_ready"] = ((np.isfinite(values) & (values >= 0)).all(axis=1)
                                & result.prior_promise_comparable_orders.gt(0) & result.prior_reviewed_orders.gt(0))
    return result


def build_design(inputs):
    validate_inputs(inputs)
    ledger = attach_history(inputs["ledger"], inputs["history"]).sort_values("order_id").reset_index(drop=True)
    ledger["common_eligible"] = ledger.paired_eligible & ledger.seller_count.eq(1) & ledger.history_ready
    ledger["ablation_route"] = "common_comparison"
    ledger.loc[~ledger.history_ready, "ablation_route"] = "historical_rates_or_activity_unavailable"
    ledger.loc[ledger.seller_count.ne(1), "ablation_route"] = "multi_seller_not_attributed"
    ledger.loc[~ledger.paired_eligible, "ablation_route"] = "parent_geography_or_context_unavailable"
    ledger.loc[~ledger.inner_role.isin(context.ROLES), "ablation_route"] = "timing_unavailable_or_deferred"
    for role, minimum in [("fit", 512), ("calibration", 200), ("validation", 1)]:
        if (ledger.inner_role.eq(role) & ledger.common_eligible).sum() < minimum:
            raise ValueError(f"Insufficient common {role} orders; never pad or impute")
    forecasts = attach_history(inputs["forecast_inputs"], inputs["history"]).sort_values("order_id").reset_index(drop=True)
    forecasts["ablation_forecast_eligible"] = forecasts.geographic_context_ready & forecasts.seller_count.eq(1) & forecasts.history_ready
    # Existing completed-case forecasts must also be representable without supplying their outcomes.
    old = ledger.loc[ledger.inner_role.eq("validation") & ledger.common_eligible]
    actual = forecasts.set_index("order_id").loc[old.order_id]
    if not actual.ablation_forecast_eligible.all():
        raise ValueError("A common completed validation order lost forecast eligibility")
    predictors = context.NUMERIC+context.DISTANCES+context.CATEGORICAL+ACTIVITY+OUTCOMES
    pd.testing.assert_frame_equal(old.set_index("order_id")[predictors], actual[predictors],
                                  check_dtype=False, check_index_type=False, check_exact=True)
    return ledger, forecasts


def fit_entry(frame, profile, depth, leaf):
    if profile not in PROFILES or (depth, leaf) not in CONFIGS:
        raise ValueError("Undeclared ablation configuration")
    shared.business.require_key(frame, ["order_id"], "seller ablation fit")
    if frame.empty or not frame.inner_role.eq("fit").all() or not frame.common_eligible.all():
        raise ValueError("Only common fit rows can train an ablation model")
    numeric = context.NUMERIC+context.DISTANCES+PROFILES[profile]
    values = frame[numeric+[context.TARGET]].to_numpy(dtype=float)
    if not (np.isfinite(values) & (values >= 0)).all():
        raise ValueError("Only observed finite nonnegative inputs and targets; no imputation")
    entry = {"kind": "gbr", "geographic": True, "profile": profile, "depth": depth, "leaf": leaf,
             "numeric": numeric, "categorical": context.CATEGORICAL.copy(), "fit_ids": frame.order_id.tolist(),
             "fit_count": len(frame), "lower_shift": 0.0, "upper_shift": 0.0, "models": []}
    grid = protocol()["gbr_grid"]
    for quantile in context.QUANTILES:
        encoder = context.ColumnTransformer([("numeric", "passthrough", numeric),
                                             ("categorical", context.OneHotEncoder(handle_unknown="ignore", sparse_output=False), context.CATEGORICAL)], remainder="drop")
        model = context.Pipeline([("encode", encoder), ("regressor", context.GradientBoostingRegressor(
            loss="quantile", alpha=quantile, n_estimators=grid["trees"], learning_rate=grid["learning_rate"],
            max_depth=depth, min_samples_leaf=leaf, random_state=grid["seed"]))])
        model.fit(frame[numeric+context.CATEGORICAL], frame[context.TARGET].to_numpy(dtype=float))
        entry["models"].append(model)
    return entry


def score_validation(bundle, frame, identity):
    if identity != shared.business.IDENTITY or identity != bundle["identity"] or bundle["protocol"] != protocol():
        raise ValueError("Wrong seller ablation model identity/protocol")
    if not frame.split.eq("validation").all():
        raise ValueError("Only development validation may be forecast in this experiment")
    shared.business.require_key(frame, ["order_id"], "seller ablation forecast")
    before = joblib.hash(bundle)
    selected = frame.loc[frame.ablation_forecast_eligible]
    result = []
    for name, entry in bundle["bank"].items():
        _, values, support, route, crossed, unknown = context.predict_entry(entry, selected)
        rows = selected[["order_id", "purchase_month", "formerly_scored_validation", "forecast_route"]].copy()
        rows["model"], rows["profile"] = name, entry["profile"]
        for i, column in enumerate(windows.FORECAST_COLUMNS):
            rows[column] = values[:, i]
        rows["reference_fit_support"], rows["reference_route"] = support, route
        rows["raw_quantiles_crossed"], rows["unseen_categorical_context"] = crossed, unknown
        result.append(rows)
    if joblib.hash(bundle) != before:
        raise ValueError("Forecasting changed fitted model state")
    return pd.concat(result, ignore_index=True)


def paired_rows(frame, depth, leaf, profile):
    base = frame.loc[frame.model.eq(f"base_geo_d{depth}_l{leaf}")].set_index("order_id").sort_index()
    other = frame.loc[frame.model.eq(f"{profile}_d{depth}_l{leaf}")].set_index("order_id").sort_index()
    if not base.index.equals(other.index) or base.index.has_duplicates:
        raise ValueError("Ablation comparison requires identical unique order IDs")
    return base, other


def paired_differences(predicted):
    validation = predicted.loc[predicted.inner_role.eq("validation")]
    rows = []
    for depth, leaf in CONFIGS:
        for profile in ["plus_activity", "plus_seller"]:
            for month in ["all", *windows.MONTHS]:
                selected = validation if month == "all" else validation.loc[validation.purchase_month.eq(month)]
                base, other = paired_rows(selected, depth, leaf, profile)
                pd.testing.assert_series_equal(base[context.TARGET], other[context.TARGET], check_exact=True)
                base_error = (base[context.TARGET]-base.calibrated_median_days).abs()
                other_error = (other[context.TARGET]-other.calibrated_median_days).abs()
                delta = other_error-base_error
                rows.append({"depth": depth, "leaf": leaf, "profile": profile, "purchase_month": month, "paired_orders": len(delta),
                             "base_mae_days": float(base_error.mean()), "augmented_mae_days": float(other_error.mean()),
                             "augmented_minus_base_mae_days": float(delta.mean()),
                             "orders_with_lower_error": int(delta.lt(0).sum()), "orders_with_higher_error": int(delta.gt(0).sum()),
                             "orders_with_equal_error": int(delta.eq(0).sum()), "is_significance_test": False, "winner_selected": False})
    return pd.DataFrame(rows)


def horizon_summaries(scores):
    metrics, comparisons = [], []
    for horizon in windows.HORIZONS:
        for month in windows.MONTHS:
            scoped = scores.loc[scores.horizon_days.eq(horizon) & scores.purchase_month.eq(month)]
            for name, profile, depth, leaf in MODELS:
                frame = scoped.loc[scoped.model.eq(name)]
                for policy in windows.POLICIES:
                    known = frame[policy+"_outcome_known"]
                    metrics.append({"model": name, "profile": profile, "horizon_days": horizon, "purchase_month": month,
                                    "evidence_policy": policy, "scored_mature_orders": len(frame), "known_outcome_orders": int(known.sum()),
                                    "unresolved_outcome_orders": int((~known).sum()),
                                    "observed_only_capped_mae_days": float(frame[policy+"_absolute_error_days"].mean()) if known.any() else np.nan,
                                    "all_scored_mae_lower_bound_days": float(frame[policy+"_error_lower_bound_days"].mean()) if len(frame) else np.nan,
                                    "all_scored_mae_upper_bound_days": float(frame[policy+"_error_upper_bound_days"].mean()) if len(frame) else np.nan,
                                    "status": "logical_missing_outcome_bounds_not_confidence_interval" if len(frame) else "no_mature_cases"})
            for depth, leaf in CONFIGS:
                for profile in ["plus_activity", "plus_seller"]:
                    base, other = paired_rows(scoped, depth, leaf, profile)
                    distance = (other.capped_prediction_days-base.capped_prediction_days).abs()
                    for policy in windows.POLICIES:
                        target = policy+"_target_days"
                        pd.testing.assert_series_equal(base[target], other[target], check_exact=True)
                        known = base[target].notna()
                        delta = other[policy+"_absolute_error_days"]-base[policy+"_absolute_error_days"]
                        lower = float(delta.where(known, -distance).mean()) if len(delta) else np.nan
                        upper = float(delta.where(known, distance).mean()) if len(delta) else np.nan
                        direction = "no_mature_cases" if not len(delta) else "negative_only" if upper < 0 else "positive_only" if lower > 0 else "overlaps_zero"
                        comparisons.append({"depth": depth, "leaf": leaf, "profile": profile, "horizon_days": horizon,
                                            "purchase_month": month, "evidence_policy": policy, "paired_mature_orders": len(delta),
                                            "known_outcome_orders": int(known.sum()), "augmented_minus_base_lower_bound_days": lower,
                                            "augmented_minus_base_upper_bound_days": upper, "logical_bound_direction": direction,
                                            "is_confidence_interval": False, "winner_selected": False})
    return pd.DataFrame(metrics), pd.DataFrame(comparisons)


def run_experiment(inputs, progress=print):
    ledger, forecast_inputs = build_design(inputs)
    selected = {role: ledger.loc[ledger.inner_role.eq(role) & ledger.common_eligible].copy() for role in context.ROLES}
    progress("Identical model populations: "+str({role: len(frame) for role, frame in selected.items()}))
    bank, predictions, shifts = {}, [], []
    for name, profile, depth, leaf in MODELS:
        progress("Fitting new paired model: "+name)
        entry = context.calibrate_entry(fit_entry(selected["fit"], profile, depth, leaf), selected["calibration"])
        bank[name] = entry
        predictions += [context.predictions(entry, selected[role], name, "common_single_seller_history") for role in context.ROLES[1:]]
        shifts.append({"model": name, "profile": profile, "fit_orders": entry["fit_count"], "calibration_orders": len(entry["calibration_ids"]),
                       "lower_shift_days": entry["lower_shift"], "upper_shift_days": entry["upper_shift"]})
    predicted = pd.concat(predictions, ignore_index=True)
    bundle = {"version": VERSION, "identity": shared.business.IDENTITY, "protocol": protocol(), "plan": inputs["plan"], "bank": bank,
              "common_validation_ids": selected["validation"].order_id.tolist(),
              "expanded_validation_ids": forecast_inputs.loc[forecast_inputs.ablation_forecast_eligible, "order_id"].tolist()}
    forecasts = score_validation(bundle, forecast_inputs, shared.business.IDENTITY)
    replay = windows.verify_replay(forecasts, predicted)
    scored = windows.make_scores(forecasts, inputs["outcomes"])
    horizon_metrics, horizon_comparisons = horizon_summaries(scored)
    evaluated = context.evaluate(predicted)
    feature_rows = [{"profile": profile, "column": column, "kind": "numeric", "source": "prior_purchase_seller_history" if column in ACTIVITY+OUTCOMES else "parent_geographic_context"}
                    for profile in PROFILES for column in context.NUMERIC+context.DISTANCES+PROFILES[profile]]
    feature_rows += [{"profile": profile, "column": column, "kind": "categorical", "source": "parent_geographic_context"}
                     for profile in PROFILES for column in context.CATEGORICAL]
    frames = {"analysis_ledger.csv": ledger, "forecast_inputs.csv": forecast_inputs, "feature_contract.csv": pd.DataFrame(feature_rows),
              "scope_coverage.csv": ledger.groupby(["split", "inner_role", "ablation_route"], sort=True).size().rename("orders").reset_index(),
              "calibration_adjustments.csv": pd.DataFrame(shifts), "predictions.csv": predicted, "metrics.csv": evaluated["metrics.csv"],
              "validation_subgroups.csv": evaluated["validation_subgroups.csv"], "paired_differences.csv": paired_differences(predicted),
              "frozen_forecasts.csv": forecasts, "forecast_replay.csv": replay, "horizon_scores.csv": scored,
              "horizon_metrics.csv": horizon_metrics, "horizon_paired_differences.csv": horizon_comparisons}
    summary = {"development_orders_retained": len(ledger), "validation_orders_retained": len(forecast_inputs),
               "common_role_counts": {role: len(frame) for role, frame in selected.items()},
               "profiles": len(PROFILES), "paired_model_configurations": len(bank), "new_quantile_regressors": sum(len(x["models"]) for x in bank.values()),
               "baseline_refitted_on_identical_subset": True, "expanded_validation_forecast_orders": int(forecasts.order_id.nunique()),
               "additional_forecast_orders_without_original_completed_eligibility": int(forecasts.loc[~forecasts.formerly_scored_validation, "order_id"].nunique()),
               "common_validation_forecasts_replayed_exactly": int(replay.replayed_validation_orders.sum()),
               "new_models_trained": True, "new_calibration_performed": True, "previous_models_changed": False, "previous_thresholds_changed": False,
               "imputation_performed": False, "synthetic_data_used": False, "human_labels_created": False, "test_scored": False,
               "anomaly_accuracy_computed": False, "real_time_validated": False, "operational_winner_selected": False,
               "causal_seller_effect_proven": False, "independent_new_data_evaluation": False,
               "interpretation": "paired_exploratory_ablation_on_reused_validation; completed_case_and_history_selection_remain"}
    return frames, bundle, summary


def render_readme(summary, frames):
    metrics = frames["metrics.csv"].loc[lambda x: x.role.eq("validation") & x.view.eq("calibrated"),
                                       ["model", "orders", "median_mae_days", "coverage", "mean_width_days", "interval_score_alpha010"]]
    effects = frames["paired_differences.csv"].loc[lambda x: x.purchase_month.eq("all")]
    return "\n\n".join(["# Actual Seller Delivery Ablation", "## Paired Validation\n"+windows.display_table(metrics),
                         "## Changes Relative to the Refit Geographic Baseline\n"+windows.display_table(effects), README.read_text(),
                         "## Run Summary\n```json\n"+json.dumps(summary, indent=2)+"\n```"])+"\n"


def write_experiment(frames, bundle, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite seller ablation: {output}")
    if set(frames) != TABLES:
        raise ValueError("Missing seller ablation evidence tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".seller-ablation-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        joblib.dump(bundle, staging/"model_bundle.joblib")
        # Only reload the trusted bundle produced in this staging directory.
        restored = joblib.load(staging/"model_bundle.joblib")
        actual = score_validation(restored, frames["forecast_inputs.csv"], shared.business.IDENTITY)
        pd.testing.assert_frame_equal(actual, frames["frozen_forecasts.csv"], check_exact=True)
        summary = {**summary, "saved_model_roundtrip_verified": True, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        for name, value in [("summary.json", summary), ("protocol.json", protocol()), ("training_plan.json", bundle["plan"])]:
            (staging/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames))
        manifest = {"status": "complete", "version": VERSION, "identity": shared.business.IDENTITY,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "summary": summary, "provenance": provenance, "schemas": schemas,
                    "runtime": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
                    "output_hashes": {path.name: shared.business.audit.file_hash(path) for path in sorted(staging.iterdir())}}
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
    required = TABLES | {"summary.json", "protocol.json", "README.md", "model_bundle.joblib", "training_plan.json"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete" or manifest.get("identity") != shared.business.IDENTITY
            or set(manifest.get("schemas", {})) != TABLES or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected complete seller-delivery ablation snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    if shared.read_json(directory/"protocol.json") != protocol() or shared.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Seller-delivery protocol/summary binding differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", type=Path, default=context.DEFAULT_OUTPUT)
    parser.add_argument("--seller", type=Path, default=seller.DEFAULT_OUTPUT)
    parser.add_argument("--windows", type=Path, default=windows.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying real source snapshots; no previous model or review labels loaded.", flush=True)
        inputs, provenance = load_inputs(args.context, args.seller, args.windows)
        frames, bundle, summary = run_experiment(inputs, lambda value: print(value, flush=True))
        summary = write_experiment(frames, bundle, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Seller delivery ablation stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
