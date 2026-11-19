"""DPC lab 6: the protected resource of the tariff manager (O6.2) and its history (O6.3).

  POST /v1/tariff/generations {garage_id, owner, generation} -> register a generation for the tariff of a garage:
                                                     larger than the registered one -> 200, the same owner and
                                                     generation again -> 200 (a retry), else 409
  PUT  /v1/tariff/{garage_id} {minor_per_hour, owner, generation} -> generation == registered -> 200 (accepted),
                                                     smaller -> 409 stale, larger -> 409 unregistered
  GET  /v1/tariff                                 -> current tariffs with the generation and owner of the write
  GET  /v1/coordinator                            -> owner and generation of the role in etcd (O6.1)
  GET  /v1/history?limit=&garage_id=              -> the operations of the resource, newest first
  GET  /v1/history/check?garage_id=               -> {ok, violations}: no accepted write of an older generation after
                                                     the registration of a newer one; tariff = the last accepted write

The protected record is the current tariff of a garage; its fence row is resource = 'tariff/<garage_id>'.
The check of the generation, the change of the tariff and the row of the history are ONE transaction that starts with
`SELECT ... FROM fence WHERE resource = 'tariff/<garage_id>' FOR UPDATE`: writers of a garage are serialized on its
fence row, a write cannot slip between the check and the registration of a newer generation, and within a garage the
order of tariff_history.id is the order of operations. The coordinator does not decide anything here: a paused old owner still sends its write, and
the resource refuses it.

FENCING=off skips the comparison (the write is accepted with reason "unfenced") for the test garage UNSAFE_GARAGE
only (dpc-race, the experiment without protection of O6.3); every other garage is always checked.
"""

import asyncio
import base64
import json
import urllib.request
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from . import auth, config, db
from .jsonlog import log
from .problems import Problem

router = APIRouter(prefix="/v1", dependencies=[Depends(auth.require_token)])

OWNER = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


class Registration(BaseModel):
    garage_id: str = Field(min_length=1, max_length=64)
    owner: str = OWNER
    generation: int = Field(gt=0)


class TariffWrite(BaseModel):
    minor_per_hour: int = Field(gt=0, le=10_000_000)
    owner: str = OWNER
    generation: int = Field(gt=0)


def resource(garage_id: str) -> str:
    return f"tariff/{garage_id}"


async def _fence(conn: AsyncConnection, garage_id: str) -> dict[str, Any]:
    """Lock the fence row of the garage's tariff (404 if the garage has no tariff). The row is created on first use
    (a tariff added after the migration), then locked: every operation of the garage passes this lock."""
    cur = await conn.execute("SELECT 1 FROM tariff WHERE garage_id = %s", (garage_id,))
    if await cur.fetchone() is None:
        raise Problem(404, "tariff-not-found", "Garage has no tariff")
    await conn.execute("INSERT INTO fence (resource) VALUES (%s) ON CONFLICT DO NOTHING", (resource(garage_id),))
    cur = await conn.execute("SELECT generation, owner FROM fence WHERE resource = %s FOR UPDATE",
                             (resource(garage_id),))
    return await cur.fetchone()


async def _history(conn: AsyncConnection, garage_id: str, minor: int | None, owner: str, generation: int,
                   accepted: bool, reason: str) -> int:
    cur = await conn.execute(
        """INSERT INTO tariff_history (garage_id, minor_per_hour, owner, generation, accepted, reason)
           VALUES (%s, %s, %s, %s, %s, %s) RETURNING id""",
        (garage_id, minor, owner, generation, accepted, reason),
    )
    return (await cur.fetchone())["id"]


@router.post("/tariff/generations")
async def register_generation(body: Registration) -> dict[str, Any]:
    async with db.connection() as conn, conn.transaction():
        fence = await _fence(conn, body.garage_id)
        current = fence["generation"]
        if body.generation == current and body.owner == fence["owner"]:
            return {"garage_id": body.garage_id, "owner": body.owner, "generation": str(current), "registered": True,
                    "repeated": True}
        if body.generation <= current:
            log("generation.refused", garage_id=body.garage_id, owner=body.owner, generation=body.generation,
                current=current)
            raise Problem(409, "generation-not-newer", "Generation is not newer than the registered one",
                          f"generation {body.generation} <= registered {current}", current_generation=str(current))
        await conn.execute("UPDATE fence SET generation = %s, owner = %s, updated_at = now() WHERE resource = %s",
                           (body.generation, body.owner, resource(body.garage_id)))
        hid = await _history(conn, body.garage_id, None, body.owner, body.generation, True, "registered")
    log("generation.registered", garage_id=body.garage_id, owner=body.owner, generation=body.generation,
        previous=current, history_id=hid)
    return {"garage_id": body.garage_id, "owner": body.owner, "generation": str(body.generation), "registered": True, "repeated": False,
            "previous_generation": str(current)}


