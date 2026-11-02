"""Retrospective holdout diagnostic for the already selected, fixed stage rule."""

import argparse
from datetime import datetime, timezone
import hashlib
from itertools import combinations
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import sklearn

import select_temporal_delivery_candidate as temporal
import apply_frozen_stage_reference as frozen
import experiment_contextual_delivery as contextual

product, shared, review = temporal.previous.product, temporal.shared, temporal.review
ROOT, business = temporal.ROOT, shared.business
performance, supplement = product.signals.performance, product.signals.supplement
VERSION = "selected_holdout_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_selected_holdout_v1"
GUIDE = ROOT/"docs/selected_holdout_evaluation.md"
COHORT = ROOT/"data/experiments/bed_bath_table_v1"
CANDIDATE, SCORE, TARGET = temporal.previous.PRODUCT_CANDIDATE, temporal.previous.PRODUCT_SCORE, temporal.TARGET
TABLES = {"order_ledger.csv", "stage_scores.csv", "reference_groups.csv", "period_metrics.csv",
          "monthly_coverage.csv", "validation_test_metrics.csv", "zip_quality.csv", "pair_quality.csv",
          "order_quality.csv", "item_source_evidence.csv", "coordinate_source_evidence.csv"}


def protocol():
    return {"version": VERSION, "candidate": CANDIDATE, "score_column": SCORE, "threshold": 0., "operator": ">",
            "population": "original_test_purchases_2018_06_01_inclusive_to_2018_09_01_exclusive",
            "target": "observed_delivery_calendar_date_after_estimated_delivery_calendar_date",
            "reference": "unchanged_saved_train_stage_reference; no_refit_or_test_reference_updates",
            "coverage": "all_test_orders_retained; metrics_on_known_valid_outcome_and_three_available_scores",
            "followup": "source_extraction_cutoff_unknown; completed_observation_diagnostic_only",
            "single_class": "undefined_auc_ap_recall_remain_missing; no_imputed_negative_outcomes",
            "geography": "saved_quality_limits; deterministic_static_source_coordinates_not_online_verified",
            "test_exposure": "exposed_after_this_run; future_tuning_requires_new_evaluation_data",
            "selection_unchanged": True, "production_activated": False}


def code_hashes():
    modules = [frozen, frozen.quality, product, shared, business, temporal, performance, supplement, contextual]
    return {str(p.relative_to(ROOT)): review.digest(p) for p in [Path(__file__), GUIDE, *[Path(m.__file__) for m in modules]]}


def verify_sources(source):
    for item in source["snapshots"].values():
        path = Path(item["directory"])
        if review.digest(path/"manifest.json") != item["manifest_sha256"]:
            raise ValueError("Holdout source manifest changed")
        shared.verify_files(path, item["output_hashes"])
    if code_hashes() != source["code_hashes"]:
        raise ValueError("Holdout implementation or contract changed; use a new version")


def require_selection(selection):
    expected = {"selected_candidate": CANDIDATE, "score_column": SCORE, "threshold": 0., "operator": ">",
                "status": "provisional_validation_choice", "selection_target": protocol()["target"]}
    if any(selection.get(k) != v for k, v in expected.items()):
        raise ValueError("Frozen candidate selection changed; no reselection on holdout")


