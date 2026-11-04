"""The current user. Until lab 3 the client sends the id of a pre-created user in X-User-Id.

The owner of an object is always taken from here, never from the request body. The header value
itself is not logged (the request log has no headers at all).
TODO (O3.1): replace the header with the login session/token; keep the `current_user` dependency.
"""

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


def _unauthorized() -> Problem:
    return Problem(401, "unauthorized", "Authentication required", "Send X-User-Id of an existing user")


async def current_user(x_user_id: Annotated[str | None, Header()] = None) -> User:
    if not x_user_id:
        raise _unauthorized()
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute("SELECT id, login, role FROM app_user WHERE id = %s", (x_user_id,))
        row = await cur.fetchone()
    if row is None:
        raise _unauthorized()
    return User(**row)


CurrentUser = Annotated[User, Depends(current_user)]
