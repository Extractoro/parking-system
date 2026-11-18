"""The notification feed of a user (written in lab 4, delivered by the gateways in lab 5).

The ONLY way to add a notification is `add_notification`, called inside the transaction of its cause
(the booking, the result of the barrier operation, the "ends in 15 minutes" check of the worker). The
counter row notification_seq of the user is locked with FOR UPDATE before the position is taken, so two
transactions of one user get positions one after another and the one with the smaller position commits
first: a reader of the feed never sees position N + 1 before N (lab 5, O5.3). A repeated source event
gives no second row, no outbox message and takes no position (no gap).

In the same transaction the message notification.created goes to the outbox (topic notifications, key =
user id); the publisher sends it to Kafka and both gateways push it to the open connections of the user.

Routes (O5.3): GET /v1/me/notifications?after=&limit= - the own feed after a position, in position order;
GET /v1/me/notifications/head - the largest position (notification_seq.last_position, 0 if none). Every
position up to head is committed already: the counter changes in the transaction that inserts the row.
"""

import re
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Query
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import db, events
from .problems import Problem
from .users import CurrentUser

router = APIRouter(prefix="/v1/me")

# types and their texts in the client (card C09)
BOOKING_CONFIRMED = "booking.confirmed"  # «Місце підтверджено»
BOOKING_ENDING = "booking.ending"  # «Бронювання закінчується за 15 хвилин»
ENTRY_OPENED = "entry.opened"  # «Шлагбаум відкрито»
ENTRY_FAILED = "entry.failed"


def to_json(row: dict[str, Any]) -> dict[str, Any]:
    """One notification as the client gets it, the same in the feed and in the WebSocket."""
    return {
        "id": row["id"],
        "position": row["position"],
        "type": row["type"],
        "text": row["payload"].get("text", ""),
        "created_at": events.utc_ms(row["created_at"]),
    }


async def add_notification(conn: AsyncConnection, user_id: str, type_: str, payload: dict[str, Any],
                           source_event_id: str) -> int | None:
    """Position of the new notification, or None if this source event already has one."""
    await conn.execute(
        "INSERT INTO notification_seq (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,)
    )
    await conn.execute("SELECT last_position FROM notification_seq WHERE user_id = %s FOR UPDATE", (user_id,))
    cur = await conn.execute(
        "SELECT position FROM notification WHERE user_id = %s AND source_event_id = %s", (user_id, source_event_id)
    )
    if await cur.fetchone() is not None:
        return None
    cur = await conn.execute(
        "UPDATE notification_seq SET last_position = last_position + 1 WHERE user_id = %s RETURNING last_position",
        (user_id,),
    )
    position = (await cur.fetchone())["last_position"]
    cur = await conn.execute(
        """INSERT INTO notification (user_id, position, id, type, payload, source_event_id)
           VALUES (%s, %s, %s, %s, %s, %s)
           RETURNING id, position, type, payload, created_at""",
        (user_id, position, str(uuid.uuid4()), type_, Jsonb(payload), source_event_id),
    )
    row = await cur.fetchone()
    await events.add_message(conn, events.NOTIFICATION_CREATED, user_id, {"user_id": user_id, **to_json(row)})
    return position


_AFTER = re.compile(r"^\d{1,18}$")


@router.get("/notifications")
async def list_notifications(
    user: CurrentUser,
    after: str = "0",
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> dict[str, Any]:
    """Own notifications with position > after, ascending. `after` that is not a number -> 400."""
    if not _AFTER.match(after):
        raise Problem(400, "invalid-request", "Invalid request data",
                      errors=[{"field": "query.after", "message": "must be a position (an integer >= 0)"}])
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """SELECT id, position, type, payload, created_at FROM notification
               WHERE user_id = %s AND position > %s ORDER BY position LIMIT %s""",
            (user.id, int(after), limit + 1),
        )
        rows = await cur.fetchall()
    page, more = rows[:limit], len(rows) > limit
    return {
        "items": [to_json(r) for r in page],
        "next_after": page[-1]["position"] if more else None,
    }


@router.get("/notifications/head")
async def notifications_head(user: CurrentUser) -> dict[str, int]:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT last_position FROM notification_seq WHERE user_id = %s", (user.id,))
        row = await cur.fetchone()
    return {"position": row["last_position"] if row else 0}
