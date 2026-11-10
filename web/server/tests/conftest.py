"""Tests of Web labs 1-3 against the running stand (docker compose up -d nginx publisher).

Run from the repository root (any OS; the tests use the sync drivers):
    uv run --no-project --with pytest --with httpx --with "psycopg[binary]" pytest web/server/tests -v

WEB_BASE (default http://localhost:8080), APP_A / APP_B (:8001 / :8002) and TEST_DATABASE_URL
(postgresql://app:app@localhost:5432/app) can be overridden. Every run books hours of its own
random future day, so runs do not interfere with each other or with the demo data.

Users: every test user is a new row of app_user inserted here directly (not through the API) with the
argon2id hash of TEST_PASSWORD, then logged in through POST /v1/auth/login; requests carry its token in
`Authorization: Bearer`. The hash was made once by `uv run scripts/hash_password.py test-pass`.
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


TEST_PASSWORD = "test-pass"
TEST_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$vkhUgKZZXM9rBCOVlAgdlw$ERIz1Kb2NiHHSPcjNM6xUMfUt5DOgsrB4XycURHXs74"


class TestUser(str):
    """The user id (a str, so it goes straight into SQL parameters) with the token of its session."""

    token: str
    login: str


def login(login: str, password: str, base: str = BASE) -> httpx.Response:
    return httpx.post(f"{base}/v1/auth/login", json={"login": login, "password": password}, timeout=10)


def make_user(db, role: str = "user", prefix: str = "t") -> TestUser:
    user_id = f"{prefix}-{uuid.uuid4().hex[:12]}"
    db.execute(
        "INSERT INTO app_user (id, login, role, password_hash) VALUES (%s, %s, %s, %s)",
        (user_id, user_id, role, TEST_PASSWORD_HASH),
    )
    r = login(user_id, TEST_PASSWORD)
    assert r.status_code == 200, r.text
    user = TestUser(user_id)
    user.token, user.login = r.json()["access_token"], user_id
    return user


@pytest.fixture
def fresh_user(db) -> TestUser:
    """A user with no bookings: the list tests see only their own rows."""
    return make_user(db)


@pytest.fixture(scope="session")
def users(db) -> tuple[TestUser, TestUser]:
    """Two test users of this run; the demo users user1, user2 stay clean for the client."""
    return make_user(db), make_user(db)


@pytest.fixture(scope="session")
def staff(db) -> TestUser:
    return make_user(db, role="staff", prefix="ts")


def auth(user: TestUser) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


def headers(user: TestUser) -> dict[str, str]:
    return {**auth(user), "Idempotency-Key": f"test-{uuid.uuid4()}"}
