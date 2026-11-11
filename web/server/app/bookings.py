"""Bookings of C09: the main operation POST /v1/bookings, the list with a cursor, one booking,
the plate change with If-Match (PATCH) and the cancellation (POST .../cancel).

Invariants (card C09):
  1) in every hour the bookings of a level do not exceed its spots;
  2) a booking of N hours takes all N hours or none.
Both hold because checking and changing every hour and inserting the booking happen in ONE
transaction, and the answer 201 is sent only after COMMIT. A retry with the same Idempotency-Key
gets the stored answer and books nothing again (app/idempotency.py).
"""

import asyncio
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from . import config, cursor, db, events, idempotency, rules
from .idempotency import Outcome
from .problems import MEDIA_TYPE, Problem
from .timefmt import local_iso
from .users import CurrentUser

router = APIRouter(prefix="/v1/bookings")

MAX_HOURS = 24
EARLIEST = datetime(1970, 1, 1, tzinfo=UTC)
LATEST = datetime(9998, 12, 31, tzinfo=UTC)


def _strip(v: object) -> object:
    return v.strip() if isinstance(v, str) else v


class BookingCreate(BaseModel):
    level_id: str = Field(min_length=1, max_length=64)
    # ISO 8601 with an offset; must be the start of an hour (in UTC and so in any whole-hour zone)
    from_hour: AwareDatetime
    hours: int = Field(ge=1, le=MAX_HOURS, strict=True)
    plate: str = Field(min_length=2, max_length=12)

    @field_validator("from_hour")
    @classmethod
    def _start_of_hour(cls, v: datetime) -> datetime:
        # checked on the instant, not on the wall clock: 10:00+05:30 is 04:30 UTC, not an hour start
        utc = v.astimezone(UTC)
        if utc.minute or utc.second or utc.microsecond:
            raise ValueError("must be the start of an hour (minutes and seconds = 0)")
        # technical range: the interval end (+24 h) and the local day must stay representable
        if not (EARLIEST <= utc <= LATEST):
            raise ValueError("is out of the supported range")
        return v

    @field_validator("plate", mode="before")
    @classmethod
    def _strip_plate(cls, v: object) -> object:
        return _strip(v)


class BookingPatch(BaseModel):
    """Only the plate may change. Any other field (status, version, user_id, level_id, from_hour,
    hours, ...) is refused with 422 by extra="forbid"."""

    model_config = ConfigDict(extra="forbid")
    plate: str = Field(min_length=2, max_length=12)

    @field_validator("plate", mode="before")
    @classmethod
    def _strip_plate(cls, v: object) -> object:
        return _strip(v)


_BOOKING_SELECT = """
    SELECT b.id, b.user_id, b.level_id, l.garage_id, g.tz, b.plate, b.from_hour, b.hours,
           b.tariff_minor_per_hour, b.price_minor, b.currency, b.status, b.version, b.created_at, b.expires_at,
           e.event_time AS entered_at
    FROM booking b JOIN level l ON l.id = b.level_id JOIN garage g ON g.id = l.garage_id
    LEFT JOIN entry e ON e.booking_id = b.id AND e.direction = 'in'
"""
# entered (decision B3): an accepted entry of DPC by this booking, read from its table `entry`, never
# written by Web. entry has at most one 'in' row per booking (unique index), so the LEFT JOIN does not
# multiply rows and the list order and cursor stay the same.


def _status(row: dict[str, Any]) -> str:
    return "entered" if row["status"] == "confirmed" and row["entered_at"] is not None else row["status"]


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
        "status": _status(row),
        "version": row["version"],
        "created_at": local_iso(row["created_at"], tz),
        "expires_at": local_iso(row["expires_at"], tz),
    }


def _etag(version: int) -> str:
    return f'"{version}"'


def _with_etag(row: dict[str, Any]) -> JSONResponse:
    return JSONResponse(_to_json(row), headers={"ETag": _etag(row["version"])})


