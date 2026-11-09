"""Offline contract regression on real Olist evidence and saved live API outputs.

Malformed submissions below are explicit protocol fault tests, not business data
or new AI responses. No ground-truth labels or accuracy metrics are produced.
"""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/05_multi_agent"))
from agent_contracts import (catalog_view, checked_verdicts, claim_catalog, gate,
                            materialize_selection, schemas, validate)
from evidence_tools import EvidenceStore, STAGES
from replay_saved_agent_outputs import RESULT, run_offline
from run_multi_agent import run_api


class ContractV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore()
        cls.pilot = cls.store.pilot()

    def setUp(self):
        self.network = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline tests forbid HTTP"))
        self.network.start()
        self.addCleanup(self.network.stop)

    def case(self, index=0):
        row = self.pilot[index]
        return self.store.case(row["order_id"], row["event_id"])

    def process_catalog(self, case):
        records = [case.get_observed_state(), *[case.get_frozen_stage_assessment(s) for s in STAGES]]
        return claim_catalog(case, "process_analyst", [r["evidence_id"] for r in records])

    def submit(self, ids):
        return {"claim_ids": ids, "check_ids": [], "unknown_ids": []}

    def test_catalog_copies_actual_value_and_keeps_provenance(self):
        case = self.case()
        catalog = self.process_catalog(case)
        record = case.get_frozen_stage_assessment("transport")
        option_id = next(k for k, c in catalog.items() if c["evidence_id"] == record["evidence_id"] and c["field"] == "duration_days")
        compiled = materialize_selection(case, "process_analyst", self.submit([option_id]), catalog)
        claim = compiled["claims"][0]
        self.assertEqual(claim["value"], record["data"]["duration_days"])
        self.assertEqual(claim["claim_type"], "observed_fact")
        self.assertIn("not_model_generated", compiled["value_origin"])
        self.assertEqual(gate(case, "process_analyst", claim)["code_gate"], "passed")
        self.assertEqual({g["evidence_id"] for g in catalog_view(catalog)}, {c["evidence_id"] for c in catalog.values()})

    def test_no_free_text_fields_or_retyped_values_in_submission(self):
        case = self.case()
        catalog = self.process_catalog(case)
        schema = schemas("process_analyst", self.store.config["peer_plans"], catalog)[-1]["parameters"]
        sample = next(iter(catalog))
        for payload in [self.submit(["unknown_option"]), {**self.submit([sample]), "value": 999},
                        {"claims": [catalog[sample]], "recommended_checks": [], "unknowns": []}, self.submit([True])]:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                validate(payload, schema)
        self.assertFalse(any(c["field"] == "events" for c in catalog.values()))

    def test_duplicate_selection_and_empty_catalog_are_handled(self):
        case = self.case()
        catalog = self.process_catalog(case)
        sample = next(iter(catalog))
        with self.assertRaises(ValueError):
            materialize_selection(case, "process_analyst", self.submit([sample, sample]), catalog)
        schema = schemas("process_analyst", self.store.config["peer_plans"], {})[-1]["parameters"]
        validate(self.submit([]), schema)
        with self.assertRaises(ValueError):
            validate(self.submit([sample]), schema)

    def test_cross_case_and_role_reuse_is_rejected(self):
        case, other = self.case(), self.case(1)
        catalog = self.process_catalog(case)
        sample = next(iter(catalog))
        with self.assertRaises(ValueError):
            materialize_selection(other, "process_analyst", self.submit([sample]), catalog)
        with self.assertRaises(ValueError):
            materialize_selection(case, "context_analyst", self.submit([sample]), catalog)
        self.assertEqual(claim_catalog(case, "context_analyst", case.registry), {})

    def test_future_evidence_is_not_a_choice_for_earlier_event(self):
        completed = self.case()
        catalog = self.process_catalog(completed)
        option_id = next(k for k, c in catalog.items() if c["field"] == "duration_days" and
                         completed.registry[c["evidence_id"]]["stage"] == "transport")
        early = self.store.case(completed.order_id, self.store.events[completed.order_id][0]["event_id"])
        early_catalog = self.process_catalog(early)
        self.assertNotIn(option_id, early_catalog)
        with self.assertRaises(ValueError):
            materialize_selection(early, "process_analyst", self.submit([option_id]), catalog)

    def test_insufficient_peers_offer_unknown_status_only(self):
        case = self.case()
        record = case.compare_declared_peer_groups("transport", "same_product_peers")
        self.assertEqual(record["data"]["n"], 0)
        catalog = claim_catalog(case, "context_analyst", [record["evidence_id"]])
        self.assertEqual(len(catalog), 1)
        claim = next(iter(catalog.values()))
        self.assertEqual((claim["field"], claim["claim_type"], claim["value"]),
                         ("status", "unknown", "insufficient_comparable_orders"))

    def test_null_assessments_remain_unknown_not_zero_or_normal(self):
        row = self.pilot[0]
        case = self.store.case(row["order_id"], row["event_id"], "verified_availability_only")
        catalog = self.process_catalog(case)
        scores = [c for c in catalog.values() if c["field"] == "delay_score_0_100"]
        self.assertEqual(len(scores), 3)
        self.assertTrue(all(c["value"] is None and c["claim_type"] == "unknown" for c in scores))

    def test_all_twenty_cases_offer_only_gate_valid_choices(self):
        for i in range(20):
            case = self.case(i)
            catalog = self.process_catalog(case)
            for claim in catalog.values():
                self.assertEqual(gate(case, "process_analyst", claim)["code_gate"], "passed")
                self.assertNotIsInstance(claim["value"], (dict, list))

    def test_verifier_cannot_accept_rejected_evidence_or_use_supported_rejection(self):
        case = self.case()
        record = case.get_frozen_stage_assessment("transport")
        bad = gate(case, "process_analyst", {"claim_type": "observed_fact", "evidence_id": record["evidence_id"],
                    "field": "transport_duration_days_and_warning", "value": record["data"]["duration_days"]})
        for verdict, reason in [("accept", "supported"), ("reject", "supported"), ("revise", "supported")]:
            with self.subTest(verdict=verdict), self.assertRaises(ValueError):
                checked_verdicts({"verdicts": [{"claim_id": bad["claim_id"], "verdict": verdict, "reason": reason}]}, [bad])
        accepted = checked_verdicts({"verdicts": [{"claim_id": bad["claim_id"], "verdict": "reject", "reason": "unsupported"}]}, [bad])
        self.assertEqual(accepted[bad["claim_id"]]["verdict"], "reject")

    def test_verifier_requires_exact_coverage_and_valid_reason(self):
        case = self.case()
        claim = gate(case, "process_analyst", next(iter(self.process_catalog(case).values())))
        good = {"claim_id": claim["claim_id"], "verdict": "accept", "reason": "supported"}
        checked_verdicts({"verdicts": [good]}, [claim])
        for verdicts in [[], [good, good], [{**good, "claim_id": "other"}], [{**good, "reason": "insufficient_evidence"}]]:
            with self.subTest(verdicts=verdicts), self.assertRaises(ValueError):
                checked_verdicts({"verdicts": verdicts}, [claim])
        self.assertEqual(checked_verdicts({"verdicts": []}, []), {})

    def test_real_saved_api_regression_has_no_new_calls_or_rewritten_outputs(self):
        self.assertTrue(RESULT.is_file(), "Real saved API outputs are required for this regression")
        original = json.loads(RESULT.read_text())
        summary, replayed = run_offline()
        self.assertTrue(summary["all_checks_passed"])
        self.assertEqual(summary["live_api_calls"], 0)
        self.assertFalse(summary["ai_improvement_demonstrated"])
        self.assertEqual(summary["replayed_saved_receipts"], 3)
        self.assertEqual(replayed["published_claims"], [])
        self.assertEqual(len(replayed["contract_violations"]), 1)
        raw = replayed["raw_agent_outputs"][-1]["output"][0]
        old_submission = next(m["body"] for m in original["messages"] if m["sender_role"] == "process_analyst")
        self.assertEqual(json.loads(raw["arguments"]), old_submission)
        self.assertEqual(replayed["detector"], original["detector"])

    def test_old_pilot_cannot_run_under_new_contract(self):
        with patch("provider.ResponsesProvider.call", side_effect=AssertionError("Must reject before API")):
            for version in (1, 2):
                with self.assertRaisesRegex(ValueError, "contract version differs"):
                    run_api(ROOT / f"data/experiments/olist_multi_agent_tools_v{version}", 0, "do-not-run")


if __name__ == "__main__":
    unittest.main()
