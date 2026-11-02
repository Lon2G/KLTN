"""Product duration means and experimental upper-tail attention scores on real Olist."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import prepare_pilot_stage_evidence as pilot

ROOT, review, shared = pilot.ROOT, pilot.review, pilot.shared
signals = pilot.signals
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_product_stage_scores_v1"
GUIDE = ROOT/"docs/product_stage_scores.md"
VERSION = "product_stage_scores_v1"
LEVELS = [
    ["product_id", "single_seller_id", "item_count", "customer_state", "distance_band"],
    ["product_id", "single_seller_id", "item_count", "distance_band"],
    ["product_id", "item_count", "customer_state", "distance_band"],
    ["product_id", "item_count", "distance_band"],
]
TABLES = {"product_stage_statistics.csv", "order_product_links.csv", "train_stage_reference.csv",
          "selected_reference_groups.csv", "stage_scores.csv", "order_scores.csv", "pilot_scope.csv"}
LEVEL_NAMES = {"none": "Chưa có cảnh báo dài", "low": "Nhẹ", "medium": "Vừa", "high": "Cao",
               "unassessed": "Chưa đủ dữ liệu"}


def protocol():
    return {"version": VERSION, "category": "bed_bath_table",
            "purpose": "experimental_descriptive_product_reference_and_attention_score_not_optimal_detector",
            "product_mean": "one_observed_order_per_product_stage; single_product_single_seller_orders_only",
            "product_profile": "train_only_mean_median_sd_and_n; unconditioned_descriptive_not_stage_SLA",
            "product_context_levels": LEVELS, "minimum_reference_orders": 30,
            "fallback": "unchanged_category_context_with_quality_screened_distance; never_called_product_mean",
            "observation_policy": "unchanged_parent_complete_case_and_time_cutoffs; test_and_held_pilot_excluded",
            "score": "0 if d<=mean or d<=0 else max(0,min(100,100*(F(d)-0.95)/0.05)); F uses reference durations <= d",
            "bands": {"none": "score=0", "low": "0<score<=50", "medium": "50<score<=80", "high": "80<score<=100",
                      "unassessed": "score missing; never zero"},
            "band_definition": "equivalent_integer_count_comparisons_avoid_float_boundary_rounding",
            "meaning": "relative_upper_tail_attention_not_probability_absolute_damage_or_confirmed_bottleneck",
            "small_reference": "report 2000/n score step and limited tail resolution when n<100; ties may make larger jumps",
            "aggregation": "order maximum over available stages, with explicit partial coverage; no mean across stages",
            "promise": "calendar_lateness_kept_separate; promise_basis_unverified",
            "multi_product": "retain order_context_score_only; never_assign_entire_order_delay_to_each_product",
            "optimization": "bands_and_minimum_n_are_working_conventions_not_accuracy_selected",
            "new_labels": False, "cause_verified": False, "test_scored": False}


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in [Path(__file__), GUIDE]}


def verify_sources(provenance):
    for name, source in provenance["snapshots"].items():
        path = Path(source["directory"])
        if review.digest(path/"manifest.json") != source["manifest_sha256"]:
            raise ValueError(f"Source manifest changed: {name}")
        shared.verify_files(path, source["output_hashes"])
    pilot.load_snapshot(Path(provenance["snapshots"]["pilot"]["directory"]))
    signals.load_snapshot(Path(provenance["snapshots"]["signals"]["directory"]))
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Product scoring code or protocol changed; use a new version")


def load_inputs():
    manifests = {"pilot": pilot.load_snapshot(), "signals": signals.load_snapshot(signals.DEFAULT_OUTPUT)}
    directories = {"pilot": pilot.DEFAULT_OUTPUT, "signals": signals.DEFAULT_OUTPUT}
    integration = pilot.audit.readiness.integration
    im = integration.load_snapshot(integration.DEFAULT_OUTPUT)
    business = im["provenance"]["snapshots"]["business"]
    directories["business"] = Path(business["directory"])
    bm = review.read_json(directories["business"]/"manifest.json")
    if review.digest(directories["business"]/"manifest.json") != business["manifest_sha256"]:
        raise ValueError("Business product mapping does not match the verified integration")
    manifests["business"] = bm
    source = {"snapshots": {name: {"directory": str(directories[name]), "manifest_sha256": review.digest(directories[name]/"manifest.json"),
                                 "output_hashes": manifest["output_hashes"]} for name, manifest in manifests.items()},
              "code_hashes": code_hashes()}
    verify_sources(source)
    frames = {}
    for name in ["order_explanations.csv", "stage_comparisons.csv", "scope_ledger.csv"]:
        frames[name] = shared.read_table(pilot.DEFAULT_OUTPUT, "analyst_only/"+name, manifests["pilot"])
    public_orders = shared.read_table(signals.DEFAULT_OUTPUT, "order_signals.csv", manifests["signals"])
    public_stages = shared.read_table(signals.DEFAULT_OUTPUT, "stage_signals.csv", manifests["signals"])
    public_orders["review_id"] = pd.Series(pd.NA, index=public_orders.index, dtype="string")
    public_stages["review_id"] = pd.Series(pd.NA, index=public_stages.index, dtype="string")
    orders = pd.concat([public_orders, frames["order_explanations.csv"]], ignore_index=True)
    stages = pd.concat([public_stages, frames["stage_comparisons.csv"]], ignore_index=True)
    reference_inputs, sources = signals.load_inputs()
    reference = reference_inputs["train_reference"]
    cm = review.read_json(Path(sources["criteria_directory"])/"manifest.json")
    items = shared.read_table(directories["business"], "item_source_evidence.csv", bm)
    features = shared.read_table(directories["business"], "order_features.csv", bm)
    if not features.product_category_name.eq("cama_mesa_banho").all():
        raise ValueError("Business source is not the chosen category")
    permitted = set(orders.order_id) | set(reference.order_id)
    items = items.loc[items.order_id.isin(permitted)].reset_index(drop=True)
    return {"orders": orders, "stages": stages, "reference": reference, "items": items,
            "pilot_scope": frames["scope_ledger.csv"],
            "reference_end_before": pd.Timestamp(cm["summary"]["train_reference_end_before"])}, source


def order_metadata(items):
    shared.business.require_key(items, ["order_id", "order_item_id"], "source item")
    if items[["product_id", "seller_id"]].isna().any().any() or items.product_id.eq("").any() or items.seller_id.eq("").any():
        raise ValueError("Product/seller identifiers are required")
    groups = items.groupby("order_id", sort=True)
    meta = groups.agg(item_count=("order_item_id", "size"), product_count=("product_id", "nunique"),
                      mapped_seller_count=("seller_id", "nunique"))
    meta["product_id"] = groups.product_id.first().where(meta.product_count.eq(1)).astype("string")
    meta["single_seller_id"] = groups.seller_id.first().where(meta.mapped_seller_count.eq(1)).astype("string")
    meta["product_attributable"] = meta.product_count.eq(1) & meta.mapped_seller_count.eq(1)
    links = items.groupby(["order_id", "product_id"], as_index=False).agg(item_rows=("order_item_id", "size"))
    return meta.reset_index(), links


def validate_inputs(inputs):
    orders, stages, reference = [inputs[name] for name in ["orders", "stages", "reference"]]
    for frame, keys in [(orders, ["order_id"]), (stages, ["order_id", "stage"]), (reference, ["order_id", "stage"])]:
        shared.business.require_key(frame, keys, "product score source")
    if (not orders.split.eq("validation").all() or not reference.split.eq("train").all()
            or set(orders.order_id) & set(reference.order_id)
            or not reference.duration_eligible.all()
            or not reference.end_timestamp.lt(inputs["reference_end_before"]).all()
            or not reference.end_timestamp.lt(reference.visible_before_exclusive).all()
            or not orders[pilot.performance.TIMES[0]].ge(inputs["reference_end_before"]).all()):
        raise ValueError("Train history must precede validation, without self-inclusion or future milestones")
    if (set(stages.order_id) != set(orders.order_id) or not stages.stage.isin(signals.STAGES).all()
            or not stages.groupby("order_id").size().eq(3).all()):
        raise ValueError("Every candidate needs exactly the three original stage rows")
    scope = inputs["pilot_scope"]
    held = set(scope.loc[~scope.comparison_generated, "order_id"])
    if held & (set(orders.order_id) | set(reference.order_id) | set(inputs["items"].order_id)):
        raise ValueError("Held pilot outcomes/items cannot enter product scoring")
    if set(inputs["items"].order_id) != set(orders.order_id) | set(reference.order_id):
        raise ValueError("Product mapping missing or outside the permitted source scope")


def tail_score(duration, values):
    values = np.asarray(values, dtype=float)
    if (len(values) < protocol()["minimum_reference_orders"] or not np.isfinite(values).all()
            or (values < 0).any() or not np.isfinite(duration) or duration < 0):
        raise ValueError("Scores need sufficient nonnegative observed reference durations")
    n, count, mean = len(values), int(np.count_nonzero(values <= duration)), float(values.mean())
    # Use exact count comparisons for zero and severity boundaries, not rounded display scores.
    if duration <= mean or duration <= 0 or 20*count <= 19*n:
        return 0.0, "none", count/n
    score = min(100.0, 100.0*(20*count-19*n)/n)
    level = "low" if 40*count <= 39*n else "medium" if 100*count <= 99*n else "high"
    return score, level, count/n


def build_tables(inputs):
    validate_inputs(inputs)
    meta, links = order_metadata(inputs["items"])
    reference = inputs["reference"].merge(meta, on="order_id", validate="many_to_one")
    # Existing order tables may already have counts; the source item mapping is authoritative here.
    orders = inputs["orders"].drop(columns=["item_count", "product_count"], errors="ignore").merge(meta, on="order_id", validate="one_to_one")
    rows = inputs["stages"].merge(meta, on="order_id", validate="many_to_one")
    clean_reference = reference.loc[reference.product_attributable]
    by_product = {(pid, stage): group for (pid, stage), group in clean_reference.groupby(["product_id", "stage"], sort=True)}
    profile_rows = []
    for pid in sorted(links.product_id.unique()):
        for stage in signals.STAGES:
            group = by_product.get((pid, stage))
            values = group.duration_days.to_numpy(float) if group is not None else np.array([], dtype=float)
            n = len(values)
            profile_rows.append({"product_id": pid, "stage": stage, "reference_n": n,
                                 "mean_days": float(values.mean()) if n else np.nan,
                                 "median_days": float(np.median(values)) if n else np.nan,
                                 "sd_days": float(values.std(ddof=1)) if n > 1 else np.nan,
                                 "mean_support": "none" if not n else "sparse_descriptive_only" if n < 30 else "descriptive_product_history",
                                 "context_matched": False, "reference_latest_end": group.end_timestamp.max() if n else pd.NaT,
                                 "interpretation": "single_product_single_seller_order_duration_not_intrinsic_product_processing_time"})
    profiles = pd.DataFrame(profile_rows)
    lookup = profiles.set_index(["product_id", "stage"])
    cache, registry, scored_rows = {}, {}, []

    def peers_for(stage, context, product_only):
        key = (stage, json.dumps(context, sort_keys=True), product_only)
        if key not in cache:
            if product_only:
                group = by_product.get((context["product_id"], stage), reference.iloc[:0])
            else:
                group = reference.loc[reference.stage.eq(stage)]
            mask = group.all_pairs_quality_eligible.copy()
            for field, value in context.items():
                mask &= group[field].eq(value).fillna(False)
            cache[key] = group.loc[mask].sort_values("order_id")
        return cache[key]

    for row in rows.to_dict("records"):
        out = {key: row[key] for key in ["review_id", "order_id", "stage", "duration_days", "assessment_status",
                                       "product_id", "single_seller_id", "product_count", "item_count", "product_attributable"]}
        out.update(old_long_stage_warning=row["long_stage_warning"], product_reference_n=0, product_mean_days=np.nan,
                   difference_from_product_mean_days=np.nan, product_mean_comparison_status="not_attributable_to_one_product",
                   selected_reference_id=pd.NA, selected_reference_scope="unavailable", selected_reference_n=pd.NA,
                   selected_reference_mean_days=np.nan, selected_reference_median_days=np.nan,
                   difference_from_reference_mean_days=np.nan, relative_difference_pct=np.nan,
                   reference_percentile_0_100=np.nan, delay_score_0_100=np.nan, severity="unassessed",
                   long_stage_warning=pd.NA, nominal_tail_score_step=np.nan, tail_resolution="unavailable",
                   reference_reason=row["assessment_status"])
        if row["product_attributable"]:
            profile = lookup.loc[(row["product_id"], row["stage"])]
            out.update(product_reference_n=int(profile.reference_n), product_mean_days=profile.mean_days,
                       difference_from_product_mean_days=row["duration_days"]-profile.mean_days,
                       product_mean_comparison_status=profile.mean_support)
        if row["assessable"]:
            selected, selected_context, selection_scope = None, {}, ""
            if row["product_attributable"]:
                for fields in LEVELS:
                    context = {field: row[field] for field in fields}
                    group = peers_for(row["stage"], context, True)
                    if len(group) >= protocol()["minimum_reference_orders"]:
                        selected, selected_context, selection_scope = group, context, "product_context"
                        break
            if selected is None:
                context = json.loads(row["reference_context_json"])
                if "distance_band" not in context:
                    raise ValueError("Fallback cannot discard geography")
                group = peers_for(row["stage"], context, False)
                if len(group) != row["reference_n"] or len(group) < 30:
                    raise ValueError("Category fallback no longer reconstructs the frozen reference")
                selected, selected_context, selection_scope = group, context, "category_context_fallback"
            values = selected.duration_days.to_numpy(float)
            mean, median = float(values.mean()), float(np.median(values))
            score, level, rank = tail_score(row["duration_days"], values)
            encoded = json.dumps(selected_context, sort_keys=True)
            gid = hashlib.sha256(json.dumps([row["stage"], selection_scope, selected_context], sort_keys=True).encode()).hexdigest()
            if gid not in registry:
                registry[gid] = {"reference_id": gid, "stage": row["stage"], "scope": selection_scope,
                                 "context_json": encoded, "n": len(values), "mean_days": mean, "median_days": median,
                                 "p95_days_descriptive": float(np.quantile(values, .95)),
                                 "latest_end": selected.end_timestamp.max(),
                                 "order_ids_json": json.dumps(selected.order_id.tolist()),
                                 "tail_step": 2000/len(values)}
            out.update(selected_reference_id=gid, selected_reference_scope=selection_scope,
                       selected_reference_n=len(values), selected_reference_mean_days=mean, selected_reference_median_days=median,
                       difference_from_reference_mean_days=row["duration_days"]-mean,
                       relative_difference_pct=100*(row["duration_days"]-mean)/mean if mean > 0 else np.nan,
                       reference_percentile_0_100=100*rank, delay_score_0_100=score, severity=level,
                       long_stage_warning=score > 0, nominal_tail_score_step=2000/len(values),
                       tail_resolution="limited_n_below_100" if len(values) < 100 else "empirical_discrete_not_probability",
                       reference_reason="matched_product_history" if selection_scope == "product_context" else
                                        "insufficient_product_context" if row["product_attributable"] else "multi_product_or_seller_order_only")
        scored_rows.append(out)
    scored = pd.DataFrame(scored_rows)
    for field in ["old_long_stage_warning", "long_stage_warning"]:
        scored[field] = scored[field].astype("boolean")
    scored["selected_reference_n"] = scored.selected_reference_n.astype("Int64")
    for field in ["selected_reference_id", "product_id", "single_seller_id", "review_id"]:
        scored[field] = scored[field].astype("string")
    order_rows = []
    for row in orders.to_dict("records"):
        stage = scored.loc[scored.order_id.eq(row["order_id"])]
        available = stage.delay_score_0_100.notna()
        warning = stage.long_stage_warning.fillna(False).any()
        complete = bool(available.all())
        maximum = float(stage.delay_score_0_100.max()) if available.any() else np.nan
        levels = stage.loc[available].sort_values("delay_score_0_100")
        level = levels.iloc[-1].severity if available.any() else "unassessed"
        order_rows.append({"review_id": row["review_id"], "order_id": row["order_id"], "product_count": row["product_count"],
                           "product_id": row["product_id"], "product_attributable": row["product_attributable"],
                           "performance_state": row["performance_state"], "days_late": row["days_late"], "days_early": row["days_early"],
                           "calendar_late_signal": row["calendar_late_signal"], "promise_basis_status": "unverified_source_method_not_provided",
                           "scored_stages": int(available.sum()), "score_coverage": "complete" if complete else "partial" if available.any() else "none",
                           "product_context_stages": int(stage.selected_reference_scope.eq("product_context").sum()),
                           "max_stage_delay_score": maximum, "severity": level,
                           "long_stage_warning": bool(warning) if warning or complete else pd.NA,
                           "attention_stages_json": json.dumps(stage.loc[stage.long_stage_warning.fillna(False), "stage"].tolist()),
                           "faster_than_selected_mean_stages_json": json.dumps(stage.loc[stage.difference_from_reference_mean_days.lt(0), "stage"].tolist()),
                           "cause_status": "not_established", "independent_reference": False})
    order_scores = pd.DataFrame(order_rows)
    for field in ["long_stage_warning", "calendar_late_signal"]:
        order_scores[field] = order_scores[field].astype("boolean")
    for field in ["days_early", "days_late"]:
        order_scores[field] = order_scores[field].astype("Int64")
    for field in ["product_id", "review_id"]:
        order_scores[field] = order_scores[field].astype("string")
    outputs = {"product_stage_statistics.csv": profiles, "order_product_links.csv": links,
               "train_stage_reference.csv": reference, "selected_reference_groups.csv": pd.DataFrame(registry.values()),
               "stage_scores.csv": scored, "order_scores.csv": order_scores, "pilot_scope.csv": inputs["pilot_scope"].copy()}
    summary = {"version": VERSION, "candidate_orders": len(orders), "pilot_orders_scored_or_retained": int(orders.review_id.notna().sum()),
               "held_pilot_ids": inputs["pilot_scope"].loc[~inputs["pilot_scope"].comparison_generated, "review_id"].tolist(),
               "train_reference_orders": int(reference.order_id.nunique()), "train_reference_stage_rows": len(reference),
               "reference_end_before": str(inputs["reference_end_before"]),
               "products_in_reference_or_candidates": int(profiles.product_id.nunique()),
               "products_with_history": int(profiles.loc[profiles.reference_n.gt(0), "product_id"].nunique()),
               "product_stage_profiles": len(profiles), "product_stage_profiles_n_ge_30": int(profiles.reference_n.ge(30).sum()),
               "reference_scope_counts": scored.selected_reference_scope.value_counts().to_dict(),
               "stage_severity_counts": scored.severity.value_counts().to_dict(),
               "order_severity_counts": order_scores.severity.value_counts().to_dict(),
               "early_with_long_warning": int((order_scores.performance_state.eq("early") & order_scores.long_stage_warning.fillna(False)).sum()),
               "calendar_late_orders": int(order_scores.calendar_late_signal.sum()),
               "changed_stage_flags_from_previous_policy": int((scored.long_stage_warning != scored.old_long_stage_warning).fillna(False).sum()),
               "held_out_test_scored": False, "new_labels_created": False, "accuracy_computed": False, "auc_computed": False,
               "optimal_threshold_claimed": False, "cause_verified": False, "models_retrained": False}
    return outputs, summary


def render_report(frames, summary):
    s = summary
    lines = ["# Trung bình theo sản phẩm và điểm cảnh báo công đoạn", "",
             "Dữ liệu Olist thật, ngành bed_bath_table. Thang điểm thử nghiệm, không phải xác suất hay bằng chứng nguyên nhân nghẽn.", "",
             f"- {s['candidate_orders']} đơn validation trước mốc bảo vệ, gồm {s['pilot_orders_scored_or_retained']} đơn pilot.",
             f"- Train: {s['train_reference_orders']} đơn, mốc công đoạn kết thúc trước {s['reference_end_before']}.",
             f"- {s['products_in_reference_or_candidates']} mã sản phẩm trong phạm vi; {s['products_with_history']} mã có lịch sử đơn một sản phẩm/một seller.",
             f"- Chỉ {s['product_stage_profiles_n_ge_30']} cặp sản phẩm/công đoạn có ít nhất 30 đơn tham chiếu.",
             "- BR0043 và BR0119 giữ riêng; không thay đổi nhãn, mô hình, ngày hẹn hoặc dữ liệu cũ.", "",
             "## Công thức", "",
             "Trung bình = tổng thời lượng / số đơn quan sát. Một đơn chỉ đóng góp một lần cho một công đoạn; không đếm lặp theo số item.",
             "Chênh ngày = thời lượng thực tế - trung bình. Chênh % = 100 * chênh ngày / trung bình (để trống nếu trung bình bằng 0).",
             "Lưu cả trung vị và độ lệch chuẩn để nhận biết phân bố lệch; không tự loại các thời lượng lớn hợp lệ.", "",
             "F(d) = số thời lượng tham chiếu <= d / n. Nếu d <= trung bình hoặc d <= 0 thì điểm bằng 0.",
             "Ngược lại, điểm = 100 * max(0, min(1, (F(d) - 0,95) / 0,05)). Thiếu bằng chứng thì để trống, không ghi 0.", "",
             "| Điểm | Mức cảnh báo |", "| --- | --- |", "| 0 | Chưa có cảnh báo dài theo quy ước này |",
             "| Trên 0 đến 50 | Nhẹ |", "| Trên 50 đến 80 | Vừa |", "| Trên 80 đến 100 | Cao |",
             "| Trống | Chưa đủ dữ liệu |", "",
             "Các khoảng này là lựa chọn triển khai để thử nghiệm, không phải ngưỡng NIST quy định hoặc ngưỡng được tối ưu bằng nhãn.",
             "Điểm biểu thị vị trí ở đuôi dài, không phải thiệt hại hay xác suất nghẽn. Điểm 100 bão hòa khi vượt lịch sử; xem thêm số ngày/% chênh.",
             "Với nhóm nhỏ, điểm nhảy bậc lớn (bước danh nghĩa 2000/n); 100 mức hiển thị không đồng nghĩa có 100 mức chính xác.", "",
             "## Tham chiếu thực sự đã dùng", "",
             "Ưu tiên cùng sản phẩm, seller, số item và bối cảnh khoảng cách/bang theo thứ tự trong protocol.json.",
             "Nếu thiếu 30 đơn cùng bối cảnh, dùng nhóm ngành hàng có khoảng cách hợp lệ từ pipeline cũ và ghi rõ fallback.",
             "Trung bình sản phẩm tổng thể chỉ là mô tả; điểm dùng trung bình và phân bố của nhóm được chọn, không trộn hai chuẩn.",
             "Đơn nhiều sản phẩm hoặc nhiều seller chỉ nhận điểm công đoạn của cả đơn, không quy nguyên nhân cho một mã.", ""]
    lines += [f"- `{key}`: {value} dòng công đoạn." for key, value in s["reference_scope_counts"].items()]
    lines += ["", "## Ví dụ sản phẩm có lịch sử nhiều", "",
              "| Mã sản phẩm | Công đoạn | Số đơn | Trung bình ngày | Trung vị ngày |", "| --- | --- | ---: | ---: | ---: |"]
    for row in frames["product_stage_statistics.csv"].sort_values(["reference_n", "product_id", "stage"], ascending=[False, True, True]).head(9).itertuples(index=False):
        lines.append(f"| {row.product_id} | {signals.NAMES[row.stage]} | {row.reference_n} | {row.mean_days:.4f} | {row.median_days:.4f} |")
    lines += ["", "## Ví dụ được so với đúng sản phẩm", "",
              "Các dòng điểm cao dưới đây chỉ là ví dụ diễn giải, không phải mẫu kiểm chứng hoặc chứng minh chất lượng.",
              "", "| Mã đơn | Sản phẩm | Công đoạn | Thực tế ngày | Trung bình nhóm ngày | n | Điểm | Mức |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: | --- |"]
    examples = frames["stage_scores.csv"].loc[lambda f: f.selected_reference_scope.eq("product_context")]
    for row in examples.sort_values(["delay_score_0_100", "order_id", "stage"], ascending=[False, True, True]).head(3).itertuples(index=False):
        lines.append(f"| {row.order_id} | {row.product_id} | {signals.NAMES[row.stage]} | {row.duration_days:.4f} | "
                     f"{row.selected_reference_mean_days:.4f} | {row.selected_reference_n} | {row.delay_score_0_100:.2f} | {LEVEL_NAMES[row.severity]} |")
    lines += ["", "## BR0079", ""]
    for row in frames["stage_scores.csv"].loc[lambda f: f.review_id.eq("BR0079")].itertuples(index=False):
        if pd.isna(row.delay_score_0_100):
            lines.append(f"- {signals.NAMES[row.stage]}: chưa đủ dữ liệu.")
        else:
            lines.append(f"- {signals.NAMES[row.stage]}: {row.duration_days:.4f} ngày; lịch sử riêng sản phẩm n={row.product_reference_n}; "
                         f"tham chiếu dùng `{row.selected_reference_scope}`, n={row.selected_reference_n}, trung bình {row.selected_reference_mean_days:.4f} ngày; "
                         f"chênh {row.difference_from_reference_mean_days:.4f} ngày; điểm {row.delay_score_0_100:.2f}, mức {LEVEL_NAMES[row.severity]}.")
    lines += ["", "## Kết quả và giới hạn", "",
              f"- {s['calendar_late_orders']} đơn trễ ngày hẹn; giữ riêng với điểm công đoạn.",
              f"- {s['early_with_long_warning']} đơn giao sớm vẫn có cảnh báo công đoạn.",
              f"- {s['changed_stage_flags_from_previous_policy']} dòng công đoạn đổi cờ so với quy ước cũ; đây là thay đổi phương pháp, không phải cải thiện độ chính xác đã chứng minh.",
              "Điểm đơn là điểm công đoạn cao nhất, kèm danh sách công đoạn và độ bao phủ; không lấy trung bình để che một công đoạn kéo dài.",
              "Mọi nguyên nhân và cơ sở đặt hẹn chưa được xác minh. Tham chiếu là các thời lượng đã quan sát, không phải tập được chứng nhận toàn bình thường.",
              "Chỉ dùng đơn hoàn tất đủ điều kiện của phiên bản nguồn; chưa là giám sát đơn đang giao hay hệ thống near real-time.",
              "Không suy tỷ lệ pilot thành tỷ lệ toàn Olist. Chưa tính precision/recall/F1/AUC hoặc chọn mô hình tối ưu.", "",
              "Chi tiết công thức, lý do và tài liệu tham khảo: docs/product_stage_scores.md trong project.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, source, output=DEFAULT_OUTPUT):
    output = pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite product scores")
    if set(frames) != TABLES:
        raise ValueError("Incomplete product score output")
    verify_sources(source)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".product-scores-", dir=output.parent) as temporary:
        stage = Path(temporary)/"snapshot"
        stage.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(stage/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(stage, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(stage/"summary.json", summary)
        review.write_json(stage/"protocol.json", protocol())
        (stage/"bao_cao_diem_cong_doan.md").write_text(render_report(frames, summary))
        review.write_json(stage/"manifest.json", {"version": VERSION, "status": "complete",
                          "created_at_utc": datetime.now(timezone.utc).isoformat(), "schemas": schemas, "provenance": source,
                          "summary": summary, "output_hashes": {str(p.relative_to(stage)): review.digest(p)
                                                              for p in sorted(stage.rglob("*")) if p.is_file()}})
        load_snapshot(stage)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        stage.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "protocol.json", "bao_cao_diem_cong_doan.md"}):
        raise ValueError("Invalid product score snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"summary.json") != manifest["summary"] or review.read_json(directory/"protocol.json") != protocol():
        raise ValueError("Product summary/protocol differs")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = load_snapshot(args.output)["summary"]
    else:
        inputs, source = load_inputs()
        frames, summary = build_tables(inputs)
        result = write_snapshot(frames, summary, source, args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
