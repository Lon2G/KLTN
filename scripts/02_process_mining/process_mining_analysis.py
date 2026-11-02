from pathlib import Path
import sys

import pandas as pd
import pm4py


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from conformance_analysis import (
    ACTIVITY_COL,
    CASE_ID_COL,
    INPUT_PATH,
    TIMESTAMP_COL,
    prepare_pm4py_dataframe,
    read_event_log,
)


def print_basic_statistics(pm4py_log: pd.DataFrame) -> None:
    """Print total number of cases and events."""
    total_cases = pm4py_log[CASE_ID_COL].nunique()
    total_events = len(pm4py_log)

    print("Basic statistics")
    print(f"Total cases: {total_cases}")
    print(f"Total events: {total_events}")
    print()


def get_sorted_variants(pm4py_log: pd.DataFrame) -> list[tuple[tuple[str, ...], int]]:
    """Return process variants sorted by descending frequency."""
    variants = pm4py.get_variants_as_tuples(pm4py_log)
    return sorted(variants.items(), key=lambda item: item[1], reverse=True)


def print_variant_statistics(pm4py_log: pd.DataFrame, top_n: int = 10) -> None:
    """Print total number of variants and the most frequent variants."""
    sorted_variants = get_sorted_variants(pm4py_log)

    print("Process variants")
    print(f"Total process variants: {len(sorted_variants)}")
    print(f"Top {top_n} process variants:")

    for index, (variant, case_count) in enumerate(sorted_variants[:top_n], start=1):
        variant_text = " -> ".join(variant)
        print(f"{index}. {variant_text}: {case_count} cases")
    print()


def discover_dfg(pm4py_log: pd.DataFrame) -> dict[tuple[str, str], int]:
    """Discover the frequency-based Directly-Follows Graph."""
    dfg, _, _ = pm4py.discover_dfg(pm4py_log)
    return dfg


def print_dfg_statistics(dfg: dict[tuple[str, str], int]) -> None:
    """Print all directly-follows relations and their frequencies."""
    sorted_dfg = sorted(dfg.items(), key=lambda item: (-item[1], item[0]))

    print("Directly-Follows Graph")
    for (source_activity, target_activity), frequency in sorted_dfg:
        print(f"{source_activity} -> {target_activity}: {frequency}")


def main() -> None:
    raw_event_log = read_event_log()
    pm4py_log = prepare_pm4py_dataframe(raw_event_log)

    print_basic_statistics(pm4py_log)
    print_variant_statistics(pm4py_log)

    dfg = discover_dfg(pm4py_log)
    print_dfg_statistics(dfg)


if __name__ == "__main__":
    main()
