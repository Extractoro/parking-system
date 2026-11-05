"""Web lab 2: idempotency keys, the plate change with If-Match, the repeated cancellation.
Against the running stand (docker compose up -d nginx publisher), like test_lr1.py."""

import shutil
import subprocess
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from conftest import APP_A, APP_B, BASE
from test_lr1 import hours_of, iso, local_offset

ROOT = Path(__file__).resolve().parents[3]


def post(body, user, key, base=BASE):
    h = {"X-User-Id": user}
    if key is not None:
        h["Idempotency-Key"] = key
    return httpx.post(f"{base}/v1/bookings", json=body, headers=h, timeout=10)


def body_of(day, off, level="mall-1", hour=10, hours=2, plate="AA1234BB"):
    return {"level_id": level, "from_hour": iso(day, hour, off), "hours": hours, "plate": plate}


def bookings_of(db, user) -> int:
    return db.execute("SELECT count(*) AS n FROM booking WHERE user_id = %s", (user,)).fetchone()["n"]


def slug(r) -> str:
    return r.json()["type"].rsplit("/", 1)[-1]


# ---------- idempotency of POST /v1/bookings ----------


def test_1_same_key_same_body_on_both_instances(api, db, day, fresh_user):
    off = local_offset(api, "mall-1", day)
    body, key = body_of(day, off), f"k-{uuid.uuid4()}"
    before = hours_of(api, "mall-1", day)

    first = post(body, fresh_user, key, APP_A)
    again = post(body, fresh_user, key, APP_B)
    assert first.status_code == again.status_code == 201, (first.text, again.text)
    assert first.json() == again.json()
    assert first.headers["location"] == again.headers["location"]
    assert again.headers["idempotent-replayed"] == "true" and "idempotent-replayed" not in first.headers
    assert again.headers["x-instance"] == "app-b"

    assert bookings_of(db, fresh_user) == 1
    after = hours_of(api, "mall-1", day)
    assert sum(before.values()) - sum(after.values()) == 2  # two hours, taken once


def test_2_same_key_other_body_is_422(api, db, day, fresh_user):
    off = local_offset(api, "mall-1", day)
    key = f"k-{uuid.uuid4()}"
    assert post(body_of(day, off), fresh_user, key).status_code == 201
    r = post(body_of(day, off, plate="BB0000BB"), fresh_user, key)
    assert r.status_code == 422 and slug(r) == "idempotency-key-reused", r.text
    assert bookings_of(db, fresh_user) == 1


def test_3_key_header_required_and_bounded(api, db, day, fresh_user):
    off = local_offset(api, "mall-1", day)
    body = body_of(day, off)
    r = post(body, fresh_user, None)
    assert r.status_code == 400 and slug(r) == "idempotency-key-required", r.text
    for bad in ("", "x" * 201):
        r = post(body, fresh_user, bad)
        assert r.status_code == 422, (len(bad), r.text)
        assert r.json()["errors"][0]["field"] == "header.Idempotency-Key"
    assert bookings_of(db, fresh_user) == 0
    assert post(body, fresh_user, "x" * 200).status_code == 201
    assert post(body_of(day, off, hour=14), fresh_user, "1").status_code == 201


def test_4_key_belongs_to_its_user(api, db, day, fresh_user, users):
    """The same key of two users is two different keys: (user, operation, key)."""
    off = local_offset(api, "mall-1", day)
    key = f"k-{uuid.uuid4()}"
    a = post(body_of(day, off), fresh_user, key)
    b = post(body_of(day, off), users[1], key)
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]


