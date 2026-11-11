"""The notification feed of a user (written in lab 4, delivered by the gateways in lab 5).

The ONLY way to add a notification is `add_notification`, called inside the transaction of its cause
(for the barrier: the transaction of the operation result). The counter row notification_seq of the
user is locked with FOR UPDATE before the position is taken, so two transactions of one user get
positions one after another and the one with the smaller position commits first: a reader of the feed
never sees position N + 1 before N (lab 5, O5.3). A repeated source event gives no second row and takes
no position (no gap).
"""

import uuid
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb


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
    await conn.execute(
        """INSERT INTO notification (user_id, position, id, type, payload, source_event_id)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, position, str(uuid.uuid4()), type_, Jsonb(payload), source_event_id),
    )
    return position
