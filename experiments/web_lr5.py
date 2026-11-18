"""Web lab 5 experiments: the authorized WebSocket connection, delivery through two gateways, the feed and
recovery. Run from the repository root against the stand, output to results/web-lr5/:

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" --with websockets \
        python experiments/web_lr5.py <what> | tee results/web-lr5/<what>.txt

  auth      tickets and Origin: a valid ticket; the same ticket again; an expired ticket (31 s); another
            Origin; no ticket; no login for a ticket; logout closes with 1008 the connections of that session
            on both gateways, another session of the same user stays open and gets the next notification
  two       one user, two clients straight to :8091 (gateway-a) and :8092 (gateway-b), user B through nginx:
            a booking -> both clients get the same notification (id, position), B gets nothing
  window    a notification created between the subscription and head (the client pauses 3 s there):
            applied exactly once (from the history, the buffered live copy is skipped)
  gateway   the failure of card C09: client on gateway-a, docker compose stop gateway-a, 3 bookings, the
            client reconnects through nginx to gateway-b and reads all 3 (no gaps, no duplicates); start
  parallel  50 notifications of one user created in parallel: positions 1..50 without gaps; a client that
            drops its connection at random gets each exactly once
  e2e       lab 4 path: a booking of the current hour, open the barrier -> «Шлагбаум відкрито» arrives live
  ending    «Бронювання закінчується за 15 хвилин»: one round of the worker check (window 60 min) adds it to a
            booking of the current hour once; a second round adds nothing

Stand: docker compose up -d nginx publisher worker relay external-sim gateway-a gateway-b (+ api for e2e).
Every run makes its own users and its own garage t5-xxxx (one level, 200 spots), written straight into the
database like the fixtures of the tests; its tariff is deleted at the end, so the catalog does not list it.
"""

import asyncio
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import psycopg
from psycopg.rows import dict_row
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

sys.path.insert(0, os.path.dirname(__file__))
import feed_client  # noqa: E402
from feed_client import ORIGIN, FeedClient  # noqa: E402

WEB = os.environ.get("WEB_BASE", "http://localhost:8080")
WS_NGINX = WEB.replace("http", "ws", 1)
WS_A = os.environ.get("GATEWAY_A", "ws://localhost:8091")
WS_B = os.environ.get("GATEWAY_B", "ws://localhost:8092")
DB = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
PASSWORD = "test-pass"
PASSWORD_HASH = "$argon2id$v=19$m=65536,t=3,p=4$vkhUgKZZXM9rBCOVlAgdlw$ERIz1Kb2NiHHSPcjNM6xUMfUt5DOgsrB4XycURHXs74"
sys.stdout.reconfigure(encoding="utf-8")

db = psycopg.connect(DB, autocommit=True, row_factory=dict_row)
T0 = time.monotonic()
feed_client.CLOCK = T0  # one time axis for the script and its clients


def say(*a) -> None:
    print(f"{time.monotonic() - T0:7.2f}s", *a, flush=True)


def sh(cmd: str) -> str:
    say("$", cmd)
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=ENV)
    text = (out.stdout + out.stderr).strip()
    if text:
        print(text, flush=True)
    return text


# --- fixtures ---------------------------------------------------------------------------------------------
def new_user() -> str:
    user_id = f"t5-{uuid.uuid4().hex[:10]}"
    db.execute("INSERT INTO app_user (id, login, role, password_hash) VALUES (%s, %s, 'user', %s)",
               (user_id, user_id, PASSWORD_HASH))
    return user_id


def login(user_id: str) -> str:
    r = httpx.post(f"{WEB}/v1/auth/login", json={"login": user_id, "password": PASSWORD}, timeout=10)
    r.raise_for_status()
    return r.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


class Garage:
    def __init__(self) -> None:
        self.id = f"t5-{uuid.uuid4().hex[:8]}"
        self.level = self.id + "-1"
        db.execute("INSERT INTO garage (id, name, tz) VALUES (%s, %s, 'Europe/Kyiv')", (self.id, self.id))
        db.execute("INSERT INTO tariff (garage_id, minor_per_hour) VALUES (%s, 1000)", (self.id,))
        db.execute("INSERT INTO level (id, garage_id, name, spots) VALUES (%s, %s, 'L1', 200)", (self.level, self.id))
        self.day = date(2045, 1, 1) + timedelta(days=int(uuid.uuid4().int % (365 * 40)))
        self.n = 0

    def close(self) -> None:
        db.execute("DELETE FROM tariff WHERE garage_id = %s", (self.id,))

    def body(self, now: bool = False) -> dict:
        if now:
            start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
        else:  # a new hour of a future day each time (the 200 spots are never the limit)
            self.n += 1
            start = datetime(self.day.year, self.day.month, self.day.day, tzinfo=UTC) + timedelta(hours=self.n % 20)
        return {"level_id": self.level, "from_hour": start.isoformat(), "hours": 1, "plate": f"LR5{self.n:04d}"}


