"""Explain real development-cohort cases using frozen rules and benchmark signals."""

import argparse
from datetime import datetime, timezone
from itertools import combinations
import json
from pathlib import Path
import tempfile

import joblib
import numpy as np
import pandas as pd

import benchmark_bed_bath_table as benchmark
from evaluate_bed_bath_table_benchmark import verify_benchmark


previous = benchmark.previous
audit = previous.bed.audit
TIMES = audit.TIMES
FEATURES = benchmark.FEATURES
DEFAULT_OUTPUT = previous.EXPERIMENT_DATA_DIR / "bed_bath_table_auto_validation_v1"
DEFAULT_REFERENCE = previous.MANUAL_REVIEW_DIR / "final_candidate_manual_validation_labeled.csv"
ML_FAMILIES = ["isolation_forest", "lof", "one_class_svm", "ensemble"]
RULES = {
    "STATUS_DELIVERY_CONFLICT": ("source_record_issue", "Recorded delivered status and delivery timestamp presence disagree."),
    "RECORDED_TIMELINE_REVERSAL": ("source_record_issue", "A later expected milestone has an earlier recorded timestamp; check every observed pair."),
    "DELIVERED_MISSING_MILESTONE": ("source_record_issue", "An order recorded as delivered lacks an actual milestone; never reconstruct it."),
    "INCOMPLETE_CLOSED_STATUS": ("context", "Missing milestones with canceled/unavailable status do not establish a process anomaly."),
    "NONFINAL_CUTOFF_UNKNOWN": ("context", "Nonfinal status and unknown extraction cutoff prevent an ongoing-age conclusion."),
    "TIMING_OUTSIDE_PARTITION_CUTOFF": ("context", "Completed snapshot events cross the partition cutoff; excluded from timing comparisons."),
    "LONG_DURATION": ("warning", "An eligible transition exceeds raw fit Q75 + 1.5*IQR."),
    "SEVERE_LONG_DURATION": ("warning", "An eligible transition exceeds raw fit Q75 + 3*IQR; still not a verified business anomaly."),
    "SHORT_DURATION_WARNING": ("warning", "A positive eligible transition is below fit Q01; warning only."),
    "ZERO_DURATION_WARNING": ("warning", "An eligible transition is exactly zero; tied timestamps do not prove invalid execution."),
    "AFTER_ESTIMATED_DATE_WARNING": ("warning", "Eligible recorded delivery is on a later calendar date than the estimate; not an anomaly ground-truth label."),
    "MODEL_CANDIDATE_SIGNAL": ("warning", "At least one exploratory ML/ensemble configuration flagged the case; no winning configuration is implied."),
}
TIMING_FLAGS = ["long_duration_warning", "severe_long_duration_warning", "short_duration_warning",
                "zero_duration_warning", "late_delivery_warning"]


def protocol():
    return {
        "version": "bed_bath_table_auto_validation_v1",
        "category": previous.bed.CATEGORY,
        "scope": "Train and validation only. Record/status checks are retrospective snapshot checks, not historical online decisions.",
        "timing_scope": "Only original partition timing-input-eligible cases; do not use post-cutoff or invalid durations.",
        "thresholds": {"reference": "Frozen benchmark inner fit only", "long": "raw Q75 + 1.5*IQR",
                       "severe_long": "raw Q75 + 3*IQR", "short_positive": "0 < duration < raw Q01", "zero": "duration == 0"},
        "threshold_status": "Explicit exploratory explanation policy, not an accuracy-optimized or selected winning formula.",
        "model_scope": "Reuse frozen calibration/validation scores and all 120 sensitivity configurations. Never novelty-score fitted rows.",
        "model_screening": "OR across ML/ensemble candidates is an exploratory inspection signal, not majority voting or an optimized ensemble decision.",
        "automatic_action_precedence": ["verify_source_record", "inspect_timing_warning", "inspect_model_signal",
                                        "insufficient_context", "no_signal_in_available_checks"],
        "manual_policy": "No new review prerequisite. Existing human labels are a separate, biased reference; never generated or overwritten.",
        "frequency_scope": "Monthly counts and warning rates only. The source has one timestamp per stage and cannot reveal repeated activity executions.",
        "limits": ["No real-world accuracy, calibrated anomaly probability, automatic business Anomaly/Normal label, or winning model is produced.",
                   "No signal does not mean confirmed Normal. Unavailable checks remain unavailable, not negative findings.",
                   "The final test is reserved. Historical category exploration already saw aggregate outcomes; it is not an entirely untouched dataset.",
                   "Ongoing age cannot be computed from today's date or maximum observed event date.",
                   "No unchanged model/threshold transfer to another category or marketplace."],
        "rules": {name: {"kind": kind, "definition": definition} for name, (kind, definition) in RULES.items()},
    }


