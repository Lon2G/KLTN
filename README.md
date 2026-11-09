# KLTN-Lon2G

Graduation thesis code for process-aware anomaly analysis in e-commerce order
fulfillment. The current experiments use real Olist data, with `bed_bath_table`
(`cama_mesa_banho`) as the main product category.

## Repository Scope

This repository contains source code, tests and configuration templates. Raw data,
processed snapshots, human review files, fitted models and generated reports are
kept locally and excluded from Git. No synthetic orders or reference labels are
provided as substitutes for the real research data.

## Structure

- `scripts/01_data_preparation/`: source audit, standardization, usage policy,
  category comparison, temporal cohorts, import contracts and business features.
- `scripts/02_process_mining/`: process discovery, conformance and performance analysis.
- `scripts/03_anomaly_detection/`: detectors, threshold experiments, model training,
  batch scoring, feature ablation, review preparation and human-review tools.
- `templates/`: import/training configurations, experiment protocols and blank review criteria.
- `tests/`: integration tests using real local observations and source-preservation checks.
- `project_paths.py`: local directory definitions.
- `requirements.txt`: Python dependencies.

## Execution Map

See [Code Lifecycle](docs/code_lifecycle.md) before choosing an entry point.
Current dataset-bound detection, supporting experiments, optional review tools
and historical baselines are separate workflows, not a sequence that runs every
script. Duration prediction supports contextual anomaly warnings; it does not
replace the process-aware anomaly-detection objective.

## Local Setup

Run from the project root:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

Obtain the Olist source data separately and place the original tables in `data/raw/`.
The repository does not download data or fabricate missing observations. The full
test suite also requires the local processed snapshots and prior experiment
artifacts; a fresh code-only checkout is not sufficient to run it successfully.

## Current Workflow

1. Audit and standardize raw tables while preserving observed values and missingness.
2. Define data-use eligibility, compare categories and form chronological partitions.
3. Import development data with explicit schema, identity and provenance checks.
4. Train and calibrate detectors on separate periods; compare validation results.
5. Compare timing-only, business-only and combined feature profiles on the same cases.
6. Prepare a blinded review pilot from real validation orders, with labels left blank.

Relevant entry points include `clean_olist_data.py`,
`prepare_bed_bath_table_experiment.py`, `import_process_data.py`,
`build_bed_bath_table_business_features.py`, `train_imported_process.py`,
`train_business_ablation.py`, `prepare_business_review.py` and
`review_business_cases.py`. Consult the corresponding files under `templates/`
for scope, prerequisites and configuration contracts. Snapshot-producing commands
refuse to overwrite existing versions.

The [confirmed fulfillment decisions](docs/fulfillment_evaluation_decisions_2026-10-16.md)
record the user's five agreed business rules, current implementation gaps and
the next evaluation steps. This decision record does not change frozen thresholds,
activate review labels or select a final model.

The [per-order performance explanations](docs/process_performance_explanation_2026-10-19.md)
add an unverified promise-basis status, signed stage deviations from frozen
train peer medians, and explicit early-delivery/insufficient-evidence summaries.
`explain_process_performance.py` preserves existing warnings and thresholds;
early delivery does not suppress a long-stage warning. See its
[output contract](templates/process_performance_explanation_v1/README.md) for
read-only inspection commands and the separate, versioned output directory.

## Validation And Limits

```sh
.venv/bin/python -m unittest discover -s tests -v
```

Software tests do not establish anomaly-detection accuracy. Model warnings are not
verified fraud labels, and anomaly scores are not calibrated probabilities. The
review pilot is provisional pending agreed criteria and human judgments. Current
business-feature experiments are retrospective, not a validated real-time system.
A model trained for one dataset is not claimed to work unchanged for another marketplace.

Retain the local versioned snapshots, manifests, environment information and review
history separately for reproducibility. Git is a code backup, not a backup of those
excluded research artifacts.

## Multi-Agent Analysis Integration

The [integration guide](docs/multi_agent_integration_2026-10-30.md) documents the
point-in-time evidence tools, declared peer comparisons, three-agent coordinator,
bounded verifier revision cycle and shared API budget. The offline pilot uses
20 real validation orders and preserves the frozen detector outputs. Technical
tests use explicitly marked provider fixtures; they do not establish AI quality.

The [first live one-case pilot](docs/multi_agent_live_pilot_2026-11-03.md) reached
the API but published no valid claims. Contract v2 now lets analysts select
evidence-backed claim IDs instead of inventing field names or copying numbers,
and rejects verifier decisions that contradict the code gate. Raw model outputs
remain separate from code-materialized facts. See the
[offline repair report](docs/multi_agent_contract_repair_2026-11-03.md).
The [v2 live retry on the same order](docs/multi_agent_live_v2_2026-11-03.md)
published six evidence-checked claims in six model requests. This is a one-case
engineering result, not independent accuracy or a general performance estimate.

The [five-case v2 live batch](docs/multi_agent_live_batch5_2026-11-04.md) completed
33 model requests for USD 0.009635015. All 28 scalar claims matched their sources,
but four cases included an unsupported missing-endpoint note. The current
v2 `evidence_checked` status therefore does not certify all explanation metadata.
Raw results and receipts are preserved. This trial does not establish detection
accuracy or causal validity.

