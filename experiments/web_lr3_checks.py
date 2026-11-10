"""Checks of Web lab 3 with the demo accounts (user1, user2, staff1 of migrations 900 + 904), from the repository root:

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_lr3_checks.py \
        | tee results/web-lr3/checks.txt

1) login through app-a, the same token on app-b; logout -> the old token 401 on both; a repeat logout 204;
   an expired session (expires_at moved into the past in SQL) -> 401;
2) a wrong password and an unknown login: the same 401 body, the answer time (median of 15);
3) a request with X-User-Id only -> 401;
4) user1 reads, changes and cancels a booking of user2 -> 404, the same as for a missing id;
5) user1 -> close 403; staff1 -> close 200 (twice); a booking on the closed level -> 409, the confirmed one
   stays; open -> a booking 201 again (level mall-2, opened again at the end);
6) CORS: the preflight from CLIENT_ORIGIN and from http://localhost:8082.
Bookings are made on a random day 3-10 years ahead, so the demo days of the client are not touched.
"""

import hashlib
import os
import random
import statistics
import time
import uuid
from datetime import date, timedelta

import httpx
from web_common import APP_A, APP_B, BASE, at, connect, offset

CLIENT_ORIGIN = os.environ.get("WEB_CLIENT_ORIGIN", "http://localhost:5173")
EVIL_ORIGIN = "http://localhost:8082"
ACCOUNTS = {"user1": "user1-pass", "user2": "user2-pass", "staff1": "staff1-pass"}
LEVEL = "mall-2"


def login(who: str, base: str = BASE, password: str | None = None) -> httpx.Response:
    return httpx.post(f"{base}/v1/auth/login", json={"login": who, "password": password or ACCOUNTS[who]}, timeout=10)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def line(label: str, r: httpx.Response, extra: str = "") -> None:
    kind = r.json().get("type", "").rsplit("/", 1)[-1] if r.status_code >= 400 and r.content else ""
    print(f"  {label:<52} {r.status_code} {kind:<22} [{r.headers.get('x-instance', '-')}] {extra}")


def section(title: str) -> None:
    print(f"\n== {title}")


