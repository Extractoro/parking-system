"""Experiment "idempotency key": repeats of POST /v1/bookings.

  a) the same key and body twice (app-a, then app-b): the same answer, one booking;
  b) the same key with another body: 422 idempotency-key-reused, nothing new;
  c) no key: 400; a key of 201 characters: 422;
  d) 10 rounds of two requests with ONE key at the same moment (app-a and app-b): one booking each round.

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_idempotency.py \
        | tee results/web-lr2/idempotency.txt
"""

import threading
import uuid
from collections import Counter

import httpx
from web_common import APP_A, APP_B, at, auth, connect, future_days, new_user, offset

LEVEL = "mall-1"


def post(base, user, key, body):
    h = auth(user) | ({"Idempotency-Key": key} if key is not None else {})
    return httpx.post(f"{base}/v1/bookings", json=body, headers=h, timeout=15)


def kind(r) -> str:
    return r.json().get("type", "").rsplit("/", 1)[-1] if r.status_code >= 400 else ""


def show(label, r) -> None:
    extra = r.json().get("id") if r.status_code < 400 else kind(r)
    replay = " replayed" if r.headers.get("idempotent-replayed") else ""
    print(f"  {label:<34} {r.status_code} {extra}{replay} [{r.headers.get('x-instance')}]")


def main() -> None:
    with connect() as db:
        count = lambda u: db.execute("SELECT count(*) AS n FROM booking WHERE user_id = %s", (u,)).fetchone()["n"]  # noqa: E731
        days = future_days(11)
        day, off = days[0], offset(LEVEL, days[0])
        body = {"level_id": LEVEL, "from_hour": at(day, 10, off), "hours": 2, "plate": "ID0001AA"}

        user, key = new_user(db, "idem"), f"idem-{uuid.uuid4()}"
        print(f"a) same key and body twice; user {user}, key {key}")
        show("first, app-a", post(APP_A, user, key, body))
        show("repeat, app-b", post(APP_B, user, key, body))
        print(f"  bookings of the user: {count(user)}")

        print("b) same key, another body (plate)")
        show("repeat with plate ID0002AA", post(APP_A, user, key, {**body, "plate": "ID0002AA"}))
        print(f"  bookings of the user: {count(user)}")

        print("c) key header")
        u2 = new_user(db, "idem")
        show("no Idempotency-Key", post(APP_A, u2, None, body))
        show("key of 201 characters", post(APP_A, u2, "k" * 201, body))
        print(f"  bookings of the user: {count(u2)}")

        print("d) two requests with one key at the same moment, 10 rounds (app-a + app-b)")
        print(f"  {'round':<6}{'answers':<34}{'bookings':>9}")
        total = Counter()
        bad = 0
        for i, d in enumerate(days[1:], 1):
            u, k = new_user(db, "idem"), f"idem-{uuid.uuid4()}"
            b = {**body, "from_hour": at(d, 10, offset(LEVEL, d))}
            barrier, res = threading.Barrier(2), [None, None]

            def one(j: int, u=u, k=k, b=b, barrier=barrier, res=res) -> None:
                with httpx.Client(timeout=15) as c:
                    barrier.wait()
                    res[j] = c.post(f"{(APP_A, APP_B)[j]}/v1/bookings", json=b,
                                    headers={**auth(u), "Idempotency-Key": k})

            ts = [threading.Thread(target=one, args=(j,)) for j in (0, 1)]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            answers = [f"{r.status_code}{' ' + kind(r) if kind(r) else ''}" for r in res]
            n = count(u)
            bad += n != 1
            total.update(answers)
            print(f"  {i:<6}{', '.join(answers):<34}{n:>9}")
        print(f"  answers: {dict(total)}; rounds with not exactly one booking: {bad}")


if __name__ == "__main__":
    main()
