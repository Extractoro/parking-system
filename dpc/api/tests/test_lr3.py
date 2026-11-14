"""DPC lab 3: the effect receiver POST /internal/effects (O3.3) and the Ray execution (O3.1).

The receiver tests call /internal/effects directly with a request row written by the test (as a Ray task
would deliver it), so they do not depend on EXECUTION. The last test sends a request through
POST /v1/entries in the mode of the stand and repeats its effect.
"""

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import httpx
from conftest import BASE, TOKEN, make_booking, make_garage, now_iso, run


def request_row(db, garage_id: str, plate: str, direction: str = "in", booking_id: str | None = None) -> dict:
    """An accepted request as POST /v1/entries stores it, in state processing (its task is running)."""
    rid, eid = str(uuid.uuid4()), f"fx-{uuid.uuid4().hex}"
    t = now_iso()
    db.execute(
        """INSERT INTO entry_request (id, event_id, garage_id, plate, direction, event_time, booking_id, state, started_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, 'processing', now())""",
        (rid, eid, garage_id, plate, direction, t, booking_id),
    )
    return {"effect_key": eid, "request_id": rid, "garage_id": garage_id, "plate": plate, "direction": direction,
            "event_time": t, "booking_id": booking_id}


def deliver(api, effect: dict) -> dict:
    r = api.post("/internal/effects", json=effect)
    assert r.status_code == 200, r.text
    return r.json()


def garage_row(db, gid: str) -> dict:
    return db.execute(
        """SELECT gs.occupied,
                  (SELECT count(*) FROM entry WHERE garage_id = %(g)s) AS entries,
                  (SELECT count(*) FROM effect f JOIN entry_request r ON r.id = f.request_id
                   WHERE r.garage_id = %(g)s) AS effects
           FROM garage_state gs WHERE gs.garage_id = %(g)s""",
        {"g": gid},
    ).fetchone()


def test_repeat_returns_stored_decision(api, db):
    gid = make_garage(db, 3)
    e = request_row(db, gid, "RCV0001")
    first = deliver(api, e)
    assert (first["decision"], first["repeated"]) == ("accepted", False)
    again = [deliver(api, e) for _ in range(3)]
    assert all((a["decision"], a["reason"], a["repeated"]) == ("accepted", None, True) for a in again)
    assert garage_row(db, gid) == {"occupied": 1, "entries": 1, "effects": 1}
    state = db.execute("SELECT state FROM entry_request WHERE id = %s", (e["request_id"],)).fetchone()["state"]
    assert state == "done"
    assert api.get(f"/v1/garage/{gid}").json()["invariant_ok"] is True


def test_rejected_decision_is_stored(api, db):
    """A full garage: the decision rejected/full is stored with the key. After a spot is freed a repeat
    of the same effect still gets rejected: the stored decision, not a new check."""
    gid = make_garage(db, 1)
    assert deliver(api, request_row(db, gid, "RCV0010"))["decision"] == "accepted"
    e = request_row(db, gid, "RCV0011")
    first = deliver(api, e)
    assert (first["decision"], first["reason"], first["repeated"]) == ("rejected", "full", False)
    row = db.execute("SELECT decision, reason FROM effect WHERE effect_key = %s", (e["effect_key"],)).fetchone()
    assert row == {"decision": "rejected", "reason": "full"}
    assert deliver(api, request_row(db, gid, "RCV0010", "out"))["decision"] == "accepted"  # a spot is free now
    again = deliver(api, e)
    assert (again["decision"], again["reason"], again["repeated"]) == ("rejected", "full", True)
    assert garage_row(db, gid) == {"occupied": 0, "entries": 2, "effects": 3}


def test_concurrent_deliveries_one_effect(api, db):
    """Eight deliveries of the same key at once: one changes the garage, seven get the stored decision."""
    gid = make_garage(db, 5)
    e = request_row(db, gid, "RCV0020")
    barrier = threading.Barrier(8)

    def send(_: int) -> dict:
        with httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
            barrier.wait()
            return deliver(c, e)

    with ThreadPoolExecutor(8) as pool:
        answers = list(pool.map(send, range(8)))
    assert sorted(a["repeated"] for a in answers) == [False] + [True] * 7
    assert {a["decision"] for a in answers} == {"accepted"}
    assert garage_row(db, gid) == {"occupied": 1, "entries": 1, "effects": 1}


def test_receiver_applies_the_rule_of_lab_1(api, db, garage):
    """The receiver uses the same rule as the worker pool of lab 1 (one function `admit`): the cases of
    the lab 1 tests give the same decisions when delivered as effects."""
    # garage: 2 spots, a level of 2 spots; one booking of the current hour holds a spot
    bid = make_booking(db, garage, "BKD0001")
    assert deliver(api, request_row(db, garage, "WLK0001"))["decision"] == "accepted"  # 0 + reserved 1 < 2
    walk = deliver(api, request_row(db, garage, "WLK0002"))
    assert (walk["decision"], walk["reason"]) == ("rejected", "full")  # occupied 1 + reserved 1 of 2
    by_booking = deliver(api, request_row(db, garage, "BKD 0001", booking_id=bid))
    assert by_booking["decision"] == "accepted"  # the booking holds its spot, no quota check (B4)
    twice = deliver(api, request_row(db, garage, "BKD0001", booking_id=bid))
    assert (twice["decision"], twice["reason"]) == ("rejected", "already-inside")
    out = deliver(api, request_row(db, garage, "NOPE0001", "out"))
    assert (out["decision"], out["reason"]) == ("rejected", "not-inside")
    g = api.get(f"/v1/garage/{garage}").json()
    assert (g["occupied"], g["inside"], g["invariant_ok"]) == (2, 2, True)


def test_receiver_checks_key_and_token(api, db):
    gid = make_garage(db, 2)
    e = request_row(db, gid, "RCV0030")
    assert api.post("/internal/effects", json={**e, "effect_key": "another"}).status_code == 409
    assert api.post("/internal/effects", json={**e, "request_id": str(uuid.uuid4())}).status_code == 404
    assert httpx.post(f"{BASE}/internal/effects", json=e).status_code == 401
    assert api.post("/internal/effects", json={**e, "direction": "up"}).status_code == 400
    assert garage_row(db, gid) == {"occupied": 0, "entries": 0, "effects": 0}


def test_request_through_the_stand_and_repeat(api, db):
    """POST /v1/entries in the mode of the stand; in EXECUTION=ray the Ray task stored the effect, and a
    repeated delivery of it changes nothing."""
    gid = make_garage(db, 2)
    t = now_iso()
    done = run(api, gid, "STD0001", event_time=t)
    assert done["state"] == "done"
    m = httpx.get(f"{BASE}/metrics").json()
    if m["execution"] != "ray":
        return
    row = db.execute("SELECT decision FROM effect WHERE request_id = %s", (done["id"],)).fetchone()
    assert row == {"decision": "accepted"}
    effect = {"effect_key": done["event_id"], "request_id": done["id"], "garage_id": gid, "plate": "STD0001",
              "direction": "in", "event_time": t, "booking_id": None}
    again = deliver(api, effect)
    assert (again["decision"], again["repeated"]) == ("accepted", True)
    assert garage_row(db, gid) == {"occupied": 1, "entries": 1, "effects": 1}
    assert 0 <= m["in_flight"] <= m["in_flight_limit"]

