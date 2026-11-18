"""WebSocket gateway (lab 5): `uvicorn app.gateway:app --host 0.0.0.0 --port 8000`.

Two instances run (gateway-a, gateway-b) behind nginx `location = /v1/ws`; each is also published
directly (:8091, :8092).

O5.1 Authorized connection. Before accept(): the Origin header must be exactly CLIENT_ORIGIN, and the
one-time ticket of ?ticket= (POST /v1/ws-tickets) is redeemed by ONE UPDATE ... WHERE used_at IS NULL AND
expires_at > now() AND the session still exists RETURNING the user and the session. Any failure ->
close(1008) before accept, which the server answers with HTTP 403 to the handshake (ASGI: a close before
accept rejects the handshake). The user comes only from the ticket, never from the client.

O5.2 Delivery through two gateways. Every gateway reads the topic notifications in its OWN consumer group
(GROUP_ID = gateway-a / gateway-b), so each gets every message, and sends it only to the connections of
its user that it holds itself (memory: user -> connections, session -> connections). Frame to the client:
{id, position, type, text, created_at}; the first frame is {"type": "hello", "gateway": <instance>}.
session.revoked (logout) is not forwarded: the connections of that session are closed with 1008.

O5.3 The gateway sends the same `position` as the feed; recovery (history, head) is the client's.
"""

import asyncio
import contextlib
import json
import time
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime

from aiokafka.structs import ConsumerRecord
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

from . import broker, config, db, events, metrics, middleware, problems, service, telemetry
from .jsonlog import log
from .users import token_hash

POLICY_VIOLATION = 1008
REVOKED_KEEP_S = 600  # a session.revoked that came before its connection registered still closes it


@dataclass(eq=False)
class Conn:
    socket: WebSocket
    user_id: str
    session_hash: str
    opened: float = field(default_factory=time.monotonic)
    send_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


by_user: dict[str, set[Conn]] = defaultdict(set)
by_session: dict[str, set[Conn]] = defaultdict(set)
revoked: dict[str, float] = {}  # session hash -> monotonic time of the revocation


def _register(conn: Conn) -> None:
    by_user[conn.user_id].add(conn)
    by_session[conn.session_hash].add(conn)


def _forget(conn: Conn) -> None:
    for index, key in ((by_user, conn.user_id), (by_session, conn.session_hash)):
        index[key].discard(conn)
        if not index[key]:
            del index[key]


async def _close(conn: Conn, reason: str) -> None:
    with contextlib.suppress(Exception):  # already closed by the client
        await conn.socket.close(code=POLICY_VIOLATION, reason=reason)


async def _send(conn: Conn, frame: dict) -> None:
    """One frame; a client that does not take it within WS_SEND_TIMEOUT_S is closed (it will recover)."""
    try:
        async with conn.send_lock:
            await asyncio.wait_for(conn.socket.send_text(json.dumps(frame, ensure_ascii=False)),
                                   timeout=config.WS_SEND_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - a broken or slow connection must not stop the reader
        log("ws.send_failed", user_id=conn.user_id, error=type(exc).__name__)
        await _close(conn, "send failed")


async def on_message(record: ConsumerRecord) -> None:
    try:
        message = json.loads(record.value)
        kind, data = message["type"], message["data"]
    except (TypeError, ValueError, KeyError):
        log("message.undecodable", topic=record.topic, partition=record.partition, offset=record.offset)
        return

    if kind == events.NOTIFICATION_CREATED:
        conns = list(by_user.get(data["user_id"], ()))
        frame = {k: data[k] for k in ("id", "position", "type", "text", "created_at")}
        await asyncio.gather(*(_send(c, frame) for c in conns))
        log("ws.delivered", user_id=data["user_id"], position=data["position"], notification_id=data["id"],
            connections=len(conns), partition=record.partition, offset=record.offset)
    elif kind == events.SESSION_REVOKED:
        session_hash = data["session_token_hash"]
        revoked[session_hash] = time.monotonic()
        conns = list(by_session.get(session_hash, ()))
        await asyncio.gather(*(_close(c, "session ended") for c in conns))
        log("ws.session_revoked", user_id=data.get("user_id"), closed=len(conns))
        _trim_revoked()


def _trim_revoked() -> None:
    old = time.monotonic() - REVOKED_KEEP_S
    for key in [k for k, t in revoked.items() if t < old]:
        del revoked[key]


async def _read_forever(topic: str, group_id: str) -> None:
    """Keep reading even if the broker is not up yet or restarts."""
    while True:
        try:
            await broker.consume(topic, group_id, on_message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - the reader must survive any error and retry
            log("gateway.reader_error", error=type(exc).__name__)
            await asyncio.sleep(2)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.open_pool(size=4)
    # GROUP_ID is gateway-a / gateway-b in compose.yaml: every gateway reads every message (O5.2)
    reader = asyncio.create_task(_read_forever(events.NOTIFICATIONS_TOPIC, config.GROUP_ID))
    log("gateway.started")

    yield

    reader.cancel()
    with contextlib.suppress(asyncio.CancelledError, Exception):
        await reader
    await db.close_pool()
    log("gateway.stopped")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
middleware.install(app)
problems.install(app)
app.include_router(service.router)
app.include_router(metrics.router)
telemetry.install(app)


async def redeem(ticket: str) -> dict | None:
    """The user, the session and its end for a valid unused ticket of a live session; marks it used."""
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """UPDATE ws_ticket t SET used_at = now()
               FROM session s
               WHERE t.ticket_hash = %s AND t.used_at IS NULL AND t.expires_at > now()
                 AND s.token_hash = t.session_token_hash AND s.expires_at > now()
               RETURNING t.user_id, t.session_token_hash, s.expires_at AS session_expires_at""",
            (token_hash(ticket),),
        )
        return await cur.fetchone()


async def _refuse(socket: WebSocket, reason: str) -> None:
    log("ws.refused", reason=reason)
    await socket.close(code=POLICY_VIOLATION)


@app.websocket("/v1/ws")
async def notifications(socket: WebSocket) -> None:
    if socket.headers.get("origin") != config.CLIENT_ORIGIN:
        return await _refuse(socket, "origin")
    ticket = socket.query_params.get("ticket", "")
    if not ticket or len(ticket) > 128:
        return await _refuse(socket, "no-ticket")
    try:
        row = await redeem(ticket)
    except Exception as exc:  # noqa: BLE001 - database down: refuse, the client retries with a new ticket
        log("ws.redeem_failed", error=type(exc).__name__)
        row = None
    if row is None:
        return await _refuse(socket, "ticket")

    conn = Conn(socket, row["user_id"], row["session_token_hash"])
    await socket.accept()
    _register(conn)  # from now on on_message finds it (a notification before this is read from the history)
    log("ws.opened", user_id=conn.user_id, connections=len(by_user.get(conn.user_id, ())))
    # the session ends (logout, expiry) -> the connection ends with it
    lifetime = (row["session_expires_at"] - datetime.now(UTC)).total_seconds()
    expiry = asyncio.get_running_loop().call_later(max(lifetime, 0), lambda: asyncio.ensure_future(
        _close(conn, "session expired")))
    if conn.session_hash in revoked:  # logout committed between the redemption and the registration
        await _close(conn, "session ended")
    else:
        await _send(conn, {"type": "hello", "gateway": config.INSTANCE})

    try:
        while True:
            await socket.receive_text()  # the client sends nothing the gateway needs; recovery is over HTTP
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        expiry.cancel()
        _forget(conn)
        log("ws.closed", user_id=conn.user_id, seconds=round(time.monotonic() - conn.opened, 1))
