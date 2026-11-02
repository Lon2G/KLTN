# Process Signals V1

Read-only derivation from the immutable `olist_evidence_criteria_v1` snapshot
and its train-only performance references. This is the signal-output layer,
not a newly selected ML detector, new labels, a dashboard or a live service.
No external provider is called. No raw observations are invented or modified.

## Outputs

- `order_signals.csv`: one row per guarded validation order. Calendar delivery
  performance, long-stage warnings, optional short-duration notes, coverage and
  source-quality issues remain separate. No whole-order Normal/Anomaly label.
- `stage_signals.csv`: three rows per real order, including stages which cannot
  be assessed. Actual visible endpoints and signed durations are preserved.
  Unavailable flags remain blank, never silently false. A missing comparison
  can reflect the parent's complete-case policy even when this stage has both
  endpoints; this limitation is explicit.
- `signal_evidence.csv`: one row per observed late-promise signal, long-stage
  warning or optional short/zero-stage note. These are not independent labels.
  Early/on-date performance and data-quality limitations stay in the other
  tables and are not silently made into process anomalies.
- `reference_groups.csv`: reproducible context definitions, train support,
  descriptive quantiles and exact decision boundaries. Boundaries are derived
  algebraically from the existing ECDF rule, not retuned on validation.
- `evaluation_contract.csv`: separate software consistency, rule agreement,
  independent signal assessment and causal attribution. Only the first is
  validated here. Independent signal review does not require root causes.
- `bao_cao_dau_hieu.md`: Vietnamese summary and actual case examples.
- `manifest.json`, `protocol.json`, `summary.json`: hashes, scope and provenance.

## Interpretation

Primary attention is triggered by recorded calendar lateness or an observed
long-stage warning. These are distinct signals, not proof of a bottleneck or
seller fault. Positive short durations are optional notes; early delivery is
performance information. No observed primary signal is not verified normality.

Each stage uses the frozen reference policy: at least 30 train peers with a
quality-screened distance match, with the parent's context fallback levels.
Other factors are not fully controlled. The parent only compares orders with
complete, nonnegative visible stages and eligible calendar performance. This
layer does not broaden that population or turn completed-case evidence into
online detection. Unknown coverage is retained explicitly, even alongside a
known late-promise signal.

For sorted train durations x[1] ... x[n], the existing upper-tail rule is
F(d) > .95, with F(d) = count(x <= d)/n. The equivalent day boundary is the
first x[k] for which k/n > .95, tested with d >= x[k]. Lower-tail F(d) < .05
uses the first x[k] for which k/n >= .05, tested with d < x[k]. Both require
d > 0. This handles ties. Interpolated P95 is descriptive, not this boundary.
An observation at the inclusive upper boundary can have zero excess days
and still satisfy the rule. Percentiles are ranks, not anomaly probabilities.

At order level, an observed positive flag is retained even if another stage
is unavailable. A negative aggregate requires all three stages assessed;
otherwise it stays unknown. Cause fields and broad unverified labels are not
inputs to detection. Nothing waits for an operational cause to be known.

All old code, data and experiment snapshots remain intact. The original pilot
and guarded final-test purchases remain excluded. This output is not an
independent new dataset and cannot establish unbiased or optimal detection.
No independent accuracy, causal explanation or near-real-time result is
claimed. A provisional operating configuration can be chosen later under
explicit criteria without first proving causes.

## Commands

Run from the repository root:

```bash
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/build_process_signals.py
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/build_process_signals.py --summary
PYTHONPATH=scripts/01_data_preparation:scripts/03_anomaly_detection .venv/bin/python -B scripts/03_anomaly_detection/build_process_signals.py --order 0f916489801ab47804cc7da10fe0dd6d
```

Creation refuses existing/protected destinations and publishes atomically.
Inspection verifies the saved output and its source chain; it does not refit,
relabel or create another snapshot. Use a new code/artifact version for future
semantic changes, not rewritten historical hashes.
