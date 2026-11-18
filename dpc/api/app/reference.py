"""O4.3: the independent reference of the batch report, without Spark.

Reads the same Parquet set as the published report with pyarrow.dataset batch by batch and folds every
row into a dict garage_id -> [count, sum_minutes, sum_amount] of Python int (exact, unbounded); the
average is sum / count, computed once per garage. The names come from the reference table. Spark and
this module share only the input files: no SQL engine, no join, no grouping operator.

compare() matches two row lists by keys: counts and sums must be equal, floats within a tolerance.
"""

import math
from pathlib import Path
from typing import Any

import pyarrow.dataset as ds

BATCH_ROWS = 262_144
INT_FIELDS = ("sessions", "total_minutes", "revenue_minor")
FLOAT_FIELDS = ("avg_minutes",)


def compute(facts: str | Path, dim: str | Path) -> list[dict[str, Any]]:
    totals: dict[str, list[int]] = {}
    for batch in ds.dataset(str(facts), format="parquet").to_batches(
        columns=["garage_id", "minutes", "amount_minor"], batch_size=BATCH_ROWS
    ):
        garages = batch.column("garage_id").to_pylist()
        minutes = batch.column("minutes").to_pylist()
        amounts = batch.column("amount_minor").to_pylist()
        for garage, mins, amount in zip(garages, minutes, amounts, strict=True):
            acc = totals.get(garage)
            if acc is None:
                acc = totals[garage] = [0, 0, 0]
            acc[0] += 1
            acc[1] += mins
            acc[2] += amount

    names = dict(
        zip(
            *ds.dataset(str(dim), format="parquet").to_table(columns=["garage_id", "name"]).to_pydict().values(),
            strict=True,
        )
    )
    # inner join of the report: a fact whose garage is missing from the reference table is not reported
    return [
        {
            "garage_id": garage,
            "name": names[garage],
            "sessions": count,
            "total_minutes": total_minutes,
            "avg_minutes": total_minutes / count,
            "revenue_minor": revenue,
        }
        for garage, (count, total_minutes, revenue) in sorted(totals.items())
        if garage in names
    ]


def signature(path: str | Path) -> tuple[tuple[str, int, int], ...]:
    """Names, sizes and modification times of the files of a set: a regenerated set is a new key."""
    root = Path(path)
    files = [root] if root.is_file() else sorted(p for p in root.rglob("*") if p.is_file())
    return tuple((str(p), p.stat().st_size, p.stat().st_mtime_ns) for p in files)


def compare(
    report: list[dict[str, Any]], reference: list[dict[str, Any]], keys: list[str], tolerance: float
) -> list[str]:
    """Differences by keys (empty list = the report equals the reference)."""
    problems: list[str] = []
    left = {tuple(r[k] for k in keys): r for r in report}
    right = {tuple(r[k] for k in keys): r for r in reference}
    if len(left) != len(report) or len(right) != len(reference):
        problems.append("duplicate keys")
    for key in sorted(set(left) | set(right)):
        if key not in left:
            problems.append(f"{key}: missing in the report")
            continue
        if key not in right:
            problems.append(f"{key}: missing in the reference")
            continue
        a, b = left[key], right[key]
        for field in sorted(set(a) | set(b)):
            x, y = a.get(field), b.get(field)
            if type(x) is float and type(y) is float:  # averages: within the tolerance
                ok = math.isclose(x, y, rel_tol=tolerance, abs_tol=tolerance)
            else:  # keys, names, counts and sums: the same value of the same type
                ok = x == y and type(x) is type(y)
            if not ok:
                problems.append(f"{key}.{field}: report {x!r} != reference {y!r}")
    return problems
