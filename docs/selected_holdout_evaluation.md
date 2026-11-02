# Frozen-candidate holdout diagnostic

This contract is fixed before inspecting holdout outcomes. It evaluates only
`product_context_stage_score_gt0`, selected by the saved temporal comparison,
with `max_stage_delay_score > 0`. No alternative candidate or threshold search.

## Population and limitations

- Use the original `bed_bath_table_v1` test partition: purchases in
  `[2018-06-01, 2018-09-01)`. Do not activate the proposed 80/20 boundary:
  it would move already-inspected validation orders into the test set.
- Retain every test order in a coverage ledger. Evaluate discrimination only
  where the calendar outcome and all three stage scores are available.
- The source extraction cutoff is unknown. Do not invent a follow-up horizon,
  treat unresolved orders as negative, or claim a mature full-cohort benchmark.
- This is a retrospective completed-observation diagnostic. Selection of the
  industry used earlier full-history exploration; this is not an untouched
  external benchmark. Once scored, the test set is exposed for future work.
- The target is recorded delivery calendar date after the promised calendar
  date, NOT a confirmed bottleneck, fraud, verified cause, or online forecast.
  Scores use completed-stage durations. The basis of the promise is unverified.

## Frozen processing

Reuse the historical train-stage table, product fallback hierarchy, minimum
support 30, tail-score formula, geographic bands, and fitted geographic-quality
limits. Do not append validation/test durations or fit new thresholds.
Preserve the existing category assessability gate before product matching.
For new ZIPs, apply the same deterministic observed-coordinate representation
to the static source map. This is not an outcome fit; coordinate availability
at event time remains unverified, so the experiment remains retrospective.

Before reading holdout outcomes, reconstruct validation scores and geographic
quality decisions and require parity with saved outputs. Source hashes bind the
selection, reference table, clean inputs, original partition, and implementation.
Stop on a mismatch. Preserve raw/clean data and every old snapshot.

## Outputs

Report total and monthly coverage, exclusions (with overlapping reason flags),
TP/FP/FN/TN, precision, recall, F1, ROC AUC and average precision. Keep undefined
metrics missing when a class or denominator is absent. AP is average precision,
not trapezoidal PR AUC. No confidence/significance claim or model reselection.
Compare validation and test descriptively, disclosing different observation
windows and complete-case selection, not as a controlled superiority test.

Keep stage warnings even on early deliveries. Report signed differences from
historical stage means independently of tail-warning flags; never force an
early order to have a faster stage. Missing references/scores are not zero.
FP here means alert on a nonlate order, not proof that its stage warning is wrong.

The result does not activate a production model. Any subsequent tuning makes
this cohort development data and requires a new independent evaluation scope.
Verified source-cutoff metadata is still needed to assess follow-up completeness.

Run from the repository root:

```sh
.venv/bin/python -B scripts/03_anomaly_detection/evaluate_selected_holdout.py
```

Add `--verify` to check the published snapshot. Existing output directories
cannot be overwritten. CSV timestamps use explicit seconds, including midnight.
