"""The current user: the opaque token of the login session (Web lab 3), header `Authorization: Bearer <token>`.

The token is 32 random bytes (base64url) made at login (app/auth.py). The database keeps only its
sha256, so the lookup is by the hash; any instance (app-a, app-b) finds the same session row, and
logout deletes it, after which the old token is refused at once. X-User-Id is not read any more.

The owner of an object is always taken from here, never from the request body. Neither the token nor
the header is logged (the request log has no headers at all).
"""

import hashlib
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header

from . import db
from .problems import Problem


@dataclass(frozen=True)
class User:
    id: str
    login: str
    role: str


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def bearer_token(authorization: str | None) -> str | None:
    """The token of `Authorization: Bearer <token>`, or None if the header is missing or of another scheme."""
    if not authorization:
        return None
    scheme, _, token = authorization.strip().partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token or len(token) > 512:
        return None
    return token


def unauthorized(detail: str, error: str | None = None) -> Problem:
    # RFC 6750: the challenge says which scheme to use; error="invalid_token" for an unknown or expired one
    challenge = "Bearer" + (f' error="{error}"' if error else "")
    return Problem(401, "unauthorized", "Authentication required", detail, headers={"WWW-Authenticate": challenge})


async def current_user(authorization: Annotated[str | None, Header()] = None) -> User:
    token = bearer_token(authorization)
    if token is None:
        raise unauthorized("Log in (POST /v1/auth/login) and send Authorization: Bearer <token>")
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            SELECT u.id, u.login, u.role
            FROM session s JOIN app_user u ON u.id = s.user_id
            WHERE s.token_hash = %s AND s.expires_at > now()
            """,
            (token_hash(token),),
        )
        row = await cur.fetchone()
    if row is None:  # unknown, logged out or expired: the same answer
        raise unauthorized("The session has ended; log in again", "invalid_token")
    return User(**row)


CurrentUser = Annotated[User, Depends(current_user)]


async def staff_user(user: CurrentUser) -> User:
    """Staff functions of the card (close a level): the role is checked on the server, before the action."""
    if user.role != "staff":
        raise Problem(403, "forbidden", "Forbidden", "Only staff can do this")
    return user


StaffUser = Annotated[User, Depends(staff_user)]


async def cleanup_expired_sessions(batch: int) -> int:
    """Delete expired sessions in batches of `batch` rows (they are refused anyway; this keeps the table small)."""
    total = 0
    while True:
        async with db.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                DELETE FROM session WHERE ctid IN (
                    SELECT ctid FROM session WHERE expires_at <= now() LIMIT %s
                )
                """,
                (batch,),
            )
            deleted = cur.rowcount
        total += deleted
        if deleted < batch:
            return total