def load_frozen():
    selected = temporal.load_snapshot()
    require_selection(selected["selection"])
    pm = product.load_snapshot()
    _, qm = contextual.load_snapshot(contextual.DEFAULT_OUTPUT)
    contextual.verify_sources(qm["provenance"])
    cm = review.read_json(COHORT/"manifest.json")
    clean = Path(cm["source_directories"]["clean"])
    clean_m = review.read_json(clean/"manifest.json")
    if (cm.get("experiment_version") != "bed_bath_table_v1" or cm.get("status") != "complete"
            or cm["design"]["source_extraction_cutoff"] is not None
            or cm["parents"]["clean_manifest_sha256"] != review.digest(clean/"manifest.json")):
        raise ValueError("Original partition/clean binding differs")
    gm = review.read_json(shared.DEFAULT_OUTPUT/"manifest.json")
    snapshots = [("selection", temporal.DEFAULT_OUTPUT, selected), ("product", product.DEFAULT_OUTPUT, pm),
                 ("context", contextual.DEFAULT_OUTPUT, qm), ("cohort", COHORT, cm),
                 ("clean", clean, clean_m), ("geography", shared.DEFAULT_OUTPUT, gm)]
    source = {"snapshots": {name: {"directory": str(path), "manifest_sha256": review.digest(path/"manifest.json"),
                                  "output_hashes": m["output_hashes"]} for name, path, m in snapshots},
              "code_hashes": code_hashes()}
    verify_sources(source)
    reference = shared.read_table(product.DEFAULT_OUTPUT, "train_stage_reference.csv", pm)
    history_end = pd.Timestamp(pm["summary"]["reference_end_before"])
    if not reference.end_timestamp.lt(history_end).all():
        raise ValueError("Reference contains events beyond its frozen boundary")
    return {"selection": selected["selection"], "product_manifest": pm, "context_manifest": qm,
            "cohort_manifest": cm, "clean_manifest": clean_m, "clean": clean,
            "geography_manifest": gm, "reference": reference,
            "thresholds": shared.read_table(contextual.DEFAULT_OUTPUT, "quality_thresholds.csv", qm)}, source


def parity_check(inputs):
    """Verify the adapter on saved development results before opening test outcomes."""
    raw, _ = product.load_inputs()
    meta, _ = product.order_metadata(raw["items"])
    rows = raw["stages"].merge(meta, on="order_id", validate="many_to_one")
    actual, _ = frozen.score_stages(rows, inputs["reference"])
    saved = shared.read_table(product.DEFAULT_OUTPUT, "stage_scores.csv", inputs["product_manifest"])
    keys = ["order_id", "stage"]
    columns = ["selected_reference_id", "selected_reference_scope", "selected_reference_n",
               "selected_reference_mean_days", "difference_from_reference_mean_days", "delay_score_0_100", "severity"]
    pd.testing.assert_frame_equal(actual.set_index(keys)[columns].sort_index(), saved.set_index(keys)[columns].sort_index(),
                                  check_dtype=False, check_exact=True)
    gm, qm = inputs["geography_manifest"], inputs["context_manifest"]
    zips = shared.read_table(shared.DEFAULT_OUTPUT, "zip_representatives.csv", gm)
    evidence = shared.read_table(shared.DEFAULT_OUTPUT, "coordinate_source_evidence.csv", gm)
    pairs = shared.read_table(shared.DEFAULT_OUTPUT, "seller_customer_distances.csv", gm)
    old_pairs = shared.read_table(contextual.DEFAULT_OUTPUT, "pair_quality.csv", qm)
    states = old_pairs[["order_id", "seller_id", "customer_state", "seller_state"]]
    _, actual_pairs, actual_orders = frozen.geography(zips, evidence, pairs, states, inputs["thresholds"])
    old_orders = shared.read_table(contextual.DEFAULT_OUTPUT, "order_quality.csv", qm)
    pd.testing.assert_frame_equal(actual_orders, old_orders, check_dtype=False, check_exact=True)
    columns = ["order_id", "seller_id", "pair_quality_eligible", "pair_quality_reasons"]
    pd.testing.assert_frame_equal(actual_pairs[columns], old_pairs[columns], check_dtype=False, check_exact=True)
    return {"validation_orders": int(actual.order_id.nunique()), "validation_stage_scores": len(actual),
            "development_geographic_orders": len(actual_orders), "exact_parity": True}


