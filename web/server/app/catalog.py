"""Catalog of C09: garages with levels and the current tariff, free spots per hour of a local day.

The catalog is public (no login, decision M10). Closing and opening a level is the staff function of
the card (lab 3): only the role staff, checked on the server before anything else (403 for others)."""

from datetime import date
from typing import Annotated, Any

from fastapi import APIRouter, Query

from . import db
from .problems import Problem
from .timefmt import local_iso
from .users import StaffUser

router = APIRouter(prefix="/v1")


@router.get("/garages")
async def list_garages() -> dict[str, Any]:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            SELECT g.id, g.name, g.tz, t.minor_per_hour, t.currency,
                   l.id AS level_id, l.name AS level_name, l.spots, l.closed
            FROM garage g
            JOIN tariff t ON t.garage_id = g.id
            LEFT JOIN level l ON l.garage_id = g.id
            ORDER BY g.id, l.id
            """
        )
        rows = await cur.fetchall()

    garages: dict[str, dict[str, Any]] = {}
    for r in rows:
        g = garages.setdefault(
            r["id"],
            {
                "id": r["id"],
                "name": r["name"],
                "tz": r["tz"],
                "tariff_minor_per_hour": r["minor_per_hour"],
                "currency": r["currency"],
                "spots": 0,
                "levels": [],
            },
        )
        if r["level_id"] is not None:
            g["levels"].append({"id": r["level_id"], "name": r["level_name"], "spots": r["spots"], "closed": r["closed"]})
            g["spots"] += r["spots"]  # garage.spots = sum of level.spots (decision B7)
    return {"items": list(garages.values())}


@router.get("/levels/{level_id}/hours")
async def level_hours(level_id: str, day: Annotated[date, Query(description="Local date in the garage zone")]) -> dict[str, Any]:
    """All hours of the local day `day` in the zone of the garage: 23 or 25 of them on the days the
    clock changes. An hour without a level_hour row has not been booked yet: free = spots."""
    if not (date(1970, 1, 1) <= day <= date(9998, 12, 30)):  # the local day must convert to a moment
        raise Problem(
            422, "invalid-request", "Invalid request data", errors=[{"field": "query.day", "message": "is out of the supported range"}]
        )
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT l.id, l.garage_id, l.spots, l.closed, g.tz FROM level l JOIN garage g ON g.id = l.garage_id WHERE l.id = %s",
            (level_id,),
        )
        level = await cur.fetchone()
        if level is None:
            raise Problem(404, "level-not-found", "Level not found", "No level with this id")
        cur = await conn.execute(
            """
            SELECT h.hour, COALESCE(lh.free, %(spots)s) AS free
            FROM generate_series(
                     (%(day)s::date)::timestamp AT TIME ZONE %(tz)s,
                     ((%(day)s::date + 1)::timestamp AT TIME ZONE %(tz)s) - interval '1 hour',
                     interval '1 hour') AS h(hour)
            LEFT JOIN level_hour lh ON lh.level_id = %(level)s AND lh.hour = h.hour
            ORDER BY h.hour
            """,
            {"spots": level["spots"], "day": day, "tz": level["tz"], "level": level_id},
        )
        hours = await cur.fetchall()

    tz = level["tz"]
    return {
        "level_id": level["id"],
        "garage_id": level["garage_id"],
        "day": day.isoformat(),
        "tz": tz,
        "spots": level["spots"],
        "closed": level["closed"],
        "hours": [{"hour": local_iso(h["hour"], tz), "free": h["free"], "spots": level["spots"]} for h in hours],
    }


async def _set_closed(level_id: str, closed: bool) -> dict[str, Any]:
    """One UPDATE; a repeat finds the level already in this state and answers 200 the same way.
    Closing refuses only NEW bookings (409 level-closed in POST /v1/bookings, read in its transaction);
    confirmed bookings of the level stay valid (decision B8), their hours are not touched."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "UPDATE level SET closed = %s WHERE id = %s RETURNING id, garage_id, name, spots, closed",
            (closed, level_id),
        )
        row = await cur.fetchone()
    if row is None:
        raise Problem(404, "level-not-found", "Level not found", "No level with this id")
    return dict(row)


@router.post("/levels/{level_id}/close")
async def close_level(level_id: str, _: StaffUser) -> dict[str, Any]:
    return await _set_closed(level_id, True)


@router.post("/levels/{level_id}/open")
async def open_level(level_id: str, _: StaffUser) -> dict[str, Any]:
    return await _set_closed(level_id, False)
