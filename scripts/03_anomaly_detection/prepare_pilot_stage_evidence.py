"""Separate analyst-assisted pilot stage evidence using unchanged train references."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import audit_review_pilot as audit

review, shared, ROOT = audit.review, audit.shared, audit.ROOT
explanation = audit.explanation
signals = explanation.signals
performance, supplement = signals.performance, signals.supplement
VERSION = "pilot_stage_evidence_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_pilot_stage_evidence_v1"
TABLES = {"scope_ledger.csv", "order_explanations.csv", "stage_comparisons.csv", "reference_groups.csv", "signal_evidence.csv"}
CONTEXT = ["all_pairs_quality_eligible", "geography_quality_reasons", "screened_distance_max_km", "seller_states_json",
           "history_route", "single_seller_prior_observed_completed_orders", "item_count", "product_count"]


def protocol():
    return {"version": VERSION, "purpose": "analyst_assisted_evidence_not_blind_or_independent_labels",
            "source": "existing_120_real_olist_bed_bath_table_pilot_no_resampling",
            "eligible_scope": "old_validation_purchases_before_unactivated_proposed_test_boundary",
            "held_scope": "identity_and_scope_metadata_only_no_stage_or_outcome_scoring",
            "reference_policy": supplement.protocol(), "signal_policy": signals.protocol(),
            "explanation_policy": explanation.protocol(),
            "complete_case_policy": "unchanged_no_new_scoring_of_partially_observed_orders",
            "case_differences": "duration_minus_train_peer_median_separate_from_tail_warning",
            "promise_basis": "unverified_not_random_or_invalid",
            "reference_exposure": "statistics_and_rule_signals_are_now_generated_for_eligible_pilot_ids_in_this_package",
            "labels": "none_created_do_not_use_rule_outputs_as_independent_ground_truth",
            "population_inference": "original_pilot_stratified_by_month_and_model_flags_not_raw_population_prevalence",
            "privacy": "local_only_no_external_upload", "new_data": False,
            "limits": "retrospective_ZIP_distance_not_actual_route_no_full_item_carrier_season_control_no_causal_proof"}


def code_hashes():
    return {**signals.code_hashes(), **supplement.code_hashes(), **explanation.code_hashes(),
            **{str(p.relative_to(ROOT)): review.digest(p) for p in [Path(__file__), Path(performance.__file__), Path(audit.__file__)]}}


def verify_sources(provenance):
    for field in ["audit", "signals"]:
        source = provenance[field]
        if review.digest(Path(source["directory"])/"manifest.json") != source["manifest_sha256"]:
            raise ValueError(f"Frozen {field} binding changed")
    audit.load_snapshot(Path(provenance["audit"]["directory"]))
    signals.load_snapshot(Path(provenance["signals"]["directory"]))
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Pilot evidence implementation/policy changed; use a new version")


def load_inputs():
    saved_rows, saved_summary = audit.load_snapshot()
    pilot, _ = audit.load_inputs()
    if audit.build_audit(pilot) != (saved_rows, saved_summary):
        raise ValueError("Current pilot does not reproduce the frozen technical audit")
    signal_inputs, signal_sources = signals.load_inputs()
    manifest = review.read_json(Path(signal_sources["criteria_directory"])/"manifest.json")
    provenance = {field: {"directory": str(directory), "manifest_sha256": review.digest(directory/"manifest.json")}
                  for field, directory in [("audit", audit.DEFAULT_OUTPUT), ("signals", signals.DEFAULT_OUTPUT)]}
    provenance["code_hashes"] = code_hashes()
    verify_sources(provenance)
    return {"pilot": pilot, "audit_rows": saved_rows, "train_reference": signal_inputs["train_reference"],
            "reference_end_before": pd.Timestamp(manifest["summary"]["train_reference_end_before"])}, provenance


def validate_inputs(inputs):
    pilot, reference = inputs["pilot"], inputs["train_reference"]
    audit.readiness.validate_inputs(pilot)
    key = pilot["key"]
    if not pilot["blind"]["cases.csv"].product_category_name.eq("cama_mesa_banho").all():
        raise ValueError("Expected the original category-scoped pilot")
    if key.order_id.isin(pilot["explained_ids"]).any():
        raise ValueError("Pilot must remain separate from the public explanation cohort")
    if [r["review_id"] for r in inputs["audit_rows"]] != key.review_id.tolist():
        raise ValueError("Frozen audit and pilot IDs differ")
    shared.business.require_key(reference, ["order_id", "stage"], "train stage reference")
    if (reference.empty or not reference.split.eq("train").all() or not reference.duration_eligible.all()
            or reference.order_id.isin(key.order_id).any()
            or not reference.end_timestamp.lt(inputs["reference_end_before"]).all()
            or not reference.end_timestamp.lt(reference.visible_before_exclusive).all()
            or not reference.stage.isin(signals.STAGES).all()
            or key.order_purchase_timestamp.lt(inputs["reference_end_before"]).any()):
        raise ValueError("References must be earlier train observations, disjoint from all pilot IDs")


def build_tables(inputs):
    validate_inputs(inputs)
    pilot, reference = inputs["pilot"], inputs["train_reference"]
    key = pilot["key"][["review_id", "order_id"]]
    source = pilot["cases"].loc[pilot["cases"].order_id.isin(key.order_id)]
    cases = performance.order_performance(source, pilot["boundary"])
    expected = {r["order_id"] for r in inputs["audit_rows"] if r["scope"] == "pre_boundary_evidence_audit"}
    if set(cases.order_id) != expected:
        raise ValueError("Audited boundary scope differs")
    durations = performance.stage_durations(cases)
    complete = durations.loc[durations.stage.isin(signals.STAGES)].groupby("order_id").duration_eligible.agg(["size", "sum"])
    ids = complete.index[complete["size"].eq(3) & complete["sum"].eq(3)]
    eligible = cases.loc[cases.performance_eligible & cases.post_cutoff_milestones.eq(0) & cases.order_id.isin(ids)]
    evidence = supplement.compare_stages(eligible, durations, reference)
    evidence["context_warning_assessable"] = evidence.reference_distance_matched & evidence.reference_n.ge(supplement.protocol()["minimum_reference_orders"])
    groups, mapping = signals.reference_groups(evidence, reference)
    stages = signals.stage_signals(cases, evidence, groups, mapping)
    cases["source_quality_status"] = np.select(
        [cases.quality_conflict, cases[performance.TIMES].isna().any(axis=1)],
        ["recorded_conflict_requires_verification", "incomplete_at_observation_cutoff"], default="no_conflict_in_executed_timing_checks")
    cases = cases.merge(source[["order_id", "payment_minus_items_and_freight_minor_units"]], on="order_id", validate="one_to_one")
    difference = cases.payment_minus_items_and_freight_minor_units
    cases["payment_reconciliation"] = np.select([difference.isna(), difference.ne(0).fillna(False)],
                                                 ["not_assessable", "difference_requires_verification"], default="matched_exactly")
    orders = signals.order_signals(cases, stages)
    explained, explanation_summary = explanation.build_tables({"order_signals.csv": orders, "stage_signals.csv": stages})
    orders = explained["order_explanations.csv"].merge(source[["order_id", *CONTEXT]], on="order_id", validate="one_to_one")
    stages = explained["stage_comparisons.csv"]
    signal_evidence = signals.signal_evidence(orders, stages)
    for table in [orders, stages, signal_evidence]:
        table.insert(0, "review_id", table.order_id.map(key.set_index("order_id").review_id))
        table["evidence_origin"] = "analyst_assisted_source_and_fixed_train_reference"
        table["independent_reference"] = False
    # Locators refer to this separate package, never to altered parent snapshots.
    orders["source_evidence_locator"] = "analyst_only/order_explanations.csv:order_id="+orders.order_id
    stages["source_evidence_locator"] = "analyst_only/stage_comparisons.csv:order_id="+stages.order_id+";stage="+stages.stage
    signal_evidence["evidence_locator"] = np.where(signal_evidence.stage.eq("delivery_promise"),
        "analyst_only/order_explanations.csv:order_id="+signal_evidence.order_id,
        "analyst_only/stage_comparisons.csv:order_id="+signal_evidence.order_id+";stage="+signal_evidence.stage)
    ledger = key.copy()
    audit_rows = {r["order_id"]: r for r in inputs["audit_rows"]}
    ledger["scope"] = [audit_rows[oid]["scope"] for oid in ledger.order_id]
    ledger["source_row_verified"] = True
    ledger["previous_rule_annotation"] = [audit_rows[oid]["previous_rule_annotation"] for oid in ledger.order_id]
    ledger["promise_basis_status"] = "unverified_source_method_not_provided"
    ledger["comparison_generated"] = ledger.order_id.isin(orders.order_id)
    ledger["assessed_stage_count"] = ledger.order_id.map(orders.set_index("order_id").assessed_stage_count).astype("Int64")
    ledger["human_review_performed"] = False
    ledger["independent_reference"] = False
    ledger["anomaly_label"] = pd.Series(pd.NA, index=ledger.index, dtype="string")
    frames = {"scope_ledger.csv": ledger, "order_explanations.csv": orders.sort_values("review_id").reset_index(drop=True),
              "stage_comparisons.csv": stages.sort_values(["review_id", "stage"]).reset_index(drop=True),
              "reference_groups.csv": groups, "signal_evidence.csv": signal_evidence.sort_values(["review_id", "stage"]).reset_index(drop=True)}
    summary = {**explanation_summary, "version": VERSION, "pilot_orders": len(ledger), "comparison_orders": len(orders),
               "held_review_ids": ledger.loc[~ledger.comparison_generated, "review_id"].tolist(),
               "reference_orders_available": int(reference.order_id.nunique()), "reference_stage_rows_available": len(reference),
               "reference_end_before": str(inputs["reference_end_before"]), "reference_groups_used": len(groups),
               "signal_evidence_rows": len(signal_evidence), "long_warning_orders": int(orders.long_stage_warning.sum()),
               "calendar_late_orders": int(orders.calendar_late_signal.sum()),
               "primary_signal_orders": int(orders.primary_attention_status.eq("signal_present").sum()),
               "assessment_status_counts": stages.assessment_status.value_counts().to_dict(),
               "unassessed_review_ids": orders.loc[~orders.speed_comparison_complete, "review_id"].tolist(),
               "geography_quality_ineligible_ids": orders.loc[~orders.all_pairs_quality_eligible, "review_id"].tolist(),
               "complete_timing_but_no_context_ids": orders.loc[orders.performance_eligible & orders.assessed_stage_count.eq(0), "review_id"].tolist(),
               "early_long_warning_ids": orders.loc[orders.performance_state.eq("early") & orders.long_stage_warning.fillna(False), "review_id"].tolist(),
               "early_without_fast_ids": orders.loc[orders.early_delivery_explanation_status.eq("early_without_faster_stage"), "review_id"].tolist(),
               "statistical_rule_applied_to_pilot": True, "ml_models_loaded_or_scored": False,
               "blind_review_packet_created": False, "independent_reference_ready": False, "human_review_performed": False,
               "original_pilot_changed": False, "public_1621_cohort_changed": False, "split_activated": False,
               "new_cases_sampled": False, "auc_computed": False}
    return frames, summary


def exposure_notice(frames):
    ledger = frames["scope_ledger.csv"]
    return {"review_mode": "analyst_assisted_not_blind", "independent_reference": False,
            "generated_comparison_review_ids": ledger.loc[ledger.comparison_generated, "review_id"].tolist(),
            "metadata_only_review_ids": ledger.loc[~ledger.comparison_generated, "review_id"].tolist(),
            "previous_automatic_annotation_review_ids": ledger.loc[ledger.previous_rule_annotation, "review_id"].tolist(),
            "human_readership": "unknown_do_not_infer_who_has_seen_this_package",
            "required_later_evaluation": "record_actual_reviewers_exposure_and_independent_evidence; do_not_grade_rules_against_their_own_outputs"}


def render_report(frames, summary):
    s = summary
    lines = ["# Bằng chứng công đoạn cho bộ 120 đơn pilot", "",
             "**Phân tích có hỗ trợ máy, không phải gán nhãn thủ công, bộ kiểm chứng độc lập hay kết quả chọn mô hình.**", "",
             f"Giữ đủ {s['pilot_orders']} mã đơn. Tính cho {s['comparison_orders']} đơn trước mốc bảo vệ; "
             f"{', '.join(s['held_review_ids'])} chỉ có thông tin phạm vi, không chấm thời lượng/kết quả.", "",
             "## Đã bổ sung", "",
             "- Thời lượng của ba khoảng: đặt-duyệt, duyệt-bàn giao, bàn giao-giao khách.",
             "- Nhóm tham chiếu train theo bang khách hàng, dải khoảng cách và một/nhiều seller, theo thứ tự dự phòng đã lưu.",
             "- Trung vị tham chiếu, chênh lệch thực tế trừ trung vị, ngưỡng ngày và cờ vượt ngưỡng tách riêng.",
             "- Cơ sở ngày hẹn vẫn chưa xác minh. Không suy ra ngày hẹn ngẫu nhiên và không chia ngày hẹn thành kế hoạch công đoạn giả.", "",
             "## Kết quả", "", "| Nội dung | Số đơn |", "| --- | ---: |",
             f"| So đủ ba công đoạn | {s['speed_complete_orders']} |",
             f"| Chưa so đủ trong phạm vi 118 đơn | {s['speed_incomplete_orders']} |",
             f"| Có cảnh báo ít nhất một công đoạn dài | {s['long_warning_orders']} |",
             f"| Trễ ngày hẹn | {s['calendar_late_orders']} |",
             f"| Giao sớm nhưng vẫn có cảnh báo công đoạn dài | {s['early_with_long_stage_warning']} |",
             f"| Giao sớm, so đủ nhưng không có công đoạn nhanh hơn | {len(s['early_without_fast_ids'])} |", "",
             "Các nhóm giao nhau, không cộng thành tổng số bất thường. Chậm hơn trung vị chưa đồng nghĩa vượt ngưỡng cảnh báo. "
             "Pilot được lấy mẫu phân tầng theo tháng và cờ mô hình cũ; không suy tỷ lệ cảnh báo trong mẫu thành tỷ lệ của toàn ngành hoặc toàn Olist.", "",
             "## Công thức và phạm vi", "",
             "Chênh lệch ngày = thời lượng thực tế - trung vị nhóm train. Âm: nhanh hơn; dương: chậm hơn. "
             "Cờ dài giữ quy tắc F(d) > 0,95 với d > 0; F(d) là tỷ lệ thời lượng train <= d, không phải xác suất bất thường. "
             "Ranh giới ngày chính xác được lưu riêng với toán tử >=; không thay bằng P95 nội suy. Ghi chú đuôi ngắn dùng F(d) < 0,05.", "",
             f"Tham chiếu khả dụng gồm {s['reference_orders_available']} đơn train / {s['reference_stage_rows_available']} dòng công đoạn, "
             f"kết thúc trước {s['reference_end_before']}. Đã tái dựng {s['reference_groups_used']} nhóm được dùng, không chứa đơn pilot. "
             "Để chấm cờ theo bối cảnh, nhóm phải có tối thiểu 30 đơn và khớp dải khoảng cách đạt kiểm tra chất lượng.", "",
             "Không train, không đổi ngưỡng, không chọn ngưỡng dựa vào kết quả pilot. Các ngưỡng này chưa được chứng minh tối ưu. "
             "Nhóm train chưa phải tập được xác nhận toàn bình thường; tham chiếu chưa kiểm soát hết khối lượng hàng, đơn vị vận chuyển, mùa vụ hoặc tuyến đường thực tế.", "",
             "## Phần chưa đánh giá", "",
             "- 10 đơn có mốc giao ngoài cửa sổ quan sát. Giữ chính sách nguồn chỉ so đơn đủ công đoạn: "
             "20 công đoạn đầu có thời lượng nhưng chưa chấm; 10 công đoạn vận chuyển thiếu mốc quan sát.",
             f"- Chất lượng địa lý chưa đạt: {', '.join(s['geography_quality_ineligible_ids'])}.",
             f"- Đủ mốc thời gian nhưng thiếu tham chiếu bối cảnh hợp lệ: {', '.join(s['complete_timing_but_no_context_ids'])}. "
             "Nhóm này bao gồm các đơn địa lý chưa đạt ở trên; không cộng hai danh sách.",
             "- Không chuyển phần chưa đánh giá thành bình thường. Chưa khẳng định phát hiện được đơn đang giao bằng phiên bản này.", "",
             "## Cách sử dụng", "",
             "Xem chi_tiet_120_don.md để đọc từng mã; các bảng analyst_only lưu số liệu đầy đủ và nguồn truy vết. "
             "Gói này công khai kết quả quy tắc cho người phân tích, không phải phiếu mù. Bộ pilot gốc, báo cáo audit trước và đầu ra 1.621 đơn được giữ nguyên.", "",
             "20 đơn từng có đối chiếu tự động không biến thành nhãn người dùng; việc tạo gói mới cũng không tạo ground truth. "
             "Ghi nhận người rà soát và mức tiếp xúc kết quả nếu dùng cho đánh giá sau này. Không tính precision/recall/F1/AUC với nhãn do chính các quy tắc này sinh ra.", "",
             "## Bước tiếp theo", "",
             "Đối chiếu tính hữu ích của các cảnh báo và phần thiếu bằng chứng theo tiêu chí đã thống nhất; tách đánh giá hỗ trợ máy khỏi tham chiếu độc lập. "
             "Sau đó mới so sánh/chọn cấu hình trên cùng phạm vi có bằng chứng. Chưa cần import thêm để đọc và đối chiếu gói này; "
             "xác minh cơ chế ngày hẹn hay nguyên nhân vận hành cần nguồn nghiệp vụ bổ sung.", ""]
    return "\n".join(lines)


def render_cases(frames):
    lines = ["# Chi tiết 120 mã pilot", "", "Phân tích có hỗ trợ máy; các cờ không phải nhãn kiểm chứng độc lập.", ""]
    for row in frames["scope_ledger.csv"].itertuples(index=False):
        lines += [f"## {row.review_id}", ""]
        if not row.comparison_generated:
            lines += ["Giữ riêng theo ranh giới test đề xuất chưa kích hoạt. Không tính kết quả hoặc chấm công đoạn. "
                      "Cơ sở ngày hẹn chưa xác minh; không suy ra bất thường hoặc bình thường.", ""]
            continue
        lines += [explanation.render_case(frames, row.order_id), ""]
        case = frames["order_explanations.csv"].set_index("order_id").loc[row.order_id]
        stages = frames["stage_comparisons.csv"].loc[lambda f: f.order_id.eq(row.order_id)]
        lines += [f"Bối cảnh: bang khách `{case.customer_state}`, dải khoảng cách `{case.distance_band}`, "
                  f"phạm vi seller `{case.seller_attribution_scope}`. Đây là khoảng cách địa lý đại diện, không phải quãng đường vận chuyển thực tế.", ""]
        for stage in stages.itertuples(index=False):
            if stage.assessable:
                lines += [f"- {signals.NAMES[stage.stage]}: {stage.reference_n} đơn train; nhóm `{stage.reference_context_json}`; "
                          f"ngưỡng dài >= {stage.upper_boundary_days_inclusive:.6f} ngày với thời lượng dương. "
                          f"F(d) = {stage.empirical_percentile:.6f}, không phải xác suất."]
            else:
                lines += [f"- {signals.NAMES[stage.stage]}: chưa chấm (`{stage.assessment_status}`)."]
        lines += ["", "Không xác nhận nguyên nhân, trách nhiệm seller hoặc lỗi nhập liệu từ những so sánh này.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite pilot stage evidence")
    if set(frames) != TABLES:
        raise ValueError("Missing pilot evidence tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pilot-stage-evidence-", dir=output.parent) as temporary:
        stage = Path(temporary)/"snapshot"
        (stage/"analyst_only").mkdir(parents=True)
        schemas = {}
        for name, frame in frames.items():
            target = "analyst_only/"+name
            frame.to_csv(stage/target, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[target] = {column: str(dtype) for column, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(stage, target, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(stage/"summary.json", summary)
        review.write_json(stage/"protocol.json", protocol())
        review.write_json(stage/"exposure_notice.json", exposure_notice(frames))
        (stage/"bao_cao_bang_chung.md").write_text(render_report(frames, summary))
        (stage/"chi_tiet_120_don.md").write_text(render_cases(frames))
        files = {str(p.relative_to(stage)): review.digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        review.write_json(stage/"manifest.json", {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                          "provenance": provenance, "schemas": schemas, "summary": summary, "output_hashes": files})
        load_snapshot(stage)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        stage.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    tables = {"analyst_only/"+name for name in TABLES}
    files = tables | {"summary.json", "protocol.json", "exposure_notice.json", "bao_cao_bang_chung.md", "chi_tiet_120_don.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != tables or set(manifest.get("output_hashes", {})) != files):
        raise ValueError("Invalid pilot evidence snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Pilot evidence protocol/summary differs")
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
