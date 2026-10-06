"""Freeze a real-data, blinded pilot; sampling decisions stay analyst-only."""

import argparse
from datetime import datetime, timezone
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import train_business_ablation as ablation
import prepare_status_context_review as legacy
import review_business_cases as review


ROOT = review.ROOT
TEMPLATE = ROOT/"templates/business_review_v1"
PROTOCOL = TEMPLATE/"sampling_protocol.json"
STRATA = ["purchase_month", "agreement_group"]


def protocol():
    return {"version": review.VERSION, "sample_size": 120, "seed": 42,
            "anchor_candidate": "ensemble_rank_mean_tail050", "profiles": list(ablation.PROFILES),
            "strata": STRATA, "allocation": "equal_capped_in_lexicographic_stratum_order",
            "within_stratum": "simple_random_without_replacement", "display_order": "random_permutation_after_sampling",
            "population": "common_validation_cases_without_recorded_prior_human_labels",
            "anchor_status": "engineering_sampling_anchor_after_exploration_not_selected_best_model",
            "sample_size_status": "workflow_pilot_not_approved_power_or_precision_calculation",
            "criteria_status": "pending_advisor", "human_labels_created": False, "test_scored": False}


def exposure():
    paths = list((ROOT/"data/manual_review").rglob("*.csv"))
    paths.extend((ROOT/"data/experiments").glob("*/review/review_progress.csv"))
    ids, sources = legacy.collect_reviewed_ids(paths)
    # New blinded sessions use pseudonyms, so resolve only their recorded judgments through the analyst key.
    for directory in sorted((ROOT/"data/manual_review").glob("*/sessions/*")):
        if directory.name.startswith(".") or not directory.is_dir():
            continue
        batch = directory.parent.parent
        frames, evidence_hash = review.load_evidence(batch)
        metadata = review.read_json(directory/"session.json")
        if (metadata["evidence_manifest_sha256"] != evidence_hash
                or review.digest(directory/"approved_criteria.json") != metadata["approved_criteria_sha256"]):
            raise ValueError("Previous review session binding changed")
        review.validate_criteria(review.read_json(directory/"approved_criteria.json"))
        progress = review.validate_progress(review.read_strings(directory/"review_progress.csv"), frames["cases.csv"].review_id)
        answered = progress.reviewer_label.ne("")
        if not answered.any():
            continue
        manifest = review.read_json(batch/"analyst_only/manifest.json")
        key_path = batch/"analyst_only/selection_key.csv"
        if review.digest(key_path) != manifest["output_hashes"]["analyst_only/selection_key.csv"]:
            raise ValueError("Previous review selection key changed")
        key = pd.read_csv(key_path, dtype={"order_id": "string", "review_id": "string"})
        if key.review_id.tolist() != frames["cases.csv"].review_id.tolist() or not key.order_id.is_unique:
            raise ValueError("Previous review IDs differ")
        ids.update(key.loc[answered.to_numpy(), "order_id"])
        for path in [key_path, directory/"review_progress.csv", directory/"session.json", directory/"approved_criteria.json"]:
            sources.append({"path": str(path.relative_to(ROOT)), "sha256": review.digest(path)})
    return ids, sources


def code_hashes():
    files = [Path(__file__), Path(review.__file__), Path(legacy.__file__), PROTOCOL,
             TEMPLATE/"criteria_draft.json", TEMPLATE/"README.md"]
    return {str(path.relative_to(ROOT)): review.digest(path) for path in files}


