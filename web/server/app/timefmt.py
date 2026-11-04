"""Moments in responses: ISO 8601 with the UTC offset of the garage zone (garage.tz)."""

from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo


@lru_cache(maxsize=64)
def zone(name: str) -> ZoneInfo:
    return ZoneInfo(name)


def local_iso(moment: datetime, tz: str) -> str:
    """2026-11-20T16:00:00Z in Europe/Kyiv -> '2026-11-20T18:00:00+02:00'."""
    return moment.astimezone(zone(tz)).isoformat()
