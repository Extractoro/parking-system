"""Web lab 4: the long operation "open the barrier", outbox, worker, notifications.

Stand: docker compose up -d nginx publisher worker relay external-sim (+ api for the relay).
SIM (http://localhost:8090) can be overridden.

Entry is possible only inside the booked hours, so these tests book the CURRENT hour. To keep the demo
data and the occupancy of DPC clean, the run makes its own garage t-xxxx with a tariff and one level
(test fixtures written straight into the database, as the other tests do with users). The garage has no
row in garage_state of DPC, so the relay gets 404 from DPC and stops there (relay.refused); the tariff is
deleted at the end of the run, so the Web catalog does not list the garage.
"""

import os
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from conftest import auth, headers, make_user

SIM = os.environ.get("SIM", "http://localhost:8090")


def slug(r) -> str:
    return r.json()["type"].rsplit("/", 1)[-1]


@pytest.fixture(scope="module")
def level(db):
    garage = f"t-{uuid.uuid4().hex[:8]}"
    db.execute("INSERT INTO garage (id, name, tz) VALUES (%s, %s, 'Europe/Kyiv')", (garage, garage))
    db.execute("INSERT INTO tariff (garage_id, minor_per_hour) VALUES (%s, 1000)", (garage,))
    db.execute("INSERT INTO level (id, garage_id, name, spots) VALUES (%s, %s, 'L1', 10)", (garage + "-1", garage))
    yield garage + "-1"
    db.execute("DELETE FROM tariff WHERE garage_id = %s", (garage,))


