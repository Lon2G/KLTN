from pathlib import Path
import json
import joblib
import sys
import numpy as np
import pandas as pd

from sklearn.ensemble import IsolationForest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import StandardScaler


# ============================================================
# CONFIG
# ============================================================

PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import ANOMALY_DATA_DIR, BASELINE_MODEL_DIR, PROCESSED_DATA_DIR


CONFORMANCE_FILE = PROCESSED_DATA_DIR / "conformance_results.csv"
EVENT_LOG_FILE = PROCESSED_DATA_DIR / "event_log.csv"

OUTPUT_FILE = ANOMALY_DATA_DIR / "anomaly_results.csv"
THRESHOLD_FILE = ANOMALY_DATA_DIR / "anomaly_thresholds.csv"

MODEL_FILE = BASELINE_MODEL_DIR / "isolation_forest.joblib"
MODEL_META_FILE = BASELINE_MODEL_DIR / "isolation_forest_metadata.json"

CASE_COLUMN_CANDIDATES = [
    "case_id",
    "case:concept:name",
    "case",
    "order_id",
]

ACTIVITY_COLUMN_CANDIDATES = [
    "activity",
    "concept:name",
    "event",
    "status",
]

TIMESTAMP_COLUMN_CANDIDATES = [
    "timestamp",
    "time:timestamp",
    "event_time",
    "datetime",
]

DURATION_FEATURES = [
    "purchased_to_approved_days",
    "approved_to_carrier_days",
    "carrier_to_delivered_days",
    "total_cycle_time_days",
]

REFERENCE_ACTIVITIES = [
    "Purchased",
    "Approved",
    "Handed to Carrier",
    "Delivered",
]


def detect_column(columns, candidates, column_role):
    """Return the first matching column, preferring known names."""
    normalized_columns = {
        column.lower(): column
        for column in columns
    }

    for candidate in candidates:
        if candidate.lower() in normalized_columns:
            return normalized_columns[candidate.lower()]

    raise ValueError(
        f"Could not detect {column_role} column. "
        f"Expected one of: {', '.join(candidates)}"
    )


def load_and_prepare_event_log(event_log_file):
    """Load event log and normalize case, activity, and timestamp columns."""
    event_log = pd.read_csv(event_log_file)

    case_col = detect_column(
        event_log.columns,
        CASE_COLUMN_CANDIDATES,
        "case id",
    )
    activity_col = detect_column(
        event_log.columns,
        ACTIVITY_COLUMN_CANDIDATES,
        "activity",
    )
    timestamp_col = detect_column(
        event_log.columns,
        TIMESTAMP_COLUMN_CANDIDATES,
        "timestamp",
    )

    event_log = event_log.rename(
        columns={
            case_col: "case_id",
            activity_col: "activity",
            timestamp_col: "timestamp",
        }
    )

    event_log = event_log[
        [
            "case_id",
            "activity",
            "timestamp",
        ]
    ].copy()

    event_log["case_id"] = event_log["case_id"].astype(str)
    event_log["activity"] = event_log["activity"].astype(str)
    event_log["timestamp"] = pd.to_datetime(
        event_log["timestamp"],
        errors="coerce",
    )

    return event_log


def calculate_duration_days(activity_times, start_activity, end_activity):
    """Calculate raw duration in days; reversed timestamps remain negative."""
    if (
        start_activity not in activity_times.columns
        or end_activity not in activity_times.columns
    ):
        return pd.Series(
            np.nan,
            index=activity_times.index,
        )

    return (
        activity_times[end_activity] - activity_times[start_activity]
    ).dt.total_seconds() / 86400


def build_case_level_performance(event_log):
    """Create case-level duration features from the normalized event log."""
    event_count_from_log = (
        event_log.groupby("case_id")
        .size()
        .rename("event_count_from_log")
    )

    activity_times = event_log.pivot_table(
        index="case_id",
        columns="activity",
        values="timestamp",
        aggfunc="min",
    )

    for activity in REFERENCE_ACTIVITIES:
        if activity not in activity_times.columns:
            activity_times[activity] = pd.NaT

    case_level = pd.DataFrame(
        index=activity_times.index
    )

    case_level["purchased_to_approved_days"] = calculate_duration_days(
        activity_times,
        "Purchased",
        "Approved",
    )
    case_level["approved_to_carrier_days"] = calculate_duration_days(
        activity_times,
        "Approved",
        "Handed to Carrier",
    )
    case_level["carrier_to_delivered_days"] = calculate_duration_days(
        activity_times,
        "Handed to Carrier",
        "Delivered",
    )
    case_level["total_cycle_time_days"] = calculate_duration_days(
        activity_times,
        "Purchased",
        "Delivered",
    )

    case_level = case_level.join(
        event_count_from_log,
        how="outer",
    )

    return case_level.reset_index()


