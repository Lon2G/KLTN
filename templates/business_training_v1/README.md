# Retrospective business-feature training v1

Run from the project root using the existing environment:

```bash
.venv/bin/python scripts/03_anomaly_detection/train_business_ablation.py
.venv/bin/python -m unittest discover -s tests -p 'test_business_ablation.py' -v
```

The default immutable output is `data/experiments/olist_business_ablation_v1`.
Existing output directories are refused. The preserved three-duration trainer,
old model bundles and real imported/clean/feature snapshots are not modified.
No new data import or labeled file is required to compute descriptive results.

`protocol.json` declares the finite grid before execution. This first ablation
varies only the feature set: three timing features, nine payment/order features,
or all twelve. The same real cases, model parameters, seed, calibration design
and threshold fractions are used across all three profiles. No profile or
threshold is automatically selected as best.

The existing chronological timing design supplies fit/calibration/validation
partitions, but payment/item observation times are unknown. This comparison is
RETROSPECTIVE, not an online backtest. Restricting all profiles to eligible
completed cases enables paired comparisons but does not measure broader
business-only coverage. Excluded and deferred cases remain in the scope ledger.

Nine business features are a declared starting subset of the twenty feature
candidates, not an empirically optimal selection. They represent amount, source
payment count, method count, installments, sequential index, item count, seller
count, freight/price ratio and mean item price. Nominal payment methods, customer
history, signed reconciliation, seller risk and geographic distance require
additional feature/evaluation policies; they do not enter these models.

IQR can be zero for sparse counts without a feature being constant. LOF/SVM use
the existing log1p + scikit-learn RobustScaler pipeline, whose actual learned
center/scale is exported. Zero/near-zero IQR uses the library's unit-scale
handling, not an invented epsilon, clipping, dropping rare observations or
monetary imputation. New univariate IQR/MAD rules are not asserted for counts.

Each profile fits ten NEW ML models. Their ten scores and two rank-ensemble
scores each receive eight calibration quantile thresholds: 96 candidate
thresholds, not 96 independent fitted models. Thirty models produce 288
profile/candidate results across the three feature profiles. Rankings are
empirical calibration CDF ranks, not anomaly probabilities. More flags,
agreement or stability are not proof of correctness.

The fresh timing-only refit is compared against verified original CSV sidecars
for the overlapping ML/rank methods. Old fitted weights are not loaded. New
bundles are reloaded and both calibration and validation scores/flags checked
before publication. Checksums bind sources, code, protocol, runtime and outputs.

The output includes `advisor_questions.md` for confirmation of independent
review targets, reviewer/sample design and the real-time claim. Until agreed
and evaluated, do not report accuracy, fraud detection, calibrated probability,
an optimal model or a completed real-time multi-agent framework. Keep the final
test closed. Tests must use real observations; invalid metadata or deleted
source rows may exercise guards but never become thesis experiment data.
