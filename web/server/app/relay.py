"""Relay Web -> DPC (joint project), a separate process: `python -m app.relay` (service relay, GROUP_ID=relay).

Reads domain-events in the group `relay`; for entry.opened sends the entry to the DPC api:
  POST {DPC_URL}/v1/entries, Authorization: Bearer DPC_TOKEN, Idempotency-Key: <operation id>
  {event_id: <operation id>, garage_id, plate, direction: "in", event_time: opened_at (UTC, ms, Z), booking_id}
Web never writes the tables of DPC: the entry and the occupancy change are done by DPC itself, which
checks the booking by booking_id (no second spot is taken). A repeated send keeps the event_id, and DPC
answers it with the same request (202, the same Location, Repeated-Request: true), so a repeat after a
crash or a lost answer creates no second entry.

202 -> the offset is committed. 429, 5xx, a timeout or a network error (DPC stopped) -> a pause
(1, 2, 4, 8, 8 ... s, at least Retry-After) and the same request again, the offset stays. Another 4xx is
final (invalid request): logged as relay.refused and committed, a repeat would get the same answer.
"""

import asyncio
from typing import Any

import httpx

from . import background, config, events
from .jsonlog import log
from .retrying import Stopping, consume_until_stopped, decode, pause_s, sleep_or_stop


def entry_body(event: dict[str, Any]) -> dict[str, Any]:
    d = event["data"]
    return {
        "event_id": d["operation_id"],
        "garage_id": d["garage_id"],
        "plate": d["plate"],
        "direction": "in",
        "event_time": d["opened_at"],
        "booking_id": d["entity_id"],
    }


async def send_entry(client: httpx.AsyncClient, event: dict[str, Any], stop: asyncio.Event) -> None:
    body = entry_body(event)
    headers = {"Authorization": f"Bearer {config.DPC_TOKEN}", "Idempotency-Key": body["event_id"]}
    attempt = 0
    while True:
        attempt += 1
        retry_after = None
        try:
            r = await client.post(f"{config.DPC_URL}/v1/entries", json=body, headers=headers)
            if r.status_code == 202:
                log("relay.sent", event_id=event["id"], operation_id=body["event_id"], booking_id=body["booking_id"],
                    location=r.headers.get("Location"), repeated=r.headers.get("Repeated-Request") == "true",
                    attempt=attempt)
                return
            if r.status_code != 429 and r.status_code < 500:
                log("relay.refused", event_id=event["id"], operation_id=body["event_id"], status=r.status_code,
                    type=r.json().get("type") if r.content else None)
                return
            reason, retry_after = f"http-{r.status_code}", r.headers.get("Retry-After")
        except httpx.TimeoutException:
            reason = "timeout"
        except httpx.TransportError as exc:
            reason = type(exc).__name__
        pause = pause_s(attempt, retry_after)
        log("relay.retry", operation_id=body["event_id"], attempt=attempt, reason=reason, pause_s=pause)
        await sleep_or_stop(stop, pause)


def handler(client: httpx.AsyncClient, stop: asyncio.Event):
    async def handle(record) -> None:
        event = decode(record)
        if event is not None and event.get("type") == events.ENTRY_OPENED:
            await send_entry(client, event, stop)

    return handle


async def main(stop: asyncio.Event) -> None:
    try:
        async with httpx.AsyncClient(timeout=config.DPC_TIMEOUT_S) as client:
            await consume_until_stopped(events.DOMAIN_TOPIC, config.GROUP_ID, handler(client, stop), stop)
    except Stopping:
        pass


if __name__ == "__main__":
    background.run(main)
