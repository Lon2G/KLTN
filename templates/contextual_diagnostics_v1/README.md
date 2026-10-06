# Contextual Delivery Diagnostics

## Scope

Post-hoc descriptive investigation of the completed contextual-delivery
experiment. All 11 paired candidates and both fallback candidates are retained.
The focal configuration gbr_geographic_d2_l60 is the configuration already
discussed with the user after inspecting validation. It is not preregistered,
an independently selected winner or a verified anomaly detector.

Diagnostics are computed solely from the saved development ledger and
predictions. Source verification also validates upstream development records.
No model is deserialized, fitted, rescored or recalibrated. No final-test
features, human labels or review-session files are read. Existing snapshots are not changed.
This is a separate immutable diagnostic snapshot, not a thesis chapter.

## Error and Support Diagnostics

Signed error = observed carrier-to-delivery days - predicted median days.
Positive values mean the forecast underestimates the observed duration.
MAE = mean(abs(signed error)). Report observed and predicted distributions,
interval coverage, separate lower/upper misses, promise lateness with its
available denominator, and the share of absolute error in the largest 10%
of errors (ceil(0.1*n) actual cases, order ID as deterministic tie break).
This ranking is for analysts, not a random or blinded reference sample.

Primary monthly summaries use purchase cohorts. They do not describe all
deliveries occurring in that calendar month. Handoff-month slices also remain
restricted to those purchase cohorts. Historical business/address snapshots
have no arrival/version timestamps; all conclusions are retrospective.

Numeric min/max and Q01/Q99 references, state frequencies, seller-state-set
frequencies and customer-state/distance-band support are computed from the
same 3,039 paired fit cases, never from validation outcomes. These references
describe feature support; they are not new anomaly thresholds. Missing
distances stay missing and cannot become zero-distance observations.
The reporting support marker at 30 cases is a descriptive convenience, not a
power calculation or proof of statistical adequacy. The feature-effect table
compares geographic/no-distance predictions with identical configuration and
order IDs, reporting all four configurations, without causal attribution.

## Composition Comparison

Compare March purchase cohorts (A) with pooled April/May (B). Perform separate
descriptive decompositions by distance band, customer state and their joint
group. Require at least 20 observed orders in each period per included group;
report excluded counts and retained shares. This cutoff is exploratory, not a
statistical significance criterion. The results are conditional on common
groups and on orders already included in the completed-case comparison.

For each group g, p_Ag and p_Bg are normalized shares on common support and
m_Ag/m_Bg are group means of the measured quantity. Quantities are absolute
prediction error, signed error and actual delivery duration, not medians.

```
gap = sum(p_Ag * m_Ag) - sum(p_Bg * m_Bg)
composition = sum((p_Ag - p_Bg) * (m_Ag + m_Bg) / 2)
within_group = sum((m_Ag - m_Bg) * (p_Ag + p_Bg) / 2)
gap = composition + within_group
```

This arithmetic identity separates changing observed group proportions from
differences within those broad groups. It is not a causal decomposition,
significance test, correction for unobserved confounding, or adjustment for
all geographic/product/operational factors. Never claim that geography has
been fully controlled merely because broad group weights were standardized.

## Observation-Window Audit

Audit every validation purchase, not only the 1,472 paired completed cases.
Retain timing-ineligible and geographic-fallback cases and their reasons.
The event cutoff is the existing imposed 2018-06-01 analysis boundary, not a
known source-extraction time. A timestamp at the cutoff is not observed before
it. Later timestamps are masked before deriving visible stage observations.

Use fixed 14/30/45-day follow-up horizons from the observed carrier handoff.
These are sensitivity windows, not SLA or anomaly thresholds. A full window
requires handoff + horizon < the exclusive event cutoff, even for orders that
were delivered quickly. The same maturity condition applies to all orders,
so early completions cannot selectively enter an immature denominator.
Invalid visible stage chronology is reported separately. This stage audit
does not grant full-pipeline timing eligibility to an otherwise excluded order.

Within each mature window, distinguish a delivery recorded within the horizon,
a delivery recorded later but before cutoff, and no delivery recorded before
cutoff. The last state is NOT proof that the order was still in transit or
anomalous. Missing evidence is not an imputed duration or a negative label.
Keep original model-eligibility counts visible within the mature denominator.

An immature order has an unknown fixed-horizon outcome in this audit, even if
an early delivery is present. Late-month cohorts may have little or no mature
support at long horizons. Blank rates mean unavailable denominators, not zero.
This describes potential completion-selection bias; it does not remove that
bias from the earlier model metrics or establish a survival model.

## Review Status

No human or semi-manual labels are created here. The existing review criteria
remain a separate human approval workflow. Before anomaly-accuracy evaluation,
the user/advisor must define the target (rarity, operational deviation, or
both), evidence requirements, insufficient-evidence treatment, reviewers and
adjudication. No approval is inferred from a general suggestion to investigate
anomalies. Analyst error tables expose predictions and must not be presented
as a blinded reviewer packet. The existing 120-order pilot is not altered.

## Reproduce

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/diagnose_contextual_delivery.py
```

Requires the actual local parent snapshot and its original sources. Fails on
hash mismatch or an existing output directory. Publishes typed evidence,
summary, protocol, documentation and a manifest only after verification.
