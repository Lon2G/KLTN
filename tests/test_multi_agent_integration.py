"""Real Olist evidence plus test-only model/HTTP fault injections; no research labels."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/05_multi_agent"))
from agent_contracts import gate, schemas, validate
from api_budget import BudgetBlocked, BudgetLedger, canonical
from coordinator import Coordinator
from evidence_tools import EvidenceStore, PROTECTED, STAGES, sha
from provider import ProviderUnavailable, ResponsesProvider


def fixture_payload():
    return {"model": "gpt-6-luna", "instructions": "Technical test only", "input": [{"role": "user", "content": "Test"}],
            "tools": schemas("process_analyst", ["same_product_peers"]), "tool_choice": "auto",
            "parallel_tool_calls": False, "max_output_tokens": 1200, "store": False,
            "reasoning": {"effort": "none"}, "service_tier": "default", "truncation": "disabled"}


def receipt(output=None):
    return {"id": "test_only_response", "model": "gpt-6-luna", "status": "completed", "service_tier": "default",
            "output": output or [], "usage": {"input_tokens": 500, "output_tokens": 100, "total_tokens": 600,
            "input_tokens_details": {"cached_tokens": 50, "cache_write_tokens": 100,
                                     "cache_write_tokens_details": {"ttl_30m_tokens": 100, "ttl_12h_tokens": 0}}}}


class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "budget.sqlite"
        self.ledger = BudgetLedger(self.path)
        self.calls = []

    def tearDown(self):
        self.ledger.close()
        self.temp.cleanup()

    def dispatch(self, post, payload=None):
        return ResponsesProvider(self.ledger, post=post).call(request_id="request-1", case_key="technical-case",
            run_key="test", role="process_analyst", payload=payload or fixture_payload())

    def post(self, endpoint, payload):
        self.calls.append(endpoint)
        if endpoint.endswith("input_tokens"):
            return {"object": "response.input_tokens", "input_tokens": 500}
        return receipt()

    def test_reserve_before_count_and_mark_sent_before_inference(self):
        def post(endpoint, body):
            row = self.ledger._row("request-1")
            self.assertEqual(row["status"], "reserved" if endpoint.endswith("input_tokens") else "in_flight")
            return self.post(endpoint, body)
        self.dispatch(post)
        self.assertEqual(self.calls, ["responses/input_tokens", "responses"])
        self.assertEqual(self.ledger.snapshot()["reserved_nanousd"], 0)
        self.assertEqual(self.ledger.snapshot()["spent_nanousd"], 98000)

    def test_persisted_receipt_prevents_repeat_spend_after_restart(self):
        self.dispatch(self.post)
        self.ledger.close()
        self.ledger = BudgetLedger(self.path)
        self.dispatch(lambda *args: self.fail("Duplicate network dispatch"))
        self.assertEqual(self.ledger.snapshot()["request_states"], {"settled": 1})

    def test_changed_payload_cannot_reuse_id(self):
        self.dispatch(self.post)
        changed = fixture_payload()
        changed["instructions"] += " changed"
        with self.assertRaises(BudgetBlocked):
            self.dispatch(self.post, changed)

    def test_input_cap_blocks_inference_and_releases_only_unsent(self):
        def post(endpoint, body):
            self.assertEqual(endpoint, "responses/input_tokens")
            return {"object": "response.input_tokens", "input_tokens": 6001}
        with self.assertRaises(BudgetBlocked):
            self.dispatch(post)
        self.assertEqual(self.ledger.snapshot()["request_states"], {"cancelled_unsent": 1})
        self.assertEqual(self.ledger.snapshot()["spent_nanousd"], 0)

    def test_timeout_after_dispatch_keeps_reservation_and_never_resends(self):
        def post(endpoint, body):
            if endpoint == "responses":
                raise ProviderUnavailable("Technical timeout injection")
            return self.post(endpoint, body)
        with self.assertRaises(ProviderUnavailable):
            self.dispatch(post)
        self.assertGreater(self.ledger.snapshot()["reserved_nanousd"], 0)
        with self.assertRaises(BudgetBlocked):
            self.dispatch(lambda *args: self.fail("Must not resend uncertain request"))

    def test_missing_usage_and_missing_cache_write_accounting_are_not_free(self):
        def post(endpoint, body):
            if endpoint != "responses":
                return self.post(endpoint, body)
            r = receipt()
            del r["usage"]["input_tokens_details"]["cache_write_tokens"]
            return r
        with self.assertRaises(ProviderUnavailable):
            self.dispatch(post)
        self.assertEqual(self.ledger.snapshot()["spent_nanousd"], 0)
        self.assertGreater(self.ledger.snapshot()["reserved_nanousd"], 0)

    def test_incomplete_response_still_accounts_usage(self):
        def post(endpoint, body):
            r = self.post(endpoint, body)
            if endpoint == "responses":
                r["status"] = "incomplete"
            return r
        with self.assertRaises(ProviderUnavailable):
            self.dispatch(post)
        self.assertEqual(self.ledger.snapshot()["request_states"], {"settled": 1})

    def test_hosted_tools_rejected_before_network(self):
        p = fixture_payload()
        p["tools"] = [{"type": "web_search"}]
        with self.assertRaises(ValueError):
            self.dispatch(lambda *args: self.fail("Unexpected network"), p)
        self.assertEqual(self.ledger.snapshot()["request_states"], {})


class ScriptedProvider:
    """Technical coordinator fixture; never presented as real model reasoning."""
    execution_kind = "technical_scripted_test_not_AI_inference"

    def __init__(self, bad_selection=False, always_revise=False, wrong_tool=False, fail_api=False):
        self.bad_selection, self.always_revise = bad_selection, always_revise
        self.wrong_tool, self.fail_api = wrong_tool, fail_api
        self.records = []

    def call(self, **kwargs):
        self.records.append(deepcopy(kwargs))
        if self.fail_api:
            raise ProviderUnavailable("Technical failure injection")
        payload, role = kwargs["payload"], kwargs["role"]
        packet = json.loads(payload["input"][0]["content"])["body"]
        results = [json.loads(x["output"]) for x in payload["input"] if x.get("type") == "function_call_output"]
        revision = json.loads(payload["input"][0]["content"])["explanation_revision"] == 1
        catalogs = list(packet.get("claim_catalog", []))
        for result in results:
            catalogs.extend(result.get("claim_catalog", []))

        def select(record, field):
            return next(option_id for group in catalogs if group["evidence_id"] == record["evidence_id"]
                        for option_id, choice in group["choices"].items() if choice["field"] == field)

        if self.wrong_tool:
            name, args = "delete_dataset", {}
        elif role == "process_analyst":
            if revision:
                record = next(r for r in packet["evidence"] if r["kind"] == "frozen_stage_assessment" and r["stage"] == "transport")
            else:
                record = next(r for r in packet["assessments"] if r["stage"] == "transport")
            if not results and not revision:
                name, args = "get_reference_membership", {"stage": "transport"}
            else:
                option_id = select(record, "duration_days")
                if self.bad_selection and not any(r.get("status") == "contract_rejected" for r in results):
                    option_id = "invalid_technical_test_selection"  # Test-only protocol fault, not an Olist value.
                name, args = "submit_analysis", {"claim_ids": [option_id],
                    "check_ids": [], "unknown_ids": []}
        elif role == "context_analyst":
            if not results and not revision:
                name, args = "compare_declared_peer_groups", {"stage": "transport", "plan": "category_peers"}
            else:
                r = next(r for r in packet["evidence"] if r["kind"] == "peer_comparison") if revision else results[-1]
                name, args = "submit_analysis", {"claim_ids": [select(r, "status")],
                    "check_ids": [], "unknown_ids": []}
        else:
            if not results and not revision:
                name, args = "recompute_reported_metrics", {"stage": "transport"}
            else:
                name, args = "submit_verdict", {"verdicts": [{"claim_id": c["claim_id"],
                    "verdict": "revise" if self.always_revise or c["code_gate"] != "passed" else "accept",
                    "reason": "needs_context" if self.always_revise else "supported" if c["code_gate"] == "passed" else "unsupported"}
                    for c in packet["claims"]]}
        return receipt([{"type": "function_call", "call_id": "test-call-" + str(len(self.records)),
                         "name": name, "arguments": canonical(args)}])


class RealEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore()
        cls.pilot = cls.store.pilot()

    def case(self, index=0, mode="assumed_static_context_at_purchase"):
        row = self.pilot[index]
        return self.store.case(row["order_id"], row["event_id"], mode)

    def test_frozen_selection_uses_real_validation_only_excludes_protected(self):
        self.assertEqual(Counter(r["stratum"] for r in self.pilot),
                         {"warning": 8, "complete_below_threshold": 8, "unassessed_or_partial": 4})
        self.assertEqual(len({r["order_id"] for r in self.pilot}), 20)
        self.assertFalse(PROTECTED & {r["order_id"] for r in self.pilot})
        self.assertEqual(self.pilot, self.store.pilot())

    def test_every_pilot_stage_recomputes_and_preserves_strict_unknowns(self):
        for i in range(20):
            c, strict = self.case(i), self.case(i, "verified_availability_only")
            for stage in STAGES:
                self.assertTrue(c.recompute_reported_metrics(stage)["data"]["matches_frozen"])
                self.assertIsNone(strict.scores[stage]["delay_score_0_100"])
                self.assertIsNone(strict.scores[stage]["long_stage_warning"])

    def test_prefix_assessment_never_reveals_future_delivery(self):
        c = self.case()
        events = self.store.events[c.order_id]
        early = self.store.case(c.order_id, events[0]["event_id"])
        self.assertEqual(len(early.get_observed_state()["data"]["events"]), 1)
        self.assertIsNone(early.scores["transport"]["end_timestamp"])
        self.assertIsNone(early.scores["transport"]["delay_score_0_100"])
        self.assertNotEqual(early.get_frozen_stage_assessment("transport")["evidence_id"], c.get_frozen_stage_assessment("transport")["evidence_id"])

    def test_peer_members_are_prior_training_orders_with_declared_controls(self):
        c = self.case()
        comparison = c.compare_declared_peer_groups("transport", "category_peers")
        ids = c.members[comparison["evidence_id"]]
        self.assertGreater(len(ids), 0)
        rows = [self.store.train_lookup[(oid, "transport")] for oid in ids]
        for r in rows:
            self.assertLess(r["end_timestamp"], c.asof)
            self.assertEqual(r["split"], "train")
            self.assertEqual(r["single_seller_id"], c.context["single_seller_id"])
            self.assertEqual(r["distance_band"], c.context["distance_band"])
            self.assertEqual(r["customer_state"], c.context["customer_state"])
            self.assertEqual(int(r["item_count"]), c.context["item_count"])
        self.assertAlmostEqual(comparison["data"]["mean_days"], sum(float(r["duration_days"]) for r in rows) / len(rows))
        self.assertIsNone(comparison["data"]["anomaly_rate"])

    def test_geography_approval_and_strict_unavailable_are_distinct(self):
        c = self.case()
        self.assertEqual(c.compare_declared_peer_groups("approval", "region_within_category")["data"]["status"],
                         "geographic_attribution_not_allowed_for_this_stage")
        strict = self.case(mode="verified_availability_only")
        self.assertEqual(strict.compare_declared_peer_groups("transport", "category_peers")["data"]["status"],
                         "context_availability_unverified")

    def test_underpowered_geographic_comparison_has_no_direction_or_cause(self):
        c = self.case()
        c.compare_declared_peer_groups("transport", "category_peers")
        d = c.compare_declared_peer_groups("transport", "region_within_category")["data"]
        self.assertEqual(d["status"], "insufficient_comparable_orders")
        self.assertIsNone(d["direction"])
        self.assertFalse(d["causal_claim_allowed"])
        self.assertIn("product_identity", d["uncontrolled_factors"])

    def test_geography_requires_prior_alternative_check_without_forced_positive(self):
        c = self.case()
        first = c.compare_declared_peer_groups("transport", "region_within_category")
        self.assertEqual(first["data"]["status"], "alternatives_not_checked")
        peer = c.compare_declared_peer_groups("transport", "category_peers")
        second = c.compare_declared_peer_groups("transport", "region_within_category")
        self.assertEqual(second["data"]["prior_peer_comparison_id"], peer["evidence_id"])
        self.assertFalse(second["data"]["all_alternative_causes_excluded"])
        self.assertEqual(second["data"]["status"], "insufficient_comparable_orders")

    def test_real_matched_cells_recompute_weighted_delta_but_fail_coverage(self):
        # A real development order: sample counts pass 30, coverage still fails 80%.
        oid = "4fd47db0e1bb9f59384ff16f8d533f80"
        c = self.store.case(oid, self.store.events[oid][-1]["event_id"])
        c.compare_declared_peer_groups("transport", "category_peers")
        evidence = c.compare_declared_peer_groups("transport", "region_within_category")
        d = evidence["data"]
        rows = [self.store.train_lookup[(oid, "transport")] for oid in c.members[evidence["evidence_id"]]]
        months = {r["start_timestamp"][:7] for r in rows}
        numerator_target = numerator_control = total_target = 0
        for month in months:
            target = [float(r["duration_days"]) for r in rows if r["start_timestamp"][:7] == month and r["customer_state"] == c.context["customer_state"]]
            control = [float(r["duration_days"]) for r in rows if r["start_timestamp"][:7] == month and r["customer_state"] != c.context["customer_state"]]
            self.assertGreaterEqual(min(len(target), len(control)), 5)
            total_target += len(target)
            numerator_target += sum(target)
            numerator_control += len(target) * sum(control) / len(control)
        self.assertAlmostEqual(d["difference_days"], (numerator_target - numerator_control) / total_target)
        self.assertEqual((d["target_n"], d["control_n"]), (48, 58))
        self.assertEqual(d["target_coverage"], 48 / 75)
        self.assertEqual(d["control_coverage"], 58 / 78)
        self.assertEqual(d["status"], "insufficient_comparable_orders")
        self.assertIsNone(d["direction"])

    def test_minimized_evidence_excludes_raw_identifiers_reviews_and_future_features(self):
        c = self.case()
        c.get_observed_state()
        c.get_available_context()
        for s in STAGES:
            c.get_frozen_stage_assessment(s)
            c.get_reference_membership(s)
            c.compare_declared_peer_groups(s, "category_peers")
        text = canonical(list(c.registry.values()))
        for raw_id in [c.order_id, c.context["product_id"], c.context["single_seller_id"]]:
            if raw_id:
                self.assertNotIn(raw_id, text)
        for field in ['"order_status"', '"review_comment_message"', '"payment_value"', '"performance_state"']:
            self.assertNotIn(field, text)

    def test_wrong_number_cross_case_and_underpowered_association_rejected(self):
        c = self.case()
        r = c.get_frozen_stage_assessment("transport")
        claim = {"claim_type": "observed_fact", "evidence_id": r["evidence_id"], "field": "duration_days", "value": 999}
        self.assertEqual(gate(c, "process_analyst", claim)["code_gate"], "rejected")
        claim["value"] = r["data"]["duration_days"]
        self.assertEqual(gate(self.case(1), "process_analyst", claim)["code_gate"], "rejected")
        g = c.compare_declared_peer_groups("transport", "category_peers")
        claim = {"claim_type": "descriptive_association", "evidence_id": g["evidence_id"], "field": "mean_days", "value": g["data"]["mean_days"]}
        self.assertEqual(gate(c, "context_analyst", claim)["code_gate"], "rejected")

    def test_role_schema_rejects_undeclared_plan_extra_fields_and_nonfinite_values(self):
        plan_schema = next(x for x in schemas("context_analyst", self.store.config["peer_plans"])
                           if x["name"] == "compare_declared_peer_groups")["parameters"]
        with self.assertRaises(ValueError):
            validate({"stage": "transport", "plan": "relax_until_positive"}, plan_schema)
        with self.assertRaises(ValueError):
            validate({"stage": "transport", "plan": "category_peers", "threshold": 0}, plan_schema)
        with self.assertRaises(ValueError):
            validate(float("nan"), {"type": "number"})

    def test_three_roles_select_tools_and_pass_evidence_without_mutating_detector(self):
        c, provider = self.case(), ScriptedProvider()
        before = deepcopy(c.scores)
        result = Coordinator(provider).run(c)
        self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
        self.assertEqual(result["request_counts"], {r: 2 for r in ["process_analyst", "context_analyst", "evidence_verifier"]})
        self.assertEqual(len(result["published_claims"]), 2)
        self.assertEqual(c.scores, before)
        self.assertEqual(result["cause_status"], "unverified")
        self.assertIn("not_AI_inference", result["execution_kind"])

    def test_bad_selection_is_recorded_and_repaired_within_original_limit(self):
        provider = ScriptedProvider(bad_selection=True)
        result = Coordinator(provider).run(self.case())
        self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
        self.assertEqual(len(result["contract_violations"]), 1)
        self.assertIn("invalid_technical_test_selection", canonical(result["raw_agent_outputs"]))
        self.assertEqual(result["request_counts"]["process_analyst"], 3)
        self.assertTrue(all(c["code_gate"] == "passed" for c in result["published_claims"]))
        self.assertLessEqual(sum(result["request_counts"].values()), 12)
        self.assertTrue(all(n <= 4 for n in result["request_counts"].values()))

    def test_verifier_revision_is_bounded_and_does_not_force_acceptance(self):
        result = Coordinator(ScriptedProvider(always_revise=True)).run(self.case())
        self.assertEqual(result["explanation_status"], "inconclusive", result.get("error"))
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(len(result["rejected_claims"]), 2)
        self.assertTrue(any(m["explanation_revision"] == 1 for m in result["messages"]))
        self.assertLessEqual(sum(result["request_counts"].values()), 12)
        self.assertTrue(all(n <= 4 for n in result["request_counts"].values()))

    def test_invalid_verifier_output_is_preserved_and_never_published(self):
        class InvalidVerifier(ScriptedProvider):
            def call(self, **kwargs):
                response = super().call(**kwargs)
                call = response["output"][0]
                if call["name"] == "submit_verdict":
                    args = json.loads(call["arguments"])
                    for verdict in args["verdicts"]:
                        verdict.update(verdict="accept", reason="unsupported")  # Explicit protocol fault.
                    call["arguments"] = canonical(args)
                return response
        result = Coordinator(InvalidVerifier()).run(self.case())
        self.assertEqual(result["explanation_status"], "inconclusive")
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(result["request_counts"]["evidence_verifier"], 3)
        self.assertEqual(len(result["contract_violations"]), 2)
        final = json.loads(result["raw_agent_outputs"][-1]["output"][0]["arguments"])
        self.assertTrue(all(v["verdict"] == "accept" and v["reason"] == "unsupported" for v in final["verdicts"]))

    def test_api_failure_preserves_detector(self):
        c = self.case()
        result = Coordinator(ScriptedProvider(fail_api=True)).run(c)
        self.assertEqual(result["explanation_status"], "unavailable_api")
        self.assertTrue(any(r["data"]["long_stage_warning"] for r in result["detector"]))
        self.assertEqual(result["published_claims"], [])

    def test_forbidden_tool_stops_without_dispatch(self):
        result = Coordinator(ScriptedProvider(wrong_tool=True)).run(self.case())
        self.assertEqual(result["explanation_status"], "inconclusive")
        self.assertIn("outside its role", result["error"])
        self.assertFalse(any(r["kind"] == "tool_call" for r in result["trace"]))

    def test_full_coordinator_to_budgeted_adapter_with_test_only_transport(self):
        brain = ScriptedProvider()
        def post(endpoint, payload):
            if endpoint.endswith("input_tokens"):
                return {"object": "response.input_tokens", "input_tokens": 500}
            role = payload["instructions"].split("Your role is ")[-1]
            return brain.call(role=role, payload=payload)
        with tempfile.TemporaryDirectory() as directory:
            with BudgetLedger(Path(directory) / "test.sqlite") as ledger:
                result = Coordinator(ResponsesProvider(ledger, post=post)).run(self.case())
                self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
                self.assertEqual(ledger.snapshot()["request_states"], {"settled": 6})
                self.assertEqual(ledger.snapshot()["spent_nanousd"], 6 * 98000)
                self.assertEqual(result["execution_kind"], "technical_test_transport_not_live_inference")


if __name__ == "__main__":
    unittest.main()
