"""Rule-derived annotations of real reviewer evidence, never human labels."""

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from itertools import combinations
from io import StringIO
import json
from pathlib import Path
import shutil
import tempfile

import pandas as pd

import prepare_hierarchical_review as hierarchy

review = hierarchy.review
ROOT = review.ROOT
VERSION = "delivery_rule_annotations20_v1"
SOURCE = ROOT/"data/experiments/olist_delivery_review20_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_delivery_rule_annotations20_v1"
TEMPLATE = ROOT/"templates/delivery_rule_annotations20_v1"
TABLES = {"case_annotations.csv", "rule_checks.csv", "scope_annotations.csv"}
FILES = TABLES | {"README.md", "protocol.json", "summary.json", "20_case_da_doi_chieu.md", "exposure_notice.json"}
DATES = hierarchy.TIMES
ESTIMATE = "order_estimated_delivery_date"
DELIVERY_NAMES = {"early": "Giao sớm", "on_date": "Đúng ngày hẹn", "late": "Giao trễ",
                  "unknown": "Chưa đủ dữ liệu", "source_conflict": "Cần xác minh mốc thời gian"}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(path.relative_to(ROOT)): review.digest(path) for path in
            [Path(__file__), Path(review.__file__), Path(hierarchy.__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}


def load_inputs(source=SOURCE):
    """Only reviewer-facing payloads are parsed; no analyst key or scores."""
    source = Path(source).resolve()
    manifest = review.read_json(source/"manifest.json")
    if manifest.get("version") != "delivery_review20_v1" or manifest.get("status") != "complete":
        raise ValueError("Expected the frozen 20-order source package")
    bound = manifest["output_hashes"]
    names = [f"reviewer_packet/reviewer/{name}" for name in [*review.SCHEMAS, "README.md", "criteria_draft.json", "manifest.json"]]
    names += ["reviewer_packet/criteria.json", "reviewer_packet/assessment_template.json", "protocol.json"]
    if not set(names).issubset(bound):
        raise ValueError("Reviewer evidence not bound by source manifest")
    hierarchy.verify_files(source, {name: bound[name] for name in names})
    frames, reviewer_hash = review.load_evidence(source/"reviewer_packet")
    if len(frames["cases.csv"]) != 20 or not frames["cases.csv"].product_category_name.eq("cama_mesa_banho").all():
        raise ValueError("Expected exactly 20 real category-scoped cases")
    criteria = review.read_json(source/"reviewer_packet/criteria.json")
    if criteria.get("version") != "delivery_review20_v1" or "delivery_performance_policy" not in criteria:
        raise ValueError("Expected the agreed performance/anomaly separation")
    if review.read_json(source/"reviewer_packet/assessment_template.json") != hierarchy.tasks_for(frames):
        raise ValueError("Original human note templates must remain unassessed")
    provenance = {"source_directory": str(source), "source_manifest_sha256": review.digest(source/"manifest.json"),
                  "reviewer_manifest_sha256": reviewer_hash, "reviewer_output_hashes": {name: bound[name] for name in names},
                  "code_hashes": code_hashes(), "parsed_source_tables": list(review.SCHEMAS),
                  "source_role": "reviewer_evidence_not_model_or_analyst_decisions"}
    verify_sources(provenance)
    return frames, provenance


def verify_sources(provenance):
    source = Path(provenance["source_directory"])
    if review.digest(source/"manifest.json") != provenance["source_manifest_sha256"]:
        raise ValueError("Source package binding changed")
    hierarchy.verify_files(source, provenance["reviewer_output_hashes"])
    if review.digest(source/"reviewer_packet/reviewer/manifest.json") != provenance["reviewer_manifest_sha256"]:
        raise ValueError("Source reviewer manifest changed")
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Annotation implementation/protocol changed; use a new version")


def date_value(value):
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S") if value else None


def minor_units(value):
    if value == "":
        return None
    try:
        scaled = Decimal(value)*100
    except InvalidOperation as exc:
        raise ValueError("Invalid source money") from exc
    if not scaled.is_finite() or scaled != scaled.to_integral_value():
        raise ValueError("Source amount is not an exact finite minor-unit value; do not round it")
    return int(scaled)


def complete_sum(values):
    return sum(values) if values and all(value is not None for value in values) else None


def money_text(value):
    return "chưa đủ dữ liệu" if value is None else f"{Decimal(value)/100:.2f}"


def source_refs(frames, review_id, fields):
    return [{"file": "reviewer_packet/reviewer/"+table, "review_id": review_id,
             "csv_line_numbers": (frames[table].index[frames[table].review_id.eq(review_id)]+2).tolist(), "columns": columns}
            for table, columns in fields.items()]


def build_annotations(frames):
    cases, checks, scopes = [], [], []
    if not frames["cases.csv"].review_id.is_unique:
        raise ValueError("Duplicate review ID")
    for record in frames["cases.csv"].to_dict("records"):
        rid = record["review_id"]
        payments = frames["payment_records.csv"].loc[lambda f: f.review_id.eq(rid)]
        items = frames["item_records.csv"].loc[lambda f: f.review_id.eq(rid)]
        times = {field: date_value(record[field]) for field in DATES+[ESTIMATE]}
        missing = [field for field in DATES if times[field] is None]
        inversions = [[a, b] for a, b in combinations(DATES, 2) if times[a] is not None and times[b] is not None and times[b] < times[a]]
        ties = [[a, b] for a, b in combinations(DATES, 2) if times[a] is not None and times[b] is not None and times[a] == times[b]]
        actual, promised = times[DATES[3]], times[ESTIMATE]
        delta = (actual.date()-promised.date()).days if actual is not None and promised is not None else None
        observed = "unknown" if delta is None else "early" if delta < 0 else "late" if delta > 0 else "on_date"
        promise_before_purchase = bool(promised and times[DATES[0]] and promised.date() < times[DATES[0]].date())
        status_conflict = (record["order_status"] == "delivered") != (actual is not None)
        chronology_conflict = bool(inversions or promise_before_purchase or status_conflict)
        delivery_label = "source_conflict" if chronology_conflict else observed
        promise_assessment = ("meets_recorded_date_promise" if delivery_label in {"early", "on_date"}
                              else "after_recorded_date_promise" if delivery_label == "late" else "insufficient_evidence")
        amounts = [minor_units(value) for value in payments.payment_value]
        prices = [minor_units(value) for value in items.price]
        freights = [minor_units(value) for value in items.freight_value]
        payment_total, price_total, freight_total = map(complete_sum, [amounts, prices, freights])
        expected_total = price_total+freight_total if price_total is not None and freight_total is not None else None
        difference = payment_total-expected_total if payment_total is not None and expected_total is not None else None
        # Compare exact source rows with the existing case aggregates, not floats.
        mismatches = []
        for field, actual_value in [("payment_value_sum", payment_total), ("item_price_sum", price_total), ("freight_sum", freight_total),
                                    ("items_plus_freight_sum", expected_total), ("payment_minus_items_and_freight", difference)]:
            if minor_units(record[field]) != actual_value:
                mismatches.append(field)
        item_count_matches = str(len(items)) == record["item_count"]
        payment_count_matches = str(len(payments)) == record["payment_record_count"]
        product_count_matches = str(items.product_id.nunique()) == record["product_count"]
        seller_count_matches = str(items.seller_id.nunique()) == record["seller_count"]
        item_ids_unique = not items.order_item_id.duplicated().any()
        negative_amount = any(value is not None and value < 0 for value in amounts+prices+freights)
        money_missing = any(value is None for value in amounts+prices+freights) or not len(items) or not len(payments)
        reconciliation = "insufficient_evidence" if difference is None else "matched_exactly" if difference == 0 else "needs_verification"
        verification_needed = chronology_conflict or bool(mismatches) or negative_amount or difference not in {0, None} or not (
            item_count_matches and payment_count_matches and product_count_matches and seller_count_matches and item_ids_unique)
        technical = "needs_verification" if verification_needed else "incomplete_checks" if missing or money_missing or delta is None else "no_issue_in_executed_checks"
        stage_values = {}
        for a, b, name in [(DATES[0], DATES[1], "approval_days"), (DATES[1], DATES[2], "preparation_days"),
                           (DATES[2], DATES[3], "transport_days")]:
            stage_values[name] = (times[b]-times[a]).total_seconds()/86400 if times[a] is not None and times[b] is not None else None
        cases.append({"review_id": rid, "annotation_origin": protocol()["annotation_origin"],
                      "delivery_label": delivery_label, "delivery_calendar_observation": observed,
                      "calendar_promise_assessment": promise_assessment, "promise_delta_calendar_days": delta,
                      "source_check_status": technical, "missing_actual_milestones": len(missing),
                      "recorded_inversion_pairs": len(inversions), "equal_timestamp_pairs": len(ties),
                      "payment_total_minor_units": payment_total, "items_plus_freight_minor_units": expected_total,
                      "payment_difference_minor_units": difference, "payment_reconciliation": reconciliation,
                      "payment_records": len(payments), "item_records": len(items), **stage_values,
                      "broad_anomaly_status": "unresolved", "human_review_performed": False, "independent_reference": False})

        def add(code, scope, outcome, observations, reason, fields):
            checks.append({"review_id": rid, "analysis_scope": scope, "rule_code": code, "outcome": outcome,
                           "observations_json": json.dumps(observations, ensure_ascii=False, allow_nan=False, sort_keys=True),
                           "reason": reason, "source_references_json": json.dumps(source_refs(frames, rid, fields), ensure_ascii=False),
                           "annotation_origin": protocol()["annotation_origin"]})

        time_refs = {"cases.csv": ["order_status", *DATES, ESTIMATE]}
        add("T01", "process_timing", "needs_verification" if chronology_conflict else "incomplete" if missing else "consistent",
            {"inverted_pairs": inversions, "equal_pairs": ties, "status_conflict": status_conflict, "promise_before_purchase": promise_before_purchase},
            "Kiểm tra trình tự mốc ghi nhận; không xác định ai nhập sai hoặc nguyên nhân vận hành.", time_refs)
        add("T02", "process_timing", "incomplete" if missing else "complete", {"missing_milestones": missing},
            "Đủ mốc ghi nhận không chứng minh dữ liệu đúng ngoài thực tế.", time_refs)
        add("T03", "process_timing", "not_assessable", stage_values,
            "Chưa có mức tham chiếu độc lập theo bối cảnh; không tự gán bất thường vì công đoạn dài/ngắn.", time_refs)
        add("T04", "process_timing", "not_assessable" if delta is None or chronology_conflict else "observed",
            {"calendar_delta_days": delta, "delivery_label": delivery_label, "promise_assessment": promise_assessment},
            "So ngày lịch: giao sớm/đúng ngày là đáp ứng hẹn, không phải nhãn bình thường cho toàn đơn; trễ chưa chứng minh tắc nghẽn.", time_refs)
        payment_refs = {"cases.csv": ["payment_value_sum", "items_plus_freight_sum", "payment_minus_items_and_freight"],
                        "payment_records.csv": ["payment_value"], "item_records.csv": ["price", "freight_value"]}
        add("P01", "payment", "needs_verification" if mismatches or not payment_count_matches else reconciliation,
            {"payment_minor_units": payment_total, "items_and_freight_minor_units": expected_total,
                                                 "difference_minor_units": difference, "aggregate_mismatches": mismatches},
            "Chênh lệch được giữ nguyên; cần xác minh cách ghi nhận/làm tròn, không tự kết luận lỗi hoặc fraud." if difference not in {0, None}
            else "Đối chiếu tổng tiền từ từng dòng nguồn; khớp tiền chưa chứng minh hợp lệ nghiệp vụ.", payment_refs)
        add("P02", "payment", "observed", {"payment_records": len(payments), "payment_types": payments.payment_type.tolist(),
                                               "installments": payments.payment_installments.tolist(), "sequences": payments.payment_sequential.tolist()},
            "Nhiều dòng thanh toán hoặc trả góp không đồng nghĩa nhiều lần thất bại hoặc bất thường.",
            {"payment_records.csv": review.PAYMENT_COLUMNS})
        add("P03", "payment", "not_assessable", {"payment_minor_units": payment_total},
            "Thiếu tham chiếu độc lập về giá trị/cấu trúc thanh toán và bằng chứng tính hợp lệ.", payment_refs)
        item_refs = {"cases.csv": ["item_count", "product_count", "seller_count", "item_price_sum", "freight_sum"],
                     "item_records.csv": review.ITEM_COLUMNS}
        add("O01", "order_contents", "needs_verification" if mismatches or negative_amount or not (
            item_count_matches and product_count_matches and seller_count_matches and item_ids_unique)
            else "incomplete" if money_missing else "consistent", {"item_count_matches": item_count_matches,
            "payment_count_matches": payment_count_matches, "product_count_matches": product_count_matches,
            "seller_count_matches": seller_count_matches, "item_ids_unique": item_ids_unique, "negative_amount_present": negative_amount,
            "aggregate_mismatches": mismatches, "price_minor_units": price_total, "freight_minor_units": freight_total},
            "Đối chiếu dòng hàng và tổng tiền; không tự sửa hoặc loại bản ghi.", item_refs)
        add("O02", "order_contents", "not_assessable", {"item_records": len(items), "sellers": int(items.seller_id.nunique())},
            "Chưa đủ tham chiếu về cấu trúc đơn, trọng lượng, tuyến vận chuyển và ảnh hưởng từng seller.", item_refs)
        for scope, required_rule in [("process_timing", "T03"), ("payment", "P03"), ("order_contents", "O02")]:
            scope_checks = [row for row in checks if row["review_id"] == rid and row["analysis_scope"] == scope]
            unresolved = next(row for row in scope_checks if row["rule_code"] == required_rule)
            scopes.append({"review_id": rid, "analysis_scope": scope, "assessment_origin": protocol()["annotation_origin"],
                           "assessment_status": "unresolved", "parent_label": "", "anomaly_subtype": "",
                           "reason_codes_json": json.dumps([row["rule_code"] for row in scope_checks]),
                           "missing_evidence": unresolved["reason"], "proposed_use_action": "pending",
                           "human_reviewer_id": "", "confidence_probability": "", "independent_reference": False})
    frames_out = {"case_annotations.csv": pd.DataFrame(cases), "rule_checks.csv": pd.DataFrame(checks), "scope_annotations.csv": pd.DataFrame(scopes)}
    summary = {"version": VERSION, "orders_annotated": len(cases), "rule_checks": len(checks), "scope_annotations": len(scopes),
               "delivery_label_counts": pd.Series([row["delivery_label"] for row in cases]).value_counts().sort_index().to_dict(),
               "source_check_status_counts": pd.Series([row["source_check_status"] for row in cases]).value_counts().sort_index().to_dict(),
               "payment_reconciliation_counts": pd.Series([row["payment_reconciliation"] for row in cases]).value_counts().sort_index().to_dict(),
               "unresolved_broad_anomaly_scopes": len(scopes), "human_labels_created": 0,
               **{field: False for field in ["human_review_performed", "independent_reference_ready", "anomaly_accuracy_computed",
                                             "models_loaded", "models_trained", "model_flags_used", "test_scored", "advisor_approval_inferred"]}}
    return frames_out, summary


def markdown_report(frames, summary):
    table = frames["case_annotations.csv"]
    lines = ["# Kết quả đối chiếu tự động 20 đơn\n", "Nhãn do quy tắc tự động tạo theo yêu cầu của bạn, không phải nhãn người dùng đã rà soát. "
             "Không dùng làm bộ nhãn chuẩn độc lập để tính độ chính xác bất thường.\n",
             "## Nhãn giao hàng và kiểm tra số liệu\n",
             "Số ngày âm là giao sớm; 0 là giao đúng ngày. Đáp ứng hẹn không đồng nghĩa toàn đơn bình thường.\n",
             "| Mã rà soát | Nhãn giao hàng | Ngày lệch so với hẹn | Chênh lệch tiền | Kiểm tra cơ học |",
             "| --- | --- | ---: | ---: | --- |"]
    for row in table.to_dict("records"):
        delta = "chưa đủ dữ liệu" if pd.isna(row["promise_delta_calendar_days"]) else str(int(row["promise_delta_calendar_days"]))
        state = "Cần xác minh" if row["source_check_status"] == "needs_verification" else "Chưa đủ dữ liệu" if row["source_check_status"] == "incomplete_checks" else "Chưa thấy vấn đề qua các phép kiểm tra đã chạy"
        lines.append(f"| {row['review_id']} | {DELIVERY_NAMES[row['delivery_label']]} | {delta} | {money_text(row['payment_difference_minor_units'])} | {state} |")
    lines += ["\n## Những điều chưa kết luận\n",
              "Cả 20 đơn chưa có nhãn bất thường tổng quát đã xác minh. Các phạm vi quy trình/thời gian, thanh toán và "
              "nội dung đơn giữ trạng thái `unresolved` vì thiếu tham chiếu độc lập hoặc bằng chứng nghiệp vụ. "
              "Đây không phải 20 đơn được xác nhận hoàn toàn bình thường.\n",
              "Sau khi xem diễn giải này, một lần tự rà soát các đơn này không còn là đánh giá mù không được hỗ trợ bởi "
              "diễn giải tự động. Bộ dữ kiện và các ô nhận xét con người gốc vẫn giữ nguyên.\n", "## Chi tiết từng đơn\n"]
    for row in table.to_dict("records"):
        lines.append(f"### {row['review_id']}\n")
        selected = frames["rule_checks.csv"].loc[lambda frame: frame.review_id.eq(row["review_id"])]
        for check in selected.to_dict("records"):
            lines.append(f"- **{check['rule_code']} ({check['outcome']}):** {check['reason']} Dữ kiện: `{check['observations_json']}`.")
        lines.append("\nNhãn bất thường rộng: **chưa đủ bằng chứng**, không tự gán bình thường hoặc dị biệt hợp lệ.\n")
    return "\n".join(lines)+"\n"


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = hierarchy.destination_guard(output, {"batch_directory": provenance["source_directory"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite annotations")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".rule-annotations20-", dir=output.parent) as temporary:
        staging = Path(temporary)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False)
        shutil.copyfile(TEMPLATE/"README.md", staging/"README.md")
        review.write_json(staging/"protocol.json", protocol())
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"exposure_notice.json", {"version": VERSION, "review_ids": frames["case_annotations.csv"].review_id.tolist(),
                          "source_manifest_sha256": provenance["source_manifest_sha256"],
                          "reason": "automated_interpretations_generated_for_user_display",
                          "affected_reviewer_scope": "requesting_user; other_reviewers_require_their_own_exposure_audit",
                          "future_unassisted_blind_reference": False, "human_review_performed": False,
                          "automatic_annotations_are_not_independent_ground_truth": True})
        (staging/"20_case_da_doi_chieu.md").write_text(markdown_report(frames, summary), encoding="utf-8")
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "summary": summary, "provenance": provenance,
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
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != FILES):
        raise ValueError("Invalid rule annotation snapshot")
    hierarchy.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    source, _ = review.load_evidence(Path(manifest["provenance"]["source_directory"])/"reviewer_packet")
    frames, summary = build_annotations(source)
    for name, expected in frames.items():
        actual = review.read_strings(directory/name)
        expected_strings = pd.read_csv(StringIO(expected.to_csv(index=False)), dtype="string", keep_default_na=False)
        pd.testing.assert_frame_equal(actual, expected_strings, check_exact=True)
    if (summary != manifest["summary"] or review.read_json(directory/"summary.json") != summary
            or review.read_json(directory/"protocol.json") != protocol()
            or (directory/"20_case_da_doi_chieu.md").read_text() != markdown_report(frames, summary)):
        raise ValueError("Rule annotations do not reproduce from source")
    notice = review.read_json(directory/"exposure_notice.json")
    if notice["review_ids"] != frames["case_annotations.csv"].review_id.tolist() or notice["future_unassisted_blind_reference"]:
        raise ValueError("Exposure notice differs")
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
            source, provenance = load_inputs()
            frames, summary = build_annotations(source)
            result = write_snapshot(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Rule annotations stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
