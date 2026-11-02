# Read-Only Process Evidence Inspection

This command-line tool connects the integrated order evidence to a concrete,
source-bound case view. It reads real saved Olist development observations only.
It does not retrain, rescore, select a winning model, change a threshold, enter a
human label, infer approval or expose final-test cases. This is not a complete
BI dashboard, multi-agent system or validated real-time deployment.

## Quick Start

Run from the project root. No additional installation, imported data or server
is needed. The default summary is VALIDATION excluding the protected pilot.

```sh
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --summary
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --list --month 2018-03 --limit 10
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --list --signal visible_milestone_inversion
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --list --delivery-state forecast_only
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --summary --split all --format json
```

To inspect one case, use `--order-id` followed by an actual ID returned by
`--list`. Exact case lookup is independent of the default validation filter and
cannot be mixed with list filters. It can inspect accessible train or validation
observations, never reserved test records. Invalid/unknown IDs fail explicitly.

Lists support split, purchase month, one evidence code and delivery-evidence
availability. Filters combine with AND. Pagination is `--offset` and `--limit`
(1..200). Rows sort by purchase time then order ID, NOT by anomaly severity.
An empty result has count zero, not invented example rows. Counts describe the
entire filtered set, not just the displayed page. Evidence codes can overlap.

## Exact Evidence and Meaning

The case view includes all four recorded milestones with their source row/column,
visibility at the original partition cutoff and observed signed stage durations.
It distinguishes source absence, post-cutoff observations, zero duration and
negative duration. Recorded final snapshot status is NOT status as of the cutoff.
The raw source timestamp can be shown for a post-cutoff milestone, but it is
explicitly excluded from the cutoff-bound process calculation.

Actual payment/item records are included with `clean_source_record` lineage.
Counts and sums in integer minor units reconcile to the saved order aggregates;
there is no item-payment Cartesian join or multiplication by installment count.
Currency is kept in original source units, without conversion. Installments and
payment sequences are not failed attempts. An accounting difference is not fraud.

Geographic evidence retains the existing provisional quality decisions and ZIP
representative straight-line distances. Seller histories retain actual support
counts and strictly prior event times, including each seller separately on
multi-seller orders. Rates are conditional on observed scoped history, not
causal seller blame, lifetime behavior or cancellation rates.

Every saved business-anchor profile and delivery configuration available for the
case is shown. The business anchor is the previous review sampling anchor, not
a chosen best method. Model-score explanations identify the threshold comparison
and evidence source, not a causal feature contribution or SHAP explanation.

Three states remain different: a saved comparison with warning false, a forecast
without an eligible observed outcome (warning null), and no saved model result.
None is converted into a Normal label. Model counts are not combined into a
confidence or probability. Missing scalar values serialize as JSON null, never
zero, false, NaN or Infinity. JSON preserves source numeric values; the readable
Markdown view may round numbers for display only.

## Blinding Guard

The original 120 pilot IDs are removed from EVERY queryable table before query,
rendering or export. Direct lookup is blocked without an override. The original
pilot key is verified but not printed, copied or exposed in public results.
Use `review_business_cases.py --status` / `--preview` for blind evidence instead.
Labels and criteria remain untouched. No reviewer session is created.

This protects the current pilot within this tool, not access to other local
files. It cannot erase earlier exposure or establish reviewer independence.
Future independent review samples must account for evidence/model views already
seen by reviewers and explicitly extend the protection protocol before use.

## Optional Exports

`--output` writes a NEW directory containing a readable README, exact result.json
and manifest with output hashes, source/pilot manifest hashes and code bindings.
Existing destinations and exports inside data/model/code/review directories are
refused. No export occurs without --output. Source hashes are checked again before
publication; failure leaves no partial report. Example, using an unused path:

```sh
.venv/bin/python scripts/03_anomaly_detection/inspect_process_evidence.py --summary --output outputs/inspections/validation_overview_v1
```

These are analyst reports, not thesis/manuscript claims or independent evaluation.
The JSON interface can support later BI work without introducing another scoring
implementation. Keep source snapshots, review criteria and model weights frozen.

```sh
.venv/bin/python -m unittest discover -s tests -p 'test_evidence_inspection.py' -v
```
