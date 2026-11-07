"""DPC lab 2: the parallel integral through POST /v1/compute (O2.1, O2.4), validation, one job at a time.

Needs the running api (docker compose up -d api). The sequential check imports app/integrate.py, so run
with numpy:
    uv run --no-project --with pytest --with httpx --with "psycopg[binary]" --with numpy pytest dpc/api/tests -v
"""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # dpc/api: app.integrate without FastAPI

from app import integrate

TOLERANCE = 1e-9  # relative, COMPUTE_TOLERANCE of the service and dpc.compute.tolerance of checks.yaml


def compute(api, **body) -> dict:
    """POST (repeating after 429 of a job of another test) and wait for the final state."""
    for _ in range(300):
        r = api.post("/v1/compute", json=body)
        if r.status_code != 429:
            break
        time.sleep(float(r.headers["Retry-After"]))
    assert r.status_code == 202, r.text
    assert r.headers["Location"] == f"/v1/compute/{r.json()['id']}"
    for _ in range(1200):
        job = api.get(r.headers["Location"]).json()
        if job["state"] != "running":
            assert job["state"] == "done", job
            assert job["active_parts"] == 0
            return job["result"]
        time.sleep(0.05)
    raise AssertionError("the computation did not end in 60 s")


def test_reference_of_the_card():
    assert integrate.reference(0, 14) == pytest.approx(8 * 81 * 14 / 15, rel=1e-15)  # 604.8 car-hours
    assert integrate.split(10, 4) == [(0, 3), (3, 6), (6, 8), (8, 10)]
    assert integrate.split(3, 4) == [(0, 1), (1, 2), (2, 3), (3, 3)]


@pytest.mark.parametrize("method", ["midpoint", "trapezoid", "simpson"])
def test_1_2_4_workers_within_tolerance(api, method):
    """O2.1: the same parameters on 1, 2 and 4 processes; every value within the tolerance of F(b) - F(a)."""
    values = []
    for workers in (1, 2, 4):
        r = compute(api, a=0, b=14, n=20_000_000, workers=workers, method=method)
        assert (r["workers"], r["n"], r["method"], r["mode"]) == (workers, 20_000_000, method, "local")
        assert r["timing"]["processes"] == workers
        assert r["reference"] == pytest.approx(604.8, rel=1e-15)
        assert r["rel_error"] <= TOLERANCE and r["within_tolerance"]
        assert r["seconds"] > 0 and 0 <= r["pool_start_seconds"] <= r["seconds"]
        values.append(r["value"])
    assert max(values) - min(values) <= TOLERANCE * 604.8  # not an exact equality: the sums differ in order


@pytest.mark.parametrize("method", ["midpoint", "trapezoid", "simpson"])
@pytest.mark.parametrize("n", [1, 2, 3, 6, 10, 1_002])
def test_small_n_parallel_equals_sequential(api, method, n):
    """The split of [0, n) and the end weights: a parallel run with tiny blocks equals the plain loop.
    n < workers gives empty ranges; [2.5; 9] has non-zero ends (on [0; 14] O(0) = O(14) = 0 hides them)."""
    if method == "simpson" and n % 2:
        pytest.skip("simpson needs an even n")
    for a, b in ((0, 14), (2.5, 9)):
        expected = integrate.sequential(a, b, n, method)
        for workers in (1, 4):
            r = compute(api, a=a, b=b, n=n, workers=workers, method=method, block=1_000)
            assert r["value"] == pytest.approx(expected, rel=1e-12), (a, b, n, workers)


def test_shared_sum_modes(api):
    """O2.4: lock and local match the reference; none on 4 processes loses updates (checked in the
    experiment with 5 runs; here at least the protected modes and the parameter itself)."""
    for mode in ("lock", "local"):
        r = compute(api, a=0, b=14, n=4_000_000, workers=4, mode=mode)
        assert r["mode"] == mode and r["rel_error"] <= TOLERANCE
    r = compute(api, a=0, b=14, n=4_000_000, workers=1, mode="none")
    assert r["rel_error"] <= TOLERANCE  # one process: no concurrent updates, nothing is lost
    assert r["block"] == 1_000


def test_validation(api):
    bad = [
        {"a": 0, "b": 14, "n": 100, "workers": 3},
        {"a": 5, "b": 5, "n": 100, "workers": 1},
        {"a": 0, "b": 15, "n": 100, "workers": 1},
        {"a": 0, "b": 14, "n": 0, "workers": 1},
        {"a": 0, "b": 14, "n": 101, "workers": 1, "method": "simpson"},
        {"a": 0, "b": 14, "n": 100, "workers": 1, "mode": "atomic"},
        {"a": 0, "b": 14, "n": 100, "workers": 1, "seed": 1},
        {"b": 14, "n": 100, "workers": 1},
    ]
    for body in bad:
        r = api.post("/v1/compute", json=body)
        assert r.status_code == 400, body
        assert r.headers["content-type"].startswith("application/problem+json")
        assert r.json()["type"].endswith("/invalid-request")
    r = api.get("/v1/compute/no-such-job")
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/problem+json")


def test_second_computation_429(api):
    """One computation at a time: the second POST while the first runs gets 429 with Retry-After."""
    for _ in range(300):
        first = api.post("/v1/compute", json={"a": 0, "b": 14, "n": 200_000_000, "workers": 1})
        if first.status_code == 202:
            break
        time.sleep(1)
    assert first.status_code == 202
    second = api.post("/v1/compute", json={"a": 0, "b": 14, "n": 1_000, "workers": 1})
    assert second.status_code == 429
    assert int(second.headers["Retry-After"]) >= 1
    assert second.json()["type"].endswith("/compute-busy")
    assert second.json()["running"] == first.json()["id"]
    health = api.get("/health")  # the event loop is free while the processes compute
    assert health.status_code == 200
    assert api.get(first.headers["Location"]).json()["state"] == "running"
    while api.get(first.headers["Location"]).json()["state"] == "running":
        time.sleep(0.1)
    listed = api.get("/v1/compute", params={"limit": 5}).json()
    assert listed["running"] is None and listed["items"][0]["id"] == first.json()["id"]
