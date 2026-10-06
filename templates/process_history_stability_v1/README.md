# Training-history sensitivity

These are real-data experiment plans, not sample orders. They change only the
training purchase start (9, 6 or 3 calendar months ending 2017-12-01) and run ID.
They use the same imported Olist bed_bath_table development snapshot, calibration
period [2017-12-01, 2018-03-01) and validation period [2018-03-01, 2018-06-01).
All actual fit milestones must precede 2017-12-01; the existing calibration and
validation completion cutoffs remain unchanged. Original split metadata is not
rewritten. Out-of-scope orders remain in each run's ledger.

Run from the project root:

```sh
.venv/bin/python scripts/03_anomaly_detection/evaluate_training_history_stability.py \
  --snapshot data/imported/olist_bed_bath_table_import_v1 \
  --plans templates/process_history_stability_v1/fit_9m.json \
          templates/process_history_stability_v1/fit_6m.json \
          templates/process_history_stability_v1/fit_3m.json \
  --output data/experiments/olist_history_stability_v1
```

Existing outputs are never overwritten. Each plan fits ten new ML pipelines and
statistical baselines. Each run calibrates the same 120 candidate definitions on
the same calibration orders, then scores the same validation orders. This yields
30 ML fits and 360 window-configuration results, not 360 unique algorithms.
The experiment never reads final-test features or human-review labels.

This isolates the evaluation population, not the causal effect of history
length: history length, fit sample size and calendar mix change together. It is
not rolling-origin backtesting, random-seed robustness or independent accuracy
evaluation. The existing validation population is being reused for exploratory
development. No candidate is automatically selected. No new import or manual
review is required to run this study.
