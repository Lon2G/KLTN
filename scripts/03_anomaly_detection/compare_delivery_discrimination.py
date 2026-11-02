"""Compare frozen scores on identical observed deliveries, not bottleneck truth."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, confusion_matrix, roc_auc_score

import build_product_stage_scores as product
import compare_signal_detectors as detectors

ROOT, shared, review = product.ROOT, product.shared, product.review
VERSION = "delivery_discrimination_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_delivery_discrimination_v1"
GUIDE = ROOT/"docs/delivery_discrimination.md"
TARGET = "calendar_late_signal"
PRODUCT_SCORE = "max_stage_delay_score"
PRODUCT_CANDIDATE = "product_context_stage_score_gt0"
FAMILIES = {**detectors.FAMILIES, "product_context_stage": "Điểm công đoạn theo bối cảnh"}
TABLES = {"cohort_ledger.csv", "common_outcomes.csv", "common_scores.csv", "common_flags.csv",
          "candidate_definitions.csv", "method_metrics.csv", "fixed_threshold_metrics.csv", "family_summary.csv"}


def protocol():
    return {
        "version": VERSION, "category": "bed_bath_table",
        "target": "observed_delivery_calendar_date_after_estimated_delivery_calendar_date",
        "evaluation": "retrospective_exploratory_validation_not_early_prediction",
        "cohort": "all_frozen_common_cases_must_have_every_score_and_three_observed_stages",
        "missing": "fail_if_common_case_missing; report_other_source_cases_not_imputed_as_zero",
        "orientation": "higher_is_more_anomalous_as_defined_in_sources; never_flip_using_target",
        "score_metrics": ["roc_auc", "average_precision"],
        "threshold_metrics": ["precision", "recall", "f1", "tp", "fp", "fn", "tn", "alert_count"],
        "thresholds": "120_frozen_candidates_plus_existing_product_score_strictly_greater_than_zero",
        "ranks": "separate_descending_ranks_with_ties; no_weighted_composite_winner",
        "frontiers": "strict_Pareto_dominance_on_AUC_AP_and_on_precision_recall_separately",
        "comparability": "same_outcomes_and_observations; legacy_21_share_fit_features; product_pipeline_has_different_context_and_history",
        "uncertainty": "descriptive_only; no_independence_or_statistical_superiority_claim",
        "selection": "exploratory_leaders_only; no_production_model_or_threshold_activation",
        "forbidden": ["invented_labels", "test_scoring", "refitting", "threshold_search", "causal_claims"],
    }


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in [Path(__file__), GUIDE]}


def verify_sources(provenance):
    for source in provenance["snapshots"].values():
        directory = Path(source["directory"])
        if review.digest(directory/"manifest.json") != source["manifest_sha256"]:
            raise ValueError("Source manifest changed during comparison")
        shared.verify_files(directory, source["output_hashes"])
    if provenance["code_hashes"] != code_hashes():
        raise ValueError("Comparison code or guide changed; use a new experiment version")


def load_inputs():
    directories = {"detectors": detectors.DEFAULT_OUTPUT, "product": product.DEFAULT_OUTPUT}
    manifests = {"detectors": detectors.load_snapshot(), "product": product.load_snapshot()}
    source = {"snapshots": {name: {"directory": str(directories[name]),
                                  "manifest_sha256": review.digest(directories[name]/"manifest.json"),
                                  "output_hashes": manifest["output_hashes"]}
                            for name, manifest in manifests.items()}, "code_hashes": code_hashes()}
    inputs = {}
    for key, filename in {"cases": "common_cases.csv", "scores": "common_scores.csv", "flags": "common_flags.csv",
                          "candidates": "candidate_definitions.csv", "scope": "comparison_scope.csv"}.items():
        inputs[key] = shared.read_table(directories["detectors"], filename, manifests["detectors"])
    for key, filename in {"orders": "order_scores.csv", "stages": "stage_scores.csv", "reference": "train_stage_reference.csv"}.items():
        inputs[key] = shared.read_table(directories["product"], filename, manifests["product"])
    batch = review.read_json(detectors.BATCH/"manifest.json")
    model = Path(batch["provenance"]["model_directory"])
    # The parent verification checks these files without deserializing a fitted model.
    inputs["fit_ids"] = pd.read_csv(model/"fit_timing_features.csv", usecols=["order_id"]).order_id
    inputs["calibration_ids"] = pd.read_csv(model/"calibration_timing_features.csv", usecols=["order_id"]).order_id
    inputs["training_plan"] = review.read_json(model/"training_plan.json")
    inputs["product_summary"] = manifests["product"]["summary"]
    verify_sources(source)
    return inputs, source


def aligned_inputs(inputs):
    cases, scores, flags, candidates, orders, stages, reference, scope = [inputs[k].copy() for k in
        ["cases", "scores", "flags", "candidates", "orders", "stages", "reference", "scope"]]
    for name, frame in [("cases", cases), ("scores", scores), ("flags", flags), ("orders", orders), ("scope", scope)]:
        detectors.previous.require_ids(frame, name)
    shared.business.require_key(stages, ["order_id", "stage"], "stage scores")
    shared.business.require_key(reference, ["order_id", "stage"], "train reference")
    shared.business.require_key(candidates, ["candidate"], "candidate definitions")
    ids = sorted(cases.order_id)
    if (set(scores.order_id) != set(ids) or set(flags.order_id) != set(ids)
            or not set(ids).issubset(orders.order_id)
            or set(scope.loc[scope.included_common, "order_id"]) != set(ids)
            or not set(scope.order_id).issubset(orders.order_id)):
        raise ValueError("Common cohort IDs differ; do not silently drop a method or case")
    if not cases.split.eq("validation").all():
        raise ValueError("Only frozen validation cases are permitted")
    if orders.review_id.dropna().isin(inputs["product_summary"]["held_pilot_ids"]).any():
        raise ValueError("Held pilot cases cannot enter this experiment")
    known = orders.performance_state.isin(["early", "on_date", "late"])
    if (not orders[TARGET].dropna().isin([True, False]).all()
            or not orders[TARGET].notna().equals(known)
            or not orders.loc[known, TARGET].eq(orders.loc[known, "performance_state"].eq("late")).all()):
        raise ValueError("Unknown target cannot become negative; target must match observed state")
    raw_product_scores = orders[PRODUCT_SCORE].dropna().to_numpy(float)
    if not np.isfinite(raw_product_scores).all() or ((raw_product_scores < 0) | (raw_product_scores > 100)).any():
        raise ValueError("Product score must be finite within 0..100")
    cases, scores, flags, selected = [f.set_index("order_id").loc[ids] for f in [cases, scores, flags, orders]]
    if (selected[TARGET].isna().any() or selected[PRODUCT_SCORE].isna().any()
            or not selected.score_coverage.eq("complete").all()):
        raise ValueError("Every common case needs a known target and complete scores; no imputation")
    if (cases[TARGET].isna().any() or not cases[TARGET].isin([True, False]).all()
            or not selected[TARGET].eq(cases[TARGET]).all()
            or not selected.performance_state.eq(cases.performance_state).all()):
        raise ValueError("Frozen targets disagree between pipelines")
    delivered, promise = cases.order_delivered_customer_date, cases.order_estimated_delivery_date
    if delivered.isna().any() or promise.isna().any():
        raise ValueError("Observed delivery and promise dates are required")
    y = delivered.dt.normalize().gt(promise.dt.normalize())
    if not y.eq(selected[TARGET]).all() or y.nunique() != 2:
        raise ValueError("Target must match calendar dates and contain both classes")
    selected_stages = stages.loc[stages.order_id.isin(ids)].sort_values(["order_id", "stage"])
    if not selected_stages.groupby("order_id").stage.agg(set).map(lambda names: names == set(product.signals.STAGES)).all():
        raise ValueError("All three stages are required for each common order")
    if set(selected_stages.order_id) != set(ids) or len(selected_stages) != 3*len(ids):
        raise ValueError("Common stage coverage differs")
    if (not selected_stages.assessment_status.eq("assessable").all()
            or not np.isfinite(selected_stages.delay_score_0_100.to_numpy(float)).all()):
        raise ValueError("Incomplete stage assessment in common cohort")
    for left, right, stage in product.pilot.performance.STAGES[:3]:
        observed = (cases[right]-cases[left]).dt.total_seconds()/86400
        saved = selected_stages.loc[selected_stages.stage.eq(stage)].set_index("order_id").loc[ids, "duration_days"]
        if (not np.isfinite(observed).all() or observed.lt(0).any()
                or not cases[left].lt(cases.visible_before_exclusive).all()
                or not cases[right].lt(cases.visible_before_exclusive).all()
                or not np.allclose(saved, observed, rtol=0, atol=1e-12)):
            raise ValueError("Stage durations or observation cutoffs differ")
    maximum = selected_stages.groupby("order_id").delay_score_0_100.max().loc[ids]
    if not np.array_equal(maximum.to_numpy(float), selected[PRODUCT_SCORE].to_numpy(float)):
        raise ValueError("Order score must equal the unchanged maximum stage score")
    train_ids = set(reference.order_id) | set(inputs["fit_ids"]) | set(inputs["calibration_ids"])
    cutoff = pd.Timestamp(inputs["product_summary"]["reference_end_before"])
    plan = inputs["training_plan"]
    if (set(ids) & train_ids or set(inputs["fit_ids"]) & set(inputs["calibration_ids"])
            or not reference.split.eq("train").all() or not reference.end_timestamp.lt(cutoff).all()
            or not cases.order_purchase_timestamp.ge(cutoff).all()
            or not cases.order_purchase_timestamp.ge(pd.Timestamp(plan["train_end_exclusive"])).all()
            or not cases.order_purchase_timestamp.lt(pd.Timestamp(plan["validation_end_exclusive"])).all()):
        raise ValueError("History/validation boundary or overlap violation")
    names = candidates.score_column.unique().tolist()
    if set(scores.columns) != set(names) or PRODUCT_SCORE in names or PRODUCT_CANDIDATE in set(candidates.candidate):
        raise ValueError("Score/candidate inventory differs")
    if (not candidates.groupby("score_column").family.nunique().eq(1).all()
            or not candidates.family.isin(detectors.FAMILIES).all()
            or not np.isfinite(scores[names].to_numpy(float)).all()
            or not np.isfinite(candidates.threshold.to_numpy(float)).all()):
        raise ValueError("Ambiguous family or nonfinite score/threshold")
    if set(flags.columns) != set(candidates.candidate):
        raise ValueError("Saved flag inventory differs")
    for row in candidates.itertuples(index=False):
        if (flags[row.candidate].isna().any() or not flags[row.candidate].isin([True, False]).all()
                or not flags[row.candidate].eq(scores[row.score_column].gt(row.threshold)).all()):
            raise ValueError("Frozen threshold flags differ; do not retune")
    candidates = candidates[detectors.previous.CANDIDATE_COLUMNS].copy()
    candidates["source"] = "frozen_timing_detectors"
    candidates = pd.concat([candidates, pd.DataFrame([{
        "candidate": PRODUCT_CANDIDATE, "family": "product_context_stage", "score_column": PRODUCT_SCORE,
        "threshold": 0., "threshold_origin": "existing_product_stage_warning", "tail_fraction": np.nan,
        "source": "frozen_product_stage_score"}])], ignore_index=True).sort_values("candidate").reset_index(drop=True)
    scores[PRODUCT_SCORE] = selected[PRODUCT_SCORE]
    flags[PRODUCT_CANDIDATE] = scores[PRODUCT_SCORE].gt(0)
    ledger = orders[["order_id", "review_id", "performance_state", TARGET, PRODUCT_SCORE, "score_coverage"]].copy()
    ledger["in_legacy_common_cohort"] = ledger.order_id.isin(ids)
    ledger["comparison_status"] = ledger.order_id.map(scope.set_index("order_id").comparison_status).fillna("outside_legacy_comparison_scope")
    ledger["included"] = ledger.in_legacy_common_cohort
    return (ledger.sort_values("order_id").reset_index(drop=True), cases, scores.sort_index(axis=1),
            flags.sort_index(axis=1), candidates, selected_stages, y.astype(int))


def nondominated(frame, columns):
    values = frame[columns].to_numpy(float)
    result = []
    for point in values:
        result.append(bool(np.isfinite(point).all() and not np.any(
            np.all(values >= point, axis=1) & np.any(values > point, axis=1))))
    return result


def leaders(frame, metric, identity):
    return sorted(frame.loc[frame[metric].eq(frame[metric].max()), identity].tolist())


def evaluate(inputs):
    ledger, cases, scores, flags, candidates, stages, y = aligned_inputs(inputs)
    methods = candidates[["score_column", "family", "source"]].drop_duplicates().sort_values("score_column").reset_index(drop=True)
    rows = []
    for row in methods.itertuples(index=False):
        s = scores[row.score_column].to_numpy(float)
        rows.append({**row._asdict(), "orders": len(y), "late": int(y.sum()),
                     "not_late": int((1-y).sum()), "unique_scores": len(np.unique(s)),
                     "score_orientation": "higher_is_more_anomalous", "roc_auc": float(roc_auc_score(y, s)),
                     "average_precision": float(average_precision_score(y, s))})
    metrics = pd.DataFrame(rows)
    for name in ["roc_auc", "average_precision"]:
        metrics["rank_"+name] = metrics[name].rank(method="min", ascending=False).astype(int)
    metrics["auc_ap_frontier"] = nondominated(metrics, ["roc_auc", "average_precision"])
    rows = []
    for row in candidates.itertuples(index=False):
        tn, fp, fn, tp = [int(v) for v in confusion_matrix(y, flags[row.candidate], labels=[0, 1]).ravel()]
        rows.append({**row._asdict(), "orders": len(y), "alert_count": tp+fp, "alert_fraction": (tp+fp)/len(y),
                     "tp": tp, "fp": fp, "fn": fn, "tn": tn,
                     "precision": tp/(tp+fp) if tp+fp else np.nan, "recall": tp/(tp+fn),
                     "f1": 2*tp/(2*tp+fp+fn), "false_positive_rate": fp/(fp+tn)})
    thresholds = pd.DataFrame(rows)
    thresholds["rank_f1"] = thresholds.f1.rank(method="min", ascending=False).astype(int)
    thresholds["precision_recall_frontier"] = nondominated(thresholds, ["precision", "recall"])
    family_rows = []
    for family, group in metrics.groupby("family", sort=True):
        configs = thresholds.loc[thresholds.family.eq(family)]
        family_rows.append({"family": family, "name": FAMILIES[family], "methods": len(group), "configurations": len(configs),
                            "best_auc": float(group.roc_auc.max()), "best_auc_methods": " | ".join(leaders(group, "roc_auc", "score_column")),
                            "best_ap": float(group.average_precision.max()), "best_ap_methods": " | ".join(leaders(group, "average_precision", "score_column")),
                            "best_f1": float(configs.f1.max()), "best_f1_candidates": " | ".join(leaders(configs, "f1", "candidate"))})
    families = pd.DataFrame(family_rows)
    compared = cases[["split", "order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
                      "order_delivered_customer_date", "order_estimated_delivery_date", "visible_before_exclusive",
                      "performance_state", TARGET, "customer_state", "distance_band"]].copy()
    summary = {
        "version": VERSION, "evaluation": protocol()["evaluation"], "target": protocol()["target"],
        "source_orders": len(ledger), "compared_orders": len(y), "late": int(y.sum()), "not_late": int((1-y).sum()),
        "positive_prevalence": float(y.mean()), "excluded_orders": int((~ledger.included).sum()),
        "exclusion_counts": ledger.loc[~ledger.included, "comparison_status"].value_counts().sort_index().to_dict(),
        "methods": len(metrics), "families": len(families), "fixed_configurations": len(thresholds),
        "common_order_ids_sha256": hashlib.sha256("\n".join(scores.index).encode()).hexdigest(),
        "validation_purchase_start": str(cases.order_purchase_timestamp.min()),
        "validation_purchase_end": str(cases.order_purchase_timestamp.max()),
        "legacy_fit_orders": len(inputs["fit_ids"]), "legacy_calibration_orders": len(inputs["calibration_ids"]),
        "legacy_training_plan": inputs["training_plan"],
        "product_reference_orders": int(inputs["reference"].order_id.nunique()),
        "product_reference_end_before": inputs["product_summary"]["reference_end_before"],
        "common_product_stage_reference_scopes": stages.selected_reference_scope.value_counts().sort_index().to_dict(),
        "best_roc_auc": float(metrics.roc_auc.max()), "best_roc_auc_methods": leaders(metrics, "roc_auc", "score_column"),
        "best_average_precision": float(metrics.average_precision.max()),
        "best_average_precision_methods": leaders(metrics, "average_precision", "score_column"),
        "best_fixed_f1": float(thresholds.f1.max()), "best_fixed_f1_candidates": leaders(thresholds, "f1", "candidate"),
        "auc_ap_frontier_methods": metrics.loc[metrics.auc_ap_frontier, "score_column"].tolist(),
        "precision_recall_frontier_configurations": int(thresholds.precision_recall_frontier.sum()),
        **{key: False for key in ["test_scored", "models_loaded", "models_retrained", "thresholds_optimized",
                                 "score_orientation_changed", "model_activated", "independent_anomaly_labels_used",
                                 "confirmed_bottleneck_accuracy_computed", "near_real_time_validated",
                                 "causes_confirmed", "confidence_intervals_computed", "statistical_superiority_claimed"]},
    }
    frames = {"cohort_ledger.csv": ledger, "common_outcomes.csv": compared.reset_index(),
              "common_scores.csv": scores.reset_index(), "common_flags.csv": flags.reset_index(),
              "candidate_definitions.csv": candidates, "method_metrics.csv": metrics,
              "fixed_threshold_metrics.csv": thresholds, "family_summary.csv": families}
    return frames, summary


def render_report(frames, s):
    metrics = frames["method_metrics.csv"].sort_values(["rank_average_precision", "rank_roc_auc", "score_column"])
    thresholds = frames["fixed_threshold_metrics.csv"].sort_values(["rank_f1", "candidate"])
    product_row = metrics.set_index("score_column").loc[PRODUCT_SCORE]
    lines = ["# So sánh công bằng các điểm phát hiện bất thường", "",
             "**Mục tiêu đối chiếu: giao trễ ngày hẹn đã quan sát. Không phải nhãn nghẽn đã được xác nhận.**", "",
             "## Phạm vi và tính công bằng", "",
             f"- Cùng {s['compared_orders']} đơn Olist bed_bath_table thật: {s['late']} trễ, {s['not_late']} không trễ.",
             f"- {s['methods']} cách tính điểm, {s['fixed_configurations']} cấu hình ngưỡng, {s['families']} nhóm phương pháp.",
             f"- Tỷ lệ trễ (mốc AP của điểm hằng): {s['positive_prevalence']:.4f}.",
             f"- Khoảng mua hàng: {s['validation_purchase_start']} đến {s['validation_purchase_end']}.",
             f"- Loại {s['excluded_orders']}/{s['source_orders']} đơn khỏi phép so chung: {json.dumps(s['exclusion_counts'], ensure_ascii=False)}.",
             "- 118 đơn pilot nằm ngoài phạm vi detector chung, không tự thêm vào một phía. Thiếu điểm không điền 0.",
             "- Kiểm tra trùng ID, cùng nhãn/ngày giao/ngày hẹn, cùng ba thời lượng và mốc quan sát; không chồng lấn train/calibration với tập so sánh.",
             "- Giữ nguyên chiều điểm và mọi ngưỡng. 120 cấu hình cũ dùng 21 cột điểm; cùng một cột chỉ tính AUC/AP một lần.",
             "- Thêm điểm công đoạn mới và ngưỡng > 0 vốn đã có. Không thử ngưỡng mới hoặc huấn luyện lại.", "",
             "## Các cách tính điểm đang so sánh", "",
             "- upper_q950: lấy thời lượng từng công đoạn trừ Q95 của công đoạn đó trên tập fit, rồi lấy phần dư lớn nhất. Điểm vẫn có thể âm; cảnh báo khi > 0. Các bản Q97.5/Q99/Q99.5 tương tự.",
             "- IQR/MAD: độ lệch phía dài được chuẩn hóa bằng độ phân tán lịch sử, lấy lớn nhất qua ba công đoạn; có bản dữ liệu gốc và log1p.",
             "- Isolation Forest, LOF, One-Class SVM: điểm bất thường đã lưu từ ba thời lượng, giữ nguyên biến đổi dữ liệu và tham số từng bản.",
             "- Ensemble: kết hợp thứ hạng điểm so với tập calibration; không học trọng số mới trong bước này.",
             "- Điểm công đoạn: mức vượt phần đuôi dài trong nhóm tham chiếu bối cảnh, thang 0-100; phần lớn đơn nhận 0. Do đó có nhiều điểm bằng nhau hơn phương pháp phần dư liên tục.", "",
             "## Kết quả theo cách tính điểm", "",
             "| Điểm | ROC-AUC | AP | Hạng AUC | Hạng AP |",
             "| --- | ---: | ---: | ---: | ---: |"]
    for r in metrics.itertuples(index=False):
        lines.append(f"| {r.score_column} | {r.roc_auc:.6f} | {r.average_precision:.6f} | {r.rank_roc_auc} | {r.rank_average_precision} |")
    lines += ["", f"- AUC cao nhất: {', '.join(s['best_roc_auc_methods'])} ({s['best_roc_auc']:.6f}).",
              f"- AP cao nhất: {', '.join(s['best_average_precision_methods'])} ({s['best_average_precision']:.6f}).",
              f"- Nhóm không bị phương pháp khác vượt đồng thời cả AUC và AP: {', '.join(s['auc_ap_frontier_methods'])}.",
              f"- Điểm công đoạn mới trên đúng tập chung: AUC {product_row.roc_auc:.6f}, AP {product_row.average_precision:.6f}.",
              "  Không dùng lại AUC 0.850714 trên 1.411 đơn của thí nghiệm trước để so với 1.307 đơn này.", "",
              "AUC đo khả năng xếp đơn trễ cao điểm hơn đơn không trễ, tính nửa điểm cho cặp bằng điểm.",
              "AP tổng hợp precision theo từng phần tăng recall, không phải diện tích PR nội suy hình thang.",
              "Không ghép hai chỉ số thành một điểm tối ưu tùy ý. Điểm cao không phải xác suất nghẽn.", "",
              "## Các cấu hình có F1 cao nhất trong ngưỡng đã có", "",
              "F1 cân bằng precision và recall của giao trễ với trọng số bằng nhau; không bảo đảm ít sai nhất với mọi chi phí.",
              "Đây là xếp hạng khám phá trong các cấu hình cũ, không tự động chọn ngưỡng triển khai.", "",
              "| Cấu hình | Cảnh báo | TP | FP | FN | TN | Precision | Recall | F1 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    display = pd.concat([thresholds.head(8), thresholds.loc[thresholds.candidate.eq(PRODUCT_CANDIDATE)]]).drop_duplicates("candidate")
    for r in display.itertuples(index=False):
        precision = f"{r.precision:.4f}" if pd.notna(r.precision) else "không xác định"
        lines.append(f"| {r.candidate} | {r.alert_count} | {r.tp} | {r.fp} | {r.fn} | {r.tn} | {precision} | {r.recall:.4f} | {r.f1:.4f} |")
    lines += ["", f"Có {s['precision_recall_frontier_configurations']} cấu hình không bị vượt đồng thời precision/recall; xem bảng CSV đầy đủ.",
              "FP ở đây chỉ là cảnh báo trên đơn không giao trễ. Đơn giao sớm vẫn có thể có công đoạn chậm, nên không được coi tất cả FP là cảnh báo nghẽn sai.", "",
              "## Những khác biệt chưa được kiểm soát", "",
              f"- 21 điểm cũ dùng cùng ba thời lượng, {s['legacy_fit_orders']} đơn fit và {s['legacy_calibration_orders']} đơn calibration. Fit trước 01/12/2017; train/calibration trước 01/03/2018.",
              f"- Điểm công đoạn dùng {s['product_reference_orders']} đơn lịch sử trước {s['product_reference_end_before']}, thêm tham chiếu sản phẩm/seller/số item/khu vực/khoảng cách và fallback.",
              f"- Trong tập chung, số dòng công đoạn theo loại tham chiếu: {json.dumps(s['common_product_stage_reference_scopes'], ensure_ascii=False)}.",
              "- Vì vậy, phần so với điểm công đoạn là so sánh các pipeline đã có; chưa thể quy toàn bộ chênh lệch cho thuật toán. Phần lớn vẫn dùng tham chiếu ngành/bối cảnh, không phải mô hình riêng mỗi sản phẩm.",
              "- Các ngưỡng đuôi calibration bằng nhau không tạo cùng số cảnh báo validation. Luôn xem số cảnh báo cùng precision/recall.", "",
              "## Kết luận được phép và chưa được phép", "",
              f"Ưu tiên kiểm chứng tiếp điểm dẫn đầu AP: {', '.join(s['best_average_precision_methods'])}; đối chiếu với cấu hình dẫn đầu F1 hiện có: {', '.join(s['best_fixed_f1_candidates'])}.",
              "Không tự ghép hai phương pháp hoặc chọn trọng số mới từ chính kết quả validation này.",
              "Không kết luận đã tìm mô hình tối ưu phổ quát hoặc xác nhận nguyên nhân nghẽn.",
              "1. Cơ sở ngày hẹn chưa xác minh; nhãn giao trễ không bao quát mọi bất thường.",
              "2. Dùng thời lượng đã hoàn tất, có chung mốc giao hàng với nhãn: chỉ là phân biệt hồi cứu, chưa chứng minh phát hiện sớm/near real-time.",
              "3. Validation đã được xem nhiều lần và đang so nhiều phương pháp; kết quả dẫn đầu có thể lạc quan do lựa chọn. Test chưa được chấm trong bước này; không gọi lịch sử test là hoàn toàn chưa từng được khảo sát.",
              "4. Chưa có khoảng tin cậy hay kiểm định chênh lệch; các đơn có thể phụ thuộc theo seller/sản phẩm/thời gian. Chênh lệch nhỏ chưa chứng minh ưu thế chắc chắn.",
              "5. Chỉ đánh giá đơn đủ dữ liệu; không suy rộng kết quả sang đơn còn đang giao, đơn bị loại, mọi ngành hay sàn khác.",
              "6. Không tạo giao dịch/nhãn giả, không gán thay nhãn người rà soát, không thay chính sách cảnh báo hiện tại.", "",
              "## Tệp và tái lập", "",
              "- method_metrics.csv: toàn bộ AUC/AP; fixed_threshold_metrics.csv: toàn bộ cấu hình và ma trận nhầm lẫn.",
              "- family_summary.csv: ứng viên tốt nhất từng họ; cohort_ledger.csv: đầy đủ đơn vào/ra.",
              "- common_outcomes.csv, common_scores.csv, common_flags.csv: dữ liệu đối chiếu chính xác.",
              "- manifest.json: hash nguồn, mã, thư viện; protocol.json: quy ước phép so sánh.",
              "- Chạy: .venv/bin/python -B scripts/03_anomaly_detection/compare_delivery_discrimination.py --output <thư_mục_mới>",
              "- Xác minh: cùng lệnh với --verify --output <thư_mục_kết_quả>", ""]
    return "\n".join(lines)


def run(output=DEFAULT_OUTPUT):
    output = product.pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite a comparison experiment")
    inputs, source = load_inputs()
    frames, summary = evaluate(inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".delivery-comparison-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"bao_cao_so_sanh.md").write_text(render_report(frames, summary), encoding="utf-8")
        verify_sources(source)
        review.write_json(staging/"manifest.json", {
            "version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provenance": source, "summary": summary,
            "libraries": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
            "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())},
        })
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "protocol.json", "bao_cao_so_sanh.md"}):
        raise ValueError("Invalid comparison manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    detectors.load_snapshot()
    product.load_snapshot()
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Comparison protocol or summary changed")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = load_snapshot(args.output)["summary"] if args.verify else run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
