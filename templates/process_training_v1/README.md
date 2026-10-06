# Per-dataset timing training v1

This stage consumes a verified `process_import_v1` snapshot. It fits new models on that dataset, saves an identity-bound model bundle, and generates development diagnostics and automatic warning evidence. It does not load the old Olist fitted models or require a new manual-review batch.

## What is reusable

The code/profile is reusable for declared order-level Purchased -> Approved -> Handed to Carrier -> Delivered data. Weights, scalers, empirical reference distributions and learned thresholds are fitted again for each dataset/category. A matching CSV schema does not prove that another marketplace has the same business semantics. Different processes or clocks need a reviewed input contract, not an arbitrary column substitution.

`training_config.json` is intentionally incomplete. Fill its dataset/marketplace/category identity and explicit source-clock calendar boundaries from the real imported dataset. No sample observations are invented. `olist_bed_bath_table.json` is the actual existing Olist design, not a universal configuration for other sources.

## Chronological design

Require purchase_start < fit_end < train_end < validation_end. Orders stay in their purchase cohort even if delivered later. Fit uses train-period orders purchased AND fully observed before fit_end. Calibration uses later train purchases completed before train_end. Earlier train purchases completed after fit_end are deferred, not moved into calibration. Validation uses later purchases completed before validation_end. Incomplete, reversed and boundary-crossing cases remain in a scope ledger and process checks.

Existing imported train/validation assignments and cutoffs must agree with the plan; they are not silently overwritten. Previously reserved test rows cannot be pulled into development. Unassigned imports may receive derived assignments from this explicit plan, without modifying the source snapshot. All imported rows reconcile to development, another category, an outside-window group or reserved test. No final-test scoring is exposed by these commands.

The current fixed method profile needs at least 512 fit and 200 calibration cases, plus nonempty timing validation. These are technical profile requirements, NOT proof of adequate statistical power. Zero IQR/MAD, unsupported clocks and insufficient data fail clearly; no fabricated observations or numerical epsilon are added to force training through.

## Model profile and interpretation

The shared tested calculation functions fit 4 Isolation Forest, 3 novelty LOF and 3 One-Class SVM models. Statistical methods and fixed rank ensembles give 21 score columns and 120 candidate configurations. Only fit cases determine transformations/statistics/models. Unseen calibration cases determine score-tail thresholds and rank reference distributions. LOF novelty scoring never receives fitted rows. Validation never refits or changes thresholds.

All three features are observed transition durations in days. IDs, category, status, human labels and total cycle time are not predictors. Olist's existing fit data are unlabeled, not a verified normal-only cohort. Calibration is threshold calibration, NOT anomaly-probability calibration. Counts and scores are not probabilities or accuracy, and automated rules are not independent ground truth. No winning candidate or automatic business Normal/Anomaly label is selected. Short/zero durations remain warnings.

## Commands for the real Olist run

```bash
.venv/bin/python scripts/03_anomaly_detection/train_imported_process.py --snapshot data/imported/olist_bed_bath_table_import_v1 --config templates/process_training_v1/olist_bed_bath_table.json --output models/datasets/olist_bed_bath_table_training_v1
.venv/bin/python scripts/03_anomaly_detection/validate_saved_process_model.py --model models/datasets/olist_bed_bath_table_training_v1 --snapshot data/imported/olist_bed_bath_table_import_v1 --output data/experiments/imported_olist_model_reload_v1
```

Outputs are immutable; reruns need new directories. The second command reloads the bundle and reproduces the same declared validation predictions without fitting anything. This is a reproducibility test, not a second independent evaluation or deployment on new future orders.

## Saved bundle and limits

The model bundle contains all fitted pipelines, fitted statistics, empirical rank references, cutoffs, IDs of fit/calibration/validation cases, feature definitions, source identity and the training plan. Sidecars record scope, actual feature matrices, scores, candidate flags, warning evidence, runtime versions and input/output/code hashes. The saved-model loader checks identity, schema, integrity and compatible runtime/engine versions before use.

Load only locally produced trusted `.joblib` files. Pickle/joblib deserialization can execute code; hashes are integrity checks, NOT authentication of an untrusted model upload. The revalidation command is bound to the same imported snapshot. It must not be used to present another dataset's outcomes as this validation experiment.

Still separate: future-batch prediction with drift checks, justified model selection, evaluation against an appropriate independent reference, calibrated anomaly probabilities, frequency-feature modeling and a user interface. None is claimed as completed by this stage. No extra data is needed for the current Olist round trip; another marketplace requires its own real data and training plan.
