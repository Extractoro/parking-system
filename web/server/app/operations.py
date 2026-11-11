"""Long operation of C09 (Web lab 4): open the barrier by a booking.

POST /v1/bookings/{id}/entries  (Idempotency-Key)  -> 202, Location: /v1/operations/{op}, Retry-After: 2
GET  /v1/operations/{op}                           -> {id, kind, booking_id, state, version, result, error}
                                                      (+ status = state, the field name of the kit contract)

The request only records the intent: ONE transaction inserts the operation (pending) and the event
entry.requested into the outbox, and 202 leaves after COMMIT. The publisher sends the event to Kafka,
the worker calls the barrier simulator and commits the result (succeeded / failed), the relay passes
an opened barrier to DPC as an entry. The route never calls the simulator nor the broker.

Checks, in this order, inside the transaction that holds the booking row (FOR UPDATE, so two keys of
one booking and a cancellation are serialised): own booking (else 404, as any foreign booking),
status confirmed (409 booking-not-active), no accepted entry in DPC and no succeeded operation
(409 already-entered), no operation in progress (409 entry-in-progress), now inside
[from_hour, from_hour + hours) - the pure rule rules.can_enter with the server clock (409 outside-interval).
A retry with the same key returns the stored 202 with the same operation (app/idempotency.py).
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse
from psycopg import AsyncConnection

from . import db, events, idempotency, rules
from .idempotency import Outcome
from .problems import MEDIA_TYPE, Problem
from .users import CurrentUser

router = APIRouter(prefix="/v1")

RETRY_AFTER_S = 2
FINAL = ("succeeded", "failed")


def view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "booking_id": row["booking_id"],
        "state": row["state"],
        "status": row["state"],  # the same value under the name of the kit contract (PC-13)
        "version": row["version"],
        "result": row["result"],
        "error": row["error"],
        "created_at": events.utc_ms(row["created_at"]),
        "updated_at": events.utc_ms(row["updated_at"]),
    }


def _booking_not_found() -> Problem:
    return Problem(404, "booking-not-found", "Booking not found", "No booking with this id for this user")


async def _start_entry(conn: AsyncConnection, booking_id: str, user_id: str) -> Outcome:
    cur = await conn.execute(
        """
        SELECT b.id, b.version, b.status, b.plate, b.from_hour, b.hours, l.garage_id,
               EXISTS (SELECT 1 FROM entry e WHERE e.booking_id = b.id AND e.direction = 'in') AS entered
        FROM booking b JOIN level l ON l.id = b.level_id
        WHERE b.id = %s AND b.user_id = %s
        FOR UPDATE OF b
        """,
        (booking_id, user_id),
    )
    b = await cur.fetchone()
    if b is None:
        raise _booking_not_found()
    if b["status"] != "confirmed":
        raise Problem(409, "booking-not-active", "Booking is not active", f"The booking is {b['status']}")

    cur = await conn.execute(
        "SELECT id, state FROM operation WHERE booking_id = %s AND kind = 'entry' AND state <> 'failed'",
        (booking_id,),
    )
    live = await cur.fetchone()
    if b["entered"] or (live and live["state"] == "succeeded"):
        raise Problem(409, "already-entered", "Already entered", "The barrier was already opened for this booking")
    if live is not None:
        raise Problem(409, "entry-in-progress", "Entry is in progress",
                      "The barrier is being opened for this booking; follow the operation",
                      headers={"Location": f"/v1/operations/{live['id']}"}, operation_id=live["id"])
    if not rules.can_enter(b["from_hour"], b["hours"], datetime.now(UTC)):
        raise Problem(409, "outside-interval", "Outside the booked interval",
                      "Entry is possible only inside the booked hours")

    op_id = str(uuid.uuid4())
    cur = await conn.execute(
        """INSERT INTO operation (id, user_id, kind, booking_id, state) VALUES (%s, %s, 'entry', %s, 'pending')
           RETURNING *""",
        (op_id, user_id, booking_id),
    )
    op = await cur.fetchone()
    await events.add_event(conn, events.ENTRY_REQUESTED, booking_id, b["version"], {
        "operation_id": op_id,
        "user_id": user_id,
        "garage_id": b["garage_id"],
        "plate": b["plate"],
    })
    # 202 leaves the server only after the COMMIT in idempotency.execute
    return Outcome(202, view(op), {"Location": f"/v1/operations/{op_id}", "Retry-After": str(RETRY_AFTER_S)})


@router.post("/bookings/{booking_id}/entries", status_code=202)
async def create_entry(
    request: Request,
    booking_id: str,
    user: CurrentUser,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    key = idempotency.require_key(idempotency_key)
    # no body: the request is the booking of the path; the same key with another booking -> 422
    req_hash = idempotency.request_hash("POST", "/v1/bookings/{id}/entries", {"booking_id": booking_id})

    async def start(conn: AsyncConnection) -> Outcome:
        return await _start_entry(conn, booking_id, user.id)

    outcome = await idempotency.execute(user.id, "entry", key, req_hash, request.url.path, start)
    headers = dict(outcome.headers)
    if outcome.replayed:
        headers["Idempotent-Replayed"] = "true"
    media = MEDIA_TYPE if outcome.status >= 400 else "application/json"
    return JSONResponse(outcome.body, status_code=outcome.status, headers=headers, media_type=media)


@router.get("/operations/{op_id}")
async def get_operation(op_id: str, user: CurrentUser) -> JSONResponse:
    """Only the owner: a foreign operation and a missing one give the same 404."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT * FROM operation WHERE id = %s AND user_id = %s", (op_id, user.id))
        row = await cur.fetchone()
    if row is None:
        raise Problem(404, "operation-not-found", "Operation not found", "No operation with this id for this user")
    headers = {} if row["state"] in FINAL else {"Retry-After": str(RETRY_AFTER_S)}
    return JSONResponse(view(row), headers=headers)
