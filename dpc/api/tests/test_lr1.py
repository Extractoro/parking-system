"""DPC lab 1: receiver, bounded queue, worker pool, rules of the garage, invariant, time marks."""

import random
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from conftest import BASE, TOKEN, entry, make_booking, make_garage, now_iso, run, wait_final


def test_accept_202_and_final_state(api, garage):
    r = entry(api, garage, "AA 0001 BB")
    assert r.status_code == 202
    assert r.headers["Location"] == f"/v1/entries/{r.json()['id']}"
    assert r.json()["state"] in ("queued", "processing")
    done = wait_final(api, r.headers["Location"])
    assert done["state"] == "done" and done["plate"] == "AA0001BB"
    g = api.get(f"/v1/garage/{garage}").json()
    assert (g["occupied"], g["inside"], g["invariant_ok"]) == (1, 1, True)


def test_validation_and_auth(api, garage):
    bad = {"event_id": "x", "garage_id": garage, "plate": "A", "direction": "up", "event_time": "2027-06-15 15:00"}
    r = api.post("/v1/entries", json=bad)
    assert r.status_code == 400
    assert r.headers["content-type"].startswith("application/problem+json")
    fields = {e["field"] for e in r.json()["errors"]}
    assert {"body.plate", "body.direction", "body.event_time"} <= fields
    assert httpx.get(f"{BASE}/v1/garage/{garage}").status_code == 401
    assert entry(api, "no-such-garage", "AA0001BB").status_code == 404


def test_full_queue_429_not_stored(api, db):
    """100 POSTs at once into a queue of 50 with 4 workers of 100-500 ms: some get 429 + Retry-After: 1
    and are not stored; the accepted ones all end."""
    gid = make_garage(db, 500)
    ids = [f"q-{uuid.uuid4().hex}" for _ in range(100)]
    barrier = threading.Barrier(len(ids))

    def send(event_id: str) -> httpx.Response:
        with httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
            barrier.wait()
            return entry(c, gid, f"Q{event_id[-8:]}", event_id=event_id)

    with ThreadPoolExecutor(len(ids)) as pool:
        answers = list(pool.map(send, ids))
    statuses = [a.status_code for a in answers]
    assert set(statuses) <= {202, 429}
    assert statuses.count(429) > 0 and statuses.count(202) >= 50
    for a, eid in zip(answers, ids):
        if a.status_code == 429:
            assert a.headers["Retry-After"] == "1"
            assert a.json()["type"].endswith("/queue-full")
    refused = [eid for a, eid in zip(answers, ids) if a.status_code == 429]
    stored = db.execute("SELECT count(*) AS n FROM entry_request WHERE event_id = ANY(%s)", (refused,)).fetchone()
    assert stored["n"] == 0
    for a in answers:
        if a.status_code == 202:
            assert wait_final(api, a.headers["Location"])["state"] == "done"


def test_walk_in_rejected_when_full(api, db):
    gid = make_garage(db, 1)
    assert run(api, gid, "FULL0001")["state"] == "done"
    r = run(api, gid, "FULL0002")
    assert (r["state"], r["reason"]) == ("rejected", "full")
    assert api.get(f"/v1/garage/{gid}").json()["occupied"] == 1


def test_booking_entry_passes_when_full_by_quota(api, garage, db):
    """2 spots, one unused booking of this hour: one walk-in fills the quota (1 + 1 reserved = 2),
    the second walk-in is rejected, the car with the booking still enters (decision B4)."""
    bid = make_booking(db, garage, "BK 0001 AA")
    assert api.get(f"/v1/garage/{garage}").json()["reserved"] == 1
    assert run(api, garage, "WALK0001")["state"] == "done"
    second = run(api, garage, "WALK0002")
    assert (second["state"], second["reason"]) == ("rejected", "full")
    booked = run(api, garage, "BK0001AA", booking_id=bid)
    assert booked["state"] == "done", booked
    g = api.get(f"/v1/garage/{garage}").json()
    assert (g["occupied"], g["inside"], g["reserved"], g["invariant_ok"]) == (2, 2, 0, True)


def test_second_entry_by_same_booking(api, garage, db):
    bid = make_booking(db, garage, "BK0002AA")
    assert run(api, garage, "BK0002AA", booking_id=bid)["state"] == "done"
    assert run(api, garage, "BK0002AA", "out")["state"] == "done"
    again = run(api, garage, "BK0002AA", booking_id=bid)
    assert (again["state"], again["reason"]) == ("rejected", "already-entered")


