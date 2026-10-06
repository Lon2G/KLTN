# Fixed-Horizon Delivery Evaluation

## Question and Scope

Compare frozen delivery forecasts after granting each evaluated order the same
14, 30 or 45 days of follow-up from recorded carrier handoff. These horizons
are sensitivity windows, not SLA or learned anomaly thresholds. This is a new,
post-hoc evaluation target and cannot be directly compared with previous full
delivery-duration MAE. It does not select an operational winner.

All 1,853 validation purchases remain in the input ledger. Use the previous
geographic screen and the same 11 paired candidates and two fallback models.
No final-test features, human labels or reviewer sessions are read. No fitting,
threshold recalibration, synthetic orders or source corrections are performed.

## Frozen Prediction Contract

Load only the existing trusted local model bundle after checking source/output
hashes and the original runtime versions. Check model identity, protocol,
training plan, fit/calibration IDs, features and saved calibration offsets.
Never accept a different marketplace's model or an untrusted pickle/joblib
file. See [scikit-learn model persistence](https://scikit-learn.org/stable/model_persistence.html).

Forecast inputs require a carrier event before the imposed cutoff and valid
visible carrier/delivery chronology. Contextual models additionally require
purchase <= approval <= carrier, observed finite nonnegative context features
and the previously screened geography where applicable. Earlier durations are
checked against real timestamps; existing numeric values are retained exactly
to reproduce the frozen forecasts. No final status or delivery duration is a
predictor. Rows missing numeric context receive only the global fallback;
rows lacking eligible geography can receive the no-distance fallback as well.
Fallback populations can differ and must not be treated as a paired comparison.

Predictions do not require an observed delivery outcome or original completed-
case eligibility. New forecasts for formerly unscored orders are kept separately
from old evaluations. Every previously saved validation forecast is replayed
and must match exactly. The in-memory bundle hash must remain unchanged.
Models were trained on completed cases; extending scoring does not remove that
training-selection limitation. Item/address snapshots have unknown version and
arrival times, so this is retrospective event-time reconstruction, not validated
real-time operation.

## Equal Follow-up and Evidence

An order enters horizon H only if carrier + H < the original exclusive cutoff
2018-06-01. This rule applies even to quick deliveries. Immature orders stay
visible but are excluded from that horizon's metrics. Preserve the prior stage
chronology audit. No recorded event at or after cutoff is used as an outcome.
The imposed cutoff is not an authoritative source-extraction timestamp.
Eligibility inherits retrospective chronology checks through that cutoff,
which can use a delivery recorded after H. `horizon_only` limits outcome
evidence for scoring; it is not a full online replay of eligibility at H.

Primary evidence policy `horizon_only`: use a delivery timestamp only if it is
recorded within H days. Otherwise the outcome is unresolved, not labeled late,
undelivered, normal or anomalous. Primary output does not expose a later exact
delivery date as a horizon-visible event.

Separate policy `cutoff_verified_sensitivity`: a recorded delivery after H but
before the existing cutoff can verify that capped duration equals H. Missing
delivery evidence remains unresolved. This policy has different ascertainment
time across purchase cohorts; it is a sensitivity analysis, not the primary
equal-evidence comparison. A duration cap is a mathematical transformation of
an observed duration, not an invented delivery date. No missing duration is
filled with H, zero, a model output or a negative label.

## Metrics and Bounds

For horizon H, target Y_H = min(recorded delivery duration, H), when supported
by the declared evidence policy. Predict p_H = min(frozen median forecast, H).
Do not interpret this as learning an H-day anomaly threshold or predicting an
anomaly probability. The forecast intervals remain frozen and are retained for
audit; capped interval coverage is deliberately not used to claim calibration.

For known outcomes, absolute error = abs(Y_H - p_H). Report this conditional
MAE together with known, unresolved and total scored counts. Never silently
discard unresolved orders from the overall denominator.

For an unresolved outcome, only the bound 0 <= Y_H <= H is assumed. Its error
lies in [0, max(p_H, H-p_H)]. Summing known errors and these bounds, then dividing
by ALL scored mature orders, yields lower/upper bounds on cohort capped MAE.
These are logical missing-outcome bounds, NOT confidence intervals and NOT
invented outcome values. Zero is a bound, not an assigned observed error.

For paired geographic/no-distance models, compare the same order IDs. Known
error difference is abs(Y_H-p_geo) - abs(Y_H-p_no). On an unresolved order its
bounds are [-abs(p_geo-p_no), +abs(p_geo-p_no)]. Average with all paired orders
in the denominator. This uses a common unknown outcome for the two models,
not independent imputation. Bounds overlapping zero are inconclusive about
which has lower mean capped error on that cohort. Bounds exclude sampling
uncertainty, other markets and anomaly-label accuracy.

All four existing ML configurations, all three horizons and every validation
purchase month are reported. Empty groups have zero counts and missing metrics,
not zero error. The >=30 reporting marker is not a statistical power guarantee.
No method is selected by these exploratory comparisons.

## How to Read the Added Forecasts

Previously unscored orders are actual validation purchases, not new data or
newly confirmed deliveries. Eligibility for a forecast and availability of
its eventual outcome are separate checks. A global fallback can score a valid
carrier-to-delivery stage even when earlier approval chronology is unsuitable
for the contextual model. Keep those populations separate in interpretation.
The focal model in the generated summary is inherited from the prior diagnostic
discussion, not selected or preregistered as the best model for this experiment.

## Reproduce

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/evaluate_fixed_horizon_delivery.py
```

Requires the actual contextual experiment, diagnostics and their source
snapshots. Publishes a new immutable directory with exact prediction inputs,
outcome availability, frozen predictions, replay checks, case-level error
bounds, monthly metrics and a hashed manifest. Does not alter old models,
data, thresholds, labels or the blinded review pilot.
