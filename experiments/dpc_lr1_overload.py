"""DPC lab 1, O1.2: the full queue and the latency of /health.

The api is recreated with QUEUE_CAPACITY=50, WORKERS=4 (locked). For --seconds (30) --clients (60)
clients send POST /v1/entries to the test garage dpc-race without pause (in and out of their own
plate, a new event_id each time); a 429 is counted and the thread goes on at once. In parallel one
process sends GET /health 10 times per second (--health-rate) on its own connection and reads
/metrics every 0.5 s; the load itself is --clients coroutines of one asyncio loop (httpx.AsyncClient),
so neither waits for the other in the client.
The load uses aiohttp, the probe http.client (see PC-5 about httpx).
Afterwards every accepted request must reach a final state.

    uv run --no-project --python 3.13 --with httpx --with aiohttp --with "psycopg[binary]" python experiments/dpc_lr1_overload.py

Writes results/dpc-lr1/overload.json; the text goes to stdout (tee it to results/dpc-lr1/overload.txt).
"""

import argparse
import asyncio
import http.client
import json
import multiprocessing as mp
import time
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp

import httpx
from dpc_common import BASE, RACE_GARAGE, TOKEN, body, client, db, metrics, reset_race_garage, restart_api, triple

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr1"


async def load(clients: int, seconds: float) -> tuple[Counter, dict, list, float]:
    """aiohttp, not httpx.AsyncClient: with 60 connections httpx itself gave only ~50 req/s even to
    /health (PC-5), the generator, not the service, was the bottleneck."""
    statuses: Counter[int] = Counter()
    post_ms: dict[int, list[float]] = {202: [], 429: []}
    accepted: list[str] = []
    stop_at = time.monotonic() + seconds

    async def sender(s: aiohttp.ClientSession, n: int) -> None:
        plate, direction = f"OVL{n:05d}", "in"
        while time.monotonic() < stop_at:
            t = time.perf_counter()
            rid = None
            try:
                async with s.post(f"{BASE}/v1/entries", json=body(RACE_GARAGE, plate, direction)) as r:
                    code = r.status
                    data = await r.json(content_type=None)
                    rid = data.get("id") if code == 202 else None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                code = 0
            statuses[code] += 1
            post_ms.setdefault(code, []).append((time.perf_counter() - t) * 1000)
            if rid:
                accepted.append(rid)
                direction = "out" if direction == "in" else "in"

    conn = aiohttp.TCPConnector(limit=clients, force_close=False)
    async with aiohttp.ClientSession(connector=conn, headers={"Authorization": f"Bearer {TOKEN}"},
                                     timeout=aiohttp.ClientTimeout(total=30)) as s:
        t0 = time.monotonic()
        await asyncio.gather(*(sender(s, n) for n in range(clients)))
        return statuses, post_ms, accepted, time.monotonic() - t0


