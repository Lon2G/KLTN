# Olist Milestone Replay v1

## Scope

Engineering infrastructure for the approved event emitter and order-state manager.
No detector is fitted, tuned or called. No labels, causes, payments, reviews,
estimated dates or final order statuses enter the consumer. The original
validation cohort is reused for engineering checks, not independent model testing.

The original `bed_bath_table_v1` validation ledger supplies order IDs only.
Category selection is retrospective research scoping, not verified live category
discovery. Every validation order is retained, including incomplete or inconsistent
timelines. No completed-case filter is applied.

## Time and Data Contract

- Source: actual Olist purchase, approval, carrier handoff and delivery timestamps.
- Window: `[2018-03-01 00:00:00, 2018-06-01 00:00:00)` in the unspecified source
  clock. No timezone conversion or inferred extraction cutoff.
- Global order: recorded event time, order ID, then activity rank. Rank is a stable
  tie breaker, NOT evidence of causal order. Equal timestamps remain unresolved.
- Unpaced replay: logical time advances through recorded events without sleeping
  for the historical gaps. Events are assumed immediately available at their
  recorded occurrence time. Actual ingestion delays are unknown in Olist.
- At the fixed cutoff, stop without inventing a closing, missing, cancellation or
  delivery event. A not-observed event may be missing or simply later; the consumer
  cannot distinguish them and never receives future-event counts.
- `source_audit.json` is an offline provenance audit; it must NOT be supplied to a
  live consumer. It can describe the source beyond the replay window.

The producer verifies frozen hashes, original cohort membership, and exact
agreement of visible milestones with both clean and raw order records. Published
events contain only schema version, event ID, order ID, activity and occurrence
time. Stable IDs hash that visible payload, not future source contents. Record
positions and full-file hashes remain in separate provenance artifacts.

## State Semantics

Each event updates only its order. States are created lazily at first observation.
The manager records observed milestones, unobserved milestones, observed sequence
inversions, equal-time pairs and three stage measurements:

1. Purchase to approval.
2. Approval to carrier handoff.
3. Carrier handoff to customer delivery.

Signed duration = recorded end minus recorded start, in seconds. Nonnegative
durations can also be expressed in days by dividing by 86,400. Negative evidence
is preserved as a conflict, with usable duration left null. Equal times retain
zero elapsed time but explicitly do not establish causal order. Missing endpoints
stay null. These are measurements, NOT anomaly scores or normal labels. A positive
stage duration can coexist with a conflict elsewhere in the observed timeline;
the later detector adapter must implement an explicit quality gate.

`highest_milestone_observed` is not final business status. Observed delivery alone
does not prove all earlier milestones were observed. `all_four_observed` counts
coverage and does not certify data validity. No causes are inferred here.

The promised-date basis remains unverified. The actual promised date is withheld
because its historical availability is not documented. This layer cannot classify
early/late deliveries; prior retrospective experiments still do that separately.

## Persistence and Failure Policy

SQLite stores the contract, an immutable event/state journal and current states.
A single transaction commits the event and updated state. Reopening the same
window and replaying already-consumed events is idempotent: no additional update
or count. A duplicate response refers to its ORIGINAL state-after snapshot, not
the latest state. Window/version mismatch, conflicting records and unknown fields
are rejected. `finish()` closes the window; duplicates remain harmless afterwards.

This is a single-writer, globally ordered local engine. A new event arriving behind
the committed ordering key is rejected, not silently inserted. Actual late-arrival
handling, watermarks, distributed brokers and concurrent consumers are not
implemented. A recorded chronology inversion is different: it is retained when
the underlying events arrive in recorded-time order. SQLite atomicity plus
idempotency is not a claim of exactly-once delivery across a network.

## Artifacts and Reproduction

```sh
.venv/bin/python -B scripts/04_streaming/replay_olist_events.py
.venv/bin/python -B scripts/04_streaming/replay_olist_events.py --verify
.venv/bin/python -B -m unittest discover -s tests -p test_event_replay.py -v
```

Default snapshot: `data/experiments/olist_event_replay_v1`. The builder refuses
overwrite and writes a staged directory before publishing it. To repeat a timing
run, use `--output data/experiments/olist_event_replay_repeat_v1`, a new direct
child of `data/experiments`. Do not manually change the frozen protocol.

| File | Meaning |
| --- | --- |
| events.jsonl | Allowlisted real events in replay order |
| event_lineage.csv | Raw/clean record positions and original timestamp field |
| journal.jsonl | Per-event sequence, full state-after and state fingerprint |
| states.jsonl | Final observed facts for each encountered order |
| state.sqlite | Durable transactional event journal and order states |
| order_states.csv | Human-readable coverage, observed quality and unknowns |
| stage_observations.csv | Three stage rows per order, including missing/conflicting evidence |
| processing_times.csv | Measured local apply-and-commit time per event |
| runtime.json | Instrumentation and explicit timing limitations |
| source_audit.json | Offline-only source hashes and population reconciliation |
| protocol.json | Fixed scope, boundaries, assumptions and nonclaims |
| summary.json | Semantic event/state counts and hashes |
| manifest.json | Code, protocol, source and output integrity hashes |

Verification checks hashes, re-reads real sources, replays in memory, and compares
every journal entry, state, lineage record and exported table with the saved
SQLite and JSON/CSV outputs. Semantic hashes exclude measured processing times.
SQLite file bytes and wall-clock instrumentation need not match between runs.

Latency covers validation, state derivation and synchronous SQLite commit on this
machine. Throughput covers the unpaced apply loop. Neither includes source loading,
final exports, anomaly models, agents, network or UI. These numbers cannot establish
production near-real-time responsiveness. No speedup claim is made from one run.

Tests use actual Olist records. Test-only timestamp/payload mutations and injected
exceptions exercise failure handling in memory or temporary databases; they are
never written into research snapshots or used as anomaly-label evidence.

## Next Approval Boundary

Propose a per-completed-stage adapter to the frozen reference and an explicit
policy for context availability before implementing it. Do not attach the old
completed-order detector directly: it depends on final delivery and full-timeline
quality gates. Ongoing-stage alerts require a separate definition and evaluation.
Multi-Agent roles, coordination, evidence checking, ablations and a local dashboard
remain subsequent work, not achieved by naming these two modules "agents".
