# Frozen validation candidate comparison

Run the comparison on a verified validation-replay artifact:

```bash
.venv/bin/python scripts/03_anomaly_detection/compare_process_candidates.py --batch data/experiments/olist_validation_batches_comparison_input_v1 --output data/experiments/olist_candidate_comparison_v1
```

The comparison input above is a fresh replay of the same frozen Olist validation. The earlier `olist_validation_batches_v1/README.md` was edited after publication and no longer matched its manifest hash, while its numeric files still matched. The older directory was preserved, not repaired or silently admitted through a weakened hash check. The new replay uses the same saved model, source snapshot and configuration; it is not a new independent experiment.

This command reads saved candidate definitions, score/flag sidecars and case-check evidence only. It verifies source/output hashes, matches cases to the original validation window, and recomputes strict score > threshold flags for consistency. No joblib deserialization, training, threshold learning, manual-label generation or final-test scoring takes place.

## Outputs

- `candidate_comparison.csv`: all configurations, actual flag volume, calibration versus validation rates, purchase-month variability and observed flag-set membership. Ordered by candidate name, not model quality.
- `monthly_candidate_rates.csv` and `monthly_coverage.csv`: explicit all/scored/flagged counts and denominators. Unscored cases are not negatives.
- `family_summary.csv`: ranges across configurations in each method family. Configurations are correlated, not independent votes.
- `pairwise_overlap.csv`: all unordered candidate pairs, with overlap, disagreements and Jaccard on the same scored population.
- `observed_equivalence_groups.csv`: configurations with exactly identical flags on this validation only. This does not make their models or future outputs equivalent.
- `threshold_sensitivity.csv`: adjacent numeric cutoffs of the same score column, with numbers of flags removed when increasing the threshold. Cross-method raw-score magnitudes are not compared.
- `candidate_rule_overlap.csv`: detector signals versus observed timing warnings on applicable cases. These rules share features with detectors and are not independent truth.

## Mathematical interpretation

Monthly rate is flagged/scored, not flagged/all. The overall rate is weighted by scored-case counts. Monthly range and weighted standard deviation are reported in percentage points. Both are unavailable if fewer than two months have scored cases. A month with zero scored cases has an unavailable rate, not zero risk. A configuration with zero flags is not automatically the best or most reliable.

Jaccard is intersection/union of flagged sets; zero union stays unavailable even when both methods flag nothing. Identical empty vectors do not establish equivalent methods. With no scored cases at all, no equivalence conclusion is made. Threshold equality uses the saved strict greater-than comparison; equal cutoffs cannot create distinct flags on the same score.

Each purchase month retains the same original validation event cutoff. Different follow-up lengths, unfinished-order exclusion and case mix can alter rates. These are descriptive warning-rate comparisons, not temporal cross-validation, retraining stability tests or accuracy estimates. Calibration helped set score cutoffs and is not independent evaluation. The small set of observed months does not establish performance on another period or marketplace.

No best candidate, arbitrary composite quality score, automatic elimination or threshold revision is produced. Operational selection criteria should be explicit before opening the final test. Claims of accuracy additionally need a suitable independent reference; another manual-label batch is not required just to run this pipeline. No additional data is needed to produce these current comparison tables.