def probe_main(seconds: float, rate: float, out) -> None:
    """Separate process: GET /health `rate` times per second on its own keep-alive connection
    (http.client, a thin client), /metrics every 0.5 s on another one."""
    u = urlsplit(BASE)
    hc = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    mc = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    health_ms: list[float] = []
    bad = 0
    samples: list[dict] = []
    t0 = time.monotonic()
    stop_at = t0 + seconds
    period = 1 / rate
    nxt = t0
    next_sample = t0
    while time.monotonic() < stop_at:
        t = time.perf_counter()
        try:
            hc.request("GET", "/health")
            r = hc.getresponse()
            r.read()
            ok = r.status == 200
        except (OSError, http.client.HTTPException):
            ok = False
            hc.close()
        health_ms.append((time.perf_counter() - t) * 1000)
        bad += 0 if ok else 1
        if time.monotonic() >= next_sample:
            mc.request("GET", "/metrics")
            m = json.loads(mc.getresponse().read())
            samples.append({"t": round(time.monotonic() - t0, 1), "queue_length": m["queue_length"],
                            "in_flight": m["in_flight"], "rejected": m["rejected"]})
            next_sample += 0.5
        nxt += period
        time.sleep(max(0.0, nxt - time.monotonic()))
    out.put((health_ms, bad, samples))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=60)
    ap.add_argument("--seconds", type=float, default=30)
    ap.add_argument("--health-rate", type=float, default=10)
    ap.add_argument("--capacity", type=int, default=50)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--no-restart", action="store_true")
    args = ap.parse_args()

    if not args.no_restart:
        restart_api(QUEUE_CAPACITY=args.capacity, WORKERS=args.workers, RESOURCE_MODE="locked")
    reset_race_garage()
    m0 = metrics()
    print(f"api: queue_capacity={m0['queue_capacity']} workers={m0['workers']}; "
          f"{args.clients} clients x {args.seconds:.0f} s on {RACE_GARAGE}; /health {args.health_rate:.0f}/s")

    # the load: --clients coroutines in this process; /health and /metrics in a separate process, so
    # the probe is not delayed by the load generator itself (one GIL, one event loop)
    ctx = mp.get_context("spawn")
    probe_q = ctx.Queue()
    probe = ctx.Process(target=probe_main, args=(args.seconds, args.health_rate, probe_q))
    probe.start()
    statuses, post_ms, accepted, wall = asyncio.run(load(args.clients, args.seconds))
    health_ms, health_bad, samples = probe_q.get(timeout=args.seconds + 60)
    probe.join()
    started = time.monotonic() - wall

    # every accepted request reaches a final state
    deadline = time.monotonic() + 120
    while True:
        with db() as conn:
            rows = conn.execute(
                "SELECT state, count(*) AS n FROM entry_request WHERE id = ANY(%s) GROUP BY state ORDER BY state",
                (accepted,),
            ).fetchall()
        open_ = sum(r["n"] for r in rows if r["state"] in ("queued", "processing"))
        if open_ == 0 or time.monotonic() > deadline:
            break
        time.sleep(0.5)
    drained = round(time.monotonic() - started - wall, 1)
    g = client().get(f"/v1/garage/{RACE_GARAGE}").json()

    full = [s for s in samples if s["queue_length"] >= m0["queue_capacity"] - 2]
    result = {
        "queue_capacity": m0["queue_capacity"], "workers": m0["workers"], "clients": args.clients,
        "seconds": round(wall, 1), "health_rate": args.health_rate,
        "statuses": {str(k): v for k, v in sorted(statuses.items())},
        "post_202_ms": triple(post_ms[202]), "post_429_ms": triple(post_ms[429]),
        "health_ms": triple(health_ms), "health_not_200": health_bad,
        "queue_samples": len(samples), "samples_queue_full": len(full),
        "queue_length_max": max(s["queue_length"] for s in samples),
        "in_flight_max": max(s["in_flight"] for s in samples),
        "final_states": {r["state"]: r["n"] for r in rows}, "drained_after_s": drained, "garage": g,
        "samples": samples,
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "overload.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"POST /v1/entries: {sum(statuses.values())} in {wall:.1f} s; statuses: "
          + ", ".join(f"{k or 'no answer'} x {v}" for k, v in sorted(statuses.items())))
    print(f"  client latency 202: {result['post_202_ms']}")
    print(f"  client latency 429: {result['post_429_ms']}")
    print(f"GET /health: {result['health_ms']}, not 200: {health_bad}  (requirement p95 <= 100 ms)")
    print(f"queue_length: max {result['queue_length_max']}, >= capacity-2 in {len(full)} of {len(samples)} samples; "
          f"in_flight max {result['in_flight_max']}")
    print(f"accepted {len(accepted)}: final states {result['final_states']}, last finished {drained} s after the load")
    print(f"garage {RACE_GARAGE}: occupied {g['occupied']}, inside {g['inside']}, invariant_ok {g['invariant_ok']}")


if __name__ == "__main__":
    main()
