"""Evaluate fixed candidates against recorded blind validation reviews, never invented labels."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import scipy
from scipy.stats import binomtest

import benchmark_bed_bath_table as benchmark
import status_context_manual_review as review


previous = benchmark.previous
DEFAULT_REVIEW = previous.DEFAULT_OUTPUT / "review"
DEFAULT_OUTPUT = previous.EXPERIMENT_DATA_DIR / "bed_bath_table_benchmark_evaluation_v1"


def verify_benchmark(path):
    path = Path(path)
    manifest = json.loads((path / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("benchmark_version") != "bed_bath_table_benchmark_v1":
        raise ValueError("Expected a completed timing benchmark")
    required = {"candidates.csv", "validation_flags.csv", "protocol.json", "model_bank.joblib"}
    if not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Benchmark manifest omits required files")
    for filename, digest in manifest["output_hashes"].items():
        file = (path / filename).resolve()
        if not file.is_relative_to(path.resolve()) or not file.is_file() or previous.bed.audit.file_hash(file) != digest:
            raise ValueError(f"Benchmark hash mismatch: {filename}")
    return manifest


def source_hashes(benchmark_dir, review_dir):
    files = {"benchmark_manifest": Path(benchmark_dir) / "manifest.json",
             "review_manifest": Path(review_dir) / "analyst_only/manifest.json",
             "review_blind": Path(review_dir) / "review_cases_blind.csv",
             "selection_key": Path(review_dir) / "analyst_only/selection_key.csv"}
    hashes = {name: previous.bed.audit.file_hash(path) for name, path in files.items()}
    progress = Path(review_dir) / "review_progress.csv"
    hashes["review_progress"] = previous.bed.audit.file_hash(progress) if progress.exists() else None
    return hashes


def load_inputs(benchmark_dir=benchmark.DEFAULT_OUTPUT, review_dir=DEFAULT_REVIEW):
    benchmark_dir, review_dir = Path(benchmark_dir), Path(review_dir)
    before = source_hashes(benchmark_dir, review_dir)
    manifest = verify_benchmark(benchmark_dir)
    review_manifest = json.loads((review_dir / "analyst_only/manifest.json").read_text())
    if review_manifest.get("parent_experiment") != manifest["parents"]:
        raise ValueError("Review and benchmark must refer to the same frozen experiment")
    for filename, digest in review_manifest["output_sha256"].items():
        path = (review_dir / filename).resolve()
        if not path.is_relative_to(review_dir.resolve()) or not path.is_file() or previous.bed.audit.file_hash(path) != digest:
            raise ValueError(f"Review source hash mismatch: {filename}")
    progress = review.load_review(review_dir)
    candidates = pd.read_csv(benchmark_dir / "candidates.csv")
    flag_types = {"order_id": "string", **{name: "bool" for name in candidates.candidate}}
    flags = pd.read_csv(benchmark_dir / "validation_flags.csv", dtype=flag_types)
    if flags.order_id.isna().any() or not flags.order_id.is_unique:
        raise ValueError("Invalid prediction IDs")
    if list(flags.columns) != ["order_id", *candidates.candidate] or not candidates.candidate.is_unique:
        raise ValueError("Candidate schema differs from prediction columns")
    if not set(progress.case_id).issubset(set(flags.order_id)):
        raise ValueError("Review includes cases outside benchmark validation")
    key = pd.read_csv(review_dir / "analyst_only/selection_key.csv", dtype={"case_id": "string"})
    if not key.case_id.is_unique or set(key.case_id) != set(progress.case_id):
        raise ValueError("Review sampling IDs differ")
    probability = key.inclusion_probability.to_numpy(dtype=float)
    weights = key.sampling_weight.to_numpy(dtype=float)
    if (not np.isfinite(probability).all() or not np.isfinite(weights).all()
            or not ((probability > 0) & (probability <= 1)).all()
            or not np.allclose(probability, probability[0]) or not np.allclose(weights, 1 / probability)):
        raise ValueError("This evaluator requires the equal-probability review sample; unequal strata need weighted evaluation")
    _, context, parents = previous.load_inputs(Path(manifest["source"]))
    if parents != manifest["parents"]:
        raise ValueError("Benchmark source experiment changed")
    source = context.set_index("order_id").loc[progress.case_id]
    if not source.split.eq("validation").all() or not source.timing_input_eligible.all() or source.previously_human_reviewed.any():
        raise ValueError("Review cases do not match unreviewed timing-validation scope")
    for column in review.TIMESTAMP_COLUMNS:
        observed = pd.to_datetime(progress[column].replace("", pd.NA), errors="raise")
        if not np.array_equal(observed.to_numpy(), source[column].to_numpy(), equal_nan=True):
            raise ValueError(f"Review timestamps differ from source: {column}")
    for column in review.DURATION_FEATURES:
        values = pd.to_numeric(progress[column].replace("", pd.NA), errors="raise").to_numpy(dtype=float)
        if not np.allclose(values, source[column].to_numpy(), equal_nan=True, rtol=1e-12, atol=1e-12):
            raise ValueError(f"Review duration differs from source: {column}")
    if progress.order_status.tolist() != source.order_status.tolist():
        raise ValueError("Review status differs from source")
    after = source_hashes(benchmark_dir, review_dir)
    if before != after:
        raise ValueError("Review changed while evaluation inputs were being read")
    return candidates, flags, progress, before


def wilson(successes, total):
    if total == 0:
        return None, None
    interval = binomtest(successes, total).proportion_ci(confidence_level=.95, method="wilson")
    return float(interval.low), float(interval.high)


def binary_metrics(truth, predictions):
    truth, predictions = np.asarray(truth), np.asarray(predictions)
    if truth.dtype != bool or predictions.dtype != bool or truth.shape != predictions.shape or truth.ndim != 1:
        raise ValueError("Metrics require aligned one-dimensional boolean labels and flags")
    tp = int((truth & predictions).sum())
    fp = int((~truth & predictions).sum())
    fn = int((truth & ~predictions).sum())
    tn = int((~truth & ~predictions).sum())
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    specificity = tn / (tn + fp) if tn + fp else None
    p_low, p_high = wilson(tp, tp + fp)
    r_low, r_high = wilson(tp, tp + fn)
    return {"evaluated_cases": len(truth), "tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": precision, "recall": recall,
            "f1": 2*tp / (2*tp + fp + fn) if 2*tp + fp + fn else None,
            "specificity": specificity,
            "accuracy": (tp+tn) / len(truth) if len(truth) else None,
            "balanced_accuracy": (recall+specificity)/2 if recall is not None and specificity is not None else None,
            "precision_wilson95_low": p_low, "precision_wilson95_high": p_high,
            "recall_wilson95_low": r_low, "recall_wilson95_high": r_high}


def evaluate(candidates, flags, progress):
    if not candidates.candidate.is_unique or not flags.order_id.is_unique or flags.order_id.isna().any():
        raise ValueError("Duplicate or missing case/candidate IDs")
    if progress.case_id.isna().any() or not progress.case_id.is_unique:
        raise ValueError("Review must have unique nonmissing IDs")
    if not progress.reviewer_label.isin(["", "Normal", "Anomaly", "Suspicious"]).all():
        raise ValueError("Unsupported human label")
    if not set(progress.case_id).issubset(set(flags.order_id)):
        raise ValueError("Missing validation prediction for a review case")
    if not flags[candidates.candidate].isin([True, False]).all().all():
        raise ValueError("Predictions must be boolean")
    definite = progress.loc[progress.reviewer_label.isin(["Normal", "Anomaly"])]
    reviewed_count = int(progress.reviewer_label.ne("").sum())
    positives = int(definite.reviewer_label.eq("Anomaly").sum())
    negatives = int(definite.reviewer_label.eq("Normal").sum())
    complete = len(progress) > 0 and reviewed_count == len(progress)
    can_rank = complete and positives > 0 and negatives > 0
    status = ("awaiting_human_review" if reviewed_count == 0 else "partial_review_no_ranking" if not complete
              else "insufficient_definite_classes" if not can_rank else "provisional_validation_ranking")
    indexed = flags.set_index("order_id").loc[definite.case_id]
    truth = definite.reviewer_label.eq("Anomaly").to_numpy(dtype=bool)
    rows = []
    for candidate in candidates.itertuples(index=False):
        metrics = binary_metrics(truth, indexed[candidate.candidate].to_numpy(dtype=bool))
        rows.append({"candidate": candidate.candidate, "family": candidate.family,
                     "status": status, **metrics})
    metrics = pd.DataFrame(rows)
    metrics["validation_f1_rank"] = metrics.f1.rank(method="min", ascending=False).astype("Int64") if can_rank else pd.Series(pd.NA, index=metrics.index, dtype="Int64")
    # Always keep candidate order stable, including when accuracy is unavailable.
    summary = {"status": status, "review_sample_cases": len(progress), "reviewed_cases": reviewed_count,
               "unreviewed_cases": len(progress)-reviewed_count, "definite_anomaly_cases": positives,
               "definite_normal_cases": negatives, "suspicious_cases": int(progress.reviewer_label.eq("Suspicious").sum()),
               "definite_review_coverage": len(definite)/len(progress) if len(progress) else None,
               "ranking_available": can_rank, "selected_candidate": None, "test_scored": False,
               "best_observed_validation_candidates": metrics.loc[metrics.validation_f1_rank.eq(1).fillna(False), "candidate"].tolist(),
               "scope": "Resolved human labels in the blind timing-validation sample only, not all orders or unseen marketplaces.",
               "caveat": "Partial review may have stopping bias. Full review with two classes permits descriptive ranking, not proof of adequacy or a global optimum. Ties retained. No automatic final selection."}
    return metrics, summary


def write_evaluation(metrics, summary, hashes, benchmark_dir=benchmark.DEFAULT_OUTPUT,
                     review_dir=DEFAULT_REVIEW, output=DEFAULT_OUTPUT):
    benchmark_dir, review_dir, output = Path(benchmark_dir), Path(review_dir), Path(output)
    if output.exists():
        raise FileExistsError(f"Evaluation already exists: {output}")
    if hashes != source_hashes(benchmark_dir, review_dir):
        raise ValueError("Input changed since evaluation")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        metrics.to_csv(staging / "validation_metrics.csv", index=False)
        (staging / "review_status.json").write_text(json.dumps(summary, indent=2) + "\n")
        lines = ["# Benchmark validation evaluation", "",
                 "## Recorded review coverage", "```json", json.dumps(summary, indent=2), "```", "",
                 "## Interpretation",
                 "Metrics use only recorded Normal/Anomaly decisions from the linked blind validation batch. Suspicious is reported separately, never changed to Normal. Missing labels produce unavailable metric values, not fabricated zero accuracy. Partial review is diagnostic progress and cannot produce a ranking. Confidence labels do not silently remove unfavorable cases.", "",
                 "F1 is the preregistered primary descriptive comparison. Precision, recall, specificity, ordinary accuracy and balanced accuracy accompany all TP/FP/FN/TN counts. Ordinary accuracy alone can hide minority-class failures. Undefined denominators stay unavailable. Wilson 95% precision/recall intervals are pointwise binomial approximations; they omit finite-population correction and do not correct label error, unresolved-case bias or 120-way model comparison. No calibrated probability is produced.", "",
                 "The sample was selected without model scores and with equal inclusion probability. Metrics target the resolved reviewed subset; incomplete or Suspicious decisions can make inference to the whole eligible pool unreliable. A complete pilot with both classes can still be too small, especially with rare anomalies. Repeatedly adjusting formulas to this sample can overfit validation. Candidate rank is provisional, tied F1 values retain ties, and selected_candidate remains null even when ranks exist.", "",
                 "## Next step",
                 "Complete the existing blind review without consulting per-case detector results. Run the evaluator again to a NEW --output version. It reuses frozen predictions, does not retrain or change thresholds, and does not access final-test predictions. Review precision/recall tradeoffs, uncertainty and class counts before proposing a configuration freeze. Any new method or finer threshold grid requires a versioned experiment and explicit rationale, with the final test still reserved.", "",
                 "This is a technical evaluation record, not a claim of model accuracy when labels are absent. Earlier biased manual references and auto labels are deliberately not used as substitute ground truth.", "",
                 "## Reproduce", "```sh",
                 f'.venv/bin/python scripts/03_anomaly_detection/evaluate_bed_bath_table_benchmark.py --benchmark "{benchmark_dir.resolve()}" --review "{review_dir.resolve()}" --output NEW_EVALUATION_DIRECTORY',
                 "```", ""]
        (staging / "README.md").write_text("\n".join(lines), encoding="utf-8")
        manifest = {"status": "complete", "evaluation_version": "bed_bath_table_benchmark_evaluation_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "benchmark": str(benchmark_dir.resolve()), "review": str(review_dir.resolve()),
                    "input_hashes": hashes, "scipy_version": scipy.__version__, "review_summary": summary,
                    "code_hashes": {str(p.relative_to(previous.ROOT)): previous.bed.audit.file_hash(p)
                                    for p in [Path(__file__), Path(review.__file__), Path(benchmark.__file__)]},
                    "output_hashes": {p.name: previous.bed.audit.file_hash(p) for p in sorted(staging.iterdir())}}
        if hashes != source_hashes(benchmark_dir, review_dir):
            raise ValueError("Input changed during evaluation publication")
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Evaluation appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=benchmark.DEFAULT_OUTPUT)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Evaluation already exists: {args.output}")
    candidates, flags, progress, hashes = load_inputs(args.benchmark, args.review)
    metrics, summary = evaluate(candidates, flags, progress)
    write_evaluation(metrics, summary, hashes, args.benchmark, args.review, args.output)
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