def input_hashes(benchmark_dir, reference, source):
    return {"benchmark_manifest": audit.file_hash(Path(benchmark_dir) / "manifest.json"),
            "experiment_manifest": audit.file_hash(Path(source) / "manifest.json"),
            "human_reference": audit.file_hash(reference)}


def load_inputs(benchmark_dir=benchmark.DEFAULT_OUTPUT, reference=DEFAULT_REFERENCE):
    benchmark_dir, reference = Path(benchmark_dir), Path(reference)
    manifest = verify_benchmark(benchmark_dir)
    source = Path(manifest["source"])
    before = input_hashes(benchmark_dir, reference, source)
    matrices, context, parents = previous.load_inputs(source)
    if manifest["parents"] != parents:
        raise ValueError("Benchmark and experiment provenance differ")
    subsets, ledger = benchmark.inner_split(matrices, context)
    bank = joblib.load(benchmark_dir / "model_bank.joblib")
    if (bank["features"] != FEATURES or bank["fit_ids"] != subsets["fit"].order_id.tolist()
            or bank["calibration_ids"] != subsets["calibration"].order_id.tolist()):
        raise ValueError("Saved model scope differs from the frozen experiment")
    candidates = pd.read_csv(benchmark_dir / "candidates.csv", float_precision="round_trip")
    pd.testing.assert_frame_equal(candidates, bank["candidates"], check_dtype=False)
    flags_by_split = {}
    for name in ["calibration", "validation"]:
        scores = pd.read_csv(benchmark_dir / f"{name}_scores.csv", dtype={"order_id": "string"}, float_precision="round_trip")
        flags = pd.read_csv(benchmark_dir / f"{name}_flags.csv", dtype={"order_id": "string", **{c: "bool" for c in candidates.candidate}})
        if set(scores.order_id) != set(subsets[name].order_id):
            raise ValueError(f"Unexpected {name} scoring population")
        pd.testing.assert_frame_equal(flags, benchmark.apply_thresholds(scores, candidates), check_dtype=False)
        flags_by_split[name] = flags
    thresholds = build_thresholds(bank)
    old = pd.read_csv(reference, dtype="string", keep_default_na=False)
    if before != input_hashes(benchmark_dir, reference, source):
        raise ValueError("Inputs changed during Auto Validation preparation")
    return context, ledger, thresholds, candidates, flags_by_split, old, before, source


def build_thresholds(bank):
    stats = bank["statistics"]["raw"]
    rows = []
    for i, feature in enumerate(FEATURES):
        rows.append({"feature": feature, "reference_orders": len(bank["fit_ids"]),
                     "q01_days": float(bank["short_q01"][i]), "q25_days": float(stats["q25"][i]),
                     "q75_days": float(stats["q75"][i]), "iqr_days": float(stats["iqr"][i]),
                     "long_threshold_days": float(stats["q75"][i] + 1.5 * stats["iqr"][i]),
                     "severe_long_threshold_days": float(stats["q75"][i] + 3 * stats["iqr"][i])})
    return pd.DataFrame(rows)


def validate_context(context):
    if context.empty or context.order_id.isna().any() or not context.order_id.is_unique:
        raise ValueError("Expected unique nonmissing development order IDs")
    if not context.split.isin(["train", "validation"]).all():
        raise ValueError("Auto Validation accepts development splits only; final test is reserved")
    if not context.order_status.isin(previous.bed.usage.clean.STATUSES).all():
        raise ValueError("Unexpected order status")
    for feature, (start, end) in zip([*FEATURES, "total_cycle_time_days"], [(0, 1), (1, 2), (2, 3), (0, 3)]):
        expected = (context[TIMES[end]] - context[TIMES[start]]).dt.total_seconds() / 86400
        if not np.allclose(context[feature], expected, rtol=1e-12, atol=1e-12, equal_nan=True):
            raise ValueError(f"Observed duration mismatch: {feature}")
    eligible = context.loc[context.timing_input_eligible]
    if (not eligible.completed_timing_eligible.all()
            or not eligible[TIMES].lt(eligible.event_time_cutoff_exclusive, axis=0).all().all()):
        raise ValueError("Ineligible or post-cutoff timing input")


