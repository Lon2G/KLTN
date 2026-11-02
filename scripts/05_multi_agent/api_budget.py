"""Offline, durable request reservations. This module never calls an API.

Units are integer nano-USD, so small token charges never round down to zero.
Unknown/in-flight outcomes retain their reservation. A future sender must enforce
the serialized input and output caps and mark_sent BEFORE network dispatch.
"""

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import re
import sqlite3

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = ROOT / "templates/multi_agent_v1/budget.json"


class BudgetBlocked(ValueError):
    """The request must not be dispatched."""


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def text_key(value, name):
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError(f"Invalid {name}")
    return value


def validate_policy(policy):
    for name in ["total_budget_vnd", "api_budget_vnd", "contingency_vnd", "planning_vnd_per_usd",
                 "usd_nanounits_per_dollar", "api_cap_usd_nanounits", "pilot_case_limit",
                 "max_requests_per_case", "max_requests_per_role_per_case",
                 "max_input_tokens_per_request", "max_output_tokens_per_request"]:
        integer(policy[name], name, 1)
    if policy["api_budget_vnd"] + policy["contingency_vnd"] > policy["total_budget_vnd"]:
        raise ValueError("Budget plus contingency exceeds user total")
    if policy["usd_nanounits_per_dollar"] != 10**9:
        raise ValueError("Accounting unit must be nano-USD")
    if policy["api_cap_usd_nanounits"] > policy["api_budget_vnd"] * 10**9 // policy["planning_vnd_per_usd"]:
        raise ValueError("USD cap exceeds planned VND allocation")
    if not policy["roles"] or len(set(policy["roles"])) != len(policy["roles"]):
        raise ValueError("Roles must be unique and nonempty")
    rate_keys = {"input_nanousd_per_token", "cached_input_nanousd_per_token",
                 "cache_write_nanousd_per_token", "output_nanousd_per_token"}
    if not policy["models"]:
        raise ValueError("Missing allowlisted model prices")
    for model, rates in policy["models"].items():
        text_key(model, "model")
        if set(rates) != rate_keys:
            raise ValueError("Unknown or missing token price category")
        for name, value in rates.items():
            integer(value, name, 1)


