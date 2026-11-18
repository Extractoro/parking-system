"""DPC lab 4: the batch report of Spark through the api (O4.2), its reference (O4.3), the pointer (D4.3).

  POST /v1/reports/main/refresh   -> spark-runner POST /jobs {script: report.py, args: [..., /data/reports/runs/{run}]}
                                     202 {job: {run, state}}; 409 report-running while a report job runs
  GET  /v1/reports/main           -> {job, published, rows}: the newest report job of the runner and the rows
                                     of the published run (the pointer /data/reports/current)
  GET  /v1/reports/main/reference -> {published, rows}: the reference over the SAME set as the published run

The api keeps no job state of its own: the runner is the source of the job state, the pointer file is the
source of the published report, so a restart of the api loses nothing. Before the first successful run
GET /v1/reports/main is 200 with job.state "none" (or "running") and rows [].
"""

import asyncio
import json
import time
from pathlib import Path
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Body, Depends, Response
from pydantic import BaseModel, ConfigDict

from . import auth, config, reference
from .jsonlog import log
from .problems import Problem

router = APIRouter(prefix="/v1/reports", dependencies=[Depends(auth.require_token)])

POINTER = Path(config.REPORTS_DIR) / "current"
RUNS = Path(config.REPORTS_DIR) / "runs"


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset: Literal["small", "big"] | None = None
    join: Literal["auto", "shuffle"] = "auto"


def dataset_paths(name: str) -> tuple[str, str]:
    root = f"{config.REPORT_DATA_DIR}/{name}"
    return f"{root}/history", f"{root}/garages"


def _runner() -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=config.RUNNER_URL, timeout=config.RUNNER_TIMEOUT_S)


def _job_view(job: dict[str, Any] | None) -> dict[str, Any]:
    if job is None:
        return {"run": None, "state": "none"}
    args = job.get("args", [])
    opt = {args[i]: args[i + 1] for i in range(len(args) - 1) if args[i].startswith("--")}
    return {
        "run": job["run"],
        "state": job["state"],
        "facts": opt.get("--facts"),
        "join": opt.get("--join", "auto"),
        "seconds": job.get("seconds"),
        "exit_code": job.get("exit_code"),
    }


async def _latest_job() -> dict[str, Any] | None:
    """The newest report job of the runner (GET /jobs is newest first); None = the runner knows none."""
    async with _runner() as client:
        r = await client.get("/jobs")
    r.raise_for_status()
    return next((j for j in r.json()["jobs"] if j["script"] == config.REPORT_SCRIPT), None)


def _read_published() -> dict[str, Any] | None:
    """The pointer and the rows of the run it names (rows.json of that run's own folder)."""
    try:
        pointer = json.loads(POINTER.read_text(encoding="utf-8"))
        rows = json.loads((Path(pointer["path"]) / "rows.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    return {"pointer": pointer, "rows": rows["rows"]}


@router.post("/main/refresh", status_code=202)
async def refresh(response: Response, body: Annotated[RefreshBody | None, Body()] = None) -> dict[str, Any]:
    body = body or RefreshBody()
    facts, dim = dataset_paths(body.dataset or config.REPORT_DATASET)
    args = ["--facts", facts, "--dim", dim, "--out", f"{RUNS}/{{run}}", "--pointer", str(POINTER), "--join", body.join]
    try:
        async with _runner() as client:
            r = await client.post("/jobs", json={"script": config.REPORT_SCRIPT, "args": args})
    except httpx.HTTPError as exc:
        raise Problem(503, "runner-unavailable", "Spark job runner is unavailable", type(exc).__name__,
                      headers={"Retry-After": "5"}) from None
    if r.status_code == 409:  # the runner starts one job per script: a report is already running
        running = None
        try:
            running = (await _latest_job() or {}).get("run")
        except httpx.HTTPError:
            pass
        raise Problem(409, "report-running", "A report is already running",
                      "Wait for the running report to finish, then refresh again", run=running)
    if r.status_code != 202:
        raise Problem(502, "runner-error", "Spark job runner refused the job", r.text[:200])
    job = r.json()
    log("report.started", run=job["run"], facts=facts, join=body.join)
    response.headers["Location"] = "/v1/reports/main"
    return {"job": _job_view(job)}


@router.get("/main")
async def get_report() -> dict[str, Any]:
    try:
        job = _job_view(await _latest_job())
    except httpx.HTTPError:
        job = {"run": None, "state": "unknown"}  # the runner is down; the published report is still readable
    published = await asyncio.to_thread(_read_published)
    if published is None:
        return {"job": job, "published": None, "rows": []}
    return {"job": job, "published": published["pointer"], "rows": published["rows"]}


class _Cache:
    key: Any = None
    value: dict[str, Any] | None = None
    lock = asyncio.Lock()


@router.get("/main/reference")
async def get_reference() -> dict[str, Any]:
    published = await asyncio.to_thread(_read_published)
    if published is not None:
        pointer = published["pointer"]
        facts, dim = pointer["facts"], pointer["dim"]
    else:
        pointer = None
        facts, dim = dataset_paths(config.REPORT_DATASET)
    if not Path(facts).exists() or not Path(dim).exists():
        raise Problem(404, "no-data", "No data set for the report",
                      f"{facts} not found: generate it with docker compose run --rm generator")

    async with _Cache.lock:  # one computation at a time; the same set is computed once
        key = await asyncio.to_thread(lambda: (reference.signature(facts), reference.signature(dim)))
        if _Cache.key != key or _Cache.value is None:
            started = time.monotonic()
            rows = await asyncio.to_thread(reference.compute, facts, dim)
            seconds = round(time.monotonic() - started, 3)
            _Cache.key, _Cache.value = key, {"facts": facts, "dim": dim, "seconds": seconds, "rows": rows}
            log("report.reference", facts=facts, rows=len(rows), seconds=seconds)
    return {"published": pointer, **_Cache.value}