async def book(http: httpx.AsyncClient, garage: Garage, token: str, now: bool = False) -> dict:
    r = await http.post(f"{WEB}/v1/bookings", json=garage.body(now),
                        headers={**auth(token), "Idempotency-Key": f"lr5-{uuid.uuid4()}"})
    assert r.status_code == 201, r.text
    return r.json()


def feed_rows(user_id: str) -> list[dict]:
    return db.execute("SELECT position, id, type, payload->>'text' AS text FROM notification "
                      "WHERE user_id = %s ORDER BY position", (user_id,)).fetchall()


def gateway_lines(since: str, pattern: str) -> str:
    out = subprocess.run(f'docker compose logs --since {since} --no-log-prefix gateway-a gateway-b | grep "{pattern}"',
                         shell=True, capture_output=True, text=True, env=ENV)
    return out.stdout.strip()


async def ticket(http: httpx.AsyncClient, token: str) -> str:
    r = await http.post(f"{WEB}/v1/ws-tickets", headers=auth(token))
    assert r.status_code == 201, r.text
    return r.json()["ticket"]


async def try_open(url: str, origin: str | None = ORIGIN) -> str:
    """Open a socket and report what happened: hello of a gateway, or the refusal."""
    try:
        async with connect(url, origin=origin, open_timeout=5) as ws:
            hello = await asyncio.wait_for(ws.recv(), 5)
            return f"open, first frame {hello}"
    except InvalidStatus as exc:
        return f"refused: HTTP {exc.response.status_code} to the handshake (close 1008 before accept)"
    except ConnectionClosed as exc:
        return f"closed: code {exc.rcvd.code if exc.rcvd else None}"


# --- experiments ------------------------------------------------------------------------------------------
async def exp_auth() -> None:
    user = new_user()
    token1, token2 = login(user), login(user)  # two login sessions of one user
    say("user", user, "with two sessions (two logins)")
    async with httpx.AsyncClient(timeout=10) as http:
        r = await http.post(f"{WEB}/v1/ws-tickets")
        say("POST /v1/ws-tickets without login ->", r.status_code, r.json().get("type", "").rsplit("/", 1)[-1])
        r = await http.post(f"{WEB}/v1/ws-tickets", headers=auth(token1))
        body = r.json()
        say("POST /v1/ws-tickets ->", r.status_code, {k: v for k, v in body.items() if k != "ticket"},
            "ticket length", len(body["ticket"]), "Cache-Control:", r.headers.get("cache-control"))
        t = body["ticket"]
        row = db.execute("SELECT user_id, expires_at - now() AS ttl, used_at FROM ws_ticket WHERE ticket_hash = "
                         "encode(sha256(%s::bytea), 'hex')", (t,)).fetchone()
        say("ws_ticket row: user", row["user_id"], "ttl", row["ttl"], "used_at", row["used_at"], "(only sha256 stored)")

        say("1) valid ticket through nginx:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}"))
        say("2) the same ticket again:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}"))
        say("   the same ticket again, straight to gateway-b:", await try_open(f"{WS_B}/v1/ws?ticket={t}"))
        t = await ticket(http, token1)
        say("3) another Origin (http://localhost:8082):", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}",
                                                                         "http://localhost:8082"))
        say("   no Origin at all:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}", None))
        say("   the same ticket with the right Origin after that:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}"))
        say("4) no ticket:", await try_open(f"{WS_NGINX}/v1/ws"))
        say("   a made-up ticket:", await try_open(f"{WS_NGINX}/v1/ws?ticket=" + "x" * 43))
        t = await ticket(http, token1)
        say("5) ticket taken, waiting 31 s (ttl 30 s) ...")
        await asyncio.sleep(31)
        say("   expired ticket:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t}"))

        say("6) logout closes the connections of that session only")
        stop = asyncio.Event()
        a1 = FeedClient("session1@gateway-a", WEB, token1, [WS_A])
        a2 = FeedClient("session1@gateway-b", WEB, token1, [WS_B])
        b = FeedClient("session2@nginx", WEB, token2, [WS_NGINX])
        tasks = [asyncio.create_task(c.run(stop)) for c in (a1, a2, b)]
        for c in (a1, a2, b):
            await asyncio.wait_for(c.online.wait(), 10)
        t_unused = await ticket(http, token1)  # taken before the logout, used after it
        since = datetime.now(UTC).isoformat()
        r = await http.post(f"{WEB}/v1/auth/logout", headers=auth(token1))
        say("POST /v1/auth/logout (session 1) ->", r.status_code)
        await asyncio.wait_for(asyncio.gather(a1.revoked.wait(), a2.revoked.wait()), 10)
        say("session 1 on gateway-a closed with", a1.close_codes[-1], "| on gateway-b closed with", a2.close_codes[-1])
        say("ticket of session 1 taken before the logout:", await try_open(f"{WS_NGINX}/v1/ws?ticket={t_unused}"))
        r = await http.post(f"{WEB}/v1/ws-tickets", headers=auth(token1))
        say("new ticket with the token of session 1 ->", r.status_code)
        garage = Garage()
        try:
            await book(http, garage, token2)
            await asyncio.sleep(2)
        finally:
            garage.close()
        say("session 2: still open:", b.online.is_set(), "closes", b.close_codes, "applied",
            [(i["position"], i["type"]) for i in b.applied])
        stop.set()
        await asyncio.gather(*tasks)
        await asyncio.sleep(1)
        print(gateway_lines(since, "ws.session_revoked"))


