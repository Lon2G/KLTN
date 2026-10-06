from pathlib import Path
import sys

import pandas as pd
import pm4py
from pm4py.objects.petri_net.obj import Marking, PetriNet
from pm4py.objects.petri_net.utils import petri_utils


PROJECT_ROOT = Path(__file__).resolve()
while PROJECT_ROOT.name != "VS_KL" and PROJECT_ROOT.parent != PROJECT_ROOT:
    PROJECT_ROOT = PROJECT_ROOT.parent
sys.path.insert(0, str(PROJECT_ROOT))

from project_paths import PROCESSED_DATA_DIR


INPUT_PATH = PROCESSED_DATA_DIR / "event_log.csv"
OUTPUT_PATH = PROCESSED_DATA_DIR / "conformance_results.csv"

CASE_ID_COL = "case:concept:name"
ACTIVITY_COL = "concept:name"
TIMESTAMP_COL = "time:timestamp"

REFERENCE_PROCESS = [
    "Purchased",
    "Approved",
    "Handed to Carrier",
    "Delivered",
]


def read_event_log(input_path: Path = INPUT_PATH) -> pd.DataFrame:
    """Read the event log CSV file."""
    return pd.read_csv(input_path)


def prepare_pm4py_dataframe(event_log: pd.DataFrame) -> pd.DataFrame:
    """Rename columns and format the dataframe according to PM4Py conventions."""
    pm4py_log = event_log.rename(
        columns={
            "case_id": CASE_ID_COL,
            "activity": ACTIVITY_COL,
            "timestamp": TIMESTAMP_COL,
        }
    ).copy()

    pm4py_log[TIMESTAMP_COL] = pd.to_datetime(pm4py_log[TIMESTAMP_COL], errors="coerce")
    pm4py_log = pm4py.format_dataframe(
        pm4py_log,
        case_id=CASE_ID_COL,
        activity_key=ACTIVITY_COL,
        timestamp_key=TIMESTAMP_COL,
    )
    return pm4py_log


def build_reference_petri_net() -> tuple[PetriNet, Marking, Marking]:
    """Build a sequential Petri net for the reference process."""
    net = PetriNet("olist_reference_process")
    places = [PetriNet.Place(f"p{i}") for i in range(len(REFERENCE_PROCESS) + 1)]
    transitions = [
        PetriNet.Transition(activity, activity) for activity in REFERENCE_PROCESS
    ]

    for place in places:
        net.places.add(place)
    for transition in transitions:
        net.transitions.add(transition)

    for index, transition in enumerate(transitions):
        petri_utils.add_arc_from_to(places[index], transition, net)
        petri_utils.add_arc_from_to(transition, places[index + 1], net)

    initial_marking = Marking({places[0]: 1})
    final_marking = Marking({places[-1]: 1})
    return net, initial_marking, final_marking


def build_case_metadata(pm4py_log: pd.DataFrame) -> pd.DataFrame:
    """Calculate variant, event count, missing events, and reversed order flag per case."""
    sorted_log = pm4py_log.sort_values(
        [CASE_ID_COL, TIMESTAMP_COL],
        kind="mergesort",
    )

    case_metadata = (
        sorted_log.groupby(CASE_ID_COL)
        .agg(
            variant=(ACTIVITY_COL, lambda activities: " -> ".join(activities)),
            event_count=(ACTIVITY_COL, "size"),
        )
        .reset_index()
    )

    activity_sets = sorted_log.groupby(CASE_ID_COL)[ACTIVITY_COL].agg(set)
    case_metadata["missing_event_count"] = case_metadata[CASE_ID_COL].map(
        activity_sets.apply(
            lambda activities: len(set(REFERENCE_PROCESS) - activities)
        )
    )

    activity_times = sorted_log.pivot_table(
        index=CASE_ID_COL,
        columns=ACTIVITY_COL,
        values=TIMESTAMP_COL,
        aggfunc="min",
    )
    case_metadata["reversed_order_flag"] = case_metadata[CASE_ID_COL].map(
        detect_reversed_order(activity_times)
    )

    return case_metadata


