"""Column kinds shared by generate_history.py and generate_events.py.

seq        {"prefix": "l", "width": 9}                     l000000000, l000000001, ...
choice     {"values": ["a", "b"], "weights": [9, 1]}       weights are optional
int        {"min": 1, "max": 60}
float      {"min": 0.5, "max": 99.9, "decimals": 2}
date       {"from": "2025-01-01", "days": 600}
timestamp  {"from": "2025-01-01T08:00:00+00:00", "seconds": 86400}
const      {"value": "x"}
ref        {"table": "books", "hot_share": 0.4}            history only
format     {"template": "{branch_id}-{book_id}-{n:04d}"}   columns defined above, n = row number
custom     {"function": "loan_days"}                       def loan_days(n, rnd, row) from --extend
"""

import importlib.util
import random
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

type Row = dict[str, Any]
type Column = Callable[[int, random.Random, Row], Any]


def make_column(
    name: str, spec: dict[str, Any], keys: dict[str, tuple[list[str], str]], extensions: dict[str, Callable[..., Any]]
) -> Column:
    """Turn one column description into a function (row number, rnd, row so far) -> value."""
    kind = spec["kind"]

    if kind == "seq":
        prefix, width = spec.get("prefix", ""), spec.get("width", 9)
        return lambda n, _rnd, _row: f"{prefix}{n:0{width}d}"

    if kind == "choice":
        values, weights = spec["values"], spec.get("weights")
        return lambda _n, rnd, _row: rnd.choices(values, weights)[0]

    if kind == "int":
        low, high = spec["min"], spec["max"]
        return lambda _n, rnd, _row: rnd.randint(low, high)

    if kind == "float":
        low, high, decimals = spec["min"], spec["max"], spec.get("decimals", 2)
        return lambda _n, rnd, _row: round(rnd.uniform(low, high), decimals)

    if kind == "date":
        first, days = date.fromisoformat(spec["from"]), spec["days"]
        return lambda _n, rnd, _row: first + timedelta(days=rnd.randrange(days))

    if kind == "timestamp":
        first, seconds = datetime.fromisoformat(spec["from"]), spec["seconds"]
        return lambda _n, rnd, _row: first + timedelta(seconds=rnd.randrange(seconds))

    if kind == "const":
        value = spec["value"]
        return lambda _n, _rnd, _row: value

    if kind == "ref":
        if spec["table"] not in keys:
            raise SystemExit(f"column {name}: no reference table {spec['table']!r}")
        table_keys, hot = keys[spec["table"]]
        ordinary = [key for key in table_keys if key != hot]
        share = spec.get("hot_share", 0.0)
        return lambda _n, rnd, _row: hot if rnd.random() < share else rnd.choice(ordinary)

    if kind == "format":
        template = spec["template"]
        return lambda n, _rnd, row: template.format(n=n, **row)

    if kind == "custom":
        function = extensions.get(spec["function"])
        if function is None:
            raise SystemExit(f"column {name}: function {spec['function']!r} not found, pass --extend FILE")
        return function

    raise SystemExit(f"column {name}: unknown kind {kind!r}")


def load_extensions(path: Path | None) -> dict[str, Callable[..., Any]]:
    """Public functions of the --extend file, by name."""
    if path is None:
        return {}

    module_spec = importlib.util.spec_from_file_location("extensions", path)
    if module_spec is None or module_spec.loader is None:
        raise SystemExit(f"cannot load {path}")

    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)

    return {name: value for name, value in vars(module).items() if callable(value) and not name.startswith("_")}


def make_columns(
    columns: dict[str, dict[str, Any]],
    keys: dict[str, tuple[list[str], str]],
    extensions: dict[str, Callable[..., Any]],
) -> dict[str, Column]:
    return {name: make_column(name, spec, keys, extensions) for name, spec in columns.items()}
