"""Experiment "last spot": 20 concurrent bookings of overlapping intervals on a 1-spot level.

Level race-test (migration 902, 1 spot). Each round uses its own future day: 20 POST /v1/bookings
with different overlapping intervals start together (a barrier) through nginx, which spreads them
over app-a and app-b (the answering instance is in X-Instance). After the round the state is
checked in SQL: no hour may have more confirmed bookings than spots, and `free` must equal
spots minus those bookings. The server mode is read from the running containers.

Before a round the rows of its hours are created in level_hour (free = spots), the usual state
once anyone has booked these hours. With --new-hours they are not: then the concurrent
INSERT ... ON CONFLICT DO NOTHING of the lazy row creation waits for the first transaction that
inserted the row, and that alone serializes the first bookings of an hour in any mode.

    # protected (the default of .env)
    docker compose up -d app-a app-b
    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" python experiments/web_last_spot.py
    # without protection: only the level race-test is changed by this mode
    INVARIANT_MODE=unprotected docker compose up -d app-a app-b
    uv run ... python experiments/web_last_spot.py
    docker compose up -d app-a app-b        # back to protected

Writes results/web-lr2/last-spot-<mode>.json and prints a table.
"""

import argparse
import json
import threading
import uuid
from collections import Counter
from datetime import UTC, datetime

import httpx
from web_common import BASE, RESULTS, at, auth, compose, connect, future_days, new_user, offset

LEVEL = "race-test"
# overlapping intervals (start hour, hours): every one shares an hour with several others
INTERVALS = [(18, 3), (19, 2), (17, 2), (20, 1), (18, 1)]

VIOLATIONS_SQL = """
    SELECT h AS hour, count(b.id) AS bookings, l.spots, lh.free
    FROM level l
    CROSS JOIN generate_series(%(start)s::timestamptz, %(end)s::timestamptz - interval '1 hour', interval '1 hour') h
    LEFT JOIN booking b ON b.level_id = l.id AND b.status = 'confirmed'
                       AND b.from_hour <= h AND h < b.expires_at
    LEFT JOIN level_hour lh ON lh.level_id = l.id AND lh.hour = h
    WHERE l.id = %(level)s
    GROUP BY h, l.spots, lh.free
    ORDER BY h
"""


def one_round(db, day, users, n: int, new_hours: bool) -> dict:
    off = offset(LEVEL, day)
    if not new_hours:
        db.execute(
            """
            INSERT INTO level_hour (level_id, hour, free)
            SELECT l.id, h, l.spots FROM level l,
                   generate_series(%(start)s::timestamptz, %(end)s::timestamptz - interval '1 hour', interval '1 hour') h
            WHERE l.id = %(level)s
            ON CONFLICT DO NOTHING
            """,
            {"start": at(day, 17, off), "end": at(day, 21, off), "level": LEVEL},
        )
    barrier = threading.Barrier(n)
    results: list[dict] = [{} for _ in range(n)]

    def send(i: int) -> None:
        hour, hours = INTERVALS[i % len(INTERVALS)]
        body = {"level_id": LEVEL, "from_hour": at(day, hour, off), "hours": hours, "plate": f"RT{i:04d}AA"}
        headers = {**auth(users[i % len(users)]), "Idempotency-Key": f"race-{uuid.uuid4()}"}
        with httpx.Client(timeout=15) as client:
            barrier.wait()
            r = client.post(f"{BASE}/v1/bookings", json=body, headers=headers)
        kind = r.json().get("type", "").rsplit("/", 1)[-1] if r.status_code >= 400 else ""
        results[i] = {"interval": f"{hour:02d}:00+{hours}", "status": r.status_code, "type": kind,
                      "instance": r.headers.get("x-instance")}

    threads = [threading.Thread(target=send, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    start, end = at(day, 17, off), at(day, 21, off)
    hours = db.execute(VIOLATIONS_SQL, {"start": start, "end": end, "level": LEVEL}).fetchall()
    over = [h for h in hours if h["bookings"] > h["spots"]]
    wrong_free = [h for h in hours if h["free"] is not None and h["free"] != h["spots"] - h["bookings"]]
    return {
        "day": day.isoformat(),
        "statuses": dict(Counter(str(r["status"]) for r in results)),
        "refusals": dict(Counter(r["type"] for r in results if r["type"])),
        "instances": dict(Counter(r["instance"] for r in results)),
        "hours": [{"hour": h["hour"].isoformat(), "bookings": h["bookings"], "spots": h["spots"], "free": h["free"]}
                  for h in hours],
        "hours_over_spots": len(over),
        "max_bookings_in_hour": max(h["bookings"] for h in hours),
        "free_not_matching_bookings": len(wrong_free),
        "requests": results,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--rounds", type=int, default=10)
    p.add_argument("--requests", type=int, default=20)
    p.add_argument("--new-hours", action="store_true", help="do not create the hour rows before a round")
    args = p.parse_args()

    modes = {s: compose("exec", "-T", s, "printenv", "INVARIANT_MODE") or "protected" for s in ("app-a", "app-b")}
    if len(set(modes.values())) != 1:
        raise SystemExit(f"app-a and app-b run in different modes: {modes}")
    mode = modes["app-a"]

    with connect() as db:
        users = [new_user(db, "race"), new_user(db, "race")]
        days = future_days(args.rounds)
        rounds = [one_round(db, d, users, args.requests, args.new_hours) for d in days]

    total = Counter()
    for r in rounds:
        total.update(r["statuses"])
    summary = {
        "mode": mode,
        "level": LEVEL,
        "hour_rows_before_round": "absent" if args.new_hours else "created, free = spots",
        "rounds": len(rounds),
        "requests_per_round": args.requests,
        "intervals": [f"{h:02d}:00+{n}" for h, n in INTERVALS],
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "statuses_total": dict(total),
        "rounds_with_violation": sum(1 for r in rounds if r["hours_over_spots"]),
        "hours_over_spots_total": sum(r["hours_over_spots"] for r in rounds),
        "rounds_free_mismatch": sum(1 for r in rounds if r["free_not_matching_bookings"]),
        "instances_total": dict(sum((Counter(r["instances"]) for r in rounds), Counter())),
        "per_round": rounds,
    }
    RESULTS.mkdir(parents=True, exist_ok=True)
    out = RESULTS / f"last-spot-{mode}{'-new-hours' if args.new_hours else ''}.json"
    out.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"mode={mode} level={LEVEL} rounds={len(rounds)} x {args.requests} requests via {BASE}; "
          f"hour rows before a round: {summary['hour_rows_before_round']}")
    print(f"{'day':<12}{'201':>5}{'409':>5}{'other':>7}  {'app-a/app-b':<12}{'max/hour':>9}{'over':>6}{'free!=':>7}")
    for r in rounds:
        s = r["statuses"]
        other = sum(v for k, v in s.items() if k not in ("201", "409"))
        inst = f"{r['instances'].get('app-a', 0)}/{r['instances'].get('app-b', 0)}"
        print(f"{r['day']:<12}{s.get('201', 0):>5}{s.get('409', 0):>5}{other:>7}  {inst:<12}"
              f"{r['max_bookings_in_hour']:>9}{r['hours_over_spots']:>6}{r['free_not_matching_bookings']:>7}")
    print(f"total: {dict(total)}; rounds with an hour over spots: {summary['rounds_with_violation']}; "
          f"hours over spots: {summary['hours_over_spots_total']}; instances: {summary['instances_total']}")
    print(f"saved {out.relative_to(RESULTS.parents[1])}")


if __name__ == "__main__":
    main()
