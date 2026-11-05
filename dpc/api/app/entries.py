"""Barrier entries of C09 (DPC lab 1): bounded queue, worker pool, protected garage occupancy.

POST /v1/entries      -> 202 + Location; full queue -> 429 Retry-After: 1 (not stored); draining -> 503
GET  /v1/entries/{id} -> state, reason, time marks of the request (O1.5)
GET  /v1/entries      -> the last requests (the Barrier page of the client)
GET  /v1/garage/{id}  -> spots, occupied, inside, invariant_ok (O1.4)

The queue is asyncio.Queue(maxsize=QUEUE_CAPACITY): a worker waits in `await queue.get()` on a
future of the event loop (Condition-like wait), so an idle worker takes no CPU. Exactly WORKERS
workers run. Each request: processing -> slow plate recognition (outside the critical section) ->
critical section = one PostgreSQL transaction holding `SELECT ... FOR UPDATE` of the garage row.
"""

import asyncio
import json
import random
import re
import signal
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field, field_validator

from . import auth, config, db, metrics
from .jsonlog import log
from .problems import Problem

FINAL = ("done", "rejected", "failed")
# ISO 8601 UTC with Z and milliseconds (guide DPC 1.5), fixed in dpc/openapi.yaml
EVENT_TIME_RE = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$"
_PLATE_JUNK = re.compile(r"[\s-]+")
TIMING_KEEP = 100_000  # time marks of the last N requests are kept in memory


def normalize_plate(plate: str) -> str:
    """AA 1234-BB and aa1234bb are the same car: upper case without spaces and hyphens."""
    return _PLATE_JUNK.sub("", plate).upper()


def walk_in_allowed(occupied: int, reserved: int, spots: int) -> bool:
    """Rule B6: an entry without a booking needs a spot that no unused booking of the hour holds."""
    return occupied + reserved < spots


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=100)
    garage_id: str = Field(min_length=1, max_length=64)
    plate: str = Field(min_length=2, max_length=16)
    direction: Literal["in", "out"]
    event_time: str = Field(pattern=EVENT_TIME_RE)
    booking_id: str | None = Field(default=None, min_length=1, max_length=64)

    @field_validator("plate")
    @classmethod
    def plate_ok(cls, v: str) -> str:
        p = normalize_plate(v)
        if not 2 <= len(p) <= 12:
            raise ValueError("plate must have 2 to 12 characters without spaces and hyphens")
        return p

    @field_validator("event_time")
    @classmethod
    def time_ok(cls, v: str) -> str:
        datetime.fromisoformat(v)  # 2027-02-30T... is not a date
        return v

    def moment(self) -> datetime:
        return datetime.fromisoformat(self.event_time).astimezone(UTC)


@dataclass
class Job:
    id: str
    body: EntryCreate


@dataclass
class Marks:
    """Monotonic marks of one request (time.monotonic_ns, this process only)."""

    received: int
    started: int | None = None
    finished: int | None = None
    delivered: int | None = None  # the first GET that returned the final state


@dataclass
class State:
    queue: asyncio.Queue[Job] = field(default_factory=lambda: asyncio.Queue(maxsize=config.QUEUE_CAPACITY))
    reserving: int = 0  # POSTs between the capacity check and put_nowait (they await the INSERT)
    busy: int = 0  # requests inside workers
    draining: bool = False
    rejected_overload: int = 0  # 429
    rejected_draining: int = 0  # 503
    rejected_rule: int = 0
    finished: dict[str, int] = field(default_factory=lambda: {s: 0 for s in FINAL})
    marks: OrderedDict[str, Marks] = field(default_factory=OrderedDict)
    workers: list[asyncio.Task[None]] = field(default_factory=list)
    rng: random.Random = field(default_factory=lambda: random.Random(config.SEED))


state: State | None = None


def _state() -> State:
    if state is None:
        raise RuntimeError("entries are not started")
    return state


def _ms(a: int | None, b: int | None) -> float | None:
    return None if a is None or b is None else round((b - a) / 1e6, 3)


