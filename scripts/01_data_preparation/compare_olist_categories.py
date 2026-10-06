"""Descriptive category comparison with explicit cohort exclusions and common support."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import apply_olist_usage_policy as usage


audit = usage.audit
DEFAULT_OUTPUT = audit.PROCESSED_DATA_DIR / "olist_category_comparison_v1"
DEFAULT_RULES = {"min_category_orders": 2000, "min_month_orders": 30,
                 "min_supported_months": 12, "min_stratum_orders": 5}
CAT = "single_category_name"
MONTH = "purchase_month"


def verify_inputs(clean_dir, policy_dir):
    clean_dir, policy_dir = Path(clean_dir), Path(policy_dir)
    clean_manifest = usage.verify_snapshot(clean_dir)
    policy_manifest = json.loads((policy_dir / "manifest.json").read_text())
    if policy_manifest.get("status") != "complete" or policy_manifest.get("policy_version") != "olist_usage_v1":
        raise ValueError("Expected a completed olist_usage_v1 snapshot")
    if policy_manifest["source_manifest_sha256"] != audit.file_hash(clean_dir / "manifest.json"):
        raise ValueError("Usage policy does not refer to this clean snapshot")
    if policy_manifest["source_output_hashes"] != clean_manifest["output_hashes"]:
        raise ValueError("Clean and policy source hashes differ")
    if "case_usage_policy.csv" not in policy_manifest.get("output_hashes", {}):
        raise ValueError("Missing case policy hash")
    for filename, digest in policy_manifest["output_hashes"].items():
        path = (policy_dir / filename).resolve()
        if not path.is_relative_to(policy_dir.resolve()) or not path.is_file() or audit.file_hash(path) != digest:
            raise ValueError(f"Policy snapshot hash mismatch: {filename}")
    return clean_manifest, policy_manifest


def load_inputs(clean_dir=usage.clean.DEFAULT_OUTPUT, policy_dir=usage.DEFAULT_OUTPUT):
    clean_dir, policy_dir = Path(clean_dir), Path(policy_dir)
    clean_manifest, policy_manifest = verify_inputs(clean_dir, policy_dir)
    schema = dict(clean_manifest["schemas"]["order_quality.csv"])
    schema.update({"usage_group": "string", "source_verification_priority": "bool",
                   "process_evidence_retained": "bool", "ongoing_age_computable": "bool",
                   "timing_exclusion_reasons": "string"})
    for column in usage.DURATIONS:
        schema[f"{column}_state"] = "string"
        schema[f"{column}_nonnegative_observed"] = "bool"
    cases = usage.read_snapshot_table(policy_dir, "case_usage_policy.csv",
                                      {"schemas": {"case_usage_policy.csv": schema}})
    tables = {name: usage.read_snapshot_table(clean_dir, audit.FILES[name], clean_manifest)
              for name in ["orders", "customers", "order_items", "products", "sellers"]}
    usage.validate_cases(cases, tables["orders"])
    if not cases.single_category_timing_eligible.eq(cases.category_assignment_eligible & cases.completed_timing_eligible).all():
        raise ValueError("Single-category eligibility is inconsistent")
    actual_month = cases.order_purchase_timestamp.dt.to_period("M").astype("string")
    if not actual_month.fillna("").eq(cases.purchase_month.fillna("")).all():
        raise ValueError("Purchase month differs from actual timestamp")
    context = cases.merge(tables["customers"][["customer_id", "customer_state"]],
                          on="customer_id", how="left", validate="many_to_one")
    pairs = tables["order_items"][["order_id", "seller_id"]].drop_duplicates()
    single_pairs = pairs.loc[pairs.groupby("order_id").seller_id.transform("size").eq(1)]
    single_pairs = single_pairs.merge(tables["sellers"][["seller_id", "seller_state"]],
                                      on="seller_id", how="left", validate="many_to_one")
    single_pairs = single_pairs.rename(columns={"seller_id": "single_seller_id", "seller_state": "single_seller_state"})
    context = context.merge(single_pairs, on="order_id", how="left", validate="one_to_one")
    if len(context) != len(cases) or context.customer_state.isna().any():
        raise ValueError("Customer enrichment changed grain or has unmatched states")
    if not context.single_seller_id.notna().eq(context.seller_count.eq(1)).all():
        raise ValueError("Seller enrichment differs from the recorded seller count")
    if context.loc[context.seller_count.eq(1), "single_seller_state"].isna().any():
        raise ValueError("Single seller has no recorded state")
    membership = tables["order_items"][["order_id", "product_id"]].merge(
        tables["products"][["product_id", "product_category_name"]], on="product_id", validate="many_to_one",
    )[["order_id", "product_category_name"]].dropna().drop_duplicates()
    parents = {"clean_manifest_sha256": audit.file_hash(clean_dir / "manifest.json"),
               "policy_manifest_sha256": audit.file_hash(policy_dir / "manifest.json"),
               "clean_output_hashes": clean_manifest["output_hashes"],
               "policy_output_hashes": policy_manifest["output_hashes"]}
    return context.sort_values("order_id").reset_index(drop=True), pairs, membership, parents


def select_window(cases, rules):
    if set(rules) != set(DEFAULT_RULES) or any(not isinstance(v, int) or v < 1 for v in rules.values()):
        raise ValueError("Coverage rules must be positive integers with known names")
    single = cases.loc[cases.category_assignment_eligible]
    months = pd.period_range(single.order_purchase_timestamp.min(), single.order_purchase_timestamp.max(), freq="M").astype(str)
    matrix = single.groupby([CAT, MONTH]).size().unstack(fill_value=0).reindex(columns=months, fill_value=0)
    gate = pd.DataFrame({"single_category_orders": single.groupby(CAT).size(),
                         "supported_months": matrix.ge(rules["min_month_orders"]).sum(axis=1)})
    gate["passes_volume_gate"] = gate.single_category_orders.ge(rules["min_category_orders"])
    gate["passes_month_gate"] = gate.supported_months.ge(rules["min_supported_months"])
    gate["candidate"] = gate.passes_volume_gate & gate.passes_month_gate
    candidates = sorted(gate.index[gate.candidate])
    if len(candidates) < 2:
        raise ValueError("Fewer than two categories meet the coverage rules")
    supported = matrix.loc[candidates].ge(rules["min_month_orders"]).all(axis=0)
    runs, current = [], []
    for month, keep in supported.items():
        if keep:
            current.append(month)
        elif current:
            runs.append(current)
            current = []
    if current:
        runs.append(current)
    if not runs:
        raise ValueError("No shared purchase month; do not silently compare incompatible periods")
    chosen = sorted(runs, key=lambda run: (-len(run), run[0]))[0]
    if len(chosen) < rules["min_supported_months"]:
        raise ValueError("Shared contiguous period is shorter than the declared minimum")
    window = {"first_month": chosen[0], "last_month": chosen[-1], "month_count": len(chosen),
              "start_inclusive": str(pd.Period(chosen[0]).start_time),
              "end_exclusive": str((pd.Period(chosen[-1]) + 1).start_time), "candidates": candidates}
    monthly = matrix.rename_axis(index=CAT, columns=MONTH).stack().rename("single_category_orders").reset_index()
    monthly["candidate"] = monthly[CAT].isin(candidates)
    monthly["supported_month"] = monthly.single_category_orders.ge(rules["min_month_orders"])
    monthly["in_common_window"] = monthly[MONTH].isin(chosen)
    return gate, monthly, window


def build_ledger(cases, gate, window):
    columns = ["order_id", "order_status", "order_purchase_timestamp", MONTH, "category_scope", CAT,
               "single_category_name_english", "usage_group", "timing_exclusion_reasons"]
    ledger = cases[columns].copy()
    ledger["category_eligible"] = cases.category_assignment_eligible
    ledger["candidate_category"] = cases[CAT].isin(gate.index[gate.candidate])
    ledger["in_common_window"] = cases.order_purchase_timestamp.ge(pd.Timestamp(window["start_inclusive"])) & cases.order_purchase_timestamp.lt(pd.Timestamp(window["end_exclusive"]))
    ledger["completed_timing_eligible"] = cases.completed_timing_eligible
    ledger["included"] = ledger.category_eligible & ledger.candidate_category & ledger.in_common_window & ledger.completed_timing_eligible
    ledger["disposition"] = "included"
    ledger.loc[~ledger.completed_timing_eligible, "disposition"] = "timing_ineligible:" + ledger.usage_group
    ledger.loc[~ledger.in_common_window, "disposition"] = "outside_common_period"
    ledger.loc[~ledger.candidate_category, "disposition"] = "below_category_coverage_gate"
    ledger.loc[~ledger.category_eligible, "disposition"] = "category_scope:" + ledger.category_scope
    return ledger


def category_overview(cases, membership, gate, ledger, window):
    single = cases.loc[cases.category_assignment_eligible]
    period = single.loc[single.order_purchase_timestamp.ge(pd.Timestamp(window["start_inclusive"]))
                        & single.order_purchase_timestamp.lt(pd.Timestamp(window["end_exclusive"]))]
    names = single.groupby(CAT).single_category_name_english.first()
    overview = gate.copy()
    overview["category_english"] = names
    overview["orders_with_category_nonexclusive"] = membership.groupby("product_category_name").order_id.nunique()
    overview["period_single_category_orders"] = period.groupby(CAT).size().reindex(gate.index, fill_value=0)
    eligible = period.loc[period.completed_timing_eligible].groupby(CAT).size().reindex(gate.index, fill_value=0)
    overview["period_timing_eligible_orders"] = eligible
    overview["period_timing_excluded_orders"] = overview.period_single_category_orders - eligible
    overview["period_timing_excluded_fraction"] = overview.period_timing_excluded_orders.div(overview.period_single_category_orders.replace(0, np.nan))
    overview["comparison_orders"] = ledger.loc[ledger.included].groupby(CAT).size().reindex(gate.index, fill_value=0)
    exclusions = period.groupby([CAT, "usage_group"]).size().rename("orders").reset_index()
    exclusions["category_candidate"] = exclusions[CAT].isin(gate.index[gate.candidate])
    return overview.reset_index().sort_values(["single_category_orders", CAT], ascending=[False, True]), exclusions


def duration_summary(main):
    rows = []
    for category, group in main.groupby(CAT, sort=True):
        for column in usage.DURATIONS:
            value = group[column]
            rows.append({CAT: category, "duration": column, "orders": len(group), "mean_days": value.mean(),
                         "median_days": value.median(), "p05_days": value.quantile(.05), "q25_days": value.quantile(.25),
                         "q75_days": value.quantile(.75), "p95_days": value.quantile(.95),
                         "max_days": value.max(), "zero_duration_orders": int(value.eq(0).sum())})
    return pd.DataFrame(rows)


def seller_summary(main, pairs):
    rows = []
    for category, group in main.groupby(CAT, sort=True):
        single = group.loc[group.seller_count.eq(1)]
        counts = single.single_seller_id.value_counts()
        rows.append({CAT: category, "comparison_orders": len(group), "single_seller_orders": len(single),
                     "multi_seller_orders": int(group.seller_count.gt(1).sum()),
                     "multi_seller_fraction": group.seller_count.gt(1).mean(),
                     "distinct_sellers": pairs.loc[pairs.order_id.isin(group.order_id), "seller_id"].nunique(),
                     "largest_seller_orders_among_single_seller": counts.max() if len(counts) else 0,
                     "largest_seller_share_among_single_seller": counts.max() / len(single) if len(single) else np.nan,
                     "same_state_fraction_among_single_seller": single.customer_state.eq(single.single_seller_state).mean(),
                     "single_seller_median_total_days": single.total_cycle_time_days.median()})
    return pd.DataFrame(rows)


def region_sensitivity(main, candidates, min_orders):
    cells = main.groupby([MONTH, "customer_state", CAT]).agg(
        orders=("order_id", "size"), mean_total_days=("total_cycle_time_days", "mean"),
    ).reset_index()
    matrix = cells.pivot(index=[MONTH, "customer_state"], columns=CAT, values="orders").reindex(columns=candidates).fillna(0)
    supported = matrix.ge(min_orders).all(axis=1)
    common = matrix.loc[supported].min(axis=1).rename("minimum_category_orders").reset_index()
    common["reference_weight"] = 1 / len(common) if len(common) else pd.Series(dtype=float)
    cells = cells.merge(common, on=[MONTH, "customer_state"], how="left", validate="many_to_one")
    cells["in_common_support"] = cells.reference_weight.notna()
    rows = []
    for category in candidates:
        group = main.loc[main[CAT].eq(category)]
        included = cells.loc[cells[CAT].eq(category) & cells.in_common_support]
        n = int(included.orders.sum())
        rows.append({CAT: category, "all_comparison_orders": len(group), "supported_orders": n,
                     "supported_fraction": n / len(group) if len(group) else np.nan,
                     "common_strata": len(common), "raw_mean_total_days": group.total_cycle_time_days.mean(),
                     "raw_mean_on_supported_strata_days": (included.mean_total_days * included.orders).sum() / n if n else np.nan,
                     "standardized_mean_total_days": (included.mean_total_days * included.reference_weight).sum() if len(common) else np.nan})
    return pd.DataFrame(rows), common, cells


def tail_sensitivity(cases, window):
    rows = []
    for category in window["candidates"]:
        base = cases.loc[cases.category_assignment_eligible & cases[CAT].eq(category)]
        for scenario, end in [("common_window", pd.Timestamp(window["end_exclusive"])),
                              ("omit_last_purchase_month", pd.Period(window["last_month"]).start_time)]:
            period = base.loc[base.order_purchase_timestamp.ge(pd.Timestamp(window["start_inclusive"])) & base.order_purchase_timestamp.lt(end)]
            eligible = period.loc[period.completed_timing_eligible]
            rows.append({CAT: category, "scenario": scenario, "all_single_category_orders": len(period),
                         "timing_eligible_orders": len(eligible), "timing_excluded_orders": len(period) - len(eligible),
                         "timing_excluded_fraction": 1 - len(eligible) / len(period) if len(period) else np.nan,
                         "mean_total_days": eligible.total_cycle_time_days.mean(), "median_total_days": eligible.total_cycle_time_days.median()})
    return pd.DataFrame(rows)


def build_comparison(cases, pairs, membership, rules=None):
    rules = dict(DEFAULT_RULES if rules is None else rules)
    cases = cases.sort_values("order_id").reset_index(drop=True)
    gate, monthly, window = select_window(cases, rules)
    ledger = build_ledger(cases, gate, window)
    main = cases.loc[ledger.included]
    if set(main[CAT]) != set(window["candidates"]):
        raise ValueError("A candidate has no eligible completed cases in the common period")
    overview, exclusions = category_overview(cases, membership, gate, ledger, window)
    region, strata, cells = region_sensitivity(main, window["candidates"], rules["min_stratum_orders"])
    frames = {"category_overview.csv": overview, "selection_ledger.csv": ledger,
              "selection_summary.csv": ledger.groupby("disposition").size().rename("orders").reset_index(),
              "monthly_coverage.csv": monthly, "period_usage_groups.csv": exclusions,
              "duration_summary.csv": duration_summary(main), "seller_composition.csv": seller_summary(main, pairs),
              "region_standardization.csv": region, "common_region_month_strata.csv": strata,
              "region_month_cells.csv": cells, "tail_month_sensitivity.csv": tail_sensitivity(cases, window)}
    return frames, {"rules": rules, "window": window, "all_orders": len(cases), "comparison_orders": len(main)}


def render_readme(frames, design):
    overview = frames["category_overview.csv"]
    shown = overview.loc[overview.candidate, [CAT, "category_english", "period_single_category_orders", "comparison_orders", "period_timing_excluded_fraction"]]
    timing = frames["duration_summary.csv"].query("duration == 'total_cycle_time_days'")[[CAT, "median_days", "p95_days"]]
    shown = shown.merge(timing, on=CAT, validate="one_to_one").round(4)
    window = design["window"]
    return "\n\n".join([
        "# Olist category comparison v1",
        "## Scope\nDescriptive feasibility and duration comparison from verified clean/usage snapshots. No raw observations, source labels, saved models or thresholds are modified. No category is automatically chosen. No model training, accuracy comparison, significance test, causal estimate or calibrated anomaly probability is produced.",
        "## Coverage design\n```json\n" + json.dumps(design, indent=2) + "\n```",
        "Candidate gates use ALL fully known single-category orders, including incomplete and reversed timelines: at least min_category_orders in the source history and min_supported_months with at least min_month_orders each. These are declared operational coverage choices, NOT universal statistical sample-size requirements or learned anomaly thresholds. All source categories remain in category_overview.csv, including those failing the gates. The rules were set before inspecting duration differences.",
        "The common period is the longest contiguous run where every candidate meets the monthly order-count gate; ties use the earliest run. Months are purchase months. Start is inclusive, end exclusive. Zero monthly counts are observed absence, not generated orders. The period is common OBSERVED support, not proof of complete extraction or equivalent follow-up. No source extraction cutoff is invented.",
        f"## Common-period comparison\nPurchase months {window['first_month']} through {window['last_month']}.\n\n" + audit.markdown_table(shown),
        "Rows are ordered by all-history single-category volume, not delivery speed, anomaly rate or model quality. comparison_orders are delivered, complete, nondecreasing single-category cases. Durations retain zeros and long tails. Median/P05/P95 and other quantiles describe these observations, are NOT detection thresholds, and must not be carried into a future held-out experiment as training estimates. Low duration is not automatically better data or a better thesis category.",
        "## Selection reconciliation\n" + audit.markdown_table(frames["selection_summary.csv"]),
        "selection_ledger.csv retains one row for EVERY source order. Exclusive disposition precedence is category scope, coverage gate, common period, then completed-timing eligibility. Individual flags and timing reasons preserve overlaps. period_usage_groups.csv exposes each category's completed, missing, reversed, canceled/unavailable and nonfinal groups within the common period. These groups are data-use categories, not anomaly labels. orders_with_category_nonexclusive may count an order under several categories; single-category counts are disjoint. Unclassified and mixed orders are not silently assigned to an industry.",
        "## Region and month sensitivity\nregion_month_cells.csv uses actual eligible orders by purchase month, customer state and category. Common support requires at least min_stratum_orders cases from EVERY candidate in each cell. common_region_month_strata.csv records supported cells and equal weights summing to one. The standardized mean is the sum of each cell's mean total days times that cell's common reference weight, never an average of medians. The chosen cell mixture is a descriptive reference distribution, NOT the Brazilian market population.",
        "region_standardization.csv shows the raw whole-cohort mean, raw mean restricted to common-support cells, standardized mean, retained counts and coverage. The first restriction changes the population; weighting then changes its month/state mix. If common support is empty the standardized result is unavailable, not zero. Low-support states remain visible but are not assigned invented estimates. This does not control seller identity/origin, freight service, product mix or unmeasured confounding and is not a causal industry effect.",
        "## Seller and tail checks\nseller_composition.csv reports actual distinct sellers, multi-seller cases, largest-seller share among SINGLE-seller orders and same-state delivery share with the same denominator. Multi-seller orders have no arbitrarily assigned first seller. Single-seller median is a sensitivity view, not a replacement for all eligible cases. These diagnostics do not adjust for seller effects.",
        "tail_month_sensitivity.csv repeats the descriptive calculation after omitting the final purchase month. This tests sensitivity to that period, but does NOT correct right-censoring or establish the extraction date. Incomplete orders remain in each scenario's exclusion denominator. Outcomes for unresolved orders cannot be inferred.",
        "## Interpretation boundary\nUse coverage, completeness, time support, seller concentration and category meaning together to propose a main industry. Do not choose solely for fast delivery or favorable anomaly scores. The Olist health_beauty taxonomy is broader than cosmetics; no unsupported finer label is inferred. These descriptive comparisons cannot prove a detector works or transfers to another marketplace. After the user chooses a primary category, freeze the cohort and a chronological train/validation/test design, fit preprocessing/thresholds only on training data, and evaluate with independently reviewed cases.",
        "## Reproduce\nRun `.venv/bin/python scripts/01_data_preparation/compare_olist_categories.py`. It refuses an existing destination. --output selects a new comparison version. --min-category-orders, --min-month-orders, --min-supported-months and --min-stratum-orders expose the declared design parameters. --clean and --policy select explicitly linked snapshots. manifest.json contains parameters, source manifests/output hashes, code hashes, counts and output checksums. No existing downstream script is switched to new inputs automatically.",
    ]) + "\n"


def write_comparison(frames, design, parents, clean_dir=usage.clean.DEFAULT_OUTPUT,
                     policy_dir=usage.DEFAULT_OUTPUT, output=DEFAULT_OUTPUT):
    clean_dir, policy_dir, output = Path(clean_dir), Path(policy_dir), Path(output)
    if output.exists():
        raise FileExistsError(f"Comparison already exists: {output}")
    def check_parents():
        clean_manifest, policy_manifest = verify_inputs(clean_dir, policy_dir)
        if (audit.file_hash(clean_dir / "manifest.json") != parents["clean_manifest_sha256"]
                or audit.file_hash(policy_dir / "manifest.json") != parents["policy_manifest_sha256"]
                or clean_manifest["output_hashes"] != parents["clean_output_hashes"]
                or policy_manifest["output_hashes"] != parents["policy_output_hashes"]):
            raise ValueError("Source changed since comparison preparation")
    check_parents()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "README.md").write_text(render_readme(frames, design), encoding="utf-8")
        manifest = {"status": "complete", "comparison_version": "olist_category_comparison_v1",
                    "created_at_utc": datetime.now(timezone.utc).isoformat(), "pandas_version": pd.__version__,
                    "design": design, "parents": parents, "source_clean": str(clean_dir.resolve()),
                    "source_policy": str(policy_dir.resolve()),
                    "code_hashes": {p.name: audit.file_hash(p) for p in [Path(__file__), Path(usage.__file__), Path(audit.__file__)]},
                    "output_hashes": {p.name: audit.file_hash(p) for p in sorted(staging.iterdir())},
                    "model_training_performed": False, "primary_category_selected": False}
        check_parents()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        if output.exists():
            raise FileExistsError(f"Comparison appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clean", type=Path, default=usage.clean.DEFAULT_OUTPUT)
    parser.add_argument("--policy", type=Path, default=usage.DEFAULT_OUTPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    for name, value in DEFAULT_RULES.items():
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=value)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Comparison already exists: {args.output}")
    print("Verifying source snapshots and comparing common-period cohorts...", flush=True)
    cases, pairs, membership, parents = load_inputs(args.clean, args.policy)
    frames, design = build_comparison(cases, pairs, membership, {name: getattr(args, name) for name in DEFAULT_RULES})
    write_comparison(frames, design, parents, args.clean, args.policy, args.output)
    print(json.dumps(design, indent=2))
    print(frames["category_overview.csv"].loc[lambda f: f.candidate, [CAT, "period_single_category_orders", "comparison_orders"]].to_string(index=False))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
