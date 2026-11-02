"""Describe contextual concentrations of real process signals, not proven causes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import build_process_signals as signals

ROOT, shared, review = signals.ROOT, signals.shared, signals.review
VERSION = "bottleneck_context_v1"
TEMPLATE = ROOT/"templates/bottleneck_context_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_bottleneck_context_v1"
TABLES = {"stage_context.csv", "stage_overview.csv", "context_groups.csv", "matched_cells.csv",
          "group_membership.csv", "investigation_actions.csv"}
UNKNOWN = "unavailable"
DIMENSION_NAMES = {"overall": "Toàn bộ", "customer_state": "Bang khách hàng",
                   "stage_start_month": "Tháng bắt đầu công đoạn", "distance_band": "Nhóm khoảng cách ZIP (km)"}
ACTION_COLUMNS = ["group_id", "stage", "dimension", "group_value", "observed_evidence_vi", "hypothesis_vi",
                  "required_verification_data_vi", "investigation_action_vi", "conditional_intervention_vi",
                  "evaluation_after_intervention_vi", "cause_status", "action_status", "evidence_locator"]
CHECKLISTS = {
    "approval": (
        "Có thể có khác biệt trong xử lý phê duyệt hoặc cách ghi nhận mốc; chưa xác nhận.",
        "Log yêu cầu/phản hồi phê duyệt, thời điểm thanh toán thực tế, hàng đợi xử lý và lịch vận hành.",
        "Đối chiếu mốc mua/phê duyệt với log gốc; kiểm tra thời gian chờ và quy tắc duyệt trong nhóm được nêu.",
        "Nếu log xác nhận chờ phê duyệt, thử điều chỉnh lịch xử lý hoặc năng lực duyệt trong phạm vi kiểm soát."),
    "preparation_handoff": (
        "Có thể có thời gian chờ chuẩn bị hoặc chờ bàn giao; dữ liệu hiện tại không tách được hai khả năng.",
        "Log tồn kho, picking/packing, thời điểm sẵn sàng giao, lịch lấy hàng và mốc hãng vận chuyển tiếp nhận.",
        "Tách thời gian chuẩn bị khỏi chờ lấy hàng bằng log bổ sung; kiểm tra các đơn cụ thể trước khi quy trách nhiệm.",
        "Nếu xác nhận chờ chuẩn bị hoặc lấy hàng, thử điều phối nguồn lực hoặc lịch lấy hàng phù hợp với khâu đã xác minh."),
    "transport": (
        "Có thể có chờ tại trung chuyển, khác biệt tuyến hoặc lần giao; chưa biết nơi hay nguyên nhân thực tế.",
        "Carrier, dịch vụ, tuyến thực tế, lần quét tracking, điểm trung chuyển, lần giao thất bại và sự cố ghi nhận.",
        "Rà soát tracking của nhóm liên quan; kiểm tra tuyến, dịch vụ và các khoảng không có cập nhật, không suy ra kẹt xe từ địa lý.",
        "Nếu tracking xác nhận điểm chờ, thử điều chỉnh tuyến, lịch hoặc phương án vận chuyển có kiểm soát; không tự kéo dài cam kết để che trễ.")}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}


def verify_sources(provenance):
    source = Path(provenance["signals_directory"])
    if review.digest(source/"manifest.json") != provenance["signals_manifest_sha256"]:
        raise ValueError("Frozen signal source changed")
    manifest = signals.load_snapshot(source)
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Context analysis changed; use a new version")
    return manifest


def load_inputs(source=signals.DEFAULT_OUTPUT):
    source = Path(source).resolve()
    provenance = {"signals_directory": str(source), "signals_manifest_sha256": review.digest(source/"manifest.json"),
                  "code_hashes": code_hashes()}
    manifest = verify_sources(provenance)
    frames = {name: shared.read_table(source, name, manifest) for name in ["order_signals.csv", "stage_signals.csv"]}
    verify_sources(provenance)
    return frames, provenance


def stable_id(*values):
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def stage_context(inputs):
    orders, stages = inputs["order_signals.csv"], inputs["stage_signals.csv"]
    shared.business.require_key(orders, ["order_id"], "context orders")
    shared.business.require_key(stages, ["order_id", "stage"], "context stages")
    if (orders.empty or not orders.split.eq("validation").all() or not stages.split.eq("validation").all()
            or set(orders.order_id) != set(stages.order_id)
            or not stages.groupby("order_id").stage.agg(set).map(lambda x: x == set(signals.STAGES)).all()):
        raise ValueError("Expected all three stages for each guarded validation order")
    indexed = orders.set_index("order_id")
    for field in ["customer_state", "distance_band", "performance_state", "visible_before_exclusive"]:
        expected = stages.order_id.map(indexed[field])
        pd.testing.assert_series_equal(stages[field], expected, check_names=False, check_dtype=False, check_exact=True)
    for field in ["start_timestamp", "end_timestamp"]:
        known = stages[field].notna()
        if not stages.loc[known, field].lt(stages.loc[known, "visible_before_exclusive"]).all():
            raise ValueError("Stage contains an event after the observation cutoff")
    if orders.visible_before_exclusive.nunique() != 1:
        raise ValueError("Expected one frozen observation cutoff")
    expected = (stages.end_timestamp-stages.start_timestamp).dt.total_seconds()/86400
    pd.testing.assert_series_equal(stages.duration_days, expected, check_names=False, check_exact=True)
    if (stages.assessable.isna().any() or stages.long_stage_warning.notna().ne(stages.assessable).any()
            or stages.loc[stages.assessable, "duration_days"].isna().any()
            or stages.loc[stages.assessable, "duration_days"].lt(0).any()):
        raise ValueError("Unknown or invalid durations cannot become assessed negatives")
    expected_flag = stages.duration_days.gt(0) & stages.duration_days.ge(stages.upper_boundary_days_inclusive)
    if not stages.loc[stages.assessable, "long_stage_warning"].eq(expected_flag[stages.assessable]).all():
        raise ValueError("Frozen signal flags changed")
    fields = ["order_id", "seller_attribution_scope", "performance_eligible", "calendar_late_signal"]
    result = stages.merge(orders[fields], on="order_id", how="left", validate="many_to_one")
    result["stage_start_month"] = result.start_timestamp.dt.strftime("%Y-%m").astype("string").fillna(UNKNOWN)
    result["start_month_contains_cutoff"] = result.stage_start_month.eq(result.visible_before_exclusive.dt.strftime("%Y-%m"))
    result["days_from_start_to_cutoff"] = (result.visible_before_exclusive-result.start_timestamp).dt.total_seconds()/86400
    result["source_evidence_locator"] = "stage_signals.csv:order_id="+result.order_id+";stage="+result.stage
    return result.sort_values(["stage", "order_id"]).reset_index(drop=True)


def descriptive(group):
    assessed = group.loc[group.assessable]
    flagged = assessed.loc[assessed.long_stage_warning]
    eligible_outcome = group.loc[group.performance_eligible]
    return {"total_orders": len(group), "assessed_orders": len(assessed), "unassessed_orders": len(group)-len(assessed),
            "assessment_coverage": len(assessed)/len(group), "long_stage_orders": len(flagged),
            "long_stage_rate": len(flagged)/len(assessed) if len(assessed) else np.nan,
            "median_duration_days_assessed": float(assessed.duration_days.median()) if len(assessed) else np.nan,
            "median_duration_days_flagged": float(flagged.duration_days.median()) if len(flagged) else np.nan,
            "sum_excess_boundary_days_flagged": float(flagged.excess_above_upper_boundary_days.sum()),
            "calendar_comparable_orders": len(eligible_outcome),
            "calendar_late_orders": int(eligible_outcome.calendar_late_signal.sum()),
            "calendar_late_rate": float(eligible_outcome.calendar_late_signal.mean()) if len(eligible_outcome) else np.nan,
            "long_and_early_orders": int(flagged.performance_state.eq("early").sum()),
            "long_and_late_orders": int(flagged.performance_state.eq("late").sum()),
            "source_conflict_orders": int(group.assessment_status.eq("source_conflict").sum()),
            "missing_endpoint_orders": int(group.assessment_status.eq("endpoint_not_visible").sum()),
            "parent_policy_excluded_orders": int(group.assessment_status.eq("parent_complete_case_policy").sum()),
            "context_reference_unavailable_orders": int(group.assessment_status.eq("context_reference_unavailable").sum()),
            "start_month_contains_cutoff_orders": int(group.start_month_contains_cutoff.sum())}


def match_group(target, outside, fields, group_id):
    config = protocol()
    target = target.loc[target.assessable]
    outside = outside.loc[outside.assessable]
    cells, membership = [], []
    for key, part in target.groupby(fields, dropna=False, sort=True):
        key = key if isinstance(key, tuple) else (key,)
        context = {field: str(value) if pd.notna(value) else UNKNOWN for field, value in zip(fields, key)}
        known = all(value not in {UNKNOWN, "unknown", ""} for value in context.values())
        mask = pd.Series(True, index=outside.index)
        for field, value in zip(fields, key):
            mask &= outside[field].eq(value).fillna(False)
        peers = outside.loc[mask] if known else outside.iloc[:0]
        included = (known and len(part) >= config["minimum_cell_target_orders"]
                    and len(peers) >= config["minimum_cell_comparator_orders"])
        status = ("included" if included else "unknown_match_context" if not known else
                  "too_few_target_orders" if len(part) < config["minimum_cell_target_orders"] else "too_few_comparator_orders")
        cell_id = stable_id(group_id, context)
        cells.append({"group_id": group_id, "cell_id": cell_id, "match_context_json": json.dumps(context, sort_keys=True),
                      "target_orders": len(part), "target_signals": int(part.long_stage_warning.sum()),
                      "comparator_orders": len(peers), "comparator_signals": int(peers.long_stage_warning.sum()),
                      "included": included, "status": status})
        membership.extend({"group_id": group_id, "order_id": oid, "role": "target", "cell_id": cell_id,
                           "included_in_comparison": included} for oid in part.order_id)
        if included:
            membership.extend({"group_id": group_id, "order_id": oid, "role": "comparator", "cell_id": cell_id,
                               "included_in_comparison": True} for oid in peers.order_id)
    columns = ["group_id", "cell_id", "match_context_json", "target_orders", "target_signals", "comparator_orders",
               "comparator_signals", "included", "status"]
    table = pd.DataFrame(cells, columns=columns).astype({
        "group_id": "string", "cell_id": "string", "match_context_json": "string", "status": "string",
        "target_orders": "int64", "target_signals": "int64", "comparator_orders": "int64",
        "comparator_signals": "int64", "included": "bool"})
    included = table.loc[table.included.eq(True)]
    n, comparator_n = int(included.target_orders.sum()), int(included.comparator_orders.sum())
    table["target_weight"] = np.nan
    if n:
        table.loc[table.included.eq(True), "target_weight"] = included.target_orders/n
    target_rate = int(included.target_signals.sum())/n if n else np.nan
    comparator_rate = float(((included.target_orders/n)*(included.comparator_signals/included.comparator_orders)).sum()) if n else np.nan
    stats = {"matched_target_orders": n, "matched_target_signals": int(included.target_signals.sum()),
             "matched_comparator_orders": comparator_n, "matched_comparator_signals_unweighted": int(included.comparator_signals.sum()),
             "target_match_coverage": n/len(target) if len(target) else np.nan,
             "matched_target_rate": target_rate, "matched_comparator_rate": comparator_rate,
             "matched_rate_difference": target_rate-comparator_rate}
    return stats, table, membership


def comparison_status(row, dimension):
    config = protocol()
    if dimension in {"overall", "distance_band"}:
        return "descriptive_only"
    if row["group_value"] == UNKNOWN:
        return "unknown_group_context"
    if dimension == "stage_start_month" and row["start_month_contains_cutoff_orders"]:
        return "partial_calendar_month_descriptive_only"
    if not row["matched_target_orders"]:
        return "no_supported_comparison_cells"
    if (row["matched_target_orders"] < config["minimum_matched_target_orders"]
            or row["matched_comparator_orders"] < config["minimum_matched_comparator_orders"]):
        return "insufficient_matched_orders"
    if row["target_match_coverage"] < config["minimum_target_match_coverage"]:
        return "insufficient_match_coverage"
    return "supported_descriptive_comparison"


def grouped_analysis(context):
    rows, cells, memberships = [], [], []
    blank_stats = {"matched_target_orders": 0, "matched_target_signals": 0, "matched_comparator_orders": 0,
                   "matched_comparator_signals_unweighted": 0, "target_match_coverage": np.nan,
                   "matched_target_rate": np.nan, "matched_comparator_rate": np.nan, "matched_rate_difference": np.nan}
    for stage, pool in context.groupby("stage", sort=True):
        for definition in protocol()["group_definitions"]:
            dimension, fields = definition["dimension"], definition["match_fields"]
            groups = [("all", pool)] if dimension == "overall" else pool.groupby(dimension, dropna=False, sort=True)
            for value, target in groups:
                value = str(value) if pd.notna(value) else UNKNOWN
                gid = stable_id(stage, dimension, value)
                row = {"group_id": gid, "stage": stage, "dimension": dimension, "group_value": value,
                       "match_fields_json": json.dumps(fields), **descriptive(target)}
                member_rows = []
                row["comparator_assessed_orders_excluded_partial_month"] = 0
                partial_target_month = dimension == "stage_start_month" and target.start_month_contains_cutoff.any()
                if fields and not partial_target_month:
                    outside = pool.loc[~pool.order_id.isin(target.order_id)]
                    if dimension == "stage_start_month":
                        row["comparator_assessed_orders_excluded_partial_month"] = int(
                            (outside.assessable & outside.start_month_contains_cutoff).sum())
                        outside = outside.loc[~outside.start_month_contains_cutoff]
                    stats, cell_table, member_rows = match_group(target, outside, fields, gid)
                    cells.append(cell_table)
                else:
                    stats = blank_stats.copy()
                recorded = {r["order_id"] for r in member_rows if r["role"] == "target"}
                member_rows.extend({"group_id": gid, "order_id": oid, "role": "target", "cell_id": pd.NA,
                                    "included_in_comparison": False} for oid in target.order_id if oid not in recorded)
                memberships.extend({**member, "stage": stage} for member in member_rows)
                row.update(stats)
                row["comparison_status"] = comparison_status(row, dimension)
                row["investigation_candidate"] = bool(
                    row["comparison_status"] == "supported_descriptive_comparison"
                    and row["matched_target_signals"] >= protocol()["minimum_matched_signals_for_investigation"]
                    and row["matched_rate_difference"] > 0)
                row["causal_status"] = "association_only_no_confirmed_operational_cause"
                rows.append(row)
    groups = pd.DataFrame(rows).sort_values(["stage", "dimension", "group_value"]).reset_index(drop=True)
    matches = pd.concat(cells, ignore_index=True).sort_values(["group_id", "cell_id"]).reset_index(drop=True)
    members = pd.DataFrame(memberships).sort_values(["group_id", "role", "order_id"]).reset_index(drop=True)
    members["cell_id"] = members.cell_id.astype("string")
    shared.business.require_key(members, ["group_id", "order_id"], "target/comparator membership")
    members["evidence_locator"] = "stage_context.csv:order_id="+members.order_id+";stage="+members.stage
    return groups, matches, members


def investigation_actions(groups):
    records = []
    for row in groups.loc[groups.investigation_candidate].itertuples(index=False):
        hypothesis, required, action, intervention = CHECKLISTS[row.stage]
        observation = (f"{signals.NAMES[row.stage]}; {DIMENSION_NAMES[row.dimension]}={row.group_value}: "
                       f"phần khớp {row.matched_target_signals}/{row.matched_target_orders} đơn có dấu hiệu dài "
                       f"({row.matched_target_rate:.2%}), đối chiếu có trọng số {row.matched_comparator_rate:.2%}; "
                       f"chênh {100*row.matched_rate_difference:.2f} điểm phần trăm. Bao phủ "
                       f"{row.matched_target_orders}/{row.assessed_orders} đơn đã chấm, trên {row.total_orders} đơn trong nhóm.")
        records.append([row.group_id, row.stage, row.dimension, row.group_value, observation, hypothesis, required,
                        action, intervention,
                        "Chưa đo hiệu quả. Cần dữ liệu sau can thiệp và nhóm đối chiếu phù hợp; giữ định nghĩa chỉ số, kiểm tra cơ cấu đơn và mức thiếu dữ liệu.",
                        "unverified_hypothesis", "investigate_before_intervention", f"context_groups.csv:group_id={row.group_id}"])
    return pd.DataFrame(records, columns=ACTION_COLUMNS).astype({c: "string" for c in ACTION_COLUMNS})


def build_tables(inputs):
    context = stage_context(inputs)
    groups, cells, members = grouped_analysis(context)
    actions = investigation_actions(groups)
    overview = groups.loc[groups.dimension.eq("overall")].reset_index(drop=True)
    orders = inputs["order_signals.csv"]
    prioritized = members.loc[members.group_id.isin(actions.group_id) & members.role.eq("target")
                             & members.included_in_comparison, ["order_id", "stage"]].drop_duplicates()
    prioritized = prioritized.merge(context[["order_id", "stage", "long_stage_warning"]],
                                    on=["order_id", "stage"], validate="one_to_one")
    counts = lambda series: {str(k): int(v) for k, v in series.value_counts().items()}
    summary = {"version": VERSION, "validation_orders": len(orders), "stage_rows": len(context),
               "assessed_stage_rows": int(context.assessable.sum()),
               "long_stage_signal_rows": int(context.long_stage_warning.sum()),
               "long_stage_unique_orders": int(context.loc[context.long_stage_warning.fillna(False), "order_id"].nunique()),
               "calendar_late_orders": int(orders.calendar_late_signal.sum()), "context_groups": len(groups),
               "comparison_status_counts": counts(groups.comparison_status), "matched_cells": int(cells.included.sum()),
               "investigation_groups": len(actions), "investigation_unique_orders": int(prioritized.loc[
                   prioritized.long_stage_warning.fillna(False), "order_id"].nunique()),
               "cutoff_exclusive": str(orders.visible_before_exclusive.iloc[0]),
               "causes_confirmed": 0, "interventions_evaluated": 0, "model_trained": False, "threshold_changed": False,
               "reference_labels_created": 0, "test_scored": False, "independent_accuracy_computed": False,
               "near_real_time_validated": False, "multi_agent_implemented": False}
    return {"stage_context.csv": context, "stage_overview.csv": overview, "context_groups.csv": groups,
            "matched_cells.csv": cells, "group_membership.csv": members, "investigation_actions.csv": actions}, summary


def render_report(frames, summary):
    lines = ["# Phân tích bối cảnh dấu hiệu tắc nghẽn", "",
             "Luồng đã chạy: dữ liệu thật -> dấu hiệu -> công đoạn -> nhóm bối cảnh -> đối chiếu -> đề xuất kiểm tra. "
             "Giữ nguyên ngưỡng và mô hình; không tạo nhãn hoặc nguyên nhân giả.", "",
             f'Phạm vi: {summary["validation_orders"]} đơn validation ngành bed_bath_table; mốc quan sát < '
             f'{summary["cutoff_exclusive"]}. Đây là dữ liệu hồi cứu, không phải luồng trực tuyến.', "",
             "## Công đoạn", "", "| Công đoạn | Tổng đơn | Chấm được | Dấu hiệu dài | Tỷ lệ trên số chấm được |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for row in frames["stage_overview.csv"].itertuples(index=False):
        rate = "chưa đủ dữ liệu" if pd.isna(row.long_stage_rate) else f"{row.long_stage_rate:.2%}"
        lines.append(f"| {signals.NAMES[row.stage]} | {row.total_orders} | {row.assessed_orders} | {row.long_stage_orders} | {rate} |")
    lines += ["", f'{summary["long_stage_signal_rows"]} dấu hiệu công đoạn thuộc {summary["long_stage_unique_orders"]} đơn. '
              f'Có {summary["calendar_late_orders"]} đơn trễ ngày hẹn với phạm vi đánh giá riêng. Không cộng các nhóm giao nhau.', "",
              "## Cách đối chiếu", "",
              "Tỷ lệ = số đơn có dấu hiệu / số đơn chấm được, không chia số cảnh báo cho toàn bộ dữ liệu rồi coi phần thiếu là bình thường. "
              "Bảng còn giữ tổng đơn và lý do chưa chấm.", "",
              "So bang: khớp nhóm khoảng cách, phạm vi một/nhiều seller và tháng bắt đầu công đoạn. So tháng: khớp bang, "
              "nhóm khoảng cách và phạm vi seller. Đối chứng là đơn ở nhóm khác trong cùng tập validation, không chứa đơn đích. "
              "Tỷ lệ đối chiếu được tính theo trọng số số đơn của từng ô trong nhóm đích; không gộp tùy ý đơn xa với đơn gần.", "",
              "So sánh theo tháng loại tháng chứa cutoff khỏi cả phía đích và đối chứng. Các tháng còn lại vẫn có thể thiếu theo dõi, "
              "nên đây chưa phải thực nghiệm cửa sổ theo dõi bằng nhau. So khu vực có khớp tháng nhưng không loại hết sai lệch chọn mẫu.", "",
              "Ô cần >=5 đơn mỗi phía; phần khớp cần >=30 đơn mỗi phía, bao phủ >=80% số đơn đích chấm được. "
              "Nhóm được đề xuất kiểm tra khi tỷ lệ đích cao hơn đối chiếu và có >=5 dấu hiệu trong phần khớp. "
              "Đây là quy ước hỗ trợ báo cáo, không phải ngưỡng tối ưu hoặc kiểm định ý nghĩa thống kê.", "",
              "## Nhóm cần ưu tiên kiểm tra", "",
              f'Có {summary["investigation_groups"]} nhóm đạt quy ước; không phải số nguyên nhân hoặc số tắc nghẽn đã xác nhận.', "",
              "| Công đoạn / nhóm | Dấu hiệu trong phần khớp | Tỷ lệ đích | Đối chiếu có trọng số | Chênh lệch (điểm %) | Khớp / chấm được / tổng |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    selected = frames["context_groups.csv"].loc[lambda x: x.investigation_candidate].sort_values(
        ["matched_rate_difference", "matched_target_signals", "group_id"], ascending=[False, False, True])
    for row in selected.itertuples(index=False):
        lines.append(f"| {signals.NAMES[row.stage]} / {DIMENSION_NAMES[row.dimension]}={row.group_value} | "
                     f"{row.matched_target_signals}/{row.matched_target_orders} | {row.matched_target_rate:.2%} | "
                     f"{row.matched_comparator_rate:.2%} | {100*row.matched_rate_difference:.2f} | "
                     f"{row.matched_target_orders}/{row.assessed_orders}/{row.total_orders} |")
    if selected.empty:
        lines += ["", "Chưa có nhóm đủ điều kiện; không tự hạ tiêu chuẩn hoặc tạo phát hiện để lấp kết quả."]
    lines += ["", "Thứ tự theo chênh lệch tỷ lệ mô tả, không phải điểm nguyên nhân hoặc mô hình tối ưu. "
              "Chênh lệch dương rất nhỏ vẫn có thể vào danh sách; không có nghĩa khác biệt đáng kể hoặc đáng can thiệp. "
              "Các nhóm giao nhau; đối chứng có thể được tái sử dụng. Toàn bộ nhóm, kể cả không nổi bật, đều được xuất.", "",
              "## Đề xuất gắn với bằng chứng", ""]
    for row in frames["investigation_actions.csv"].itertuples(index=False):
        lines += [f"### {signals.NAMES[row.stage]} / {row.group_value}", "", row.observed_evidence_vi, "",
                  f"- Giả thuyết chưa xác nhận: {row.hypothesis_vi}", f"- Kiểm tra: {row.investigation_action_vi}",
                  f"- Cần thêm: {row.required_verification_data_vi}", f"- Can thiệp có điều kiện: {row.conditional_intervention_vi}",
                  f"- Đánh giá: {row.evaluation_after_intervention_vi}", f"- Truy vết: `{row.evidence_locator}`.", ""]
    lines += ["## Giới hạn và phần cần làm tiếp", "",
              "Không đủ khớp thì chưa kết luận, không phải nhóm bình thường. Tháng chứa cutoff bị chặn một phần nên chỉ mô tả; "
              "các tháng trước cũng có thể thiếu theo dõi. Không lấy tỷ lệ thấp ở nhóm nhiều đơn chưa hoàn tất làm bằng chứng vận hành tốt.", ""]
    lines += [f"- `{key}`: {count} nhóm." for key, count in summary["comparison_status_counts"].items()]
    lines += ["", "Đây là liên hệ mô tả với dấu hiệu do quy tắc sinh ra, không phải độ chính xác độc lập hay nguyên nhân được chứng minh. "
              "Mỗi dấu hiệu đã dùng tham chiếu train theo bối cảnh: tỷ lệ ở đây là tỷ lệ vượt ranh giới tương ứng, không phải so cùng một số ngày cố định. "
              "Bang khách hàng không phải vị trí sự cố; khoảng cách ZIP không phải tuyến thực tế. Chưa kiểm soát seller cụ thể, "
              "carrier, dịch vụ, khối lượng hàng, tồn kho, nguồn lực, thời tiết và các yếu tố vận hành khác.", "",
              "Số ngày vượt ranh giới không phải thiệt hại, thời gian chờ thực tế hoặc ngày có thể tiết kiệm. "
              "Giả thuyết và checklist đề xuất là quy tắc minh bạch, chưa phải multi-agent AI. Chưa thực hiện hoặc chứng minh hiệu quả can thiệp.", "",
              "Bước tiếp theo: đối chiếu các đơn thật trong nhóm ưu tiên, kiểm tra độ ổn định ở các cửa sổ quan sát thích hợp, "
              "và tích hợp lớp bằng chứng này vào báo cáo/dashboard. Nếu cần xác nhận nguyên nhân, phải bổ sung log vận hành; "
              "không cần dữ liệu mới để đọc lại các kết quả hiện tại. Không dùng test cuối để chọn nhóm hoặc sửa ngưỡng.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    source_manifest = verify_sources(provenance)
    output = signals.performance.destination_guard(output, {"inspection": source_manifest["provenance"]["performance_source"]["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite context analysis")
    if set(frames) != TABLES:
        raise ValueError("Incomplete context analysis tables")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".bottleneck-context-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame,
                                          check_dtype=False, check_exact=True)
        review.write_json(staging/"protocol.json", protocol())
        review.write_json(staging/"summary.json", summary)
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_boi_canh.md").write_text(render_report(frames, summary))
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
    expected = TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_boi_canh.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES or set(manifest.get("output_hashes", {})) != expected):
        raise ValueError("Invalid context analysis manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Context analysis summary/protocol mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--summary", action="store_true")
    args = parser.parse_args()
    try:
        if args.summary:
            result = load_snapshot(args.output)["summary"]
        else:
            if args.output.exists():
                raise FileExistsError("Output already exists")
            inputs, provenance = load_inputs()
            frames, summary = build_tables(inputs)
            result = write_snapshot(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Context analysis stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
