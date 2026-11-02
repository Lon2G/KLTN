"""Apply frozen geography limits and stage references without fitting on new orders."""

import hashlib
import json

import numpy as np
import pandas as pd

import assess_geographic_quality as quality
import build_product_stage_scores as product

geo = quality.geo


def geography(zips, evidence, pairs, states, thresholds):
    """Same screen as assess_geographic_quality, but consume saved fit thresholds."""
    geo.business.require_key(zips, [geo.ZIP], "frozen-screen ZIPs")
    geo.business.require_key(states, ["order_id", "seller_id"], "actual states")
    geo.business.require_key(thresholds, ["diagnostic"], "frozen thresholds")
    if set(thresholds.diagnostic) != set(quality.SPREADS):
        raise ValueError("Expected all frozen geography thresholds")
    limits = thresholds.set_index("diagnostic").threshold_km
    if not np.isfinite(limits).all() or limits.lt(0).any():
        raise ValueError("Invalid frozen geography thresholds")
    policy = quality.protocol()
    zips = zips.sort_values(geo.ZIP).reset_index(drop=True).copy()
    inside = (evidence.geolocation_lat.between(*policy["latitude_envelope"])
              & evidence.geolocation_lng.between(*policy["longitude_envelope"])).fillna(False)
    counts = (~inside).groupby(evidence[geo.ZIP]).sum()
    zips["outside_envelope_rows"] = zips[geo.ZIP].map(counts).fillna(0).astype(int)
    zips["single_source_state"] = zips.source_state_codes_json.map(
        lambda x: json.loads(x)[0] if len(json.loads(x)) == 1 else "unavailable")
    checks = {
        "coordinates_unavailable": zips.availability.ne("available"),
        "insufficient_coordinate_support": zips.unique_valid_points.lt(policy["minimum_unique_points"]),
        "has_invalid_coordinate_rows": zips.invalid_coordinate_rows.gt(0),
        "coordinate_outside_broad_envelope": zips.outside_envelope_rows.gt(0),
        "ambiguous_or_missing_source_state": zips.source_state_count.ne(1),
        **{f"high_{name}": zips[name].gt(limits[name]) for name in quality.SPREADS},
    }
    for name, values in checks.items():
        zips[name] = values
    zips["zip_quality_eligible"] = ~pd.DataFrame(checks).any(axis=1)
    zips["quality_reasons"] = [";".join(k for k in checks if bool(zips.at[i, k])) or "screen_passed"
                               for i in zips.index]
    selected = [geo.ZIP, "zip_quality_eligible", "quality_reasons", "single_source_state"]
    result = pairs.merge(states, on=["order_id", "seller_id"], how="left", validate="one_to_one")
    if len(result) != len(states) or result[["customer_state", "seller_state"]].isna().any().any():
        raise ValueError("Actual states must reconcile with every pair")
    for role in ["customer", "seller"]:
        mapped = zips[selected].rename(columns={c: f"{role}_{c}" for c in selected})
        result = result.merge(mapped, left_on=f"{role}_zip_code_prefix", right_on=f"{role}_{geo.ZIP}",
                              how="left", validate="many_to_one").drop(columns=f"{role}_{geo.ZIP}")
        result[f"{role}_zip_quality_eligible"] = result[f"{role}_zip_quality_eligible"].fillna(False).astype(bool)
        result[f"{role}_quality_reasons"] = result[f"{role}_quality_reasons"].fillna("missing_zip")
        result[f"{role}_state_matches"] = result[f"{role}_state"].eq(result[f"{role}_single_source_state"]).fillna(False)
    result["pair_quality_eligible"] = result[["distance_available", "customer_zip_quality_eligible",
                                             "seller_zip_quality_eligible", "customer_state_matches", "seller_state_matches"]].all(axis=1)
    result["pair_quality_reasons"] = [";".join(
        [f"{role}:{getattr(row, role+'_quality_reasons')}" for role in ["customer", "seller"]
         if not getattr(row, role+"_zip_quality_eligible")]
        + [f"{role}:source_state_mismatch" for role in ["customer", "seller"]
           if not getattr(row, role+"_state_matches")]) or "screen_passed" for row in result.itertuples(index=False)]
    grouped = result.groupby("order_id", sort=True)
    orders = grouped.agg(all_pairs_quality_eligible=("pair_quality_eligible", "all"),
                         total_seller_pairs=("seller_id", "size"), eligible_seller_pairs=("pair_quality_eligible", "sum"),
                         geography_quality_reasons=("pair_quality_reasons", lambda x: ";".join(sorted(set(x)-{"screen_passed"})) or "screen_passed"))
    orders["screened_distance_max_km"] = grouped.distance_km.max().where(orders.all_pairs_quality_eligible)
    weighted = result.distance_km.mul(result.seller_item_count).groupby(result.order_id).sum(min_count=1)
    orders["screened_distance_item_weighted_mean_km"] = (weighted/grouped.seller_item_count.sum()).where(orders.all_pairs_quality_eligible)
    return zips, result.sort_values(["order_id", "seller_id"]).reset_index(drop=True), orders.reset_index()


