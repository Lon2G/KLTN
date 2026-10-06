"""Fit train-only timing candidates, inspect validation, and prepare blind human review."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import platform
import sys
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts/01_data_preparation"))
import prepare_bed_bath_table_experiment as bed
from order_status_context_audit import ACTIVITY_COLUMNS, build_blind_cases
from prepare_status_context_review import collect_reviewed_ids
from project_paths import EXPERIMENT_DATA_DIR, MANUAL_REVIEW_DIR


FEATURES = list(bed.FEATURES)
DEFAULT_OUTPUT = EXPERIMENT_DATA_DIR / "bed_bath_table_training_v1"
TRANSFORMS = ["raw", "log1p"]
TAIL_FRACTIONS = [.03, .05, .08, .10]
SEED = 42
TREES = 300
REVIEW_SIZE = 120


def verify_experiment(source):
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("experiment_version") != "bed_bath_table_v1":
        raise ValueError("Expected a completed bed_bath_table_v1 experiment")
    required = {"cohort_cases.csv", "train_timing_features.csv", "validation_timing_features.csv",
                "test_timing_features.csv", "feature_contract.json"}
    if not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Experiment manifest omits required files")
    for filename, digest in manifest["output_hashes"].items():
        path = (source / filename).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file() or bed.audit.file_hash(path) != digest:
            raise ValueError(f"Experiment hash mismatch: {filename}")
    contract = json.loads((source / "feature_contract.json").read_text())
    if contract != bed.feature_contract():
        raise ValueError("Feature contract differs from the frozen timing experiment")
    if manifest["design"]["category_original"] != bed.CATEGORY:
        raise ValueError("Unexpected industry")
    return manifest


def validate_features(frame):
    if list(frame.columns) != ["order_id", *FEATURES]:
        raise ValueError("Use only order_id plus the ordered duration allowlist")
    if frame.empty or frame.order_id.isna().any() or not frame.order_id.is_unique:
        raise ValueError("Feature rows must have unique nonmissing order IDs")
    values = frame[FEATURES].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Timing features must be finite and nonnegative; do not impute")
    return frame.sort_values("order_id").reset_index(drop=True)


def load_inputs(source=bed.DEFAULT_OUTPUT):
    source = Path(source)
    manifest = verify_experiment(source)
    # Hash-check the frozen test file, but never parse its feature matrix here.
    context = bed.usage.read_snapshot_table(source, "cohort_cases.csv", manifest)
    if context.order_id.isna().any() or not context.order_id.is_unique:
        raise ValueError("Cohort IDs must be unique and nonmissing")
    context = context.loc[context.split.isin(["train", "validation"])].copy()
    matrices = {}
    partitions = {row["split"]: row for row in manifest["design"]["partitions"]}
    for name in ["train", "validation"]:
        frame = validate_features(bed.usage.read_snapshot_table(source, f"{name}_timing_features.csv", manifest))
        group = context.loc[context.split.eq(name)].set_index("order_id")
        eligible = group.loc[group.timing_input_eligible]
        if set(frame.order_id) != set(eligible.index):
            raise ValueError(f"Feature IDs differ from {name} eligibility")
        observed = eligible.loc[frame.order_id]
        if not np.allclose(frame[FEATURES], observed[FEATURES], rtol=1e-12, atol=1e-12):
            raise ValueError(f"Feature values differ from {name} observations")
        cutoff = pd.Timestamp(partitions[name]["event_time_cutoff_exclusive"])
        if not observed[bed.audit.TIMES].lt(cutoff).all().all():
            raise ValueError(f"Future or missing event in {name} timing features")
        if not observed.completed_timing_eligible.all():
            raise ValueError(f"Ineligible timing case in {name}")
        matrices[name] = frame
    if set(matrices["train"].order_id) & set(matrices["validation"].order_id):
        raise ValueError("Train/validation overlap")
    parents = {"experiment_manifest_sha256": bed.audit.file_hash(source / "manifest.json"),
               "experiment_output_hashes": manifest["output_hashes"]}
    return matrices, context, parents


def current_review_exposure():
    paths = list(MANUAL_REVIEW_DIR.rglob("*.csv"))
    paths.extend(EXPERIMENT_DATA_DIR.glob("*/review/review_progress.csv"))
    return collect_reviewed_ids(paths)


def learn_statistics(train):
    train = validate_features(train)
    rows = []
    for feature in FEATURES:
        values = train[feature]
        q01, q25, median, q75, q99 = values.quantile([.01, .25, .5, .75, .99], interpolation="linear")
        rows.append({"feature": feature, "train_orders": len(train), "q01_days": q01, "q25_days": q25,
                     "median_days": median, "q75_days": q75, "q99_days": q99, "iqr_days": q75 - q25,
                     "long_iqr_threshold_days": q75 + 1.5 * (q75 - q25),
                     "zero_train_orders": int(values.eq(0).sum())})
    return pd.DataFrame(rows)


def statistical_signals(frame, statistics):
    frame = validate_features(frame)
    thresholds = statistics.set_index("feature").loc[FEATURES]
    result = frame.copy()
    long_columns, short_columns = [], []
    for feature in FEATURES:
        long_column, short_column = f"{feature}_long_warning", f"{feature}_short_warning"
        result[long_column] = frame[feature].gt(thresholds.at[feature, "long_iqr_threshold_days"])
        result[short_column] = frame[feature].lt(thresholds.at[feature, "q01_days"]) | frame[feature].eq(0)
        long_columns.append(long_column)
        short_columns.append(short_column)
    result["long_duration_warning"] = result[long_columns].any(axis=1)
    result["short_duration_warning"] = result[short_columns].any(axis=1)
    return result


def candidate_id(transform, fraction):
    return f"if_{transform}_tail_{round(fraction * 100):02d}"


def fit_candidates(train):
    train = validate_features(train)
    models, cutoffs = {}, []
    for transform in TRANSFORMS:
        preprocessing = "passthrough" if transform == "raw" else FunctionTransformer(
            np.log1p, feature_names_out="one-to-one")
        pipeline = Pipeline([
            ("duration_transform", preprocessing),
            ("isolation_forest", IsolationForest(n_estimators=TREES, max_samples=256,
                                                contamination="auto", max_features=1.0,
                                                bootstrap=False, random_state=SEED, n_jobs=1)),
        ])
        pipeline.fit(train[FEATURES])
        scores = -pipeline.score_samples(train[FEATURES])
        thresholds = {}
        for fraction in TAIL_FRACTIONS:
            name = candidate_id(transform, fraction)
            threshold = float(np.quantile(scores, 1 - fraction, method="linear"))
            thresholds[name] = threshold
            cutoffs.append({"candidate": name, "transform": transform, "train_tail_fraction": fraction,
                            "score_threshold": threshold, "train_orders": len(train),
                            "train_flagged_orders": int((scores > threshold).sum())})
        models[transform] = {"pipeline": pipeline, "features": FEATURES, "score_thresholds": thresholds,
                             "score_definition": "negative sklearn score_samples; higher is more unusual; NOT probability",
                             "train_order_ids": train.order_id.tolist(), "transform": transform,
                             "random_state": SEED, "sklearn_version": sklearn.__version__}
    return models, pd.DataFrame(cutoffs)


def score_candidates(frame, models, statistics):
    frame = validate_features(frame)
    result = statistical_signals(frame, statistics)
    for transform, bundle in models.items():
        if bundle["features"] != FEATURES:
            raise ValueError("Saved model feature order differs")
        score = -bundle["pipeline"].score_samples(frame[FEATURES])
        result[f"if_{transform}_score"] = score
        for name, threshold in bundle["score_thresholds"].items():
            result[name] = score > threshold
    # No automatic anomaly/normal label: ML-only or short-duration evidence needs review.
    return result


def compare_candidates(train_scores, validation_scores, cutoffs):
    names = ["long_duration_warning", *cutoffs.candidate]
    rows = []
    for name in names:
        train_flags, validation_flags = train_scores[name], validation_scores[name]
        rows.append({"candidate": name, "train_orders": len(train_scores),
                     "train_flagged_orders": int(train_flags.sum()), "train_flagged_fraction": train_flags.mean(),
                     "validation_orders": len(validation_scores), "validation_flagged_orders": int(validation_flags.sum()),
                     "validation_flagged_fraction": validation_flags.mean(),
                     "validation_flagged_without_long_warning": int((validation_flags & ~validation_scores.long_duration_warning).sum()),
                     "validation_flagged_with_short_warning": int((validation_flags & validation_scores.short_duration_warning).sum())})
    overlaps = []
    for left, right in combinations(names, 2):
        first, second = validation_scores[left], validation_scores[right]
        union, intersection = int((first | second).sum()), int((first & second).sum())
        overlaps.append({"first_candidate": left, "second_candidate": right,
                         "validation_orders": len(validation_scores), "intersection": intersection, "union": union,
                         "jaccard": intersection / union if union else None})
    return pd.DataFrame(rows), pd.DataFrame(overlaps)


def prepare_blind_review(context, validation, reviewed_ids, size=REVIEW_SIZE):
    pool = context.loc[context.split.eq("validation") & context.order_id.isin(validation.order_id)
                       & ~context.order_id.isin(reviewed_ids) & ~context.previously_human_reviewed].sort_values("order_id")
    if not isinstance(size, int) or not 0 < size <= len(pool):
        raise ValueError("Review sample size must be positive and not exceed the eligible real cases")
    rng = np.random.default_rng(SEED)
    sample = pool.iloc[rng.choice(len(pool), size=size, replace=False)].copy().reset_index(drop=True)
    sample = sample.rename(columns={"order_id": "case_id"})
    sample["review_id"] = [f"V{number:04d}" for number in range(1, len(sample) + 1)]
    missing = sample[list(ACTIVITY_COLUMNS)].isna()
    sample["raw_missing_event_count"] = missing.sum(axis=1)
    sample["missing_activities"] = missing.apply(
        lambda row: "; ".join(ACTIVITY_COLUMNS[column] for column in ACTIVITY_COLUMNS if row[column]), axis=1)
    sample["delivery_vs_estimate_calendar_days"] = (
        sample.order_delivered_customer_date.dt.normalize() - sample.order_estimated_delivery_date.dt.normalize()).dt.days
    blind = build_blind_cases(sample)
    key = sample[["review_id", "case_id"]].copy()
    key["inclusion_probability"] = size / len(pool)
    key["sampling_weight"] = len(pool) / size
    design = {"method": "Simple random sampling without replacement from sorted eligible IDs, randomized display order",
              "population": "Validation timing-input cases without a previously recorded human label",
              "validation_timing_orders": len(validation), "eligible_orders": len(pool), "sample_orders": size,
              "excluded_previous_review_orders": len(validation) - len(pool),
              "seed": SEED, "rng": "numpy.default_rng/PCG64", "inclusion_probability": size / len(pool),
              "sampling_weight": len(pool) / size,
              "limitation": "Pilot workload, not a power calculation. May contain few anomalies. Not representative of excluded/incomplete cases or another marketplace."}
    return blind, key, design


def evaluation_plan():
    return {"selection_status": "awaiting_independent_validation_review", "selected_candidate": None,
            "accuracy_metrics_computed": False, "test_scored": False,
            "planned_metrics": ["confusion_matrix", "precision", "recall", "f1", "review_coverage"],
            "primary_comparison": "F1 on human Normal vs Anomaly labels, accompanied by precision/recall and class counts; never rank on alert volume alone.",
            "uncertain_labels": "Suspicious is unresolved, not silently Normal or Anomaly. Report excluded counts and coverage separately.",
            "insufficient_labels": "Do not select if both definite classes are not observed or evidence is too sparse. Expand independent review and report uncertainty.",
            "scope": "Compare detector review flags, not automatic business-anomaly decisions; IQR is a candidate, not ground truth.",
            "final_selection": "Requires reviewed validation evidence and an explicit decision. Freeze configuration before any test scoring.",
            "probability_calibration": "None. Neither scores, train-tail fractions nor warning rates are calibrated anomaly probabilities."}


def build_training(matrices, context, reviewed_ids, review_size=REVIEW_SIZE):
    if set(matrices) != {"train", "validation"}:
        raise ValueError("Training stage accepts train and validation only, not test")
    train, validation = (validate_features(matrices[name]) for name in ["train", "validation"])
    if set(train.order_id) & set(validation.order_id):
        raise ValueError("Train/validation overlap")
    statistics = learn_statistics(train)
    models, cutoffs = fit_candidates(train)
    train_scores = score_candidates(train, models, statistics)
    validation_scores = score_candidates(validation, models, statistics)
    comparison, overlaps = compare_candidates(train_scores, validation_scores, cutoffs)
    blind, key, review_design = prepare_blind_review(context, validation, reviewed_ids, review_size)
    process_columns = ["order_id", "order_status", *bed.audit.TIMES, "order_estimated_delivery_date",
                       "usage_group", "missing_milestone_count", "has_reversed_recorded_milestones",
                       "source_verification_priority", "timing_input_eligible", "timing_input_reason"]
    process = context.loc[context.split.eq("validation"), process_columns].sort_values("order_id").reset_index(drop=True)
    process["observation_basis"] = "retrospective_snapshot_not_historical_status"
    frames = {"train_statistics.csv": statistics, "score_thresholds.csv": cutoffs,
              "train_scores.csv": train_scores, "validation_scores.csv": validation_scores,
              "validation_comparison.csv": comparison, "validation_overlap.csv": overlaps,
              "validation_process_audit.csv": process,
              "validation_usage_summary.csv": process.groupby(["usage_group", "timing_input_reason"]).size().rename("orders").reset_index(),
              "review/review_cases_blind.csv": blind, "review/analyst_only/selection_key.csv": key}
    return frames, models, review_design


def render_readme(frames, review_design):
    thresholds = frames["train_statistics.csv"][["feature", "train_orders", "long_iqr_threshold_days", "q01_days"]].round(6)
    comparison = frames["validation_comparison.csv"][["candidate", "train_flagged_orders", "validation_orders", "validation_flagged_orders", "validation_flagged_fraction"]].round(4)
    return "\n\n".join([
        "# Bed bath table train-only timing candidates v1",
        "## Scope\nActual imported Olist observations from bed_bath_table_v1 only. The fit set has 5,078 cases and validation has 1,523 under the default frozen design. No fabricated rows, timestamps or labels, no global baseline reuse, and no source or existing model overwrite. Only three transition durations enter models. Negative/missing-duration cases remain in the linked cohort and validation_process_audit.csv, never imputed or labeled normal.",
        "## Train-only statistical reference\n" + bed.audit.markdown_table(thresholds),
        "For each transition in days: IQR = Q75 - Q25; long warning is duration > Q75 + 1.5*IQR. Short warning is duration < train Q01 OR duration == 0. Quantiles use linear interpolation on train only. The multiplier 1.5 and lower 1% tail are declared exploratory conventions, not validated business SLAs or universal optimal thresholds. All zeros and long tails remain in train. A short duration alone never creates an automatic anomaly label. A long warning also requires contextual validation.",
        "## Isolation Forest candidates\nTwo forests use 300 trees, max_samples=256, all three features, no bootstrap, random_state=42 and n_jobs=1. One uses raw days; the other uses log(1+days) to test sensitivity to right-skewed duration magnitudes. This transform preserves zeros and ordering and does not generate observations. No imputer or scaler is fitted. Log1p is a candidate, not assumed superior.",
        "Score = -pipeline.score_samples(X), so higher means more unusual. Models use contamination='auto', but their default predict/decision_function labels are NOT used here. Each fitted forest is paired with four custom score cutoffs: the TRAIN score Q97, Q95, Q92 and Q90. Candidate flags use strict score > cutoff, including ties consistently. These 3%, 5%, 8%, 10% train-tail settings are sensitivity choices, NOT measured prevalence or guaranteed false-positive rates. They calibrate on in-sample train scores; validation flag rates are free to differ. There are two fitted forests and eight operating configurations, not eight independent models.",
        "## Validation diagnostics\n" + bed.audit.markdown_table(comparison),
        "Counts and overlap/Jaccard describe agreement and review workload. They are NOT accuracy, precision, recall, F1, probability calibration, or evidence that one candidate is optimal. Statistical and ML signals use the same durations and are correlated; do not interpret their agreement as independent confirmation or multiply probabilities. Per-feature long/short warnings are contextual signals, not causal feature attribution for the forest. No ensemble or final anomaly/normal labels are produced.",
        "## Retained process evidence\nvalidation_process_audit.csv contains every validation-cohort order, including timing-ineligible and post-cutoff completions, with recorded status and original milestones. It is snapshot evidence, NOT historically available status at the model cutoff. Missing/reversed timestamps require separate source/process interpretation; no fabricated pending age or missing-event reconstruction is applied. This stage trains the timing branch, not a complete learned process/conformance/frequency detector.",
        "## Human review and selection\nA separate reviewer-facing review/README.md describes a random pilot sample. Labels start blank. Old human-reviewed IDs are excluded from sampling, but NOT from training on the basis of their labels. No existing human label or auto-generated label enters model fitting or diagnostic ranking. Sampling does not use any detector score, alert status, or preferred model. See evaluation_plan.json for the pre-review comparison policy. Models are candidates only; selection remains pending and no test predictions are produced.",
        "The reviewer should use review/review_cases_blind.csv or the existing review CLI, and avoid analyst_only, validation_scores.csv and other detector outputs until review is complete. SRS sampling represents eligible validation timing cases only; it does not establish performance for unresolved/reversed cases. A 120-case pilot can contain very few anomalies and may need expansion. Human interpretation is a reference with uncertainty, not automatically objective ground truth.",
        "## Leakage limits\nOnly train is passed to fit and quantile learning. Validation is passed only to fixed transforms/score_samples. The frozen test file is read as bytes for integrity checks but is NOT parsed as a feature matrix, fitted, scored or used for selection. The combined cohort is read for source validation and filtered immediately to train/validation; no test outcome summaries are made here. Earlier exploratory category selection and global detectors already inspected the imported history, so the future holdout is not claimed pristine. The parent snapshot's unknown extraction date, completion-boundary selection bias, final-status availability and marketplace limits still apply.",
        "## Saved artifacts\nmodels/if_raw.joblib and models/if_log1p.joblib contain the fitted pipeline, ordered features, thresholds and exact training IDs. statistics_model.json records formulas and train-derived values; score_thresholds.csv exposes every custom score cutoff. Use the saved transform and matching threshold for later inference, not pipeline.predict(). Only load trusted local joblib files; loading arbitrary serialized models is unsafe. Runtime and input/code/output hashes are recorded in manifest.json. No existing downstream entrypoint is automatically switched to these candidates.",
        "## Reproduce\nRun `.venv/bin/python scripts/03_anomaly_detection/train_bed_bath_table.py`. --source selects the frozen experiment, --output selects a new run directory, --review-size selects a declared pilot workload. Existing destinations are refused. There is deliberately no option to score test or select a winner in this script. Review progress is saved separately from immutable run outputs and is not included in the run's fixed output checksum list.",
        "## References\nThe score sign and distinction between score_samples, offset and prediction follow [scikit-learn IsolationForest](https://scikit-learn.org/stable/modules/generated/sklearn.ensemble.IsolationForest.html). The train-only fit workflow follows [scikit-learn data leakage guidance](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage).",
    ]) + "\n"


def write_training(frames, models, review_design, parents, review_sources,
                   source=bed.DEFAULT_OUTPUT, output=DEFAULT_OUTPUT):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f"Training run already exists: {output}")

    def check_sources():
        manifest = verify_experiment(source)
        current = {"experiment_manifest_sha256": bed.audit.file_hash(source / "manifest.json"),
                   "experiment_output_hashes": manifest["output_hashes"]}
        _, current_review = current_review_exposure()
        if current != parents or current_review != review_sources:
            raise ValueError("Source changed since training preparation")

    check_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            path = staging / filename
            path.parent.mkdir(parents=True, exist_ok=True)
            frame.to_csv(path, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "models").mkdir()
        for name, bundle in models.items():
            joblib.dump(bundle, staging / "models" / f"if_{name}.joblib", compress=3)
        plan = evaluation_plan()
        (staging / "evaluation_plan.json").write_text(json.dumps(plan, indent=2) + "\n")
        (staging / "statistics_model.json").write_text(json.dumps({
            "features": FEATURES, "fit_split": "train", "quantile_interpolation": "linear",
            "long_formula": "duration > Q75 + 1.5*(Q75-Q25)",
            "short_formula": "duration < Q01 or duration == 0; warning only",
            "statistics": frames["train_statistics.csv"].to_dict("records")}, indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(frames, review_design), encoding="utf-8")
        review_manifest = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "sampling_design": review_design,
                           "reviewed_sources": review_sources, "parent_experiment": parents,
                           "output_sha256": {"review_cases_blind.csv": bed.audit.file_hash(staging / "review/review_cases_blind.csv"),
                                             "analyst_only/selection_key.csv": bed.audit.file_hash(staging / "review/analyst_only/selection_key.csv")}}
        (staging / "review/analyst_only/manifest.json").write_text(json.dumps(review_manifest, indent=2) + "\n")
        review_text = (
            "# Bed bath table validation review\n\n"
            f"{review_design['sample_orders']} real validation orders selected randomly without replacement. All human fields are blank. "
            "Do not open analyst_only or model-score outputs before completing the review.\n\n"
            "Use recorded status, actual milestones and estimated delivery date. Review missing/reversed events, "
            "process timing and business context separately. Explain each decision and uncertainty. "
            "A short nonnegative duration or zero alone is a warning, not proof of anomaly. "
            "Delivery after the estimated calendar date is observed lateness, not by itself proof of every anomaly type. "
            "Use Suspicious when the context is insufficient; do not force a definite label. "
            "No verified source extraction cutoff or universal transition SLA is supplied. "
            "Do not infer pending age from today. Source timestamps and old labels must not be edited.\n\n"
            "Run from the project directory:\n\n```sh\n"
            f'.venv/bin/python scripts/03_anomaly_detection/status_context_manual_review.py --batch-dir "{output.resolve() / "review"}"\n'
            "```\n\nThe existing CLI can save, quit and resume. It writes review_progress.csv; the original blind file stays unchanged. "
            "Enter a label, confidence and reason yourself. No model prediction is shown. "
            "This is a validation pilot, not a final test set or a guaranteed adequate accuracy sample. "
            "The sample covers completed timing-eligible orders only; structural/incomplete cases need separate review.\n"
        )
        (staging / "review/README.md").write_text(review_text, encoding="utf-8")
        code_paths = [Path(__file__), Path(bed.__file__), Path(bed.usage.__file__),
                      ROOT / "scripts/03_anomaly_detection/order_status_context_audit.py",
                      ROOT / "scripts/03_anomaly_detection/prepare_status_context_review.py",
                      ROOT / "scripts/03_anomaly_detection/status_context_manual_review.py"]
        manifest = {"status": "complete", "training_version": "bed_bath_table_training_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "source": str(source.resolve()),
                    "parents": parents, "review_sources": review_sources, "review_design": review_design,
                    "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                                "sklearn": sklearn.__version__, "joblib": joblib.__version__},
                    "features": FEATURES, "transforms": TRANSFORMS, "train_tail_fractions": TAIL_FRACTIONS,
                    "trees": TREES, "seed": SEED, "fit_split": "train", "score_splits": ["train", "validation"],
                    "selection_status": plan["selection_status"], "selected_candidate": None,
                    "test_scored": False, "human_labels_created": False,
                    "row_counts": {name: len(frame) for name, frame in frames.items()},
                    "code_hashes": {str(p.relative_to(ROOT)): bed.audit.file_hash(p) for p in code_paths},
                    "output_hashes": {str(p.relative_to(staging)): bed.audit.file_hash(p)
                                      for p in sorted(staging.rglob("*")) if p.is_file()}}
        check_sources()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Training destination appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=bed.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--review-size", type=int, default=REVIEW_SIZE)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Training run already exists: {args.output}")
    print("Verifying frozen experiment; fitting on train only...", flush=True)
    matrices, context, parents = load_inputs(args.source)
    reviewed, review_sources = current_review_exposure()
    frames, models, design = build_training(matrices, context, reviewed, args.review_size)
    write_training(frames, models, design, parents, review_sources, args.source, args.output)
    print(frames["validation_comparison.csv"].to_string(index=False))
    print(frames["train_statistics.csv"].to_string(index=False))
    print(f"Selection pending human review; test NOT scored. Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