def load_inputs(study=ablation.DEFAULT_OUTPUT):
    study = Path(study).resolve()
    manifest_hash = review.digest(study/"manifest.json")
    manifest = review.read_json(study/"manifest.json")
    if (manifest.get("status") != "complete" or manifest.get("study_version") != ablation.VERSION
            or manifest.get("identity") != ablation.business.IDENTITY or manifest.get("code_hashes") != ablation.code_hashes()):
        raise ValueError("Expected the unchanged completed paired business ablation")
    ablation.verify_files(study, manifest)
    ablation.verify_sources(manifest["provenance"])
    if review.read_json(PROTOCOL) != protocol():
        raise ValueError("Sampling protocol differs; change version explicitly, not the saved pilot")
    features_dir = Path(manifest["provenance"]["features_directory"])
    features, parent = ablation.business.load_feature_snapshot(features_dir)
    read = ablation.business.usage.read_snapshot_table
    ledger = read(study, "scope_ledger.csv", manifest)
    population = ablation.ordered(ledger.loc[ledger.analysis_role.eq("validation"), ["order_id", "split", "order_purchase_timestamp"]])
    if (population.empty or not population.order_id.is_unique or not population.split.eq("validation").all()
            or not population.order_purchase_timestamp.between(pd.Timestamp("2018-03-01"), pd.Timestamp("2018-06-01"), inclusive="left").all()):
        raise ValueError("Invalid common validation scope")
    for profile in ablation.PROFILES:
        scores = read(study, f"{profile}_validation_scores.csv", manifest)
        flags = read(study, f"{profile}_validation_flags.csv", manifest)
        candidates = read(study, f"{profile}_candidates.csv", manifest)
        pd.testing.assert_frame_equal(flags, ablation.training.engine.apply_thresholds(scores, candidates), check_dtype=False)
        if flags.order_id.tolist() != population.order_id.tolist():
            raise ValueError("Paired validation populations differ")
        anchor = candidates.loc[candidates.candidate.eq(protocol()["anchor_candidate"])]
        if len(anchor) != 1 or anchor.iloc[0].score_column != "ensemble_rank_mean" or anchor.iloc[0].tail_fraction != .05:
            raise ValueError("Sampling anchor definition differs")
        population[f"{profile}_flag"] = flags[protocol()["anchor_candidate"]].to_numpy(dtype=bool)
    count = population[[f"{profile}_flag" for profile in ablation.PROFILES]].sum(axis=1)
    population["agreement_group"] = np.where(count.eq(0), "none_flag", np.where(count.eq(3), "all_flag", "disagreement"))
    population["purchase_month"] = population.order_purchase_timestamp.dt.strftime("%Y-%m")
    ids, sources = exposure()
    population["previously_human_reviewed"] = population.order_id.isin(ids)
    evidence = {"features": features, "context": read(features_dir, "order_context.csv", parent),
                "payments": read(features_dir, "payment_source_evidence.csv", parent),
                "items": read(features_dir, "item_source_evidence.csv", parent)}
    provenance = {"study_directory": str(study), "study_manifest_sha256": manifest_hash,
                  "study_output_hashes": manifest["output_hashes"], "study_sources": manifest["provenance"],
                  "reviewed_source_files": sources, "reviewed_ids": sorted(ids), "code_hashes": code_hashes()}
    verify_sources(provenance)
    return population, evidence, provenance


def verify_sources(provenance):
    study = Path(provenance["study_directory"])
    if review.digest(study/"manifest.json") != provenance["study_manifest_sha256"]:
        raise ValueError("Parent study manifest changed")
    ablation.verify_files(study, {"output_hashes": provenance["study_output_hashes"]})
    ablation.verify_sources(provenance["study_sources"])
    ids, sources = exposure()
    if sources != provenance["reviewed_source_files"] or sorted(ids) != provenance["reviewed_ids"]:
        raise ValueError("Recorded human-review exposure changed during preparation")
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Review implementation or protocol changed during preparation")


def select_sample(population, size=120, seed=42):
    if (population.order_id.isna().any() or not population.order_id.is_unique
            or population[STRATA].isna().any().any() or population.previously_human_reviewed.isna().any()):
        raise ValueError("Invalid sampling population")
    eligible = population.loc[~population.previously_human_reviewed].sort_values("order_id")
    strata = population.groupby(STRATA).size().rename("population_cases").to_frame()
    strata = strata.join(eligible.groupby(STRATA).size().rename("eligible_cases")).fillna(0).astype(int).reset_index()
    strata["excluded_reviewed_cases"] = strata.population_cases-strata.eligible_cases
    strata["sample_cases"] = 0
    occupied = strata.index[strata.eligible_cases.gt(0)]
    if not len(occupied) <= size <= len(eligible) or size == 0:
        raise ValueError("Sample must cover each occupied eligible stratum without padding or replacement")
    remaining = size
    while remaining:
        for index in occupied:
            if strata.at[index, "sample_cases"] < strata.at[index, "eligible_cases"]:
                strata.at[index, "sample_cases"] += 1
                remaining -= 1
                if not remaining:
                    break
    strata["inclusion_probability"] = strata.sample_cases/strata.eligible_cases.replace(0, np.nan)
    strata["sampling_weight"] = strata.eligible_cases/strata.sample_cases.replace(0, np.nan)
    rng = np.random.default_rng(seed)
    pieces = []
    groups = eligible.groupby(STRATA, sort=True)
    for row in strata.itertuples(index=False):
        if row.sample_cases:
            group = groups.get_group(tuple(getattr(row, key) for key in STRATA))
            pieces.append(group.iloc[rng.choice(len(group), row.sample_cases, replace=False)])
    sample = pd.concat(pieces, ignore_index=True)
    sample = sample.iloc[rng.permutation(len(sample))].reset_index(drop=True)
    sample.insert(0, "review_id", [f"BR{index:04d}" for index in range(1, len(sample)+1)])
    key = sample.merge(strata, on=STRATA, how="left", validate="many_to_one", sort=False)
    return key, strata


