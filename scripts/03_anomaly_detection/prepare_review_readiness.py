"""Prepare an unchanged blind pilot packet and analyst-only readiness audit."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import tempfile

import numpy as np
import pandas as pd

import integrate_process_evidence as integration

shared = integration.shared
pilot = integration.pilot
review = pilot.review
ROOT = integration.ROOT
VERSION = "business_review_readiness_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_review_readiness_v1"
TEMPLATE = ROOT/"templates/review_readiness_v1"
DOCUMENTS = ["README.md", "advisor_questions.md", "evaluation_plan.md", "reviewer_guide.md"]
TABLES = {"analyst_only/review_case_bridge.csv", "analyst_only/sampling_strata.csv", "analyst_only/readiness_checks.csv"}


def code_hashes():
    return {**integration.code_hashes(), str(Path(__file__).relative_to(ROOT)): review.digest(Path(__file__)),
            **{str((TEMPLATE/name).relative_to(ROOT)): review.digest(TEMPLATE/name) for name in DOCUMENTS}}


def verify_sources(provenance):
    directory = Path(provenance["integration_directory"])
    if review.digest(directory/"manifest.json") != provenance["integration_manifest_sha256"]:
        raise ValueError("Integrated evidence changed")
    shared.verify_files(directory, provenance["integration_output_hashes"])
    integration.verify_sources(provenance["integration_sources"])
    batch = Path(provenance["pilot_directory"])
    if review.digest(batch/"analyst_only/manifest.json") != provenance["pilot_manifest_sha256"]:
        raise ValueError("Pilot manifest changed")
    shared.verify_files(batch, provenance["pilot_output_hashes"])
    pilot.verify_sources(provenance["pilot_sources"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Readiness code/protocol changed")


def load_inputs(directory=integration.DEFAULT_OUTPUT, batch=review.DEFAULT_BATCH):
    directory, batch = Path(directory).resolve(), Path(batch).resolve()
    im = integration.load_snapshot(directory)
    pm = review.read_json(batch/"analyst_only/manifest.json")
    if pm.get("version") != review.VERSION or pm.get("status") != "complete":
        raise ValueError("Expected original completed blank pilot")
    if pm["provenance"]["study_manifest_sha256"] != im["provenance"]["snapshots"]["ablation"]["manifest_sha256"]:
        raise ValueError("Pilot and integration must share their business experiment")
    provenance = {"integration_directory": str(directory), "integration_manifest_sha256": review.digest(directory/"manifest.json"),
                  "integration_output_hashes": im["output_hashes"], "integration_sources": im["provenance"],
                  "pilot_directory": str(batch), "pilot_manifest_sha256": review.digest(batch/"analyst_only/manifest.json"),
                  "pilot_output_hashes": pm["output_hashes"], "pilot_sources": pm["provenance"], "code_hashes": code_hashes()}
    verify_sources(provenance)
    evidence, evidence_hash = review.load_evidence(batch)
    inputs = {"cases": shared.read_table(directory, "case_evidence.csv", im), "blind": evidence,
              "key": shared.read_table(batch, "analyst_only/selection_key.csv", pm),
              "strata": shared.read_table(batch, "analyst_only/sampling_strata.csv", pm),
              "criteria": review.read_json(batch/"reviewer/criteria_draft.json"), "evidence_hash": evidence_hash}
    validate_inputs(inputs)
    return inputs, provenance


def validate_inputs(inputs):
    key, strata, cases = (inputs[name] for name in ["key", "strata", "cases"])
    integration.business.require_key(key, ["order_id"], "review order key")
    integration.business.require_key(key, ["review_id"], "review pseudonym key")
    integration.business.require_key(cases, ["order_id"], "review integration")
    integration.business.require_key(strata, pilot.STRATA, "review strata")
    if key.review_id.tolist() != inputs["blind"]["cases.csv"].review_id.tolist() or len(key) != pilot.protocol()["sample_size"]:
        raise ValueError("Preserve the original pilot IDs and display order; never resample")
    source = cases.set_index("order_id")
    if not set(key.order_id).issubset(source.index) or not source.loc[key.order_id, "split"].eq("validation").all():
        raise ValueError("Pilot must map one-to-one into integrated validation orders")
    if key.previously_human_reviewed.any():
        raise ValueError("Previously reviewed orders cannot be introduced")
    if (inputs["criteria"]["status"] != "draft_pending_advisor"
            or any(inputs["criteria"][field] for field in review.CRITERIA_FIELDS-{"version", "status"})):
        raise ValueError("This preparation expects unchanged blank pending criteria, not an inferred approval")
    np.testing.assert_allclose(strata.inclusion_probability, strata.sample_cases/strata.eligible_cases, rtol=1e-14)
    np.testing.assert_allclose(strata.sampling_weight, strata.eligible_cases/strata.sample_cases, rtol=1e-14)
    actual = key.groupby(pilot.STRATA).size().sort_index()
    expected = strata.set_index(pilot.STRATA).sample_cases.sort_index()
    pd.testing.assert_series_equal(actual, expected, check_names=False, check_dtype=False, check_index_type=False)
    joined = key.merge(strata[pilot.STRATA+["inclusion_probability", "sampling_weight"]], on=pilot.STRATA, suffixes=("", "_expected"), validate="many_to_one")
    for field in ["inclusion_probability", "sampling_weight"]:
        np.testing.assert_array_equal(joined[field], joined[field+"_expected"])


def build_readiness(inputs):
    validate_inputs(inputs)
    key, cases = inputs["key"], inputs["cases"]
    fields = ["order_id", "process_replay_status", "process_inverted_pairs", "process_visible_events", "all_pairs_quality_eligible",
              "history_route", "business_profiles_scored", "delivery_models_forecast", "delivery_models_evaluated", "evidence_codes_json"]
    bridge = key.merge(cases[fields], on="order_id", validate="one_to_one", how="left", sort=False)
    checks = [
        ("source_lineage", "verified", False, "Pilot and integration use the same unchanged business experiment"),
        ("pilot_sample", "verified", False, "120 original real cases; no resampling or fabricated cases"),
        ("blinded_source_labels", "verified_blank", False, "No source label, confidence, reason, note or review timestamp filled"),
        ("sampling_weights", "verified", False, "Original unequal inclusion probabilities preserved; not population accuracy"),
        ("integration_mapping", "verified", False, "Every review pseudonym maps to one validation order in analyst-only bridge"),
        ("target_and_group_definitions", "pending_advisor", True, "Unusual observation versus operational problem; legitimate rare cases"),
        ("class_and_evidence_rules", "pending_advisor", True, "Define Normal/Suspicious/Anomaly and handling of insufficient evidence"),
        ("reviewers_and_adjudication", "pending_advisor", True, "Assign reviewers, independence, disagreements and documented revisions"),
        ("approval_record", "missing", True, "Separate genuinely approved protocol required; never edit frozen draft"),
        ("evaluation_scope", "pending_advisor", True, "Pilot is not a powered final evaluation; broader seller/geographic targets need separate evidence design"),
    ]
    frames = {"analyst_only/review_case_bridge.csv": bridge, "analyst_only/sampling_strata.csv": inputs["strata"].copy(),
              "analyst_only/readiness_checks.csv": pd.DataFrame(checks, columns=["check", "status", "blocks_label_entry", "detail"])}
    summary = {"status": "prepared_pending_human_protocol", "pilot_cases": len(bridge),
               "payment_records": len(inputs["blind"]["payment_records.csv"]), "item_records": len(inputs["blind"]["item_records.csv"]),
               "same_original_pilot": True, "sampling_weight_sum": float(bridge.sampling_weight.sum()),
               "eligible_original_sampling_population": int(inputs["strata"].eligible_cases.sum()),
               "mapped_cases_with_business_results": int(bridge.business_profiles_scored.notna().sum()),
               "mapped_cases_with_delivery_forecasts": int(bridge.delivery_models_forecast.notna().sum()),
               "mapped_cases_with_saved_delivery_evaluation": int(bridge.delivery_models_evaluated.notna().sum()),
               "pending_protocol_checks": int(frames["analyst_only/readiness_checks.csv"].blocks_label_entry.sum()),
               **{key: False for key in ["labels_entered", "approval_created", "approved_criteria_available", "ready_for_label_entry",
                                        "model_outputs_exposed_to_reviewers", "test_scored", "accuracy_computed", "new_cases_sampled"]}}
    return frames, summary


def write_readiness(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = Path(output).resolve()
    if output.exists():
        raise FileExistsError(f"Readiness output exists: {output}")
    if set(frames) != TABLES:
        raise ValueError("Missing readiness audit tables")
    verify_sources(provenance)
    batch = Path(provenance["pilot_directory"])
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".review-readiness-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        (staging/"analyst_only").mkdir(parents=True)
        # Copy the frozen blind evidence byte for byte; never enrich it with model output.
        target = staging/"reviewer_packet/reviewer"
        target.mkdir(parents=True)
        original = review.read_json(batch/"reviewer/manifest.json")
        for name in [*original["output_hashes"], "manifest.json"]:
            shutil.copyfile(batch/"reviewer"/name, target/name)
            if review.digest(target/name) != review.digest(batch/"reviewer"/name):
                raise ValueError("Blind packet copy differs")
        review.load_evidence(staging/"reviewer_packet")
        (staging/"reviewer_packet/README.md").write_text((TEMPLATE/"reviewer_guide.md").read_text())
        for name in DOCUMENTS[:-1]:
            (staging/name).write_text((TEMPLATE/name).read_text())
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        summary = {**summary, "blind_packet_byte_identical": True}
        (staging/"summary.json").write_text(json.dumps(summary, indent=2, allow_nan=False)+"\n")
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "schemas": schemas, "provenance": provenance,
                    "canonical_review_batch": str(batch), "reviewer_packet_usage": "read_only_preview_copy; real_sessions_use_canonical_batch",
                    "output_hashes": {str(path.relative_to(staging)): review.digest(path) for path in sorted(staging.rglob("*")) if path.is_file()}}
        (staging/"manifest.json").write_text(json.dumps(manifest, indent=2)+"\n")
        verify_sources(provenance)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory):
    directory = Path(directory)
    manifest = shared.read_json(directory/"manifest.json")
    required = TABLES | {"README.md", "advisor_questions.md", "evaluation_plan.md", "summary.json", "reviewer_packet/README.md",
                         *["reviewer_packet/reviewer/"+name for name in [*review.SCHEMAS, "manifest.json", "README.md", "criteria_draft.json"]]}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete" or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != required):
        raise ValueError("Expected complete review-readiness snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    if shared.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Readiness summary differs")
    review.load_evidence(directory/"reviewer_packet")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--integration", type=Path, default=integration.DEFAULT_OUTPUT)
    parser.add_argument("--pilot", type=Path, default=review.DEFAULT_BATCH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise FileExistsError(f"Output already exists: {args.output}")
        print("Verifying canonical pilot; no labeling, approval or resampling.", flush=True)
        inputs, provenance = load_inputs(args.integration, args.pilot)
        frames, summary = build_readiness(inputs)
        summary = write_readiness(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Review preparation stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
