"""Compare frozen validation candidates descriptively, without selecting an accuracy winner."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import score_process_batch as batch


VERSION = "process_candidate_comparison_v1"
RULES = batch.training.automatic.TIMING_FLAGS
CASE_BOOLEANS = ["timing_input_eligible", "model_signals_available", *RULES]
CASE_COLUMNS = ["order_id", "split", "order_purchase_timestamp", "purchase_month", *CASE_BOOLEANS]
CANDIDATE_COLUMNS = ["candidate", "family", "score_column", "threshold", "threshold_origin", "tail_fraction"]


def verify_artifact(directory, version_key, version, required):
    directory = Path(directory)
    manifest = json.loads((directory / "manifest.json").read_text(), object_pairs_hook=batch.training.importer.unique_json_keys)
    if manifest.get("status") != "complete" or manifest.get(version_key) != version:
        raise ValueError(f"Expected a complete {version} artifact")
    if not set(required).issubset(manifest.get("output_hashes", {})):
        raise ValueError("Artifact manifest omits required outputs")
    for name, digest in manifest["output_hashes"].items():
        path = (directory / name).resolve()
        if not path.is_relative_to(directory.resolve()) or not path.is_file() or batch.audit.file_hash(path) != digest:
            raise ValueError(f"Artifact hash/path mismatch: {name}")
    return manifest


def require_ids(frame, name):
    if (frame.order_id.isna().any() or frame.order_id.eq("").any() or not frame.order_id.is_unique):
        raise ValueError(f"{name}: expected unique nonmissing order IDs")


def read_flags(path, candidates):
    frame = pd.read_csv(path, dtype="string", keep_default_na=False)
    if frame.columns.tolist() != ["order_id", *candidates.candidate]:
        raise ValueError("Candidate flag schema differs")
    require_ids(frame, "flags")
    for column in candidates.candidate:
        if not frame[column].isin(["True", "False"]).all():
            raise ValueError(f"Invalid or unavailable flag in scored population: {column}")
        frame[column] = frame[column].map({"True": True, "False": False}).astype(bool)
    return frame.sort_values("order_id").reset_index(drop=True)


def read_cases(path):
    frame = pd.read_csv(path, usecols=CASE_COLUMNS, dtype="string", keep_default_na=False)[CASE_COLUMNS]
    require_ids(frame, "cases")
    frame["order_purchase_timestamp"] = pd.to_datetime(frame.order_purchase_timestamp, format="%Y-%m-%d %H:%M:%S", errors="raise")
    for column in CASE_BOOLEANS:
        allowed = ["True", "False"] if column not in RULES else ["True", "False", ""]
        if not frame[column].isin(allowed).all():
            raise ValueError(f"Invalid case-check availability/value: {column}")
        frame[column] = frame[column].map({"True": True, "False": False}).astype("boolean")
    return frame.sort_values("order_id").reset_index(drop=True)


def validate_inputs(candidates, cases, flags, calibration):
    if (candidates.empty or candidates.columns.tolist() != CANDIDATE_COLUMNS
            or candidates.candidate.isna().any() or candidates.candidate.eq("").any() or not candidates.candidate.is_unique
            or candidates[["family", "score_column", "threshold_origin"]].isna().any().any()
            or not np.isfinite(candidates.threshold.to_numpy(dtype=float)).all()):
        raise ValueError("Invalid candidate definitions")
    if not candidates.tail_fraction.dropna().between(0, 1, inclusive="neither").all():
        raise ValueError("Invalid calibration tail fractions")
    require_ids(cases, "cases")
    if cases.empty or not cases.split.eq("validation").all():
        raise ValueError("Comparison requires validation cases only; no test or fitting cases")
    months = cases.order_purchase_timestamp.dt.to_period("M").astype("string")
    if cases.order_purchase_timestamp.isna().any() or not cases.purchase_month.eq(months).all():
        raise ValueError("Purchase-month metadata disagrees with actual purchase timestamps")
    for column in ["timing_input_eligible", "model_signals_available"]:
        if cases[column].isna().any() or not cases[column].isin([True, False]).all():
            raise ValueError("Case scoring availability must be explicit")
    if not cases.model_signals_available.eq(cases.timing_input_eligible).all():
        raise ValueError("Batch score availability and timing eligibility disagree")
    eligible_ids = set(cases.loc[cases.timing_input_eligible, "order_id"])
    for name, frame in [("flags", flags), ("calibration", calibration)]:
        require_ids(frame, name)
        if frame.columns.tolist() != ["order_id", *candidates.candidate] or not frame[candidates.candidate].isin([True, False]).all().all():
            raise ValueError("Comparison flags must be complete booleans with the exact candidate schema")
    if set(flags.order_id) != eligible_ids or set(flags.order_id) & set(calibration.order_id):
        raise ValueError("Scored-case scope mismatch or calibration/validation overlap")
    for rule in RULES:
        if not cases[rule].dropna().isin([True, False]).all():
            raise ValueError("Invalid timing rule signal")
        if cases.loc[~cases.timing_input_eligible, rule].notna().any():
            raise ValueError("Unscored timing rules must remain unavailable")
        if rule != "late_delivery_warning" and cases.loc[cases.timing_input_eligible, rule].isna().any():
            raise ValueError("Missing observed timing rule on an eligible case")


def verify_score_flags(path, flags, candidates):
    scores = pd.read_csv(path, dtype={"order_id": "string"}, float_precision="round_trip", keep_default_na=False)
    require_ids(scores, "scores")
    scores = scores.sort_values("order_id").reset_index(drop=True)
    if not set(candidates.score_column).issubset(scores.columns) or not np.isfinite(scores.drop(columns="order_id").to_numpy(dtype=float)).all():
        raise ValueError("Missing or nonfinite saved score")
    expected = batch.training.engine.apply_thresholds(scores, candidates)
    pd.testing.assert_frame_equal(flags, expected, check_dtype=False, check_exact=True)


def load_inputs(batch_dir):
    batch_dir = Path(batch_dir)
    batch_hash = batch.audit.file_hash(batch_dir / "manifest.json")
    manifest = verify_artifact(batch_dir, "batch_version", batch.VERSION,
                               ["candidates.csv", "candidate_flags.csv", "candidate_scores.csv", "case_results.csv", "batch_config.json", "summary.json"])
    batch.verify_sources(manifest["provenance"])
    config = json.loads((batch_dir / "batch_config.json").read_text())
    start, end, cutoff = batch.validate_config(config)
    if config["mode"] != "validation_replay" or manifest["summary"]["mode"] != "validation_replay":
        raise ValueError("Candidate comparison accepts the declared validation replay only, not deployment/test batches")
    model_dir = Path(manifest["provenance"]["model_directory"])
    model_hash = batch.audit.file_hash(model_dir / "manifest.json")
    model = verify_artifact(model_dir, "training_version", batch.training.VERSION,
                            ["candidates.csv", "calibration_flags.csv", "calibration_scores.csv", "validation_flags.csv", "auto_case_results.csv"])
    if model_hash != manifest["provenance"]["model_manifest_sha256"] or model["identity"] != manifest["summary"]["identity"]:
        raise ValueError("Model and batch provenance/identity differ")
    candidates = pd.read_csv(batch_dir / "candidates.csv", float_precision="round_trip")
    pd.testing.assert_frame_equal(candidates, pd.read_csv(model_dir / "candidates.csv", float_precision="round_trip"), check_exact=True)
    flags = read_flags(batch_dir / "candidate_flags.csv", candidates)
    calibration = read_flags(model_dir / "calibration_flags.csv", candidates)
    cases = read_cases(batch_dir / "case_results.csv")
    validate_inputs(candidates, cases, flags, calibration)
    verify_score_flags(batch_dir / "candidate_scores.csv", flags, candidates)
    verify_score_flags(model_dir / "calibration_scores.csv", calibration, candidates)
    saved_cases = read_cases(model_dir / "auto_case_results.csv")
    expected_cases = saved_cases.loc[saved_cases.split.eq("validation")
                                   & saved_cases.order_purchase_timestamp.ge(start)
                                   & saved_cases.order_purchase_timestamp.lt(end)].reset_index(drop=True)
    pd.testing.assert_frame_equal(cases, expected_cases, check_exact=True)
    saved_flags = read_flags(model_dir / "validation_flags.csv", candidates)
    expected_flags = saved_flags.loc[saved_flags.order_id.isin(cases.order_id)].reset_index(drop=True)
    pd.testing.assert_frame_equal(flags, expected_flags, check_exact=True)
    if (len(cases) != manifest["summary"]["batch_cases"] or len(flags) != manifest["summary"]["scored_cases"]
            or len(candidates) != manifest["summary"]["candidate_configurations"]):
        raise ValueError("Batch summary and case/candidate counts disagree")
    provenance = {"batch_directory": str(batch_dir.resolve()), "batch_manifest_sha256": batch_hash,
                  "batch_output_hashes": manifest["output_hashes"], "upstream": manifest["provenance"],
                  "identity": model["identity"], "observation_cutoff_exclusive": str(cutoff),
                  "code_sha256": batch.audit.file_hash(Path(__file__)), "runtime": batch.training.runtime()}
    verify_sources(provenance)
    return candidates, cases, flags, calibration, provenance


def verify_sources(provenance):
    batch.verify_sources(provenance["upstream"])
    root = Path(provenance["batch_directory"])
    checks = [(root / "manifest.json", provenance["batch_manifest_sha256"]), (Path(__file__), provenance["code_sha256"])]
    checks.extend((root / name, digest) for name, digest in provenance["batch_output_hashes"].items())
    for path, digest in checks:
        if batch.audit.file_hash(path) != digest:
            raise ValueError(f"Comparison input/code changed: {path}")


def monthly_comparison(candidates, cases, flags):
    rows, coverage = [], []
    for month, group in cases.groupby("purchase_month", sort=True):
        selected = flags.loc[flags.order_id.isin(group.order_id)]
        coverage.append({"purchase_month": month, "all_cases": len(group), "scored_cases": len(selected),
                         "unscored_cases": len(group)-len(selected), "scoring_coverage": len(selected)/len(group)})
        for candidate in candidates.itertuples(index=False):
            count = int(selected[candidate.candidate].sum())
            rows.append({"candidate": candidate.candidate, "family": candidate.family, "purchase_month": month,
                         "all_cases": len(group), "scored_cases": len(selected), "flagged_cases": count,
                         "flag_fraction_of_scored": count/len(selected) if len(selected) else None,
                         "flag_fraction_of_all_cases": count/len(group),
                         "denominator_note": "Unscored cases are unknown, not negative findings."})
    return pd.DataFrame(rows), pd.DataFrame(coverage)


def observed_equivalence(candidates, flags):
    columns = ["group_id", "candidate_count", "members_json", "scored_cases", "flagged_cases"]
    if flags.empty:
        return pd.DataFrame(columns=columns), {}
    groups = {}
    for name in sorted(candidates.candidate):
        signature = flags[name].to_numpy(dtype=bool).tobytes()
        groups.setdefault(signature, []).append(name)
    rows, membership = [], {}
    for index, names in enumerate(groups.values(), start=1):
        group_id = f"observed_set_{index:03d}"
        membership.update({name: group_id for name in names})
        rows.append({"group_id": group_id, "candidate_count": len(names), "members_json": json.dumps(names),
                     "scored_cases": len(flags), "flagged_cases": int(flags[names[0]].sum())})
    return pd.DataFrame(rows, columns=columns), membership


def pairwise_comparison(candidates, flags):
    names = sorted(candidates.candidate)
    definitions = candidates.set_index("candidate")
    values = flags[names].to_numpy(dtype=np.int64)
    counts, intersections = values.sum(axis=0), values.T @ values
    rows = []
    for i, j in combinations(range(len(names)), 2):
        both = int(intersections[i, j])
        left_only, right_only = int(counts[i])-both, int(counts[j])-both
        union = both+left_only+right_only
        rows.append({"left_candidate": names[i], "right_candidate": names[j], "scored_cases": len(flags),
                     "same_family": definitions.at[names[i], "family"] == definitions.at[names[j], "family"],
                     "same_score_column": definitions.at[names[i], "score_column"] == definitions.at[names[j], "score_column"],
                     "both_flag": both, "left_only": left_only, "right_only": right_only,
                     "neither_flag": len(flags)-union, "union_flags": union,
                     "jaccard_overlap": both/union if union else None,
                     "identical_observed_flags": left_only+right_only == 0 if len(flags) else None})
    return pd.DataFrame(rows)


def rule_comparison(candidates, cases, flags):
    aligned = cases.set_index("order_id").loc[flags.order_id]
    rows = []
    for name in candidates.candidate:
        predicted = flags[name].to_numpy(dtype=bool)
        for rule in RULES:
            known = aligned[rule].notna().to_numpy()
            signal = aligned[rule].fillna(False).to_numpy(dtype=bool)[known]
            selected = predicted[known]
            both, union = int((selected & signal).sum()), int((selected | signal).sum())
            rows.append({"candidate": name, "rule": rule, "compared_cases": int(known.sum()),
                         "rule_unavailable_scored_cases": int((~known).sum()), "both_flag": both,
                         "candidate_only": int((selected & ~signal).sum()), "rule_only": int((~selected & signal).sum()),
                         "neither_flag": int((~selected & ~signal).sum()), "jaccard_overlap": both/union if union else None})
    return pd.DataFrame(rows)


def threshold_sensitivity(candidates, flags, calibration):
    rows = []
    for score_column, group in candidates.groupby("score_column", sort=True):
        ordered = group.sort_values(["threshold", "candidate"]).to_dict("records")
        for loose, strict in zip(ordered, ordered[1:]):
            left, right = loose["candidate"], strict["candidate"]
            row = {"score_column": score_column, "looser_candidate": left, "stricter_candidate": right,
                   "looser_threshold": loose["threshold"], "stricter_threshold": strict["threshold"],
                   "equal_thresholds": loose["threshold"] == strict["threshold"]}
            for prefix, frame in [("validation", flags), ("calibration", calibration)]:
                if (frame[right] & ~frame[left]).any():
                    raise ValueError("Higher cutoff cannot add flags for the same saved score")
                removed = int((frame[left] & ~frame[right]).sum())
                if row["equal_thresholds"] and removed:
                    raise ValueError("Identical score cutoffs must give identical flags")
                row.update({f"{prefix}_scored_cases": len(frame), f"{prefix}_looser_flags": int(frame[left].sum()),
                            f"{prefix}_stricter_flags": int(frame[right].sum()), f"{prefix}_removed_flags": removed,
                            f"{prefix}_decrease_pp": 100*removed/len(frame) if len(frame) else None,
                            f"{prefix}_observed_plateau": removed == 0 if len(frame) else None})
            rows.append(row)
    return pd.DataFrame(rows)


def build_outputs(candidates, cases, flags, calibration):
    validate_inputs(candidates, cases, flags, calibration)
    cases = cases.sort_values("order_id").reset_index(drop=True)
    flags = flags.sort_values("order_id").reset_index(drop=True)
    candidates = candidates.sort_values("candidate").reset_index(drop=True)
    monthly, coverage = monthly_comparison(candidates, cases, flags)
    equivalents, membership = observed_equivalence(candidates, flags)
    rows = []
    for candidate in candidates.to_dict("records"):
        name = candidate["candidate"]
        by_month = monthly.loc[monthly.candidate.eq(name) & monthly.scored_cases.gt(0)]
        count, cal_count = int(flags[name].sum()), int(calibration[name].sum())
        rate, cal_rate = count/len(flags) if len(flags) else None, cal_count/len(calibration) if len(calibration) else None
        enough = len(by_month) >= 2
        rates = by_month.flag_fraction_of_scored.to_numpy(dtype=float)
        weighted_sd = float(np.sqrt(np.average((rates-rate)**2, weights=by_month.scored_cases))) if enough else None
        rows.append({**candidate, "scored_cases": len(flags), "flagged_cases": count, "flag_fraction_of_scored": rate,
                     "all_cases": len(cases), "unscored_cases": len(cases)-len(flags),
                     "calibration_cases": len(calibration), "calibration_flags": cal_count,
                     "calibration_flag_fraction": cal_rate,
                     "validation_minus_calibration_pp": 100*(rate-cal_rate) if rate is not None and cal_rate is not None else None,
                     "months_with_scored_cases": len(by_month), "months_without_scored_cases": len(coverage)-len(by_month),
                     "monthly_rate_min": float(rates.min()) if len(rates) else None,
                     "monthly_rate_max": float(rates.max()) if len(rates) else None,
                     "monthly_rate_range_pp": 100*float(rates.max()-rates.min()) if enough else None,
                     "monthly_rate_weighted_sd_pp": 100*weighted_sd if enough else None,
                     "observed_behavior": "no_scored_cases" if not len(flags) else
                                          "no_flags_observed" if not count else
                                          "all_scored_cases_flagged" if count == len(flags) else "mixed_flags",
                     "observed_equivalence_group": membership.get(name)})
    comparison = pd.DataFrame(rows)
    family = comparison.groupby("family", sort=True).agg(
        configurations=("candidate", "size"), score_columns=("score_column", "nunique"),
        minimum_flags=("flagged_cases", "min"), maximum_flags=("flagged_cases", "max"),
        minimum_flag_fraction=("flag_fraction_of_scored", "min"), maximum_flag_fraction=("flag_fraction_of_scored", "max"),
        minimum_monthly_range_pp=("monthly_rate_range_pp", "min"), maximum_monthly_range_pp=("monthly_rate_range_pp", "max"),
    ).reset_index()
    pairwise = pairwise_comparison(candidates, flags)
    frames = {"candidate_comparison.csv": comparison, "monthly_candidate_rates.csv": monthly,
              "monthly_coverage.csv": coverage, "family_summary.csv": family,
              "observed_equivalence_groups.csv": equivalents, "pairwise_overlap.csv": pairwise,
              "threshold_sensitivity.csv": threshold_sensitivity(candidates, flags, calibration),
              "candidate_rule_overlap.csv": rule_comparison(candidates, cases, flags)}
    summary = {"candidate_configurations": len(candidates), "score_columns": candidates.score_column.nunique(),
               "families": candidates.family.nunique(), "validation_cases": len(cases), "scored_cases": len(flags),
               "unscored_cases": len(cases)-len(flags), "calibration_cases": len(calibration),
               "purchase_months": coverage.purchase_month.tolist(), "pairwise_comparisons": len(pairwise),
               "distinct_observed_flag_sets": len(equivalents) if len(flags) else None,
               "multi_candidate_equivalence_groups": int(equivalents.candidate_count.gt(1).sum()),
               "candidates_without_flags": int(comparison.observed_behavior.eq("no_flags_observed").sum()),
               "minimum_flags": int(comparison.flagged_cases.min()), "maximum_flags": int(comparison.flagged_cases.max()),
               "selection_status": "descriptive_comparison_only", "selected_candidate": None,
               "accuracy_ranking_available": False, "accuracy_computed": False, "anomaly_probabilities_computed": False,
               "human_labels_created": False, "test_scored": False, "training_performed": False,
               "thresholds_updated": False, "model_deserialized": False,
               "independent_new_data_evaluation": False}
    return frames, summary


def render_readme(frames, summary):
    return "\n\n".join([
        "# Frozen candidate comparison",
        "## Scope\n```json\n" + json.dumps(summary, indent=2) + "\n```",
        "All results describe already-used validation cases. The saved calibration population was used to set many score cutoffs and is NOT independent evaluation. No fitting, model deserialization, threshold search, final-test scoring, new labels or winning configuration is performed. Candidate order is lexical, not a quality ranking.",
        "## Monthly coverage\n" + batch.audit.markdown_table(frames["monthly_coverage.csv"]),
        "Each month uses the original fixed validation event cutoff, not its own month-end snapshot. Later purchase cohorts have less follow-up; unavailable/unfinished cases are not negatives. Coverage, case mix and censoring can change observed warning rates. Do not infer better performance from fewer flags or shorter completed-case durations.",
        "## Method families\n" + batch.audit.markdown_table(frames["family_summary.csv"]),
        "These are configuration ranges, not independent model votes, confidence or accuracy. Comparing a raw score/cutoff magnitude across different score columns is invalid. Empty/constant-output configurations are not automatically reliable or optimal.",
        "## Formulas\nFor month m, r_m = flagged_m / scored_m (blank when scored_m = 0). Overall r = sum(flagged_m) / sum(scored_m), not the unweighted monthly average. Monthly range = 100*(max r_m - min r_m) percentage points. Weighted monthly SD = 100*sqrt(sum(scored_m*(r_m-r)^2)/sum(scored_m)). Range/SD are blank with fewer than two scored months. These are observed warning-rate variation, not retraining stability, a significance test or model accuracy. Validation-minus-calibration = 100*(validation rate - calibration rate).",
        "Pairwise Jaccard = both_flag / union_flags; a zero union is undefined and remains blank, including when both configurations flag nothing. neither_flag counts agreements among scored cases only. Equivalence groups have exactly the same flag vector on these scored cases; this does NOT make models, fitted distributions, calibration outcomes or future predictions equivalent. With no scored cases, equivalence/identical-flag conclusions remain unavailable.",
        "## Sensitivity and rule overlap\nthreshold_sensitivity.csv compares adjacent numeric cutoffs of the SAME score column. Raising a strict score > cutoff threshold cannot add flagged cases; same-threshold ties must match exactly. A plateau means no observed case changes decision, not no underlying model difference. Score columns with only one candidate have no adjacent cutoff comparison. candidate_rule_overlap.csv compares recorded timing warnings and detector flags on their common applicable cases. Rules share duration information with detectors and are not independent ground truth; neither overlap nor agreement is accuracy/precision/recall/F1.",
        "## Files and decisions\ncandidate_comparison.csv has all candidates, operational flag volume, calibration comparison, monthly variability and observed equivalence membership. monthly_candidate_rates.csv includes all/scored denominators. pairwise_overlap.csv gives all unordered candidate pairs. No candidate is removed or auto-selected, and no arbitrary weighted 'best score' is introduced. Choose operational criteria explicitly before a final test; an accuracy claim additionally needs an appropriate independent reference. Another manual-label batch is not a prerequisite for running this comparison.",
        "All observations remain real imported Olist development records. Parent manifests, every output and relevant source/code hashes are verified. Results are written to a new immutable directory. Hash checks establish integrity, not scientific correctness or independence of the underlying labels.",
    ]) + "\n"


def write_outputs(frames, summary, provenance, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite candidate comparison: {output}")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False)
        (staging / "summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False) + "\n")
        (staging / "README.md").write_text(render_readme(frames, summary), encoding="utf-8")
        manifest = {"status": "complete", "comparison_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "summary": summary,
                    "output_hashes": {path.name: batch.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        verify_sources(provenance)
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Comparison output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    try:
        candidates, cases, flags, calibration, provenance = load_inputs(args.batch)
        frames, summary = build_outputs(candidates, cases, flags, calibration)
        write_outputs(frames, summary, provenance, args.output)
    except (ValueError, OSError, AssertionError) as exc:
        parser.exit(2, f"Candidate comparison stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