def build_frames(population, evidence):
    key, strata = select_sample(population, protocol()["sample_size"], protocol()["seed"])
    ids = key[["review_id", "order_id"]]
    cases = ids.merge(evidence["context"][["order_id", *review.CONTEXT_COLUMNS]], on="order_id", validate="one_to_one", how="left")
    cases = cases.merge(evidence["features"][["order_id", *review.FEATURE_COLUMNS]], on="order_id", validate="one_to_one", how="left")
    if cases.order_status.isna().any() or cases.payment_record_count.isna().any():
        raise ValueError("Selected cases lack real source evidence")
    cases = cases.drop(columns="order_id")
    for column in review.REVIEW_COLUMNS:
        cases[column] = pd.Series(pd.NA, index=cases.index, dtype="string")
    frames = {"reviewer/cases.csv": cases, "analyst_only/selection_key.csv": key,
              "analyst_only/sampling_strata.csv": strata, "analyst_only/sampling_population.csv": population}
    lineage = []
    for source, columns, filename in [("payments", review.PAYMENT_COLUMNS, "payment_records.csv"),
                                      ("items", review.ITEM_COLUMNS, "item_records.csv")]:
        rows = ids.merge(evidence[source], on="order_id", validate="one_to_many", how="inner", sort=False)
        frames[f"reviewer/{filename}"] = rows[["review_id", *columns]]
        trace = rows[["review_id", "order_id", "clean_source_record"]].copy()
        trace.insert(0, "reviewer_table", filename)
        trace.insert(1, "reviewer_record", np.arange(1, len(trace)+1))
        lineage.append(trace)
    frames["analyst_only/source_lineage.csv"] = pd.concat(lineage, ignore_index=True)
    return frames


def summary_for(frames, provenance):
    key = frames["analyst_only/selection_key.csv"]
    population = frames["analyst_only/sampling_population.csv"]
    return {"status": "blank_pilot_pending_criteria", "common_validation_cases": len(population),
            "previously_reviewed_ids_known": len(provenance["reviewed_ids"]),
            "excluded_previously_reviewed": int(population.previously_human_reviewed.sum()),
            "eligible_cases": int((~population.previously_human_reviewed).sum()), "sample_cases": len(key),
            "sample_by_month": key.purchase_month.value_counts().sort_index().to_dict(),
            "sample_by_agreement_group": key.agreement_group.value_counts().sort_index().to_dict(),
            "payment_records": len(frames["reviewer/payment_records.csv"]),
            "item_records": len(frames["reviewer/item_records.csv"]),
            "human_labels_created": 0, "models_fitted": 0, "models_deserialized": 0,
            "test_scored": False, "accuracy_computed": False, "approved_criteria_available": False}


def write_batch(frames, provenance, output=review.DEFAULT_BATCH):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Pilot already exists; never overwrite: {output}")
    verify_sources(provenance)
    summary = summary_for(frames, provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".business-review-", dir=output.parent) as temp:
        staging = Path(temp)/"batch"
        (staging/"reviewer").mkdir(parents=True)
        (staging/"analyst_only").mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
        for name, frame in frames.items():
            actual = ablation.business.usage.read_snapshot_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(actual, frame, check_dtype=False, rtol=1e-12, atol=1e-12)
        (staging/"reviewer/README.md").write_text(REVIEWER_README)
        review.write_json(staging/"reviewer/criteria_draft.json", review.read_json(TEMPLATE/"criteria_draft.json"))
        reviewer_manifest = {"version": review.VERSION, "status": summary["status"], "case_count": len(frames["reviewer/cases.csv"]),
                             "output_hashes": {path.name: review.digest(path) for path in sorted((staging/"reviewer").iterdir())}}
        review.write_json(staging/"reviewer/manifest.json", reviewer_manifest)
        review.write_json(staging/"analyst_only/protocol.json", protocol())
        review.write_json(staging/"analyst_only/summary.json", summary)
        (staging/"analyst_only/README.md").write_text(ANALYST_README)
        (staging/"README.md").write_text(BATCH_README)
        manifest = {"version": review.VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "summary": summary, "runtime": {"numpy": np.__version__, "pandas": pd.__version__},
                    "schemas": schemas, "output_hashes": {str(path.relative_to(staging)): review.digest(path)
                                                         for path in sorted(staging.rglob("*")) if path.is_file()}}
        review.write_json(staging/"analyst_only/manifest.json", manifest)
        review.load_evidence(staging)
        verify_sources(provenance)
        if output.exists():
            raise FileExistsError("Pilot appeared during publication")
        staging.rename(output)
    return summary


