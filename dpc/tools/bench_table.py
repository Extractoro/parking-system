"""Speedup table for lab 2 (O2.3) from measured times: median, spread, S_p, E_p.

The measurements are yours (your client, your service); the tool only turns them into the
table of the README. Input: CSV with the columns `workers,seconds`, one row per run.
Standard library only.

Run from the project root:

    python tools/bench_table.py runs.csv

    runs.csv               output
    workers,seconds        | p | T_p, s (median) | spread, s | runs | S_p | E_p |
    1,8.02                 |---|---|---|---|---|---|
    1,7.95                 | 1 | 8.020 | 0.160 | 3 | 1.00 | 1.00 |
    ...                    | 2 | 4.110 | 0.250 | 3 | 1.95 | 0.98 |

S_p = T_1 / T_p and E_p = S_p / p use medians; the spread (max - min) shows the variation
of repeated runs, it is not a significance test.
"""

import argparse
import csv
import math
import statistics
from collections import defaultdict
from pathlib import Path


def read_runs(path: Path) -> dict[int, list[float]]:
    """Measured times by the number of workers, sorted by the number of workers."""
    runs: dict[int, list[float]] = defaultdict(list)
    with path.open(encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        if not reader.fieldnames or not {"workers", "seconds"} <= set(reader.fieldnames):
            raise ValueError("CSV must contain the columns workers and seconds")

        for line, row in enumerate(reader, start=2):
            workers, seconds = int(row["workers"]), float(row["seconds"])
            if workers <= 0 or not math.isfinite(seconds) or seconds <= 0:
                raise ValueError(f"line {line}: workers and seconds must be positive numbers")
            runs[workers].append(seconds)

    if 1 not in runs:
        raise ValueError("the table needs runs with workers = 1 (the baseline T_1)")
    return dict(sorted(runs.items()))


def table(runs: dict[int, list[float]]) -> list[str]:
    baseline = statistics.median(runs[1])
    lines = ["| p | T_p, s (median) | spread, s | runs | S_p | E_p |", "|---|---|---|---|---|---|"]
    for workers, times in runs.items():
        median = statistics.median(times)
        speedup = baseline / median
        spread = max(times) - min(times)
        lines.append(
            f"| {workers} | {median:.3f} | {spread:.3f} | {len(times)} | {speedup:.2f} | {speedup / workers:.2f} |"
        )

    return lines


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("csv", type=Path)
    args = parser.parse_args()

    try:
        runs = read_runs(args.csv)
    except (OSError, ValueError) as error:
        parser.error(str(error))

    print("\n".join(table(runs)))


if __name__ == "__main__":
    main()