@router.put("/tariff/{garage_id}")
async def write_tariff(garage_id: str, body: TariffWrite) -> dict[str, Any]:
    unfenced = not config.FENCING and garage_id == config.UNSAFE_GARAGE
    async with db.connection() as conn, conn.transaction():
        fence = await _fence(conn, garage_id)
        current = fence["generation"]
        if unfenced:
            reason = "unfenced"
        elif body.generation == current:
            reason = "accepted"
        else:
            reason = "stale" if body.generation < current else "unregistered"
        accepted = reason in ("accepted", "unfenced")
        if accepted:
            await conn.execute(
                """UPDATE tariff SET minor_per_hour = %s, generation = %s, owner = %s, updated_at = now()
                   WHERE garage_id = %s""",
                (body.minor_per_hour, body.generation, body.owner, garage_id),
            )
        # a rejected write is recorded too: the row commits with this transaction, the 409 is raised after it
        hid = await _history(conn, garage_id, body.minor_per_hour, body.owner, body.generation, accepted, reason)
    log("tariff.write", garage_id=garage_id, minor_per_hour=body.minor_per_hour, owner=body.owner,
        generation=body.generation, registered=current, accepted=accepted, reason=reason, history_id=hid)
    if not accepted:
        title = "Generation is stale" if reason == "stale" else "Generation is not registered"
        raise Problem(409, f"{reason}-generation", title,
                      f"generation {body.generation}, registered {current}", current_generation=str(current),
                      history_id=str(hid), reason=reason)
    return {"garage_id": garage_id, "minor_per_hour": body.minor_per_hour, "owner": body.owner,
            "generation": str(body.generation), "accepted": True, "reason": reason, "history_id": str(hid)}


@router.get("/tariff")
async def list_tariffs() -> dict[str, Any]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT t.garage_id, g.name, t.minor_per_hour, t.currency, t.generation, t.owner, t.updated_at,
                      f.generation AS registered_generation, f.owner AS registered_owner
               FROM tariff t JOIN garage g ON g.id = t.garage_id
               LEFT JOIN fence f ON f.resource = 'tariff/' || t.garage_id
               WHERE t.garage_id !~ '^t-'
               ORDER BY t.garage_id"""
        )
        rows = await cur.fetchall()
    return {"items": [_tariff(r) for r in rows]}


def _tariff(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "garage_id": r["garage_id"], "name": r["name"], "minor_per_hour": r["minor_per_hour"],
        "currency": r["currency"], "generation": str(r["generation"]), "owner": r["owner"],
        "updated_at": r["updated_at"].isoformat(),
        "registered": {"generation": str(r["registered_generation"] or 0), "owner": r["registered_owner"]},
    }


# ---------------------------------------------------------------- etcd: who holds the role (O6.1)


def _etcd(path: str, body: dict[str, Any]) -> dict[str, Any]:
    request = urllib.request.Request(config.ETCD_URL.rstrip("/") + path, data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=2) as reply:
        return json.loads(reply.read() or b"{}")


def _read_role() -> dict[str, Any]:
    key = base64.b64encode(config.ROLE_KEY.encode()).decode()
    reply = _etcd("/v3/kv/range", {"key": key})
    kvs = reply.get("kvs", [])
    revision = reply.get("header", {}).get("revision")
    if not kvs:
        return {"owner": None, "generation": None, "lease_ttl_s": config.LEASE_TTL_S, "ttl_left_s": None,
                "etcd_revision": revision}
    kv = kvs[0]
    lease = kv.get("lease", "0")
    ttl = _etcd("/v3/lease/timetolive", {"ID": lease}) if lease != "0" else {}
    return {
        "owner": base64.b64decode(kv.get("value", "")).decode(),
        "generation": str(kv["create_revision"]),
        "lease": lease,
        "lease_ttl_s": int(ttl.get("grantedTTL", config.LEASE_TTL_S)),
        "ttl_left_s": int(ttl["TTL"]) if "TTL" in ttl else None,
        "etcd_revision": revision,
    }


@router.get("/coordinator")
async def get_coordinator() -> dict[str, Any]:
    try:
        role = await asyncio.to_thread(_read_role)
    except OSError as error:
        raise Problem(503, "etcd-unavailable", "etcd is not reachable", type(error).__name__) from None
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT substr(resource, 8) AS garage_id, generation, owner, updated_at FROM fence
               WHERE resource LIKE 'tariff/%' AND resource !~ '^tariff/t-' ORDER BY resource"""
        )
        fences = await cur.fetchall()
    return {
        "role": config.ROLE_KEY,
        **role,
        "registered": [{"garage_id": f["garage_id"], "generation": str(f["generation"]), "owner": f["owner"],
                        "at": f["updated_at"].isoformat()} for f in fences],
    }