def test_5_concurrent_requests_with_one_key(api, db, day, fresh_user):
    """Requests with one key at the same time: one does the work, others get 409 in-progress
    or the stored 201; one booking in the end."""
    off = local_offset(api, "mall-2", day)
    body, key = body_of(day, off, level="mall-2", hour=7, hours=3), f"k-{uuid.uuid4()}"
    n = 10
    barrier = threading.Barrier(n)
    results: list[httpx.Response] = []

    def one(i: int) -> None:
        barrier.wait()
        results.append(post(body, fresh_user, key, APP_A if i % 2 else APP_B))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    statuses = sorted(r.status_code for r in results)
    assert set(statuses) <= {201, 409}, statuses
    assert 201 in statuses
    for r in results:
        if r.status_code == 409:
            assert slug(r) == "idempotency-in-progress" and r.headers["retry-after"] == "1", r.text
    assert len({r.json()["id"] for r in results if r.status_code == 201}) == 1
    assert bookings_of(db, fresh_user) == 1

    final = post(body, fresh_user, key)  # the retry the client makes after 409
    assert final.status_code == 201 and final.headers["idempotent-replayed"] == "true"
    assert bookings_of(db, fresh_user) == 1


def test_6_domain_refusal_is_stored_validation_is_not(api, db, day, fresh_user, users):
    off = local_offset(api, "mall-one", day)
    assert post(body_of(day, off, level="mall-one", hour=12, hours=1), users[0], f"k-{uuid.uuid4()}").status_code == 201

    key = f"k-{uuid.uuid4()}"
    busy = body_of(day, off, level="mall-one", hour=12, hours=1)
    r1 = post(busy, fresh_user, key)
    r2 = post(busy, fresh_user, key)
    assert r1.status_code == r2.status_code == 409 and slug(r1) == "no-free-spots", r1.text
    assert r2.json() == r1.json() and r2.headers["idempotent-replayed"] == "true"
    assert r2.headers["content-type"].startswith("application/problem+json")

    # 422 of the time rule is not a result of the operation: the key stays free for the corrected request
    key = f"k-{uuid.uuid4()}"
    past = {"level_id": "mall-1", "from_hour": "2020-01-01T10:00:00+02:00", "hours": 1, "plate": "AA1234BB"}
    assert post(past, fresh_user, key).status_code == 422
    assert post(past, fresh_user, key).status_code == 422
    assert db.execute("SELECT count(*) AS n FROM idempotency_key WHERE key = %s", (key,)).fetchone()["n"] == 0


# ---------- PATCH /v1/bookings/{id} with If-Match ----------


def test_7_plate_change_with_if_match(api, db, day, fresh_user, users):
    off = local_offset(api, "center-1", day)
    b = post(body_of(day, off, level="center-1", hour=9), fresh_user, f"k-{uuid.uuid4()}").json()
    url = f"/v1/bookings/{b['id']}"
    me = {"X-User-Id": fresh_user}

    got = api.get(url, headers=me)
    assert got.status_code == 200 and got.headers["etag"] == '"1"'

    r = api.patch(url, json={"plate": "AT0001AA"}, headers=me)
    assert r.status_code == 428 and slug(r) == "precondition-required", r.text

    r = api.patch(url, json={"plate": "AT0001AA"}, headers={**me, "If-Match": got.headers["etag"]})
    assert r.status_code == 200, r.text
    assert r.json()["plate"] == "AT0001AA" and r.json()["version"] == 2 and r.headers["etag"] == '"2"'

    stale = api.patch(url, json={"plate": "AT0002AA"}, headers={**me, "If-Match": '"1"'})
    assert stale.status_code == 412 and slug(stale) == "version-mismatch", stale.text
    assert stale.headers["etag"] == '"2"'
    assert api.patch(url, json={"plate": "AT0002AA"}, headers={**me, "If-Match": "garbage"}).status_code == 412

    for forbidden in ({"status": "cancelled"}, {"version": 7}, {"user_id": users[1]}, {"plate": "AT0003AA", "level_id": "mall-2"},
                      {"plate": "AT0003AA", "from_hour": iso(day, 11, off)}, {"plate": "AT0003AA", "hours": 5}, {"plate": "A"}):
        r = api.patch(url, json=forbidden, headers={**me, "If-Match": '"2"'})
        assert r.status_code == 422, (forbidden, r.text)

    foreign = api.patch(url, json={"plate": "AT0004AA"}, headers={"X-User-Id": users[1], "If-Match": '"2"'})
    assert foreign.status_code == 404
    row = db.execute("SELECT plate, version, status FROM booking WHERE id = %s", (b["id"],)).fetchone()
    assert row == {"plate": "AT0001AA", "version": 2, "status": "confirmed"}

    assert api.post(f"{url}/cancel", headers=me).status_code == 200
    r = api.patch(url, json={"plate": "AT0005AA"}, headers={**me, "If-Match": '"3"'})
    assert r.status_code == 409 and slug(r) == "booking-not-modifiable", r.text


