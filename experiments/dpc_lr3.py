"""DPC lab 3: Ray tasks, the effect receiver, the controlled failure, the limit of unfinished tasks.

Needs the stand of lab 3: docker compose up -d api ray-head ray-worker (EXECUTION=ray by default).
From the repository root:

    uv run --no-project --python 3.13 --with httpx --with aiohttp --with "psycopg[binary]" \
        python experiments/dpc_lr3.py fault | compare | inflight [--delays 0,500]

fault     O3.4: /faults/after_effect_before_ack is armed, then ONE entry is sent to the test garage dpc-race.
          The Ray worker dies after the receiver accepted the effect; Ray runs the task again with the same
          event_id; the receiver returns the stored decision. Two cases: an accepted entry (free garage) and a
          rejected one (dpc-race filled to 5 of 5 first). Prints the log lines, the attempts of the task in the
          Ray State API and SQL counts. Writes results/dpc-lr3/fault.json.
compare   the same load in EXECUTION=local and EXECUTION=ray: 40 cars drive in and out of dpc-race until 200
          requests are sent (the O1.4 scenario), WORKERS=8, locked, after a warm-up of 40 requests. Invariant, states, the full execution time
          (total_ms of every request, received -> finished in the api) p50/p95. Writes compare.json.
inflight  O3.2: EXECUTION=ray, IN_FLIGHT_LIMIT=8, RECEIVER_DELAY_MS = each of --delays; 60 clients send POST
          without pause for 30 s (as O1.2). /metrics every 0.5 s (queue_length, in_flight), /health 10/s,
          unfinished tasks in the Ray State API every 2 s. Throughput, p95 of the full execution, share of 429,
          max in_flight, max unfinished Ray tasks. Writes inflight.json.

The api is recreated with the settings of each run (restart_api) and left with the defaults of .env at the end.
"""

import argparse
import asyncio
import http.client
import json
import multiprocessing as mp
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
import httpx
from dpc_common import (BASE, FINAL, RACE_GARAGE, TOKEN, body, client, compose, db, metrics, reset_race_garage,
                        restart_api, triple, wait_final)

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr3"
RAY = "http://localhost:8265"
FLAG = "after_effect_before_ack"


# ------------------------------------------------------------------ helpers


def ray_tasks(since_ms: int) -> list[dict]:
    """Attempts of process_entry started after since_ms (Ray State API of the dashboard)."""
    r = httpx.get(f"{RAY}/api/v0/tasks", timeout=30, params={
        "limit": 10000, "detail": 1, "filter_keys": "name", "filter_predicates": "=",
        "filter_values": "process_entry"})
    r.raise_for_status()
    rows = r.json()["data"]["result"]["result"]
    out = []
    for t in rows:
        start = t.get("start_time_ms") or t.get("creation_time_ms") or 0
        if start >= since_ms:
            out.append({"task_id": t["task_id"][:16], "attempt": t["attempt_number"], "state": t["state"],
                        "error_type": t.get("error_type"), "node": (t.get("node_id") or "")[:8],
                        "pid": t.get("worker_pid"), "start_ms": start})
    return sorted(out, key=lambda x: (x["task_id"], x["attempt"]))


def unfinished_ray_tasks() -> int | None:
    try:
        r = httpx.get(f"{RAY}/api/v0/tasks", timeout=10, params={
            "limit": 10000, "filter_keys": ["name", "state"], "filter_predicates": ["=", "!="],
            "filter_values": ["process_entry", "FINISHED"]})
        rows = r.json()["data"]["result"]["result"]
        return sum(1 for t in rows if t["state"] not in ("FINISHED", "FAILED"))
    except (httpx.HTTPError, KeyError, ValueError):
        return None


def logs_since(since: str, *services: str, needles: tuple[str, ...]) -> list[str]:
    text = compose("logs", "--no-color", "--since", since, *services, check=False)
    return [line.strip() for line in text.splitlines() if any(n in line for n in needles)]


