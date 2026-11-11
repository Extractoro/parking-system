"""Web lab 4 experiments: the barrier operation end to end, the two controlled failures, a refused opening,
the simulator and DPC unavailable, and the checks of the entry route.

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_lr4.py <what> \
        | tee results/web-lr4/<what>.txt

  e2e        booking of the current hour -> POST .../entries -> succeeded -> DPC request done -> booking entered;
             effects of the key, notifications of the operation, entries of the booking in DPC; the same
             event_id sent to DPC again -> the same request (Repeated-Request)
  publisher  fault publisher_before_send: the event is stored and not sent, after the restart it is sent and handled
  worker     fault after_external_before_commit: the barrier opened, the result lost; after the restart the same
             key, one effect, one notification
  rejected   SIM_REJECT_SHARE=1 (external-sim recreated, then restored): failed with the reason, no entry.opened,
             no entry in DPC
  simdown    POST /admin/unavailable?seconds=20: the operation waits (retries) and ends after the simulator is back
  dpcdown    docker compose stop api for 20 s: the relay retries, after start the entry is written once
  api        a retry with the same key -> the same operation; a foreign operation -> 404; outside the interval -> 409

Stand: docker compose up -d nginx publisher worker relay external-sim api. Bookings are made by user1 on
center-1 for the current hour with a new plate each time (the plate must not be inside the garage in DPC).
"""

import os
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
from psycopg.rows import dict_row

WEB = os.environ.get("WEB_BASE", "http://localhost:8080")
DPC = os.environ.get("DPC_BASE", "http://localhost:8000")
SIM = os.environ.get("SIM", "http://localhost:8090")
DB = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
DPC_TOKEN = os.environ.get("API_TOKEN", "dev-token")
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
sys.stdout.reconfigure(encoding="utf-8")  # the reasons of the simulator are Ukrainian (Windows console)

db = psycopg.connect(DB, autocommit=True, row_factory=dict_row)
web = httpx.Client(base_url=WEB, timeout=15)


def say(*a) -> None:
    print(*a, flush=True)


def sh(cmd: str, **env: str) -> str:
    say("$", " ".join(f"{k}={v}" for k, v in env.items()), cmd)
    out = subprocess.run(cmd, shell=True, capture_output=True, text=True, env=ENV | env)
    text = (out.stdout + out.stderr).strip()
    if text:
        say(text)
    return text


def one(sql: str, *args):
    row = db.execute(sql, args).fetchone()
    return None if row is None else next(iter(row.values()))


def login(name: str = "user1") -> dict:
    tok = web.post("/v1/auth/login", json={"login": name, "password": f"{name}-pass"}).json()["access_token"]
    return {"Authorization": f"Bearer {tok}"}


def book_now(h: dict, hours: int = 2, start: datetime | None = None) -> dict:
    start = start or datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    plate = f"LR{uuid.uuid4().hex[:4].upper()}EX"
    PLATES.append(plate)
    r = web.post("/v1/bookings", headers=h | {"Idempotency-Key": str(uuid.uuid4())},
                 json={"level_id": "center-1", "from_hour": start.isoformat(), "hours": hours, "plate": plate})
    r.raise_for_status()
    b = r.json()
    say(f"booking {b['id']} plate {plate} {b['from_hour']} .. {b['to_hour']} status {b['status']}")
    return b


def open_barrier(h: dict, booking_id: str, key: str | None = None) -> httpx.Response:
    r = web.post(f"/v1/bookings/{booking_id}/entries", headers=h | {"Idempotency-Key": key or str(uuid.uuid4())})
    say(f"POST /v1/bookings/{booking_id[:8]}../entries -> {r.status_code} Location={r.headers.get('Location')} "
        f"Retry-After={r.headers.get('Retry-After')} replayed={r.headers.get('Idempotent-Replayed')}")
    return r


def poll(h: dict, location: str, seconds: float = 90, quiet: bool = False) -> dict:
    deadline, last = time.monotonic() + seconds, None
    while time.monotonic() < deadline:
        op = web.get(location, headers=h).json()
        if not quiet and (op["state"], op["version"]) != last:
            say(f"  t={time.strftime('%H:%M:%S')} state={op['state']} version={op['version']} "
                f"result={op['result']} error={op['error']}")
            last = (op["state"], op["version"])
        if op["state"] in ("succeeded", "failed"):
            return op
        time.sleep(1)
    return op


def wait_entered(h: dict, booking_id: str, seconds: float = 30) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        st = web.get(f"/v1/bookings/{booking_id}", headers=h).json()["status"]
        if st == "entered":
            return st
        time.sleep(1)
    return st


def effects(op_id: str) -> dict:
    return httpx.get(f"{SIM}/effects", params={"key": op_id}).json()["effects"][op_id]


PLATES: list[str] = []


