"""Bookings of C09: the main operation POST /v1/bookings (O1.4), the list with a cursor (O1.5), one booking.

Invariants (card C09):
  1) in every hour the bookings of a level do not exceed its spots;
  2) a booking of N hours takes all N hours or none.
Both hold because checking and changing every hour and inserting the booking happen in ONE
transaction, and the answer 201 is sent only after COMMIT.
"""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from pydantic import AwareDatetime, BaseModel, Field, field_validator

from . import config, cursor, db, rules
from .problems import Problem
from .timefmt import local_iso
from .users import CurrentUser

router = APIRouter(prefix="/v1/bookings")

MAX_HOURS = 24


class BookingCreate(BaseModel):
    level_id: str = Field(min_length=1, max_length=64)
    # ISO 8601 with an offset; must be the start of an hour (in UTC and so in any whole-hour zone)
    from_hour: AwareDatetime
    hours: int = Field(ge=1, le=MAX_HOURS, strict=True)
    plate: str = Field(min_length=2, max_length=12)

    @field_validator("from_hour")
    @classmethod
    def _start_of_hour(cls, v: datetime) -> datetime:
        if v.minute or v.second or v.microsecond:
            raise ValueError("must be the start of an hour (minutes and seconds = 0)")
        return v

    @field_validator("plate", mode="before")
    @classmethod
    def _strip_plate(cls, v: object) -> object:
        return v.strip() if isinstance(v, str) else v


_BOOKING_SELECT = """
    SELECT b.id, b.user_id, b.level_id, l.garage_id, g.tz, b.plate, b.from_hour, b.hours,
           b.tariff_minor_per_hour, b.price_minor, b.currency, b.status, b.version, b.created_at, b.expires_at
    FROM booking b JOIN level l ON l.id = b.level_id JOIN garage g ON g.id = l.garage_id
"""


def _to_json(row: dict[str, Any]) -> dict[str, Any]:
    tz = row["tz"]
    return {
        "id": row["id"],
        "level_id": row["level_id"],
        "garage_id": row["garage_id"],
        "plate": row["plate"],
        "from_hour": local_iso(row["from_hour"], tz),
        "to_hour": local_iso(row["expires_at"], tz),
        "hours": row["hours"],
        "tariff_minor_per_hour": row["tariff_minor_per_hour"],
        "price_minor": row["price_minor"],
        "currency": row["currency"],
        "status": row["status"],
        "version": row["version"],
        "created_at": local_iso(row["created_at"], tz),
        "expires_at": local_iso(row["expires_at"], tz),
    }


def _no_free_spots(hour: datetime, tz: str) -> Problem:
    at = local_iso(hour, tz)
    return Problem(409, "no-free-spots", "No free spots", f"No free spots at {at}", hour=at)


async def _reserve_hours(conn: AsyncConnection, level_id: str, start: datetime, end: datetime, hours: int, tz: str) -> None:
    """Take one spot in every hour of [start, end) or raise 409; the caller's transaction rolls back."""
    # rows of hours nobody booked yet: free = spots of the level
    await conn.execute(
        """
        INSERT INTO level_hour (level_id, hour, free)
        SELECT l.id, h, l.spots
        FROM level l, generate_series(%(start)s::timestamptz, %(end)s::timestamptz - interval '1 hour', interval '1 hour') h
        WHERE l.id = %(level)s
        ON CONFLICT (level_id, hour) DO NOTHING
        """,
        {"level": level_id, "start": start, "end": end},
    )

    if config.INVARIANT_MODE == "unprotected":
        await _reserve_unprotected(conn, level_id, start, end, tz)
        return

    # Lock the rows in ONE fixed order (by hour): two overlapping multi-hour bookings never wait
    # for each other crosswise, so there is no deadlock. Other bookings of these hours wait here.
    cur = await conn.execute(
        """
        SELECT hour, free FROM level_hour
        WHERE level_id = %s AND hour >= %s AND hour < %s
        ORDER BY hour
        FOR UPDATE
        """,
        (level_id, start, end),
    )
    rows = await cur.fetchall()
    full = next((r["hour"] for r in rows if r["free"] == 0), None)
    if full is not None:
        raise _no_free_spots(full, tz)

    cur = await conn.execute(
        "UPDATE level_hour SET free = free - 1 WHERE level_id = %s AND hour >= %s AND hour < %s AND free > 0",
        (level_id, start, end),
    )
    if cur.rowcount != hours:  # all hours or none (invariant 2): never commit a partial change
        raise RuntimeError(f"reserved {cur.rowcount} of {hours} hours")


