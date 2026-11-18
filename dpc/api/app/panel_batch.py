"""DPC lab 5: the windows of the streaming job computed without Spark (O5.5); used by /v1/panel/batch and tests.

Rules = rules of spark/jobs/stream.py: windows of window_minutes from the Unix epoch in UTC, [start, end);
the service event tick is not counted but moves the watermark; a repeated event_id is counted once (the
first delivery, as dropDuplicatesWithinWatermark keeps the first one); only windows that the stream must
have finalized: window end <= max(event_time) - watermark. Lateness is not modelled: a late delivery counts
in its event-time window (the stream may drop it, experiment O5.3).
"""

import json
import os
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

TICK = "tick"
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
MS = timedelta(milliseconds=1)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def epoch_ms(text: str) -> int:
    moment = datetime.fromisoformat(text)
    if moment.tzinfo is None:
        raise ValueError(f"event_time without a zone: {text!r}")
    return (moment - EPOCH) // MS


def compute(lines: list[str], window_minutes: int, watermark_minutes: int) -> dict[str, Any]:
    """Count entries and exits per (garage, window) over the delivered lines; pure, used by the tests too."""
    width = window_minutes * 60_000
    seen: set[str] = set()
    counts: dict[tuple[int, str], list[int]] = {}
    duplicates = ticks = 0
    max_ms: int | None = None

    for line in lines:
        if not line.strip():
            continue
        event = json.loads(line)
        if event["event_id"] in seen:
            duplicates += 1
            continue
        seen.add(event["event_id"])
        at = epoch_ms(event["event_time"])
        max_ms = at if max_ms is None else max(max_ms, at)
        if event["garage_id"] == TICK:
            ticks += 1
            continue
        if event["direction"] not in ("in", "out"):
            raise ValueError(f"event {event['event_id']}: direction {event['direction']!r}")
        cell = counts.setdefault((at - at % width, event["garage_id"]), [0, 0])
        cell[0 if event["direction"] == "in" else 1] += 1

    watermark = None if max_ms is None else max_ms - watermark_minutes * 60_000
    windows = [
        {
            "garage_id": garage_id,
            "window_start": iso(EPOCH + start * MS),
            "window_end": iso(EPOCH + (start + width) * MS),
            "entries": entries,
            "exits": exits,
        }
        for (start, garage_id), (entries, exits) in sorted(counts.items())
        if watermark is not None and start + width <= watermark
    ]
    return {
        "watermark": None if watermark is None else iso(EPOCH + watermark * MS),
        "events": len(seen) - ticks,
        "duplicates": duplicates,
        "ticks": ticks,
        "not_final": sum(1 for (start, _g) in counts if watermark is None or start + width > watermark),
        "windows": windows,
    }


@lru_cache(maxsize=16)
def _compute_file(path: str, _mtime_ns: int, _size: int, window_minutes: int, watermark_minutes: int) -> dict[str, Any]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    return compute(lines, window_minutes, watermark_minutes)


def compute_file(path: str, window_minutes: int, watermark_minutes: int) -> dict[str, Any]:
    """compute() over a file; cached by name, size and modification time."""
    stat = os.stat(path)
    return _compute_file(path, stat.st_mtime_ns, stat.st_size, window_minutes, watermark_minutes)
