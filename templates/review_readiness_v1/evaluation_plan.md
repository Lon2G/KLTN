# Proposed Evaluation Workflow, Pending Agreement

## Before Entry

Freeze the target, evidence rules, missing-evidence policy, reviewer roles and
adjudication procedure. Preserve the approval reference. Run the evidence hash
and schema checks. Share only `reviewer_packet/`, never the analyst mapping.
The coordinator must not supply model flags as hints. Pilot case counts alone
do not establish adequate statistical power or representative class prevalence.

## Independent Review

Use the original canonical pilot and existing `review_business_cases.py` entry
flow only after genuine approval. Each reviewer has a separate ID/session bound
to the exact evidence and criteria hashes. Record an explicit decision with
evidence/reason, ordinal confidence and audit timestamp. Skip uncertain cases
under the agreed policy; never silently impute a missing judgment.

If multiple reviewers are available, preserve their independent decisions before
adjudication. Report disagreement and unresolved cases rather than retaining only
agreement. Any consensus or corrected judgment must have separate provenance;
do not replace the original human records. Nothing in this preparation performs
human review, authenticates approval, or computes agreement/accuracy in advance.

## Analysis After Review

Only the analyst joins review IDs back to order IDs/model outputs. Report actual
response counts by stratum and class, missing/uncertain judgments and the model
coverage denominator. The 120 cases have unequal inclusion probabilities:
preserve their original sampling weights. A naive unweighted fraction cannot be
reported as population accuracy. Weighting does not fix nonresponse, ambiguous
targets, reviewer bias or the restricted sampling population.

The original eligible population is the common completed validation cohort minus
known previously reviewed cases. It excludes incomplete/unscored orders and does
not represent all Olist orders or other marketplaces. Broader seller/geographic
claims need agreed evidence and coverage; integrated model availability is not
an evaluation label. No new model was selected by creating the analyst bridge.

Report detection metrics only for agreed class mappings and genuinely reviewed
outcomes. Keep operational metrics (MAE, intervals, alert workload, coverage)
separate from classification correctness. Rule-generated labels, customer
ratings and model-to-model agreement are not independent ground truth.

## Final Evaluation

The repeatedly explored validation period is not an independent new benchmark.
Freeze feature/model/threshold selection and the evaluation protocol before final
test use. Do not tune on final-test results or claim those historical periods were
never inspected during earlier exploration. Any final reviewed sample requires
its own recorded design and sufficient support, not reuse of the pilot as both
selection data and final proof. Until then, keep claims descriptive and scoped.
