"""Check explicit warning statements against the frozen source for this case."""
from copy import deepcopy
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from comparison_core import CONTRACT_VERSION, STAGES, sha, gate, metadata_gate

VERSION = "warning_coverage_v1"
INSTRUCTIONS = (
    "\nExplanation completeness: prioritize the claim_id for long_stage_warning=true "
    "in every warned stage. The process section has up to three scalar slots for "
    "three stages. Optional facts use remaining slots. Preserve assumptions and unknowns. "
    "A recommended check alone does not state a stage warning."
)
METADATA_FIELDS = ("metadata_kind", "code", "stage", "field", "evidence_ids", "evidence_fields",
                   "requirements", "rule_version", "meaning")


def recheck(case, item):
    if "metadata_kind" in item:
        return metadata_gate(case, item["origin_role"], {k: item[k] for k in METADATA_FIELDS})
    return gate(case, item["origin_role"], {k: item[k] for k in ("claim_type", "evidence_id", "field", "value")})


def coverage(case, result):
    if (result["contract_version"] != CONTRACT_VERSION or
            result["case_key"] != case.case_key or result["mode"] != case.mode or
            result["as_of_event_time"] != case.asof or result["assessment_revision"] != case.revision or
            sha(result["detector"]) != sha([case.get_frozen_stage_assessment(s) for s in STAGES])):
        raise ValueError("Coverage result differs from frozen case observations")
    expected = [s for s in STAGES if case.scores[s]["long_stage_warning"] is True]
    covered = set()
    for item in result["published_claims"]:
        checked = recheck(case, item)
        if (checked["code_gate"] == "passed" and checked["field"] == "long_stage_warning" and
                checked["value"] is True and item.get("supporting_evidence") == case.resolve_evidence(item["evidence_id"])):
            covered.add(checked["stage"])
    missing = [stage for stage in expected if stage not in covered]
    status = "not_applicable" if not expected else "incomplete" if missing else "complete"
    return {"version": VERSION, "status": status, "expected_stages": expected,
            "covered_stages": [s for s in expected if s in covered], "missing_stages": missing,
            "fraction": (len(expected) - len(missing)) / len(expected) if expected else None,
            "meaning": "coverage_of_recorded_warning_flags"}


def annotate(case, original):
    result = deepcopy(original)
    result["source_verification_status"] = original["explanation_status"]
    result["warning_coverage"] = coverage(case, result)
    if (result["warning_coverage"]["status"] == "incomplete" and
            result["explanation_status"] in {"evidence_checked", "evidence_checked_with_rejections", "code_checked"}):
        result["explanation_status"] = "incomplete_warning_coverage"
    return result
