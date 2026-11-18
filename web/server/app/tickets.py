"""One-time tickets of the WebSocket channel (Web lab 5, O5.1): POST /v1/ws-tickets.

The browser cannot send Authorization in a WebSocket handshake, and the session token must not go into a
URL (logs of nginx, history). So the client trades its session for a ticket: 32 random bytes, valid
TICKET_TTL_S (30 s), only sha256 of it is stored with the user and the hash of the session token. The
gateway redeems it before accept() (gateway.py, `redeem`): one UPDATE marks it used, so a ticket opens one
connection at most, and the connection belongs to that session (logout closes it, session.revoked).
"""

import secrets
from datetime import UTC
from typing import Annotated, Any

from fastapi import APIRouter, Header
from fastapi.responses import JSONResponse

from . import config, db
from .users import CurrentUser, bearer_token, token_hash

router = APIRouter(prefix="/v1")


@router.post("/ws-tickets", status_code=201)
async def create_ticket(user: CurrentUser, authorization: Annotated[str | None, Header()] = None) -> JSONResponse:
    session_hash = token_hash(bearer_token(authorization) or "")  # CurrentUser has checked the token already
    ticket = secrets.token_urlsafe(32)
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """INSERT INTO ws_ticket (ticket_hash, user_id, session_token_hash, expires_at)
               VALUES (%s, %s, %s, now() + %s * interval '1 second') RETURNING expires_at""",
            (token_hash(ticket), user.id, session_hash, config.TICKET_TTL_S),
        )
        expires_at = (await cur.fetchone())["expires_at"]
    body: dict[str, Any] = {
        "ticket": ticket,
        "expires_in": config.TICKET_TTL_S,
        "expires_at": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
    }
    return JSONResponse(body, status_code=201, headers={"Cache-Control": "no-store"})


async def cleanup_expired(batch: int) -> int:
    """Delete expired tickets in batches (a used or expired ticket is refused anyway)."""
    total = 0
    while True:
        async with db.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """DELETE FROM ws_ticket WHERE ticket_hash IN (
                       SELECT ticket_hash FROM ws_ticket WHERE expires_at <= now() - interval '1 minute' LIMIT %s
                   )""",
                (batch,),
            )
            deleted = cur.rowcount
        total += deleted
        if deleted < batch:
            return total
