# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "httpx>=0.28",
#     "psycopg[binary]>=3.3",
# ]
# ///
"""DPC lab 6: transition of the role (O6.1), the paused old owner (O6.3) and the same without protection.

Needs: docker compose up -d api etcd coordinator-a coordinator-b (and postgres). From the repository root:

    uv run experiments/dpc_lr6.py transition [--repeats 5] | pause [--gap-ms 2000] | unfenced | restore

transition  O6.1: `docker compose stop <owner>` (SIGTERM: the owner revokes its lease) and `docker compose kill
            <owner>` (no revoke: the lease expires after the TTL), --repeats times each. The script polls etcd and the
            fence row of mall every 20 ms on the monotonic clock of the host from the moment it starts the command:
            t_owner - the role key holds the other coordinator (its role.acquired), t_registered - the fence of mall
            holds the new generation (its generation.registered on the resource). The log times of role.acquired and
            generation.registered of the new owner minus the wall time of the command are printed beside them.
            The stopped coordinator is started again after every repeat.
pause       O6.3: coordinator-a becomes the owner with WRITE_GAP_MS=<gap> (a pause between "tariff.prepared" and the
            write); right after its next tariff.prepared: docker compose pause coordinator-a; sleep 9; docker compose
            unpause coordinator-a. Expected: B took the role and registered a newer generation, the write of A with
            the old generation is rejected (tariff_history stale), A itself logs role.lost, /v1/history/check ok.
            --gap-ms 0 pauses at a random moment instead (the stale write then may or may not happen).
unfenced    the same pause on the test garage dpc-race with FENCING=off (api) and TARIFF_GARAGES=dpc-race
            (coordinators), dpc-race at 2 of 5 spots: A (rounds up) and B (rounds down) both write, different
            tariffs, /v1/history/check?garage_id=dpc-race ok = false. Restores FENCING=on, the coordinators and
            dpc-race (tariff, fence, history removed, occupied 0) afterwards; the results are saved first.
restore     api and coordinators as configured (FENCING=on, WRITE_GAP_MS 0, mall and center), dpc-race cleaned.
Outputs: results/dpc-lr6/<command>.json; the text goes to stdout (tee it to results/dpc-lr6/<command>.txt).
"""

import argparse
import base64
import json
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import psycopg
from psycopg.rows import dict_row

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "results" / "dpc-lr6"
BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
ETCD = os.environ.get("ETCD_BASE", "http://localhost:2379")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
ROLE_KEY = "/roles/tariff-manager"
COORDS = ("coordinator-a", "coordinator-b")
RACE = "dpc-race"
POLL_S = 0.02

api = httpx.Client(base_url=BASE, timeout=10, headers={"Authorization": f"Bearer {TOKEN}"})


def say(*parts: object) -> None:
    print(*parts, flush=True)


def db() -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)


def compose(*args: str, env: dict | None = None) -> str:
    e = {**os.environ, **(env or {}), "MSYS_NO_PATHCONV": "1"}
    p = subprocess.run(["docker", "compose", *args], cwd=ROOT, capture_output=True, text=True, env=e, check=True)
    return p.stdout + p.stderr


def role() -> tuple[str | None, int | None]:
    reply = httpx.post(f"{ETCD}/v3/kv/range", json={"key": base64.b64encode(ROLE_KEY.encode()).decode()},
                       timeout=2).json()
    kvs = reply.get("kvs", [])
    if not kvs:
        return None, None
    return base64.b64decode(kvs[0]["value"]).decode(), int(kvs[0]["create_revision"])


def fence(conn: psycopg.Connection, garage: str) -> tuple[str | None, int]:
    row = conn.execute("SELECT owner, generation FROM fence WHERE resource = %s", (f"tariff/{garage}",)).fetchone()
    return (row["owner"], row["generation"]) if row else (None, 0)


def logs(service: str, since: str) -> list[dict]:
    out = compose("logs", "--no-log-prefix", "--since", since, service)
    lines = []
    for line in out.splitlines():
        try:
            lines.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return lines


