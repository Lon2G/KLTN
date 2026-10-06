"""Reload a trusted local model and reproduce its fixed validation without refitting."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pandas as pd

import train_imported_process as training


def reproduce_validation(model_dir, snapshot):
    model_dir, snapshot = Path(model_dir), Path(snapshot)
    before = {"model_manifest_sha256": training.importer.audit.file_hash(model_dir / "manifest.json"),
              "import_manifest_sha256": training.importer.audit.file_hash(snapshot / "manifest.json")}
    bundle, manifest = training.load_model_bundle(model_dir)
    orders, imported = training.importer.load_import_snapshot(snapshot)
    identity = training.identity_from_config(imported["config"])
    if identity != bundle["identity"]:
        raise ValueError("Saved-model/import identity mismatch; a separate dataset needs a separate trained bundle")
    if (before["import_manifest_sha256"] != bundle["provenance"]["import_manifest_sha256"]
            or imported["output_hashes"] != bundle["provenance"]["import_output_hashes"]):
        raise ValueError("Revalidation requires the identical imported snapshot, not another batch")
    subsets, context, _, _ = training.build_design(orders, bundle["plan"], identity)
    if (subsets["fit"].order_id.tolist() != bundle["bank"]["fit_ids"]
            or subsets["calibration"].order_id.tolist() != bundle["bank"]["calibration_ids"]
            or subsets["validation"].order_id.tolist() != bundle["validation_ids"]):
        raise ValueError("Saved case populations differ from the declared design")
    scores, flags = training.score_bundle(bundle, subsets["validation"], identity)
    expected_scores = pd.read_csv(model_dir / "validation_scores.csv", dtype={"order_id": "string"}, float_precision="round_trip")
    expected_flags = pd.read_csv(model_dir / "validation_flags.csv", dtype={"order_id": "string", **{name: "bool" for name in bundle["bank"]["candidates"].candidate}})
    pd.testing.assert_frame_equal(scores, expected_scores, check_dtype=False, rtol=1e-12, atol=1e-12)
    pd.testing.assert_frame_equal(flags, expected_flags, check_dtype=False, check_exact=True)
    after = {"model_manifest_sha256": training.importer.audit.file_hash(model_dir / "manifest.json"),
             "import_manifest_sha256": training.importer.audit.file_hash(snapshot / "manifest.json")}
    if before != after:
        raise ValueError("Model/import changed during saved-model validation")
    summary = {"identity": identity, "run_id": manifest["summary"]["run_id"],
               "validation_orders_scored": len(scores), "validation_all_orders": int(context.split.eq("validation").sum()),
               "candidate_configurations": len(bundle["bank"]["candidates"]),
               "scores_match_with_tolerance": True, "score_tolerance": {"rtol": 1e-12, "atol": 1e-12},
               "candidate_flags_match_exactly": True, "refitting_performed": False,
               "test_scored": False, "accuracy_computed": False, "selected_candidate": None,
               "interpretation": "Reproducibility check on the same validation snapshot, not independent accuracy evaluation or new-batch deployment."}
    return {"validation_scores.csv": scores, "validation_flags.csv": flags}, summary, before


def write_validation(frames, summary, hashes, model_dir, snapshot, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite saved-model validation: {output}")

    def check_sources():
        training.load_model_bundle(model_dir)
        training.importer.load_import_snapshot(snapshot)
        current = {"model_manifest_sha256": training.importer.audit.file_hash(Path(model_dir) / "manifest.json"),
                   "import_manifest_sha256": training.importer.audit.file_hash(Path(snapshot) / "manifest.json")}
        if current != hashes:
            raise ValueError("Model/import changed before revalidation publication")

    check_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False)
        (staging / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (staging / "README.md").write_text(
            "# Saved-model validation replay\n\n" + json.dumps(summary, indent=2) + "\n\n"
            "The trusted local bundle was deserialized and applied to the same declared validation cases. "
            "No fitting, threshold learning, new human labels, final-test scoring or best-model selection occurred. "
            "Every candidate flag matches the training-run sidecar exactly; numeric scores match within the recorded tolerance. "
            "This verifies reproducibility of saved models, not business-anomaly accuracy or transfer to another marketplace.\n", encoding="utf-8")
        manifest = {"status": "complete", "version": "process_saved_model_validation_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "model_directory": str(Path(model_dir).resolve()), "import_snapshot": str(Path(snapshot).resolve()),
                    "input_hashes": hashes, "summary": summary,
                    "code_sha256": training.importer.audit.file_hash(Path(__file__)),
                    "output_hashes": {path.name: training.importer.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        check_sources()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Validation output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    try:
        frames, summary, hashes = reproduce_validation(args.model, args.snapshot)
        write_validation(frames, summary, hashes, args.model, args.snapshot, args.output)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Saved-model validation stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
