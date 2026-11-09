"""Wire parity against the frozen offline protocol. No inference or synthetic orders."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/06_agent_evaluation/live"))
from run_smoke import DEFAULT, INDEX, EvidenceStore, read, single
from comparison_runners import run_single_offline
from test_agent_comparison import SingleFixture


class LiveWireParityTests(unittest.TestCase):
    def test_single_role_matches_frozen_protocol(self):
        store = EvidenceStore()
        row = read(DEFAULT / "cohort.json")[INDEX]

        def case():
            return store.case(row["order_id"], row["event_id"])

        with patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline test")):
            for options in ({}, {"query": True}, {"invalid_once": True}, {"always_tool": True},
                            {"always_invalid": True}, {"fail": True}):
                with self.subTest(options=options):
                    first, second = SingleFixture(**options), SingleFixture(**options)
                    reference, observed = run_single_offline(case(), first), single(case(), second)
                    self.assertEqual(first.records, second.records)
                    for field in ("detector", "contract_violations", "explanation_status", "published_claims",
                                  "verified_notes", "verified_recommended_checks"):
                        self.assertEqual(reference[field], observed[field], field)
