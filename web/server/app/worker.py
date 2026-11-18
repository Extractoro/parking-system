"""Event handler (O4.3), a separate process: `python -m app.worker` (service worker, GROUP_ID=worker).

Reads domain-events in the group `worker`; handles entry.requested, skips the other types.
For one event:
  1. (worker, event id) already in processed_message -> skip (a repeated delivery).
  2. operation pending -> running, version + 1 (a short transaction).
  3. POST {EXTERNAL_URL}/open with Idempotency-Key = operation id and a timeout of EXTERNAL_TIMEOUT_S.
     The simulator does the external action (opens the barrier); no local transaction spans it.
     200 accepted / rejected is final. 503, a timeout or a network error is temporary: nothing is
     committed, a pause (1, 2, 4, 8, 8 ... s, at least Retry-After) and the same call again with the
     same key, until the simulator answers. The simulator keeps the final result by key, so a repeat
     after a crash returns it and opens nothing twice.
  4. fault("after_external_before_commit").
  5. ONE transaction: processed_message (worker, event id), the operation succeeded (result: opened_at,
     effect_id) or failed (error: the reason), the notification of the user, and for an opened barrier
     the event entry.opened in the outbox. COMMIT, then the offset is committed (broker.consume).

Lab 5: once per ENDING_CHECK_S (60 s) the same process looks for confirmed bookings that end within
ENDING_WINDOW_MIN (15) minutes and adds «Бронювання закінчується за 15 хвилин» to each, one transaction per
booking, source event = booking id + ':ending' (so the next rounds and a second worker add nothing).
`python -m app.worker --ending-once [minutes]` runs one round and exits (used by the tests).
"""

import asyncio
import sys
from datetime import datetime
from typing import Any

import httpx
import psycopg
from psycopg.types.json import Jsonb

from . import background, config, db, events, notifications
from .faults import fault
from .jsonlog import log
from .retrying import Stopping, consume_until_stopped, decode, pause_s, sleep_or_stop
from .timefmt import local_short

CONSUMER = "worker"


async def _processed(event_id: str) -> bool:
    async with db.connection() as conn:
        cur = await conn.execute(
            "SELECT 1 FROM processed_message WHERE consumer = %s AND event_id = %s", (CONSUMER, event_id)
        )
        return await cur.fetchone() is not None


async def _mark_running(op_id: str) -> dict[str, Any] | None:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """UPDATE operation SET state = 'running', version = version + 1, updated_at = now()
               WHERE id = %s AND state = 'pending' RETURNING *""",
            (op_id,),
        )
        op = await cur.fetchone()
        if op is None:  # running already (a restart after a crash) or final
            cur = await conn.execute("SELECT * FROM operation WHERE id = %s", (op_id,))
            op = await cur.fetchone()
    return op


async def _open_barrier(client: httpx.AsyncClient, op: dict[str, Any], data: dict[str, Any],
                        stop: asyncio.Event) -> dict[str, Any]:
    """The final answer of the simulator (accepted / rejected); temporary failures are retried."""
    url = config.EXTERNAL_URL + config.EXTERNAL_ROUTE
    body = {"booking_id": op["booking_id"], "garage_id": data.get("garage_id"), "plate": data.get("plate")}
    attempt = 0
    while True:
        attempt += 1
        retry_after = None
        try:
            r = await client.post(url, json=body, headers={"Idempotency-Key": op["id"]})
            if r.status_code == 200 and r.json().get("status") in ("accepted", "rejected"):
                return r.json()
            reason, retry_after = f"http-{r.status_code}", r.headers.get("Retry-After")
        except httpx.TimeoutException:
            reason = "timeout"
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        pause = pause_s(attempt, retry_after)
        log("worker.external_retry", operation_id=op["id"], attempt=attempt, reason=reason, pause_s=pause)
        await sleep_or_stop(stop, pause)


def _opened_at(result: dict[str, Any]) -> str:
    """The moment of the effect from the simulator (kept by key, so the same on every repeat)."""
    return events.utc_ms(datetime.fromisoformat(result["done_at"]))


