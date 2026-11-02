# Code Lifecycle

The objective remains process-aware anomaly analysis for real Olist
`bed_bath_table` orders. A file being present does not make it a required step
in every run. No final detector, verified anomaly accuracy or completed
multi-agent/near-real-time service is implied by this map.

## Current Components

Paths below are relative to `scripts/`; consult each corresponding template for
its input contract. Existing snapshots must not be overwritten.

| Role | Entry points | Meaning |
| --- | --- | --- |
| Source preparation | `01_data_preparation/clean_olist_data.py`, `apply_olist_usage_policy.py`, `prepare_bed_bath_table_experiment.py`, `import_process_data.py` | Audit, preserve observed values and missingness, scope the category, import with provenance. |
| Process evidence | `02_process_mining/conformance_analysis.py`; `03_anomaly_detection/integrate_process_evidence.py` | Reference process and integrated observed evidence; integration is not a final anomaly verdict. |
| Dataset-bound detection | `03_anomaly_detection/train_imported_process.py`, `score_process_batch.py`, `compare_process_candidates.py` | Train, save, score and compare candidates for the declared source. Do not run historical baselines first. |
| Business-feature comparison | `03_anomaly_detection/train_business_ablation.py` | Compare timing, payment/order and combined inputs; retrospective, not automatically online. |
| Context experiments | `03_anomaly_detection/experiment_contextual_delivery.py`, `experiment_seller_delivery.py`, `evaluate_fixed_horizon_delivery.py` | Supporting time references, geography, seller history and observation-window sensitivity. Not a replacement thesis target. |
| Evidence inspection | `03_anomaly_detection/inspect_process_evidence.py` | Read-only investigation of existing outputs, not new labels or training. |

These components consume specific prepared artifacts; this table is not an
executable run-all recipe. The temporal 80/20 plan remains a proposal until an
explicit evaluation run activates a reviewed design. Selection and the final
test remain separate from code cleanup.

## Optional Human Review

- `prepare_business_review.py` creates the original blind pilot;
  `review_business_cases.py` supports its original criteria and review sessions.
- `prepare_review_readiness.py` links that unchanged pilot to analyst-only
  evidence and readiness checks. It does not replace the reviewer interface.
- `prepare_hierarchical_review.py` adds the provisional scope-specific hierarchy
  and a separate revision journal. It does not convert legacy labels.
- `manage_data_disposition.py` records separate scope-specific use proposals for
  analyst-visible cases. It is not independent blind reference labeling.

All four roles are currently distinct. They are not mandatory serial steps for
automatic batch detection. Consolidating interfaces must not expose analyst
model outputs to blind reviewers, merge different label versions, or turn
provisional notes into reference truth.

## Historical Reproduction Only

Do not use these as the current default training/evaluation workflow:

- `03_anomaly_detection/anomaly_detection.py` and `anomaly_evaluation.py`:
  original global baseline, fixed output paths and in-sample diagnostics.
- `03_anomaly_detection/experiments/anomaly_refinement_experiment.py` and
  `anomaly_final_candidate_experiment.py`: earlier IF/contamination studies.
- `03_anomaly_detection/auto_validation.py` and
  `auto_validation_threshold_analysis.py`: earlier rule-reference experiments.
- `03_anomaly_detection/automated_case_validation.py`: optional LLM prototype
  for the old 100-row sample, not independently verified labels. Explicitly
  running it with provider credentials may send case data to that provider.

These files are retained for research history, old artifact dependencies and
reproduction, not silently deleted or moved. Some historical scripts overwrite
fixed outputs; never run them as part of a blanket cleanup or run-all command.
`auto_validation_evaluation.py`, older review helpers and
`train_bed_bath_table.py` also supply utilities still imported by newer code;
an old filename is not evidence that a module is unused.

## Deduplication Boundary

- Process discovery reuses the existing conformance event-log preparation.
- Historical IF studies share preprocessing, IF fitting and Jaccard helpers in
  `scripts/legacy_anomaly_helpers.py`; their earlier imputation/in-sample behavior
  is intentionally preserved, not introduced into current temporal trainers.
- Historical data/manual audits share scalar formatting in `scripts/reporting.py`.

Other duplicated utilities remain in files whose exact hashes are bound to
existing manifests. Editing them in place invalidates historical verification;
future consolidation needs a new code/artifact version, not rewritten old hashes
or disabled integrity checks. Research data, stored models, thresholds and human
decisions are outside the scope of this cleanup.
