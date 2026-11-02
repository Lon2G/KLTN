# Delivery Performance v1

Analyst-only, retrospective BI inputs for real Olist `bed_bath_table` orders.
Early delivery is performance information, not an anomaly. Late delivery is a
calendar promise observation, not proof of a bottleneck. Neither is a whole-order
normal/anomaly label. Existing model signals remain in a separate table.

## Scope and clocks

The whole original 120-case blind pilot is excluded from every table. Purchases
at or after the frozen proposed 80/20 boundary are also excluded. Actual events
must be strictly earlier than both that boundary and the original partition
cutoff. This is a conservative diagnostic exclusion, not activation of a new
training split. Earlier historical exposure is not undone.

Payment, item, geography and final status snapshots are retrospective context;
their live arrival times are unknown. Future actual timestamps are not exported.
The saved model-evidence table is historical validation evidence, NOT a new
pre-boundary experiment or an online prediction result. No model is loaded or
trained and no threshold is changed.

## Tables

- `order_performance.csv`: one row per accessible order. Calendar delta is
  delivery date minus estimated date; negative means early. `early/on_date/late`
  observations remain distinct from `performance_state`, which can exclude a
  source conflict. No visible delivery or absent estimate means unknown.
- `stage_durations.csv`: three actual transitions and total cycle. Missing,
  negative, zero and positive elapsed durations remain distinct. Quality-excluded
  rows remain present but do not enter valid duration statistics.
- `overview.csv`, `context_summary.csv`: denominator-aware counts and rates;
  group medians are descriptive, not causal adjustments or anomaly thresholds.
- `product_order_links.csv`, `seller_order_links.csv`: unique entity/order pairs.
  A multi-product order belongs to several product groups, so group totals are
  not additive. Seller association does not assign fault. Multi-seller outcomes
  are reported separately from single-seller outcomes.
- `product_summary.csv`, `seller_summary.csv`: unique-order counts, eligible
  denominators, missing/conflict counts, early/on-date/late rates and medians.
- `stage_summary.csv`, `stage_context_summary.csv`: duration quartiles and
  medians by delivery state and context. There is no per-stage promised deadline
  or automatically inferred fastest/slowest cause.
- `saved_signals.csv`: unchanged descriptive signal fields for these orders;
  all signal fields are withheld when actual milestones cross the stricter
  diagnostic cutoff, since an old warning may encode that future outcome. The
  original experiment cutoff remains explicit. Missing scores are not normal
  labels. Not an independent reference or new pre-boundary model selection.

Rows failing chronology/status/promise checks are retained and reported, not
silently removed or repaired. Equal timestamps are not automatically errors.
The estimated-delivery field is interpreted at calendar-date granularity by this
declared protocol. No source timezone, cancellation time or extraction time is
invented. Distance is quality-screened ZIP representative straight-line distance,
not a verified road route. Small denominators remain visible; no best-seller
ranking or calibrated confidence is implied.

## Reproduction

Run `.venv/bin/python scripts/03_anomaly_detection/build_delivery_performance.py`.
Use `--summary` to verify and read an existing snapshot. Output is immutable;
an existing directory is refused. All sources, code, schemas and output hashes
are bound in the manifest. The original data and experiments are unchanged.
