"""Exercise logging and the token stop with an isolated technical transport."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/06_agent_evaluation/live"))
from run_compact_pilot import LoggedProvider, ResponsesProvider, BudgetLedger, BudgetBlocked
from test_multi_agent_integration import fixture_payload, receipt


class CompactLiveLoggingTests(unittest.TestCase):
    def exercise(self, tokens):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            calls = []

            def post(endpoint, body):
                calls.append(endpoint)
                return {"object": "response.input_tokens", "input_tokens": tokens} if endpoint.endswith("input_tokens") else receipt()

            with BudgetLedger(path / "test.sqlite") as ledger, \
                 patch.object(ResponsesProvider, "_post", side_effect=post), \
                 patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline only")):
                provider = LoggedProvider(ledger, "technical-test-key", path)
                provider.execution_kind = "technical_test_transport_not_live_inference"
                request = dict(request_id="test-request", case_key="technical-case", run_key="test-run",
                               role="process_analyst", payload=fixture_payload())
                if tokens > 6000:
                    with self.assertRaises(BudgetBlocked):
                        provider.call(**request)
                else:
                    provider.call(**request)
                saved = json.loads((path / "requests.jsonl").read_text())
                count = json.loads((path / "token_counts.jsonl").read_text())
                self.assertEqual(saved, request)
                self.assertEqual(count["response"]["input_tokens"], tokens)
                self.assertEqual(count["request_id"], request["request_id"])
                self.assertNotIn("technical-test-key", (path / "requests.jsonl").read_text())
                return calls, ledger.snapshot()

    def test_over_cap_is_logged_before_inference_and_releases_reservation(self):
        calls, budget = self.exercise(6001)
        self.assertEqual(calls, ["responses/input_tokens"])
        self.assertEqual(budget["request_states"], {"cancelled_unsent": 1})
        self.assertEqual(budget["spent_nanousd"], 0)
        self.assertEqual(budget["reserved_nanousd"], 0)

    def test_under_cap_is_logged_and_receipt_settled(self):
        calls, budget = self.exercise(500)
        self.assertEqual(calls, ["responses/input_tokens", "responses"])
        self.assertEqual(budget["request_states"], {"settled": 1})
        self.assertEqual(budget["spent_nanousd"], 98000)
