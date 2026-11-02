"""Ordered, idempotent milestone replay; no model, source lookup or inferred events."""

from datetime import datetime
import hashlib
import json
import re
import sqlite3


VERSION = "olist_milestone_event_v1"
TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
ACTIVITIES = {
    "Purchased": "order_purchase_timestamp",
    "Approved": "order_approved_at",
    "Handed to Carrier": "order_delivered_carrier_date",
    "Delivered": "order_delivered_customer_date",
}
RANK = {activity: i for i, activity in enumerate(ACTIVITIES)}
STAGES = {
    "approval": ("Purchased", "Approved"),
    "preparation_handoff": ("Approved", "Handed to Carrier"),
    "transport_delivery": ("Handed to Carrier", "Delivered"),
}
EVENT_FIELDS = {"schema_version", "event_id", "order_id", "activity", "occurred_at"}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}", value):
        raise ValueError("Expected a source-clock timestamp without timezone conversion")
    return datetime.strptime(value, TIME_FORMAT)


def make_event(order_id, activity, occurred_at):
    body = {"schema_version": VERSION, "order_id": order_id, "activity": activity, "occurred_at": occurred_at}
    return {**body, "event_id": fingerprint(body)}


def validate_event(event):
    if set(event) != EVENT_FIELDS or event["schema_version"] != VERSION:
        raise ValueError("Event schema is an exact allowlist; extra snapshot fields are forbidden")
    if not isinstance(event["order_id"], str) or not re.fullmatch(r"[0-9a-f]{32}", event["order_id"]):
        raise ValueError("Invalid Olist order identifier")
    if event["activity"] not in ACTIVITIES:
        raise ValueError("Unknown milestone activity")
    parse_time(event["occurred_at"])
    if event != make_event(event["order_id"], event["activity"], event["occurred_at"]):
        raise ValueError("Event ID does not match its payload")


def event_key(event):
    return (event["occurred_at"], event["order_id"], RANK[event["activity"]])


def state_from_events(events):
    """Derive only observed facts; activity rank is not a causal ordering claim."""
    if not events or len({e["order_id"] for e in events}) != 1:
        raise ValueError("State requires events for exactly one observed order")
    observed = {e["activity"]: e["occurred_at"] for e in events}
    if len(observed) != len(events):
        raise ValueError("Multiple records for the same order milestone")
    names = list(ACTIVITIES)
    inversions, ties = [], []
    for i, before in enumerate(names):
        for after in names[i + 1:]:
            if before in observed and after in observed:
                if observed[before] > observed[after]:
                    inversions.append([before, after])
                elif observed[before] == observed[after]:
                    ties.append([before, after])
    stages = {}
    for stage, (start, end) in STAGES.items():
        a, b = observed.get(start), observed.get(end)
        signed = int((parse_time(b) - parse_time(a)).total_seconds()) if a and b else None
        if signed is None:
            status = "neither_endpoint_observed" if not a and not b else (
                "end_not_observed" if a else "start_not_observed")
        else:
            status = "negative_recorded_duration" if signed < 0 else (
                "equal_recorded_time_order_unresolved" if signed == 0 else "positive_recorded_duration")
        stages[stage] = {"start_activity": start, "end_activity": end,
                         "start_timestamp": a, "end_timestamp": b,
                         "signed_duration_seconds": signed,
                         "duration_days": signed / 86400 if signed is not None and signed >= 0 else None,
                         "observation_status": status}
    highest = max(observed, key=RANK.get)
    gaps = [name for name in names[:RANK[highest]] if name not in observed]
    quality = "observed_timeline_conflict" if inversions else (
        "equal_timestamp_order_unresolved" if ties else "no_conflict_in_observed_timestamps")
    return {"order_id": events[0]["order_id"], "observed_milestones": observed,
            "not_observed_milestones": [name for name in names if name not in observed],
            "observed_event_count": len(events), "highest_milestone_observed": highest,
            "earlier_milestones_not_observed": gaps, "all_four_observed": len(observed) == 4,
            "delivery_observed": "Delivered" in observed,
            "last_observed_event_time": max(observed.values()),
            "observed_inversions": inversions, "observed_equal_time_pairs": ties,
            "timestamp_quality": quality, "stages": stages,
            "anomaly_assessment": "not_run_in_replay_infrastructure",
            "promise_basis_status": "unverified_source_method_not_provided"}


