"""Tests of DPC lab 1 against the running stand (docker compose up -d api, defaults QUEUE_CAPACITY=50,
WORKERS=4, RESOURCE_MODE=locked).

Run from the repository root (any OS; the tests use the sync drivers):
    uv run --no-project --with pytest --with httpx --with "psycopg[binary]" pytest dpc/api/tests -v

DPC_BASE (default http://localhost:8000), API_TOKEN (dev-token) and TEST_DATABASE_URL
(postgresql://app:app@localhost:5432/app) can be overridden. Every test makes its own garage
(t-xxxx, no tariff, so the Web catalog does not list it) and, if needed, its own level, user and
booking rows: these are test fixtures written straight into the database, the service itself
never writes the tables of Web.
"""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
FINAL = ("done", "rejected", "failed")


@pytest.fixture(scope="session")
def db():
    with psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}) as client:
        yield client


def iso(t: datetime) -> str:
    return t.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def now_iso() -> str:
    return iso(datetime.now(UTC))


def make_garage(db, spots: int) -> str:
    gid = f"t-{uuid.uuid4().hex[:10]}"
    db.execute("INSERT INTO garage (id, name, tz) VALUES (%s, %s, 'Europe/Kyiv')", (gid, gid))
    db.execute("INSERT INTO garage_state (garage_id, spots, occupied) VALUES (%s, %s, 0)", (gid, spots))
    return gid


@pytest.fixture
def garage(db):
    """A garage with 2 spots and one level of 2 spots for bookings."""
    gid = make_garage(db, 2)
    db.execute("INSERT INTO level (id, garage_id, name, spots) VALUES (%s, %s, 'L1', 2)", (f"{gid}-1", gid))
    return gid


def make_booking(db, garage_id: str, plate: str, hours_back: int = 0, status: str = "confirmed") -> str:
    """A booking of the current hour (or hours_back earlier) on the first level of the garage."""
    user = f"t-{uuid.uuid4().hex[:10]}"
    db.execute("INSERT INTO app_user (id, login, role) VALUES (%s, %s, 'user')", (user, user))
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0) - timedelta(hours=hours_back)
    bid = str(uuid.uuid4())
    db.execute(
        """INSERT INTO booking (id, user_id, level_id, plate, from_hour, hours, tariff_minor_per_hour,
                                price_minor, currency, status, expires_at)
           VALUES (%s, %s, %s, %s, %s, 1, 100, 100, 'UAH', %s, %s)""",
        (bid, user, f"{garage_id}-1", plate, start, status, start + timedelta(hours=1)),
    )
    return bid


def entry(api, garage_id: str, plate: str, direction: str = "in", booking_id: str | None = None,
          event_id: str | None = None, event_time: str | None = None) -> httpx.Response:
    body = {"event_id": event_id or f"e-{uuid.uuid4().hex}", "garage_id": garage_id, "plate": plate,
            "direction": direction, "event_time": event_time or now_iso()}
    if booking_id:
        body["booking_id"] = booking_id
    return api.post("/v1/entries", json=body)


def wait_final(api, location: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        r = api.get(location)
        assert r.status_code == 200
        if r.json()["state"] in FINAL:
            return r.json()
        time.sleep(0.1)
    raise AssertionError(f"{location} did not finish in {timeout} s")


def run(api, garage_id: str, plate: str, direction: str = "in", booking_id: str | None = None,
        **kw) -> dict:
    """POST (repeating after 429 with the same event_id) and wait for the final state."""
    event_id = kw.pop("event_id", None) or f"e-{uuid.uuid4().hex}"
    for _ in range(200):
        r = entry(api, garage_id, plate, direction, booking_id, event_id=event_id, **kw)
        if r.status_code != 429:
            break
        time.sleep(float(r.headers["Retry-After"]))
    assert r.status_code == 202, r.text
    return wait_final(api, r.headers["Location"])