def remove_duplicate_columns(df):
    """Remove duplicate columns created by accidental merges."""
    return df.loc[:, ~df.columns.duplicated()].copy()


# ============================================================
# 1. LOAD DATA
# ============================================================

print("=" * 70)
print("HYBRID ANOMALY DETECTION")
print("=" * 70)

print("\n[1/9] Loading data...")

conformance_df = pd.read_csv(CONFORMANCE_FILE)
event_log_df = load_and_prepare_event_log(EVENT_LOG_FILE)

print(f"Conformance cases : {len(conformance_df):,}")
print(f"Event log events  : {len(event_log_df):,}")


# ============================================================
# 2. NORMALIZE CASE ID
# ============================================================

if "case_id" not in conformance_df.columns:
    raise ValueError(
        "conformance_results.csv does not contain 'case_id'."
    )

conformance_df["case_id"] = conformance_df["case_id"].astype(str)


# ============================================================
# 3. BUILD CASE-LEVEL PERFORMANCE + MERGE CONFORMANCE
# ============================================================

print("\n[2/9] Building case-level duration features from event log...")

performance_df = build_case_level_performance(event_log_df)
performance_df["case_id"] = performance_df["case_id"].astype(str)

print(f"Performance cases : {len(performance_df):,}")

print("\n[3/9] Merging case-level performance and conformance data...")

df = pd.merge(
    conformance_df,
    performance_df,
    on="case_id",
    how="left",
    suffixes=("", "_from_log")
)

df = remove_duplicate_columns(df)

merged_case_count = int(
    df["event_count_from_log"]
    .notna()
    .sum()
)

print(f"Merged cases      : {len(df):,}")
print(f"Cases with log data: {merged_case_count:,}")


# ============================================================
# 4. CLEAN IMPORTANT COLUMNS
# ============================================================

print("\n[4/9] Preparing features...")

numeric_defaults = {
    "trace_fitness": 1.0,
    "event_count": 0,
    "missing_event_count": 0,
    "reversed_order_flag": 0,
}

for column, default in numeric_defaults.items():
    if column not in df.columns:
        df[column] = default

    df[column] = pd.to_numeric(
        df[column],
        errors="coerce"
    ).fillna(default)


# ============================================================
# 5. RULE-BASED ANOMALY DETECTION
# ============================================================

print("\n[5/9] Running rule-based detection...")

df["low_trace_fitness_flag"] = (
    df["trace_fitness"] < 1.0
).astype(int)

df["missing_event_flag"] = (
    df["missing_event_count"] > 0
).astype(int)

df["reversed_order_flag"] = (
    df["reversed_order_flag"] > 0
).astype(int)


# Use only the four duration features calculated from event_log.csv.
duration_candidates = [
    col for col in DURATION_FEATURES
    if col in df.columns
]

print("\nDetected duration features:")
for col in duration_candidates:
    print(f"  - {col}")


if duration_candidates:
    negative_duration_mask = pd.Series(
        False,
        index=df.index
    )

    for col in duration_candidates:
        numeric_series = pd.to_numeric(
            df[col],
            errors="coerce"
        )

        negative_duration_mask |= numeric_series < 0

    df["negative_duration_flag"] = (
        negative_duration_mask.astype(int)
    )

else:
    df["negative_duration_flag"] = 0


df["rule_anomaly_flag"] = (
    (df["missing_event_flag"] == 1)
    | (df["reversed_order_flag"] == 1)
    | (df["low_trace_fitness_flag"] == 1)
    | (df["negative_duration_flag"] == 1)
).astype(int)


# ============================================================
# 6. STATISTICAL ANOMALY DETECTION - IQR
# ============================================================

