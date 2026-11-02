# Temporal Evaluation Plan

## Status and Purpose

This implements a metadata-only, versioned 80/20 design following the supplied
advisor direction. It is a proposal, NOT an activated split, a new training run,
or a claim that the detailed evaluation protocol has been approved. The original
cohort, models, thresholds, 120-case pilot and final-test reservation stay intact.
No synthetic observations, source corrections, reference labels or scores are
generated. This is a technical experiment record, not a thesis chapter.

## Fixed Partition Rule

Use all real orders in the frozen single-category bed_bath_table cohort. Sort by
the recorded purchase timestamp. Set the nominal test count to ceil(N/5). Take
the timestamp of the first order in that final block as the boundary. All orders
at or after that timestamp go into the proposed test; all earlier orders go into
development. Keep a complete equal-timestamp group together on the test side,
even when this makes test larger than 20%. Report actual counts and fractions.
Reject a dataset with no development orders after applying this rule.

Order IDs only stabilize display ordering; they cannot split simultaneous
purchases. Missing timestamps, duplicate IDs and invalid old split metadata are
rejected rather than repaired. Original split, review exposure, model outcomes
and anomaly frequency do not determine the boundary. Do not search alternative
boundaries for better metrics. Do not remove pilot overlaps to improve the ratio
or to make the proposed test appear untouched.

## Reading the Files

- `split_counts.csv`: proposed sizes and purchase-time ranges.
- `migration_counts.csv`: old split versus proposed outer role.
- `exposure_counts.csv`: overlapping counts from named saved experiments. These
  are not mutually exclusive and must not be summed into unique case counts.
- `analyst_only/partition_plan.csv`: exact real order IDs, purchase timestamps,
  old split, frozen legacy-review marker, proposed role and test exposure stratum.
  It is NOT an import configuration or a feature matrix. It has no pilot marker,
  review pseudonym, model score, anomaly label or outcome. Do not send this
  analyst file to blinded reviewers.
- `protocol.json`, `summary.json`, `manifest.json`: rules, actual counts, allowed
  metadata columns and SHA256 bindings. Existing destinations cannot be replaced.

Source files are byte-hashed for integrity. Only allowlisted CSV metadata columns
are parsed: IDs, purchase times, old roles and the old review-exposure boolean.
Delivery forecast/evaluation membership is read as IDs/roles, never values.
Model files may be byte-hashed but are never deserialized. Inspector payloads and
review files outside these allowlists are inventoried by hash, not parsed.
This binds named snapshots, not a fresh verification of every upstream derivation.

## Exposure Is Not Erased by Renaming a Split

The saved chronological design has 5,480 train, 1,853 validation and 1,691 test
orders. Moving the boundary earlier to obtain approximately 80/20 moves some
previously analyzed validation orders into the proposed test. Label them as
reused old development. Preserve the original 120-case pilot and only report
aggregate overlap counts; no new review key or human assessment is created.
Pilot membership does not mean somebody has already reviewed a case.

Earlier category selection and global analyses used the full historical data.
Even the old reserved-test stratum is not proven never seen. Known recorded
reviews, modeling membership and saved inspection activity do not constitute a
complete human access log. A zero count is not evidence of independence. The
legacy reviewed marker reflects the original cohort freeze, not all later work.
The new result can support a disclosed retrospective temporal experiment, not
a clean prospective generalization claim or transfer to another marketplace.

## Next Implementation

1. Prepare new versioned development inputs from this fixed boundary. Keep old
   experiments usable and keep the proposed test closed to model tuning. Freeze
   inner chronological fit, calibration and model-selection periods within the
   80%; no random split or repeated test-driven boundary search.
2. Recompute feature availability for those boundaries. A purchase in development
   does not make a later delivery outcome available for training. Do not reuse
   old validation results computed using the later old cutoff. Keep unfinished,
   missing and inconsistent cases in coverage ledgers without invented values.
3. Fit preprocessing and models in fit; calibrate on calibration; compare declared
   candidates on the inner selection period. Predeclare metrics and how a choice
   is made. Quantile-forecast MAE/coverage are not anomaly-classification accuracy.
4. Freeze the model and thresholds before any evaluation on the proposed test.
   Report the full test and the predeclared old-test/reused-validation strata,
   with eligible denominators and exposure limitations. If anomaly accuracy is
   claimed, define the reference target and collect actual reference judgments;
   neither a holdout split nor model warnings supply ground truth.
5. Implement recorded-event replay separately for near real-time measurement.
   Payment snapshots remain retrospective; missing event/arrival timestamps are
   not invented. Replay is not evidence of live operation or multi-agent AI.

## Reproduce

Run `.venv/bin/python scripts/01_data_preparation/plan_temporal_evaluation.py
--prepare` to publish a new plan, or `--summary` to verify an existing plan.
`--output` selects another fresh destination. There is deliberately no command
to train, score test, activate the proposal or overwrite an earlier experiment.
If bound sources or recorded review/inspection activity change, make a new audit
version; do not rewrite this historical record.
