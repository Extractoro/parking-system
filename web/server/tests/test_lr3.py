"""Web lab 3: login with an opaque token, access to objects, the staff function, CORS.
Against the running stand (docker compose up -d nginx publisher), like test_lr1.py."""

import hashlib
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import httpx
import pytest
from conftest import APP_A, APP_B, BASE, TEST_PASSWORD, auth, headers, login, make_user
from test_lr1 import book, iso, local_offset

ROOT = Path(__file__).resolve().parents[3]
CLIENT_ORIGIN = os.environ.get("WEB_CLIENT_ORIGIN", "http://localhost:5173")
EVIL_ORIGIN = "http://localhost:8082"


def slug(r) -> str:
    return r.json()["type"].rsplit("/", 1)[-1]


def expire(db, token: str) -> None:
    db.execute(
        "UPDATE session SET created_at = now() - interval '9 hours', expires_at = now() - interval '1 minute'"
        " WHERE token_hash = %s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )


# ---------- O3.1 login, logout, expiry ----------


def test_1_login_on_app_a_token_works_on_app_b(db):
    user = make_user(db)
    r = login(user.login, TEST_PASSWORD, APP_A)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "Bearer" and len(body["access_token"]) == 43  # 32 bytes, base64url
    assert body["user"] == {"id": user, "login": user.login, "name": user.login, "role": "user"}
    # only the hash is stored
    row = db.execute("SELECT token_hash FROM session WHERE user_id = %s ORDER BY created_at DESC LIMIT 1",
                     (user,)).fetchone()
    assert row["token_hash"] == hashlib.sha256(body["access_token"].encode()).hexdigest()

    me = httpx.get(f"{APP_B}/v1/me", headers={"Authorization": f"Bearer {body['access_token']}"})
    assert me.status_code == 200 and me.headers["x-instance"] == "app-b"
    assert me.json()["id"] == user and me.json()["role"] == "user"


def test_2_wrong_password_and_unknown_login_look_the_same(db):
    user = make_user(db)
    wrong = login(user.login, "not-the-password")
    unknown = login(f"nobody-{uuid.uuid4().hex[:8]}", TEST_PASSWORD)
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()
    assert slug(wrong) == "invalid-credentials"
    # a user without a password (default '!') cannot log in at all
    uid = f"t-{uuid.uuid4().hex[:12]}"
    db.execute("INSERT INTO app_user (id, login, role) VALUES (%s, %s, 'user')", (uid, uid))
    assert login(uid, "!").status_code == 401
    # invalid body is not a login attempt
    assert httpx.post(f"{BASE}/v1/auth/login", json={"login": "x"}).status_code == 422


def test_3_logout_ends_the_session_on_both_instances(db):
    user = make_user(db)
    assert httpx.get(f"{APP_A}/v1/me", headers=auth(user)).status_code == 200
    r = httpx.post(f"{APP_A}/v1/auth/logout", headers=auth(user))
    assert r.status_code == 204
    for base in (APP_A, APP_B):
        old = httpx.get(f"{base}/v1/me", headers=auth(user))
        assert old.status_code == 401 and old.headers["www-authenticate"].startswith("Bearer"), old.text
    # a repeat is safe; logout without any token is 401
    assert httpx.post(f"{APP_B}/v1/auth/logout", headers=auth(user)).status_code == 204
    assert httpx.post(f"{BASE}/v1/auth/logout").status_code == 401


def test_4_expired_session_is_401(db):
    user = make_user(db)
    assert httpx.get(f"{BASE}/v1/me", headers=auth(user)).status_code == 200
    expire(db, user.token)
    r = httpx.get(f"{BASE}/v1/me", headers=auth(user))
    assert r.status_code == 401 and 'error="invalid_token"' in r.headers["www-authenticate"], r.text


def test_5_x_user_id_is_not_accepted(api, day, users):
    off = local_offset(api, "mall-1", day)
    body = {"level_id": "mall-1", "from_hour": iso(day, 8, off), "hours": 1, "plate": "XU0001AA"}
    only_id = {"X-User-Id": str(users[0])}
    assert api.get("/v1/me", headers=only_id).status_code == 401
    assert api.get("/v1/bookings", headers=only_id).status_code == 401
    r = api.post("/v1/bookings", json=body, headers={**only_id, "Idempotency-Key": f"xu-{uuid.uuid4()}"})
    assert r.status_code == 401, r.text
    assert api.get("/v1/bookings", headers={"Authorization": "Bearer not-a-token"}).status_code == 401
    assert api.get("/v1/bookings", headers={"Authorization": "Basic dTE6cA=="}).status_code == 401


def test_6_catalog_is_open_without_login(api, day):
    assert api.get("/v1/garages").status_code == 200
    assert api.get("/v1/levels/mall-1/hours", params={"day": day.isoformat()}).status_code == 200


# ---------- O3.2 owner in SQL, staff function ----------


def test_7_foreign_booking_is_404_like_a_missing_one(api, day, users):
    owner, other = users
    off = local_offset(api, "mall-2", day)
    r = book(api, "mall-2", iso(day, 7, off), 1, owner)
    assert r.status_code == 201, r.text
    url = f"/v1/bookings/{r.json()['id']}"
    missing = "/v1/bookings/00000000-0000-0000-0000-000000000000"

    def same(foreign, absent):
        assert foreign.status_code == absent.status_code == 404, (foreign.text, absent.text)
        f, a = foreign.json(), absent.json()
        assert (f["type"], f["title"], f["detail"]) == (a["type"], a["title"], a["detail"])

    same(api.get(url, headers=auth(other)), api.get(missing, headers=auth(other)))
    same(api.patch(url, json={"plate": "ZZ0000ZZ"}, headers={**auth(other), "If-Match": '"1"'}),
         api.patch(missing, json={"plate": "ZZ0000ZZ"}, headers={**auth(other), "If-Match": '"1"'}))
    same(api.post(f"{url}/cancel", headers=auth(other)), api.post(f"{missing}/cancel", headers=auth(other)))
    # the list of the other user has no foreign rows; the owner's booking is unchanged
    ids = [b["id"] for b in api.get("/v1/bookings", params={"limit": 50}, headers=auth(other)).json()["items"]]
    assert r.json()["id"] not in ids
    mine = api.get(url, headers=auth(owner)).json()
    assert mine["plate"] == "AA1234BB" and mine["status"] == "confirmed" and mine["version"] == 1


def test_8_idempotency_key_of_another_user_gives_no_foreign_answer(api, day, users):
    off = local_offset(api, "mall-1", day)
    body = {"level_id": "mall-1", "from_hour": iso(day, 6, off), "hours": 1, "plate": "IK0001AA"}
    key = f"shared-{uuid.uuid4()}"
    a = api.post("/v1/bookings", json=body, headers={**auth(users[0]), "Idempotency-Key": key})
    b = api.post("/v1/bookings", json=body, headers={**auth(users[1]), "Idempotency-Key": key})
    assert a.status_code == b.status_code == 201
    assert a.json()["id"] != b.json()["id"]
    assert "idempotent-replayed" not in b.headers
    # the first user's repeat is the replay of its own answer
    again = api.post("/v1/bookings", json=body, headers={**auth(users[0]), "Idempotency-Key": key})
    assert again.json()["id"] == a.json()["id"] and again.headers["idempotent-replayed"] == "true"


def test_9_close_level_staff_only(api, db, day, users, staff):
    level = "mall-2"
    off = local_offset(api, level, day)
    kept = book(api, level, iso(day, 15, off), 1, users[0])
    assert kept.status_code == 201, kept.text
    try:
        r = api.post(f"/v1/levels/{level}/close", headers=auth(users[0]))
        assert r.status_code == 403 and slug(r) == "forbidden", r.text
        assert api.post("/v1/levels/no-such-level/close", headers=auth(users[0])).status_code == 403  # role first
        assert api.post(f"/v1/levels/{level}/close").status_code == 401
        assert api.get("/v1/garages").json()  # still open
        assert db.execute("SELECT closed FROM level WHERE id = %s", (level,)).fetchone()["closed"] is False

        for _ in range(2):  # a repeat answers 200 the same way
            r = api.post(f"/v1/levels/{level}/close", headers=auth(staff))
            assert r.status_code == 200 and r.json()["closed"] is True, r.text
        assert api.post("/v1/levels/no-such-level/close", headers=auth(staff)).status_code == 404

        r = book(api, level, iso(day, 16, off), 1, users[1])
        assert r.status_code == 409 and slug(r) == "level-closed", r.text
        # the confirmed booking stays valid (B8)
        got = api.get(f"/v1/bookings/{kept.json()['id']}", headers=auth(users[0]))
        assert got.json()["status"] == "confirmed"

        r = api.post(f"/v1/levels/{level}/open", headers=auth(users[0]))
        assert r.status_code == 403
        r = api.post(f"/v1/levels/{level}/open", headers=auth(staff))
        assert r.status_code == 200 and r.json()["closed"] is False
        assert book(api, level, iso(day, 16, off), 1, users[1]).status_code == 201
    finally:
        db.execute("UPDATE level SET closed = false WHERE id = %s", (level,))


def test_10_me_shows_the_role(staff, users):
    assert httpx.get(f"{BASE}/v1/me", headers=auth(staff)).json()["role"] == "staff"
    assert httpx.get(f"{BASE}/v1/me", headers=auth(users[0])).json()["role"] == "user"


# ---------- O3.3 CORS ----------


def test_11_cors_allows_only_the_client_origin(users):
    pre = {"Access-Control-Request-Method": "POST",
           "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"}
    ok = httpx.options(f"{BASE}/v1/bookings", headers={"Origin": CLIENT_ORIGIN, **pre})
    assert ok.status_code == 200, ok.text
    assert ok.headers["access-control-allow-origin"] == CLIENT_ORIGIN
    allowed = ok.headers["access-control-allow-headers"].lower()
    assert all(h in allowed for h in ("authorization", "content-type", "idempotency-key", "if-match"))
    assert "x-user-id" not in allowed
    assert "access-control-allow-credentials" not in ok.headers

    evil = httpx.options(f"{BASE}/v1/bookings", headers={"Origin": EVIL_ORIGIN, **pre})
    assert evil.status_code == 400 and "access-control-allow-origin" not in evil.headers

    # an actual request from another origin gets no permission to be read either
    r = httpx.get(f"{BASE}/v1/bookings", headers={"Origin": EVIL_ORIGIN, **auth(users[0])})
    assert "access-control-allow-origin" not in r.headers
    r = httpx.get(f"{BASE}/v1/bookings", headers={"Origin": CLIENT_ORIGIN, **auth(users[0])})
    assert r.headers["access-control-allow-origin"] == CLIENT_ORIGIN
    assert "etag" in r.headers["access-control-expose-headers"].lower()


# ---------- cleanup of expired sessions (publisher) ----------


def test_12_cleanup_deletes_expired_sessions_only(db):
    if shutil.which("docker") is None:
        pytest.skip("docker is not available")
    old, live = make_user(db), make_user(db)
    expire(db, old.token)
    run = subprocess.run(
        ["docker", "compose", "exec", "-T", "publisher", "python", "-m", "app.publisher", "--cleanup-once"],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert run.returncode == 0, run.stderr
    assert '"session.cleanup"' in run.stdout
    left = {r["user_id"] for r in db.execute("SELECT user_id FROM session WHERE user_id IN (%s, %s)", (old, live))}
    assert left == {live}


def test_13_headers_helper_sends_the_token(api, users):
    """The helper of all booking tests: Authorization + a new Idempotency-Key, no X-User-Id."""
    h = headers(users[0])
    assert set(h) == {"Authorization", "Idempotency-Key"}
