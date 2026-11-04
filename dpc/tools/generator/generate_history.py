# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "pyarrow>=26",
# ]
# ///
"""History generator for lab 4: a fact table and a reference table in Parquet from a JSON spec.

Run from the project root (uv installs pyarrow):

    uv run tools/generator/generate_history.py tools/generator/spec.example.json --out data \\
        --extend tools/generator/columns.example.py
    uv run tools/generator/generate_history.py my-spec.json --out data --rows 20000000 --jobs 8

Column kinds are listed in columns.py and in the README of the course kit.
"""

import argparse
import hashlib
import json
import random
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from columns import Column, Row, load_extensions, make_columns

BLOCK_ROWS = 1024  # part of the data definition - changing it changes every generated row
PART_ROWS = 200 * BLOCK_ROWS


def block_random(seed: int, table: str, block: int) -> random.Random:
    """Depends only on the seed, the table and the block, never on the process or the part."""
    digest = hashlib.blake2b(f"{seed}/{table}/{block}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest))


def build_rows(table: str, seed: int, start: int, count: int, columns: dict[str, Column]) -> pa.Table:
    """Rows start .. start+count-1 of one table (start is a multiple of BLOCK_ROWS)."""
    data: dict[str, list[Any]] = {name: [] for name in columns}
    rnd = block_random(seed, table, start // BLOCK_ROWS)

    for number in range(start, start + count):
        if number % BLOCK_ROWS == 0:
            rnd = block_random(seed, table, number // BLOCK_ROWS)

        row: Row = {}
        for name, column in columns.items():
            row[name] = column(number, rnd, row)
            data[name].append(row[name])

    return pa.table(data)


def write_reference(
    spec: dict[str, Any], seed: int, out: Path, extensions: dict[str, Callable[..., Any]]
) -> dict[str, tuple[list[str], str]]:
    """The reference table; returns its keys and the hot key for `ref` columns."""
    ref = spec["reference"]
    name, key, hot, rows = ref["name"], ref["key"], ref["hot_key"], ref["rows"]
    columns = {
        key: {"kind": "seq", "prefix": ref.get("key_prefix", ""), "width": ref.get("key_width", 5)},
    }
    columns |= ref.get("columns", {})

    table = build_rows(name, seed, 0, rows, make_columns(columns, {}, extensions))
    keys = table.column(key).to_pylist()
    keys[0] = hot  # the first row of the reference is the hot key
    table = table.set_column(table.schema.get_field_index(key), key, pa.array(keys))

    (out / name).mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out / name / f"{name}.parquet")
    print(f"{name}: {rows} rows, hot key {hot!r}")

    return {name: (keys, hot)}


@dataclass(frozen=True)
class FactsJob:
    """Column functions do not pickle, so every process rebuilds them from the spec."""

    spec: dict[str, Any]
    seed: int
    folder: Path
    keys: dict[str, tuple[list[str], str]]
    extend: Path | None


_process_columns: dict[Path, dict[str, Column]] = {}  # one run = one job: keyed by its output folder


def _columns_of(job: FactsJob) -> dict[str, Column]:
    """Column functions of the job, built once per process."""
    if job.folder not in _process_columns:
        _process_columns[job.folder] = make_columns(job.spec["facts"]["columns"], job.keys, load_extensions(job.extend))
    return _process_columns[job.folder]


def write_part(job: FactsJob, part: int, start: int, count: int) -> Counter[str]:
    """One part file; returns how many rows of every `ref` column got the hot key."""
    facts = job.spec["facts"]
    table = build_rows(facts["name"], job.seed, start, count, _columns_of(job))
    pq.write_table(table, job.folder / f"part-{part:05d}.parquet")

    hot_counts: Counter[str] = Counter()
    for column, column_spec in facts["columns"].items():
        if column_spec["kind"] == "ref":
            hot = job.keys[column_spec["table"]][1]
            hot_counts[column] = table.column(column).to_pylist().count(hot)

    return hot_counts


def write_facts(job: FactsJob, rows: int, jobs: int) -> None:
    """Replace existing parts, including excess parts left by a larger prior run."""
    facts = job.spec["facts"]
    name = facts["name"]

    job.folder.mkdir(parents=True, exist_ok=True)
    for old in job.folder.glob("part-*.parquet"):
        old.unlink()

    starts = range(0, rows, PART_ROWS)
    counts = [min(PART_ROWS, rows - start) for start in starts]
    hot_counts: Counter[str] = Counter()
    done = 0

    with ProcessPoolExecutor(max_workers=jobs) as pool:
        results = pool.map(write_part, [job] * len(counts), range(len(counts)), starts, counts)
        for part_counts, count in zip(results, counts, strict=True):
            hot_counts += part_counts
            done += count
            print(f"{name}: {done}/{rows}")

    for column, column_spec in facts["columns"].items():
        if column_spec["kind"] == "ref":
            share = hot_counts[column] / rows
            print(f"{name}.{column}: hot key share {share:.4f} (spec {column_spec.get('hot_share', 0.0)})")


def missing_functions(spec: dict[str, Any], extensions: dict[str, Callable[..., Any]]) -> list[str]:
    """Custom columns of the spec whose functions the --extend file does not define."""
    return [
        column["function"]
        for table in (spec["reference"], spec["facts"])
        for column in table.get("columns", {}).values()
        if column["kind"] == "custom" and column["function"] not in extensions
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--rows", type=int, help="rows of the fact table (default: from the spec)")
    parser.add_argument("--extend", type=Path, help="Python file with functions for custom columns")
    parser.add_argument("--jobs", type=int, default=1, help="processes writing parts at once")
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    rows = spec["facts"]["rows"] if args.rows is None else args.rows
    if not isinstance(rows, int) or isinstance(rows, bool) or rows <= 0 or args.jobs <= 0:
        parser.error("rows and jobs must be positive integers")

    extensions = load_extensions(args.extend)
    if missing := missing_functions(spec, extensions):
        parser.error(f"custom functions not found: {', '.join(missing)}; pass --extend FILE")

    seed = spec.get("seed", 7)
    keys = write_reference(spec, seed, args.out, extensions)
    job = FactsJob(spec, seed, args.out / spec["facts"]["name"], keys, args.extend)
    write_facts(job, rows, args.jobs)


if __name__ == "__main__":
    main()
