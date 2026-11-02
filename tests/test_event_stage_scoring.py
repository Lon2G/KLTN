"""Real Olist replay fixtures; mutations are test-only, never research observations."""

from collections import defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/04_streaming"))
import run_stage_scoring_replay as run
import score_event_stages as scoring

core, replay = scoring.core, run.replay


class EventStageScoringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.inputs = run.load_inputs()
        cls.scorer = cls.inputs["scorer"]
        cls.results = [json.loads(line) for line in (run.DEFAULT_OUTPUT / "latest_stage_scores.jsonl").read_text().splitlines()]
        cls.decisions = [json.loads(line) for line in (run.DEFAULT_OUTPUT / "stage_decisions.jsonl").read_text().splitlines()]
        cls.by_order = defaultdict(list)
        for event in cls.inputs["events"]:
            cls.by_order[event["order_id"]].append(event)
        cls.example = cls.by_order["000e63d38ae8c00bbcb5a30573b99628"]

    @classmethod
    def store(cls, path=":memory:", scorer=None):
        return scoring.ScoredStateStore(path, cls.inputs["start"], cls.inputs["cutoff"], scorer or cls.scorer)

    def test_real_population_event_count_and_two_separate_scenarios(self):
        self.assertEqual(len(self.inputs["events"]), 7076)
        self.assertEqual(len(self.scorer.contexts), 1853)
        self.assertEqual(len(self.results), 2 * 1853 * 3)
        for mode in scoring.MODES:
            rows = [r for r in self.results if r["mode"] == mode]
            self.assertEqual({r["order_id"] for r in rows}, set(self.scorer.contexts))

    def test_verified_only_mode_abstains_and_never_selects_peers(self):
        with patch.object(self.scorer.bank, "select", side_effect=AssertionError("must not use unknown context")):
            state = core.state_from_events(self.example)
            for stage in core.STAGES:
                result = self.scorer.assess(state, stage, scoring.MODES[0])
                self.assertEqual(result["assessment_status"], "context_availability_unverified")
                self.assertIsNone(result["delay_score_0_100"])
        self.assertTrue(all(r["delay_score_0_100"] is None for r in self.results if r["mode"] == scoring.MODES[0]))

    def test_every_scored_output_discloses_assumption_not_verified_availability(self):
        scored = [r for r in self.results if r["delay_score_0_100"] is not None]
        self.assertGreater(len(scored), 0)
        for r in scored:
            self.assertEqual(r["mode"], scoring.MODES[1])
            self.assertTrue(r["context_assumption_used"])
            self.assertEqual(r["context_availability_basis"], "assumed_static_at_observed_purchase_not_source_verified")
            self.assertGreaterEqual(r["selected_reference_n"], 30)
            self.assertTrue(0 <= r["delay_score_0_100"] <= 100)

    def test_static_projection_contains_no_target_times_status_payments_or_reviews(self):
        for c in self.scorer.contexts.values():
            self.assertEqual(set(c), scoring.CONTEXT_FIELDS)
        contexts = deepcopy(self.scorer.contexts)
        oid = next(iter(contexts))
        for field in ["order_delivered_customer_date", "order_status", "review_score", "payment_value", "available_at"]:
            altered = deepcopy(contexts)
            altered[oid][field] = "test-only"
            with self.assertRaisesRegex(ValueError, "allowlist"):
                scoring.StageScorer(self.scorer.bank, altered, self.scorer.held_ids)

    def test_first_stage_scored_at_approval_without_delivery(self):
        with self.store() as store:
            for event in self.example[:2]:
                store.apply(event)
            rows = [r for r in store.stage_results() if r["mode"] == scoring.MODES[1]]
            approval = next(r for r in rows if r["stage"] == "approval")
            self.assertEqual(approval["signed_duration_seconds"], 1163)
            self.assertIsNotNone(approval["delay_score_0_100"])
            self.assertTrue(all(r["delay_score_0_100"] is None for r in rows if r["stage"] != "approval"))
            self.assertFalse(store.states()[0]["delivery_observed"])
            self.assertEqual({d["event_id"] for d in store.scoring_journal() if d["result"]["delay_score_0_100"] is not None},
                             {self.example[1]["event_id"]})

    def test_second_stage_scored_at_handoff_without_delivery(self):
        with self.store() as store:
            for event in self.example[:3]:
                store.apply(event)
            rows = [r for r in store.stage_results() if r["mode"] == scoring.MODES[1]]
            self.assertEqual(sum(r["delay_score_0_100"] is not None for r in rows), 2)
            self.assertFalse(store.states()[0]["delivery_observed"])

    def test_scorer_does_not_access_context_before_purchase_or_with_missing_endpoints(self):
        with patch.object(self.scorer.bank, "select", side_effect=AssertionError("no context lookup")):
            for events in [self.example[:1], self.example[1:3]]:
                state = core.state_from_events(events)
                for stage in core.STAGES:
                    self.assertIsNone(self.scorer.assess(state, stage, scoring.MODES[1])["delay_score_0_100"])

    def test_each_decision_has_only_observed_endpoints_and_reference_precedes_event(self):
        seen, cursor = defaultdict(list), 0
        refs = {g["reference_id"]: g for g in replay.read_csv(run.DEFAULT_OUTPUT / "reference_groups.csv")}
        for d in self.decisions:
            while cursor < d["sequence"]:
                event = self.inputs["events"][cursor]
                seen[event["order_id"]].append(event)
                cursor += 1
            r = d["result"]
            state = core.state_from_events(seen[r["order_id"]])
            self.assertEqual(d["delivery_observed_when_evaluated"], state["delivery_observed"])
            for activity, timestamp in [(r["start_activity"], r["start_timestamp"]), (r["end_activity"], r["end_timestamp"])]:
                self.assertEqual(timestamp, state["observed_milestones"].get(activity))
                if timestamp:
                    self.assertLessEqual(timestamp, d["event_time"])
            if r["delay_score_0_100"] is not None:
                self.assertIn("Purchased", state["observed_milestones"])
                self.assertLess(refs[r["selected_reference_id"]]["latest_end"], self.inputs["start"])
            self.assertEqual(core.fingerprint(r), d["result_sha256"])

    def test_real_warnings_can_precede_delivery_without_forecasting_claim(self):
        early = [d for d in self.decisions if d["action"] == "warning_emitted" and not d["delivery_observed_when_evaluated"]]
        self.assertEqual(len(early), 95)
        self.assertTrue(all(d["result"]["stage"] in {"approval", "preparation_handoff"} for d in early))
        self.assertTrue(all(d["result"]["delay_score_0_100"] > 0 for d in early))

    def test_indexed_reference_membership_matches_independent_record_filter(self):
        history = self.scorer.bank.reference.to_dict("records")
        for group in replay.read_csv(run.DEFAULT_OUTPUT / "reference_groups.csv"):
            context = json.loads(group["context_json"])
            expected = [r["order_id"] for r in history if r["stage"] == group["stage"] and r["all_pairs_quality_eligible"]
                        and (group["scope"] != "product_context" or r["product_attributable"])
                        and all(not pd.isna(r[k]) and r[k] == v for k, v in context.items())]
            self.assertEqual(expected, json.loads(group["order_ids_json"]))
            self.assertEqual(len(expected), int(group["n"]))
            self.assertFalse(set(expected) & set(self.scorer.contexts))

    def test_prefix_replay_equals_saved_prefix_without_future_events(self):
        n = 160
        with self.store() as store:
            for e in self.inputs["events"][:n]:
                store.apply(e)
            self.assertEqual(list(store.scoring_journal()), [d for d in self.decisions if d["sequence"] <= n])

    def test_later_timeline_conflict_withdraws_prior_assessment_without_erasing_history(self):
        events = deepcopy(self.example)
        approval, handoff = [core.parse_time(events[i]["occurred_at"]) for i in [1, 2]]
        # Test-only corruption of one REAL order's delivery time, never exported.
        between = (approval + (handoff - approval) / 2).replace(microsecond=0).strftime(core.TIME_FORMAT)
        events[3] = core.make_event(events[3]["order_id"], "Delivered", between)
        with self.store() as store:
            for e in sorted(events, key=core.event_key):
                store.apply(e)
            rows = [r for r in store.stage_results() if r["mode"] == scoring.MODES[1]]
            self.assertTrue(all(r["delay_score_0_100"] is None for r in rows))
            self.assertTrue(all(r["assessment_status"] == "observed_timeline_conflict" for r in rows))
            journal = list(store.scoring_journal())
            self.assertTrue(any(d["action"] == "completed_stage_scored" for d in journal))
            self.assertTrue(any(d["action"] == "assessment_withdrawn_after_new_evidence" for d in journal))

    def test_existing_real_inversions_withheld_without_negative_or_zero_scores(self):
        bad = {r["order_id"] for r in self.results if r["assessment_status"] == "observed_timeline_conflict"}
        self.assertEqual(len(bad), 51)
        self.assertTrue(all(r["delay_score_0_100"] is None for r in self.results if r["order_id"] in bad))

    def test_equal_recorded_times_do_not_become_normal(self):
        events = deepcopy(self.example[:2])
        events[1] = core.make_event(events[1]["order_id"], "Approved", events[0]["occurred_at"])
        state = core.state_from_events(events)
        r = self.scorer.assess(state, "approval", scoring.MODES[1])
        self.assertEqual(r["assessment_status"], "equal_timestamp_order_unresolved")
        self.assertEqual(r["duration_days"], 0)
        self.assertIsNone(r["delay_score_0_100"])

    def test_geographic_quality_failure_cannot_use_geography_free_fallback(self):
        contexts = deepcopy(self.scorer.contexts)
        contexts[self.example[0]["order_id"]]["all_pairs_quality_eligible"] = False
        scorer = scoring.StageScorer(self.scorer.bank, contexts, self.scorer.held_ids)
        with patch.object(scorer.bank, "select", side_effect=AssertionError("no fallback")):
            r = scorer.assess(core.state_from_events(self.example), "approval", scoring.MODES[1])
        self.assertEqual(r["assessment_status"], "geographic_quality_not_eligible")
        self.assertIsNone(r["delay_score_0_100"])

    def test_sparse_or_unknown_reference_is_unassessed(self):
        contexts = deepcopy(self.scorer.contexts)
        contexts[self.example[0]["order_id"]]["distance_band"] = "test_only_nonexistent_band"
        scorer = scoring.StageScorer(self.scorer.bank, contexts, self.scorer.held_ids)
        r = scorer.assess(core.state_from_events(self.example), "approval", scoring.MODES[1])
        self.assertEqual(r["assessment_status"], "insufficient_matched_reference")
        self.assertIsNone(r["delay_score_0_100"])

    def test_protected_pilot_cases_never_scored_in_either_mode(self):
        self.assertEqual(len(self.scorer.held_ids), 2)
        rows = [r for r in self.results if r["order_id"] in self.scorer.held_ids]
        self.assertEqual(len(rows), 12)
        self.assertTrue(all(r["assessment_status"] == "protected_review_hold" and r["delay_score_0_100"] is None for r in rows))

    def test_partial_no_warning_is_unknown_not_false(self):
        state = core.state_from_events(self.example[:2])
        results = [self.scorer.assess(state, s, scoring.MODES[1]) for s in core.STAGES]
        order = scoring.aggregate_orders(results)[0]
        self.assertEqual(order["coverage"], "partial")
        self.assertEqual(order["max_stage_delay_score"], 0)
        self.assertIsNone(order["any_long_stage_warning"])

    def test_score_zero_can_have_nonzero_signed_difference_from_mean(self):
        zero = [r for r in self.results if r["delay_score_0_100"] == 0]
        self.assertTrue(any(r["difference_from_reference_mean_days"] > 0 for r in zero))
        self.assertTrue(any(r["difference_from_reference_mean_days"] < 0 for r in zero))
        self.assertTrue(all(r["long_stage_warning"] is False for r in zero))

    def test_product_or_fallback_identity_and_every_existing_scored_value_unchanged(self):
        rows, summary = run.parity_check(self.results, self.inputs["saved_scores"])
        self.assertEqual(summary["existing_scored_stages_compared"], 4233)
        self.assertEqual(len(rows), 4233)
        self.assertTrue(summary["all_exactly_equal"])

    def test_reference_cannot_contain_current_orders_or_future_endpoints(self):
        reference = self.scorer.bank.reference.copy()
        reference.loc[0, "end_timestamp"] = pd.Timestamp(self.inputs["start"])
        with self.assertRaisesRegex(ValueError, "train reference"):
            scoring.ReferenceBank(reference, self.scorer.contexts, self.inputs["start"])
        reference = self.scorer.bank.reference.copy()
        reference.loc[0, "order_id"] = self.example[0]["order_id"]
        with self.assertRaisesRegex(ValueError, "train reference"):
            scoring.ReferenceBank(reference, self.scorer.contexts, self.inputs["start"])

    def test_atomic_rollback_includes_raw_state_and_scoring_journal(self):
        with self.store() as store:
            original, count = self.scorer.assess, 0
            def fail_after_partial_scoring(*args):
                nonlocal count
                count += 1
                if count == 4:
                    raise RuntimeError("test-only scorer failure after the first scenario")
                return original(*args)
            with patch.object(self.scorer, "assess", side_effect=fail_after_partial_scoring):
                with self.assertRaises(RuntimeError):
                    store.apply(self.example[0])
            self.assertEqual(store.states(), [])
            self.assertEqual(list(store.journal()), [])
            self.assertEqual(store.stage_results(), [])
            self.assertEqual(list(store.scoring_journal()), [])
            self.assertTrue(store.apply(self.example[0])["applied"])

    def test_duplicate_retries_after_close_never_emit_second_warning(self):
        with self.store() as store:
            for e in self.example:
                store.apply(e)
            before, journal = store.stage_results(), list(store.scoring_journal())
            store.finish()
            for e in reversed(self.example):
                self.assertFalse(store.apply(e)["applied"])
            self.assertEqual(store.stage_results(), before)
            self.assertEqual(list(store.scoring_journal()), journal)

    def test_restart_reproduces_scored_prefix(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite"
            with self.store(path) as store:
                for e in self.inputs["events"][:80]:
                    store.apply(e)
            with self.store(path) as store:
                for e in self.inputs["events"][:160]:
                    store.apply(e)
                self.assertEqual(list(store.scoring_journal()), [d for d in self.decisions if d["sequence"] <= 160])

    def test_changed_context_contract_cannot_resume_existing_checkpoint(self):
        contexts = deepcopy(self.scorer.contexts)
        contexts[self.example[0]["order_id"]]["item_count"] += 1
        other = scoring.StageScorer(self.scorer.bank, contexts, self.scorer.held_ids)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite"
            with self.store(path):
                pass
            with self.assertRaisesRegex(ValueError, "contract differs"):
                self.store(path, other)

    def test_unscored_nonempty_checkpoint_cannot_be_retrofitted(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite"
            with core.StateStore(path, self.inputs["start"], self.inputs["cutoff"]) as store:
                store.apply(self.example[0])
            with self.assertRaisesRegex(ValueError, "contract differs"):
                self.store(path)

    def test_no_overwrite_or_outputs_outside_experiment_directory(self):
        for path in [run.DEFAULT_OUTPUT, ROOT / "data/raw", ROOT / "data/experiments/nested/new"]:
            with self.assertRaisesRegex(ValueError, "NEW direct child"):
                run.build(path)

    def test_published_snapshot_reproduces_full_event_scoring(self):
        verified = run.verify_snapshot()
        self.assertTrue(verified["verified"])
        self.assertEqual(verified["events"], 7076)
        self.assertEqual(verified["stage_rows"], 11118)
        self.assertEqual(verified["batch_parity_stages"], 4233)


if __name__ == "__main__":
    unittest.main()
