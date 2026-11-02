# Temporal Selection of Two Frozen Candidates

## Decision Contract

This is an exploratory choice between `upper_q950 > 0` and the existing
`max_stage_delay_score > 0` rule. It is conditional on observed calendar lateness
in the previously inspected Olist bed_bath_table validation cohort. It is not
confirmed-bottleneck accuracy, independent cross-validation or online detection.

Before inspecting candidate-specific monthly results, use this decision order:

1. Maximize mean monthly F1 over purchase months containing both observed classes.
   Give eligible months equal weight, not weight proportional to their order count.
2. On a tie within 1e-12, maximize the worst eligible-month F1.
3. On a further tie, maximize pooled F1. Preserve a remaining tie; no arbitrary winner.

This operationalizes the user's preference to balance precision and recall. It is
a working preference, not an advisor-approved cost function or universal optimum.
Choose a provisional candidate in an output artifact, but do not activate it in
the existing pipeline. Compare AUC/AP, recall, precision, alert workload and equal
FP/FN cost independently; do not invent an overall weighted quality score.

## Periods and Sensitivity

- Primary: fixed calendar purchase months. Do not split by delivery date, whose
  timing is also part of the outcome.
- Secondary: calendar half-months (days 1-15, then 16 to month end), set before
  inspecting their scores. No best-boundary search or reweighting after results.
- Remove each observed Monday-Sunday purchase week in turn, then repeat the same
  monthly decision on the SAME originally eligible months. If any required month
  loses a class, mark that deletion noncomparable instead of silently dropping it.
- Repeat this sensitivity by removing each eligible month in turn. These are
  influence diagnostics on overlapping observations, not new test folds, model
  retraining, confidence intervals or independent replications.
- Report paired alert overlap/disagreement by outcome and period, and the exact
  disagreement case IDs. No new reference label is assigned from either detector.
- Show FP + r*FN for r in {0.5, 1, 2, 5}, and any positive crossover ratio between
  the two pooled cost lines. These are hypothetical preferences, not monetary costs.

The inspected cohort contains 121 late orders in March 2018, 17 in April and zero
in May. AUC, AP and F1-for-selection are unavailable in a single-class period.
Raw F1 is still reported when 2*TP+FP+FN > 0 (zero can be mathematically defined
when there are alerts but no late orders); do not treat it as evidence of late-case
detection. Recall is missing with no positives; precision is missing with no alerts.
Negative-only months still contribute to pooled counts and have separately reported
false-positive/alert rates. No extra positive cases or labels are manufactured.

## Coverage and Limitations

Keep all 1,307 comparison orders and frozen thresholds. No per-candidate exclusions.
Audit monthly coverage against the 1,621 public guarded signal orders, not all Olist
or the additional pilot cases. Missing/excluded outcomes stay unknown. Record
available follow-up from purchase to the unchanged observation cutoff, month
completeness and class prevalence. Less follow-up and completed-case selection can
change apparent performance; do not infer the cause of a monthly change.

Both methods use completed stage durations. Their features/reference populations
differ. Calendar lateness is a proxy outcome and the promised-date basis is unknown.
The two shortlisted candidates and this selection criterion are based on prior
validation inspection; no confirmatory or pristine-test claim is made. With only
two months containing positives and few April positives, stability evidence is
limited even if every deletion chooses the same candidate.

## Reproduce

```sh
.venv/bin/python -B scripts/03_anomaly_detection/select_temporal_delivery_candidate.py
.venv/bin/python -B scripts/03_anomaly_detection/select_temporal_delivery_candidate.py --verify
.venv/bin/python -B -m unittest discover -s tests -p test_temporal_candidate_selection.py
```

Default output: `data/experiments/olist_temporal_candidate_selection_v1`.
No overwrite. Source/implementation hashes bind each new snapshot. Data, trained
models, review journals and earlier snapshots remain unchanged.

Metric definitions and evaluation cautions follow the primary documentation:
- [scikit-learn F1](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.f1_score.html)
- [scikit-learn evaluation and model selection](https://scikit-learn.org/stable/modules/cross_validation.html)
