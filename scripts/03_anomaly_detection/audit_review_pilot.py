"""Audit the frozen 120-case pilot; no labels, resampling or model scoring."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
import math
from pathlib import Path
import tempfile

import pandas as pd

import annotate_delivery_review20 as annotations
import build_delivery_performance as performance
import explain_process_performance as explanation
import prepare_review_readiness as readiness

review, shared, ROOT = readiness.review, readiness.shared, readiness.ROOT
hierarchy = annotations.hierarchy
VERSION = "review_pilot_audit_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_review_pilot_audit_v1"
STAGE_FIELDS = ["purchased_to_approved_days", "approved_to_carrier_days", "carrier_to_delivered_days"]
STATE_VI = {"early": "Sớm", "on_date": "Đúng ngày", "late": "Trễ", "unknown": "Chưa đủ mốc",
            "proposed_test_hold": "Giữ riêng theo mốc test", "excluded_source_conflict": "Mâu thuẫn nguồn"}


def inventory(directory):
    return {str(p.relative_to(ROOT)): review.digest(p) for p in sorted(Path(directory).rglob("*")) if p.is_file()}


def source_checks(inputs, provenance):
    """Trace every pilot row to a 1-based clean CSV data record, not line number."""
    batch = Path(provenance["pilot_directory"])
    pm = review.read_json(batch/"analyst_only/manifest.json")
    source = provenance["pilot_sources"]["study_sources"]
    clean = Path(source["feature_sources"]["clean_directory"])
    lineage = shared.read_table(batch, "analyst_only/source_lineage.csv", pm)
    key = inputs["key"].set_index("review_id")
    counts = {}
    for table, filename, columns in [
        ("payment_records.csv", "olist_order_payments_dataset.csv", review.PAYMENT_COLUMNS),
        ("item_records.csv", "olist_order_items_dataset.csv", review.ITEM_COLUMNS),
    ]:
        records = lineage.loc[lineage.reviewer_table.eq(table)].sort_values("reviewer_record")
        packet = inputs["blind"][table].reset_index(drop=True)
        if records.reviewer_record.tolist() != list(range(1, len(packet)+1)) or not records.clean_source_record.is_unique:
            raise ValueError("Missing or duplicate child source locators")
        clean_rows = review.read_strings(clean/filename)
        if not records.clean_source_record.between(1, len(clean_rows)).all():
            raise ValueError("Invalid clean data record")
        selected = clean_rows.iloc[records.clean_source_record.to_numpy(dtype=int)-1].reset_index(drop=True)
        for index, locator in enumerate(records.itertuples(index=False)):
            if (packet.iloc[index].review_id != locator.review_id or key.loc[locator.review_id, "order_id"] != locator.order_id
                    or selected.iloc[index].order_id != locator.order_id):
                raise ValueError("Child row belongs to a different order")
            for field in columns:
                left, right = packet.iloc[index][field], selected.iloc[index][field]
                money = field in {"payment_value", "price", "freight_value"}
                mismatch = annotations.minor_units(left) != annotations.minor_units(right) if money else left != right
                if mismatch:
                    raise ValueError(f"Source mismatch: {locator.review_id}/{table}/{field}")
        counts[table] = len(packet)
    clean_orders = review.read_strings(clean/"olist_orders_dataset.csv")
    integrated = inputs["cases"].set_index("order_id")
    for record in inputs["blind"]["cases.csv"].to_dict("records"):
        oid = key.loc[record["review_id"], "order_id"]
        position = int(integrated.loc[oid, "clean_order_record"])
        if not 1 <= position <= len(clean_orders):
            raise ValueError("Invalid order source locator")
        original = clean_orders.iloc[position-1]
        if original.order_id != oid:
            raise ValueError("Order source locator mismatch")
        for field in ["order_status", *performance.TIMES, performance.ESTIMATE]:
            if record[field] != original[field]:
                raise ValueError(f"Order source differs: {record['review_id']}/{field}")
    return {"orders": len(key), **counts}


def business_checks(record, frames):
    rid = record["review_id"]
    payments = frames["payment_records.csv"].loc[lambda f: f.review_id.eq(rid)]
    items = frames["item_records.csv"].loc[lambda f: f.review_id.eq(rid)]
    amounts, prices, freight = [[annotations.minor_units(v) for v in column]
                                for column in [payments.payment_value, items.price, items.freight_value]]
    paid, price, shipping = map(annotations.complete_sum, [amounts, prices, freight])
    expected = price+shipping if price is not None and shipping is not None else None
    delta = paid-expected if paid is not None and expected is not None else None
    aggregates = {"payment_value_sum": paid, "item_price_sum": price, "freight_sum": shipping,
                  "items_plus_freight_sum": expected, "payment_minus_items_and_freight": delta,
                  "item_price_max": max(prices) if prices and all(v is not None for v in prices) else None}
    mismatches = [field for field, value in aggregates.items() if annotations.minor_units(record[field]) != value]
    expected_counts = {"payment_record_count": len(payments), "item_count": len(items), "product_count": items.product_id.nunique(),
                       "seller_count": items.seller_id.nunique(), "payment_type_count": payments.payment_type.nunique()}
    for field, value in expected_counts.items():
        if Decimal(record[field]) != value:
            mismatches.append(field)
    for field, column in [("payment_installments_max", payments.payment_installments),
                          ("payment_sequential_max", payments.payment_sequential)]:
        values = pd.to_numeric(column.replace("", pd.NA))
        expected_value = values.max()
        actual = float(record[field]) if record[field] else float("nan")
        if not ((pd.isna(actual) and pd.isna(expected_value)) or actual == expected_value):
            mismatches.append(field)
    if sorted(json.loads(record["payment_types_json"])) != sorted(payments.payment_type.unique()):
        mismatches.append("payment_types_json")
    for field, value in [("item_price_mean", price/100/len(items) if price is not None and len(items) else None),
                         ("freight_to_item_price_ratio", shipping/price if price and shipping is not None else None)]:
        actual = float(record[field]) if record[field] else None
        if (actual is None or value is None):
            if actual != value:
                mismatches.append(field)
        elif not math.isclose(actual, value, rel_tol=1e-12, abs_tol=1e-12):
            mismatches.append(field)
    return {"payment_total_minor_units": paid, "items_plus_freight_minor_units": expected,
            "payment_difference_minor_units": delta,
            "reconciliation": "unknown" if delta is None else "matched" if delta == 0 else "needs_verification",
            "aggregate_mismatches": mismatches, "payment_records": len(payments), "item_records": len(items),
            "duplicate_item_ids": bool(items.order_item_id.duplicated().any()),
            "duplicate_payment_sequences": bool(payments.payment_sequential.duplicated().any()),
            "negative_source_amounts": sum(v is not None and v < 0 for v in amounts+prices+freight),
            "missing_source_amounts": sum(v is None for v in amounts+prices+freight),
            "temporal_scope": "retrospective_only_payment_and_item_availability_not_timestamped"}


def load_inputs():
    inputs, provenance = readiness.load_inputs()
    inputs["source_checks"] = source_checks(inputs, provenance)
    inputs["boundary"], _ = performance.load_boundary()
    em = explanation.load_snapshot()
    inputs["explained_ids"] = set(shared.read_table(explanation.DEFAULT_OUTPUT, "order_explanations.csv", em).order_id)
    annotations.load_snapshot()
    prior = review.read_strings(annotations.DEFAULT_OUTPUT/"case_annotations.csv")
    inputs["previous_rule_ids"] = set(prior.review_id)
    journal, _ = hierarchy.load_journal()
    inputs["journal_events"] = len(journal["events"])
    inputs["session_labels"] = 0
    for directory in sorted((review.DEFAULT_BATCH/"sessions").glob("*")):
        if directory.is_dir() and not directory.name.startswith("."):
            metadata = review.read_json(directory/"session.json")
            criteria_hash = review.digest(directory/"approved_criteria.json")
            if (metadata.get("reviewer_id") != directory.name or metadata.get("version") != review.VERSION
                    or metadata.get("evidence_manifest_sha256") != inputs["evidence_hash"]
                    or metadata.get("approved_criteria_sha256") != criteria_hash):
                raise ValueError("Recorded review session binding changed")
            review.validate_criteria(review.read_json(directory/"approved_criteria.json"))
            progress = review.validate_progress(review.read_strings(directory/"review_progress.csv"),
                                                inputs["blind"]["cases.csv"].review_id)
            inputs["session_labels"] += int(progress.reviewer_label.ne("").sum())
    watched = [review.DEFAULT_BATCH, readiness.integration.DEFAULT_OUTPUT, performance.PLAN,
               explanation.DEFAULT_OUTPUT, annotations.DEFAULT_OUTPUT, hierarchy.DEFAULT_OUTPUT,
               hierarchy.DEFAULT_JOURNAL.parent]
    hashes = {name: digest for directory in watched for name, digest in inventory(directory).items()}
    for path in [Path(__file__), Path(readiness.__file__), Path(performance.__file__), Path(annotations.__file__),
                 Path(hierarchy.__file__), Path(review.__file__), explanation.DECISIONS]:
        hashes[str(path.relative_to(ROOT))] = review.digest(path)
    return inputs, {"watched_directories": [str(p.relative_to(ROOT)) for p in watched], "source_hashes": hashes,
                    "source_lineage_verified": True, "boundary": str(inputs["boundary"])}


def build_audit(inputs):
    readiness.validate_inputs(inputs)
    if not inputs["blind"]["cases.csv"].product_category_name.eq("cama_mesa_banho").all():
        raise ValueError("Expected only the original bed_bath_table category")
    key = inputs["key"].set_index("review_id")
    if set(key.order_id) & inputs["explained_ids"]:
        raise ValueError("Pilot overlaps public explanation output; investigate exposure first")
    source = inputs["cases"].loc[inputs["cases"].order_id.isin(key.order_id)]
    observed = performance.order_performance(source, inputs["boundary"]).set_index("order_id")
    integrated = source.set_index("order_id")
    rows = []
    for record in inputs["blind"]["cases.csv"].to_dict("records"):
        rid = record["review_id"]
        oid = key.loc[rid, "order_id"]
        held = oid not in observed.index
        row = {"review_id": rid, "order_id": oid, "scope": "proposed_test_hold" if held else "pre_boundary_evidence_audit",
               "source_row_verified": True, "clean_order_record": int(integrated.loc[oid, "clean_order_record"]),
               "pilot_evidence_records": {name: (frame.index[frame.review_id.eq(rid)]+1).tolist()
                                          for name, frame in inputs["blind"].items()},
               "previous_rule_annotation": rid in inputs["previous_rule_ids"],
               "promise_basis_status": "unverified_source_method_not_provided",
               "stage_reference_status": "not_scored_protected_pilot", "long_stage_warning": None,
               "faster_stage_observed": None, "anomaly_label": None, "independent_reference": False,
               "delivery_state": "proposed_test_hold", "promise_delta_calendar_days": None,
               "post_cutoff_milestones": None, "visible_inversions": None, "stage_durations_days": {},
               "duration_aggregate_mismatches": [], "business": None, "geography_quality_eligible": None,
               "geography_quality_reasons": None, "seller_history_route": None}
        if not held:
            event = observed.loc[oid]
            row.update(delivery_state=str(event.performance_state),
                       promise_delta_calendar_days=None if pd.isna(event.promise_delta_calendar_days) else int(event.promise_delta_calendar_days),
                       post_cutoff_milestones=int(event.post_cutoff_milestones), visible_inversions=int(event.visible_chronology_inversions),
                       geography_quality_eligible=bool(event.all_pairs_quality_eligible),
                       geography_quality_reasons=str(event.geography_quality_reasons),
                       seller_history_route=str(integrated.loc[oid, "history_route"]),
                       business=business_checks(record, inputs["blind"]))
            for (start, end, name), field in zip(performance.STAGES, STAGE_FIELDS+["total_cycle_time_days"]):
                duration = (event[end]-event[start]).total_seconds()/86400 if pd.notna(event[start]) and pd.notna(event[end]) else None
                row["stage_durations_days"][name] = duration
                if duration is not None and (not record[field] or not math.isclose(float(record[field]), duration, abs_tol=1e-12, rel_tol=1e-12)):
                    row["duration_aggregate_mismatches"].append(field)
        rows.append(row)
    active = [row for row in rows if row["business"] is not None]
    counts = pd.Series([row["delivery_state"] for row in rows]).value_counts().to_dict()
    summary = {"version": VERSION, "status": "technical_audit_not_independent_labels", "pilot_cases": len(rows),
               "source_checks": inputs["source_checks"], "original_category": "bed_bath_table", "unique_order_ids": len(set(key.order_id)),
               "boundary_exclusive": str(inputs["boundary"]), "pre_boundary_cases": len(active), "delivery_states": counts,
               "held_review_ids": [r["review_id"] for r in rows if r["scope"] == "proposed_test_hold"],
               "post_cutoff_review_ids": [r["review_id"] for r in active if r["post_cutoff_milestones"]],
               "payment_difference_cases": [{"review_id": r["review_id"], "difference_minor_units": r["business"]["payment_difference_minor_units"]}
                                            for r in active if r["business"]["reconciliation"] == "needs_verification"],
               "money_matched_cases": sum(r["business"]["reconciliation"] == "matched" for r in active),
               "money_unknown_cases": sum(r["business"]["reconciliation"] == "unknown" for r in active),
               "aggregate_mismatch_cases": sum(bool(r["business"]["aggregate_mismatches"] or r["duration_aggregate_mismatches"]) for r in active),
               "negative_amounts": sum(r["business"]["negative_source_amounts"] for r in active),
               "missing_amounts": sum(r["business"]["missing_source_amounts"] for r in active),
               "duplicate_item_cases": sum(r["business"]["duplicate_item_ids"] for r in active),
               "duplicate_payment_sequence_cases": sum(r["business"]["duplicate_payment_sequences"] for r in active),
               "visible_inversion_cases": sum(r["visible_inversions"] > 0 for r in active),
               "geography_ineligible_review_ids": [r["review_id"] for r in active if not r["geography_quality_eligible"]],
               "seller_history_routes": pd.Series([r["seller_history_route"] for r in active]).value_counts().to_dict(),
               "previous_rule_annotation_cases": sum(r["previous_rule_annotation"] for r in rows),
               "recorded_pilot_session_labels": inputs["session_labels"], "hierarchical_journal_events": inputs["journal_events"],
               "pilot_cases_in_current_explanations": 0, "sample_by_month": key.purchase_month.value_counts().sort_index().to_dict(),
               "sample_by_anchor_group": key.agreement_group.value_counts().sort_index().to_dict(),
               "original_sampling_population": int(inputs["strata"].eligible_cases.sum()),
               "sampling_weight_sum": float(key.sampling_weight.sum()),
               **{name: False for name in ["labels_created", "model_fitted", "model_scored", "test_scored", "split_activated",
                                            "accuracy_computed", "auc_computed", "new_cases_sampled", "source_edited"]}}
    return rows, summary


def report(rows, summary):
    s = summary
    lines = ["# Rà soát kỹ thuật 120 đơn pilot", "", "Đây là tài liệu dành cho người phân tích, không phải bộ nhãn độc lập hoặc phiếu rà soát mù.", "",
             "## Kết quả", "", f"- Truy vết đúng {s['pilot_cases']} đơn, {s['source_checks']['payment_records.csv']} dòng thanh toán và {s['source_checks']['item_records.csv']} dòng hàng hóa về nguồn đã làm sạch; không sửa nguồn.",
             f"- {s['pre_boundary_cases']} đơn trước mốc {s['boundary_exclusive']}; giữ riêng {', '.join(s['held_review_ids'])} theo đề xuất test chưa kích hoạt.",
             f"- Trạng thái trong cửa sổ quan sát: {json.dumps(s['delivery_states'], ensure_ascii=False)}.",
             f"- Tiền: {s['money_matched_cases']} khớp, {len(s['payment_difference_cases'])} có chênh lệch cần xác minh, {s['money_unknown_cases']} thiếu dữ liệu, trong {s['pre_boundary_cases']} đơn; không tính nội dung hai đơn giữ riêng.",
             f"- {s['aggregate_mismatch_cases']} đơn sai lệch giữa số tổng hợp và phép tính lại; {s['visible_inversion_cases']} đơn có mốc đảo trong cửa sổ quan sát.",
             f"- {s['negative_amounts']} khoản tiền âm, {s['missing_amounts']} khoản tiền thiếu; {s['duplicate_item_cases']} đơn trùng item ID, {s['duplicate_payment_sequence_cases']} đơn trùng payment sequence.",
             f"- Địa lý chưa đạt điều kiện nguồn: {', '.join(s['geography_ineligible_review_ids']) or 'không có'} (trong 118 đơn).",
             f"- {s['previous_rule_annotation_cases']} đơn đã có đối chiếu tự động ở gói 20 đơn trước; không phải nhãn người dùng.",
             f"- Tìm thấy {s['recorded_pilot_session_labels']} nhãn trong session pilot và {s['hierarchical_journal_events']} sự kiện nhật ký phân cấp. Không suy ra hoạt động ngoài repository.", "",
             "## Phạm vi và giới hạn", "",
             "- Chưa xác minh được cơ sở ngày hẹn cho cả 120 đơn. Không kết luận ngày hẹn ngẫu nhiên và không sửa ngày hẹn.",
             "- Chỉ so ngày lịch giao thực tế với ngày hẹn. Giao trễ là dấu hiệu cần xem xét, chưa chứng minh nguyên nhân hay một công đoạn bị tắc nghẽn.",
             "- Giữ các mốc ngoài cửa sổ quan sát là chưa biết. Không dùng ngày giao sau mốc bảo vệ để suy ra sớm/trễ hoặc thời lượng vận chuyển.",
             "- Thanh toán/hàng hóa chỉ được đối chiếu hồi cứu. Chưa biết thời điểm từng bản ghi có sẵn, nên không coi đây là bằng chứng near real-time.",
             "- 120 đơn được bảo vệ, không nằm trong đầu ra diễn giải 1.621 đơn. Chưa chạy tham chiếu/ngưỡng trên pilot trong lần audit này: cờ chậm và có công đoạn nhanh đều để null, không phải false.",
             "- Do đó chưa kết luận đơn giao sớm không có công đoạn chậm, cũng không ép tìm công đoạn nhanh. Thời lượng bên dưới là số quan sát, chưa phải chênh lệch so nhóm tương đồng.",
             "- Bằng chứng địa lý/lịch sử seller tồn tại ở phía phân tích nhưng không có đầy đủ trong bộ phiếu mù cũ. Cần gói bằng chứng phiên bản mới trước khi mở rộng phạm vi đánh giá.",
             "- Tiêu chí người dùng đã chốt trong docs/fulfillment_evaluation_decisions_2026-10-16.md vẫn được giữ. File criteria_draft cũ chưa cập nhật không có nghĩa người dùng phải chốt lại các quy ước đó.",
             f"- Pilot lấy phân tầng theo tháng và nhóm cờ mô hình; quần thể gốc {s['original_sampling_population']} đơn, tổng trọng số {s['sampling_weight_sum']:.0f}. Không dùng tỷ lệ thô 120/118 đơn làm tỷ lệ của toàn Olist; trọng số cũ không tự hợp lệ sau giới hạn phạm vi mới.",
             "- Không tạo nhãn, không tính precision/recall/F1/AUC, không chọn mô hình hay gọi các lần đối chiếu tự động là kiểm chứng độc lập.", "",
             "## Chênh lệch tiền cần xác minh", "", "Đơn vị BRL; số chênh = tổng thanh toán - (giá hàng + phí vận chuyển). Không làm tròn để ép khớp.", "",
             "| Mã | Tổng thanh toán | Giá hàng + vận chuyển | Chênh lệch BRL |", "| --- | ---: | ---: | ---: |"]
    for row in rows:
        b = row["business"]
        if b and b["reconciliation"] == "needs_verification":
            lines.append(f"| {row['review_id']} | {Decimal(b['payment_total_minor_units'])/100:.2f} | "
                         f"{Decimal(b['items_plus_freight_minor_units'])/100:.2f} | {Decimal(b['payment_difference_minor_units'])/100:.2f} |")
    lines += ["", "Chưa có bằng chứng xác nhận nguyên nhân của chênh lệch. Không tự quy cho trả góp, làm tròn, nhập sai hoặc gian lận.",
              "", "## Bằng chứng địa lý chưa đạt", "", "| Mã | Lý do kiểm tra chất lượng nguồn |", "| --- | --- |"]
    lines += [f"| {r['review_id']} | {r['geography_quality_reasons']} |" for r in rows if r["geography_quality_eligible"] is False]
    lines += ["", "Các lý do trên lần lượt phản ánh thiếu số điểm tọa độ hỗ trợ, độ phân tán lớn hoặc khoảng cách nhạy với cách chọn tọa độ đại diện. "
              "Đây là giới hạn bằng chứng, không phải kết luận đơn giao bất thường vì địa lý."]
    lines += ["", "## Đối chiếu từng đơn", "", "Thời lượng tính bằng ngày; 'chưa biết' không phải 0. Ba công đoạn: đặt-duyệt, duyệt-bàn giao, bàn giao-giao khách.", "",
              "| Mã | Phạm vi/trạng thái | Lệch ngày hẹn | Đặt-duyệt | Duyệt-bàn giao | Bàn giao-giao | Lệch tiền BRL | Đã có rule 20 |",
              "| --- | --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    def number(value, decimals=3):
        return "chưa biết" if value is None else f"{value:.{decimals}f}"
    for r in rows:
        d = r["stage_durations_days"]
        business = r["business"]
        money = business["payment_difference_minor_units"] if business else None
        lines.append(f"| {r['review_id']} | {STATE_VI[r['delivery_state']]} | {number(r['promise_delta_calendar_days'], 0)} | "
                     f"{number(d.get('approval'))} | {number(d.get('preparation_handoff'))} | {number(d.get('transport'))} | "
                     f"{number(None if money is None else Decimal(money)/100, 2)} | {'có' if r['previous_rule_annotation'] else 'không'} |")
    lines += ["", "## Bước tiếp theo", "",
              "Giữ nguyên pilot cũ. Chuẩn bị gói bằng chứng mới theo quy ước đã chốt, định rõ tập được sử dụng và phần bảo vệ; "
              "tách kết quả rà soát hỗ trợ bằng máy khỏi bộ tham chiếu độc lập. Chỉ sau đó mới đối chiếu mô hình trên cùng phạm vi đủ bằng chứng. "
              "Chưa cần import thêm dữ liệu để chuẩn bị gói bằng chứng; muốn xác nhận chênh tiền, cơ chế ngày hẹn hoặc nguyên nhân vận hành cần nguồn nghiệp vụ bổ sung."]
    return "\n".join(lines)+"\n"


def verify_unchanged(provenance):
    for name, digest in provenance["source_hashes"].items():
        if not (ROOT/name).is_file() or review.digest(ROOT/name) != digest:
            raise ValueError(f"Audit input changed: {name}")
    expected = provenance["source_hashes"]
    for name in provenance["watched_directories"]:
        if any(path not in expected for path in inventory(ROOT/name)):
            raise ValueError("New source/review event appeared during audit")


def run(output=DEFAULT_OUTPUT):
    output = hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Audit output exists; never overwrite a frozen audit")
    inputs, provenance = load_inputs()
    rows, summary = build_audit(inputs)
    verify_unchanged(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".pilot-audit-", dir=output.parent) as temp:
        stage = Path(temp)/"snapshot"
        (stage/"analyst_only").mkdir(parents=True)
        review.write_json(stage/"summary.json", summary)
        review.write_json(stage/"analyst_only/case_audit.json", rows)
        (stage/"bao_cao_ra_soat.md").write_text(report(rows, summary))
        outputs = {str(p.relative_to(stage)): review.digest(p) for p in sorted(stage.rglob("*")) if p.is_file()}
        review.write_json(stage/"manifest.json", {"version": VERSION, "status": "complete",
                          "created_at_utc": datetime.now(timezone.utc).isoformat(), "provenance": provenance,
                          "summary": summary, "output_hashes": outputs})
        verify_unchanged(provenance)
        stage.rename(output)
    return summary


def load_snapshot(output=DEFAULT_OUTPUT):
    output = Path(output)
    manifest = review.read_json(output/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != {"summary.json", "analyst_only/case_audit.json", "bao_cao_ra_soat.md"}):
        raise ValueError("Invalid pilot audit snapshot")
    hierarchy.verify_files(output, manifest["output_hashes"])
    verify_unchanged(manifest["provenance"])
    summary = review.read_json(output/"summary.json")
    rows = review.read_json(output/"analyst_only/case_audit.json")
    if summary != manifest["summary"] or len(rows) != summary["pilot_cases"]:
        raise ValueError("Pilot audit summary differs")
    return rows, summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = load_snapshot(args.output)[1] if args.verify else run(args.output)
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))
