# Geographic Features, Version 1

## Scope

Only the frozen bed_bath_table development orders are enriched. Parent business
features, source manifests and code hashes are verified. The reservation ledger
is read only for order IDs and partitions. No final-test feature matrix, model
weights, detector flags or human review labels enter this builder.

Raw/clean tables, previous features, models and review files remain unchanged.
This is retrospective enrichment, not a trained geographic anomaly detector.
No labels, probabilities, accuracy or optimal representative policy are claimed.
The summary marks geographic source quality as unverified and model-training
readiness as false. Available coordinates do not mean verified coordinates.

## Representative ZIP Coordinates

1. Keep every supplied row for linked ZIPs in coordinate_source_evidence.csv.
2. Exclude only missing, nonfinite or out-of-Earth-bounds coordinates from the
   calculation, not from the evidence. Bounds validity does not prove location accuracy.
3. Give each distinct valid latitude/longitude pair one vote within its ZIP.
   Repeated coordinates with different city strings do not receive extra weight.
4. Calculate a coordinate-wise median anchor and choose the observed point closest
   to that anchor by spherical distance. The published representative is an actual
   source point, not the calculated anchor. This is NOT an exact geographic medoid.
5. Resolve exact distance ties by latitude, longitude, then clean_source_record.

This fixed engineering choice has not been optimized against labels or delivery
outcomes. It is intended for the current Brazilian ZIP reference, not established
as a universal policy for geographically dispersed or antimeridian-spanning areas.
For sensitivity only, also select an observed point nearest the arithmetic-mean
anchor. Keep both policies' distance results; neither is an accuracy winner.

Each representative carries its one-based clean source record (header excluded),
source-row counts, unique-point counts and source state codes. Spread percentiles
and maximum spread are measured over unique valid points about the primary point.
Unusually dispersed coordinates are retained, not silently clipped to Brazil,
replaced, or interpreted as unusual customer behavior. Source state strings are
evidence, not verification that every coordinate lies inside the named state.

## Distance And Multiple Sellers

Distances use scikit-learn's Haversine implementation with latitude/longitude in
radians and a declared spherical radius of 6371 km. See the
[official implementation documentation](https://scikit-learn.org/stable/modules/generated/sklearn.metrics.pairwise.haversine_distances.html).

These are approximate great-circle distances between ZIP representatives. They
are not street addresses, road lengths, actual shipment routes or travel speeds.
Same-ZIP distance can equal zero without the two parties sharing an address.
The source supplies no coordinate observation or record-arrival dates; geography
is not claimed to have been available at purchase time or in an online system.

Every distinct order/seller pair is retained. Two candidate order features are:

- Maximum seller-customer distance across all sellers of the order.
- Item-row-weighted mean: sum(distance * seller item-row count) / order item-row count.

These are summaries of seller origins, not a multi-stop route. If even one seller
or the customer lacks coordinates, BOTH order features remain missing. Available
pair evidence stays visible, but no partial order average or invented point is used.
Payments and freight totals are never multiplied by the number of sellers.

Per-pair source envelopes use d +/- (seller maximum ZIP spread + customer maximum
ZIP spread), bounded by zero and pi*R. They enclose spherical distances among the
supplied valid coordinate sets. They are NOT confidence intervals, bounds on actual
addresses or road routes, or guarantees about missing/incorrect source locations.

## Descriptive Context Only

All orders, including those without timing eligibility, retain geographic coverage
records. Existing freight totals and eligible durations are copied from the parent.
Fixed distance bands and split-wise Spearman associations describe distance,
freight and completed delivery duration. They are not learned thresholds, causal
effects, fairness-adjusted seller comparisons or anomaly labels. Denominators and
missing geography/timing counts are explicit. No divide-by-distance speed or cost
ratio is created, because zero and approximate ZIP distances do not support that.

Before modeling, inspect source dispersion and sensitivity, define geographic
quality handling explicitly, select features on development data only, and compare
against the preserved baseline on identical eligible cases. Keep the final test
closed and avoid treating unavailable geographic features as zero distance.

## Run And Artifacts

```sh
.venv/bin/python scripts/01_data_preparation/build_geographic_features.py
.venv/bin/python -m unittest discover -s tests -p test_geographic_features.py -v
```

The default output is data/experiments/bed_bath_table_geographic_features_v1.
Existing output is never overwritten. CSV schemas and exact numeric round trips,
input/code/output hashes, runtime versions, the fixed protocol and summary are
saved together. Use a new snapshot for a changed policy; preserve earlier results.
The evidence directory remains excluded from Git under the existing data policy.