# ---------------------------------------------------------------- history and its check (O6.3)

GARAGE = Query(None, min_length=1, max_length=64)


def _row(r: dict[str, Any]) -> dict[str, Any]:
    return {**r, "id": str(r["id"]), "generation": str(r["generation"]), "created_at": r["created_at"].isoformat()}


@router.get("/history")
async def get_history(limit: Annotated[int, Query(ge=1, le=1000)] = 50,
                      garage_id: str | None = GARAGE) -> dict[str, Any]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT id, garage_id, minor_per_hour, owner, generation, accepted, reason, created_at
               FROM tariff_history
               WHERE %(g)s::text IS NULL OR garage_id = %(g)s
               ORDER BY id DESC LIMIT %(n)s""",
            {"g": garage_id, "n": limit},
        )
        rows = await cur.fetchall()
    return {"items": [_row(r) for r in rows]}


async def check_history(conn: AsyncConnection, garage_id: str | None = None) -> dict[str, Any]:
    """Two rules over the whole history of each garage:
    1. stale-accepted: an accepted write whose generation is below a generation registered for the same garage
       BEFORE it (by id: the order of the fence lock);
    2. tariff-mismatch: the current tariff of a garage is not the value, generation and owner of its last accepted
       write."""
    cur = await conn.execute(
        """WITH h AS (
               SELECT *, max(generation) FILTER (WHERE reason = 'registered')
                         OVER (PARTITION BY garage_id ORDER BY id
                               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS registered
               FROM tariff_history WHERE %(g)s::text IS NULL OR garage_id = %(g)s)
           SELECT id, garage_id, minor_per_hour, owner, generation, reason, registered, created_at FROM h
           WHERE accepted AND reason <> 'registered' AND generation < registered
           ORDER BY id""",
        {"g": garage_id},
    )
    violations: list[dict[str, Any]] = [
        {"kind": "stale-accepted", **_row(r), "registered": str(r["registered"])} for r in await cur.fetchall()
    ]
    cur = await conn.execute(
        """SELECT DISTINCT ON (h.garage_id) h.garage_id, h.id, h.minor_per_hour, h.owner, h.generation,
                  t.minor_per_hour AS t_minor, t.owner AS t_owner, t.generation AS t_generation
           FROM tariff_history h JOIN tariff t ON t.garage_id = h.garage_id
           WHERE h.accepted AND h.reason <> 'registered' AND (%(g)s::text IS NULL OR h.garage_id = %(g)s)
           ORDER BY h.garage_id, h.id DESC""",
        {"g": garage_id},
    )
    checked = 0
    for r in await cur.fetchall():
        checked += 1
        if (r["t_minor"], r["t_owner"], r["t_generation"]) != (r["minor_per_hour"], r["owner"], r["generation"]):
            violations.append({
                "kind": "tariff-mismatch", "garage_id": r["garage_id"], "history_id": str(r["id"]),
                "expected": {"minor_per_hour": r["minor_per_hour"], "owner": r["owner"],
                             "generation": str(r["generation"])},
                "actual": {"minor_per_hour": r["t_minor"], "owner": r["t_owner"],
                           "generation": str(r["t_generation"])},
            })
    cur = await conn.execute(
        """SELECT count(*) FILTER (WHERE reason <> 'registered') AS writes,
                  count(*) FILTER (WHERE NOT accepted) AS rejected,
                  count(*) FILTER (WHERE reason = 'registered') AS registrations
           FROM tariff_history WHERE %(g)s::text IS NULL OR garage_id = %(g)s""",
        {"g": garage_id},
    )
    counts = await cur.fetchone()
    return {"ok": not violations, "garage_id": garage_id, "garages_checked": checked, **counts,
            "violations": violations}


@router.get("/history/check")
async def get_history_check(garage_id: str | None = GARAGE) -> dict[str, Any]:
    async with db.connection() as conn, conn.transaction():
        await conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")  # one snapshot for both rules
        return await check_history(conn, garage_id)
