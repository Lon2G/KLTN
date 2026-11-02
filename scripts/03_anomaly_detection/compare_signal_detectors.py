"""Compare frozen timing detectors against declared signals, not invented truth."""

import argparse
import csv
from datetime import datetime, timezone
from io import StringIO
import json
from pathlib import Path
import tempfile

import numpy as np
import pandas as pd

import build_process_signals as signals
import compare_process_candidates as previous

ROOT, shared, review = signals.ROOT, signals.shared, signals.review
VERSION = "signal_detector_comparison_v1"
TEMPLATE = ROOT/"templates/signal_detector_comparison_v1"
BATCH = ROOT/"data/experiments/olist_validation_batches_comparison_input_v1"
DEFAULT_OUTPUT = ROOT/"data/experiments/olist_signal_detector_comparison_v1"
TABLES = {"comparison_scope.csv", "common_cases.csv", "common_scores.csv", "common_flags.csv", "candidate_definitions.csv",
          "candidate_comparison.csv", "focus_comparison.csv", "stage_overlap.csv", "group_comparison.csv",
          "family_summary.csv", "rule_tradeoff_frontier.csv", "focus_disagreement_cases.csv"}
FEATURES = previous.batch.FEATURES
FAMILIES = {"isolation_forest": "Isolation Forest", "lof": "LOF (novelty)", "one_class_svm": "One-Class SVM",
            "iqr": "IQR", "mad": "MAD", "upper_quantile": "Phân vị trên", "ensemble": "Ensemble thứ hạng"}


def protocol():
    return review.read_json(TEMPLATE/"protocol.json")


def code_hashes():
    return {str(p.relative_to(ROOT)): review.digest(p) for p in
            [Path(__file__), TEMPLATE/"protocol.json", TEMPLATE/"README.md", Path(previous.__file__)]}


def verify_sources(provenance):
    signal_dir, batch_dir = Path(provenance["signals_directory"]), Path(provenance["batch_directory"])
    if review.digest(signal_dir/"manifest.json") != provenance["signals_manifest_sha256"]:
        raise ValueError("Frozen signal source changed")
    sm = signals.load_snapshot(signal_dir)
    if review.digest(batch_dir/"manifest.json") != provenance["batch_manifest_sha256"]:
        raise ValueError("Frozen batch source changed")
    bm = previous.verify_artifact(batch_dir, "batch_version", previous.batch.VERSION,
                                  ["candidate_flags.csv", "candidate_scores.csv", "candidates.csv", "timing_features.csv", "case_results.csv"])
    previous.batch.verify_sources(bm["provenance"])
    if code_hashes() != provenance["code_hashes"]:
        raise ValueError("Comparison implementation changed; create a new version")
    return sm, bm


