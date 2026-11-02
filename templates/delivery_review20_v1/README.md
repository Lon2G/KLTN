# Delivery Review 20 v1

Versioned subset of real original blind evidence for a user-requested 20-order
workflow pilot. The original 120-case packet and all historical protocols remain
unchanged. This is not an approved final reference or a new test split.

`reviewer_packet` contains source evidence, the inherited hierarchy/checklist,
an explicit early/on-date/late performance clarification, 60 blank scoped note
templates and a Vietnamese casebook. It contains no detector scores, flags,
agreement strata, selection probabilities or raw order-ID mapping. Original
product/seller source IDs remain as in the source packet; blinding is procedural,
not access control against joining other local tables.

`analyst_only` contains the reproducible selection audit. Never distribute it to
the reviewer. Ten cases have at least one saved sampling-anchor flag and ten have
none. That anchor is not a selected model or a truth label. Selection uses a
seeded hash within each group and preserves the original blind display order.
Exclude purchases and actual milestones on/after the proposed outer boundary.
All selected cases inherit the original completed-timing pilot eligibility;
missing/invalid cases in the full population are not represented by this pilot.

No new human labels, approval, exclusion decisions, model selection, training or
accuracy result are generated. Preparing the sample is not performing review.
The user has selected self-review and a 20-order pilot, not a fully validated
research reference. No fake evidence or second reviewer is supplied.

Run `.venv/bin/python scripts/03_anomaly_detection/prepare_delivery_review20.py`.
`--summary` verifies a saved package. `--check-note PATH` validates one structured
provisional note against the selected real evidence without saving it. Journal
entry is not performed by this preparation command. Existing output is refused.
