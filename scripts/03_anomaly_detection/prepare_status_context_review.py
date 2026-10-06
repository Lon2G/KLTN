"""Prepare a reproducible status-aware human review batch with blank labels."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, MANUAL_REVIEW_DIR
from auto_validation_evaluation import AUTO_FILE, BASELINE_FILE, file_sha256, markdown_table
from order_status_context_audit import ORDERS_FILE, build_blind_cases, load_context


DEFAULT_BATCH_DIR = MANUAL_REVIEW_DIR / "status_context_review_v1"
STRATA_COLUMNS = ["comparison_group", "order_status", "recorded_event_profile"]
THRESHOLDS_FILE = ANOMALY_DATA_DIR / "auto_validation_selected_thresholds.csv"


def collect_reviewed_ids(paths: list[Path]) -> tuple[set[str], list[dict]]:
    """Union recorded human-review IDs, including duplicates in derived comparison files."""
    reviewed_ids = set()
    sources = []
    for path in sorted(paths):
        header = pd.read_csv(path, nrows=0)
        if not {"case_id", "reviewer_label"}.issubset(header.columns):
            continue
        frame = pd.read_csv(path, usecols=["case_id", "reviewer_label"], dtype="string")
        reviewed = frame.reviewer_label.fillna("").str.strip().ne("")
        if not reviewed.any():
            continue
        if frame.loc[reviewed, "case_id"].isna().any():
            raise ValueError(f"Reviewed row without case_id in {path}")
        ids = set(frame.loc[reviewed, "case_id"])
        reviewed_ids.update(ids)
        sources.append({"path": str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
                        "sha256": file_sha256(path), "reviewed_rows": int(reviewed.sum()),
                        "unique_reviewed_cases": len(ids)})
    return reviewed_ids, sources


def select_sample(context: pd.DataFrame, reviewed_ids: set[str], sample_size: int, seed: int):
    if context.case_id.duplicated().any() or context.case_id.isna().any():
        raise ValueError("Sampling frame must have unique non-null case_id")
    if context[STRATA_COLUMNS].isna().any().any():
        raise ValueError("Missing sampling stratum")
    eligible = context.loc[~context.case_id.isin(reviewed_ids)].sort_values("case_id").copy()
    if not 0 < sample_size <= len(eligible):
        raise ValueError("Sample size must be positive and not exceed eligible cases")
    population = context.groupby(STRATA_COLUMNS).size().rename("population_cases")
    available = eligible.groupby(STRATA_COLUMNS).size().rename("eligible_cases")
    strata = population.to_frame().join(available).fillna({"eligible_cases": 0}).astype(int).reset_index()
    strata["excluded_reviewed_cases"] = strata.population_cases - strata.eligible_cases
    strata["sample_cases"] = 0
    occupied = strata.index[strata.eligible_cases.gt(0)]
    if sample_size < len(occupied):
        raise ValueError(f"Need at least {len(occupied)} cases to cover every eligible stratum")
    remaining = sample_size
    # Equal allocation across occupied strata, capped by their available real cases.
    while remaining:
        for index in occupied:
            if strata.at[index, "sample_cases"] < strata.at[index, "eligible_cases"]:
                strata.at[index, "sample_cases"] += 1
                remaining -= 1
                if remaining == 0:
                    break
    strata["inclusion_probability"] = strata.sample_cases / strata.eligible_cases.replace(0, np.nan)
    strata["sampling_weight"] = strata.eligible_cases / strata.sample_cases.replace(0, np.nan)
    rng = np.random.default_rng(seed)
    pieces = []
    grouped = eligible.groupby(STRATA_COLUMNS, sort=True)
    for row in strata.itertuples(index=False):
        if row.sample_cases == 0:
            continue
        key = tuple(getattr(row, column) for column in STRATA_COLUMNS)
        group = grouped.get_group(key)
        pieces.append(group.iloc[rng.choice(len(group), size=row.sample_cases, replace=False)])
    sample = pd.concat(pieces, ignore_index=True)
    sample = sample.iloc[rng.permutation(len(sample))].reset_index(drop=True)
    sample.insert(0, "review_id", [f"R{index:04d}" for index in range(1, len(sample) + 1)])
    selection_key = sample.merge(strata, on=STRATA_COLUMNS, validate="many_to_one")
    return sample, strata, selection_key


def write_batch(batch_dir: Path, sample: pd.DataFrame, strata: pd.DataFrame,
                selection_key: pd.DataFrame, reviewed_ids: set[str], sources: list[dict], seed: int) -> None:
    if batch_dir.exists():
        raise FileExistsError(f"Review batch already exists: {batch_dir}. Use it or choose a new batch directory.")
    blind = build_blind_cases(sample)
    batch_dir.mkdir(parents=True)
    analyst_dir = batch_dir / "analyst_only"
    analyst_dir.mkdir()
    blind_path = batch_dir / "review_cases_blind.csv"
    blind.to_csv(blind_path, index=False)
    selection_key.to_csv(analyst_dir / "selection_key.csv", index=False)
    strata.to_csv(analyst_dir / "sampling_strata.csv", index=False)
    pd.read_csv(THRESHOLDS_FILE).to_csv(analyst_dir / THRESHOLDS_FILE.name, index=False)

    source_paths = [ORDERS_FILE, BASELINE_FILE, AUTO_FILE, THRESHOLDS_FILE]
    code_paths = [Path(__file__).resolve(),
                  *(Path(__file__).with_name(name) for name in [
                      "order_status_context_audit.py", "auto_validation_evaluation.py",
                      "auto_validation.py", "auto_validation_threshold_analysis.py",
                      "status_context_manual_review.py",
                  ])]
    manifest = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "seed": seed, "rng": "numpy.default_rng/PCG64", "numpy_version": np.__version__,
        "pandas_version": pd.__version__, "requested_sample_size": len(sample),
        "population_cases": int(strata.population_cases.sum()),
        "eligible_cases": int(strata.eligible_cases.sum()),
        "previously_reviewed_ids_in_source_files": len(reviewed_ids),
        "excluded_reviewed_cases_in_population": int(strata.excluded_reviewed_cases.sum()),
        "strata_columns": STRATA_COLUMNS,
        "sampling_design": "Equal capped allocation; simple random sampling without replacement within each stratum; randomized display order.",
        "target_population": "Imported cases without a previously recorded human label at batch creation.",
        "reviewed_sources": sources,
        "input_sha256": {str(path.relative_to(PROJECT_ROOT)): file_sha256(path) for path in source_paths},
        "code_sha256": {str(path.relative_to(PROJECT_ROOT)): file_sha256(path) for path in code_paths},
        "output_sha256": {str(path.relative_to(batch_dir)): file_sha256(path)
                          for path in [blind_path, *sorted(analyst_dir.glob("*.csv"))]},
        "limitations": [
            "Pilot diagnostic sample size chosen for review workload, not a precision or power calculation.",
            "Unweighted sample rates are not population estimates because strata are deliberately oversampled.",
            "Weights N_h/n_h represent eligible cases only, not the excluded previously reviewed cases.",
            "Novel human-review cases are not a model holdout: existing detectors and thresholds used the imported population.",
            "Recorded status and timestamps cannot establish the unavailable authoritative observation cutoff.",
            "No generated labels are human ground truth; reviewer fields start blank.",
        ],
    }
    (analyst_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Status-Context Review Batch", "",
        f"Prepared {len(sample)} real, unique cases. Human-review labels are blank.", "",
        "## Reviewer Workflow", "",
        "Use review_cases_blind.csv or the terminal review tool. Do not consult analyst_only "
        "or case-level detector outputs until all decisions in this batch are saved.", "",
        "The review file contains recorded status, actual milestone timestamps, estimated "
        "delivery date, missing milestone names, and durations. It excludes model labels, "
        "votes, scores, thresholds, fitness and sampling-group information.", "",
        "1. Inspect recorded status and actual timestamps; a missing later milestone in a "
        "cancelled, unavailable or nonfinal order does not alone establish an anomaly.",
        "2. Consider structural consistency, timing and data quality separately. A delivery "
        "timestamp with a non-delivered status needs context rather than an automatic judgment.",
        "3. Use Suspicious with an uncertainty reason when available context is insufficient. "
        "Do not infer time spent pending from today's date or the largest observed timestamp.",
        "4. Enter label, confidence, reason and optional notes. The terminal tool records "
        "the actual review time and saves after each completed decision.",
        "5. Resume the same batch to continue. Detector information is not revealed during "
        "the batch, even after an individual decision.", "",
        "Duration and calendar-day differences are descriptive measurements, not fixed "
        "business limits. Negative calendar-day delivery difference means early delivery, "
        "not a reversed transition. No calibrated business SLA is supplied for transition durations.", "",
        "This is a status-aware extension of the earlier manual protocol. Keep earlier "
        "labels unchanged and do not pool the two rounds as identical review conditions.", "",
        "## Run", "", "```sh",
        f'.venv/bin/python scripts/03_anomaly_detection/status_context_manual_review.py --batch-dir "{batch_dir}"',
        "```", "",
        "Progress goes to review_progress.csv; the original blind file stays unchanged. "
        "Rerunning batch preparation will refuse to overwrite this directory.", "",
    ]
    (batch_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    analyst_lines = [
        "# Sampling Design (Analyst Only)", "",
        f"Population: {manifest['population_cases']:,}. Eligible: {manifest['eligible_cases']:,}. "
        f"Previously reviewed exclusions: {manifest['excluded_reviewed_cases_in_population']:,}.", "",
        f"Sample: {len(sample)}. Seed: {seed}. Stratification: comparison group x status x milestone completeness.", "",
        "Allocation cycles through lexicographically sorted occupied strata, giving one "
        "additional place each pass until the target is reached, capped at each stratum's "
        "eligible size. Cases are randomly drawn without replacement within strata.", "",
        "For each eligible case in stratum h, inclusion probability = n_h / N_h; "
        "sampling weight = N_h / n_h. All eligible strata receive at least one case. "
        "Previously reviewed cases have zero inclusion probability in this batch.", "",
        *markdown_table(strata), "", "## Limits", "",
        *(f"- {item}" for item in manifest["limitations"]), "",
        "Freeze this batch's inputs/rules while reviewing. Any rule tuning based on its "
        "labels makes it development data for that revised rule set. New timestamps stored "
        "in manifest/progress are audit metadata, not fabricated order events.", "",
    ]
    (analyst_dir / "sampling_design.md").write_text("\n".join(analyst_lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample-size", type=int, default=120)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--batch-dir", type=Path, default=DEFAULT_BATCH_DIR)
    args = parser.parse_args()
    batch_dir = args.batch_dir.resolve()
    if batch_dir.exists():
        parser.error(f"Batch already exists: {batch_dir}; existing review work will not be overwritten")
    context = load_context()
    reviewed_ids, sources = collect_reviewed_ids(list(MANUAL_REVIEW_DIR.rglob("*.csv")))
    sample, strata, selection_key = select_sample(context, reviewed_ids, args.sample_size, args.seed)
    write_batch(batch_dir, sample, strata, selection_key, reviewed_ids, sources, args.seed)
    print(f"Prepared {len(sample)} unique cases; excluded {strata.excluded_reviewed_cases.sum()} reviewed cases.")
    print(f"Occupied eligible strata: {int(strata.eligible_cases.gt(0).sum())}")
    print(f"Blind review file: {batch_dir / 'review_cases_blind.csv'}")
    print("Human labels are blank. Analyst files are separate from the reviewer file.")


if __name__ == "__main__":
    main()
