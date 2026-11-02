"""Explain frozen holdout disagreements using observed stages, reviews and context."""

import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import re
import tempfile

import numpy as np
import pandas as pd

import evaluate_selected_holdout as parent
import analyze_bottleneck_context as context
import screen_review_wording as wording

ROOT, shared, review, business = parent.ROOT, parent.shared, parent.review, parent.business
VERSION = "holdout_process_evidence_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_holdout_process_evidence_v1"
GUIDE = ROOT/"docs/holdout_process_evidence.md"
GROUPS = {
    "overall": [],
    "customer_state": ["distance_band", "seller_attribution_scope", "stage_start_month", "item_count"],
    "stage_start_month": ["customer_state", "distance_band", "seller_attribution_scope", "item_count"],
    "distance_band": [],
    "single_seller_id": ["customer_state", "distance_band", "stage_start_month", "item_count"],
}
TABLES = {"order_evidence.csv", "stage_evidence.csv", "review_evidence.csv", "review_summary.csv",
          "review_comparison.csv", "disagreement_orders.csv", "disagreement_stages.csv", "disagreement_reviews.csv",
          "context_groups.csv", "matched_cells.csv", "group_membership.csv", "investigation_actions.csv",
          "evaluation_contract.csv"}


def protocol():
    return {"version": VERSION, "scope": "all_original_test_orders_with_detailed_FP_FN_calendar_disagreements",
            "purpose": "abnormal_processing_time_and_potential_bottlenecks_not_confirmed_causes",
            "source_scores": "unchanged_selected_holdout_v1", "group_match_fields": GROUPS,
            "support_guards": {k: v for k, v in context.protocol().items() if k.startswith("minimum_")},
            "review_patterns": wording.PATTERNS, "low_score_convention": [1, 2],
            "review_screen": "literal_exploratory_retrieval_not_validated_complaint_classifier_or_ground_truth",
            "review_scope": "all_source_rows_for_test_ids; preserve_multiple_reviews_and_missingness",
            "review_time": "retrospective_only; creation>=purchase_and_answer>=creation; no_source_cutoff_inferred",
            "reference_boundary": "sorted_reference[(19*n)//20]; still_requires_duration>mean_and>0",
            "groups": "all_groups_published; matched_rates_descriptive; unknown_followup; multi_seller_not_attributed",
            "new_labels": False, "model_fit": False, "threshold_tuning": False,
            "new_accuracy_metrics": False, "near_real_time_validated": False, "causes_verified": False}


def code_hashes():
    paths = [Path(__file__), Path(wording.__file__), Path(context.__file__), GUIDE,
             context.TEMPLATE/"protocol.json", context.TEMPLATE/"README.md"]
    return {str(p.relative_to(ROOT)): review.digest(p) for p in paths}


def verify_sources(source):
    for item in source["snapshots"].values():
        path = Path(item["directory"])
        if review.digest(path/"manifest.json") != item["manifest_sha256"]:
            raise ValueError("Process evidence source manifest changed")
        shared.verify_files(path, item["output_hashes"])
    if source["code_hashes"] != code_hashes():
        raise ValueError("Evidence code or protocol changed; use a new version")


def load_inputs():
    pm = parent.load_snapshot()
    product_dir = parent.product.DEFAULT_OUTPUT
    product_m = parent.product.load_snapshot()
    clean = Path(pm["provenance"]["snapshots"]["clean"]["directory"])
    cm = review.read_json(clean/"manifest.json")
    if review.digest(clean/"manifest.json") != pm["provenance"]["snapshots"]["clean"]["manifest_sha256"]:
        raise ValueError("Review source is not the frozen clean dataset")
    snapshots = [("holdout", parent.DEFAULT_OUTPUT, pm), ("clean", clean, cm), ("product", product_dir, product_m)]
    source = {"snapshots": {name: {"directory": str(path), "manifest_sha256": review.digest(path/"manifest.json"),
                                  "output_hashes": m["output_hashes"]} for name, path, m in snapshots}, "code_hashes": code_hashes()}
    verify_sources(source)
    frames = {name: shared.read_table(parent.DEFAULT_OUTPUT, name, pm)
              for name in ["order_ledger.csv", "stage_scores.csv", "reference_groups.csv"]}
    frames["reference"] = shared.read_table(product_dir, "train_stage_reference.csv", product_m)
    frames["reviews"] = business.read_selected(clean, "order_reviews", cm, "order_id", set(frames["order_ledger.csv"].order_id))
    frames["parent_summary"] = pm["summary"]
    return frames, source


