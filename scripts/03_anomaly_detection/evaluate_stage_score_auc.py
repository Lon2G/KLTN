"""Exploratory retrospective AUC against observed calendar lateness, not bottleneck truth."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, confusion_matrix, precision_recall_curve, roc_auc_score, roc_curve

import build_product_stage_scores as product

DEFAULT_OUTPUT = product.ROOT/"data/experiments/olist_stage_score_auc_v1"
VERSION = "stage_score_auc_v1"
SCORE = "max_stage_delay_score"
TARGET = "calendar_late_signal"


def evaluate(orders):
    product.shared.business.require_key(orders, ["order_id"], "AUC order")
    frame = orders[["order_id", "review_id", "performance_state", TARGET, SCORE, "score_coverage"]].copy()
    known = frame.performance_state.isin(["early", "on_date", "late"])
    if (not frame[TARGET].dropna().isin([True, False]).all()
            or not frame[TARGET].notna().equals(known)
            or not frame.loc[known, TARGET].eq(frame.loc[known, "performance_state"].eq("late")).all()):
        raise ValueError("Target must be observed calendar lateness; unknown is not negative")
    scores = frame[SCORE].dropna().to_numpy(float)
    if not np.isfinite(scores).all() or ((scores < 0) | (scores > 100)).any():
        raise ValueError("Existing stage scores must be finite and within 0..100")
    frame["evaluation_status"] = np.select(
        [~known & frame[SCORE].isna(), ~known, frame[SCORE].isna()],
        ["missing_target_and_score", "missing_target", "missing_score"], default="included")
    selected = frame.loc[frame.evaluation_status.eq("included")].sort_values("order_id")
    y, s = selected[TARGET].astype(int).to_numpy(), selected[SCORE].to_numpy(float)
    if len(np.unique(y)) != 2:
        raise ValueError("ROC-AUC needs both late and non-late observed orders")
    tn, fp, fn, tp = [int(v) for v in confusion_matrix(y, s > 0, labels=[0, 1]).ravel()]
    summary = {
        "version": VERSION, "target": "observed_delivery_date_after_estimated_delivery_date",
        "score": SCORE, "category": "bed_bath_table", "evaluation": "retrospective_exploratory_validation",
        "source_orders": len(frame), "evaluated_orders": len(selected), "late": int(y.sum()),
        "not_late": int((1-y).sum()), "excluded_orders": len(frame)-len(selected),
        "exclusion_counts": frame.loc[frame.evaluation_status.ne("included"), "evaluation_status"].value_counts().to_dict(),
        "excluded_known_late": int(frame.loc[frame.evaluation_status.ne("included"), TARGET].sum()),
        "score_coverage": selected.score_coverage.value_counts().to_dict(),
        "zero_scores": int((s == 0).sum()), "unique_scores": len(np.unique(s)),
        "roc_auc": float(roc_auc_score(y, s)),
        "average_precision": float(average_precision_score(y, s)),
        "positive_prevalence": float(y.mean()),
        "fixed_existing_warning": {
            "rule": "max_stage_delay_score > 0; not optimized here",
            "true_positive": tp, "false_positive": fp, "false_negative": fn, "true_negative": tn,
            "precision_for_lateness": tp/(tp+fp) if tp+fp else None,
            "recall_for_lateness": tp/(tp+fn),
        },
        "bottleneck_auc_computed": False, "held_out_test_scored": False,
        "independent_anomaly_labels_used": False, "models_retrained": False,
        "thresholds_optimized": False, "model_selected": False,
    }
    fpr, tpr, thresholds = roc_curve(y, s, drop_intermediate=False)
    # Infinity means predict no positives, not a valid score within the 0..100 range.
    roc = pd.DataFrame({"false_positive_rate": fpr, "true_positive_rate": tpr,
                        "threshold_ge": np.where(np.isfinite(thresholds), thresholds, np.nan),
                        "predict_none": ~np.isfinite(thresholds)})
    precision, recall, pr_thresholds = precision_recall_curve(y, s)
    pr = pd.DataFrame({"precision": precision, "recall": recall,
                       "threshold_ge": np.r_[pr_thresholds, np.nan]})
    return {"evaluation_orders.csv": frame, "roc_curve.csv": roc, "precision_recall_curve.csv": pr}, summary


def render_report(s):
    c = s["fixed_existing_warning"]
    return f"""# AUC thử nghiệm của điểm công đoạn

**Đây là đánh giá hồi cứu giao trễ ngày hẹn, KHÔNG phải AUC phát hiện nghẽn đã được kiểm chứng.**

## Dữ liệu và cách tính

- Dùng dữ liệu Olist thật, ngành bed_bath_table, từ snapshot olist_product_stage_scores_v1.
- Tổng {s['source_orders']} đơn validation trong phạm vi nguồn; đánh giá {s['evaluated_orders']} đơn đủ điểm và kết quả giao hàng.
- Lớp 1: {s['late']} đơn giao sau ngày hẹn. Lớp 0: {s['not_late']} đơn giao sớm hoặc đúng ngày.
- Điểm đầu vào: max_stage_delay_score, điểm cao nhất trong các công đoạn, giữ nguyên thang 0-100.
- Không lấy cờ cảnh báo 0/1 thay cho điểm khi tính AUC; không lấy số ngày trễ làm điểm để tự kiểm chứng chính nhãn.
- Loại {s['excluded_orders']} đơn thiếu điểm hoặc kết quả; không điền 0 cho dữ liệu thiếu.
- Chi tiết loại: {json.dumps(s['exclusion_counts'], ensure_ascii=False)}. Có {s['excluded_known_late']} đơn đã biết trễ nhưng thiếu điểm.
- Đánh giá toàn bộ đơn đủ điều kiện của cohort nguồn, không chỉ mẫu pilot. Không suy rộng thành toàn Olist.