def selected_cohort(inputs):
    schema = inputs["cohort_manifest"]["schemas"]["cohort_cases.csv"]
    frames = []
    for chunk in pd.read_csv(COHORT/"cohort_cases.csv", dtype="string", chunksize=5000, keep_default_na=False):
        frames.append(chunk.loc[chunk.split.eq("test")])
    cases = pd.concat(frames, ignore_index=True)
    for field, dtype in schema.items():
        if dtype.startswith("datetime64"):
            cases[field] = pd.to_datetime(cases[field], format="%Y-%m-%d %H:%M:%S", errors="raise")
        elif dtype in ["bool", "boolean"]:
            allowed = ["True", "False", ""] if dtype == "boolean" else ["True", "False"]
            if not cases[field].isin(allowed).all():
                raise ValueError("Invalid recorded cohort boolean")
            cases[field] = cases[field].map({"True": True, "False": False, "": pd.NA}).astype(dtype)
        elif dtype not in ["object", "string", "str"]:
            cases[field] = cases[field].replace("", pd.NA).astype(dtype)
    business.require_key(cases, ["order_id"], "original test")
    purchase = cases.order_purchase_timestamp
    if (not purchase.ge("2018-06-01").all() or not purchase.lt("2018-09-01").all()
            or not cases.event_time_cutoff_exclusive.isna().all()
            or cases.previously_human_reviewed.any()):
        raise ValueError("Original test boundaries or exposure policy differ")
    development = pd.read_csv(COHORT/"cohort_cases.csv", usecols=["order_id", "split"], dtype="string")
    if (set(cases.order_id) & set(inputs["reference"].order_id)
            or set(cases.order_id) & set(development.loc[development.split.ne("test"), "order_id"])):
        raise ValueError("Test overlaps development history")
    return cases.sort_values("order_id").reset_index(drop=True)


def load_test(inputs):
    cohort = selected_cohort(inputs)
    ids = set(cohort.order_id)
    clean, cm = inputs["clean"], inputs["clean_manifest"]
    read = lambda name, key, allowed: business.read_selected(clean, name, cm, key, allowed)
    orders, items = read("orders", "order_id", ids), read("order_items", "order_id", ids)
    business.require_key(orders, ["order_id"], "clean test orders")
    if set(orders.order_id) != ids or set(items.order_id) != ids:
        raise ValueError("Missing source orders/items")
    a, b = orders.set_index("order_id").sort_index(), cohort.set_index("order_id").sort_index()
    for field in [*performance.TIMES, performance.ESTIMATE, "order_status", "customer_id"]:
        pd.testing.assert_series_equal(a[field], b[field], check_dtype=False, check_names=False, check_exact=True)
    customers = read("customers", "customer_id", set(orders.customer_id))
    sellers = read("sellers", "seller_id", set(items.seller_id))
    links = items[["order_id", "seller_id"]].drop_duplicates().merge(
        orders[["order_id", "customer_id"]], on="order_id", validate="many_to_one").merge(
        customers[["customer_id", "customer_zip_code_prefix", "customer_state"]], on="customer_id", how="left", validate="many_to_one").merge(
        sellers[["seller_id", "seller_zip_code_prefix", "seller_state"]], on="seller_id", how="left", validate="many_to_one")
    requested = set(links.customer_zip_code_prefix.dropna()) | set(links.seller_zip_code_prefix.dropna())
    coordinates = read("geolocation", shared.ZIP, requested)
    zips, evidence = shared.representatives(coordinates, requested)
    pairs = shared.seller_distances({"links": links, "items": items}, zips)
    zq, pq, oq = frozen.geography(zips, evidence, pairs, links[["order_id", "seller_id", "customer_state", "seller_state"]], inputs["thresholds"])
    # The static coordinate transform must also reproduce existing ZIP representations exactly.
    old = shared.read_table(shared.DEFAULT_OUTPUT, "zip_representatives.csv", inputs["geography_manifest"]).set_index(shared.ZIP)
    common = sorted(set(zips[shared.ZIP]) & set(old.index))
    fields = ["latitude", "longitude", "representative_source_record", *frozen.quality.SPREADS]
    pd.testing.assert_frame_equal(zips.set_index(shared.ZIP).loc[common, fields], old.loc[common, fields], check_dtype=False, check_exact=True)
    meta, _ = product.order_metadata(items)
    orders = orders.merge(cohort[["order_id", "split", "status_delivery_conflict"]], on="order_id", validate="one_to_one").merge(
        customers[["customer_id", "customer_state"]], on="customer_id", how="left", validate="many_to_one").merge(
        oq, on="order_id", how="left", validate="one_to_one").merge(meta, on="order_id", validate="one_to_one")
    if orders.all_pairs_quality_eligible.isna().any():
        raise ValueError("Missing geography coverage rows")
    orders["distance_band"] = pd.cut(orders.screened_distance_max_km,
        [*contextual.protocol()["distance_band_edges"], np.inf], labels=contextual.BANDS, right=False).astype("string").fillna("unavailable")
    orders["seller_attribution_scope"] = np.where(orders.mapped_seller_count.eq(1), "single_seller_order", "multi_seller_order")
    return orders.sort_values("order_id").reset_index(drop=True), {
        "zip_quality.csv": zq, "pair_quality.csv": pq, "order_quality.csv": oq,
        "item_source_evidence.csv": items, "coordinate_source_evidence.csv": evidence}


