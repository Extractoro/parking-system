"""DPC lab 1, D1.4: SIGTERM with a full queue.

The api is recreated with the defaults (QUEUE_CAPACITY=50, WORKERS=4). --fill (80) POSTs to the test
garage dpc-race at once fill the queue (the rest get 429). Right after them `docker compose stop api`
sends SIGTERM (stop_grace_period 120 s); meanwhile a probe sends a new POST every 100 ms and records
the answers (503 + Retry-After while the queue drains) until the port closes. Then the log lines
api.draining / api.drained / api.stopped, the exit of the container, and the states of every accepted
request in the database are printed; after `docker compose start api` the states are read again
(a request lost by the process would now be failed/lost-on-restart).

    uv run --no-project --python 3.13 --with httpx --with aiohttp --with "psycopg[binary]" python experiments/dpc_lr1_drain.py
"""

import argparse
import asyncio
import json
import subprocess
import threading
import time
from collections import Counter
from pathlib import Path

import aiohttp
from dpc_common import BASE, RACE_GARAGE, TOKEN, body, compose, db, reset_race_garage, restart_api, wait_health

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr1"


async def fill(n: int) -> tuple[list[str], Counter]:
    codes: Counter[int] = Counter()
    ids: list[str] = []
    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {TOKEN}"},
                                     connector=aiohttp.TCPConnector(limit=n)) as s:
        async def one(i: int) -> None:
            async with s.post(f"{BASE}/v1/entries", json=body(RACE_GARAGE, f"DRN{i:04d}", "in")) as r:
                codes[r.status] += 1
                if r.status == 202:
                    ids.append((await r.json())["id"])
        await asyncio.gather(*(one(i) for i in range(n)))
    return ids, codes


def states(ids: list[str]) -> dict:
    with db() as conn:
        rows = conn.execute(
            "SELECT state, coalesce(reason, '-') AS reason, count(*) AS n FROM entry_request WHERE id = ANY(%s) "
            "GROUP BY state, reason ORDER BY state, reason", (ids,)
        ).fetchall()
    return {f"{r['state']}/{r['reason']}": r["n"] for r in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fill", type=int, default=80)
    args = ap.parse_args()
    restart_api()
    reset_race_garage()
    since = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(time.time() - 1))

    ids, codes = asyncio.run(fill(args.fill))
    print(f"fill: {args.fill} POSTs at once -> " + ", ".join(f"{k} x {v}" for k, v in sorted(codes.items())))
    m = json.loads(subprocess.run(["curl", "-s", f"{BASE}/metrics"], capture_output=True, text=True).stdout)
    print(f"before SIGTERM: queue_length {m['queue_length']}, in_flight {m['in_flight']}")

    stop_done = threading.Event()
    stop_out: list[str] = []
    t_stop = time.monotonic()

    def stopper() -> None:
        stop_out.append(compose("stop", "api", check=False))
        stop_done.set()

    threading.Thread(target=stopper).start()
    probes: list[tuple[float, str]] = []
    import http.client
    from urllib.parse import urlsplit
    u = urlsplit(BASE)
    n = 0
    while not stop_done.is_set():
        n += 1
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=3)
        try:
            conn.request("POST", "/v1/entries", json.dumps(body(RACE_GARAGE, f"LATE{n:04d}", "in")),
                         {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"})
            r = conn.getresponse()
            data = r.read()
            if r.status == 202:  # accepted before SIGTERM reached the process: must be finished too
                ids.append(json.loads(data)["id"])
            ans = f"{r.status} Retry-After={r.getheader('Retry-After')}" if r.status != 202 else "202"
        except (OSError, http.client.HTTPException) as e:
            ans = f"no answer ({type(e).__name__})"
        finally:
            conn.close()
        probes.append((round(time.monotonic() - t_stop, 2), ans))
        time.sleep(0.1)
    stop_s = round(time.monotonic() - t_stop, 1)

    logs = compose("logs", "api", "--since", since, "--no-log-prefix", check=False)
    lines = [ln for ln in logs.splitlines() if any(k in ln for k in ('"api.draining"', '"api.drained"', '"api.stopped"',
                                                                        "Shutting down", "Finished server process"))]
    exit_code = subprocess.run(["docker", "inspect", "parking-system-api-1", "--format", "{{.State.ExitCode}}"],
                               capture_output=True, text=True).stdout.strip()
    before_start = states(ids)
    compose("start", "api")
    wait_health()
    after_start = states(ids)

    kinds = Counter(a for _, a in probes)
    print(f"docker compose stop api: {stop_s} s; container exit code {exit_code}")
    print(f"probe POSTs during the stop ({len(probes)}): " + ", ".join(f"{k} x {v}" for k, v in kinds.items()))
    if probes:
        print(f"  first {probes[0]}, last {probes[-1]}")
    print("log:")
    for ln in lines:
        print("  " + ln)
    print(f"accepted {len(ids)} (fill + probe answers 202): states while stopped {before_start}")
    print(f"accepted {len(ids)}: states after docker compose start api {after_start}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "drain.json").write_text(json.dumps({"fill": dict(codes), "accepted": len(ids), "stop_s": stop_s,
                                                "exit_code": exit_code, "probes": probes, "log": lines,
                                                "states_stopped": before_start, "states_restarted": after_start},
                                               indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
