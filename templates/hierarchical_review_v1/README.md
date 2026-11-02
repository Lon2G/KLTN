# Hierarchical Review Protocol

This is the concrete technical draft following the user's chosen hierarchy:
anomaly -> data error / valid deviation. It prepares real blinded evidence and
allows explicitly attributed provisional notes. It does not authenticate advisor
approval, create reference truth, choose a model or score a final test.

The original 120-order pilot, blank criteria, pseudonyms and selection remain
unchanged. The reviewer packet is a byte-identical copy of its evidence. No
analyst key, model output, sampling stratum or new order mapping is read by this
implementation. The parent manifest binds the original reviewer evidence only;
sampling metadata remains in the original analyst-only pilot.

## Artifacts

- `reviewer_packet/criteria.json`: labels, scope checklist and evidence limits.
- `reviewer_packet/review_tasks.json`: three unassessed tasks per original case.
- `reviewer_packet/note_template.json`: blank structured input for one scope.
- `reviewer_packet/reviewer/`: unchanged source packet, including its old blank
  labels and pending criteria. Those old files are not an entry form.
- `reviewer_packet/README.md`: reviewer instructions.
- `evaluation_plan.json`: proposed metrics, sampling and selection workflow.
- `summary.json` and `manifest.json`: preparation counts and integrity hashes.

The three supported scopes are recorded process/timing, payment and order
contents. Geographic, customer and seller-behavior claims require evidence not
present in this pilot. Do not imply that this packet validates all six proposed
anomaly families or covers incomplete/unscored orders outside its source cohort.

## Commands

```sh
.venv/bin/python scripts/03_anomaly_detection/prepare_hierarchical_review.py --prepare
.venv/bin/python scripts/03_anomaly_detection/prepare_hierarchical_review.py --summary
.venv/bin/python scripts/03_anomaly_detection/prepare_hierarchical_review.py --preview BR0001
```

`--output` selects a new immutable package directory. `--check-note PATH` validates
a supplied note without writing. `--record-note PATH --reviewer-id NAME` validates
and appends a real supplied assessment to a separate journal; `--history` reads
the journal without creating one. `--journal PATH` selects a separate journal.
No command starts the legacy approved-review workflow or computes accuracy.

Every submitted note needs a known scope, evidence fields and checklist codes,
an explicit reason, human confidence and a compatible parent/subtype/use action.
Positive claims require verification references; valid deviations require a
separate comparison reference. References are declarations, not automatically
authenticated proof. A draft note, even with valid syntax, is not a final label.
No note is automatically copied into a training matrix or a test label file.

Journals are outside immutable packages, append-only by revision and SHA256
chain, bound to exact source/protocol hashes, and protected against stale writes.
Corrections append a new revision; no consensus or final label is inferred from
the last note. Recording notes changes review exposure and calls for a fresh
exposure audit before future evaluation. The 120 cases must not become both
model-selection evidence and purported independent final-test evidence.

## Next Step

Inspect this rubric and evidence, record actual reviewer observations, resolve
ambiguous rules and version the agreed reference protocol. Preserve unresolved
cases and do not invent a reason to turn them into normal or anomaly. Only then
use the appropriately scoped reference to compare declared models/thresholds.
The supplied feedback establishes direction, not a claim that this entire
engineering draft, sampling size or metric choice has already been approved.
