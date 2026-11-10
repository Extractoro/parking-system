"""Experiment "cancel twice": a cancelled booking frees its hours exactly once.

  a) two cancels one after the other;
  b) two cancels at the same moment (app-a and app-b), 5 rounds.
Each case books 3 hours of level mall-2 on its own future day; `free` of each hour is read
in SQL before the booking, after it and after the cancels.

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_cancel_twice.py \
        | tee results/web-lr2/cancel-twice.txt
"""

import threading
import uuid

import httpx
from web_common import APP_A, APP_B, at, auth, connect, future_days, new_user, offset

LEVEL = "mall-2"


def free(db, start: str) -> list[int]:
    rows = db.execute(
        """SELECT h, coalesce(lh.free, l.spots) AS free
           FROM level l CROSS JOIN generate_series(%(s)s::timestamptz, %(s)s::timestamptz + interval '2 hours', interval '1 hour') h
           LEFT JOIN level_hour lh ON lh.level_id = l.id AND lh.hour = h WHERE l.id = %(l)s ORDER BY h""",
        {"s": start, "l": LEVEL},
    ).fetchall()
    return [r["free"] for r in rows]


def main() -> None:
    with connect() as db:
        days = future_days(6)
        print(f"{'case':<14}{'cancel answers':<36}{'free before':<14}{'booked':<14}{'cancelled':<14}{'ok':>4}")
        for i, day in enumerate(days):
            user = new_user(db, "cancel")
            start = at(day, 15, offset(LEVEL, day))
            before = free(db, start)
            r = httpx.post(f"{APP_A}/v1/bookings", timeout=15,
                           json={"level_id": LEVEL, "from_hour": start, "hours": 3, "plate": "CN0001AA"},
                           headers={**auth(user), "Idempotency-Key": f"cancel-{uuid.uuid4()}"})
            assert r.status_code == 201, r.text
            booking = r.json()["id"]
            booked = free(db, start)
            url = f"/v1/bookings/{booking}/cancel"
            answers: list[str] = []
            if i == 0:
                case = "sequential"
                for base in (APP_A, APP_B):
                    c = httpx.post(base + url, headers=auth(user), timeout=15)
                    answers.append(f"{c.status_code} {c.json()['status']} v{c.json()['version']}")
            else:
                case = f"concurrent {i}"
                barrier, res = threading.Barrier(2), [None, None]

                def one(j: int, barrier=barrier, res=res, url=url, user=user) -> None:
                    with httpx.Client(timeout=15) as cl:
                        barrier.wait()
                        res[j] = cl.post((APP_A, APP_B)[j] + url, headers=auth(user))

                ts = [threading.Thread(target=one, args=(j,)) for j in (0, 1)]
                for t in ts:
                    t.start()
                for t in ts:
                    t.join()
                answers = [f"{c.status_code} {c.json()['status']} v{c.json()['version']}" for c in res]
            after = free(db, start)
            ok = after == before and all(b - 1 == a for a, b in zip(booked, before))
            status = db.execute("SELECT status, version FROM booking WHERE id = %s", (booking,)).fetchone()
            print(f"{case:<14}{', '.join(answers):<36}{str(before):<14}{str(booked):<14}{str(after):<14}{'yes' if ok else 'NO':>4}"
                  f"  db: {status['status']} v{status['version']}")


if __name__ == "__main__":
    main()