async def exp_two() -> None:
    user, other = new_user(), new_user()
    token, token_b = login(user), login(other)
    garage = Garage()
    stop = asyncio.Event()
    c_a = FeedClient("U@:8091", WEB, token, [WS_A])
    c_b = FeedClient("U@:8092", WEB, token, [WS_B])
    c_other = FeedClient("B@nginx", WEB, token_b, [WS_NGINX])
    clients = (c_a, c_b, c_other)
    tasks = [asyncio.create_task(c.run(stop)) for c in clients]
    try:
        for c in clients:
            await asyncio.wait_for(c.online.wait(), 10)
        since = datetime.now(UTC).isoformat()
        async with httpx.AsyncClient(timeout=10) as http:
            for _ in range(2):
                b = await book(http, garage, token)
                say("booked", b["id"][:8], "for user U")
        await asyncio.sleep(3)
    finally:
        stop.set()
        await asyncio.gather(*tasks)
        garage.close()
    for c in clients:
        say(c.name, "gateway", c.gateways, "applied", [(i["position"], i["id"][:8], i["text"]) for i in c.applied])
    same = [(i["id"], i["position"]) for i in c_a.applied] == [(i["id"], i["position"]) for i in c_b.applied]
    say("U on gateway-a and gateway-b got the same (id, position):", same, "| B got:", len(c_other.applied))
    say("feed of U in the database:", [(r["position"], r["id"][:8]) for r in feed_rows(user)])
    print(gateway_lines(since, "ws.delivered"))


async def exp_window() -> None:
    user = new_user()
    token = login(user)
    garage = Garage()
    stop = asyncio.Event()
    http = httpx.AsyncClient(timeout=10)

    async def during_window() -> None:
        b = await book(http, garage, token)
        say("subscribed; booking", b["id"][:8], "created before head is read")

    client = FeedClient("window", WEB, token, [WS_NGINX], pause_s=3, on_subscribed=during_window)
    task = asyncio.create_task(client.run(stop))
    try:
        await asyncio.wait_for(client.online.wait(), 15)
        await asyncio.sleep(1)
    finally:
        stop.set()
        await task
        await http.aclose()
        garage.close()
    say("applied", [(i["position"], i["type"]) for i in client.applied], "times by id",
        dict((k[:8], v) for k, v in client.times_applied.items()), "skipped live duplicates", client.skipped)


async def exp_gateway() -> None:
    user = new_user()
    token = login(user)
    garage = Garage()
    stop = asyncio.Event()
    client = FeedClient("client", WEB, token, [WS_A, WS_NGINX])
    task = asyncio.create_task(client.run(stop))
    try:
        await asyncio.wait_for(client.online.wait(), 10)
        async with httpx.AsyncClient(timeout=10) as http:
            await book(http, garage, token)
            await asyncio.sleep(1)
            say("before the failure: last_applied", client.last_applied, "on", client.gateways[-1])
            sh("docker compose stop gateway-a")
            for _ in range(3):
                b = await book(http, garage, token)
                say("booked", b["id"][:8], "while gateway-a is stopped")
            say("head now", db.execute("SELECT last_position FROM notification_seq WHERE user_id = %s",
                                       (user,)).fetchone()["last_position"])
            deadline = time.monotonic() + 30
            while client.last_applied < 4 and time.monotonic() < deadline:
                await asyncio.sleep(0.2)
            await asyncio.sleep(1)
    finally:
        stop.set()
        await task
        garage.close()
        sh("docker compose start gateway-a")
    say("sessions", client.sessions, "gateways", client.gateways, "close codes", client.close_codes)
    say("applied positions", [i["position"] for i in client.applied], "skipped duplicates", client.skipped,
        "max times applied", max(client.times_applied.values()))


