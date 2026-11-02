# Reviewer Guide

This folder contains real Olist observations for the original 120 blinded cases.
Read `criteria.json` first. The protocol is a technical pilot draft; your notes
are provisional assessments, not approved ground truth or fraud judgments.

For each case, review process/timing, payment and order contents separately.
Use only the evidence in `reviewer/` and independently documented verification
or comparison references. Do not consult model scores, analyst keys, case
selection strata or model-selected thresholds before making your assessment.

1. Inspect raw milestones, payment rows and item rows. A blank means unavailable,
   not zero. Payment rows are not failed attempts; installments are not rows.
2. Check all applicable rules in the scope. Chronology inversions or unmatched
   money sums are observations; do not invent their causes. Duration, value,
   multiple sellers and rare payment methods alone do not prove invalidity.
3. When evidence is insufficient, choose `review_status=unresolved`, leave parent
   and subtype empty, explain `missing_evidence`, and keep `proposed_use_action=pending`.
4. A completed `no_supported_anomaly` assessment is limited to its named scope.
   It requires observed mandatory fields, all scope checklist codes and a
   declared comparison reference. It does
   not imply every aspect of the order is normal or that fraud was ruled out.
5. A completed `anomaly` needs a verification reference. Use `data_error` only for
   a documented invalidity, `valid_deviation` only with separate legitimacy and
   comparison support, or `unclassified` when anomaly is supported but subtype
   evidence is missing. Never copy the detector's decision as the answer.
6. Cite exact `table.column` names from the packet. Record your reasoning and
   low/medium/high confidence; this is not an anomaly probability.
7. Keep/exclude decisions are separate scope-specific proposals. Valid deviation
   does not mean delete; raw records are never removed by this workflow.

Use the program to check and record an explicitly supplied note. It timestamps
the recorded revision and requires your reviewer ID. Do not edit the frozen
source CSVs, the original blank criteria or `review_tasks.json` in place.
Corrections are new journal entries, not replacements of earlier notes.

No reviewer has been assigned and no assessment is prefilled by preparation.
The packet lacks geographic and prior-customer/seller evidence, so it cannot
establish context-adjusted bottlenecks or those behavioral anomaly types.
The source is retrospective, not a verified point-in-time operational record.
