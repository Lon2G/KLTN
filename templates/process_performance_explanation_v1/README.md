# Process Performance Explanations V1

Read-only derivation from the frozen `olist_process_signals_v1` snapshot.
This implements the five confirmed business decisions without changing source
data, existing flags, thresholds, human labels or fitted models.

## Outputs

- `order_explanations.csv`: original order fields plus an unverified promise
  basis, speed-comparison coverage, faster/slower/equal stage lists, nullable
  faster-stage presence and an explanation of early delivery.
- `stage_comparisons.csv`: original stage fields plus signed deviation from
  the train peer median, comparison direction and Vietnamese interpretation.
- `bao_cao_dien_giai.md`: summary and examples selected from actual orders.
- `protocol.json`, `summary.json`, `manifest.json`: scope, counts and hashes
  binding the source snapshot, code, template and business decision record.

Original columns are preserved exactly. New columns do not replace the source
`policy_version`, `assessable`, `long_stage_warning`, `short_stage_note`,
`calendar_late_signal` or `primary_attention_status`.

The derivation consumes only `order_signals.csv` and `stage_signals.csv`.
Recursive source verification also checks older audit/import/review tables;
those checks do not pass historical labels into the explanation calculations.
No model bundle is loaded, fitted or selected, and no final-test scoring occurs.

## Interpretation

The dataset does not provide the method that generated each promise date.
`unverified_source_method_not_provided` means unverified, not random or wrong.
No promise is recalculated and no stage plan is fabricated. A future verified
plan would require a new source contract and version, not a silent override.

For an assessable positive duration, `difference_from_reference_days` equals
actual duration minus the frozen contextual train median. Negative means faster,
positive means slower and exact zero means equal. This descriptive comparison
is separate from the existing tail-warning rule. It is not an optimized
threshold, original operational plan, probability or causal attribution.

Non-assessable stages retain their original reasons and nullable flags. Recorded
zero durations remain notes, not positive speed evidence. The deviation is blank
when a positive-duration speed comparison is unavailable.

An observed faster stage is retained even with partial coverage. A claim that
no stage is faster requires all three positive durations to be compared. Other
cases remain unknown. Early delivery never suppresses a long-stage warning.
Stage deviations must not be summed to explain calendar days early or late.

## Commands

From the repository root:

```sh
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/explain_process_performance.py
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/explain_process_performance.py --summary
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/explain_process_performance.py --order 0f916489801ab47804cc7da10fe0dd6d
```

Creation refuses existing/protected destinations. `--summary` and `--order`
verify hashes and inspect saved results without rebuilding or changing them.
The scope remains retrospective validation, not final-test evaluation, a new
model winner, independently verified anomaly accuracy or a live service.