# ---------- POST /v1/bookings/{id}/cancel ----------


def _three_hours(api, day, user, level="mall-2", hour=15):
    off = local_offset(api, level, day)
    r = post(body_of(day, off, level=level, hour=hour, hours=3), user, f"k-{uuid.uuid4()}")
    assert r.status_code == 201, r.text
    return r.json(), [iso(day, hour + i, off) for i in range(3)]


def test_8_cancel_twice_frees_once(api, day, fresh_user, users):
    b, hrs = _three_hours(api, day, fresh_user)
    taken = hours_of(api, "mall-2", day)
    me = {"X-User-Id": fresh_user}

    assert api.post(f"/v1/bookings/{b['id']}/cancel", headers={"X-User-Id": users[1]}).status_code == 404
    first = api.post(f"/v1/bookings/{b['id']}/cancel", headers=me)
    second = api.post(f"/v1/bookings/{b['id']}/cancel", headers=me)
    assert first.status_code == second.status_code == 200
    assert first.json()["status"] == second.json()["status"] == "cancelled"
    assert first.json()["version"] == second.json()["version"] == 2

    freed = hours_of(api, "mall-2", day)
    assert all(freed[h] == taken[h] + 1 for h in hrs)
    assert sum(freed.values()) - sum(taken.values()) == 3


def test_9_concurrent_cancels_free_once(api, day, fresh_user):
    b, hrs = _three_hours(api, day, fresh_user, level="mall-1", hour=19)
    taken = hours_of(api, "mall-1", day)
    n = 6
    barrier = threading.Barrier(n)
    statuses: list[int] = []

    def one(i: int) -> None:
        barrier.wait()
        r = httpx.post(f"{APP_A if i % 2 else APP_B}/v1/bookings/{b['id']}/cancel", headers={"X-User-Id": fresh_user}, timeout=10)
        statuses.append(r.status_code)

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert statuses == [200] * n
    freed = hours_of(api, "mall-1", day)
    assert all(freed[h] == taken[h] + 1 for h in hrs)
    assert sum(freed.values()) - sum(taken.values()) == 3


def test_10_started_booking_is_not_cancelled(api, fresh_user):
    current = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    body = {"level_id": "center-1", "from_hour": current.isoformat(), "hours": 1, "plate": "AA1234BB"}
    r = post(body, fresh_user, f"k-{uuid.uuid4()}")
    if r.status_code == 409:
        pytest.skip("the real current hour of center-1 is full")
    assert r.status_code == 201, r.text
    c = api.post(f"/v1/bookings/{r.json()['id']}/cancel", headers={"X-User-Id": fresh_user})
    assert c.status_code == 409 and slug(c) == "booking-started", c.text


# ---------- cleanup of expired keys (publisher) ----------


def test_11_cleanup_deletes_expired_keys_only(db, fresh_user):
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    expired, fresh = f"old-{uuid.uuid4()}", f"new-{uuid.uuid4()}"
    for key, created, expires in ((expired, "now() - interval '2 days'", "now() - interval '1 day'"),
                                  (fresh, "now()", "now() + interval '1 day'")):
        db.execute(
            f"""INSERT INTO idempotency_key (user_id, operation, key, request_hash, state, response_status,
                                             response_body, created_at, expires_at)
                VALUES (%s, 'create-booking', %s, repeat('0', 64), 'completed', 201, '{{}}', {created}, {expires})""",
            (fresh_user, key),
        )
    run = subprocess.run(
        ["docker", "compose", "exec", "-T", "publisher", "python", "-m", "app.publisher", "--cleanup-once"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert '"idempotency.cleanup"' in run.stdout
    left = {r["key"] for r in db.execute("SELECT key FROM idempotency_key WHERE user_id = %s", (fresh_user,))}
    assert left == {fresh}
