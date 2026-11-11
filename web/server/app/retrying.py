"""Retries of the background processes (worker -> simulator, relay -> DPC) and their stop.

A pause grows 1, 2, 4, 8 s and stays at RETRY_CAP_S; a Retry-After of the answer is a lower bound.
There is no attempt limit (no quarantine, D4.4 is not chosen): the message waits at its offset until the
other side is back. SIGTERM ends the wait with Stopping, the offset is not committed, and after the
restart the same message is read again.
"""

import asyncio
import json

from aiokafka.structs import ConsumerRecord

from . import broker, config
from .jsonlog import log


class Stopping(Exception):
    """SIGTERM during a retry: leave the message uncommitted."""


def pause_s(attempt: int, retry_after: str | None = None) -> float:
    pause = min(config.RETRY_BASE_S * 2 ** (attempt - 1), config.RETRY_CAP_S)
    try:
        return max(pause, float(retry_after)) if retry_after else pause
    except ValueError:  # an HTTP date instead of seconds: keep the computed pause
        return pause


async def sleep_or_stop(stop: asyncio.Event, seconds: float) -> None:
    try:
        await asyncio.wait_for(stop.wait(), timeout=seconds)
    except TimeoutError:
        return
    raise Stopping


def decode(record: ConsumerRecord) -> dict | None:
    try:
        return json.loads(record.value)
    except (TypeError, ValueError):
        log("message.undecodable", topic=record.topic, partition=record.partition, offset=record.offset)
        return None


async def consume_until_stopped(topic: str, group: str, handle, stop: asyncio.Event) -> None:
    """broker.consume until SIGTERM. The offset is committed only after `handle` returned; a cancel or
    Stopping inside `handle` leaves it uncommitted."""
    task = asyncio.create_task(broker.consume(topic, group, handle))
    waiter = asyncio.create_task(stop.wait())
    done, _ = await asyncio.wait({task, waiter}, return_when=asyncio.FIRST_COMPLETED)
    task.cancel()
    waiter.cancel()
    results = await asyncio.gather(task, return_exceptions=True)
    if task in done and isinstance(results[0], Exception) and not isinstance(results[0], Stopping):
        raise results[0]  # a crash of the handler: the process exits, Docker restarts it