def garage_sql(event_id: str | None = None) -> dict:
    with db() as conn:
        row = conn.execute(
            """SELECT gs.occupied, gs.spots,
                      (SELECT count(*) FROM entry WHERE garage_id = %(g)s AND direction = 'in')
                    - (SELECT count(*) FROM entry WHERE garage_id = %(g)s AND direction = 'out') AS inside,
                      (SELECT count(*) FROM entry_request WHERE garage_id = %(g)s) AS requests,
                      (SELECT count(*) FROM effect f JOIN entry_request r ON r.id = f.request_id
                       WHERE r.garage_id = %(g)s) AS effects
               FROM garage_state gs WHERE gs.garage_id = %(g)s""",
            {"g": RACE_GARAGE},
        ).fetchone()
        if event_id:
            row["effect_rows"] = conn.execute(
                "SELECT effect_key, decision, reason FROM effect WHERE effect_key = %s", (event_id,)).fetchall()
            row["entry_rows"] = conn.execute(
                "SELECT count(*) AS n FROM entry WHERE event_id = %s", (event_id,)).fetchone()["n"]
    return row


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


# ------------------------------------------------------------------ O3.4


def one_fault(c: httpx.Client, label: str, plate: str) -> dict:
    before = garage_sql()
    since, since_ms = utc_now(), int(time.time() * 1000) - 2000
    time.sleep(1)
    compose("exec", "-T", "api", "touch", f"/faults/{FLAG}")
    b = body(RACE_GARAGE, plate, "in")
    r = c.post("/v1/entries", json=b)
    assert r.status_code == 202, r.text
    final = wait_final(c, r.headers["Location"], timeout=120)
    time.sleep(3)  # the repeated attempt logs task.done after the request is final
    flag_left = "missing" in compose("exec", "-T", "api", "sh", "-c", f"test -e /faults/{FLAG} || echo missing")
    after = garage_sql(b["event_id"])
    g = c.get(f"/v1/garage/{RACE_GARAGE}").json()
    lines = logs_since(since, "api", "ray-head", "ray-worker",
                       needles=("fault.hit", "task.done", "effect.accepted", "effect.repeated", "task_failed"))
    attempts = ray_tasks(since_ms)
    retried = {t["task_id"] for t in attempts if t["attempt"] > 0}
    return {"case": label, "event_id": b["event_id"], "request_state": final["state"], "reason": final["reason"],
            "total_ms": final["total_ms"], "flag_taken": flag_left,
            "occupied_before": before["occupied"], "occupied_after": after["occupied"],
            "effect_rows": after["effect_rows"], "entry_rows": after["entry_rows"],
            "garage": {k: g[k] for k in ("occupied", "inside", "spots", "invariant_ok", "execution")},
            "log": [ln for ln in lines if b["event_id"] in ln or "fault.hit" in ln],
            "ray_attempts": [t for t in attempts if t["task_id"] in retried]}


def cmd_fault(_: argparse.Namespace) -> None:
    restart_api(EXECUTION="ray")
    reset_race_garage()
    results = []
    with client() as c:
        results.append(one_fault(c, "accepted entry (dpc-race empty)", "FLT0001"))
        for n in range(2, 6):  # fill dpc-race: 5 of 5, without the fault
            r = c.post("/v1/entries", json=body(RACE_GARAGE, f"FLT000{n}"))
            assert wait_final(c, r.headers["Location"])["state"] == "done"
        results.append(one_fault(c, "rejected entry (dpc-race full)", "FLT0006"))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "fault.json").write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    for r in results:
        print(f"== {r['case']}: event_id {r['event_id']}")
        print(f"request: {r['request_state']} {r['reason'] or ''}, total {r['total_ms']} ms; "
              f"flag file taken: {r['flag_taken']}")
        print("log:")
        for ln in r["log"]:
            print("  " + ln)
        print("Ray State API, attempts of the retried task:")
        for t in r["ray_attempts"]:
            print(f"  task {t['task_id']} attempt {t['attempt']} {t['state']:<9} error {t['error_type']} "
                  f"node {t['node']} pid {t['pid']}")
        print(f"SQL: effect rows {r['effect_rows']}, entry rows {r['entry_rows']}, "
              f"occupied {r['occupied_before']} -> {r['occupied_after']}")
        print(f"GET /v1/garage/{RACE_GARAGE}: {r['garage']}\n")


# ------------------------------------------------------------------ local vs ray


