from pathlib import Path
import sys

import pandas as pd


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import PROCESSED_DATA_DIR, RAW_DATA_DIR


INPUT_PATH = RAW_DATA_DIR / "olist_orders_dataset.csv"
OUTPUT_PATH = PROCESSED_DATA_DIR / "event_log.csv"

TIMESTAMP_ACTIVITY_MAP = {
    "order_purchase_timestamp": "Purchased",
    "order_approved_at": "Approved",
    "order_delivered_carrier_date": "Handed to Carrier",
    "order_delivered_customer_date": "Delivered",
}


def main() -> None:
    orders = pd.read_csv(INPUT_PATH)
    order_count = len(orders)

    event_frames = []
    for timestamp_col, activity in TIMESTAMP_ACTIVITY_MAP.items():
        events = orders[["order_id", timestamp_col]].copy()
        events = events.rename(
            columns={
                "order_id": "case_id",
                timestamp_col: "timestamp",
            }
        )
        events["activity"] = activity
        event_frames.append(events[["case_id", "activity", "timestamp"]])

    event_log = pd.concat(event_frames, ignore_index=True)
    event_log["timestamp"] = pd.to_datetime(event_log["timestamp"], errors="coerce")
    event_log = event_log.dropna(subset=["timestamp"])
    event_log = event_log.sort_values(["case_id", "timestamp"]).reset_index(drop=True)

    event_log.to_csv(OUTPUT_PATH, index=False)

    print(f"Number of orders: {order_count}")
    print(f"Number of events: {len(event_log)}")
    print(event_log.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
