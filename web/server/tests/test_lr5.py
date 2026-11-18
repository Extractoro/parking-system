"""Web lab 5: WebSocket tickets, the gateways, the notification feed.

Stand: docker compose up -d nginx publisher worker gateway-a gateway-b. The tests need `websockets` besides
the packages of conftest.py (`--with websockets`). GATEWAY_A / GATEWAY_B (ws://localhost:8091 / :8092) and
CLIENT_ORIGIN (http://localhost:5173) can be overridden. Notifications are made by bookings of a future day
on the test garage of the run (as test_lr4.py does).
"""

import json
import os
import subprocess
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from websockets.exceptions import ConnectionClosed, InvalidStatus
from websockets.sync.client import connect

from conftest import BASE, auth, headers, login, make_user

WS = BASE.replace("http", "ws", 1)
GATEWAY_A = os.environ.get("GATEWAY_A", "ws://localhost:8091")
GATEWAY_B = os.environ.get("GATEWAY_B", "ws://localhost:8092")
ORIGIN = os.environ.get("CLIENT_ORIGIN", "http://localhost:5173")


@pytest.fixture(scope="module")
def level(db):
    garage = f"t-{uuid.uuid4().hex[:8]}"
    db.execute("INSERT INTO garage (id, name, tz) VALUES (%s, %s, 'Europe/Kyiv')", (garage, garage))
    db.execute("INSERT INTO tariff (garage_id, minor_per_hour) VALUES (%s, 1000)", (garage,))
    db.execute("INSERT INTO level (id, garage_id, name, spots) VALUES (%s, %s, 'L1', 100)", (garage + "-1", garage))
    yield garage + "-1"
    db.execute("DELETE FROM tariff WHERE garage_id = %s", (garage,))


_hour = iter(range(10_000))


def book(api, user, level_id: str, now: bool = False) -> dict:
    if now:
        start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    else:
        start = datetime(2050, 1, 1, tzinfo=UTC) + timedelta(hours=next(_hour))
    body = {"level_id": level_id, "from_hour": start.isoformat(), "hours": 1, "plate": "TT 0005 TT"}
    r = api.post("/v1/bookings", json=body, headers=headers(user))
    assert r.status_code == 201, r.text
    return r.json()


def ticket(api, user) -> str:
    r = api.post("/v1/ws-tickets", headers=auth(user))
    assert r.status_code == 201, r.text
    assert r.json()["expires_in"] == 30
    return r.json()["ticket"]


def open_ws(url: str, t: str | None, origin: str | None = ORIGIN):
    return connect(f"{url}/v1/ws" + (f"?ticket={t}" if t is not None else ""), origin=origin, open_timeout=5)


def refused(url: str, t: str | None, origin: str | None = ORIGIN) -> int:
    with pytest.raises(InvalidStatus) as exc:
        with open_ws(url, t, origin):
            pass
    return exc.value.response.status_code


def frames(ws, seconds: float) -> list[dict]:
    """Every notification frame that comes within `seconds` (the hello frame is skipped)."""
    out, deadline = [], time.monotonic() + seconds
    while (left := deadline - time.monotonic()) > 0:
        try:
            frame = json.loads(ws.recv(timeout=left))
        except TimeoutError:
            break
        if "position" in frame:
            out.append(frame)
    return out


def hello(ws) -> str:
    frame = json.loads(ws.recv(timeout=5))
    assert frame["type"] == "hello"
    return frame["gateway"]


def test_1_a_ticket_opens_one_connection(api, db):
    user = make_user(db)
    assert api.post("/v1/ws-tickets").status_code == 401
    t = ticket(api, user)
    row = db.execute("SELECT user_id, used_at FROM ws_ticket WHERE ticket_hash = encode(sha256(%s::bytea), 'hex')",
                     (t,)).fetchone()
    assert row == {"user_id": str(user), "used_at": None}  # only the hash of the ticket is stored
    with open_ws(WS, t) as ws:
        assert hello(ws) in ("gateway-a", "gateway-b")
        # the same ticket again, also on the other gateway: refused before accept (close 1008 -> HTTP 403)
        assert refused(GATEWAY_A, t) == 403
        assert refused(GATEWAY_B, t) == 403


def test_2_expired_ticket_other_origin_and_no_ticket_are_refused(api, db):
    user = make_user(db)
    t = ticket(api, user)
    assert refused(WS, t, "http://localhost:8082") == 403
    assert refused(WS, t, None) == 403
    assert refused(WS, None) == 403
    assert refused(WS, "x" * 43) == 403
    db.execute("UPDATE ws_ticket SET expires_at = now() - interval '1 second' "
               "WHERE ticket_hash = encode(sha256(%s::bytea), 'hex')", (t,))
    assert refused(WS, t) == 403  # expired (the wrong Origin above did not use it up)