print("\n[6/9] Running statistical anomaly detection (IQR)...")

df["statistical_anomaly_count"] = 0

threshold_records = []

for col in duration_candidates:

    series = pd.to_numeric(
        df[col],
        errors="coerce"
    )

    valid = series.dropna()

    if len(valid) < 10:
        continue

    q1 = valid.quantile(0.25)
    q3 = valid.quantile(0.75)

    iqr = q3 - q1

    lower_bound = q1 - 1.5 * iqr
    upper_bound = q3 + 1.5 * iqr

    # For process duration, long duration is usually the important anomaly.
    # Negative duration is already handled by rule-based detection.
    flag_column = f"{col}_iqr_anomaly"

    df[flag_column] = (
        series > upper_bound
    ).fillna(False).astype(int)

    df["statistical_anomaly_count"] += df[flag_column]

    threshold_records.append(
        {
            "feature": col,
            "q1": q1,
            "q3": q3,
            "iqr": iqr,
            "lower_bound": lower_bound,
            "upper_bound": upper_bound,
        }
    )


df["statistical_anomaly_flag"] = (
    df["statistical_anomaly_count"] > 0
).astype(int)


threshold_df = pd.DataFrame(threshold_records)
threshold_df.to_csv(
    THRESHOLD_FILE,
    index=False
)


# ============================================================
# 7. ISOLATION FOREST
# ============================================================

print("\n[7/9] Running Isolation Forest...")

candidate_features = [
    "event_count",
    "trace_fitness",
    "missing_event_count",
    "reversed_order_flag",
]

candidate_features += duration_candidates

# Remove duplicates
candidate_features = list(
    dict.fromkeys(candidate_features)
)

# Keep only existing numeric features
ml_features = []

for col in candidate_features:

    if col not in df.columns:
        continue

    numeric_values = pd.to_numeric(
        df[col],
        errors="coerce"
    )

    # Skip feature if almost completely empty
    if numeric_values.notna().sum() < 10:
        continue

    df[col] = numeric_values

    ml_features.append(col)


if not ml_features:
    raise ValueError(
        "No usable numeric features found for Isolation Forest."
    )


print("\nIsolation Forest features:")

for feature in ml_features:
    print(f"  - {feature}")


X = df[ml_features].copy()


# Fill missing values using median
imputer = SimpleImputer(strategy="median")

X_imputed = imputer.fit_transform(X)


# Standardize values
scaler = StandardScaler()

X_scaled = scaler.fit_transform(X_imputed)


# Baseline model
isolation_forest = IsolationForest(
    n_estimators=300,
    contamination="auto",
    random_state=42,
    n_jobs=-1
)

isolation_forest.fit(X_scaled)


# sklearn:
#   1  = normal
#  -1  = anomaly
prediction = isolation_forest.predict(X_scaled)

# Higher decision_function = more normal
decision_scores = isolation_forest.decision_function(
    X_scaled
)

# Convert so that higher score = more anomalous
df["isolation_anomaly_score"] = -decision_scores

df["isolation_forest_flag"] = (
    prediction == -1
).astype(int)


# Save fitted components
model_package = {
    "model": isolation_forest,
    "imputer": imputer,
    "scaler": scaler,
    "features": ml_features,
}

joblib.dump(
    model_package,
    MODEL_FILE
)


metadata = {
    "algorithm": "IsolationForest",
    "n_estimators": 300,
    "contamination": "auto",
    "random_state": 42,
    "features": ml_features,
}

with open(
    MODEL_META_FILE,
    "w",
    encoding="utf-8"
) as f:
    json.dump(
        metadata,
        f,
        ensure_ascii=False,
        indent=4
    )


# ============================================================
# 8. HYBRID ANOMALY VOTING
# ============================================================

print("\n[8/9] Combining detectors...")

df["anomaly_vote_count"] = (
    df["rule_anomaly_flag"]
    + df["statistical_anomaly_flag"]
    + df["isolation_forest_flag"]
)


def classify_anomaly_level(vote_count):
    if vote_count == 0:
        return "Normal"

    if vote_count == 1:
        return "Warning"

    if vote_count == 2:
        return "Anomaly"

    return "High-confidence anomaly"


