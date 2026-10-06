# Olist business feature contract, version 1

## Run

From the project root, with the existing Python environment:

```bash
.venv/bin/python scripts/01_data_preparation/build_bed_bath_table_business_features.py
.venv/bin/python -m unittest discover -s tests -p 'test_bed_bath_table_business_features.py' -v
```

The default destination is `data/experiments/bed_bath_table_business_features_v1`.
Existing destinations are never overwritten. For a new revision, pass a new
`--output` directory and retain the old snapshot. No raw data, prior clean
snapshot, review labels, model weights or thresholds are modified.

## Data scope

- Only real, already imported Olist orders from the exact frozen `bed_bath_table`
  development cohort: 5,480 training and 1,853 validation orders.
- The reserved 1,691 test orders are checked by identifier/split only. Their
  feature values are not materialized or scored by this builder.
- The standardized clean tables supply observed item, payment, product,
  customer, seller, review and ZIP-coordinate evidence. Source manifests, file
  hashes, code hashes and logical column schemas accompany the output.
- `clean_source_record` is a one-based data record in the standardized source
  CSV, excluding its header, not the physical text line number of a multiline
  review. Missing values remain missing; no invented orders, values or labels.

## Grain and calculations

`order_features.csv` has exactly one row per development order. Payments and
items are aggregated separately before joining, never joined at their raw
many-to-many grain. Each `payment_value` is counted once, not multiplied by
`payment_installments`. Exact two-decimal source amounts are aggregated as
integer minor units before conversion back to source currency units.

The 20 candidate features cover payment record counts, totals, methods,
installments and sequential indices; item/product/seller counts; item prices,
freight and ratios; signed payment/item reconciliation; strictly prior scoped
customer purchases; and the existing three eligible process durations.

The feature dictionary specifies candidate vs audit-only fields, formulas,
units and availability. Do not train on every numeric column: identifiers,
quality flags and duplicate integer/currency representations are audit metadata.
Several candidate features are correlated; they are candidates for later
selection/ablation, not an instruction to fit all of them at once.

Zero observed payment rows means no payment record was supplied, not zero
money paid. An incomplete monetary group yields a missing total, not a partial
sum. Zero freight, large amounts and observed discrepancies are not removed or
automatically labeled as errors, anomalies or fraud.

## Coverage of the advisor's six groups

| Group | Implemented in this version | Not yet implemented |
| --- | --- | --- |
| Transaction value | Observed payment total and payment/item reconciliation | Peer reference, fitted thresholds and warning score |
| Payment patterns | Row/method counts, max installments and sequential index | Contextual pattern detector; no evidence of failed retries |
| Customer behavior | Strictly prior category/development purchase count and recency | Full purchase history and comparison against customer peers |
| Seller behavior | Every seller/order link, review usability at partition cutoff, source counts | As-of late/cancel/low-review rates and seller detector |
| Order/process | Item/price/freight features plus existing eligible durations | New business-feature training profile and baseline comparison |
| Geography | Actual ZIP-coordinate source coverage for each seller/customer pair | Justified representative coordinates, distances and peer comparison |

`advisor_group_coverage.csv` records the denominator and exact meaning of every
support count. A support count is not an anomaly count or model coverage rate.

## Time and attribution boundaries

Payment and item records have no supplied record-arrival timestamps. These are
retrospective snapshot features, not proven purchase-time or partition-cutoff
inputs. All columns are currently marked `online_ready = false`. A chronological
order split does not by itself establish point-in-time feature availability.

Customer history uses `customer_unique_id`, strictly earlier purchase events,
and the selected category/development population only. Equal-time and future
purchases do not enter earlier counts; a zero count means no observed prior
purchase in this scope, not a new customer across Olist.

Review scores describe orders, not necessarily individual sellers on multi-seller
orders. Review answers must be temporally consistent and before the partition
cutoff to count in the source-availability audit. These counts are not seller
features known at the earlier purchase time. No cancellation time is invented.
ZIP coordinates describe an area, not an exact customer location or road route.

## Evaluation boundary and next implementation

This version builds evidence and audits sources. It does not train, select
thresholds, assign anomaly/fraud labels, estimate accuracy or produce calibrated
probabilities. The old three-duration model remains unchanged and its strict
schema must not be bypassed to accept this table.

The next implementation should define a separate retrospective business-feature
profile with explicit feature selection and missing-value handling, fit only on
training data, calibrate on the designated calibration subset, and compare on
development validation without opening the reserved final test. Use ablations
to assess whether business features add useful information to process features.

Define a semi-manual review protocol for both flagged and unflagged real cases,
with reviewer identity, permitted evidence, group-specific reasons, uncertainty
and labels. Detector outputs are not ground truth. Seller/geographic detectors,
multi-agent coordination and event-time replay require separate implementation
and evidence before the manuscript can claim those capabilities.
