"""Web lab 1: the main operation (O1.4), validation, the cursor list (O1.5), access to one object.
Numbers in the names follow section 8 of docs/plans/stage-1-web-lr1.md."""

import threading
from datetime import UTC, datetime, timedelta

import httpx
from conftest import APP_A, APP_B, BASE, headers

def iso(day, hour: int, offset: str) -> str:
    return f"{day.isoformat()}T{hour:02d}:00:00{offset}"


def hours_of(api, level: str, day) -> dict[str, int]:
    r = api.get(f"/v1/levels/{level}/hours", params={"day": day.isoformat()})
    assert r.status_code == 200, r.text
    return {h["hour"]: h["free"] for h in r.json()["hours"]}


def local_offset(api, level: str, day) -> str:
    """UTC offset of the garage zone at noon of that day ('+02:00' in winter, '+03:00' in summer).
    Days when the clock changes are skipped by the `day` fixture, so one offset fits the whole day."""
    hours = api.get(f"/v1/levels/{level}/hours", params={"day": day.isoformat()}).json()["hours"]
    return hours[12]["hour"][-6:]


def book(api, level, from_hour, hours, user, plate="AA1234BB", base=None):
    body = {"level_id": level, "from_hour": from_hour, "hours": hours, "plate": plate}
    if base:
        return httpx.post(f"{base}/v1/bookings", json=body, headers=headers(user), timeout=10)
    return api.post("/v1/bookings", json=body, headers=headers(user))


def test_1_booking_three_hours(api, day, users):
    off = local_offset(api, "mall-1", day)
    before = hours_of(api, "mall-1", day)
    r = book(api, "mall-1", iso(day, 18, off), 3, users[0])
    assert r.status_code == 201, r.text
    b = r.json()
    assert r.headers["location"] == f"/v1/bookings/{b['id']}"
    assert isinstance(b["id"], str) and b["status"] == "confirmed"
    assert b["price_minor"] == 3 * b["tariff_minor_per_hour"] and b["currency"] == "UAH"
    after = hours_of(api, "mall-1", day)
    changed = {h for h in after if after[h] != before[h]}
    assert changed == {iso(day, 18, off), iso(day, 19, off), iso(day, 20, off)}
    assert all(after[h] == before[h] - 1 for h in changed)


def test_2_partial_busy_changes_nothing(api, day, users):
    off = local_offset(api, "mall-one", day)
    assert book(api, "mall-one", iso(day, 19, off), 1, users[0]).status_code == 201
    before = hours_of(api, "mall-one", day)
    r = book(api, "mall-one", iso(day, 18, off), 3, users[0])
    assert r.status_code == 409, r.text
    p = r.json()
    assert r.headers["content-type"].startswith("application/problem+json")
    assert p["type"].endswith("/no-free-spots") and p["status"] == 409
    assert hours_of(api, "mall-one", day) == before  # 18:00 and 20:00 are untouched (invariant 2)


def test_3_over_midnight_takes_two_dates(api, day, users):
    off = local_offset(api, "mall-2", day)
    next_day = day + timedelta(days=1)
    before_1, before_2 = hours_of(api, "mall-2", day), hours_of(api, "mall-2", next_day)
    r = book(api, "mall-2", iso(day, 23, off), 2, users[0])
    assert r.status_code == 201, r.text
    after_1, after_2 = hours_of(api, "mall-2", day), hours_of(api, "mall-2", next_day)
    h23 = iso(day, 23, off)
    h00 = next(h for h in after_2 if h.startswith(next_day.isoformat() + "T00:00"))
    assert after_1[h23] == before_1[h23] - 1
    assert after_2[h00] == before_2[h00] - 1
    assert sum(before_1.values()) - sum(after_1.values()) == 1
    assert sum(before_2.values()) - sum(after_2.values()) == 1


def test_4_validation(api, day, users):
    off = local_offset(api, "mall-1", day)
    good = {"level_id": "mall-1", "from_hour": iso(day, 10, off), "hours": 2, "plate": "AA1234BB"}
    bad_bodies = [
        {**good, "from_hour": f"{day.isoformat()}T10:30:00{off}"},  # not the start of an hour
        {**good, "from_hour": f"{day.isoformat()}T10:00:00"},  # no zone
        {**good, "from_hour": f"{day.isoformat()}T10:00:00+05:30"},  # 04:30 UTC: not an hour start (was 500)
        {**good, "from_hour": "9999-12-31T23:00:00+00:00"},  # out of range (was 500)
        {**good, "hours": 2.0},
        {**good, "plate": "A" * 13},
        {**good, "hours": 0},
        {**good, "hours": 25},
        {**good, "hours": "2"},
        {**good, "plate": ""},
        {**good, "plate": "   "},
        {k: v for k, v in good.items() if k != "level_id"},
    ]
    for body in bad_bodies:
        r = api.post("/v1/bookings", json=body, headers=headers(users[0]))
        assert r.status_code == 422, (body, r.text)
        assert r.json()["errors"], r.text

    r = api.post("/v1/bookings", content=b'{"level_id": "mall-1",', headers={**headers(users[0]), "Content-Type": "application/json"})
    assert r.status_code == 400, r.text

    r = api.post("/v1/bookings", json={**good, "level_id": "no-such-level"}, headers=headers(users[0]))
    assert r.status_code == 404, r.text

    r = api.post("/v1/bookings", json=good, headers={"Idempotency-Key": "test-no-user"})
    assert r.status_code == 401, r.text

    assert api.get("/v1/levels/mall-1/hours", params={"day": "2026-13-01"}).status_code == 422
    assert api.get("/v1/levels/mall-1/hours", params={"day": "0001-01-01"}).status_code == 422  # was 500
    assert api.get("/v1/levels/mall-1/hours").status_code == 422
    assert api.get("/v1/levels/no-such-level/hours", params={"day": "2026-11-20"}).status_code == 404