async def exp_parallel() -> None:
    user = new_user()
    token = login(user)
    garage = Garage()
    stop = asyncio.Event()
    client = FeedClient("dropper", WEB, token, [WS_NGINX, WS_A, WS_B, WS_NGINX], drop_share=0.15, seed=5,
                        quiet=True)
    task = asyncio.create_task(client.run(stop))
    try:
        await asyncio.wait_for(client.online.wait(), 10)
        async with httpx.AsyncClient(timeout=30, limits=httpx.Limits(max_connections=60)) as http:
            started = time.monotonic()
            rng = random.Random(7)

            async def one() -> dict:  # 50 concurrent tasks, each starts within 3 s
                await asyncio.sleep(rng.uniform(0, 3))
                return await book(http, garage, token)

            results = await asyncio.gather(*(one() for _ in range(50)), return_exceptions=True)
            failed = [r for r in results if isinstance(r, Exception)]
            say("50 bookings in parallel (each starts at random within 3 s):", 50 - len(failed), "x 201 in",
                round(time.monotonic() - started, 2), "s, errors", len(failed))
            peak = db.execute("""SELECT count(*) AS n FROM notification n1 JOIN notification n2
                                 ON n1.user_id = n2.user_id AND n1.position < n2.position
                                 AND n1.created_at > n2.created_at WHERE n1.user_id = %s""", (user,)).fetchone()
            say("pairs where a larger position has an earlier created_at (start of the transaction):", peak["n"])
        deadline = time.monotonic() + 60
        while client.last_applied < 50 and time.monotonic() < deadline:
            await asyncio.sleep(0.2)
    finally:
        stop.set()
        await task
        garage.close()
    rows = feed_rows(user)
    positions = [r["position"] for r in rows]
    say("positions in the database:", len(positions), "from", positions[0], "to", positions[-1],
        "gaps:", sorted(set(range(1, 51)) - set(positions)))
    applied = [i["position"] for i in client.applied]
    say("client: sessions", client.sessions, "(random drops)", "applied", len(applied), "in order",
        applied == list(range(1, 51)), "each id once", set(client.times_applied.values()) == {1},
        "ids match the database", {i["id"] for i in client.applied} == {r["id"] for r in rows},
        "live duplicates skipped", client.skipped, "via", dict(client.via))
    say("gateways of the sessions:", client.gateways)


async def exp_e2e() -> None:
    user = new_user()
    token = login(user)
    garage = Garage()
    stop = asyncio.Event()
    client = FeedClient("e2e", WEB, token, [WS_NGINX])
    task = asyncio.create_task(client.run(stop))
    try:
        await asyncio.wait_for(client.online.wait(), 10)
        async with httpx.AsyncClient(timeout=10) as http:
            b = await book(http, garage, token, now=True)
            say("booking of the current hour", b["id"][:8])
            r = await http.post(f"{WEB}/v1/bookings/{b['id']}/entries",
                                headers={**auth(token), "Idempotency-Key": f"lr5-{uuid.uuid4()}"})
            say("POST .../entries ->", r.status_code, r.headers.get("location"))
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and not any(i["type"] == "entry.opened" for i in client.applied):
                await asyncio.sleep(0.2)
            op = (await http.get(WEB + r.headers["location"], headers=auth(token))).json()
            say("operation", op["state"], op.get("result"))
    finally:
        stop.set()
        await task
        garage.close()
    say("applied live:", [(i["position"], i["type"], i["text"]) for i in client.applied])


async def exp_ending() -> None:
    user = new_user()
    token = login(user)
    garage = Garage()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            b = await book(http, garage, token, now=True)
        say("booking of the current hour", b["id"][:8], "ends", b["to_hour"])
        for _ in range(2):
            sh("docker compose exec -T worker python -m app.worker --ending-once 60")
    finally:
        garage.close()
    for r in feed_rows(user):
        say("feed", r["position"], r["type"], r["text"])


EXPERIMENTS = {"auth": exp_auth, "two": exp_two, "window": exp_window, "gateway": exp_gateway,
               "parallel": exp_parallel, "e2e": exp_e2e, "ending": exp_ending}

if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    if what not in EXPERIMENTS:
        sys.exit(f"usage: web_lr5.py {{{'|'.join(EXPERIMENTS)}}}")
    say("experiment", what, datetime.now(UTC).isoformat(timespec="seconds"))
    asyncio.run(EXPERIMENTS[what]())
