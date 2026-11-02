# Seller Delivery Ablation

## Question and Declared Design

Does adding strictly prior seller activity and outcome context change delivery
duration prediction error relative to a geographic model on the SAME orders?
This is a new exploratory experiment on already used validation data, not an
independent replication, causal effect, seller ranking or anomaly-accuracy test.

Three profiles share the existing geographic numeric context and categorical
customer/seller states:

1. `base_geo`: geographic context only, freshly refitted on the common subset.
2. `plus_activity`: add prior scoped order count, prior 30-day count and recency.
3. `plus_seller`: add activity plus prior promise-comparable delivery count,
   fraction delivered after the recorded promise, prior reviewed-order count,
   and fractions of 1-, 2-, 3- and 4-star reviews.

The fifth review fraction is omitted as a redundant reference because all five
sum to one. The original five fractions remain in the history snapshot. No
low-rating cutoff is selected. Support counts accompany fractions so a small
denominator is represented rather than hidden. No rate is an anomaly probability.
The mean score is not added again because it is determined by the fractions.

All profiles test depth 2/3 crossed with minimum leaf size 30/60, using the
existing 150-tree, learning-rate .05, seed-42 quantile boosting setup at
Q05/Q50/Q95. Thus there are 12 configurations and 36 newly fitted regressors.
No adaptive threshold search or final winner selection occurs.

## Common Population and Missing Values

Preserve every development order in `analysis_ledger.csv`. Fit, calibration and
validation retain the exact original roles and event-time cutoffs. The original
training-only provisional ZIP-quality screen is reused, not re-estimated.

Every profile requires the SAME single-seller, geography/context-eligible rows
with finite, nonnegative activity features and at least one usable prior
delivery and one usable prior review. Missing rates/recency are not filled with
zero, a median or a model estimate. No minimum history count is claimed as a
statistical guarantee. Prior evidence can be sparse and source coverage is
limited to this selected category/development period, not lifetime seller data.

The baseline is retrained on this identical subset. Comparing its new MAE
directly against an older model fitted/evaluated on a larger population would
confound feature and population changes. Multi-seller and insufficient-history
orders remain in the ledger with explicit reasons; they are not labeled normal
or anomalous. Existing fallback predictions in earlier snapshots are untouched.

## Time and Leakage Boundaries

Seller features are frozen at each current order's purchase time. All contributing
historical purchases, deliveries and review answers must be strictly earlier.
The delivery prediction retains the parent's handoff-stage numeric context, so
this is NOT purchase-time delivery prediction. History is deliberately not
updated between purchase and carrier handoff in this version.

Earlier validation events may inform later validation history after their recorded
event times; model weights are not refitted on validation. This is chronological
history reconstruction, not a frozen-history-at-train-end experiment. Source
arrival/version times for seller assignments, estimates, item records and reviews
remain unknown, so true online availability has not been established.

Current order delivery outcome, current rating, final status, seller ID and order
ID are never predictors. All encoders/estimators fit only on common fit rows.
Additive lower/upper residual corrections use only the common calibration rows,
with the same constraints as the parent. These corrections are not a guarantee
of future interval coverage or calibrated anomaly probabilities.

## Complete-Case and Fixed-Horizon Evaluation

Primary complete-case diagnostics report MAE of the predicted median, quantile
loss, interval coverage/width and interval score, plus purchase-month/context
slices. Coverage is not anomaly classification accuracy. All four configurations
and all three profiles are retained, including worse outcomes.

For each augmented profile, compare exactly paired orders against the matching
depth/leaf baseline. Negative augmented-minus-base MAE means lower observed
error on that declared cohort. No p-value, confidence claim or winner is produced.
Repeated validation reuse and multiple comparisons remain limitations.

Also forecast every validation order with valid stage/geography and the same
complete single-seller history, even when its eventual delivery is unavailable.
Keep all 1,853 orders in the forecast ledger. Replaying the completed-case
validation forecasts must reproduce exactly the new models' values.

Reuse the parent's 14/30/45-day maturity masks and TWO separate evidence policies.
`horizon_only` is primary; post-horizon delivery evidence does not supply its
target. `cutoff_verified_sensitivity` can use a later delivery recorded before
the original cutoff to verify the capped target. The latter has unequal extra
ascertainment time and must not replace the primary comparison silently.

The capped target/prediction are min(duration,H) and min(predicted median,H),
NOT the full-duration target. Missing outcomes stay missing. Individual absolute
error bounds are [0,max(p,H-p)]. For paired models sharing an unresolved outcome,
the difference bounds are [-abs(p_aug-p_base),abs(p_aug-p_base)]. Average these
with known errors over ALL scored mature orders. These are logical bounds, not
confidence intervals or invented outcomes. Empty groups have missing metrics.
May has no mature 30/45-day groups under the original exclusive June 1 cutoff.

The parent stage-quality audit can consult later pre-cutoff chronology. Therefore
the horizon-only policy restricts target evidence, not a fully online replay of
eligibility. Completed-case training and complete-history selection remain even
when the validation forecasting population is expanded.

## Artifacts and Preservation

The snapshot contains the full selection ledger, outcome-free forecast inputs,
explicit feature contract, source-bound model bundle/training plan, calibration
offsets, predictions, paired metrics, monthly/subgroup diagnostics, replay checks,
fixed-horizon scores and missing-outcome bounds. CSVs must round-trip exactly;
the newly saved trusted local model bundle must reproduce forecasts exactly.
Only trusted locally generated joblib files may be deserialized.

Raw data, previous snapshots/models/thresholds, final test and human review files
are unchanged. No synthetic data, human labels, cancellation events or seller
fault labels are generated. The reserved 1,691 test orders are not scored. A new
model is not automatically substituted into any previous operational entry point.

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/experiment_seller_delivery.py
.venv/bin/python -m unittest discover -s tests -p 'test_seller_delivery.py' -v
```

The default destination is `data/experiments/olist_seller_delivery_v1`. Existing
destinations are refused. Use a new version and documented protocol for changes.