def test_5_cursor_pages(api, db, day, fresh_user):
    off = local_offset(api, "mall-1", day)
    ids = []
    for h in range(7):
        r = book(api, "mall-1", iso(day, h, off), 1, user=fresh_user)
        assert r.status_code == 201, r.text
        ids.append(r.json()["id"])
    # three of them with the same created_at: the cursor must neither lose nor repeat them
    same = datetime(2030, 1, 1, tzinfo=UTC)
    db.execute("UPDATE booking SET created_at = %s WHERE id = ANY(%s)", (same, ids[2:5]))

    seen, pages, cursor = [], 0, None
    while True:
        params = {"limit": 3} | ({"cursor": cursor} if cursor else {})
        r = api.get("/v1/bookings", params=params, headers={"X-User-Id": fresh_user})
        assert r.status_code == 200, r.text
        page = r.json()
        pages += 1
        seen += [b["id"] for b in page["items"]]
        cursor = page["next_cursor"]
        if cursor is None:
            break
    assert pages == 3
    assert sorted(seen) == sorted(ids) and len(seen) == len(set(seen))

    created = {b: db.execute("SELECT created_at FROM booking WHERE id = %s", (b,)).fetchone()["created_at"] for b in seen}
    keys = [(created[b], b) for b in seen]
    assert keys == sorted(keys, reverse=True)  # newest first, then by id

    for bad in ("not-base64!!", "eyJ0IjoiIn0", "e30"):
        r = api.get("/v1/bookings", params={"cursor": bad}, headers={"X-User-Id": fresh_user})
        assert r.status_code == 400, (bad, r.text)
    for limit in (0, 51, "x"):
        r = api.get("/v1/bookings", params={"limit": limit}, headers={"X-User-Id": fresh_user})
        assert r.status_code == 422, (limit, r.text)


def test_6_foreign_booking_is_404(api, day, users):
    off = local_offset(api, "center-1", day)
    r = book(api, "center-1", iso(day, 12, off), 1, users[0])
    assert r.status_code == 201
    booking_id = r.json()["id"]
    assert api.get(f"/v1/bookings/{booking_id}", headers={"X-User-Id": users[0]}).status_code == 200
    foreign = api.get(f"/v1/bookings/{booking_id}", headers={"X-User-Id": users[1]})
    missing = api.get("/v1/bookings/no-such-booking", headers={"X-User-Id": users[1]})
    assert foreign.status_code == missing.status_code == 404
    assert foreign.json()["type"] == missing.json()["type"]


def test_7_race_last_spot_two_instances(api, day, users):
    off = local_offset(api, "mall-one", day)
    start = iso(day, 8, off)
    results: list[int] = []
    barrier = threading.Barrier(20)

    def one(i: int) -> None:
        barrier.wait()
        r = book(api, "mall-one", start, 2, user=users[i % 2], base=APP_A if i % 2 else APP_B)
        results.append(r.status_code)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == [201] + [409] * 19, results
    free = hours_of(api, "mall-one", day)
    assert free[iso(day, 8, off)] == 0 and free[iso(day, 9, off)] == 0


def test_8_past_start_is_rejected_current_hour_allowed(api, users):
    """PC-1: a booking cannot start in an hour that has already ended; the running hour is allowed."""
    now = datetime.now(UTC)
    current = now.replace(minute=0, second=0, microsecond=0)
    past = current - timedelta(hours=1)

    r = book(api, "mall-1", past.isoformat(), 1, users[1])
    assert r.status_code == 422, r.text
    assert any(e["field"] == "body.from_hour" for e in r.json()["errors"]), r.text

    r = book(api, "mall-1", (current - timedelta(days=1)).isoformat(), 2, users[1])
    assert r.status_code == 422, r.text

    r = book(api, "center-1", current.isoformat(), 1, users[1])
    assert r.status_code in (201, 409), r.text  # 409 only if the real current hour is full


def test_9_closed_level_takes_no_new_bookings(api, db, day, users):
    """Decision B8/M8: a closed level answers 409 level-closed; nothing is reserved."""
    off = local_offset(api, "center-1", day)
    before = hours_of(api, "center-1", day)
    db.execute("UPDATE level SET closed = true WHERE id = 'center-1'")
    try:
        r = book(api, "center-1", iso(day, 10, off), 2, users[0])
        assert r.status_code == 409, r.text
        assert r.json()["type"].endswith("/level-closed")
        assert hours_of(api, "center-1", day) == before
    finally:
        db.execute("UPDATE level SET closed = false WHERE id = 'center-1'")


def test_health_and_request_id():
    for base in (BASE, APP_A, APP_B):
        r = httpx.get(f"{base}/health", headers={"X-Request-ID": "lr1-test-req-1"})
        assert r.status_code == 200 and r.json()["status"] == "ok"
        assert r.headers["x-request-id"] == "lr1-test-req-1"