def book_now(api, user, level_id: str, hours: int = 2) -> dict:
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    body = {"level_id": level_id, "from_hour": start.isoformat(), "hours": hours, "plate": "TT 0001 TT"}
    r = api.post("/v1/bookings", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def open_barrier(api, user, booking_id: str, key: str | None = None) -> httpx.Response:
    h = {**auth(user), "Idempotency-Key": key or f"test-{uuid.uuid4()}"}
    return api.post(f"/v1/bookings/{booking_id}/entries", headers=h)


def wait_final(api, user, location: str, seconds: float = 60) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        r = api.get(location, headers=auth(user))
        assert r.status_code == 200
        if r.json()["state"] in ("succeeded", "failed"):
            assert "Retry-After" not in r.headers
            return r.json()
        assert r.headers["Retry-After"] == "2"
        time.sleep(0.5)
    raise AssertionError("the operation did not finish")


def test_1_entry_returns_202_and_a_retry_returns_the_same_operation(api, db, level):
    user = make_user(db)
    b = book_now(api, user, level)
    key = f"test-{uuid.uuid4()}"
    first = open_barrier(api, user, b["id"], key)
    assert first.status_code == 202, first.text
    op = first.json()
    assert first.headers["Location"] == f"/v1/operations/{op['id']}"
    assert first.headers["Retry-After"] == "2"
    assert (op["kind"], op["booking_id"], op["state"], op["version"]) == ("entry", b["id"], "pending", 1)

    again = open_barrier(api, user, b["id"], key)
    assert again.status_code == 202 and again.json()["id"] == op["id"]
    assert again.headers["Idempotent-Replayed"] == "true"
    assert db.execute("SELECT count(*) AS n FROM operation WHERE booking_id = %s", (b["id"],)).fetchone()["n"] == 1

    other = open_barrier(api, user, b["id"])  # another key while the first one runs
    assert other.status_code == 409 and slug(other) in ("entry-in-progress", "already-entered")

    # the outbox event of the same transaction, in the CloudEvents envelope
    ev = db.execute("SELECT * FROM outbox WHERE type = 'entry.requested' AND msg_key = %s", (b["id"],)).fetchone()
    p = ev["payload"]
    assert (p["specversion"], p["source"], p["type"], p["subject"], p["id"]) == (
        "1.0", "parking-web", "entry.requested", b["id"], ev["id"])
    assert p["data"]["entity_id"] == b["id"] and p["data"]["operation_id"] == op["id"]


def test_2_operation_succeeds_with_one_effect_and_one_notification(api, db, level):
    user = make_user(db)
    b = book_now(api, user, level)
    r = open_barrier(api, user, b["id"])
    assert r.status_code == 202
    op = wait_final(api, user, r.headers["Location"])
    assert op["state"] == "succeeded", op  # SIM_REJECT_SHARE=0: 503s are retried until accepted
    assert op["version"] == 3 and op["result"]["opened_at"].endswith("Z")

    effects = httpx.get(f"{SIM}/effects", params={"key": op["id"]}).json()["effects"][op["id"]]
    assert effects["effects"] == 1
    n = db.execute("SELECT count(*) AS n FROM notification WHERE user_id = %s", (user,)).fetchone()["n"]
    assert n == 1
    opened = db.execute("SELECT payload FROM outbox WHERE type = 'entry.opened' AND msg_key = %s",
                        (b["id"],)).fetchall()
    assert len(opened) == 1 and opened[0]["payload"]["data"]["operation_id"] == op["id"]

    again = open_barrier(api, user, b["id"])
    assert again.status_code == 409 and slug(again) == "already-entered"


def test_3_repeated_event_gives_no_second_notification(api, db, level):
    """The publisher sends the stored entry.requested again with the same id (as after a crash
    between the send and COMMIT): the worker finds (worker, id) in processed_message and skips it."""
    user = make_user(db)
    b = book_now(api, user, level)
    r = open_barrier(api, user, b["id"])
    op = wait_final(api, user, r.headers["Location"])
    ev = db.execute("SELECT id FROM outbox WHERE type = 'entry.requested' AND msg_key = %s", (b["id"],)).fetchone()
    db.execute("UPDATE outbox SET published_at = NULL WHERE id = %s", (ev["id"],))
    deadline = time.monotonic() + 10
    while db.execute("SELECT published_at FROM outbox WHERE id = %s", (ev["id"],)).fetchone()["published_at"] is None:
        assert time.monotonic() < deadline, "not published again"
        time.sleep(0.2)
    time.sleep(3)  # the worker reads the repeated message
    assert db.execute("SELECT count(*) AS n FROM notification WHERE user_id = %s", (user,)).fetchone()["n"] == 1
    assert db.execute("SELECT count(*) AS n FROM processed_message WHERE event_id = %s",
                      (ev["id"],)).fetchone()["n"] == 1
    now = api.get(r.headers["Location"], headers=auth(user)).json()
    assert (now["state"], now["version"]) == (op["state"], op["version"])


def test_4_foreign_operation_is_404(api, db, level):
    owner, other = make_user(db), make_user(db)
    b = book_now(api, owner, level, hours=1)
    r = open_barrier(api, owner, b["id"])
    assert r.status_code == 202
    foreign = api.get(r.headers["Location"], headers=auth(other))
    missing = api.get(f"/v1/operations/{uuid.uuid4()}", headers=auth(other))
    assert foreign.status_code == missing.status_code == 404
    assert slug(foreign) == slug(missing) == "operation-not-found"
    assert api.get(r.headers["Location"]).status_code == 401
    # someone else's booking: the same 404 as a missing one, no operation is created
    stolen = open_barrier(api, other, b["id"])
    assert stolen.status_code == 404 and slug(stolen) == "booking-not-found"


def test_5_entry_outside_the_interval_is_409(api, db, level, day):
    user = make_user(db)
    start = datetime(day.year, day.month, day.day, 10, tzinfo=UTC)
    body = {"level_id": level, "from_hour": start.isoformat(), "hours": 1, "plate": "TT 0002 TT"}
    b = api.post("/v1/bookings", json=body, headers=headers(user)).json()
    r = open_barrier(api, user, b["id"])
    assert r.status_code == 409 and slug(r) == "outside-interval"
    assert db.execute("SELECT count(*) AS n FROM operation WHERE booking_id = %s", (b["id"],)).fetchone()["n"] == 0


def test_6_entry_needs_a_key_and_a_login(api, db, level):
    user = make_user(db)
    b = book_now(api, user, level, hours=1)
    assert api.post(f"/v1/bookings/{b['id']}/entries", headers=auth(user)).status_code == 400
    assert api.post(f"/v1/bookings/{b['id']}/entries", headers={"Idempotency-Key": "k"}).status_code == 401


def test_7_booking_created_event_is_in_the_outbox(api, db, level):
    user = make_user(db)
    b = book_now(api, user, level, hours=1)
    ev = db.execute("SELECT * FROM outbox WHERE type = 'booking.created' AND msg_key = %s", (b["id"],)).fetchone()
    data = ev["payload"]["data"]
    assert (data["entity_id"], data["entity_version"], data["user_id"]) == (b["id"], 1, user)
    deadline = time.monotonic() + 10
    while db.execute("SELECT published_at FROM outbox WHERE id = %s", (ev["id"],)).fetchone()["published_at"] is None:
        assert time.monotonic() < deadline, "the publisher did not send it"
        time.sleep(0.2)


def test_8_status_entered_comes_from_the_entries_of_dpc(api, db, level):
    """B3: Web reads the entry table of DPC. A test row of entry (a fixture, as DPC itself would write it)
    turns the booking into 'entered' in the object and in the list; the stored status stays confirmed."""
    user = make_user(db)
    b = book_now(api, user, level, hours=1)
    garage = level.rsplit("-", 1)[0]
    rid = str(uuid.uuid4())
    db.execute("INSERT INTO garage_state (garage_id, spots, occupied) VALUES (%s, 10, 0) ON CONFLICT DO NOTHING",
               (garage,))
    db.execute(
        """INSERT INTO entry_request (id, event_id, garage_id, plate, direction, event_time, booking_id, state,
                                      finished_at) VALUES (%s, %s, %s, 'TT0001TT', 'in', now(), %s, 'done', now())""",
        (rid, rid, garage, b["id"]))
    db.execute("""INSERT INTO entry (event_id, request_id, garage_id, plate, direction, booking_id, event_time)
                  VALUES (%s, %s, %s, 'TT0001TT', 'in', %s, now())""", (rid, rid, garage, b["id"]))
    assert api.get(f"/v1/bookings/{b['id']}", headers=auth(user)).json()["status"] == "entered"
    items = api.get("/v1/bookings", headers=auth(user)).json()["items"]
    assert [i["status"] for i in items] == ["entered"]
    assert db.execute("SELECT status FROM booking WHERE id = %s", (b["id"],)).fetchone()["status"] == "confirmed"
    r = open_barrier(api, user, b["id"])
    assert r.status_code == 409 and slug(r) == "already-entered"
