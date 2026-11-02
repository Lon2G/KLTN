# Retrospective Seller History

## Scope and Purpose

This step extends the advisor's seller-behavior group with traceable historical
context. It is not a seller anomaly detector, fraud labeler, probability model
or proof that a seller caused a delay. It uses the frozen 7,333 Olist
bed_bath_table development orders and all their distinct seller links. The
1,691 reserved test orders are consulted only by ID/split for exclusion.
No existing data, model, threshold, human decision or review protocol is edited.

## Query Time and History

Every order-seller pair is queried at that order's purchase timestamp. Purchase
counts and recency use only the same seller's strictly earlier purchases in
this selected category/development scope. Simultaneous purchases do not count
as prior purchases. The 30-day activity count uses [query minus 30 days, query).
This is a descriptive window, not a learned anomaly threshold.

History is left-truncated at the beginning of the selected cohort and does not
represent a seller's lifetime or all-category activity. Zero prior orders means
no earlier observed order in this scope, not a seller newly joining Olist.
Later validation orders may use earlier validation outcomes only after their
recorded event times. This is expanding event history, not refitting a model.

Purchase counts include every prior linked order. Delivery and review outcome
statistics use only prior SINGLE-seller orders. Multi-seller orders remain in
source evidence and purchase counts but their order-level outcomes are not
assigned to each seller. Even a single-seller order's outcome is contextual
evidence: carrier, customer and other factors may contribute.

## Delivery Evidence

A prior order contributes to `prior_observed_completed_orders` only when all
four recorded milestones exist, are nondecreasing, and actual delivery is
strictly before the query time. Final snapshot status is not used as a
historical input. `prior_completion_history_unavailable_orders` counts the
remaining prior single-seller orders; it does NOT mean those orders were
undelivered, canceled, abnormal or overdue.

An order enters `prior_promise_comparable_orders` only when that completion is
usable and the recorded estimated delivery date is present and no earlier
than the purchase calendar date. Calendar delay is actual delivery DATE minus
estimated delivery DATE. Delivery later in the SAME date is not counted late
merely because the estimate is stored at midnight.

`prior_after_promise_fraction` = prior_after_promise_orders /
prior_promise_comparable_orders. A missing/invalid estimate is excluded from
both numerator and denominator, never treated as on time. The rate is
conditional on observed usable completions, not on all prior orders, and can
be biased by incomplete outcomes. Every support count is reported alongside
the rate. No support bin guarantees statistical adequacy.

No cancellation rate is calculated: the source has no cancellation-event
timestamp. Final canceled status is not assigned a guessed occurrence date.

## Review Evidence

Review evidence must have a score from 1 to 5 and recorded times satisfying
purchase <= review creation <= review answer < query time. Reviews need not
follow delivery, because feedback can precede completion. Invalid reviews are
retained as source evidence but not counted in historical review rates.

For each eligible prior single-seller order, select the latest answer visible
BEFORE that query time, not the latest answer in the entire final snapshot.
Thus a later answer cannot suppress a previously visible answer. Equal latest
timestamps with the same score count once, using the smallest source-record
index as deterministic evidence. Conflicting scores at the same latest time
are withheld and counted explicitly, not averaged or resolved arbitrarily.

`prior_reviewed_orders` counts distinct usable orders, not review rows. The
five `prior_review_score_N_orders` counts sum to this denominator; fractions
are each count divided by it. Mean score is the mean of those selected scores.
All five score categories are retained. No low-score cutoff or human anomaly
criterion is selected while the review criteria are pending.

## Outputs and Missing Values

- `seller_order_history.csv`: one row per actual order-seller pair; earlier
  purchase counts/recency, delivery and review denominators, rates, and latest
  contributing event times. These timestamps must be strictly before query.
- `order_seller_history.csv`: one row per actual order. All sellers remain
  represented by pair counts, min/max prior support and no-history counts.
  `single_seller_*` fields are populated only for current single-seller orders;
  multi-seller values are missing, not an average or an arbitrarily picked seller.
- `order_source_evidence.csv`, `review_source_evidence.csv`: minimal observed
  records and retrospective eligibility checks supporting recomputation.
  These audit tables contain later events and are NOT model input matrices.
- `coverage_by_month.csv`, `history_support_summary.csv`: coverage and exact
  denominators, not anomaly prevalence or seller rankings. The latter's 0,
  1-4, 5-19, 20-49 and 50+ bins describe support only.

No earlier observations means zero observed counts. Undefined rates, means,
recency and contributing timestamps stay missing. No fabricated records,
coordinates, dates, scores or replacement values are generated. All source
order IDs are preserved; no many-to-many join multiplies reviewed order counts.

## Availability Limits

Strict event-time filtering prevents recorded future events from entering the
earlier query, but it cannot establish when the source system received those
events. Seller assignments, estimated delivery dates and review values have
unknown arrival/revision histories. Promise comparisons use the recorded
estimate, not a verified original contractual promise. This is retrospective
event-time reconstruction, not proven online feature availability or real-time
operation. Final-status cancellation behavior and causal seller attribution
remain unavailable.

These features have not been added to or used to retrain existing detectors.
A subsequent version can compare the SAME eligible orders and frozen splits
with and without seller context. Accuracy claims still need independent
reference labels; rate magnitudes are not calibrated anomaly probabilities.

## Reproduce

```bash
.venv/bin/python scripts/01_data_preparation/build_seller_history.py
.venv/bin/python -m unittest discover -s tests -p 'test_seller_history.py' -v
```

The default output is `data/experiments/olist_seller_history_v1`. Existing
outputs are refused. Source/output/code hashes, logical schemas, runtime
versions, the protocol and exact CSV round trips bind each immutable snapshot.