async def _reserve_unprotected(conn: AsyncConnection, level_id: str, start: datetime, end: datetime, tz: str) -> None:
    """INVARIANT_MODE=unprotected - for the comparison experiment only, on test data.
    Read without a lock, then write back a value computed in the application (lost update):
    two concurrent requests both see free = 1 and both write 0, so a 1-spot hour gets two bookings."""
    cur = await conn.execute(
        "SELECT hour, free FROM level_hour WHERE level_id = %s AND hour >= %s AND hour < %s ORDER BY hour",
        (level_id, start, end),
    )
    rows = await cur.fetchall()
    full = next((r["hour"] for r in rows if r["free"] == 0), None)
    if full is not None:
        raise _no_free_spots(full, tz)
    await asyncio.sleep(0.05)  # widens the window between the check and the write
    for r in rows:
        await conn.execute(
            "UPDATE level_hour SET free = %s WHERE level_id = %s AND hour = %s", (r["free"] - 1, level_id, r["hour"])
        )


@router.post("", status_code=201)
async def create_booking(
    body: BookingCreate,
    user: CurrentUser,
    # TODO (O2.2): the idempotency mechanism (lab 2). Accepted and not used yet.
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    start = body.from_hour.astimezone(UTC)
    end = start + timedelta(hours=body.hours)
    booking_id = str(uuid.uuid4())

    # the time rule is a pure function; the route gives it the server clock (PC-1, D1.1)
    reason = rules.booking_start_error(start, datetime.now(UTC))
    if reason:
        raise Problem(
            422, "invalid-request", "Invalid request data", errors=[{"field": "body.from_hour", "message": reason}]
        )

    async with db.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                """
                SELECT l.id, l.closed, t.minor_per_hour, t.currency, g.tz
                FROM level l JOIN garage g ON g.id = l.garage_id JOIN tariff t ON t.garage_id = l.garage_id
                WHERE l.id = %s
                """,
                (body.level_id,),
            )
            level = await cur.fetchone()
            if level is None:
                raise Problem(404, "level-not-found", "Level not found", f"No level {body.level_id!r}")
            if level["closed"]:
                raise Problem(409, "level-closed", "Level is closed", "The level takes no new bookings")

            await _reserve_hours(conn, body.level_id, start, end, body.hours, level["tz"])

            tariff = level["minor_per_hour"]  # the tariff at booking time is kept in the booking
            await conn.execute(
                """
                INSERT INTO booking (id, user_id, level_id, plate, from_hour, hours, tariff_minor_per_hour,
                                     price_minor, currency, status, expires_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
                """,
                (booking_id, user.id, body.level_id, body.plate, start, body.hours, tariff,
                 tariff * body.hours, level["currency"], end),
            )
            cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
            row = await cur.fetchone()
        # leaving the block above is the COMMIT; only after it the answer says "created" (201)

    return JSONResponse(_to_json(row), status_code=201, headers={"Location": f"/v1/bookings/{booking_id}"})


@router.get("")
async def list_bookings(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    cursor_param: Annotated[str | None, Query(alias="cursor")] = None,
) -> dict[str, Any]:
    """Own bookings, newest first: ORDER BY created_at DESC, id DESC, keyset pagination."""
    after = cursor.decode(cursor_param) if cursor_param is not None else None
    query = _BOOKING_SELECT + " WHERE b.user_id = %(user)s"
    params: dict[str, Any] = {"user": user.id, "n": limit + 1}
    if after is not None:
        query += " AND (b.created_at, b.id) < (%(t)s, %(id)s)"
        params.update(t=after[0], id=after[1])
    query += " ORDER BY b.created_at DESC, b.id DESC LIMIT %(n)s"

    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(query, params)
        rows = await cur.fetchall()

    page, more = rows[:limit], len(rows) > limit
    last = page[-1] if page else None
    return {
        "items": [_to_json(r) for r in page],
        "next_cursor": cursor.encode(last["created_at"], last["id"]) if more and last else None,
    }


@router.get("/{booking_id}")
async def get_booking(booking_id: str, user: CurrentUser) -> dict[str, Any]:
    """Someone else's booking and a missing one give the same 404: the owner is part of the query."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s AND b.user_id = %s", (booking_id, user.id))
        row = await cur.fetchone()
    if row is None:
        raise Problem(404, "booking-not-found", "Booking not found", f"No booking {booking_id!r}")
    return _to_json(row)
