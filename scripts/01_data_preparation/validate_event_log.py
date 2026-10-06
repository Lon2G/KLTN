from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import PROCESSED_DATA_DIR


INPUT_PATH = PROCESSED_DATA_DIR / "event_log.csv"

ACTIVITIES = [
    "Purchased",
    "Approved",
    "Handed to Carrier",
    "Delivered",
]


def main() -> None:
    event_log = pd.read_csv(INPUT_PATH)
    event_log["timestamp"] = pd.to_datetime(event_log["timestamp"], errors="coerce")

    total_cases = event_log["case_id"].nunique()
    total_events = len(event_log)
    activity_counts = event_log["activity"].value_counts().reindex(ACTIVITIES, fill_value=0)
    null_timestamps = event_log["timestamp"].isna().sum()
    duplicate_events = event_log.duplicated(
        subset=["case_id", "activity", "timestamp"],
        keep=False,
    ).sum()

    events_per_case = event_log.groupby("case_id").size()
    case_event_counts = events_per_case.value_counts().reindex([4, 3, 2, 1], fill_value=0)

    case_activity_times = event_log.pivot_table(
        index="case_id",
        columns="activity",
        values="timestamp",
        aggfunc="min",
    )

    carrier_before_approved = (
        case_activity_times["Handed to Carrier"].notna()
        & case_activity_times["Approved"].notna()
        & (case_activity_times["Handed to Carrier"] < case_activity_times["Approved"])
    ).sum()

    delivered_before_carrier = (
        case_activity_times["Delivered"].notna()
        & case_activity_times["Handed to Carrier"].notna()
        & (case_activity_times["Delivered"] < case_activity_times["Handed to Carrier"])
    ).sum()

    print(f"Total unique case_id: {total_cases}")
    print(f"Total events: {total_events}")
    print()
    print("Activity counts:")
    for activity, count in activity_counts.items():
        print(f"  {activity}: {count}")
    print()
    print(f"Null timestamps: {null_timestamps}")
    print(f"Duplicate events: {duplicate_events}")
    print()
    print("Cases by number of events:")
    for event_count, case_count in case_event_counts.items():
        print(f"  {event_count} events: {case_count}")
    print()
    print("Abnormal timestamp order:")
    print(f"  Handed to Carrier before Approved: {carrier_before_approved}")
    print(f"  Delivered before Handed to Carrier: {delivered_before_carrier}")


if __name__ == "__main__":
    main()
