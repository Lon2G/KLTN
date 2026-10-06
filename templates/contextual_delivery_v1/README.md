# Contextual Delivery Experiment

## Purpose

Retrospective comparison of carrier-handoff-to-customer duration references for
the frozen Olist bed_bath_table development cohort. Real imported observations
only. No artificial orders, coordinates, timestamps, labels or approvals.
This extends order/process and geographic analysis, not the entire six-group
anomaly framework. It does not implement a real-time or multi-agent service.

## Geographic Eligibility

The existing geographic snapshot remains unchanged and unverified. This new
experiment applies an explicit, provisional screen, not a correction of source
coordinates. A ZIP requires at least two unique valid points, one source state,
no invalid coordinates and no points outside a deliberately broad envelope
latitude [-35, 7], longitude [-75, -25]. This rounded enclosure is a coarse
sanity check motivated by [IBGE territorial extent](https://anuario.ibge.gov.br/2024/territorio/posicao-e-extensao.html),
not a country/state polygon or address verification.

For each of p90 spread, maximum spread and representative sensitivity, the
upper screen is expm1(Q75(log1p(x)) + 3 * IQR(log1p(x))). Reference ZIPs are
counted once each and must be linked to inner-fit orders and pass structural
checks. Calibration and validation outcomes do not fit this screen. Both
endpoints must match the actual customer/seller state and every seller pair
must pass before an order can use distance. A screen failure means uncertain
geographic support, not a proven bad coordinate or anomalous transaction.
Rural/large ZIPs may be excluded more often. Passing does not establish correct
locations. Static coordinates have no observation/arrival dates.

## Design and Formulas

Use the existing purchase-time splits and event cutoffs. Fit before 2017-12-01,
calibration purchases 2017-12-01 through February 2018, validation purchases
March through May 2018. Outcomes must be observed before their cutoffs.
Previously deferred cases remain deferred. Incomplete/reversed/out-of-window
cases are retained but not forced into a duration target. This completed-case
design has censoring/selection limitations and cannot estimate ongoing orders.
Reserved final-test features and human review labels are not read.

All paired candidates share the same fit/calibration/validation orders that
have eligible geography and complete context. Comparators:

- Legacy-style duration reference: fit median, raw Q01 and Q75+1.5IQR.
  It is not a nominal 90% interval and is not recalibrated. The same formula
  is refitted on the paired population, not a replay of the old frozen values.
- Global fit Q05/median/Q95 reference.
- Peer reference: customer state + distance band, then distance band, then
  global fallback. Require at least 50 actual fit orders per non-global group.
  Bands [0,50,200,500,1000,2000,infinity) are comparison bins, not anomaly limits.
- Quantile gradient boosting with and without geographic predictors. Grid:
  depth 2/3, leaf minimum 30/60, 150 trees, learning rate .05, seed 42.
  Three regressors per configuration: Q05, Q50, Q95.

No-distance context: observed item count, seller count, item-price sum, freight
sum, earlier approval/handling durations, and handoff month/weekday. Geographic
context additionally uses maximum and item-weighted mean distances, customer
state and the sorted set of actual seller states. Categorical encoders fit on
fit cases only and ignore unseen categories (recorded explicitly). Missing
numeric predictors are never imputed. Source amounts remain in original units.
Items/addresses are retrospective snapshots: their arrival/version history is
unknown, so an as-of production claim is not supported. Outcome timestamps,
reviews, final status, identifiers and target duration are not predictors.

Quantile predictions are monotonically rearranged then constrained nonnegative.
For nonlegacy methods, separate lower/upper additive residual quantiles are
learned on calibration cases: Q05(y-lower), Q95(y-upper). Calibrated endpoints
are constrained around the unchanged median and lower bound zero. Record raw
and calibrated intervals, crossing counts and calibration shifts. This is
empirical calibration, not a distribution-free guarantee under temporal drift.
Nominal central coverage is 90%, not a 90% anomaly probability or SLA.

Flag long only when y > upper, short only when 0 < y < lower, and zero
separately. All flags are warnings, not labels. A farther order is not
automatically anomalous. Promised-date lateness remains a separate descriptive
outcome, not the target or predictor.

## Evaluation and Fallback

Report validation MAE, Q05/Q50/Q95 pinball losses (not assigned to legacy IQR
endpoints), coverage, interval width, lower/upper misses and interval score.
Coverage is reported by month, distance band and multi-seller status, with
sample counts. Calibration metrics are in-sample calibration diagnostics.
Do not compare the legacy interval as if its endpoints were Q05/Q95.

For geographic-screen failures, fit a separate fixed depth-2/leaf-30
no-distance model on all context-eligible fit cases and calibrate on all
context-eligible calibration cases. Also provide a global quantile fallback,
including for cases missing numeric context. Evaluate fallback cases separately,
not as an equivalent population to the paired comparison. No operational
winner is selected by this study. Predictive quality is not anomaly accuracy.

Regional/monthly warning summaries include counts and denominators. Seller
summaries include only single-seller orders, with multi-seller exclusions
explicit; no order-level outcome is attributed to every seller. Small groups
remain visible with an insufficient-support marker. Repeated long warnings
suggest where to investigate; neither queue bottlenecks nor responsibility or
causation are proven. No human labels are generated or changed.

## Reproduction

From the repository root:

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/experiment_contextual_delivery.py
```

Requires existing real local snapshots; never downloads or substitutes demo
data. Writes a new immutable experiment directory with typed CSV evidence,
quality thresholds, model bundle, predictions, metrics and a hashed manifest.
Existing data, snapshots, models, thresholds and review pilot remain untouched.
Joblib files are local trusted artifacts only; do not load arbitrary bundles.

Method reference: [scikit-learn quantile gradient boosting](https://scikit-learn.org/stable/auto_examples/ensemble/plot_gradient_boosting_quantile.html).
Only its estimator API is used, never the documentation's synthetic dataset.
