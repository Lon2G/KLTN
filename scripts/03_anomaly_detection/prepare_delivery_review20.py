"""Prepare 20 real, blinded pilot cases with separate performance/anomaly rules."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import pandas as pd

import build_delivery_performance as performance
import prepare_business_review as pilot
import prepare_hierarchical_review as hierarchy

review = hierarchy.review
shared = performance.shared
ROOT = review.ROOT
VERSION = "delivery_review20_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_delivery_review20_v1"
TEMPLATE = ROOT/"templates/delivery_review20_v1"
PACKET_FILES = {f"reviewer_packet/reviewer/{name}" for name in [*review.SCHEMAS, "README.md", "criteria_draft.json", "manifest.json"]}
OUTPUT_FILES = PACKET_FILES | {"README.md", "summary.json", "protocol.json", "analyst_only/selection_key.csv",
                              "analyst_only/selection_audit.json", "reviewer_packet/README.md", "reviewer_packet/criteria.json",
                              "reviewer_packet/assessment_template.json", "reviewer_packet/20_don_can_ra_soat.md"}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {**hierarchy.code_hashes(), **performance.code_hashes(), **pilot.code_hashes(),
            **{str(p.relative_to(ROOT)): review.digest(p) for p in
               [Path(__file__), TEMPLATE/"README.md", TEMPLATE/"reviewer_guide.md", TEMPLATE/"protocol.json"]}}


def verify_sources(provenance):
    batch = Path(provenance["batch_directory"])
    if review.digest(batch/"analyst_only/manifest.json") != provenance["pilot_manifest_sha256"]:
        raise ValueError("Original pilot changed")
    manifest = review.read_json(batch/"analyst_only/manifest.json")
    shared.verify_files(batch, manifest["output_hashes"])
    pilot.verify_sources(manifest["provenance"])
    if review.load_evidence(batch)[1] != provenance["reviewer_manifest_sha256"]:
        raise ValueError("Original blind packet changed")
    if str(performance.verify_boundary(provenance["outer_plan"])) != provenance["boundary"]:
        raise ValueError("Review boundary differs")
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Review20 code/protocol changed; use a new version")


def load_inputs(batch=review.DEFAULT_BATCH):
    batch = Path(batch).resolve()
    frames, evidence_hash = review.load_evidence(batch)
    manifest = review.read_json(batch/"analyst_only/manifest.json")
    if manifest.get("status") != "complete" or manifest.get("version") != review.VERSION:
        raise ValueError("Expected frozen original pilot")
    shared.verify_files(batch, manifest["output_hashes"])
    key = shared.read_table(batch, "analyst_only/selection_key.csv", manifest)
    if (len(key) != 120 or key.review_id.tolist() != frames["cases.csv"].review_id.tolist()
            or not key.order_id.is_unique or key.previously_human_reviewed.any()):
        raise ValueError("Original pilot identity/exposure differs")
    boundary, plan = performance.load_boundary()
    provenance = {"batch_directory": str(batch), "pilot_manifest_sha256": review.digest(batch/"analyst_only/manifest.json"),
                  "reviewer_manifest_sha256": evidence_hash, "outer_plan": plan, "boundary": str(boundary),
                  "code_hashes": code_hashes()}
    verify_sources(provenance)
    return frames, key, boundary, provenance


def select_subset(frames, key, boundary):
    cases = frames["cases.csv"].set_index("review_id")
    eligible = pd.Series(True, index=cases.index)
    for field in hierarchy.TIMES:
        dates = pd.to_datetime(cases[field].replace("", pd.NA), format="%Y-%m-%d %H:%M:%S", errors="raise")
        eligible &= dates.notna() & dates.lt(boundary)
    pool = key.loc[key.review_id.isin(eligible.index[eligible])].copy()
    pool["sampling_group"] = pool.agreement_group.map({"none_flag": "none_anchor_flag", "all_flag": "any_anchor_flag",
                                                       "disagreement": "any_anchor_flag"})
    if pool.sampling_group.isna().any():
        raise ValueError("Unknown original anchor group")
    config = protocol()
    pool["selection_hash"] = pool.review_id.map(lambda value: hashlib.sha256((config["seed"]+":"+value).encode()).hexdigest())
    selected = []
    for group, size in config["allocation"].items():
        candidates = pool.loc[pool.sampling_group.eq(group)].sort_values(["selection_hash", "review_id"])
        if len(candidates) < size:
            raise ValueError("Not enough real cases; no padding or synthetic replacements")
        selected.extend(candidates.head(size).review_id)
    chosen = key.loc[key.review_id.isin(selected)].merge(pool[["review_id", "sampling_group", "selection_hash"]],
                                                       on="review_id", validate="one_to_one", sort=False)
    subset = {name: frame.loc[frame.review_id.isin(chosen.review_id)].copy().reset_index(drop=True) for name, frame in frames.items()}
    if len(chosen) != config["sample_size"] or subset["cases.csv"].review_id.tolist() != chosen.review_id.tolist():
        raise ValueError("Invalid size or original display order")
    audit = {"original_pilot_orders": len(key), "eligible_original_pilot_orders": len(pool),
             "excluded_purchase_or_actual_milestone_on_or_after_boundary": len(key)-len(pool),
             "selected_orders": len(chosen), "eligible_by_sampling_group": pool.sampling_group.value_counts().sort_index().to_dict(),
             "selected_by_sampling_group": chosen.sampling_group.value_counts().sort_index().to_dict(),
             "selection_is_accuracy_reference": False, "population_weights_derived": False}
    return subset, chosen, audit


def criteria():
    value = deepcopy(review.read_json(hierarchy.TEMPLATE/"criteria.json"))
    value["version"] = VERSION
    value["status"] = "user_selected_pilot_rules_not_advisor_approved_final_reference"
    value["scope"] = "selected_20_original_blind_pilot_orders"
    value["delivery_performance_policy"] = {
        "early": "Performance information, not an anomaly solely because delivery precedes the estimated date.",
        "on_date": "Same calendar date meets the date-level promise, even after midnight.",
        "late": "Descriptive after-promise observation, not itself a statistical anomaly or causal bottleneck.",
        "whole_order": "Performance is separate from scope-specific anomaly assessment.",
        "context": "No per-stage deadline or independent reference is invented; unresolved when unavailable."}
    for rule in value["rules"]:
        if rule["code"] == "T03":
            rule["limit"] += " Short positive duration alone must not create an anomaly finding."
        if rule["code"] == "T04":
            rule["limit"] += " Early or on-date delivery is performance information, not an anomaly or whole-order normal label."
    return value


def md_table(columns, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ") if str(value) else "Chưa có dữ liệu"
    return "\n".join(["| "+" | ".join(columns)+" |", "| "+" | ".join(["---"]*len(columns))+" |",
                      *["| "+" | ".join(cell(value) for value in row)+" |" for row in rows]])


def casebook(frames):
    fields = [("order_purchase_timestamp", "Đặt hàng"), ("order_approved_at", "Phê duyệt"),
              ("order_delivered_carrier_date", "Bàn giao vận chuyển"), ("order_delivered_customer_date", "Giao khách"),
              ("order_estimated_delivery_date", "Ngày giao dự kiến"), ("purchased_to_approved_days", "Đặt hàng đến phê duyệt (ngày)"),
              ("approved_to_carrier_days", "Phê duyệt đến bàn giao (ngày)"), ("carrier_to_delivered_days", "Bàn giao đến giao khách (ngày)"),
              ("total_cycle_time_days", "Tổng thời gian (ngày)"), ("payment_value_sum", "Tổng thanh toán (đơn vị tiền nguồn)"),
              ("item_price_sum", "Tổng tiền hàng"), ("freight_sum", "Tổng phí vận chuyển"), ("item_count", "Số dòng hàng"),
              ("seller_count", "Số seller")]
    parts = ["# 20 đơn cần rà soát\n", "Dữ kiện nguồn, chưa có nhận xét của người rà soát. Không có kết quả mô hình. "
             "Đọc `README.md` trước khi bắt đầu. Giao sớm/trễ và đánh giá bất thường là hai việc riêng.\n"]
    for record in frames["cases.csv"].to_dict("records"):
        review_id = record["review_id"]
        rows = []
        for field, label in fields:
            value = record[field]
            if field.endswith("_days") and value:
                value = f"{float(value):.4f}"
            rows.append([label, value])
        parts.extend([f"## {review_id}\n", md_table(["Dữ kiện", "Giá trị"], rows), "\n### Các khoản thanh toán\n"])
        payments = frames["payment_records.csv"].loc[lambda f: f.review_id.eq(review_id), review.PAYMENT_COLUMNS]
        parts.append(md_table(review.PAYMENT_COLUMNS, payments.values.tolist()))
        parts.append("\n### Các dòng hàng\n")
        items = frames["item_records.csv"].loc[lambda f: f.review_id.eq(review_id), review.ITEM_COLUMNS]
        parts.append(md_table(review.ITEM_COLUMNS, items.values.tolist()))
        parts.append("\n### Nhận xét của bạn\n\n- Kết quả so với ngày hẹn: chưa ghi nhận.\n"
                     "- Quy trình/thời gian: chưa đánh giá.\n- Thanh toán: chưa đánh giá.\n"
                     "- Nội dung đơn: chưa đánh giá.\n- Bằng chứng, lý do hoặc thông tin còn thiếu: chưa ghi nhận.\n")
    return "\n\n".join(parts)+"\n"


def prepare(output=DEFAULT_OUTPUT):
    frames, key, boundary, provenance = load_inputs()
    output = hierarchy.destination_guard(output, provenance)
    if output.exists():
        raise FileExistsError("Refusing to overwrite review20 packet")
    subset, selected, audit = select_subset(frames, key, boundary)
    summary = {"version": VERSION, "status": "prepared_20_real_orders_zero_assessments", "review_orders": len(selected),
               "review_tasks": len(hierarchy.tasks_for(subset)), "completed_assessments": 0,
               "payment_records": len(subset["payment_records.csv"]), "item_records": len(subset["item_records.csv"]),
               "original_pilot_preserved": True, "outer_boundary_guard": str(boundary),
               **{name: False for name in ["human_labels_created", "reviewer_session_started", "advisor_approval_inferred",
                                          "reference_ready", "model_trained", "threshold_changed", "test_scored", "accuracy_computed"]}}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".delivery-review20-", dir=output.parent) as temporary:
        staging = Path(temporary)/"packet"
        reviewer = staging/"reviewer_packet/reviewer"
        reviewer.mkdir(parents=True)
        (staging/"analyst_only").mkdir()
        for name, frame in subset.items():
            frame.to_csv(reviewer/name, index=False)
        for name in ["README.md", "criteria_draft.json"]:
            shutil.copyfile(Path(provenance["batch_directory"])/"reviewer"/name, reviewer/name)
        review.write_json(reviewer/"manifest.json", {"version": review.VERSION, "status": "blank_pilot_pending_criteria",
                          "case_count": len(selected), "output_hashes": {p.name: review.digest(p) for p in sorted(reviewer.iterdir())}})
        selected.to_csv(staging/"analyst_only/selection_key.csv", index=False, date_format="%Y-%m-%d %H:%M:%S")
        review.write_json(staging/"analyst_only/selection_audit.json", audit)
        for source, target in [("README.md", "README.md"), ("reviewer_guide.md", "reviewer_packet/README.md")]:
            shutil.copyfile(TEMPLATE/source, staging/target)
        review.write_json(staging/"protocol.json", protocol())
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"reviewer_packet/criteria.json", criteria())
        review.write_json(staging/"reviewer_packet/assessment_template.json", hierarchy.tasks_for(subset))
        (staging/"reviewer_packet/20_don_can_ra_soat.md").write_text(casebook(subset), encoding="utf-8")
        manifest = {"version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "provenance": provenance, "summary": summary,
                    "output_hashes": {str(p.relative_to(staging)): review.digest(p) for p in sorted(staging.rglob("*")) if p.is_file()}}
        review.write_json(staging/"manifest.json", manifest)
        load_package(staging)
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_package(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != OUTPUT_FILES):
        raise ValueError("Invalid review20 package")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    frames, _ = review.load_evidence(directory/"reviewer_packet")
    original, _ = review.load_evidence(manifest["provenance"]["batch_directory"])
    source = Path(manifest["provenance"]["batch_directory"])
    pm = review.read_json(source/"analyst_only/manifest.json")
    key = shared.read_table(source, "analyst_only/selection_key.csv", pm)
    expected, selected, audit = select_subset(original, key, pd.Timestamp(manifest["provenance"]["boundary"]))
    for name, frame in expected.items():
        pd.testing.assert_frame_equal(frames[name], frame, check_exact=True)
    actual_key = pd.read_csv(directory/"analyst_only/selection_key.csv", dtype="string", keep_default_na=False)
    if actual_key.review_id.tolist() != selected.review_id.tolist() or actual_key.order_id.tolist() != selected.order_id.tolist():
        raise ValueError("Selection mapping changed")
    if (review.read_json(directory/"analyst_only/selection_audit.json") != audit
            or review.read_json(directory/"protocol.json") != protocol()
            or review.read_json(directory/"summary.json") != manifest["summary"]
            or review.read_json(directory/"reviewer_packet/criteria.json") != criteria()
            or review.read_json(directory/"reviewer_packet/assessment_template.json") != hierarchy.tasks_for(frames)
            or (directory/"reviewer_packet/20_don_can_ra_soat.md").read_text() != casebook(frames)):
        raise ValueError("Review20 content/criteria differ")
    return frames, manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--summary", action="store_true")
    group.add_argument("--check-note", type=Path)
    args = parser.parse_args()
    try:
        if args.summary or args.check_note:
            frames, manifest = load_package(args.output)
            result = manifest["summary"]
            if args.check_note:
                before = review.digest(args.check_note)
                note = review.read_json(args.check_note)
                if review.digest(args.check_note) != before:
                    raise ValueError("Input note changed while read")
                hierarchy.validate_note(frames, note)
                result = {"syntax_valid": True, "saved": False, "verification_authenticated": False, "reference_ready": False}
        else:
            if args.output.exists():
                raise FileExistsError("Output already exists")
            result = prepare(args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Review20 stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