def validate_cases(context, ledger, thresholds, candidates, flags_by_split):
    validate_context(context)
    if list(thresholds.feature) != FEATURES or not candidates.candidate.is_unique:
        raise ValueError("Threshold feature or candidate schema mismatch")
    if not np.isfinite(thresholds.drop(columns="feature").to_numpy(dtype=float)).all():
        raise ValueError("Nonfinite timing threshold")
    if not thresholds.severe_long_threshold_days.ge(thresholds.long_threshold_days).all():
        raise ValueError("Severe threshold must not be below long threshold")
    threshold_map = thresholds.set_index("feature").to_dict("index")
    flags = pd.concat(list(flags_by_split.values()), ignore_index=True)
    if flags.order_id.isna().any() or not flags.order_id.is_unique or not set(flags.order_id).issubset(context.order_id):
        raise ValueError("Detector flags must be unique development cases")
    if list(flags.columns) != ["order_id", *candidates.candidate] or not flags[candidates.candidate].isin([True, False]).all().all():
        raise ValueError("Invalid detector flag schema or value")
    inner_roles = ledger.set_index("order_id").inner_role.to_dict()
    if set(flags.order_id) & set(ledger.loc[ledger.inner_role.eq("fit"), "order_id"]):
        raise ValueError("Fit rows must not have novelty model signals")
    indexed_flags = flags.set_index("order_id")
    ml_candidates = candidates.loc[candidates.family.isin(ML_FAMILIES), "candidate"].tolist()
    families = {family: group.candidate.tolist() for family, group in candidates.groupby("family")}
    output, evidence = [], []
    for source in context.sort_values("order_id").to_dict("records"):
        row = dict(source)
        case_evidence = []

        def add(code, feature="", observed=None, threshold=None, details=None):
            item = {"order_id": row["order_id"], "split": row["split"], "reason_code": code,
                    "kind": RULES[code][0], "feature": feature, "observed_days": observed,
                    "threshold_days": threshold, "details_json": json.dumps(details or {}, allow_nan=False)}
            evidence.append(item)
            case_evidence.append(item)

        observed = [pd.notna(row[name]) for name in TIMES]
        missing = [name for name, present in zip(TIMES, observed) if not present]
        delivered = row["order_status"] == "delivered"
        if delivered != observed[-1]:
            add("STATUS_DELIVERY_CONFLICT", details={"order_status": row["order_status"], "delivery_timestamp_present": observed[-1]})
        for i, j in combinations(range(len(TIMES)), 2):
            if observed[i] and observed[j] and row[TIMES[j]] < row[TIMES[i]]:
                delta = (row[TIMES[j]] - row[TIMES[i]]).total_seconds() / 86400
                add("RECORDED_TIMELINE_REVERSAL", feature=f"{TIMES[i]} -> {TIMES[j]}", observed=delta, threshold=0,
                    details={"start": str(row[TIMES[i]]), "end": str(row[TIMES[j]])})
        if delivered and missing:
            add("DELIVERED_MISSING_MILESTONE", details={"missing_timestamp_fields": missing})
        elif missing:
            code = "INCOMPLETE_CLOSED_STATUS" if row["order_status"] in ["canceled", "unavailable"] else "NONFINAL_CUTOFF_UNKNOWN"
            add(code, details={"order_status": row["order_status"], "missing_timestamp_fields": missing, "extraction_cutoff": None})
        if row["timing_input_reason"] == "completion_not_before_partition_cutoff":
            add("TIMING_OUTSIDE_PARTITION_CUTOFF", details={"cutoff_exclusive": str(row["event_time_cutoff_exclusive"])})

        row["timing_rules_available"] = bool(row["timing_input_eligible"])
        row.update({name: None for name in TIMING_FLAGS})
        row["late_calendar_days"] = None
        if row["timing_rules_available"]:
            row.update({name: False for name in TIMING_FLAGS})
            for feature in FEATURES:
                value, limits = row[feature], threshold_map[feature]
                if value > limits["long_threshold_days"]:
                    row["long_duration_warning"] = True
                    add("LONG_DURATION", feature, value, limits["long_threshold_days"])
                if value > limits["severe_long_threshold_days"]:
                    row["severe_long_duration_warning"] = True
                    add("SEVERE_LONG_DURATION", feature, value, limits["severe_long_threshold_days"])
                if value == 0:
                    row["short_duration_warning"] = row["zero_duration_warning"] = True
                    add("ZERO_DURATION_WARNING", feature, value, 0)
                elif 0 < value < limits["q01_days"]:
                    row["short_duration_warning"] = True
                    add("SHORT_DURATION_WARNING", feature, value, limits["q01_days"])
            if pd.notna(row["order_estimated_delivery_date"]):
                days = (row["order_delivered_customer_date"].normalize() - row["order_estimated_delivery_date"].normalize()).days
                row["late_calendar_days"] = days
                row["late_delivery_warning"] = days > 0
                if days > 0:
                    add("AFTER_ESTIMATED_DATE_WARNING", "delivery_vs_estimate_calendar_days", days, 0)
            else:
                row["late_delivery_warning"] = None

        role = inner_roles.get(row["order_id"], "validation" if row["split"] == "validation" and row["timing_rules_available"] else "timing_ineligible")
        row["benchmark_role"] = role
        row["model_signals_available"] = row["order_id"] in indexed_flags.index
        row["model_candidate_signal"] = None
        row["positive_candidate_ids_json"] = "[]"
        row["candidate_support_by_family_json"] = "{}"
        if row["model_signals_available"]:
            case_flags = indexed_flags.loc[row["order_id"]]
            positive = case_flags.index[case_flags].tolist()
            row["positive_candidate_ids_json"] = json.dumps(positive)
            row["candidate_support_by_family_json"] = json.dumps({name: {"flagged": int(case_flags[names].sum()), "evaluated": len(names)} for name, names in families.items()})
            row["model_candidate_signal"] = bool(case_flags[ml_candidates].any())
            if row["model_candidate_signal"]:
                add("MODEL_CANDIDATE_SIGNAL", details={"positive_ml_candidate_ids": [name for name in positive if name in ml_candidates],
                                                      "score_and_threshold_source": "frozen benchmark scores.csv and candidates.csv"})
        codes = list(dict.fromkeys(item["reason_code"] for item in case_evidence))
        row["reason_codes_json"] = json.dumps(codes)
        row["source_record_issue"] = any(RULES[code][0] == "source_record_issue" for code in codes)
        row["has_timing_warning"] = any(row[name] is True for name in TIMING_FLAGS)
        row["has_warning_or_record_issue"] = row["source_record_issue"] or row["has_timing_warning"] or row["model_candidate_signal"] is True
        row["auto_action"] = ("verify_source_record" if row["source_record_issue"] else
                              "inspect_timing_warning" if row["has_timing_warning"] else
                              "inspect_model_signal" if row["model_candidate_signal"] is True else
                              "insufficient_context" if not row["timing_rules_available"] else "no_signal_in_available_checks")
        output.append(row)
    cases = pd.DataFrame(output)
    for name in [*TIMING_FLAGS, "model_candidate_signal"]:
        cases[name] = cases[name].astype("boolean")
    return cases, pd.DataFrame(evidence, columns=["order_id", "split", "reason_code", "kind", "feature",
                                                "observed_days", "threshold_days", "details_json"])