def _not_found() -> Problem:
    # the id is not echoed back: it is user input of any length
    return Problem(404, "booking-not-found", "Booking not found", "No booking with this id for this user")


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

    # the mode without protection touches only the test level of the experiment (migration 902)
    if config.INVARIANT_MODE == "unprotected" and level_id == config.UNPROTECTED_LEVEL:
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
    """INVARIANT_MODE=unprotected - for the comparison experiment only, on the test level.
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


def _outcome_response(outcome: Outcome) -> JSONResponse:
    headers = dict(outcome.headers)
    if outcome.replayed:
        headers["Idempotent-Replayed"] = "true"
    media = MEDIA_TYPE if outcome.status >= 400 else "application/json"
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers, media_type=media)


@router.post("", status_code=201)
async def create_booking(
    request: Request,
    body: BookingCreate,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    key = idempotency.require_key(idempotency_key)
    # the hash is of the body AS SENT (canonical JSON), not of the parsed model
    req_hash = idempotency.request_hash("POST", "/v1/bookings", await request.json())

    async def book(conn: AsyncConnection) -> Outcome:
        return await _create(conn, body, user.id)

    outcome = await idempotency.execute(user.id, "create-booking", key, req_hash, request.url.path, book)
    return _outcome_response(outcome)


async def _create(conn: AsyncConnection, body: BookingCreate, user_id: str) -> Outcome:
    """The operation itself; runs inside the transaction of the idempotency key."""
    start = body.from_hour.astimezone(UTC)
    end = start + timedelta(hours=body.hours)
    booking_id = str(uuid.uuid4())

    # the time rule is a pure function; the route gives it the server clock
    reason = rules.booking_start_error(start, datetime.now(UTC))
    if reason:
        raise Problem(
            422, "invalid-request", "Invalid request data", errors=[{"field": "body.from_hour", "message": reason}]
        )

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
        (booking_id, user_id, body.level_id, body.plate, start, body.hours, tariff,
         tariff * body.hours, level["currency"], end),
    )
    # the event in the same transaction (transactional outbox); the publisher sends it to domain-events
    await events.add_event(conn, events.BOOKING_CREATED, booking_id, 1, {
        "user_id": user_id,
        "level_id": body.level_id,
        "from_hour": events.utc_ms(start),
        "hours": body.hours,
    })
    cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
    row = await cur.fetchone()
    # 201 leaves the server only after the COMMIT in idempotency.execute
    return Outcome(201, _to_json(row), {"Location": f"/v1/bookings/{booking_id}", "ETag": _etag(row["version"])})


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
async def get_booking(booking_id: str, user: CurrentUser) -> JSONResponse:
    """Someone else's booking and a missing one give the same 404: the owner is part of the query.
    ETag is the version: PATCH sends it back in If-Match."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s AND b.user_id = %s", (booking_id, user.id))
        row = await cur.fetchone()
    if row is None:
        raise _not_found()
    return _with_etag(row)


_IF_MATCH = re.compile(r'^\s*(?:W/)?"(\d{1,9})"\s*$')


@router.patch("/{booking_id}")
async def update_booking(
    booking_id: str,
    body: BookingPatch,
    user: CurrentUser,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JSONResponse:
    """Conditional change of the plate: ONE UPDATE with the version from If-Match.
    No If-Match -> 428; another version -> 412; not 'confirmed' -> 409; missing or foreign -> 404."""
    if if_match is None:
        raise Problem(428, "precondition-required", "If-Match is required",
                      "Read the booking first and send its ETag in If-Match")
    m = _IF_MATCH.match(if_match)
    version = int(m.group(1)) if m else 0  # an ETag that is not ours matches no version (they start at 1)

    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            UPDATE booking SET plate = %s, version = version + 1
            WHERE id = %s AND user_id = %s AND version = %s AND status = 'confirmed'
            RETURNING id
            """,
            (body.plate, booking_id, user.id, version),
        )
        changed = await cur.fetchone()
        cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s AND b.user_id = %s", (booking_id, user.id))
        row = await cur.fetchone()

    if changed is not None:
        return _with_etag(row)
    # 0 rows changed: the reason is found from the current state
    if row is None:
        raise _not_found()
    if row["version"] != version:
        raise Problem(412, "version-mismatch", "Booking has changed",
                      "The booking was changed after you read it; read it again",
                      headers={"ETag": _etag(row["version"])})
    raise Problem(409, "booking-not-modifiable", "Booking cannot be changed",
                  f"Only a confirmed booking can be changed; this one is {row['status']}")


@router.post("/{booking_id}/cancel")
async def cancel_booking(booking_id: str, user: CurrentUser) -> JSONResponse:
    """Cancel is safe to repeat: a cancelled booking answers 200 with its state and frees nothing.
    One transaction: lock the booking row, then the hour rows in hour order (as booking does)."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            SELECT level_id, from_hour, expires_at, status, from_hour > now() AS not_started
            FROM booking WHERE id = %s AND user_id = %s
            FOR UPDATE
            """,
            (booking_id, user.id),
        )
        b = await cur.fetchone()
        if b is None:
            raise _not_found()
        if b["status"] == "confirmed":
            if not b["not_started"]:
                raise Problem(409, "booking-started", "Booking has already started",
                              "Only a booking whose interval has not started yet can be cancelled")
            hours = (b["level_id"], b["from_hour"], b["expires_at"])
            await conn.execute(
                "SELECT hour FROM level_hour WHERE level_id = %s AND hour >= %s AND hour < %s ORDER BY hour FOR UPDATE",
                hours,
            )
            await conn.execute(
                "UPDATE level_hour SET free = free + 1 WHERE level_id = %s AND hour >= %s AND hour < %s", hours
            )
            await conn.execute("UPDATE booking SET status = 'cancelled', version = version + 1 WHERE id = %s",
                               (booking_id,))
        elif b["status"] != "cancelled":
            raise Problem(409, "booking-not-cancellable", "Booking cannot be cancelled", f"The booking is {b['status']}")
        # cancelled before: nothing is freed again, the answer is the current state
        cur = await conn.execute(_BOOKING_SELECT + " WHERE b.id = %s", (booking_id,))
        row = await cur.fetchone()
    return _with_etag(row)
