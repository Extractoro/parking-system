"""Domain events of C09 in the CloudEvents 1.0 envelope (structured mode, JSON) and the outbox writer.

An event is written to the table `outbox` in the transaction that changes the domain state; the
HTTP handlers never talk to the broker. The publisher (app/publisher.py) sends the stored envelope as
it is, so a repeated send keeps the event id, and the handlers deduplicate by that id.

Envelope: specversion "1.0", id (uuid), source "parking-web", type, subject (entity id = message key),
time (UTC, milliseconds, Z), datacontenttype "application/json", data {entity_id, entity_version, ...}.
Types in the topic domain-events (card C09): booking.created, entry.requested, entry.opened.
The entity of all three is the booking: the message key is the booking id, so the events of one
booking are read in order from one partition.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

SOURCE = "parking-web"
DOMAIN_TOPIC = "domain-events"
CONTENT_TYPE = "application/cloudevents+json"

BOOKING_CREATED = "booking.created"
ENTRY_REQUESTED = "entry.requested"
ENTRY_OPENED = "entry.opened"


def utc_ms(moment: datetime) -> str:
    """2026-11-11T10:15:00.123Z: the time format of events and of the DPC contract."""
    return moment.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def envelope(type_: str, entity_id: str, entity_version: int, data: dict[str, Any],
             now: datetime | None = None) -> dict[str, Any]:
    return {
        "specversion": "1.0",
        "id": str(uuid.uuid4()),
        "source": SOURCE,
        "type": type_,
        "subject": entity_id,
        "time": utc_ms(now or datetime.now(UTC)),
        "datacontenttype": "application/json",
        "data": {"entity_id": entity_id, "entity_version": entity_version, **data},
    }


async def add_event(conn: AsyncConnection, type_: str, entity_id: str, entity_version: int,
                    data: dict[str, Any], topic: str = DOMAIN_TOPIC) -> str:
    """Store one event in the outbox inside the caller's transaction; returns the event id."""
    event = envelope(type_, entity_id, entity_version, data)
    await conn.execute(
        "INSERT INTO outbox (id, topic, msg_key, type, payload) VALUES (%s, %s, %s, %s, %s)",
        (event["id"], topic, entity_id, type_, Jsonb(event)),
    )
    return event["id"]
