# KLTN-Lon2G

Graduation thesis code for process-aware anomaly analysis in e-commerce order
fulfillment. The current experiments use real Olist data, with `bed_bath_table`
(`cama_mesa_banho`) as the main product category.

## Repository Scope

This repository contains source code, tests and configuration templates. Raw data,
processed snapshots, human review files, fitted models and generated reports are
kept locally and excluded from Git. No synthetic orders or reference labels are
provided as substitutes for the real research data.

## Structure

- `scripts/01_data_preparation/`: source audit, standardization, usage policy,
  category comparison, temporal cohorts, import contracts and business features.
- `scripts/02_process_mining/`: process discovery, conformance and performance analysis.
- `scripts/03_anomaly_detection/`: detectors, threshold experiments, model training,
  batch scoring, feature ablation, review preparation and human-review tools.
- `templates/`: import/training configurations, experiment protocols and blank review criteria.
- `tests/`: integration tests using real local observations and source-preservation checks.
- `project_paths.py`: local directory definitions.
- `requirements.txt`: Python dependencies.

## Local Setup

Run from the project root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Obtain the Olist source data separately and place the original tables in `data/raw/`.
The repository does not download data or fabricate missing observations. The full
test suite also requires the local processed snapshots and prior experiment
artifacts; a fresh code-only checkout is not sufficient to run it successfully.

## Current Workflow

1. Audit and standardize raw tables while preserving observed values and missingness.
2. Define data-use eligibility, compare categories and form chronological partitions.
3. Import development data with explicit schema, identity and provenance checks.
4. Train and calibrate detectors on separate periods; compare validation results.
5. Compare timing-only, business-only and combined feature profiles on the same cases.
6. Prepare a blinded review pilot from real validation orders, with labels left blank.

Relevant entry points include `clean_olist_data.py`,
`prepare_bed_bath_table_experiment.py`, `import_process_data.py`,
`build_bed_bath_table_business_features.py`, `train_imported_process.py`,
`train_business_ablation.py`, `prepare_business_review.py` and
`review_business_cases.py`. Consult the corresponding files under `templates/`
for scope, prerequisites and configuration contracts. Snapshot-producing commands
refuse to overwrite existing versions.

## Validation And Limits

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Software tests do not establish anomaly-detection accuracy. Model warnings are not
verified fraud labels, and anomaly scores are not calibrated probabilities. The
review pilot is provisional pending agreed criteria and human judgments. Current
business-feature experiments are retrospective, not a validated real-time system.
A model trained for one dataset is not claimed to work unchanged for another marketplace.

Retain the local versioned snapshots, manifests, environment information and review
history separately for reproducibility. Git is a code backup, not a backup of those
excluded research artifacts.
