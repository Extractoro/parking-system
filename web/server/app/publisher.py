"""Outbox publisher (O4.2), a separate process: `python -m app.publisher` (service publisher).

Outbox loop, one transaction per batch:
  SELECT ... FROM outbox WHERE published_at IS NULL ORDER BY seq LIMIT OUTBOX_BATCH FOR UPDATE SKIP LOCKED
  -> fault("publisher_before_send") -> send every row (topic = outbox.topic, key = msg_key, value = the
  stored CloudEvents envelope; acks=all, wait for every confirmation) -> UPDATE published_at = now() -> COMMIT.
A crash before COMMIT leaves the rows unpublished: they are sent again after the restart with the same
event id (at least once; the consumers deduplicate by (consumer, event id)). SKIP LOCKED lets a second
publisher take other rows instead of waiting. An empty batch waits OUTBOX_IDLE_S (200 ms).

Also here: the cleanup of expired idempotency keys and expired login sessions (lab 3), once per
IDEMPOTENCY_CLEANUP_S seconds, in batches of IDEMPOTENCY_CLEANUP_BATCH rows. `python -m app.publisher --cleanup-once` runs one
pass and exits (used by the tests).
"""

import asyncio
import sys

import psycopg
from aiokafka.errors import KafkaError

from . import background, broker, config, db, events, idempotency, users
from .faults import fault
from .jsonlog import log


async def publish_batch(producer) -> int:
    """One batch in one transaction; returns the number of rows published."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """SELECT id, topic, msg_key, type, payload FROM outbox
               WHERE published_at IS NULL ORDER BY seq LIMIT %s FOR UPDATE SKIP LOCKED""",
            (config.OUTBOX_BATCH,),
        )
        rows = await cur.fetchall()
        if not rows:
            return 0
        fault("publisher_before_send")  # the events are stored, none is sent yet
        # send_and_wait: returns after the broker stored the message (acks=all); the order of the batch is kept
        for r in rows:
            await broker.send(producer, r["topic"], r["msg_key"], r["payload"],
                              headers={"content-type": events.CONTENT_TYPE, "ce_type": r["type"], "ce_id": r["id"]})
        await conn.execute("UPDATE outbox SET published_at = now() WHERE id = ANY(%s)", ([r["id"] for r in rows],))
    for r in rows:
        log("outbox.published", event_id=r["id"], type=r["type"], key=r["msg_key"], topic=r["topic"])
    return len(rows)


async def outbox_loop(stop: asyncio.Event) -> None:
    producer = None
    while not stop.is_set():
        try:
            if producer is None:
                producer = await broker.start_producer()
            sent = await publish_batch(producer)
        except (psycopg.Error, OSError, KafkaError) as exc:  # database or broker down: nothing is marked
            log("outbox.publish_failed", error=type(exc).__name__)
            if producer is not None and isinstance(exc, KafkaError):
                await producer.stop()
                producer = None
            sent = 0
            await asyncio.sleep(1.0)
        if not sent:
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.OUTBOX_IDLE_S)
            except TimeoutError:
                pass
    if producer is not None:
        await producer.stop()


async def cleanup_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            deleted = await idempotency.cleanup_expired(config.IDEMPOTENCY_CLEANUP_BATCH)
            if deleted:
                log("idempotency.cleanup", deleted=deleted)
            sessions = await users.cleanup_expired_sessions(config.IDEMPOTENCY_CLEANUP_BATCH)
            if sessions:
                log("session.cleanup", deleted=sessions)
        except (psycopg.Error, OSError) as exc:  # database down: try again on the next round
            log("idempotency.cleanup_failed", error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.IDEMPOTENCY_CLEANUP_S)
        except TimeoutError:
            pass


async def main(stop: asyncio.Event) -> None:
    await db.open_pool(size=2)
    try:
        await asyncio.gather(outbox_loop(stop), cleanup_loop(stop))
    finally:
        await db.close_pool()


async def cleanup_once() -> None:
    await db.open_pool(size=1)
    try:
        deleted = await idempotency.cleanup_expired(config.IDEMPOTENCY_CLEANUP_BATCH)
        log("idempotency.cleanup", deleted=deleted)
        sessions = await users.cleanup_expired_sessions(config.IDEMPOTENCY_CLEANUP_BATCH)
        log("session.cleanup", deleted=sessions)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if "--cleanup-once" in sys.argv:
        asyncio.run(cleanup_once())
    else:
        background.run(main)
