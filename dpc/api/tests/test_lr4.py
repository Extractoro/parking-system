"""DPC lab 4: the reference without Spark (O4.3), the comparison by keys, the report through the api (O4.2).

The first tests need only pyarrow (a fixed set of 7 rows written to a temporary folder); the last ones run
against the stand: docker compose up -d api spark-master spark-worker-1 spark-worker-2 spark-runner and the
small set (docker compose run --rm generator). Run with --with pyarrow:
    uv run --no-project --with pytest --with httpx --with "psycopg[binary]" --with pyarrow pytest dpc/api/tests -v
"""

import sys
import time
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import reference  # noqa: E402

KEYS = ["garage_id"]


@pytest.fixture
def fixed_set(tmp_path: Path) -> tuple[Path, Path]:
    """7 facts in two Parquet parts (one garage only in the second part), 3 garages of which 1 has no facts."""
    facts, dim = tmp_path / "history", tmp_path / "garages"
    facts.mkdir()
    dim.mkdir()
    pq.write_table(
        pa.table({"garage_id": ["mall", "mall", "center", "mall"], "minutes": [10, 61, 600, 59],
                  "amount_minor": [3000, 6000, 40000, 3000]}),
        facts / "part-00000.parquet",
    )
    pq.write_table(
        pa.table({"garage_id": ["center", "mall", "podil"], "minutes": [120, 1, 7],
                  "amount_minor": [8000, 3000, 3500]}),
        facts / "part-00001.parquet",
    )
    pq.write_table(
        pa.table({"garage_id": ["mall", "center", "airport"], "name": ["ТЦ Південний", "Центр", "Аеропорт"]}),
        dim / "garages.parquet",
    )
    return facts, dim


def test_reference_on_fixed_set(fixed_set):
    rows = reference.compute(*fixed_set)
    assert rows == [
        {"garage_id": "center", "name": "Центр", "sessions": 2, "total_minutes": 720, "avg_minutes": 360.0,
         "revenue_minor": 48000},
        # 4 sessions, (10 + 61 + 59 + 1) / 4 = 32.75: the sum over the count, not an average of part averages
        {"garage_id": "mall", "name": "ТЦ Південний", "sessions": 4, "total_minutes": 131, "avg_minutes": 32.75,
         "revenue_minor": 15000},
    ]  # podil has no row in the reference table (inner join), airport has no facts
    assert all(type(r[f]) is int for r in rows for f in reference.INT_FIELDS)


def test_reference_reads_in_batches(fixed_set, monkeypatch):
    whole = reference.compute(*fixed_set)
    monkeypatch.setattr(reference, "BATCH_ROWS", 2)  # many small batches give the same totals
    assert reference.compute(*fixed_set) == whole


def test_compare_by_keys():
    a = [{"garage_id": "mall", "sessions": 4, "avg_minutes": 32.75}, {"garage_id": "center", "sessions": 2,
                                                                      "avg_minutes": 360.0}]
    b = list(reversed(a))  # order does not matter, keys do
    assert reference.compare(a, b, KEYS, 1e-9) == []
    assert reference.compare(a, [b[0] | {"avg_minutes": 360.0 + 1e-5}, b[1]], KEYS, 1e-9)  # beyond the tolerance
    assert reference.compare(a, [b[0] | {"avg_minutes": 360.0 * (1 + 1e-12)}, b[1]], KEYS, 1e-9) == []
    assert reference.compare(a, [b[0] | {"sessions": 3}, b[1]], KEYS, 1e-9)  # counts exactly
    assert reference.compare(a, [b[0] | {"sessions": 2.0}, b[1]], KEYS, 1e-9)  # and of integer type
    assert reference.compare(a, b[:1], KEYS, 1e-9) == ["('mall',): missing in the reference"]


def wait_not_running(api, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = api.get("/v1/reports/main")
        assert r.status_code == 200, r.text
        if r.json()["job"]["state"] != "running":
            return r.json()
        time.sleep(1)
    raise AssertionError("the report job did not finish")


def test_refresh_then_report_equals_reference(api):
    wait_not_running(api)
    r = api.post("/v1/reports/main/refresh", json={"dataset": "small"})
    assert r.status_code == 202, r.text
    run = r.json()["job"]["run"]
    assert r.json()["job"]["state"] == "running" and r.headers["Location"] == "/v1/reports/main"

    again = api.post("/v1/reports/main/refresh")  # the runner keeps one report job at a time
    assert again.status_code == 409, again.text
    assert again.headers["content-type"].startswith("application/problem+json")
    assert again.json()["type"].endswith("/report-running") and again.json()["run"] == run

    report = wait_not_running(api)
    assert report["job"]["run"] == run and report["job"]["state"] == "done", report["job"]
    assert report["published"]["run"] == run  # D4.3: the pointer names the new run
    ref = api.get("/v1/reports/main/reference").json()
    assert ref["facts"] == report["published"]["facts"]
    assert len(report["rows"]) == 10
    assert reference.compare(report["rows"], ref["rows"], KEYS, 1e-9) == []


def test_refresh_validation_and_auth(api):
    assert api.post("/v1/reports/main/refresh", json={"dataset": "../etc"}).status_code == 400
    assert api.post("/v1/reports/main/refresh", json={"join": "nested"}).status_code == 400
    assert api.get("/v1/reports/main", headers={"Authorization": "Bearer wrong"}).status_code == 401