## Kết quả

| Chỉ số | Giá trị |
| --- | ---: |
| ROC-AUC | {s['roc_auc']:.6f} |
| Average Precision (AP) | {s['average_precision']:.6f} |
| Tỷ lệ đơn trễ trong tập đánh giá | {s['positive_prevalence']:.6f} |

AUC tính trên các ngưỡng của điểm. Có thể kiểm tra chéo bằng tỷ lệ cặp đơn
(trễ, không trễ) được xếp đúng thứ tự, cộng nửa trọng số cho cặp bằng điểm.
{s['zero_scores']} đơn có điểm 0; {s['unique_scores']} giá trị điểm phân biệt.
Giữ nguyên các điểm bằng nhau, không thêm nhiễu để làm đẹp AUC.
AP là trung bình precision có trọng số theo phần tăng recall, không phải PR-AUC nội suy hình thang.

## Cờ cảnh báo hiện có: điểm > 0

| Kết quả thực tế | Có cảnh báo công đoạn | Không có cảnh báo |
| --- | ---: | ---: |
| Giao trễ ngày hẹn | {c['true_positive']} | {c['false_negative']} |
| Không trễ ngày hẹn | {c['false_positive']} | {c['true_negative']} |

- Precision đối với giao trễ: {c['precision_for_lateness']:.2%}.
- Recall đối với giao trễ: {c['recall_for_lateness']:.2%}.
- Các ô trên chỉ đúng với mục tiêu giao trễ. Đơn giao sớm vẫn có thể có công đoạn chậm;
  không được kết luận mọi cảnh báo trên đơn không trễ đều là cảnh báo nghẽn sai.
- Không thay đổi ngưỡng hay chọn mô hình theo kết quả này.

## Giới hạn để trình bày trong khóa luận

1. Điểm sử dụng thời lượng công đoạn đã hoàn tất, cùng liên quan tới thời điểm giao hàng của nhãn.
   Đây là quan hệ hồi cứu, không chứng minh dự báo trước khi giao hoặc near real-time.
2. Cơ sở đặt ngày hẹn vẫn chưa được xác minh. Trễ ngày hẹn không đồng nghĩa đã xác nhận nguyên nhân nghẽn.
3. Không sử dụng nhãn bất thường kiểm chứng độc lập. AUC này không đánh giá lỗi nhập liệu, gian lận hoặc toàn bộ loại bất thường.
4. Đây là validation khám phá đã được xem, không phải lần đánh giá cuối trên test giữ riêng.
   Chưa tính khoảng tin cậy hoặc suy rộng thống kê; các đơn có thể phụ thuộc theo seller/sản phẩm/thời gian.
5. Loại đơn thiếu dữ liệu làm thay đổi phạm vi đánh giá. Phần lớn điểm hiện dùng tham chiếu ngành/bối cảnh
   do lịch sử riêng sản phẩm ít; không gọi đây là AUC mô hình riêng cho từng mã sản phẩm.
6. Không gán nhãn thủ công thay người dùng, không tạo giao dịch giả, không train lại hay sửa snapshot cũ.

## Tái lập và nguồn

Chạy bằng .venv/bin/python -B scripts/03_anomaly_detection/evaluate_stage_score_auc.py --output <thư_mục_mới>.
Manifest lưu hash snapshot đầu vào, mã phép tính và phiên bản thư viện.

- [scikit-learn: ROC-AUC](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.roc_auc_score.html)
- [scikit-learn: Average Precision](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.average_precision_score.html)
"""


def run(output=DEFAULT_OUTPUT):
    output = product.pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(product.review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite an AUC experiment")
    source = product.DEFAULT_OUTPUT
    before = product.review.digest(source/"manifest.json")
    manifest = product.load_snapshot(source)
    orders = product.shared.read_table(source, "order_scores.csv", manifest)
    frames, summary = evaluate(orders)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".stage-auc-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False)
        product.review.write_json(staging/"summary.json", summary)
        (staging/"bao_cao_auc.md").write_text(render_report(summary), encoding="utf-8")
        product.shared.verify_files(source, manifest["output_hashes"])
        if product.review.digest(source/"manifest.json") != before:
            raise ValueError("Source changed during evaluation")
        product.review.write_json(staging/"manifest.json", {
            "version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "source_directory": str(source), "source_manifest_sha256": before,
            "source_output_hashes": manifest["output_hashes"],
            "code_sha256": product.review.digest(Path(__file__)),
            "libraries": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
            "output_hashes": {p.name: product.review.digest(p) for p in sorted(staging.iterdir())},
        })
        if output.exists():
            raise FileExistsError("Output appeared during publication")
        staging.rename(output)
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    print(json.dumps(run(args.output), indent=2, ensure_ascii=False, allow_nan=False))
