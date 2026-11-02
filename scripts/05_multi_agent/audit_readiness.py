"""Read-only integrity/readiness audit; does not reconstruct scores or run agents."""

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STAGE_SNAPSHOT = "data/experiments/olist_event_stage_scoring_v1"
EXPECTED_AUXILIARY = ["tests/test_event_stage_scoring.py", "docs/event_stage_scoring.md",
                      "docs/progress_event_scoring_2026-10-28.md"]
INTEGRATION_FILES = ["scripts/05_multi_agent/evidence_tools.py", "scripts/05_multi_agent/agent_contracts.py",
                     "scripts/05_multi_agent/provider.py", "scripts/05_multi_agent/coordinator.py",
                     "scripts/05_multi_agent/run_multi_agent.py", "templates/multi_agent_v1/integration.json",
                     "tests/test_multi_agent_integration.py"]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inspect_manifest(root, relative):
    root = Path(root).resolve()
    directory = root / relative
    path = directory / "manifest.json"
    if not path.is_file():
        return {"snapshot": relative, "status": "missing_manifest", "integrity_ok": False,
                "source_code_available": False, "checks": []}
    manifest = json.loads(path.read_text())
    checks = []
    for section, base in [("output_hashes", directory), ("source_files", root), ("implementation_hashes", root)]:
        mapping = manifest.get(section)
        if not isinstance(mapping, dict) or not mapping:
            checks.append({"section": section, "path": None, "status": "missing_hash_map"})
            continue
        for name, expected in sorted(mapping.items()):
            full = (base / name).resolve()
            if not full.is_relative_to(base.resolve()):
                checks.append({"section": section, "path": name, "status": "unsafe_path"})
                continue
            actual = digest(full) if full.is_file() else None
            checks.append({"section": section, "path": name,
                           "status": "missing" if actual is None else "match" if actual == expected else "mismatch",
                           "expected_sha256": expected, "actual_sha256": actual})
    integrity_ok = all(c["status"] == "match" for c in checks if c["section"] != "implementation_hashes")
    source_ok = all(c["status"] == "match" for c in checks if c["section"] == "implementation_hashes")
    return {"snapshot": relative, "manifest_sha256": digest(path),
            "status": "hashes_match" if integrity_ok and source_ok else "incomplete_or_changed",
            "integrity_ok": integrity_ok, "source_code_available": source_ok,
            "semantic_reproduction_performed": False,
            "counts": {section: {state: sum(c["section"] == section and c["status"] == state for c in checks)
                                  for state in ["match", "missing", "mismatch", "missing_hash_map", "unsafe_path"]}
                       for section in ["output_hashes", "source_files", "implementation_hashes"]},
            "checks": checks}


def audit(root=ROOT):
    root = Path(root)
    snapshots = [inspect_manifest(root, "data/experiments/olist_event_replay_v1"),
                 inspect_manifest(root, STAGE_SNAPSHOT)]
    auxiliary = [{"path": p, "exists": (root/p).is_file()} for p in EXPECTED_AUXILIARY]
    recovery_tasks = []
    if not snapshots[1]["integrity_ok"]:
        recovery_tasks.append("investigate_missing_or_changed_stage_scoring_outputs_and_sources")
    if not snapshots[1]["source_code_available"]:
        recovery_tasks.append("recover_or_reimplement_and_verify_stage_scoring_sources")
    if not all(item["exists"] for item in auxiliary):
        recovery_tasks.append("restore_stage_scoring_tests_and_documentation")
    integration = [{"path": p, "exists": (root/p).is_file()} for p in INTEGRATION_FILES]
    integration_present = all(item["exists"] for item in integration)
    return {
        "version": "multi_agent_readiness_audit_v1",
        "scope": "local_file_integrity_against_saved_manifests_not_independent_proof_of_correctness",
        "snapshots": snapshots, "expected_auxiliary_files": auxiliary,
        "api_pilot_ready": False,
        "integration_files": integration,
        "integration_source_available": integration_present,
        "integration_note": "File presence is not evidence of successful paid inference; inspect the integration test report and frozen pilot separately.",
        "outstanding": [*recovery_tasks,
                        *([] if integration_present else ["implement_and_test_agent_tools_temporal_evidence_and_peer_comparisons",
                        "connect_and_test_budget_guard_before_any_provider_call"]),
                        "verify_frozen_pilot_and_current_integration_test_evidence",
                        "complete_live_provider_smoke_test_and_human_explanation_review",
                        "verify_current_prices_account_access_and_payload_minimization"],
        "paid_api_calls": 0, "research_data_modified": False,
        "recovery_note": "Saved output and protocol are evidence, not substitutes for missing implementation. Do not overwrite frozen manifests to make new code appear identical."
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--require-ready", action="store_true", help="Exit 2 when paid pilot prerequisites remain unmet")
    args = parser.parse_args()
    result = audit()
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if args.require_ready and not result["api_pilot_ready"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
