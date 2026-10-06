"""Evaluate frozen Olist forecasts on equal follow-up windows with unknown outcomes."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn

import diagnose_contextual_delivery as diagnostics


experiment = diagnostics.experiment
geo = experiment.geo
ROOT = experiment.ROOT
VERSION = "fixed_horizon_delivery_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_fixed_horizon_delivery_v1"
README = ROOT/"templates/fixed_horizon_delivery_v1/README.md"
HORIZONS = [14, 30, 45]
MONTHS = ["2018-03", "2018-04", "2018-05"]
POLICIES = ["horizon_only", "cutoff_verified_sensitivity"]
PAIRED_NAMES = ["legacy_iqr", "global_quantile", "peer_quantile"] + [
    f"gbr_{profile}_d{depth}_l{leaf}" for profile in ["no_distance", "geographic"] for depth in [2, 3] for leaf in [30, 60]]
FALLBACK_NAMES = ["fallback_global_quantile", "fallback_no_distance_d2_l30"]
FORECAST_COLUMNS = ["calibrated_lower_days", "calibrated_median_days", "calibrated_upper_days"]
TABLES = {"prediction_inputs.csv", "window_outcomes.csv", "frozen_predictions.csv", "parent_prediction_replay.csv",
          "model_state_audit.csv", "horizon_scores.csv", "window_coverage.csv", "window_metrics.csv", "paired_feature_comparisons.csv"}


def protocol():
    return {"version": VERSION, "horizon_days": HORIZONS, "months": MONTHS, "evidence_policies": POLICIES,
            "target": "minimum_of_observed_duration_and_horizon_when_evidence_available",
            "prediction": "minimum_of_frozen_median_prediction_and_horizon",
            "unresolved_error_bounds": "[0, max(capped_prediction, horizon-capped_prediction)]",
            "unresolved_paired_difference_bounds": "[-abs(p_geo-p_no), abs(p_geo-p_no)]",
            "primary_policy": "horizon_only", "paired_models": PAIRED_NAMES, "fallback_models": FALLBACK_NAMES,
            "refitting": False, "threshold_changes": False, "synthetic_data": False, "human_labels": False,
            "real_time_claim": False, "reporting_support_orders": 30}


def code_hashes():
    return {**diagnostics.code_hashes(), **{str(p.relative_to(ROOT)): geo.business.audit.file_hash(p)
                                          for p in [Path(__file__), README]}}


def verify_sources(provenance):
    path = Path(provenance["diagnostic_directory"])
    if geo.business.audit.file_hash(path/"manifest.json") != provenance["diagnostic_manifest_sha256"]:
        raise ValueError("Diagnostic parent manifest changed")
    geo.verify_files(path, provenance["diagnostic_output_hashes"])
    diagnostics.verify_sources(provenance["diagnostic_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Fixed-horizon source code changed")


def validate_bundle(bundle, manifest, ledger, adjustments, plan):
    if (bundle.get("version") != experiment.VERSION or bundle.get("identity") != geo.business.IDENTITY
            or bundle.get("protocol") != experiment.protocol() or bundle.get("plan") != plan
            or set(bundle.get("bank", {})) != set(PAIRED_NAMES+FALLBACK_NAMES)):
        raise ValueError("Frozen model identity/protocol/plan differs")
    for name, installed in {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__}.items():
        if manifest["runtime"][name] != installed:
            raise ValueError("Use the original model runtime; never silently migrate frozen estimators")
    adjustments = adjustments.set_index("model")
    expected_validation = set(ledger.loc[ledger.inner_role.eq("validation"), "order_id"])
    if set(bundle["validation_ids"]) != expected_validation:
        raise ValueError("Frozen validation identity differs")
    for name, entry in bundle["bank"].items():
        for role, key in [("fit", "fit_ids"), ("calibration", "calibration_ids")]:
            mask = ledger.inner_role.eq(role)
            if name in PAIRED_NAMES:
                mask &= ledger.paired_eligible
            elif name == "fallback_no_distance_d2_l30":
                mask &= ledger.numeric_context_eligible
            if len(entry[key]) != len(set(entry[key])) or set(entry[key]) != set(ledger.loc[mask, "order_id"]):
                raise ValueError("Frozen fit/calibration population differs")
        if entry["fit_count"] != len(entry["fit_ids"]):
            raise ValueError("Frozen fit support differs")
        for key, column in [("lower_shift", "lower_shift_days"), ("upper_shift", "upper_shift_days")]:
            if entry[key] != adjustments.at[name, column]:
                raise ValueError("Frozen calibration offsets differ")
        if entry["kind"] == "gbr":
            expected = [*experiment.NUMERIC, *(experiment.DISTANCES if entry["geographic"] else [])]
            if (entry["numeric"] != expected or entry["categorical"] != (experiment.CATEGORICAL if entry["geographic"] else [])
                    or len(entry["models"]) != 3):
                raise ValueError("Frozen predictor allowlist differs")


def load_inputs(diagnostic_dir=diagnostics.DEFAULT_OUTPUT):
    diagnostic_dir = Path(diagnostic_dir).resolve()
    before = geo.business.audit.file_hash(diagnostic_dir/"manifest.json")
    dm = diagnostics.load_snapshot(diagnostic_dir)
    diagnostics.verify_sources(dm["provenance"])
    parent = Path(dm["provenance"]["parent_directory"])
    ledger, manifest = experiment.load_snapshot(parent)
    predicted = geo.read_table(parent, "predictions.csv", manifest)
    diagnostics.validate_inputs(ledger, predicted)
    followup = geo.read_table(diagnostic_dir, "followup_case_audit.csv", dm)
    adjustments = geo.read_table(parent, "calibration_adjustments.csv", manifest)
    business = Path(manifest["provenance"]["business_directory"])
    bm = geo.read_json(business/"manifest.json")
    context = geo.read_table(business, "order_context.csv", bm)
    plan = geo.read_json(parent/"training_plan.json")
    provenance = {"diagnostic_directory": str(diagnostic_dir), "diagnostic_manifest_sha256": before,
                  "diagnostic_output_hashes": dm["output_hashes"], "diagnostic_sources": dm["provenance"],
                  "code_hashes": code_hashes(), "model_bundle_sha256": manifest["output_hashes"]["model_bundle.joblib"],
                  "reserved_test_orders_excluded": dm["summary"]["reserved_test_orders_excluded"]}
    verify_sources(provenance)
    # Only this locally produced, fully verified bundle is deserialized.
    runtime = {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__}
    if manifest["runtime"] != runtime:
        raise ValueError("Original frozen runtime required before deserializing the bundle")
    bundle = joblib.load(parent/"model_bundle.joblib")
    validate_bundle(bundle, manifest, ledger, adjustments, plan)
    return {"ledger": ledger, "predicted": predicted, "followup": followup, "context": context,
            "bundle": bundle, "manifest": manifest, "adjustments": adjustments, "plan": plan}, provenance


def build_prediction_inputs(inputs):
    ledger = inputs["ledger"]
    validation = ledger.loc[ledger.split.eq("validation")].sort_values("order_id").reset_index(drop=True)
    geo.business.require_key(validation, ["order_id"], "fixed-horizon validation")
    columns = ["order_id", "split", "purchase_month", "order_purchase_timestamp", "event_time_cutoff_exclusive",
               "inner_role", "timing_input_reason", "evaluation_route", "order_delivered_carrier_date",
               *experiment.NUMERIC, *experiment.DISTANCES, *experiment.CATEGORICAL, "distance_band", "all_pairs_quality_eligible"]
    frame = validation[columns].merge(inputs["context"][["order_id", "order_approved_at"]], on="order_id", how="left", validate="one_to_one")
    audit = inputs["followup"].loc[inputs["followup"].horizon_days.eq(HORIZONS[0])].set_index("order_id")
    if set(audit.index) != set(frame.order_id):
        raise ValueError("Follow-up and validation scope differ")
    frame["stage_ready"] = frame.order_id.map(audit.available_followup_days.notna())
    carrier = frame.order_delivered_carrier_date
    approved = frame.order_approved_at
    frame["approval_chronology_valid"] = (approved.ge(frame.order_purchase_timestamp) & approved.le(carrier)
                                           & carrier.lt(frame.event_time_cutoff_exclusive)).fillna(False)
    checks = {"purchased_to_approved_days": (approved-frame.order_purchase_timestamp).dt.total_seconds()/86400,
              "approved_to_carrier_days": (carrier-approved).dt.total_seconds()/86400}
    for name, actual in checks.items():
        observed = frame[name].notna() & actual.notna()
        np.testing.assert_allclose(frame.loc[observed, name].to_numpy(dtype=float), actual.loc[observed], rtol=1e-12, atol=1e-12)
    numeric = frame[experiment.NUMERIC].to_numpy(dtype=float, na_value=np.nan)
    frame["context_ready"] = frame.stage_ready & frame.approval_chronology_valid & (np.isfinite(numeric) & (numeric >= 0)).all(axis=1)
    frame["geographic_context_ready"] = frame.context_ready & frame.all_pairs_quality_eligible & frame[experiment.CATEGORICAL].notna().all(axis=1)
    frame["forecast_route"] = "stage_not_ready"
    frame.loc[frame.stage_ready, "forecast_route"] = "global_fallback_only"
    frame.loc[frame.context_ready, "forecast_route"] = "no_distance_fallback"
    frame.loc[frame.geographic_context_ready, "forecast_route"] = "paired_geographic_context"
    frame["formerly_scored_validation"] = frame.inner_role.eq("validation")
    frame["availability"] = "retrospective_snapshots_no_arrival_history"
    return frame


def score_frozen(bundle, frame, identity):
    if identity != bundle["identity"] or identity != geo.business.IDENTITY:
        raise ValueError("Different marketplace/category identity requires a separately trained model")
    if not frame.split.eq("validation").all() or not frame.purchase_month.isin(MONTHS).all():
        raise ValueError("Only declared development-validation forecasts are allowed")
    before = joblib.hash(bundle)
    results, audit = [], []
    for name in PAIRED_NAMES+FALLBACK_NAMES:
        entry = bundle["bank"][name]
        if name in PAIRED_NAMES:
            mask = frame.geographic_context_ready
            population = "paired_geographic_context"
        else:
            mask = frame.stage_ready & ~frame.geographic_context_ready
            if name == "fallback_no_distance_d2_l30":
                mask &= frame.context_ready
            population = "fallback_all_stage_ready" if name == "fallback_global_quantile" else "fallback_numeric_context_ready"
        selected = frame.loc[mask].copy()
        _, values, support, route, crossed, unknown = experiment.predict_entry(entry, selected)
        result = selected[["order_id", "purchase_month", "formerly_scored_validation", "forecast_route"]].copy()
        result["model"], result["population"] = name, population
        for i, column in enumerate(FORECAST_COLUMNS):
            result[column] = values[:, i]
        result["reference_fit_support"] = support
        result["reference_route"] = route
        result["raw_quantiles_crossed"] = crossed
        result["unseen_categorical_context"] = unknown
        results.append(result)
        audit.append({"model": name, "forecast_orders": len(result), "formerly_unscored_orders": int((~selected.formerly_scored_validation).sum()),
                      "fit_orders_unchanged": entry["fit_count"], "calibration_orders_unchanged": len(entry["calibration_ids"]),
                      "lower_shift_days_unchanged": entry["lower_shift"], "upper_shift_days_unchanged": entry["upper_shift"],
                      "refitted": False, "recalibrated": False})
    if joblib.hash(bundle) != before:
        raise ValueError("Scoring changed frozen model state")
    return pd.concat(results, ignore_index=True), pd.DataFrame(audit)


def verify_replay(forecasts, parent_predictions):
    parent = parent_predictions.loc[parent_predictions.inner_role.eq("validation")]
    rows = []
    for model, expected in parent.groupby("model", sort=True):
        actual = forecasts.loc[forecasts.model.eq(model)].set_index("order_id")
        if not set(expected.order_id).issubset(actual.index):
            raise ValueError("Previously scored validation cases missing from frozen replay")
        left = expected.set_index("order_id")[FORECAST_COLUMNS].sort_index()
        right = actual.loc[left.index, FORECAST_COLUMNS]
        pd.testing.assert_frame_equal(left, right, check_dtype=False, check_exact=True)
        rows.append({"model": model, "replayed_validation_orders": len(left), "max_absolute_prediction_difference": float(np.abs(left.to_numpy()-right.to_numpy()).max()),
                     "all_predictions_identical": True})
    return pd.DataFrame(rows)


def window_outcomes(inputs, frame):
    prior = inputs["followup"].copy()
    geo.business.require_key(prior, ["order_id", "horizon_days"], "fixed-horizon windows")
    for horizon in HORIZONS:
        if set(prior.loc[prior.horizon_days.eq(horizon), "order_id"]) != set(frame.order_id):
            raise ValueError("Every validation order must be retained at each horizon")
    if set(prior.horizon_days) != set(HORIZONS):
        raise ValueError("Unexpected follow-up horizons")
    result = prior[["order_id", "purchase_month", "event_time_cutoff_exclusive", "horizon_days", "visible_carrier_timestamp",
                    "available_followup_days", "full_followup_window", "followup_state"]].copy()
    result["horizon_endpoint"] = prior.visible_carrier_timestamp+pd.to_timedelta(prior.horizon_days, unit="D")
    within = prior.full_followup_window & prior.visible_delivery_timestamp.notna() & prior.observed_duration_days.le(prior.horizon_days)
    verified = prior.full_followup_window & prior.observed_duration_days.notna()
    result["horizon_visible_delivery_timestamp"] = prior.visible_delivery_timestamp.where(within)
    result["horizon_only_target_days"] = prior.observed_duration_days.where(within)
    result["cutoff_verified_sensitivity_target_days"] = np.minimum(prior.observed_duration_days, prior.horizon_days).where(verified)
    result["cutoff_verified_duration_days"] = prior.observed_duration_days.where(verified)
    result = result.merge(frame[["order_id", "stage_ready", "context_ready", "geographic_context_ready", "forecast_route", "formerly_scored_validation"]],
                          on="order_id", how="left", validate="many_to_one")
    expected_maturity = result.available_followup_days.notna() & result.horizon_endpoint.lt(result.event_time_cutoff_exclusive)
    if not expected_maturity.eq(result.full_followup_window).all():
        raise ValueError("Follow-up maturity differs from the recorded strict cutoff")
    return result


def make_scores(forecasts, outcomes):
    mature = outcomes.loc[outcomes.full_followup_window]
    scores = forecasts.merge(mature[["order_id", "horizon_days", "horizon_only_target_days", "cutoff_verified_sensitivity_target_days"]],
                              on="order_id", how="inner", validate="many_to_many")
    geo.business.require_key(scores, ["model", "order_id", "horizon_days"], "window scores")
    scores["capped_prediction_days"] = np.minimum(scores.calibrated_median_days, scores.horizon_days)
    for policy in POLICIES:
        target = scores[policy+"_target_days"]
        known = target.notna()
        error = (target-scores.capped_prediction_days).abs()
        scores[policy+"_outcome_known"] = known
        scores[policy+"_absolute_error_days"] = error
        scores[policy+"_error_lower_bound_days"] = error.where(known, 0.0)
        scores[policy+"_error_upper_bound_days"] = error.where(known, np.maximum(scores.capped_prediction_days, scores.horizon_days-scores.capped_prediction_days))
    return scores.sort_values(["model", "horizon_days", "purchase_month", "order_id"]).reset_index(drop=True)


def coverage_table(outcomes):
    rows = []
    for (horizon, month), group in outcomes.groupby(["horizon_days", "purchase_month"], sort=True):
        mature = group.loc[group.full_followup_window]
        row = {"horizon_days": horizon, "purchase_month": month, "validation_purchases": len(group),
               "mature_stage_orders": len(mature), "paired_context_orders": int(mature.geographic_context_ready.sum()),
               "fallback_stage_orders": int((mature.stage_ready & ~mature.geographic_context_ready).sum()),
               "formerly_unscored_mature_orders": int((~mature.formerly_scored_validation).sum()),
               "insufficient_followup_orders": int(group.followup_state.eq("insufficient_followup").sum()),
               "invalid_visible_stage_orders": int(group.followup_state.eq("invalid_visible_stage_chronology").sum()),
               "carrier_not_recorded_before_cutoff_orders": int(group.followup_state.eq("carrier_not_recorded_before_cutoff").sum())}
        for policy in POLICIES:
            row[policy+"_known_stage_outcomes"] = int(mature[policy+"_target_days"].notna().sum())
            row[policy+"_unresolved_stage_outcomes"] = int(mature[policy+"_target_days"].isna().sum())
        rows.append(row)
    return pd.DataFrame(rows)


def summarize_scores(scores):
    rows = []
    for model in PAIRED_NAMES+FALLBACK_NAMES:
        for horizon in HORIZONS:
            for month in MONTHS:
                frame = scores.loc[scores.model.eq(model) & scores.horizon_days.eq(horizon) & scores.purchase_month.eq(month)]
                for policy in POLICIES:
                    known = frame[policy+"_outcome_known"]
                    n = len(frame)
                    rows.append({"model": model, "horizon_days": horizon, "purchase_month": month, "evidence_policy": policy,
                                 "scored_mature_orders": n, "known_outcome_orders": int(known.sum()), "unresolved_outcome_orders": int((~known).sum()),
                                 "known_outcome_fraction": float(known.mean()) if n else np.nan,
                                 "observed_only_capped_mae_days": float(frame[policy+"_absolute_error_days"].mean()) if known.any() else np.nan,
                                 "all_scored_mae_lower_bound_days": float(frame[policy+"_error_lower_bound_days"].mean()) if n else np.nan,
                                 "all_scored_mae_upper_bound_days": float(frame[policy+"_error_upper_bound_days"].mean()) if n else np.nan,
                                 "formerly_unscored_orders": int((~frame.formerly_scored_validation).sum()),
                                 "reporting_support_at_least_30": n >= 30, "status": "bounded_missing_outcomes_not_confidence_interval" if n else "no_mature_cases"})
    return pd.DataFrame(rows)


def paired_comparisons(scores):
    rows = []
    for depth in [2, 3]:
        for leaf in [30, 60]:
            for horizon in HORIZONS:
                for month in MONTHS:
                    scoped = scores.loc[scores.horizon_days.eq(horizon) & scores.purchase_month.eq(month)]
                    geo_rows = scoped.loc[scoped.model.eq(f"gbr_geographic_d{depth}_l{leaf}")].set_index("order_id").sort_index()
                    base_rows = scoped.loc[scoped.model.eq(f"gbr_no_distance_d{depth}_l{leaf}")].set_index("order_id").sort_index()
                    if not geo_rows.index.equals(base_rows.index):
                        raise ValueError("Geographic comparison requires exactly paired orders")
                    distance = (geo_rows.capped_prediction_days-base_rows.capped_prediction_days).abs()
                    for policy in POLICIES:
                        target = policy+"_target_days"
                        pd.testing.assert_series_equal(geo_rows[target], base_rows[target], check_exact=True)
                        known = geo_rows[target].notna()
                        delta = geo_rows[policy+"_absolute_error_days"]-base_rows[policy+"_absolute_error_days"]
                        n = len(delta)
                        lower = float(delta.where(known, -distance).mean()) if n else np.nan
                        upper = float(delta.where(known, distance).mean()) if n else np.nan
                        direction = "no_mature_cases" if not n else "negative_only" if upper < 0 else "positive_only" if lower > 0 else "overlaps_zero"
                        rows.append({"depth": depth, "leaf": leaf, "horizon_days": horizon, "purchase_month": month, "evidence_policy": policy,
                                     "paired_mature_orders": n, "known_outcome_orders": int(known.sum()),
                                     "observed_only_geo_minus_no_distance_mae_days": float(delta.mean()) if known.any() else np.nan,
                                     "all_paired_difference_lower_bound_days": lower, "all_paired_difference_upper_bound_days": upper,
                                     "logical_bound_direction": direction, "is_confidence_interval": False, "winner_selected": False})
    return pd.DataFrame(rows)


def run_evaluation(inputs):
    frame = build_prediction_inputs(inputs)
    forecasts, model_audit = score_frozen(inputs["bundle"], frame, geo.business.IDENTITY)
    replay = verify_replay(forecasts, inputs["predicted"])
    outcomes = window_outcomes(inputs, frame)
    scores = make_scores(forecasts, outcomes)
    metrics = summarize_scores(scores)
    frames = {"prediction_inputs.csv": frame, "window_outcomes.csv": outcomes, "frozen_predictions.csv": forecasts,
              "parent_prediction_replay.csv": replay, "model_state_audit.csv": model_audit, "horizon_scores.csv": scores,
              "window_coverage.csv": coverage_table(outcomes), "window_metrics.csv": metrics,
              "paired_feature_comparisons.csv": paired_comparisons(scores)}
    summary = {"validation_purchases_retained": len(frame), "forecast_route_counts": frame.forecast_route.value_counts().to_dict(),
               "distinct_orders_with_frozen_forecasts": int(forecasts.order_id.nunique()),
               "formerly_unscored_orders_with_forecasts": int(forecasts.loc[~forecasts.formerly_scored_validation, "order_id"].nunique()),
               "frozen_forecast_rows": len(forecasts), "parent_validation_forecasts_replayed": int(replay.replayed_validation_orders.sum()),
               "all_parent_forecasts_reproduced_exactly": bool(replay.all_predictions_identical.all()),
               "horizon_case_rows": len(outcomes), "mature_case_forecast_rows": len(scores), "metrics_rows": len(metrics),
               "training_performed": False, "thresholds_changed": False, "model_state_changed": False,
               "missing_delivery_imputed": False, "synthetic_data_used": False, "human_labels_created": False,
               "test_scored": False, "operational_winner_selected": False, "anomaly_accuracy_computed": False,
               "real_time_validated": False, "primary_evidence_policy": "horizon_only",
               "limitation": "capped_target_not_original_duration_MAE; logical_bounds_not_sampling_confidence; retrospective_snapshots"}
    return frames, summary


def display_table(frame):
    display = frame.copy()
    for column in display.select_dtypes(include=["floating"]).columns:
        display[column] = display[column].map(lambda x: "" if pd.isna(x) else f"{x:.3f}")
    return geo.business.audit.markdown_table(display)


def render_readme(summary, frames):
    focal = frames["window_metrics.csv"].loc[lambda x: x.model.eq(diagnostics.FOCAL) & x.evidence_policy.eq("horizon_only"),
                                            ["horizon_days", "purchase_month", "scored_mature_orders", "known_outcome_orders", "unresolved_outcome_orders",
                                             "observed_only_capped_mae_days", "all_scored_mae_lower_bound_days", "all_scored_mae_upper_bound_days"]]
    paired = frames["paired_feature_comparisons.csv"].loc[lambda x: x.depth.eq(2) & x.leaf.eq(60) & x.evidence_policy.eq("horizon_only"),
                                                          ["horizon_days", "purchase_month", "paired_mature_orders", "all_paired_difference_lower_bound_days",
                                                           "all_paired_difference_upper_bound_days", "logical_bound_direction"]]
    return "\n\n".join(["# Actual Fixed-Horizon Evaluation", "## Primary Focal Results\nMissing outcomes remain unresolved. Capped MAE is a different target from the earlier full-duration MAE. Bounds are not confidence intervals.\n"+display_table(focal),
                         "## Paired Geographic Comparison\nNegative differences favor geographic forecasts on the stated capped target and cohort only.\n"+display_table(paired),
                         "## Frozen Replay\n"+display_table(frames["parent_prediction_replay.csv"]),
                         README.read_text(), "## Run Summary\n```json\n"+json.dumps(summary, indent=2)+"\n```"])+"\n"


def write_evaluation(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite fixed-horizon results: {output}")
    if set(frames) != TABLES:
        raise ValueError("Expected every fixed-horizon evidence table")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".fixed-horizon-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            restored = geo.read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(restored, frame, check_dtype=False, check_exact=True)
        summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            (staging/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames))
        manifest = {"status": "complete", "version": VERSION, "identity": geo.business.IDENTITY, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "provenance": provenance, "schemas": schemas,
                    "runtime": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
                    "output_hashes": {p.name: geo.business.audit.file_hash(p) for p in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = geo.read_json(directory/"manifest.json")
    required = TABLES | {"summary.json", "protocol.json", "README.md"}
    if (manifest.get("status") != "complete" or manifest.get("version") != VERSION or manifest.get("identity") != geo.business.IDENTITY
            or set(manifest.get("schemas", {})) != TABLES or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected complete fixed-horizon snapshot")
    geo.verify_files(directory, manifest["output_hashes"])
    if geo.read_json(directory/"protocol.json") != protocol() or geo.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Fixed-horizon summary/protocol binding differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--diagnostics", type=Path, default=diagnostics.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying sources and frozen local models; no fitting or test scoring.", flush=True)
        inputs, provenance = load_inputs(args.diagnostics)
        print("Replaying forecasts and evaluating equal follow-up with unresolved outcomes.", flush=True)
        frames, summary = run_evaluation(inputs)
        summary = write_evaluation(frames, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Fixed-horizon evaluation stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