def wall(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def wait_owner(garage: str = "mall", timeout: float = 30) -> tuple[str, int]:
    """Until a coordinator holds the role and registered its generation for `garage`."""
    deadline = time.monotonic() + timeout
    with db() as conn:
        while time.monotonic() < deadline:
            owner, gen = role()
            if owner and fence(conn, garage) == (owner, gen):
                return owner, gen
            time.sleep(0.1)
    raise TimeoutError("no registered owner")


def make_owner(wanted: str, garage: str = "mall") -> int:
    """Hand the role to `wanted`: restart the other one (SIGTERM revokes its lease) while `wanted` waits."""
    for _ in range(5):
        owner, gen = wait_owner(garage)
        if owner == wanted:
            return gen
        compose("restart", owner)
        time.sleep(1)
    raise RuntimeError(f"{wanted} did not become the owner")


def save(name: str, data: dict) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ---------------------------------------------------------------- O6.1


def one_transition(action: str) -> dict:
    old, old_gen = wait_owner()
    new = COORDS[1] if old == COORDS[0] else COORDS[0]
    time.sleep(1.0)  # the waiting coordinator is in its watch
    wall0 = datetime.now(UTC)
    since = wall0.isoformat()
    t0 = time.monotonic()
    done: dict[str, float] = {}

    def run() -> None:
        compose(action, old)
        done["cmd"] = time.monotonic() - t0

    threading.Thread(target=run, daemon=True).start()
    t_owner = t_registered = None
    new_gen = None
    with db() as conn:
        while time.monotonic() - t0 < 30 and t_registered is None:
            owner, gen = role()
            now = time.monotonic() - t0
            if t_owner is None and owner == new:
                t_owner, new_gen = now, gen
            if t_owner is not None and fence(conn, "mall") == (new, new_gen):
                t_registered = time.monotonic() - t0
            time.sleep(POLL_S)
    while "cmd" not in done:
        time.sleep(0.05)
    events = {e["event"]: e for e in logs(new, since) if e["event"] in ("role.acquired", "generation.registered")}
    log_acq = (wall(events["role.acquired"]["time"]) - wall0).total_seconds() if "role.acquired" in events else None
    log_reg = ((wall(events["generation.registered"]["time"]) - wall0).total_seconds()
               if "generation.registered" in events else None)
    old_lines = [e for e in logs(old, since) if e["event"] in ("role.lost", "role.released")]
    compose("start", old)
    r = {"action": action, "old": old, "old_generation": old_gen, "new": new, "new_generation": new_gen,
         "command_s": round(done["cmd"], 3),
         "to_role_acquired_s": None if t_owner is None else round(t_owner, 3),
         "to_registered_s": None if t_registered is None else round(t_registered, 3),
         "log_role_acquired_s": None if log_acq is None else round(log_acq, 3),
         "log_generation_registered_s": None if log_reg is None else round(log_reg, 3),
         "old_owner_log": [e["event"] + (f" ({e['reason']})" if "reason" in e else "") for e in old_lines]}
    say(f"  {action:4} {old} gen {old_gen} -> {new} gen {new_gen}: role.acquired +{r['to_role_acquired_s']} s "
        f"(log {r['log_role_acquired_s']}), registered +{r['to_registered_s']} s "
        f"(log {r['log_generation_registered_s']}), command {r['command_s']} s; old: {r['old_owner_log']}")
    time.sleep(3)  # the restarted one is back in its watch
    return r


def transition(repeats: int) -> None:
    say(f"O6.1: role transition, LEASE_TTL_S=5, renew every 1.5 s, poll {POLL_S * 1000:.0f} ms, {repeats} repeats")
    say("from: the command docker compose stop|kill <owner> is started (host monotonic clock)")
    say("to:   role key holds the other coordinator (= its role.acquired); fence of mall holds its generation")
    result: dict = {"repeats": repeats, "poll_s": POLL_S, "runs": []}
    for action in ("stop", "kill"):
        say(f"\n{action}:")
        runs = [one_transition(action) for _ in range(repeats)]
        result["runs"] += runs
        for field in ("to_role_acquired_s", "to_registered_s", "log_role_acquired_s", "command_s"):
            values = [r[field] for r in runs if r[field] is not None]
            if values:
                result.setdefault("median", {}).setdefault(action, {})[field] = round(statistics.median(values), 3)
        m = result["median"][action]
        say(f"  median: role.acquired {m['to_role_acquired_s']} s, registered {m['to_registered_s']} s, "
            f"log role.acquired {m.get('log_role_acquired_s')} s, command {m['command_s']} s")
    save("transition", result)


# ---------------------------------------------------------------- O6.3


def pause_scenario(gap_ms: int, garages: str, garage: str) -> dict:
    """coordinator-a owns the role (WRITE_GAP_MS=gap_ms); pause it right after its tariff.prepared, sleep 9, unpause."""
    env = {"WRITE_GAP_MS_A": str(gap_ms), "TARIFF_GARAGES": garages}
    compose("up", "-d", "--force-recreate", "--no-deps", "coordinator-a", "coordinator-b", env=env)
    time.sleep(2)
    gen_a = make_owner("coordinator-a", garage)
    with db() as conn:
        mark = conn.execute("SELECT coalesce(max(id), 0) AS m FROM tariff_history").fetchone()["m"]
    since = datetime.now(UTC).isoformat()
    say(f"  coordinator-a owns the role, generation {gen_a}; WRITE_GAP_MS={gap_ms}; history after id {mark}")

    if gap_ms:
        # docker logs (not docker compose logs: its plugin child would outlive kill() and hold the output pipe)
        container = compose("ps", "-q", "coordinator-a").split()[0]
        follow = subprocess.Popen(["docker", "logs", "-f", "--tail", "0", container], cwd=ROOT,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        for line in follow.stdout:
            if '"tariff.prepared"' in line:
                break
        follow.kill()
        follow.wait()
    t0 = time.monotonic()
    compose("pause", "coordinator-a")
    paused_at = datetime.now(UTC)
    say(f"  paused coordinator-a ({time.monotonic() - t0:.2f} s for the command); sleep 9")
    time.sleep(9)
    owner_during, gen_during = role()
    compose("unpause", "coordinator-a")
    say(f"  unpaused; during the pause the role was held by {owner_during}, generation {gen_during}")
    time.sleep(5)

    a_log = [e for e in logs("coordinator-a", since) if e["event"] not in ("tariff.prepared",)]
    b_log = [e for e in logs("coordinator-b", since)
             if e["event"] in ("role.acquired", "generation.registered", "role.lost")]
    with db() as conn:
        rows = conn.execute(
            """SELECT id, garage_id, minor_per_hour, owner, generation, accepted, reason, created_at
               FROM tariff_history WHERE id > %s AND garage_id = %s ORDER BY id""", (mark, garage)).fetchall()
    check = api.get("/v1/history/check", params={"garage_id": garage}).json()
    tariff = api.get("/v1/tariff").json()["items"] if garage != RACE else None
    return {"gap_ms": gap_ms, "garage": garage, "generation_a": gen_a, "paused_at": paused_at.isoformat(),
            "owner_during_pause": owner_during, "generation_during_pause": gen_during,
            "coordinator_a": a_log, "coordinator_b": b_log, "history": rows, "check": check, "tariff": tariff}


def show(r: dict) -> None:
    after = [e for e in r["coordinator_a"] if wall(e["time"]) > wall(r["paused_at"])]
    say("  coordinator-a after the pause:")
    for e in after[:6]:
        extra = {k: v for k, v in e.items() if k not in ("time", "instance", "event")}
        say(f"    {e['time'][11:23]}  {e['event']}  {json.dumps(extra, ensure_ascii=False)}")
    say("  coordinator-b:")
    for e in r["coordinator_b"]:
        extra = {k: v for k, v in e.items() if k not in ("time", "instance", "event", "role", "lease")}
        say(f"    {e['time'][11:23]}  {e['event']}  {json.dumps(extra, ensure_ascii=False)}")
    say(f"  tariff_history of {r['garage']} after the mark (id, owner, generation, tariff, accepted, reason):")
    interesting = [h for h in r["history"] if h["reason"] != "accepted" or h["owner"] == "coordinator-a"]
    shown = interesting + [h for h in r["history"] if h not in interesting][:4]
    for h in sorted(shown, key=lambda h: h["id"]):
        say(f"    {h['id']:>6}  {h['created_at'].isoformat()[11:23]}  {h['owner']}  gen {h['generation']}  "
            f"{h['minor_per_hour']}  {h['accepted']}  {h['reason']}")
    c = r["check"]
    say(f"  /v1/history/check?garage_id={r['garage']}: ok={c['ok']} writes={c['writes']} rejected={c['rejected']} "
        f"registrations={c['registrations']} violations={json.dumps(c['violations'], ensure_ascii=False)[:400]}")


def pause(gap_ms: int) -> None:
    say("O6.3: pause of the owner coordinator-a for 9 s (LEASE_TTL_S=5), FENCING=on, garages mall,center")
    r = pause_scenario(gap_ms, "mall,center", "mall")
    show(r)
    full = api.get("/v1/history/check").json()
    say(f"  /v1/history/check (all garages): ok={full['ok']} writes={full['writes']} rejected={full['rejected']}")
    r["check_all"] = full
    compose("up", "-d", "--force-recreate", "--no-deps", "coordinator-a", "coordinator-b")
    save("pause" if gap_ms else "pause-random", r)


# ---------------------------------------------------------------- without protection


def prepare_race() -> None:
    with db() as conn:
        conn.execute("DELETE FROM tariff_history WHERE garage_id = %s", (RACE,))
        conn.execute("DELETE FROM fence WHERE resource = %s", (f"tariff/{RACE}",))
        conn.execute("INSERT INTO tariff (garage_id, minor_per_hour) VALUES (%s, 2750) ON CONFLICT (garage_id) "
                     "DO UPDATE SET minor_per_hour = 2750, generation = 0, owner = NULL", (RACE,))
        conn.execute("UPDATE garage_state SET occupied = 2 WHERE garage_id = %s", (RACE,))


def clean_race() -> None:
    with db() as conn:
        conn.execute("DELETE FROM tariff_history WHERE garage_id = %s", (RACE,))
        conn.execute("DELETE FROM fence WHERE resource = %s", (f"tariff/{RACE}",))
        conn.execute("DELETE FROM tariff WHERE garage_id = %s", (RACE,))
        conn.execute("UPDATE garage_state SET occupied = 0 WHERE garage_id = %s", (RACE,))


def wait_api() -> None:
    for _ in range(120):
        try:
            if httpx.get(f"{BASE}/health", timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    raise TimeoutError("api")


def restore() -> None:
    compose("up", "-d", "--force-recreate", "--no-deps", "api")
    wait_api()
    compose("up", "-d", "--force-recreate", "--no-deps", "coordinator-a", "coordinator-b")
    clean_race()
    owner, gen = wait_owner()
    say(f"restored: FENCING=on, coordinators on mall,center; owner {owner}, generation {gen}; dpc-race cleaned")


def unfenced(gap_ms: int) -> None:
    say("Without protection: FENCING=off (only dpc-race skips the check), TARIFF_GARAGES=dpc-race, "
        "dpc-race 2 of 5 spots, base 2750: A rounds 3850 up to 3900, B down to 3800")
    prepare_race()
    compose("up", "-d", "--force-recreate", "--no-deps", "api", env={"FENCING": "off"})
    wait_api()
    try:
        r = pause_scenario(gap_ms, RACE, RACE)
        show(r)
        accepted = {(h["owner"], h["minor_per_hour"]) for h in r["history"] if h["accepted"] and
                    h["reason"] != "registered"}
        say(f"  accepted writes (owner, tariff): {sorted(accepted)}")
        r["accepted_pairs"] = sorted(accepted)
        save("unfenced", r)
    finally:
        restore()


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("command", choices=["transition", "pause", "unfenced", "restore"])
    p.add_argument("--repeats", type=int, default=5)
    p.add_argument("--gap-ms", type=int, default=2000)
    a = p.parse_args()
    {"transition": lambda: transition(a.repeats), "pause": lambda: pause(a.gap_ms),
     "unfenced": lambda: unfenced(a.gap_ms), "restore": restore}[a.command]()


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