def _iso(v: datetime | None) -> str | None:
    return None if v is None else v.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _view(row: dict[str, Any]) -> dict[str, Any]:
    out = {
        "id": row["id"],
        "event_id": row["event_id"],
        "state": row["state"],
        "reason": row["reason"],
        "detail": row["detail"],
        "garage_id": row["garage_id"],
        "plate": row["plate"],
        "direction": row["direction"],
        "event_time": _iso(row["event_time"]),
        "booking_id": row["booking_id"],
        "received_at": _iso(row["received_at"]),
        "finished_at": _iso(row["finished_at"]),
    }
    m = _state().marks.get(row["id"]) if state else None
    if m is not None:
        out["queue_wait_ms"] = _ms(m.received, m.started)
        out["processing_ms"] = _ms(m.started, m.finished)
        out["total_ms"] = _ms(m.received, m.finished)
        out["delivery_wait_ms"] = _ms(m.finished, m.delivered)
        out["marks_ms"] = {  # offsets from receiving: 0 <= started <= finished <= delivered
            "received": 0.0,
            "started": _ms(m.received, m.started),
            "finished": _ms(m.received, m.finished),
            "delivered": _ms(m.received, m.delivered),
        }
    return out


# ------------------------------------------------------------------ lifecycle (lifespan of main.py)


async def start() -> None:
    global state
    state = State()
    metrics.gauge("queue_length", lambda: _state().queue.qsize())
    metrics.gauge("in_flight", lambda: _state().busy)
    metrics.gauge("rejected", lambda: _state().rejected_overload)
    metrics.gauge("rejected_rule", lambda: _state().rejected_rule)
    metrics.gauge("rejected_draining", lambda: _state().rejected_draining)
    metrics.gauge("queue_capacity", lambda: config.QUEUE_CAPACITY)
    metrics.gauge("workers", lambda: config.WORKERS)
    for s in FINAL:
        metrics.gauge(f"finished_{s}", lambda s=s: _state().finished[s])

    await _close_lost_requests()
    state.workers = [asyncio.create_task(_worker(n), name=f"worker-{n}") for n in range(config.WORKERS)]
    _install_sigterm()
    log("entries.started", workers=config.WORKERS, queue_capacity=config.QUEUE_CAPACITY, mode=config.RESOURCE_MODE)


async def stop() -> None:
    st = _state()
    for t in st.workers:
        t.cancel()
    await asyncio.gather(*st.workers, return_exceptions=True)


async def _close_lost_requests() -> None:
    """The queue lives in memory: requests that a crashed process had accepted are lost. On start
    they are closed as failed, so every stored request ends in a final state."""
    try:
        async with db.connection() as conn, conn.transaction():
            cur = await conn.execute(
                """UPDATE entry_request SET state = 'failed', reason = 'lost-on-restart',
                       detail = 'the process stopped before processing it', finished_at = now()
                   WHERE state IN ('queued', 'processing')"""
            )
            if cur.rowcount:
                log("entries.lost_on_restart", count=cur.rowcount)
    except Exception as exc:  # noqa: BLE001 - the database may still be starting; the service starts anyway
        log("entries.lost_check_failed", error=type(exc).__name__)


def _install_sigterm() -> None:
    """D1.4: SIGTERM -> draining (new POST -> 503), workers finish everything accepted, then the
    handler of uvicorn starts its normal shutdown (lifespan shutdown stops workers, closes the pool)."""
    loop = asyncio.get_running_loop()
    uvicorn_handler = signal.getsignal(signal.SIGTERM)

    def on_term(sig: int, frame: Any) -> None:
        st = _state()
        if st.draining:
            return
        st.draining = True
        loop.call_soon_threadsafe(lambda: loop.create_task(_drain_then_exit(uvicorn_handler, sig, frame)))

    signal.signal(signal.SIGTERM, on_term)