def leave_all() -> None:
    """The cars of the run drive out (POST /v1/entries direction out of the DPC api), so repeated runs
    do not fill the garage; only cars that DPC let in are inside."""
    for plate in PLATES:
        inside = one("""SELECT count(*) FILTER (WHERE direction = 'in') - count(*) FILTER (WHERE direction = 'out')
                        FROM entry WHERE garage_id = 'center' AND plate = %s""", plate)
        if inside:
            r = httpx.post(f"{DPC}/v1/entries", headers={"Authorization": f"Bearer {DPC_TOKEN}"}, json={
                "event_id": f"lr4-exit-{uuid.uuid4()}", "garage_id": "center", "plate": plate, "direction": "out",
                "event_time": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")})
            say(f"cleanup: {plate} leaves the garage (DPC exit) -> {r.status_code}")


def summary(op_id: str, booking_id: str) -> None:
    e = effects(op_id)
    say(f"GET :8090/effects?key={op_id[:8]}.. -> effects={e['effects']} result={e['result']} ids={e['ids']}")
    say("notifications of the operation:",
        one("SELECT count(*) FROM notification WHERE payload->>'operation_id' = %s", op_id))
    say("processed_message (worker) of entry.requested:", one(
        """SELECT count(*) FROM processed_message p JOIN outbox o ON o.id = p.event_id
           WHERE p.consumer = 'worker' AND o.type = 'entry.requested' AND o.payload->'data'->>'operation_id' = %s""",
        op_id))
    say("outbox of the booking:", [(r["type"], r["published"]) for r in db.execute(
        "SELECT type, published_at IS NOT NULL AS published FROM outbox WHERE msg_key = %s ORDER BY seq",
        (booking_id,))])
    say("DPC entry_request with event_id = operation:", [(r["state"], r["reason"]) for r in db.execute(
        "SELECT state, reason FROM entry_request WHERE event_id = %s", (op_id,))])
    say("DPC entry rows of the booking:", one("SELECT count(*) FROM entry WHERE booking_id = %s", booking_id))


def logs(service: str, since: str, pattern: str) -> None:
    out = subprocess.run(f"docker compose logs {service} --since {since} --no-log-prefix", shell=True,
                         capture_output=True, text=True, env=ENV).stdout
    for line in out.splitlines():
        if any(p in line for p in pattern.split("|")):
            say("  ", line[:260])


def wait_health(url: str, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(1)


# ------------------------------------------------------------------------------------------------ scenarios

def e2e() -> None:
    h = login()
    b = book_now(h)
    r = open_barrier(h, b["id"])
    op = poll(h, r.headers["Location"])
    say("booking status:", wait_entered(h, b["id"]))
    req = db.execute("SELECT id, state FROM entry_request WHERE event_id = %s", (op["id"],)).fetchone()
    say("DPC GET /v1/entries/{id}:", httpx.get(f"{DPC}/v1/entries/{req['id']}",
                                              headers={"Authorization": f"Bearer {DPC_TOKEN}"}).json()["state"])
    summary(op["id"], b["id"])
    say("== the same event_id sent to DPC again (as the relay does after a lost answer)")
    ev = one("SELECT payload FROM outbox WHERE type = 'entry.opened' AND msg_key = %s", b["id"])["data"]
    body = {"event_id": op["id"], "garage_id": ev["garage_id"], "plate": ev["plate"], "direction": "in",
            "event_time": ev["opened_at"], "booking_id": b["id"]}
    for i in (1, 2):
        rr = httpx.post(f"{DPC}/v1/entries", json=body, headers={"Authorization": f"Bearer {DPC_TOKEN}"})
        say(f"  repeat {i}: {rr.status_code} Location={rr.headers.get('Location')} "
            f"Repeated-Request={rr.headers.get('Repeated-Request')} state={rr.json()['state']}")
    say("DPC entry_request with this event_id:", one("SELECT count(*) FROM entry_request WHERE event_id = %s", op["id"]),
        "entry rows:", one("SELECT count(*) FROM entry WHERE booking_id = %s", b["id"]))
    logs("relay", "3m", op["id"])


def publisher() -> None:
    h = login()
    b = book_now(h)
    time.sleep(1.5)  # booking.created is published before the fault is armed
    sh("docker compose exec -T publisher touch /faults/publisher_before_send")
    r = open_barrier(h, b["id"])
    time.sleep(0.6)  # the publisher took the batch and died; Docker restarts it in about a second
    loc = r.headers["Location"]
    say("right after the crash:", web.get(loc, headers=h).json()["state"], "| entry.requested published:",
        one("SELECT published_at IS NOT NULL FROM outbox WHERE type = 'entry.requested' AND msg_key = %s", b["id"]))
    logs("publisher", "15s", "fault.hit|process.started")
    op = poll(h, loc)
    logs("publisher", "40s", b["id"])
    summary(op["id"], b["id"])
    say("booking status:", wait_entered(h, b["id"]))


def worker() -> None:
    h = login()
    b = book_now(h)
    sh("docker compose exec -T worker touch /faults/after_external_before_commit")
    r = open_barrier(h, b["id"])
    loc = r.headers["Location"]
    op_id = r.json()["id"]
    deadline = time.monotonic() + 30
    while effects(op_id)["effects"] == 0 and time.monotonic() < deadline:
        time.sleep(0.3)
    say("after the external action:", web.get(loc, headers=h).json()["state"], "| effects:", effects(op_id)["effects"],
        "| notifications:", one("SELECT count(*) FROM notification WHERE payload->>'operation_id' = %s", op_id))
    time.sleep(2)
    logs("worker", "15s", "fault.hit|process.started")
    op = poll(h, loc)
    logs("worker", "2m", op_id)
    logs("external-sim", "2m", op_id)
    summary(op["id"], b["id"])
    say("booking status:", wait_entered(h, b["id"]))


def rejected() -> None:
    sh("docker compose up -d external-sim", SIM_REJECT_SHARE="1")
    wait_health(f"{SIM}/health")
    try:
        h = login()
        b = book_now(h)
        r = open_barrier(h, b["id"])
        op = poll(h, r.headers["Location"])
        time.sleep(3)
        summary(op["id"], b["id"])
        say("booking status:", web.get(f"/v1/bookings/{b['id']}", headers=h).json()["status"])
        say("notification:", one("SELECT payload->>'text' FROM notification WHERE payload->>'operation_id' = %s",
                                 op["id"]))
        r2 = open_barrier(h, b["id"])  # a failed operation does not block a new attempt
        say("  new attempt with a new key:", r2.status_code, r2.json().get("state"))
        if r2.status_code == 202:
            say("  ->", poll(h, r2.headers["Location"], quiet=True)["state"])
    finally:
        sh("docker compose up -d external-sim")
        wait_health(f"{SIM}/health")


def simdown() -> None:
    h = login()
    b = book_now(h)
    say(httpx.post(f"{SIM}/admin/unavailable", params={"seconds": 20}).text)
    t0 = time.monotonic()
    r = open_barrier(h, b["id"])
    op = poll(h, r.headers["Location"], seconds=120)
    say(f"final after {time.monotonic() - t0:.1f} s")
    logs("worker", "2m", op["id"])
    summary(op["id"], b["id"])


def dpcdown() -> None:
    h = login()
    b = book_now(h)
    sh("docker compose stop api")
    t0 = time.monotonic()
    try:
        r = open_barrier(h, b["id"])
        op = poll(h, r.headers["Location"])
        time.sleep(max(0.0, 20 - (time.monotonic() - t0)))
        say("while DPC is stopped: booking status", web.get(f"/v1/bookings/{b['id']}", headers=h).json()["status"])
    finally:
        sh("docker compose start api")
    wait_health(f"{DPC}/health")
    say("booking status:", wait_entered(h, b["id"], 60), f"({time.monotonic() - t0:.1f} s after the stop)")
    logs("relay", "3m", op["id"])
    summary(op["id"], b["id"])


def api() -> None:
    h, other = login("user1"), login("user2")
    b = book_now(h, hours=1)
    key = str(uuid.uuid4())
    r1 = open_barrier(h, b["id"], key)
    r2 = open_barrier(h, b["id"], key)
    say("same operation:", r1.json()["id"] == r2.json()["id"], "| operations of the booking:",
        one("SELECT count(*) FROM operation WHERE booking_id = %s", b["id"]))
    r3 = open_barrier(h, b["id"])
    say("  another key while it runs:", r3.json()["type"].rsplit("/", 1)[-1])
    f = web.get(r1.headers["Location"], headers=other)
    m = web.get(f"/v1/operations/{uuid.uuid4()}", headers=other)
    say(f"user2 GET foreign operation -> {f.status_code} {f.json()['type'].rsplit('/', 1)[-1]}; "
        f"missing -> {m.status_code} {m.json()['type'].rsplit('/', 1)[-1]}")
    s = open_barrier(other, b["id"])
    say("  user2 opens the booking of user1:", s.json()["type"].rsplit("/", 1)[-1])
    poll(h, r1.headers["Location"], quiet=True)
    later = book_now(h, hours=1, start=datetime.now(UTC).replace(minute=0, second=0, microsecond=0) + timedelta(days=2))
    o = open_barrier(h, later["id"])
    say("  outside the interval:", o.json()["type"].rsplit("/", 1)[-1], "| operations:",
        one("SELECT count(*) FROM operation WHERE booking_id = %s", later["id"]))
    a = open_barrier(h, b["id"])
    say("  after it succeeded:", a.json()["type"].rsplit("/", 1)[-1])


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else ""
    fn = {"e2e": e2e, "publisher": publisher, "worker": worker, "rejected": rejected, "simdown": simdown,
          "dpcdown": dpcdown, "api": api}.get(what)
    if fn is None:
        sys.exit(__doc__)
    say(f"== web lab 4: {what}, {datetime.now(UTC).isoformat(timespec='seconds')}")
    try:
        fn()
    finally:
        leave_all()
