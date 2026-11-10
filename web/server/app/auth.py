"""Login, logout and the current user (Web lab 3): POST /v1/auth/login, POST /v1/auth/logout, GET /v1/me.

Login checks the argon2id hash of app_user.password_hash and creates a row of `session` with
sha256(token) and expires_at = now() + SESSION_TTL_H. The token itself is returned once and never stored.
An unknown login and a wrong password give the same 401, and both run one argon2 verification
(the unknown login against a dummy hash), so the answer time does not tell whether the login exists.
"""

import asyncio
import secrets
from datetime import UTC
from typing import Annotated, Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Header, Response
from pydantic import BaseModel, Field

from . import config, db
from .problems import Problem
from .users import CurrentUser, bearer_token, token_hash, unauthorized

router = APIRouter(prefix="/v1")

_hasher = PasswordHasher()  # argon2id with the library defaults: m = 64 MiB, t = 3, p = 4
# a hash with the same parameters for logins that do not exist (or users without a password)
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))
# argon2 takes ~64 MiB and tens of ms of CPU per check: at most this many at once per process,
# the rest wait, so a burst of logins neither exhausts memory nor blocks the event loop
_verify_slots = asyncio.Semaphore(config.LOGIN_CONCURRENCY)


class LoginBody(BaseModel):
    login: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


def _check(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerificationError, InvalidHashError):
        return False


async def _verify(stored: str | None, password: str) -> bool:
    real = stored is not None and stored.startswith("$argon2")
    async with _verify_slots:
        ok = await asyncio.to_thread(_check, stored if real else _DUMMY_HASH, password)
    return ok and real


def _user_json(row: dict[str, Any]) -> dict[str, Any]:
    return {"id": row["id"], "login": row["login"], "name": row["login"], "role": row["role"]}


@router.post("/auth/login")
async def login(body: LoginBody) -> dict[str, Any]:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT id, login, role, password_hash FROM app_user WHERE login = %s", (body.login,)
        )
        row = await cur.fetchone()
    # the hash is checked outside the transaction: no connection is held while argon2 runs
    if not await _verify(row["password_hash"] if row else None, body.password):
        raise Problem(401, "invalid-credentials", "Invalid login or password", "Check the login and the password")

    token = secrets.token_urlsafe(32)  # 32 random bytes -> 43 characters of base64url
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            """
            INSERT INTO session (token_hash, user_id, expires_at)
            VALUES (%s, %s, now() + %s * interval '1 hour')
            RETURNING expires_at
            """,
            (token_hash(token), row["id"], config.SESSION_TTL_H),
        )
        expires_at = (await cur.fetchone())["expires_at"]
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": expires_at.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "user": _user_json(row),
    }


@router.post("/auth/logout", status_code=204)
async def logout(authorization: Annotated[str | None, Header()] = None) -> Response:
    """Deletes the session of the token. Safe to repeat: an already deleted or expired one gives 204 too."""
    token = bearer_token(authorization)
    if token is None:
        raise unauthorized("Send Authorization: Bearer <token> of the session to end")
    async with db.connection() as conn, conn.transaction():
        await conn.execute("DELETE FROM session WHERE token_hash = %s", (token_hash(token),))
    return Response(status_code=204)


@router.get("/me")
async def me(user: CurrentUser) -> dict[str, Any]:
    return {"id": user.id, "login": user.login, "name": user.login, "role": user.role}
