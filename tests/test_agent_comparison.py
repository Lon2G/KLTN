"""Real Olist cases; scripted replies below test plumbing, not AI quality."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/06_agent_evaluation"))
from comparison_core import initial_evidence, source_metrics, sha
from comparison_runners import run_rules, run_single_offline, run_three_offline
from prepare_comparison import PILOT, PROTOCOL, prepare, read, verify_manifest, validate_protocol
from evidence_tools import EvidenceStore, STAGES
from provider import ProviderUnavailable
from test_multi_agent_integration import ScriptedProvider


class SingleFixture:
    """Explicit test-only protocol replies. No simulated business rows or labels."""
    execution_kind = "technical_scripted_test_not_AI_inference"

    def __init__(self, query=False, invalid_once=False, always_invalid=False, always_tool=False, fail=False):
        self.query, self.invalid_once, self.always_invalid = query, invalid_once, always_invalid
        self.always_tool, self.fail = always_tool, fail
        self.records = []

    def call(self, **kwargs):
        self.records.append(deepcopy(kwargs))
        if self.fail:
            raise ProviderUnavailable("Explicit test-only service failure")
        payload = kwargs["payload"]
        packet = json.loads(payload["input"][0]["content"])
        choices = deepcopy(packet["catalogs"])
        for item in payload["input"]:
            if item.get("type") == "function_call_output":
                addition = json.loads(item["output"]).get("catalogs", {})
                for domain, groups in addition.items():
                    for kind, rows in groups.items():
                        choices[domain][kind].extend(rows)
        if self.always_tool or self.query and len(self.records) == 1:
            name, args = "compare_declared_peer_groups", {"stage": "transport", "plan": "same_product_peers"}
        else:
            name, args = "submit_case_analysis", {}
            for domain, groups in choices.items():
                claims = [k for group in groups["claim_catalog"] for k in group["choices"]]
                notes = [k for group in groups["metadata_catalog"] for k, choice in group["choices"].items() if choice["metadata_kind"] == "unknown"]
                checks = [k for group in groups["metadata_catalog"] for k, choice in group["choices"].items() if choice["metadata_kind"] == "recommended_check"]
                args[domain] = {"claim_ids": list(dict.fromkeys(claims))[:1], "unknown_ids": list(dict.fromkeys(notes))[:1], "check_ids": list(dict.fromkeys(checks))[:1]}
            if self.always_invalid or self.invalid_once and len(self.records) == 1:
                args["process"]["unknown_ids"] = ["invalid_test_only_metadata_id"]
        return {"id": "test-only-response", "output": [{"type": "function_call", "call_id": "test-call-" + str(len(self.records)),
                                                       "name": name, "arguments": json.dumps(args)}]}


class AgentComparisonTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore()
        cls.pilot = read(PILOT / "pilot.json")

    def setUp(self):
        guard = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("No HTTP in comparison tests"))
        guard.start()
        self.addCleanup(guard.stop)

    def case(self, index=0):
        row = self.pilot[index]
        return self.store.case(row["order_id"], row["event_id"])

    def test_twenty_rules_outputs_are_real_and_source_checked_without_AI(self):
        for index in range(20):
            case = self.case(index)
            before = deepcopy(case.scores)
            result = run_rules(case)
            metrics = source_metrics(case, result)
            self.assertEqual(result["dispatched_model_requests"], 0)
            self.assertEqual(result["API_cost_usd"], 0)
            self.assertEqual(result["raw_agent_outputs"], [])
            self.assertEqual(result["execution_kind"], "deterministic_rules_real_olist")
            self.assertEqual(case.scores, before)
            self.assertEqual(metrics["source_supported_fraction"], 1)
            self.assertIsNone(metrics["independent_accuracy"])
            self.assertFalse(metrics["model_performance_eligible"])

    def test_rules_target_plan_is_fixed_without_automatic_widening(self):
        for index in [0, 8, 16]:
            case = self.case(index)
            result = run_rules(case)
            calls = [r for r in result["trace"] if r["kind"] == "tool_call"]
            self.assertTrue(calls)
            self.assertTrue(all(r["arguments"]["plan"] == "same_product_peers" for r in calls))
            warnings = [s for s in STAGES if case.scores[s]["long_stage_warning"] is True]
            if warnings:
                self.assertEqual([r["arguments"]["stage"] for r in calls], warnings)

    def test_same_initial_data_and_detector_across_controls(self):
        case = self.case()
        expected, _ = initial_evidence(case)
        provider = SingleFixture()
        result = run_single_offline(self.case(), provider)
        received = json.loads(provider.records[0]["payload"]["input"][0]["content"])
        self.assertEqual({k: received[k] for k in expected}, expected)
        self.assertEqual(result["detector"], run_rules(self.case())["detector"])

    def test_single_is_one_role_with_union_of_tools_and_no_AI_verifier(self):
        provider = SingleFixture(query=True)
        result = run_single_offline(self.case(), provider)
        self.assertEqual(result["explanation_status"], "code_checked", result.get("error"))
        self.assertEqual({r["role"] for r in provider.records}, {"single_agent_baseline"})
        names = {t["name"] for t in provider.records[0]["payload"]["tools"]}
        self.assertTrue({"get_reference_membership", "compare_declared_peer_groups", "recompute_reported_metrics", "resolve_evidence"} <= names)
        self.assertFalse(result["agent_verifier_used"])
        self.assertFalse(result["research_result"])
        self.assertTrue(all("verifier_verdict" not in item for item in result["published_claims"] + result["verified_notes"]))

    def test_invalid_single_note_is_preserved_and_repaired_within_cap(self):
        result = run_single_offline(self.case(), SingleFixture(invalid_once=True))
        self.assertEqual(result["explanation_status"], "code_checked", result.get("error"))
        self.assertEqual(len(result["contract_violations"]), 1)
        self.assertIn("invalid_test_only_metadata_id", json.dumps(result["raw_agent_outputs"][0]))
        self.assertEqual(result["dispatched_model_requests"], 2)

    def test_single_failure_after_four_attempts_is_not_dropped_or_retried(self):
        provider = SingleFixture(always_invalid=True)
        result = run_single_offline(self.case(), provider)
        self.assertEqual(len(provider.records), 4)
        self.assertEqual(result["explanation_status"], "inconclusive")
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(len(result["contract_violations"]), 4)

    def test_final_request_cannot_keep_querying_tools(self):
        result = run_single_offline(self.case(), SingleFixture(always_tool=True))
        self.assertEqual(result["dispatched_model_requests"], 4)
        self.assertEqual(result["explanation_status"], "inconclusive")
        self.assertEqual(result["published_claims"], [])

    def test_three_agent_shared_cap_stops_before_fifth_dispatch(self):
        provider = ScriptedProvider()
        result = run_three_offline(self.case(), provider, "matched_4")
        self.assertEqual(result["explanation_status"], "unavailable_budget")
        self.assertEqual(result["dispatched_model_requests"], 4)
        self.assertEqual(len(provider.records), 4)
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(len(result["detector"]), 3)

    def test_native_profile_retains_three_roles_without_changing_main_coordinator(self):
        provider = ScriptedProvider()
        result = run_three_offline(self.case(), provider, "native_bounded")
        self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
        self.assertEqual(result["dispatched_model_requests"], 6)
        self.assertEqual({r["role"] for r in provider.records}, {"process_analyst", "context_analyst", "evidence_verifier"})
        self.assertTrue(result["agent_verifier_used"])
        self.assertFalse(result["research_result"])

    def test_live_provider_cannot_be_used_by_offline_adapters(self):
        class Forbidden:
            execution_kind = "live_openai_api"
            def call(self, **kwargs):
                raise AssertionError("Must reject before a provider call")
        for runner in [run_single_offline, run_three_offline]:
            with self.assertRaisesRegex(ValueError, "offline providers only"):
                runner(self.case(), Forbidden())

    def test_API_failure_is_kept_as_unavailable_not_a_success(self):
        result = run_single_offline(self.case(), SingleFixture(fail=True))
        self.assertEqual(result["explanation_status"], "unavailable_api")
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(len(result["detector"]), 3)

    def test_missing_runs_and_inapplicable_coverage_are_null_not_zero_or_perfect(self):
        missing = source_metrics(self.case(), None)
        self.assertEqual(missing["status"], "not_run")
        self.assertIsNone(missing["source_supported_fraction"])
        case = self.case(8)
        metrics = source_metrics(case, run_rules(case))
        self.assertIsNone(metrics["warning_stage_coverage"])
        self.assertIsNone(metrics["missing_endpoint_coverage"])
        self.assertIsNone(metrics["timeline_conflict_coverage"])

    def test_source_metric_does_not_trust_claims_own_pass_flag(self):
        case = self.case()
        result = run_rules(case)
        bad = deepcopy(result)
        claim = next(x for x in bad["published_claims"] if x["field"] == "duration_days")
        claim["value"] += 999  # Explicit adversarial protocol fault only.
        self.assertEqual(claim["code_gate"], "passed")
        self.assertLess(source_metrics(case, bad)["source_supported_fraction"], 1)

    def test_cross_case_and_changed_detector_results_are_rejected(self):
        case = self.case()
        result = run_rules(case)
        with self.assertRaises(ValueError):
            source_metrics(self.case(1), result)
        changed = deepcopy(result)
        changed["detector"][0]["data"]["duration_days"] = 999  # Test-only corruption.
        with self.assertRaisesRegex(ValueError, "changed the frozen detector"):
            source_metrics(case, changed)

    def test_duplicate_items_do_not_inflate_source_metrics(self):
        case = self.case()
        result = run_rules(case)
        metrics = source_metrics(case, result)
        result["published_claims"].append(deepcopy(result["published_claims"][0]))
        repeated = source_metrics(case, result)
        self.assertEqual(repeated["distinct_published_items"], metrics["distinct_published_items"])
        self.assertEqual(repeated["duplicate_published_items"], metrics["duplicate_published_items"] + 1)

    def test_conflict_null_scores_are_never_recast_as_below_threshold(self):
        case = self.case(16)
        result = run_rules(case)
        self.assertTrue(all(r["data"]["delay_score_0_100"] is None for r in result["detector"]))
        self.assertTrue(all(r["data"]["long_stage_warning"] is None for r in result["detector"]))
        metrics = source_metrics(case, result)
        self.assertEqual(metrics["timeline_conflict_coverage"], 1)
        self.assertIsNone(metrics["missing_endpoint_coverage"])

    def test_existing_frozen_snapshot_remains_valid_and_cannot_be_overwritten(self):
        self.assertTrue(verify_manifest(PILOT))
        with self.assertRaisesRegex(ValueError, "never overwrite"):
            prepare(PILOT)
        protocol = read(PROTOCOL)
        self.assertEqual(protocol["profiles"]["matched_4"]["single_agent_max_requests"], protocol["profiles"]["matched_4"]["three_agents_max_requests"])
        self.assertEqual(protocol["main_system"], "three_agents")
        self.assertEqual(protocol["new_model_calls_in_this_package"], 0)

    def test_protocol_cannot_claim_matched_caps_that_the_code_does_not_apply(self):
        protocol = read(PROTOCOL)
        self.assertTrue(validate_protocol(protocol))
        bad = deepcopy(protocol)
        bad["profiles"]["matched_4"]["three_agents_max_requests"] = 12
        with self.assertRaisesRegex(ValueError, "request cap differs"):
            validate_protocol(bad)
        bad = deepcopy(protocol)
        bad["limits"]["max_output_tokens_per_request"] = 2400
        with self.assertRaisesRegex(ValueError, "token cap differs"):
            validate_protocol(bad)


if __name__ == "__main__":
    unittest.main()
