"""Outbox publisher (O4.2), a separate process: `python -m app.publisher` (service publisher).

Stub: starts and waits. Yours: read a batch of the outbox with FOR UPDATE SKIP LOCKED, send it
with broker.send() and mark it published after the broker confirmed; fault("publisher_before_send").

Already here: the cleanup of expired idempotency keys, once per IDEMPOTENCY_CLEANUP_S seconds,
in batches of IDEMPOTENCY_CLEANUP_BATCH rows. `python -m app.publisher --cleanup-once` runs one
pass and exits (used by the tests).
"""

import asyncio
import sys

import psycopg

from . import background, config, db, idempotency
from .jsonlog import log


async def cleanup_loop(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            deleted = await idempotency.cleanup_expired(config.IDEMPOTENCY_CLEANUP_BATCH)
            if deleted:
                log("idempotency.cleanup", deleted=deleted)
        except (psycopg.Error, OSError) as exc:  # database down: try again on the next round
            log("idempotency.cleanup_failed", error=type(exc).__name__)
        try:
            await asyncio.wait_for(stop.wait(), timeout=config.IDEMPOTENCY_CLEANUP_S)
        except TimeoutError:
            pass


async def main(stop: asyncio.Event) -> None:
    await db.open_pool(size=2)
    try:
        log("todo", item="O4.2", what="outbox publisher")
        await cleanup_loop(stop)
    finally:
        await db.close_pool()


async def cleanup_once() -> None:
    await db.open_pool(size=1)
    try:
        deleted = await idempotency.cleanup_expired(config.IDEMPOTENCY_CLEANUP_BATCH)
        log("idempotency.cleanup", deleted=deleted)
    finally:
        await db.close_pool()


if __name__ == "__main__":
    if "--cleanup-once" in sys.argv:
        asyncio.run(cleanup_once())
    else:
        background.run(main)
