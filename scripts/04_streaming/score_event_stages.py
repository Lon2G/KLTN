"""Point-in-time completed-stage scoring with explicit context-availability scenarios."""

import hashlib
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "scripts/01_data_preparation"), str(ROOT / "scripts/03_anomaly_detection")]
import build_product_stage_scores as product
import event_state as core


VERSION = "event_stage_scoring_v1"
MODES = ("verified_availability_only", "assumed_static_context_at_purchase")
STAGE_MAP = {"approval": "approval", "preparation_handoff": "preparation_handoff", "transport_delivery": "transport"}
CONTEXT_FIELDS = {"order_id", "product_id", "single_seller_id", "product_count", "item_count",
                  "product_attributable", "customer_state", "distance_band", "seller_attribution_scope",
                  "all_pairs_quality_eligible"}


class ReferenceBank:
    """Frozen peer selection; reuses the existing hierarchy and tail-score kernel."""

    def __init__(self, reference, scope_ids, start):
        self.reference = reference.sort_values(["order_id", "stage"]).reset_index(drop=True).copy()
        r = self.reference
        if (r.duplicated(["order_id", "stage"]).any() or not r.split.eq("train").all()
                or set(r.order_id) & set(scope_ids) or not r.duration_eligible.all()
                or not r.end_timestamp.lt(pd.Timestamp(start)).all()
                or not r.end_timestamp.lt(r.visible_before_exclusive).all()
                or not np.isfinite(r.duration_days).all() or r.duration_days.lt(0).any()
                or set(r.stage) != set(STAGE_MAP.values())):
            raise ValueError("Expected disjoint eligible train reference ending before replay starts")
        self.scope_ids = frozenset(scope_ids)
        self.minimum = product.protocol()["minimum_reference_orders"]
        self.category_levels = product.signals.supplement.protocol()["context_levels"]
        self.signature = core.fingerprint({
            "reference": hashlib.sha256(r.to_csv(index=False, date_format=core.TIME_FORMAT).encode()).hexdigest(),
            "product_rules": product.protocol(), "category_levels": self.category_levels,
            "adapter_code": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "kernel_code": hashlib.sha256(Path(product.__file__).read_bytes()).hexdigest()})
        self.cache, self.registry, self.indexes, self.selections = {}, {}, {}, {}

    def peers(self, stage, context, product_only):
        key = (stage, core.canonical(context), product_only)
        if key not in self.cache:
            fields = tuple(context)
            index_key = (fields, product_only)
            if index_key not in self.indexes:
                r = self.reference
                mask = r.all_pairs_quality_eligible.copy()
                if product_only:
                    mask &= r.product_attributable
                base = r.loc[mask]
                self.indexes[index_key] = (base, base.groupby(["stage", *fields], sort=False, dropna=True).indices)
            base, groups = self.indexes[index_key]
            positions = groups.get((stage, *context.values()), [])
            self.cache[key] = base.iloc[positions].sort_values("order_id")
        return self.cache[key]

    def select(self, stage, context):
        key = (stage, core.canonical({k: v for k, v in context.items() if k != "order_id"}))
        if key not in self.selections:
            self.selections[key] = self._select(stage, context)
        return self.selections[key]

    def _select(self, stage, context):
        # Preserve the old category-distance gate even when a product group exists.
        category_group, category_context = None, None
        for fields in self.category_levels:
            if "distance_band" not in fields:
                continue
            selected_context = {f: context[f] for f in fields}
            if any(v is None or v in {"", "unavailable"} for v in selected_context.values()):
                continue
            group = self.peers(stage, selected_context, False)
            if len(group) >= self.minimum:
                category_group, category_context = group, selected_context
                break
        if category_group is None:
            return None
        selected, selected_context, scope = category_group, category_context, "category_context_fallback"
        if context["product_attributable"]:
            for fields in product.LEVELS:
                candidate_context = {f: context[f] for f in fields}
                if any(v is None or v == "" for v in candidate_context.values()):
                    continue
                candidate = self.peers(stage, candidate_context, True)
                if len(candidate) >= self.minimum:
                    selected, selected_context, scope = candidate, candidate_context, "product_context"
                    break
        values = selected.duration_days.to_numpy(float)
        gid = hashlib.sha256(json.dumps([stage, scope, selected_context], sort_keys=True).encode()).hexdigest()
        self.registry[gid] = {"reference_id": gid, "stage": stage, "scope": scope,
                              "context_json": json.dumps(selected_context, sort_keys=True), "n": len(selected),
                              "mean_days": float(values.mean()), "median_days": float(np.median(values)),
                              "upper_tail_boundary_days": float(np.sort(values)[(19 * len(values)) // 20]),
                              "latest_end": selected.end_timestamp.max().strftime(core.TIME_FORMAT),
                              "order_ids_json": json.dumps(selected.order_id.tolist())}
        return values, self.registry[gid]


class StageScorer:
    def __init__(self, bank, contexts, held_ids=()):
        if any(set(c) != CONTEXT_FIELDS or oid != c["order_id"] for oid, c in contexts.items()):
            raise ValueError("Context must use the exact static-field allowlist")
        if set(contexts) != bank.scope_ids or not set(held_ids) <= bank.scope_ids:
            raise ValueError("Context and held-case scope differ from replay scope")
        self.bank = bank
        # Copy caller-owned metadata so mutations cannot silently alter a running scorer.
        self.contexts = json.loads(core.canonical(contexts))
        self.held_ids = frozenset(held_ids)
        self.signature = core.fingerprint({"version": VERSION, "bank": bank.signature,
                                           "contexts": self.contexts, "held_ids": sorted(held_ids), "modes": MODES})

    def assess(self, state, stage, mode):
        if mode not in MODES or stage not in STAGE_MAP:
            raise ValueError("Unknown scoring scenario or stage")
        oid, observation = state["order_id"], state["stages"][stage]
        if oid not in self.bank.scope_ids:
            raise ValueError("Order is outside the scoring scope")
        result = {"order_id": oid, "stage": STAGE_MAP[stage], "mode": mode,
                  "context_availability_basis": "unverified_no_source_availability_history",
                  "context_assumption_used": False, **observation,
                  "assessment_status": None, "selected_reference_id": None,
                  "selected_reference_scope": "unavailable", "selected_reference_n": None,
                  "selected_reference_mean_days": None, "selected_reference_median_days": None,
                  "difference_from_reference_mean_days": None, "upper_tail_boundary_days": None,
                  "reference_percentile_0_100": None, "delay_score_0_100": None,
                  "severity": "unassessed", "long_stage_warning": None,
                  "promise_basis_status": state["promise_basis_status"],
                  "meaning": "completed_stage_tail_signal_not_probability_or_confirmed_bottleneck"}
        reason = None
        if oid in self.held_ids:
            reason = "protected_review_hold"
        elif state["observed_inversions"]:
            reason = "observed_timeline_conflict"
        elif state["observed_equal_time_pairs"]:
            reason = "equal_timestamp_order_unresolved"
        elif observation["signed_duration_seconds"] is None:
            reason = "stage_endpoint_not_observed"
        elif observation["signed_duration_seconds"] <= 0:
            reason = "nonpositive_or_unresolved_stage"
        elif "Purchased" not in state["observed_milestones"]:
            reason = "purchase_not_observed"
        elif mode == MODES[0]:
            reason = "context_availability_unverified"
        if reason:
            result["assessment_status"] = reason
            return result
        context = self.contexts[oid]
        result["context_availability_basis"] = "assumed_static_at_observed_purchase_not_source_verified"
        result["context_assumption_used"] = True
        if not context["all_pairs_quality_eligible"] or context["distance_band"] in {None, "", "unavailable"}:
            result["assessment_status"] = "geographic_quality_not_eligible"
            return result
        selected = self.bank.select(STAGE_MAP[stage], context)
        if selected is None:
            result["assessment_status"] = "insufficient_matched_reference"
            return result
        values, group = selected
        score, severity, percentile = product.tail_score(observation["duration_days"], values)
        result.update(assessment_status="assessed_under_context_assumption",
                      selected_reference_id=group["reference_id"], selected_reference_scope=group["scope"],
                      selected_reference_n=group["n"], selected_reference_mean_days=group["mean_days"],
                      selected_reference_median_days=group["median_days"],
                      difference_from_reference_mean_days=observation["duration_days"] - group["mean_days"],
                      upper_tail_boundary_days=group["upper_tail_boundary_days"],
                      reference_percentile_0_100=100 * percentile, delay_score_0_100=score,
                      severity=severity, long_stage_warning=score > 0)
        return result


class ScoredStateStore(core.StateStore):
    """Extend the original atomic write hook without changing the frozen replay core."""

    def __init__(self, path, start, cutoff, scorer):
        super().__init__(path, start, cutoff)
        self.scorer = scorer
        existing = self.connection.execute("SELECT value FROM metadata WHERE key='scorer_signature'").fetchone()
        if ((existing and existing[0] != scorer.signature)
                or (not existing and self.connection.execute("SELECT 1 FROM journal LIMIT 1").fetchone())):
            self.close()
            raise ValueError("Scorer/checkpoint contract differs; do not append new scoring to an old replay")
        self.connection.executescript("""
            CREATE TABLE IF NOT EXISTS stage_states (
                order_id TEXT NOT NULL, stage TEXT NOT NULL, mode TEXT NOT NULL,
                result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
                PRIMARY KEY(order_id, stage, mode));
            CREATE TABLE IF NOT EXISTS score_journal (
                sequence INTEGER NOT NULL REFERENCES journal(sequence),
                order_id TEXT NOT NULL, stage TEXT NOT NULL, mode TEXT NOT NULL,
                event_id TEXT NOT NULL, event_time TEXT NOT NULL,
                action TEXT NOT NULL, delivery_observed INTEGER NOT NULL,
                result_json TEXT NOT NULL, result_sha256 TEXT NOT NULL,
                PRIMARY KEY(sequence, stage, mode));
        """)
        with self.connection:
            self.connection.execute("INSERT OR IGNORE INTO metadata VALUES ('scorer_signature', ?)", (scorer.signature,))

    def _write_state(self, state):
        super()._write_state(state)
        sequence, event_id, event_time = self.connection.execute(
            "SELECT sequence, event_id, occurred_at FROM journal ORDER BY sequence DESC LIMIT 1").fetchone()
        for mode in MODES:
            for stage in STAGE_MAP:
                result = self.scorer.assess(state, stage, mode)
                sha, encoded = core.fingerprint(result), core.canonical(result)
                key = (state["order_id"], STAGE_MAP[stage], mode)
                old = self.connection.execute("SELECT result_json, result_sha256 FROM stage_states WHERE order_id=? AND stage=? AND mode=?", key).fetchone()
                if old and old[1] == sha:
                    continue
                prior = json.loads(old[0]) if old else None
                action = "assessment_updated"
                if result["delay_score_0_100"] is not None:
                    action = "warning_emitted" if result["long_stage_warning"] else "completed_stage_scored"
                elif prior and prior["delay_score_0_100"] is not None:
                    action = "assessment_withdrawn_after_new_evidence"
                self.connection.execute("INSERT INTO stage_states VALUES (?, ?, ?, ?, ?) ON CONFLICT(order_id,stage,mode) DO UPDATE SET "
                                        "result_json=excluded.result_json,result_sha256=excluded.result_sha256", (*key, encoded, sha))
                self.connection.execute("INSERT INTO score_journal VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                        (sequence, *key, event_id, event_time, action, int(state["delivery_observed"]), encoded, sha))

    def stage_results(self):
        return [json.loads(r[0]) for r in self.connection.execute("SELECT result_json FROM stage_states ORDER BY mode,order_id,stage")]

    def scoring_journal(self):
        for seq, event, time, action, delivered, result, sha in self.connection.execute(
                "SELECT sequence,event_id,event_time,action,delivery_observed,result_json,result_sha256 FROM score_journal ORDER BY sequence,mode,stage"):
            yield {"sequence": seq, "event_id": event, "event_time": time, "action": action,
                   "delivery_observed_when_evaluated": bool(delivered), "result": json.loads(result), "result_sha256": sha}


def aggregate_orders(results):
    frame = pd.DataFrame(results)
    rows = []
    for (mode, oid), group in frame.groupby(["mode", "order_id"], sort=True):
        if len(group) != 3 or set(group.stage) != set(STAGE_MAP.values()):
            raise ValueError("Every observed order needs exactly three stage assessment rows per scenario")
        scored = group.delay_score_0_100.notna()
        warned = group.long_stage_warning.eq(True)
        n = int(scored.sum())
        rows.append({"mode": mode, "order_id": oid, "scored_stages": n,
                     "coverage": "complete" if n == 3 else "partial" if n else "unavailable",
                     "max_stage_delay_score": float(group.delay_score_0_100.max()) if n else None,
                     "any_long_stage_warning": True if warned.any() else False if n == 3 else None,
                     "warning_stages_json": core.canonical(sorted(group.loc[warned, "stage"])),
                     "unassessed_reasons_json": core.canonical(sorted(set(group.loc[~scored, "assessment_status"]))),
                     "promise_basis_status": "unverified_source_method_not_provided"})
    return rows