def main() -> None:
    day = date.today() + timedelta(days=random.randrange(365 * 3, 365 * 10))
    while day.month in (3, 10):  # no clock change on the test day
        day += timedelta(days=40)
    off = offset(LEVEL, day)
    print(f"stand {BASE} (app-a {APP_A}, app-b {APP_B}); test day {day}, level {LEVEL}")

    with connect() as db:
        section("1) login on app-a, token on app-b, logout, expiry")
        r = login("user1", APP_A)
        token = r.json()["access_token"]
        line("POST /v1/auth/login user1 -> app-a", r, f"token {len(token)} chars, expires_at {r.json()['expires_at']}")
        stored = db.execute("SELECT count(*) AS n FROM session WHERE token_hash = %s",
                            (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()["n"]
        raw = db.execute("SELECT count(*) AS n FROM session WHERE token_hash = %s", (token,)).fetchone()["n"]
        print(f"  rows of session with sha256(token): {stored}; with the token itself: {raw}")
        me = httpx.get(f"{APP_B}/v1/me", headers=bearer(token))
        line("GET /v1/me with this token -> app-b", me, str(me.json()))
        line("POST /v1/auth/logout -> app-a", httpx.post(f"{APP_A}/v1/auth/logout", headers=bearer(token)))
        for base in (APP_A, APP_B):
            old = httpx.get(f"{base}/v1/me", headers=bearer(token))
            line(f"GET /v1/me with the old token -> {base[-4:]}", old, old.headers.get("www-authenticate", ""))
        line("POST /v1/auth/logout again (same token)", httpx.post(f"{APP_B}/v1/auth/logout", headers=bearer(token)))

        token = login("user2").json()["access_token"]
        line("GET /v1/me user2 before expiry", httpx.get(f"{BASE}/v1/me", headers=bearer(token)))
        db.execute("UPDATE session SET created_at = now() - interval '9 hours', expires_at = now() - interval '1 minute'"
                   " WHERE token_hash = %s", (hashlib.sha256(token.encode()).hexdigest(),))
        print("  SQL: expires_at of this session moved 1 minute into the past")
        r = httpx.get(f"{BASE}/v1/me", headers=bearer(token))
        line("GET /v1/me user2 after expiry", r, r.headers.get("www-authenticate", ""))

        section("2) wrong password and unknown login")
        wrong = login("user1", password="not-the-password")
        unknown = login(f"nobody-{uuid.uuid4().hex[:6]}", password="not-the-password")
        line("user1 + wrong password", wrong)
        line("unknown login", unknown)
        same = {k: v for k, v in wrong.json().items()} == {k: v for k, v in unknown.json().items()}
        print(f"  bodies equal: {same}; body {wrong.json()}")
        times: dict[str, list[float]] = {"wrong password": [], "unknown login": [], "right password": []}
        with httpx.Client(base_url=APP_A, timeout=10) as c:
            for _ in range(15):
                for label, body in (("wrong password", {"login": "user1", "password": "x"}),
                                    ("unknown login", {"login": "nobody-x", "password": "x"}),
                                    ("right password", {"login": "user1", "password": "user1-pass"})):
                    t0 = time.perf_counter()
                    c.post("/v1/auth/login", json=body)
                    times[label].append((time.perf_counter() - t0) * 1000)
        for label, v in times.items():
            print(f"  {label:<16} median {statistics.median(v):6.1f} ms, min {min(v):6.1f}, max {max(v):6.1f} (n = {len(v)}, app-a)")

        section("3) X-User-Id only")
        body = {"level_id": LEVEL, "from_hour": at(day, 9, off), "hours": 1, "plate": "XU0001AA"}
        line("GET /v1/bookings, X-User-Id: u-1", httpx.get(f"{BASE}/v1/bookings", headers={"X-User-Id": "u-1"}))
        line("POST /v1/bookings, X-User-Id: u-1", httpx.post(f"{BASE}/v1/bookings", json=body,
             headers={"X-User-Id": "u-1", "Idempotency-Key": f"xu-{uuid.uuid4()}"}))
        line("GET /v1/me, X-User-Id: s-1", httpx.get(f"{BASE}/v1/me", headers={"X-User-Id": "s-1"}))

        section("4) user1 and a booking of user2")
        t1, t2 = login("user1").json()["access_token"], login("user2").json()["access_token"]
        b = httpx.post(f"{BASE}/v1/bookings", json={**body, "plate": "OW0002BB"},
                       headers={**bearer(t2), "Idempotency-Key": f"ow-{uuid.uuid4()}"})
        line("user2 POST /v1/bookings", b, f"id {b.json()['id']}")
        url, missing = f"{BASE}/v1/bookings/{b.json()['id']}", f"{BASE}/v1/bookings/{uuid.uuid4()}"
        for label, own, absent in (
            ("GET", httpx.get(url, headers=bearer(t1)), httpx.get(missing, headers=bearer(t1))),
            ("PATCH plate", httpx.patch(url, json={"plate": "HACK00AA"}, headers={**bearer(t1), "If-Match": '"1"'}),
             httpx.patch(missing, json={"plate": "HACK00AA"}, headers={**bearer(t1), "If-Match": '"1"'})),
            ("POST cancel", httpx.post(f"{url}/cancel", headers=bearer(t1)), httpx.post(f"{missing}/cancel", headers=bearer(t1))),
        ):
            line(f"user1 {label} booking of user2", own)
            line(f"user1 {label} missing id", absent)
            print(f"  same type/title/detail: {all(own.json()[k] == absent.json()[k] for k in ('type', 'title', 'detail'))}")
        ids = [x["id"] for x in httpx.get(f"{BASE}/v1/bookings?limit=50", headers=bearer(t1)).json()["items"]]
        print(f"  booking of user2 in the list of user1: {b.json()['id'] in ids}")
        mine = httpx.get(url, headers=bearer(t2)).json()
        print(f"  user2 reads it: plate {mine['plate']}, status {mine['status']}, version {mine['version']}")

        section("5) staff function: close a level")
        ts = login("staff1").json()["access_token"]
        line(f"user1 POST /v1/levels/{LEVEL}/close", httpx.post(f"{BASE}/v1/levels/{LEVEL}/close", headers=bearer(t1)))
        line("user1 POST /v1/levels/no-such-level/close", httpx.post(f"{BASE}/v1/levels/no-such-level/close", headers=bearer(t1)))
        line(f"no token POST /v1/levels/{LEVEL}/close", httpx.post(f"{BASE}/v1/levels/{LEVEL}/close"))
        try:
            for n in (1, 2):
                r = httpx.post(f"{BASE}/v1/levels/{LEVEL}/close", headers=bearer(ts))
                line(f"staff1 POST /v1/levels/{LEVEL}/close ({n})", r, f"closed={r.json()['closed']}")
            r = httpx.post(f"{BASE}/v1/bookings", json={**body, "from_hour": at(day, 11, off), "plate": "CL0003CC"},
                           headers={**bearer(t1), "Idempotency-Key": f"cl-{uuid.uuid4()}"})
            line("user1 POST /v1/bookings on the closed level", r)
            line("user2 GET its confirmed booking of this level", httpx.get(url, headers=bearer(t2)),
                 httpx.get(url, headers=bearer(t2)).json()["status"])
            line(f"user1 POST /v1/levels/{LEVEL}/open", httpx.post(f"{BASE}/v1/levels/{LEVEL}/open", headers=bearer(t1)))
            r = httpx.post(f"{BASE}/v1/levels/{LEVEL}/open", headers=bearer(ts))
            line(f"staff1 POST /v1/levels/{LEVEL}/open", r, f"closed={r.json()['closed']}")
            r = httpx.post(f"{BASE}/v1/bookings", json={**body, "from_hour": at(day, 11, off), "plate": "CL0003CC"},
                           headers={**bearer(t1), "Idempotency-Key": f"cl-{uuid.uuid4()}"})
            line("user1 POST /v1/bookings after open", r)
        finally:
            db.execute("UPDATE level SET closed = false WHERE id = %s", (LEVEL,))

        section("6) CORS (nginx :8080)")
        pre = {"Access-Control-Request-Method": "POST",
               "Access-Control-Request-Headers": "authorization,content-type,idempotency-key"}
        for origin in (CLIENT_ORIGIN, EVIL_ORIGIN):
            r = httpx.options(f"{BASE}/v1/bookings", headers={"Origin": origin, **pre})
            print(f"  OPTIONS /v1/bookings Origin {origin}: {r.status_code}")
            for h in ("access-control-allow-origin", "access-control-allow-methods", "access-control-allow-headers",
                      "access-control-allow-credentials", "access-control-max-age"):
                print(f"    {h}: {r.headers.get(h, '(absent)')}")
            r = httpx.get(f"{BASE}/v1/garages", headers={"Origin": origin})
            print(f"  GET /v1/garages Origin {origin}: {r.status_code}, "
                  f"access-control-allow-origin: {r.headers.get('access-control-allow-origin', '(absent)')}, "
                  f"access-control-expose-headers: {r.headers.get('access-control-expose-headers', '(absent)')}")


if __name__ == "__main__":
    main()
