"""DPC lab 1, O1.4: the garage without synchronization and with the lock, the same scenario.

For each mode the api is recreated with WORKERS=8 and RESOURCE_MODE=unsafe or locked (unsafe changes
ONLY the test garage dpc-race, 5 spots), dpc-race is reset (occupied 0, no entries), then
--clients (40) cars drive in and out: a car sends an entry, waits for its final state, and after an
accepted entry sends an exit, until --requests (200) requests in total are sent. A 429 is repeated
with the same event_id. Afterwards GET /v1/garage/dpc-race and SQL show occupied, inside and the
invariant. The api is left in the default mode (locked, WORKERS=4) at the end.

    uv run --no-project --python 3.13 --with httpx --with aiohttp --with "psycopg[binary]" python experiments/dpc_lr1_race.py

Writes results/dpc-lr1/race.json and prints the table.
"""

import argparse
import asyncio
import json
import time
from pathlib import Path

import aiohttp
from dpc_common import BASE, RACE_GARAGE, TOKEN, FINAL, body, client, db, reset_race_garage, restart_api

OUT = Path(__file__).resolve().parents[1] / "results" / "dpc-lr1"


async def drive(clients: int, total: int) -> dict:
    sent = 0
    states: dict[str, int] = {}

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
        key = f"{direction}:{data['state']}" + (f":{data['reason']}" if data["reason"] else "")
        states[key] = states.get(key, 0) + 1
        return data

    async def car(s: aiohttp.ClientSession, n: int) -> None:
        nonlocal sent
        plate = f"RACE{n:03d}"
        while sent < total:
            sent += 1
            if (await one(s, plate, "in"))["state"] == "done" and sent < total:
                sent += 1
                await one(s, plate, "out")

    async with aiohttp.ClientSession(headers={"Authorization": f"Bearer {TOKEN}"},
                                     connector=aiohttp.TCPConnector(limit=clients * 2)) as s:
        await asyncio.gather(*(car(s, n) for n in range(clients)))
    return dict(sorted(states.items()))


def run_mode(mode: str, clients: int, total: int) -> dict:
    restart_api(RESOURCE_MODE=mode, WORKERS=8, QUEUE_CAPACITY=50)
    reset_race_garage()
    t0 = time.monotonic()
    states = asyncio.run(drive(clients, total))
    wall = time.monotonic() - t0
    g = client().get(f"/v1/garage/{RACE_GARAGE}").json()
    with db() as conn:
        sql = conn.execute(
            """SELECT gs.occupied, gs.spots,
                      (SELECT count(*) FROM entry WHERE garage_id = %(g)s AND direction = 'in') AS entries,
                      (SELECT count(*) FROM entry WHERE garage_id = %(g)s AND direction = 'out') AS exits,
                      (SELECT count(*) FROM entry_request WHERE garage_id = %(g)s) AS requests
               FROM garage_state gs WHERE gs.garage_id = %(g)s""",
            {"g": RACE_GARAGE},
        ).fetchone()
    return {"mode": mode, "workers": 8, "seconds": round(wall, 1), "states": states,
            "occupied": g["occupied"], "inside": g["inside"], "spots": g["spots"],
            "invariant_ok": g["invariant_ok"], "garage_mode": g["mode"],
            "entries": sql["entries"], "exits": sql["exits"], "requests": sql["requests"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--clients", type=int, default=40)
    ap.add_argument("--requests", type=int, default=200)
    ap.add_argument("--modes", default="unsafe,locked")
    args = ap.parse_args()
    results = [run_mode(m, args.clients, args.requests) for m in args.modes.split(",")]
    restart_api()  # back to the defaults of compose.yaml / .env
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "race.json").write_text(json.dumps(results, indent=2), encoding="utf-8")

    print(f"{RACE_GARAGE}: 5 spots, {args.clients} cars, {args.requests} requests, WORKERS=8")
    print(f"{'mode':<8}{'requests':>9}{'entries':>8}{'exits':>7}{'occupied':>9}{'inside':>7}{'invariant_ok':>13}  states")
    for r in results:
        print(f"{r['mode']:<8}{r['requests']:>9}{r['entries']:>8}{r['exits']:>7}{r['occupied']:>9}{r['inside']:>7}"
              f"{str(r['invariant_ok']):>13}  {r['states']}")


if __name__ == "__main__":
    main()
