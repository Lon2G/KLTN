"""Temporal sensitivity and a conditional choice between two frozen Olist rules."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd
import sklearn
from sklearn.metrics import average_precision_score, roc_auc_score

import compare_delivery_discrimination as previous

ROOT, shared, review = previous.ROOT, previous.shared, previous.review
VERSION = "temporal_candidate_selection_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_temporal_candidate_selection_v1"
GUIDE = ROOT/"docs/temporal_candidate_selection.md"
CANDIDATES = {"upper_q950": "upper_q950", previous.PRODUCT_CANDIDATE: previous.PRODUCT_SCORE}
NAMES = {"upper_q950": "Phân vị 95%", previous.PRODUCT_CANDIDATE: "Điểm công đoạn theo bối cảnh"}
TARGET = previous.TARGET
DATES = ["order_purchase_timestamp", "order_approved_at", "order_delivered_carrier_date",
         "order_delivered_customer_date", "order_estimated_delivery_date", "visible_before_exclusive"]
TABLES = {"paired_cases.csv", "period_metrics.csv", "monthly_coverage.csv", "candidate_stability.csv",
          "paired_comparisons.csv", "disagreement_cases.csv", "deletion_sensitivity.csv", "cost_sensitivity.csv",
          "criterion_comparison.csv"}


def protocol():
    return {"version": VERSION, "target": previous.protocol()["target"],
            "candidates": CANDIDATES, "fixed_rule": "score > 0 for both; no threshold tuning",
            "primary_periods": "calendar_purchase_month", "secondary_periods": "calendar_days_1_to_15_and_16_to_end",
            "selection_order": ["macro_monthly_f1", "worst_monthly_f1", "pooled_f1"], "tie_tolerance": 1e-12,
            "eligible_selection_periods": "both_observed_classes; same_periods_for_both_candidates",
            "single_class": "retain_raw_counts_and_defined_rates; no_auc_ap_or_selection_f1",
            "sensitivity": "remove_one_purchase_week_or_one_eligible_month; no_refitting; not_cross_validation",
            "cost_fn_to_fp_ratios": [.5, 1., 2., 5.],
            "selection_status": "provisional_validation_choice_not_production_activation",
            "inference": "descriptive; no_p_values_confidence_intervals_or_independent_test_claims"}


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in [Path(__file__), GUIDE]}


def verify_sources(source):
    for item in source["snapshots"].values():
        directory = Path(item["directory"])
        if review.digest(directory/"manifest.json") != item["manifest_sha256"]:
            raise ValueError("Temporal comparison source manifest changed")
        shared.verify_files(directory, item["output_hashes"])
    if source["code_hashes"] != code_hashes():
        raise ValueError("Temporal implementation/contract changed; use a new version")


def load_inputs():
    directory = previous.DEFAULT_OUTPUT
    manifest = previous.load_snapshot(directory)
    public_dir = previous.product.signals.DEFAULT_OUTPUT
    public_manifest = previous.product.signals.load_snapshot(public_dir)
    source = {"snapshots": {name: {"directory": str(path), "manifest_sha256": review.digest(path/"manifest.json"),
                                  "output_hashes": m["output_hashes"]}
                            for name, path, m in [("comparison", directory, manifest), ("public_signals", public_dir, public_manifest)]},
              "code_hashes": code_hashes()}
    inputs = {"outcomes": pd.read_csv(directory/"common_outcomes.csv", dtype={"order_id": "string"}, parse_dates=DATES),
              "scores": pd.read_csv(directory/"common_scores.csv", usecols=["order_id", *CANDIDATES.values()],
                                    dtype={"order_id": "string"}, float_precision="round_trip"),
              "flags": pd.read_csv(directory/"common_flags.csv", usecols=["order_id", *CANDIDATES], dtype="string"),
              "definitions": pd.read_csv(directory/"candidate_definitions.csv", float_precision="round_trip"),
              "public": shared.read_table(public_dir, "order_signals.csv", public_manifest),
              "parent_summary": manifest["summary"]}
    verify_sources(source)
    return inputs, source


def prepare_cases(inputs):
    outcomes, scores, flags, public = [inputs[k].copy() for k in ["outcomes", "scores", "flags", "public"]]
    for name, frame in [("outcomes", outcomes), ("scores", scores), ("flags", flags), ("public", public)]:
        previous.detectors.previous.require_ids(frame, name)
    ids = sorted(outcomes.order_id)
    if (set(scores.order_id) != set(ids) or set(flags.order_id) != set(ids) or not set(ids).issubset(public.order_id)
            or set(scores.columns) != {"order_id", *CANDIDATES.values()} or set(flags.columns) != {"order_id", *CANDIDATES}):
        raise ValueError("Both candidates must have the identical complete case IDs")
    if (not outcomes.split.eq("validation").all() or not public.split.eq("validation").all()
            or outcomes[DATES].isna().any().any() or public.order_purchase_timestamp.isna().any()):
        raise ValueError("Only known validation dates are permitted")
    if outcomes[TARGET].isna().any() or not outcomes[TARGET].isin([True, False]).all():
        raise ValueError("Unknown outcomes must not become negative")
    actual = outcomes.order_delivered_customer_date.dt.normalize().gt(outcomes.order_estimated_delivery_date.dt.normalize())
    if not outcomes[TARGET].eq(actual).all() or not actual.eq(outcomes.performance_state.eq("late")).all():
        raise ValueError("Target differs from recorded calendar lateness")
    for left, right, _ in previous.product.pilot.performance.STAGES[:3]:
        if (not outcomes[right].ge(outcomes[left]).all()
                or not outcomes[left].lt(outcomes.visible_before_exclusive).all()
                or not outcomes[right].lt(outcomes.visible_before_exclusive).all()):
            raise ValueError("Stage chronology or observation cutoff differs")
    history_end = pd.Timestamp(inputs["parent_summary"]["product_reference_end_before"])
    end = pd.Timestamp(inputs["parent_summary"]["legacy_training_plan"]["validation_end_exclusive"])
    if not outcomes.order_purchase_timestamp.ge(history_end).all() or not outcomes.order_purchase_timestamp.lt(end).all():
        raise ValueError("Comparison cases left the frozen temporal scope")
    source_rows = public.set_index("order_id").loc[ids]
    ordered = outcomes.set_index("order_id").loc[ids]
    for name in [*DATES, TARGET, "performance_state"]:
        if not np.array_equal(source_rows[name].to_numpy(), ordered[name].to_numpy()):
            raise ValueError("Public source and paired observations differ")
    s = inputs["parent_summary"]
    if (len(ids) != s["compared_orders"] or int(actual.sum()) != s["late"]
            or hashlib.sha256("\n".join(ids).encode()).hexdigest() != s["common_order_ids_sha256"]):
        raise ValueError("Frozen comparison scope or labels changed")
    definitions = inputs["definitions"].loc[lambda f: f.candidate.isin(CANDIDATES)].set_index("candidate")
    if set(definitions.index) != set(CANDIDATES) or not definitions.index.is_unique:
        raise ValueError("Two unique frozen candidate definitions are required")
    frame = ordered.copy()
    scores, flags = [f.set_index("order_id").loc[ids] for f in [scores, flags]]
    for candidate, score in CANDIDATES.items():
        if definitions.loc[candidate, "score_column"] != score or definitions.loc[candidate, "threshold"] != 0:
            raise ValueError("Frozen candidate definition/threshold changed")
        if not np.isfinite(scores[score].to_numpy(float)).all() or not flags[candidate].isin(["True", "False"]).all():
            raise ValueError("Nonfinite score or missing/invalid flag")
        predicted = flags[candidate].eq("True")
        if not predicted.eq(scores[score].gt(0)).all():
            raise ValueError("Frozen score/threshold/flag mismatch")
        frame[score] = scores[score]
        frame[candidate+"_flag"] = predicted
    if not frame[previous.PRODUCT_SCORE].between(0, 100).all():
        raise ValueError("Product score must stay within its original range")
    dates = frame.order_purchase_timestamp
    frame["purchase_month"] = dates.dt.strftime("%Y-%m")
    frame["purchase_half_month"] = frame.purchase_month+np.where(dates.dt.day.le(15), "-H1", "-H2")
    frame["purchase_week"] = (dates.dt.normalize()-pd.to_timedelta(dates.dt.dayofweek, unit="D")).dt.strftime("%Y-%m-%d")
    frame["followup_available_days"] = (frame.visible_before_exclusive-dates).dt.total_seconds()/86400
    return frame.reset_index()


def metrics(frame, candidate):
    y = frame[TARGET].to_numpy(bool)
    predicted = frame[candidate+"_flag"].to_numpy(bool)
    tp, fp = int((y & predicted).sum()), int((~y & predicted).sum())
    fn, tn = int((y & ~predicted).sum()), int((~y & ~predicted).sum())
    n, positives = len(y), int(y.sum())
    both = 0 < positives < n
    f1 = 2*tp/(2*tp+fp+fn) if 2*tp+fp+fn else np.nan
    scores = frame[CANDIDATES[candidate]].to_numpy(float)
    return {"candidate": candidate, "orders": n, "late": positives, "not_late": n-positives,
            "positive_prevalence": positives/n if n else np.nan, "both_classes": both,
            "metric_status": "two_classes" if both else "empty" if not n else "no_late_orders" if not positives else "no_nonlate_orders",
            "tp": tp, "fp": fp, "fn": fn, "tn": tn, "alerts": tp+fp, "alert_rate": (tp+fp)/n if n else np.nan,
            "precision": tp/(tp+fp) if tp+fp else np.nan, "recall": tp/positives if positives else np.nan,
            "f1_raw": f1, "f1_for_selection": f1 if both else np.nan,
            "false_positive_rate": fp/(fp+tn) if fp+tn else np.nan,
            "roc_auc": float(roc_auc_score(y, scores)) if both else np.nan,
            "average_precision": float(average_precision_score(y, scores)) if both else np.nan,
            "equal_cost_errors": fp+fn}


def period_metrics(cases):
    rows = [{"period_kind": "pooled", "period": "all", **metrics(cases, candidate)} for candidate in CANDIDATES]
    for kind, column in [("month", "purchase_month"), ("half_month", "purchase_half_month")]:
        for period, frame in cases.groupby(column, sort=True):
            rows += [{"period_kind": kind, "period": period, **metrics(frame, candidate)} for candidate in CANDIDATES]
    return pd.DataFrame(rows)


def rank_candidates(periods, required_months):
    rows = []
    for candidate in CANDIDATES:
        monthly = periods.loc[periods.period_kind.eq("month") & periods.candidate.eq(candidate)
                              & periods.period.isin(required_months)]
        if (not required_months or set(monthly.period) != set(required_months) or not monthly.both_classes.all()):
            return pd.DataFrame(), []
        overall = periods.loc[periods.period_kind.eq("pooled") & periods.candidate.eq(candidate)].iloc[0]
        rows.append({"candidate": candidate, "eligible_months": len(monthly),
                     "macro_monthly_f1": float(monthly.f1_for_selection.mean()),
                     "worst_monthly_f1": float(monthly.f1_for_selection.min()),
                     "monthly_f1_range": float(monthly.f1_for_selection.max()-monthly.f1_for_selection.min()),
                     "macro_monthly_precision": float(monthly.precision.mean(skipna=False)),
                     "macro_monthly_recall": float(monthly.recall.mean()),
                     "macro_monthly_auc": float(monthly.roc_auc.mean()),
                     "macro_monthly_ap": float(monthly.average_precision.mean()),
                     "pooled_f1": float(overall.f1_raw), "pooled_fp": int(overall.fp), "pooled_fn": int(overall.fn),
                     "pooled_equal_cost_errors": int(overall.equal_cost_errors)})
    table = pd.DataFrame(rows)
    remaining = table.copy()
    for key in protocol()["selection_order"]:
        best = remaining[key].max()
        remaining = remaining.loc[np.isclose(remaining[key], best, rtol=0, atol=protocol()["tie_tolerance"])]
    winners = sorted(remaining.candidate)
    table["chosen_by_decision_order"] = table.candidate.isin(winners)
    return table, winners


def coverage(inputs, cases):
    public = inputs["public"].copy()
    public["month"] = public.order_purchase_timestamp.dt.strftime("%Y-%m")
    rows = []
    for month, full in public.groupby("month", sort=True):
        common = cases.loc[cases.purchase_month.eq(month)]
        excluded = full.loc[~full.order_id.isin(cases.order_id)]
        month_end = pd.Period(month, "M").end_time.normalize()+pd.Timedelta(days=1)
        rows.append({"month": month, "public_source_orders": len(full), "compared_orders": len(common),
                     "excluded_public_orders": len(excluded), "coverage_fraction": len(common)/len(full),
                     "compared_late": int(common[TARGET].sum()), "compared_nonlate": int((~common[TARGET]).sum()),
                     "excluded_known_late": int(excluded[TARGET].sum()), "excluded_unknown_outcome": int(excluded[TARGET].isna().sum()),
                     "compared_late_prevalence": float(common[TARGET].mean()) if len(common) else np.nan,
                     "earliest_purchase": common.order_purchase_timestamp.min(), "latest_purchase": common.order_purchase_timestamp.max(),
                     "observation_cutoff": full.visible_before_exclusive.max(),
                     "observation_cutoff_before_month_end": bool(full.visible_before_exclusive.max() < month_end),
                     "followup_min_days": float(common.followup_available_days.min()),
                     "followup_median_days": float(common.followup_available_days.median()),
                     "followup_max_days": float(common.followup_available_days.max())})
    return pd.DataFrame(rows)


def paired_agreement(cases):
    left, right = CANDIDATES
    rows = []
    for period, group in [("all", cases), *list(cases.groupby("purchase_month", sort=True))]:
        a, b, y = [group[col].to_numpy(bool) for col in [left+"_flag", right+"_flag", TARGET]]
        for label, mask in [("all", np.ones(len(y), bool)), ("late", y), ("not_late", ~y)]:
            rows.append({"period": period, "outcome_group": label, "orders": int(mask.sum()),
                         "both_flag": int((mask & a & b).sum()), "q95_only": int((mask & a & ~b).sum()),
                         "context_only": int((mask & ~a & b).sum()), "neither_flag": int((mask & ~a & ~b).sum())})
    disagreement = cases.loc[cases[left+"_flag"].ne(cases[right+"_flag"])].copy()
    disagreement["only_warning_from"] = np.where(disagreement[left+"_flag"], left, right)
    return pd.DataFrame(rows), disagreement


def deletion_sensitivity(cases, required_months):
    rows = []
    for kind, column, periods in [("week", "purchase_week", sorted(cases.purchase_week.unique())),
                                   ("month", "purchase_month", required_months)]:
        for period in periods:
            dropped = cases[column].eq(period)
            remaining = cases.loc[~dropped]
            required = [m for m in required_months if kind != "month" or m != period]
            ranked, winners = rank_candidates(period_metrics(remaining), required)
            if ranked.empty:
                rows.append({"removed_kind": kind, "removed_period": period, "removed_orders": int(dropped.sum()),
                             "remaining_orders": len(remaining), "comparable": False,
                             "reason": "required_month_missing_a_class", "winner": None})
            else:
                values = ranked.set_index("candidate")
                rows.append({"removed_kind": kind, "removed_period": period, "removed_orders": int(dropped.sum()),
                             "remaining_orders": len(remaining), "comparable": True, "reason": "same_required_months",
                             "winner": " | ".join(winners), "eligible_months": len(required),
                             "q95_macro_f1": float(values.loc["upper_q950", "macro_monthly_f1"]),
                             "context_macro_f1": float(values.loc[previous.PRODUCT_CANDIDATE, "macro_monthly_f1"]),
                             "touches_selection_months": bool(cases.loc[dropped, "purchase_month"].isin(required_months).any())})
    return pd.DataFrame(rows)


def build_tables(inputs):
    cases = prepare_cases(inputs)
    periods = period_metrics(cases)
    eligible = sorted(periods.loc[periods.period_kind.eq("month") & periods.both_classes, "period"].unique())
    stability, winners = rank_candidates(periods, eligible)
    if stability.empty:
        raise ValueError("No comparable two-class purchase months for selecting a candidate")
    agreement, disagreements = paired_agreement(cases)
    sensitivity = deletion_sensitivity(cases, eligible)
    cost_rows = []
    pooled = periods.loc[periods.period_kind.eq("pooled")].set_index("candidate")
    for ratio in protocol()["cost_fn_to_fp_ratios"]:
        losses = {c: float(pooled.loc[c, "fp"]+ratio*pooled.loc[c, "fn"]) for c in CANDIDATES}
        cost_rows += [{"fn_to_fp_cost_ratio": ratio, "candidate": c, "weighted_errors": value,
                       "lowest_cost": bool(value == min(losses.values()))} for c, value in losses.items()]
    criterion_rows = []
    for name in [*protocol()["selection_order"], "macro_monthly_precision", "macro_monthly_recall", "macro_monthly_auc",
                 "macro_monthly_ap", "pooled_equal_cost_errors"]:
        higher = name != "pooled_equal_cost_errors"
        best = stability[name].max() if higher else stability[name].min()
        criterion_rows += [{"criterion": name, "candidate": r.candidate, "value": float(getattr(r, name)),
                            "direction": "higher" if higher else "lower", "best": bool(np.isclose(getattr(r, name), best, rtol=0, atol=1e-12))}
                           for r in stability.itertuples(index=False)]
    q95, context = [pooled.loc[c] for c in CANDIDATES]
    d_fp, d_fn = float(q95.fp-context.fp), float(q95.fn-context.fn)
    crossover = -d_fp/d_fn if d_fn and -d_fp/d_fn > 0 else None
    half = periods.loc[periods.period_kind.eq("half_month") & periods.both_classes]
    half_means = half.groupby("candidate").f1_for_selection.mean()
    half_winners = sorted(half_means.index[np.isclose(half_means, half_means.max(), rtol=0, atol=1e-12)]) if len(half_means) else []
    selected = winners[0] if len(winners) == 1 else None
    selection = {"version": VERSION, "status": "provisional_validation_choice" if selected else "unresolved_tie",
                 "selected_candidate": selected, "tied_candidates": winners if selected is None else [],
                 "score_column": CANDIDATES[selected] if selected else None, "threshold": 0., "operator": ">",
                 "decision_order": protocol()["selection_order"], "eligible_purchase_months": eligible,
                 "selection_target": previous.protocol()["target"], "production_activated": False,
                 "confirmed_bottleneck_optimum": False, "independent_test_validation": False,
                 "interpretation": "Best of these two frozen configurations under the declared monthly F1 preference; not a universal optimum."}
    weeks = sensitivity.loc[sensitivity.removed_kind.eq("week") & sensitivity.comparable]
    month_checks = sensitivity.loc[sensitivity.removed_kind.eq("month") & sensitivity.comparable]
    relevant_weeks = weeks.loc[weeks.touches_selection_months.eq(True)]
    reversals = weeks.loc[weeks.winner.ne(selected)] if selected else weeks
    summary = {"version": VERSION, "orders": len(cases), "late": int(cases[TARGET].sum()),
               "months": int(cases.purchase_month.nunique()), "eligible_selection_months": eligible,
               "single_class_months": sorted(set(cases.purchase_month)-set(eligible)),
               "late_counts_by_month": {str(k): int(v) for k, v in cases.groupby("purchase_month")[TARGET].sum().items()},
               "selected_candidate": selected, "half_month_macro_f1_winners": half_winners,
               "half_month_macro_f1": {str(k): float(v) for k, v in half_means.items()},
               "week_deletions": int(sensitivity.removed_kind.eq("week").sum()), "comparable_week_deletions": len(weeks),
               "week_deletion_winners": weeks.winner.value_counts().sort_index().to_dict(),
               "relevant_week_deletions": len(relevant_weeks),
               "relevant_week_deletion_winners": relevant_weeks.winner.value_counts().sort_index().to_dict(),
               "week_selection_reversals": reversals[["removed_period", "winner", "q95_macro_f1", "context_macro_f1"]].to_dict("records"),
               "month_deletion_winners": month_checks.winner.value_counts().sort_index().to_dict(),
               "candidate_disagreement_orders": len(disagreements), "fn_to_fp_cost_crossover": crossover,
               "all_sensitivity_checks_agree": bool(selected and len(weeks) and len(month_checks)
                                                      and len(weeks) == int(sensitivity.removed_kind.eq("week").sum())
                                                      and len(month_checks) == len(eligible)
                                                      and weeks.winner.eq(selected).all() and month_checks.winner.eq(selected).all()
                                                      and half_winners == [selected]),
               **{k: False for k in ["test_scored", "models_loaded", "models_retrained", "thresholds_optimized", "production_activated",
                                     "new_reference_labels", "confirmed_bottleneck_accuracy", "statistical_significance_claimed",
                                     "independent_cross_validation", "near_real_time_validated"]}}
    frames = {"paired_cases.csv": cases, "period_metrics.csv": periods, "monthly_coverage.csv": coverage(inputs, cases),
              "candidate_stability.csv": stability, "paired_comparisons.csv": agreement, "disagreement_cases.csv": disagreements,
              "deletion_sensitivity.csv": sensitivity, "cost_sensitivity.csv": pd.DataFrame(cost_rows),
              "criterion_comparison.csv": pd.DataFrame(criterion_rows)}
    return frames, summary, selection


def display(value):
    return "N/A" if pd.isna(value) else f"{value:.4f}"


def render_report(frames, summary, selection):
    selected = selection["selected_candidate"]
    lines = ["# Độ ổn định theo thời gian và lựa chọn ứng viên", "",
             "**Chỉ đánh giá hồi cứu giao trễ ngày hẹn, chưa phải độ chính xác xác nhận nghẽn.**", "",
             "## Quy tắc chọn", "",
             "Ưu tiên F1 trung bình các tháng có cả đơn trễ và không trễ, mỗi tháng trọng số bằng nhau.",
             "Nếu hòa: xét F1 tháng kém nhất, rồi F1 gộp. Giữ nguyên cả hai ngưỡng > 0; không tìm ngưỡng mới.",
             "F1 là cách cân bằng precision/recall đã công bố trước khi xem kết quả từng ứng viên theo tháng, không phải hàm chi phí do giảng viên xác nhận.", "",
             f"**Ứng viên chọn theo quy tắc trên: {NAMES[selected] if selected else 'chưa phân định'} ({selected}).**",
             "Lưu lựa chọn thử nghiệm trong selection.json; chưa kích hoạt vào pipeline cũ.", "",
             "## Kết quả theo tháng", "",
             "| Tháng | Ứng viên | Số đơn | Trễ | TP | FP | FN | Precision | Recall | F1 chọn | AUC | AP |",
             "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in frames["period_metrics.csv"].query("period_kind == 'month'").itertuples(index=False):
        lines.append(f"| {r.period} | {NAMES[r.candidate]} | {r.orders} | {r.late} | {r.tp} | {r.fp} | {r.fn} | {display(r.precision)} | {display(r.recall)} | {display(r.f1_for_selection)} | {display(r.roc_auc)} | {display(r.average_precision)} |")
    lines += ["", "Tháng không có đơn trễ không cho biết khả năng tìm đơn trễ. Không gán AUC/AP=0 hoặc 1.",
              "Bảng CSV vẫn lưu F1 thô nếu công thức xác định, nhưng không dùng F1 tháng một lớp để chọn; vẫn tính FP/tháng đó và giữ mọi đơn trong kết quả gộp.", "",
              "## Đối chiếu tiêu chí", "",
              "| Ứng viên | F1 tháng trung bình | F1 tháng kém nhất | Dao động F1 | F1 gộp | FP gộp | FN gộp |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for r in frames["candidate_stability.csv"].itertuples(index=False):
        lines.append(f"| {NAMES[r.candidate]} | {r.macro_monthly_f1:.4f} | {r.worst_monthly_f1:.4f} | {r.monthly_f1_range:.4f} | {r.pooled_f1:.4f} | {r.pooled_fp} | {r.pooled_fn} |")
    lines += ["", "AUC/AP, precision, recall và số lỗi có thể chọn ứng viên khác nhau. Xem criterion_comparison.csv; không tự cộng chúng thành một điểm chất lượng.",
              "FP chỉ là cảnh báo trên đơn không trễ, không tự chứng minh cảnh báo công đoạn sai. Không bỏ cảnh báo của đơn giao sớm.", "",
              "## Kiểm tra độ nhạy", "",
              f"- F1 trung bình theo nửa tháng: {json.dumps(summary['half_month_macro_f1'], ensure_ascii=False)}.",
              f"- Bỏ từng tuần: {summary['comparable_week_deletions']}/{summary['week_deletions']} lượt so được; số lần dẫn đầu: {json.dumps(summary['week_deletion_winners'], ensure_ascii=False)}.",
              f"- Trong đó chỉ {summary['relevant_week_deletions']} tuần có đơn thuộc các tháng dùng tiêu chí chọn: {json.dumps(summary['relevant_week_deletion_winners'], ensure_ascii=False)}. Bỏ tuần chỉ có tháng một lớp không làm đổi F1 trung bình tháng dùng để chọn.",
              f"- Những lần đảo lựa chọn: {json.dumps(summary['week_selection_reversals'], ensure_ascii=False)}.",
              f"- Bỏ từng tháng có hai lớp: {json.dumps(summary['month_deletion_winners'], ensure_ascii=False)}.",
              f"- Tất cả kiểm tra bổ sung cùng lựa chọn: {summary['all_sensitivity_checks_agree']}.",
              "Các lượt bỏ dữ liệu dùng lại phần lớn cùng đơn và không train lại. Đây là kiểm tra ảnh hưởng của giai đoạn, KHÔNG phải các fold kiểm chứng độc lập hay xác suất ứng viên đúng.",
              f"- Có {summary['candidate_disagreement_orders']} đơn hai ứng viên cảnh báo khác nhau; lưu đầy đủ ID và dữ kiện, không gán nhãn mới.",
              f"- Mốc đổi ưu tiên theo chi phí giả định FP + r*FN: r = {display(summary['fn_to_fp_cost_crossover'])}. Đọc cost_sensitivity.csv cho r=0.5, 1, 2, 5.",
              "r là mức ưu tiên bỏ sót so với cảnh báo trên đơn không trễ, không phải giá trị tiền hay ước lượng thiệt hại đã xác minh.", "",
              "## Kiểm tra phạm vi", "",
              "| Tháng | Nguồn công khai | Được so | Tỷ lệ đủ điều kiện | Trung vị thời gian theo dõi (ngày) | Tháng bị cắt trước khi kết thúc |",
              "| --- | ---: | ---: | ---: | ---: | --- |"]
    for r in frames["monthly_coverage.csv"].itertuples(index=False):
        lines.append(f"| {r.month} | {r.public_source_orders} | {r.compared_orders} | {r.coverage_fraction:.2%} | {r.followup_median_days:.2f} | {r.observation_cutoff_before_month_end} |")
    lines += ["", "Mẫu so sánh chỉ gồm đơn đủ dữ liệu. Thời gian theo dõi ngắn và việc chỉ giữ đơn đã hoàn tất có thể làm số đơn trễ tháng cuối thấp; không kết luận vận hành tháng đó đã tốt lên.",
              "Mẫu số coverage là 1.621 đơn public được bảo vệ của phiên bản nguồn, không phải toàn Olist và không cộng thêm pilot ngoài tập detector chung.", "",
              "## Kết luận và giới hạn", "",
              "- Đã chọn được cấu hình phù hợp nhất TRONG HAI ứng viên theo quy tắc F1 theo tháng đã nêu; lựa chọn có điều kiện trên validation đã xem.",
              "- Chỉ hai tháng có đơn trễ, tháng 4 chỉ có 17 đơn trễ. Chưa đủ để khẳng định ổn định lâu dài hay tối ưu trên mọi giai đoạn.",
              "- Cả hai ứng viên đều giảm F1 đáng kể từ tháng 3 sang tháng 4. Ít dao động hơn không có nghĩa đã ổn định; tỷ lệ lớp và phạm vi quan sát cũng thay đổi.",
              "- Mỗi ứng viên sử dụng lịch sử/bối cảnh khác nhau. Chênh lệch là giữa hai pipeline, không cô lập riêng thuật toán.",
              "- Chưa có kiểm định chênh lệch/khoảng tin cậy; chưa chấm tập test, chưa chứng minh near real-time hoặc nguyên nhân nghẽn.",
              "- Cơ sở ngày hẹn chưa xác minh; chỉ dùng mốc giao thực tế so ngày hẹn làm kết quả đối chiếu.",
              "- Bước kiểm chứng cuối phải giữ lựa chọn/ngưỡng cố định và dùng phạm vi thời gian theo dõi phù hợp; không sửa kết quả để đạt chỉ số mong muốn.", "",
              "## Tái lập", "",
              "Chạy select_temporal_delivery_candidate.py; --verify kiểm tra hash. Mỗi lần chạy dùng thư mục mới, không sửa snapshot trước.",
              "Bảng period_metrics.csv chứa cả gộp, tháng và nửa tháng; deletion_sensitivity.csv ghi từng lượt bỏ giai đoạn; selection.json ghi lựa chọn có điều kiện.", "",
              "- [Định nghĩa F1 và mẫu số bằng 0](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html)",
              "- [Phân biệt lựa chọn mô hình và kiểm chứng](https://scikit-learn.org/stable/modules/cross_validation.html)", ""]
    return "\n".join(lines)


def run(output=DEFAULT_OUTPUT):
    output = previous.product.pilot.audit.hierarchy.destination_guard(output, {"batch_directory": str(review.DEFAULT_BATCH)})
    if output.exists():
        raise FileExistsError("Refusing to overwrite temporal selection")
    inputs, source = load_inputs()
    frames, summary, selection = build_tables(inputs)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".temporal-selection-", dir=output.parent) as tmp:
        staging = Path(tmp)/"snapshot"
        staging.mkdir()
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False)
        for name, value in [("summary.json", summary), ("selection.json", selection), ("protocol.json", protocol())]:
            review.write_json(staging/name, value)
        (staging/"bao_cao_on_dinh.md").write_text(render_report(frames, summary, selection), encoding="utf-8")
        verify_sources(source)
        review.write_json(staging/"manifest.json", {
            "version": VERSION, "status": "complete", "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "provenance": source, "summary": summary, "selection": selection,
            "libraries": {"sklearn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
            "output_hashes": {p.name: review.digest(p) for p in sorted(staging.iterdir())}})
        if output.exists():
            raise FileExistsError("Destination appeared during publication")
        staging.rename(output)
    return summary


def load_snapshot(directory=DEFAULT_OUTPUT):
    directory = Path(directory)
    manifest = review.read_json(directory/"manifest.json")
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete"
            or set(manifest.get("output_hashes", {})) != TABLES | {"summary.json", "selection.json", "protocol.json", "bao_cao_on_dinh.md"}):
        raise ValueError("Invalid temporal selection snapshot")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    previous.load_snapshot()
    for name, expected in [("summary.json", manifest["summary"]), ("selection.json", manifest["selection"]), ("protocol.json", protocol())]:
        if review.read_json(directory/name) != expected:
            raise ValueError("Temporal selection metadata differs")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = load_snapshot(args.output)["summary"] if args.verify else run(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
