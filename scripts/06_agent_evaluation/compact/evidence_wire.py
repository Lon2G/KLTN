"""Lossless evidence references within one complete agent request.

The encoder retains the first occurrence of each record and its provenance.
Each tool response contains new definitions and references to definitions already
visible in the same history. State is rebuilt for every request and every role.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparison_core import sha
from api_budget import canonical

VERSION = "evidence_refs_v1"
SCOPE_FIELDS = ("case_key", "as_of_event_time", "assessment_revision", "mode", "source_signature")
RECORD_FIELDS = (*SCOPE_FIELDS, "kind", "stage", "data", "evidence_id")
REFERENCE = "$evidence"
INSTRUCTIONS = (
    "\nEvidence wire format evidence_refs_v1: each JSON packet has a body and new "
    "scope/evidence definitions. Resolve $evidence handles using definitions in this or earlier "
    "packets of this request. Each record has its original evidence_id and inherits its named scope. Read all data and "
    "limitations from that record. Sibling fields supplement the reference. "
    "Use the original evidence and selection IDs in tool calls."
)


def record_from(value):
    record = {key: value[key] for key in RECORD_FIELDS}
    if sha({key: v for key, v in record.items() if key != "evidence_id"}) != record["evidence_id"]:
        raise ValueError("Evidence content does not match its source fingerprint")
    return record


class EvidenceEncoder:
    def __init__(self):
        self.records = {}
        self.handles = {}
        self.scope = None
        self.repeated_records = 0

    def packet(self, original):
        added_scopes, added_records = {}, {}

        def encode(value):
            if isinstance(value, list):
                return [encode(item) for item in value]
            if not isinstance(value, dict):
                return value
            if REFERENCE in value:
                raise ValueError("Input collides with the reserved evidence reference key")
            if {"evidence_id", "kind", "data"} <= value.keys():
                record = record_from(value)
                scope = {key: record[key] for key in SCOPE_FIELDS}
                if self.scope is None:
                    self.scope = scope
                    added_scopes["s0"] = scope
                if scope != self.scope:
                    raise ValueError("A request mixes evidence cases, observation times or source versions")
                evidence_id = record["evidence_id"]
                if evidence_id in self.records:
                    if self.records[evidence_id] != record:
                        raise ValueError("Conflicting evidence definitions")
                    self.repeated_records += 1
                else:
                    self.handles[evidence_id] = "e" + str(len(self.records))
                    self.records[evidence_id] = record
                    added_records[self.handles[evidence_id]] = {"scope": "s0", "evidence_id": evidence_id,
                                                              **{k: record[k] for k in ("kind", "stage", "data")}}
                # Catalog additions belong to the tool response, even when its
                # evidence record was seen earlier. Retain every such field.
                extras = {key: encode(item) for key, item in value.items() if key not in RECORD_FIELDS}
                return {REFERENCE: self.handles[evidence_id], **extras}
            return {key: encode(item) for key, item in value.items()}

        body = encode(original)
        return {"wire_format": VERSION, "new_scopes": added_scopes,
                "new_evidence": added_records, "body": body}


class EvidenceDecoder:
    def __init__(self):
        self.scopes = {}
        self.records = {}

    def packet(self, packet):
        if set(packet) != {"wire_format", "new_scopes", "new_evidence", "body"} or packet["wire_format"] != VERSION:
            raise ValueError("Invalid evidence packet")
        for name, scope in packet["new_scopes"].items():
            if name in self.scopes or set(scope) != set(SCOPE_FIELDS):
                raise ValueError("Repeated or malformed scope definition")
            self.scopes[name] = deepcopy(scope)
        for handle, item in packet["new_evidence"].items():
            if handle in self.records or set(item) != {"scope", "evidence_id", "kind", "stage", "data"}:
                raise ValueError("Repeated or malformed evidence definition")
            if item["scope"] not in self.scopes:
                raise ValueError("Evidence refers to an unavailable scope")
            record = {**self.scopes[item["scope"]], **{k: item[k] for k in ("kind", "stage", "data")},
                      "evidence_id": item["evidence_id"]}
            self.records[handle] = record_from(record)

        def decode(value):
            if isinstance(value, list):
                return [decode(item) for item in value]
            if not isinstance(value, dict):
                return value
            if REFERENCE in value:
                evidence_id = value[REFERENCE]
                if evidence_id not in self.records:
                    raise ValueError("Evidence reference is unavailable in the visible history")
                extras = {k: decode(v) for k, v in value.items() if k != REFERENCE}
                if set(extras) & set(RECORD_FIELDS):
                    raise ValueError("Reference attempts to override source evidence")
                return {**deepcopy(self.records[evidence_id]), **extras}
            return {key: decode(item) for key, item in value.items()}

        return decode(packet["body"])


def message_field(message):
    if message.get("role") == "user":
        return "content"
    if message.get("type") == "function_call_output":
        return "output"
    return None


def expand_payload(payload):
    if not payload["instructions"].endswith(INSTRUCTIONS):
        raise ValueError("Missing evidence wire instructions")
    result = deepcopy(payload)
    result["instructions"] = result["instructions"][:-len(INSTRUCTIONS)]
    decoder = EvidenceDecoder()
    for message in result["input"]:
        field = message_field(message)
        if field:
            message[field] = canonical(decoder.packet(json.loads(message[field])))
    return result


def compact_payload(payload):
    result = deepcopy(payload)
    encoder = EvidenceEncoder()
    for message in result["input"]:
        field = message_field(message)
        if field:
            original = json.loads(message[field])
            if canonical(original) != message[field]:
                raise ValueError("Expected canonical structured input from the agent runner")
            message[field] = canonical(encoder.packet(original))
    result["instructions"] += INSTRUCTIONS
    if canonical(expand_payload(result)) != canonical(payload):
        raise ValueError("Evidence compaction failed exact round-trip verification")
    return result, {"version": VERSION, "unique_evidence_records": len(encoder.records),
                    "repeated_evidence_records": encoder.repeated_records,
                    "original_utf8_bytes": len(canonical(payload).encode()),
                    "compact_utf8_bytes": len(canonical(result).encode()), "exact_round_trip": True,
                    "official_input_tokens": None}