def observations(source):
    orders = source.sort_values("order_id").reset_index(drop=True).copy()
    business.require_key(orders, ["order_id"], "holdout observations")
    dates = orders.order_purchase_timestamp
    if not orders.split.eq("test").all() or not dates.ge("2018-06-01").all() or not dates.lt("2018-09-01").all():
        raise ValueError("Only original test dates are allowed")
    orders["purchase_month"] = dates.dt.strftime("%Y-%m")
    orders["visible_before_exclusive"] = pd.NaT
    orders["source_extraction_cutoff_status"] = "unknown_not_inferred"
    orders["promise_basis_status"] = "unverified_source_method_not_provided"
    orders["chronology_inversions"] = sum(orders[b].lt(orders[a]).astype(int) for a, b in combinations(performance.TIMES, 2))
    orders["promise_before_purchase_date"] = orders[performance.ESTIMATE].dt.normalize().lt(dates.dt.normalize())
    orders["quality_conflict"] = orders.chronology_inversions.gt(0) | orders.status_delivery_conflict | orders.promise_before_purchase_date
    delta = (orders[performance.TIMES[3]].dt.normalize()-orders[performance.ESTIMATE].dt.normalize()).dt.days.astype("Int64")
    orders["promise_delta_calendar_days"] = delta
    orders["performance_eligible"] = delta.notna() & ~orders.quality_conflict
    orders["performance_state"] = "unknown"
    for mask, state in [(delta.lt(0), "early"), (delta.eq(0), "on_date"), (delta.gt(0), "late")]:
        orders.loc[mask.fillna(False), "performance_state"] = state
    orders.loc[orders.quality_conflict, "performance_state"] = "excluded_source_conflict"
    orders[TARGET] = delta.gt(0).astype("boolean").where(orders.performance_eligible)
    orders["missing_stage_endpoint"] = orders[performance.TIMES].isna().any(axis=1)
    durations = performance.stage_durations(orders)
    durations = durations.loc[durations.stage.isin(product.signals.STAGES)].reset_index(drop=True)
    complete = durations.groupby("order_id").duration_eligible.all()
    orders["complete_timing_candidate"] = orders.performance_eligible & orders.order_id.map(complete).fillna(False)
    return orders, durations