def test_booking_checks(api, garage, db):
    other = make_garage(db, 5)
    past = make_booking(db, garage, "BK0003AA", hours_back=2)
    cancelled = make_booking(db, garage, "BK0004AA", status="cancelled")
    ok = make_booking(db, garage, "BK0005AA")
    cases = [
        ("BK0003AA", past, garage),          # event_time outside the interval
        ("BK0004AA", cancelled, garage),     # not confirmed
        ("ZZ0000ZZ", ok, garage),            # another plate
        ("BK0005AA", ok, other),             # another garage
        ("BK0005AA", str(uuid.uuid4()), garage),  # no such booking
    ]
    for plate, bid, gid in cases:
        r = run(api, gid, plate, booking_id=bid)
        assert (r["state"], r["reason"]) == ("rejected", "booking-invalid"), (plate, r)


def test_exit_without_entry(api, garage):
    r = run(api, garage, "OUT00001", "out")
    assert (r["state"], r["reason"]) == ("rejected", "not-inside")
    g = api.get(f"/v1/garage/{garage}").json()
    assert (g["occupied"], g["invariant_ok"]) == (0, True)


def test_same_event_id_is_the_same_request(api, garage, db):
    eid = f"rep-{uuid.uuid4().hex}"
    t = now_iso()
    first = entry(api, garage, "REP00001", event_id=eid, event_time=t)
    second = entry(api, garage, "REP 0000 1", event_id=eid, event_time=t)  # the same plate, other spelling
    assert first.status_code == second.status_code == 202
    assert first.headers["Location"] == second.headers["Location"]
    wait_final(api, first.headers["Location"])
    n = db.execute("SELECT count(*) AS n FROM entry_request WHERE event_id = %s", (eid,)).fetchone()["n"]
    assert n == 1
    assert api.get(f"/v1/garage/{garage}").json()["occupied"] == 1
    other = entry(api, garage, "REP00002", event_id=eid, event_time=t)
    assert other.status_code == 409 and other.json()["type"].endswith("/event-id-reused")


def test_time_marks_order(api, garage):
    r = entry(api, garage, "TIME0001")
    early = api.get(r.headers["Location"]).json()
    assert early["delivery_wait_ms"] is None  # no delivery mark before the final result is read
    done = wait_final(api, r.headers["Location"])
    m = done["marks_ms"]
    assert 0 <= m["started"] <= m["finished"] <= m["delivered"]
    assert 100 <= done["processing_ms"] < 1500  # recognition 100-500 ms + the transaction
    again = api.get(r.headers["Location"]).json()
    assert again["marks_ms"]["delivered"] == m["delivered"]  # only the FIRST read sets it


def test_metrics(api):
    m = httpx.get(f"{BASE}/metrics").json()
    for key in ("queue_length", "in_flight", "rejected", "rejected_rule", "workers"):
        assert isinstance(m[key], int)
    assert m["workers"] == 4
    assert 0 <= m["in_flight"] <= m["workers"]
    prom = httpx.get(f"{BASE}/metrics/prom").text
    plain = [line.split(" ")[0] for line in prom.splitlines() if "{" not in line]
    assert len(plain) == len(set(plain))  # no duplicated series (in_flight of HTTP is http_in_flight)
    assert "queue_length" in plain and "in_flight" in plain


def test_invariant_after_200_parallel_requests(api, db):
    """200 entries and exits of 20 cars in a garage of 5 spots, 20 parallel senders: the garage
    stays consistent (occupied = inside <= spots)."""
    gid = make_garage(db, 5)
    plates = [f"PAR{n:05d}" for n in range(20)]
    rnd = random.Random(7)

    def one(i: int) -> str:
        with httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
            return run(c, gid, rnd.choice(plates), rnd.choice(["in", "out"]))["state"]

    with ThreadPoolExecutor(20) as pool:
        states = list(pool.map(one, range(200)))
    assert len(states) == 200 and "failed" not in states
    g = api.get(f"/v1/garage/{gid}").json()
    assert g["invariant_ok"] is True and g["occupied"] == g["inside"] <= 5
    counts = db.execute(
        "SELECT state, count(*) AS n FROM entry_request WHERE garage_id = %s GROUP BY state", (gid,)
    ).fetchall()
    assert sum(c["n"] for c in counts) == 200 and {c["state"] for c in counts} <= {"done", "rejected"}


def test_health_without_token():
    r = httpx.get(f"{BASE}/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"