async def _drain_then_exit(handler: Any, sig: int, frame: Any) -> None:
    st = _state()
    queued, busy = st.queue.qsize(), st.busy
    before = sum(st.finished.values())
    log("api.draining", queued=queued, processing=busy)
    started = time.monotonic()
    await st.queue.join()  # every accepted request reached a final state
    log("api.drained", count=sum(st.finished.values()) - before, seconds=round(time.monotonic() - started, 2))
    if callable(handler):
        handler(sig, frame)


# ------------------------------------------------------------------ worker pool


async def _worker(n: int) -> None:
    st = _state()
    while True:
        job = await st.queue.get()  # an idle worker sleeps here without CPU
        st.busy += 1
        try:
            await _process(job)
        except Exception as exc:  # noqa: BLE001 - one bad request must not stop the worker
            log("entry.worker_error", id=job.id, error=type(exc).__name__)
        finally:
            st.busy -= 1
            st.queue.task_done()


async def _process(job: Job) -> None:
    st = _state()
    m = st.marks.get(job.id)
    if m is not None:
        m.started = time.monotonic_ns()
    async with db.connection() as conn, conn.transaction():
        await conn.execute(
            "UPDATE entry_request SET state = 'processing', started_at = now() WHERE id = %s", (job.id,)
        )

    # the slow call of the card, OUTSIDE the critical section: other workers go on meanwhile
    await asyncio.sleep(st.rng.uniform(config.RECOGNITION_MIN_MS, config.RECOGNITION_MAX_MS) / 1000)

    try:
        if config.RESOURCE_MODE in ("unsafe", "unprotected") and job.body.garage_id == config.UNSAFE_GARAGE:
            final, reason, detail = await _apply_unsafe(job)
        else:
            final, reason, detail = await _apply_locked(job)
    except Exception as exc:  # noqa: BLE001 - the request ends as failed, the effect was rolled back
        final, reason, detail = "failed", "error", type(exc).__name__
        async with db.connection() as conn, conn.transaction():
            await _finish(conn, job.id, final, reason, detail)

    if m is not None:
        m.finished = time.monotonic_ns()
    st.finished[final] += 1
    if final == "rejected":
        st.rejected_rule += 1
    log("entry.finished", id=job.id, garage_id=job.body.garage_id, direction=job.body.direction,
        state=final, reason=reason, total_ms=_ms(m.received, m.finished) if m else None)


async def _finish(conn: AsyncConnection, request_id: str, final: str, reason: str | None, detail: str | None) -> None:
    await conn.execute(
        "UPDATE entry_request SET state = %s, reason = %s, detail = %s, finished_at = now() WHERE id = %s",
        (final, reason, detail, request_id),
    )


async def _apply_locked(job: Job) -> tuple[str, str | None, str | None]:
    """Critical section: lock the garage row, check the rule, write the entry and the occupancy."""
    b = job.body
    async with db.connection() as conn, conn.transaction():
        cur = await conn.execute(
            "SELECT spots, occupied FROM garage_state WHERE garage_id = %s FOR UPDATE", (b.garage_id,)
        )
        g = await cur.fetchone()
        if g is None:
            await _finish(conn, job.id, "rejected", "unknown-garage", None)
            return "rejected", "unknown-garage", None

        verdict = await _check_rule(conn, b, g["spots"], g["occupied"])
        if verdict is not None:
            await _finish(conn, job.id, "rejected", *verdict)
            return "rejected", *verdict

        await _write_effect(conn, job)
        delta = 1 if b.direction == "in" else -1
        await conn.execute(
            "UPDATE garage_state SET occupied = occupied + %s WHERE garage_id = %s", (delta, b.garage_id)
        )
        await _finish(conn, job.id, "done", None, None)
    return "done", None, None


