# Blind Reviewer Preparation Guide

Status: waiting for agreed criteria. This is a read-only preview packet.

1. Read only this folder. Do not consult model flags, sampling strata, analyst
   keys, the integrated evidence output or previously judged cases.
2. Inspect `reviewer/cases.csv`, `payment_records.csv` and `item_records.csv`.
   BR identifiers refer to actual Olist orders, not generated examples.
3. Keep all labels, confidence, reasons, notes and review timestamps blank until
   the protocol and reviewer responsibilities have been agreed.
4. Review raw evidence against the future approved criteria, not against a
   model verdict. A large price, multiple payments, many installments, short
   positive duration or missing event does not by itself establish fraud.
5. If evidence is insufficient, do not guess. The current interface can skip
   a case without a label. Whether insufficient evidence needs its own class
   must be decided before entry; it must not automatically become Suspicious.
6. Keep each reviewer's independent decisions separate. Disagreements and later
   corrections require an auditable revision/adjudication procedure.

Confidence Low/Medium/High is a human judgment, not a calibrated probability.
The original evidence is a retrospective snapshot; not all fields have record
arrival times. The packet does not establish what was known in real time.

The coordinator can preview without creating a review session:

```sh
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --status
.venv/bin/python scripts/03_anomaly_detection/review_business_cases.py --preview BR0001
```

After genuine approval, use the original canonical batch, a separate approved
criteria file and a distinct reviewer ID. Do not edit the frozen draft or start
a competing review session inside this preview copy.