def evaluate(orders, reference):
    orders, durations = observations(orders)
    candidates = orders.loc[orders.complete_timing_candidate]
    keys = ["order_id", "stage"]
    fields = [*keys, "reference_n", "reference_context_json", "reference_distance_matched"]
    comparisons = (supplement.compare_stages(candidates, durations, reference) if len(candidates)
                   else pd.DataFrame(columns=fields))
    rows = durations.merge(comparisons[fields], on=keys, how="left", validate="one_to_one")
    rows["assessable"] = rows.reference_distance_matched.fillna(False).astype(bool) & rows.reference_n.ge(30)
    meta_fields = ["order_id", "product_id", "single_seller_id", "product_count", "item_count", "product_attributable"]
    rows = rows.merge(orders[meta_fields], on="order_id", validate="many_to_one")
    stages, groups = frozen.score_stages(rows, reference)
    grouped = stages.groupby("order_id", sort=True)
    orders["scored_stages"] = orders.order_id.map(grouped.delay_score_0_100.count())
    orders[SCORE] = orders.order_id.map(grouped.delay_score_0_100.max())
    orders["metric_eligible"] = orders.scored_stages.eq(3) & orders.performance_eligible
    orders[CANDIDATE+"_flag"] = orders[SCORE].gt(0).astype("boolean").where(orders.scored_stages.eq(3) | orders[SCORE].gt(0))
    orders["exclusion_reason"] = np.select(
        [orders.quality_conflict, ~orders.performance_eligible, orders.missing_stage_endpoint,
         ~orders.all_pairs_quality_eligible, ~orders.scored_stages.eq(3)],
        ["source_conflict", "unknown_calendar_outcome", "missing_stage_endpoint", "geographic_quality_not_eligible",
         "insufficient_matched_reference"], default="included")
    orders["calendar_proxy_confusion"] = "not_evaluated"
    for actual, predicted, label in [(True, True, "TP"), (False, True, "FP"), (True, False, "FN"), (False, False, "TN")]:
        mask = orders.metric_eligible & orders[TARGET].eq(actual) & orders[CANDIDATE+"_flag"].eq(predicted)
        orders.loc[mask.fillna(False), "calendar_proxy_confusion"] = label
    faster = stages.loc[stages.difference_from_reference_mean_days.lt(0)].groupby("order_id").stage.agg(list)
    warnings = stages.loc[stages.delay_score_0_100.gt(0)].groupby("order_id").stage.agg(list)
    orders["faster_stages_json"] = orders.order_id.map(lambda key: json.dumps(faster.get(key, [])))
    orders["warning_stages_json"] = orders.order_id.map(lambda key: json.dumps(warnings.get(key, [])))
    orders["faster_stage_evidence_status"] = np.select(
        [orders.order_id.isin(faster.index), orders.scored_stages.eq(3)],
        ["observed_below_reference_mean", "no_observed_stage_below_reference_mean"], default="insufficient_reference_coverage")
    eligible = orders.loc[orders.metric_eligible]
    periods, coverage = [], []
    for month, full in [("all", orders), *list(orders.groupby("purchase_month", sort=True))]:
        subset = full.loc[full.metric_eligible]
        periods.append({"period": month, **temporal.metrics(subset, CANDIDATE)})
        coverage.append({"period": month, "total_orders": len(full), "evaluated_orders": len(subset),
                         "coverage_fraction": len(subset)/len(full), "excluded_orders": int((~full.metric_eligible).sum()),
                         "quality_conflicts": int(full.quality_conflict.sum()),
                         "missing_calendar_endpoints": int(full.promise_delta_calendar_days.isna().sum()),
                         "missing_stage_endpoints": int(full.missing_stage_endpoint.sum()),
                         "geographic_quality_withheld": int((~full.all_pairs_quality_eligible).sum()),
                         "insufficient_reference_among_complete_geo": int((full.complete_timing_candidate & full.all_pairs_quality_eligible & ~full.metric_eligible).sum()),
                         "known_late_total": int(full[TARGET].sum()), "known_late_excluded": int(full.loc[~full.metric_eligible, TARGET].sum()),
                         "unknown_or_conflicted_outcome": int(full[TARGET].isna().sum()), "followup_verified": False})
    summary = {"version": VERSION, "status": "retrospective_completed_observation_diagnostic",
               "candidate": CANDIDATE, "threshold": 0., "operator": ">", "total_test_orders": len(orders),
               "evaluated_orders": len(eligible), "excluded_orders": len(orders)-len(eligible),
               "exclusion_reasons": orders.exclusion_reason.value_counts().sort_index().to_dict(),
               "reference_scope_stage_counts": stages.selected_reference_scope.value_counts().sort_index().to_dict(),
               "early_orders_with_stage_warning": int((orders.performance_state.eq("early") & orders[SCORE].gt(0)).sum()),
               "early_orders_without_observed_faster_stage": int((orders.performance_state.eq("early") & orders.faster_stage_evidence_status.eq("no_observed_stage_below_reference_mean")).sum()),
               "source_extraction_cutoff": None, "test_orders_sha256": hashlib.sha256("\n".join(sorted(orders.order_id)).encode()).hexdigest(),
               "pooled_metrics": {key: None if pd.isna(value) else value for key, value in periods[0].items()},
               "test_scored": True, "test_exposed_for_future_work": True,
               **{key: False for key in ["models_retrained", "thresholds_tuned", "synthetic_data_used", "new_reference_labels",
                    "selection_changed", "production_activated", "full_cohort_followup_verified", "near_real_time_validated",
                    "confirmed_bottleneck_accuracy", "causes_verified", "untouched_external_benchmark"]}}
    return {"order_ledger.csv": orders, "stage_scores.csv": stages, "reference_groups.csv": groups,
            "period_metrics.csv": pd.DataFrame(periods), "monthly_coverage.csv": pd.DataFrame(coverage)}, summary