class BudgetLedger:
    def __init__(self, path, policy=None):
        source = json.loads(DEFAULT_POLICY.read_text()) if policy is None else policy
        self._policy_json = canonical(source)
        self._policy = json.loads(self._policy_json)
        validate_policy(self._policy)
        self.connection = sqlite3.connect(str(path), isolation_level=None, timeout=10)
        self.connection.row_factory = sqlite3.Row
        try:
            with self._transaction():
                self.connection.execute("CREATE TABLE IF NOT EXISTS budget_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
                saved = self.connection.execute("SELECT value FROM budget_meta WHERE key='policy'").fetchone()
                if saved is not None and saved[0] != self._policy_json:
                    raise BudgetBlocked("Policy changed; do not reset accumulated spending")
                self.connection.execute("INSERT OR IGNORE INTO budget_meta VALUES ('policy', ?)", (self._policy_json,))
                self.connection.execute("INSERT OR IGNORE INTO budget_meta VALUES ('breached', 'false')")
                self.connection.execute("""CREATE TABLE IF NOT EXISTS budget_requests (
                    request_id TEXT PRIMARY KEY, case_key TEXT NOT NULL, run_key TEXT NOT NULL, role TEXT NOT NULL,
                    model TEXT NOT NULL, payload_sha256 TEXT NOT NULL, contract TEXT NOT NULL,
                    max_input INTEGER NOT NULL, max_output INTEGER NOT NULL,
                    reserved_nanousd INTEGER NOT NULL, status TEXT NOT NULL,
                    actual_nanousd INTEGER, usage TEXT, overrun INTEGER NOT NULL DEFAULT 0)""")
        except BaseException:
            self.connection.close()
            raise

    @contextmanager
    def _transaction(self):
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            yield
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def close(self):
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _row(self, request_id):
        row = self.connection.execute("SELECT * FROM budget_requests WHERE request_id=?", (request_id,)).fetchone()
        if row is None:
            raise ValueError("Unknown request")
        return dict(row)

    def _totals(self):
        row = self.connection.execute("""SELECT
            COALESCE(SUM(CASE WHEN status='settled' THEN actual_nanousd ELSE 0 END),0),
            COALESCE(SUM(CASE WHEN status IN ('reserved','in_flight') THEN reserved_nanousd ELSE 0 END),0)
            FROM budget_requests""").fetchone()
        spent, held = row
        return {"spent_nanousd": spent, "reserved_nanousd": held,
                "remaining_nanousd": self._policy["api_cap_usd_nanounits"] - spent - held,
                "breached": self.connection.execute("SELECT value FROM budget_meta WHERE key='breached'").fetchone()[0] == "true"}

    def snapshot(self):
        with self._transaction():
            return {**self._totals(), "request_states": dict(self.connection.execute(
                "SELECT status, COUNT(*) FROM budget_requests GROUP BY status")),
                "case_count": self.connection.execute("SELECT COUNT(DISTINCT case_key) FROM budget_requests").fetchone()[0],
                "policy_sha256": hashlib.sha256(self._policy_json.encode()).hexdigest()}

    def reserve(self, request_id, case_key, role, model, payload_sha256, max_input, max_output, run_key="pilot"):
        """Idempotent bookkeeping; created=False MUST NOT cause another dispatch."""
        for name, value in [("request_id", request_id), ("case_key", case_key), ("run_key", run_key)]:
            text_key(value, name)
        if not isinstance(payload_sha256, str) or re.fullmatch(r"[0-9a-f]{64}", payload_sha256) is None:
            raise ValueError("Expected fingerprint of complete request payload")
        if role not in self._policy["roles"] or model not in self._policy["models"]:
            raise BudgetBlocked("Role or model is not allowlisted")
        integer(max_input, "max_input", 1)
        integer(max_output, "max_output", 1)
        if max_input > self._policy["max_input_tokens_per_request"] or max_output > self._policy["max_output_tokens_per_request"]:
            raise BudgetBlocked("Per-request token cap exceeded")
        contract = canonical([case_key, run_key, role, model, payload_sha256, max_input, max_output])
        prices = self._policy["models"][model]
        input_price = max(prices[k] for k in prices if k != "output_nanousd_per_token")
        reserve = max_input * input_price + max_output * prices["output_nanousd_per_token"]
        with self._transaction():
            old = self.connection.execute("SELECT * FROM budget_requests WHERE request_id=?", (request_id,)).fetchone()
            if old is not None:
                if old["contract"] != contract:
                    raise BudgetBlocked("Request ID reused with a different payload or limits")
                return {**dict(old), "created": False}
            totals = self._totals()
            if totals["breached"] or reserve > totals["remaining_nanousd"]:
                raise BudgetBlocked("Budget exhausted or frozen after an accounting overrun")
            known_case = self.connection.execute("SELECT 1 FROM budget_requests WHERE case_key=? LIMIT 1", (case_key,)).fetchone()
            if known_case is None and self.connection.execute("SELECT COUNT(DISTINCT case_key) FROM budget_requests").fetchone()[0] >= self._policy["pilot_case_limit"]:
                raise BudgetBlocked("Pilot case limit reached")
            case_count = self.connection.execute("SELECT COUNT(*) FROM budget_requests WHERE case_key=? AND run_key=?", (case_key, run_key)).fetchone()[0]
            role_count = self.connection.execute("SELECT COUNT(*) FROM budget_requests WHERE case_key=? AND run_key=? AND role=?", (case_key, run_key, role)).fetchone()[0]
            if case_count >= self._policy["max_requests_per_case"] or role_count >= self._policy["max_requests_per_role_per_case"]:
                raise BudgetBlocked("Case or role request limit reached")
            self.connection.execute("""INSERT INTO budget_requests
                (request_id,case_key,run_key,role,model,payload_sha256,contract,max_input,max_output,reserved_nanousd,status)
                VALUES (?,?,?,?,?,?,?,?,?,?,'reserved')""",
                (request_id, case_key, run_key, role, model, payload_sha256, contract, max_input, max_output, reserve))
            return {**self._row(request_id), "created": True}

    def mark_sent(self, request_id):
        """Commit before dispatch. Crash/timeout after this point keeps funds held."""
        with self._transaction():
            if self._totals()["breached"]:
                raise BudgetBlocked("Ledger frozen after overrun")
            if self._row(request_id)["status"] != "reserved":
                raise BudgetBlocked("Request cannot be dispatched again")
            self.connection.execute("UPDATE budget_requests SET status='in_flight' WHERE request_id=?", (request_id,))

    def cancel_unsent(self, request_id):
        with self._transaction():
            if self._row(request_id)["status"] != "reserved":
                raise BudgetBlocked("Cannot release a sent or uncertain request")
            self.connection.execute("UPDATE budget_requests SET status='cancelled_unsent' WHERE request_id=?", (request_id,))

    def settle(self, request_id, input_tokens, output_tokens, cached_input_tokens=0, cache_write_tokens=0):
        """Record actual billed usage, including reasoning in output_tokens.

        Token counts are normalized from provider usage; cache categories are
        mutually exclusive subsets of total input, never extra invented usage.
        Overruns are persisted truthfully and stop new requests, not hidden.
        """
        usage = dict(input_tokens=input_tokens, output_tokens=output_tokens,
                     cached_input_tokens=cached_input_tokens, cache_write_tokens=cache_write_tokens)
        for key, value in usage.items():
            integer(value, key)
        if cached_input_tokens + cache_write_tokens > input_tokens:
            raise ValueError("Cache token categories exceed total input")
        serialized = canonical(usage)
        with self._transaction():
            row = self._row(request_id)
            if row["status"] == "settled":
                if row["usage"] != serialized:
                    raise BudgetBlocked("Conflicting repeated usage receipt")
                return row
            if row["status"] != "in_flight":
                raise BudgetBlocked("Only dispatched requests may be settled")
            prices = self._policy["models"][row["model"]]
            actual = ((input_tokens - cached_input_tokens - cache_write_tokens) * prices["input_nanousd_per_token"]
                      + cached_input_tokens * prices["cached_input_nanousd_per_token"]
                      + cache_write_tokens * prices["cache_write_nanousd_per_token"]
                      + output_tokens * prices["output_nanousd_per_token"])
            overrun = input_tokens > row["max_input"] or output_tokens > row["max_output"] or actual > row["reserved_nanousd"]
            self.connection.execute("""UPDATE budget_requests SET status='settled', actual_nanousd=?, usage=?, overrun=?
                WHERE request_id=?""", (actual, serialized, int(overrun), request_id))
            if overrun or self._totals()["remaining_nanousd"] < 0:
                self.connection.execute("UPDATE budget_meta SET value='true' WHERE key='breached'")
            return self._row(request_id)
