# Review Readiness, Not Review Completion

This package prepares the original real 120-order business review pilot for a
human-approved review workflow. It does not create new labels, approvals or cases.
The original evidence and draft criteria are unchanged. The current status is
PREPARED, NOT AUTHORIZED FOR LABEL ENTRY.

## Contents and Access

- `reviewer_packet/`: the only folder intended for blinded reviewers. It contains
  a byte-identical copy of the original reviewer evidence and a read-only guide.
- `analyst_only/review_case_bridge.csv`: original selection key, inclusion
  probabilities and mapping to the integrated process/model evidence. NEVER
  distribute this table or the integrated snapshot to blinded reviewers.
- `analyst_only/sampling_strata.csv`: the frozen sampling design, unchanged.
- `analyst_only/readiness_checks.csv`: verified technical prerequisites and
  explicit unresolved human protocol decisions.
- `advisor_questions.md`: concrete decisions to confirm with the advisor.
- `evaluation_plan.md`: proposed workflow and safeguards, not approved criteria.
- `summary.json`, `manifest.json`: counts, source/code bindings and output hashes.

The preview copy is not a second active review batch. Future approved entry uses
the canonical `data/manual_review/business_review_pilot_v1` batch and its existing
per-reviewer session mechanism. This preserves review exposure tracking and one
auditable decision history. No session is started by this preparation step.

The packet includes only the original timing/payment/item evidence. New seller
and geographic evidence is intentionally NOT inserted into the blind packet.
The pilot cannot validate broader seller/geographic causes just because its IDs
can be linked to those experiments. A new evidence protocol/version is needed
if the advisor expands the review task.

The draft criteria remain blank. The existing CLI refuses draft approval. It
checks the completeness of a recorded approval declaration, not the authenticity
of an email. No model flag, order rating or trace fitness becomes ground truth.

## Run

```sh
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/prepare_review_readiness.py
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --status
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --preview BR0001
.venv/bin/python -m unittest discover -s tests -p 'test_review_readiness.py' -v
```

Existing outputs are refused. The preparation needs no additional imported data.
It needs actual advisor/reviewer decisions before labeling can begin.
