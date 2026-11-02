# Decisions Requested Before Human Review

These are unresolved questions, not proposed answers already treated as approval.

1. What is the reference target: an unusual observation, an operational problem,
   or two separate judgments? Confirm that no verified fraud claim is intended.
2. What observable evidence distinguishes Normal, Suspicious and Anomaly for
   timing, transaction value, payment structure and order contents? How should
   legitimate high-value orders, many installments and short nonnegative
   durations be treated without using model thresholds as the answer key?
3. How should missing or insufficient evidence be recorded: leave unanswered,
   separate status, or an approved class? The present CLI has three labels and
   skip; a separate class would require an explicit interface/protocol revision.
4. Who reviews, how many independent reviewers are feasible, and who adjudicates
   disagreements? Confirm how independence, blinding and revisions are recorded.
5. May the existing stratified 120-case set be used as a WORKFLOW PILOT? It was
   sampled from 1,522 eligible validation orders with unequal inclusion
   probabilities. What resources/design are appropriate for the later evaluation?
6. Should this pilot remain restricted to its original process/payment/item
   evidence? Seller/geographic validation needs an explicitly revised evidence
   packet and cannot be claimed from this unchanged pilot automatically.
7. May the manuscript distinguish retrospective snapshot analysis, event-time
   replay and a genuinely live system? Payment/item record-arrival times and
   cancellation event times are unavailable in the imported Olist tables.

After agreement, preserve the actual approval reference and create a separate
versioned criteria file. Do not overwrite the frozen draft or manufacture an
approved protocol as a software example. No email is sent by this script.
