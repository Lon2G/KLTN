"""Diagnose saved Olist duration predictions without refitting or labeling."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import experiment_contextual_delivery as experiment


ROOT = experiment.ROOT
VERSION = "contextual_delivery_diagnostics_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_contextual_diagnostics_v1"
README = ROOT/"templates/contextual_diagnostics_v1/README.md"
FOCAL = "gbr_geographic_d2_l60"
TARGET = experiment.TARGET
PAIRED = "paired_geography_eligible"
HORIZONS = [14, 30, 45]
NUMERIC = [*experiment.NUMERIC, *experiment.DISTANCES]
TABLES = {"case_fit_support.csv", "fit_numeric_references.csv", "feature_shift_by_month.csv",
          "analyst_case_diagnostics.csv", "monthly_model_diagnostics.csv", "validation_error_slices.csv",
          "paired_geographic_effects.csv", "composition_details.csv", "composition_summary.csv",
          "observation_coverage.csv", "timing_exclusion_reasons.csv", "followup_case_audit.csv", "followup_summary.csv"}


def protocol():
    return {"version": VERSION, "mode": "post_hoc_saved_prediction_diagnostics",
            "focal_model": FOCAL, "focal_selection": "previously_discussed_after_validation_not_preregistered",
            "retain_all_parent_candidates": True, "numeric_reference": "paired_inner_fit_only",
            "numeric_reference_quantiles": [.01, .99], "composition_period_A": ["2018-03"],
            "composition_period_B": ["2018-04", "2018-05"], "minimum_common_group_orders_per_period": 20,
            "composition_dimensions": ["distance_band", "customer_state", "state_distance_group"],
            "composition_quantities": ["absolute_error_days", "signed_error_days", TARGET],
            "followup_horizons_days": HORIZONS, "full_followup": "carrier_plus_horizon_strictly_before_exclusive_cutoff",
            "support_marker_orders": 30, "error_concentration_fraction": .1,
            "refitting": False, "threshold_changes": False, "human_label_creation": False,
            "test_scoring": False, "causal_inference": False}


def code_hashes():
    return {**experiment.code_hashes(), **{str(p.relative_to(ROOT)): experiment.geo.business.audit.file_hash(p)
                                         for p in [Path(__file__), README]}}


def verify_sources(provenance):
    parent = Path(provenance["parent_directory"])
    if experiment.geo.business.audit.file_hash(parent/"manifest.json") != provenance["parent_manifest_sha256"]:
        raise ValueError("Parent contextual manifest changed")
    experiment.geo.verify_files(parent, provenance["parent_output_hashes"])
    experiment.verify_sources(provenance["parent_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Diagnostic code or protocol changed")


def load_inputs(parent=experiment.DEFAULT_OUTPUT):
    parent = Path(parent).resolve()
    before = experiment.geo.business.audit.file_hash(parent/"manifest.json")
    ledger, manifest = experiment.load_snapshot(parent)
    predicted = experiment.geo.read_table(parent, "predictions.csv", manifest)
    validate_inputs(ledger, predicted)
    provenance = {"parent_directory": str(parent), "parent_manifest_sha256": before,
                  "parent_output_hashes": manifest["output_hashes"], "parent_sources": manifest["provenance"],
                  "code_hashes": code_hashes(), "reserved_test_orders_excluded": manifest["summary"]["reserved_test_orders_excluded"]}
    verify_sources(provenance)
    return ledger, predicted, provenance


def validate_inputs(ledger, predicted):
    experiment.geo.business.require_key(ledger, ["order_id"], "diagnostic development ledger")
    experiment.geo.business.require_key(predicted, ["model", "inner_role", "order_id"], "saved predictions")
    if ledger.empty or not ledger.split.isin(["train", "validation"]).all():
        raise ValueError("Only nonempty development scope allowed")
    if predicted.empty or not predicted.inner_role.isin(["calibration", "validation"]).all():
        raise ValueError("Expected held-out predictions, never fitted or reserved test cases")
    if not set(predicted.order_id).issubset(set(ledger.order_id)):
        raise ValueError("Prediction IDs outside development ledger")
    paired_names = {"legacy_iqr", "global_quantile", "peer_quantile"} | {
        f"gbr_{profile}_d{depth}_l{leaf}" for profile in ["geographic", "no_distance"] for depth in [2, 3] for leaf in [30, 60]}
    expected_names = paired_names | {"fallback_global_quantile", "fallback_no_distance_d2_l30"}
    if set(predicted.model) != expected_names:
        raise ValueError("All frozen candidate predictions must be retained")
    expected_groups = {(name, role) for name in paired_names for role in ["calibration", "validation"]}
    actual_groups = set(zip(predicted.model, predicted.inner_role))
    if not expected_groups.issubset(actual_groups):
        raise ValueError("Every paired candidate must retain both held-out roles")
    for role in ["calibration", "validation"]:
        outside = ledger.inner_role.eq(role) & ~ledger.paired_eligible
        if outside.any():
            expected_groups.add(("fallback_global_quantile", role))
        if (outside & ledger.numeric_context_eligible).any():
            expected_groups.add(("fallback_no_distance_d2_l30", role))
    if actual_groups != expected_groups:
        raise ValueError("Frozen fallback role coverage differs")
    for (model, role), frame in predicted.groupby(["model", "inner_role"], sort=True):
        scope = ledger.inner_role.eq(role)
        if model in paired_names:
            scope &= ledger.paired_eligible
            population = PAIRED
        else:
            scope &= ~ledger.paired_eligible
            if model == "fallback_no_distance_d2_l30":
                scope &= ledger.numeric_context_eligible
            population = "outside_paired_geography_population"
        if set(frame.order_id) != set(ledger.loc[scope, "order_id"]) or not frame.population.eq(population).all():
            raise ValueError("Frozen candidate coverage differs")
    ordered = ledger.set_index("order_id").loc[predicted.order_id].reset_index()
    columns = ["order_id", "inner_role", "purchase_month", "customer_state", "seller_count", "distance_band", TARGET]
    pd.testing.assert_frame_equal(predicted[columns].reset_index(drop=True), ordered[columns], check_dtype=False, check_exact=True)
    values = predicted[["calibrated_lower_days", "calibrated_median_days", "calibrated_upper_days", TARGET]].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any() or (np.diff(values[:, :3], axis=1) < 0).any():
        raise ValueError("Saved predictions require finite ordered nonnegative values")


def state_distance_keys(frame):
    return pd.Series([json.dumps([str(state), str(band)]) for state, band in zip(frame.customer_state, frame.distance_band)], index=frame.index)


def fit_support(ledger):
    fit = ledger.loc[ledger.inner_role.eq("fit") & ledger.paired_eligible].copy()
    if fit.empty:
        raise ValueError("Actual paired fit population required")
    references = []
    for name in NUMERIC:
        values = fit[name].to_numpy(dtype=float)
        if not np.isfinite(values).all():
            raise ValueError("Paired fit predictors must be observed")
        references.append({"feature": name, "fit_orders": len(fit), "fit_min": float(values.min()),
                           "fit_q01": float(np.quantile(values, .01)), "fit_median": float(np.median(values)),
                           "fit_q99": float(np.quantile(values, .99)), "fit_max": float(values.max())})
    reference = pd.DataFrame(references).set_index("feature")
    held = ledger.loc[ledger.inner_role.isin(["calibration", "validation"])].copy()
    result = held[["order_id", "inner_role", "purchase_month", "paired_eligible"]].copy()
    result["state_distance_group"] = state_distance_keys(held)
    fit["state_distance_group"] = state_distance_keys(fit)
    held["state_distance_group"] = state_distance_keys(held)
    for name in ["customer_state", "seller_states_json", "distance_band", "state_distance_group"]:
        counts = fit[name].value_counts()
        result[name+"_fit_orders"] = held[name].map(counts).fillna(0).astype(int)
    result["distance_context_available"] = held[experiment.DISTANCES].notna().all(axis=1)
    result["fit_support_group"] = "at_least_30_state_distance_fit_orders"
    result.loc[result.state_distance_group_fit_orders.lt(30), "fit_support_group"] = "1_to_29_state_distance_fit_orders"
    result.loc[result.state_distance_group_fit_orders.eq(0), "fit_support_group"] = "unseen_state_distance_group"
    result.loc[~result.distance_context_available, "fit_support_group"] = "distance_context_unavailable"
    result["numeric_outside_fit_range_count"] = 0
    result["missing_numeric_context_count"] = held[NUMERIC].isna().sum(axis=1)
    for name in NUMERIC:
        result["numeric_outside_fit_range_count"] += (held[name].lt(reference.at[name, "fit_min"])
                                                       | held[name].gt(reference.at[name, "fit_max"])).fillna(False).astype(int)
    shifts = []
    for (role, month), group in held.loc[held.paired_eligible].groupby(["inner_role", "purchase_month"], sort=True):
        for name in NUMERIC:
            values = group[name]
            ref = reference.loc[name]
            shifts.append({"role": role, "purchase_month": month, "feature": name, "orders": len(group),
                           **ref.to_dict(), "observed_median": float(values.median()), "observed_p90": float(values.quantile(.9)),
                           "outside_fit_range_fraction": float((values.lt(ref.fit_min) | values.gt(ref.fit_max)).mean()),
                           "outside_fit_q01_q99_fraction": float((values.lt(ref.fit_q01) | values.gt(ref.fit_q99)).mean())})
    return result.reset_index(drop=True), reference.reset_index(), pd.DataFrame(shifts)


def case_diagnostics(ledger, predicted, support):
    columns = [name for name in ["order_id", *NUMERIC, "seller_states_json", "order_delivered_carrier_date",
                                "order_delivered_customer_date", "event_time_cutoff_exclusive"] if name == "order_id" or name not in predicted]
    cases = predicted.merge(ledger[columns], on="order_id", how="left", validate="many_to_one")
    columns = [name for name in support if name == "order_id" or name not in cases]
    cases = cases.merge(support[columns], on="order_id", how="left", validate="many_to_one")
    cases["signed_error_days"] = cases[TARGET]-cases.calibrated_median_days
    cases["absolute_error_days"] = cases.signed_error_days.abs()
    cases["observed_duration_above_prediction"] = cases.signed_error_days.gt(0)
    cases["covered_by_interval"] = cases[TARGET].ge(cases.calibrated_lower_days) & cases[TARGET].le(cases.calibrated_upper_days)
    cases["handoff_month_key"] = cases.order_delivered_carrier_date.dt.strftime("%Y-%m")
    cases["audience"] = "analyst_only_not_blinded_review"
    cases = cases.sort_values(["model", "inner_role", "purchase_month", "absolute_error_days", "order_id"],
                              ascending=[True, True, True, False, True]).reset_index(drop=True)
    cases["absolute_error_rank_in_model_purchase_month"] = cases.groupby(["model", "inner_role", "purchase_month"]).cumcount()+1
    return cases


def error_summary(group):
    total = float(group.absolute_error_days.sum())
    largest = int(np.ceil(len(group)*.1))
    top = group.sort_values(["absolute_error_days", "order_id"], ascending=[False, True]).head(largest)
    late = group.calendar_late_vs_promise.dropna()
    return {**experiment.metric_row(group, "calibrated"),
            "actual_mean_days": float(group[TARGET].mean()), "actual_median_days": float(group[TARGET].median()),
            "actual_p90_days": float(group[TARGET].quantile(.9)), "predicted_mean_days": float(group.calibrated_median_days.mean()),
            "mean_signed_error_days": float(group.signed_error_days.mean()), "absolute_error_p90_days": float(group.absolute_error_days.quantile(.9)),
            "underprediction_fraction": float(group.observed_duration_above_prediction.mean()),
            "top_error_orders": largest, "top_10pct_share_of_absolute_error": float(top.absolute_error_days.sum()/total) if total else np.nan,
            "long_warnings": int(group.long_warning.sum()), "short_warnings": int(group.short_warning.sum()),
            "promise_lateness_observed_orders": len(late), "calendar_late_fraction": float(late.mean()) if len(late) else np.nan,
            "unseen_state_distance_orders": int(group.state_distance_group_fit_orders.eq(0).sum()),
            "numeric_outside_fit_range_orders": int(group.numeric_outside_fit_range_count.gt(0).sum())}


def summarize_errors(cases):
    monthly, slices = [], []
    for (population, model, role, month), group in cases.groupby(["population", "model", "inner_role", "purchase_month"], sort=True):
        monthly.append({"population": population, "model": model, "role": role, "purchase_month": month,
                        "status": "held_out_validation_diagnostic" if role == "validation" else "in_sample_calibration_diagnostic",
                        **error_summary(group)})
    validation = cases.loc[cases.inner_role.eq("validation")]
    for (population, model, month), frame in validation.groupby(["population", "model", "purchase_month"], sort=True):
        for dimension in ["distance_band", "customer_state", "seller_count", "fit_support_group", "handoff_month_key"]:
            for key, group in frame.groupby(dimension, sort=True):
                slices.append({"population": population, "model": model, "purchase_month": month, "dimension": dimension,
                               "group": str(key), "reporting_support_at_least_30": len(group) >= 30, **error_summary(group)})
    return pd.DataFrame(monthly), pd.DataFrame(slices)


def geographic_effects(cases):
    rows = []
    validation = cases.loc[cases.population.eq(PAIRED) & cases.inner_role.eq("validation")]
    for depth in [2, 3]:
        for leaf in [30, 60]:
            left_name, right_name = f"gbr_no_distance_d{depth}_l{leaf}", f"gbr_geographic_d{depth}_l{leaf}"
            left = validation.loc[validation.model.eq(left_name)].set_index("order_id").sort_index()
            right = validation.loc[validation.model.eq(right_name)].set_index("order_id").sort_index()
            if not left.index.equals(right.index):
                raise ValueError("Paired feature-effect order IDs differ")
            paired = left[["purchase_month", "distance_band", "absolute_error_days"]].rename(columns={"absolute_error_days": "without_geography"})
            paired["with_geography"] = right.absolute_error_days
            groups = [("all_validation", "all", paired)]
            groups += [(dimension, str(key), group) for dimension in ["purchase_month", "distance_band"]
                       for key, group in paired.groupby(dimension, sort=True)]
            for dimension, key, group in groups:
                delta = group.with_geography-group.without_geography
                rows.append({"depth": depth, "leaf": leaf, "dimension": dimension, "group": key, "orders": len(group),
                             "no_distance_mae_days": float(group.without_geography.mean()),
                             "geographic_mae_days": float(group.with_geography.mean()),
                             "geographic_minus_no_distance_mae_days": float(delta.mean()),
                             "orders_with_lower_absolute_error": int(delta.lt(0).sum()),
                             "orders_with_higher_absolute_error": int(delta.gt(0).sum()), "orders_with_equal_absolute_error": int(delta.eq(0).sum())})
    return pd.DataFrame(rows)


def decompose(cases):
    details, summaries = [], []
    paired = cases.loc[cases.population.eq(PAIRED) & cases.inner_role.eq("validation")].copy()
    paired["period"] = np.where(paired.purchase_month.eq("2018-03"), "A", "B")
    if not paired.purchase_month.isin(["2018-03", "2018-04", "2018-05"]).all():
        raise ValueError("Diagnostic periods differ; define an explicit new version")
    for model, model_frame in paired.groupby("model", sort=True):
        for dimension in protocol()["composition_dimensions"]:
            counts = model_frame.groupby([dimension, "period"]).size().unstack(fill_value=0).reindex(columns=["A", "B"], fill_value=0)
            common = counts.index[(counts >= protocol()["minimum_common_group_orders_per_period"]).all(axis=1)]
            frame = model_frame.loc[model_frame[dimension].isin(common)]
            totals = model_frame.period.value_counts()
            kept = frame.period.value_counts()
            for quantity in protocol()["composition_quantities"]:
                output = {"model": model, "dimension": dimension, "quantity": quantity, "common_groups": len(common),
                          "period_A_total_orders": int(totals.get("A", 0)), "period_B_total_orders": int(totals.get("B", 0)),
                          "period_A_kept_orders": int(kept.get("A", 0)), "period_B_kept_orders": int(kept.get("B", 0))}
                for period in ["A", "B"]:
                    output[f"period_{period}_retained_fraction"] = output[f"period_{period}_kept_orders"]/output[f"period_{period}_total_orders"]
                local = []
                for key, group in frame.groupby(dimension, sort=True):
                    a, b = group.loc[group.period.eq("A"), quantity], group.loc[group.period.eq("B"), quantity]
                    pa, pb = len(a)/kept["A"], len(b)/kept["B"]
                    ma, mb = float(a.mean()), float(b.mean())
                    local.append({"model": model, "dimension": dimension, "quantity": quantity, "group": str(key),
                                  "period_A_orders": len(a), "period_B_orders": len(b), "weight_A": pa, "weight_B": pb,
                                  "mean_A": ma, "mean_B": mb, "composition_contribution": (pa-pb)*(ma+mb)/2,
                                  "within_group_contribution": (ma-mb)*(pa+pb)/2})
                if local:
                    means = frame.groupby("period")[quantity].mean()
                    gap = float(means["A"]-means["B"])
                    composition = sum(row["composition_contribution"] for row in local)
                    within = sum(row["within_group_contribution"] for row in local)
                    if not np.isclose(gap, composition+within, atol=1e-10):
                        raise ValueError("Composition identity does not reconcile")
                    output.update(mean_A=float(means["A"]), mean_B=float(means["B"]), gap=gap,
                                  composition_component=composition, within_group_component=within,
                                  identity_residual=gap-composition-within, status="descriptive_common_support_only")
                else:
                    output.update(mean_A=np.nan, mean_B=np.nan, gap=np.nan, composition_component=np.nan,
                                  within_group_component=np.nan, identity_residual=np.nan, status="insufficient_common_support")
                details.extend(local)
                summaries.append(output)
    return pd.DataFrame(details), pd.DataFrame(summaries)


def observation_audit(ledger):
    frame = ledger.loc[ledger.split.eq("validation")].copy()
    purchase, cutoff = frame.order_purchase_timestamp, frame.event_time_cutoff_exclusive
    carrier = frame.order_delivered_carrier_date.where(frame.order_delivered_carrier_date.lt(cutoff))
    delivered = frame.order_delivered_customer_date.where(frame.order_delivered_customer_date.lt(cutoff))
    invalid = carrier.notna() & (carrier.lt(purchase) | (delivered.notna() & delivered.lt(carrier)))
    available = carrier.notna() & ~invalid
    followup = (cutoff-carrier).dt.total_seconds()/86400
    duration = (delivered-carrier).dt.total_seconds()/86400
    coverage, horizons, summaries = [], [], []
    frame["carrier_visible_before_cutoff"] = carrier.notna()
    frame["valid_stage_observation"] = available
    frame["stage_followup_days_to_cutoff"] = followup.where(available)
    for month, group in frame.groupby("purchase_month", sort=True):
        coverage.append({"purchase_month": month, "all_orders": len(group),
                         "paired_scored_orders": int(group.evaluation_route.eq("paired_comparison").sum()),
                         "fallback_scored_orders": int(group.evaluation_route.isin(["no_distance_fallback", "global_fallback_only"]).sum()),
                         "timing_ineligible_orders": int((~group.inner_role.eq("validation")).sum()),
                         "completion_not_before_cutoff_orders": int(group.timing_input_reason.eq("completion_not_before_partition_cutoff").sum()),
                         "geographic_quality_withheld_orders": int((~group.all_pairs_quality_eligible).sum()),
                         "carrier_visible_before_cutoff_orders": int(group.carrier_visible_before_cutoff.sum()),
                         "valid_stage_observation_orders": int(group.valid_stage_observation.sum()),
                         "available_followup_median_days": float(group.stage_followup_days_to_cutoff.median()),
                         "paired_scored_fraction_of_all_orders": float(group.evaluation_route.eq("paired_comparison").mean())})
    for horizon in HORIZONS:
        endpoint = carrier+pd.Timedelta(days=horizon)
        mature = available & endpoint.lt(cutoff)
        within = mature & delivered.notna() & duration.ge(0) & duration.le(horizon)
        after = mature & delivered.notna() & duration.gt(horizon)
        not_recorded = mature & delivered.isna()
        state = pd.Series("insufficient_followup", index=frame.index)
        state.loc[carrier.isna()] = "carrier_not_recorded_before_cutoff"
        state.loc[invalid] = "invalid_visible_stage_chronology"
        state.loc[within] = "delivery_recorded_within_horizon"
        state.loc[after] = "delivery_recorded_after_horizon_before_cutoff"
        state.loc[not_recorded] = "no_delivery_recorded_before_cutoff"
        rows = frame[["order_id", "purchase_month", "event_time_cutoff_exclusive", "evaluation_route", "timing_input_reason"]].copy()
        rows["horizon_days"] = horizon
        rows["visible_carrier_timestamp"] = carrier
        rows["visible_delivery_timestamp"] = delivered
        rows["available_followup_days"] = followup.where(available)
        rows["full_followup_window"] = mature
        rows["followup_state"] = state
        rows["delivery_recorded_within_horizon"] = within.astype("boolean").where(mature)
        rows["observed_duration_days"] = duration.where(available & delivered.notna())
        horizons.append(rows.reset_index(drop=True))
        for month, group in rows.groupby("purchase_month", sort=True):
            known = group.delivery_recorded_within_horizon.dropna()
            summaries.append({"purchase_month": month, "horizon_days": horizon, "all_orders": len(group),
                              "mature_stage_orders": len(known), "delivery_recorded_within_horizon_orders": int(known.sum()),
                              "delivery_recorded_within_horizon_fraction": float(known.mean()) if len(known) else np.nan,
                              "recorded_after_horizon_before_cutoff_orders": int(group.followup_state.eq("delivery_recorded_after_horizon_before_cutoff").sum()),
                              "no_delivery_recorded_before_cutoff_orders": int(group.followup_state.eq("no_delivery_recorded_before_cutoff").sum()),
                              "insufficient_followup_orders": int(group.followup_state.eq("insufficient_followup").sum()),
                              "carrier_not_recorded_before_cutoff_orders": int(group.followup_state.eq("carrier_not_recorded_before_cutoff").sum()),
                              "invalid_visible_stage_chronology_orders": int(group.followup_state.eq("invalid_visible_stage_chronology").sum()),
                              "mature_but_original_timing_ineligible_orders": int((group.full_followup_window & group.evaluation_route.eq("timing_unavailable_or_deferred")).sum()),
                              "reporting_support_at_least_30": len(known) >= 30})
    reasons = frame.groupby(["purchase_month", "timing_input_reason"], sort=True).size().rename("orders").reset_index()
    return pd.DataFrame(coverage), reasons, pd.concat(horizons, ignore_index=True), pd.DataFrame(summaries)


def build_diagnostics(ledger, predicted):
    validate_inputs(ledger, predicted)
    support, references, shifts = fit_support(ledger)
    cases = case_diagnostics(ledger, predicted, support)
    monthly, slices = summarize_errors(cases)
    details, decomposition = decompose(cases)
    coverage, reasons, followup, followup_summary = observation_audit(ledger)
    frames = {"case_fit_support.csv": support, "fit_numeric_references.csv": references, "feature_shift_by_month.csv": shifts,
              "analyst_case_diagnostics.csv": cases, "monthly_model_diagnostics.csv": monthly,
              "validation_error_slices.csv": slices, "paired_geographic_effects.csv": geographic_effects(cases),
              "composition_details.csv": details, "composition_summary.csv": decomposition,
              "observation_coverage.csv": coverage, "timing_exclusion_reasons.csv": reasons,
              "followup_case_audit.csv": followup, "followup_summary.csv": followup_summary}
    focal = monthly.loc[monthly.model.eq(FOCAL) & monthly.role.eq("validation")]
    summary = {"development_orders_retained_in_parent": len(ledger), "validation_purchase_orders_audited": int(ledger.split.eq("validation").sum()),
               "paired_validation_orders": int((ledger.inner_role.eq("validation") & ledger.paired_eligible).sum()),
               "saved_prediction_rows_reconciled": len(predicted), "paired_candidates_retained": int(predicted.loc[predicted.population.eq(PAIRED), "model"].nunique()),
               "fit_support_reference_orders": int((ledger.inner_role.eq("fit") & ledger.paired_eligible).sum()),
               "focal_model": FOCAL, "focal_monthly_diagnostics": focal[["purchase_month", "orders", "actual_mean_days", "actual_median_days",
                                                                      "predicted_mean_days", "median_mae_days", "mean_signed_error_days", "coverage"]].to_dict("records"),
               "training_performed": False, "thresholds_changed": False, "model_bundle_deserialized": False,
               "test_scored": False, "human_labels_created": False, "human_review_started": False,
               "anomaly_accuracy_computed": False, "causal_explanation_proven": False, "synthetic_data_used": False,
               "diagnostic_scope": "post_hoc_descriptive; completed_case_selection_and_unknown_arrival_times_remain"}
    return frames, summary


def render_readme(summary, frames):
    monthly = frames["monthly_model_diagnostics.csv"]
    focal = monthly.loc[monthly.model.eq(FOCAL) & monthly.role.eq("validation"),
                        ["purchase_month", "orders", "actual_mean_days", "predicted_mean_days", "median_mae_days", "coverage", "long_warnings", "short_warnings"]]
    composition = frames["composition_summary.csv"]
    composition = composition.loc[composition.model.eq(FOCAL) & composition.quantity.eq("absolute_error_days"),
                                   ["dimension", "period_A_kept_orders", "period_B_kept_orders", "gap", "composition_component", "within_group_component"]]
    coverage = frames["observation_coverage.csv"][["purchase_month", "all_orders", "paired_scored_orders", "fallback_scored_orders",
                                                   "timing_ineligible_orders", "completion_not_before_cutoff_orders"]]
    followup = frames["followup_summary.csv"][["purchase_month", "horizon_days", "mature_stage_orders",
                                               "delivery_recorded_within_horizon_fraction", "no_delivery_recorded_before_cutoff_orders",
                                               "insufficient_followup_orders"]]
    source = focal.set_index("purchase_month")
    may = coverage.set_index("purchase_month").loc["2018-05"]
    findings = ("## Reading the Results\n"
                f"- March-purchase focal MAE: {source.at['2018-03', 'median_mae_days']:.3f} days; "
                f"April: {source.at['2018-04', 'median_mae_days']:.3f}; May: {source.at['2018-05', 'median_mae_days']:.3f}.\n"
                f"- March observed mean duration: {source.at['2018-03', 'actual_mean_days']:.3f} days; "
                f"mean predicted median: {source.at['2018-03', 'predicted_mean_days']:.3f} days.\n"
                f"- {int(may.completion_not_before_cutoff_orders)} of {int(may.all_orders)} May purchases fail the existing "
                "completion-before-cutoff requirement. Lower May MAE is conditional on the completed-case subset.\n"
                "- Common-support decompositions below describe observed group composition and within-group differences, not causes.\n"
                "- Blank fixed-horizon rates mean no mature denominator. They do not mean zero delivery problems.\n"
                "- Human anomaly accuracy, operational causes and a final model choice remain unverified.")
    return "\n\n".join(["# Actual Contextual Delivery Diagnostics", findings,
                         "## Focal Purchase Cohorts\n"+experiment.geo.business.audit.markdown_table(focal),
                         "## Observation Coverage\n"+experiment.geo.business.audit.markdown_table(coverage),
                         "## Error Gap on Common Support\n"+experiment.geo.business.audit.markdown_table(composition),
                         "## Fixed Follow-up Windows\n"+experiment.geo.business.audit.markdown_table(followup),
                         README.read_text(), "## Run Summary\n```json\n"+json.dumps(summary, indent=2)+"\n```"])+"\n"


def write_diagnostics(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite diagnostics: {output}")
    if set(frames) != TABLES:
        raise ValueError("Expected all diagnostic evidence tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contextual-diagnostics-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            restored = experiment.geo.read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(restored, frame, check_dtype=False, check_exact=True)
        summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            (staging/name).write_text(json.dumps(value, indent=2, allow_nan=False)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames))
        manifest = {"status": "complete", "version": VERSION, "identity": experiment.geo.business.IDENTITY,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
                    "runtime": {"numpy": np.__version__, "pandas": pd.__version__}, "summary": summary, "schemas": schemas,
                    "output_hashes": {p.name: experiment.geo.business.audit.file_hash(p) for p in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Diagnostic output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = experiment.geo.read_json(directory/"manifest.json")
    required = TABLES | {"summary.json", "protocol.json", "README.md"}
    if (manifest.get("status") != "complete" or manifest.get("version") != VERSION
            or manifest.get("identity") != experiment.geo.business.IDENTITY or set(manifest.get("schemas", {})) != TABLES
            or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected complete contextual diagnostics")
    experiment.geo.verify_files(directory, manifest["output_hashes"])
    if experiment.geo.read_json(directory/"protocol.json") != protocol() or experiment.geo.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Diagnostic summary/protocol binding differs")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent", type=Path, default=experiment.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying saved development predictions without refitting or labeling.", flush=True)
        ledger, predicted, provenance = load_inputs(args.parent)
        print("Auditing errors, common-support comparisons and observation windows.", flush=True)
        frames, summary = build_diagnostics(ledger, predicted)
        summary = write_diagnostics(frames, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Contextual diagnostics stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
