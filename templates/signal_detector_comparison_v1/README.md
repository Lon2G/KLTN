# Signal Detector Comparison V1

Compare the existing timing detectors with the newly explicit process-signal
policy on the same real Olist `bed_bath_table` validation orders. This is a
new diagnostic view of already-used development data, not a new independent
experiment, model training, threshold search or root-cause analysis.

## Scope and Integrity

Sources are immutable `olist_process_signals_v1` and the verified replay
`olist_validation_batches_comparison_input_v1`. The older replay with an
edited README is not used. Verify all hashes and model/source identity before
publishing. Existing sources, outputs, models and thresholds stay unchanged.

Start with the signal snapshot's guarded validation population, already
excluding the original pilot and proposed-test purchases. Read saved score
availability as IDs only. Parse numeric scores/flags/durations and historical
case timestamps only for the common complete-context cohort. CSV records are
scanned to filter by ID; discarded records are not used in analysis. Integrity
checks hash files, including protected source contents, without evaluating them.
For retained IDs, match the exact timestamps and duration inputs to the masked
current observation window. Scores encoding later outcomes cannot be admitted.

All candidates share this same cohort. Outside it, neither an absent score nor
an unavailable reference becomes a negative. This remains completed-case,
retrospective analysis with incomplete context, not live detection on all orders.

## Outputs

- `comparison_scope.csv`: every signal-snapshot order and inclusion/exclusion.
- `common_cases.csv`, `common_scores.csv`, `common_flags.csv`: the actual common
  cohort, frozen scores and decisions, without changing thresholds.
- `candidate_definitions.csv`: every saved candidate and parameter-slice marker.
- `candidate_comparison.csv`: same-case counts, intersections, differences,
  Jaccard and hypothetical extra workload if added to the current signal policy.
- `focus_comparison.csv`: all score methods with calibration tail .05 plus the
  five declared fixed baselines. This is a readable slice, not a chosen winner.
- `focus_disagreement_cases.csv`: actual candidate/order disagreements for
  that slice, with saved scores/cutoffs and the reference stage list. A pair is
  not a distinct order. Reference labels stay blank. This model-exposed analyst
  view is not a blinded reviewer packet and does not classify errors.
- `stage_overlap.csv`: co-occurrence with each context-stage long warning.
  It is NOT an explanation of which feature caused an ML score or which stage
  caused lateness. Model-only warnings are not falsely assigned a stage.
- `group_comparison.csv`: purchase-month and distance-band denominators and
  descriptive intersections. Month cohorts share a fixed observation cutoff;
  this is not rolling validation, retraining stability or causal geography.
- `family_summary.csv`: configuration ranges, not independent votes.
- `rule_tradeoff_frontier.csv`: nondominated configurations relative to the
  context-long rule only, keeping ties and all original candidates elsewhere.
- `bao_cao_so_sanh.md`: Vietnamese report with explicit interpretation limits.

## Interpretation

For candidate set C and context-long set R on the common cohort:

- both = |C intersect R|;
- candidate_without_context_long = |C minus R|;
- context_long_without_candidate = |R minus C|;
- Jaccard = |C intersect R| / |C union R|, undefined for an empty union.

These are NOT independently verified TP/FP/FN, precision, recall, F1 or accuracy.
Rules and detectors use the same duration observations and can agree for
non-independent reasons. A model-only flag might describe a short duration,
a multivariate pattern, or an unhelpful alert; this comparison cannot decide
which. A long stage does not require knowing its operational root cause.

The exploratory frontier maximizes `both` while minimizing
`candidate_without_context_long`. A candidate is dominated only when another
is no worse on both counts and strictly better on at least one. No arbitrary
weighted score is introduced. This is a tradeoff against an explicit rule,
not proof of a best detector; candidates outside it are not deleted or rejected.

The .05 calibration-tail slice does not enforce equal validation alert rates,
is not calibrated anomaly probability, and is not numerically comparable to
per-stage contextual ECDF .95 as though they were the same decision rule.
Raw score magnitudes across different methods are not compared.

The primary policy remains calendar lateness OR context-long warning. Adding
an ML candidate is evaluated as a counterfactual union, not activated. Positive
short-duration notes and early delivery do not automatically become anomalies.
An independent reference for warning usefulness need not identify causes.
Operational provisional selection is possible later with explicit criteria;
an accuracy-optimality claim needs suitable independent assessment.

## Commands

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/compare_signal_detectors.py
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/compare_signal_detectors.py --summary
```

Existing and protected destinations are refused. All tables are round-trip
checked before atomic publication. No provider call, fake case, model loading,
training, human label, final-test scoring or near-real-time claim is made.
