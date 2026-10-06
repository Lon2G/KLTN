"""Paired retrospective feature ablation on real, development-only Olist orders."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, RobustScaler
from sklearn.svm import OneClassSVM

import train_imported_process as training
import build_bed_bath_table_business_features as business


ROOT = training.ROOT
VERSION = "business_feature_ablation_v1"
BUNDLE_VERSION = "retrospective_business_banks_v1"
DEFAULT_PLAN = ROOT / "templates/process_training_v1/olist_bed_bath_table.json"
DEFAULT_PROTOCOL = ROOT / "templates/business_training_v1/protocol.json"
DEFAULT_BASELINE = ROOT / "models/datasets/olist_bed_bath_table_training_v1"
DEFAULT_OUTPUT = ROOT / "data/experiments/olist_business_ablation_v1"
BUSINESS_FEATURES = ["payment_value_sum", "payment_record_count", "payment_type_count", "payment_installments_max",
                     "payment_sequential_max", "item_count", "seller_count", "freight_to_item_price_ratio", "item_price_mean"]
PROFILES = {"timing_only": list(training.FEATURES), "business_only": BUSINESS_FEATURES,
            "combined": [*training.FEATURES, *BUSINESS_FEATURES]}
ROLES = ["fit", "calibration", "validation"]
ENSEMBLES = ["ensemble_rank_mean", "ensemble_rank_median"]
SCORES = [*training.engine.MODEL_NAMES, *ENSEMBLES]


def protocol():
    return {"version": VERSION, "mode": "retrospective_snapshot_paired_ablation", "profiles": PROFILES,
            "ml_grid": {name: training.engine.protocol()[name] for name in ["isolation_forest", "lof", "one_class_svm"]},
            "ensemble_members": training.engine.ENSEMBLE_ML, "ensembles": ENSEMBLES,
            "tail_fractions": training.engine.TAIL_FRACTIONS, "quantile_method": "linear",
            "threshold_comparison": "strict_greater_than", "missing_policy": "common_complete_nonnegative_finite_cases_no_imputation",
            "population": "same_fit_calibration_validation_orders_for_all_profiles",
            "zero_iqr_policy": "sklearn_RobustScaler_unit_scale_for_zero_or_near_zero_iqr; record_actual_scale",
            "threshold_selection": "fixed_grid_no_winner_without_independent_labels", "online_claim": False}


def code_hashes():
    return {**training.engine_hashes(), **business.code_hashes(),
            str(Path(__file__).relative_to(ROOT)): business.audit.file_hash(Path(__file__))}


def verify_files(directory, manifest):
    directory = Path(directory)
    for name, digest in manifest["output_hashes"].items():
        path = (directory/name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or business.audit.file_hash(path) != digest:
            raise ValueError(f"Artifact hash mismatch: {directory/name}")


def read_baseline(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"manifest.json").read_text(), object_pairs_hook=training.importer.unique_json_keys)
    required = {"candidates.csv", "validation_scores.csv", "validation_flags.csv", "training_plan.json",
                *[f"{role}_timing_features.csv" for role in ROLES]}
    if (manifest.get("status") != "complete" or manifest.get("training_version") != training.VERSION
            or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected a complete preserved timing baseline")
    verify_files(directory, manifest)
    if manifest["identity"] != business.IDENTITY or manifest["engine_hashes"] != training.engine_hashes():
        raise ValueError("Timing baseline identity/engine differs")
    return manifest


def verify_sources(provenance):
    for field in ["features", "baseline"]:
        directory = Path(provenance[f"{field}_directory"])
        if business.audit.file_hash(directory/"manifest.json") != provenance[f"{field}_manifest_sha256"]:
            raise ValueError(f"{field} manifest changed during training")
        verify_files(directory, {"output_hashes": provenance[f"{field}_output_hashes"]})
    business.verify_sources(provenance["feature_sources"])
    training.verify_sources(provenance["training_sources"])
    if business.audit.file_hash(Path(provenance["protocol_path"])) != provenance["protocol_sha256"]:
        raise ValueError("Ablation protocol changed during training")
    if provenance["code_hashes"] != code_hashes():
        raise ValueError("Ablation code changed during execution")


def ordered(frame):
    return frame.sort_values("order_id").reset_index(drop=True)


def build_design(features, timing_design):
    context = ordered(timing_design[1])
    features = ordered(features)
    business.require_key(features, ["order_id"], "ablation features")
    if features.order_id.tolist() != context.order_id.tolist():
        raise ValueError("Business and timing development IDs differ")
    keys = ["order_id", "dataset_id", "marketplace_id", "product_category_name", "split",
            "order_purchase_timestamp", "event_time_cutoff_exclusive", "timing_input_eligible"]
    try:
        pd.testing.assert_frame_equal(features[keys], context[keys], check_dtype=False, check_exact=True)
        expected = context[training.FEATURES].where(context.timing_input_eligible, axis=0)
        pd.testing.assert_frame_equal(features[training.FEATURES], expected, check_dtype=False, rtol=1e-12, atol=1e-12)
    except AssertionError as exc:
        raise ValueError("Business/timing facts or availability differ") from exc
    values = features[BUSINESS_FEATURES].to_numpy(dtype=float, na_value=np.nan)
    valid = np.isfinite(values) & (values >= 0)
    ledger = context[["order_id", "split", "order_purchase_timestamp", "timing_input_eligible", "timing_input_reason", "inner_role"]].copy()
    ledger["business_features_eligible"] = valid.all(axis=1)
    ledger["unavailable_business_columns_json"] = [json.dumps([name for name, available in zip(BUSINESS_FEATURES, row) if not available]) for row in valid]
    ledger["analysis_role"] = ledger.inner_role
    ledger.loc[ledger.inner_role.isin(ROLES) & ~ledger.business_features_eligible, "analysis_role"] = "business_features_unavailable"
    ledger["business_availability"] = "retrospective_snapshot_no_record_arrival_times"
    subsets = {role: features.loc[ledger.analysis_role.eq(role), ["order_id", *PROFILES["combined"]]].reset_index(drop=True) for role in ROLES}
    if len(subsets["fit"]) < 512 or len(subsets["calibration"]) < 200 or subsets["validation"].empty:
        raise ValueError("Insufficient common real cases: need 512 fit, 200 calibration and nonempty validation; no padding")
    return subsets, ledger


def load_inputs(features_dir=business.DEFAULT_OUTPUT, plan_path=DEFAULT_PLAN, protocol_path=DEFAULT_PROTOCOL, baseline=DEFAULT_BASELINE):
    features_dir, plan_path, protocol_path, baseline = map(Path, [features_dir, plan_path, protocol_path, baseline])
    before = {"features_manifest_sha256": business.audit.file_hash(features_dir/"manifest.json"),
              "baseline_manifest_sha256": business.audit.file_hash(baseline/"manifest.json"),
              "protocol_sha256": business.audit.file_hash(protocol_path)}
    declared = json.loads(protocol_path.read_text(), object_pairs_hook=training.importer.unique_json_keys)
    if declared != protocol():
        raise ValueError("Ablation protocol differs from the declared version; define a new version, not adaptive retuning")
    features, parent = business.load_feature_snapshot(features_dir)
    business.verify_sources(parent["provenance"])
    timing_design, plan, identity, timing_provenance = training.load_training_inputs(parent["provenance"]["import_directory"], plan_path)
    baseline_manifest = read_baseline(baseline)
    if (identity != business.IDENTITY or baseline_manifest["identity"] != identity
            or json.loads((baseline/"training_plan.json").read_text()) != plan):
        raise ValueError("Ablation and baseline training plan/identity must agree")
    subsets, ledger = build_design(features, timing_design)
    dictionary = business.usage.read_snapshot_table(features_dir, "feature_dictionary.csv", parent).set_index("column")
    if not dictionary.loc[PROFILES["combined"], "role"].eq("candidate_feature").all():
        raise ValueError("Audit-only fields cannot become predictors")
    provenance = {**before, "features_directory": str(features_dir.resolve()), "features_output_hashes": parent["output_hashes"],
                  "feature_sources": parent["provenance"], "training_sources": timing_provenance,
                  "baseline_directory": str(baseline.resolve()), "baseline_output_hashes": baseline_manifest["output_hashes"],
                  "protocol_path": str(protocol_path.resolve()), "code_hashes": code_hashes()}
    verify_sources(provenance)
    return subsets, ledger, plan, provenance


def validate_matrix(frame, columns):
    if frame.columns.tolist() != ["order_id", *columns]:
        raise ValueError("Exact ordered feature schema required; no IDs, labels or extra predictors")
    business.require_key(frame, ["order_id"], "model matrix")
    values = frame[columns].to_numpy(dtype=float, na_value=np.nan)
    if frame.empty or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Require nonempty finite nonnegative observed features; no imputation")
    return ordered(frame)


def fit_bank(frame, columns):
    frame = validate_matrix(frame, columns)
    if len(frame) < 512:
        raise ValueError("Need at least 512 real fit cases")
    bank = {"features": list(columns), "fit_ids": frame.order_id.tolist(), "models": {}}
    for transform in ["raw", "log1p"]:
        for samples in [256, 512]:
            prep = "passthrough" if transform == "raw" else FunctionTransformer(np.log1p, feature_names_out="one-to-one")
            model = Pipeline([("transform", prep), ("detector", IsolationForest(
                n_estimators=300, max_samples=samples, contamination="auto", random_state=42, n_jobs=1))])
            model.fit(frame[columns])
            bank["models"][f"if_{transform}_{samples}"] = model
    for neighbors in [20, 35, 50]:
        model = Pipeline([("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")), ("scale", RobustScaler()),
                          ("detector", LocalOutlierFactor(n_neighbors=neighbors, novelty=True, contamination="auto", n_jobs=1))])
        model.fit(frame[columns])
        bank["models"][f"lof_log1p_{neighbors}"] = model
    for nu in [3, 5, 10]:
        model = Pipeline([("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")), ("scale", RobustScaler()),
                          ("detector", OneClassSVM(nu=nu/100, gamma="scale", kernel="rbf", max_iter=100000))])
        model.fit(frame[columns])
        if model.named_steps["detector"].fit_status_ != 0:
            raise ValueError("One-Class SVM did not converge; no partial model study is published")
        bank["models"][f"ocsvm_log1p_nu{nu:02d}"] = model
    return bank


def base_scores(bank, frame):
    frame = validate_matrix(frame, bank["features"])
    if set(frame.order_id) & set(bank["fit_ids"]):
        raise ValueError("Novelty scoring on fit cases is forbidden")
    scores = frame[["order_id"]].copy()
    for name, model in bank["models"].items():
        scores[name] = -model.score_samples(frame[bank["features"]])
    if not np.isfinite(scores.drop(columns="order_id").to_numpy()).all():
        raise ValueError("Nonfinite model score")
    return scores


def add_ensembles(scores, references):
    result = scores.copy()
    ranks = np.column_stack([np.searchsorted(references[name], scores[name].to_numpy(), side="right")/len(references[name])
                             for name in training.engine.ENSEMBLE_ML])
    result["ensemble_rank_mean"] = ranks.mean(axis=1)
    result["ensemble_rank_median"] = np.median(ranks, axis=1)
    return result


def learn_candidates(calibration):
    rows = []
    for name in SCORES:
        family = "ensemble" if name in ENSEMBLES else "isolation_forest" if name.startswith("if_") else "lof" if name.startswith("lof_") else "one_class_svm"
        for fraction in training.engine.TAIL_FRACTIONS:
            rows.append({"candidate": f"{name}_tail{round(fraction*1000):03d}", "family": family, "score_column": name,
                         "threshold": float(np.quantile(calibration[name], 1-fraction, method="linear")),
                         "threshold_origin": "unseen_calibration_score_quantile", "tail_fraction": fraction})
    return pd.DataFrame(rows)


def score_bundle(bundle, profile, frame, identity):
    if bundle["bundle_version"] != BUNDLE_VERSION or bundle["protocol"] != protocol() or identity != bundle["identity"]:
        raise ValueError("Business model version/protocol/identity mismatch; never transfer fitted weights to another marketplace")
    if profile not in PROFILES or bundle["banks"][profile]["features"] != PROFILES[profile]:
        raise ValueError("Business model feature profile differs")
    bank = bundle["banks"][profile]
    scores = add_ensembles(base_scores(bank, frame), bank["rank_references"])
    return scores, training.engine.apply_thresholds(scores, bank["candidates"])


def fit_diagnostics(profile, frame, bank):
    rows = []
    scaler = bank["models"]["lof_log1p_35"].named_steps["scale"]
    for index, column in enumerate(bank["features"]):
        raw = frame[column].to_numpy(dtype=float)
        logged = np.log1p(raw)
        q25, median, q75 = np.quantile(logged, [.25, .5, .75], method="linear")
        rows.append({"profile": profile, "feature": column, "fit_unique_values": int(frame[column].nunique()),
                     "raw_min": float(raw.min()), "raw_max": float(raw.max()), "log1p_median": float(median),
                     "log1p_iqr": float(q75-q25), "actual_robust_center": float(scaler.center_[index]),
                     "actual_robust_scale": float(scaler.scale_[index]), "zero_iqr": bool(q75 == q25),
                     "scale_policy": "sklearn RobustScaler; zero/near-zero IQR uses unit scale, not imputation"})
    return pd.DataFrame(rows)


def compare_profiles(candidates, flags_by_profile, ledger):
    metrics, pairs, changes, monthly = [], [], [], []
    reference_ids = None
    definitions = None
    coverage = ledger.loc[ledger.split.eq("validation")].copy()
    coverage["purchase_month"] = coverage.order_purchase_timestamp.dt.to_period("M").astype("string")
    coverage["scored"] = coverage.analysis_role.eq("validation")
    month_coverage = coverage.groupby("purchase_month").agg(all_orders=("order_id", "size"), scored_orders=("scored", "sum")).reset_index()
    month_coverage["unscored_orders"] = month_coverage.all_orders-month_coverage.scored_orders
    aligned = {}
    for profile, flags in flags_by_profile.items():
        flags = ordered(flags)
        current = candidates[profile]
        business.require_key(flags, ["order_id"], "profile flags")
        if flags.empty or flags.columns.tolist() != ["order_id", *current.candidate] or not flags[current.candidate].isin([True, False]).all().all():
            raise ValueError("Complete boolean candidate flags required")
        shared = current.drop(columns="threshold")
        if reference_ids is None:
            reference_ids, definitions = flags.order_id.tolist(), shared
        elif flags.order_id.tolist() != reference_ids:
            raise ValueError("Profile validation IDs differ")
        else:
            pd.testing.assert_frame_equal(shared, definitions)
        aligned[profile] = flags
        if set(flags.order_id) != set(coverage.loc[coverage.scored, "order_id"]):
            raise ValueError("Flagged population differs from validation coverage ledger")
        by_month = flags.merge(coverage[["order_id", "purchase_month"]], on="order_id", validate="one_to_one")
        for row in current.itertuples(index=False):
            metrics.append({"profile": profile, **row._asdict(), "validation_scored_orders": len(flags),
                            "flagged_orders": int(flags[row.candidate].sum()), "flag_fraction_of_scored": float(flags[row.candidate].mean())})
            for month in month_coverage.itertuples(index=False):
                selected = by_month.loc[by_month.purchase_month.eq(month.purchase_month), row.candidate]
                monthly.append({"profile": profile, "candidate": row.candidate, "purchase_month": month.purchase_month,
                                "scored_orders": len(selected), "flagged_orders": int(selected.sum()),
                                "flag_fraction_of_scored": float(selected.mean()) if len(selected) else np.nan})
    for left, right in combinations(aligned, 2):
        names = candidates[left].candidate.tolist()
        lvalues, rvalues = (aligned[name][names].to_numpy(dtype=bool) for name in [left, right])
        different = lvalues != rvalues
        for index, name in enumerate(names):
            lv, rv = lvalues[:, index], rvalues[:, index]
            union, both = int((lv | rv).sum()), int((lv & rv).sum())
            pairs.append({"left_profile": left, "right_profile": right, "candidate": name, "scored_orders": len(lv),
                          "both_flag": both, "left_only": int((lv & ~rv).sum()), "right_only": int((rv & ~lv).sum()),
                          "neither_flag": int((~lv & ~rv).sum()), "union_flags": union,
                          "jaccard_overlap": both/union if union else np.nan,
                          "changed_decisions": int(different[:, index].sum()), "changed_fraction": float(different[:, index].mean())})
        for index in np.flatnonzero(different.any(axis=1)):
            changes.append({"order_id": reference_ids[index], "left_profile": left, "right_profile": right,
                            "changed_candidates": int(different[index].sum()),
                            "changed_candidate_names_json": json.dumps([name for name, changed in zip(names, different[index]) if changed])})
    return {"validation_candidate_metrics.csv": pd.DataFrame(metrics), "profile_pairwise_agreement.csv": pd.DataFrame(pairs),
            "validation_decision_changes.csv": pd.DataFrame(changes, columns=["order_id", "left_profile", "right_profile", "changed_candidates", "changed_candidate_names_json"]),
            "monthly_coverage.csv": month_coverage, "validation_monthly_flags.csv": pd.DataFrame(monthly)}


def check_preserved_baseline(frames, subsets, baseline):
    baseline = Path(baseline)
    rows = []
    for role in ROLES:
        original = pd.read_csv(baseline/f"{role}_timing_features.csv", float_precision="round_trip")
        current = subsets[role][["order_id", *training.FEATURES]]
        if original.order_id.tolist() != current.order_id.tolist():
            return pd.DataFrame([{"check": "population", "status": "not_comparable", "detail": f"Different common {role} population; not a baseline reproduction claim"}])
        pd.testing.assert_frame_equal(original, current, check_dtype=False, rtol=1e-12, atol=1e-12)
        rows.append({"check": f"{role}_features", "status": "matched", "detail": f"{len(current)} identical real case IDs and timing inputs"})
    current_candidates = frames["timing_only_candidates.csv"]
    names = current_candidates.candidate.tolist()
    original = pd.read_csv(baseline/"candidates.csv", float_precision="round_trip")
    original = original.loc[original.candidate.isin(names)].reset_index(drop=True)
    pd.testing.assert_frame_equal(current_candidates, original, check_dtype=False, rtol=1e-12, atol=1e-12)
    for kind, columns in [("scores", SCORES), ("flags", names)]:
        original = pd.read_csv(baseline/f"validation_{kind}.csv", float_precision="round_trip")[["order_id", *columns]]
        pd.testing.assert_frame_equal(frames[f"timing_only_validation_{kind}.csv"], original, check_dtype=False, rtol=1e-12, atol=1e-12)
        rows.append({"check": f"validation_{kind}", "status": "matched", "detail": "Fresh timing refit reproduces the overlapping preserved ML/rank baseline; no old weights loaded"})
    return pd.DataFrame(rows)


def train_study(subsets, ledger, plan, provenance, progress=None):
    bundle = {"bundle_version": BUNDLE_VERSION, "identity": dict(business.IDENTITY), "protocol": protocol(), "plan": plan,
              "provenance": provenance, "runtime": training.runtime(), "code_hashes": code_hashes(), "banks": {},
              "case_ids": {role: subsets[role].order_id.tolist() for role in ROLES}}
    frames = {"scope_ledger.csv": ledger}
    candidates, flags_by_profile, diagnostics, calibration_metrics = {}, {}, [], []
    for profile, columns in PROFILES.items():
        if progress:
            progress(f"Fitting {profile}: {len(columns)} features; {len(subsets['fit'])} fit / {len(subsets['calibration'])} calibration / {len(subsets['validation'])} validation orders.")
        inputs = {role: subsets[role][["order_id", *columns]].copy() for role in ROLES}
        bank = fit_bank(inputs["fit"], columns)
        base = base_scores(bank, inputs["calibration"])
        bank["rank_references"] = {name: np.sort(base[name].to_numpy()) for name in training.engine.ENSEMBLE_ML}
        calibration = add_ensembles(base, bank["rank_references"])
        bank["candidates"] = learn_candidates(calibration)
        bundle["banks"][profile] = bank
        validation, flags = score_bundle(bundle, profile, inputs["validation"], bundle["identity"])
        cal_flags = training.engine.apply_thresholds(calibration, bank["candidates"])
        candidates[profile], flags_by_profile[profile] = bank["candidates"], flags
        diagnostics.append(fit_diagnostics(profile, inputs["fit"], bank))
        for row in bank["candidates"].itertuples(index=False):
            calibration_metrics.append({"profile": profile, "candidate": row.candidate, "calibration_orders": len(calibration),
                                        "calibration_flags": int(cal_flags[row.candidate].sum()),
                                        "calibration_equal_threshold": int(calibration[row.score_column].eq(row.threshold).sum())})
        frames.update({f"{profile}_{role}_features.csv": frame for role, frame in inputs.items()})
        frames.update({f"{profile}_candidates.csv": bank["candidates"], f"{profile}_calibration_scores.csv": calibration,
                       f"{profile}_calibration_flags.csv": cal_flags, f"{profile}_validation_scores.csv": validation,
                       f"{profile}_validation_flags.csv": flags})
    frames["fit_preprocessing.csv"] = pd.concat(diagnostics, ignore_index=True)
    frames["calibration_diagnostics.csv"] = pd.DataFrame(calibration_metrics)
    frames.update(compare_profiles(candidates, flags_by_profile, ledger))
    frames["preserved_baseline_checks.csv"] = check_preserved_baseline(frames, subsets, provenance["baseline_directory"])
    summary = {"study_type": protocol()["mode"], "identity": business.IDENTITY, "feature_counts": {name: len(columns) for name, columns in PROFILES.items()},
               "development_orders": len(ledger), "common_orders": {role: len(subsets[role]) for role in ROLES},
               "analysis_role_counts": ledger.analysis_role.value_counts().to_dict(),
               "validation_all_orders": int(ledger.split.eq("validation").sum()),
               "validation_unscored_orders": int((ledger.split.eq("validation") & ~ledger.analysis_role.eq("validation")).sum()),
               "reserved_test_orders_excluded": provenance["feature_sources"]["reserved_test_orders_excluded"],
               "fresh_ml_fits": sum(len(bank["models"]) for bank in bundle["banks"].values()),
               "score_methods_per_profile": len(SCORES), "candidate_thresholds_per_profile": len(candidates["timing_only"]),
               "profile_candidate_results": len(frames["validation_candidate_metrics.csv"]),
               "paired_candidate_comparisons": len(frames["profile_pairwise_agreement.csv"]),
               "preserved_timing_baseline_reproduced": bool(frames["preserved_baseline_checks.csv"].status.eq("matched").all()),
               "training_performed": True, "previous_fitted_weights_loaded": False, "test_scored": False,
               "human_labels_created": False, "fraud_labels_created": False, "accuracy_computed": False,
               "anomaly_probabilities_computed": False, "imputation_performed": False, "online_validated": False,
               "selected_profile": None, "selected_candidate": None,
               "interpretation": "Warning workload and paired decision changes only; not accuracy, improvement, independent new-data evaluation or anomaly prevalence."}
    return frames, bundle, summary


def advisor_questions():
    return """# Questions for advisor confirmation

