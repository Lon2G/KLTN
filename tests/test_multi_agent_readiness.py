"""Audit real saved artifacts; corruptions below exist only in temporary files."""

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/"scripts/05_multi_agent"))
import audit_readiness as readiness


class ReadinessTests(unittest.TestCase):
    def test_real_saved_outputs_and_sources_match_but_no_semantic_replay_claim(self):
        report = readiness.audit()
        for snapshot in report["snapshots"]:
            self.assertTrue(snapshot["integrity_ok"], snapshot.get("checks"))
            self.assertFalse(snapshot["semantic_reproduction_performed"])
        self.assertFalse(report["api_pilot_ready"])
        self.assertEqual(report["paid_api_calls"], 0)

    def test_recovery_task_tracks_actual_integrity_instead_of_staying_hardcoded(self):
        healthy = {"integrity_ok": True, "source_code_available": True}
        with patch.object(readiness, "inspect_manifest", return_value=healthy):
            result = readiness.audit()
        self.assertNotIn("recover_or_reimplement_and_verify_stage_scoring_sources", result["outstanding"])
        self.assertFalse(result["api_pilot_ready"])
        missing = {"integrity_ok": True, "source_code_available": False}
        with patch.object(readiness, "inspect_manifest", return_value=missing):
            result = readiness.audit()
        self.assertIn("recover_or_reimplement_and_verify_stage_scoring_sources", result["outstanding"])
        corrupt = {"integrity_ok": False, "source_code_available": True}
        with patch.object(readiness, "inspect_manifest", return_value=corrupt):
            result = readiness.audit()
        self.assertIn("investigate_missing_or_changed_stage_scoring_outputs_and_sources", result["outstanding"])

    def test_missing_implementation_is_distinct_from_corrupt_output(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            directory = root/"snapshot"
            directory.mkdir()
            (directory/"value.txt").write_text("technical audit fixture")
            sha = hashlib.sha256((directory/"value.txt").read_bytes()).hexdigest()
            (root/"source.txt").write_text("technical audit fixture")
            manifest = {"output_hashes": {"value.txt": sha}, "source_files": {"source.txt": sha},
                        "implementation_hashes": {"missing.py": "0"*64}}
            (directory/"manifest.json").write_text(json.dumps(manifest))
            first = readiness.inspect_manifest(root, "snapshot")
            self.assertTrue(first["integrity_ok"])
            self.assertFalse(first["source_code_available"])
            (directory/"value.txt").write_text("test-only corruption")
            second = readiness.inspect_manifest(root, "snapshot")
            self.assertFalse(second["integrity_ok"])
            self.assertEqual(second["counts"]["output_hashes"]["mismatch"], 1)

    def test_missing_hash_maps_cannot_appear_verified(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/"manifest.json").write_text("{}")
            result = readiness.inspect_manifest(root, ".")
            self.assertFalse(result["integrity_ok"])
            self.assertFalse(result["source_code_available"])

    def test_outside_manifest_paths_are_not_read(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            (root/"manifest.json").write_text(json.dumps({"output_hashes": {"../outside": "0"*64}}))
            result = readiness.inspect_manifest(root, ".")
            self.assertEqual(result["counts"]["output_hashes"]["unsafe_path"], 1)


if __name__ == "__main__":
    unittest.main()