def compare_human_reference(old, cases):
    required = ["case_id", "reviewer_label", "reviewer_confidence", "reviewer_reason", "reviewer_notes"]
    if not set(required).issubset(old.columns) or old.case_id.isna().any() or old.case_id.eq("").any():
        raise ValueError("Missing human-reference fields or IDs")
    if not old.reviewer_label.isin(["Anomaly", "Suspicious", "Normal"]).all():
        raise ValueError("Reference must contain existing reviewed labels only")
    if not old.groupby("case_id").reviewer_label.nunique().eq(1).all():
        raise ValueError("Conflicting duplicate human labels; do not resolve automatically")
    rows = []
    for case_id, group in old.groupby("case_id", sort=True):
        rows.append({"order_id": case_id, "reference_row_count": len(group), "human_reference_label": group.reviewer_label.iloc[0],
                     **{f"human_{name}_values_json": json.dumps(sorted(group[name].unique().tolist()))
                        for name in required[2:]}})
    reference = pd.DataFrame(rows)
    reference["in_development_scope"] = reference.order_id.isin(cases.order_id)
    reference = reference.merge(cases[["order_id", "split", "usage_group", "timing_rules_available", "model_signals_available",
                                       "auto_action", "reason_codes_json"]], on="order_id", how="left", validate="one_to_one")
    summary = {"source_rows": len(old), "unique_cases": len(reference), "duplicate_rows": len(old)-len(reference),
               "labels_by_unique_case": reference.human_reference_label.value_counts().to_dict(),
               "in_development_scope": int(reference.in_development_scope.sum()),
               "outside_development_scope": int((~reference.in_development_scope).sum()),
               "accuracy_computed": False,
               "limitation": "Candidate-selected historical reference, not representative truth; no label/action equivalence or accuracy claim."}
    return reference, summary