async def _apply_unsafe(job: Job) -> tuple[str, str | None, str | None]:
    """O1.4 without synchronization (test garage only): read without a lock, wait, write the value
    computed in the application. Two workers that read the same value lose one update."""
    b = job.body
    async with db.connection() as conn:
        cur = await conn.execute("SELECT spots, occupied FROM garage_state WHERE garage_id = %s", (b.garage_id,))
        g = await cur.fetchone()
        verdict = await _check_rule(conn, b, g["spots"], g["occupied"])
        await conn.commit()
        if verdict is not None:
            async with conn.transaction():
                await _finish(conn, job.id, "rejected", *verdict)
            return "rejected", *verdict

        await asyncio.sleep(config.UNSAFE_GAP_MS / 1000)  # the gap between the check and the write
        new = g["occupied"] + (1 if b.direction == "in" else -1)
        async with conn.transaction():
            await _write_effect(conn, job)
            await conn.execute("UPDATE garage_state SET occupied = %s WHERE garage_id = %s", (new, b.garage_id))
            await _finish(conn, job.id, "done", None, None)
    return "done", None, None


async def _write_effect(conn: AsyncConnection, job: Job) -> None:
    b = job.body
    await conn.execute(
        """INSERT INTO entry (event_id, request_id, garage_id, plate, direction, booking_id, event_time)
           VALUES (%s, %s, %s, %s, %s, %s, %s)""",
        (b.event_id, job.id, b.garage_id, b.plate, b.direction, b.booking_id if b.direction == "in" else None,
         b.moment()),
    )


async def _inside(conn: AsyncConnection, garage_id: str, plate: str) -> int:
    cur = await conn.execute(
        """SELECT count(*) FILTER (WHERE direction = 'in') - count(*) FILTER (WHERE direction = 'out') AS n
           FROM entry WHERE garage_id = %s AND plate = %s""",
        (garage_id, plate),
    )
    return (await cur.fetchone())["n"]


RESERVED_SQL = """
    SELECT count(*) AS n
    FROM booking bk JOIN level l ON l.id = bk.level_id
    WHERE l.garage_id = %(garage)s AND NOT (l.id = ANY(%(excluded)s))
      AND bk.status = 'confirmed' AND bk.from_hour <= %(t)s AND %(t)s < bk.expires_at
      AND NOT EXISTS (SELECT 1 FROM entry e WHERE e.booking_id = bk.id AND e.direction = 'in')
"""


async def reserved(conn: AsyncConnection, garage_id: str, t: datetime) -> int:
    """Unused bookings: confirmed bookings of the garage levels whose interval holds t, without an
    accepted entry. The table booking belongs to Web; DPC only reads it (decision B2)."""
    cur = await conn.execute(RESERVED_SQL, {"garage": garage_id, "excluded": list(config.EXCLUDED_LEVELS), "t": t})
    return (await cur.fetchone())["n"]


async def _check_rule(
    conn: AsyncConnection, b: EntryCreate, spots: int, occupied: int
) -> tuple[str, str] | None:
    """None if the entry/exit is allowed, else (reason, detail). Runs inside the critical section."""
    if b.direction == "out":
        if await _inside(conn, b.garage_id, b.plate) <= 0:
            return "not-inside", f"{b.plate} is not inside {b.garage_id}"
        return None

    if await _inside(conn, b.garage_id, b.plate) > 0:
        return "already-inside", f"{b.plate} is already inside {b.garage_id}"

    if b.booking_id is not None:
        cur = await conn.execute(
            """SELECT bk.status, bk.plate, bk.from_hour, bk.expires_at, l.garage_id, l.id AS level_id
               FROM booking bk JOIN level l ON l.id = bk.level_id WHERE bk.id = %s""",
            (b.booking_id,),
        )
        bk = await cur.fetchone()
        t = b.moment()
        if bk is None:
            return "booking-invalid", "booking not found"
        if bk["garage_id"] != b.garage_id or bk["level_id"] in config.EXCLUDED_LEVELS:
            return "booking-invalid", "the booking is for another garage"
        if bk["status"] != "confirmed":
            return "booking-invalid", f"the booking is {bk['status']}"
        if normalize_plate(bk["plate"]) != b.plate:
            return "booking-invalid", "the plate differs from the booking"
        if not bk["from_hour"] <= t < bk["expires_at"]:
            return "booking-invalid", "event_time is outside the booking interval"
        cur = await conn.execute(
            "SELECT 1 FROM entry WHERE booking_id = %s AND direction = 'in'", (b.booking_id,)
        )
        if await cur.fetchone() is not None:
            return "already-entered", "the booking was already used for an entry"
        # B4: the spot is reserved by Web, the quota of unused bookings is not checked. Physical
        # spots still bound occupied: walk-ins taken before the booking was made (PC-4).
        if occupied >= spots:
            return "full", "no physical spot is free"
        return None

    held = await reserved(conn, b.garage_id, b.moment())
    if not walk_in_allowed(occupied, held, spots):
        return "full", f"occupied {occupied} + reserved {held} of {spots} spots"
    return None


