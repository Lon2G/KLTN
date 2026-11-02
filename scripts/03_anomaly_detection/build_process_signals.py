"""Expose observed process signals and evidence, independently of root causes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import resolve_evidence_criteria as criteria

ROOT, review, shared = criteria.ROOT, criteria.review, criteria.shared
performance, supplement = criteria.performance, criteria.supplement
VERSION = "process_signals_v1"
TEMPLATE = ROOT/"templates/process_signals_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_process_signals_v1"
TABLES = {"order_signals.csv", "stage_signals.csv", "signal_evidence.csv",
          "reference_groups.csv", "evaluation_contract.csv"}
STAGES = supplement.STAGES
NAMES = supplement.STAGE_NAMES
STATE_NAMES = {**supplement.STATE_NAMES, "unknown": "Chưa đủ dữ liệu",
               "excluded_source_conflict": "Tách riêng do mâu thuẫn nguồn"}
LIMITS = {
    "assessable": "Tham chiếu mô tả; chưa kiểm soát đầy đủ bối cảnh vận hành.",
    "source_conflict": "Mốc hoặc trạng thái nguồn mâu thuẫn; chưa chấm cảnh báo thời gian.",
    "endpoint_not_visible": "Thiếu mốc quan sát; không thay bằng 0 hoặc kết luận bình thường.",
    "parent_complete_case_policy": "Có thời lượng nhưng đơn chưa thuộc phạm vi so sánh đầy đủ của phiên bản nguồn.",
    "context_reference_unavailable": "Chưa đủ tham chiếu khớp khoảng cách đạt điều kiện; không coi là không cảnh báo.",
}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}


def verify_sources(provenance):
    source = Path(provenance["criteria_directory"])
    if review.digest(source/"manifest.json") != provenance["criteria_manifest_sha256"]:
        raise ValueError("Frozen criteria source changed")
    manifest = criteria.load_snapshot(source)
    if manifest["provenance"]["source"] != provenance["performance_source"] or code_hashes() != provenance["code_hashes"]:
        raise ValueError("Signal source/code binding changed; use a new version")
    return manifest


def load_inputs(source=criteria.DEFAULT_OUTPUT):
    source = Path(source).resolve()
    manifest = criteria.load_snapshot(source)
    provenance = {"criteria_directory": str(source), "criteria_manifest_sha256": review.digest(source/"manifest.json"),
                  "performance_source": manifest["provenance"]["source"], "code_hashes": code_hashes()}
    frames = {name: shared.read_table(source, name, manifest) for name in ["case_decisions.csv", "stage_evidence.csv"]}
    parent = Path(provenance["performance_source"]["performance_directory"])
    parent_manifest = review.read_json(parent/"manifest.json")
    reference_inputs = {name: shared.read_table(parent, name, parent_manifest)
                        for name in ["order_performance.csv", "stage_durations.csv"]}
    reference, cutoff = supplement.stage_references(reference_inputs)
    if str(cutoff) != manifest["summary"]["train_reference_end_before"]:
        raise ValueError("Reference cutoff differs from frozen criteria")
    frames["train_reference"] = reference
    verify_sources(provenance)
    return frames, provenance


def exact_boundaries(values):
    values = np.sort(np.asarray(values, dtype=float))
    if not len(values) or not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Expected observed nonnegative train durations")
    ranks = np.arange(1, len(values)+1)/len(values)
    config = protocol()
    upper = values[np.flatnonzero(ranks > config["working_upper_percentile"])[0]]
    lower = values[np.flatnonzero(ranks >= config["working_lower_percentile"])[0]]
    return float(lower), float(upper)


def reference_groups(evidence, reference):
    keys = ["stage", "reference_context_json"]
    groups, mapping = [], {}
    for (stage, encoded), rows in evidence.groupby(keys, sort=True):
        context = json.loads(encoded)
        if not isinstance(context, dict) or not set(context).issubset({"customer_state", "distance_band", "seller_attribution_scope"}):
            raise ValueError("Unexpected context fields")
        mask = reference.stage.eq(stage)
        for field, value in context.items():
            mask &= reference[field].eq(value)
        if "distance_band" in context:
            mask &= reference.all_pairs_quality_eligible
        peers = reference.loc[mask].sort_values("order_id")
        if not len(peers) or not rows.reference_n.eq(len(peers)).all() or not peers.order_id.is_unique:
            raise ValueError("Cannot reconstruct the exact frozen reference group")
        values = peers.duration_days.to_numpy(dtype=float)
        ranks = np.searchsorted(np.sort(values), rows.duration_days, side="right")/len(values)
        np.testing.assert_array_equal(ranks, rows.empirical_percentile.to_numpy())
        np.testing.assert_array_equal(rows.reference_median_days, np.full(len(rows), np.quantile(values, .5)))
        np.testing.assert_array_equal(rows.reference_p95_days, np.full(len(rows), np.quantile(values, .95)))
        if not peers.split.eq("train").all() or set(peers.order_id) & set(evidence.order_id):
            raise ValueError("Reference must be train-only, without candidate self-inclusion")
        group_id = hashlib.sha256(json.dumps([stage, context], sort_keys=True).encode()).hexdigest()
        lower, upper = exact_boundaries(values)
        groups.append({"reference_group_id": group_id, "stage": stage, "reference_context_json": encoded,
                       "reference_n": len(peers), "reference_median_days": float(np.quantile(values, .5)),
                       "descriptive_p95_days": float(np.quantile(values, .95)),
                       "lower_boundary_days_exclusive": lower, "upper_boundary_days_inclusive": upper,
                       "reference_latest_end_timestamp": peers.end_timestamp.max(),
                       "reference_order_ids_sha256": hashlib.sha256("\n".join(peers.order_id).encode()).hexdigest(),
                       "context_assessable": bool("distance_band" in context and len(peers) >= supplement.protocol()["minimum_reference_orders"])})
        mapping[(stage, encoded)] = group_id
    result = pd.DataFrame(groups)
    if result.empty or not result.reference_group_id.is_unique:
        raise ValueError("Missing or duplicate reference groups")
    return result, mapping


def stage_signals(cases, evidence, groups, mapping):
    base = performance.stage_durations(cases)
    base = base.loc[base.stage.isin(STAGES)].reset_index(drop=True)
    shared.business.require_key(evidence, ["order_id", "stage"], "frozen stage evidence")
    observed = base.merge(evidence[["order_id", "stage", "start_timestamp", "end_timestamp", "duration_days"]],
                          on=["order_id", "stage"], how="inner", suffixes=("", "_saved"), validate="one_to_one")
    for field in ["start_timestamp", "end_timestamp", "duration_days"]:
        pd.testing.assert_series_equal(observed[field], observed[field+"_saved"], check_names=False, check_exact=True)
    e = evidence[["order_id", "stage", "reference_context_json", "empirical_percentile", "context_warning_assessable"]].copy()
    e["reference_group_id"] = [mapping[(s, c)] for s, c in zip(e.stage, e.reference_context_json)]
    e = e.drop(columns="reference_context_json")
    rows = base.merge(e, on=["order_id", "stage"], how="left", validate="one_to_one")
    rows = rows.merge(groups.drop(columns=["stage", "reference_latest_end_timestamp", "reference_order_ids_sha256"]),
                      on="reference_group_id", how="left", validate="many_to_one")
    rows["comparison_available"] = rows.reference_group_id.notna()
    rows["assessable"] = rows.context_warning_assessable.astype("boolean").fillna(False)
    if not rows.assessable.eq(rows.context_assessable.astype("boolean").fillna(False)).all():
        raise ValueError("Reference eligibility changed")
    rows["assessment_status"] = np.select(
        [rows.quality_conflict, rows.duration_days.isna(), ~rows.comparison_available, ~rows.assessable],
        ["source_conflict", "endpoint_not_visible", "parent_complete_case_policy", "context_reference_unavailable"], default="assessable")
    positive = rows.duration_days.gt(0)
    config = protocol()
    rows["long_stage_warning"] = (positive & rows.empirical_percentile.gt(config["working_upper_percentile"])).astype("boolean").where(rows.assessable)
    rows["short_stage_note"] = (positive & rows.empirical_percentile.lt(config["working_lower_percentile"])).astype("boolean").where(rows.assessable)
    rows["zero_duration_observed"] = rows.duration_days.eq(0).astype("boolean").where(rows.duration_days.notna())
    rows["stage_signal"] = "not_assessable"
    rows.loc[rows.assessable, "stage_signal"] = "no_tail_signal_under_reference"
    rows.loc[rows.assessable & rows.zero_duration_observed.fillna(False), "stage_signal"] = "zero_duration_note"
    rows.loc[rows.short_stage_note.fillna(False), "stage_signal"] = "short_duration_note"
    rows.loc[rows.long_stage_warning.fillna(False), "stage_signal"] = "long_duration_warning"
    rows["excess_above_upper_boundary_days"] = (rows.duration_days-rows.upper_boundary_days_inclusive).clip(lower=0).where(rows.assessable)
    rows["shortfall_below_lower_boundary_days"] = (rows.lower_boundary_days_exclusive-rows.duration_days).clip(lower=0).where(rows.assessable)
    rows["upper_rank_threshold"] = config["working_upper_percentile"]
    rows["lower_rank_threshold"] = config["working_lower_percentile"]
    rows["upper_day_operator"] = "greater_than_or_equal_and_positive_duration"
    rows["lower_day_operator"] = "less_than_and_positive_duration"
    rows["stage_name_vi"] = rows.stage.map(NAMES)
    rows["limitation_vi"] = rows.assessment_status.map(LIMITS)
    rows["evidence_source"] = "frozen_case_endpoints_and_train_reference_not_causal_attribution"
    for field in ["reference_group_id", "reference_context_json"]:
        rows[field] = rows[field].astype("string")
    rows["reference_n"] = rows.reference_n.astype("Int64")
    return rows.drop(columns=["context_warning_assessable", "context_assessable"]).sort_values(["order_id", "stage"]).reset_index(drop=True)


def order_signals(cases, stages):
    fields = ["order_id", "split", "clean_order_record", "customer_state", "distance_band", "seller_count",
              "seller_attribution_scope", "visible_before_exclusive", *performance.TIMES, performance.ESTIMATE,
              "performance_state", "promise_delta_calendar_days", "days_early", "days_late", "performance_eligible",
              "source_quality_status", "payment_reconciliation", "payment_minus_items_and_freight_minor_units"]
    result = cases[fields].copy().set_index("order_id")
    grouped = stages.groupby("order_id", sort=True)
    result["assessed_stage_count"] = grouped.assessable.sum().astype(int)
    result["unassessed_stage_count"] = 3-result.assessed_stage_count
    result["assessment_coverage"] = np.select([result.assessed_stage_count.eq(3), result.assessed_stage_count.gt(0)],
                                            ["complete", "partial"], default="unavailable")
    for name in ["long_stage_warning", "short_stage_note"]:
        count = grouped[name].sum().astype(int)
        result[name+"_count"] = count
        result[name] = count.gt(0).astype("boolean").where(count.gt(0) | result.assessed_stage_count.eq(3))
        selected = stages.loc[stages[name].fillna(False)].groupby("order_id").stage.agg(list)
        result[name+"_stages_json"] = [json.dumps(selected.get(oid, [])) for oid in result.index]
    result["calendar_late_signal"] = result.performance_state.eq("late").astype("boolean").where(result.performance_eligible)
    primary = result.calendar_late_signal.fillna(False) | result.long_stage_warning.fillna(False)
    complete = result.performance_eligible & result.assessed_stage_count.eq(3)
    result["primary_attention_status"] = np.select([primary, complete],
        ["signal_present", "no_primary_signal_under_policy"], default="incomplete_assessment_no_primary_signal_observed")
    result["cause_required_for_detection"] = False
    result["policy_version"] = VERSION
    return result.reset_index().sort_values("order_id").reset_index(drop=True)


def signal_evidence(orders, stages):
    records = []
    columns = ["order_id", "stage", "signal_code", "attention_level", "observed_value", "comparison_value",
               "comparison_operator", "unit", "reference_group_id", "evidence_locator", "explanation_vi"]
    for row in orders.loc[orders.calendar_late_signal.fillna(False)].itertuples(index=False):
        records.append([row.order_id, "delivery_promise", "calendar_late", "primary", float(row.days_late), 0.0,
                        "greater_than", "calendar_days", pd.NA, f"order_signals.csv:order_id={row.order_id}",
                        f"Giao sau ngày hẹn {row.days_late} ngày; đây là dấu hiệu trễ hẹn, chưa xác định công đoạn gây trễ."])
    for row in stages.itertuples(index=False):
        if row.stage_signal not in {"long_duration_warning", "short_duration_note", "zero_duration_note"}:
            continue
        if row.stage_signal == "long_duration_warning":
            code, attention, boundary, operator = "long_stage", "primary", row.upper_boundary_days_inclusive, "greater_than_or_equal_and_positive_duration"
            explanation = (f"{NAMES[row.stage]}: {row.duration_days:.6f} ngày; thứ hạng thực nghiệm {row.empirical_percentile:.6f} > "
                           f"{row.upper_rank_threshold:.2f} trong {row.reference_n} đơn train tham chiếu. Ranh giới ngày: >= {boundary:.6f}, thời lượng > 0.")
        elif row.stage_signal == "short_duration_note":
            code, attention, boundary, operator = "short_stage", "secondary", row.lower_boundary_days_exclusive, "less_than_and_positive_duration"
            explanation = (f"{NAMES[row.stage]}: thời lượng dương {row.duration_days:.6f} ngày; thứ hạng {row.empirical_percentile:.6f} < "
                           f"{row.lower_rank_threshold:.2f}. Ghi chú thời gian ngắn, không tự kết luận lỗi hoặc bất thường.")
        else:
            code, attention, boundary, operator = "zero_stage", "secondary", 0.0, "equal"
            explanation = f"{NAMES[row.stage]}: hai mốc ghi nhận bằng nhau; không tự kết luận nhập sai hoặc bất thường."
        records.append([row.order_id, row.stage, code, attention, row.duration_days, boundary, operator, "elapsed_days",
                        row.reference_group_id, f"stage_signals.csv:order_id={row.order_id};stage={row.stage}", explanation])
    result = pd.DataFrame(records, columns=columns)
    result["reference_group_id"] = result.reference_group_id.astype("string")
    return result.sort_values(["order_id", "stage", "signal_code"]).reset_index(drop=True)


def evaluation_contract():
    rows = [
        ("software_consistency", "computed_now", "Actual timestamps, frozen ECDF ranks and explicit boundaries", "Implementation consistency only, not detection accuracy"),
        ("rule_agreement", "not_scored_here", "Separately declared rule reference and identical eligible cases", "Agreement with rules, not independent anomaly accuracy"),
        ("independent_signal_assessment", "not_scored_here", "Predeclared signal criteria, independent judgments, known/unknown cases and protected evaluation split", "Precision/recall/F1 only within a justified reference scope; root cause not required"),
        ("root_cause_attribution", "out_of_scope", "Additional operational or causal evidence would be needed", "Not a prerequisite for signal detection, localization or provisional configuration selection"),
    ]
    result = pd.DataFrame(rows, columns=["evaluation_scope", "status", "required_evidence", "allowed_interpretation"])
    result["required_for_signal_generation"] = [True, False, False, False]
    result["root_cause_required_for_signal_reference"] = False
    return result


def validate_inputs(inputs):
    cases, evidence = inputs["case_decisions.csv"], inputs["stage_evidence.csv"]
    shared.business.require_key(cases, ["order_id"], "signal input orders")
    if cases.empty or not cases.split.eq("validation").all() or not set(evidence.order_id).issubset(cases.order_id):
        raise ValueError("Only guarded parent validation orders are allowed")
    parent = criteria.protocol()
    for field in ["working_upper_percentile", "working_lower_percentile"]:
        if protocol()[field] != parent[field]:
            raise ValueError("This layer cannot change frozen thresholds")


def build_tables(inputs):
    validate_inputs(inputs)
    cases, evidence = inputs["case_decisions.csv"], inputs["stage_evidence.csv"]
    groups, mapping = reference_groups(evidence, inputs["train_reference"])
    stages = stage_signals(cases, evidence, groups, mapping)
    orders = order_signals(cases, stages)
    signals = signal_evidence(orders, stages)
    available = stages.assessable
    exact_long = stages.duration_days.gt(0) & stages.duration_days.ge(stages.upper_boundary_days_inclusive)
    exact_short = stages.duration_days.gt(0) & stages.duration_days.lt(stages.lower_boundary_days_exclusive)
    if (not stages.loc[available, "long_stage_warning"].eq(exact_long[available]).all()
            or not stages.loc[available, "short_stage_note"].eq(exact_short[available]).all()):
        raise ValueError("Day boundaries do not reproduce the frozen ECDF rule")
    counts = lambda series: {str(k): int(v) for k, v in series.value_counts().items()}
    summary = {"version": VERSION, "validation_orders": len(orders), "stage_rows": len(stages),
               "comparison_stage_rows": int(stages.comparison_available.sum()), "assessed_stage_rows": int(available.sum()),
               "reference_groups": len(groups), "performance_counts": counts(orders.performance_state),
               "assessment_coverage_counts": counts(orders.assessment_coverage), "stage_assessment_counts": counts(stages.assessment_status),
               "primary_attention_counts": counts(orders.primary_attention_status), "evidence_counts": counts(signals.signal_code),
               "calendar_late_orders": int(orders.calendar_late_signal.fillna(False).sum()),
               "long_stage_warning_orders": int(orders.long_stage_warning.fillna(False).sum()),
               "short_stage_note_orders": int(orders.short_stage_note.fillna(False).sum()),
               "early_with_long_stage_orders": int((orders.performance_state.eq("early") & orders.long_stage_warning.fillna(False)).sum()),
               "late_with_long_stage_orders": int((orders.calendar_late_signal.fillna(False) & orders.long_stage_warning.fillna(False)).sum()),
               "exact_boundary_consistency": True, "cause_required_for_detection": False,
               "reference_labels_created": 0, "model_trained": False, "threshold_changed": False,
               "test_scored": False, "split_activated": False, "anomaly_accuracy_computed": False,
               "selected_ml_detector": None, "near_real_time_validated": False}
    return {"order_signals.csv": orders, "stage_signals.csv": stages, "signal_evidence.csv": signals,
            "reference_groups.csv": groups, "evaluation_contract.csv": evaluation_contract()}, summary


def render_case(frames, order_id):
    selected = frames["order_signals.csv"].loc[lambda x: x.order_id.eq(order_id)]
    if len(selected) != 1:
        raise ValueError("Order is outside this guarded validation snapshot")
    case = selected.iloc[0]
    rows = frames["stage_signals.csv"].loc[lambda x: x.order_id.eq(order_id)]
    lines = [f"### Đơn {order_id}", "", f"- Giao hàng: **{STATE_NAMES[case.performance_state]}**.",
             f"- Đã đánh giá được {case.assessed_stage_count}/3 công đoạn theo chính sách hiện tại.",
             "- Không cần biết nguyên nhân để ghi nhận dấu hiệu; không quy trách nhiệm seller.", ""]
    if case.performance_eligible:
        lines.insert(3, f"- Chênh lệch ngày giao so với hẹn: {case.promise_delta_calendar_days} ngày (âm là sớm, dương là trễ).")
    lines.insert(-1, f"- Chất lượng mốc: `{case.source_quality_status}`; đối chiếu tiền: `{case.payment_reconciliation}`.")
    for row in rows.itertuples(index=False):
        duration = "chưa quan sát đủ mốc" if pd.isna(row.duration_days) else f"{row.duration_days:.6f} ngày"
        lines.append(f"- **{NAMES[row.stage]}:** {duration}; trạng thái `{row.stage_signal}`.")
        if row.assessable:
            lines.append(f"  Tham chiếu: {row.reference_n} đơn, trung vị {row.reference_median_days:.6f} ngày; "
                         f"cảnh báo dài khi thời lượng dương và >= {row.upper_boundary_days_inclusive:.6f} ngày. "
                         f"Nhóm: `{row.reference_context_json}`.")
        else:
            lines.append(f"  {row.limitation_vi}")
    signals = frames["signal_evidence.csv"].loc[lambda x: x.order_id.eq(order_id)]
    lines += ["", "**Bằng chứng đã phát sinh:**"]
    lines += [f"- {row.explanation_vi}" for row in signals.itertuples(index=False)] or ["- Không có tín hiệu được ghi nhận trong phạm vi đã chấm; không đồng nghĩa toàn đơn bình thường."]
    return "\n".join(lines)


def render_report(frames, summary):
    lines = ["# Đầu ra phát hiện dấu hiệu theo quy trình", "",
             "Đã tách phát hiện dấu hiệu khỏi giải thích nguyên nhân. Đầu ra sử dụng dữ liệu Olist thật đã có; "
             "không tạo đơn, nhãn người rà soát hoặc kết luận nguyên nhân giả.", "",
             f'Phạm vi: **{summary["validation_orders"]} đơn validation**, {summary["stage_rows"]} dòng công đoạn; '
             "không đọc/chấm đặc trưng test cuối và không thay đổi bộ pilot.", "",
             "## Kết quả", "", "| Nội dung | Số đơn |", "| --- | ---: |",
             f'| Trễ ngày hẹn | {summary["calendar_late_orders"]} |',
             f'| Có công đoạn dài theo quy tắc hiện tại | {summary["long_stage_warning_orders"]} |',
             f'| Có ghi chú thời gian ngắn dương | {summary["short_stage_note_orders"]} |',
             f'| Giao sớm nhưng có công đoạn dài | {summary["early_with_long_stage_orders"]} |',
             f'| Giao trễ và có công đoạn dài | {summary["late_with_long_stage_orders"]} |', "",
             "Các nhóm giao nhau, không cộng thành tổng số đơn. Đây là số tín hiệu, không phải số bất thường đã được kiểm chứng.", "",
             "## Phạm vi đánh giá", ""]
    for key, count in summary["assessment_coverage_counts"].items():
        lines.append(f"- `{key}`: {count} đơn.")
    lines += ["", "Thiếu dữ liệu, mâu thuẫn nguồn hoặc thiếu tham chiếu không được đổi thành không cảnh báo. "
              "Phiên bản này giữ chính sách so sánh đơn có đầy đủ công đoạn của nguồn; có thời lượng ở một công đoạn "
              "chưa đảm bảo đơn đã thuộc phạm vi chấm. Không tuyên bố xử lý được tất cả đơn đang giao.", "",
              "## Quy tắc và bằng chứng", "",
              "Cảnh báo dài giữ nguyên quy tắc F(d) > 0,95, với F(d) là tỷ lệ thời lượng train tham chiếu <= d. "
              "Đây là thứ hạng, không phải xác suất bất thường. Ranh giới số ngày tương đương được lưu cùng toán tử >=; "
              "thời lượng phải dương. P95 nội suy chỉ là thống kê mô tả, không bị đánh tráo thành ranh giới quyết định.", "",
              "Giao sớm/đúng hẹn là thông tin hiệu suất. Thời gian ngắn dương chỉ là ghi chú phụ; bằng 0 không tự chứng minh lỗi. "
              "Trễ hẹn và công đoạn dài là hai loại tín hiệu độc lập về ý nghĩa, không tự chứng minh nguyên nhân.", "",
              "## Ví dụ từ dữ liệu thật", ""]
    orders = frames["order_signals.csv"]
    masks = [orders.performance_state.eq("early") & orders.long_stage_warning.fillna(False),
             orders.calendar_late_signal.fillna(False) & orders.long_stage_warning.fillna(False),
             orders.calendar_late_signal.fillna(False) & ~orders.long_stage_warning.fillna(False),
             orders.assessment_coverage.eq("unavailable")]
    used = set()
    for mask in masks:
        candidates = orders.loc[mask & ~orders.order_id.isin(used), "order_id"]
        if len(candidates):
            oid = candidates.iloc[0]
            used.add(oid)
            lines.extend([render_case(frames, oid), ""])
    lines += ["## Đánh giá tiếp theo", "",
              "Đánh giá dấu hiệu và định vị công đoạn không bắt buộc biết nguyên nhân gốc. Có thể chọn cấu hình chạy thử "
              "theo tiêu chí vận hành công khai; bước này chưa chọn mô hình ML tốt nhất. Khi có tham chiếu độc lập về "
              "dấu hiệu đáng cảnh báo, mới báo cáo precision/recall/F1 đúng phạm vi. Đối chiếu quy tắc phải được gọi là "
              "mức khớp quy tắc, không lấy nhãn do chính quy tắc sinh ra để chứng minh nó chính xác.", "",
              "Bước này hoàn thành chuẩn hóa đầu ra và tách phạm vi đánh giá; chưa thực hiện so sánh/chọn lại detector, "
              "dashboard, multi-agent hoặc phát lại sự kiện near real-time. Tham chiếu chưa được xác nhận bình thường "
              "và chưa kiểm soát đầy đủ các yếu tố vận hành. Kết quả là hồi cứu.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = performance.destination_guard(output, {"inspection": provenance["performance_source"]["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite process signals")
    if set(frames) != TABLES:
        raise ValueError("Incomplete process signal tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".process-signals-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_dau_hieu.md").write_text(render_report(frames, summary))
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "schemas": schemas, "summary": summary,
                    "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())}}
        review.write_json(staging/"manifest.json", manifest)
        load_snapshot(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    expected = TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_dau_hieu.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES or set(manifest.get("output_hashes", {})) != expected):
        raise ValueError("Invalid process signal manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Signal summary/protocol mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--order", help="Inspect one real order in the saved snapshot")
    args = parser.parse_args()
    try:
        if args.summary or args.order:
            manifest = load_snapshot(args.output)
            if args.order:
                frames = {name: shared.read_table(args.output, name, manifest) for name in
                          ["order_signals.csv", "stage_signals.csv", "signal_evidence.csv"]}
                print(render_case(frames, args.order))
                return
            result = manifest["summary"]
        else:
            if args.output.exists():
                raise FileExistsError("Output already exists")
            inputs, provenance = load_inputs()
            frames, summary = build_tables(inputs)
            result = write_snapshot(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Process signals stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
