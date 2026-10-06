# Business Review Pilot Contract

This version prepares 120 real Olist validation orders for a blinded workflow pilot.
It does not decide that 120 is statistically sufficient or approved by the advisor.
No human label, advisor approval, ground truth, probability or model winner is generated.

The sampling protocol is a fixed engineering design following the existing paired
timing/business/combined study. The criteria draft intentionally has empty definitions
and approval fields. Do not approve it automatically or infer criteria from model flags.

Pending decisions for the advisor:

1. Is the target statistical rarity, operational deviation, or separate labels for both?
   How should legitimate expensive orders and unusually fast nonnegative durations be treated?
2. Define Normal, Suspicious and Anomaly with evidence requirements and an explicit
   insufficient-evidence policy. These categories must not imply verified fraud.
3. Who reviews, how many independent reviewers, and how are disagreements adjudicated?
4. Approve or revise the pilot design and plan a separate adequate evaluation sample.
5. Confirm retrospective scope; payment records have no event/arrival timestamps.

Before real review, preserve a separate approved criteria file with the same schema,
status approved, a protocol ID, definitions, approving person and approval reference.
The CLI checks completeness and binds the file hash to a reviewer's session. This is
an audit of a human declaration, not authentication of the advisor's approval.

Prepare once from the project root:

```sh
.venv/bin/python scripts/03_anomaly_detection/prepare_business_review.py
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --status
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --preview BR0001
```

Real entry later requires --review --reviewer-id and --approved-criteria. No approved
example is supplied because none has been confirmed. Quit or skip leaves labels blank.
No final test cases are sampled or scored; no old models are deserialized or fitted.
Future revisions must be explicit, versioned and retain earlier evidence and decisions.
