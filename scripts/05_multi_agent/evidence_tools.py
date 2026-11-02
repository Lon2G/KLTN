"""Read-only, point-in-time tools over frozen real Olist artifacts.

Case IDs and member IDs remain local. Historical comparisons describe completed
training stages; they are not causal estimates or independent anomaly labels.
"""
from collections import defaultdict
from copy import deepcopy
import csv
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics

from api_budget import canonical
from audit_readiness import digest

ROOT = Path(__file__).resolve().parents[2]
STAGES = ("approval", "preparation_handoff", "transport")
PROTECTED = {"2042abeefae68e63a8aa30edd4b0eb48", "9ba2e8784e3e43f617fd0faa356d309d"}
CONFIG = ROOT / "templates/multi_agent_v1/integration.json"
SNAPSHOT = "data/experiments/olist_event_stage_scoring_v1"
TRAIN = "data/experiments/olist_product_stage_scores_v1/train_stage_reference.csv"
EVENTS = "data/experiments/olist_event_replay_v1/events.jsonl"


def sha(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def jsonl(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream if line.strip()]


def read_csv(path):
    with path.open(newline="") as stream:
        return list(csv.DictReader(stream))


def elapsed(start, end):
    return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds() / 86400


class EvidenceStore:
    def __init__(self, root=ROOT):
        self.root = Path(root)
        self.config = json.loads((self.root / "templates/multi_agent_v1/integration.json").read_text())
        base = self.root / SNAPSHOT
        manifest = json.loads((base / "manifest.json").read_text())
        self.source_hashes = {f"{SNAPSHOT}/manifest.json": digest(base / "manifest.json")}
        required = {f"{SNAPSHOT}/{name}": manifest["output_hashes"][name] for name in
                    ["stage_decisions.jsonl", "static_context_scenario_inputs.jsonl", "reference_groups.csv"]}
        required.update({name: manifest["source_files"][name] for name in [TRAIN, EVENTS]})
        for name, expected in required.items():
            if digest(self.root / name) != expected:
                raise ValueError(f"Frozen source changed: {name}")
        self.source_hashes.update(required)
        self.source_signature = sha(self.source_hashes)
        self.events = defaultdict(list)
        for row in jsonl(self.root / EVENTS):
            self.events[row["order_id"]].append(row)
        self.decisions = defaultdict(list)
        for row in jsonl(base / "stage_decisions.jsonl"):
            self.decisions[row["result"]["order_id"]].append(row)
        self.context = {r["order_id"]: r for r in jsonl(base / "static_context_scenario_inputs.jsonl")}
        self.references = {r["reference_id"]: r for r in read_csv(base / "reference_groups.csv")}
        self.train = read_csv(self.root / TRAIN)
        self.train_lookup = {(r["order_id"], r["stage"]): r for r in self.train}
        if len(self.train_lookup) != len(self.train):
            raise ValueError("Duplicate training stage keys")
        if set(self.events) & {r["order_id"] for r in self.train}:
            raise ValueError("Training and replay orders overlap")

    def case(self, order_id, event_id, mode="assumed_static_context_at_purchase"):
        if mode not in self.config["modes"] or order_id in PROTECTED:
            raise ValueError("Unsupported mode or protected review order")
        events = self.events.get(order_id, [])
        positions = [i for i, e in enumerate(events) if e["event_id"] == event_id]
        if len(positions) != 1:
            raise ValueError("Assessment must name one real observed event")
        visible = events[:positions[0] + 1]
        visible_ids = {e["event_id"] for e in visible}
        scores = {}
        revisions = []
        for row in self.decisions[order_id]:
            if row["event_id"] in visible_ids and row["result"]["mode"] == mode:
                scores[row["result"]["stage"]] = deepcopy(row["result"])
                revisions.append(row["sequence"])
        if set(scores) != set(STAGES):
            raise ValueError("Incomplete stage decision journal")
        return CaseTools(self, order_id, visible, mode, scores, max(revisions))

    def pilot(self):
        pools = defaultdict(list)
        for order_id, events in self.events.items():
            if order_id in PROTECTED:
                continue
            case = self.case(order_id, events[-1]["event_id"])
            flags = [r["long_stage_warning"] for r in case.scores.values()]
            stratum = ("warning" if any(f is True for f in flags) else
                       "complete_below_threshold" if all(f is False for f in flags) else "unassessed_or_partial")
            pools[stratum].append({"order_id": order_id, "event_id": events[-1]["event_id"],
                                   "as_of_event_time": case.asof, "stratum": stratum,
                                   "case_key": case.case_key})
        result = []
        for stratum, n in self.config["pilot"].items():
            if len(pools[stratum]) < n:
                raise ValueError("Insufficient real pilot cases")
            result.extend(sorted(pools[stratum], key=lambda r: sha(r["order_id"]))[:n])
        return result


class CaseTools:
    def __init__(self, store, order_id, events, mode, scores, revision):
        self.store, self.order_id, self.events, self.mode = store, order_id, events, mode
        self.scores, self.revision = scores, revision
        self.asof = events[-1]["occurred_at"]
        self.case_key = "olist-" + sha(order_id)[:20]
        self.context = deepcopy(store.context[order_id])
        self.registry = {}
        self.members = {}
        self.comparisons = {}
        self.tool_log = []

    def put(self, kind, stage, data, member_ids=()):
        record = {"case_key": self.case_key, "as_of_event_time": self.asof,
                  "assessment_revision": self.revision, "mode": self.mode, "kind": kind,
                  "stage": stage, "source_signature": self.store.source_signature, "data": data}
        evidence_id = sha(record)
        record["evidence_id"] = evidence_id
        self.registry[evidence_id] = deepcopy(record)
        self.members[evidence_id] = sorted(member_ids)
        return deepcopy(record)

    def get_observed_state(self):
        return self.put("observed_state", None, {
            "events": [{"activity": e["activity"], "occurred_at": e["occurred_at"]} for e in self.events],
            "promise_basis_status": "unverified_source_method_not_provided",
            "observation_clock": "source_event_time_not_ingestion_time"})

    def get_frozen_stage_assessment(self, stage):
        if stage not in STAGES:
            raise ValueError("Unknown stage")
        # Only the local order_id is removed; no score, status or warning is changed.
        data = {k: v for k, v in self.scores[stage].items() if k != "order_id"}
        return self.put("frozen_stage_assessment", stage, data)

    def get_reference_membership(self, stage):
        assessment = self.scores[stage]
        ref_id = assessment["selected_reference_id"]
        if ref_id is None:
            return self.put("reference_membership", stage, {"status": "unavailable", "n": None})
        ref = self.store.references[ref_id]
        ids = json.loads(ref["order_ids_json"])
        rows = [self.store.train_lookup[(oid, stage)] for oid in ids]
        temporal = all(r["end_timestamp"] < self.asof and r["split"] == "train" for r in rows)
        values = [float(r["duration_days"]) for r in rows]
        mean = statistics.fmean(values)
        numeric = (len(ids) == assessment["selected_reference_n"] and
                   abs(mean - assessment["selected_reference_mean_days"]) < 1e-10)
        if not temporal or not numeric or self.order_id in ids:
            raise ValueError("Frozen reference failed provenance / numerical check")
        return self.put("reference_membership", stage, {
            "status": "recomputed_from_training_members", "reference_id": ref_id,
            "n": len(rows), "mean_days": mean, "median_days": statistics.median(values),
            "latest_end": max(r["end_timestamp"] for r in rows), "member_ids_sha256": sha(sorted(ids)),
            "training_only_before_assessment": True}, ids)

    def get_available_context(self):
        allowed = self.mode == "assumed_static_context_at_purchase" and any(e["activity"] == "Purchased" for e in self.events)
        data = {"status": "available_under_explicit_assumption" if allowed else "availability_unverified",
                "category": "bed_bath_table", "context_availability_source_verified": False,
                "uncontrolled_factors": self.store.config["uncontrolled_factors"],
                "payment_and_review_features": "not_available_in_this_event_contract"}
        if allowed:
            data.update({k: v for k, v in self.context.items() if k not in {"order_id", "product_id", "single_seller_id"}})
            data["single_product_available"] = bool(self.context.get("product_id"))
            data["single_seller_available"] = bool(self.context.get("single_seller_id"))
        return self.put("available_context", None, data)

    def compare_declared_peer_groups(self, stage, plan):
        cfg = self.store.config
        if stage not in STAGES or plan not in cfg["peer_plans"]:
            raise ValueError("Undeclared comparison")
        cache_key = (stage, plan)
        if cache_key in self.comparisons:
            return deepcopy(self.registry[self.comparisons[cache_key]])
        c = self.context
        regional = plan.startswith("region_")
        by_product = plan in {"same_product_peers", "region_within_product"}
        base = {"plan": plan, "category_fallback": not by_product, "matched_on": cfg["matching"][plan],
                "uncontrolled_factors": cfg["uncontrolled_factors"] + ([] if by_product else ["product_identity"]),
                "claim_scope": "historical_completed_training_stages_not_current_cause",
                "causal_claim_allowed": False, "anomaly_rate": None,
                "anomaly_rate_reason": "no_independent_bottleneck_labels",
                "context_availability_source_verified": False}
        def finish(status, members=(), **metrics):
            result = self.put("peer_comparison", stage, {**base, "status": status, **metrics}, members)
            self.comparisons[cache_key] = result["evidence_id"]
            return result
        if self.mode != "assumed_static_context_at_purchase":
            return finish("context_availability_unverified")
        if self.scores[stage]["assessment_status"] != "assessed_under_context_assumption":
            return finish("stage_unassessed")
        if regional and stage != "transport":
            return finish("geographic_attribution_not_allowed_for_this_stage")
        if not c.get("single_seller_id") or c.get("seller_attribution_scope") != "single_seller_order":
            return finish("multi_seller_attribution_unavailable")
        if not c.get("all_pairs_quality_eligible") or not c.get("distance_band") or not c.get("customer_state"):
            return finish("missing_or_ineligible_geography")
        if by_product and not c.get("product_attributable"):
            return finish("single_product_attribution_unavailable")
        if regional:
            alternative_plan = "same_product_peers" if by_product else "category_peers"
            alternative_id = self.comparisons.get((stage, alternative_plan))
            if alternative_id is None:
                # This transient refusal is not cached: the analyst may perform the
                # required non-geographic check and then ask again within its budget.
                return self.put("peer_comparison", stage, {**base, "status": "alternatives_not_checked",
                                "required_prior_plan": alternative_plan})
            base["prior_peer_comparison_id"] = alternative_id
            base["prior_peer_status"] = self.registry[alternative_id]["data"]["status"]
            base["all_alternative_causes_excluded"] = False
        rows = []
        for r in self.store.train:
            if (r["stage"] != stage or r["split"] != "train" or r["end_timestamp"] >= self.asof
                    or r["duration_eligible"] != "True" or r["all_pairs_quality_eligible"] != "True"
                    or r["quality_conflict"] != "False" or r["seller_attribution_scope"] != "single_seller_order"):
                continue
            if r["single_seller_id"] != c["single_seller_id"] or int(r["item_count"]) != c["item_count"] or r["distance_band"] != c["distance_band"]:
                continue
            if by_product and (r["product_attributable"] != "True" or r["product_id"] != c["product_id"]):
                continue
            if not regional and r["customer_state"] != c["customer_state"]:
                continue
            rows.append(r)
        ids = [r["order_id"] for r in rows]
        if not regional:
            n = len(rows)
            mean = statistics.fmean(float(r["duration_days"]) for r in rows) if rows else None
            return finish("descriptive_peer_reference" if n >= cfg["min_peer_orders"] else "insufficient_comparable_orders",
                          ids, n=n, mean_days=mean,
                          difference_from_peer_mean_days=self.scores[stage]["duration_days"] - mean if mean is not None else None,
                          minimum_n=cfg["min_peer_orders"], member_ids_sha256=sha(sorted(ids)),
                          period_matching="current_period_not_matched_historical_reference_only")
        # Historical target/control comparison: exact month strata, no outcome matching.
        cells = defaultdict(lambda: [[], []])
        for r in rows:
            key = (r["start_timestamp"][:7], r["single_seller_id"], int(r["item_count"]), r["distance_band"])
            if by_product:
                key += (r["product_id"],)
            cells[key][0 if r["customer_state"] == c["customer_state"] else 1].append(r)
        target_total = sum(len(a) for a, b in cells.values())
        control_total = sum(len(b) for a, b in cells.values())
        matched = [(a, b) for a, b in cells.values() if min(len(a), len(b)) >= cfg["min_cell_orders_per_side"]]
        nt, nc = sum(len(a) for a, b in matched), sum(len(b) for a, b in matched)
        ct, cc = nt / target_total if target_total else 0.0, nc / control_total if control_total else 0.0
        supported = (nt >= cfg["min_group_orders"] and nc >= cfg["min_group_orders"]
                     and ct >= cfg["min_target_coverage"] and cc >= cfg["min_control_coverage"])
        target_mean = sum(sum(float(r["duration_days"]) for r in a) for a, b in matched) / nt if nt else None
        control_mean = sum(len(a) * statistics.fmean(float(r["duration_days"]) for r in b) for a, b in matched) / nt if nt else None
        delta = target_mean - control_mean if nt else None
        matched_ids = [r["order_id"] for a, b in matched for r in a + b]
        return finish("supported_descriptive_comparison" if supported else "insufficient_comparable_orders",
                      matched_ids, target_state=c["customer_state"], target_total=target_total, control_total=control_total,
                      target_n=nt, control_n=nc, target_coverage=ct, control_coverage=cc, matched_cells=len(matched),
                      target_mean_days=target_mean, target_weighted_control_mean_days=control_mean,
                      difference_days=delta, direction=("higher" if delta > 0 else "lower" if delta < 0 else "equal") if supported else None,
                      member_ids_sha256=sha(sorted(matched_ids)), significance_test_performed=False)

    def resolve_evidence(self, evidence_id):
        if evidence_id not in self.registry:
            raise ValueError("Unknown evidence for this case / event / mode")
        return deepcopy(self.registry[evidence_id])

    def get_group_support_and_coverage(self, evidence_id):
        record = self.resolve_evidence(evidence_id)
        if record["kind"] != "peer_comparison":
            raise ValueError("Not a group comparison")
        return record

    def recompute_reported_metrics(self, stage):
        r = self.scores[stage]
        duration = elapsed(r["start_timestamp"], r["end_timestamp"]) if r["start_timestamp"] and r["end_timestamp"] else None
        reference = self.get_reference_membership(stage)
        scored = r["delay_score_0_100"] is not None
        difference = duration - reference["data"]["mean_days"] if scored else None
        matches = (not scored or (abs(duration - r["duration_days"]) < 1e-10 and
                   abs(difference - r["difference_from_reference_mean_days"]) < 1e-10))
        return self.put("recomputed_metrics", stage, {"scored": scored, "matches_frozen": matches,
                        "signed_observed_duration_days": duration, "difference_from_reference_mean_days": difference})

    def check_temporal_and_comparison_contract(self):
        future = any(t and t > self.asof for r in self.scores.values() for t in [r["start_timestamp"], r["end_timestamp"]])
        return self.put("temporal_contract", None, {"no_future_stage_endpoints": not future,
            "case_excluded_from_training": all(r["order_id"] != self.order_id for r in self.store.train),
            "history_used": "completed_training_stages_before_assessment",
            "mode": self.mode, "static_availability_verified": False, "causal_claim_allowed": False,
            "missing_stage_is_not_a_confirmed_missing_source_record": True})
