"""Run a declared multi-method timing benchmark without tuning on the final test."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import tempfile

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import IsolationForest
from sklearn.neighbors import LocalOutlierFactor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import FunctionTransformer, RobustScaler
from sklearn.svm import OneClassSVM

import train_bed_bath_table as previous


FEATURES = previous.FEATURES
DEFAULT_OUTPUT = previous.EXPERIMENT_DATA_DIR / "bed_bath_table_benchmark_v1"
FIT_END = "2017-12-01"
TAIL_FRACTIONS = [.005, .01, .02, .03, .05, .08, .10, .15]
IQR_MULTIPLIERS = [1.5, 2.0, 3.0]
MAD_CUTOFFS = [3.0, 3.5, 4.5]
UPPER_QUANTILES = [.95, .975, .99, .995]
ENSEMBLE_ML = ["if_log1p_256", "lof_log1p_35", "ocsvm_log1p_nu05"]
MODEL_NAMES = ([f"if_{transform}_{samples}" for transform in ["raw", "log1p"] for samples in [256, 512]]
               + [f"lof_log1p_{neighbors}" for neighbors in [20, 35, 50]]
               + [f"ocsvm_log1p_nu{nu:02d}" for nu in [3, 5, 10]])
ENSEMBLE_NAMES = ["ensemble_rank_mean", "ensemble_rank_median", "hybrid_rank_mean_mad"]


def protocol():
    return {
        "version": "bed_bath_table_benchmark_v1", "features": FEATURES,
        "inner_fit_end_exclusive": FIT_END,
        "fit_rule": "Original train timing cases purchased and completed before fit end.",
        "calibration_rule": "Original train timing cases purchased at/after fit end; already complete before original train end.",
        "deferred_rule": "Earlier purchases completed at/after fit end stay excluded from inner fit/calibration; never reassigned by outcome.",
        "tail_fractions": TAIL_FRACTIONS, "iqr_multipliers": IQR_MULTIPLIERS,
        "mad_cutoffs": MAD_CUTOFFS, "upper_quantiles": UPPER_QUANTILES,
        "isolation_forest": {"transforms": ["raw", "log1p"], "max_samples": [256, 512], "trees": 300, "seed": 42},
        "lof": {"transform": "log1p", "scaler": "RobustScaler", "n_neighbors": [20, 35, 50], "novelty": True},
        "one_class_svm": {"transform": "log1p", "scaler": "RobustScaler", "nu": [.03, .05, .10], "gamma": "scale", "kernel": "rbf"},
        "ensemble_reference_models": ENSEMBLE_ML,
        "ensemble_formulas": {"ensemble_rank_mean": "mean of the three ML calibration-reference empirical CDF ranks",
                              "ensemble_rank_median": "median of those three ranks",
                              "hybrid_rank_mean_mad": "0.5*ML_mean_rank + 0.5*log1p_positive_MAD_rank"},
        "quantile_method": "linear", "threshold_comparison": "strict score > threshold; do not force alert counts across ties",
        "short_duration_policy": "Separate Q01/zero warning; no automatic business-anomaly label from short durations or model flags.",
        "evaluation": previous.evaluation_plan(),
        "search_limit": "One predeclared finite grid, not all possible formulas or proof of a global optimum. No adaptive retuning in this run.",
        "selection_policy": "Descriptive validation ranking only after completed independent review with both definite classes. No automatic final winner or test access.",
        "refinement_policy": "Further grids require a new version and a recorded rationale; never tune from final test results. Many comparisons on a small review sample risk validation overfitting.",
    }


def inner_split(matrices, context):
    if set(matrices) != {"train", "validation"}:
        raise ValueError("Benchmark accepts train and validation only")
    train = previous.validate_features(matrices["train"])
    validation = previous.validate_features(matrices["validation"])
    if set(train.order_id) & set(validation.order_id):
        raise ValueError("Train/validation overlap")
    rows = context.set_index("order_id").loc[train.order_id]
    cutoff = pd.Timestamp(FIT_END)
    early = rows.order_purchase_timestamp.lt(cutoff)
    observed = rows[previous.bed.audit.TIMES].lt(cutoff).all(axis=1)
    ledger = rows[["order_purchase_timestamp", *previous.bed.audit.TIMES[1:]]].reset_index()
    ledger["inner_role"] = np.where(~early.to_numpy(), "calibration",
                                     np.where(observed.to_numpy(), "fit", "deferred_at_inner_cutoff"))
    subsets = {name: train.loc[train.order_id.isin(ledger.loc[ledger.inner_role.eq(name), "order_id"])].reset_index(drop=True)
               for name in ["fit", "calibration"]}
    if len(subsets["fit"]) < 512 or len(subsets["calibration"]) < 200:
        raise ValueError("Insufficient real cases for this declared fit/calibration design")
    subsets["validation"] = validation
    return subsets, ledger


def fit_bank(fit):
    fit = previous.validate_features(fit)
    if len(fit) < 512:
        raise ValueError("This benchmark needs at least 512 fit cases")
    bank = {"features": FEATURES, "fit_ids": fit.order_id.tolist(), "statistics": {}, "models": {}}
    x = fit[FEATURES].to_numpy(dtype=float)
    for transform in ["raw", "log1p"]:
        values = x if transform == "raw" else np.log1p(x)
        q25, median, q75 = np.quantile(values, [.25, .5, .75], axis=0, method="linear")
        mad = np.median(np.abs(values - median), axis=0)
        iqr = q75 - q25
        if (mad <= 0).any() or (iqr <= 0).any():
            raise ValueError("Zero MAD/IQR: explicit feature policy required; do not invent an epsilon")
        bank["statistics"][transform] = {"q25": q25, "median": median, "q75": q75, "mad": mad, "iqr": iqr}
        for samples in [256, 512]:
            prep = "passthrough" if transform == "raw" else FunctionTransformer(np.log1p, feature_names_out="one-to-one")
            model = Pipeline([("transform", prep), ("detector", IsolationForest(
                n_estimators=300, max_samples=samples, contamination="auto", random_state=42, n_jobs=1))])
            model.fit(fit[FEATURES])
            bank["models"][f"if_{transform}_{samples}"] = model
    for neighbors in [20, 35, 50]:
        model = Pipeline([("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                          ("scale", RobustScaler()),
                          ("detector", LocalOutlierFactor(n_neighbors=neighbors, novelty=True, contamination="auto", n_jobs=1))])
        model.fit(fit[FEATURES])
        bank["models"][f"lof_log1p_{neighbors}"] = model
    for nu in [3, 5, 10]:
        model = Pipeline([("log1p", FunctionTransformer(np.log1p, feature_names_out="one-to-one")),
                          ("scale", RobustScaler()),
                          ("detector", OneClassSVM(nu=nu / 100, gamma="scale", kernel="rbf", max_iter=100000))])
        model.fit(fit[FEATURES])
        if model.named_steps["detector"].fit_status_ != 0:
            raise ValueError("One-Class SVM did not converge")
        bank["models"][f"ocsvm_log1p_nu{nu:02d}"] = model
    bank["upper_quantiles"] = {str(q): np.quantile(x, q, axis=0, method="linear") for q in UPPER_QUANTILES}
    bank["short_q01"] = np.quantile(x, .01, axis=0, method="linear")
    return bank


def base_scores(bank, frame):
    frame = previous.validate_features(frame)
    if bank["features"] != FEATURES:
        raise ValueError("Saved feature order differs")
    if set(frame.order_id) & set(bank["fit_ids"]):
        raise ValueError("Novelty scoring must not use fit cases; use unseen calibration/validation cases")
    result = frame[["order_id"]].copy()
    x = frame[FEATURES].to_numpy(dtype=float)
    for transform, stats in bank["statistics"].items():
        values = x if transform == "raw" else np.log1p(x)
        result[f"iqr_{transform}"] = np.maximum(0, ((values - stats["q75"]) / stats["iqr"]).max(axis=1))
        result[f"mad_{transform}"] = np.maximum(0, (0.6744897501960817 * (values - stats["median"]) / stats["mad"]).max(axis=1))
    for q, thresholds in bank["upper_quantiles"].items():
        result[f"upper_q{round(float(q)*1000):03d}"] = (x - thresholds).max(axis=1)
    for name, model in bank["models"].items():
        result[name] = -model.score_samples(frame[FEATURES])
    if not np.isfinite(result.drop(columns="order_id").to_numpy()).all():
        raise ValueError("Nonfinite score; do not silently exclude a case or method")
    return result


def add_ensembles(scores, references):
    result = scores.copy()
    ranks = {name: np.searchsorted(reference, scores[name].to_numpy(), side="right") / len(reference)
             for name, reference in references.items()}
    ml = np.column_stack([ranks[name] for name in ENSEMBLE_ML])
    result["ensemble_rank_mean"] = ml.mean(axis=1)
    result["ensemble_rank_median"] = np.median(ml, axis=1)
    result["hybrid_rank_mean_mad"] = .5 * ml.mean(axis=1) + .5 * ranks["mad_log1p"]
    return result


def learn_thresholds(calibration):
    rows = []
    for transform in ["raw", "log1p"]:
        for family, cutoffs in [("iqr", IQR_MULTIPLIERS), ("mad", MAD_CUTOFFS)]:
            for cutoff in cutoffs:
                rows.append({"candidate": f"{family}_{transform}_k{round(cutoff*10):02d}", "family": family,
                             "score_column": f"{family}_{transform}", "threshold": cutoff,
                             "threshold_origin": "fixed_multiplier_of_fit_statistics", "tail_fraction": None})
    for quantile in UPPER_QUANTILES:
        name = f"upper_q{round(quantile*1000):03d}"
        rows.append({"candidate": name, "family": "upper_quantile", "score_column": name,
                     "threshold": 0., "threshold_origin": "per_feature_fit_quantile", "tail_fraction": None})
    for name in [*MODEL_NAMES, *ENSEMBLE_NAMES]:
        family = ("ensemble" if name in ENSEMBLE_NAMES else "isolation_forest" if name.startswith("if_")
                  else "lof" if name.startswith("lof_") else "one_class_svm")
        for fraction in TAIL_FRACTIONS:
            rows.append({"candidate": f"{name}_tail{round(fraction*1000):03d}", "family": family,
                         "score_column": name, "threshold": float(np.quantile(calibration[name], 1-fraction, method="linear")),
                         "threshold_origin": "unseen_calibration_score_quantile", "tail_fraction": fraction})
    return pd.DataFrame(rows)


def apply_thresholds(scores, candidates):
    if scores.order_id.isna().any() or not scores.order_id.is_unique or not candidates.candidate.is_unique:
        raise ValueError("Invalid case or candidate IDs")
    if not np.isfinite(scores[candidates.score_column.unique()].to_numpy()).all() or not np.isfinite(candidates.threshold).all():
        raise ValueError("Nonfinite score/threshold")
    columns = {"order_id": scores.order_id}
    for row in candidates.itertuples(index=False):
        columns[row.candidate] = scores[row.score_column].gt(row.threshold)
    return pd.DataFrame(columns)


def build_benchmark(matrices, context):
    subsets, ledger = inner_split(matrices, context)
    bank = fit_bank(subsets["fit"])
    calibration_base = base_scores(bank, subsets["calibration"])
    bank["rank_references"] = {name: np.sort(calibration_base[name].to_numpy()) for name in [*ENSEMBLE_ML, "mad_log1p"]}
    calibration = add_ensembles(calibration_base, bank["rank_references"])
    validation = add_ensembles(base_scores(bank, subsets["validation"]), bank["rank_references"])
    candidates = learn_thresholds(calibration)
    bank["candidates"] = candidates
    bank["calibration_ids"] = subsets["calibration"].order_id.tolist()
    cal_flags, val_flags = (apply_thresholds(scores, candidates) for scores in [calibration, validation])
    diagnostic_rows = []
    for row in candidates.itertuples(index=False):
        diagnostic_rows.append({"candidate": row.candidate, "family": row.family,
                                "calibration_orders": len(cal_flags), "calibration_flags": int(cal_flags[row.candidate].sum()),
                                "validation_orders": len(val_flags), "validation_flags": int(val_flags[row.candidate].sum()),
                                "validation_flag_fraction": val_flags[row.candidate].mean(),
                                "validation_equal_threshold": int(validation[row.score_column].eq(row.threshold).sum())})
    stats_rows = []
    for transform, stats in bank["statistics"].items():
        for index, feature in enumerate(FEATURES):
            stats_rows.append({"transform": transform, "feature": feature,
                               **{name: float(values[index]) for name, values in stats.items()}})
    short = subsets["validation"].copy()
    for index, feature in enumerate(FEATURES):
        short[f"{feature}_short_warning"] = short[feature].lt(bank["short_q01"][index]) | short[feature].eq(0)
    short["short_duration_warning"] = short[[f"{feature}_short_warning" for feature in FEATURES]].any(axis=1)
    monthly = val_flags.merge(context[["order_id", "purchase_month"]], on="order_id", validate="one_to_one")
    monthly = monthly.groupby("purchase_month")[candidates.candidate.tolist()].sum().T.rename_axis("candidate").reset_index()
    process_columns = ["order_id", "usage_group", "timing_input_eligible", "timing_input_reason"]
    frames = {"inner_split_ledger.csv": ledger, "fit_statistics.csv": pd.DataFrame(stats_rows),
              "calibration_scores.csv": calibration, "validation_scores.csv": validation,
              "candidates.csv": candidates, "calibration_flags.csv": cal_flags, "validation_flags.csv": val_flags,
              "validation_diagnostics.csv": pd.DataFrame(diagnostic_rows), "validation_monthly_flags.csv": monthly,
              "validation_short_warnings.csv": short,
              "validation_scope_ledger.csv": context.loc[context.split.eq("validation"), process_columns].reset_index(drop=True)}
    design = {"fit_orders": len(subsets["fit"]), "calibration_orders": len(calibration),
              "inner_deferred_orders": int(ledger.inner_role.eq("deferred_at_inner_cutoff").sum()),
              "validation_orders": len(validation), "fitted_ml_models": len(bank["models"]),
              "candidate_configurations": len(candidates), "score_methods": len(validation.columns)-1,
              "test_scored": False, "selected_candidate": None}
    return frames, bank, design


def render_readme(frames, design):
    counts = frames["candidates.csv"].groupby("family").size().rename("configurations").reset_index()
    return "\n\n".join([
        "# Bed bath table multi-method benchmark v1",
        "## Scope\nAll observations come from the frozen real-data experiment. No synthetic cases, reconstructed events, generated human labels or old baseline scores are used. This benchmark extends the timing branch only, not a complete process/frequency detector. Incomplete or reversed timelines remain in the source cohort and validation_scope_ledger.csv. None is silently made normal.",
        "## Actual split\n```json\n" + json.dumps(design, indent=2) + "\n```",
        "The original chronological train is partitioned again by purchase time at 2017-12-01. Fit requires every actual milestone strictly before that date. Later train purchases form calibration and were already required to finish before 2018-03-01 by the parent experiment. Earlier purchases finishing after the inner cutoff are deferred, not reassigned. All stages keep their original order IDs. This completion-based availability restriction can underrepresent long unfinished cases and is explicitly reconciled in inner_split_ledger.csv.",
        "Every method sees the SAME inner fit cases; every score threshold based on a tail fraction uses the SAME unseen calibration cases; every comparison uses the SAME validation cases. Models/scalers/medians/IQR/MAD/per-feature quantiles fit only on inner fit. LOF novelty score_samples is never called on fit rows. No final-test matrix is parsed or scored. Old v1 training results used a different, larger fit cohort and must not be treated as a controlled head-to-head comparison with this benchmark.",
        "## Declared grid\n" + previous.bed.audit.markdown_table(counts),
        "protocol.json declares the finite grid and all parameters before human-label evaluation. This is not every possible formula and cannot establish a global optimum. IQR k=[1.5,2,3] and positive modified-MAD z=[3,3.5,4.5] are tested on raw and log1p durations. Upper per-feature fit quantiles are [0.95,0.975,0.99,0.995]. Statistical warnings are one-sided long-duration rules, not normality tests or a claim that 1% of orders are faulty.",
        "For transformed durations v_j, IQR score=max(0,max_j((v_j-Q75_j)/IQR_j)); positive MAD score=max(0,max_j(0.6744897501960817*(v_j-median_j)/MAD_j)). A zero MAD or IQR fails explicitly instead of inventing an epsilon. Each empirical-quantile score is max_j(x_j-Q_j), flagged if >0. These are any-transition rules; their total alert rate need not equal the per-feature tail probability.",
        "Four Isolation Forests combine raw/log1p with max_samples 256/512, 300 trees and seed 42. Three LOF novelty models use log1p, fit-only RobustScaler and 20/35/50 neighbors. Three One-Class SVMs use the same preprocessing, RBF gamma='scale' and nu=.03/.05/.10. Default model predict labels are not used. Their common score direction is -score_samples, higher meaning more unusual. SVM nu affects fitting and is NOT the same as the post-fit score-tail parameter. Training is not guaranteed free of anomalies; novelty assumptions and SVM contamination sensitivity may limit results.",
        "The 10 ML score methods and 3 ensembles each test calibration upper-tail fractions [.005,.01,.02,.03,.05,.08,.10,.15]. Threshold=linear calibration quantile(1-tail); flag=score>threshold. Ties are never broken to force a count. Calibration counts are descriptive, not a guaranteed future false-positive rate or anomaly prevalence. Quantile scores from different models are not directly comparable in their original units.",
        "## Ensemble hypotheses\nThe declared members are IF log1p/256, LOF/35 and SVM nu=.05. Each score becomes its empirical upper-inclusive CDF rank against its own calibration reference. We test mean(rank), median(rank), and 0.5*mean(ML ranks)+0.5*rank(log1p positive MAD). Ensemble thresholds are also calibration quantiles. Rank references use no validation outcomes; calibration self-ranks have finite-sample/tie effects. Ranks are NOT anomaly probabilities or conformal p-values. Members share features and are correlated, so agreement is not independent evidence. These are engineering hypotheses to compare, not claimed novel research or assumed improvements.",
        "## Diagnostics, not accuracy\nvalidation_diagnostics.csv and validation_monthly_flags.csv show alert workload and its variation over purchase months. They are not a quality leaderboard. validation_short_warnings.csv separately records durations below fit Q01 or equal to zero. All candidate outputs are review flags, never final business-anomaly labels; short duration alone remains only a warning. Physical status/process defects are not repaired or judged by the timing model.",
        "## Accuracy evaluation\nUse evaluate_bed_bath_table_benchmark.py with the existing blind validation review batch. It validates IDs, source facts and recorded human labels, computes metrics only when applicable, and reports missing labels as unavailable rather than zero accuracy. Partial review is progress only; ranking is withheld until the batch is complete and both definite classes are represented. Suspicious remains unresolved and its coverage is reported. F1 is the previously declared primary comparison, with precision, recall, specificity, balanced accuracy and ordinary accuracy shown alongside counts. Precision/recall intervals express sampling uncertainty; they do not correct human-label error, selection bias or multiple comparisons.",
        "No automatic final winner is selected. Comparing many candidates on a small pilot risks validation overfitting; any refinement must be versioned, declared and justified. A provisional best validation result is not a proven best method. Keep an independently reviewed final test for evaluation after configuration freeze, without feeding that result back into tuning. Prior all-history category selection/global experiments and unknown source cutoff remain limitations from the parent snapshot. Training/calibration/validation use this industry and marketplace only.",
        "## Reproduce\n`.venv/bin/python scripts/03_anomaly_detection/benchmark_bed_bath_table.py` creates a new immutable run. --source selects a compatible experiment; --output must not exist. Models, calibration reference scores, thresholds and training IDs are stored together in model_bank.joblib. Only load trusted serialized artifacts. manifest.json records source/code/output hashes and runtime versions. No background loop, final test predictions or existing-file overwrite is performed.",
        "## References\n[scikit-learn novelty/outlier methods](https://scikit-learn.org/stable/modules/outlier_detection.html) documents method assumptions and LOF's unseen-data scoring requirement. [NIST outlier guidance](https://www.itl.nist.gov/div898/handbook/eda/section3/eda35h.htm) describes modified MAD scores and cautions on interpretation; this benchmark explicitly uses an upper-tail adaptation. [scikit-learn threshold tuning](https://scikit-learn.org/stable/modules/classification_threshold.html) explains separate tuning data and metric choice.",
    ]) + "\n"


def write_benchmark(frames, bank, design, parents, source=previous.bed.DEFAULT_OUTPUT, output=DEFAULT_OUTPUT):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError(f"Benchmark already exists: {output}")

    def check_source():
        manifest = previous.verify_experiment(source)
        current = {"experiment_manifest_sha256": previous.bed.audit.file_hash(source / "manifest.json"),
                   "experiment_output_hashes": manifest["output_hashes"]}
        if parents != current:
            raise ValueError("Experiment changed since benchmark preparation")

    check_source()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        joblib.dump(bank, staging / "model_bank.joblib", compress=3)
        (staging / "protocol.json").write_text(json.dumps(protocol(), indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(frames, design), encoding="utf-8")
        manifest = {"status": "complete", "benchmark_version": "bed_bath_table_benchmark_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "source": str(source.resolve()),
                    "parents": parents, "design": design, "protocol": protocol(),
                    "runtime": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__,
                                "sklearn": sklearn.__version__, "joblib": joblib.__version__},
                    "code_hashes": {str(p.relative_to(previous.ROOT)): previous.bed.audit.file_hash(p)
                                    for p in [Path(__file__), Path(previous.__file__), Path(previous.bed.__file__)]},
                    "output_hashes": {p.name: previous.bed.audit.file_hash(p) for p in sorted(staging.iterdir())},
                    "human_labels_created": False, "test_scored": False, "selected_candidate": None}
        check_source()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Benchmark appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=previous.bed.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Benchmark already exists: {args.output}")
    print("Fitting declared methods and calibrating on unseen train-period cases...", flush=True)
    matrices, context, parents = previous.load_inputs(args.source)
    frames, bank, design = build_benchmark(matrices, context)
    write_benchmark(frames, bank, design, parents, args.source, args.output)
    print(json.dumps(design, indent=2))
    print(frames["validation_diagnostics.csv"].groupby("family").agg(
        configurations=("candidate", "size"), min_validation_flags=("validation_flags", "min"),
        max_validation_flags=("validation_flags", "max")).to_string())
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
