"""Compare observable criteria and select evidence semantics, not fake truth."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import prepare_delivery_supplement as supplement

performance = supplement.performance
review, shared, ROOT = supplement.review, supplement.shared, supplement.ROOT
VERSION = "evidence_criteria_v1"
TEMPLATE = ROOT/"templates/evidence_criteria_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_evidence_criteria_v1"
TABLES = {"criteria_decisions.csv", "case_decisions.csv", "stage_evidence.csv", "threshold_sensitivity.csv",
          "threshold_disagreements.csv", "saved_model_comparison.csv", "contract_checks.csv"}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}


def load_inputs():
    frames, source = supplement.load_inputs()
    return frames, {"source": source, "code_hashes": code_hashes()}


def verify_sources(provenance):
    supplement.verify_sources(provenance["source"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Criteria implementation changed; create a new version")


def stage_evidence(inputs, validation):
    durations = inputs["stage_durations.csv"]
    complete = durations.loc[durations.stage.isin(supplement.STAGES)].groupby("order_id").duration_eligible.agg(["size", "sum"])
    ids = complete.index[complete["size"].eq(3) & complete["sum"].eq(3)]
    eligible = validation.loc[validation.performance_eligible & validation.post_cutoff_milestones.eq(0)
                              & validation.order_id.isin(ids)].copy()
    reference, cutoff = supplement.stage_references(inputs)
    stages = supplement.compare_stages(eligible, durations, reference)
    stages["context_warning_assessable"] = (stages.reference_distance_matched
                                           & stages.reference_n.ge(supplement.protocol()["minimum_reference_orders"]))
    stages["global_empirical_percentile"] = np.nan
    stages["global_reference_n"] = 0
    for name in supplement.STAGES:
        peers = np.sort(reference.loc[reference.stage.eq(name), "duration_days"].to_numpy())
        if not len(peers):
            raise ValueError("Missing global train reference")
        mask = stages.stage.eq(name)
        stages.loc[mask, "global_empirical_percentile"] = np.searchsorted(peers, stages.loc[mask, "duration_days"], side="right")/len(peers)
        stages.loc[mask, "global_reference_n"] = len(peers)
    return stages, eligible, cutoff


def long_flags(stages, column, percentile, index):
    return (stages.duration_days.gt(0) & stages[column].gt(percentile)).groupby(stages.order_id).any().reindex(index)


def compare_thresholds(stages, eligible):
    available = stages.groupby("order_id").context_warning_assessable.agg(["size", "sum"])
    common = eligible.loc[eligible.order_id.isin(available.index[available["size"].eq(3) & available["sum"].eq(3)])].set_index("order_id")
    selected = stages.loc[stages.order_id.isin(common.index)]
    results, differences = [], []
    for q in protocol()["sensitivity_percentiles"]:
        global_flag = long_flags(selected, "global_empirical_percentile", q, common.index)
        context_flag = long_flags(selected, "empirical_percentile", q, common.index)
        for method, flag in [("global_ecdf", global_flag), ("context_ecdf", context_flag)]:
            late = common.performance_state.eq("late")
            results.append({"method": method, "percentile": q, "common_orders": len(common),
                            "warning_orders": int(flag.sum()), "warning_fraction": float(flag.mean()) if len(flag) else np.nan,
                            "late_promise_orders": int(late.sum()), "late_promise_with_warning": int((late & flag).sum()),
                            "late_promise_without_warning": int((late & ~flag).sum()),
                            "meets_promise_with_warning": int((~late & flag).sum()),
                            "context_only_warnings": int((context_flag & ~global_flag).sum()),
                            "global_only_warnings": int((global_flag & ~context_flag).sum()),
                            "interpretation": "observed_overlap_not_anomaly_accuracy"})
        for oid in common.index[global_flag.ne(context_flag)]:
            differences.append({"order_id": oid, "percentile": q, "global_warning": bool(global_flag.loc[oid]),
                                "context_warning": bool(context_flag.loc[oid]), "performance_state": common.loc[oid, "performance_state"],
                                "correct_detector": "unresolved"})
    columns = ["order_id", "percentile", "global_warning", "context_warning", "performance_state", "correct_detector"]
    return pd.DataFrame(results), pd.DataFrame(differences, columns=columns)


def compare_saved_models(inputs, stages, eligible):
    summary, models = supplement.model_comparisons(eligible, inputs["delivery_model_evidence.csv"], stages)
    common_ids = summary.loc[summary.complete_saved_comparison, "order_id"]
    common = eligible.loc[eligible.order_id.isin(common_ids)].set_index("order_id")
    models = models.loc[models.order_id.isin(common_ids) & models.outcome_available_for_saved_comparison]
    results = []
    for name in sorted(supplement.EXPECTED_MODELS):
        group = models.loc[models.model.eq(name)].set_index("order_id").reindex(common.index)
        if len(group) and group.long_warning.isna().any():
            raise ValueError("Saved comparison must have the same evaluated cases")
        late = common.performance_state.eq("late")
        warning = group.long_warning.astype(bool)
        results.append({"model": name, "common_orders": len(group), "long_warning_orders": int(warning.sum()),
                        "short_warning_orders": int(group.short_warning.sum()),
                        "late_promise_orders": int(late.sum()), "late_promise_with_long_warning": int((late & warning).sum()),
                        "late_promise_without_long_warning": int((late & ~warning).sum()),
                        "meets_promise_with_long_warning": int((~late & warning).sum()),
                        "selected_as_best_detector": False, "interpretation": "historical_same_target_overlap_not_ground_truth"})
    return pd.DataFrame(results), summary


def policy_decisions(inputs, validation, stages, model_summary):
    result = validation.copy()
    context = inputs["case_evidence.csv"][["order_id", "payment_minus_items_and_freight_minor_units"]]
    result = result.merge(context, on="order_id", validate="one_to_one").merge(model_summary, on="order_id", how="left", validate="one_to_one")
    result["complete_saved_comparison"] = result.complete_saved_comparison.astype("boolean")
    result["source_quality_status"] = np.select(
        [result.quality_conflict, result[performance.TIMES].isna().any(axis=1)],
        ["recorded_conflict_requires_verification", "incomplete_at_observation_cutoff"], default="no_conflict_in_executed_timing_checks")
    difference = result.payment_minus_items_and_freight_minor_units
    result["payment_reconciliation"] = np.select([difference.isna(), difference.ne(0).fillna(False)],
                                                 ["not_assessable", "difference_requires_verification"], default="matched_exactly")
    ready = stages.groupby("order_id").context_warning_assessable.agg(["size", "sum"])
    full_ids = ready.index[ready["size"].eq(3) & ready["sum"].eq(3)]
    result["context_timing_assessable"] = result.order_id.isin(full_ids)
    for label, predicate in [("long", stages.empirical_percentile.gt(protocol()["working_upper_percentile"])),
                             ("short", stages.empirical_percentile.lt(protocol()["working_lower_percentile"]))]:
        # Retain observable warnings per stage even when another stage lacks context.
        flag = predicate & stages.duration_days.gt(0) & stages.context_warning_assessable
        by_order = flag.groupby(stages.order_id).any()
        any_available = stages.groupby("order_id").context_warning_assessable.any()
        result[f"context_{label}_warning"] = result.order_id.map(by_order).astype("boolean")
        available = result.order_id.map(any_available).astype("boolean").fillna(False)
        result.loc[~available, f"context_{label}_warning"] = pd.NA
    result["timing_signal_status"] = "not_assessable"
    any_warning = (result.context_long_warning | result.context_short_warning).fillna(False)
    result.loc[result.context_timing_assessable, "timing_signal_status"] = "no_warning_under_working_reference"
    result.loc[result.context_timing_assessable & any_warning, "timing_signal_status"] = "statistical_warning_not_confirmed_anomaly"
    result.loc[~result.context_timing_assessable & any_warning, "timing_signal_status"] = "partial_context_warning_other_stages_unassessed"
    result["broad_anomaly_status"] = "unresolved"
    result["cause_or_seller_fault"] = "unresolved"
    result["human_reference_label"] = pd.Series(pd.NA, index=result.index, dtype="string")
    result["policy_version"] = VERSION
    return result


def criteria_matrix(decisions):
    same_day = decisions.performance_eligible & decisions.promise_delta_calendar_days.eq(0).fillna(False)
    midnight = decisions[performance.ESTIMATE].notna() & decisions[performance.ESTIMATE].eq(decisions[performance.ESTIMATE].dt.normalize())
    rows = [
        ("C01", "Ngày hẹn", "Chọn ngày lịch, không so giờ sau 00:00", int(same_day.sum()),
         "order_delivered_customer_date; order_estimated_delivery_date; promise_delta_calendar_days", "Chỉ xác nhận đáp ứng ngày hẹn, không phải nhãn bất thường"),
        ("C02", "Chất lượng mốc", "Giữ riêng mâu thuẫn trước khi diễn giải thời lượng", int(decisions.quality_conflict.sum()),
         "visible_chronology_inversions; recorded_status_delivery_conflict; promise_before_purchase_date", "Không xác định người nhập sai hoặc nguyên nhân"),
        ("C03", "Thiếu dữ liệu", "Không thay thiếu bằng 0 hoặc bình thường", int(decisions.source_quality_status.eq("incomplete_at_observation_cutoff").sum()),
         "actual_milestones; visible_before_exclusive", "Phân biệt dữ liệu thiếu với kết quả nằm sau mốc quan sát"),
        ("C04", "Giao sớm", "Thông tin hiệu suất; không kết luận toàn đơn bình thường", int(decisions.performance_state.eq("early").sum()),
         "performance_state; stage_evidence.csv", "Công đoạn dài vẫn có thể xuất hiện trong đơn giao sớm"),
        ("C05", "Thời gian theo bối cảnh", "Chọn tách cảnh báo có tham chiếu và phần chưa chấm được", int(decisions.context_timing_assessable.sum()),
         "stage_evidence.csv; reference_n; reference_context_json", "Bối cảnh chưa đầy đủ; 95% là mặc định mô tả, không chứng minh tối ưu"),
        ("C06", "Thời gian ngắn dương", "Chỉ cảnh báo thống kê, không tự gán lỗi hay loại dữ liệu", int(decisions.context_short_warning.fillna(False).sum()),
         "duration_days; empirical_percentile", "Không có xác suất bất thường đã hiệu chuẩn"),
        ("C07", "Đối chiếu tiền", "Giữ chênh lệch chính xác và yêu cầu xác minh", int(decisions.payment_reconciliation.eq("difference_requires_verification").sum()),
         "payment_minus_items_and_freight_minor_units", "Không tự sửa số tiền, suy ra refund/discount hay gian lận"),
        ("C08", "Đồng thuận mô hình", "So sánh riêng; không lấy đa số làm nhãn chuẩn", int(decisions.complete_saved_comparison.fillna(False).sum()),
         "saved_model_comparison.csv; saved_configurations_evaluated", "Ít cảnh báo hoặc khớp ngày hẹn không chứng minh detector tốt nhất"),
        ("C09", "Nhãn và chọn mô hình", "Chưa chọn detector khi không có reference độc lập", len(decisions),
         "broad_anomaly_status; human_reference_label", "Chính sách này không tạo ground truth cho chính nó"),
    ]
    matrix = pd.DataFrame(rows, columns=["criterion", "topic", "selected_interpretation", "relevant_orders", "evidence_fields", "limit"])
    matrix["count_scope"] = "guarded_validation; rows_are_overlapping_not_additive"
    return matrix, int(midnight.sum())


def check_contract(decisions, stages):
    invalid = decisions.quality_conflict
    late = decisions.performance_eligible & decisions.promise_delta_calendar_days.gt(0).fillna(False)
    expected_late = decisions.performance_state.eq("late")
    checks = {
        "calendar_late_matches_recorded_date_difference": int(late.ne(expected_late).sum()),
        "conflict_not_scored_as_valid_context": int((invalid & decisions.context_timing_assessable).sum()),
        "missing_milestones_not_fully_scored": int((decisions[performance.TIMES].isna().any(axis=1) & decisions.context_timing_assessable).sum()),
        "no_broad_normal_or_anomaly_label_invented": int(decisions.broad_anomaly_status.ne("unresolved").sum()),
        "no_human_reference_labels_fabricated": int(decisions.human_reference_label.notna().sum()),
        "no_causal_or_seller_fault_claim": int(decisions.cause_or_seller_fault.ne("unresolved").sum()),
        "duration_nonnegative_in_comparison": int(stages.duration_days.lt(0).sum()),
        "context_reference_has_support": int((stages.context_warning_assessable & stages.reference_n.lt(supplement.protocol()["minimum_reference_orders"])).sum()),
    }
    result = pd.DataFrame([{"check": key, "violations": value, "passed": value == 0,
                            "meaning": "software_semantics_not_business_accuracy"} for key, value in checks.items()])
    if not result.passed.all():
        raise ValueError("Evidence policy violates its semantic contract")
    return result


def build_tables(inputs):
    validation = inputs["order_performance.csv"].loc[lambda x: x.split.eq("validation")].sort_values("order_id").reset_index(drop=True)
    if validation.empty:
        raise ValueError("No guarded validation orders")
    stages, eligible, cutoff = stage_evidence(inputs, validation)
    thresholds, differences = compare_thresholds(stages, eligible)
    models, model_summary = compare_saved_models(inputs, stages, eligible)
    decisions = policy_decisions(inputs, validation, stages, model_summary)
    criteria, midnight = criteria_matrix(decisions)
    checks = check_contract(decisions, stages)
    calendar_eligible = decisions.performance_eligible
    timestamp_late = decisions[performance.TIMES[3]].gt(decisions[performance.ESTIMATE]) & calendar_eligible
    date_late = decisions.performance_state.eq("late")
    summary = {"version": VERSION, "selected_policy": protocol()["selected_policy"],
               "selection_scope": protocol()["decision_scope"], "selected_detector": None, "optimized_detector_threshold": None,
               "validation_orders": len(validation), "calendar_comparable_orders": int(calendar_eligible.sum()),
               "complete_timing_candidates": len(eligible), "stage_evidence_rows": len(stages), "train_reference_end_before": str(cutoff),
               "estimated_dates_at_midnight": midnight, "timestamp_late_orders": int(timestamp_late.sum()),
               "calendar_late_orders": int(date_late.sum()), "same_day_misclassified_by_timestamp_rule": int((timestamp_late & ~date_late).sum()),
               "context_assessable_orders": int(decisions.context_timing_assessable.sum()),
               "context_long_warning_orders": int(decisions.context_long_warning.fillna(False).sum()),
               "context_short_warning_orders": int(decisions.context_short_warning.fillna(False).sum()),
               "context_unassessable_or_partial_orders": int((~decisions.context_timing_assessable).sum()),
               "timing_signal_counts": {str(k): int(v) for k, v in decisions.timing_signal_status.value_counts().items()},
               "payment_reconciliation_counts": {str(k): int(v) for k, v in decisions.payment_reconciliation.value_counts().items()},
               "sensitivity_common_orders": int(thresholds.common_orders.iloc[0]), "saved_model_common_orders": int(models.common_orders.iloc[0]),
               "saved_configurations_compared": len(models), "criterion_decisions": len(criteria), "contract_violations": int(checks.violations.sum()),
               "business_accuracy_known": False, "reference_labels_created": 0, "model_trained": False,
               "working_warning_policy_created": True, "working_warning_thresholds_optimized": False,
               "detector_threshold_changed": False, "test_scored": False, "split_activated": False,
               "policy_is_unbiased_or_globally_optimal": False, "near_real_time_validated": False}
    return {"criteria_decisions.csv": criteria, "case_decisions.csv": decisions, "stage_evidence.csv": stages,
            "threshold_sensitivity.csv": thresholds, "threshold_disagreements.csv": differences,
            "saved_model_comparison.csv": models, "contract_checks.csv": checks}, summary


def render_report(frames, summary):
    lines = ["# Kết quả so sánh tiêu chí và bằng chứng", "",
             "## Đã chọn gì?", "",
             "**Chọn tách ba lớp: chất lượng nguồn, kết quả giao hàng theo ngày hẹn và cảnh báo thống kê có tham chiếu.** "
             "Đây là lựa chọn chính sách diễn giải cho phát triển, chưa phải detector chính xác nhất. "
             "Không có cơ sở kết luận không sai lệch hoặc không báo nhầm/bỏ sót.", "",
             f'Phạm vi: {summary["validation_orders"]} đơn validation được bảo vệ, không dùng riêng nhóm 40 đơn. '
             f'Có {summary["calendar_comparable_orders"]} đơn so ngày hẹn được và {summary["complete_timing_candidates"]} đơn đủ ba công đoạn. '
             "Dữ liệu train làm tham chiếu, không đọc/chấm đặc trưng test cuối; giữ nguyên bộ 120 đơn pilot.", "",
             "## Sai khác xác định được trực tiếp", "",
             f'- So timestamp với mốc hẹn 00:00 gắn trễ {summary["timestamp_late_orders"]} đơn; '
             f'so ngày lịch có {summary["calendar_late_orders"]} đơn trễ. '
             f'**{summary["same_day_misclassified_by_timestamp_rule"]} đơn giao trong ngày hẹn** bị quy tắc timestamp gọi trễ. '
             "Giữ cách so ngày lịch đã có theo quy ước thống nhất. Timestamp là phương án đối chứng, không phải kết luận "
             "pipeline hiện tại đang gán sai các đơn này. Đây là sai khác của tiêu chí trễ hẹn, không phải số báo nhầm của detector bất thường.",
             f'- {summary["context_assessable_orders"]} đơn đủ tham chiếu bối cảnh cho ba công đoạn; '
             f'{summary["context_unassessable_or_partial_orders"]} đơn chưa chấm đầy đủ. Không chuyển thiếu kết quả thành bình thường.',
             f'- Mặc định mô tả 95%/5% tạo dấu hiệu dài ở {summary["context_long_warning_orders"]} đơn, '
             f'ngắn dương ở {summary["context_short_warning_orders"]} đơn. Hai nhóm có thể chồng lấn, không phải nhãn bất thường.', "",
             "## Độ nhạy ngưỡng", "",
             "Cùng tập đơn đủ tham chiếu cho cả hai cách. Cờ xét bất kỳ trong ba công đoạn; phân vị là thứ hạng thực nghiệm "
             "trong train, không phải xác suất. Không chọn ngưỡng bằng số cảnh báo thấp nhất.", "",
             "| Cách | Phân vị | Cùng số đơn | Cảnh báo dài | Trễ hẹn có cảnh báo | Trễ hẹn không cảnh báo | Đáp ứng hẹn có cảnh báo |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for row in frames["threshold_sensitivity.csv"].itertuples(index=False):
        lines.append(f"| {row.method} | {row.percentile:.3f} | {row.common_orders} | {row.warning_orders} | "
                     f"{row.late_promise_with_warning} | {row.late_promise_without_warning} | {row.meets_promise_with_warning} |")
    lines += ["", "Các giao cắt trên **không phải TP/FP/FN về bất thường**: giao trễ không chứng minh tắc nghẽn; "
              "công đoạn dài trong đơn giao sớm có thể vẫn là dấu hiệu cần xem xét. Mức 95% được giữ làm mặc định mô tả, "
              "không được công bố là mức chính xác nhất.", "", "## Các cấu hình đã lưu", "",
              "12 cấu hình cùng mục tiêu thời gian vận chuyển, không phải 12 họ thuật toán. Kết quả lịch sử chỉ so trên cùng "
              "các đơn có đủ kết quả ở cả 12 cấu hình. Không dùng MAE hoặc độ khớp ngày hẹn để chọn detector chính của đề tài.", "",
              "| Cấu hình | Cùng số đơn | Cảnh báo dài | Trễ hẹn có cảnh báo | Trễ hẹn không cảnh báo | Đáp ứng hẹn có cảnh báo |",
              "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in frames["saved_model_comparison.csv"].itertuples(index=False):
        lines.append(f"| {row.model} | {row.common_orders} | {row.long_warning_orders} | {row.late_promise_with_long_warning} | "
                     f"{row.late_promise_without_long_warning} | {row.meets_promise_with_long_warning} |")
    lines += ["", "## Tiêu chí đã giải quyết", ""]
    for row in frames["criteria_decisions.csv"].itertuples(index=False):
        lines.append(f"- **{row.criterion} - {row.topic}:** {row.selected_interpretation}. Giới hạn: {row.limit}.")
    lines += ["", "## Điều chưa thể suy ra", "",
              "Không có bộ nhãn độc lập được tạo trong bước này. Tất cả nhãn bất thường rộng giữ chưa kết luận. "
              "Do đó không tính Precision/Recall/F1 hoặc chọn IF/LOF/OCSVM là chính xác nhất. Không dùng số cảnh báo, "
              "đồng thuận hoặc chính bộ quy tắc này làm đáp án để tự chấm detector.", "",
              "Nhóm train chưa được xác nhận bình thường, có giới hạn mốc quan sát và không kiểm soát đầy đủ mùa vụ, "
              "phương thức thanh toán, kích thước hàng, hãng vận chuyển hay cấp dịch vụ. Phân tích hồi cứu không chứng minh near real-time. "
              "Chính sách được chọn vì phù hợp quy ước và không đánh đồng bằng chứng, không phải vì đã chứng minh ít sai lệch nhất.", "",
              f'Kiểm tra hợp đồng phần mềm: {summary["contract_violations"]} vi phạm trong các điều kiện đã kiểm tra. '
              "**Không được diễn giải con số này là 0 sai sót phát hiện bất thường.**", "",
              "Bước tiếp theo cho đánh giá detector: xây dựng kết luận độc lập cho từng phạm vi từ bằng chứng nghiệp vụ/nguồn "
              "đã xác minh, hoặc công bố rõ một bài toán đối chiếu quy tắc hẹp hơn. Không tự đổi đề tài thành dự báo thời gian. "
              "Nếu chưa có căn cứ đó, vẫn có thể dùng chính sách hiện tại để hiển thị dấu hiệu và giới hạn trên BI.", "",
              "Nguồn phương pháp: [scikit-learn: outlier detection](https://scikit-learn.org/stable/modules/outlier_detection.html), "
              "[data leakage](https://scikit-learn.org/stable/common_pitfalls.html#data-leakage). "
              "Đây không phải bằng chứng xác nhận từng đơn Olist.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = performance.destination_guard(output, {"inspection": provenance["source"]["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite criteria snapshot")
    if set(frames) != TABLES:
        raise ValueError("Incomplete criteria tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".evidence-criteria-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_tieu_chi.md").write_text(render_report(frames, summary))
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
    expected = TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_tieu_chi.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES or set(manifest.get("output_hashes", {})) != expected):
        raise ValueError("Invalid criteria manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Criteria protocol/summary mismatch")
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
        parser.exit(2, f"Evidence criteria stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
