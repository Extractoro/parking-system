"""DPC lab 1: the rules of the garage on the demo data, with a booking made through the Web API.

Needs both parts: docker compose up -d nginx api. A booking of the current hour on level mall-1 is made
by user u-1 through Web (POST /v1/bookings via nginx); DPC reads it from the table booking (read only).
Then, in garage mall:
  1. entry with booking_id                       -> done
  2. second entry with the same booking_id       -> rejected already-entered (after the car left)
  3. exit of a car that is not inside            -> rejected not-inside
  4. the same event_id sent twice                -> the same Location, one stored request
  5. entry with booking_id and another plate     -> rejected booking-invalid
The quota case (a booked car enters a garage full by quota) needs a nearly full garage and is
checked by dpc/api/tests (test_booking_entry_passes_when_full_by_quota).

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/dpc_lr1_rules.py
"""

import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from dpc_common import body, client, db, post_until_accepted, wait_final

WEB = os.environ.get("WEB_BASE", "http://localhost:8080")


def show(step: str, r: dict) -> None:
    print(f"  {step:<44} {r['state']:<9} {r['reason'] or '':<16} {r['detail'] or ''}")


def main() -> None:
    plate = f"RL{uuid.uuid4().int % 10000:04d}AA"
    hour = datetime.now(ZoneInfo("Europe/Kyiv")).replace(minute=0, second=0, microsecond=0).isoformat()
    w = httpx.post(f"{WEB}/v1/bookings", timeout=10,
                   json={"level_id": "mall-1", "from_hour": hour, "hours": 1, "plate": plate},
                   headers={"X-User-Id": "u-1", "Idempotency-Key": str(uuid.uuid4())})
    w.raise_for_status()
    bid = w.json()["id"]
    print(f"Web: POST /v1/bookings -> {w.status_code}, booking {bid}, mall-1, {hour} + 1 h, plate {plate}")

    with client() as c:
        g0 = c.get("/v1/garage/mall").json()
        print(f"DPC garage mall before: occupied {g0['occupied']}, inside {g0['inside']}, reserved {g0['reserved']}")

        def go(step: str, b: dict) -> dict:
            r = post_until_accepted(c, b)
            assert r.status_code == 202, r.text
            res = wait_final(c, r.headers["Location"])
            show(step, res)
            return res

        e1 = body("mall", plate, "in") | {"booking_id": bid}
        assert go("1 entry with the booking", e1)["state"] == "done"
        g1 = c.get("/v1/garage/mall").json()
        print(f"    garage mall: occupied {g1['occupied']}, inside {g1['inside']}, reserved {g1['reserved']}, "
              f"invariant_ok {g1['invariant_ok']}")
        assert go("  exit of the same car", body("mall", plate, "out"))["state"] == "done"
        r2 = go("2 second entry by the same booking", body("mall", plate, "in") | {"booking_id": bid})
        assert r2["reason"] == "already-entered"
        r3 = go("3 exit of a car that is not inside", body("mall", f"NO{plate}", "out"))
        assert r3["reason"] == "not-inside"

        rep = body("mall", f"RP{plate}", "in")
        a = post_until_accepted(c, rep)
        b = post_until_accepted(c, rep)
        wait_final(c, a.headers["Location"])
        with db() as conn:
            n = conn.execute("SELECT count(*) AS n FROM entry_request WHERE event_id = %s", (rep["event_id"],)).fetchone()["n"]
        print(f"  4 same event_id twice: {a.status_code} {a.headers['Location']}")
        print(f"                         {b.status_code} {b.headers['Location']} Repeated-Request={b.headers.get('Repeated-Request')}"
              f"; stored requests with this event_id: {n}")
        assert a.headers["Location"] == b.headers["Location"] and n == 1
        assert go("  exit of that car", body("mall", f"RP{plate}", "out"))["state"] == "done"

        r5 = go("5 entry with the booking, another plate", body("mall", f"X{plate}", "in") | {"booking_id": bid})
        assert r5["reason"] == "booking-invalid"
        g2 = c.get("/v1/garage/mall").json()
        print(f"DPC garage mall after: occupied {g2['occupied']}, inside {g2['inside']}, reserved {g2['reserved']}, "
              f"invariant_ok {g2['invariant_ok']}")
        with db() as conn:
            row = conn.execute("SELECT status FROM booking WHERE id = %s", (bid,)).fetchone()
        print(f"booking row in Web table after the entry: status {row['status']} (DPC does not write it)")


if __name__ == "__main__":
    main()
