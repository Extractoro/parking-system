"""Helpers of the DPC experiments (lab 1): a client of the api, entry bodies, final states, percentiles.

Experiments run from the repository root against the stand (docker compose up -d api):
    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/dpc_lr1_<name>.py
DPC_BASE (http://localhost:8000), API_TOKEN (dev-token), TEST_DATABASE_URL (postgresql://app:app@localhost:5432/app).
"""

import math
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime

import httpx
import psycopg
from psycopg.rows import dict_row

BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
FINAL = ("done", "rejected", "failed")
RACE_GARAGE = "dpc-race"


def client(timeout: float = 30) -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=timeout, headers={"Authorization": f"Bearer {TOKEN}"})


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def body(garage: str, plate: str, direction: str = "in", event_id: str | None = None) -> dict:
    return {"event_id": event_id or str(uuid.uuid4()), "garage_id": garage, "plate": plate,
            "direction": direction, "event_time": now_iso()}


def wait_final(c: httpx.Client, location: str, poll_s: float = 0.05, timeout: float = 120) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = c.get(location)
        r.raise_for_status()
        if r.json()["state"] in FINAL:
            return r.json()
        time.sleep(poll_s)
    raise TimeoutError(location)


def post_until_accepted(c: httpx.Client, b: dict, attempts: int = 600) -> httpx.Response:
    """Repeat after 429 with the same event_id (the same request), honouring Retry-After."""
    for _ in range(attempts):
        r = c.post("/v1/entries", json=b)
        if r.status_code != 429:
            return r
        time.sleep(float(r.headers.get("Retry-After", "1")))
    return r


def pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return None
    s = sorted(values)
    k = max(0, min(len(s) - 1, math.ceil(q / 100 * len(s)) - 1))
    return round(s[k], 1)


def triple(values: list[float]) -> dict:
    return {"n": len(values), "p50": pct(values, 50), "p95": pct(values, 95), "p99": pct(values, 99),
            "max": round(max(values), 1) if values else None}


def db() -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)


def reset_race_garage() -> None:
    """The test garage dpc-race only: forget its requests and entries, occupied = 0."""
    with db() as conn:
        conn.execute("DELETE FROM entry WHERE garage_id = %s", (RACE_GARAGE,))
        conn.execute("DELETE FROM entry_request WHERE garage_id = %s", (RACE_GARAGE,))
        conn.execute("UPDATE garage_state SET occupied = 0 WHERE garage_id = %s", (RACE_GARAGE,))


def compose(*args: str, env: dict | None = None, check: bool = True) -> str:
    e = {**os.environ, **(env or {}), "MSYS_NO_PATHCONV": "1"}
    p = subprocess.run(["docker", "compose", *args], capture_output=True, text=True, env=e, check=check)
    return p.stdout + p.stderr


def restart_api(**env: str) -> None:
    """Recreate api with the given experiment settings (QUEUE_CAPACITY, WORKERS, RESOURCE_MODE)."""
    compose("up", "-d", "--force-recreate", "--no-deps", "api", env={k: str(v) for k, v in env.items()})
    wait_health()


def wait_health(timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.3)
    raise TimeoutError("api /health")


def metrics() -> dict:
    return httpx.get(f"{BASE}/metrics", timeout=5).json()