async def drive(clients: int, total: int) -> tuple[list[dict], dict]:
    """The O1.4 scenario of lab 1 (40 cars, in and out), collecting the final views of the requests."""
    sent = 0
    rows: list[dict] = []
    states: Counter[str] = Counter()

    async def one(s: aiohttp.ClientSession, plate: str, direction: str) -> dict:
        b = body(RACE_GARAGE, plate, direction)
        while True:
            async with s.post(f"{BASE}/v1/entries", json=b) as r:
                data = await r.json(content_type=None)
                if r.status != 429:
                    break
                await asyncio.sleep(float(r.headers.get("Retry-After", "1")))
        while data["state"] not in FINAL:
            await asyncio.sleep(0.05)
            async with s.get(f"{BASE}/v1/entries/{data['id']}") as r:
                data = await r.json()
        states[f"{direction}:{data['state']}" + (f":{data['reason']}" if data["reason"] else "")] += 1
        rows.append(data)
        return data

    async def car(s: aiohttp.ClientSession, n: int) -> None:
        nonlocal sent
        plate = f"CMP{n:03d}"
        while sent < total:
            sent += 1
            if (await one(s, plate, "in"))["state"] == "done" and sent < total:
                sent += 1
                await one(s, plate, "out")

    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {TOKEN}"},
                                     connector=aiohttp.TCPConnector(limit=clients * 2)) as s:
        await asyncio.gather(*(car(s, n) for n in range(clients)))
    return rows, dict(sorted(states.items()))


def cmd_compare(args: argparse.Namespace) -> None:
    results = []
    for mode in args.modes.split(","):
        restart_api(EXECUTION=mode, WORKERS=8, RESOURCE_MODE="locked")
        if mode == "ray":
            time.sleep(8)  # ray.init of the new api process
        reset_race_garage()
        asyncio.run(drive(args.clients, args.warmup))  # warm-up, the same in both modes: Ray starts its
        reset_race_garage()                            # worker processes on demand (~1-3 s each)
        t0 = time.monotonic()
        rows, states = asyncio.run(drive(args.clients, args.requests))
        wall = time.monotonic() - t0
        g = client().get(f"/v1/garage/{RACE_GARAGE}").json()
        sql = garage_sql()
        m = metrics()
        results.append({"execution": mode, "workers": 8, "requests": len(rows), "seconds": round(wall, 1),
                        "states": states, "total_ms": triple([r["total_ms"] for r in rows]),
                        "processing_ms": triple([r["processing_ms"] for r in rows]),
                        "queue_wait_ms": triple([r["queue_wait_ms"] for r in rows]),
                        "occupied": g["occupied"], "inside": g["inside"], "invariant_ok": g["invariant_ok"],
                        "garage_execution": g["execution"], "effects": sql["effects"],
                        "in_flight_max": m.get("in_flight_max"), "effects_repeated": m.get("effects_repeated")})
    restart_api()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "compare.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"{RACE_GARAGE}: 5 spots, {args.clients} cars, {args.requests} requests, WORKERS=8, locked")
    print(f"{'execution':<10}{'req':>5}{'s':>7}{'occ':>5}{'inside':>7}{'inv_ok':>7}{'effects':>8}"
          f"{'total p50':>11}{'total p95':>11}{'proc p50':>10}{'queue p95':>11}  states")
    for r in results:
        print(f"{r['execution']:<10}{r['requests']:>5}{r['seconds']:>7}{r['occupied']:>5}{r['inside']:>7}"
              f"{str(r['invariant_ok']):>7}{r['effects']:>8}{r['total_ms']['p50']:>11}{r['total_ms']['p95']:>11}"
              f"{r['processing_ms']['p50']:>10}{r['queue_wait_ms']['p95']:>11}  {r['states']}")


# ------------------------------------------------------------------ O3.2


