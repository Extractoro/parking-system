"""Idempotency keys of state-changing requests (header Idempotency-Key).

A retry of a request with the same key must not repeat its effect: the client may resend after a
lost answer, a timeout or a 502, and the server cannot know whether the first attempt was done.

How one request with a key runs (`execute`), all in ONE transaction:
  1. pg_try_advisory_xact_lock(user:operation:key). Not taken: another request with this key is
     running right now -> 409 idempotency-in-progress with Retry-After. The lock IS the state
     "in progress": it lives exactly as long as that transaction, and a crash releases it.
  2. A stored row for the key: another body -> 422; the same body -> the stored answer, no work.
  3. No row: the operation runs inside a savepoint; its final answer (2xx, or a domain refusal
     404/409) is written as the row with state 'completed'; COMMIT stores the key and the
     domain change together. Validation errors (400/422) and crashes are not stored: the
     savepoint and the whole transaction roll back, and the same key may be sent again.
     202 of a long operation (lab 4) is stored too: a retry gets the same operation.
After COMMIT and before the answer leaves the process: fault("after_commit_before_response").
"""

import hashlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from . import config, db
from .faults import fault
from .problems import Problem

MAX_KEY = 200
# final answers that are stored and replayed; other errors are not results of the operation
STORED = {200, 201, 202, 404, 409}


@dataclass
class Outcome:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    replayed: bool = False


def require_key(raw: str | None) -> str:
    """No header -> 400; a header of the wrong length -> 422."""
    if raw is None:
        raise Problem(
            400,
            "idempotency-key-required",
            "Idempotency-Key header is required",
            "Send a new unique Idempotency-Key with every user action and the same one with its retries",
        )
    if not 1 <= len(raw) <= MAX_KEY:
        raise Problem(
            422,
            "invalid-request",
            "Invalid request data",
            errors=[{"field": "header.Idempotency-Key", "message": f"must be 1 to {MAX_KEY} characters"}],
        )
    return raw


def request_hash(method: str, route: str, body: Any) -> str:
    """sha256 of the method, the route TEMPLATE and the canonical JSON of the body (sorted keys)."""
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(f"{method}\n{route}\n{canonical}".encode()).hexdigest()


Action = Callable[[AsyncConnection], Awaitable[Outcome]]


async def execute(user_id: str, operation: str, key: str, req_hash: str, instance: str, action: Action) -> Outcome:
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT pg_try_advisory_xact_lock(hashtextextended(%s, 0)) AS taken",
            (f"{user_id}:{operation}:{key}",),
        )
        if not (await cur.fetchone())["taken"]:
            raise Problem(
                409,
                "idempotency-in-progress",
                "Request with this key is in progress",
                "A request with the same Idempotency-Key is being processed; retry later",
                headers={"Retry-After": "1"},
            )

        cur = await conn.execute(
            """
                SELECT request_hash, response_status, response_body, response_headers
                FROM idempotency_key
                WHERE user_id = %s AND operation = %s AND key = %s AND expires_at > now()
                """,
            (user_id, operation, key),
        )
        stored = await cur.fetchone()
        if stored is not None:
            if stored["request_hash"] != req_hash:
                raise Problem(
                    422,
                    "idempotency-key-reused",
                    "Idempotency-Key reused with another request",
                    "This key was already used with a different request body; use a new key",
                )
            return Outcome(stored["response_status"], stored["response_body"], stored["response_headers"], True)

        try:
            async with conn.transaction():  # savepoint: a refusal undoes only the operation
                outcome = await action(conn)
        except Problem as p:
            if p.status not in STORED:
                raise  # not a result of the operation: nothing is stored, the key stays free
            outcome = Outcome(p.status, p.body(instance), p.headers)

        # an expired row of the same key (not deleted yet) is replaced
        await conn.execute(
            """
                INSERT INTO idempotency_key (user_id, operation, key, request_hash, state, response_status,
                                             response_body, response_headers, expires_at)
                VALUES (%s, %s, %s, %s, 'completed', %s, %s, %s, now() + make_interval(hours => %s))
                ON CONFLICT (user_id, operation, key) DO UPDATE SET
                    request_hash = EXCLUDED.request_hash, state = EXCLUDED.state,
                    response_status = EXCLUDED.response_status, response_body = EXCLUDED.response_body,
                    response_headers = EXCLUDED.response_headers, created_at = now(),
                    expires_at = EXCLUDED.expires_at
                """,
            (
                user_id,
                operation,
                key,
                req_hash,
                outcome.status,
                Jsonb(outcome.body),
                Jsonb(outcome.headers),
                config.IDEMPOTENCY_TTL_H,
            ),
        )
        # the block above has committed: the booking and the key are stored together.
        # A crash here loses only the answer; a retry with the key gets the stored one.
    fault("after_commit_before_response")
    return outcome


async def cleanup_expired(batch: int) -> int:
    """Delete expired keys in batches of `batch` rows (short transactions, no long locks)."""
    total = 0
    while True:
        async with db.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """
                DELETE FROM idempotency_key WHERE ctid IN (
                    SELECT ctid FROM idempotency_key WHERE expires_at < now() LIMIT %s
                )
                """,
                (batch,),
            )
            deleted = cur.rowcount
        total += deleted
        if deleted < batch:
            return total