async def _commit(event: dict[str, Any], op: dict[str, Any], result: dict[str, Any]) -> str:
    data = event["data"]
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "INSERT INTO processed_message (consumer, event_id) VALUES (%s, %s) ON CONFLICT DO NOTHING RETURNING 1",
            (CONSUMER, event["id"]),
        )
        if await cur.fetchone() is None:
            return "duplicate"  # handled by another delivery that committed first: nothing else is written
        if result["status"] == "accepted":
            opened_at = _opened_at(result)
            await conn.execute(
                """UPDATE operation SET state = 'succeeded', version = version + 1, updated_at = now(), result = %s
                   WHERE id = %s AND state IN ('pending', 'running')""",
                (Jsonb({"opened_at": opened_at, "effect_id": result["effect_id"]}), op["id"]),
            )
            await notifications.add_notification(conn, op["user_id"], "entry.opened", {
                "text": "Barrier opened", "operation_id": op["id"], "booking_id": op["booking_id"],
                "opened_at": opened_at,
            }, event["id"])
            await events.add_event(conn, events.ENTRY_OPENED, op["booking_id"], data["entity_version"], {
                "operation_id": op["id"], "user_id": op["user_id"], "garage_id": data["garage_id"],
                "plate": data["plate"], "opened_at": opened_at, "effect_id": result["effect_id"],
            })
            return "succeeded"
        reason = result.get("reason") or "rejected"
        await conn.execute(
            """UPDATE operation SET state = 'failed', version = version + 1, updated_at = now(), error = %s
               WHERE id = %s AND state IN ('pending', 'running')""",
            (reason, op["id"]),
        )
        await notifications.add_notification(conn, op["user_id"], "entry.failed", {
            "text": f"Barrier not opened: {reason}", "operation_id": op["id"], "booking_id": op["booking_id"],
        }, event["id"])
        return "failed"


def handler(client: httpx.AsyncClient, stop: asyncio.Event):
    async def handle(record) -> None:
        event = decode(record)
        if event is None or event.get("type") != events.ENTRY_REQUESTED:
            return
        op_id = event["data"]["operation_id"]
        if await _processed(event["id"]):
            log("worker.duplicate", event_id=event["id"], operation_id=op_id)
            return
        op = await _mark_running(op_id)
        if op is None:
            log("worker.unknown_operation", event_id=event["id"], operation_id=op_id)
            return
        log("worker.started", event_id=event["id"], operation_id=op_id, booking_id=op["booking_id"])
        result = await _open_barrier(client, op, event["data"], stop)
        fault("after_external_before_commit")  # the barrier is open, the result is not committed yet
        outcome = await _commit(event, op, result)
        log("worker.done", event_id=event["id"], operation_id=op_id, outcome=outcome,
            external=result["status"], reason=result.get("reason"))

    return handle


async def ending_round(window_min: int) -> int:
    """«Бронювання закінчується за 15 хвилин» for every confirmed booking that ends within window_min minutes
    and has no such notification yet; returns how many were added."""
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT b.id, b.user_id, b.plate, b.expires_at, g.tz, g.name AS garage_name, l.name AS level_name
               FROM booking b JOIN level l ON l.id = b.level_id JOIN garage g ON g.id = l.garage_id
               WHERE b.status = 'confirmed' AND b.expires_at > now()
                 AND b.expires_at <= now() + %s * interval '1 minute'
                 AND NOT EXISTS (SELECT 1 FROM notification n
                                 WHERE n.user_id = b.user_id AND n.source_event_id = b.id || ':ending')
               ORDER BY b.expires_at LIMIT 500""",
            (window_min,),
        )
        rows = await cur.fetchall()
    added = 0
    for b in rows:
        async with db.connection() as conn, conn.transaction():
            position = await notifications.add_notification(conn, b["user_id"], notifications.BOOKING_ENDING, {
                "text": f"Booking ends at {local_short(b['expires_at'], b['tz'])}: {b['garage_name']}, "
                        f"{b['level_name']}, {b['plate']}",
                "booking_id": b["id"],
            }, b["id"] + ":ending")
        if position is not None:
            added += 1
            log("notification.ending", booking_id=b["id"], user_id=b["user_id"], position=position)
    return added


async def ending_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            await ending_round(config.ENDING_WINDOW_MIN)
        except (psycopg.Error, OSError) as exc:  # database down: the next round tries again
            log("notification.ending_failed", error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.ENDING_CHECK_S)
        except TimeoutError:
            pass


async def main(stop: asyncio.Event) -> None:
    await db.open_pool(size=3)
    ending = asyncio.create_task(ending_loop(stop))
    try:
        async with httpx.AsyncClient(timeout=config.EXTERNAL_TIMEOUT_S) as client:
            await consume_until_stopped(events.DOMAIN_TOPIC, config.GROUP_ID, handler(client, stop), stop)
    except Stopping:
        pass
    finally:
        stop.set()
        await ending
        await db.close_pool()


async def ending_once(window_min: int) -> None:
    await db.open_pool(size=1)
    try:
        log("notification.ending_round", added=await ending_round(window_min), window_min=window_min)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if "--ending-once" in sys.argv:
        rest = sys.argv[sys.argv.index("--ending-once") + 1:]
        asyncio.run(ending_once(int(rest[0]) if rest else config.ENDING_WINDOW_MIN))
    else:
        background.run(main)