REVIEWER_README = """# Blinded Business Review Pilot

Read only this folder while reviewing. Do not inspect analyst_only or model outputs.
BR identifiers are administrative pseudonyms, not invented orders. All observations
come from real Olist bed_bath_table orders. Source labels are deliberately blank.
The pilot is retrospective. Evidence does not establish real-time availability.

Payment records are not failed attempts or retries. payment_sequential is a source
sequence field, not a retry count. Installments are not extra order payments.
Totals are in original monetary units. Payment-minus-items-and-freight is a signed
accounting difference, not proof of wrongdoing. Multiple payments can be legitimate.
Estimated delivery and shipping_limit_date are targets, not observed events.
Durations are observed elapsed days; short positive duration alone is not proof of error.
Missing values remain missing. No customer/seller behavioral or geographic verdict is supplied.

Do not enter judgments until the target, criteria, insufficient-evidence policy and
reviewer responsibilities are agreed. Normal/Suspicious/Anomaly are draft workflow
categories, not fraud labels. Confidence Low/Medium/High is not a probability.
The approved protocol must define the categories, including rare-but-valid cases.
The software validates the recorded approval declaration; it cannot authenticate mail.

Use review_business_cases.py --status or --preview BR0001 for read-only inspection.
After agreement, --review requires --reviewer-id and --approved-criteria pointing to
a separate approved protocol. Do not edit the frozen draft or evidence in this folder.
Sessions are separate per reviewer, save only explicit decisions, and resume unanswered
cases. Completed judgments are not silently edited by this interface. Adjudication or
correction requires a separately documented revision, not replacing original decisions.
Sharing only this folder supports blinding; directory separation is not access control.
"""

ANALYST_README = """# Analyst-Only Sampling Record

Do not show selection_key, sampling strata or model agreements to the reviewer.
The fixed engineering anchor is ensemble_rank_mean_tail050 across three feature
profiles. It was chosen after exploration to structure this workflow pilot, not as
a proven best model, accuracy result, or preregistered confirmatory choice.
120 is a workload choice, not an approved sample-size or power calculation.

Population: common scored validation orders without recorded prior human labels.
Known labeled IDs are excluded, not relabeled. Prior unrecorded viewing is unknown;
do not claim the reviewer has never seen these orders. Pending older blank review
assignments are not human labels. Coordinate assignments before collecting judgments.
The 330 other validation orders and reserved test set are outside this sampling frame.
This pilot cannot validate detectors or anomaly types not present in the paired study.

Strata are purchase month crossed with agreement group (all flag, disagreement,
none flag). Equal capped allocation, then simple random sampling without replacement
within each stratum, followed by shuffled display order; seed 42, NumPy default_rng.
Within eligible stratum h, inclusion probability = n_h/N_h; weight = N_h/n_h.
Unweighted sample rates are NOT estimates for the validation population. Even weighted
rates address only this eligible common validation population, not all Olist orders.
Any future evaluation needs defined labels, completed human review, uncertainty and
missing-response handling. Model flags are never reference labels. No winner is selected.
Using this validation review to select models requires later separate final evaluation.

source_lineage maps each reviewer child-table record (one-based, header excluded)
back to the parent evidence's clean_source_record and original order_id.
The manifest hashes immutable files. sessions are mutable human work and excluded.
Keep the frozen pilot even if an approved protocol later requires a new sample version.
"""

BATCH_README = """# Business Review Pilot

Preparation complete; human review and criteria approval are NOT complete.
No labels, approval, accuracy, probability or model winner have been manufactured.
reviewer/ contains blinded real observations and blank label fields.
analyst_only/ contains the selection key, probabilities, provenance and sampling audit.
Do not inspect analyst_only before conducting a blinded review.
sessions/ is created only when an identified reviewer opens an approved protocol.
Original data, fitted models, experiments and earlier review files are unchanged.
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, default=ablation.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=review.DEFAULT_BATCH)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Pilot already exists: {args.output}")
        population, evidence, provenance = load_inputs(args.study)
        summary = write_batch(build_frames(population, evidence), provenance, args.output)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(2, f"Pilot preparation stopped: {exc}\n")
    print(f"Prepared {summary['sample_cases']} real cases with blank labels: {args.output.resolve()}")
    print("Pending advisor criteria. Sampling summary is analyst-only; no accuracy result.")


if __name__ == "__main__":
    main()
