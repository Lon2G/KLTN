"""Explain frozen order/stage signals without changing thresholds or labels."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import build_process_signals as signals

ROOT, shared, review = signals.ROOT, signals.shared, signals.review
VERSION = "process_performance_explanation_v1"
TEMPLATE = ROOT/"templates/process_performance_explanation_v1"
DECISIONS = ROOT/"docs/fulfillment_evaluation_decisions_2026-10-16.md"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_process_performance_explanation_v1"
TABLES = {"order_explanations.csv", "stage_comparisons.csv"}
DIRECTION_NAMES = {"faster": "Nhanh hơn", "slower": "Chậm hơn", "equal": "Bằng tham chiếu",
                   "zero_recorded_duration": "Hai mốc ghi nhận bằng nhau", "unavailable": "Chưa đủ bằng chứng so sánh"}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md", DECISIONS]}


def verify_sources(provenance):
    source = Path(provenance["signals_directory"])
    if review.digest(source/"manifest.json") != provenance["signals_manifest_sha256"]:
        raise ValueError("Frozen signal source changed")
    manifest = signals.load_snapshot(source)
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Explanation code/protocol changed; use a new version")
    return manifest


def load_inputs(source=signals.DEFAULT_OUTPUT):
    source = Path(source).resolve()
    provenance = {"signals_directory": str(source), "signals_manifest_sha256": review.digest(source/"manifest.json"),
                  "code_hashes": code_hashes()}
    manifest = verify_sources(provenance)
    frames = {name: shared.read_table(source, name, manifest) for name in ["order_signals.csv", "stage_signals.csv"]}
    verify_sources(provenance)
    return frames, provenance


def validate_inputs(inputs):
    orders, stages = inputs["order_signals.csv"], inputs["stage_signals.csv"]
    shared.business.require_key(orders, ["order_id"], "explanation orders")
    shared.business.require_key(stages, ["order_id", "stage"], "explanation stages")
    if (orders.empty or not orders.split.eq("validation").all() or not stages.split.eq("validation").all()
            or set(orders.order_id) != set(stages.order_id)
            or not stages.groupby("order_id").stage.agg(set).map(lambda x: x == set(signals.STAGES)).all()):
        raise ValueError("Expected three frozen stages per guarded validation order")
    indexed = orders.set_index("order_id")
    for field in ["performance_state", "visible_before_exclusive", "customer_state", "distance_band"]:
        pd.testing.assert_series_equal(stages[field], stages.order_id.map(indexed[field]),
                                       check_names=False, check_dtype=False, check_exact=True)
    for field in ["start_timestamp", "end_timestamp"]:
        known = stages[field].notna()
        if not stages.loc[known, field].lt(stages.loc[known, "visible_before_exclusive"]).all():
            raise ValueError("Stage event is outside the observation cutoff")
    expected = (stages.end_timestamp-stages.start_timestamp).dt.total_seconds()/86400
    pd.testing.assert_series_equal(stages.duration_days, expected, check_names=False, check_exact=True)
    if stages.assessable.isna().any():
        raise ValueError("Missing stage eligibility")
    eligible = stages.loc[stages.assessable]
    if (not np.isfinite(eligible[["duration_days", "reference_median_days", "upper_boundary_days_inclusive"]]).all().all()
            or eligible.duration_days.lt(0).any() or eligible.reference_median_days.lt(0).any()
            or eligible.quality_conflict.any() or eligible.reference_group_id.isna().any()):
        raise ValueError("Assessed stages require valid observations and a reference")
    for field in ["long_stage_warning", "short_stage_note"]:
        if not stages[field].notna().eq(stages.assessable).all():
            raise ValueError("Unknown stage flags cannot become negative decisions")
    expected_long = eligible.duration_days.gt(0) & eligible.duration_days.ge(eligible.upper_boundary_days_inclusive)
    if not eligible.long_stage_warning.eq(expected_long).all():
        raise ValueError("Frozen long-stage flags changed")
    grouped = stages.groupby("order_id")
    assessed = grouped.assessable.sum().astype(int).reindex(indexed.index)
    long_count = grouped.long_stage_warning.sum().astype(int).reindex(indexed.index)
    pd.testing.assert_series_equal(indexed.assessed_stage_count, assessed, check_names=False, check_exact=True)
    pd.testing.assert_series_equal(indexed.long_stage_warning_count, long_count, check_names=False, check_exact=True)
    expected_flag = long_count.gt(0).astype("boolean").where(long_count.gt(0) | assessed.eq(3))
    pd.testing.assert_series_equal(indexed.long_stage_warning, expected_flag, check_names=False, check_exact=True)
    attention = indexed.calendar_late_signal.fillna(False) | indexed.long_stage_warning.fillna(False)
    if not indexed.primary_attention_status.eq("signal_present").eq(attention).all():
        raise ValueError("Order attention differs from frozen signals")


def stage_description(row):
    if row.comparison_direction == "unavailable":
        return f"Chưa so sánh nhanh/chậm. {row.limitation_vi}"
    if row.comparison_direction == "zero_recorded_duration":
        return "Hai mốc ghi nhận bằng nhau; không tự coi là xử lý nhanh hoặc lỗi dữ liệu."
    if row.comparison_direction == "equal":
        text = "Thời lượng bằng trung vị nhóm tham chiếu."
    else:
        text = f"{DIRECTION_NAMES[row.comparison_direction]} trung vị nhóm tham chiếu {abs(row.difference_from_reference_days):.6f} ngày."
    if row.long_stage_warning:
        text += " Vượt ngưỡng cảnh báo dài hiện tại; giữ cảnh báo công đoạn kéo dài cần kiểm tra."
    else:
        text += " Không vượt ngưỡng cảnh báo dài hiện tại."
    return text+" Đây là so sánh thống kê, không phải kế hoạch gốc hay nguyên nhân đã xác minh."


def stage_comparisons(source):
    rows = source.copy().sort_values(["order_id", "stage"]).reset_index(drop=True)
    rows["stage_plan_status"] = "not_provided_in_frozen_source"
    rows["reference_type"] = np.where(rows.assessable, "train_peer_median", "unavailable")
    rows["speed_comparable"] = rows.assessable & rows.duration_days.gt(0)
    rows["difference_from_reference_days"] = (rows.duration_days-rows.reference_median_days).where(rows.speed_comparable)
    rows["comparison_direction"] = "unavailable"
    rows.loc[rows.assessable & rows.duration_days.eq(0), "comparison_direction"] = "zero_recorded_duration"
    for mask, direction in [(rows.difference_from_reference_days.lt(0), "faster"),
                            (rows.difference_from_reference_days.gt(0), "slower"),
                            (rows.difference_from_reference_days.eq(0), "equal")]:
        rows.loc[rows.speed_comparable & mask, "comparison_direction"] = direction
    rows["comparison_direction_vi"] = rows.comparison_direction.map(DIRECTION_NAMES)
    rows["comparison_explanation_vi"] = [stage_description(row) for row in rows.itertuples(index=False)]
    rows["source_evidence_locator"] = "stage_signals.csv:order_id="+rows.order_id+";stage="+rows.stage
    rows["explanation_version"] = VERSION
    return rows


def order_explanation(row):
    lines = [row.promise_basis_vi+"; không kết luận ngày hẹn được đặt ngẫu nhiên."]
    if row.performance_eligible:
        lines.append(f"{signals.STATE_NAMES[row.performance_state]}; chênh lệch so ngày hẹn {row.promise_delta_calendar_days} ngày lịch (âm là sớm).")
    else:
        lines.append("Chưa đủ điều kiện kết luận sớm/đúng/trễ theo nguồn và mốc quan sát hiện tại.")
    if row.faster_stage_count:
        names = ", ".join(signals.NAMES[s] for s in json.loads(row.faster_stages_json))
        lines.append(f"Có công đoạn nhanh hơn trung vị tham chiếu: {names}.")
    elif row.speed_comparison_complete:
        lines.append("Không có công đoạn nào nhanh hơn trung vị nhóm tham chiếu; không ép tìm công đoạn nhanh để giải thích giao sớm.")
    else:
        lines.append("Chưa đủ bằng chứng để kết luận có hay không có công đoạn nhanh trong toàn đơn.")
    if not row.speed_comparison_complete:
        lines.append(f"So sánh nhanh/chậm được {row.speed_compared_stage_count}/3 công đoạn; giữ riêng phần chưa so sánh được.")
    if row.long_stage_warning_count:
        names = ", ".join(signals.NAMES[s] for s in json.loads(row.long_stage_warning_stages_json))
        lines.append(f"Giữ cảnh báo công đoạn kéo dài: {names}, kể cả khi đơn giao sớm.")
    lines.append("Chênh lệch từng công đoạn không cộng thành số ngày sớm/trễ so với ngày hẹn và không chứng minh nguyên nhân.")
    return " ".join(lines)


def order_explanations(source, stages):
    rows = source.copy().sort_values("order_id").reset_index(drop=True).set_index("order_id")
    rows["promise_basis_status"] = protocol()["promise_basis_status"]
    rows["promise_basis_vi"] = "Chưa xác minh được cơ sở ngày hẹn"
    rows["promise_basis_evidence_status"] = "source_method_documentation_not_provided"
    rows["promise_review_action_vi"] = "Rà soát quy tắc đặt hẹn khi có nguồn vận hành; không tự tính lại hoặc sửa ngày hẹn lịch sử."
    compared = stages.groupby("order_id").speed_comparable.sum()
    rows["speed_compared_stage_count"] = compared.reindex(rows.index, fill_value=0).astype(int)
    rows["speed_uncompared_stage_count"] = 3-rows.speed_compared_stage_count
    rows["speed_comparison_complete"] = rows.speed_compared_stage_count.eq(3)
    for direction in ["faster", "slower", "equal"]:
        chosen = stages.loc[stages.comparison_direction.eq(direction)].groupby("order_id").stage.agg(list)
        selected = [chosen.get(oid, []) for oid in rows.index]
        rows[direction+"_stage_count"] = [len(value) for value in selected]
        rows[direction+"_stages_json"] = [json.dumps(value) for value in selected]
    has_fast = rows.faster_stage_count.gt(0)
    rows["faster_stage_observed"] = has_fast.astype("boolean").where(has_fast | rows.speed_comparison_complete)
    rows["early_delivery_explanation_status"] = "not_early_delivery"
    rows.loc[~rows.performance_eligible, "early_delivery_explanation_status"] = "delivery_outcome_unavailable"
    early = rows.performance_state.eq("early")
    rows.loc[early, "early_delivery_explanation_status"] = "early_insufficient_stage_evidence"
    rows.loc[early & has_fast, "early_delivery_explanation_status"] = "early_with_faster_stage_partial_comparison"
    rows.loc[early & has_fast & rows.speed_comparison_complete, "early_delivery_explanation_status"] = "early_with_faster_stage"
    rows.loc[early & ~has_fast & rows.speed_comparison_complete, "early_delivery_explanation_status"] = "early_without_faster_stage"
    rows["order_explanation_vi"] = [order_explanation(row) for row in rows.itertuples()]
    rows["source_evidence_locator"] = ["order_signals.csv:order_id="+oid for oid in rows.index]
    rows["explanation_version"] = VERSION
    return rows.reset_index()


def build_tables(inputs):
    validate_inputs(inputs)
    stages = stage_comparisons(inputs["stage_signals.csv"])
    orders = order_explanations(inputs["order_signals.csv"], stages)
    for original, result, keys in [(inputs["order_signals.csv"], orders, ["order_id"]),
                                   (inputs["stage_signals.csv"], stages, ["order_id", "stage"])]:
        pd.testing.assert_frame_equal(original.sort_values(keys).reset_index(drop=True), result[original.columns], check_exact=True)
    early = orders.performance_state.eq("early")
    long_flag = orders.long_stage_warning.fillna(False)
    counts = lambda column: {str(k): int(v) for k, v in column.value_counts().items()}
    summary = {"version": VERSION, "validation_orders": len(orders), "stage_rows": len(stages),
               "promise_basis_counts": counts(orders.promise_basis_status),
               "stage_comparison_counts": counts(stages.comparison_direction),
               "speed_complete_orders": int(orders.speed_comparison_complete.sum()),
               "speed_incomplete_orders": int((~orders.speed_comparison_complete).sum()),
               "early_orders": int(early.sum()), "early_explanation_counts": counts(orders.loc[early, "early_delivery_explanation_status"]),
               "early_with_long_stage_warning": int((early & long_flag).sum()),
               "early_long_without_faster_stage": int((early & long_flag & orders.faster_stage_observed.eq(False).fillna(False)).sum()),
               "stage_counts": {stage: {**counts(part.comparison_direction), "long_warnings": int(part.long_stage_warning.sum())}
                                for stage, part in stages.groupby("stage", sort=True)},
               "source_columns_preserved": True, "reference_type": "train_peer_median_not_original_stage_plan",
               "reference_labels_created": 0, "model_trained": False, "threshold_changed": False,
               "test_scored": False, "promise_dates_changed": False, "anomaly_accuracy_computed": False,
               "selected_ml_detector": None, "near_real_time_validated": False}
    return {"order_explanations.csv": orders, "stage_comparisons.csv": stages}, summary


def render_case(frames, order_id):
    selected = frames["order_explanations.csv"].loc[lambda x: x.order_id.eq(order_id)]
    if len(selected) != 1:
        raise ValueError("Order is outside this guarded validation snapshot")
    case = selected.iloc[0]
    stages = frames["stage_comparisons.csv"].loc[lambda x: x.order_id.eq(order_id)].set_index("stage")
    lines = [f"### Đơn {order_id}", "", case.order_explanation_vi, "",
             "| Công đoạn | Thực tế (ngày) | Trung vị tham chiếu (ngày) | Chênh lệch (ngày) | Cảnh báo dài |",
             "| --- | ---: | ---: | ---: | --- |"]
    number = lambda value: "chưa đủ dữ liệu" if pd.isna(value) else f"{value:.6f}"
    for stage in signals.STAGES:
        row = stages.loc[stage]
        flag = "chưa đánh giá" if pd.isna(row.long_stage_warning) else "có" if row.long_stage_warning else "không"
        median = row.reference_median_days if row.assessable else np.nan
        lines.append(f"| {signals.NAMES[stage]} | {number(row.duration_days)} | {number(median)} | {number(row.difference_from_reference_days)} | {flag} |")
    lines += ["", "Chênh lệch âm: nhanh hơn; dương: chậm hơn. Đây là tham chiếu thống kê, không phải kế hoạch gốc.", ""]
    for stage in signals.STAGES:
        lines.append(f"- {signals.NAMES[stage]}: {stages.loc[stage, 'comparison_explanation_vi']}")
    return "\n".join(lines)


def render_report(frames, summary):
    lines = ["# Diễn giải hiệu suất từng đơn và công đoạn", "",
             f'Phạm vi: {summary["validation_orders"]} đơn validation thật, {summary["stage_rows"]} dòng công đoạn. '
             "Không đổi ngày hẹn, ngưỡng, cờ cảnh báo hoặc nhãn.", "",
             "## Cơ sở ngày hẹn", "",
             "Chưa xác minh được cơ sở ngày hẹn: nguồn hiện có không cung cấp phương pháp đặt hẹn hoặc kế hoạch đầy đủ từng công đoạn. "
             "Không suy ra ngày hẹn ngẫu nhiên hoặc không hợp lý. Hệ thống chỉ đề xuất rà soát khi có thêm bằng chứng vận hành.", "",
             "## Nhanh/chậm so với tham chiếu", "",
             "Chênh lệch = thời lượng thực tế - trung vị nhóm train tham chiếu. Chỉ so khi đủ điều kiện và thời lượng dương. "
             "Dấu của chênh lệch là mô tả, không thay cờ vượt ngưỡng. Thời lượng bằng 0 hoặc thiếu dữ liệu không tự thành hiệu suất tốt.", "",
             "| Công đoạn | Nhanh hơn | Chậm hơn | Bằng tham chiếu | Chưa so sánh | Cảnh báo dài |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for stage in signals.STAGES:
        row = summary["stage_counts"][stage]
        lines.append(f"| {signals.NAMES[stage]} | {row.get('faster', 0)} | {row.get('slower', 0)} | {row.get('equal', 0)} | "
                     f"{row.get('unavailable', 0)+row.get('zero_recorded_duration', 0)} | {row['long_warnings']} |")
    lines += ["", "## Giao sớm", "", f'Tổng số đơn giao sớm: {summary["early_orders"]}.', ""]
    for status, count in summary["early_explanation_counts"].items():
        lines.append(f"- `{status}`: {count} đơn.")
    lines += [f'- Giao sớm nhưng vẫn có cảnh báo công đoạn dài: {summary["early_with_long_stage_warning"]} đơn.',
              f'- Trong nhóm này, không có công đoạn nhanh hơn khi so đủ: {summary["early_long_without_faster_stage"]} đơn.', "",
              "Không cộng các nhóm giao nhau. Không cộng các chênh lệch công đoạn để giải thích chính xác số ngày giao sớm/trễ.", "",
              "## Ví dụ từ đơn thật", ""]
    orders = frames["order_explanations.csv"]
    masks = [orders.early_delivery_explanation_status.eq("early_without_faster_stage") & orders.long_stage_warning.fillna(False),
             orders.early_delivery_explanation_status.eq("early_with_faster_stage"),
             orders.early_delivery_explanation_status.eq("early_insufficient_stage_evidence")]
    for mask in masks:
        ids = orders.loc[mask, "order_id"]
        if len(ids):
            lines += [render_case(frames, ids.iloc[0]), ""]
    lines += ["## Giới hạn", "",
              "Tham chiếu theo bối cảnh vẫn chịu giới hạn của phiên bản nguồn; chưa phải kế hoạch công đoạn hay nhóm được xác nhận toàn bình thường. "
              "Cảnh báo vẫn dùng ngưỡng phát triển cũ, chưa được chứng minh tối ưu. Chưa xác minh nguyên nhân, tính chính xác độc lập, "
              "multi-agent hoặc near real-time. Phần chưa đánh giá được giữ nguyên, không biến thành bình thường.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    manifest = verify_sources(provenance)
    output = signals.performance.destination_guard(output, {"inspection": manifest["provenance"]["performance_source"]["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite performance explanations")
    if set(frames) != TABLES:
        raise ValueError("Incomplete explanation tables")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".performance-explanations-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"protocol.json", protocol())
        review.write_json(staging/"summary.json", summary)
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_dien_giai.md").write_text(render_report(frames, summary))
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
    expected = TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_dien_giai.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES or set(manifest.get("output_hashes", {})) != expected):
        raise ValueError("Invalid performance explanation manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Explanation summary/protocol mismatch")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--summary", action="store_true")
    mode.add_argument("--order", help="Inspect an actual order in the saved explanations")
    args = parser.parse_args()
    try:
        if args.summary or args.order:
            manifest = load_snapshot(args.output)
            if args.order:
                frames = {name: shared.read_table(args.output, name, manifest) for name in TABLES}
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
        parser.exit(2, f"Performance explanation stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
