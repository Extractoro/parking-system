"""Generate reproducible events and apply a delivery scenario; see the project README."""

import argparse
import hashlib
import json
import random
import sys
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from columns import Column, load_extensions, make_columns

type Event = dict[str, Any]


def seeded(seed: int, purpose: str) -> random.Random:
    digest = hashlib.blake2b(f"{seed}/{purpose}".encode(), digest_size=8).digest()
    return random.Random(int.from_bytes(digest))


def to_json(value: Any) -> str:
    if isinstance(value, datetime):
        return iso(value)
    if isinstance(value, date):
        return value.isoformat()
    raise TypeError(f"cannot write {type(value).__name__}")


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def make_events(
    spec: dict[str, Any], moments: list[datetime], fields: dict[str, Column], purpose: str, prefix: str
) -> list[Event]:
    rnd = seeded(spec.get("seed", 1), purpose)
    id_field, time_field = spec.get("id_field", "event_id"), spec.get("time_field", "event_time")

    events = []
    for number, moment in enumerate(sorted(moments), 1):
        row: Event = {id_field: f"{prefix}{number:08d}", time_field: iso(moment)}
        for name, make in fields.items():
            row[name] = make(number, rnd, row)
        events.append(row)

    return events


def base_events(spec: dict[str, Any], fields: dict[str, Column]) -> list[Event]:
    rnd = seeded(spec.get("seed", 1), "moments")
    start = datetime.fromisoformat(spec["start"])
    minutes = int(spec["minutes"])
    rate = int(spec["rate_per_minute"])
    moments = [start + timedelta(seconds=rnd.uniform(0, minutes * 60)) for _ in range(minutes * rate)]
    return make_events(spec, moments, fields, "fields", "e")


def boundary_events(spec: dict[str, Any], fields: dict[str, Column]) -> list[Event]:
    start = datetime.fromisoformat(spec["start"])
    window = timedelta(minutes=spec.get("window_minutes", 15))
    if window <= timedelta(0):
        raise ValueError("window_minutes must be positive")

    moments = []
    edge = start + window
    while edge < start + timedelta(minutes=int(spec["minutes"])):
        moments.extend((edge - timedelta(milliseconds=1), edge))
        edge += window

    return make_events(spec, moments, fields, "boundary-fields", "boundary-")


def apply_delivery(
    events: list[Event], spec: dict[str, Any], scenario: dict[str, Any]
) -> tuple[list[Event], dict[str, int]]:
    late_random = seeded(spec.get("seed", 1), "delays")
    duplicate_random = seeded(spec.get("seed", 1), "duplicates")
    time_field = spec.get("time_field", "event_time")
    sent: list[tuple[datetime, int, Event]] = []
    stats = {"events": len(events), "late": 0, "duplicates": 0}
    late_share, duplicate_share = scenario.get("late_share", 0), scenario.get("duplicate_share", 0)
    low, high = scenario.get("late_minutes", [1, 10])

    for event in sorted(events, key=lambda row: row[time_field]):
        moment = datetime.fromisoformat(event[time_field])
        arrival = moment
        delayed, delay = late_random.random(), late_random.uniform(low, high)
        if delayed < late_share:
            arrival += timedelta(minutes=delay)
            stats["late"] += 1

        sent.append((arrival, len(sent), dict(event)))
        repeated, delay = duplicate_random.random(), duplicate_random.uniform(1, 30)
        if repeated < duplicate_share:
            sent.append((arrival + timedelta(seconds=delay), len(sent), dict(event)))
            stats["duplicates"] += 1

    sent.sort(key=lambda item: (item[0], item[1]))
    return [event for _, _, event in sent], stats


def generate(
    spec: dict[str, Any], scenario_name: str, extensions: dict[str, Callable[..., Any]] | None = None
) -> tuple[list[Event], dict[str, int]]:
    scenario = spec["scenarios"][scenario_name]
    fields = make_columns(spec["fields"], {}, extensions or {})

    events = base_events(spec, fields)
    if scenario.get("boundary"):
        events.extend(boundary_events(spec, fields))

    delivered, stats = apply_delivery(events, spec, scenario)

    if tick := spec.get("tick"):
        start = datetime.fromisoformat(spec["start"])
        moment = start + timedelta(minutes=int(spec["minutes"]) + tick.get("after_minutes", 30))
        id_field, time_field = spec.get("id_field", "event_id"), spec.get("time_field", "event_time")
        row = {id_field: "tick-" + iso(moment), time_field: iso(moment), **tick.get("fields", {})}
        delivered.append(row)

    return delivered, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", type=Path)
    parser.add_argument("--scenario", default="normal")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--extend", type=Path, help="Python file with your custom field functions")
    args = parser.parse_args()

    spec = json.loads(args.spec.read_text(encoding="utf-8"))
    if args.scenario not in spec["scenarios"]:
        print(f"no scenario {args.scenario!r}; the spec has {sorted(spec['scenarios'])}", file=sys.stderr)
        return 2

    events, stats = generate(spec, args.scenario, load_extensions(args.extend))
    with args.out.open("w", encoding="utf-8", newline="\n") as out:
        for event in events:
            out.write(json.dumps(event, ensure_ascii=False, default=to_json) + "\n")

    print(
        f"{args.out}: {len(events)} lines, {stats['events']} distinct events, "
        f"{stats['late']} late, {stats['duplicates']} duplicates"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