def detector_rule_overlap(cases, candidates, validation_flags):
    aligned = cases.set_index("order_id").loc[validation_flags.order_id]
    if not aligned.split.eq("validation").all() or not aligned.timing_rules_available.all():
        raise ValueError("Overlap diagnostics require timing-eligible validation cases")
    rows = []
    for candidate in candidates.itertuples(index=False):
        flag = validation_flags[candidate.candidate].to_numpy(dtype=bool)
        for rule in ["long_duration_warning", "short_duration_warning", "late_delivery_warning"]:
            applicable = aligned[rule].notna().to_numpy()
            signal = aligned[rule].fillna(False).to_numpy(dtype=bool)[applicable]
            predicted = flag[applicable]
            both = int((predicted & signal).sum())
            union = int((predicted | signal).sum())
            rows.append({"candidate": candidate.candidate, "family": candidate.family, "rule": rule,
                         "compared_validation_cases": int(applicable.sum()), "both_flag": both,
                         "detector_only": int((predicted & ~signal).sum()), "rule_only": int((~predicted & signal).sum()),
                         "neither_flag": int((~predicted & ~signal).sum()), "jaccard_overlap": both/union if union else None})
    return pd.DataFrame(rows)


def build_outputs(context, ledger, thresholds, candidates, flags_by_split, old):
    cases, evidence = validate_cases(context, ledger, thresholds, candidates, flags_by_split)
    reference, reference_summary = compare_human_reference(old, cases)
    actions = cases.groupby(["split", "auto_action"]).size().rename("case_count").reset_index()
    reasons = evidence.groupby(["split", "reason_code", "kind"]).order_id.nunique().rename("unique_cases").reset_index()
    monthly = cases.groupby(["split", "purchase_month"]).agg(
        all_cases=("order_id", "size"), timing_available=("timing_rules_available", "sum"),
        model_available=("model_signals_available", "sum"), source_record_issues=("source_record_issue", "sum"),
        long_warnings=("long_duration_warning", "sum"), short_warnings=("short_duration_warning", "sum"),
        model_signals=("model_candidate_signal", "sum"), any_warning_or_record_issue=("has_warning_or_record_issue", "sum"),
    ).reset_index()
    monthly["long_warning_fraction_of_timing_available"] = monthly.long_warnings.div(monthly.timing_available.replace(0, np.nan))
    monthly["short_warning_fraction_of_timing_available"] = monthly.short_warnings.div(monthly.timing_available.replace(0, np.nan))
    monthly["model_signal_fraction_of_model_available"] = monthly.model_signals.div(monthly.model_available.replace(0, np.nan))
    examples = cases.groupby("auto_action", sort=True).head(2).copy()
    frames = {"case_results.csv": cases, "rule_evidence.csv": evidence, "timing_thresholds.csv": thresholds,
              "action_summary.csv": actions, "reason_summary.csv": reasons, "monthly_warning_summary.csv": monthly,
              "detector_rule_overlap.csv": detector_rule_overlap(cases, candidates, flags_by_split["validation"]),
              "human_reference_comparison.csv": reference, "real_case_examples.csv": examples}
    summary = {"development_cases": len(cases), "split_cases": cases.split.value_counts().to_dict(),
               "timing_rules_available": int(cases.timing_rules_available.sum()),
               "timing_rules_unavailable": int((~cases.timing_rules_available).sum()),
               "model_signals_available": int(cases.model_signals_available.sum()),
               "model_signals_unavailable": int((~cases.model_signals_available).sum()),
               "actions": cases.auto_action.value_counts().to_dict(),
               "reference": reference_summary, "test_scored": False, "selected_candidate": None,
               "human_labels_created": False, "anomaly_probabilities_computed": False, "accuracy_computed": False}
    return frames, summary


