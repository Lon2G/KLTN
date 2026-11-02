"""Select real development cases for analyst-assisted comparison, not labels."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import build_delivery_performance as performance

ROOT = performance.ROOT
review, shared = performance.review, performance.shared
VERSION = "delivery_supplement_v1"
SOURCE = performance.DEFAULT_OUTPUT
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_delivery_supplement_v1"
TEMPLATE = ROOT/"templates/delivery_supplement_v1"
STAGES = ["approval", "preparation_handoff", "transport"]
TARGET = "carrier_to_delivered_days"
EXPECTED_MODELS = {f"{profile}_d{depth}_l{leaf}" for profile in ["base_geo", "plus_activity", "plus_seller"]
                   for depth in [2, 3] for leaf in [30, 60]}
TABLES = {"case_evidence.csv", "stage_comparisons.csv", "candidate_audit.csv", "reference_members.csv",
          "saved_model_evidence.csv", "payment_records.csv", "item_records.csv", "assessment_status.csv"}
GROUP_NAMES = {"saved_model_disagreement": "Các cấu hình bất đồng", "late_promise": "Trễ ngày hẹn",
               "long_stage_on_time": "Công đoạn dài dù đáp ứng hẹn", "comparison": "Nhóm đối chiếu"}
STAGE_NAMES = {"approval": "Phê duyệt", "preparation_handoff": "Chuẩn bị/bàn giao", "transport": "Vận chuyển"}
STATE_NAMES = {"early": "Giao sớm", "on_date": "Đúng ngày", "late": "Giao trễ"}
CONTEXT_FIELDS = ["item_count", "product_count", "payment_record_count", "payment_installments_max",
                  "payment_types_json", "payment_value_sum_minor_units", "items_plus_freight_minor_units",
                  "payment_minus_items_and_freight_minor_units", "seller_states_json",
                  "screened_distance_max_km", "screened_distance_item_weighted_mean_km",
                  "history_route", "single_seller_prior_observed_completed_orders"]


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md"]}


def verify_sources(provenance):
    source = Path(provenance["performance_directory"])
    if review.digest(source/"manifest.json") != provenance["performance_manifest_sha256"]:
        raise ValueError("Performance source changed")
    manifest = performance.load_snapshot(source)
    if manifest["provenance"]["inspection"] != provenance["inspection"] or code_hashes() != provenance["code_hashes"]:
        raise ValueError("Source/code binding changed; use a new version")
    return manifest


def load_inputs(source=SOURCE):
    source = Path(source).resolve()
    manifest = performance.load_snapshot(source)
    provenance = {"performance_directory": str(source), "performance_manifest_sha256": review.digest(source/"manifest.json"),
                  "inspection": manifest["provenance"]["inspection"], "code_hashes": code_hashes()}
    frames = {name: shared.read_table(source, name, manifest) for name in
              ["order_performance.csv", "stage_durations.csv", "saved_signals.csv"]}
    integrated = Path(provenance["inspection"]["source_directory"])
    im = review.read_json(integrated/"manifest.json")
    for name in ["case_evidence.csv", "delivery_model_evidence.csv"]:
        frames[name] = shared.read_table(integrated, name, im)
    business = Path(im["provenance"]["snapshots"]["business"]["directory"])
    bm = review.read_json(business/"manifest.json")
    for name in ["payment_source_evidence.csv", "item_source_evidence.csv"]:
        frames[name] = shared.read_table(business, name, bm)
    # The frozen performance table already excludes all pilot and guarded test purchases.
    allowed = set(frames["order_performance.csv"].order_id)
    frames = {name: frame.loc[frame.order_id.isin(allowed)].reset_index(drop=True) for name, frame in frames.items()}
    if not frames["order_performance.csv"].split.isin(["train", "validation"]).all():
        raise ValueError("Only original development partitions are allowed")
    return frames, provenance


def stage_references(frames):
    orders, durations = frames["order_performance.csv"], frames["stage_durations.csv"]
    first_validation = orders.loc[orders.split.eq("validation"), performance.TIMES[0]].min()
    if pd.isna(first_validation):
        raise ValueError("No validation boundary")
    context = orders[["order_id", "seller_attribution_scope", "all_pairs_quality_eligible"]]
    rows = durations.loc[durations.stage.isin(STAGES)].merge(context, on="order_id", validate="many_to_one")
    reference = rows.loc[rows.split.eq("train") & rows.duration_eligible & rows.end_timestamp.lt(first_validation)].copy()
    if reference.empty or reference.order_id.isin(orders.loc[orders.split.eq("validation"), "order_id"]).any():
        raise ValueError("Invalid train-only duration reference")
    return reference.sort_values(["order_id", "stage"]).reset_index(drop=True), first_validation


def compare_stages(candidates, durations, reference):
    config = protocol()
    comparisons, cache = [], {}
    for case in candidates.to_dict("records"):
        stages = durations.loc[durations.order_id.eq(case["order_id"]) & durations.stage.isin(STAGES)]
        if len(stages) != 3 or not stages.duration_eligible.all():
            raise ValueError("Candidate needs three nonnegative visible stages")
        for stage in stages.to_dict("records"):
            selected, fields, key, level = None, [], [], None
            for index, context in enumerate(config["context_levels"]):
                if "distance_band" in context and not case["all_pairs_quality_eligible"]:
                    continue
                if any(pd.isna(case[field]) or case[field] in {"", "unavailable"} for field in context):
                    continue
                values = [case[field] for field in context]
                cache_key = (stage["stage"], index, *values)
                if cache_key not in cache:
                    mask = reference.stage.eq(stage["stage"])
                    for field, value in zip(context, values):
                        mask &= reference[field].eq(value)
                    if "distance_band" in context:
                        mask &= reference.all_pairs_quality_eligible
                    cache[cache_key] = reference.loc[mask, "duration_days"].to_numpy(dtype=float)
                peers = cache[cache_key]
                if len(peers) >= config["minimum_reference_orders"]:
                    selected, fields, key, level = peers, context, values, index
                    break
            n = len(selected) if selected is not None else 0
            value = stage["duration_days"]
            comparisons.append({"order_id": case["order_id"], "stage": stage["stage"],
                                "start_timestamp": stage["start_timestamp"], "end_timestamp": stage["end_timestamp"],
                                "duration_days": value, "reference_n": n, "reference_level": level,
                                "reference_context_json": json.dumps(dict(zip(fields, key)), sort_keys=True),
                                "reference_median_days": float(np.quantile(selected, .5)) if n else np.nan,
                                "reference_p95_days": float(np.quantile(selected, .95)) if n else np.nan,
                                "empirical_percentile": float(np.count_nonzero(selected <= value)/n) if n else np.nan,
                                "reference_distance_matched": "distance_band" in fields,
                                "reference_status": "available_descriptive_not_normal_baseline" if n else "insufficient_reference"})
    return pd.DataFrame(comparisons).sort_values(["order_id", "stage"]).reset_index(drop=True)


def model_comparisons(candidates, models, stages):
    models = models.loc[models.order_id.isin(candidates.order_id)].copy()
    transport = stages.loc[stages.stage.eq("transport")].set_index("order_id").duration_days
    evaluated = models.loc[models.outcome_available_for_saved_comparison].copy()
    if evaluated.duplicated(["order_id", "model"]).any() or not set(models.model).issubset(EXPECTED_MODELS):
        raise ValueError("Unexpected or duplicate saved configuration")
    if evaluated[["long_warning", "short_warning", "zero_duration_warning", TARGET, "calibrated_upper_days"]].isna().any().any():
        raise ValueError("Missing evaluated comparison must not become no warning")
    np.testing.assert_allclose(evaluated[TARGET], evaluated.order_id.map(transport), rtol=0, atol=1e-12)
    if not evaluated.long_warning.eq(evaluated[TARGET].gt(evaluated.calibrated_upper_days)).all():
        raise ValueError("Saved long-warning semantics differ")
    rows = []
    for order_id in candidates.order_id:
        group = evaluated.loc[evaluated.order_id.eq(order_id)]
        n = len(group)
        rows.append({"order_id": order_id, "saved_configurations_evaluated": n,
                     "saved_long_warnings": int(group.long_warning.sum()) if n else pd.NA,
                     "saved_short_warnings": int(group.short_warning.sum()) if n else pd.NA,
                     "saved_zero_warnings": int(group.zero_duration_warning.sum()) if n else pd.NA,
                     "complete_saved_comparison": set(group.model) == EXPECTED_MODELS})
    summary = pd.DataFrame(rows)
    for field in ["saved_long_warnings", "saved_short_warnings", "saved_zero_warnings"]:
        summary[field] = summary[field].astype("Int64")
    return summary, models


def choose_cases(audit):
    config, chosen = protocol(), []
    used = set()
    for group in config["selection_order"]:
        pool = audit.loc[audit["eligible_"+group] & ~audit.order_id.isin(used), "order_id"]
        ranked = sorted(pool, key=lambda oid: (hashlib.sha256(f'{config["selection_seed"]}|{group}|{oid}'.encode()).hexdigest(), oid))
        for order_id in ranked[:config["per_group"]]:
            chosen.append({"order_id": order_id, "selection_group": group})
            used.add(order_id)
    selected = pd.DataFrame(chosen, columns=["order_id", "selection_group"]).sort_values("order_id").reset_index(drop=True)
    selected.insert(0, "review_id", [f"DS{index:03d}" for index in range(1, len(selected)+1)])
    return selected


def build_tables(frames):
    orders, durations = frames["order_performance.csv"], frames["stage_durations.csv"]
    complete = durations.loc[durations.stage.isin(STAGES)].groupby("order_id").duration_eligible.agg(["size", "sum"])
    ids = complete.index[complete["size"].eq(3) & complete["sum"].eq(3)]
    candidates = orders.loc[orders.split.eq("validation") & orders.performance_eligible & orders.post_cutoff_milestones.eq(0)
                            & orders.order_id.isin(ids)].sort_values("order_id").reset_index(drop=True)
    if candidates.empty:
        raise ValueError("No complete eligible development cases")
    reference, first_validation = stage_references(frames)
    stages = compare_stages(candidates, durations, reference)
    model_summary, models = model_comparisons(candidates, frames["delivery_model_evidence.csv"], stages)
    audit = candidates[["order_id", "split", "performance_state", "promise_delta_calendar_days"]].merge(model_summary, on="order_id", validate="one_to_one")
    groups = stages.groupby("order_id")
    audit["reference_stages_available"] = audit.order_id.map(groups.empirical_percentile.count())
    audit["max_stage_percentile"] = audit.order_id.map(groups.empirical_percentile.max())
    long = stages.empirical_percentile.gt(protocol()["long_stage_sampling_percentile"]) & stages.reference_distance_matched
    audit["distance_matched_long_stages"] = audit.order_id.map(stages.assign(flag=long).groupby("order_id").flag.sum())
    audit["eligible_saved_model_disagreement"] = (audit.saved_long_warnings.gt(0) & audit.saved_long_warnings.lt(audit.saved_configurations_evaluated)).fillna(False)
    audit["eligible_late_promise"] = audit.performance_state.eq("late")
    on_time = audit.performance_state.isin(["early", "on_date"])
    audit["eligible_long_stage_on_time"] = on_time & audit.distance_matched_long_stages.gt(0)
    audit["eligible_comparison"] = (on_time & audit.reference_stages_available.eq(3)
                                     & audit.max_stage_percentile.le(protocol()["comparison_sampling_max_percentile"])
                                     & audit.complete_saved_comparison & audit.saved_long_warnings.eq(0)
                                     & audit.saved_short_warnings.eq(0) & audit.saved_zero_warnings.eq(0)).fillna(False)
    selection = choose_cases(audit)
    audit = audit.merge(selection, on="order_id", how="left", validate="one_to_one")
    audit["selected"] = audit.review_id.notna()
    cases = selection.merge(candidates, on="order_id", validate="one_to_one").merge(
        frames["case_evidence.csv"][["order_id", *CONTEXT_FIELDS]], on="order_id", validate="one_to_one")
    cases = cases.merge(model_summary, on="order_id", validate="one_to_one").sort_values("review_id").reset_index(drop=True)
    selected_stages = selection[["review_id", "order_id"]].merge(stages, on="order_id", validate="one_to_many")
    statuses = []
    for case in cases.to_dict("records"):
        difference = case["payment_minus_items_and_freight_minor_units"]
        reconciliation = "unknown" if pd.isna(difference) else "matched_exactly" if difference == 0 else "needs_verification"
        statuses.append({"review_id": case["review_id"], "order_id": case["order_id"],
                         "calendar_performance": case["performance_state"], "payment_reconciliation": reconciliation,
                         "broad_anomaly_status": "unresolved", "confirmed_bottleneck": "unresolved",
                         "data_error_or_valid_deviation": "unresolved", "human_review_performed": False,
                         "independent_reference": False, "evidence_origin": "source_facts_and_exploratory_diagnostics",
                         "missing_evidence": "independent_case_adjudication; carrier_service_level_and_actual_route; per_stage_SLA_or_operational_cause",
                         "next_action": "verify_source_and_business_context_not_retrain_until_label_changes"})
    output = {"case_evidence.csv": cases, "stage_comparisons.csv": selected_stages,
              "candidate_audit.csv": audit, "reference_members.csv": reference,
              "assessment_status.csv": pd.DataFrame(statuses)}
    for source, target in [("payment_source_evidence.csv", "payment_records.csv"), ("item_source_evidence.csv", "item_records.csv")]:
        output[target] = selection[["review_id", "order_id"]].merge(frames[source], on="order_id", validate="one_to_many")
    output["saved_model_evidence.csv"] = selection[["review_id", "order_id"]].merge(models, on="order_id", validate="one_to_many")
    selected_counts = selection.selection_group.value_counts().to_dict()
    summary = {"version": VERSION, "source_accessible_development_orders": len(orders), "eligible_validation_candidates": len(candidates),
               "reference_orders": int(reference.order_id.nunique()), "reference_stage_rows": len(reference),
               "reference_end_before": str(first_validation), "selected_orders": len(cases), "requested_orders": protocol()["sample_size"],
               "pool_counts_before_overlap_removal": {g: int(audit["eligible_"+g].sum()) for g in GROUP_NAMES},
               "selected_group_counts": {g: int(selected_counts.get(g, 0)) for g in GROUP_NAMES},
               "group_shortfalls": {g: protocol()["per_group"]-int(selected_counts.get(g, 0)) for g in GROUP_NAMES},
               "selected_performance_counts": {str(k): int(v) for k, v in cases.performance_state.value_counts().items()},
               "selected_reference_level_counts": {str(k): int(v) for k, v in selected_stages.reference_level.value_counts(dropna=False).items()},
               "payment_reconciliation_counts": {str(k): int(v) for k, v in output["assessment_status.csv"].payment_reconciliation.value_counts().items()},
               "stage_comparisons": len(selected_stages), "saved_configuration_count": len(EXPECTED_MODELS),
               "saved_configuration_rows": len(output["saved_model_evidence.csv"]),
               "payment_rows": len(output["payment_records.csv"]), "item_rows": len(output["item_records.csv"]),
               "unresolved_orders": len(cases), "reference_labels_created": 0,
               **{key: False for key in ["model_trained", "detector_threshold_changed", "model_selected", "test_scored", "split_activated",
                                        "anomaly_accuracy_computed", "causal_bottleneck_identified", "near_real_time_validated",
                                        "human_review_performed", "independent_reference_ready", "external_data_imported"]}}
    return output, summary


def render_report(frames, summary):
    cases, stages = frames["case_evidence.csv"], frames["stage_comparisons.csv"]
    lines = ["# Đối chiếu bổ sung các đơn Olist thật", "",
             f'Đã chọn **{len(cases)} đơn** từ **{summary["eligible_validation_candidates"]} ứng viên validation**. '
             "Không nhập thêm dữ liệu, không dùng tập test và không trùng 120 đơn pilot gốc.", "",
             "Đây là phân tích có hỗ trợ máy, không phải nhãn chuyên gia hoặc bộ kiểm chứng độc lập. "
             "Chưa chốt mô hình, chưa tính độ chính xác bất thường.", "", "## Cách chọn và cách đọc", "",
             "Mỗi nhóm dự kiến 10 đơn; lấy nhóm bất đồng trước rồi loại trùng. Các nhóm được cân bằng có chủ đích, "
             "không đại diện tỷ lệ của toàn ngành. Mã DS là mã rà soát, không phải mã đơn giả.", "",
             "Ngày lệch = ngày giao thực tế trừ ngày dự kiến. Số dương là trễ. Phân vị công đoạn là tỷ lệ "
             "đơn train trong nhóm tham chiếu có thời gian không dài hơn đơn đang xem, không phải xác suất bất thường. "
             "Mốc 95% chỉ phục vụ chọn mẫu, chưa phải ngưỡng tối ưu.", "",
             "## Danh sách", "", "| Mã | Nhóm chọn | Giao hàng | Lệch ngày | Cấu hình cảnh báo dài / đã xét |",
             "| --- | --- | --- | ---: | ---: |"]
    for row in cases.itertuples(index=False):
        warnings = "chưa có" if pd.isna(row.saved_long_warnings) else str(row.saved_long_warnings)
        lines.append(f"| {row.review_id} | {GROUP_NAMES[row.selection_group]} | {STATE_NAMES[row.performance_state]} | "
                     f"{row.promise_delta_calendar_days} | {warnings} / {row.saved_configurations_evaluated} |")
    lines += ["", "## Giới hạn và thông tin còn thiếu", "",
              "- Giao trễ là dữ kiện, chưa xác định tắc nghẽn hoặc ai chịu trách nhiệm. Giao sớm không tự bảo đảm mọi công đoạn bình thường.",
              "- 12 cấu hình ở đây là kết quả lịch sử của mô hình khoảng thời gian vận chuyển, không phải 12 thuật toán độc lập; "
              "chỉ dùng để tìm bất đồng, không lấy đa số làm nhãn. Chưa so sánh lại toàn bộ IF/LOF/OCSVM.",
              "- Nhóm train tham chiếu chưa có nhãn bình thường; nhóm nhỏ hoặc thiếu địa lý được mở rộng và ghi rõ. "
              "Khoảng cách ZIP là đường thẳng, không phải tuyến xe thực tế; chưa kiểm soát mùa vụ, thời tiết, cấp dịch vụ hoặc hãng vận chuyển.",
              "- Thiếu SLA từng công đoạn và thông tin vận hành để xác nhận nguyên nhân. Không cần nhập thêm để làm bước đối chiếu này; "
              "nếu Olist không cung cấp bằng chứng, giữ kết luận chưa xác minh, không tự tạo dữ liệu.",
              "- Các nhận định sau khi xem báo cáo này là đánh giá có hỗ trợ, không gọi là đánh giá mù độc lập. "
              "Muốn đo hiệu quả phát hiện bất thường cần quy trình kết luận độc lập với các cờ chọn mẫu này.", "", "## Chi tiết từng đơn", ""]
    for row in cases.itertuples(index=False):
        delta = row.payment_minus_items_and_freight_minor_units
        money = "chưa đủ dữ liệu" if pd.isna(delta) else f"{int(delta)/100:.2f}"
        lines += [f"### {row.review_id}", "", f"- `order_id`: `{row.order_id}`; nhóm: **{GROUP_NAMES[row.selection_group]}**.",
                  f"- Ngày mua {row.order_purchase_timestamp}; hẹn {row.order_estimated_delivery_date}; giao {row.order_delivered_customer_date}. "
                  f"Kết quả: {STATE_NAMES[row.performance_state]}, lệch {row.promise_delta_calendar_days} ngày.",
                  f"- Bối cảnh: bang {row.customer_state}; nhóm khoảng cách `{row.distance_band}`; {row.seller_count} seller; "
                  f"{row.item_count} dòng hàng. Chênh thanh toán so với tiền hàng + vận chuyển: {money} (tiền tệ nguồn).",
                  "", "| Công đoạn | Ngày thực tế | Trung vị train | P95 train | Vị trí trong nhóm | Số đơn train | Bối cảnh so sánh |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
        for s in stages.loc[stages.order_id.eq(row.order_id)].itertuples(index=False):
            median = "chưa đủ" if pd.isna(s.reference_median_days) else f"{s.reference_median_days:.2f}"
            p95 = "chưa đủ" if pd.isna(s.reference_p95_days) else f"{s.reference_p95_days:.2f}"
            percentile = "chưa đủ" if pd.isna(s.empirical_percentile) else f"{100*s.empirical_percentile:.1f}%"
            lines.append(f"| {STAGE_NAMES[s.stage]} | {s.duration_days:.2f} | {median} | {p95} | {percentile} | "
                         f"{s.reference_n} | `{s.reference_context_json}` |")
        lines += ["", "**Kết luận được:** ngày giao so với hẹn và thời gian các công đoạn theo mốc nguồn. "
                  "**Chưa kết luận:** bất thường do nhập sai/dị biệt hợp lệ, tắc nghẽn thực sự hoặc lỗi seller. "
                  "Số liệu đầy đủ chưa làm tròn nằm trong CSV; nguồn và mã băm nằm trong manifest.", ""]
    return "\n".join(lines)+"\n"


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    output = performance.destination_guard(output, {"inspection": provenance["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite supplement")
    if set(frames) != TABLES:
        raise ValueError("Incomplete supplement tables")
    verify_sources(provenance)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".delivery-supplement-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            actual = shared.read_table(staging, name, {"schemas": schemas})
            pd.testing.assert_frame_equal(actual, frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_doi_chieu.md").write_text(render_report(frames, summary))
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
    expected = TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_doi_chieu.md"}
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("schemas", {})) != TABLES or set(manifest.get("output_hashes", {})) != expected):
        raise ValueError("Invalid supplement manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Supplement protocol/summary mismatch")
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
            frames, provenance = load_inputs()
            tables, summary = build_tables(frames)
            result = write_snapshot(tables, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Delivery supplement stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