async def flood(clients: int, seconds: float) -> tuple[Counter, list[str], float]:
    statuses: Counter[int] = Counter()
    accepted: list[str] = []
    stop_at = time.monotonic() + seconds

    async def sender(s: aiohttp.ClientSession, n: int) -> None:
        plate, direction = f"INF{n:05d}", "in"
        while time.monotonic() < stop_at:
            try:
                async with s.post(f"{BASE}/v1/entries", json=body(RACE_GARAGE, plate, direction)) as r:
                    code = r.status
                    data = await r.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError):
                code, data = 0, {}
            statuses[code] += 1
            if code == 202:
                accepted.append(data["id"])
                direction = "out" if direction == "in" else "in"

    async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(limit=clients),
                                     headers={"Authorization": f"Bearer {TOKEN}"},
                                     timeout=aiohttp.ClientTimeout(total=30)) as s:
        t0 = time.monotonic()
        await asyncio.gather(*(sender(s, n) for n in range(clients)))
        return statuses, accepted, time.monotonic() - t0


def probe_main(seconds: float, out) -> None:
    """Separate process: /health 10/s, /metrics every 0.5 s, unfinished Ray tasks every 2 s."""
    u = urlsplit(BASE)
    hc = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    mc = http.client.HTTPConnection(u.hostname, u.port, timeout=5)
    health: list[float] = []
    samples: list[dict] = []
    ray_samples: list[dict] = []
    t0 = time.monotonic()
    nxt, next_m, next_r = t0, t0, t0
    while time.monotonic() < t0 + seconds + 2:
        t = time.perf_counter()
        try:
            hc.request("GET", "/health")
            hc.getresponse().read()
        except (OSError, http.client.HTTPException):
            hc.close()
        health.append((time.perf_counter() - t) * 1000)
        now = time.monotonic()
        if now >= next_m:
            mc.request("GET", "/metrics")
            m = json.loads(mc.getresponse().read())
            samples.append({"t": round(now - t0, 1), "queue_length": m["queue_length"], "in_flight": m["in_flight"],
                            "rejected": m["rejected"],
                            "finished": sum(m[f"finished_{s}"] for s in FINAL)})
            next_m += 0.5
        if now >= next_r:
            ray_samples.append({"t": round(now - t0, 1), "unfinished": unfinished_ray_tasks()})
            next_r += 2
        nxt += 0.1
        time.sleep(max(0.0, nxt - time.monotonic()))
    out.put((health, samples, ray_samples))


async def views(ids: list[str]) -> list[dict]:
    sem = asyncio.Semaphore(20)

    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {TOKEN}"}) as s:
        async def get(i: str) -> dict:
            async with sem, s.get(f"{BASE}/v1/entries/{i}") as r:
                return await r.json()
        return await asyncio.gather(*(get(i) for i in ids))


def one_inflight(delay: int, clients: int, seconds: float) -> dict:
    restart_api(EXECUTION="ray", RECEIVER_DELAY_MS=delay, IN_FLIGHT_LIMIT=8, QUEUE_CAPACITY=50, WORKERS=4)
    time.sleep(8)  # ray.init of the new api process
    reset_race_garage()
    asyncio.run(drive(40, 40))  # warm-up as in compare: a new driver gets new worker processes of Ray
    reset_race_garage()
    ctx = mp.get_context("spawn")
    q = ctx.Queue()
    probe = ctx.Process(target=probe_main, args=(seconds, q))
    m0 = metrics()
    probe.start()
    statuses, accepted, wall = asyncio.run(flood(clients, seconds))
    m1 = metrics()
    t_drain = time.monotonic()  # the load stopped: the queue (up to 50) is finished from here
    health, samples, ray_samples = q.get(timeout=seconds + 120)
    probe.join()
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        with db() as conn:
            open_ = conn.execute("SELECT count(*) AS n FROM entry_request WHERE id = ANY(%s) AND state IN "
                                 "('queued', 'processing')", (accepted,)).fetchone()["n"]
        if open_ == 0:
            break
        time.sleep(0.5)
    drain_s = round(time.monotonic() - t_drain, 1)
    rows = asyncio.run(views(accepted))
    m2 = metrics()
    fin = lambda m: sum(m[f"finished_{s}"] for s in FINAL)  # noqa: E731
    total = sum(statuses.values())
    in_window = [s for s in samples if s["t"] <= seconds]
    g = client().get(f"/v1/garage/{RACE_GARAGE}").json()
    return {
        "receiver_delay_ms": delay, "in_flight_limit": 8, "workers": 4, "queue_capacity": 50,
        "clients": clients, "seconds": round(wall, 1), "posts": total, "statuses": dict(statuses),
        "share_429": round(statuses[429] / total, 4) if total else None,
        "accepted": len(accepted),
        "finished_in_window": fin(m1) - fin(m0),
        "throughput_per_s": round((fin(m1) - fin(m0)) / wall, 2),
        "drain_after_load_s": drain_s,
        "states": dict(Counter(r["state"] for r in rows)),
        "total_ms": triple([r["total_ms"] for r in rows if r["total_ms"] is not None]),
        "queue_wait_ms": triple([r["queue_wait_ms"] for r in rows if r["queue_wait_ms"] is not None]),
        "processing_ms": triple([r["processing_ms"] for r in rows if r["processing_ms"] is not None]),
        "in_flight_max_samples": max(s["in_flight"] for s in samples),
        "in_flight_max_gauge": m2["in_flight_max"],
        "queue_length_max": max(s["queue_length"] for s in in_window),
        "queue_length_mean": round(sum(s["queue_length"] for s in in_window) / len(in_window), 1),
        "ray_unfinished_max": max((r["unfinished"] for r in ray_samples if r["unfinished"] is not None), default=None),
        "ray_samples": ray_samples,
        "health_ms": triple(health),
        "invariant_ok": g["invariant_ok"], "occupied": g["occupied"], "inside": g["inside"],
        "samples": samples,
    }


