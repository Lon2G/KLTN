"""Import a declared order-level CSV without inventing observations or hiding errors."""

import argparse
import csv
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np
import pandas as pd

import apply_olist_usage_policy as usage


audit = usage.audit
ROOT = audit.ROOT
VERSION = "process_import_v1"
REQUIRED = ["order_id", "order_status", "product_category_name", *audit.TIMES]
OPTIONAL = ["order_estimated_delivery_date", "split", "event_time_cutoff_exclusive"]
FIELDS = REQUIRED + OPTIONAL
DATE_FORMATS = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y %H:%M:%S"]
CONFIG_KEYS = {"schema_version", "dataset_id", "marketplace_id", "primary_category", "dataset_scope",
               "timestamp_basis", "timestamp_format", "estimated_delivery_format", "delimiter", "columns", "status_mapping"}
ISSUE_COLUMNS = ["source_record", "order_id", "field", "severity", "issue_code", "raw_value", "detail"]
CHANGE_COLUMNS = ["source_record", "field", "operation", "before", "after"]
DUPLICATE_COLUMNS = ["order_id", "records", "source_records_json", "raw_ids_json", "kind", "conflicting_columns_json"]


def unique_json_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def validate_config(config):
    if not isinstance(config, dict) or set(config) != CONFIG_KEYS:
        raise ValueError("Configuration must contain exactly the documented v1 keys")
    if config["schema_version"] != VERSION:
        raise ValueError("Unsupported import schema_version")
    for key in ["dataset_id", "marketplace_id"]:
        if not isinstance(config[key], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", config[key]):
            raise ValueError(f"Set {key} to an explicit lowercase identifier, up to 64 characters")
    category = config["primary_category"]
    if not isinstance(category, str) or not category or category.strip() != category:
        raise ValueError("Set primary_category to an exact nonempty source category")
    if config["dataset_scope"] not in ["unassigned", "development_only"]:
        raise ValueError("Unsupported dataset_scope")
    if config["timestamp_basis"] != "source_wall_clock_naive":
        raise ValueError("V1 requires source_wall_clock_naive; timezone conversion is not inferred")
    if config["timestamp_format"] not in DATE_FORMATS:
        raise ValueError("Unsupported exact timestamp_format")
    if config["estimated_delivery_format"] not in [*DATE_FORMATS, "%Y-%m-%d", "%d/%m/%Y"]:
        raise ValueError("Unsupported estimated_delivery_format")
    delimiter = config["delimiter"]
    if not isinstance(delimiter, str) or len(delimiter) != 1 or delimiter in ['"', "\r", "\n", "\0"]:
        raise ValueError("Use one unambiguous CSV delimiter")
    columns = config["columns"]
    if not isinstance(columns, dict) or set(columns) != set(FIELDS):
        raise ValueError("Column mapping must explicitly cover every required and optional canonical field")
    mapped = []
    for field, name in columns.items():
        if name is None and field in OPTIONAL:
            continue
        if not isinstance(name, str) or not name or name.strip() != name:
            raise ValueError(f"Missing or invalid source column mapping: {field}")
        mapped.append(name)
    if len(set(mapped)) != len(mapped):
        raise ValueError("A source column cannot represent multiple canonical fields")
    if (columns["split"] is None) != (columns["event_time_cutoff_exclusive"] is None):
        raise ValueError("Split and cutoff mappings must be supplied together")
    mapping = config["status_mapping"]
    if not isinstance(mapping, dict) or not mapping:
        raise ValueError("Declare an explicit nonempty status_mapping")
    for source, target in mapping.items():
        if (not isinstance(source, str) or not source or source.strip() != source
                or not isinstance(target, str) or target not in usage.clean.STATUSES):
            raise ValueError("Invalid status mapping; target must be a supported process state")


def read_csv_strict(path, config):
    with Path(path).open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.reader(stream, delimiter=config["delimiter"], strict=True)
        try:
            header = next(reader, None)
            if not header or any(not name or name.strip() != name for name in header) or len(set(header)) != len(header):
                raise ValueError("CSV header must have unique nonempty names without surrounding whitespace")
            missing = set(name for name in config["columns"].values() if name is not None) - set(header)
            if missing:
                raise ValueError(f"Missing mapped source columns: {sorted(missing)}")
            rows = []
            for record, values in enumerate(reader, start=1):
                if len(values) != len(header):
                    raise ValueError(f"CSV data record {record} has {len(values)} fields; expected {len(header)}")
                rows.append(values)
        except csv.Error as exc:
            raise ValueError(f"Malformed CSV near physical line {reader.line_num}: {exc}") from exc
    if not rows:
        raise ValueError("No data records; the header-only template is not a dataset")
    return pd.DataFrame(rows, columns=header, dtype="string")


def parse_exact_dates(values, date_format):
    parsed = pd.to_datetime(values.mask(values.eq("")), format=date_format, errors="coerce")
    # pandas accepts some relaxed forms even with format=; require a lossless format round trip.
    exact = parsed.dt.strftime(date_format).astype("string").eq(values).fillna(False)
    invalid = values.ne("") & (parsed.isna() | ~exact)
    return parsed.mask(invalid), invalid


def build_import(source, config):
    validate_config(config)
    if source.empty or not source.columns.is_unique or source.isna().any().any():
        raise ValueError("Use nonempty, strictly parsed string records without implicit NA conversion")
    missing_columns = set(name for name in config["columns"].values() if name is not None) - set(source.columns)
    if missing_columns:
        raise ValueError(f"Missing mapped source columns: {sorted(missing_columns)}")
    source = source.reset_index(drop=True).copy()
    canonical = pd.DataFrame(index=source.index)
    issues, changes, duplicate_groups = [], [], []
    blocked = pd.Series(False, index=source.index)

    def issue_records(indices, field, severity, code, detail):
        if severity == "error":
            blocked.loc[indices] = True
        source_column = config["columns"].get(field)
        for index in indices:
            issues.append({"source_record": index+1, "order_id": canonical.at[index, "order_id"],
                           "field": field, "severity": severity, "issue_code": code,
                           "raw_value": source.at[index, source_column] if source_column else "", "detail": detail})

    def issue(mask, field, severity, code, detail):
        issue_records(source.index[mask.fillna(False)], field, severity, code, detail)

    for field in FIELDS:
        name = config["columns"][field]
        raw = source[name] if name is not None else pd.Series("", index=source.index, dtype="string")
        value = raw.str.strip()
        canonical[field] = value
        for index in source.index[raw.ne(value)]:
            changes.append({"source_record": index+1, "field": field, "operation": "trim_surrounding_whitespace",
                            "before": raw.at[index], "after": value.at[index]})
    for field in ["order_id", "order_status", "product_category_name", audit.TIMES[0]]:
        issue(canonical[field].eq(""), field, "error", "missing_required_value", "Required value is absent; no imputation.")

    repeated = canonical.order_id.ne("") & canonical.order_id.duplicated(keep=False)
    for order_id, group in canonical.loc[repeated].groupby("order_id", sort=True):
        indices = group.index
        payload = source.loc[indices]
        conflicting = [column for column in source if payload[column].nunique(dropna=False) > 1]
        kind = "duplicate_id_conflicting_rows" if conflicting else "duplicate_id_identical_rows"
        issue_records(indices, "order_id", "error", kind, "Every member is quarantined; no first/last record is selected.")
        raw_ids = sorted(payload[config["columns"]["order_id"]].unique().tolist())
        if len(raw_ids) > 1:
            issue_records(indices, "order_id", "error", "normalized_id_collision", "Distinct raw IDs collide after whitespace trimming.")
        duplicate_groups.append({"order_id": order_id, "records": len(group),
                                 "source_records_json": json.dumps((indices+1).tolist()), "raw_ids_json": json.dumps(raw_ids),
                                 "kind": kind, "conflicting_columns_json": json.dumps(conflicting)})

    original_status = canonical.order_status.copy()
    unknown_status = original_status.ne("") & ~original_status.isin(config["status_mapping"])
    issue(unknown_status, "order_status", "error", "unmapped_status", "Add an explicit semantic status mapping; never infer it.")
    canonical["order_status"] = original_status.map(config["status_mapping"]).astype("string")
    for index in source.index[canonical.order_status.notna() & original_status.ne(canonical.order_status)]:
        changes.append({"source_record": index+1, "field": "order_status", "operation": "declared_status_mapping",
                        "before": original_status.at[index], "after": canonical.at[index, "order_status"]})
    for field in [*audit.TIMES, "order_estimated_delivery_date", "event_time_cutoff_exclusive"]:
        date_format = config["estimated_delivery_format"] if field == "order_estimated_delivery_date" else config["timestamp_format"]
        parsed, invalid = parse_exact_dates(canonical[field], date_format)
        issue(invalid, field, "error", "invalid_timestamp", f"Nonblank value must exactly match {date_format}; no repaired timestamp is created.")
        canonical[field] = parsed
    split = canonical.split
    issue(split.ne("") & ~split.isin(["train", "validation", "test"]), "split", "error", "unknown_split", "Allowed declared splits: train, validation, test.")
    if config["dataset_scope"] == "development_only":
        issue(split.eq("test"), "split", "error", "test_in_development_source", "Reserved test rows cannot enter a development-only import.")
    issue(split.eq("") & canonical.event_time_cutoff_exclusive.notna(), "split", "error", "cutoff_without_split", "A cutoff needs an explicit partition assignment.")
    bounded = split.isin(["train", "validation"])
    issue(bounded & canonical.event_time_cutoff_exclusive.le(canonical.order_purchase_timestamp), "event_time_cutoff_exclusive",
          "error", "purchase_outside_partition_cutoff", "A train/validation purchase must be strictly before its event cutoff.")
    canonical["source_record"] = source.index + 1
    accepted = canonical.loc[~blocked].copy()
    for field in ["order_id", "product_category_name", "split"]:
        accepted[field] = accepted[field].mask(accepted[field].eq(""))
    accepted["missing_milestone_count"] = accepted[audit.TIMES].isna().sum(axis=1)
    for field, start, end in zip(audit.DURATIONS, audit.TIMES, audit.TIMES[1:]):
        accepted[field] = (accepted[end] - accepted[start]).dt.total_seconds() / 86400
    accepted["total_cycle_time_days"] = (accepted[audit.TIMES[-1]] - accepted[audit.TIMES[0]]).dt.total_seconds() / 86400
    accepted["has_reversed_recorded_milestones"] = False
    for i, start in enumerate(audit.TIMES):
        for end in audit.TIMES[i+1:]:
            accepted["has_reversed_recorded_milestones"] |= accepted[end].lt(accepted[start])
    accepted["negative_transition_count"] = accepted[audit.DURATIONS].lt(0).sum(axis=1)
    accepted["status_delivery_conflict"] = accepted.order_status.eq("delivered").ne(accepted.order_delivered_customer_date.notna())
    accepted["completed_timing_eligible"] = accepted.order_status.eq("delivered") & accepted.missing_milestone_count.eq(0) & ~accepted.has_reversed_recorded_milestones
    accepted["selected_category"] = accepted.product_category_name.eq(config["primary_category"])

    def quality_issue(mask, field, severity, code, detail):
        issue(mask.reindex(source.index, fill_value=False), field, severity, code, detail)

    quality_issue(accepted.has_reversed_recorded_milestones, "order_status", "warning", "recorded_timeline_reversal", "Retain observed timestamps and negative durations; exclude completed-timing comparisons.")
    quality_issue(accepted.status_delivery_conflict, "order_status", "warning", "status_delivery_conflict", "Recorded status and delivery timestamp presence disagree; retain both.")
    quality_issue(accepted.order_status.eq("delivered") & accepted.missing_milestone_count.gt(0), "order_status", "warning", "delivered_missing_milestones", "Retain incomplete delivered records for source verification.")
    quality_issue(accepted.order_status.ne("delivered") & accepted.missing_milestone_count.gt(0), "order_status", "context", "incomplete_status_context", "Missing completion with this status is not an automatic anomaly; extraction cutoff is unknown.")
    for field in audit.DURATIONS:
        quality_issue(accepted[field].eq(0), field, "warning", "zero_transition_duration", "Observed zero remains zero; this warning is not an anomaly label.")
    accepted = usage.build_case_policy(accepted)
    accepted.insert(0, "dataset_id", config["dataset_id"])
    accepted.insert(1, "marketplace_id", config["marketplace_id"])
    has_cutoff = accepted.event_time_cutoff_exclusive.notna()
    crosses = accepted[audit.TIMES].ge(accepted.event_time_cutoff_exclusive, axis=0).any(axis=1)
    accepted["has_event_at_or_after_cutoff"] = crosses.astype("boolean").where(has_cutoff)
    accepted["timing_input_eligible"] = (accepted.selected_category & accepted.completed_timing_eligible
                                        & accepted.split.isin(["train", "validation"]) & has_cutoff & ~crosses)
    accepted["timing_input_reason"] = "eligible_completed_timing"
    accepted.loc[crosses, "timing_input_reason"] = "completion_not_before_partition_cutoff"
    accepted.loc[~has_cutoff, "timing_input_reason"] = "partition_cutoff_required"
    accepted.loc[~accepted.completed_timing_eligible, "timing_input_reason"] = "timing_ineligible:" + accepted.usage_group
    accepted.loc[~accepted.selected_category, "timing_input_reason"] = "outside_selected_category"
    accepted.loc[accepted.split.isna(), "timing_input_reason"] = "chronological_split_required"
    accepted.loc[accepted.split.eq("test").fillna(False), "timing_input_reason"] = "reserved_test_not_used"
    # Quality issues after sorting must link back to the original source-record index.
    for mask, field, code, detail in [
        (accepted.split.isin(["train", "validation"]) & ~has_cutoff, "event_time_cutoff_exclusive", "partition_cutoff_required", "Imported, but unavailable for timing inputs until a valid split design is supplied."),
        (accepted.completed_timing_eligible & crosses, "event_time_cutoff_exclusive", "completion_crosses_cutoff", "Future observed events remain in the audit view, not timing inputs."),
    ]:
        original_mask = pd.Series(False, index=source.index)
        original_mask.loc[accepted.loc[mask, "source_record"].to_numpy()-1] = True
        issue(original_mask, field, "context", code, detail)
    events = usage.clean.build_event_log(accepted)
    event_context = accepted[["order_id", "split", "event_time_cutoff_exclusive"]].rename(columns={"order_id": "case_id"})
    events = events.merge(event_context, on="case_id", how="left", validate="many_to_one")
    events["timestamp_before_partition_cutoff"] = events.timestamp.lt(events.event_time_cutoff_exclusive).astype("boolean").where(events.event_time_cutoff_exclusive.notna())
    issues = pd.DataFrame(issues, columns=ISSUE_COLUMNS).sort_values(["source_record", "severity", "field", "issue_code"]).reset_index(drop=True)
    disposition = pd.DataFrame({"source_record": source.index+1, "order_id": canonical.order_id,
                                "disposition": np.where(blocked, "quarantined", "accepted")})
    error_codes = issues.loc[issues.severity.eq("error")].groupby("source_record").issue_code.agg(lambda values: json.dumps(sorted(set(values))))
    disposition["blocking_codes_json"] = disposition.source_record.map(error_codes).fillna("[]")
    quarantine = disposition.loc[blocked].copy()
    quarantine["raw_row_json"] = [json.dumps(source.loc[index].to_dict(), ensure_ascii=False) for index in source.index[blocked]]
    scope_errors = [] if canonical.product_category_name.eq(config["primary_category"]).any() else ["declared_primary_category_absent"]
    summary = {"input_records": len(source), "accepted_records": len(accepted), "quarantined_records": int(blocked.sum()),
               "blocking_issues": int(issues.severity.eq("error").sum()), "warning_issues": int(issues.severity.eq("warning").sum()),
               "context_issues": int(issues.severity.eq("context").sum()), "duplicate_id_groups": len(duplicate_groups),
               "selected_category_records": int(accepted.selected_category.sum()),
               "completed_timing_eligible": int(accepted.completed_timing_eligible.sum()),
               "timing_input_eligible": int(accepted.timing_input_eligible.sum()),
               "observed_events": len(events), "normalization_changes": len(changes),
               "scope_errors": scope_errors, "ready_for_pipeline": not blocked.any() and not scope_errors,
               "training_performed": False, "test_scored": False, "human_labels_created": False}
    if len(accepted)+len(quarantine) != len(source) or not accepted.order_id.is_unique:
        raise ValueError("Import record reconciliation failed")
    frames = {"orders.csv": accepted, "event_log.csv": events, "issues.csv": issues,
              "row_disposition.csv": disposition, "quarantined_rows.csv": quarantine,
              "duplicate_groups.csv": pd.DataFrame(duplicate_groups, columns=DUPLICATE_COLUMNS),
              "normalization_changes.csv": pd.DataFrame(changes, columns=CHANGE_COLUMNS),
              "column_mapping.csv": pd.DataFrame([{"canonical_field": field, "source_column": config["columns"][field], "required_column": field in REQUIRED} for field in FIELDS]),
              "category_summary.csv": accepted.groupby("product_category_name").agg(orders=("order_id", "size"), completed_timing_eligible=("completed_timing_eligible", "sum"), timing_input_eligible=("timing_input_eligible", "sum")).reset_index()}
    return frames, summary


def prepare_import(input_path, config_path):
    paths = {"input_csv": Path(input_path), "config_json": Path(config_path)}
    before = {name: audit.file_hash(path) for name, path in paths.items()}
    config = json.loads(paths["config_json"].read_text(encoding="utf-8-sig"), object_pairs_hook=unique_json_keys)
    validate_config(config)
    source = read_csv_strict(paths["input_csv"], config)
    frames, summary = build_import(source, config)
    if before != {name: audit.file_hash(path) for name, path in paths.items()}:
        raise ValueError("Source/config changed during import")
    provenance = {"paths": {name: str(path.resolve()) for name, path in paths.items()}, "sha256": before,
                  "unmapped_source_columns": [name for name in source if name not in config["columns"].values()]}
    return frames, summary, config, provenance


def write_import(frames, summary, config, provenance, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite import snapshot: {output}")

    def check_sources():
        for name, path in provenance["paths"].items():
            if audit.file_hash(Path(path)) != provenance["sha256"][name]:
                raise ValueError(f"Source changed before publication: {name}")

    check_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for name, frame in frames.items():
            frame.to_csv(staging / name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        for key, name in [("input_csv", "source.csv"), ("config_json", "source_config.json")]:
            shutil.copyfile(provenance["paths"][key], staging / name)
            if audit.file_hash(staging / name) != provenance["sha256"][key]:
                raise ValueError("Source changed while copying immutable input")
        (staging / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (staging / "README.md").write_text(
            "# Process import audit\n\n" + json.dumps(summary, indent=2) + "\n\n"
            "Accepted means importable, NOT normal. Invalid source rows are preserved in source.csv and quarantined_rows.csv. "
            "Every duplicate group is quarantined in full, never silently deduplicated. Any blocking row or missing declared primary category prevents the official loader from admitting the entire snapshot to a downstream pipeline.\n\n"
            "orders.csv preserves quality evidence and distinguishes completed_timing_eligible from timing_input_eligible. "
            "Only the selected category with an explicit train/validation split, valid cutoff and observed completion before the cutoff can be timing-input eligible. "
            "No model is trained, selected, transferred or scored here; no normal/anomaly label or probability is created. "
            "event_log.csv is an audit log of actual accepted milestones, including post-cutoff events; it is not an unfiltered training event log.\n\n"
            "IDs remain case-sensitive strings. Source clock is timezone-naive; no timezone, missing event or extraction cutoff is inferred. "
            "Source bytes, mapping config, dataset/marketplace identity, schemas, issues and hashes are recorded. "
            "A successful import does not verify the source's truthfulness, category assignment or business status mapping. "
            "The template contract is in templates/process_import_v1/README.md. Load with load_import_snapshot(), not a bypass of the blocking-error gate.\n",
            encoding="utf-8")
        manifest = {"status": "complete", "import_version": VERSION, "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "config": config, "provenance": provenance, "summary": summary,
                    "schemas": {name: {column: str(dtype) for column, dtype in frame.dtypes.items()} for name, frame in frames.items()},
                    "code_hashes": {str(path.relative_to(ROOT)): audit.file_hash(path) for path in
                                    [Path(__file__), Path(usage.__file__), Path(usage.clean.__file__), Path(audit.__file__)]},
                    "output_hashes": {path.name: audit.file_hash(path) for path in sorted(staging.iterdir())}}
        check_sources()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Output appeared during publication: {output}")
        staging.rename(output)


def load_import_snapshot(snapshot):
    snapshot = Path(snapshot)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    if manifest.get("status") != "complete" or manifest.get("import_version") != VERSION:
        raise ValueError("Expected a completed process_import_v1 snapshot")
    required = {"orders.csv", "issues.csv", "row_disposition.csv", "quarantined_rows.csv", "source.csv", "source_config.json"}
    if not required.issubset(manifest.get("output_hashes", {})):
        raise ValueError("Import manifest omits required files")
    for name, digest in manifest["output_hashes"].items():
        path = (snapshot / name).resolve()
        if not path.is_relative_to(snapshot.resolve()) or not path.is_file() or audit.file_hash(path) != digest:
            raise ValueError(f"Import hash mismatch: {name}")
    if (not manifest["summary"]["ready_for_pipeline"] or manifest["summary"]["quarantined_records"]
            or manifest["summary"].get("scope_errors")):
        raise ValueError("Import has blocking row/scope errors; correct the source/config and publish a new version before training")
    orders = usage.read_snapshot_table(snapshot, "orders.csv", manifest)
    if len(orders) != manifest["summary"]["input_records"] or not orders.order_id.is_unique or orders.order_id.isna().any():
        raise ValueError("Imported order reconciliation failed")
    return orders, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.exit(2, f"Output already exists: {args.output}\n")
    try:
        frames, summary, config, provenance = prepare_import(args.input, args.config)
        write_import(frames, summary, config, provenance, args.output)
    except (ValueError, OSError, UnicodeError) as exc:
        parser.exit(2, f"Import stopped: {exc}\n")
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")
    if not summary["ready_for_pipeline"]:
        parser.exit(2, "Blocking row/scope errors remain; this snapshot cannot be used for training.\n")


if __name__ == "__main__":
    main()