def render_readme(frames, summary):
    return "\n\n".join([
        "# Bed bath table Auto Validation v1",
        "## Purpose\nAutomate evidence checks on real Olist development cases, without requiring another manual-label batch. This is a decision-support and explanation layer, not a replacement ground-truth labeling system. Raw data, frozen experiments, models and human reviews remain unchanged. No fabricated cases, events, timestamps or human labels are used.",
        "## Results\n" + audit.markdown_table(frames["action_summary.csv"]),
        "Action groups are mutually exclusive. Individual reasons overlap. verify_source_record means a recorded inconsistency, not proof that the real-world process failed. inspect_timing_warning includes short/zero, long and calendar-late warnings. inspect_model_signal means an exploratory model flag without a timing-rule flag. insufficient_context means checks cannot support a completed-case conclusion. no_signal_in_available_checks is NOT a verified Normal label.",
        "## Scope\n" + json.dumps({key: summary[key] for key in ["development_cases", "split_cases", "timing_rules_available", "timing_rules_unavailable", "model_signals_available", "model_signals_unavailable"]}, indent=2),
        "All 7,333 train/validation cases receive retrospective record/status checks, including incomplete/reversed cases. The 1,691 final-test cases are not processed or scored. Timing warnings use only the original 6,601 timing-eligible development cases. The 732 excluded cases retain blank timing flags, not False. Timing values after partition cutoffs are not compared. Snapshot status was not necessarily known at a historical partition date; these are not online backtest decisions. Missing canceled/nonfinal milestones do not automatically imply an anomaly; no ongoing age is invented.",
        "The reference contains 3,190 inner-fit cases. They receive descriptive statistical checks, not out-of-fit ML evaluation. Only the saved 1,412 calibration and 1,523 validation cases have benchmark signals. The 476 inner-deferred cases receive eligible retrospective timing rules but no new ML scoring. benchmark_role and availability columns expose these different scopes. Calibration was used to set thresholds and is not independent performance evaluation.",
        "## Timing policy\n" + audit.markdown_table(frames["timing_thresholds.csv"]),
        "All thresholds are from the frozen inner fit, never from validation/test. Main long warning uses raw Q75 + 1.5*IQR; severe long warning uses Q75 + 3*IQR. Positive durations below fit Q01 and durations equal to zero only trigger warnings. These are explicit exploratory explanation rules, NOT a selected best formula or established business SLA. Strict >/< comparisons retain boundary ties. The three transitions are evaluated separately; total cycle time is retained as context, not an additional independent feature. Calendar lateness compares dates, so delivery later in the same day is not marked late.",
        "## Model integration\nThe 120 frozen candidate flags are carried as per-case positive IDs and counts by family. MODEL_CANDIDATE_SIGNAL uses OR across the ML/ensemble subset as an exploratory inspection signal. Threshold variants and ensembles are correlated, not independent votes. Counts, ranks, scores and flag rates are not anomaly probabilities. A large flag count does not establish that a method is better. Exact scores and thresholds remain in the parent benchmark; candidates.csv names match positive_candidate_ids_json.",
        "detector_rule_overlap.csv compares all 120 candidates with long, short and calendar-late rules on the same 1,523 validation cases. These are overlapping signals, not predicted-vs-true confusion matrices, accuracy, precision, recall or F1. Rules also share information with the detectors. No winner is selected from this agreement.",
        "## Existing human reference\n```json\n" + json.dumps(summary["reference"], indent=2) + "\n```",
        "Duplicate candidate rows are consolidated only in the derived comparison after requiring consistent labels. Original confidence, reason and note variants remain as lists. All 71 unique IDs stay visible, including 66 outside the current development scope. The five in-scope cases are a biased, very small reference, not an independent validation set. No new human labels are created; optional blind-review batches stay untouched and do not block this pipeline.",
        "## Relation to the manual protocol\nMissing activity becomes a status-aware record check, reversed order becomes all-pair observed timestamp comparisons, very long/multiple durations become feature-level evidence, and multivariate patterns link to saved model signals. Borderline/other reasons and subjective human confidence are not mechanically converted to truth. No old trace-fitness value is fabricated or copied across incompatible populations.",
        "## Frequency and remaining work\nmonthly_warning_summary.csv reports counts and rates with explicit available-case denominators. It does not implement a validated frequency-anomaly model. One Olist timestamp per stage cannot reveal repeat executions; event repetition must not be invented. Next development can add supported behavior features and the per-dataset import/train/save interface. Real-world accuracy and calibrated anomaly probability still require an appropriate independent reference; automation alone cannot establish them. These limitations do not prevent automatic processing.",
        "## Files and rerun\ncase_results.csv: all case evidence/actions; rule_evidence.csv: one row per triggered rule/feature with observations and thresholds; timing_thresholds.csv: exact fit-derived day thresholds; summaries/overlap/reference/examples: derived real-case views; protocol.json: explicit policy; manifest.json: provenance and hashes. Output is immutable; reruns must use a new directory.",
        "```bash\n.venv/bin/python scripts/03_anomaly_detection/auto_validate_bed_bath_table.py --output data/experiments/bed_bath_table_auto_validation_v2\n```",
    ]) + "\n"