def test_3_both_gateways_deliver_only_to_the_user(api, db, level):
    a, b = make_user(db), make_user(db)
    with open_ws(GATEWAY_A, ticket(api, a)) as wa, open_ws(GATEWAY_B, ticket(api, a)) as wb, \
            open_ws(WS, ticket(api, b)) as other:
        assert (hello(wa), hello(wb)) == ("gateway-a", "gateway-b")
        hello(other)
        booking = book(api, a, level)
        got_a, got_b = frames(wa, 3), frames(wb, 0.5)
        assert len(got_a) == 1 and got_a == got_b
        assert set(got_a[0]) == {"id", "position", "type", "text", "created_at"}
        assert (got_a[0]["position"], got_a[0]["type"]) == (1, "booking.confirmed")
        assert frames(other, 0.5) == []
    row = db.execute("SELECT id, payload->>'booking_id' AS booking FROM notification WHERE user_id = %s",
                     (a,)).fetchone()
    assert row == {"id": got_a[0]["id"], "booking": booking["id"]}


def test_4_logout_closes_the_connections_of_that_session_only(api, db, level):
    user = make_user(db)
    other_session = login(user.login, "test-pass").json()["access_token"]
    with open_ws(GATEWAY_A, ticket(api, user)) as wa, open_ws(GATEWAY_B, ticket(api, user)) as wb:
        hello(wa), hello(wb)
        r = api.post("/v1/ws-tickets", headers={"Authorization": f"Bearer {other_session}"})
        with open_ws(WS, r.json()["ticket"]) as kept:
            hello(kept)
            assert api.post("/v1/auth/logout", headers=auth(user)).status_code == 204
            for ws in (wa, wb):
                with pytest.raises(ConnectionClosed) as exc:
                    ws.recv(timeout=10)
                assert exc.value.rcvd.code == 1008
            # the other session of the same user keeps its connection and gets the next notification
            other = type(user)(user)
            other.token = other_session
            book(api, other, level)
            assert [f["type"] for f in frames(kept, 3)] == ["booking.confirmed"]
    assert api.post("/v1/ws-tickets", headers=auth(user)).status_code == 401


def test_5_feed_and_head(api, db, level):
    user = make_user(db)
    assert api.get("/v1/me/notifications/head", headers=auth(user)).json() == {"position": 0}
    for _ in range(5):
        book(api, user, level)
    assert api.get("/v1/me/notifications/head", headers=auth(user)).json() == {"position": 5}
    page = api.get("/v1/me/notifications", params={"after": 1, "limit": 3}, headers=auth(user)).json()
    assert [i["position"] for i in page["items"]] == [2, 3, 4] and page["next_after"] == 4
    page = api.get("/v1/me/notifications", params={"after": 4, "limit": 3}, headers=auth(user)).json()
    assert [i["position"] for i in page["items"]] == [5] and page["next_after"] is None
    other = make_user(db)  # only own notifications
    assert api.get("/v1/me/notifications", headers=auth(other)).json()["items"] == []
    r = api.get("/v1/me/notifications", params={"after": "abc"}, headers=auth(user))
    assert r.status_code == 400 and r.headers["content-type"].startswith("application/problem+json")
    assert api.get("/v1/me/notifications").status_code == 401


def test_6_parallel_notifications_get_positions_without_gaps(db, level):
    user = make_user(db)
    import concurrent.futures

    def one(_):
        with httpx.Client(base_url=BASE, timeout=30) as client:
            return book(client, user, level)

    with concurrent.futures.ThreadPoolExecutor(20) as pool:
        list(pool.map(one, range(20)))
    positions = [r["position"] for r in db.execute(
        "SELECT position FROM notification WHERE user_id = %s ORDER BY position", (user,)).fetchall()]
    assert positions == list(range(1, 21))
    # every notification has its message in the outbox of the topic notifications, key = user
    n = db.execute("SELECT count(*) AS n FROM outbox WHERE topic = 'notifications' AND msg_key = %s "
                   "AND type = 'notification.created'", (user,)).fetchone()["n"]
    assert n == 20


def test_7_ending_soon_is_added_once(api, db, level):
    user = make_user(db)
    b = book(api, user, level, now=True)  # ends at the next full hour, within 60 minutes
    cmd = ["docker", "compose", "exec", "-T", "worker", "python", "-m", "app.worker", "--ending-once", "60"]
    env = {**os.environ, "MSYS_NO_PATHCONV": "1"}
    for _ in range(2):
        subprocess.run(cmd, check=True, capture_output=True, env=env, timeout=60)
    rows = db.execute("SELECT type, source_event_id FROM notification WHERE user_id = %s ORDER BY position",
                      (user,)).fetchall()
    assert rows == [{"type": "booking.confirmed", "source_event_id": b["id"]},
                    {"type": "booking.ending", "source_event_id": b["id"] + ":ending"}]