df["anomaly_level"] = (
    df["anomaly_vote_count"]
    .apply(classify_anomaly_level)
)


# Final anomaly:
# at least 2 of 3 detectors agree
df["anomaly_flag"] = (
    df["anomaly_vote_count"] >= 2
).astype(int)


# ============================================================
# GENERATE EXPLAINABLE ANOMALY REASON
# ============================================================

def build_reason(row):

    reasons = []

    if row["missing_event_flag"] == 1:
        reasons.append(
            f"Missing event(s): {int(row['missing_event_count'])}"
        )

    if row["reversed_order_flag"] == 1:
        reasons.append(
            "Reversed activity order"
        )

    if row["trace_fitness"] < 1:
        reasons.append(
            f"Trace fitness={row['trace_fitness']:.3f}"
        )

    if row["negative_duration_flag"] == 1:
        reasons.append(
            "Negative process duration"
        )

    if row["statistical_anomaly_flag"] == 1:

        abnormal_features = []

        for col in duration_candidates:

            flag_col = f"{col}_iqr_anomaly"

            if (
                flag_col in row.index
                and row[flag_col] == 1
            ):
                abnormal_features.append(col)

        if abnormal_features:
            reasons.append(
                "Extreme duration: "
                + ", ".join(abnormal_features)
            )

    if row["isolation_forest_flag"] == 1:
        reasons.append(
            "Isolation Forest anomaly"
        )

    if not reasons:
        return "No anomaly detected"

    return "; ".join(reasons)


df["anomaly_reason"] = df.apply(
    build_reason,
    axis=1
)


# ============================================================
# SAVE OUTPUT
# ============================================================

print("\n[9/9] Saving results...")

# Preferred columns first
preferred_columns = [
    "case_id",
    "variant",
    "trace_fitness",
    "event_count",
    "event_count_from_log",
    "missing_event_count",
    "reversed_order_flag",
]

preferred_columns += duration_candidates

preferred_columns += [
    "rule_anomaly_flag",
    "statistical_anomaly_count",
    "statistical_anomaly_flag",
    "isolation_anomaly_score",
    "isolation_forest_flag",
    "anomaly_vote_count",
    "anomaly_level",
    "anomaly_flag",
    "anomaly_reason",
]

# Keep only columns that exist
preferred_columns = [
    c for c in preferred_columns
    if c in df.columns
]

# Preserve remaining useful columns after them
remaining_columns = [
    c for c in df.columns
    if c not in preferred_columns
]

output_df = df[
    preferred_columns + remaining_columns
]

output_df.to_csv(
    OUTPUT_FILE,
    index=False
)


# ============================================================
# SUMMARY
# ============================================================

total_cases = len(df)

rule_cases = int(
    df["rule_anomaly_flag"].sum()
)

stat_cases = int(
    df["statistical_anomaly_flag"].sum()
)

if_cases = int(
    df["isolation_forest_flag"].sum()
)

final_cases = int(
    df["anomaly_flag"].sum()
)


print("\n" + "=" * 70)
print("HYBRID ANOMALY DETECTION SUMMARY")
print("=" * 70)

print(f"Total cases               : {total_cases:,}")
print(f"Cases merged with log data: {merged_case_count:,}")

print("\nDuration features used:")

for duration_feature in duration_candidates:
    print(f"  - {duration_feature}")

print(
    f"Rule-based anomalies      : "
    f"{rule_cases:,} "
    f"({rule_cases / total_cases * 100:.2f}%)"
)

print(
    f"Statistical anomalies     : "
    f"{stat_cases:,} "
    f"({stat_cases / total_cases * 100:.2f}%)"
)

print(
    f"Isolation Forest anomalies: "
    f"{if_cases:,} "
    f"({if_cases / total_cases * 100:.2f}%)"
)

print(
    f"Final hybrid anomalies    : "
    f"{final_cases:,} "
    f"({final_cases / total_cases * 100:.2f}%)"
)


print("\nAnomaly levels:")

print(
    df["anomaly_level"]
    .value_counts()
    .to_string()
)


print("\nFiles created:")

print(f"  {OUTPUT_FILE}")
print(f"  {THRESHOLD_FILE}")
print(f"  {MODEL_FILE}")
print(f"  {MODEL_META_FILE}")

print("\nDone.")
