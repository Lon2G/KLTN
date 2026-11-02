"""Actual Olist milestone fixtures. All mutations are isolated test-only fault injections."""

from collections import Counter, defaultdict
from copy import deepcopy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts/04_streaming"))
import event_state as core
import replay_olist_events as replay


class EventReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.protocol, cls.events, cls.lineage, cls.scope, cls.source = replay.load_inputs()
        cls.start, cls.cutoff = [cls.protocol[n] for n in ["start_inclusive", "cutoff_exclusive"]]
        with cls.store() as store:
            for event in cls.events:
                store.apply(event)
            cls.states, cls.journal = store.states(), list(store.journal())
        cls.by_order = defaultdict(list)
        for event in cls.events:
            cls.by_order[event["order_id"]].append(event)
        good = next(s for s in cls.states if s["all_four_observed"] and not s["observed_inversions"])
        cls.good_events = cls.by_order[good["order_id"]]

    @classmethod
    def store(cls, path=":memory:"):
        return core.StateStore(path, cls.start, cls.cutoff)

    def test_exact_scope_source_event_counts_and_no_complete_case_filter(self):
        self.assertEqual(len(self.scope), 1853)
        self.assertEqual(len(self.events), 7076)
        self.assertEqual({s["order_id"] for s in self.states}, self.scope)
        self.assertEqual(Counter(s["observed_event_count"] for s in self.states), {1: 2, 2: 49, 3: 232, 4: 1570})
        self.assertEqual(self.source["offline_only_event_counts"], {"visible": 7076, "at_or_after_cutoff": 301})

    def test_events_are_ordered_unique_and_have_only_allowed_fields(self):
        self.assertEqual(self.events, sorted(self.events, key=core.event_key))
        self.assertEqual(len({e["event_id"] for e in self.events}), len(self.events))
        for e in self.events:
            self.assertEqual(set(e), core.EVENT_FIELDS)
            core.validate_event(e)
            self.assertTrue(self.start <= e["occurred_at"] < self.cutoff)

    def test_each_visible_event_matches_raw_and_clean_record_and_original_field(self):
        clean = ROOT / self.protocol["clean_directory"]
        raw = ROOT / "data/raw"
        orders = list(replay.read_csv(clean / replay.ORDERS_FILE))
        raw_orders = list(replay.read_csv(raw / replay.ORDERS_FILE))
        events = list(replay.read_csv(clean / "event_log.csv"))
        lookup = {e["event_id"]: e for e in self.events}
        for line in self.lineage:
            e = lookup[line["event_id"]]
            source = events[line["clean_event_record"] - 1]
            self.assertEqual((source["case_id"], source["activity"], source["timestamp"]),
                             (e["order_id"], e["activity"], e["occurred_at"]))
            for rows, field in [(orders, "clean_order_record"), (raw_orders, "raw_order_record")]:
                order = rows[line[field] - 1]
                self.assertEqual(order["order_id"], e["order_id"])
                self.assertEqual(order[line["source_field"]], e["occurred_at"])

    def test_every_journal_state_uses_only_events_observed_at_that_point(self):
        seen = defaultdict(list)
        for sequence, row in enumerate(self.journal, 1):
            e = row["event"]
            seen[e["order_id"]].append(e)
            self.assertEqual(row["sequence"], sequence)
            state = core.state_from_events(seen[e["order_id"]])
            self.assertEqual(state, row["state_after"])
            self.assertEqual(core.fingerprint(state), row["state_sha256"])
            self.assertTrue(all(t <= e["occurred_at"] for t in state["observed_milestones"].values()))

    def test_future_valid_mutation_or_append_does_not_change_visible_payloads(self):
        rows = [{"case_id": e["order_id"], "activity": e["activity"], "timestamp": e["occurred_at"]} for e in self.events]
        future = {"case_id": self.events[0]["order_id"], "activity": "Delivered", "timestamp": "2018-07-01 00:00:00"}
        first, _ = replay.select_visible_events([*rows, future], self.scope, self.start, self.cutoff)
        future["timestamp"] = "2018-08-01 00:00:00"
        second, _ = replay.select_visible_events([*rows, future, future], self.scope, self.start, self.cutoff)
        self.assertEqual(first, self.events)
        self.assertEqual(second, self.events)

    def test_source_order_does_not_change_canonical_stream(self):
        rows = [{"case_id": e["order_id"], "activity": e["activity"], "timestamp": e["occurred_at"]} for e in reversed(self.events)]
        events, _ = replay.select_visible_events(rows, self.scope, self.start, self.cutoff)
        self.assertEqual(events, self.events)

    def test_prefix_does_not_initialize_future_order_states(self):
        with self.store() as store:
            self.assertEqual(store.states(), [])
            for event in self.events[:50]:
                store.apply(event)
            self.assertEqual({s["order_id"] for s in store.states()}, {e["order_id"] for e in self.events[:50]})
            self.assertLess(len(store.states()), len(self.scope))

    def test_duplicate_retries_are_noops_even_after_newer_events_and_close(self):
        with self.store() as store:
            for event in self.events[:100]:
                store.apply(event)
            before, journal = store.states(), list(store.journal())
            store.finish()
            for event in reversed(self.events[:100]):
                self.assertFalse(store.apply(event)["applied"])
            self.assertEqual(store.states(), before)
            self.assertEqual(list(store.journal()), journal)

    def test_restart_and_replay_from_beginning_preserves_semantics(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite"
            with self.store(path) as store:
                for event in self.events[:80]:
                    store.apply(event)
            with self.store(path) as store:
                applied = [store.apply(e)["applied"] for e in self.events[:200]]
                self.assertEqual(applied, [False] * 80 + [True] * 120)
                self.assertEqual(list(store.journal()), self.journal[:200])

    def test_different_window_checkpoint_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "state.sqlite"
            with self.store(path):
                pass
            with self.assertRaisesRegex(ValueError, "Checkpoint contract differs"):
                core.StateStore(path, self.start, "2018-07-01 00:00:00")

    def test_state_write_failure_rolls_back_both_journal_and_state(self):
        with self.store() as store:
            with patch.object(store, "_write_state", side_effect=RuntimeError("test-only failure")):
                with self.assertRaises(RuntimeError):
                    store.apply(self.events[0])
            self.assertEqual(store.states(), [])
            self.assertEqual(list(store.journal()), [])
            self.assertTrue(store.apply(self.events[0])["applied"])

    def test_new_late_arrival_is_rejected_not_rewritten(self):
        with self.store() as store:
            store.apply(self.events[1])
            with self.assertRaisesRegex(ValueError, "Out-of-order"):
                store.apply(self.events[0])
            self.assertEqual(len(list(store.journal())), 1)

    def test_same_id_with_modified_payload_is_rejected(self):
        changed = deepcopy(self.events[0])
        changed["activity"] = "Delivered"
        with self.store() as store:
            store.apply(self.events[0])
            with self.assertRaisesRegex(ValueError, "ID does not match"):
                store.apply(changed)
            self.assertEqual(len(list(store.journal())), 1)

    def test_different_id_same_milestone_is_rejected(self):
        e = self.events[0]
        changed = core.make_event(e["order_id"], e["activity"], "2018-03-02 00:00:00")
        with self.store() as store:
            store.apply(e)
            with self.assertRaisesRegex(ValueError, "Conflicting record"):
                store.apply(changed)

    def test_extra_fields_final_status_reviews_and_precomputed_flags_forbidden(self):
        for field in ["order_status", "order_delivered_customer_date", "has_event_at_or_after_cutoff",
                      "source_missing", "payment_value", "review_score", "order_estimated_delivery_date"]:
            with self.subTest(field=field), self.store() as store:
                with self.assertRaisesRegex(ValueError, "allowlist"):
                    store.apply({**self.events[0], field: None})

    def test_before_window_and_exact_cutoff_are_excluded_and_rejected(self):
        for timestamp in ["2018-02-28 23:59:59", self.cutoff]:
            event = core.make_event(self.events[0]["order_id"], "Purchased", timestamp)
            rows = [{"case_id": event["order_id"], "activity": event["activity"], "timestamp": timestamp}]
            self.assertEqual(replay.select_visible_events(rows, self.scope, self.start, self.cutoff)[0], [])
            with self.store() as store:
                with self.assertRaisesRegex(ValueError, "outside"):
                    store.apply(event)

    def test_unobserved_endpoints_are_neither_zero_nor_normal(self):
        state = core.state_from_events(self.good_events[:1])
        self.assertEqual(state["anomaly_assessment"], "not_run_in_replay_infrastructure")
        for stage in state["stages"].values():
            self.assertIsNone(stage["signed_duration_seconds"])
            self.assertIsNone(stage["duration_days"])
        self.assertFalse(state["delivery_observed"])

    def test_completed_transition_can_exist_before_delivery(self):
        state = core.state_from_events(self.good_events[:2])
        self.assertIsNotNone(state["stages"]["approval"]["duration_days"])
        self.assertFalse(state["delivery_observed"])
        self.assertIsNone(state["stages"]["preparation_handoff"]["duration_days"])

    def test_inversions_are_observed_only_when_both_real_events_arrive(self):
        state = next(s for s in self.states if s["observed_inversions"])
        events = self.by_order[state["order_id"]]
        first = core.state_from_events(events[:1])
        self.assertEqual(first["observed_inversions"], [])
        self.assertEqual(core.state_from_events(events)["observed_inversions"], state["observed_inversions"])
        negative = [v for v in state["stages"].values() if v["observation_status"] == "negative_recorded_duration"]
        self.assertTrue(negative)
        self.assertTrue(all(v["duration_days"] is None and v["signed_duration_seconds"] < 0 for v in negative))

    def test_real_equal_timestamps_are_retained_without_asserting_causality(self):
        clean = ROOT / self.protocol["clean_directory"]
        order = next(r for r in replay.read_csv(clean / replay.ORDERS_FILE)
                     if r["order_purchase_timestamp"] and r["order_purchase_timestamp"] == r["order_approved_at"])
        events = sorted([core.make_event(order["order_id"], a, order[f]) for a, f in core.ACTIVITIES.items() if order[f]], key=core.event_key)
        with core.StateStore(":memory:", "2016-01-01 00:00:00", "2019-01-01 00:00:00") as store:
            for event in events:
                store.apply(event)
            state = store.states()[0]
        self.assertIn(["Purchased", "Approved"], state["observed_equal_time_pairs"])
        self.assertEqual(state["stages"]["approval"]["signed_duration_seconds"], 0)
        self.assertEqual(state["stages"]["approval"]["observation_status"], "equal_recorded_time_order_unresolved")

    def test_unknown_activity_invalid_timestamp_and_unknown_keys_rejected(self):
        for activity, timestamp in [("Canceled", self.events[0]["occurred_at"]), ("Purchased", "2018-03-01"),
                                    ("Purchased", "2018-03-01 00:00:00Z"), ("Purchased", "2018-02-30 00:00:00")]:
            with self.subTest(activity=activity, timestamp=timestamp), self.store() as store:
                with self.assertRaises(ValueError):
                    store.apply(core.make_event(self.events[0]["order_id"], activity, timestamp))

    def test_source_duplicate_not_silently_deduplicated(self):
        e = self.events[0]
        row = {"case_id": e["order_id"], "activity": e["activity"], "timestamp": e["occurred_at"]}
        with self.assertRaisesRegex(ValueError, "Duplicate source"):
            replay.select_visible_events([row, row], self.scope, self.start, self.cutoff)

    def test_finish_does_not_invent_business_events_or_zero_fill_missing_stages(self):
        with self.store() as store:
            store.apply(self.events[0])
            before = store.states()
            store.finish()
            store.finish()
            self.assertEqual(store.states(), before)
            self.assertEqual(len(list(store.journal())), 1)
            with self.assertRaisesRegex(ValueError, "already closed"):
                store.apply(self.events[1])

    def test_output_guard_refuses_existing_and_outside_directories(self):
        for path in [ROOT / "data/raw", ROOT / "data/experiments/bed_bath_table_v1", ROOT / "data/experiments/nested/new"]:
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "NEW direct child"):
                replay.build(path)

    def test_published_snapshot_reproduces_exactly(self):
        result = replay.verify_snapshot()
        self.assertEqual(result, {"verified": True, "orders": 1853, "events": 7076, "semantic_replay_reproduced": True})

    def test_runtime_is_separate_measurement_not_source_ingestion_or_model_latency(self):
        runtime = replay.read_json(replay.DEFAULT_OUTPUT / "runtime.json")
        timings = list(replay.read_csv(replay.DEFAULT_OUTPUT / "processing_times.csv"))
        self.assertEqual(len(timings), len(self.events))
        self.assertEqual([r["event_id"] for r in timings], [e["event_id"] for e in self.events])
        self.assertTrue(all(float(r["apply_commit_ms"]) > 0 for r in timings))
        self.assertGreater(runtime["loop_seconds"], 0)
        self.assertLessEqual(runtime["apply_commit_ms_p50"], runtime["apply_commit_ms_p95"])
        self.assertLessEqual(runtime["apply_commit_ms_p95"], runtime["apply_commit_ms_max"])
        self.assertFalse(self.protocol["actual_ingestion_timestamps_available"])
        self.assertFalse(self.protocol["production_near_real_time_validated"])


if __name__ == "__main__":
    unittest.main()
