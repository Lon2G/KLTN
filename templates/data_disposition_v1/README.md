# Data Disposition: Evidence Is Not a Human Judgment

This extension implements the advisor's distinction between erroneous data and
genuine unusual observations, and a separate proposed decision about analytical
use. It does not infer either kind from a model flag. This is analyst-side work
on actual imported Olist records, not a new independent labeling protocol.

## Separate Decisions

The immutable ledger has one row per accessible order and named analysis scope.
Five scopes cover process evidence, completed timing, the existing nine payment/
order features, screened geography and single-seller prior outcome context.
Counts across scopes overlap; they must not be summed as distinct orders.

`support_state` and `automatic_use_recommendation` describe observed availability
under explicit existing rules. `withheld` means unavailable for THAT analysis,
not a confirmed error, abnormal order or permission to delete source records.
Partial process traces remain evidence. The seller scope checks support for
prior delivery/review context, not the complete delivery model input contract.
Numerically available business features do not prove event-time availability.
None of these recommendations creates or filters a training matrix.

The three separate human fields start unassessed/pending on EVERY row:

- `data_quality_assessment`: not_assessed, insufficient_evidence,
  suspected_error, confirmed_error, no_error_identified.
- `atypicality_assessment`: not_assessed, insufficient_evidence,
  suspected_deviation, valid_deviation, no_supported_deviation.
- `proposed_use_action`: pending, retain, exclude_from_named_analysis.

No-error-identified is not proof of correctness. Valid-deviation requires an
explicit consistent quality assessment and a verification reference. A claimed
confirmed error likewise requires verification. Software checks completeness,
not whether the reviewer or reference is genuine or scientifically sufficient.
Never invent an event, cause, external verification or approving person.

An unusual legitimate case may be important research evidence. Excluding it
from a particular future reference fit is a separate protocol decision, not a
change to its validity and not a reason to erase it from evaluation. Notes
cannot override missing values, cutoff restrictions or geographic quality.
Preserve source records and earlier proposals. No change is automatically applied
to existing model inputs, model weights, thresholds, labels or metrics.

## Blinding and Research Limits

The existing inspector removes all 120 frozen blind-pilot orders from every
public source table. They are absent from this ledger and cannot be annotated
through this command. Final-test IDs also cannot be inspected. Use the original
blind-review workflow for that pilot; never feed this analyst ledger to judges.

Saved model signal codes are explicitly analyst evidence, not verified causes.
All notes here have the role `analyst_proposals_not_applied_not_independent_labels`.
They are NOT a source of ground truth for Precision/Recall/F1 and do not unlock
the original approved-criteria gate. No human review session is started by
preparing or inspecting a snapshot. Original review labels/criteria stay intact.

## Commands

Run from the repository root with the existing Python environment:

```sh
.venv/bin/python scripts/03_anomaly_detection/manage_data_disposition.py --prepare
.venv/bin/python scripts/03_anomaly_detection/manage_data_disposition.py --summary
.venv/bin/python scripts/03_anomaly_detection/manage_data_disposition.py --history
```

Use `--order-id` with a real accessible ID from `inspect_process_evidence.py`
to view its five scope rows. JSON output preserves exact source values and nulls.
Use `--snapshot` for a new explicit output version; existing outputs are refused.

The generated `note_template.json` is deliberately blank and not executable.
A human-supplied note must include every template field, a real accessible order,
one named scope, explicit assessments/action, reason and nonempty distinct
`supporting_fields` drawn from that scope's `source_fields_json` keys. A model
score cannot serve as verification. `verification_reference` may be empty only
when neither confirmed_error nor valid_deviation is claimed.

`--record-note PATH_TO_HUMAN_NOTE --reviewer-id REVIEWER_ID` records a supplied
note after checks; optional `--journal` selects a separate journal. The default
is `data/manual_review/analyst_disposition_notes_v1/journal.json`. The command
never invents a review. Reviewer IDs are human declarations, not authentication.
Prepare/summary/case lookup do not create this journal.
`--summary` describes the immutable initial ledger, not subsequent human work.
`--history` verifies and displays all actual journal revisions, without creating
a missing journal or choosing an automatic consensus among different reviewers.

Each save appends a revision with source values, a reviewer ID, an actual UTC
audit timestamp and a previous-record hash. Corrections append new revisions,
never replace an earlier assessment. Multiple reviewers remain separately
identifiable; no automatic consensus or last-writer ground truth is produced.
Writes use a lock, expected previous file hash and atomic replacement. Hashes
detect accidental modification, not a malicious rewrite of a whole hash chain.

## Artifacts

`analysis_use_ledger.csv` contains actual source references and availability;
`coverage.csv` counts by split/scope/support. `summary.json`, the blank template,
these documents and the manifest are immutable. Code/source hashes are verified
before consumption and publication. See `implementation_plan.md` for the scope
of feedback implemented here versus the separate 80/20 and near-real-time work.
