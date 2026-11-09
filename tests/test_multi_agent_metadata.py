"""Real Olist evidence and explicit protocol fixtures; no invented business rows.

Fixture model replies exercise contracts only, never research accuracy.
"""
from copy import deepcopy
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/05_multi_agent"))
from agent_contracts import checked_verdicts, materialize_selection, schemas, validate
from coordinator import Coordinator
from evidence_tools import EvidenceStore, STAGES
from metadata_contracts import metadata_catalog, metadata_gate, materialize_metadata
from replay_saved_metadata import run_offline
from test_multi_agent_integration import ScriptedProvider


class MetadataProviderFixture(ScriptedProvider):
    """Test-only selected IDs and verdict faults, not actual AI performance."""
    def __init__(self, reject_metadata=False, omit_metadata=False, bad_note_once=False):
        super().__init__()
        self.reject_metadata = reject_metadata
        self.omit_metadata = omit_metadata
        self.bad_note_once = bad_note_once
        self.injected = False

    def call(self, **kwargs):
        response = super().call(**kwargs)
        payload, role = kwargs["payload"], kwargs["role"]
        packet = json.loads(payload["input"][0]["content"])["body"]
        outputs = [json.loads(x["output"]) for x in payload["input"] if x.get("type") == "function_call_output"]
        catalogs = list(packet.get("metadata_catalog", []))
        for output in outputs:
            catalogs.extend(output.get("metadata_catalog", []))
        choices = {key: item for group in catalogs for key, item in group["choices"].items()}
        call = response["output"][0]
        args = json.loads(call["arguments"])
        if call["name"] == "submit_analysis":
            args["unknown_ids"] = [next(key for key, item in choices.items() if item["code"] == "context_availability_unverified" and item["metadata_kind"] == "unknown")]
            if role == "process_analyst":
                args["check_ids"] = [next(key for key, item in choices.items() if item["code"] == "review_carrier_route")]
            if self.bad_note_once and not self.injected:
                args["unknown_ids"] = ["invalid_technical_metadata_id"]
                self.injected = True
        elif call["name"] == "submit_verdict":
            metadata = {c["claim_id"] for c in packet["claims"] if "metadata_kind" in c}
            if self.omit_metadata:
                args["verdicts"] = [v for v in args["verdicts"] if v["claim_id"] not in metadata]
            elif self.reject_metadata:
                for verdict in args["verdicts"]:
                    if verdict["claim_id"] in metadata:
                        verdict.update(verdict="reject", reason="unsupported")
        call["arguments"] = json.dumps(args)
        return response


class MetadataContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore()
        cls.pilot = cls.store.pilot()

    def setUp(self):
        guard = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline tests forbid HTTP"))
        guard.start()
        self.addCleanup(guard.stop)

    def case(self, index=0, mode="assumed_static_context_at_purchase"):
        row = self.pilot[index]
        return self.store.case(row["order_id"], row["event_id"], mode)

    def stage_catalog(self, case, role="process_analyst"):
        records = [case.get_observed_state(), *[case.get_frozen_stage_assessment(s) for s in STAGES]]
        return metadata_catalog(case, role, [r["evidence_id"] for r in records])

    def test_complete_endpoints_never_offer_missing_endpoint_note(self):
        for index in [0, 1, 2, 8, 9, 16]:
            catalog = self.stage_catalog(self.case(index))
            self.assertFalse(any(x["code"] == "stage_endpoint_not_observed" for x in catalog.values()))

    def test_real_early_event_offers_only_the_unobserved_endpoint_scope(self):
        completed = self.case()
        case = self.store.case(completed.order_id, self.store.events[completed.order_id][0]["event_id"])
        catalog = self.stage_catalog(case)
        notes = [x for x in catalog.values() if x["code"] == "stage_endpoint_not_observed"]
        self.assertTrue(notes)
        for note in notes:
            self.assertIn(note["field"], {"start_timestamp", "end_timestamp"})
            self.assertTrue(all(fields[note["field"]] is None for fields in note["evidence_fields"].values()))
        self.assertFalse(any(x["code"] == "timeline_conflict" for x in catalog.values()))

    def test_conflict_is_negative_recorded_interval_not_missing_or_zero(self):
        case = self.case(16)
        catalog = self.stage_catalog(case)
        conflicts = [x for x in catalog.values() if x["code"] == "timeline_conflict"]
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["stage"], "preparation_handoff")
        self.assertEqual(next(iter(conflicts[0]["evidence_fields"].values()))["signed_duration_seconds"], -214)
        self.assertIsNone(case.scores["preparation_handoff"]["duration_days"])
        self.assertTrue(all(x["delay_score_0_100"] is None for x in case.scores.values()))

    def test_low_count_and_unperformed_comparison_are_distinct(self):
        for index in (0, 16):
            case = self.case(index)
            record = case.compare_declared_peer_groups("transport", "same_product_peers")
            catalog = metadata_catalog(case, "context_analyst", [record["evidence_id"]])
            codes = {x["code"] for x in catalog.values()}
            if index == 0:
                self.assertEqual(record["data"]["n"], 0)
                self.assertIn("insufficient_comparable_orders", codes)
                self.assertIn("collect_more_comparable_history", codes)
            else:
                self.assertEqual(record["data"]["status"], "stage_unassessed")
                self.assertEqual(codes, {"comparison_not_performed_stage_unassessed"})

    def test_sufficient_counts_but_poor_coverage_is_not_low_count(self):
        order = "4fd47db0e1bb9f59384ff16f8d533f80"
        case = self.store.case(order, self.store.events[order][-1]["event_id"])
        case.compare_declared_peer_groups("transport", "category_peers")
        rec = case.compare_declared_peer_groups("transport", "region_within_category")
        self.assertEqual((rec["data"]["target_n"], rec["data"]["control_n"]), (48, 58))
        catalog = metadata_catalog(case, "context_analyst", [rec["evidence_id"]])
        codes = {x["code"] for x in catalog.values()}
        self.assertIn("insufficient_comparison_coverage", codes)
        self.assertNotIn("insufficient_comparable_orders", codes)

    def test_no_warning_does_not_offer_seller_route_or_workload_investigation(self):
        for index in (8, 9):
            case = self.case(index)
            self.stage_catalog(case)
            case.get_available_context()
            codes = {r["code"] for r in metadata_catalog(case, "context_analyst", case.registry).values()}
            self.assertFalse(codes & {"review_carrier_route", "review_seller_preparation", "collect_workload_stock", "collect_weight_dimensions", "inspect_source_timestamps"})

    def test_warning_check_has_correct_stage_and_two_source_context_dependency(self):
        case = self.case()
        stage = case.get_frozen_stage_assessment("transport")
        context = case.get_available_context()
        only_context = metadata_catalog(case, "context_analyst", [context["evidence_id"]])
        self.assertFalse(any(r["code"] == "collect_weight_dimensions" for r in only_context.values()))
        full = metadata_catalog(case, "context_analyst", [stage["evidence_id"], context["evidence_id"]])
        check = next(r for r in full.values() if r["code"] == "collect_weight_dimensions")
        self.assertEqual(set(check["evidence_ids"]), {stage["evidence_id"], context["evidence_id"]})
        self.assertEqual(check["stage"], "transport")
        self.assertNotIn("review_seller_preparation", {r["code"] for r in full.values()})
        self.assertIn("not_confirmed_cause", check["meaning"])

    def test_strict_context_block_does_not_invent_missing_fields(self):
        case = self.case(mode="verified_availability_only")
        rec = case.get_available_context()
        catalog = metadata_catalog(case, "context_analyst", [rec["evidence_id"]])
        codes = {r["code"] for r in catalog.values()}
        self.assertIn("context_availability_unverified", codes)
        self.assertNotIn("missing_context", codes)
        self.assertIn("feature_not_in_event_contract", codes)
        self.assertIn("uncontrolled_factor", codes)

    def test_metadata_ids_reject_wrong_role_case_revision_kind_and_duplicates(self):
        case = self.case()
        catalog = self.stage_catalog(case)
        key = next(k for k, v in catalog.items() if v["metadata_kind"] == "unknown")
        for other_case, role, ids, kind in [(self.case(1), "process_analyst", [key], "unknown"),
                (case, "context_analyst", [key], "unknown"), (case, "process_analyst", [key, key], "unknown"),
                (case, "process_analyst", [key], "recommended_check")]:
            with self.assertRaises(ValueError):
                materialize_metadata(other_case, role, ids, catalog, kind, 3)
        early = self.store.case(case.order_id, self.store.events[case.order_id][0]["event_id"])
        self.stage_catalog(early)
        with self.assertRaises(ValueError):
            materialize_metadata(early, "process_analyst", [key], catalog, "unknown", 3)

    def test_forged_metadata_cannot_be_accepted_by_verifier(self):
        case = self.case()
        item = deepcopy(next(iter(self.stage_catalog(case).values())))
        item["code"] = "stage_endpoint_not_observed"  # Explicit protocol fault, not a new Olist row.
        bad = metadata_gate(case, "process_analyst", item)
        self.assertEqual(bad["code_gate"], "rejected")
        with self.assertRaises(ValueError):
            checked_verdicts({"verdicts": [{"claim_id": bad["claim_id"], "verdict": "accept", "reason": "supported"}]}, [bad])

    def test_schema_rejects_unscoped_strings_extra_notes_and_duplicate_ids(self):
        case = self.case()
        catalog = self.stage_catalog(case)
        schema = schemas("process_analyst", self.store.config["peer_plans"], metadata=catalog)[-1]["parameters"]
        key = next(k for k, v in catalog.items() if v["metadata_kind"] == "unknown")
        good = {"claim_ids": [], "unknown_ids": [key], "check_ids": []}
        validate(good, schema)
        with self.assertRaises(ValueError):
            validate({**good, "unknowns": ["stage_endpoint_not_observed"]}, schema)
        with self.assertRaises(ValueError):
            validate({**good, "unknown_ids": ["stage_endpoint_not_observed"]}, schema)
        with self.assertRaises(ValueError):
            materialize_selection(case, "process_analyst", {**good, "unknown_ids": [key, key]}, {}, catalog)

    def test_verifier_sees_and_checks_metadata_with_their_source_records(self):
        provider = MetadataProviderFixture()
        result = Coordinator(provider).run(self.case())
        self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
        self.assertTrue(result["metadata_verification_complete"])
        self.assertEqual(len(result["verified_notes"]), 2)
        self.assertEqual(result["recommended_checks"], ["review_carrier_route"])
        for item in result["verified_notes"] + result["verified_recommended_checks"]:
            self.assertTrue(item["supporting_evidence"])
            self.assertEqual(item["verifier_verdict"]["verdict"], "accept")
        packet = json.loads(next(r["payload"]["input"][0]["content"] for r in provider.records if r["role"] == "evidence_verifier"))["body"]
        self.assertEqual(sum("metadata_kind" in c for c in packet["claims"]), 3)

    def test_rejected_metadata_never_leaks_into_compatibility_summaries(self):
        result = Coordinator(MetadataProviderFixture(reject_metadata=True)).run(self.case())
        self.assertEqual(result["explanation_status"], "evidence_checked_with_rejections")
        self.assertEqual(result["recommended_checks"], [])
        self.assertEqual(result["unknowns"], ["cause_unverified"])
        self.assertEqual(len(result["rejected_metadata"]), 3)
        self.assertEqual(len(result["published_claims"]), 2)

    def test_verifier_cannot_ignore_metadata_while_approving_only_numbers(self):
        result = Coordinator(MetadataProviderFixture(omit_metadata=True)).run(self.case())
        self.assertEqual(result["explanation_status"], "inconclusive")
        self.assertFalse(result["metadata_verification_complete"])
        self.assertEqual(result["verified_notes"], [])
        self.assertEqual(result["published_claims"], [])
        self.assertEqual(result["recommended_checks"], [])
        self.assertTrue(result["contract_violations"])

    def test_invalid_note_selection_is_logged_before_bounded_repair(self):
        result = Coordinator(MetadataProviderFixture(bad_note_once=True)).run(self.case())
        self.assertEqual(result["explanation_status"], "evidence_checked", result.get("error"))
        self.assertEqual(len(result["contract_violations"]), 1)
        self.assertTrue(any("invalid_technical_metadata_id" in json.dumps(r) for r in result["raw_agent_outputs"]))
        self.assertLessEqual(sum(result["request_counts"].values()), 12)
        self.assertTrue(all(n <= 4 for n in result["request_counts"].values()))

    def test_all_real_pilot_metadata_options_pass_with_their_exact_evidence(self):
        for index in range(20):
            case = self.case(index)
            self.stage_catalog(case)
            case.get_available_context()
            for stage in STAGES:
                for plan in self.store.config["peer_plans"]:
                    case.compare_declared_peer_groups(stage, plan)
            for role in ["process_analyst", "context_analyst"]:
                for item in metadata_catalog(case, role, case.registry).values():
                    self.assertEqual(metadata_gate(case, role, item)["code_gate"], "passed")

    def test_saved_real_responses_retain_valid_limits_and_block_known_bad_notes(self):
        # The frozen v2 audit checks the complete ledger file as of its own run.
        # Mount those exact bytes for replay after the shared ledger has grown.
        archive = ROOT / "data/experiments/olist_agent_live_comparison_v1_case2/budget_before.sqlite"
        if archive.exists():
            with TemporaryDirectory() as directory:
                historical = Path(directory)
                (historical / "data/runtime").mkdir(parents=True)
                for name in ("data/experiments", "data/raw", "data/runtime/multi_agent_results"):
                    (historical / name).symlink_to(ROOT / name, target_is_directory=True)
                (historical / "data/runtime/multi_agent_budget.sqlite").symlink_to(archive)
                with patch("replay_saved_metadata.ROOT", historical):
                    summary = run_offline()
        else:
            summary = run_offline()
        self.assertEqual(summary["real_v2_cases"], 6)
        self.assertEqual(summary["original_model_requests_verified"], 39)
        self.assertEqual(summary["historical_scalar_claims_still_match"], 34)
        self.assertEqual(summary["known_false_missing_endpoint_cases_blocked"], [2, 8, 9, 16])
        self.assertEqual(summary["new_API_requests"], 0)
        self.assertTrue(summary["shared_ledger_unchanged"])
        self.assertFalse(summary["AI_quality_improvement_demonstrated"])
        conflict = next(r for r in summary["cases"] if r["case_index"] == 16)
        n = [r for r in conflict["metadata_checks"] if r["legacy_code"] == "insufficient_comparable_orders"]
        self.assertTrue(n)
        self.assertTrue(all(r["status"] == "rejected_no_supporting_condition" for r in n))
        self.assertTrue(any(r["legacy_code"] == "timeline_conflict" and r["status"] == "eligible_evidence_exists_not_a_new_model_selection" for r in conflict["metadata_checks"]))


if __name__ == "__main__":
    unittest.main()