def detect_reversed_order(activity_times: pd.DataFrame) -> pd.Series:
    """Flag cases where any adjacent reference-process timestamps are reversed."""
    reversed_flags = pd.Series(False, index=activity_times.index)

    for start_activity, end_activity in zip(REFERENCE_PROCESS, REFERENCE_PROCESS[1:]):
        has_both_events = (
            activity_times[start_activity].notna()
            & activity_times[end_activity].notna()
        )
        reversed_pair = (
            has_both_events
            & (activity_times[end_activity] < activity_times[start_activity])
        )
        reversed_flags = reversed_flags | reversed_pair

    return reversed_flags


def calculate_trace_fitness(pm4py_log: pd.DataFrame) -> pd.DataFrame:
    """Run PM4Py token-based replay and return trace fitness per case.

    Token-based replay replays each trace on the reference Petri net. The returned
    trace fitness measures how well the observed events can be replayed by the
    reference process; this script stores the score only and does not label
    low-fitness cases as anomalies.
    """
    net, initial_marking, final_marking = build_reference_petri_net()
    replay_results = pm4py.conformance_diagnostics_token_based_replay(
        pm4py_log,
        net,
        initial_marking,
        final_marking,
        activity_key=ACTIVITY_COL,
        timestamp_key=TIMESTAMP_COL,
        case_id_key=CASE_ID_COL,
    )

    case_ids = (
        pm4py_log.sort_values([CASE_ID_COL, TIMESTAMP_COL], kind="mergesort")[
            CASE_ID_COL
        ]
        .drop_duplicates()
        .tolist()
    )

    fitness_rows = []
    for case_id, replay_result in zip(case_ids, replay_results):
        fitness_rows.append(
            {
                CASE_ID_COL: case_id,
                "trace_fitness": replay_result["trace_fitness"],
            }
        )

    return pd.DataFrame(fitness_rows)


def build_conformance_results(pm4py_log: pd.DataFrame) -> pd.DataFrame:
    """Combine PM4Py fitness results with case metadata."""
    fitness = calculate_trace_fitness(pm4py_log)
    case_metadata = build_case_metadata(pm4py_log)

    results = fitness.merge(case_metadata, on=CASE_ID_COL, how="left")
    results = results.rename(columns={CASE_ID_COL: "case_id"})
    results = results[
        [
            "case_id",
            "trace_fitness",
            "variant",
            "event_count",
            "missing_event_count",
            "reversed_order_flag",
        ]
    ]
    return results


def calculate_fitness_bands(conformance_results: pd.DataFrame) -> pd.DataFrame:
    """Count cases by requested trace fitness bands."""
    fitness = conformance_results["trace_fitness"]
    bands = {
        "1.0": (fitness == 1.0).sum(),
        "0.8-<1.0": ((fitness >= 0.8) & (fitness < 1.0)).sum(),
        "0.5-<0.8": ((fitness >= 0.5) & (fitness < 0.8)).sum(),
        "<0.5": (fitness < 0.5).sum(),
    }
    return pd.DataFrame(
        [{"fitness_band": band, "case_count": int(count)} for band, count in bands.items()]
    )


def save_results(conformance_results: pd.DataFrame) -> None:
    """Save case-level conformance results."""
    conformance_results.to_csv(OUTPUT_PATH, index=False)


def print_summary(conformance_results: pd.DataFrame) -> None:
    """Print conformance summary and the 20 lowest-fitness cases."""
    print("Conformance analysis")
    print(f"Total cases: {len(conformance_results)}")
    print()

    print("Fitness bands")
    fitness_bands = calculate_fitness_bands(conformance_results)
    print(fitness_bands.to_string(index=False))
    print()

    print("20 lowest-fitness cases")
    lowest_fitness = conformance_results.sort_values(
        ["trace_fitness", "case_id"],
        ascending=[True, True],
    ).head(20)
    print(lowest_fitness.to_string(index=False))
    print()
    print(f"Saved conformance results: {OUTPUT_PATH}")


def main() -> None:
    event_log = read_event_log()
    pm4py_log = prepare_pm4py_dataframe(event_log)
    conformance_results = build_conformance_results(pm4py_log)

    save_results(conformance_results)
    print_summary(conformance_results)


if __name__ == "__main__":
    main()