def validate_inputs(inputs):
    orders, stages, reference = [inputs[k] for k in ["order_ledger.csv", "stage_scores.csv", "reference"]]
    for frame, keys in [(orders, ["order_id"]), (stages, ["order_id", "stage"]), (reference, ["order_id", "stage"])]:
        business.require_key(frame, keys, "evidence inputs")
    if (not orders.split.eq("test").all() or not stages.split.eq("test").all()
            or not reference.split.eq("train").all() or set(orders.order_id) & set(reference.order_id)
            or set(stages.order_id) != set(orders.order_id)
            or not stages.groupby("order_id").stage.agg(set).map(lambda s: s == set(parent.product.signals.STAGES)).all()):
        raise ValueError("Frozen test stages must be complete and disjoint from train")
    summary = inputs["parent_summary"]
    ids_hash = hashlib.sha256("\n".join(sorted(orders.order_id)).encode()).hexdigest()
    if len(orders) != summary["total_test_orders"] or ids_hash != summary["test_orders_sha256"]:
        raise ValueError("Frozen holdout population differs")
    known = orders.metric_eligible
    if (orders.loc[known, parent.TARGET].isna().any()
            or not orders.loc[known, parent.CANDIDATE+"_flag"].eq(orders.loc[known, parent.SCORE].gt(0)).all()):
        raise ValueError("Frozen flag or outcome differs")
    for actual, predicted, label in [(True, True, "TP"), (False, True, "FP"), (True, False, "FN"), (False, False, "TN")]:
        mask = (known & orders[parent.TARGET].eq(actual) & orders[parent.CANDIDATE+"_flag"].eq(predicted)).fillna(False)
        if not mask.eq(orders.calendar_proxy_confusion.eq(label)).all() or int(mask.sum()) != summary["pooled_metrics"][label.lower()]:
            raise ValueError("Frozen calendar-disagreement classification differs")
    if not orders.loc[~known, "calendar_proxy_confusion"].eq("not_evaluated").all():
        raise ValueError("Unassessed orders cannot enter evaluated confusion groups")
    recomputed_duration = (stages.end_timestamp-stages.start_timestamp).dt.total_seconds()/86400
    np.testing.assert_array_equal(stages.duration_days.to_numpy(float), recomputed_duration.to_numpy(float))


