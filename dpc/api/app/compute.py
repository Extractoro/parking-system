"""POST /v1/compute and its state (DPC lab 2, O2.1-O2.4).

POST /v1/compute       -> 202 + Location + ComputeJob; a computation already running -> 429 Retry-After
GET  /v1/compute/{id}  -> ComputeJob: state running | done | failed, active_parts, result
GET  /v1/compute       -> the last jobs (the Compute page of the client)

The integral itself is in integrate.py. One computation at a time: a new process pool per job takes
all CPUs of the container, a second pool would only slow both down (and dozens of pools would exhaust
memory). The pool is created and awaited in a thread of the default executor (run_in_executor), so the
event loop keeps serving /health and the queue of lab 1 while the processes compute. Jobs live in
memory (the last COMPUTE_KEEP); a restart loses them, the computation is deterministic and repeatable.
"""

import asyncio
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_validator

from . import auth, config, integrate, metrics
from .jsonlog import log
from .problems import Problem

N_MAX = 1_000_000_000


class ComputeCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    a: float = Field(ge=0, le=integrate.T, description="start hour of the day")
    b: float = Field(ge=0, le=integrate.T, description="end hour of the day")
    n: int = Field(ge=1, le=N_MAX, description="number of subintervals")
    workers: Literal[1, 2, 4]
    method: Literal["midpoint", "trapezoid", "simpson"] = "midpoint"
    order: Literal["forward", "reverse"] = "forward"
    mode: Literal["none", "lock", "local"] = "local"
    block: int | None = Field(default=None, ge=1_000, le=10_000_000)
    omax: float = Field(default=integrate.OMAX, gt=0, le=100_000)

    @model_validator(mode="after")
    def interval_ok(self) -> ComputeCreate:
        if not self.a < self.b:
            raise ValueError("a must be less than b")
        if self.method == "simpson" and self.n % 2:
            raise ValueError("simpson needs an even n")
        return self


@dataclass
class Job:
    id: str
    params: dict[str, Any]
    created: datetime
    state: str = "running"
    active_parts: int = 0
    result: dict[str, Any] | None = None
    error: str | None = None
    finished: datetime | None = None
    pool: Any = None


@dataclass
class State:
    jobs: OrderedDict[str, Job] = field(default_factory=OrderedDict)
    running: Job | None = None
    done: int = 0
    rejected: int = 0
    tasks: set[asyncio.Task[None]] = field(default_factory=set)


state = State()


def _iso(v: datetime | None) -> str | None:
    return v.isoformat(timespec="milliseconds").replace("+00:00", "Z") if v else None


def view(job: Job) -> dict[str, Any]:
    result = None
    if job.result is not None:
        result = dict(job.result)
        result["within_tolerance"] = result["rel_error"] <= config.COMPUTE_TOLERANCE
        result["tolerance"] = config.COMPUTE_TOLERANCE
    return {
        "id": job.id,
        "state": job.state,
        "active_parts": job.active_parts,
        "params": job.params,
        "result": result,
        "error": job.error,
        "created_at": _iso(job.created),
        "finished_at": _iso(job.finished),
    }


def start() -> None:
    metrics.gauge("compute_running", lambda: 1 if state.running else 0)
    metrics.gauge("compute_done", lambda: state.done)
    metrics.gauge("compute_rejected", lambda: state.rejected)


async def stop() -> None:
    """Shutdown: a running computation is not drained, its processes are terminated."""
    job = state.running
    if job and job.pool is not None:
        job.pool.terminate()
    for t in list(state.tasks):
        t.cancel()
    await asyncio.gather(*state.tasks, return_exceptions=True)


async def _run(job: Job) -> None:
    loop = asyncio.get_running_loop()

    def part_done() -> None:  # called in the result thread of the pool
        job.active_parts -= 1

    def keep_pool(pool: Any) -> None:
        job.pool = pool

    p = job.params
    started = time.monotonic()
    try:
        job.result = await loop.run_in_executor(
            None,
            lambda: integrate.run(
                p["a"], p["b"], p["n"], p["workers"], p["method"], p["order"], p["mode"], p["block"], p["omax"],
                nice=config.COMPUTE_NICE, on_part=part_done, on_pool=keep_pool,
            ),
        )
        job.state = "done"
        r = job.result
        log("compute.done", id=job.id, workers=r["workers"], n=r["n"], method=r["method"], mode=r["mode"],
            seconds=round(r["seconds"], 4), rel_error=r["rel_error"])
    except Exception as exc:  # noqa: BLE001 - the job records the failure, the service goes on
        job.state = "failed"
        job.error = type(exc).__name__
        log("compute.failed", id=job.id, error=type(exc).__name__, seconds=round(time.monotonic() - started, 3))
    finally:
        job.active_parts = 0
        job.pool = None
        job.finished = datetime.now(UTC)
        state.done += 1
        state.running = None


router = APIRouter(prefix="/v1", dependencies=[Depends(auth.require_token)])


@router.post("/compute", status_code=202)
async def create_compute(body: ComputeCreate, response: Response) -> dict[str, Any]:
    if state.running is not None:  # check and set without an await in between: atomic on the event loop
        state.rejected += 1
        raise Problem(429, "compute-busy", "A computation is already running",
                      "One computation at a time; repeat after the current one ends",
                      headers={"Retry-After": str(config.COMPUTE_RETRY_AFTER_S)}, running=state.running.id)
    params = body.model_dump()
    params["block"] = body.block or (integrate.BLOCK if body.mode == "local" else integrate.SHARED_BLOCK)
    job = Job(id=str(uuid.uuid4()), params=params, created=datetime.now(UTC), active_parts=body.workers)
    state.running = job
    state.jobs[job.id] = job
    while len(state.jobs) > config.COMPUTE_KEEP:
        state.jobs.popitem(last=False)
    task = asyncio.create_task(_run(job), name=f"compute-{job.id}")
    state.tasks.add(task)
    task.add_done_callback(state.tasks.discard)
    log("compute.started", id=job.id, workers=body.workers, n=body.n, method=body.method, mode=body.mode)
    response.headers["Location"] = f"/v1/compute/{job.id}"
    return view(job)


@router.get("/compute/{job_id}")
async def get_compute(job_id: str) -> dict[str, Any]:
    job = state.jobs.get(job_id)
    if job is None:
        raise Problem(404, "not-found", "No such computation", f"computation {job_id} not found")
    return view(job)


@router.get("/compute")
async def list_compute(limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    jobs = list(state.jobs.values())[-limit:]
    jobs.reverse()
    return {"items": [view(j) for j in jobs], "running": state.running.id if state.running else None}