def report(frames, summary):
    comparison = frames["validation_test_metrics.csv"].set_index("scope")
    old, new = comparison.loc["validation_all"], comparison.loc["test_all"]
    lines = ["# Kiểm tra ứng viên cố định trên tập test gốc", "",
             "**Đánh giá hồi cứu giao trễ ngày hẹn, không phải độ chính xác phát hiện nghẽn đã xác nhận.**", "",
             f"Giữ `{CANDIDATE}`, ngưỡng `{SCORE} > 0`; không train lại hoặc chọn ngưỡng theo test.",
             f"Tập test có {summary['total_test_orders']} đơn mua tháng 6-8/2018: {summary['evaluated_orders']} đơn đủ điều kiện, {summary['excluded_orders']} đơn tách riêng.",
             "Chưa có ngày chốt nguồn được xác minh. Không tự suy ra thời gian theo dõi từ ngày tải, ngày hẹn hay mốc lớn nhất trong CSV.", "",
             "## Kết quả đối chiếu", "", "| Phạm vi | Đơn | Trễ | TP | FP | FN | TN | Precision | Recall | F1 | ROC AUC | AP |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in frames["validation_test_metrics.csv"].itertuples(index=False):
        values = " | ".join(temporal.display(getattr(row, k)) for k in ["precision", "recall", "f1_raw", "roc_auc", "average_precision"])
        lines.append(f"| {row.scope} | {row.orders} | {row.late} | {row.tp} | {row.fp} | {row.fn} | {row.tn} | {values} |")
    lines += ["", "AP là average precision, không phải diện tích PR tính hình thang. AUC đo xếp hạng điểm, không phải phần trăm dự đoán đúng.",
              "FP ở đây chỉ là cảnh báo trên đơn không trễ ngày hẹn; công đoạn của đơn đó vẫn có thể chậm so với tham chiếu.",
              f"F1 validation/test: {temporal.display(old.f1_raw)} / {temporal.display(new.f1_raw)}; AUC: {temporal.display(old.roc_auc)} / {temporal.display(new.roc_auc)}.",
              f"Tỷ lệ trễ quan sát được thay đổi từ {old.positive_prevalence:.2%} xuống {new.positive_prevalence:.2%}. Thay đổi tỷ lệ lớp ảnh hưởng precision; không tự giải thích toàn bộ thay đổi AUC hay chứng minh nguyên nhân.",
              "Validation và test có cửa sổ quan sát khác nhau. Không dùng chênh lệch này để chỉnh mô hình hay khẳng định ưu thế thống kê.", "",
              "## Bao phủ", "", "| Kỳ | Tổng | Được đánh giá | Loại khỏi metric | Mâu thuẫn nguồn | Thiếu kết quả | Địa lý không đạt |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in frames["monthly_coverage.csv"].itertuples(index=False):
        lines.append(f"| {row.period} | {row.total_orders} | {row.evaluated_orders} | {row.excluded_orders} | {row.quality_conflicts} | {row.missing_calendar_endpoints} | {row.geographic_quality_withheld} |")
    lines += ["", "Các cờ lý do có thể trùng nhau; không cộng các cột này thành số bị loại. order_ledger.csv giữ lý do chính cho từng đơn.",
              "Đơn thiếu kết quả hoặc mâu thuẫn không bị gán bình thường. Việc chỉ đánh giá đơn đủ dữ liệu có thể gây thiên lệch chọn mẫu.", "",
              "## Bằng chứng công đoạn", "",
              f"- Giữ cảnh báo công đoạn cho {summary['early_orders_with_stage_warning']} đơn giao sớm.",
              f"- Có {summary['early_orders_without_observed_faster_stage']} đơn giao sớm, đủ tham chiếu nhưng không công đoạn nào nhanh hơn trung bình tham chiếu. Không ép gán một công đoạn nhanh.",
              "- stage_scores.csv ghi thời lượng, trung bình tham chiếu, chênh lệch ngày có dấu, điểm và cảnh báo tách biệt.",
              "- reference_groups.csv ghi chính xác các đơn lịch sử được dùng, không thêm đơn test/validation vào tham chiếu.",
              "- Điểm 0-100 là điểm đuôi phân phối, không phải xác suất nghẽn. Ngày hẹn vẫn chưa xác minh được cơ sở.", "",
              "## Kết luận và bước tiếp", "",
              "Đã kiểm tra hồi cứu ứng viên đã chọn trên phân vùng test gốc. Chưa kích hoạt production, chưa chốt mô hình tối ưu cho nghẽn thực sự.",
              "Kết quả test hiện tại giảm so với validation; không có cơ sở gọi ứng viên ổn định chỉ vì nó dẫn đầu ở bước chọn trước đó.",
              f"Bước phân tích tiếp: đối chiếu {int(new.fn)} đơn trễ không cảnh báo và {int(new.fp)} đơn không trễ nhưng có cảnh báo công đoạn. Lọc cột calendar_proxy_confusion trong order_ledger.csv và nối stage_scores.csv; chưa dùng các kết quả đó để sửa ngưỡng.",
              "Cần metadata ngày chốt nguồn để đánh giá độ đầy đủ theo dõi; cần tiêu chí/bằng chứng kiểm chứng độc lập cho nghẽn nếu muốn báo cáo độ chính xác mục tiêu đó.",
              "Tập test đã được xem từ lần chạy này. Không sửa tham chiếu/ngưỡng dựa vào kết quả rồi báo cáo lại trên cùng tập như test độc lập.",
              "Ngành hàng đã được chọn sau khám phá lịch sử dữ liệu; không tuyên bố đây là benchmark bên ngoài chưa từng được xem.",
              "Cần tách khả năng nhận ra công đoạn khác thường khỏi khả năng phân biệt đơn trễ ngày hẹn. Không coi hai mục tiêu là một nhãn chuẩn duy nhất.",
              "Sau khi làm rõ giới hạn đánh giá, có thể hoàn thiện dashboard và phát lại sự kiện bằng cấu hình cố định; chưa dùng kết quả này để chứng minh near real-time.", ""]
    return "\n".join(lines)


def run(output=DEFAULT_OUTPUT):
    output = product.pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite holdout snapshot")
    print("Verifying frozen selection, history and source hashes...", flush=True)
    inputs, source = load_frozen()
    print("Checking adapter parity on saved development results...", flush=True)
    parity = parity_check(inputs)
    print("Parity passed. Applying fixed rule to original test orders...", flush=True)
    orders, evidence = load_test(inputs)
    frames, summary = evaluate(orders, inputs["reference"])
    summary["pre_test_parity"] = parity
    old = pd.read_csv(temporal.DEFAULT_OUTPUT/"period_metrics.csv", float_precision="round_trip")
    old = old.loc[old.candidate.eq(CANDIDATE) & old.period_kind.eq("pooled")].assign(scope="validation_all")
    frames["validation_test_metrics.csv"] = pd.concat([old, frames["period_metrics.csv"].assign(
        scope=lambda f: "test_"+f.period)], ignore_index=True)
    frames.update(evidence)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".selected-holdout-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        for name, value in [("summary.json", summary), ("protocol.json", protocol()), ("selection.json", inputs["selection"])]:
            review.write_json(staging/name, value)
        (staging/"bao_cao_test.md").write_text(report(frames, summary), encoding="utf-8")
        verify_sources(source)
        review.write_json(staging/"manifest.json", {
            "version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": summary, "provenance": source,
            "libraries": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
            "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())},
            "schemas": {name: {column: str(dtype) for column, dtype in frame.dtypes.items()} for name, frame in frames.items()}})
        if output.exists():
            raise FileExistsError("Destination appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("status") != "complete" or manifest.get("version") != VERSION
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "protocol.json", "selection.json", "bao_cao_test.md"}):
        raise ValueError("Invalid holdout snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    require_selection(review.read_json(directory/"selection.json"))
    if (review.read_json(directory/"summary.json") != manifest["summary"]
            or review.read_json(directory/"protocol.json") != protocol()):
        raise ValueError("Holdout metadata differs")
    temporal.load_snapshot()
    product.load_snapshot()
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = load_snapshot(args.output)["summary"] if args.verify else run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
