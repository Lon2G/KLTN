# Holdout process evidence v1

## Scope

Follow the advisor's distinction between abnormal processing time and potential
bottlenecks. Consume the frozen `olist_selected_holdout_v1` outputs without
changing scores, thresholds, references, partitions or the published metrics.
The 24 FN and 59 FP orders are disagreements with calendar lateness, not known
detector mistakes. Describe all 83, but compute coverage and contextual rates on
all 1,691 original test orders, with an explicit assessed denominator.

This is exploratory post-test analysis. No independent bottleneck labels,
accuracy claim, model selection, probability calibration or causal claim is
created. Any later tuning needs a new evaluation protocol; this test is exposed.
The source extraction cutoff and promised-date basis remain unverified.

## Stage evidence

Keep actual stage endpoints, duration, signed difference from the saved selected
reference mean, empirical percentile, original score and warning. Above-mean
duration alone is not an upper-tail warning. Reconstruct the frozen reference
membership to show the first observed value whose cumulative count exceeds 95%:
`sorted_values[(19*n)//20]`. A warning also requires duration > mean and > 0.
This describes the existing rule; it is not a new threshold or validation label.
Preserve warnings on early deliveries and missing/unassessed states.

## Reviews

Use all actual clean-source review rows linked to the original test order IDs,
not just comments on the 83 selected cases. Keep multiple reviews and original
text, score, dates, review ID and one-based clean-record locator. Do not collapse
reviews by global review ID, infer missing text, translate into fabricated
testimony, or assign any human-reviewed label.

Review timing is valid for retrospective summaries only when creation >=
purchase and answer >= creation, with both dates present. Retain invalid/missing
dates in source evidence. Keep separate flags for review before/after recorded
delivery; a pre-delivery nonreceipt comment is not automatically a contradiction.
Score 1-2 is a declared descriptive low-score convention, not a bottleneck label.

The Portuguese phrase screen is a transparent retrieval aid, not a trained NLP
model, validated complaint classifier or independent ground truth. It records
delivery-topic, delay, nonreceipt, early/on-time and negated-delay wording.
Keep mixed wording. Absence of a matched phrase is not absence of a complaint;
absence of review/text is unknown, not positive customer experience. Do not
derive new ROC AUC/precision/recall from these phrase hits. The phrase list was
written after inspecting a few actual comments, so it is exploratory, not a
preregistered evaluation instrument. Reviews never feed the frozen detector or
an earlier near-real-time decision.

## Context groups

Use the saved stage warnings, not calendar lateness, as the descriptive signal.
Report all groups and denominators, not only high-rate groups. Analyze overall,
customer state, observed stage-start month, distance band and single-seller ID.
Multi-seller orders remain in overall/geographic/monthly tables and are excluded
from per-seller attribution; neither duration nor review can be assigned to an
individual seller in a multi-seller order.

Reuse the existing exact-cell comparison and support guards: at least 5 assessed
orders per side of each cell, 30 per matched side, 80% target match coverage and
5 target warnings for an investigation candidate. These are reporting guards,
not tuned anomaly thresholds, significance tests or validated optimal settings.

- State: match distance band, seller attribution scope, stage-start month, items.
- Month: match customer state, distance band, seller attribution scope, items.
- Single seller: match customer state, distance band, stage-start month, items;
  compare only with other single-seller orders.
- Overall and distance-band views are descriptive only.

Use target-cell weights for comparator rates, exclude target IDs from controls,
and publish cell membership. No fallback pooling to force a result. Product mix,
carrier/service, route, operating capacity and actual disruption causes remain
uncontrolled. All month comparisons have unknown follow-up completeness; do not
claim mature equal follow-up. Reused controls/overlapping groups are not separate
confirmations. A positive matched difference with support is only a potential
concentration worth investigating. Reuse the existing stage-specific checklist
for required logs and conditional interventions, never assert a proven cause.

## Reproduction

```sh
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B scripts/03_anomaly_detection/analyze_holdout_process_evidence.py
env MPLCONFIGDIR=/tmp/kltn-matplotlib .venv/bin/python -B scripts/03_anomaly_detection/analyze_holdout_process_evidence.py --verify
```

Use a new output directory for another run. Source/output/code hashes bind each
snapshot. Outputs include all-order/stage evidence, all linked reviews, detailed
83-case files, review summaries, context groups/cells/membership, investigation
actions, an evaluation-claim contract and Vietnamese reports. CSV timestamps
include seconds; identifiers stay strings. No raw or previous snapshot is edited.
