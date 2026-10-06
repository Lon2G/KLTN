"""Refit nested history windows and compare decisions on identical validation cases."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import train_imported_process as training
import validate_saved_process_model as replay
import compare_process_candidates as comparison


VERSION = "process_history_stability_v1"
DEFINITION_COLUMNS = [name for name in comparison.CANDIDATE_COLUMNS if name != "threshold"]
CHANGED_COLUMNS = ["order_id", "candidate", "family", "windows_evaluated", "windows_flagged", "flagged_run_ids_json"]


def code_hashes():
    paths = [Path(__file__), Path(replay.__file__), Path(comparison.__file__)]
    return {**training.engine_hashes(),
            **{str(path.relative_to(training.ROOT)): training.importer.audit.file_hash(path) for path in paths}}


def ordered(frame):
    return frame.sort_values("order_id").reset_index(drop=True)


def require_same(left, right, message):
    try:
        pd.testing.assert_frame_equal(left, right, check_exact=True)
    except AssertionError as exc:
        raise ValueError(message) from exc


def validate_designs(inputs):
    if len(inputs) < 2:
        raise ValueError("Require at least two distinct training history plans")
    reference_design, reference_plan, identity, provenance = inputs[0]
    starts, run_ids, previous_fit = set(), set(), None
    fixed = [key for key in training.PLAN_KEYS if key not in ["run_id", "purchase_start_inclusive"]]
    for design, plan, current_identity, current_provenance in inputs:
        training.validate_plan(plan)
        if current_identity != identity or any(plan[key] != reference_plan[key] for key in fixed):
            raise ValueError("History plans must share identity, method profile and all end boundaries")
        if any(current_provenance[key] != provenance[key] for key in ["snapshot", "import_manifest_sha256", "import_output_hashes"]):
            raise ValueError("History plans must use the identical imported snapshot")
        start = pd.Timestamp(plan["purchase_start_inclusive"])
        if start in starts or plan["run_id"] in run_ids:
            raise ValueError("History starts and run IDs must be distinct")
        if starts and start <= max(starts):
            raise ValueError("Order plans from longest to shortest history")
        starts.add(start)
        run_ids.add(plan["run_id"])
        subsets, context, _, _ = design
        fit_ids = set(subsets["fit"].order_id)
        if previous_fit is not None and not fit_ids < previous_fit:
            raise ValueError("Shorter histories must have strictly nested fit populations")
        previous_fit = fit_ids
        for role in ["calibration", "validation"]:
            require_same(subsets[role], reference_design[0][role], f"Common {role} features differ")
            if fit_ids & set(subsets[role].order_id):
                raise ValueError("Fit and evaluation populations overlap")
        require_same(ordered(context.loc[context.split.eq("validation")]),
                     ordered(reference_design[1].loc[reference_design[1].split.eq("validation")]),
                     "All validation case facts and eligibility must stay fixed")


def load_inputs(snapshot, plans):
    inputs = [training.load_training_inputs(snapshot, plan) for plan in plans]
    validate_designs(inputs)
    return inputs


def compare_windows(candidates, flags_by_run):
    """Align real case IDs before comparing binary decisions, never raw score scales."""
    run_ids = list(flags_by_run)
    if len(run_ids) < 2:
        raise ValueError("Require at least two flag matrices")
    names = sorted(candidates.candidate)
    definitions = candidates.set_index("candidate")
    aligned = []
    for frame in flags_by_run.values():
        comparison.require_ids(frame, "window flags")
        if set(frame.columns) != {"order_id", *names} or not frame[names].isin([True, False]).all().all():
            raise ValueError("Window flags must have the same complete boolean candidate schema")
        aligned.append(ordered(frame)[["order_id", *names]])
        if aligned[-1].order_id.tolist() != aligned[0].order_id.tolist():
            raise ValueError("Window validation case IDs differ")
    if aligned[0].empty:
        raise ValueError("No scored validation cases to compare")
    values = np.stack([frame[names].to_numpy(dtype=bool) for frame in aligned])
    counts = values.sum(axis=1, dtype=np.int64)
    votes = values.sum(axis=0, dtype=np.int64)
    changed = (votes > 0) & (votes < len(run_ids))
    n = len(aligned[0])
    stability, changes, pairs = [], [], []
    for column, name in enumerate(names):
        stability.append({"candidate": name, "family": definitions.at[name, "family"],
                          "windows_evaluated": len(run_ids), "scored_cases_per_window": n,
                          "min_flagged_cases": int(counts[:, column].min()), "max_flagged_cases": int(counts[:, column].max()),
                          "flag_rate_range_pp": float(100*(counts[:, column].max()-counts[:, column].min())/n),
                          "cases_flagged_in_every_window": int((votes[:, column] == len(run_ids)).sum()),
                          "cases_flagged_in_any_window": int((votes[:, column] > 0).sum()),
                          "cases_with_changed_decision": int(changed[:, column].sum()),
                          "changed_decision_fraction": float(changed[:, column].mean())})
        for row in np.flatnonzero(changed[:, column]):
            changes.append({"order_id": aligned[0].order_id.iloc[row], "candidate": name,
                            "family": definitions.at[name, "family"], "windows_evaluated": len(run_ids),
                            "windows_flagged": int(votes[row, column]),
                            "flagged_run_ids_json": json.dumps([run_ids[i] for i in range(len(run_ids)) if values[i, row, column]])})
        for i, j in combinations(range(len(run_ids)), 2):
            left, right = values[i, :, column], values[j, :, column]
            both, union = int((left & right).sum()), int((left | right).sum())
            different = int((left != right).sum())
            pairs.append({"candidate": name, "family": definitions.at[name, "family"],
                          "left_run_id": run_ids[i], "right_run_id": run_ids[j], "scored_cases": n,
                          "both_flag": both, "left_only": int((left & ~right).sum()),
                          "right_only": int((right & ~left).sum()), "neither_flag": n-union, "union_flags": union,
                          "jaccard_overlap": both/union if union else None,
                          "changed_decisions": different, "changed_decision_fraction": different/n})
    return {"candidate_stability.csv": pd.DataFrame(stability),
            "changed_case_decisions.csv": pd.DataFrame(changes, columns=CHANGED_COLUMNS),
            "window_pairwise_agreement.csv": pd.DataFrame(pairs)}


def build_outputs(runs):
    metrics, windows, thresholds, monthly, coverage = [], [], [], [], []
    flags_by_run, reference_candidates, reference_features, reference_cases = {}, None, {}, None
    for plan, frames, summary in runs:
        run_id = plan["run_id"]
        if run_id in flags_by_run:
            raise ValueError("Duplicate run ID")
        candidates = frames["candidates.csv"]
        cases = ordered(frames["auto_case_results.csv"].loc[lambda frame: frame.split.eq("validation")])
        flags, calibration = frames["validation_flags.csv"], frames["calibration_flags.csv"]
        comparison.validate_inputs(candidates, cases, flags, calibration)
        definitions = candidates[DEFINITION_COLUMNS].sort_values("candidate").reset_index(drop=True)
        if reference_candidates is None:
            reference_candidates = candidates
            reference_cases = cases[[name for name in comparison.CASE_COLUMNS if name not in comparison.RULES]]
        else:
            require_same(definitions, reference_candidates[DEFINITION_COLUMNS].sort_values("candidate").reset_index(drop=True),
                         "Candidate definitions changed across histories")
            require_same(cases[reference_cases.columns], reference_cases, "Validation availability changed across histories")
        for role in ["calibration", "validation"]:
            features = ordered(frames[f"{role}_timing_features.csv"])
            reference_features.setdefault(role, features)
            require_same(features, reference_features[role], f"Common {role} features differ")
            actual = training.engine.apply_thresholds(frames[f"{role}_scores.csv"], candidates)
            require_same(ordered(frames[f"{role}_flags.csv"]), ordered(actual), "Score/threshold/flag mismatch")
        flags_by_run[run_id] = flags
        start, end = pd.Timestamp(plan["purchase_start_inclusive"]), pd.Timestamp(plan["fit_end_exclusive"])
        windows.append({"run_id": run_id, **{key: plan[key] for key in training.BOUNDARIES},
                        "history_calendar_days": (end-start).total_seconds()/86400,
                        **{key: summary[key] for key in ["imported_orders", "development_orders", "fit_orders", "calibration_orders",
                                                        "validation_all_orders", "validation_timing_orders", "fitted_ml_models"]},
                        "outside_scope_orders": summary["imported_orders"]-summary["development_orders"]})
        for row in candidates.itertuples(index=False):
            metrics.append({"run_id": run_id, **row._asdict(), "fit_orders": summary["fit_orders"],
                            "calibration_orders": len(calibration), "calibration_flags": int(calibration[row.candidate].sum()),
                            "calibration_flag_fraction": float(calibration[row.candidate].mean()),
                            "validation_orders": len(flags), "validation_flags": int(flags[row.candidate].sum()),
                            "validation_flag_fraction": float(flags[row.candidate].mean())})
        thresholds.append(frames["timing_thresholds.csv"].assign(run_id=run_id))
        month, covered = comparison.monthly_comparison(candidates, cases, flags)
        monthly.append(month.assign(run_id=run_id))
        coverage.append(covered.assign(run_id=run_id))
    if not runs:
        raise ValueError("No history runs to summarize")
    output = compare_windows(reference_candidates, flags_by_run)
    output.update({"window_summary.csv": pd.DataFrame(windows), "candidate_window_metrics.csv": pd.DataFrame(metrics),
                   "timing_thresholds_by_window.csv": pd.concat(thresholds, ignore_index=True),
                   "monthly_candidate_rates.csv": pd.concat(monthly, ignore_index=True),
                   "monthly_coverage.csv": pd.concat(coverage, ignore_index=True)})
    stability, changes = output["candidate_stability.csv"], output["changed_case_decisions.csv"]
    summary = {"study_type": "training_history_length_sensitivity", "windows": len(runs),
               "run_ids": list(flags_by_run), "identity": runs[0][2]["identity"],
               "fit_orders_by_run": {row["run_id"]: row["fit_orders"] for row in windows},
               "fresh_ml_fits": sum(row["fitted_ml_models"] for row in windows),
               "unique_candidate_configurations": len(reference_candidates), "window_configuration_results": len(metrics),
               "common_calibration_orders": len(reference_features["calibration"]),
               "common_validation_all_orders": len(reference_cases), "common_validation_scored_orders": len(reference_features["validation"]),
               "common_validation_unscored_orders": len(reference_cases)-len(reference_features["validation"]),
               "candidates_with_changed_decisions": int(stability.cases_with_changed_decision.gt(0).sum()),
               "changed_case_candidate_pairs": len(changes), "unique_cases_with_any_candidate_change": int(changes.order_id.nunique()),
               "max_candidate_flag_rate_range_pp": float(stability.flag_rate_range_pp.max()),
               "selected_candidate": None, "accuracy_computed": False, "anomaly_probabilities_computed": False,
               "human_labels_created": False, "test_scored": False, "existing_fitted_model_reused": False,
               "independent_new_data_evaluation": False, "rolling_origin_backtest": False,
               "interpretation": "Paired exploratory sensitivity on reused validation data, not accuracy or an optimal-history selection."}
    return output, summary


def render_readme(summary, frames):
    windows = frames["window_summary.csv"][["run_id", "fit_orders", "calibration_orders", "validation_all_orders", "validation_timing_orders"]]
    return "\n\n".join([
        "# Training-history sensitivity on real Olist data",
        training.importer.audit.markdown_table(windows),
        "## Actual results\n```json\n" + json.dumps(summary, indent=2) + "\n```",
        "## Design\nOnly the fit purchase start and run ID change across plans. Fit, calibration and validation end dates, "
        "imported observations, candidate definitions and random seed stay fixed. Each history refits all ten ML pipelines "
        "and statistical baselines; thresholds/rank references are relearned on identical calibration cases. "
        "Validation never fits models or thresholds. Earlier purchases are retained outside scope in each ledger. "
        "Fit events must precede the fit cutoff; original calibration/validation event cutoffs remain unchanged.",
        "## Interpretation\nHistory length, fit sample size and calendar composition change together. This is not rolling-origin "
        "backtesting, random-seed robustness, independent accuracy evaluation or a claim that the shortest/longest history is best. "
        "These are repeated decisions on the SAME validation cases, not new observations. Stability can describe consistently wrong "
        "decisions, and fewer alerts are not necessarily better. No winner is selected. Final-test features and human-review labels are not read.",
        "## Tables\ncandidate_window_metrics.csv records each run's score cutoff and alert count. "
        "candidate_stability.csv uses 100*(max flagged count - min flagged count)/common scored cases for the rate range in percentage points. "
        "window_pairwise_agreement.csv compares the SAME candidate between two histories: Jaccard = both flagged / either flagged, "
        "undefined when neither flags anything; changed-decision fraction = disagreeing decisions / common scored cases. "
        "changed_case_decisions.csv identifies real cases whose flag changes for a candidate, with exact flagging run IDs. "
        "Counts of flagging histories are NOT anomaly probabilities or new labels; the histories are correlated and nested. "
        "Raw anomaly score scales are not averaged or compared as probabilities.",
        "timing_thresholds_by_window.csv contains thresholds in DAYS, which can change even when a fixed IQR/MAD multiplier stays the same. "
        "monthly_coverage.csv preserves unscored cases as unknown; completed-case selection near the cutoff underrepresents unfinished long cases. "
        "Short/zero duration alone remains a warning. Fit statistics describe unlabeled data, not a verified normal population. "
        "Process/status checks are retrospective, not historical online decisions.",
        "## Reproducibility\nEach runs/<run_id>/ directory is a complete saved-model artifact with input/plan/code/output hashes. "
        "reload_checks.csv confirms that reloading every new bundle reproduces its validation flags exactly and scores within 1e-12. "
        "This checks software reproducibility, not anomaly accuracy. Only load trusted locally generated joblib files. "
        "The top manifest hashes every published file recursively; existing data, models, reviews and experiments are not overwritten.",
        "## Remaining work\nTrue rolling-origin evaluation on a documented temporal design, richer process/frequency features, "
        "and an advisor-approved independent evaluation reference remain separate work. The present study needs no new import or manual batch.",
    ]) + "\n"


def run_study(snapshot, plans, output, progress=None):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite history study: {output}")
    inputs = load_inputs(snapshot, plans)
    hashes = code_hashes()

    def verify_sources():
        if hashes != code_hashes():
            raise ValueError("History-study code changed during execution")
        for _, _, _, provenance in inputs:
            training.verify_sources(provenance)

    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging, runs, checks = Path(temporary), [], []
        for design, plan, identity, provenance in inputs:
            if progress:
                progress(f"History {plan['run_id']}: {plan['purchase_start_inclusive']} to {plan['fit_end_exclusive']}")
            frames, bundle, summary = training.train_dataset(design, plan, identity, provenance, progress=progress)
            directory = staging / "runs" / plan["run_id"]
            summary = training.write_training(frames, bundle, summary, directory)
            _, checked, _ = replay.reproduce_validation(directory, snapshot)
            checks.append({"run_id": plan["run_id"], "model_relative_path": str(directory.relative_to(staging)),
                           **{key: checked[key] for key in ["validation_orders_scored", "candidate_configurations",
                                                         "scores_match_with_tolerance", "candidate_flags_match_exactly", "refitting_performed"]}})
            runs.append((plan, frames, summary))
        frames, summary = build_outputs(runs)
        frames["reload_checks.csv"] = pd.DataFrame(checks)
        summary["all_saved_models_reloaded_and_verified"] = True
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False)
        (staging / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(summary, frames), encoding="utf-8")
        verify_sources()
        manifest = {"status": "complete", "version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "runtime": training.runtime(), "code_hashes": hashes,
                    "input_provenance": [item[3] for item in inputs],
                    "output_hashes": {str(path.relative_to(staging)): training.importer.audit.file_hash(path)
                                      for path in sorted(staging.rglob("*")) if path.is_file()}}
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"History-study output appeared during publication: {output}")
        staging.rename(output)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--plans", type=Path, nargs="+", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        summary = run_study(args.snapshot, args.plans, args.output, progress=lambda message: print(message, flush=True))
    except (ValueError, OSError) as exc:
        parser.exit(2, f"History study stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
