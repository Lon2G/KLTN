"""Retrospective, paired delivery-duration experiment using real Olist data."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.metrics import mean_absolute_error, mean_pinball_loss
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

import assess_geographic_quality as quality
import build_geographic_features as geo
import train_imported_process as training


ROOT = geo.ROOT
VERSION = "contextual_delivery_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_contextual_delivery_v1"
PLAN = ROOT/"templates/process_training_v1/olist_bed_bath_table.json"
README = ROOT/"templates/contextual_delivery_v1/README.md"
TARGET = "carrier_to_delivered_days"
ROLES = ["fit", "calibration", "validation"]
NUMERIC = ["item_count", "seller_count", "item_price_sum", "freight_sum", "purchased_to_approved_days",
           "approved_to_carrier_days", "handoff_month", "handoff_weekday"]
DISTANCES = ["screened_distance_max_km", "screened_distance_item_weighted_mean_km"]
CATEGORICAL = ["customer_state", "seller_states_json"]
QUANTILES = [.05, .5, .95]
BANDS = ["0_to_50", "50_to_200", "200_to_500", "500_to_1000", "1000_to_2000", "2000_plus"]


def protocol():
    return {"version": VERSION, "quality_screen": quality.protocol(), "target": TARGET,
            "numeric_context": NUMERIC, "geographic_numeric": DISTANCES, "geographic_categorical": CATEGORICAL,
            "quantiles": QUANTILES, "gbr_grid": {"depth": [2, 3], "min_samples_leaf": [30, 60],
                                                  "trees": 150, "learning_rate": .05, "seed": 42},
            "peer_minimum_fit_orders": 50, "distance_band_edges": [0, 50, 200, 500, 1000, 2000],
            "paired_comparison": "identical_geography_and_context_eligible_fit_calibration_validation_cases",
            "fallback": "separate_all_context_fit_depth2_leaf30_and_all_timing_global",
            "calibration": "additive_Q05_lower_residual_and_Q95_upper_residual; constrain_around_median",
            "legacy": "fit_raw_Q01_and_Q75_plus_1.5IQR; uncalibrated; refit_on_paired_population",
            "minimum_summary_support": 30, "test_scored": False, "anomaly_accuracy_computed": False,
            "operational_winner_selected": False, "real_time_validated": False}


def code_hashes():
    return {**geo.code_hashes(), **geo.business.code_hashes(), **training.engine_hashes(),
            **{str(p.relative_to(ROOT)): geo.business.audit.file_hash(p)
               for p in [Path(__file__), Path(quality.__file__), Path(training.__file__), README]}}


def verify_sources(provenance):
    for prefix in ["geography", "business"]:
        path = Path(provenance[prefix+"_directory"])
        if geo.business.audit.file_hash(path/"manifest.json") != provenance[prefix+"_manifest_sha256"]:
            raise ValueError(f"Changed {prefix} manifest")
        geo.verify_files(path, provenance[prefix+"_output_hashes"])
    geo.verify_sources(provenance["geography_sources"])
    training.verify_sources(provenance["training_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Contextual experiment source code changed")


def load_inputs(geography=geo.DEFAULT_OUTPUT, plan_path=PLAN):
    geography = Path(geography).resolve()
    before = geo.business.audit.file_hash(geography/"manifest.json")
    geographic_features, manifest = geo.load_snapshot(geography)
    geo.verify_sources(manifest["provenance"])
    parent = Path(manifest["provenance"]["business_directory"])
    features, business_manifest = geo.business.load_feature_snapshot(parent)
    sources = business_manifest["provenance"]
    design, plan, identity, training_sources = training.load_training_inputs(sources["import_directory"], plan_path)
    if identity != geo.business.IDENTITY:
        raise ValueError("Marketplace/category identity differs; do not transfer a fitted model")
    context = geo.read_table(parent, "order_context.csv", business_manifest)
    links = geo.read_table(parent, "seller_order_links.csv", business_manifest)
    clean = Path(sources["clean_directory"])
    sellers = geo.business.read_selected(clean, "sellers", geo.read_json(clean/"manifest.json"), "seller_id", set(links.seller_id))
    states = links[["order_id", "seller_id"]].merge(context[["order_id", "customer_state"]], on="order_id", validate="many_to_one")
    states = states.merge(sellers[["seller_id", "seller_state"]], on="seller_id", validate="many_to_one", how="left")
    inputs = {"features": features, "context": context, "timing": design[1], "states": states,
              "geographic_features": geographic_features,
              **{key: geo.read_table(geography, name, manifest) for key, name in
                 [("zips", "zip_representatives.csv"), ("evidence", "coordinate_source_evidence.csv"),
                  ("pairs", "seller_customer_distances.csv")]}}
    provenance = {"geography_directory": str(geography), "geography_manifest_sha256": before,
                  "geography_output_hashes": manifest["output_hashes"], "geography_sources": manifest["provenance"],
                  "business_directory": str(parent), "business_manifest_sha256": geo.business.audit.file_hash(parent/"manifest.json"),
                  "business_output_hashes": business_manifest["output_hashes"], "training_sources": training_sources,
                  "code_hashes": code_hashes(), "reserved_test_orders_excluded": sources["reserved_test_orders_excluded"]}
    verify_sources(provenance)
    return inputs, plan, provenance


def build_design(inputs):
    timing = inputs["timing"].sort_values("order_id").reset_index(drop=True)
    features = inputs["features"].sort_values("order_id").reset_index(drop=True)
    geo.business.require_key(timing, ["order_id"], "duration design")
    if not timing.split.isin(["train", "validation"]).all() or timing.empty:
        raise ValueError("Only nonempty development partitions allowed")
    for name in ["features", "context", "geographic_features"]:
        if set(inputs[name].order_id) != set(timing.order_id):
            raise ValueError("Context/geographic/timing order scope differs")
    keys = ["order_id", "split", "order_purchase_timestamp", "event_time_cutoff_exclusive", "timing_input_eligible"]
    pd.testing.assert_frame_equal(timing[keys], features[keys], check_dtype=False)
    expected = timing[training.FEATURES].where(timing.timing_input_eligible, axis=0)
    pd.testing.assert_frame_equal(features[training.FEATURES], expected, check_dtype=False, rtol=1e-12, atol=1e-12)
    fit_ids = timing.loc[timing.inner_role.eq("fit"), "order_id"].tolist()
    frames = quality.assess(inputs["zips"], inputs["evidence"], inputs["pairs"], fit_ids, inputs["states"])
    columns = ["order_id", "split", "inner_role", "order_purchase_timestamp", "event_time_cutoff_exclusive",
               "timing_input_eligible", "timing_input_reason", *training.FEATURES,
               "order_delivered_carrier_date", "order_delivered_customer_date", "order_estimated_delivery_date"]
    ledger = timing[columns].merge(features[["order_id", "item_count", "seller_count", "item_price_sum", "freight_sum"]],
                                    on="order_id", how="left", validate="one_to_one")
    ledger = ledger.merge(inputs["context"][["order_id", "customer_state"]], on="order_id", how="left", validate="one_to_one")
    sellers = inputs["states"].groupby("order_id").agg(
        seller_states_json=("seller_state", lambda x: json.dumps(sorted(set(x)))),
        single_seller_id=("seller_id", lambda x: x.iloc[0] if len(x) == 1 else "multi_seller_not_attributed")).reset_index()
    ledger = ledger.merge(sellers, on="order_id", how="left", validate="one_to_one")
    ledger = ledger.merge(frames["order_quality.csv"], on="order_id", how="left", validate="one_to_one")
    ledger["handoff_month"] = ledger.order_delivered_carrier_date.dt.month.astype(float)
    ledger["handoff_weekday"] = ledger.order_delivered_carrier_date.dt.dayofweek.astype(float)
    values = ledger[NUMERIC].to_numpy(dtype=float, na_value=np.nan)
    ledger["numeric_context_eligible"] = (np.isfinite(values) & (values >= 0)).all(axis=1)
    ledger["paired_eligible"] = (ledger.numeric_context_eligible & ledger.all_pairs_quality_eligible
                                   & ledger[CATEGORICAL].notna().all(axis=1))
    ledger["evaluation_route"] = "timing_unavailable_or_deferred"
    valid = ledger.inner_role.isin(ROLES)
    ledger.loc[valid, "evaluation_route"] = "global_fallback_only"
    ledger.loc[valid & ledger.numeric_context_eligible, "evaluation_route"] = "no_distance_fallback"
    ledger.loc[valid & ledger.paired_eligible, "evaluation_route"] = "paired_comparison"
    ledger["purchase_month"] = ledger.order_purchase_timestamp.dt.strftime("%Y-%m")
    ledger["distance_band"] = pd.cut(ledger[DISTANCES[0]], [*protocol()["distance_band_edges"], np.inf],
                                      labels=BANDS, right=False).astype("string").fillna("unavailable")
    late_available = ledger.order_delivered_customer_date.notna() & ledger.order_estimated_delivery_date.notna()
    ledger["calendar_late_vs_promise"] = ledger.order_delivered_customer_date.dt.normalize().gt(
        ledger.order_estimated_delivery_date.dt.normalize()).astype("boolean").where(late_available)
    for role, minimum in [("fit", 512), ("calibration", 200), ("validation", 1)]:
        selected = ledger.loc[ledger.inner_role.eq(role) & ledger.paired_eligible]
        if len(selected) < minimum:
            raise ValueError(f"Insufficient actual paired {role} orders; require {minimum}, never pad")
    frames["analysis_ledger.csv"] = ledger
    return frames, ledger


def fit_entry(frame, kind, geographic=False, depth=2, leaf=30):
    geo.business.require_key(frame, ["order_id"], "fit observations")
    if frame.empty or not frame.inner_role.eq("fit").all():
        raise ValueError("Only declared fit observations may train an entry")
    y = frame[TARGET].to_numpy(dtype=float)
    if not np.isfinite(y).all() or (y < 0).any():
        raise ValueError("Require actual finite nonnegative delivery durations")
    entry = {"kind": kind, "geographic": geographic, "fit_ids": frame.order_id.tolist(),
             "fit_count": len(frame), "lower_shift": 0.0, "upper_shift": 0.0}
    if kind == "legacy":
        q25, median, q75 = np.quantile(y, [.25, .5, .75])
        entry["reference"] = [float(np.quantile(y, .01)),
                              float(median), float(q75+1.5*(q75-q25))]
    elif kind in ["global", "peer"]:
        entry["reference"] = np.quantile(y, QUANTILES).tolist()
        if kind == "peer":
            entry["groups"] = {}
            for columns in [["customer_state", "distance_band"], ["distance_band"]]:
                for key, group in frame.groupby(columns, sort=True, observed=True):
                    if len(group) >= protocol()["peer_minimum_fit_orders"]:
                        entry["groups"][(len(columns), *key)] = {"count": len(group), "values": np.quantile(group[TARGET], QUANTILES).tolist()}
    elif kind == "gbr":
        numeric, categorical = [*NUMERIC, *(DISTANCES if geographic else [])], CATEGORICAL if geographic else []
        entry.update(numeric=numeric, categorical=list(categorical), models=[], depth=depth, leaf=leaf)
        for q in QUANTILES:
            transforms = [("numeric", "passthrough", numeric)]
            if categorical:
                transforms.append(("categorical", OneHotEncoder(handle_unknown="ignore", sparse_output=False), categorical))
            model = Pipeline([("encode", ColumnTransformer(transforms, remainder="drop")),
                              ("regressor", GradientBoostingRegressor(loss="quantile", alpha=q, n_estimators=150,
                                                                      learning_rate=.05, max_depth=depth,
                                                                      min_samples_leaf=leaf, random_state=42))])
            model.fit(frame[numeric+list(categorical)], y)
            entry["models"].append(model)
    else:
        raise ValueError("Unknown duration-reference family")
    return entry


def predict_entry(entry, frame):
    if set(frame.order_id) & set(entry["fit_ids"]):
        raise ValueError("Held-out scoring cannot include fitted orders")
    n = len(frame)
    support = np.full(n, entry["fit_count"], dtype=int)
    route = np.full(n, "global", dtype=object)
    unknown = np.zeros(n, dtype=bool)
    if entry["kind"] == "gbr":
        cols = entry["numeric"]+entry["categorical"]
        raw = np.column_stack([model.predict(frame[cols]) for model in entry["models"]]) if n else np.empty((0, 3))
        route[:] = "gbr"
        if entry["categorical"]:
            encoder = entry["models"][0].named_steps["encode"].named_transformers_["categorical"]
            for column, categories in zip(entry["categorical"], encoder.categories_):
                unknown |= ~frame[column].isin(categories).to_numpy()
    else:
        raw = np.tile(entry["reference"], (n, 1))
        if entry["kind"] == "peer":
            for i, row in enumerate(frame.itertuples(index=False)):
                for key, label in [((2, row.customer_state, row.distance_band), "state_distance_band"),
                                    ((1, row.distance_band), "distance_band")]:
                    if key in entry["groups"]:
                        chosen = entry["groups"][key]
                        raw[i], support[i], route[i] = chosen["values"], chosen["count"], label
                        break
    crossed = (np.diff(raw, axis=1) < 0).any(axis=1)
    raw = np.maximum(np.sort(raw, axis=1), 0)
    calibrated = raw.copy()
    if entry["kind"] != "legacy":
        calibrated[:, 0] = np.maximum(0, np.minimum(raw[:, 0]+entry["lower_shift"], raw[:, 1]))
        calibrated[:, 2] = np.maximum(raw[:, 2]+entry["upper_shift"], raw[:, 1])
    return raw, calibrated, support, route, crossed, unknown


def calibrate_entry(entry, frame):
    if frame.empty or not frame.inner_role.eq("calibration").all():
        raise ValueError("Nonempty declared calibration cases required")
    raw = predict_entry(entry, frame)[0]
    y = frame[TARGET].to_numpy(dtype=float)
    if entry["kind"] != "legacy":
        entry["lower_shift"] = float(np.quantile(y-raw[:, 0], .05))
        entry["upper_shift"] = float(np.quantile(y-raw[:, 2], .95))
    entry["calibration_ids"] = frame.order_id.tolist()
    return entry


def predictions(entry, frame, model_name, population):
    raw, corrected, support, route, crossed, unknown = predict_entry(entry, frame)
    columns = ["order_id", "inner_role", "purchase_month", "customer_state", "single_seller_id", "seller_count",
               "distance_band", "calendar_late_vs_promise", TARGET]
    result = frame[columns].reset_index(drop=True).copy()
    result["model"] = model_name
    result["population"] = population
    for prefix, values in [("raw", raw), ("calibrated", corrected)]:
        for i, name in enumerate(["lower_days", "median_days", "upper_days"]):
            result[f"{prefix}_{name}"] = values[:, i]
    result["reference_fit_support"] = support
    result["reference_route"] = route
    result["raw_quantiles_crossed"] = crossed
    result["unseen_categorical_context"] = unknown
    result["long_warning"] = result[TARGET].gt(result.calibrated_upper_days)
    result["short_warning"] = result[TARGET].gt(0) & result[TARGET].lt(result.calibrated_lower_days)
    result["zero_duration_warning"] = result[TARGET].eq(0)
    result["excess_above_upper_days"] = (result[TARGET]-result.calibrated_upper_days).clip(lower=0)
    result["is_quantile_interval"] = entry["kind"] != "legacy"
    return result


def metric_row(frame, view):
    y = frame[TARGET].to_numpy(dtype=float)
    low, median, high = (frame[f"{view}_{name}_days"].to_numpy(dtype=float) for name in ["lower", "median", "upper"])
    nominal = bool(frame.is_quantile_interval.all())
    width = high-low
    score = width + 20*np.maximum(low-y, 0) + 20*np.maximum(y-high, 0)
    return {"orders": len(frame), "median_mae_days": mean_absolute_error(y, median),
            "q05_pinball": mean_pinball_loss(y, low, alpha=.05) if nominal else np.nan,
            "q50_pinball": mean_pinball_loss(y, median, alpha=.5),
            "q95_pinball": mean_pinball_loss(y, high, alpha=.95) if nominal else np.nan,
            "coverage": float(((y >= low) & (y <= high)).mean()), "mean_width_days": float(width.mean()),
            "below_lower_fraction": float((y < low).mean()), "above_upper_fraction": float((y > high).mean()),
            "interval_score_alpha010": float(score.mean()) if nominal else np.nan,
            "nominal_coverage": .9 if nominal else np.nan,
            "raw_crossed_orders": int(frame.raw_quantiles_crossed.sum()),
            "unseen_category_orders": int(frame.unseen_categorical_context.sum())}


def evaluate(predicted):
    rows, subgroups, warnings = [], [], []
    for (population, model, role), frame in predicted.groupby(["population", "model", "inner_role"], sort=True):
        for view in ["raw", "calibrated"]:
            rows.append({"population": population, "model": model, "role": role, "view": view,
                         "evaluation_status": "held_out_validation" if role == "validation" else "in_sample_calibration_diagnostic",
                         **metric_row(frame, view)})
        if role != "validation":
            continue
        for dimension in ["purchase_month", "distance_band", "customer_state", "seller_count"]:
            for key, group in frame.groupby(dimension, sort=True):
                subgroups.append({"population": population, "model": model, "dimension": dimension, "group": str(key),
                                  "sufficient_support": len(group) >= 30, **metric_row(group, "calibrated")})
        for dimension in ["purchase_month", "customer_state", "single_seller_id"]:
            scoped = frame.loc[frame.seller_count.eq(1)] if dimension == "single_seller_id" else frame
            for key, group in scoped.groupby(dimension, sort=True):
                warnings.append({"population": population, "model": model, "dimension": dimension, "group": str(key),
                                 "scored_orders": len(group), "long_warnings": int(group.long_warning.sum()),
                                 "long_warning_fraction": float(group.long_warning.mean()), "short_warnings": int(group.short_warning.sum()),
                                 "zero_warnings": int(group.zero_duration_warning.sum()),
                                 "mean_excess_days": float(group.excess_above_upper_days.mean()),
                                 "sufficient_support": len(group) >= 30,
                                 "multi_seller_excluded_from_seller_attribution": int(frame.seller_count.gt(1).sum()) if dimension == "single_seller_id" else 0,
                                 "interpretation": "investigation_signal_not_proven_bottleneck_or_cause"})
    return {"metrics.csv": pd.DataFrame(rows), "validation_subgroups.csv": pd.DataFrame(subgroups),
            "warning_summaries.csv": pd.DataFrame(warnings)}


def run_experiment(inputs, plan, progress=print):
    frames, ledger = build_design(inputs)
    counts = ledger.groupby(["inner_role", "evaluation_route"]).size().rename("orders").reset_index()
    frames["role_coverage.csv"] = counts
    bank, outputs, calibration_rows = {}, [], []
    paired = {role: ledger.loc[ledger.inner_role.eq(role) & ledger.paired_eligible].copy() for role in ROLES}
    progress("Paired populations: "+str({key: len(value) for key, value in paired.items()}))
    definitions = [("legacy_iqr", "legacy", False, 2, 30), ("global_quantile", "global", False, 2, 30),
                   ("peer_quantile", "peer", True, 2, 30)]
    definitions += [(f"gbr_{'geographic' if geographic else 'no_distance'}_d{depth}_l{leaf}", "gbr", geographic, depth, leaf)
                    for geographic in [False, True] for depth in [2, 3] for leaf in [30, 60]]
    for name, kind, geographic, depth, leaf in definitions:
        progress(f"Fitting paired reference: {name}")
        entry = calibrate_entry(fit_entry(paired["fit"], kind, geographic, depth, leaf), paired["calibration"])
        bank[name] = entry
        outputs += [predictions(entry, paired[role], name, "paired_geography_eligible") for role in ROLES[1:]]
    for name, kind in [("fallback_global_quantile", "global"), ("fallback_no_distance_d2_l30", "gbr")]:
        usable = pd.Series(True, index=ledger.index) if kind == "global" else ledger.numeric_context_eligible
        subsets = {role: ledger.loc[ledger.inner_role.eq(role) & usable].copy() for role in ROLES}
        progress(f"Fitting separate fallback: {name}")
        entry = calibrate_entry(fit_entry(subsets["fit"], kind), subsets["calibration"])
        bank[name] = entry
        for role in ROLES[1:]:
            outside = subsets[role].loc[~subsets[role].paired_eligible]
            if not outside.empty:
                outputs.append(predictions(entry, outside, name, "outside_paired_geography_population"))
    predicted = pd.concat(outputs, ignore_index=True)
    for name, entry in bank.items():
        calibration_rows.append({"model": name, "family": entry["kind"], "fit_orders": entry["fit_count"],
                                 "calibration_orders": len(entry["calibration_ids"]),
                                 "lower_shift_days": entry["lower_shift"], "upper_shift_days": entry["upper_shift"],
                                 "nominal_coverage": np.nan if entry["kind"] == "legacy" else .9})
    frames.update(evaluate(predicted))
    frames["predictions.csv"] = predicted
    frames["calibration_adjustments.csv"] = pd.DataFrame(calibration_rows)
    frames["monthly_coverage.csv"] = ledger.groupby(["split", "purchase_month", "evaluation_route"], sort=True).size().rename("orders").reset_index()
    bundle = {"version": VERSION, "identity": geo.business.IDENTITY, "protocol": protocol(), "plan": plan,
              "bank": bank, "validation_ids": ledger.loc[ledger.inner_role.eq("validation"), "order_id"].tolist()}
    summary = {"development_orders": len(ledger), "timing_role_counts": ledger.inner_role.value_counts().to_dict(),
               "quality_eligible_orders": int(ledger.all_pairs_quality_eligible.sum()),
               "quality_withheld_orders": int((~ledger.all_pairs_quality_eligible).sum()),
               "paired_role_counts": {role: len(frame) for role, frame in paired.items()},
               "validation_routes": ledger.loc[ledger.split.eq("validation"), "evaluation_route"].value_counts().to_dict(),
               "zip_quality_eligible": int(frames["zip_quality.csv"].zip_quality_eligible.sum()),
               "source_rows_outside_broad_envelope": len(frames["outside_envelope_evidence.csv"]),
               "paired_candidates": len(definitions), "separate_fallback_candidates": 2,
               "fitted_quantile_regressors": sum(len(entry.get("models", [])) for entry in bank.values()),
               "quality_screen_provisional": True, "geographic_locations_verified": False,
               "raw_data_changed": False, "synthetic_data_used": False, "test_scored": False,
               "human_labels_created": False, "anomaly_accuracy_computed": False, "operational_winner_selected": False,
               "real_time_validated": False, "causal_bottlenecks_proven": False}
    return frames, bundle, summary


def render_readme(summary, frames):
    metrics = frames["metrics.csv"]
    selected = metrics.loc[metrics.population.eq("paired_geography_eligible") & metrics.role.eq("validation") & metrics.view.eq("calibrated"),
                           ["model", "orders", "median_mae_days", "coverage", "mean_width_days", "interval_score_alpha010"]]
    return "\n\n".join(["# Actual Contextual Delivery Results", "```json\n"+json.dumps(summary, indent=2)+"\n```",
                         "## Paired Validation\n"+geo.business.audit.markdown_table(selected),
                         "## Fit-Only ZIP Screen Thresholds\n"+geo.business.audit.markdown_table(frames["quality_thresholds.csv"]),
                         "## Coverage\n"+geo.business.audit.markdown_table(frames["role_coverage.csv"]), README.read_text()])+"\n"


def write_experiment(frames, bundle, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite experiment: {output}")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".contextual-delivery-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            restored = geo.read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(restored, frame, check_dtype=False, check_exact=True)
        summary = {**summary, "reserved_test_orders_excluded": provenance["reserved_test_orders_excluded"]}
        joblib.dump(bundle, staging/"model_bundle.joblib")
        restored = joblib.load(staging/"model_bundle.joblib")
        ledger = frames["analysis_ledger.csv"]
        for name, entry in restored["bank"].items():
            actual = frames["predictions.csv"].query("model == @name and inner_role == 'validation'")
            inputs = ledger.set_index("order_id").loc[actual.order_id].reset_index()
            values = predict_entry(entry, inputs)[1]
            np.testing.assert_array_equal(values, actual[["calibrated_lower_days", "calibrated_median_days", "calibrated_upper_days"]].to_numpy())
        for name, value in [("summary.json", summary), ("protocol.json", protocol()), ("training_plan.json", bundle["plan"])]:
            (staging/name).write_text(json.dumps(value, indent=2)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames))
        manifest = {"status": "complete", "version": VERSION, "identity": geo.business.IDENTITY,
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
                    "runtime": {"numpy": np.__version__, "pandas": pd.__version__, "scikit_learn": sklearn.__version__},
                    "summary": summary, "schemas": schemas,
                    "output_hashes": {p.name: geo.business.audit.file_hash(p) for p in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Experiment output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = geo.read_json(directory/"manifest.json")
    required = {"analysis_ledger.csv", "predictions.csv", "metrics.csv", "quality_thresholds.csv", "model_bundle.joblib",
                "protocol.json", "training_plan.json", "README.md", "summary.json"}
    if (manifest.get("status") != "complete" or manifest.get("version") != VERSION
            or manifest.get("identity") != geo.business.IDENTITY or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected complete contextual delivery snapshot")
    geo.verify_files(directory, manifest["output_hashes"])
    if geo.read_json(directory/"protocol.json") != protocol() or geo.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Experiment summary/protocol binding differs")
    ledger = geo.read_table(directory, "analysis_ledger.csv", manifest)
    geo.business.require_key(ledger, ["order_id"], "saved contextual ledger")
    if len(ledger) != manifest["summary"]["development_orders"] or not ledger.split.isin(["train", "validation"]).all():
        raise ValueError("Saved development population differs")
    return ledger, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geography", type=Path, default=geo.DEFAULT_OUTPUT)
    parser.add_argument("--plan", type=Path, default=PLAN)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying real development snapshots; no test features or review labels.", flush=True)
        inputs, plan, provenance = load_inputs(args.geography, args.plan)
        frames, bundle, summary = run_experiment(inputs, plan, lambda x: print(x, flush=True))
        summary = write_experiment(frames, bundle, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError, KeyError) as exc:
        parser.exit(2, f"Contextual experiment stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