Implementation may continue with real-data fitting and descriptive diagnostics.
Do not claim accuracy, fraud, online validation or a winning model until the
corresponding evaluation requirements are resolved.

1. Independent reference: for transaction/payment/order warnings, should review
   labels describe an unusual observation, an operational problem, or both as
   separate fields? Please confirm group-specific decision criteria, handling
   of Suspicious/Uncertain, reviewer(s) and disagreement adjudication. Olist
   does not supply verified fraud/anomaly labels. Detector flags must not be
   copied into ground truth; order review ratings are not anomaly truth either.
2. Review design: may we establish a new blinded, stratified reference sample
   containing flagged and unflagged real validation orders, with inclusion
   probabilities recorded? Please agree sample size and independent reviewer
   capacity. The previous mostly-positive reviewed set cannot establish overall
   accuracy or false-positive rate for these new business targets.
3. Real-time claim: may the manuscript distinguish retrospective business
   snapshot analysis from a separate event-time replay of observed process
   milestones? Payment/item record arrival times and cancellation times are not
   supplied, so this run cannot prove online business-feature availability.

Current assumptions are implementation choices, not advisor-approved criteria.
Keep the reserved final test closed until feature/method/threshold selection and
the independent evaluation protocol are frozen. No acceptance or publication
outcome is inferred from this experiment.
"""


def render_readme(summary, frames):
    return "\n\n".join([
        "# Paired retrospective business-feature ablation",
        "## Actual run\n```json\n"+json.dumps(summary, indent=2)+"\n```",
        "## Design\nThree profiles use the SAME fit, calibration and validation order IDs. Timing-only has three process durations; "
        "business-only has nine declared nonnegative payment/order features; combined has all twelve. No other observed/audit column enters models. "
        "Counts describe source rows, not failed payment attempts. payment_type is represented only by its distinct-method count, not nominal method identity. "
        "Customer-history features, signed payment discrepancies, nominal payment-type encoding, seller behavior and geographic distances remain outside this first ablation. "
        "Correlated business predictors can overweight shared signals; feature-level selection/ablation is still future work.",
        "The preserved timing plan determines purchase/event cutoffs. Inner-fit events precede 2017-12-01; calibration purchases start on that date "
        "and complete before 2018-03-01; validation purchases occur from 2018-03-01 to 2018-06-01 with eligible completion before the latter cutoff. "
        "Earlier purchases completing after the fit cutoff are deferred, not reassigned. All development orders remain in scope_ledger.csv; "
        "unscored orders are not classified as normal. This common completed-case population underrepresents unfinished long cases and "
        "does not demonstrate the wider coverage possible for business-only scoring.",
        "## Temporal limitation\nThe BUSINESS inputs are final snapshot observations with unknown record-arrival times. A chronological purchase split "
        "does NOT make them proven point-in-time training or prediction data. This is a retrospective, conditional comparison, not an online backtest "
        "or evidence of real-time detection. Even the timing-only refit is compared on the restricted common cohort. The reserved test is neither parsed as a feature matrix nor scored.",
        "## Models and thresholds\nThe original finite ML grid is preserved: four IF models (raw/log1p, max_samples 256/512, 300 trees, seed 42), "
        "three LOF novelty models (log1p + fit-only RobustScaler, neighbors 20/35/50), three RBF One-Class SVM models (same preprocessing, nu .03/.05/.10, "
        "gamma=scale). Ten NEW models per profile are fitted on unlabeled data, not known-normal data. No prior fitted model is loaded. "
        "Score=-score_samples; higher is more unusual. Fit cases are never novelty-scored. LOF's novelty assumption and contamination of fit data remain limitations.",
        "Two ensembles use the fixed IF/log1p/256, LOF/35 and SVM/.05 members. Each member becomes its upper-inclusive empirical CDF rank against "
        "its own calibration scores; mean and median ranks are compared. Ranks and agreement are NOT calibrated probabilities, independent votes or proof of anomaly. "
        "For each of twelve score methods, threshold=linear_quantile(calibration_scores,1-tail_fraction), using tails [.005,.01,.02,.03,.05,.08,.10,.15]. "
        "Flag if score > threshold; ties are not broken to force percentages. Validation never fits transformations, rank references or thresholds. "
        "The result is 96 candidate thresholds per profile, not 96 separately trained models. No automatic winner is chosen.",
        "## Zero spread and missing values\nAll selected values must be observed, finite and nonnegative. No imputation, clipping or outlier removal occurs. "
        "For sparse count features, an IQR of zero does not necessarily mean a constant feature. RobustScaler's actual zero/near-zero IQR handling uses unit scale; "
        "fit_preprocessing.csv records medians, IQRs, unique counts and actual scales. This does not alter source observations. "
        "No new IQR/MAD score divides by zero, and no epsilon is inserted into a statistical formula. Previous timing statistical baselines are unchanged; "
        "this study compares only overlapping ML/rank methods, not all previous 120 candidates.",
        "## Interpretation\nvalidation_candidate_metrics.csv and validation_monthly_flags.csv report workload, not precision/recall. "
        "profile_pairwise_agreement.csv compares the same candidate on the same orders; Jaccard=both/either, undefined for an empty union. "
        "changed_fraction=disagreeing orders/common scored orders. validation_decision_changes.csv lists exact changed candidate names per real order/profile pair. "
        "Fewer, more, or more stable flags do not prove higher accuracy. The SAME validation data have been used in earlier experiments; this is not independent replication. "
        "preserved_baseline_checks.csv checks fresh timing refit reproducibility against verified CSV sidecars without loading old weights.",
        "## Reproducibility\nmodel_banks.joblib saves all thirty new fitted pipelines, feature contracts, training IDs, calibration references and thresholds. "
        "Only load trusted locally generated bundles; hashes do not authenticate pickle content. load_study() checks runtime, code and file hashes before deserialization. "
        "All CSV tables and both calibration/validation predictions are verified after save/reload before atomic publication. Raw data, old models, labels and prior outputs are unchanged. "
        "Use a new output path for every revision. No synthetic records, review labels or outcomes are generated.",
        "## Advisor questions\nSee advisor_questions.md. Independent target definitions, a blinded reviewed reference and the real-time claim need confirmation. "
        "Human review is not required to execute these diagnostics, but accuracy and a best-model claim cannot be manufactured from agreement among detectors. "
        "No real-time multi-agent implementation or paper acceptance is claimed by this experiment.",
    ])+"\n"


def write_study(frames, bundle, summary, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite business study: {output}")
    verify_sources(bundle["provenance"])
    if bundle["code_hashes"] != code_hashes():
        raise ValueError("Model code changed before saving")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        joblib.dump(bundle, staging/"model_banks.joblib", compress=3)
        restored = joblib.load(staging/"model_banks.joblib")
        reload_rows = []
        for profile in PROFILES:
            for role in ["calibration", "validation"]:
                scores, flags = score_bundle(restored, profile, frames[f"{profile}_{role}_features.csv"], bundle["identity"])
                pd.testing.assert_frame_equal(scores, frames[f"{profile}_{role}_scores.csv"], rtol=1e-12, atol=1e-12)
                pd.testing.assert_frame_equal(flags, frames[f"{profile}_{role}_flags.csv"], check_exact=True)
                reload_rows.append({"profile": profile, "partition": role, "scores_match": True, "flags_match_exactly": True, "refitting_performed": False})
        frames = {**frames, "reload_checks.csv": pd.DataFrame(reload_rows)}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        schemas = {name: {column: str(dtype) for column, dtype in frame.dtypes.items()} for name, frame in frames.items()}
        for name, expected in frames.items():
            actual = business.usage.read_snapshot_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(actual, expected, check_dtype=False, rtol=1e-12, atol=1e-12)
        summary = {**summary, "saved_model_roundtrip_verified": True, "serialized_tables_verified": len(frames)}
        for name, value in [("summary.json", summary), ("protocol.json", bundle["protocol"]), ("training_plan.json", bundle["plan"])]:
            (staging/name).write_text(json.dumps(value, indent=2)+"\n")
        (staging/"README.md").write_text(render_readme(summary, frames), encoding="utf-8")
        (staging/"advisor_questions.md").write_text(advisor_questions(), encoding="utf-8")
        manifest = {"status": "complete", "study_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "identity": bundle["identity"], "summary": summary, "provenance": bundle["provenance"],
                    "runtime": bundle["runtime"], "code_hashes": bundle["code_hashes"], "schemas": schemas,
                    "output_hashes": {path.name: business.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        load_study(staging)
        verify_sources(bundle["provenance"])
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_study(directory):
    directory = Path(directory)
    manifest = json.loads((directory/"manifest.json").read_text(), object_pairs_hook=training.importer.unique_json_keys)
    required = {"model_banks.joblib", "protocol.json", "training_plan.json", "summary.json", "scope_ledger.csv", "reload_checks.csv"}
    required.update(f"{profile}_candidates.csv" for profile in PROFILES)
    if (manifest.get("status") != "complete" or manifest.get("study_version") != VERSION
            or not required.issubset(manifest.get("output_hashes", {}))):
        raise ValueError("Expected a completed business-feature study")
    verify_files(directory, manifest)
    if manifest["code_hashes"] != code_hashes() or manifest["runtime"] != training.runtime():
        raise ValueError("Saved business model code/runtime differs; do not silently deserialize")
    bundle = joblib.load(directory/"model_banks.joblib")
    for key in ["identity", "provenance", "runtime", "code_hashes"]:
        if bundle[key] != manifest[key]:
            raise ValueError(f"Saved bundle/manifest binding differs: {key}")
    if bundle["bundle_version"] != BUNDLE_VERSION or bundle["identity"] != business.IDENTITY or bundle["protocol"] != protocol():
        raise ValueError("Saved business model contract differs")
    for filename, value in [("protocol.json", bundle["protocol"]), ("training_plan.json", bundle["plan"]), ("summary.json", manifest["summary"])]:
        if json.loads((directory/filename).read_text()) != value:
            raise ValueError(f"Saved contract differs: {filename}")
    training.validate_plan(bundle["plan"])
    ids = bundle["case_ids"]
    if set(ids) != set(ROLES) or any(len(set(values)) != len(values) or not values for values in ids.values()):
        raise ValueError("Invalid saved study case IDs")
    if any(set(ids[left]) & set(ids[right]) for left, right in combinations(ROLES, 2)):
        raise ValueError("Saved fit/calibration/validation populations overlap")
    if set(bundle["banks"]) != set(PROFILES):
        raise ValueError("Saved profiles differ")
    for profile, bank in bundle["banks"].items():
        if bank["fit_ids"] != ids["fit"] or bank["features"] != PROFILES[profile] or list(bank["models"]) != training.engine.MODEL_NAMES:
            raise ValueError("Saved feature/model/fit contract differs")
        candidates = business.usage.read_snapshot_table(directory, f"{profile}_candidates.csv", manifest)
        pd.testing.assert_frame_equal(candidates, bank["candidates"], check_dtype=False, rtol=1e-12, atol=1e-12)
    return bundle, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--features", type=Path, default=business.DEFAULT_OUTPUT)
    parser.add_argument("--training-config", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Study already exists: {args.output}\n")
    try:
        inputs = load_inputs(args.features, args.training_config, args.protocol, args.baseline)
        frames, bundle, summary = train_study(*inputs, progress=lambda value: print(value, flush=True))
        summary = write_study(frames, bundle, summary, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Business training stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
