"""Reversible column tables for repeated catalog field names, over evidence_refs_v1."""
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "compact"))
from evidence_wire import compact_payload as compact_evidence, expand_payload as expand_evidence
from evidence_wire import canonical, message_field, sha

VERSION = "evidence_catalog_v2"
COLUMNS = {"claim_catalog": ("field", "claim_type"),
           "metadata_catalog": ("metadata_kind", "code", "stage", "field")}
INSTRUCTIONS = (
    "\nCatalogs use columns and groups. Each group is [evidence reference, rows]. "
    "Each row follows columns in order, starting with the original selection ID. "
    "Claim groups reference one evidence_id; metadata groups reference a list of evidence_ids."
)


def catalog_table(name, groups):
    reference = "evidence_id" if name == "claim_catalog" else "evidence_ids"
    columns = COLUMNS[name]
    tables = []
    for group in groups:
        if set(group) != {reference, "choices"}:
            raise ValueError("Unexpected catalog fields")
        rows = []
        for selection_id, choice in group["choices"].items():
            if set(choice) != set(columns):
                raise ValueError("Unexpected catalog choice fields")
            rows.append([selection_id, *[choice[field] for field in columns]])
        tables.append([group[reference], rows])
    return {"columns": ["id", *columns], "groups": tables}


def expand_table(name, table):
    columns = COLUMNS[name]
    if set(table) != {"columns", "groups"} or table["columns"] != ["id", *columns]:
        raise ValueError("Invalid catalog table columns")
    reference = "evidence_id" if name == "claim_catalog" else "evidence_ids"
    result = []
    for evidence, rows in table["groups"]:
        choices = {}
        for row in rows:
            if len(row) != len(columns) + 1 or row[0] in choices:
                raise ValueError("Malformed or duplicate catalog row")
            choices[row[0]] = dict(zip(columns, row[1:], strict=True))
        result.append({reference: evidence, "choices": choices})
    return result


def transform(value, expanding=False):
    if isinstance(value, list):
        return [transform(item, expanding) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in COLUMNS and item:
            result[key] = expand_table(key, item) if expanding else catalog_table(key, item)
        else:
            result[key] = transform(item, expanding)
    return result


def expand_payload(payload):
    if not payload["instructions"].endswith(INSTRUCTIONS):
        # Verifier packets contain no selection catalogs; do not add overhead.
        return expand_evidence(payload)
    result = deepcopy(payload)
    result["instructions"] = result["instructions"][:-len(INSTRUCTIONS)]
    for message in result["input"]:
        field = message_field(message)
        if field:
            message[field] = canonical(transform(json.loads(message[field]), expanding=True))
    return expand_evidence(result)


def compact_payload(payload):
    result, metrics = compact_evidence(payload)
    previous_bytes = metrics["compact_utf8_bytes"]
    changed = False
    for message in result["input"]:
        field = message_field(message)
        if field:
            original = json.loads(message[field])
            transformed = transform(original)
            changed |= transformed != original
            message[field] = canonical(transformed)
    if changed:
        result["instructions"] += INSTRUCTIONS
    if canonical(expand_payload(result)) != canonical(payload):
        raise ValueError("Catalog compaction failed exact round-trip verification")
    metrics.update(version=VERSION, evidence_v1_utf8_bytes=previous_bytes,
                   compact_utf8_bytes=len(canonical(result).encode()))
    return result, metrics
