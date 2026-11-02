# Integrated Process Evidence

This is an ANALYST-ONLY retrospective evidence snapshot, not a new detector,
combined verdict, fraud labeler, probability model or real-time deployment.
It connects the existing real Olist bed_bath_table development snapshots.
No fitted weights are deserialized, no model is trained, no threshold is changed,
and no final-test observations are scored. Every development order is retained.

## Process View

`milestone_evidence.csv` records all four source fields per order with source
record/column lineage. Each field is separately marked `visible`, `source_missing`
or `at_or_after_cutoff`. Only actual timestamps strictly before the order's
original train/validation partition cutoff enter `visible_events.csv`.
The cutoff is an experiment boundary, not an asserted source extraction time.
The displayed source order status is from the final snapshot, not status as of
that cutoff. This view is diagnostic, not a new fit-time feature matrix.

Reuse the existing Purchased -> Approved -> Handed to Carrier -> Delivered
reference Petri net with PM4Py token replay. Score only traces with all four
visible milestones and distinct timestamps. Missing/incomplete traces are NOT
penalized as anomalies: cancellation and source absence cannot be resolved from
missing events alone. Equal-time traces are retained but excluded from replay
fitness and DFG. Reference-rank ordering of ties is only for stable display.
Replay results join by explicit PM4Py case ID, never by assumed dataframe order.

All six ordered milestone pairs are checked for timestamp inversions, including
nonadjacent pairs with a missing intermediate event. These are source chronology
signals, not proof of an operational cause. Signed adjacent-stage and total-cycle
durations are preserved; zero, negative and unknown durations remain distinct.
No IQR or other threshold is fitted on this combined development view.

Process variants cover every order. DFG counts are computed separately per
partition, exclude entire equal-time traces, and describe observed succession,
not causation. Neither high fitness nor an ordinary duration proves normality.

## Joined Evidence

`case_evidence.csv` has one row per order. Original context and business values
remain traceable to the source snapshots, alongside process diagnostics,
provisional geographic context, prior seller history and separate model counts.
Original context durations/status can include snapshot evidence after a cutoff;
the explicitly named `process_*` fields and `process_durations.csv` are the
cutoff-filtered view. These two views must not be substituted for each other.

`business_anchor_evidence.csv` preserves the THREE existing feature profiles at
the pilot's declared `ensemble_rank_mean_tail050` sampling anchor. Stored scores,
thresholds and flags are checked against the existing threshold application.
This is a display anchor, not a newly selected winner. A joint business warning
cannot identify which payment/item field caused it. Other candidates remain in
the unchanged source ablation; this integration does not discard that experiment.

`delivery_model_evidence.csv` preserves ALL twelve saved seller-ablation model
configurations. Additional forecasts without an eligible saved outcome have
missing warnings, not False warnings. Long/short interval warnings are not verified
bottlenecks. Counts of agreeing models are descriptive, not votes converted to
an anomaly probability, severity, selected ensemble or operational priority.

Unscored orders remain with missing model counts. Training orders are not scored
against their fitted models here. No warning is renamed Normal, Suspicious or
Anomaly. `evidence_codes_json` includes both signals and limitations; its length
is not an anomaly score. Accounting differences and multiple payments can be
legitimate; installments and payment_sequential do not count failed retries.

Geography uses the existing quality-screened ZIP representatives and straight-line
distances, not verified routes. Seller rates use strictly prior observed events;
denominators describe available category-scoped history, not lifetime activity.
Multi-seller pair histories remain in `seller_pair_evidence.csv`, without assigning
one order outcome to every seller. Customer history is partial, not a finished
customer anomaly detector. Payment/item/geographic source arrival times remain
unknown, so these features are not claimed to be online-available.

## Reproducibility and Review Separation

`evidence_catalog.csv` documents the different evidence clocks and limitations.
`coverage.csv` reconciles process/history routes rather than reporting anomaly
prevalence. The manifest binds all source snapshots, code and output hashes.
CSVs round-trip exactly and publication is atomic; existing outputs are refused.

DO NOT distribute this snapshot to blinded reviewers. The original 120-case
reviewer packet remains separate and unchanged. Preparing a review-readiness
packet must not copy model flags, distances, seller verdicts or new criteria into
the frozen blind evidence. No new human decisions or approvals are manufactured.

```sh
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python scripts/03_anomaly_detection/integrate_process_evidence.py
.venv/bin/python -m unittest discover -s tests -p 'test_integrated_evidence.py' -v
```

Default output: `data/experiments/olist_integrated_evidence_v1`.
