"""Conservative, auditable eligibility for exploratory geographic modeling."""

import json

import numpy as np
import pandas as pd

import build_geographic_features as geo


SPREADS = ["spread_p90_km", "spread_max_km", "representative_sensitivity_km"]


def protocol():
    return {
        "version": "geographic_quality_screen_v1",
        "minimum_unique_points": 2,
        "latitude_envelope": [-35, 7],
        "longitude_envelope": [-75, -25],
        "envelope_meaning": "deliberately_broad_Brazil_screen_not_country_or_state_polygon",
        "envelope_reference": "https://anuario.ibge.gov.br/2024/territorio/posicao-e-extensao.html",
        "fit_reference": "unique_structurally_eligible_ZIPs_linked_to_inner_fit_orders_only",
        "dispersion_columns": SPREADS,
        "dispersion_formula": "expm1(Q75(log1p(x)) + 3 * IQR(log1p(x)))",
        "comparison": "strict_greater_than",
        "state_policy": "one_nonmissing_source_state_matching_actual_customer_or_seller_state",
        "order_policy": "every_distinct_seller_customer_pair_must_pass",
        "interpretation": "provisional_model_eligibility_not_verified_location_or_business_anomaly",
        "source_rows_changed": False,
        "coordinate_imputation": False,
    }


def assess(zips, evidence, pairs, fit_ids, states):
    """Use only coordinate diagnostics, never duration/outcome/model scores."""
    geo.business.require_key(zips, [geo.ZIP], "ZIP quality source")
    geo.business.require_key(states, ["order_id", "seller_id"], "actual source states")
    if not set(fit_ids) or not set(fit_ids).issubset(set(pairs.order_id)):
        raise ValueError("Nonempty fit IDs must belong to geographic development pairs")
    policy = protocol()
    zips = zips.sort_values(geo.ZIP).reset_index(drop=True).copy()
    evidence = evidence.copy()
    inside = (evidence.geolocation_lat.between(*policy["latitude_envelope"])
              & evidence.geolocation_lng.between(*policy["longitude_envelope"]))
    evidence["inside_broad_brazil_envelope"] = inside.fillna(False)
    counts = evidence.groupby(geo.ZIP).inside_broad_brazil_envelope.agg(lambda x: int((~x).sum()))
    zips["outside_envelope_rows"] = zips[geo.ZIP].map(counts).fillna(0).astype(int)
    zips["single_source_state"] = zips.source_state_codes_json.map(
        lambda x: json.loads(x)[0] if len(json.loads(x)) == 1 else "unavailable")
    checks = {
        "coordinates_unavailable": zips.availability.ne("available"),
        "insufficient_coordinate_support": zips.unique_valid_points.lt(policy["minimum_unique_points"]),
        "has_invalid_coordinate_rows": zips.invalid_coordinate_rows.gt(0),
        "coordinate_outside_broad_envelope": zips.outside_envelope_rows.gt(0),
        "ambiguous_or_missing_source_state": zips.source_state_count.ne(1),
    }
    structural = ~pd.DataFrame(checks).any(axis=1)
    linked = pairs.loc[pairs.order_id.isin(fit_ids)]
    reference_zips = set(linked.customer_zip_code_prefix) | set(linked.seller_zip_code_prefix)
    zips["linked_to_inner_fit"] = zips[geo.ZIP].isin(reference_zips)
    zips["used_for_quality_threshold_fit"] = structural & zips.linked_to_inner_fit
    reference = zips.loc[zips.used_for_quality_threshold_fit]
    if len(reference) < 20:
        raise ValueError("Insufficient actual ZIPs for quality reference; no invented support")
    thresholds = []
    for name in SPREADS:
        values = reference[name].to_numpy(dtype=float)
        if not np.isfinite(values).all() or (values < 0).any():
            raise ValueError("ZIP dispersion must be finite and nonnegative")
        q25, q75 = np.quantile(np.log1p(values), [.25, .75], method="linear")
        limit = float(np.expm1(q75 + 3*(q75-q25)))
        thresholds.append({"diagnostic": name, "fit_zip_count": len(reference), "log_q25": q25,
                           "log_q75": q75, "threshold_km": limit, "iqr_multiplier": 3.0})
        checks[f"high_{name}"] = zips[name].gt(limit)
    for name, values in checks.items():
        zips[name] = values
    zips["zip_quality_eligible"] = ~pd.DataFrame(checks).any(axis=1)
    zips["quality_reasons"] = [";".join(k for k in checks if bool(zips.at[i, k])) or "screen_passed"
                               for i in zips.index]
    selected = [geo.ZIP, "zip_quality_eligible", "quality_reasons", "single_source_state"]
    result = pairs.copy().merge(states, on=["order_id", "seller_id"], how="left", validate="one_to_one")
    if len(result) != len(states) or result[["customer_state", "seller_state"]].isna().any().any():
        raise ValueError("Actual source states must reconcile with every order/seller pair")
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
    return {"zip_quality.csv": zips, "pair_quality.csv": result.sort_values(["order_id", "seller_id"]).reset_index(drop=True),
            "order_quality.csv": orders.reset_index(), "quality_thresholds.csv": pd.DataFrame(thresholds),
            "outside_envelope_evidence.csv": evidence.loc[~evidence.inside_broad_brazil_envelope].reset_index(drop=True)}