def score_stages(rows, reference):
    """Preserve the category assessability gate and exact product fallback order."""
    geo.business.require_key(rows, ["order_id", "stage"], "stage candidates")
    geo.business.require_key(reference, ["order_id", "stage"], "frozen history")
    if (not reference.split.eq("train").all() or set(rows.order_id) & set(reference.order_id)
            or not reference.duration_eligible.all()
            or not reference.end_timestamp.lt(reference.visible_before_exclusive).all()
            or not np.isfinite(reference.duration_days).all() or reference.duration_days.lt(0).any()):
        raise ValueError("Reference must be disjoint, eligible train history")
    cache, registry, results = {}, {}, []

    def peers(stage, context, product_only):
        key = (stage, json.dumps(context, sort_keys=True), product_only)
        if key not in cache:
            mask = reference.stage.eq(stage) & reference.all_pairs_quality_eligible
            if product_only:
                mask &= reference.product_attributable
            for field, value in context.items():
                mask &= reference[field].eq(value).fillna(False)
            cache[key] = reference.loc[mask].sort_values("order_id")
        return cache[key]

    for row in rows.sort_values(["order_id", "stage"]).to_dict("records"):
        out = dict(row)
        out.update(selected_reference_id=pd.NA, selected_reference_scope="unavailable", selected_reference_n=pd.NA,
                   selected_reference_mean_days=np.nan, selected_reference_median_days=np.nan,
                   difference_from_reference_mean_days=np.nan, delay_score_0_100=np.nan,
                   reference_percentile_0_100=np.nan, severity="unassessed", long_stage_warning=pd.NA)
        if row["assessable"]:
            selected, context, scope = None, {}, ""
            if row["product_attributable"]:
                for fields in product.LEVELS:
                    context = {field: row[field] for field in fields}
                    group = peers(row["stage"], context, True)
                    if len(group) >= product.protocol()["minimum_reference_orders"]:
                        selected, scope = group, "product_context"
                        break
            if selected is None:
                context = json.loads(row["reference_context_json"])
                if "distance_band" not in context:
                    raise ValueError("Fallback must preserve geography")
                selected, scope = peers(row["stage"], context, False), "category_context_fallback"
                if len(selected) != row["reference_n"] or len(selected) < 30:
                    raise ValueError("Category fallback differs from frozen history")
            values = selected.duration_days.to_numpy(float)
            score, level, rank = product.tail_score(row["duration_days"], values)
            gid = hashlib.sha256(json.dumps([row["stage"], scope, context], sort_keys=True).encode()).hexdigest()
            registry[gid] = {"reference_id": gid, "stage": row["stage"], "scope": scope,
                             "context_json": json.dumps(context, sort_keys=True), "n": len(values),
                             "mean_days": float(values.mean()), "median_days": float(np.median(values)),
                             "latest_end": selected.end_timestamp.max(), "order_ids_json": json.dumps(selected.order_id.tolist())}
            out.update(selected_reference_id=gid, selected_reference_scope=scope, selected_reference_n=len(values),
                       selected_reference_mean_days=float(values.mean()), selected_reference_median_days=float(np.median(values)),
                       difference_from_reference_mean_days=row["duration_days"]-float(values.mean()),
                       delay_score_0_100=score, reference_percentile_0_100=100*rank,
                       severity=level, long_stage_warning=score > 0)
        results.append(out)
    scored = pd.DataFrame(results)
    scored["selected_reference_n"] = scored.selected_reference_n.astype("Int64")
    scored["selected_reference_id"] = scored.selected_reference_id.astype("string")
    scored["long_stage_warning"] = scored.long_stage_warning.astype("boolean")
    columns = ["reference_id", "stage", "scope", "context_json", "n", "mean_days", "median_days", "latest_end", "order_ids_json"]
    return scored, pd.DataFrame([registry[key] for key in sorted(registry)], columns=columns)
