# Process import v1

This is an input contract for recorded order fulfillment, not a synthetic dataset. `orders.csv` has only a header. No example orders, events or labels are invented.

## Input grain and semantics

One CSV record represents one actual order in one dataset/marketplace, with one explicitly assigned category. Item-level exports need a separate, documented aggregation step first. Do not keep the first item or assign a mixed-category basket arbitrarily. Data from different marketplaces must be imported as separate datasets.

Required columns: `order_id`, `order_status`, `product_category_name`, `order_purchase_timestamp`, `order_approved_at`, `order_delivered_carrier_date`, `order_delivered_customer_date`. Required values: ID, status, category and purchase timestamp. Later milestones may be empty; an absent milestone is not reconstructed.

`order_estimated_delivery_date` is optional and is an estimate, NOT an actual event. Set its mapping to null when unavailable. Order IDs and category names are case-sensitive text; leading zeroes and literal `NA` identifiers are preserved. Only surrounding whitespace is trimmed, with every change logged. Only empty/whitespace cells mean missing; `NA`, `null` and similar timestamp strings are invalid unless corrected at the source.

V1 uses explicitly declared source wall-clock timestamps without timezone inference or conversion. Supported actual timestamp formats are `%Y-%m-%d %H:%M:%S`, `%Y-%m-%dT%H:%M:%S`, `%d/%m/%Y %H:%M:%S`. The estimate additionally supports `%Y-%m-%d` and `%d/%m/%Y`. Format matching is exact. Offset-bearing or mixed-clock exports are not supported in v1; do not strip timezone offsets to make them pass. This limitation must be resolved before comparing sources across clocks or daylight-saving transitions. Extraction cutoff is unknown and ongoing order age is not computed.

## Configuration

Set the three null metadata values in `import_config.json`: a dataset ID, marketplace ID and the exact primary category value from the real source. Map canonical column names to actual CSV headers and explicitly map the source status vocabulary to the eight supported process states. The initial mappings match the Olist-style vocabulary; they do not automatically translate another platform's meanings. The importer rejects missing metadata, unknown keys, duplicate JSON keys, ambiguous column mappings and unknown statuses.

The template is intentionally not runnable without real metadata and records. It does not make up a marketplace or category. CSV is UTF-8 (optional BOM), with the declared one-character delimiter. Duplicate headers, malformed CSV records and missing mapped columns stop the import before publication. Extra source columns remain in the byte-for-byte source copy and are not implicit predictors or labels.

## Existing split metadata

Optional `split` and `event_time_cutoff_exclusive` mappings must be supplied together. Splits may be train, validation or test. Train/validation eligibility requires a recorded cutoff strictly after purchase and every actual event strictly before it. A missing cutoff leaves a valid order imported but not timing-input eligible. Test cases remain reserved and are never exported as a model feature matrix or scored. `dataset_scope: development_only` rejects any test rows; `unassigned` does not invent a split.

Without split metadata, import can succeed but `timing_input_eligible` remains false. Completed-timing eligibility only describes observed data quality, not training permission. A future training stage must validate the declared chronological design, use train-only fitted statistics and never mix categories/marketplaces. This importer neither trains nor transfers an existing model.

## Error and quality policy

- Missing IDs/status/category/purchase, malformed nonblank dates, unknown statuses and duplicate normalized IDs are blocking row errors. ALL records in a duplicate-ID group are quarantined, including byte-identical duplicates. Exact duplicates, conflicting payloads and IDs colliding after whitespace trimming are distinguished. No first/last row wins.
- Validly parsed negative durations, recorded reversals, status/delivery conflicts and incomplete histories remain in accepted orders with explicit quality/context issues. They are evidence, not parser errors to erase. Zero durations remain unchanged and are warnings only.
- All source records reconcile to accepted or quarantined records. A snapshot with any blocking errors has `ready_for_pipeline: false`; the official loader refuses it. Do not train on a hand-picked accepted subset while silently dropping quarantined records.
- If the declared primary category is absent, the snapshot has a blocking scope error. Other observed categories are retained, not relabeled or counted as training inputs for the selected category.

## Run and outputs

```bash
.venv/bin/python scripts/01_data_preparation/import_process_data.py --input PATH_TO_REAL_CSV --config PATH_TO_CONFIG_JSON --output PATH_TO_NEW_SNAPSHOT
```

Output must not exist. Success exits 0; quarantined rows exit 2 after writing the audit snapshot. File/config errors produce no snapshot. Reruns use a new output directory.

The snapshot contains the original source/config bytes, typed `orders.csv`, actual-only `event_log.csv`, row dispositions, detailed issues, duplicate groups, quarantined source payloads, normalization changes, column mappings, category counts and a manifest with hashes/schemas. No human label, anomaly probability, model, statistical threshold or accuracy metric is generated. Accepted means structurally importable, NOT a clean/normal business process.

## Real Olist round trip

`export_olist_import_source.py` creates a real input package from the frozen bed_bath_table experiment's train/validation records only. It preserves their existing split/cutoff metadata and exports a lineage table. The 1,691 test orders are excluded. This is a format round trip of previously analyzed data, not a new independent evaluation set or proof of cross-marketplace validity.

The real-data commands used for this version are below. Existing versioned outputs are deliberately not overwritten; use new output paths on a rerun.

```bash
.venv/bin/python scripts/01_data_preparation/export_olist_import_source.py
.venv/bin/python scripts/01_data_preparation/import_process_data.py --input data/import_sources/olist_bed_bath_table_development_v1/orders.csv --config data/import_sources/olist_bed_bath_table_development_v1/import_config.json --output data/imported/olist_bed_bath_table_import_v1
```
