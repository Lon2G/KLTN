# Implementation Following the Advisor Feedback

## Direction Received

The user supplied advisor feedback distinguishing erroneous observations from
genuine unusual observations, recommending a note about exclusion, a build/test
split of 80/20, and allowing retrospective analysis with the keyword near
real-time for the event-replay direction. This records the supplied direction,
not independent authentication or approval of every engineering choice below.

## Implemented in This Version

Separate source-based analytical eligibility, quality assessment, atypicality
assessment and proposed use action. Preserve actual observations and missing
values. Every human field initially remains unassessed/pending. Confirmed claims
need declared verification. All actual human proposals are stored separately
with revision history; none automatically filters training or test data.

This is an analyst-facing extension, not the blind 120-case evaluation packet.
It does not require cross-validation or multiple reviewers to run. The protected
pilot, original blank criteria and old reviews remain unchanged; detailed
evaluation labels must never be generated from the analyst's model output.

## Next: Evaluation Design

The requested outer design is 80% development/build and 20% final evaluation,
with calibration/model selection inside development. Keep chronological order
for recorded fulfillment data; define rounding and purchase-timestamp tie rules
in advance. Split membership and availability at each boundary are different
checks. For each model, report full cohort and eligible denominators separately.

The saved 9,024-order experiment currently has 5,480 train, 1,853 validation and
1,691 reserved test orders: combined development 81.261%, test 18.739%. This is
not exact 80/20. THIS VERSION DOES NOT REASSIGN ANY ORDER OR SCORE TEST FEATURES.

A strict 80/20 run needs a new versioned design and an exposure audit. Do not move
previously explored validation cases into a new test and call them untouched.
Document earlier all-history exploration, pilot exposure and any old/new overlap.
Do not obtain a more favorable result by repeatedly changing test boundaries.
No resampling or new fit is performed here, and no exact split is claimed.

## Next: Reference Assessment

Use the two anomaly concepts as separate dimensions; do not equate legitimate
rarity with erroneous data or fraud. Define observable evidence, unresolved
cases, the scope of each judgment and any eventual binary metric mapping.
Normal/Suspicious/Anomaly in the old pilot is not silently relabeled. Preserve
its sampling weights; this analyst population is not a replacement pilot.

Record who actually assessed cases. One reviewer is technically possible, but
that does not create independent truth or erase review uncertainty. A larger
dataset and an 80/20 split do not supply missing reference labels. Do not treat
arbitrary row ordering as authorization to fabricate random labels. No criterion
or reviewer approval is invented. Forecast MAE and interval diagnostics remain
separate from anomaly-classification correctness.

## Next: Near Real-Time

Retrospective payment/item snapshots stay retrospective. A separate replay can
consume actual recorded milestones in order, distinguish event time from replay
processing time, and measure processing latency, throughput and eligible coverage.
Missing payment/record-arrival/cancellation timestamps must not be manufactured.
Replay and operational deployment are different claims. Existing batch/forecast
replay checks do not prove a deployed near-real-time or multi-agent service.

## Not Claimed

No synthetic data, human judgments, fraud findings, calibrated anomaly
probabilities, operational winner, final-test accuracy, new 80/20 split, BI UI,
multi-agent service, live connector or measured near-real-time deployment is
produced by this data-disposition step.
