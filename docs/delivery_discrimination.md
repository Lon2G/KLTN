# Frozen Delivery Discrimination Comparison

This experiment compares existing scores against **observed calendar lateness**,
not independently verified bottlenecks, fraud, root causes or early predictions.
It uses real Olist `bed_bath_table` observations only. No new training or labels.

## Contract

- Start with all 1,307 orders in `olist_signal_detector_comparison_v1`.
- Require every order to have all 21 saved detector scores, the saved product/context
  stage score, a known outcome and three complete observed stages. Fail on a
  missing common score instead of allowing method-specific samples.
- Recompute the outcome from delivery and promised calendar dates. Check both
  source labels, stage durations, event cutoffs and history/evaluation separation.
- Keep the fixed score orientation (higher is more anomalous), including negative
  One-Class SVM scores. Do not reverse scores to improve measured AUC.
- Calculate ROC-AUC and Average Precision once per score. AP is not trapezoidal
  PR-AUC. Ties are retained, not broken with noise or outcome labels.
- Evaluate all 120 existing operating configurations plus the existing product
  stage score `> 0` rule. Strict comparisons and threshold values are unchanged.
- Report precision, recall, F1, TP/FP/FN/TN and alert counts for lateness. Undefined
  precision with no alerts stays missing, not 1. F1 is a descriptive equal-weight
  compromise, not an assertion about operational costs or the optimal model.
- Preserve all candidates and tied leaders. Separately show nondominated AUC/AP
  and precision/recall configurations. Never activate a winner automatically.

## Fairness Boundaries

The 21 legacy scores share three timing inputs, 3,190 fit orders and 1,412
calibration orders. The product score uses a different historical reference
population and contextual matching. This experiment compares existing pipelines
on the same evaluation observations, **not a controlled algorithm-only ablation**.
It does not ensure identical validation alert budgets for equal calibration tails.

The product score previously had an AUC measured on 1,411 cases. Recompute it on
the shared 1,307 orders; never copy the larger-cohort metric into this comparison.
The ledger explains exclusion of incomplete-context and pilot cases. No test
features are scored; no missing target is made negative and no score is imputed.

The full completed durations are known only retrospectively. Calendar lateness
and duration scores share the delivery timestamp. Results are not online prediction
quality, bottleneck ground truth, or evidence that promised dates are justified.
An alert on an early delivery can still correctly identify a slow process stage.

This is an already-inspected validation cohort with multiple candidate comparisons.
Reported leaders are exploratory and potentially selection-optimistic. We do not
calculate confidence intervals, assume independent orders or claim statistically
significant differences. Product/seller/time dependence and excluded observations
must be addressed in subsequent validation. No claim that historical test data
has never been explored is made.

## Run

```sh
.venv/bin/python -B scripts/03_anomaly_detection/compare_delivery_discrimination.py
.venv/bin/python -B scripts/03_anomaly_detection/compare_delivery_discrimination.py --verify
.venv/bin/python -B -m unittest discover -s tests -p test_delivery_discrimination.py
```

The default output is `data/experiments/olist_delivery_discrimination_v1`. Existing
directories are never overwritten; reruns require a fresh `--output` directory.
Snapshots contain a Vietnamese report, per-method and per-threshold metrics,
source-case ledger, exact compared scores/outcomes/flags, protocol and SHA256
provenance. Upstream snapshots, models, raw data and review labels stay unchanged.
