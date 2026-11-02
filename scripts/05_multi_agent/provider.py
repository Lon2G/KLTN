"""Responses adapter: explicit accounting, no automatic HTTP retries or model fallback."""
import hashlib
import json
import os
import urllib.error
import urllib.request

from api_budget import BudgetBlocked, canonical, integer


class ProviderUnavailable(RuntimeError):
    pass


class ResponsesProvider:
    execution_kind = "live_openai_api"

    def __init__(self, ledger, api_key=None, post=None):
        self.ledger = ledger
        self.key = api_key if api_key is not None else os.environ.get("OPENAI_API_KEY", "").strip()
        if post is None and not self.key:
            raise ProviderUnavailable("OPENAI_API_KEY is not configured in the local environment")
        self.post = post or self._post
        if post is not None:
            self.execution_kind = "technical_test_transport_not_live_inference"
        with ledger._transaction():
            ledger.connection.execute("""CREATE TABLE IF NOT EXISTS provider_receipts (
                request_id TEXT PRIMARY KEY, payload_sha256 TEXT NOT NULL, response_json TEXT NOT NULL)""")

    def _post(self, endpoint, body):
        request = urllib.request.Request("https://api.openai.com/v1/" + endpoint,
            data=canonical(body).encode(), headers={"Authorization": "Bearer " + self.key,
            "Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=45) as response:
                return json.loads(response.read(2_000_000))
        except urllib.error.HTTPError as exc:
            raise ProviderUnavailable(f"OpenAI HTTP {exc.code}; no automatic retry") from None
        except (OSError, ValueError) as exc:
            raise ProviderUnavailable(f"OpenAI transport failure: {type(exc).__name__}; no automatic retry") from None

    def _settle(self, request_id, response, model):
        # Reject unknown billing dimensions rather than assuming missing usage is free.
        if response.get("service_tier") not in {None, "default"}:
            raise ProviderUnavailable("Unexpected billing tier; reservation retained")
        if not (response.get("model") == model or str(response.get("model", "")).startswith(model + "-")):
            raise ProviderUnavailable("Unexpected response model; reservation retained")
        try:
            usage = response["usage"]
            details = usage["input_tokens_details"]
            inp, out = usage["input_tokens"], usage["output_tokens"]
            for name, n in [("input", inp), ("output", out), ("total", usage["total_tokens"]),
                            ("cached", details["cached_tokens"]), ("writes", details["cache_write_tokens"])]:
                integer(n, name)
            if usage["total_tokens"] != inp + out:
                raise ValueError("Token totals differ")
            lifetime = details.get("cache_write_tokens_details")
            if lifetime is not None and (lifetime.get("ttl_12h_tokens") != 0 or
                                         lifetime.get("ttl_30m_tokens") != details["cache_write_tokens"]):
                raise ValueError("Unpriced cache lifetime")
            row = self.ledger.settle(request_id, inp, out, details["cached_tokens"], details["cache_write_tokens"])
        except (KeyError, TypeError, ValueError):
            raise ProviderUnavailable("Incomplete or unsupported usage receipt; reservation retained") from None
        if row["overrun"]:
            raise BudgetBlocked("Actual usage exceeded reservation; ledger frozen")
        if response.get("status") != "completed":
            raise ProviderUnavailable("Model response incomplete; usage recorded, no automatic retry")
        return response

    def call(self, *, request_id, case_key, run_key, role, payload):
        # Caller cannot silently add paid hosted tools, image inputs, or background mode.
        expected = {"model", "instructions", "input", "tools", "tool_choice", "parallel_tool_calls",
                    "max_output_tokens", "store", "reasoning", "service_tier", "truncation"}
        if set(payload) != expected or payload["store"] is not False or payload["service_tier"] != "default":
            raise ValueError("Unsupported request contract")
        if payload["reasoning"] != {"effort": "none"} or payload["truncation"] != "disabled":
            raise ValueError("Unsupported reasoning / truncation contract")
        if any(t.get("type") != "function" or t.get("strict") is not True for t in payload["tools"]):
            raise ValueError("Only strict local function tools are supported")
        payload_hash = hashlib.sha256(canonical(payload).encode()).hexdigest()
        max_input = self.ledger._policy["max_input_tokens_per_request"]
        row = self.ledger.reserve(request_id, case_key, role, payload["model"], payload_hash,
                                  max_input, payload["max_output_tokens"], run_key)
        if not row["created"]:
            receipt = self.ledger.connection.execute("SELECT * FROM provider_receipts WHERE request_id=?", (request_id,)).fetchone()
            if receipt is None or receipt["payload_sha256"] != payload_hash:
                raise BudgetBlocked("Prior request has no usable receipt; refusing duplicate dispatch")
            return self._settle(request_id, json.loads(receipt["response_json"]), payload["model"])
        # Official count includes input messages, instructions, function schemas and formatting.
        count_body = {k: payload[k] for k in ["model", "instructions", "input", "tools", "tool_choice", "parallel_tool_calls", "reasoning"]}
        try:
            count = self.post("responses/input_tokens", count_body)
            n = integer(count["input_tokens"], "count")
            if count.get("object") != "response.input_tokens" or n > max_input:
                raise BudgetBlocked("Serialized input exceeds token cap or count response is invalid")
        except Exception:
            self.ledger.cancel_unsent(request_id)  # No inference request has been sent.
            raise
        self.ledger.mark_sent(request_id)
        response = self.post("responses", payload)
        # Receipt survives interruption before settlement; replay never resends the request.
        with self.ledger._transaction():
            self.ledger.connection.execute("INSERT INTO provider_receipts VALUES (?,?,?)",
                (request_id, payload_hash, canonical(response)))
        return self._settle(request_id, response, payload["model"])
