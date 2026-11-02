# Completed-Stage Event Scoring v1

## Purpose and Approval Scope

Connect the frozen `product_context_stage_score_gt0` rule to the real Olist event
replay. No refitting, threshold search, relabeling, new business events, LLM calls,
dashboard or Multi-Agent orchestration. The previous replay snapshot and all old
experiments remain unchanged. This is engineering integration, not a new claim of
independent anomaly accuracy or production near-real-time detection.

Original validation scope: 1,853 bed_bath_table orders and the 7,076 recorded
milestones in `[2018-03-01, 2018-06-01)`. Preserve both protected pilot IDs from
the frozen product snapshot: observe their events but abstain from scoring them.

## Availability Contract

The Olist item, customer, seller and ZIP-reference snapshots do not establish when
their values were known or updated. This implementation therefore always publishes
two separately named scenarios; their results must never be pooled:

1. `verified_availability_only`: do not use static context whose availability has
   not been verified. Completed stages remain unassessed for that reason.
2. `assumed_static_context_at_purchase`: engineering scenario assuming the actual
   snapshot product/item composition, seller association, customer state and static
   geographic map were available and unchanged at the observed purchase milestone.
   This is an explicit, unverified availability assumption, not an invented source
   timestamp. Historical reference context has the same snapshot limitation.

There are NO artificial context-arrival events or `available_at` timestamps. The
scorer accesses the context only after purchase has been observed, and labels every
assumption-based output accordingly. The fixed category cohort is still retrospective
research scoping; it is not live cohort discovery.

The projected context allowlist includes only order ID, product ID when attributable,
single seller ID when applicable, item/product counts, product-attribution flag,
customer state, distance band, seller-attribution scope and geographic-quality flag.
No final order status, promised/delivered date, future-event flag, payment or review
enters that projection. Existing static geometry/quality rules are reused without
fitting on validation. Missing/ineligible geography cannot fall back to a rule that
drops geography. No new order-level monetary or behavior detector is introduced.

## Event-Time Scoring

Use the unchanged event-time clock and ordered single-writer replay. The three
stage mappings are approval, preparation_handoff and transport. The observer calls
the last one `transport_delivery`; the adapter maps it explicitly to the existing
frozen detector's `transport` key.

After each event, reevaluate all three stage statuses for that order:

- Both observed endpoints and a positive duration are required. A future delivery
  is never required to score approval or preparation/handoff.
- The purchase milestone must already have been observed.
- Any inversion currently visible in the order's timestamps withholds all stage
  assessments under this version's conservative quality gate. Equal-time pairs
  are also unresolved, not normal labels. This gate does not certify business truth.
- Missing endpoints are unknown, not zero. A negative signed duration remains
  visible as source evidence, not a valid duration for the score.
- Strict mode abstains without verified context availability. Assumption mode
  proceeds only when the static geography and reference-support gates pass.
- Protected pilot IDs remain unscored in both modes.

The original category-distance eligibility gate, product-context fallback order,
minimum support 30 and `build_product_stage_scores.tail_score` kernel are retained.
Reference rows must be eligible train history, disjoint from replay IDs, with stage
endpoints strictly before the replay start. No validation durations enter the
reference. Exact group membership, mean, median, tail boundary and history IDs are
exported. Group indexes/cache only accelerate lookup; they do not alter membership.

Signed difference from the reference mean is independent of the tail-warning flag.
A duration above the mean can still have score zero. Score zero means no long-tail
warning under this rule, not a verified normal order. Scores are not probabilities.

For order summaries, take the maximum AVAILABLE stage score and report coverage.
Any observed stage warning can set the order attention flag true. False requires
all three stages to be scored without warnings; partial/no coverage without a
warning stays unknown. This avoids labeling unfinished orders normal.

## Persistence and Changed Evidence

`ScoredStateStore` extends the original transactional state-write hook. Raw event,
observed state, current stage results and changed-result journal all commit together
or roll back together. Already-seen event retries do not rescore or emit duplicates.
The scoring contract binds the reference, rules, adapter/kernel implementation,
contexts, modes and held IDs. It cannot be changed while resuming a checkpoint.
An existing nonempty unscored checkpoint cannot silently acquire retroactive scores.

Only changed stage results receive a journal entry. If a later observed event
reveals a chronology conflict, prior usable scores are withdrawn in current state
with `assessment_withdrawn_after_new_evidence`; the earlier entries remain. An
earlier warning was based on less information and must not be erased from history.

The raw `states`/`journal` tables preserve the old observer schema verbatim, including
its no-scoring marker. They describe the observation layer only. Authoritative
scoring results are in `stage_states`/`score_journal` and the exported score files;
do not treat the observer's legacy marker as the integrated scorer's status.

Source timestamps are assumed immediately observable for this ordered replay.
Late network arrivals, corrections to a recorded milestone, distributed consumers
and upstream ingestion latency are not implemented or validated. Reject unsupported
new out-of-order events instead of silently rewriting history.

## Numerical Checks and Limits

Compare every previously scored stage in `olist_product_stage_scores_v1` with the
new final assumption-scenario result on the shared eligible scope. Require exact
agreement of durations, reference IDs/scopes/support, means/medians, signed
differences, percentiles, scores, severity and flags before publishing a snapshot.
New coverage outside the old completed-case gate is reported separately; it is
not an accuracy improvement. Reference-group selection can be independently
reconstructed by filtering frozen train records.

Do not calculate AUC using these same flags as labels. This step creates no
confirmed-bottleneck labels, does not revise previous test metrics and does not
reselect the model. A warning before observing delivery is a completed-stage
signal, not a forecast of lateness or an alarm during an unfinished stage.
Promise performance and root-cause attribution remain outside the event scorer.

## Reproduction

```sh
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B scripts/04_streaming/run_stage_scoring_replay.py
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B scripts/04_streaming/run_stage_scoring_replay.py --verify
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B -m unittest discover -s tests -p test_event_stage_scoring.py -v
```

Default output: `data/experiments/olist_event_stage_scoring_v1`. No overwrite.
Another timing run requires a new output folder via `--output`. The old replay
builder/verifier remains usable and unchanged.

| Output | Meaning |
| --- | --- |
| stage_decisions.jsonl | Changed results, triggering real event, logical time and withdrawal history |
| latest_stage_scores.jsonl / stage_scores.csv | Latest three stage results per order and scenario |
| order_scores.csv | Score/attention with explicit complete, partial or unavailable coverage |
| static_context_scenario_inputs.jsonl | Allowlisted actual snapshot context; availability not verified |
| reference_groups.csv | Exact historical peers and descriptive boundaries |
| batch_parity.csv / parity_summary.json | Exact numerical regression against the frozen batch result |
| state.sqlite | Atomic raw-state and scoring checkpoint |
| processing_times.csv / runtime.json | Local execution instrumentation, not source ingestion timestamps |
| protocol.json / summary.json / manifest.json | Assumptions, counts, integrity and nonclaims |

The verifier reproduces the entire stream and scores in memory, compares decisions,
current results, exported tables and the SQLite scoring store, and requires the raw
observation state/journal to remain identical to the original replay. Runtime is
separate from semantic reproducibility. One local unpaced timing run includes both
scenarios and synchronous commits but excludes source loading, parity/export,
agents, network, dashboard and notifications. It is not end-to-end latency or a
validated capacity/SLA.

Tests use actual Olist records. Test-only corruptions, missing-context scenarios
and injected failures exist solely in memory/temporary databases, not experiment
outputs. No synthetic orders or labels are used for research evaluation.
