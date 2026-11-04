"""Tests of Web lab 1 against the running stand (docker compose up -d nginx).

Run from the repository root (any OS; the tests use the sync drivers):
    uv run --no-project --with pytest --with httpx --with "psycopg[binary]" pytest web/server/tests -v

WEB_BASE (default http://localhost:8080), APP_A / APP_B (:8001 / :8002) and TEST_DATABASE_URL
(postgresql://app:app@localhost:5432/app) can be overridden. Every run books hours of its own
random future day, so runs do not interfere with each other or with the demo data.
"""

import os
import random
import uuid
from datetime import date, timedelta

import httpx
import psycopg
import pytest
from psycopg.rows import dict_row

BASE = os.environ.get("WEB_BASE", "http://localhost:8080")
APP_A = os.environ.get("APP_A", "http://localhost:8001")
APP_B = os.environ.get("APP_B", "http://localhost:8002")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")


@pytest.fixture(scope="session")
def db():
    with psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row) as conn:
        yield conn


@pytest.fixture(scope="session")
def api():
    with httpx.Client(base_url=BASE, timeout=10) as client:
        yield client


@pytest.fixture
def day() -> date:
    """A random day far in the future that no other run has used (with high probability).
    Not a day when the clock changes (last Sundays of March and October) nor the day before it,
    so the test day and the next one have 24 hours with one UTC offset."""
    while True:
        d = date(2040, 1, 1) + timedelta(days=random.randrange(0, 365 * 50))
        if not any(x.month in (3, 10) and x.weekday() == 6 and (x + timedelta(days=7)).month != x.month
                   for x in (d, d + timedelta(days=1))):
            return d


def _new_user(db) -> str:
    user_id = f"t-{uuid.uuid4().hex[:12]}"
    db.execute("INSERT INTO app_user (id, login, role) VALUES (%s, %s, 'user')", (user_id, user_id))
    return user_id


@pytest.fixture
def fresh_user(db) -> str:
    """A user with no bookings: the list tests see only their own rows."""
    return _new_user(db)


@pytest.fixture(scope="session")
def users(db) -> tuple[str, str]:
    """Two test users of this run; the demo users u-1, u-2 stay clean for the client."""
    return _new_user(db), _new_user(db)


def headers(user: str) -> dict[str, str]:
    return {"X-User-Id": user, "Idempotency-Key": f"test-{uuid.uuid4()}"}
