"""DPC lab 1, O1.5: HTTP response time of POST versus the full execution of a request, and its parts.

--requests (300) requests from --clients (20) concurrent clients against the running api (defaults
QUEUE_CAPACITY=50, WORKERS=4, locked) on the test garage dpc-race. Each client sends POST
/v1/entries (a 429 is repeated after Retry-After with the same event_id), then polls
GET /v1/entries/{id} every --poll-ms (50) until the state is final. The final GET carries the
monotonic marks of the service: received <= started <= finished <= delivered (the first GET that
returned the final state).

Intervals (sample: the requests of this run, every one counted once):
  post_http_ms     client: POST sent -> 202 received (only the answer that was 202)
  queue_wait_ms    service: received -> processing started
  processing_ms    service: started -> finished (recognition 100-500 ms + the transaction)
  total_ms         service: received -> finished, the full execution of the request
  delivery_wait_ms service: finished -> the first GET that returned the final state

    uv run --no-project --python 3.13 --with httpx --with aiohttp --with "psycopg[binary]" python experiments/dpc_lr1_timing.py
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp
from dpc_common import BASE, RACE_GARAGE, TOKEN, FINAL, body, reset_race_garage, triple

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr1"
KEYS = ("post_http_ms", "queue_wait_ms", "processing_ms", "total_ms", "delivery_wait_ms")


async def main_async(args: argparse.Namespace) -> list[dict]:
    rows: list[dict] = []
    todo = list(range(args.requests))

    async def client(s: aiohttp.ClientSession, n: int) -> None:
        direction = "in"
        while todo:
            todo.pop()
            b = body(RACE_GARAGE, f"TIM{n:04d}", direction)
            while True:
                t = time.perf_counter()
                async with s.post(f"{BASE}/v1/entries", json=b) as r:
                    data = await r.json(content_type=None)
                    status, retry = r.status, r.headers.get("Retry-After", "1")
                post_ms = (time.perf_counter() - t) * 1000
                if status != 429:
                    break
                await asyncio.sleep(float(retry))
            assert status == 202, data
            loc = f"{BASE}/v1/entries/{data['id']}"
            while data["state"] not in FINAL:
                await asyncio.sleep(args.poll_ms / 1000)
                async with s.get(loc) as r:
                    data = await r.json()
            rows.append({"id": data["id"], "state": data["state"], "post_http_ms": post_ms,
                         **{k: data[k] for k in KEYS[1:]}, "marks_ms": data["marks_ms"]})
            if data["state"] == "done":
                direction = "out" if direction == "in" else "in"

    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {TOKEN}"},
                                     connector=aiohttp.TCPConnector(limit=args.clients * 2)) as s:
        await asyncio.gather(*(client(s, n) for n in range(args.clients)))
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--requests", type=int, default=300)
    ap.add_argument("--clients", type=int, default=20)
    ap.add_argument("--poll-ms", type=int, default=50)
    args = ap.parse_args()
    reset_race_garage()
    t0 = time.monotonic()
    rows = asyncio.run(main_async(args))
    wall = time.monotonic() - t0

    def ordered(m: dict) -> bool:
        return m["received"] <= m["started"] <= m["finished"] <= m["delivered"]

    bad_order = sum(1 for r in rows if not ordered(r["marks_ms"]))
    stats = {k: triple([r[k] for r in rows if r[k] is not None]) for k in KEYS}
    states = {s: sum(1 for r in rows if r["state"] == s) for s in FINAL}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "timing.json").write_text(json.dumps({"requests": len(rows), "clients": args.clients,
                                                 "poll_ms": args.poll_ms, "seconds": round(wall, 1),
                                                 "states": states, "marks_out_of_order": bad_order,
                                                 "stats": stats, "rows": rows}, indent=1), encoding="utf-8")
    print(f"{len(rows)} requests, {args.clients} clients, GET poll {args.poll_ms} ms, {wall:.1f} s; states {states}")
    print(f"{'interval':<18}{'n':>5}{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}   ms")
    for k in KEYS:
        s = stats[k]
        print(f"{k:<18}{s['n']:>5}{s['p50']:>9}{s['p95']:>9}{s['p99']:>9}{s['max']:>9}")
    print(f"order received <= started <= finished <= delivered: {len(rows) - bad_order} of {len(rows)} ok")


if __name__ == "__main__":
    main()
