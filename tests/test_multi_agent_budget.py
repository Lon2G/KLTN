"""Accounting-only examples in temporary SQLite; no business data or API calls."""

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/05_multi_agent"))
from api_budget import BudgetBlocked, BudgetLedger, DEFAULT_POLICY


class BudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)/"accounting.sqlite"
        self.policy = json.loads(DEFAULT_POLICY.read_text())

    def ledger(self, policy=None):
        return BudgetLedger(self.path, self.policy if policy is None else policy)

    def reserve(self, ledger, request="req-1", case="accounting-test-1", role="process_analyst",
                model="gpt-6-luna", max_input=4000, max_output=1000, fingerprint="a"*64, run="pilot"):
        return ledger.reserve(request, case, role, model, fingerprint, max_input, max_output, run_key=run)

    def test_conservative_reservation_and_exact_usage_without_rounding(self):
        with self.ledger() as ledger:
            row = self.reserve(ledger)
            self.assertEqual(row["reserved_nanousd"], 1_000_000)  # 4000*125 + 1000*500
            ledger.mark_sent("req-1")
            row = ledger.settle("req-1", 4000, 1000)
            self.assertEqual(row["actual_nanousd"], 900_000)
            self.assertEqual(ledger.snapshot()["reserved_nanousd"], 0)
            self.reserve(ledger, "tiny", max_input=1, max_output=1)
            ledger.mark_sent("tiny")
            self.assertEqual(ledger.settle("tiny", 1, 1)["actual_nanousd"], 600)

    def test_cache_read_and_write_categories_reconcile_to_total_input(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            ledger.mark_sent("req-1")
            self.assertEqual(ledger.settle("req-1", 4000, 1000, 1000, 2000)["actual_nanousd"], 860_000)

    def test_duplicate_reservation_and_dispatch_do_not_double_count(self):
        with self.ledger() as ledger:
            self.assertTrue(self.reserve(ledger)["created"])
            self.assertFalse(self.reserve(ledger)["created"])
            self.assertEqual(ledger.snapshot()["reserved_nanousd"], 1_000_000)
            ledger.mark_sent("req-1")
            with self.assertRaises(BudgetBlocked):
                ledger.mark_sent("req-1")
            self.assertFalse(self.reserve(ledger)["created"])

    def test_reused_id_with_different_payload_is_rejected(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, fingerprint="b"*64)

    def test_unknown_network_result_stays_reserved_after_restart(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            ledger.mark_sent("req-1")
        with self.ledger() as reopened:
            self.assertEqual(reopened.snapshot()["reserved_nanousd"], 1_000_000)
            with self.assertRaises(BudgetBlocked):
                reopened.cancel_unsent("req-1")
            reopened.settle("req-1", 3000, 1000)
            self.assertEqual(reopened.snapshot()["spent_nanousd"], 800_000)

    def test_unsent_cancellation_releases_only_reservation(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            ledger.cancel_unsent("req-1")
            self.assertEqual(ledger.snapshot()["reserved_nanousd"], 0)
            with self.assertRaises(BudgetBlocked):
                ledger.mark_sent("req-1")
            with self.assertRaises(BudgetBlocked):
                ledger.settle("req-1", 1, 1)

    def test_budget_rejects_before_spending_and_keeps_audit_counts(self):
        self.policy["api_cap_usd_nanounits"] = 1_000_000
        with self.ledger() as ledger:
            self.reserve(ledger)
            before = ledger.snapshot()
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "req-2")
            self.assertEqual(ledger.snapshot(), before)

    def test_actual_overrun_is_recorded_and_freezes_future_requests(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            self.reserve(ledger, "req-2")
            ledger.mark_sent("req-1")
            receipt = ledger.settle("req-1", 4000, 2000)
            self.assertEqual(receipt["actual_nanousd"], 1_400_000)
            self.assertEqual(receipt["overrun"], 1)
            self.assertTrue(ledger.snapshot()["breached"])
            with self.assertRaises(BudgetBlocked):
                ledger.mark_sent("req-2")
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "req-3")
        with self.ledger() as ledger:
            self.assertTrue(ledger.snapshot()["breached"])

    def test_repeated_receipt_is_idempotent_but_conflicting_receipt_rejected(self):
        with self.ledger() as ledger:
            self.reserve(ledger)
            ledger.mark_sent("req-1")
            first = ledger.settle("req-1", 3000, 800)
            self.assertEqual(first, ledger.settle("req-1", 3000, 800))
            with self.assertRaises(BudgetBlocked):
                ledger.settle("req-1", 3000, 801)

    def test_policy_change_cannot_reset_spending_and_caller_mutation_is_isolated(self):
        with self.ledger() as ledger:
            self.policy["models"]["gpt-6-luna"]["output_nanousd_per_token"] = 1
            self.assertEqual(self.reserve(ledger)["reserved_nanousd"], 1_000_000)
        with self.assertRaises(BudgetBlocked):
            self.ledger()

    def test_pilot_and_role_limits_count_every_attempt(self):
        self.policy["pilot_case_limit"] = 1
        self.policy["max_requests_per_role_per_case"] = 1
        with self.ledger() as ledger:
            self.reserve(ledger)
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "other-role-attempt")
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "other-case", case="accounting-test-2")
            self.reserve(ledger, "other-role", role="context_analyst")

    def test_total_case_request_limit_is_separate_from_role_limit(self):
        self.policy["max_requests_per_case"] = 1
        with self.ledger() as ledger:
            self.reserve(ledger)
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "other-role", role="context_analyst")

    def test_invalid_counts_models_hashes_and_overlarge_inputs_fail_closed(self):
        with self.ledger() as ledger:
            for args in [{"max_input": True}, {"max_output": -1}, {"max_input": 6001},
                         {"model": "unpriced-model"}, {"fingerprint": "bad"}, {"role": "unknown"}]:
                with self.subTest(args=args), self.assertRaises(ValueError):
                    self.reserve(ledger, **args)
            self.assertEqual(ledger.snapshot()["case_count"], 0)
            self.reserve(ledger)
            ledger.mark_sent("req-1")
            with self.assertRaises(ValueError):
                ledger.settle("req-1", 1, 1, 2, 0)
            self.assertEqual(ledger.snapshot()["reserved_nanousd"], 1_000_000)

    def test_two_connections_cannot_spend_same_remaining_budget(self):
        self.policy["api_cap_usd_nanounits"] = 1_000_000
        with self.ledger():
            pass
        def attempt(request):
            try:
                with self.ledger() as ledger:
                    return self.reserve(ledger, request)["created"]
            except BudgetBlocked:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(attempt, ["concurrent-1", "concurrent-2"]))
        self.assertEqual(sorted(results), [False, True])
        with self.ledger() as ledger:
            self.assertEqual(ledger.snapshot()["remaining_nanousd"], 0)

    def test_price_allowlist_models_are_accounted_separately(self):
        with self.ledger() as ledger:
            self.assertEqual(self.reserve(ledger, model="gpt-6.1-terra")["reserved_nanousd"], 10_000_000)

    def test_repeated_runs_share_budget_and_stable_case_limit(self):
        self.policy["api_cap_usd_nanounits"] = 2_000_000
        self.policy["pilot_case_limit"] = 1
        self.policy["max_requests_per_case"] = 1
        with self.ledger() as ledger:
            self.reserve(ledger, run="multi-agent-first")
            self.reserve(ledger, "baseline-request", role="single_agent_baseline", run="baseline-first")
            self.assertEqual(ledger.snapshot()["case_count"], 1)
            self.assertEqual(ledger.snapshot()["remaining_nanousd"], 0)
            with self.assertRaises(BudgetBlocked):
                self.reserve(ledger, "repeat", run="multi-agent-second")

    def test_invalid_policy_cannot_exceed_user_budget(self):
        policy = deepcopy(self.policy)
        policy["api_cap_usd_nanounits"] += 1
        with self.assertRaises(ValueError):
            self.ledger(policy)


if __name__ == "__main__":
    unittest.main()
