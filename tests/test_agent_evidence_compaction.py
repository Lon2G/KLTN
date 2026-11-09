"""Source retention, isolation and agent integration on real frozen Olist evidence."""
from copy import deepcopy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts/06_agent_evaluation/compact"))
from evidence_wire import (EvidenceEncoder, EvidenceDecoder, compact_payload, expand_payload,
                           REFERENCE, VERSION, canonical, sha)
from compact_runner import run_compact
from validate_offline import SavedWireReplay
from run_smoke import (DEFAULT, OUTPUT as LIVE, KEYS, EvidenceStore, Coordinator, initial_evidence, read, single)
from test_multi_agent_integration import ScriptedProvider
from test_multi_agent_metadata import MetadataProviderFixture
from test_agent_comparison import SingleFixture


class ExpandedFixture:
    """Technical replies after round-trip decoding; never claims new AI behavior."""
    execution_kind = "technical_scripted_test_not_AI_inference"

    def __init__(self, fixture):
        self.fixture = fixture
        self.received = []

    def call(self, **request):
        self.received.append(deepcopy(request))
        return self.fixture.call(**{**request, "payload": expand_payload(request["payload"])})


class EvidenceCompactionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = EvidenceStore()
        cls.rows = read(DEFAULT / "cohort.json")

    def setUp(self):
        guard = patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Offline only"))
        guard.start()
        self.addCleanup(guard.stop)

    def case(self, index=2, mode="assumed_static_context_at_purchase"):
        row = self.rows[index]
        return self.store.case(row["order_id"], row["event_id"], mode)

    def test_repeated_record_keeps_original_id_values_and_new_catalog_additions(self):
        record = self.case().get_observed_state()
        encoder, decoder = EvidenceEncoder(), EvidenceDecoder()
        first = encoder.packet(record)
        self.assertEqual(decoder.packet(first), record)
        addition = {**record, "claim_catalog": [{"evidence_id": record["evidence_id"], "choices": {}}]}
        repeated = encoder.packet(addition)
        self.assertEqual(repeated["new_evidence"], {})
        self.assertEqual(repeated["body"][REFERENCE], "e0")
        self.assertEqual(decoder.packet(repeated), addition)

    def test_data_tampering_with_same_id_is_rejected(self):
        record = self.case().get_frozen_stage_assessment("transport")
        record["data"]["duration_days"] += 1
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            EvidenceEncoder().packet(record)

    def test_case_time_mode_and_source_scopes_cannot_be_mixed(self):
        record = self.case().get_observed_state()
        cases = [self.case(0), self.case(mode="verified_availability_only")]
        current = self.case()
        cases.append(self.store.case(current.order_id, current.events[0]["event_id"]))
        for other in cases:
            with self.subTest(case=other.case_key, mode=other.mode, time=other.asof):
                encoder = EvidenceEncoder()
                encoder.packet(record)
                with self.assertRaisesRegex(ValueError, "mixes evidence"):
                    encoder.packet(other.get_observed_state())

    def test_reference_cannot_use_evidence_from_another_role_or_later_packet(self):
        encoder = EvidenceEncoder()
        record = self.case().get_observed_state()
        first = encoder.packet(record)
        repeated = encoder.packet(record)
        with self.assertRaisesRegex(ValueError, "unavailable"):
            EvidenceDecoder().packet(repeated)
        decoder = EvidenceDecoder()
        self.assertEqual(decoder.packet(first), record)
        self.assertEqual(decoder.packet(repeated), record)

    def test_reference_cannot_override_source_values(self):
        encoder, decoder = EvidenceEncoder(), EvidenceDecoder()
        record = self.case().get_observed_state()
        decoder.packet(encoder.packet(record))
        repeated = encoder.packet(record)
        repeated["body"]["data"] = {}
        with self.assertRaisesRegex(ValueError, "override"):
            decoder.packet(repeated)

    def test_wire_definition_tampering_is_rejected(self):
        packed = EvidenceEncoder().packet(self.case().get_frozen_stage_assessment("transport"))
        packed["new_evidence"]["e0"]["data"]["duration_days"] += 1
        with self.assertRaisesRegex(ValueError, "fingerprint"):
            EvidenceDecoder().packet(packed)

    def test_nulls_negative_durations_and_insufficient_support_survive_exactly(self):
        cases = [self.case(16), self.case()]
        cases.append(self.store.case(cases[1].order_id, cases[1].events[0]["event_id"]))
        observed_null, observed_negative, observed_low_support = False, False, False
        for case in cases:
            records = [case.get_frozen_stage_assessment(s) for s in ("approval", "preparation_handoff", "transport")]
            records.append(case.compare_declared_peer_groups("transport", "same_product_peers"))
            encoder, decoder = EvidenceEncoder(), EvidenceDecoder()
            for _ in range(2):
                self.assertEqual(decoder.packet(encoder.packet(records)), records)
            for record in records:
                data = record["data"]
                observed_null |= record["kind"] == "frozen_stage_assessment" and data["end_timestamp"] is None
                duration = data.get("signed_duration_seconds")
                observed_negative |= duration is not None and duration < 0
                observed_low_support |= data.get("status") == "insufficient_comparable_orders"
        self.assertTrue(observed_null and observed_negative and observed_low_support)

    def test_payload_and_reserved_marker_cannot_be_compacted_twice(self):
        with self.assertRaisesRegex(ValueError, "reserved"):
            EvidenceEncoder().packet({REFERENCE: "e0"})
        fixture = SingleFixture()
        single(self.case(), fixture)
        packed, _ = compact_payload(fixture.records[0]["payload"])
        with self.assertRaises(ValueError):
            compact_payload(packed)

    def test_both_saved_histories_match_all_source_payloads_including_blocked_requests(self):
        for arm in KEYS:
            case = self.case()
            initial_evidence(case)
            provider = SavedWireReplay(arm)
            result = run_compact(case, provider, arm)
            original = read(LIVE / (arm + ".json"))
            self.assertEqual(result["raw_agent_outputs"], original["raw_agent_outputs"])
            self.assertEqual(result["detector"], original["detector"])
            self.assertEqual(result["explanation_status"], original["explanation_status"])
            last = result["wire_requests"][-1]
            self.assertLess(last["compact_utf8_bytes"], last["original_utf8_bytes"])
            self.assertIsNone(last["official_input_tokens"])
            self.assertEqual(result["run_key"], KEYS[arm] + "-" + VERSION)
            self.assertIsNone(next(provider.receipts, None))

    def test_twenty_real_cases_reach_same_fixture_outputs_through_both_engines(self):
        for index in range(20):
            for arm in KEYS:
                with self.subTest(index=index, arm=arm):
                    fixture = ScriptedProvider if arm == "three_agents" else SingleFixture
                    expected = (Coordinator(fixture(), KEYS[arm]).run(self.case(index)) if arm == "three_agents"
                                else single(self.case(index), fixture()))
                    provider = ExpandedFixture(fixture())
                    result = run_compact(self.case(index), provider, arm)
                    for field in ("explanation_status", "published_claims", "verified_notes", "detector"):
                        self.assertEqual(result[field], expected[field], field)
                    for request in provider.received:
                        self.assertEqual(request["payload"]["max_output_tokens"], 1200)
                        self.assertTrue(request["run_key"].endswith("-" + VERSION))
                    self.assertEqual([t["request_id"] for t in result["trace"] if t["kind"] == "model_request"],
                                     [r["request_id"] for r in provider.received])

    def test_metadata_verifier_rejection_and_contract_repair_keep_same_outputs(self):
        for options in ({}, {"reject_metadata": True}, {"omit_metadata": True}, {"bad_note_once": True}):
            with self.subTest(options=options):
                expected = Coordinator(MetadataProviderFixture(**options), KEYS["three_agents"]).run(self.case(0))
                result = run_compact(self.case(0), ExpandedFixture(MetadataProviderFixture(**options)), "three_agents")
                for field in ("verified_notes", "verified_recommended_checks", "rejected_metadata",
                              "metadata_verification_complete", "explanation_status", "contract_violations"):
                    self.assertEqual(result[field], expected[field], field)

    def test_revision_requests_include_complete_local_definitions(self):
        provider = ExpandedFixture(ScriptedProvider(always_revise=True))
        result = run_compact(self.case(0), provider, "three_agents")
        self.assertEqual(result["request_counts"], {role: 3 for role in ("process_analyst", "context_analyst", "evidence_verifier")})
        for request in provider.received:
            # A fresh decoder reconstructs each whole request independently.
            restored = expand_payload(request["payload"])
            self.assertTrue(restored["input"])

    def test_single_four_request_cap_and_failed_contract_are_preserved(self):
        result = run_compact(self.case(), ExpandedFixture(SingleFixture(always_invalid=True)), "single_agent")
        self.assertEqual(len(result["wire_requests"]), 4)
        self.assertEqual(result["explanation_status"], "inconclusive")

