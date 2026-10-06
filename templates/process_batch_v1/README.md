# Saved-model batch application v1

This is a command-line backend, not a UI or an accuracy evaluation. It consumes a trusted local `process_training_v1` model and a verified `process_import_v1` snapshot. The existing model, training code, input snapshots and final-test partition are not edited.

## Real development run

```bash
.venv/bin/python scripts/03_anomaly_detection/score_process_batch.py --model models/datasets/olist_bed_bath_table_training_v1 --snapshot data/imported/olist_bed_bath_table_import_v1 --config templates/process_batch_v1/olist_validation_replay.json --output data/experiments/olist_validation_batches_v1
```

This replays the same 1,853 validation cases, not newly collected or previously unseen data. March, April and May refer to purchase months. All use the original June 1 exclusive event cutoff, NOT an invented monthly extraction date. Timing-eligible cases retain the same frozen candidate scores and flags. Recorded statuses are retrospective snapshot evidence, not guaranteed historical online states.

## Future real imports

`new_batch_config.json` deliberately leaves unknown metadata blank. Do not fill these fields with invented source facts. The new import must have the same logical dataset, marketplace, primary category and clock contract as the trained model; `batch_id` distinguishes incoming batches. A changed source, category or process semantics needs a separately trained model. Do not relabel a different source to bypass this check.

Use unassigned split/cutoff mappings in the import configuration. Supply only real orders in the declared category and purchase window. Their purchase window must follow the saved development period; every actual event must precede the declared extraction cutoff. The cutoff must come from the source/export metadata, never today's date or the latest observed event. Estimated delivery dates are plans, not actual events, and may lie beyond extraction.

All previous development IDs, including incomplete/deferred cases, are rejected as new. Updated versions of existing orders need a separately designed update workflow. A known held-out test ID is also rejected even if its imported split is removed or changed. The trusted reservation CSV must cover the model's development IDs with `train`/`validation` markers and list all reserved IDs as `test`. Only the `order_id` and `split` columns are parsed. Declare its SHA256; relative paths resolve from the project root. Hashes check integrity, not whether a caller omitted a reservation or declared truthful source metadata.

There is currently no independent post-development Olist import outside the reserved final test. The saved Olist run is therefore a validation replay. Functional tests can exercise a new-batch contract using real development observations and an earlier training window, but do not establish new-data performance or an actual historical extraction date.

## Interpretation

- `case_results.csv`: every selected order, check availability, warning reasons and suggested inspection action. No automatic verified Anomaly/Normal label.
- `positive_candidate_evidence.csv`: actual score, frozen threshold and excess for each flagged candidate. Scores are not probabilities, and this is not causal feature attribution.
- `candidate_rates.csv`: flag counts divided by scored cases, with all-case counts shown separately. Zero scored cases produce an unavailable rate, not zero risk.
- `distribution_diagnostics.csv`: full-batch and purchase-month KS distance, Wasserstein distance in days, median and Q90 shifts, using the saved calibration timing sample.
- `quality_summary.csv`: missingness, reversed timestamps, status conflicts, status mix and timing coverage on all cases. Its reference includes all purchases in the calibration purchase period, not only completed timing cases.

KS is the largest absolute gap between empirical cumulative distributions. Wasserstein is the area between those distributions and retains the duration unit (days). A zero distance means these measured distributions agree; it does not establish absence of anomalies. Only individual duration distributions are compared, not all multivariate dependence or process frequency behavior.

The current KS warning setting of 0.10 and minimum 50 cases per sample are declared exploratory screening policies, not statistically optimized thresholds or accuracy claims. They are separate from per-order model thresholds. No p-values or calibrated probabilities are reported. Small/empty eligible samples remain unavailable for a warning decision. A distribution warning requests investigation; it never modifies weights/thresholds or automatically retrains.

Completed-case comparisons have availability bias and cannot characterize unfinished long-running orders. Compare coverage and missingness alongside duration distances. Nonfinal orders are not automatically anomalous, even when an extraction cutoff is known; this version does not implement an ongoing-age model.

Every output is versioned and hashed. Use a new directory for reruns. The command does not select the best candidate, prove fraud/causality, compute anomaly accuracy, request new human labels, or score the reserved final test. Only trusted local joblib bundles may be loaded.