# ------------------------------------------------------------------ admission (O1.2)


class Admission:
    """Outermost ASGI layer: POST /v1/entries with a full queue (or while draining) is refused here,
    before FastAPI routing, validation and the other middleware. Under a flood of refused requests
    each 429 then costs a few dozen microseconds instead of ~1.5 ms, so the event loop stays free
    for /health and for the accepted requests (O1.2). The route below repeats the same check for
    the requests that pass this one."""

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == "/v1/entries" and state:
            st = state
            if st.draining:
                st.rejected_draining += 1
                return await _refuse(scope, send, 503, "shutting-down", "The service is shutting down",
                                     str(config.DRAIN_RETRY_AFTER_S))
            if st.queue.qsize() + st.reserving >= config.QUEUE_CAPACITY:
                st.rejected_overload += 1
                return await _refuse(scope, send, 429, "queue-full", "The queue is full", "1")
        await self.app(scope, receive, send)


async def _refuse(scope: dict, send: Any, status: int, slug: str, title: str, retry_after: str) -> None:
    started = time.perf_counter()
    metrics.request_started()
    body = json.dumps({"type": config.PROBLEM_BASE + slug, "title": title, "status": status,
                       "detail": "The request was not accepted; repeat it later", "instance": "/v1/entries"}).encode()
    headers = [(b"content-type", b"application/problem+json"), (b"content-length", str(len(body)).encode()),
               (b"retry-after", retry_after.encode()), (b"x-instance", config.INSTANCE.encode())]
    origin = dict(scope["headers"]).get(b"origin")
    if origin is not None and origin.decode() == config.CLIENT_ORIGIN:  # the Barrier page must see the 429
        headers += [(b"access-control-allow-origin", origin),
                    (b"access-control-expose-headers", b"Location, Retry-After, X-Request-ID")]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
    # counted in /metrics (responses, latency); no JSON log line per refusal: the counters are the record
    metrics.request_finished(status, (time.perf_counter() - started) * 1000)


# ------------------------------------------------------------------ routes

router = APIRouter(prefix="/v1", dependencies=[Depends(auth.require_token)])


def _same_request(row: dict[str, Any], b: EntryCreate) -> bool:
    return (
        row["garage_id"] == b.garage_id
        and row["plate"] == b.plate
        and row["direction"] == b.direction
        and row["booking_id"] == b.booking_id
        and row["event_time"] == b.moment()
    )