def stage_evidence(inputs):
    orders, saved, reference, groups = [inputs[k] for k in ["order_ledger.csv", "stage_scores.csv", "reference", "reference_groups.csv"]]
    bounds = []
    for row in groups.itertuples(index=False):
        ids = json.loads(row.order_ids_json)
        peers = reference.loc[reference.stage.eq(row.stage) & reference.order_id.isin(ids)].sort_values("order_id")
        values = peers.duration_days.to_numpy(float)
        if (len(ids) != len(set(ids)) or peers.order_id.tolist() != ids or len(values) != row.n or row.n < 30
                or not np.isfinite(values).all() or (values < 0).any() or values.mean() != row.mean_days):
            raise ValueError("Saved reference membership or mean differs")
        boundary = float(np.sort(values)[(19*len(values))//20])
        bounds.append({"selected_reference_id": row.reference_id, "frozen_upper_tail_boundary_days": boundary,
                       "selected_context_json": row.context_json, "selected_reference_latest_end": row.latest_end})
    result = saved.merge(pd.DataFrame(bounds), on="selected_reference_id", how="left", validate="many_to_one")
    result = result.astype(saved.dtypes.to_dict())
    assessed = result.delay_score_0_100.notna()
    expected = (result.duration_days.ge(result.frozen_upper_tail_boundary_days)
                & result.duration_days.gt(result.selected_reference_mean_days) & result.duration_days.gt(0))
    if (not assessed.eq(result.assessable).all() or result.loc[assessed, "frozen_upper_tail_boundary_days"].isna().any()
            or not expected[assessed].eq(result.loc[assessed, "long_stage_warning"]).all()
            or not expected[assessed].eq(result.loc[assessed, "delay_score_0_100"].gt(0)).all()):
        raise ValueError("Stage evidence would change frozen flags")
    result["difference_from_tail_boundary_days"] = (result.duration_days-result.frozen_upper_tail_boundary_days).where(assessed)
    result["stage_observation"] = np.select(
        [~assessed, result.delay_score_0_100.gt(0), result.difference_from_reference_mean_days.gt(0),
         result.difference_from_reference_mean_days.lt(0)],
        ["unassessed", "upper_tail_warning", "above_mean_without_tail_warning", "below_reference_mean"], default="equal_reference_mean")
    result["stage_start_month"] = result.start_timestamp.dt.strftime("%Y-%m").astype("string").fillna(context.UNKNOWN)
    result = result.merge(orders[["order_id", "seller_attribution_scope", "calendar_proxy_confusion", "performance_eligible", parent.TARGET]],
                          on="order_id", how="left", validate="many_to_one")
    result["evidence_locator"] = "stage_scores.csv:order_id="+result.order_id+";stage="+result.stage
    return result.sort_values(["order_id", "stage"]).reset_index(drop=True)


def order_evidence(orders, stages, reviews):
    result = orders.sort_values("order_id").reset_index(drop=True).merge(reviews, on="order_id", how="left", validate="one_to_one")
    for name, mask in [("above_mean_stages", stages.difference_from_reference_mean_days.gt(0)),
                       ("above_mean_without_warning_stages", stages.stage_observation.eq("above_mean_without_tail_warning")),
                       ("long_warning_stages", stages.long_stage_warning.fillna(False)),
                       ("below_mean_stages", stages.difference_from_reference_mean_days.lt(0))]:
        count = mask.groupby(stages.order_id).sum()
        result[name] = result.order_id.map(count).astype(int)
    result["actual_cycle_elapsed_days"] = (result.order_delivered_customer_date-result.order_purchase_timestamp).dt.total_seconds()/86400
    result["actual_cycle_calendar_days"] = (result.order_delivered_customer_date.dt.normalize()-result.order_purchase_timestamp.dt.normalize()).dt.days.astype("Int64")
    result["promised_lead_calendar_days"] = (result.order_estimated_delivery_date.dt.normalize()-result.order_purchase_timestamp.dt.normalize()).dt.days.astype("Int64")
    result["selected_for_detailed_disagreement"] = result.calendar_proxy_confusion.isin(["FP", "FN"])
    result["observation_not_cause"] = np.select(
        [result.calendar_proxy_confusion.eq("FN"), result.calendar_proxy_confusion.eq("FP"), ~result.metric_eligible],
        ["late_calendar_outcome_without_frozen_upper_tail_warning", "upper_tail_stage_warning_without_calendar_lateness",
         "unassessed_or_invalid_source"], default="calendar_outcome_and_stage_flag_agree_not_ground_truth")
    return result


def review_comparison(orders):
    rows = []
    alert_status = np.select([orders[parent.SCORE].gt(0), ~orders.scored_stages.eq(3)],
                            ["stage_warning", "unassessed_or_incomplete"], default="no_stage_warning")
    for dimension, values in [("overall", pd.Series("all", index=orders.index)),
                              ("calendar_proxy_confusion", orders.calendar_proxy_confusion),
                              ("stage_warning_status", pd.Series(alert_status, index=orders.index))]:
        for value, group in orders.groupby(values, sort=True):
            score = group.loc[group.usable_score_records.gt(0)]
            text = group.loc[group.has_usable_text]
            rows.append({"dimension": dimension, "group_value": value, "orders": len(group),
                         "orders_with_review": int(group.review_records.gt(0).sum()), "orders_without_review": int(group.review_records.eq(0).sum()),
                         "orders_with_usable_score": len(score), "orders_with_usable_text": len(text),
                         "orders_with_low_score_1_2": int(score.any_low_score_1_2.sum()),
                         "low_score_rate_among_usable_scores": float(score.any_low_score_1_2.mean()) if len(score) else np.nan,
                         "text_with_delay_or_nonreceipt_wording": int(text.any_delay_or_nonreceipt_wording.sum()),
                         "wording_rate_among_usable_text": float(text.any_delay_or_nonreceipt_wording.mean()) if len(text) else np.nan,
                         "text_with_on_time_or_early_wording": int(text.any_on_time_or_early_wording.sum()),
                         "text_with_mixed_wording": int(text.any_mixed_wording.sum()),
                         "interpretation": "retrospective_cooccurrence_not_classifier_accuracy_or_causality"})
    return pd.DataFrame(rows)


def group_stats(group):
    assessed = group.loc[group.assessable]
    warned = assessed.loc[assessed.long_stage_warning]
    return {"total_orders": len(group), "assessed_orders": len(assessed), "unassessed_orders": len(group)-len(assessed),
            "assessment_coverage": len(assessed)/len(group), "warning_orders": len(warned),
            "warning_rate": len(warned)/len(assessed) if len(assessed) else np.nan,
            "median_duration_days_assessed": float(assessed.duration_days.median()) if len(assessed) else np.nan,
            "median_difference_from_reference_mean_days": float(assessed.difference_from_reference_mean_days.median()) if len(assessed) else np.nan,
            "early_orders_with_warning": int(warned.performance_state.eq("early").sum()),
            "late_orders_with_warning": int(warned.performance_state.eq("late").sum())}


def contextual_evidence(stages):
    rows, cells, members = [], [], []
    policy = context.protocol()
    blank = {"matched_target_orders": 0, "matched_target_signals": 0, "matched_comparator_orders": 0,
             "matched_comparator_signals_unweighted": 0, "target_match_coverage": np.nan,
             "matched_target_rate": np.nan, "matched_comparator_rate": np.nan, "matched_rate_difference": np.nan}
    for stage, all_stage in stages.groupby("stage", sort=True):
        for dimension, fields in GROUPS.items():
            pool = all_stage.loc[all_stage.seller_attribution_scope.eq("single_seller_order")] if dimension == "single_seller_id" else all_stage
            groups = [("all", pool)] if dimension == "overall" else pool.groupby(dimension, sort=True, dropna=False)
            for value, target in groups:
                value = str(value) if pd.notna(value) else context.UNKNOWN
                gid = context.stable_id(VERSION, stage, dimension, value)
                stats, membership = blank.copy(), []
                if fields and value != context.UNKNOWN:
                    outside = pool.loc[~pool.order_id.isin(target.order_id)]
                    stats, cell_rows, membership = context.match_group(target, outside, fields, gid)
                    cells.append(cell_rows)
                recorded = {r["order_id"] for r in membership if r["role"] == "target"}
                membership.extend({"group_id": gid, "order_id": oid, "role": "target", "cell_id": pd.NA,
                                   "included_in_comparison": False} for oid in target.order_id if oid not in recorded)
                members.extend({**r, "stage": stage} for r in membership)
                supported = bool(fields and stats["matched_target_orders"] >= policy["minimum_matched_target_orders"]
                                 and stats["matched_comparator_orders"] >= policy["minimum_matched_comparator_orders"]
                                 and stats["target_match_coverage"] >= policy["minimum_target_match_coverage"])
                candidate = bool(supported and stats["matched_target_signals"] >= policy["minimum_matched_signals_for_investigation"]
                                 and stats["matched_rate_difference"] > 0)
                rows.append({"group_id": gid, "stage": stage, "dimension": dimension, "group_value": value,
                             "match_fields_json": json.dumps(fields), **group_stats(target), **stats,
                             "support_status": "descriptive_only" if not fields else "supported_exploratory_comparison" if supported else "insufficient_common_support",
                             "potential_concentration_for_investigation": candidate,
                             "followup_status": "unknown_source_cutoff_not_equal_followup_verified",
                             "causal_status": "unverified_association_not_cause_or_confirmed_bottleneck"})
    groups = pd.DataFrame(rows).sort_values(["stage", "dimension", "group_value"]).reset_index(drop=True)
    cells = pd.concat(cells, ignore_index=True).sort_values(["group_id", "cell_id"]).reset_index(drop=True)
    members = pd.DataFrame(members).sort_values(["group_id", "role", "order_id"]).reset_index(drop=True)
    members["cell_id"] = members.cell_id.astype("string")
    business.require_key(members, ["group_id", "order_id"], "context members")
    actions = []
    for row in groups.loc[groups.potential_concentration_for_investigation].itertuples(index=False):
        hypothesis, required, action, intervention = context.CHECKLISTS[row.stage]
        actions.append({"group_id": row.group_id, "stage": row.stage, "dimension": row.dimension, "group_value": row.group_value,
                        "hypothesis_unverified_vi": hypothesis, "required_data_vi": required,
                        "investigation_action_vi": action, "conditional_intervention_vi": intervention,
                        "cause_status": "unverified", "intervention_effect_measured": False})
    columns = ["group_id", "stage", "dimension", "group_value", "hypothesis_unverified_vi", "required_data_vi",
               "investigation_action_vi", "conditional_intervention_vi", "cause_status", "intervention_effect_measured"]
    return groups, cells, members, pd.DataFrame(actions, columns=columns)


def evaluation_contract():
    return pd.DataFrame([
        ["stage_signal", "fixed_stage_score_and_coverage", "frozen_train_reference", False, "relative_unusual_duration_not_verified_business_failure"],
        ["calendar_outcome", "previous_AUC_F1_preserved_not_recomputed", "recorded_actual_vs_estimated_calendar_dates", False, "lateness_proxy_not_bottleneck_truth"],
        ["review_evidence", "coverage_and_literal_wording_cooccurrence", "actual_customer_score_comment_dates", False, "statement_not_verified_cause;_missing_review_not_normal"],
        ["context", "matched_warning_rates_and_support", "frozen_signals_same_test_cohort", False, "exploratory_potential_concentration_not_causal_confirmation"],
        ["near_real_time", "not_evaluated_here", "no_record_ingestion_times_or_verified_cutoff", False, "reviews_and_completed_durations_not_available_for_earlier_alerts"],
    ], columns=["evaluation_axis", "reported_measure", "evidence", "independent_bottleneck_accuracy", "limitation"])


def build_tables(inputs):
    validate_inputs(inputs)
    stages = stage_evidence(inputs)
    reviews = wording.enrich_reviews(inputs["reviews"], inputs["order_ledger.csv"])
    review_summary = wording.aggregate_reviews(reviews, inputs["order_ledger.csv"].order_id)
    orders = order_evidence(inputs["order_ledger.csv"], stages, review_summary)
    detailed = orders.loc[orders.selected_for_detailed_disagreement].reset_index(drop=True)
    ids = set(detailed.order_id)
    groups, cells, members, actions = contextual_evidence(stages)
    fn, fp = [detailed.loc[detailed.calendar_proxy_confusion.eq(label)] for label in ["FN", "FP"]]
    summary = {"version": VERSION, "all_orders": len(orders), "stage_rows": len(stages), "detailed_orders": len(detailed),
               "late_without_warning": len(fn), "warning_without_lateness": len(fp),
               "late_without_warning_but_above_mean": int(fn.above_mean_without_warning_stages.gt(0).sum()),
               "late_without_warning_all_stages_at_or_below_mean": int(fn.above_mean_stages.eq(0).sum()),
               "early_with_warning": int((orders.performance_state.eq("early") & orders[parent.SCORE].gt(0)).sum()),
               "review_rows": len(reviews), "orders_with_review": int(orders.review_records.gt(0).sum()),
               "orders_with_usable_review_text": int(orders.has_usable_text.sum()),
               "orders_with_multiple_review_rows": int(orders.review_records.gt(1).sum()),
               "review_records_invalid_or_missing_dates": int((~reviews.valid_review_dates).sum()),
               "detailed_orders_with_review": int(detailed.review_records.gt(0).sum()),
               "detailed_orders_with_usable_text": int(detailed.has_usable_text.sum()),
               "detailed_orders_with_delay_or_nonreceipt_wording": int(detailed.any_delay_or_nonreceipt_wording.sum()),
               "context_groups": len(groups), "supported_context_groups": int(groups.support_status.eq("supported_exploratory_comparison").sum()),
               "potential_concentration_groups": len(actions), "matched_cells": int(cells.included.sum()),
               "multi_seller_orders_not_attributed_to_individual_seller": int(orders.seller_attribution_scope.ne("single_seller_order").sum()),
               "source_extraction_cutoff": None, "parent_metrics_preserved": inputs["parent_summary"]["pooled_metrics"],
               **{k: False for k in ["raw_data_changed", "models_retrained", "thresholds_changed", "new_reference_labels", "synthetic_data_used",
                    "independent_bottleneck_accuracy_computed", "review_classifier_validated", "causes_verified", "near_real_time_validated", "production_activated"]}}
    return {"order_evidence.csv": orders, "stage_evidence.csv": stages, "review_evidence.csv": reviews,
            "review_summary.csv": review_summary, "review_comparison.csv": review_comparison(orders),
            "disagreement_orders.csv": detailed, "disagreement_stages.csv": stages.loc[stages.order_id.isin(ids)].reset_index(drop=True),
            "disagreement_reviews.csv": reviews.loc[reviews.order_id.isin(ids)].reset_index(drop=True),
            "context_groups.csv": groups, "matched_cells.csv": cells, "group_membership.csv": members,
            "investigation_actions.csv": actions, "evaluation_contract.csv": evaluation_contract()}, summary


def escaped(value):
    if pd.isna(value):
        return "(không có nội dung)"
    text = html.escape(str(value))
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text).replace("\r", "").replace("\n", "<br>")


def case_report(frames):
    lines = ["# Chi tiết 83 đơn: cảnh báo công đoạn và ngày hẹn", "",
             "Đây là mô tả tự động từ dữ liệu thật, không phải rà soát/gán nhãn bởi con người. FP/FN chỉ là mã đối chiếu với giao trễ ngày hẹn.",
             "Chênh lệch = thời lượng thực tế trừ trung bình nhóm tham chiếu đã lưu. Cảnh báo cần vượt điều kiện đuôi phân phối, không chỉ lớn hơn trung bình.",
             "Ranh giới đuôi là số ngày tái dựng từ tham chiếu cũ; vẫn cần thời lượng > trung bình và > 0. Không thay đổi ngưỡng.",
             "Review là lời khách hàng tại thời điểm trả lời; không xác nhận nguyên nhân hay trách nhiệm seller. Bộ tìm từ chỉ hỗ trợ đọc.", ""]
    for row in frames["disagreement_orders.csv"].itertuples(index=False):
        lines += [f"## {row.order_id} ({row.calendar_proxy_confusion})", "",
                  f"- Kết quả lịch: `{row.performance_state}`; chênh ngày hẹn: {row.promise_delta_calendar_days} ngày. Cơ sở ngày hẹn chưa xác minh.",
                  f"- Mua: {row.order_purchase_timestamp}; giao: {row.order_delivered_customer_date}; hẹn: {row.order_estimated_delivery_date}.",
                  f"- Bang khách: {row.customer_state}; khoảng cách: {row.distance_band}; {row.item_count} item; {row.product_count} sản phẩm; {row.mapped_seller_count} seller.",
                  f"- Sản phẩm đơn nhất: {row.product_id}; seller đơn nhất: {row.single_seller_id}. Không gán thời gian riêng cho từng item/seller nếu đơn nhiều thành phần.",
                  f"- Có {row.above_mean_stages} công đoạn trên trung bình và {row.long_warning_stages} công đoạn cảnh báo. Trạng thái bằng chứng review: `{row.review_evidence_status}`.", "",
                  "| Công đoạn | Thực tế (ngày) | Trung bình | Chênh lệch | Ranh giới đuôi | Điểm | Cảnh báo | N tham chiếu |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | --- | ---: |"]
        for stage in frames["disagreement_stages.csv"].loc[lambda f: f.order_id.eq(row.order_id)].itertuples(index=False):
            lines.append(f"| {context.signals.NAMES[stage.stage]} | {stage.duration_days:.4f} | {stage.selected_reference_mean_days:.4f} | {stage.difference_from_reference_mean_days:+.4f} | {stage.frozen_upper_tail_boundary_days:.4f} | {stage.delay_score_0_100:.2f} | {'Có' if stage.long_stage_warning else 'Không'} | {stage.selected_reference_n} |")
        records = frames["disagreement_reviews.csv"].loc[lambda f: f.order_id.eq(row.order_id)]
        if records.empty:
            lines += ["", "Không có review trong nguồn; không suy ra khách hài lòng hay đơn bình thường."]
        for item in records.itertuples(index=False):
            lines += ["", f"Review `{item.review_id}`, bản ghi sạch {item.clean_source_record}; điểm {item.review_score}; trả lời {item.review_answer_timestamp}.",
                      f"- Ngày hợp lệ cho đối chiếu: {item.valid_review_dates}; trả lời trước giao ghi nhận: {item.answered_before_recorded_delivery}.",
                      f"- Tiêu đề gốc: {escaped(item.review_comment_title)}", f"- Bình luận gốc: {escaped(item.review_comment_message)}",
                      f"- Cụm từ tìm thấy (không phải nhãn): {escaped(item.literal_matches_json)}"]
        lines.append("")
    return "\n".join(lines)


def report(frames, summary):
    lines = ["# Thời gian bất thường và điểm nghẽn tiềm năng", "",
             "**Phân tích sau test, không train lại, không tạo nhãn nghẽn hay nguyên nhân giả.**", "",
             f"Giữ nguyên điểm và ngưỡng của {summary['all_orders']} đơn test; lập hồ sơ {summary['detailed_orders']} đơn khác nhau giữa hai tiêu chí.",
             f"- {summary['late_without_warning']} đơn trễ không cảnh báo; {summary['late_without_warning_but_above_mean']} đơn trong đó vẫn có công đoạn trên trung bình, nhưng chưa thỏa ngưỡng cảnh báo đã đóng băng.",
             f"- {summary['late_without_warning_all_stages_at_or_below_mean']} đơn trễ có cả ba công đoạn không vượt trung bình tham chiếu; chưa có cơ sở kết luận ngày hẹn sai hoặc được đặt ngẫu nhiên.",
             f"- {summary['warning_without_lateness']} đơn không trễ có cảnh báo; giữ nguyên cảnh báo của {summary['early_with_warning']} đơn giao sớm.",
             "Cơ sở ngày hẹn và ngày chốt nguồn chưa xác minh. Không dùng nhóm 83 đơn để tính độ chính xác đại diện cho toàn bộ dữ liệu.", "",
             "## Review thực tế", "",
             f"Có {summary['review_rows']} bản ghi review của {summary['orders_with_review']}/{summary['all_orders']} đơn; {summary['orders_with_usable_review_text']} đơn có nội dung viết và ngày hợp lệ để đối chiếu hồi cứu.",
             f"Giữ {summary['orders_with_multiple_review_rows']} đơn có nhiều review; {summary['review_records_invalid_or_missing_dates']} bản ghi thiếu/mâu thuẫn ngày được giữ nhưng không đưa vào tổng hợp review hợp lệ.",
             f"Trong 83 đơn: {summary['detailed_orders_with_review']} có review, {summary['detailed_orders_with_usable_text']} có nội dung hợp lệ; {summary['detailed_orders_with_delay_or_nonreceipt_wording']} có cụm từ chậm/chưa nhận theo bộ tìm từ công khai.",
             "Các cụm từ này KHÔNG phải số khiếu nại đã xác minh. Giữ nguyên tiếng Bồ Đào Nha, kể cả ý phủ định và nội dung trái chiều. Review thấp có thể nói về sản phẩm; không có review không phải bình thường.", "",
             "| Nhóm đối chiếu | Đơn | Có review | Có chữ/ngày hợp lệ | Có cụm từ chậm/chưa nhận | Điểm 1-2 / đơn có điểm hợp lệ |",
             "| --- | ---: | ---: | ---: | ---: | ---: |"]
    for row in frames["review_comparison.csv"].loc[lambda f: f.dimension.eq("calendar_proxy_confusion")].itertuples(index=False):
        lines.append(f"| {row.group_value} | {row.orders} | {row.orders_with_review} | {row.orders_with_usable_text} | {row.text_with_delay_or_nonreceipt_wording} | {row.orders_with_low_score_1_2}/{row.orders_with_usable_score} |")
    lines += ["", "Mẫu số của tỷ lệ cụm từ là các đơn có nội dung/ngày hợp lệ, không phải toàn bộ đơn. Không xem đây là precision/recall hoặc nhãn độc lập.", "",
              "## Theo công đoạn", "", "| Công đoạn | Tổng đơn | Chấm được | Cảnh báo | Tỷ lệ trên số chấm được |",
              "| --- | ---: | ---: | ---: | ---: |"]
    for row in frames["context_groups.csv"].loc[lambda f: f.dimension.eq("overall")].itertuples(index=False):
        lines.append(f"| {context.signals.NAMES[row.stage]} | {row.total_orders} | {row.assessed_orders} | {row.warning_orders} | {row.warning_rate:.2%} |")
    lines += ["", "Một đơn có thể có nhiều công đoạn cảnh báo; không cộng thành số đơn độc lập.", "",
              "## Nhóm bối cảnh", "",
              f"Xuất đủ {summary['context_groups']} nhóm; {summary['supported_context_groups']} nhóm có phần đối chiếu đạt quy ước hỗ trợ; {summary['potential_concentration_groups']} nhóm có mức tập trung cảnh báo cần xem xét thêm.",
              f"{summary['multi_seller_orders_not_attributed_to_individual_seller']} đơn nhiều seller không được gán cảnh báo/review cho một seller cụ thể.",
              "So bang có khớp khoảng cách, phạm vi seller, tháng công đoạn và số item. So tháng khớp bang, khoảng cách, phạm vi seller và số item. So seller chỉ dùng đơn một seller, khớp bang, khoảng cách, tháng và số item.",
              "Giữ quy ước ô >=5 đơn mỗi phía; phần khớp >=30 mỗi phía, phủ >=80% phía đích; ít nhất 5 cảnh báo và chênh tỷ lệ dương mới đưa vào danh sách điều tra. Không hạ quy ước khi ít kết quả.",
              "Các so sánh chỉ mô tả: chưa kiểm soát đủ sản phẩm/carrier/dịch vụ, chưa xác minh thời gian theo dõi ngang nhau. Không gọi bang khách là nơi xảy ra sự cố hoặc quy trách nhiệm seller.", "",
              "| Công đoạn | Chiều nhóm | Nhóm | Cảnh báo / phần khớp | Tỷ lệ đích | Đối chiếu có trọng số | Chênh điểm % |",
              "| --- | --- | --- | ---: | ---: | ---: | ---: |"]
    candidates = frames["context_groups.csv"].loc[lambda f: f.potential_concentration_for_investigation]
    for row in candidates.sort_values(["matched_rate_difference", "group_id"], ascending=[False, True]).itertuples(index=False):
        lines.append(f"| {context.signals.NAMES[row.stage]} | {row.dimension} | {row.group_value} | {row.matched_target_signals}/{row.matched_target_orders} | {row.matched_target_rate:.2%} | {row.matched_comparator_rate:.2%} | {100*row.matched_rate_difference:.2f} |")
    if candidates.empty:
        lines += ["", "Chưa có nhóm thỏa điều kiện để đề xuất điều tra theo phép so sánh này. Không suy ra toàn bộ quy trình bình thường."]
    lines += ["", "Danh sách không phải xác nhận nghẽn hay ý nghĩa thống kê. Các nhóm chồng lấn và dùng lại đối chứng; không cộng số nhóm thành số nguyên nhân.", "",
              "## Điều tra và hướng xử lý", ""]
    for action in frames["investigation_actions.csv"].itertuples(index=False):
        lines += [f"### {action.stage}: {action.dimension}={action.group_value}", "", action.investigation_action_vi,
                  f"- Dữ liệu cần kiểm tra: {action.required_data_vi}", f"- Can thiệp chỉ khi xác minh: {action.conditional_intervention_vi}", ""]
    lines += ["## Giới hạn đánh giá", "",
              f"- Giữ nguyên AUC {summary['parent_metrics_preserved']['roc_auc']:.4f} và F1 {summary['parent_metrics_preserved']['f1_raw']:.4f} của đối chiếu giao trễ; không đổi nhãn để làm chỉ số tăng.",
              "- Chưa có AUC/precision/recall với nghẽn đã xác nhận. Review và đồng thuận mô hình không tự tạo ra chuẩn đúng.",
              "- Các thống kê cảnh báo/độ bao phủ là mô tả đầu ra, không phải kiểm chứng độc lập bằng chính quy tắc sinh cảnh báo.",
              "- Review là bằng chứng sau sự kiện; không đưa ngược vào cảnh báo sớm. Chưa chứng minh near real-time hoặc hiệu quả can thiệp.",
              "- Đây là phân tích test đã xem. Cải tiến sau đây cần kế hoạch đánh giá mới, không gọi lần đánh giá lại là test chưa từng xem.", "",
              "## Đọc kết quả", "",
              "- chi_tiet_83_don.md: từng đơn, số ngày, tham chiếu và bình luận gốc.",
              "- disagreement_*.csv: toàn bộ 83 đơn và bằng chứng liên quan.",
              "- order_evidence.csv, review_comparison.csv: toàn bộ mẫu số và phần thiếu.",
              "- context_groups.csv, matched_cells.csv, group_membership.csv: mọi nhóm, điều kiện khớp và thành viên.",
              "- investigation_actions.csv: yêu cầu kiểm tra và can thiệp có điều kiện, không phải kết quả đã xác minh.",
              "- evaluation_contract.csv: điều được và chưa được kết luận trên từng trục đánh giá.", ""]
    return "\n".join(lines)


def run(output=DEFAULT_OUTPUT):
    output = parent.product.pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite process evidence snapshot")
    print("Verifying frozen holdout and real review sources...", flush=True)
    inputs, source = load_inputs()
    print("Building stage explanations, literal review evidence and matched context tables...", flush=True)
    frames, summary = build_tables(inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".holdout-evidence-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
        for name, value in [("summary.json", summary), ("protocol.json", protocol())]:
            review.write_json(staging/name, value)
        (staging/"bao_cao_bang_chung.md").write_text(report(frames, summary), encoding="utf-8")
        (staging/"chi_tiet_83_don.md").write_text(case_report(frames), encoding="utf-8")
        verify_sources(source)
        review.write_json(staging/"manifest.json", {
            "version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "summary": summary, "provenance": source, "libraries": {"numpy": np.__version__, "pandas": pd.__version__},
            "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())},
            "schemas": {name: {c: str(dtype) for c, dtype in frame.dtypes.items()} for name, frame in frames.items()}})
        if output.exists():
            raise FileExistsError("Destination appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "protocol.json", "bao_cao_bang_chung.md", "chi_tiet_83_don.md"}):
        raise ValueError("Invalid process evidence snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    parent.load_snapshot()
    if (review.read_json(directory/"summary.json") != manifest["summary"] or review.read_json(directory/"protocol.json") != protocol()):
        raise ValueError("Evidence summary/protocol differs")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = load_snapshot(args.output)["summary"] if args.verify else run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