def cmd_inflight(args: argparse.Namespace) -> None:
    results = [one_inflight(int(d), args.clients, args.seconds) for d in args.delays.split(",")]
    if not args.keep:
        restart_api()
    OUT.mkdir(parents=True, exist_ok=True)
    name = args.out or "inflight.json"
    (OUT / name).write_text(json.dumps(results, indent=1), encoding="utf-8")
    print(f"EXECUTION=ray, IN_FLIGHT_LIMIT=8, WORKERS=4, QUEUE_CAPACITY=50; {args.clients} clients x "
          f"{args.seconds:.0f} s on {RACE_GARAGE}; /metrics every 0.5 s, Ray State API every 2 s")
    print(f"{'delay':>6}{'POST':>7}{'202':>6}{'429':>7}{'429 %':>7}{'thr/s':>7}{'total p50':>10}{'total p95':>10}"
          f"{'in_fl max':>10}{'queue max':>10}{'q mean':>7}{'ray max':>8}{'health p95':>11}{'inv':>6}")
    for r in results:
        print(f"{r['receiver_delay_ms']:>6}{r['posts']:>7}{r['statuses'].get(202, 0):>6}{r['statuses'].get(429, 0):>7}"
              f"{r['share_429'] * 100:>7.1f}{r['throughput_per_s']:>7}{r['total_ms']['p50']:>10}{r['total_ms']['p95']:>10}"
              f"{r['in_flight_max_samples']:>10}{r['queue_length_max']:>10}{r['queue_length_mean']:>7}"
              f"{str(r['ray_unfinished_max']):>8}{r['health_ms']['p95']:>11}{str(r['invariant_ok']):>6}")
    for r in results:
        print(f"delay {r['receiver_delay_ms']} ms: in_flight_max gauge {r['in_flight_max_gauge']}, states {r['states']}, "
              f"queue finished {r['drain_after_load_s']} s after the load, queue_wait p95 {r['queue_wait_ms']['p95']} ms, "
              f"processing p95 {r['processing_ms']['p95']} ms")
        print("  unfinished Ray tasks (t s: n): " + ", ".join(f"{s['t']}: {s['unfinished']}" for s in r["ray_samples"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("fault")
    c = sub.add_parser("compare")
    c.add_argument("--clients", type=int, default=40)
    c.add_argument("--requests", type=int, default=200)
    c.add_argument("--modes", default="local,ray")
    c.add_argument("--warmup", type=int, default=40)
    i = sub.add_parser("inflight")
    i.add_argument("--delays", default="0,500")
    i.add_argument("--clients", type=int, default=60)
    i.add_argument("--seconds", type=float, default=30)
    i.add_argument("--keep", action="store_true", help="leave the api with the last settings (for a screenshot)")
    i.add_argument("--out", default=None)
    args = ap.parse_args()
    {"fault": cmd_fault, "compare": cmd_compare, "inflight": cmd_inflight}[args.cmd](args)


if __name__ == "__main__":
    main()