@router.post("/entries", status_code=202)
async def create_entry(body: EntryCreate, response: Response) -> dict[str, Any]:
    st = _state()
    received = time.monotonic_ns()
    if st.draining:
        st.rejected_draining += 1
        raise Problem(503, "shutting-down", "The service is shutting down",
                      "Accepted requests are being finished; send this one again later",
                      headers={"Retry-After": str(config.DRAIN_RETRY_AFTER_S)})
    if st.queue.qsize() + st.reserving >= config.QUEUE_CAPACITY:
        st.rejected_overload += 1
        raise Problem(429, "queue-full", "The queue is full", "The request was not accepted; repeat it later",
                      headers={"Retry-After": "1"})

    st.reserving += 1  # holds a place in the queue while the row is inserted
    try:
        request_id = str(uuid.uuid4())
        async with db.connection() as conn, conn.transaction():
            cur = await conn.execute("SELECT 1 FROM garage_state WHERE garage_id = %s", (body.garage_id,))
            if await cur.fetchone() is None:
                raise Problem(404, "garage-not-found", "Garage not found", f"no garage {body.garage_id}")
            cur = await conn.execute(
                """INSERT INTO entry_request (id, event_id, garage_id, plate, direction, event_time, booking_id, state)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued')
                   ON CONFLICT (event_id) DO NOTHING
                   RETURNING *""",
                (request_id, body.event_id, body.garage_id, body.plate, body.direction, body.moment(), body.booking_id),
            )
            row = await cur.fetchone()
            if row is None:  # the same event_id again: the same request, nothing is queued twice
                cur = await conn.execute("SELECT * FROM entry_request WHERE event_id = %s", (body.event_id,))
                row = await cur.fetchone()
                if not _same_request(row, body):
                    raise Problem(409, "event-id-reused", "event_id belongs to another request",
                                  "The same event_id was sent with other fields")
                response.headers["Location"] = f"/v1/entries/{row['id']}"
                response.headers["Repeated-Request"] = "true"
                return _view(row)

        st.marks[request_id] = Marks(received=received)
        while len(st.marks) > TIMING_KEEP:
            st.marks.popitem(last=False)
        st.queue.put_nowait(Job(request_id, body))  # a place was held: never QueueFull here
    finally:
        st.reserving -= 1

    response.headers["Location"] = f"/v1/entries/{request_id}"
    return _view(row)


@router.get("/entries/{request_id}")
async def get_entry(request_id: str) -> dict[str, Any]:
    async with db.connection() as conn:
        cur = await conn.execute("SELECT * FROM entry_request WHERE id = %s", (request_id,))
        row = await cur.fetchone()
    if row is None:
        raise Problem(404, "entry-not-found", "Entry request not found")
    m = _state().marks.get(request_id)
    if m is not None and row["state"] in FINAL and m.delivered is None:
        # the first read of the final result; reads before the end do not set it
        m.delivered = max(time.monotonic_ns(), m.finished or 0)
    return _view(row)


@router.get("/entries")
async def list_entries(
    garage_id: str | None = None, limit: int = Query(20, ge=1, le=100)
) -> dict[str, Any]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT * FROM entry_request WHERE (%(g)s::text IS NULL OR garage_id = %(g)s)
               ORDER BY received_at DESC, id LIMIT %(n)s""",
            {"g": garage_id, "n": limit},
        )
        rows = await cur.fetchall()
    return {"items": [_view(r) for r in rows]}


@router.get("/garage/{garage_id}")
async def get_garage(garage_id: str) -> dict[str, Any]:
    async with db.connection() as conn, conn.transaction():
        # one snapshot: occupied and inside are read in the same REPEATABLE READ transaction
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        cur = await conn.execute(
            """SELECT gs.spots, gs.occupied,
                      (SELECT count(*) FILTER (WHERE direction = 'in') - count(*) FILTER (WHERE direction = 'out')
                       FROM entry e WHERE e.garage_id = gs.garage_id) AS inside
               FROM garage_state gs WHERE gs.garage_id = %s""",
            (garage_id,),
        )
        g = await cur.fetchone()
        if g is None:
            raise Problem(404, "garage-not-found", "Garage not found")
        held = await reserved(conn, garage_id, datetime.now(UTC))
    ok = g["occupied"] == g["inside"] and 0 <= g["occupied"] <= g["spots"]
    return {
        "garage_id": garage_id,
        "spots": g["spots"],
        "occupied": g["occupied"],
        "inside": g["inside"],
        "reserved": held,
        "free_walk_in": max(0, g["spots"] - g["occupied"] - held),
        "invariant_ok": ok,
        "mode": "unsafe" if config.RESOURCE_MODE in ("unsafe", "unprotected") and garage_id == config.UNSAFE_GARAGE
        else "locked",
    }