The [v3 offline metadata repair](docs/multi_agent_metadata_repair_2026-11-04.md)
now requires evidence-bound uncertainty/check IDs and verifier decisions for all
items. Missing endpoints, timeline conflicts, unperformed comparisons, low sample
counts and low matched coverage have separate conditions. All 77 tests pass;
39 saved v2 responses across six cases were audited without new API calls or
rewriting history. The gates blocked the four known false endpoint notes in the offline replay.

The [offline comparison package](docs/agent_comparison_preparation_2026-11-04.md)
adds a deterministic rules control and tested single-agent/three-agent adapters
without editing the frozen v3 code. It prepares the same 20 real pilot cases under
native request caps (4 vs 12) and a shared four-request cap. There are 20 actual
rules outputs and 80 **pending**, not simulated, AI result slots. All 95 relevant
tests pass. Source checks are engineering checks, not independent anomaly
accuracy; review ratings and rankings remain empty. No new API requests were made.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/prepare_comparison.py --verify data/experiments/olist_agent_comparison_v1
.venv/bin/python -B -m unittest discover -s tests -p 'test_agent_comparison.py'
```

The [5 November live comparison](docs/agent_live_comparison_2026-11-05.md)
attempted two of those slots on one previously exposed development order under
native caps. Four model responses cost USD 0.002093985 in total. The next inputs
required 6,032 and 6,259 tokens against a 6,000-token limit. Both arms stopped
before final publication. Verify the receipts and reproduce both stops offline with
`.venv/bin/python -B scripts/06_agent_evaluation/live/audit_case2.py`.

The [offline evidence optimization](docs/agent_evidence_compaction_2026-11-05.md)
stores repeated evidence and shared provenance once within each agent request.
All six historical request payloads reconstruct exactly, and 540 evidence records
across 20 Olist orders pass round-trip checks. The two blocked payloads shrink by
4.53% and 4.93% in serialized bytes. The provider retains its 6,000-input-token
limit. Official token counts and model behavior on the new wire format remain
pending the next approved live trial. All 109 related tests pass.

The [compact live pilot](docs/agent_live_compact_2026-11-06.md) now completes the
three-agent path in six model calls, with 12 source-supported published items.
Its selected scalar facts omit the transport warning, yielding warning coverage
of 0/1. The single-agent arm stops at a newly generated 6,050-token request.
Total recorded inference cost is USD 0.003790635. All 111 related tests pass.
Replay and verify the saved experiment offline with
`.venv/bin/python -B scripts/06_agent_evaluation/live/run_compact_pilot.py --verify`.

```sh
.venv/bin/python -B -m unittest discover -s tests -p 'test_multi_agent*.py' -v
.venv/bin/python -B scripts/05_multi_agent/run_multi_agent.py --prepare
.venv/bin/python -B scripts/05_multi_agent/replay_saved_agent_outputs.py
```

Preparation refuses to overwrite its existing snapshot. Live model execution is
separate (`--run-api --case-index 0`), requires a locally configured API key and
shares the project budget ledger. This package does not establish causal
bottlenecks, independent detection accuracy or production near-real-time latency.

The default snapshot is now `data/experiments/olist_multi_agent_tools_v3` and the
default live run key is `multi-agent-pilot-v3`. The v1/v2 pilots, receipts and shared
ledger are preserved; v2 code is archived under
`data/experiments/olist_multi_agent_implementation_v2`. Old pilot manifests are
rejected by the v3 live runner. The v1 offline replay requires the original local v1 live
result and ledger, refuses HTTP access, and does not generate new AI responses.
An existing snapshot is inspected rather than overwritten when repeating checks.
The v3 metadata audit uses immutable saved receipts and rebuilds evidence for
six historical v2 cases; it does not generate new model selections. Its protection
check also detects any later change to the shared ledger. Use a new output folder
for a deliberate repeated offline export; do not rewrite historical manifests.
The CLI reads `OPENAI_API_KEY` from its process environment, not automatically
from `.env`. Do not paste credentials into commands, logs or the chat.

The [7 November offline review update](docs/agent_review_2026-11-07.md) adds a
versioned catalog-table wire format and a source-based warning completeness gate.
Incomplete explanations can receive one bounded repair; three-agent repairs need
another verifier response within the existing caps. All eight saved requests
round-trip exactly. The formerly blocked single-agent request shrinks from 22,254
to 20,800 UTF-8 bytes including the new instructions; its new official token count
and live completion are still unverified. All 125 relevant tests pass, with zero
HTTP requests and an unchanged budget ledger. Frozen live engines/results remain
preserved; the new adapter is `review_v2/runner.py:run_review`, not a silent change
to the old live CLI.

```sh
.venv/bin/python -B scripts/06_agent_evaluation/review_v2/validate_review.py --verify
```

See the [thesis writing readiness note](docs/thesis_readiness_2026-11-07.md) for
completed evidence, pending demo/evaluation work, research limitations, and the
proposed schedule to the user's 30 November submission deadline.

The [7 November live review trial](docs/agent_live_review_2026-11-07.md) has now
run the approved pair. Three agents finish in seven responses and explicitly
report the transport warning: 11 distinct source-supported items and 1/1 warning
coverage. The single-agent control receives two responses, then stops before
inference on a 6,024-token request after querying already available support data.
The nine actual responses cost USD 0.004518515; no retry or cap increase was made.
All 131 relevant tests pass. This one exposed development case does not establish
a general multi-agent advantage. Audit the exact receipts offline with:

```sh
.venv/bin/python -B scripts/06_agent_evaluation/live/run_review_pilot.py --verify
```
