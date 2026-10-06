"""Apply a trusted dataset-bound model to a declared batch without learning from it."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd
import scipy
from scipy.stats import ks_2samp, wasserstein_distance

import train_imported_process as training


VERSION = "process_batch_v1"
FEATURES, TIMES = training.FEATURES, training.TIMES
audit = training.importer.audit
KEYS = {"schema_version", "batch_id", "mode", "purchase_start_inclusive", "purchase_end_exclusive",
        "observation_cutoff_exclusive", "cutoff_basis", "reservation_ledger", "reservation_ledger_sha256",
        "drift_min_cases", "drift_ks_warning"}


def exact_boundary(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise ValueError("Declare an exact source-clock YYYY-MM-DD HH:MM:SS boundary; never infer today's date")
    result = pd.Timestamp(value)
    if pd.isna(result) or result.tzinfo is not None or result.strftime("%Y-%m-%d %H:%M:%S") != value:
        raise ValueError("Invalid source-clock boundary")
    return result


def validate_config(config):
    if not isinstance(config, dict) or set(config) != KEYS or config["schema_version"] != VERSION:
        raise ValueError("Expected the exact process_batch_v1 schema")
    if not isinstance(config["batch_id"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", config["batch_id"]):
        raise ValueError("Declare a valid batch_id")
    if config["mode"] not in ["validation_replay", "new_batch"]:
        raise ValueError("Unsupported batch mode")
    expected_basis = "validation_partition" if config["mode"] == "validation_replay" else "source_extract"
    if config["cutoff_basis"] != expected_basis:
        raise ValueError("New batches need an explicitly declared source extraction cutoff, not a validation boundary")
    start, end, cutoff = [exact_boundary(config[key]) for key in
                          ["purchase_start_inclusive", "purchase_end_exclusive", "observation_cutoff_exclusive"]]
    if not start < end <= cutoff:
        raise ValueError("Require purchase start < purchase end <= observation cutoff")
    if not isinstance(config["reservation_ledger"], str) or not config["reservation_ledger"]:
        raise ValueError("Declare the trusted local case-reservation ledger")
    if not isinstance(config["reservation_ledger_sha256"], str) or not re.fullmatch(r"[a-f0-9]{64}", config["reservation_ledger_sha256"]):
        raise ValueError("Declare the frozen reservation ledger SHA256")
    if type(config["drift_min_cases"]) is not int or config["drift_min_cases"] < 2:
        raise ValueError("drift_min_cases must be an integer >= 2")
    if type(config["drift_ks_warning"]) not in [int, float] or not 0 < config["drift_ks_warning"] <= 1:
        raise ValueError("drift_ks_warning must be in (0, 1]")
    return start, end, cutoff


def reservation_path(config):
    path = Path(config["reservation_ledger"])
    return path if path.is_absolute() else training.ROOT / path


def load_reservations(config, known_ids):
    path = reservation_path(config)
    if audit.file_hash(path) != config["reservation_ledger_sha256"]:
        raise ValueError("Reservation ledger hash mismatch")
    # Read identities and partition markers only, never reserved-test timestamps/features.
    ledger = pd.read_csv(path, usecols=["order_id", "split"], dtype="string", keep_default_na=False)
    if (ledger.empty or not ledger.order_id.is_unique or ledger.order_id.eq("").any()
            or not ledger.split.isin(["train", "validation", "test"]).all()):
        raise ValueError("Invalid reservation ledger")
    development = set(ledger.loc[ledger.split.isin(["train", "validation"]), "order_id"])
    if not set(known_ids).issubset(development):
        raise ValueError("Reservation ledger does not cover the model's development cases")
    return set(ledger.loc[ledger.split.eq("test"), "order_id"])


def prepare_batch(orders, bundle, reference_orders, identity, config, reserved_ids):
    start, end, cutoff = validate_config(config)
    if identity != bundle["identity"]:
        raise ValueError("Model/import identity mismatch; train separately for another source/category")
    if orders.empty or orders.order_id.isna().any() or not orders.order_id.is_unique:
        raise ValueError("Batch order IDs must be unique and nonmissing")
    if set(orders.order_id) & set(reserved_ids) or orders.split.eq("test").fillna(False).any():
        raise ValueError("Reserved test cases cannot enter batch development, even with relabeled split metadata")
    for key in ["dataset_id", "marketplace_id"]:
        if not orders[key].eq(identity[key]).all():
            raise ValueError(f"Mixed source identity: {key}")
    selected = orders.product_category_name.eq(identity["primary_category"])
    in_window = orders.order_purchase_timestamp.ge(start) & orders.order_purchase_timestamp.lt(end)
    ledger = orders[["order_id", "source_record", "product_category_name", "split", "order_purchase_timestamp"]].copy()
    ledger["batch_scope"] = "selected"
    ledger.loc[~in_window, "batch_scope"] = "outside_purchase_window"
    ledger.loc[~selected, "batch_scope"] = "outside_primary_category"
    context = orders.loc[selected & in_window].sort_values("order_id").reset_index(drop=True).copy()
    if context.empty:
        raise ValueError("No batch cases in the declared category/purchase window")
    if config["mode"] == "validation_replay":
        if (start < pd.Timestamp(bundle["plan"]["train_end_exclusive"])
                or end > pd.Timestamp(bundle["plan"]["validation_end_exclusive"])
                or cutoff != pd.Timestamp(bundle["plan"]["validation_end_exclusive"])):
            raise ValueError("Replay must stay in the original validation period and keep its fixed cutoff")
        expected = reference_orders.loc[reference_orders.split.eq("validation")
                                       & reference_orders.product_category_name.eq(identity["primary_category"])
                                       & reference_orders.order_purchase_timestamp.ge(start)
                                       & reference_orders.order_purchase_timestamp.lt(end)].sort_values("order_id").reset_index(drop=True)
        facts = ["order_id", "order_status", "product_category_name", *TIMES,
                 "order_estimated_delivery_date", "split", "event_time_cutoff_exclusive", *FEATURES]
        pd.testing.assert_frame_equal(context[facts], expected[facts], check_dtype=False, check_exact=True)
    else:
        known = set(reference_orders.order_id)
        if set(orders.order_id) & known:
            raise ValueError("New batches must not overlap any prior development cases, including deferred/ineligible cases")
        if start < pd.Timestamp(bundle["plan"]["validation_end_exclusive"]):
            raise ValueError("A new batch must start after the saved development period")
        if (not orders.split.isna().all() or orders.event_time_cutoff_exclusive.notna().any()):
            raise ValueError("New-batch imports must be unassigned, not rewritten experiment partitions")
        if not (selected & in_window).all():
            raise ValueError("A new-batch import must contain exactly the declared category and purchase window")
        if orders[TIMES].ge(cutoff).any().any():
            raise ValueError("Observed events reach/exceed the declared extraction cutoff; correct the declaration/source, do not truncate facts")
    context["imported_event_time_cutoff_exclusive"] = context.event_time_cutoff_exclusive
    context["event_time_cutoff_exclusive"] = cutoff
    context["observation_cutoff_basis"] = config["cutoff_basis"]
    context["imported_usage_group"] = context.usage_group
    if config["mode"] == "new_batch":
        context.loc[context.usage_group.eq("nonfinal_status_cutoff_unknown"), "usage_group"] = "nonfinal_status_completed_model_unavailable"
    complete = context.order_status.eq("delivered") & context[TIMES].notna().all(axis=1)
    for index, left in enumerate(TIMES):
        for right in TIMES[index+1:]:
            complete &= ~context[right].lt(context[left])
    if not context.completed_timing_eligible.eq(complete).all():
        raise ValueError("Imported eligibility disagrees with actual observations")
    crosses = context[TIMES].ge(cutoff).any(axis=1)
    context["has_event_at_or_after_cutoff"] = crosses.astype("boolean")
    context["timing_input_eligible"] = complete & ~crosses
    context["timing_input_reason"] = "eligible_completed_timing"
    context.loc[crosses, "timing_input_reason"] = "completion_not_before_partition_cutoff"
    context.loc[~complete, "timing_input_reason"] = "timing_ineligible:" + context.loc[~complete, "usage_group"]
    context["purchase_month"] = context.order_purchase_timestamp.dt.to_period("M").astype("string")
    # The frozen explanation helper calls out-of-fit application 'validation'. Restore source metadata on output.
    check = context.assign(split="validation")
    training.automatic.validate_context(check)
    ledger["timing_input_eligible"] = ledger.order_id.map(context.set_index("order_id").timing_input_eligible).astype("boolean")
    return context, ledger


def score_cases(bundle, context, identity, score_columns):
    features = context.loc[context.timing_input_eligible, ["order_id", *FEATURES]].reset_index(drop=True)
    if features.empty:
        scores = pd.DataFrame({"order_id": pd.Series(dtype="string"),
                               **{name: pd.Series(dtype=float) for name in score_columns if name != "order_id"}})
        flags = training.engine.apply_thresholds(scores, bundle["bank"]["candidates"])
    else:
        scores, flags = training.score_bundle(bundle, features, identity)
    return features, scores, flags


def explain_cases(bundle, context, flags, config):
    thresholds = training.automatic.build_thresholds(bundle["bank"])
    helper_context = context.assign(split="validation")
    roles = pd.DataFrame({"order_id": pd.Series(dtype="string"), "inner_role": pd.Series(dtype="string")})
    cases, evidence = training.automatic.validate_cases(helper_context, roles, thresholds,
                                                      bundle["bank"]["candidates"], {"batch": flags})
    cases["split"] = cases.order_id.map(context.set_index("order_id").split)
    cases = cases.drop(columns="benchmark_role")
    cases["application_mode"] = config["mode"]
    cases["batch_id"] = config["batch_id"]
    evidence = evidence.drop(columns="split")
    evidence.insert(1, "batch_id", config["batch_id"])
    for index in evidence.index[evidence.reason_code.eq("MODEL_CANDIDATE_SIGNAL")]:
        details = json.loads(evidence.at[index, "details_json"])
        details["score_and_threshold_source"] = "This batch's candidate_scores.csv and frozen candidates.csv"
        evidence.at[index, "details_json"] = json.dumps(details)
    if config["mode"] == "new_batch":
        old, new = "NONFINAL_CUTOFF_UNKNOWN", "NONFINAL_COMPLETED_MODEL_UNAVAILABLE"
        mask = evidence.reason_code.eq(old)
        for index in evidence.index[mask]:
            details = json.loads(evidence.at[index, "details_json"])
            details.update({"extraction_cutoff": config["observation_cutoff_exclusive"],
                            "limitation": "Completed-transition model cannot score unfinished cases; ongoing-age modeling is not implemented."})
            evidence.at[index, "details_json"] = json.dumps(details)
        evidence.loc[mask, "reason_code"] = new
        cases["reason_codes_json"] = cases.reason_codes_json.map(lambda value: json.dumps([new if code == old else code for code in json.loads(value)]))
    return cases, evidence, thresholds


def candidate_evidence(scores, flags, candidates):
    records = []
    indexed = scores.set_index("order_id")
    for row in candidates.itertuples(index=False):
        for order_id in flags.loc[flags[row.candidate], "order_id"]:
            score = float(indexed.at[order_id, row.score_column])
            records.append({"order_id": order_id, "candidate": row.candidate, "family": row.family,
                            "score_column": row.score_column, "score": score, "threshold": row.threshold,
                            "score_excess": score-row.threshold, "comparison": "strictly_greater"})
    return pd.DataFrame(records, columns=["order_id", "candidate", "family", "score_column", "score", "threshold", "score_excess", "comparison"])


def drift_diagnostics(reference, current, config, window):
    rows = []
    for feature in FEATURES:
        ref, batch = reference[feature].to_numpy(dtype=float), current[feature].to_numpy(dtype=float)
        if not np.isfinite(ref).all() or not np.isfinite(batch).all() or (ref < 0).any() or (batch < 0).any():
            raise ValueError("Drift requires actual finite nonnegative eligible durations")
        enough = min(len(ref), len(batch)) >= config["drift_min_cases"]
        row = {"window": window, "feature": feature, "reference_cases": len(ref), "batch_cases": len(batch),
               "ks_distance": None, "wasserstein_days": None, "median_shift_days": None, "q90_shift_days": None,
               "ks_warning_threshold": config["drift_ks_warning"], "minimum_cases": config["drift_min_cases"],
               "distribution_warning": None, "status": "insufficient_cases"}
        if len(ref) and len(batch):
            # Use distances descriptively; no continuous-null p-value is reported for tied operational timestamps.
            row.update({"ks_distance": float(ks_2samp(ref, batch, method="asymp").statistic),
                        "wasserstein_days": float(wasserstein_distance(ref, batch)),
                        "median_shift_days": float(np.median(batch)-np.median(ref)),
                        "q90_shift_days": float(np.quantile(batch, .9)-np.quantile(ref, .9))})
        if enough:
            warning = row["ks_distance"] >= config["drift_ks_warning"]
            row.update({"distribution_warning": bool(warning), "status": "inspect_distribution_change" if warning else "no_distance_warning"})
        rows.append(row)
    return pd.DataFrame(rows)


def quality_summary(context, window):
    total = len(context)
    indicators = {"timing_eligible": context.timing_input_eligible,
                  "missing_actual_milestone": context[TIMES].isna().any(axis=1),
                  "recorded_timeline_reversal": context.has_reversed_recorded_milestones,
                  "status_delivery_conflict": context.status_delivery_conflict,
                  **{f"status_{status}": context.order_status.eq(status) for status in training.importer.usage.clean.STATUSES}}
    return [{"window": window, "indicator": name, "cases": int(mask.sum()), "all_cases": total,
             "fraction_of_all_cases": float(mask.mean()) if total else None} for name, mask in indicators.items()]


def build_outputs(bundle, context, ledger, reference_features, reference_context, score_columns, identity, config):
    features, scores, flags = score_cases(bundle, context, identity, score_columns)
    cases, evidence, thresholds = explain_cases(bundle, context, flags, config)
    candidates = bundle["bank"]["candidates"]
    windows = {"all_batch": cases, **{str(month): frame for month, frame in cases.groupby("purchase_month", sort=True)}}
    drifts, quality, rates = [], quality_summary(reference_context, "reference_calibration_purchase_cohort"), []
    for window, group in windows.items():
        eligible = features.loc[features.order_id.isin(group.order_id)]
        drifts.append(drift_diagnostics(reference_features, eligible, config, window))
        quality.extend(quality_summary(group, window))
        window_flags = flags.loc[flags.order_id.isin(group.order_id)]
        for row in candidates.itertuples(index=False):
            rates.append({"window": window, "candidate": row.candidate, "all_cases": len(group),
                          "scored_cases": len(window_flags), "flagged_cases": int(window_flags[row.candidate].sum()),
                          "flag_fraction_of_scored": float(window_flags[row.candidate].mean()) if len(window_flags) else None})
    drift = pd.concat(drifts, ignore_index=True)
    frames = {"scope_ledger.csv": ledger, "case_results.csv": cases, "rule_evidence.csv": evidence,
              "timing_features.csv": features, "candidate_scores.csv": scores, "candidate_flags.csv": flags,
              "candidates.csv": candidates.copy(), "positive_candidate_evidence.csv": candidate_evidence(scores, flags, candidates),
              "timing_thresholds.csv": thresholds, "distribution_diagnostics.csv": drift,
              "quality_summary.csv": pd.DataFrame(quality), "candidate_rates.csv": pd.DataFrame(rates),
              "action_summary.csv": cases.groupby("auto_action").size().rename("cases").reset_index()}
    summary = {"batch_id": config["batch_id"], "mode": config["mode"], "identity": identity,
               "imported_cases": len(ledger), "batch_cases": len(cases), "scored_cases": len(scores),
               "unscored_cases": len(cases)-len(scores), "candidate_configurations": len(candidates),
               "actions": cases.auto_action.value_counts().to_dict(),
               "distribution_warning_features": drift.loc[drift.window.eq("all_batch") & drift.distribution_warning.eq(True), "feature"].tolist(),
               "reference_timing_cases": len(reference_features), "refitting_performed": False,
               "thresholds_updated": False, "test_scored": False, "accuracy_computed": False,
               "anomaly_probabilities_computed": False, "human_labels_created": False, "selected_candidate": None,
               "independent_new_data_evaluation": False,
               "interpretation": "Operational/development diagnostics only; no ground-truth accuracy or best-model claim."}
    return frames, summary


def load_inputs(model_dir, snapshot, config_path):
    model_dir, snapshot, config_path = map(Path, [model_dir, snapshot, config_path])
    config_hash = audit.file_hash(config_path)
    config = json.loads(config_path.read_text(), object_pairs_hook=training.importer.unique_json_keys)
    validate_config(config)
    model_hash = audit.file_hash(model_dir / "manifest.json")
    bundle, model_manifest = training.load_model_bundle(model_dir)
    import_hash = audit.file_hash(snapshot / "manifest.json")
    orders, imported = training.importer.load_import_snapshot(snapshot)
    identity = training.identity_from_config(imported["config"])
    source = Path(bundle["provenance"]["snapshot"])
    reference_orders, source_manifest = training.importer.load_import_snapshot(source)
    if (audit.file_hash(source / "manifest.json") != bundle["provenance"]["import_manifest_sha256"]
            or source_manifest["output_hashes"] != bundle["provenance"]["import_output_hashes"]):
        raise ValueError("Saved training source changed")
    _, development, _, _ = training.build_design(reference_orders, bundle["plan"], bundle["identity"])
    reserved = load_reservations(config, development.order_id)
    if config["mode"] == "validation_replay" and import_hash != bundle["provenance"]["import_manifest_sha256"]:
        raise ValueError("Validation replay requires the identical saved import snapshot")
    context, ledger = prepare_batch(orders, bundle, development, identity, config, reserved)
    reference_features = pd.read_csv(model_dir / "calibration_timing_features.csv", dtype={"order_id": "string"}, float_precision="round_trip")
    reference_features = training.engine.previous.validate_features(reference_features)
    if reference_features.order_id.tolist() != bundle["bank"]["calibration_ids"]:
        raise ValueError("Saved drift reference differs from calibration IDs")
    reference_context = development.loc[development.split.eq("train")
                                        & development.order_purchase_timestamp.ge(pd.Timestamp(bundle["plan"]["fit_end_exclusive"]))].copy()
    score_columns = pd.read_csv(model_dir / "calibration_scores.csv", nrows=0).columns.tolist()
    provenance = {"model_directory": str(model_dir.resolve()), "snapshot": str(snapshot.resolve()),
                  "config_path": str(config_path.resolve()), "config_sha256": config_hash,
                  "model_manifest_sha256": model_hash, "model_output_hashes": model_manifest["output_hashes"],
                  "import_manifest_sha256": import_hash, "import_output_hashes": imported["output_hashes"],
                  "source_snapshot": str(source.resolve()), "source_manifest_sha256": bundle["provenance"]["import_manifest_sha256"],
                  "source_output_hashes": source_manifest["output_hashes"],
                  "reservation_path": str(reservation_path(config).resolve()), "reservation_sha256": config["reservation_ledger_sha256"],
                  "code_sha256": audit.file_hash(Path(__file__)), "runtime": {**training.runtime(), "scipy": scipy.__version__}}
    verify_sources(provenance)
    return bundle, context, ledger, reference_features, reference_context, score_columns, identity, config, provenance


def verify_sources(provenance):
    checks = [(provenance["config_path"], provenance["config_sha256"]),
              (provenance["reservation_path"], provenance["reservation_sha256"]), (Path(__file__), provenance["code_sha256"])]
    for key, manifest_key, outputs_key in [("model_directory", "model_manifest_sha256", "model_output_hashes"),
                                           ("snapshot", "import_manifest_sha256", "import_output_hashes"),
                                           ("source_snapshot", "source_manifest_sha256", "source_output_hashes")]:
        root = Path(provenance[key])
        checks.append((root / "manifest.json", provenance[manifest_key]))
        checks.extend((root / name, digest) for name, digest in provenance[outputs_key].items())
    for path, digest in checks:
        if audit.file_hash(Path(path)) != digest:
            raise ValueError(f"Batch input/code changed: {path}")


def render_readme(summary):
    return "\n\n".join([
        "# Saved-model batch application and distribution diagnostics",
        "## Actual result\n```json\n" + json.dumps(summary, indent=2) + "\n```",
        "validation_replay is the same previously used validation population, grouped into purchase-month batches. It is NOT new independent data or an online historical backtest. Its observation boundary remains the original end of validation, including for earlier purchase months. Snapshot status may not have been known historically. new_batch instead requires unassigned, disjoint orders after development and a user-declared actual source extraction cutoff. No missing timestamp or ongoing age is invented.",
        "## Scores and evidence\nAll eligible cases use the saved transformations, models, calibration references and strict score > threshold rule. Nothing is fitted or recalibrated. positive_candidate_evidence.csv gives each positive candidate's score, cutoff and excess; it is not a causal feature attribution. rule_evidence.csv gives observed timing and source-record reasons. Unavailable checks remain blank. Short/zero durations are warnings, never automatic verified anomalies. Correlated configurations are not independent votes or anomaly probabilities. No candidate winner is selected.",
        "## Distribution diagnostics\nThe frozen 1D duration reference is the saved calibration timing population. KS distance is max_x |F_reference(x)-F_batch(x)|; Wasserstein distance is the integral of that absolute CDF gap, expressed in days. Median and Q90 changes are batch minus reference. SciPy supplies the distances; no p-value or statistical significance claim is emitted. The declared KS screening cutoff and minimum sample size are exploratory policy parameters, not accuracy-optimized anomaly thresholds or statistical power guarantees. Small/empty batches return unavailable warnings, not a clean bill of health. No automatic retraining is triggered.",
        "Only completed, temporally eligible cases enter duration comparisons. This conditional population can underrepresent unfinished/very long orders. quality_summary.csv separately reports full-cohort missingness, source conflicts, statuses and scoring coverage with explicit denominators. The quality reference uses all purchases in the calibration purchase period, not just its eligible timing subset. Monthly batch coverage can differ with follow-up time; distribution changes do not prove fraud, process failure, model degradation or causality.",
        "## Integrity\nThe declared hashed reservation ledger is read for order_id and split only. Reserved test IDs are refused even if an import relabels them. All prior development IDs, including deferred/ineligible ones, are refused in new_batch mode. Dataset/marketplace/category/source-clock identity must match. Identity and caller-supplied source declarations are not independent evidence of provenance or matching business semantics. manifest.json records inputs, code, runtime and outputs; old sources/models are never overwritten. Load trusted local joblib bundles only.",
    ]) + "\n"


def write_outputs(frames, summary, config, provenance, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite batch results: {output}")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        for name, value in [("summary.json", summary), ("batch_config.json", config)]:
            (staging / name).write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")
        (staging / "README.md").write_text(render_readme(summary), encoding="utf-8")
        manifest = {"status": "complete", "batch_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "summary": summary,
                    "output_hashes": {path.name: audit.file_hash(path) for path in sorted(staging.iterdir())}}
        verify_sources(provenance)
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Batch output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    try:
        inputs = load_inputs(args.model, args.snapshot, args.config)
        *scoring_inputs, config, provenance = inputs
        print(f"Applying saved model in {config['mode']} mode; no fitting or threshold changes.", flush=True)
        frames, summary = build_outputs(*scoring_inputs, config)
        write_outputs(frames, summary, config, provenance, args.output)
    except (ValueError, OSError, AssertionError) as exc:
        parser.exit(2, f"Batch application stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
