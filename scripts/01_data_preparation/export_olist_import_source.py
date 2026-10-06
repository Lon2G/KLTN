"""Export real frozen Olist development records into the reusable import contract."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import import_process_data as importer
import prepare_bed_bath_table_experiment as bed


DEFAULT_OUTPUT = bed.audit.ROOT / "data/import_sources/olist_bed_bath_table_development_v1"
TEMPLATE = bed.audit.ROOT / "templates/process_import_v1/import_config.json"


def verify_source(source):
    source = Path(source)
    manifest = json.loads((source / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("experiment_version") != "bed_bath_table_v1":
        raise ValueError("Expected the completed bed_bath_table_v1 experiment")
    if "cohort_cases.csv" not in manifest.get("output_hashes", {}):
        raise ValueError("Source manifest omits the cohort")
    for name, digest in manifest["output_hashes"].items():
        path = (source / name).resolve()
        if not path.is_relative_to(source.resolve()) or not path.is_file() or bed.audit.file_hash(path) != digest:
            raise ValueError(f"Frozen experiment hash mismatch: {name}")
    return manifest


def prepare_source(source=bed.DEFAULT_OUTPUT):
    source = Path(source)
    before = bed.audit.file_hash(source / "manifest.json")
    template_hash = bed.audit.file_hash(TEMPLATE)
    manifest = verify_source(source)
    cohort = bed.usage.read_snapshot_table(source, "cohort_cases.csv", manifest)
    if cohort.order_id.isna().any() or not cohort.order_id.is_unique or not cohort.split.isin(bed.SPLITS).all():
        raise ValueError("Invalid frozen cohort IDs or partitions")
    selected = cohort.loc[cohort.split.isin(["train", "validation"])].copy()
    if not selected.single_category_name.eq(bed.CATEGORY).all() or not selected.category_assignment_eligible.all():
        raise ValueError("Source is not the explicitly selected single-category cohort")
    columns = ["order_id", "order_status", "single_category_name", *bed.audit.TIMES,
               "order_estimated_delivery_date", "split", "event_time_cutoff_exclusive"]
    orders = selected[columns].rename(columns={"single_category_name": "product_category_name"}).reset_index(drop=True)
    lineage = selected[["order_id", "source_record", "split"]].rename(columns={"source_record": "original_raw_source_record"})
    lineage["upstream_cohort_record"] = selected.index + 1
    lineage = lineage.reset_index(drop=True)
    lineage.insert(0, "import_source_record", range(1, len(lineage)+1))
    config = json.loads(TEMPLATE.read_text())
    config.update({"dataset_id": "olist_bed_bath_table_development_v1", "marketplace_id": "olist",
                   "primary_category": bed.CATEGORY, "dataset_scope": "development_only"})
    config["columns"]["split"] = "split"
    config["columns"]["event_time_cutoff_exclusive"] = "event_time_cutoff_exclusive"
    importer.validate_config(config)
    provenance = {"experiment_directory": str(source.resolve()), "experiment_manifest_sha256": before,
                  "experiment_output_hashes": manifest["output_hashes"], "template_sha256": template_hash,
                  "source_cohort_records": len(cohort), "exported_development_records": len(orders),
                  "reserved_test_records_excluded": int(cohort.split.eq("test").sum()),
                  "exported_partitions": selected.split.value_counts().to_dict(),
                  "training_performed": False, "test_scored": False, "human_labels_created": False}
    if bed.audit.file_hash(source / "manifest.json") != before or bed.audit.file_hash(TEMPLATE) != template_hash:
        raise ValueError("Source/template changed during export preparation")
    return orders, lineage, config, provenance


def write_source(orders, lineage, config, provenance, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite real import source: {output}")

    def check_sources():
        source = Path(provenance["experiment_directory"])
        manifest = verify_source(source)
        if (bed.audit.file_hash(source / "manifest.json") != provenance["experiment_manifest_sha256"]
                or manifest["output_hashes"] != provenance["experiment_output_hashes"]
                or bed.audit.file_hash(TEMPLATE) != provenance["template_sha256"]):
            raise ValueError("Source/template changed before publication")

    check_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        orders.to_csv(staging / "orders.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
        lineage.to_csv(staging / "lineage.csv", index=False)
        (staging / "import_config.json").write_text(json.dumps(config, indent=2) + "\n")
        (staging / "README.md").write_text(
            "# Real Olist import source\n\n"
            "These are the frozen bed_bath_table train/validation orders, not synthetic samples or a new evaluation dataset. "
            "All accepted upstream observations, including missing/reversed timestamps and zero durations, are exported without repair. "
            "Existing split assignments and event cutoffs remain attached. The source category is cama_mesa_banho. "
            "lineage.csv maps every CSV record to the frozen cohort record and original raw order record. "
            "The final-test orders are excluded; no model or label is produced.\n\n"
            "Run import_process_data.py with this orders.csv and import_config.json, using a new output directory. "
            "Metadata describes the actual Olist source only; it must not be reused to impersonate another marketplace.\n\n"
            + json.dumps(provenance, indent=2) + "\n", encoding="utf-8")
        manifest = {"status": "complete", "export_version": "olist_process_import_source_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
                    "code_sha256": bed.audit.file_hash(Path(__file__)),
                    "output_hashes": {path.name: bed.audit.file_hash(path) for path in sorted(staging.iterdir())}}
        check_sources()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=bed.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    orders, lineage, config, provenance = prepare_source(args.source)
    write_source(orders, lineage, config, provenance, args.output)
    print(json.dumps({key: provenance[key] for key in ["exported_development_records", "reserved_test_records_excluded", "exported_partitions"]}, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