class StateStore:
    """One writer, ordered stream. Each event and its derived state commit atomically."""

    def __init__(self, path, start_inclusive, cutoff_exclusive):
        if parse_time(start_inclusive) >= parse_time(cutoff_exclusive):
            raise ValueError("Invalid replay window")
        self.contract = {"version": VERSION, "start_inclusive": start_inclusive,
                         "cutoff_exclusive": cutoff_exclusive,
                         "arrival_policy": "global_event_time_order_with_stable_ties_no_late_new_events"}
        self.connection = sqlite3.connect(str(path))
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA synchronous = FULL")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journal (
                sequence INTEGER PRIMARY KEY, event_id TEXT UNIQUE NOT NULL,
                order_id TEXT NOT NULL, activity TEXT NOT NULL, occurred_at TEXT NOT NULL,
                payload TEXT NOT NULL, state_after TEXT NOT NULL, state_sha256 TEXT NOT NULL,
                UNIQUE(order_id, activity));
            CREATE TABLE IF NOT EXISTS states (
                order_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, state_sha256 TEXT NOT NULL);
        """)
        old = self.connection.execute("SELECT value FROM metadata WHERE key='contract'").fetchone()
        if old and old[0] != canonical(self.contract):
            self.close()
            raise ValueError("Checkpoint contract differs; do not resume across windows or versions")
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('contract', ?)", (canonical(self.contract),))

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _write_state(self, state):
        self.connection.execute("INSERT INTO states VALUES (?, ?, ?) ON CONFLICT(order_id) DO UPDATE SET "
                                "state_json=excluded.state_json, state_sha256=excluded.state_sha256",
                                (state["order_id"], canonical(state), fingerprint(state)))

    def apply(self, event):
        validate_event(event)
        if not self.contract["start_inclusive"] <= event["occurred_at"] < self.contract["cutoff_exclusive"]:
            raise ValueError("Event lies outside the replay window")
        with self.connection:
            existing = self.connection.execute("SELECT sequence, payload, state_after FROM journal WHERE event_id=?",
                                               (event["event_id"],)).fetchone()
            if existing:
                if existing[1] != canonical(event):
                    raise ValueError("Duplicate event ID has conflicting payload")
                return {"applied": False, "sequence": existing[0], "state_after": json.loads(existing[2])}
            if self.connection.execute("SELECT 1 FROM metadata WHERE key='closed_at_exclusive'").fetchone():
                raise ValueError("Replay already closed; new events require a separate run")
            previous = self.connection.execute("SELECT sequence, payload FROM journal ORDER BY sequence DESC LIMIT 1").fetchone()
            if previous and event_key(event) < event_key(json.loads(previous[1])):
                raise ValueError("Out-of-order arrival is unsupported; never silently rewrite prior states")
            prior = [json.loads(row[0]) for row in self.connection.execute(
                "SELECT payload FROM journal WHERE order_id=? ORDER BY sequence", (event["order_id"],))]
            if any(e["activity"] == event["activity"] for e in prior):
                raise ValueError("Conflicting record for an already observed milestone")
            state = state_from_events([*prior, event])
            sequence = previous[0] + 1 if previous else 1
            self.connection.execute("INSERT INTO journal VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                    (sequence, event["event_id"], event["order_id"], event["activity"],
                                     event["occurred_at"], canonical(event), canonical(state), fingerprint(state)))
            self._write_state(state)
        return {"applied": True, "sequence": sequence, "state_after": state}

    def finish(self):
        # A run boundary is control metadata, never a fabricated business event.
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('closed_at_exclusive', ?)",
                                    (self.contract["cutoff_exclusive"],))

    def states(self):
        return [json.loads(row[0]) for row in self.connection.execute("SELECT state_json FROM states ORDER BY order_id")]

    def journal(self):
        for sequence, payload, state, digest in self.connection.execute(
                "SELECT sequence, payload, state_after, state_sha256 FROM journal ORDER BY sequence"):
            yield {"sequence": sequence, "event": json.loads(payload), "state_after": json.loads(state), "state_sha256": digest}
