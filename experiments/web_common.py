"""Shared helpers of the Web lab 2 experiments (run from the repository root against the stand).

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/<script>.py

WEB_BASE (http://localhost:8080), APP_A (:8001), APP_B (:8002) and TEST_DATABASE_URL can be overridden.
Every experiment books hours of its own random future days and its own new users. Since Web lab 3 a
new user is inserted with the argon2id hash of TEST_PASSWORD (made by `uv run scripts/hash_password.py
test-pass`) and logged in; requests send its token in `Authorization: Bearer` (X-User-Id is refused).
"""

import os
import random
import subprocess
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

BASE = os.environ.get("WEB_BASE", "http://localhost:8080")
APP_A = os.environ.get("APP_A", "http://localhost:8001")
APP_B = os.environ.get("APP_B", "http://localhost:8002")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
ROOT = Path(__file__).resolve().parents[1]
RESULTS = ROOT / "results" / "web-lr2"


def connect() -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)


TEST_PASSWORD = "test-pass"
TEST_PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$vkhUgKZZXM9rBCOVlAgdlw$ERIz1Kb2NiHHSPcjNM6xUMfUt5DOgsrB4XycURHXs74"


class User(str):
    """The user id (a str: goes straight into SQL parameters) with the token of its login session."""

    token: str


def new_user(db: psycopg.Connection, prefix: str) -> User:
    user_id = f"{prefix}-{uuid.uuid4().hex[:10]}"
    db.execute(
        "INSERT INTO app_user (id, login, role, password_hash) VALUES (%s, %s, 'user', %s)",
        (user_id, user_id, TEST_PASSWORD_HASH),
    )
    r = httpx.post(f"{BASE}/v1/auth/login", json={"login": user_id, "password": TEST_PASSWORD}, timeout=10)
    r.raise_for_status()
    user = User(user_id)
    user.token = r.json()["access_token"]
    return user


def demo_login(login: str, password: str) -> str:
    """Token of a demo user (user1/user1-pass, user2/user2-pass, staff1/staff1-pass, migration 904)."""
    r = httpx.post(f"{BASE}/v1/auth/login", json={"login": login, "password": password}, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def auth(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {user.token}"}


def future_days(n: int, rng: random.Random | None = None) -> list[date]:
    """n different days far in the future, none of them on or just before a clock change
    (last Sunday of March / October), so one UTC offset fits the day and the next one."""
    rng = rng or random.Random()
    out: set[date] = set()
    while len(out) < n:
        d = date(2045, 1, 1) + timedelta(days=rng.randrange(0, 365 * 40))
        if not any(x.month in (3, 10) and x.weekday() == 6 and (x + timedelta(days=7)).month != x.month
                   for x in (d, d + timedelta(days=1))):
            out.add(d)
    return sorted(out)


def offset(level: str, day: date) -> str:
    """UTC offset of the garage zone at noon of the day, from the API itself."""
    r = httpx.get(f"{BASE}/v1/levels/{level}/hours", params={"day": day.isoformat()}, timeout=10)
    r.raise_for_status()
    return r.json()["hours"][12]["hour"][-6:]


def at(day: date, hour: int, off: str) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00{off}"


def compose(*args: str) -> str:
    """docker compose <args> from the repository root; stdout as text."""
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    run = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, env=env, timeout=120)
    return run.stdout.strip()