def read_selected(path, allowed, columns=None, **kwargs):
    # Filter CSV records by actual ID before numeric/date conversion.
    buffer = StringIO()
    with Path(path).open(newline="") as handle:
        reader = csv.DictReader(handle)
        fields = reader.fieldnames if columns is None else columns
        if "order_id" not in reader.fieldnames or not set(fields).issubset(reader.fieldnames):
            raise ValueError("Selected source schema differs")
        writer = csv.DictWriter(buffer, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in reader:
            if row["order_id"] in allowed:
                writer.writerow({name: row[name] for name in fields})
    buffer.seek(0)
    frame = pd.read_csv(buffer, keep_default_na=False, float_precision="round_trip", **kwargs)
    previous.require_ids(frame, str(path))
    return frame.sort_values("order_id").reset_index(drop=True)


def read_flags(path, allowed, candidates):
    frame = read_selected(path, allowed, dtype="string")
    if frame.columns.tolist() != ["order_id", *candidates.candidate]:
        raise ValueError("Saved candidate flags have a different schema")
    for name in candidates.candidate:
        if not frame[name].isin(["True", "False"]).all():
            raise ValueError("Unknown saved flag cannot become False")
        frame[name] = frame[name].eq("True")
    return frame


def load_inputs(signal_dir=signals.DEFAULT_OUTPUT, batch_dir=BATCH):
    if list(FEATURES) != protocol()["timing_input_features"]:
        raise ValueError("Timing feature contract changed")
    signal_dir, batch_dir = Path(signal_dir).resolve(), Path(batch_dir).resolve()
    provenance = {"signals_directory": str(signal_dir), "signals_manifest_sha256": review.digest(signal_dir/"manifest.json"),
                  "batch_directory": str(batch_dir), "batch_manifest_sha256": review.digest(batch_dir/"manifest.json"),
                  "code_hashes": code_hashes()}
    sm, bm = verify_sources(provenance)
    identity = bm["summary"]["identity"]
    expected = shared.business.IDENTITY
    if any(identity[key] != expected[key] for key in ["dataset_id", "marketplace_id", "primary_category"]):
        raise ValueError("Batch and signal dataset identities differ")
    config = review.read_json(batch_dir/"batch_config.json")
    start, end, cutoff = previous.batch.validate_config(config)
    if config["mode"] != "validation_replay" or bm["summary"]["mode"] != "validation_replay":
        raise ValueError("Expected historical validation replay, not test/deployment")
    orders = shared.read_table(signal_dir, "order_signals.csv", sm)
    stages = shared.read_table(signal_dir, "stage_signals.csv", sm)
    if orders.empty or not orders.split.eq("validation").all():
        raise ValueError("Guarded validation scope required")
    ids = pd.read_csv(batch_dir/"timing_features.csv", usecols=["order_id"], dtype="string", keep_default_na=False)
    previous.require_ids(ids, "saved score availability")
    scope = orders[["order_id", "performance_state", "assessment_coverage", "visible_before_exclusive"]].copy()
    scope["historical_score_id_present"] = scope.order_id.isin(ids.order_id)
    scope["complete_context_reference"] = orders.assessment_coverage.eq("complete") & orders.performance_eligible
    scope["included_common"] = scope.historical_score_id_present & scope.complete_context_reference
    scope["comparison_status"] = np.select([~scope.complete_context_reference, ~scope.historical_score_id_present],
        ["context_signal_assessment_incomplete", "saved_score_unavailable"], default="included_same_observations")
    allowed = set(scope.loc[scope.included_common, "order_id"])
    if not allowed:
        raise ValueError("No real common observations; do not create fake cases")
    common = orders.loc[orders.order_id.isin(allowed)].sort_values("order_id").reset_index(drop=True)
    candidates = pd.read_csv(batch_dir/"candidates.csv", float_precision="round_trip")
    if candidates.columns.tolist() != previous.CANDIDATE_COLUMNS or not candidates.candidate.is_unique or candidates.empty:
        raise ValueError("Invalid candidate definitions")
    model_dir = Path(bm["provenance"]["model_directory"])
    pd.testing.assert_frame_equal(candidates, pd.read_csv(model_dir/"candidates.csv", float_precision="round_trip"), check_exact=True)
    scores = read_selected(batch_dir/"candidate_scores.csv", allowed, dtype={"order_id": "string"})
    flags = read_flags(batch_dir/"candidate_flags.csv", allowed, candidates)
    features = read_selected(batch_dir/"timing_features.csv", allowed, dtype={"order_id": "string"})
    fields = ["order_id", "split", "order_status", "event_time_cutoff_exclusive", *signals.performance.TIMES]
    saved_cases = read_selected(batch_dir/"case_results.csv", allowed, columns=fields, dtype="string")
    for frame in [scores, flags, features, saved_cases]:
        if set(frame.order_id) != allowed:
            raise ValueError("Every candidate must cover the same common cohort")
    for field in ["event_time_cutoff_exclusive", *signals.performance.TIMES]:
        saved_cases[field] = pd.to_datetime(saved_cases[field], format="%Y-%m-%d %H:%M:%S", errors="raise")
    if not saved_cases.split.eq("validation").all() or not saved_cases.order_status.eq("delivered").all():
        raise ValueError("Saved score case eligibility differs")
    if (not saved_cases.event_time_cutoff_exclusive.eq(cutoff).all()
            or not common.order_purchase_timestamp.ge(start).all() or not common.order_purchase_timestamp.lt(end).all()
            or not common.visible_before_exclusive.le(cutoff).all()):
        raise ValueError("Saved/current observation windows differ unexpectedly")
    for field in signals.performance.TIMES:
        pd.testing.assert_series_equal(saved_cases[field], common[field], check_dtype=False, check_names=False, check_exact=True)
        if not common[field].lt(common.visible_before_exclusive).all():
            raise ValueError("Score uses an event unavailable under the guarded cutoff")
    for feature, (left, right, _) in zip(FEATURES, signals.performance.STAGES[:3]):
        observed = (common[right]-common[left]).dt.total_seconds()/86400
        np.testing.assert_allclose(features[feature], observed, atol=1e-12, rtol=0)
    regenerated = previous.batch.training.engine.apply_thresholds(scores, candidates)
    pd.testing.assert_frame_equal(flags, regenerated, check_dtype=False, check_exact=True)
    for name, frame in [("validation_scores.csv", scores), ("validation_flags.csv", flags)]:
        original = (read_flags(model_dir/name, allowed, candidates) if "flags" in name
                    else read_selected(model_dir/name, allowed, dtype={"order_id": "string"}))
        pd.testing.assert_frame_equal(frame, original, check_dtype=False, check_exact=True)
    if not np.isfinite(scores.drop(columns="order_id").to_numpy(dtype=float)).all():
        raise ValueError("Scores must be finite")
    verify_sources(provenance)
    return {"scope": scope, "cases": common, "stages": stages.loc[stages.order_id.isin(allowed)].reset_index(drop=True),
            "candidates": candidates, "scores": scores, "flags": flags}, provenance


def intersections(flag, reference):
    flag, reference = np.asarray(flag, dtype=bool), np.asarray(reference, dtype=bool)
    both = int((flag & reference).sum())
    candidate_only, reference_only = int((flag & ~reference).sum()), int((~flag & reference).sum())
    union = both+candidate_only+reference_only
    return {"both": both, "candidate_only": candidate_only, "reference_only": reference_only,
            "neither": len(flag)-union, "jaccard": both/union if union else np.nan}


def diagnostic_frontier(table):
    covered = table.both_context_long.to_numpy()
    extra = table.candidate_without_context_long.to_numpy()
    dominated = ((covered[:, None] >= covered[None, :]) & (extra[:, None] <= extra[None, :])
                 & ((covered[:, None] > covered[None, :]) | (extra[:, None] < extra[None, :])))
    return ~dominated.any(axis=0)


def build_tables(inputs):
    cases, flags = inputs["cases"], inputs["flags"]
    if not cases.order_id.tolist() == flags.order_id.tolist() or not cases.assessment_coverage.eq("complete").all():
        raise ValueError("Expected identical fully assessed order alignment")
    for field in ["long_stage_warning", "short_stage_note", "calendar_late_signal"]:
        if cases[field].isna().any():
            raise ValueError("Reference unavailable; do not convert to a negative")
    candidates = inputs["candidates"].sort_values("candidate").reset_index(drop=True).copy()
    candidates["focus_parameter_slice"] = (candidates.tail_fraction.eq(protocol()["focus_calibration_tail"])
                                            | candidates.candidate.isin(protocol()["focus_fixed_candidates"]))
    if not set(protocol()["focus_fixed_candidates"]).issubset(candidates.candidate):
        raise ValueError("Missing declared focus baselines")
    long = cases.long_stage_warning.to_numpy(dtype=bool)
    short = cases.short_stage_note.to_numpy(dtype=bool)
    late = cases.calendar_late_signal.to_numpy(dtype=bool)
    primary = long | late
    stage_flags = inputs["stages"].pivot(index="order_id", columns="stage", values="long_stage_warning").reindex(cases.order_id)
    if stage_flags.isna().any().any() or set(stage_flags) != set(signals.STAGES):
        raise ValueError("Every common case needs all reference stages")
    rows, stage_rows, group_rows, disagreement_parts = [], [], [], []
    for candidate in candidates.itertuples(index=False):
        flag = flags[candidate.candidate].to_numpy(dtype=bool)
        overlap, policy = intersections(flag, long), intersections(flag, primary)
        rows.append({"candidate": candidate.candidate, "family": candidate.family, "score_column": candidate.score_column,
                     "focus_parameter_slice": candidate.focus_parameter_slice, "compared_orders": len(cases),
                     "candidate_flags": int(flag.sum()), "candidate_rate": float(flag.mean()),
                     "context_long_orders": int(long.sum()), "both_context_long": overlap["both"],
                     "candidate_without_context_long": overlap["candidate_only"],
                     "context_long_without_candidate": overlap["reference_only"], "neither_context_long": overlap["neither"],
                     "context_long_jaccard": overlap["jaccard"], "calendar_late_orders": int(late.sum()),
                     "candidate_and_calendar_late": int((flag & late).sum()), "primary_signal_orders": int(primary.sum()),
                     "candidate_and_primary_signal": policy["both"], "additional_flags_beyond_primary": policy["candidate_only"],
                     "primary_signal_without_candidate": policy["reference_only"],
                     "additional_flags_with_short_note": int((flag & ~primary & short).sum()),
                     "hypothetical_primary_union_orders": int((primary | flag).sum()),
                     "interpretation": "rule_cooccurrence_not_independent_accuracy"})
        if candidate.focus_parameter_slice:
            different = flag != long
            piece = cases.loc[different, ["order_id", "performance_state", "distance_band", "long_stage_warning",
                                         "short_stage_note", "calendar_late_signal", "long_stage_warning_stages_json"]].copy()
            piece.insert(0, "candidate", candidate.candidate)
            piece["candidate_flag"] = flag[different]
            piece["relation"] = np.where(flag[different], "candidate_only_not_verified_false_positive",
                                         "context_long_only_not_verified_false_negative")
            piece["score_column"] = candidate.score_column
            piece["score"] = inputs["scores"].loc[different, candidate.score_column].to_numpy()
            piece["threshold"] = candidate.threshold
            piece["score_operator"] = "strict_greater_than"
            piece["independent_reference_label"] = pd.Series(pd.NA, index=piece.index, dtype="string")
            piece["view_status"] = "model_exposed_analyst_evidence_not_blind_review"
            disagreement_parts.append(piece)
        for stage in signals.STAGES:
            ref = stage_flags[stage].to_numpy(dtype=bool)
            values = intersections(flag, ref)
            stage_rows.append({"candidate": candidate.candidate, "stage": stage, "compared_orders": len(cases),
                               "reference_stage_long_orders": int(ref.sum()), **values,
                               "interpretation": "cooccurrence_not_ML_attribution_or_causal_localization"})
        for dimension in protocol()["group_slices"]:
            values = cases.order_purchase_timestamp.dt.strftime("%Y-%m") if dimension == "purchase_month" else cases[dimension]
            for group in sorted(values.unique()):
                mask = values.eq(group).to_numpy(dtype=bool)
                overlap = intersections(flag[mask], long[mask])
                group_rows.append({"candidate": candidate.candidate, "dimension": dimension, "group": group,
                                   "compared_orders": int(mask.sum()), "candidate_flags": int(flag[mask].sum()),
                                   "candidate_rate": float(flag[mask].mean()), "context_long_orders": int(long[mask].sum()), **overlap})
    comparison = pd.DataFrame(rows)
    comparison["rule_tradeoff_nondominated"] = diagnostic_frontier(comparison)
    family = comparison.groupby("family", sort=True).agg(
        configurations=("candidate", "size"), score_methods=("score_column", "nunique"),
        min_flags=("candidate_flags", "min"), max_flags=("candidate_flags", "max"),
        min_both_context_long=("both_context_long", "min"), max_both_context_long=("both_context_long", "max"),
        frontier_configurations=("rule_tradeoff_nondominated", "sum")).reset_index()
    frames = {"comparison_scope.csv": inputs["scope"], "common_cases.csv": cases,
              "common_scores.csv": inputs["scores"], "common_flags.csv": flags, "candidate_definitions.csv": candidates,
              "candidate_comparison.csv": comparison, "focus_comparison.csv": comparison.loc[comparison.focus_parameter_slice].reset_index(drop=True),
              "stage_overlap.csv": pd.DataFrame(stage_rows), "group_comparison.csv": pd.DataFrame(group_rows), "family_summary.csv": family,
              "rule_tradeoff_frontier.csv": comparison.loc[comparison.rule_tradeoff_nondominated].reset_index(drop=True),
              "focus_disagreement_cases.csv": pd.concat(disagreement_parts, ignore_index=True)}
    summary = {"version": VERSION, "guarded_validation_orders": len(inputs["scope"]), "common_compared_orders": len(cases),
               "excluded_orders": int((~inputs["scope"].included_common).sum()), "candidate_configurations": len(candidates),
               "score_methods": int(candidates.score_column.nunique()), "families": int(candidates.family.nunique()),
               "focus_configurations": int(candidates.focus_parameter_slice.sum()),
               "focus_disagreement_pairs": len(frames["focus_disagreement_cases.csv"]),
               "focus_disagreement_unique_orders": int(frames["focus_disagreement_cases.csv"].order_id.nunique()),
               "rule_frontier_configurations": int(comparison.rule_tradeoff_nondominated.sum()),
               "context_long_orders": int(long.sum()), "calendar_late_orders": int(late.sum()), "primary_signal_orders": int(primary.sum()),
               "candidate_flags_min": int(comparison.candidate_flags.min()), "candidate_flags_max": int(comparison.candidate_flags.max()),
               "scope_exclusion_counts": {str(k): int(v) for k, v in inputs["scope"].comparison_status.value_counts().items()},
               "selected_ml_detector": None, "root_cause_required": False, "model_loaded": False, "training_performed": False,
               "thresholds_changed": False, "reference_labels_created": 0, "independent_accuracy_computed": False,
               "test_scored": False, "near_real_time_validated": False, "primary_policy_changed": False}
    return frames, summary


def render_report(frames, summary):
    lines = ["# So sánh detector theo dấu hiệu quy trình", "",
             f'Đã đối chiếu **{summary["candidate_configurations"]} cấu hình**, {summary["score_methods"]} cách tính điểm, '
             f'{summary["families"]} nhóm phương pháp trên cùng **{summary["common_compared_orders"]} đơn thật**. '
             "Không train lại, không thay ngưỡng, không tạo nhãn và không yêu cầu biết nguyên nhân giao chậm.", "",
             "Phần này so detector dùng ba thời lượng: phê duyệt, chuẩn bị/bàn giao, vận chuyển. Không gộp các "
             "profile thanh toán/đơn hàng hoặc mô hình hồi quy phân vị theo bối cảnh vào cùng bảng xếp so sánh vì khác đầu vào/mục tiêu.", "",
             "## Phạm vi", "",
             f'Từ {summary["guarded_validation_orders"]} đơn validation được bảo vệ, có {summary["excluded_orders"]} đơn chưa đủ '
             "điều kiện đối chiếu chung. Giữ riêng các đơn này, không coi là không cảnh báo. Đã kiểm tra mốc thời gian và "
             "ba thời lượng đầu vào trùng với kết quả đã lưu; không đưa kết quả sau mốc quan sát vào so sánh.", "",
             f'Trong tập chung: {summary["context_long_orders"]} đơn có công đoạn dài, {summary["calendar_late_orders"]} đơn trễ hẹn, '
             f'{summary["primary_signal_orders"]} đơn có ít nhất một trong hai dấu hiệu. '
             "Các số này có thể khác báo cáo toàn bộ đơn vì mẫu số khác.", "",
             "## Các nhóm phương pháp", "", "| Nhóm | Cấu hình | Số đơn cảnh báo thấp nhất | Cao nhất |", "| --- | ---: | ---: | ---: |"]
    for row in frames["family_summary.csv"].itertuples(index=False):
        lines.append(f"| {FAMILIES[row.family]} | {row.configurations} | {row.min_flags} | {row.max_flags} |")
    lines += ["", "Không dùng ít cảnh báo nhất để kết luận tốt nhất. Các cấu hình không phải các mô hình độc lập để bỏ phiếu xác suất.", "",
              "## Mặt cắt tham số để đọc kết quả", "",
              "Giữ tất cả cách tính điểm có tỷ lệ đuôi hiệu chuẩn 5%, cùng năm baseline đã khai báo. "
              "Đây không phải tỷ lệ bất thường thực, xác suất hoặc nhóm thắng cuộc. IQR/MAD/phân vị có ngưỡng riêng, "
              "không mặc định có cùng khối lượng cảnh báo với các mô hình ML.", "",
              "| Cấu hình | Cảnh báo | Trùng dấu hiệu dài | Cảnh báo ngoài dấu hiệu dài | Dấu hiệu dài không được mô hình cảnh báo |", "| --- | ---: | ---: | ---: | ---: |"]
    for row in frames["focus_comparison.csv"].itertuples(index=False):
        lines.append(f"| `{row.candidate}` | {row.candidate_flags} | {row.both_context_long} | {row.candidate_without_context_long} | {row.context_long_without_candidate} |")
    lines += ["", "**Không gọi hai cột bất đồng là báo sai hoặc bỏ sót bất thường thực.** Quy tắc tham chiếu và detector "
              "đều dùng thời lượng, chưa phải nhãn độc lập. Cảnh báo ngoài quy tắc có thể liên quan thời gian ngắn, "
              "tổ hợp thời lượng khác thường hoặc cảnh báo không hữu ích; bước này chưa phân xử.", "",
              "## Các đánh đổi so với quy tắc", "",
              f'Có {summary["rule_frontier_configurations"]} cấu hình không bị cấu hình khác lấn át: đối thủ phải không kém ở cả '
              "hai mặt (nhiều dấu hiệu dài trùng hơn, ít cảnh báo ngoài dấu hiệu dài hơn) và tốt hơn ở ít nhất một mặt. "
              "Chỉ dùng hai số đếm, không cộng trọng số tùy ý. "
              "Đây là nhóm đối chiếu tiếp theo theo quy tắc, **không phải mô hình chính xác nhất**; giữ nguyên mọi ứng viên và cả trường hợp hòa.", "",
              "| Cấu hình | Trùng dấu hiệu dài | Ngoài dấu hiệu dài |", "| --- | ---: | ---: |"]
    for row in frames["rule_tradeoff_frontier.csv"].itertuples(index=False):
        lines.append(f"| `{row.candidate}` | {row.both_context_long} | {row.candidate_without_context_long} |")
    lines += ["", "## Dùng bằng chứng đúng phạm vi", "",
              f'- `focus_disagreement_cases.csv` có {summary["focus_disagreement_pairs"]} cặp cấu hình/đơn bất đồng, '
              f'thuộc {summary["focus_disagreement_unique_orders"]} đơn khác nhau trong mặt cắt 18 cấu hình. '
              "Có điểm số, ngưỡng và công đoạn tham chiếu; chưa có nhãn độc lập. Không coi các cặp là các đơn riêng hoặc gọi đây là rà soát mù.",
              "- `stage_overlap.csv` cho biết cảnh báo mô hình cùng xuất hiện với công đoạn dài nào. Không suy ra công đoạn đó gây điểm ML hoặc gây trễ hẹn.",
              "- `group_comparison.csv` giữ số đơn từng tháng và nhóm khoảng cách. Khác biệt tỷ lệ có thể do cơ cấu mẫu và thời gian theo dõi, không tự chứng minh mô hình suy giảm.",
              "- `additional_flags_beyond_primary` là số đơn phải xem thêm nếu ghép cảnh báo ML vào chính sách trễ hẹn hoặc công đoạn dài. Chưa kích hoạt cách ghép này.",
              "- Cảnh báo quá ngắn và giao sớm không bị tự chuyển thành nhãn bất thường. Thiếu dữ liệu không bị chuyển thành nhãn bình thường.", "",
              "## Bước tiếp theo", "",
              "Dùng các bất đồng để kiểm tra tính hữu ích của dấu hiệu theo tiêu chí đã thống nhất, không điều tra nguyên nhân gốc. "
              "Có thể chọn cấu hình vận hành thử theo mức đánh đổi được chấp nhận, nhưng chưa dùng kết quả khớp quy tắc này để công bố "
              "precision/recall/F1 độc lập hoặc mô hình tối ưu. Ngưỡng hiện tại và chính sách cảnh báo chính vẫn được giữ nguyên; "
              "test cuối chưa chấm, chưa có kết quả near real-time.", ""]
    return "\n".join(lines)


def write_snapshot(frames, summary, provenance, output=DEFAULT_OUTPUT):
    sm, _ = verify_sources(provenance)
    output = signals.performance.destination_guard(output, {"inspection": sm["provenance"]["performance_source"]["inspection"]})
    if output.exists():
        raise FileExistsError("Refusing to overwrite signal detector comparison")
    if set(frames) != TABLES:
        raise ValueError("Incomplete comparison tables")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".signal-detector-comparison-", dir=output.parent) as temp:
        staging = Path(temp)/"snapshot"
        staging.mkdir()
        schemas = {}
        for name, frame in frames.items():
            frame.to_csv(staging/name, index=False, date_format="%Y-%m-%d %H:%M:%S")
            schemas[name] = {c: str(dtype) for c, dtype in frame.dtypes.items()}
            pd.testing.assert_frame_equal(shared.read_table(staging, name, {"schemas": schemas}), frame, check_dtype=False, check_exact=True)
        review.write_json(staging/"summary.json", summary)
        review.write_json(staging/"protocol.json", protocol())
        (staging/"README.md").write_text((TEMPLATE/"README.md").read_text())
        (staging/"bao_cao_so_sanh.md").write_text(render_report(frames, summary))
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
    if (manifest.get("version") != VERSION or manifest.get("status") != "complete" or set(manifest.get("schemas", {})) != TABLES
            or set(manifest.get("output_hashes", {})) != TABLES | {"README.md", "protocol.json", "summary.json", "bao_cao_so_sanh.md"}):
        raise ValueError("Invalid detector comparison manifest")
    shared.verify_files(directory, manifest["output_hashes"])
    verify_sources(manifest["provenance"])
    if review.read_json(directory/"protocol.json") != protocol() or review.read_json(directory/"summary.json") != manifest["summary"]:
        raise ValueError("Detector comparison protocol/summary differs")
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
            inputs, provenance = load_inputs()
            frames, summary = build_tables(inputs)
            result = write_snapshot(frames, summary, provenance, args.output)
    except (ValueError, OSError, KeyError, AssertionError) as exc:
        parser.exit(2, f"Signal detector comparison stopped: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