def write_outputs(frames, summary, hashes, source, benchmark_dir, reference, output):
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite Auto Validation: {output}")

    def check_sources():
        verify_benchmark(benchmark_dir)
        previous.verify_experiment(source)
        if hashes != input_hashes(benchmark_dir, reference, source):
            raise ValueError("Inputs changed before Auto Validation publication")

    check_sources()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f".{output.name}-", dir=output.parent) as temporary:
        staging = Path(temporary)
        for filename, frame in frames.items():
            frame.to_csv(staging / filename, index=False, date_format="%Y-%m-%d %H:%M:%S")
        (staging / "protocol.json").write_text(json.dumps(protocol(), indent=2) + "\n")
        (staging / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        (staging / "README.md").write_text(render_readme(frames, summary), encoding="utf-8")
        manifest = {"status": "complete", "auto_validation_version": protocol()["version"],
                    "created_at_utc": datetime.now(timezone.utc).isoformat(),
                    "sources": {"experiment": str(Path(source).resolve()), "benchmark": str(Path(benchmark_dir).resolve()),
                                "human_reference": str(Path(reference).resolve())},
                    "source_hashes": hashes, "summary": summary,
                    "code_hashes": {str(path.relative_to(previous.ROOT)): audit.file_hash(path)
                                    for path in [Path(__file__), Path(benchmark.__file__), Path(previous.__file__)]},
                    "output_hashes": {path.name: audit.file_hash(path) for path in sorted(staging.iterdir())}}
        check_sources()
        (staging / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        if output.exists():
            raise FileExistsError(f"Auto Validation output appeared during publication: {output}")
        staging.rename(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", type=Path, default=benchmark.DEFAULT_OUTPUT)
    parser.add_argument("--reference", type=Path, default=DEFAULT_REFERENCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(f"Refusing to overwrite Auto Validation: {args.output}")
    context, ledger, thresholds, candidates, flags, old, hashes, source = load_inputs(args.benchmark, args.reference)
    frames, summary = build_outputs(context, ledger, thresholds, candidates, flags, old)
    write_outputs(frames, summary, hashes, source, args.benchmark, args.reference, args.output)
    print(json.dumps(summary, indent=2))
    print(f"Output: {args.output.resolve()}")


if __name__ == "__main__":
    main()
