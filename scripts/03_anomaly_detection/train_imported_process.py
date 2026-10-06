"""Fit and save a dataset-bound timing model bundle from a verified import snapshot."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import re
import sys
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import import_process_data as importer
import benchmark_bed_bath_table as engine
import auto_validate_bed_bath_table as automatic


VERSION = "process_training_v1"
BUNDLE_VERSION = "process_timing_bundle_v1"
FEATURES = engine.FEATURES
TIMES = importer.audit.TIMES
BOUNDARIES = ["purchase_start_inclusive", "fit_end_exclusive", "train_end_exclusive", "validation_end_exclusive"]
IDENTITY_FIELDS = ["dataset_id", "marketplace_id", "primary_category", "timestamp_basis"]
PLAN_KEYS = {"schema_version", "run_id", "dataset_id", "marketplace_id", "primary_category", "method_profile", *BOUNDARIES}


def validate_plan(plan):
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["schema_version"] != VERSION:
        raise ValueError("Expected the exact process_training_v1 plan schema")
    for key in ["run_id", "dataset_id", "marketplace_id"]:
        if not isinstance(plan[key], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", plan[key]):
            raise ValueError(f"Set an explicit valid {key}")
    if not isinstance(plan["primary_category"], str) or not plan["primary_category"] or plan["primary_category"].strip() != plan["primary_category"]:
        raise ValueError("Set an exact primary_category")
    if plan["method_profile"] != "multi_method_timing_v1":
        raise ValueError("Unsupported method_profile")
    values = []
    for key in BOUNDARIES:
        value = plan[key]
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
            raise ValueError(f"Set {key} as an explicit timezone-naive YYYY-MM-DD HH:MM:SS boundary")
        parsed = pd.Timestamp(value)
        if pd.isna(parsed) or parsed.tzinfo is not None or parsed.strftime("%Y-%m-%d %H:%M:%S") != value:
            raise ValueError(f"Invalid source-clock boundary: {key}")
        values.append(parsed)
    if any(left >= right for left, right in zip(values, values[1:])):
        raise ValueError("Require purchase start < fit end < train end < validation end")
    return dict(zip(BOUNDARIES, values))


def identity_from_config(config):
    identity = {name: config[name] for name in IDENTITY_FIELDS}
    if identity["timestamp_basis"] != "source_wall_clock_naive":
        raise ValueError("This profile requires the declared source wall-clock contract")
    return identity


def feature_contract():
    return {"process_profile": "purchased-approved-carrier-delivered-v1", "columns": FEATURES,
            "units": "days, calculated as total_seconds / 86400",
            "formulas": {name: f"({end} - {start}).total_seconds() / 86400" for name, start, end in zip(FEATURES, TIMES, TIMES[1:])},
            "missing": "No imputation; keep excluded cases in the ledger and process checks.",
            "zero": "Preserve observed zero; short/zero duration alone is a warning, not a verified anomaly.",
            "long_tails": "Do not trim outliers from the fit data.",
            "forbidden_predictors": ["order_id", "dataset_id", "marketplace_id", "order_status", "product_category_name",
                                     "split", "reviewer_label", "anomaly_label", "total_cycle_time_days"],
            "scores": "Higher is more unusual; not a calibrated anomaly probability."}


def engine_hashes():
    paths = [Path(__file__), Path(engine.__file__), Path(engine.previous.__file__), Path(automatic.__file__), Path(importer.__file__)]
    return {str(path.relative_to(ROOT)): importer.audit.file_hash(path) for path in paths}


def runtime():
    return {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
            "sklearn": sklearn.__version__, "joblib": joblib.__version__}


def build_design(orders, plan, identity):
    dates = validate_plan(plan)
    for key in ["dataset_id", "marketplace_id", "primary_category"]:
        if plan[key] != identity[key]:
            raise ValueError(f"Plan/import identity mismatch: {key}; train a separate model for the intended dataset")
    if orders.order_id.isna().any() or not orders.order_id.is_unique:
        raise ValueError("Imported order IDs must be unique and nonmissing")
    for key in ["dataset_id", "marketplace_id"]:
        if not orders[key].eq(identity[key]).all():
            raise ValueError(f"Mixed dataset identity in imported rows: {key}")
    orders = orders.sort_values("order_id").reset_index(drop=True).copy()
    selected = orders.product_category_name.eq(identity["primary_category"])
    in_period = orders.order_purchase_timestamp.ge(dates[BOUNDARIES[0]]) & orders.order_purchase_timestamp.lt(dates[BOUNDARIES[-1]])
    expected_split = pd.Series(np.where(orders.order_purchase_timestamp.lt(dates["train_end_exclusive"]), "train", "validation"), index=orders.index, dtype="string")
    expected_cutoff = pd.to_datetime(expected_split.map({"train": plan["train_end_exclusive"], "validation": plan["validation_end_exclusive"]}))
    in_scope = selected & in_period
    if (in_scope & orders.split.eq("test").fillna(False)).any():
        raise ValueError("Plan attempts to pull a reserved test order into development")
    declared = orders.split.notna() & in_scope
    if (declared & orders.split.ne(expected_split).fillna(False)).any():
        raise ValueError("Declared imported split disagrees with purchase-based training plan")
    if (in_scope & orders.event_time_cutoff_exclusive.notna() & orders.event_time_cutoff_exclusive.ne(expected_cutoff)).any():
        raise ValueError("Declared imported event cutoff disagrees with training plan")
    ledger = orders[["order_id", "source_record", "product_category_name", "order_purchase_timestamp", "split", "event_time_cutoff_exclusive"]].rename(
        columns={"split": "imported_split", "event_time_cutoff_exclusive": "imported_event_time_cutoff_exclusive"})
    ledger["scope_reason"] = "development"
    ledger.loc[~in_period, "scope_reason"] = "outside_declared_purchase_period"
    ledger.loc[orders.split.eq("test").fillna(False), "scope_reason"] = "reserved_test_not_used"
    ledger.loc[~selected, "scope_reason"] = "outside_primary_category"
    ledger["assigned_split"] = expected_split.where(in_scope)

    context = orders.loc[in_scope].copy()
    if context.empty:
        raise ValueError("No selected-category development orders in the declared period")
    context["imported_split"] = context.split
    context["imported_event_time_cutoff_exclusive"] = context.event_time_cutoff_exclusive
    context["split"] = expected_split.loc[in_scope]
    context["event_time_cutoff_exclusive"] = expected_cutoff.loc[in_scope]
    missing = context[TIMES].isna().sum(axis=1)
    reversed_times = pd.Series(False, index=context.index)
    for index, start in enumerate(TIMES):
        for end in TIMES[index+1:]:
            reversed_times |= context[end].lt(context[start])
    complete = context.order_status.eq("delivered") & missing.eq(0) & ~reversed_times
    if not context.completed_timing_eligible.eq(complete).all():
        raise ValueError("Imported completed-timing eligibility differs from actual observations")
    crosses = context[TIMES].ge(context.event_time_cutoff_exclusive, axis=0).any(axis=1)
    context["has_event_at_or_after_cutoff"] = crosses.astype("boolean")
    context["timing_input_eligible"] = complete & ~crosses
    context["timing_input_reason"] = "eligible_completed_timing"
    context.loc[crosses, "timing_input_reason"] = "completion_not_before_partition_cutoff"
    context.loc[~complete, "timing_input_reason"] = "timing_ineligible:" + context.loc[~complete, "usage_group"]
    context["purchase_month"] = context.order_purchase_timestamp.dt.to_period("M").astype("string")
    context = context.reset_index(drop=True)
    automatic.validate_context(context)
    context["inner_role"] = "process_only"
    train = context.split.eq("train") & context.timing_input_eligible
    early = context.order_purchase_timestamp.lt(dates["fit_end_exclusive"])
    observed = context[TIMES].lt(dates["fit_end_exclusive"]).all(axis=1)
    context.loc[train & early & observed, "inner_role"] = "fit"
    context.loc[train & ~early, "inner_role"] = "calibration"
    context.loc[train & early & ~observed, "inner_role"] = "deferred_at_inner_cutoff"
    context.loc[context.split.eq("validation") & context.timing_input_eligible, "inner_role"] = "validation"
    inner_ledger = context.loc[train, ["order_id", *TIMES, "inner_role"]].reset_index(drop=True)
    subsets = {}
    for role in ["fit", "calibration", "validation"]:
        subset = context.loc[context.inner_role.eq(role), ["order_id", *FEATURES]]
        if subset.empty:
            raise ValueError(f"No eligible {role} cases; revise the documented plan or supply real data, never invent observations")
        subsets[role] = engine.previous.validate_features(subset)
    if len(subsets["fit"]) < 512 or len(subsets["calibration"]) < 200:
        raise ValueError("This method profile needs at least 512 fit and 200 calibration cases; these are technical minima, not adequacy guarantees")
    role_map = context.set_index("order_id").inner_role
    ledger["inner_role"] = ledger.order_id.map(role_map).fillna("outside_scope")
    ledger["timing_input_eligible"] = ledger.order_id.map(context.set_index("order_id").timing_input_eligible).astype("boolean").where(in_scope)
    if len(ledger) != len(orders) or not ledger.order_id.is_unique:
        raise ValueError("Training-scope reconciliation failed")
    return subsets, context, ledger, inner_ledger


def load_training_inputs(snapshot, config_path):
    snapshot, config_path = Path(snapshot), Path(config_path)
    hashes = {"import_manifest_sha256": importer.audit.file_hash(snapshot / "manifest.json"),
              "plan_sha256": importer.audit.file_hash(config_path)}
    plan = json.loads(config_path.read_text(encoding="utf-8-sig"), object_pairs_hook=importer.unique_json_keys)
    validate_plan(plan)
    orders, manifest = importer.load_import_snapshot(snapshot)
    identity = identity_from_config(manifest["config"])
    design = build_design(orders, plan, identity)
    provenance = {"snapshot": str(snapshot.resolve()), "plan_path": str(config_path.resolve()), **hashes,
                  "import_output_hashes": manifest["output_hashes"]}
    verify_sources(provenance)
    return design, plan, identity, provenance


def verify_sources(provenance):
    snapshot = Path(provenance["snapshot"])
    _, manifest = importer.load_import_snapshot(snapshot)
    if (importer.audit.file_hash(snapshot / "manifest.json") != provenance["import_manifest_sha256"]
            or importer.audit.file_hash(Path(provenance["plan_path"])) != provenance["plan_sha256"]
            or manifest["output_hashes"] != provenance["import_output_hashes"]):
        raise ValueError("Training source/config changed; no model is published")


def score_bundle(bundle, frame, identity):
    if bundle["bundle_version"] != BUNDLE_VERSION or bundle["feature_contract"] != feature_contract():
        raise ValueError("Saved feature/bundle contract differs")
    if identity != bundle["identity"]:
        raise ValueError("Model/import identity mismatch; do not transfer this fitted model to another dataset/marketplace/category")
    scores = engine.add_ensembles(engine.base_scores(bundle["bank"], frame), bundle["bank"]["rank_references"])
    return scores, engine.apply_thresholds(scores, bundle["bank"]["candidates"])


def train_dataset(design, plan, identity, provenance, progress=None):
    subsets, context, ledger, inner_ledger = design
    if progress:
        progress(f"Fit: {len(subsets['fit'])}; calibration: {len(subsets['calibration'])}; validation timing: {len(subsets['validation'])}")
        progress("Fitting 10 new ML pipelines and fit-only statistics; no existing fitted Olist model is loaded.")
    bank = engine.fit_bank(subsets["fit"])
    calibration_base = engine.base_scores(bank, subsets["calibration"])
    bank["rank_references"] = {name: np.sort(calibration_base[name].to_numpy()) for name in [*engine.ENSEMBLE_ML, "mad_log1p"]}
    calibration = engine.add_ensembles(calibration_base, bank["rank_references"])
    bank["candidates"] = engine.learn_thresholds(calibration)
    bank["calibration_ids"] = subsets["calibration"].order_id.tolist()
    bundle = {"bundle_version": BUNDLE_VERSION, "identity": identity, "plan": plan,
              "feature_contract": feature_contract(), "provenance": provenance, "bank": bank,
              "validation_ids": subsets["validation"].order_id.tolist(), "runtime": runtime(), "engine_hashes": engine_hashes()}
    validation, validation_flags = score_bundle(bundle, subsets["validation"], identity)
    calibration_flags = engine.apply_thresholds(calibration, bank["candidates"])
    if progress:
        progress("Frozen 120 candidate thresholds. Producing validation diagnostics and status-aware warning evidence.")
    timing_thresholds = automatic.build_thresholds(bank)
    cases, evidence = automatic.validate_cases(context, inner_ledger, timing_thresholds, bank["candidates"],
                                               {"calibration": calibration_flags, "validation": validation_flags})
    diagnostics = []
    for row in bank["candidates"].itertuples(index=False):
        diagnostics.append({"candidate": row.candidate, "family": row.family,
                            "calibration_orders": len(calibration), "calibration_flags": int(calibration_flags[row.candidate].sum()),
                            "validation_orders": len(validation), "validation_flags": int(validation_flags[row.candidate].sum()),
                            "validation_flag_fraction": float(validation_flags[row.candidate].mean())})
    stats = [{"transform": transform, "feature": feature, **{key: float(value[i]) for key, value in values.items()}}
             for transform, values in bank["statistics"].items() for i, feature in enumerate(FEATURES)]
    monthly = validation_flags.merge(context[["order_id", "purchase_month"]], on="order_id", validate="one_to_one")
    monthly = monthly.groupby("purchase_month")[bank["candidates"].candidate.tolist()].sum().T.rename_axis("candidate").reset_index()
    frames = {"scope_ledger.csv": ledger, "development_cases.csv": context, "inner_split_ledger.csv": inner_ledger,
              **{f"{name}_timing_features.csv": frame for name, frame in subsets.items()},
              "fit_statistics.csv": pd.DataFrame(stats), "candidates.csv": bank["candidates"],
              "calibration_scores.csv": calibration, "validation_scores.csv": validation,
              "calibration_flags.csv": calibration_flags, "validation_flags.csv": validation_flags,
              "validation_diagnostics.csv": pd.DataFrame(diagnostics), "validation_monthly_flags.csv": monthly,
              "timing_thresholds.csv": timing_thresholds, "auto_case_results.csv": cases, "auto_rule_evidence.csv": evidence,
              "auto_action_summary.csv": cases.groupby(["split", "auto_action"]).size().rename("cases").reset_index()}
    summary = {"run_id": plan["run_id"], "identity": identity, "imported_orders": len(ledger), "development_orders": len(context),
               "scope_counts": ledger.scope_reason.value_counts().to_dict(), "role_counts": context.inner_role.value_counts().to_dict(),
               "fit_orders": len(subsets["fit"]), "calibration_orders": len(calibration), "validation_timing_orders": len(validation),
               "validation_all_orders": int(context.split.eq("validation").sum()), "fitted_ml_models": len(bank["models"]),
               "score_methods": len(validation.columns)-1, "candidate_configurations": len(bank["candidates"]),
               "validation_actions": cases.loc[cases.split.eq("validation"), "auto_action"].value_counts().to_dict(),
               "training_performed": True, "previous_fitted_model_reused": False, "test_scored": False,
               "human_labels_created": False, "accuracy_computed": False, "anomaly_probabilities_computed": False,
               "selected_candidate": None, "selection_status": "unselected_exploratory_candidates",
               "fit_population": "Unlabeled observed completed cases, not a verified normal-only population."}
    return frames, bundle, summary


def render_readme(summary, frames):
    return "\n\n".join([
        "# Dataset-bound timing model bundle",
        "## Actual run\n```json\n" + json.dumps(summary, indent=2) + "\n```",
        "## Learning and validation\nFit learns statistics/transforms and the 10 ML pipelines. Calibration uses separate later train purchases to set score cutoffs and empirical rank references. Validation uses still later purchases and never refits. Earlier purchases completed after a cutoff remain excluded/deferred; they are not moved to a later group by their outcome. This completed-case availability design can underrepresent long unfinished cases. Status/record checks are retrospective snapshot checks, not historical online decisions.",
        "All imported records remain in scope_ledger.csv. development_cases.csv keeps the original observations plus explicit derived split metadata. Old declared partitions/cutoffs must agree with this plan. Reserved test cases never enter fitting, calibration, scoring or configuration selection. The same code can fit a separate bundle on another compatible imported dataset; it does not copy these weights or thresholds to it.",
        "## Validation actions\n" + importer.audit.markdown_table(frames["auto_action_summary.csv"].loc[lambda frame: frame.split.eq("validation")]),
        "Actions and thresholds are exploratory decision support, not verified business labels. Short/zero durations are warnings only. Missing milestones in unfinished/canceled orders do not automatically imply anomaly. Source inconsistencies remain visible. Fit-case statistical checks are in-sample descriptions; only calibration/validation have novelty-model flags. Any-ML flag uses an exploratory OR across correlated configurations, not a calibrated vote probability or selected winning ensemble.",
        "## Files\nmodel_bundle.joblib contains identity, source fingerprint, plan, feature contract, all fitted pipelines, statistics, calibration references, cutoffs and case IDs. CSV sidecars contain exact real input features, scores, 120 candidate flags and case-level warning evidence. training_plan.json and feature_contract.json are the contracts. manifest.json records source/output/code hashes and runtime versions. Existing source data, old fitted models and reviews are not edited.",
        "## Limits\nNo independent anomaly accuracy, calibrated anomaly probability or best candidate is claimed. Warning volume/rule agreement is not accuracy. The fixed method profile needs 512 fit and 200 calibration cases for technical compatibility, not a guarantee of adequate evidence. Frequency modeling and future-batch/drift handling remain separate work. No new human review is required for this automatic pipeline to run.",
        "## Reload\nvalidate_saved_process_model.py loads this trusted local bundle and reproduces validation on the identical imported snapshot without fitting. It is a reproducibility check, not another independent experiment. The bundle is not authenticated merely because hashes match: never load an untrusted uploaded joblib/pickle file. Runtime or scoring-engine changes require an explicit compatibility review/new training version.",
    ]) + "\n"


def write_training(frames, bundle, summary, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite model bundle: {output}")
    verify_sources(bundle["provenance"])
    if bundle["engine_hashes"] != engine_hashes():
        raise ValueError("Training/scoring code changed during the run")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        joblib.dump(bundle, staging / "model_bundle.joblib", compress=3)
        restored = joblib.load(staging / "model_bundle.joblib")
        scores, flags = score_bundle(restored, frames["validation_timing_features.csv"], bundle["identity"])
        pd.testing.assert_frame_equal(scores, frames["validation_scores.csv"])
        pd.testing.assert_frame_equal(flags, frames["validation_flags.csv"])
        summary = {**summary, "saved_model_roundtrip_verified": True}
        for name, value in [("summary.json", summary), ("training_plan.json", bundle["plan"]), ("feature_contract.json", feature_contract())]:
            (staging / name).write_text(json.dumps(value, indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(summary, frames), encoding="utf-8")
        manifest = {"status": "complete", "training_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "identity": bundle["identity"], "provenance": bundle["provenance"], "summary": summary,
                    "runtime": bundle["runtime"], "engine_hashes": bundle["engine_hashes"],
                    "output_hashes": {path.name: importer.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        verify_sources(bundle["provenance"])
        if bundle["engine_hashes"] != engine_hashes():
            raise ValueError("Training/scoring code changed before publication")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Model output appeared during publication: {output}")
        staging.rename(output)
    return summary


def load_model_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("training_version") != VERSION:
        raise ValueError("Expected a completed process_training_v1 model")
    required = {"model_bundle.joblib", "training_plan.json", "feature_contract.json", "candidates.csv",
                "validation_scores.csv", "validation_flags.csv", "summary.json"}
    if not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Model manifest omits required artifacts")
    for name, digest in manifest["output_hashes"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or importer.audit.file_hash(path) != digest:
            raise ValueError(f"Model hash mismatch: {name}")
    if manifest["engine_hashes"] != engine_hashes():
        raise ValueError("Scoring engine differs from the saved model version")
    if any(manifest["runtime"][key] != runtime()[key] for key in ["numpy", "sklearn", "joblib"]):
        raise ValueError("Incompatible saved-model runtime; do not silently load across versions")
    # This API accepts trusted locally generated bundles only; hashes do not authenticate a pickle.
    bundle = joblib.load(directory / "model_bundle.joblib")
    if bundle.get("bundle_version") != BUNDLE_VERSION:
        raise ValueError("Unsupported saved bundle version")
    if (bundle["identity"] != manifest["identity"] or bundle["provenance"] != manifest["provenance"]
            or bundle["engine_hashes"] != manifest["engine_hashes"] or bundle["runtime"] != manifest["runtime"]):
        raise ValueError("Bundle/manifest binding differs")
    if bundle["plan"] != json.loads((directory / "training_plan.json").read_text()):
        raise ValueError("Bundle training plan differs")
    validate_plan(bundle["plan"])
    if (bundle["feature_contract"] != feature_contract() or bundle["bank"]["features"] != FEATURES
            or json.loads((directory / "feature_contract.json").read_text()) != feature_contract()):
        raise ValueError("Saved model feature contract differs")
    for ids in [bundle["bank"]["fit_ids"], bundle["bank"]["calibration_ids"], bundle["validation_ids"]]:
        if len(set(ids)) != len(ids):
            raise ValueError("Duplicate saved case IDs")
    if (set(bundle["bank"]["fit_ids"]) & set(bundle["bank"]["calibration_ids"])
            or set(bundle["bank"]["fit_ids"] + bundle["bank"]["calibration_ids"]) & set(bundle["validation_ids"])):
        raise ValueError("Saved training/calibration/validation IDs overlap")
    candidates = pd.read_csv(directory / "candidates.csv", float_precision="round_trip")
    pd.testing.assert_frame_equal(candidates, bundle["bank"]["candidates"], check_dtype=False)
    return bundle, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Model output already exists: {args.output}\n")
    try:
        design, plan, identity, provenance = load_training_inputs(args.snapshot, args.config)
        frames, bundle, summary = train_dataset(design, plan, identity, provenance, progress=lambda message: print(message, flush=True))
        summary = write_training(frames, bundle, summary, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Training stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
