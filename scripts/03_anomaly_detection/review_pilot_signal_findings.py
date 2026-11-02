"""Confront pilot signals with their evidence; never create independent labels."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import pandas as pd

import analyze_bottleneck_context as context
import prepare_pilot_stage_evidence as evidence

ROOT, review, shared = evidence.ROOT, evidence.review, evidence.shared
VERSION = "pilot_signal_findings_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_pilot_signal_findings_v1"
TABLES = {"case_findings.csv", "stage_margins.csv", "group_summary.csv", "verification_actions.csv"}
GROUPS = {
    "primary_signal": "Có tín hiệu chính cần kiểm tra",
    "no_primary_signal": "Đủ điều kiện, chưa có tín hiệu chính theo quy tắc",
    "insufficient_evidence": "Chưa đủ bằng chứng đánh giá",
    "proposed_test_hold": "Giữ riêng theo ranh giới test đề xuất",
}


def protocol():
    return {"version": VERSION, "mode": "machine_assisted_policy_confrontation_not_human_adjudication",
            "grouping": "mutually_exclusive_primary_else_complete_no_primary_else_unknown; held_orders_separate",
            "margin": "actual_duration_minus_frozen_upper_boundary_in_elapsed_days; no_tolerance_or_new_threshold",
            "near_boundary_examples": "three_smallest_absolute_margins_on_each_side_for_inspection_only_not_new_sample",
            "negative_scope": "no_primary_signal_under_current_rule_not_confirmed_normal_or_no_problem",
            "short_duration": "secondary_note_not_primary_anomaly",
            "payment": "separate_exact_source_reconciliation_not_fraud_or_data_error_label",
            "actions": "conditional_verification_checklists_only_no_intervention_executed",
            "unknown": "never_convert_missing_or_unassessed_to_negative",
            "reference": "prior_analyst_assisted_outputs_not_independent_ground_truth",
            "source_locators": "relative_to_source_directory_bound_in_manifest_provenance",
            "selection": "no_model_selection_or_independent_accuracy_metrics_in_this_step"}


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), Path(context.__file__), Path(evidence.__file__), evidence.explanation.DECISIONS]}


def verify_sources(provenance):
    source = Path(provenance["source_directory"])
    if review.digest(source/"manifest.json") != provenance["source_manifest_sha256"]:
        raise ValueError("Pilot evidence source changed")
    manifest = evidence.load_snapshot(source)
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Findings implementation or agreed decisions changed; use a new version")
    return manifest


def load_inputs(source=evidence.DEFAULT_OUTPUT):
    source = Path(source).resolve()
    provenance = {"source_directory": str(source), "source_manifest_sha256": review.digest(source/"manifest.json"),
                  "code_hashes": code_hashes()}
    manifest = verify_sources(provenance)
    frames = {name: shared.read_table(source, "analyst_only/"+name, manifest) for name in evidence.TABLES}
    return frames, provenance


def validate_inputs(frames):
    ledger, orders, stages = [frames[name] for name in ["scope_ledger.csv", "order_explanations.csv", "stage_comparisons.csv"]]
    for name, frame, keys in [("scope", ledger, ["review_id"]), ("orders", orders, ["order_id"]),
                              ("stages", stages, ["order_id", "stage"])]:
        shared.business.require_key(frame, keys, name)
    if (not ledger.order_id.is_unique or not orders.review_id.is_unique
            or set(ledger.loc[ledger.comparison_generated, "order_id"]) != set(orders.order_id)
            or ledger.anomaly_label.notna().any() or ledger.human_review_performed.any() or ledger.independent_reference.any()):
        raise ValueError("Unexpected scope or nonblank source assessment")
    ids = ledger.set_index("order_id").review_id
    for frame in [orders, stages]:
        if not frame.review_id.eq(frame.order_id.map(ids)).all():
            raise ValueError("Review pseudonym/source order mismatch")
    evidence.explanation.validate_inputs({"order_signals.csv": orders, "stage_signals.csv": stages})
    regenerated_stages = evidence.explanation.stage_comparisons(stages).set_index(["order_id", "stage"])
    current_stages = stages.set_index(["order_id", "stage"]).sort_index()
    fields = ["speed_comparable", "difference_from_reference_days", "comparison_direction"]
    pd.testing.assert_frame_equal(current_stages[fields], regenerated_stages.loc[current_stages.index, fields], check_exact=True)
    regenerated_orders = evidence.explanation.order_explanations(orders, stages).set_index("order_id")
    current = orders.set_index("order_id")
    fields = ["faster_stage_count", "slower_stage_count", "faster_stage_observed", "speed_comparison_complete",
              "early_delivery_explanation_status", "order_explanation_vi"]
    pd.testing.assert_frame_equal(current[fields], regenerated_orders.loc[current.index, fields],
                                  check_dtype=False, check_index_type=False, check_exact=True)
    promised, actual = orders[evidence.performance.ESTIMATE], orders[evidence.performance.TIMES[3]]
    delta = (actual.dt.normalize()-promised.dt.normalize()).dt.days.astype("Int64")
    pd.testing.assert_series_equal(delta, orders.promise_delta_calendar_days, check_names=False, check_exact=True)
    valid = orders.performance_eligible
    if not orders.loc[valid, "calendar_late_signal"].eq(delta.loc[valid].gt(0)).all():
        raise ValueError("Calendar signal is inconsistent with observed dates")
    scored = stages.loc[stages.assessable]
    expected = scored.duration_days.gt(0) & scored.empirical_percentile.gt(evidence.signals.protocol()["working_upper_percentile"])
    if (not scored.long_stage_warning.eq(expected).all()
            or scored.reference_n.lt(evidence.supplement.protocol()["minimum_reference_orders"]).any()
            or not scored.reference_context_json.map(lambda value: "distance_band" in json.loads(value)).all()):
        raise ValueError("Reference evidence differs from the unchanged rule")


def build_tables(inputs):
    validate_inputs(inputs)
    orders = inputs["order_explanations.csv"].set_index("review_id")
    stages = inputs["stage_comparisons.csv"]
    findings, actions = [], []

    def action(rid, scope, code, observation, needed, proposed, locator):
        actions.append({"review_id": rid, "scope": scope, "action_code": code, "observation_vi": observation,
                        "needed_evidence_vi": needed, "proposed_check_vi": proposed, "source_locator": locator,
                        "cause_status": "not_established", "action_status": "proposed_not_executed",
                        "independent_reference": False})

    for row in inputs["scope_ledger.csv"].itertuples(index=False):
        out = {"review_id": row.review_id, "order_id": row.order_id, "finding_group": "proposed_test_hold",
               "calendar_state": pd.NA, "days_late": pd.NA, "calendar_late_signal": pd.NA,
               "long_stage_warning": pd.NA, "short_stage_note": pd.NA, "slower_stage_count": pd.NA,
               "long_stages_json": "[]", "unassessed_stages_json": "[]", "payment_difference_minor_units": pd.NA,
               "criteria_status": "identity_and_scope_only", "independent_judgment_status": "not_provided_by_this_analysis",
               "human_label": pd.NA, "anomaly_subtype": pd.NA, "independent_reference": False,
               "promise_basis_status": row.promise_basis_status, "analyst_conclusion_vi": "Giữ riêng; không chấm kết quả hoặc công đoạn.",
               "source_locator": f"analyst_only/scope_ledger.csv:review_id={row.review_id}"}
        if row.comparison_generated:
            case = orders.loc[row.review_id]
            group = {"signal_present": "primary_signal", "no_primary_signal_under_policy": "no_primary_signal",
                     "incomplete_assessment_no_primary_signal_observed": "insufficient_evidence"}[case.primary_attention_status]
            selected = stages.loc[stages.review_id.eq(row.review_id)]
            long = selected.loc[selected.long_stage_warning.fillna(False)]
            unavailable = selected.loc[~selected.assessable]
            locator = f"analyst_only/order_explanations.csv:review_id={row.review_id}"
            interpretation = {
                "primary_signal": "Có bằng chứng cho tín hiệu theo quy tắc đã công bố; chưa xác nhận bất thường nghiệp vụ hoặc nguyên nhân.",
                "no_primary_signal": "Không có trễ hẹn/công đoạn vượt ngưỡng dài trong phạm vi đã xét; không gán bình thường toàn diện.",
                "insufficient_evidence": "Chưa đủ bằng chứng; không gán bình thường hoặc không cảnh báo cho phần chưa đánh giá.",
            }[group]
            out.update(finding_group=group, calendar_state=case.performance_state, days_late=case.days_late,
                       calendar_late_signal=case.calendar_late_signal, long_stage_warning=case.long_stage_warning,
                       short_stage_note=case.short_stage_note, slower_stage_count=case.slower_stage_count,
                       long_stages_json=json.dumps(long.stage.tolist()), unassessed_stages_json=json.dumps(unavailable.stage.tolist()),
                       payment_difference_minor_units=case.payment_minus_items_and_freight_minor_units,
                       criteria_status="consistent_with_declared_policy_not_independent_accuracy", analyst_conclusion_vi=interpretation,
                       source_locator=locator)
            if pd.notna(case.calendar_late_signal) and case.calendar_late_signal:
                action(row.review_id, "delivery_promise", "verify_late_promise",
                       f"Giao sau ngày hẹn {case.days_late} ngày lịch; chưa xác định công đoạn gây trễ.",
                       "Cơ chế đặt hẹn và lịch sử cam kết gốc, mức dịch vụ, các mốc thực tế.",
                       "Giữ tín hiệu trễ; kiểm tra cam kết và các mốc, không tự sửa ngày hẹn để xóa trễ.", locator)
            for stage in long.itertuples(index=False):
                _, needed, proposed, _ = context.CHECKLISTS[stage.stage]
                action(row.review_id, stage.stage, "verify_long_stage",
                       f"{stage.duration_days:.6f} ngày >= ngưỡng {stage.upper_boundary_days_inclusive:.6f} ngày; "
                       f"nhóm train có {stage.reference_n} đơn. Đây không phải kế hoạch gốc hoặc nguyên nhân đã xác minh.",
                       needed, proposed, f"analyst_only/stage_comparisons.csv:review_id={row.review_id};stage={stage.stage}")
            if len(unavailable):
                reasons = sorted(set(unavailable.assessment_status))
                action(row.review_id, "evidence_coverage", "resolve_missing_evidence", "; ".join(reasons),
                       "Mốc thời gian trong cửa sổ quan sát hoặc tham chiếu địa lý đạt chất lượng và đủ số lượng, tùy lý do.",
                       "Giữ phần chưa xác định. Không nhập ngày giả, dùng kết quả tương lai hoặc hạ yêu cầu tham chiếu để ép chấm.", locator)
            amount = case.payment_minus_items_and_freight_minor_units
            if pd.notna(amount) and amount != 0:
                action(row.review_id, "payment", "verify_payment_reconciliation",
                       f"Chênh thanh toán so với giá hàng + vận chuyển: {int(amount)} centavos (1/100 BRL).",
                       "Chứng từ/định nghĩa giá trị thanh toán và các điều chỉnh thực sự được ghi nhận.",
                       "Giữ chênh lệch; chưa quy cho trả góp, làm tròn, nhập sai hay gian lận.", locator)
        findings.append(out)
    findings = pd.DataFrame(findings)
    for field in ["calendar_late_signal", "long_stage_warning", "short_stage_note"]:
        findings[field] = findings[field].astype("boolean")
    for field in ["days_late", "slower_stage_count", "payment_difference_minor_units"]:
        findings[field] = findings[field].astype("Int64")
    for field in ["human_label", "anomaly_subtype", "calendar_state"]:
        findings[field] = findings[field].astype("string")
    margins = stages[["review_id", "order_id", "stage", "assessment_status", "assessable", "duration_days", "reference_n",
                      "reference_group_id", "reference_median_days", "upper_boundary_days_inclusive", "empirical_percentile",
                      "long_stage_warning", "comparison_direction", "difference_from_reference_days"]].copy()
    margins["signed_margin_to_upper_days"] = (margins.duration_days-margins.upper_boundary_days_inclusive).where(margins.assessable)
    margins["margin_interpretation"] = "descriptive_distance_to_fixed_boundary_not_confidence_or_probability"
    counts = findings.finding_group.value_counts()
    groups = pd.DataFrame([{"finding_group": group, "meaning_vi": meaning, "orders": int(counts.get(group, 0))}
                           for group, meaning in GROUPS.items()])
    late_only = orders.calendar_late_signal.eq(True) & orders.long_stage_warning.eq(False)
    no_primary = orders.primary_attention_status.eq("no_primary_signal_under_policy")
    summary = {"version": VERSION, "orders": len(findings), "active_orders": len(orders), "group_counts": counts.to_dict(),
               "stage_rows": len(margins), "assessed_stage_rows": int(margins.assessable.sum()),
               "late_only_ids": orders.index[late_only.fillna(False)].tolist(),
               "no_primary_but_slower_stage_ids": orders.index[no_primary & orders.slower_stage_count.gt(0)].tolist(),
               "short_note_without_primary_ids": orders.index[no_primary & orders.short_stage_note.fillna(False)].tolist(),
               "slow_but_not_long_stage_rows": int((stages.comparison_direction.eq("slower") & stages.long_stage_warning.eq(False).fillna(False)).sum()),
               "verification_action_rows": len(actions), "action_counts": pd.Series([a["action_code"] for a in actions]).value_counts().to_dict(),
               "software_policy_check": "passed_not_business_accuracy", "source_promise_basis_unverified": len(findings),
               "independent_reference_ready": False, "human_labels_created": 0, "accuracy_computed": False,
               "auc_computed": False, "threshold_changed": False, "model_trained": False, "model_selected": False,
               "test_scored": False, "new_cases_sampled": False, "intervention_executed": False,
               "selection_gate": "independent_signal_judgments_and_evaluation_protocol_still_required"}
    return {"case_findings.csv": findings, "stage_margins.csv": margins, "group_summary.csv": groups,
            "verification_actions.csv": pd.DataFrame(actions)}, summary


def report(frames, summary):
    s = summary
    lines = ["# Đối chiếu tín hiệu với bằng chứng của 120 đơn", "",
             "Đây là rà soát bằng máy theo quy ước đã thống nhất, không phải quyết định của người rà soát độc lập.", "",
             "## Các nhóm không trùng nhau", "", "| Nhóm | Số đơn |", "| --- | ---: |"]
    lines += [f"| {r.meaning_vi} | {r.orders} |" for r in frames["group_summary.csv"].itertuples(index=False)]
    lines += ["", "Tín hiệu chính = trễ ngày hẹn hoặc có công đoạn vượt ngưỡng dài. 73 đơn không có tín hiệu chính "
              "không được đổi thành nhãn bình thường. 14 đơn thiếu bằng chứng không phải 14 dự đoán âm. "
              "Pilot lấy mẫu theo tháng và cờ mô hình cũ, không dùng tỷ lệ thô để suy ra tỷ lệ toàn Olist.", "",
              "## Những đối chiếu quan trọng", "",
              f"- Trễ ngày hẹn nhưng không công đoạn nào vượt ngưỡng dài: {', '.join(s['late_only_ids'])}. "
              "Giữ tín hiệu trễ hẹn, không tự chỉ định công đoạn gây trễ.",
              f"- Trong nhóm không có tín hiệu chính, {len(s['no_primary_but_slower_stage_ids'])} đơn vẫn có công đoạn chậm hơn trung vị. "
              "Đây không phải mâu thuẫn: dấu chênh lệch và vượt ngưỡng là hai tiêu chí khác nhau.",
              f"- Có {s['slow_but_not_long_stage_rows']} dòng công đoạn chậm hơn trung vị nhưng dưới ngưỡng dài.",
              f"- {len(s['short_note_without_primary_ids'])} đơn chỉ có ghi chú thời gian ngắn, không có tín hiệu chính: "
              f"{', '.join(s['short_note_without_primary_ids'])}. Không nâng ghi chú này thành anomaly.",
              "- BR0079 giao sớm 13 ngày nhưng khoảng duyệt-bàn giao vượt ngưỡng dài; cả ba công đoạn không nhanh hơn trung vị. "
              "Giữ cảnh báo, không ép giải thích giao sớm bằng một công đoạn nhanh.",
              "- BR0051 và BR0060 vẫn giữ chênh lệch tiền gốc; không tự gán nhãn nhập sai hoặc dị biệt hợp lệ.", "",
              "## Các ví dụ gần ranh giới", "",
              "Chọn ba công đoạn gần nhất ở mỗi phía chỉ để kiểm tra. Không đổi ngưỡng và không đặt thêm ngưỡng 'gần'. "
              "Khoảng cách tới ngưỡng không phải độ tin cậy hay xác suất đúng/sai.", "",
              "| Mã | Công đoạn | Thời lượng | Ngưỡng dài | Chênh (ngày) | Cờ dài |", "| --- | --- | ---: | ---: | ---: | --- |"]
    scored = frames["stage_margins.csv"].loc[lambda f: f.assessable]
    for flag in [True, False]:
        rows = scored.loc[scored.long_stage_warning.eq(flag)].copy()
        rows["distance"] = rows.signed_margin_to_upper_days.abs()
        for row in rows.sort_values(["distance", "review_id", "stage"]).head(3).itertuples(index=False):
            lines.append(f"| {row.review_id} | {evidence.signals.NAMES[row.stage]} | {row.duration_days:.6f} | "
                         f"{row.upper_boundary_days_inclusive:.6f} | {row.signed_margin_to_upper_days:.6f} | {'có' if flag else 'không'} |")
    lines += ["", "## Việc cần kiểm tra theo bằng chứng", "",
              f"Đã lập {s['verification_action_rows']} dòng đề xuất; một đơn có thể có nhiều việc, không phải số đơn bất thường.", ""]
    lines += [f"- `{code}`: {count} dòng." for code, count in s["action_counts"].items()]
    lines += ["", "Chi tiết trong analyst_only/verification_actions.csv. Checklist đề xuất được tái sử dụng từ phần phân tích bối cảnh; "
              "không lấy các giả thuyết làm nguyên nhân đã chứng minh. Chưa thực hiện can thiệp hay kết luận trách nhiệm seller.", "",
              "## Điều kiện để chọn mô hình", "",
              "Các phép tính và diễn giải nhất quán với quy ước hiện tại. Điều đó không chứng minh độ chính xác nghiệp vụ, "
              "không xác nhận ngưỡng tối ưu và không tạo nhãn độc lập. Không cần biết nguyên nhân gốc để đánh giá tính hữu ích của tín hiệu, "
              "nhưng phải có đánh giá thực sự với người thực hiện, bằng chứng và mức đã tiếp xúc kết quả được ghi nhận.", "",
              "Không cần chốt lại các quy ước đã đồng ý. Phần còn thiếu là đánh giá độc lập về tín hiệu, cùng quy tắc đánh giá/chọn mô hình "
              "và phạm vi mẫu phù hợp. Chạy thêm cùng quy tắc hoặc để AI tự xác nhận các cờ không thay thế được phần này. "
              "Cơ sở ngày hẹn vẫn chưa xác minh cho 120 đơn; không sửa ngày hẹn, loại đơn hay train lại để ép kết quả khớp.", "",
              "## Danh sách 120 mã", "", "| Mã | Nhóm | Trạng thái giao | Công đoạn dài | Kết luận trong phạm vi |",
              "| --- | --- | --- | --- | --- |"]
    for row in frames["case_findings.csv"].itertuples(index=False):
        state = "không chấm" if pd.isna(row.calendar_state) else evidence.audit.STATE_VI.get(row.calendar_state, row.calendar_state)
        stages = ", ".join(evidence.signals.NAMES[name] for name in json.loads(row.long_stages_json))
        if not stages:
            stages = "chưa đánh giá" if pd.isna(row.long_stage_warning) else "không vượt ngưỡng"
        lines.append(f"| {row.review_id} | {GROUPS[row.finding_group]} | {state} | {stages} | {row.analyst_conclusion_vi} |")
    return "\n".join(lines)+"\n"


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = evidence.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite pilot findings")
    if set(frames) != TABLES:
        raise ValueError("Incomplete findings output")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pilot-findings-", dir=output.parent) as temp:
        stage = Path(temp)/"snapshot"
        (stage/"analyst_only").mkdir(parents=True)
        schemas = {}
        for name, frame in frames.items():
            target = "analyst_only/"+name
            frame.to_csv(stage/target, index=False)
            schemas[target] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(stage, target, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(stage/"summary.json", summary)
        review.write_json(stage/"protocol.json", protocol())
        (stage/"bao_cao_doi_chieu.md").write_text(report(frames, summary))
        outputs = {str(p.relative_to(stage)): review.digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        review.write_json(stage/"manifest.json", {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                          "schemas": schemas, "summary": summary, "provenance": provenance, "output_hashes": outputs})
        load_snapshot(stage)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        stage.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    tables = {"analyst_only/"+name for name in TABLES}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != tables
            or set(manifest.get("output_hashes", {})) != tables | {"summary.json", "protocol.json", "bao_cao_doi_chieu.md"}):
        raise ValueError("Invalid pilot findings snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"summary.json") != manifest["summary"] or review.read_json(directory/"protocol.json") != protocol():
        raise ValueError("Findings summary/protocol changed")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if args.verify:
        result = load_snapshot(args.output)["summary"]
    else:
        inputs, provenance = load_inputs()
        frames, summary = build_tables(inputs)
        result = write_snapshot(frames, summary, provenance, args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
