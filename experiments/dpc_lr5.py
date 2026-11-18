# /// script
# requires-python = ">=3.14"
# dependencies = [
#     "confluent-kafka>=2.16",
#     "httpx>=0.28",
#     "psycopg[binary]>=3.3",
# ]
# ///
"""DPC lab 5: the main run (O5.2, O5.5), duplicates (O5.1), restart from the checkpoint (O5.4), lateness (O5.3).

Needs: docker compose up -d api spark-master spark-worker-1 spark-worker-2 broker postgres and the event sets
in the data volume (docker compose run --rm --entrypoint sh generator /tools/generator/events_c09.sh).
Every run goes through the service `stream` itself: the script recreates it with STREAM_TOPIC, STREAM_RUN_ID,
STREAM_EVENTS_FILE, STREAM_WATERMARK_MINUTES, TRACK_EVENTS (compose.yaml), so the failure of O5.4 is literally
`docker compose kill stream`. An independent run first gets an empty topic barrier-events-<run> (1 partition),
no checkpoint /data/checkpoints/<run> and no rows of <run>. The events are sent from the host
(localhost:9094) in file order, key garage_id, like dpc/tools/jsonl_to_kafka.py, and the send time of every
event is kept (converted to the clock of PostgreSQL, which writes panel_window.written_at).
From the repository root:

    uv run experiments/dpc_lr5.py main | control | dedup | restart | predict | lateness | restore

main      O5.2/O5.5: normal.jsonl -> barrier-events, run main (the service as configured); /v1/panel vs /v1/panel/batch
control   the control example of the kit (control.jsonl, 15-minute windows) through the stream = expected.json
dedup     O5.1: normal.jsonl and duplicates.jsonl on isolated runs dedup-normal, dedup-dups; rows must be equal
restart   O5.4: run restart-ref without failure; run restart-kill: half of the events, docker compose kill stream,
          docker compose up -d stream (same checkpoint), the rest; rows of both runs and of the batch must be equal
predict   O5.3 before the runs: lateness of every event of late.jsonl at its arrival -> expected drops per watermark
lateness  O5.3: late.jsonl with watermark 2 and 5 min (TRACK_EVENTS=1, the same --pace-ms): counted and dropped
          events, the dropped event_id, the delay of the result per window
restore   the service stream back to the main run (no overrides)
Outputs: results/dpc-lr5/<command>.json; the text goes to stdout (tee it to results/dpc-lr5/<command>.txt).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dpc" / "api"))
from app.panel_batch import compute, epoch_ms, iso

BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
DB_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://app:app@localhost:5432/app")
BOOTSTRAP = os.environ.get("KAFKA_EXTERNAL", "localhost:9094")
OUT = Path("results/dpc-lr5")
LOCAL = Path(tempfile.gettempdir()) / "dpc-lr5-events"
KEYS = ("garage_id", "window_start", "window_end", "entries", "exits")
KAFKA_TOPICS = "/opt/kafka/bin/kafka-topics.sh"


# ---------------------------------------------------------------------------------------------- plumbing


def sh(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    p = subprocess.run(args, capture_output=True, text=True, encoding="utf-8",
                       env={**os.environ, **(env or {}), "MSYS_NO_PATHCONV": "1"}, check=False)
    if check and p.returncode:
        raise RuntimeError(f"{' '.join(args)}: {p.returncode}\n{p.stdout}\n{p.stderr}")
    return p.stdout + p.stderr


def compose(*args: str, env: dict[str, str] | None = None, check: bool = True) -> str:
    return sh("docker", "compose", *args, env=env, check=check)


def db() -> psycopg.Connection:
    return psycopg.connect(DB_URL, autocommit=True, row_factory=dict_row)


def api() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=30, headers={"Authorization": f"Bearer {TOKEN}"})


def stamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def events_file(name: str) -> Path:
    """A local copy of /data/events/<name>.jsonl (the volume is not visible from the host)."""
    LOCAL.mkdir(parents=True, exist_ok=True)
    local = LOCAL / f"{name}.jsonl"
    compose("cp", f"api:/data/events/{name}.jsonl", str(local))
    return local


def lines_of(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def db_clock_offset() -> float:
    """PostgreSQL clock minus host clock, seconds (the midpoint of 5 round trips, the shortest one kept)."""
    best = None
    with db() as conn:
        for _ in range(5):
            t0 = time.time()
            server = conn.execute("SELECT extract(epoch FROM clock_timestamp()) AS t").fetchone()["t"]
            t1 = time.time()
            if best is None or t1 - t0 < best[0]:
                best = (t1 - t0, float(server) - (t0 + t1) / 2)
    return best[1]


def offsets(topic: str) -> int:
    out = compose("exec", "-T", "broker", "/opt/kafka/bin/kafka-get-offsets.sh", "--bootstrap-server",
                  "broker:9092", "--topic", topic, check=False)
    return sum(int(line.rsplit(":", 1)[1]) for line in out.splitlines() if line.startswith(f"{topic}:"))


def fresh_topic(topic: str) -> None:
    """Delete and create the topic with one partition (an independent run starts from an empty input)."""
    compose("exec", "-T", "broker", KAFKA_TOPICS, "--bootstrap-server", "broker:9092", "--delete",
            "--if-exists", "--topic", topic)
    for _ in range(60):
        out = compose("exec", "-T", "broker", KAFKA_TOPICS, "--bootstrap-server", "broker:9092", "--create",
                      "--topic", topic, "--partitions", "1", "--replication-factor", "1", check=False)
        if "Created topic" in out:
            return
        time.sleep(1)
    raise RuntimeError(f"cannot create {topic}: {out}")


def reset_run(run: str) -> None:
    """No rows, no checkpoint, an empty topic of its own."""
    with db() as conn:
        for table in ("panel_event", "panel_window", "panel_run"):
            conn.execute(f"DELETE FROM {table} WHERE run_id = %s", (run,))
    compose("exec", "-T", "spark-master", "rm", "-rf", f"/data/checkpoints/{run}")
    fresh_topic(f"barrier-events-{run}")


def stream_env(run: str, name: str, watermark: int = 5, track: bool = False, window: int = 5) -> dict[str, str]:
    return {"STREAM_RUN_ID": run, "STREAM_TOPIC": f"barrier-events-{run}",
            "STREAM_EVENTS_FILE": f"/data/events/{name}.jsonl", "STREAM_WATERMARK_MINUTES": str(watermark),
            "TRACK_EVENTS": "1" if track else "0", "WINDOW_MINUTES": str(window)}


def start_stream(run: str, env: dict[str, str] | None, settle_s: float = 15) -> None:
    """(Re)create the service stream with the environment of the run and wait until the query runs."""
    since = stamp()
    compose("up", "-d", "--no-deps", "stream", env=env)
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        if f"stream run={run} " in compose("logs", "--since", since, "stream"):
            time.sleep(settle_s)  # executors registered, the query started
            return
        time.sleep(1)
    raise RuntimeError(f"stream run {run} did not start")


def send(lines: list[str], topic: str, pace_ms: float, offset: float) -> dict[str, float]:
    """Produce in file order (key garage_id); event_id -> send time on the clock of PostgreSQL (epoch s).

    A repeated event_id keeps its first send time."""
    from confluent_kafka import Producer

    producer = Producer({"bootstrap.servers": BOOTSTRAP, "acks": "all", "enable.idempotence": True})
    errors: list[str] = []
    sent: dict[str, float] = {}

    def done(error: Any, _message: Any) -> None:
        if error is not None:
            errors.append(str(error))

    for line in lines:
        event = json.loads(line)
        sent.setdefault(event["event_id"], time.time() + offset)
        producer.produce(topic, key=event["garage_id"], value=line, on_delivery=done)
        producer.poll(0)
        if pace_ms:
            time.sleep(pace_ms / 1000)
    pending = producer.flush(30)
    if errors or pending:
        raise RuntimeError(f"send to {topic}: {len(errors)} errors, {pending} pending: {errors[:3]}")
    return sent


def wait_rows(run: str, expected: int, quiet_s: float = 12, timeout: float = 240) -> list[dict[str, Any]]:
    """Wait until the run has at least `expected` windows and nothing was written for quiet_s seconds."""
    deadline = time.monotonic() + timeout
    last = None
    since = time.monotonic()
    while time.monotonic() < deadline:
        with db() as conn:
            state = conn.execute("SELECT count(*) AS n, max(written_at) AS w FROM panel_window WHERE run_id = %s",
                                 (run,)).fetchone()
        key = (state["n"], state["w"])
        if key != last:
            last, since = key, time.monotonic()
        elif state["n"] >= expected and time.monotonic() - since >= quiet_s:
            return rows(run)
        time.sleep(1)
    print(f"  ! run {run}: {last} after {timeout} s (expected {expected} windows)")
    return rows(run)


def rows(run: str) -> list[dict[str, Any]]:
    with db() as conn:
        found = conn.execute(
            """SELECT garage_id, window_start, window_end, entries, exits, batch_id, written_at
                 FROM panel_window WHERE run_id = %s ORDER BY window_start, garage_id""", (run,)).fetchall()
    return [{**r, "window_start": iso(r["window_start"]), "window_end": iso(r["window_end"])} for r in found]


def keyed(windows: list[dict[str, Any]]) -> list[tuple]:
    return [tuple(w[k] for k in KEYS) for w in windows]


def diff(a: list[dict[str, Any]], b: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Windows whose values differ (or that one side lacks), by (garage_id, window_start)."""
    left = {(w["garage_id"], w["window_start"]): w for w in a}
    right = {(w["garage_id"], w["window_start"]): w for w in b}
    out = []
    for key in sorted(left.keys() | right.keys(), key=lambda k: (k[1], k[0])):
        lw, rw = left.get(key), right.get(key)
        if lw is None or rw is None or (lw["entries"], lw["exits"]) != (rw["entries"], rw["exits"]):
            out.append({"garage_id": key[0], "window_start": key[1],
                        "a": lw and [lw["entries"], lw["exits"]], "b": rw and [rw["entries"], rw["exits"]]})
    return out


def batch_logs(since: str) -> list[str]:
    return [line.split("| ", 1)[-1] for line in compose("logs", "--since", since, "stream").splitlines()
            if " batch " in line or line.split("| ", 1)[-1].startswith("batch ") or "stream run=" in line]


def save(name: str, result: dict[str, Any]) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str) + "\n",
                                      encoding="utf-8")


def independent_run(run: str, name: str, pace_ms: float, watermark: int = 5, track: bool = False,
                    window: int = 5) -> dict[str, Any]:
    """Reset, start the stream with the run's environment, send the file, wait for the windows."""
    path = events_file(name)
    lines = lines_of(path)
    batch = compute(lines, window, watermark)
    reset_run(run)
    start_stream(run, stream_env(run, name, watermark, track, window))
    offset = db_clock_offset()
    t0 = time.monotonic()
    sent = send(lines, f"barrier-events-{run}", pace_ms, offset)
    send_s = time.monotonic() - t0
    got = wait_rows(run, len(batch["windows"]))
    return {"run": run, "file": name, "watermark_minutes": watermark, "pace_ms": pace_ms, "lines": len(lines),
            "send_s": round(send_s, 1), "clock_offset_s": round(offset, 3), "batch": batch, "rows": got,
            "sent": sent, "lines_list": lines}


# ---------------------------------------------------------------------------------------------- commands


def cmd_main(args: argparse.Namespace) -> None:
    topic = "barrier-events"
    already = offsets(topic)
    if already and not args.force:
        raise SystemExit(f"{topic} already holds {already} messages; the main run was fed (use --force to resend)")
    path = events_file("normal")
    lines = lines_of(path)
    start_stream("main", None)
    offset = db_clock_offset()
    print(f"main: {len(lines)} lines of normal.jsonl -> {topic}, --pace-ms {args.pace_ms}")
    send(lines, topic, args.pace_ms, offset)
    batch_local = compute(lines, 5, 5)
    wait_rows("main", len(batch_local["windows"]))
    with api() as c:
        panel = c.get("/v1/panel", params={"run": "main"}).json()
        batch = c.get("/v1/panel/batch", params={"run": "main"}).json()
    differences = diff(panel["windows"], batch["windows"])
    print(f"  /v1/panel: {len(panel['windows'])} windows; /v1/panel/batch: {len(batch['windows'])} windows "
          f"(events {batch['events']}, ticks {batch['ticks']}, watermark {batch['watermark']})")
    print(f"  equal by keys and values: {keyed(panel['windows']) == keyed(batch['windows'])}; "
          f"differences: {len(differences)}")
    print(f"  entries + exits: stream {sum(w['entries'] + w['exits'] for w in panel['windows'])}, "
          f"batch {sum(w['entries'] + w['exits'] for w in batch['windows'])}")
    for w in panel["windows"][:4]:
        print("   ", {k: w[k] for k in (*KEYS, "batch_id", "written_at")})
    save("main", {"panel": panel, "batch": batch, "differences": differences})


def cmd_control(args: argparse.Namespace) -> None:
    """The control example of the kit (15-minute windows, key renamed to garage_id) through the stream itself."""
    expected = json.loads((Path(__file__).resolve().parents[1] / "dpc" / "tools" / "generator" / "control"
                           / "expected.json").read_text(encoding="utf-8"))
    r = independent_run("control", "control", args.pace_ms, window=15)
    want = sorted((e["window_start"], e["gate_id"], e["window_end"], e["in"], e["out"]) for e in expected)
    got = [(w["window_start"], w["garage_id"], w["window_end"], w["entries"], w["exits"]) for w in r["rows"]]
    print(f"control: {r['lines']} lines, window 15 min, watermark 5 min; stream rows:")
    for row in got:
        print("   ", row)
    print(f"  equal to expected.json of the kit: {got == want}; equal to the batch calculation: "
          f"{keyed(r['rows']) == keyed(r['batch']['windows'])}")
    save("control", {"rows": r["rows"], "expected": expected, "equal": got == want})


def cmd_dedup(args: argparse.Namespace) -> None:
    results = {}
    for run, name in (("dedup-normal", "normal"), ("dedup-dups", "duplicates")):
        r = independent_run(run, name, args.pace_ms)
        results[run] = r
        print(f"{run}: {r['lines']} lines of {name}.jsonl ({r['batch']['duplicates']} repeated event_id), "
              f"sent in {r['send_s']} s; {len(r['rows'])} windows, entries + exits "
              f"{sum(w['entries'] + w['exits'] for w in r['rows'])}; equal to the batch calculation: "
              f"{keyed(r['rows']) == keyed(r['batch']['windows'])}")
    a, b = results["dedup-normal"]["rows"], results["dedup-dups"]["rows"]
    print(f"dedup-normal == dedup-dups by keys and values: {keyed(a) == keyed(b)}; differences: {len(diff(a, b))}")
    save("dedup", {run: {k: v for k, v in r.items() if k not in ("sent", "lines_list")}
                   for run, r in results.items()} | {"equal": keyed(a) == keyed(b)})


def checkpoint_listing(run: str) -> str:
    return compose("exec", "-T", "spark-master", "sh", "-c",
                   f"cd /data/checkpoints/{run} && echo offsets: $(ls offsets | sort -n) && "
                   f"echo commits: $(ls commits | sort -n)", check=False).strip()


def cmd_restart(args: argparse.Namespace) -> None:
    ref = independent_run("restart-ref", "normal", args.pace_ms)
    print(f"restart-ref (no failure): {len(ref['rows'])} windows, sent in {ref['send_s']} s")

    run = "restart-kill"
    lines = ref["lines_list"]
    half = len(lines) // 2
    reset_run(run)
    env = stream_env(run, "normal")
    since = stamp()
    start_stream(run, env)
    offset = db_clock_offset()
    send(lines[:half], f"barrier-events-{run}", args.pace_ms, offset)
    time.sleep(args.kill_after_s)
    before = rows(run)
    log_before = batch_logs(since)
    killed_at = stamp()
    print(f"{run}: sent {half} of {len(lines)} lines; {len(before)} windows written; "
          f"docker compose kill stream at {killed_at}")
    compose("kill", "stream", env=env)
    listing = checkpoint_listing(run)
    print(f"  checkpoint after the kill: {listing}")
    print(f"  last lines of the stream before the kill: {log_before[-3:]}")
    time.sleep(3)
    since = stamp()
    start_stream(run, env)  # the same RUN_ID: the same checkpoint /data/checkpoints/restart-kill
    log_after_start = batch_logs(since)
    send(lines[half:], f"barrier-events-{run}", args.pace_ms, offset)
    got = wait_rows(run, len(ref["batch"]["windows"]))
    log_after = batch_logs(since)
    print(f"  first lines after the restart: {log_after_start[:4]}")
    batch_ids_before = {w["batch_id"] for w in before}
    rewritten = [w for w in got if w["batch_id"] in batch_ids_before
                 and (w["garage_id"], w["window_start"]) in {(b["garage_id"], b["window_start"]) for b in before}]
    same_ref, same_batch = keyed(got) == keyed(ref["rows"]), keyed(got) == keyed(ref["batch"]["windows"])
    print(f"  {len(got)} windows; equal to restart-ref: {same_ref}; equal to the batch calculation: {same_batch}; "
          f"differences to restart-ref: {len(diff(got, ref['rows']))}")
    print(f"  windows written before the kill: {len(before)}, of them unchanged after the restart: "
          f"{sum(1 for b in before if any(keyed([b]) == keyed([g]) for g in got))}")
    save("restart", {"ref": {k: v for k, v in ref.items() if k not in ("sent", "lines_list")},
                     "kill": {"rows": got, "before_kill": before, "killed_at": killed_at, "checkpoint": listing,
                              "log_before": log_before, "log_after": log_after,
                              "rewritten_same_batch": len(rewritten)},
                     "equal_ref": same_ref, "equal_batch": same_batch})


def lateness_at_arrival(lines: list[str]) -> list[dict[str, Any]]:
    """For every distinct event: how far behind the newest event time seen before it it arrives (minutes)."""
    newest = None
    seen: set[str] = set()
    out = []
    for position, line in enumerate(lines):
        event = json.loads(line)
        if event["event_id"] in seen:
            continue
        seen.add(event["event_id"])
        at = epoch_ms(event["event_time"])
        late = 0.0 if newest is None else max(0.0, (newest - at) / 60_000)
        newest = at if newest is None else max(newest, at)
        if event["garage_id"] != "tick":
            out.append({"event_id": event["event_id"], "event_time": event["event_time"],
                        "garage_id": event["garage_id"], "position": position, "late_min": round(late, 3)})
    return out


def cmd_predict(args: argparse.Namespace) -> None:
    lines = lines_of(events_file("late"))
    late = lateness_at_arrival(lines)
    delayed = [e for e in late if e["late_min"] > 0.05]
    print(f"late.jsonl: {len(lines)} lines, {len(late)} events; {len(delayed)} arrive behind the newest event time")
    for low, high in ((0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 99)):
        n = sum(1 for e in delayed if low < e["late_min"] <= high)
        print(f"  lateness ({low}; {high}] min: {n}")
    expectation = {}
    for wm in args.watermarks:
        over = sum(1 for e in late if e["late_min"] > wm)
        expectation[wm] = over
        print(f"expected with watermark {wm} min: about {over} dropped ({over / len(late):.2%}), the ones more than "
              f"{wm} min behind; a few of them may still be counted (the watermark of the previous micro-batch "
              f"applies), events less than {wm} min behind are all counted")
    save("predict", {"events": len(late), "delayed": len(delayed), "expected_dropped": expectation})


def cmd_lateness(args: argparse.Namespace) -> None:
    results = {}
    for wm in args.watermarks:
        run = f"late-wm{wm}"
        r = independent_run(run, "late", args.pace_ms, watermark=wm, track=True)
        lines = r["lines_list"]
        arrival = {e["event_id"]: e for e in lateness_at_arrival(lines)}
        with db() as conn:
            counted_ids = {x["event_id"] for x in conn.execute(
                "SELECT event_id FROM panel_event WHERE run_id = %s", (run,)).fetchall()}
        counted = sum(w["entries"] + w["exits"] for w in r["rows"])
        dropped = sorted((arrival[i] for i in arrival.keys() - counted_ids), key=lambda e: e["position"])
        # the delay of a window: written_at minus the send time of the first event at or after the window end
        order = [(epoch_ms(json.loads(x)["event_time"]), json.loads(x)["event_id"]) for x in lines]
        delays = []
        for w in r["rows"]:
            end = epoch_ms(w["window_end"])
            first = next(eid for at, eid in order if at >= end)
            delays.append(w["written_at"].timestamp() - r["sent"][first])
        late_counted = [e for e in arrival.values() if e["event_id"] in counted_ids and e["late_min"] > wm]
        results[run] = {
            "watermark_minutes": wm, "pace_ms": args.pace_ms, "send_s": r["send_s"], "events": len(arrival),
            "counted": counted, "counted_ids": len(counted_ids), "dropped": len(dropped),
            "dropped_share": round(len(dropped) / len(arrival), 5),
            "dropped_late_min": {"min": min((e["late_min"] for e in dropped), default=None),
                                 "max": max((e["late_min"] for e in dropped), default=None)},
            "counted_although_later_than_watermark": len(late_counted),
            "late_counted_examples": sorted(late_counted, key=lambda e: -e["late_min"])[:5],
            "delay_s": {"median": round(statistics.median(delays), 2), "min": round(min(delays), 2),
                        "max": round(max(delays), 2)},
            "windows": len(r["rows"]), "windows_below_batch": len(diff(r["rows"], r["batch"]["windows"])),
            "dropped_events": dropped,
        }
        x = results[run]
        print(f"{run}: sent {r['lines']} lines in {r['send_s']} s (--pace-ms {args.pace_ms}); counted {counted} "
              f"(ids {len(counted_ids)}), dropped {len(dropped)} of {len(arrival)} ({x['dropped_share']:.2%}); "
              f"lateness of the dropped {x['dropped_late_min']}; counted although later than {wm} min: "
              f"{len(late_counted)}; delay of a window median {x['delay_s']['median']} s "
              f"(min {x['delay_s']['min']}, max {x['delay_s']['max']}); windows below the batch calculation: "
              f"{x['windows_below_batch']}")
        for e in dropped[:8]:
            print(f"    dropped {e['event_id']} {e['garage_id']} {e['event_time']} late {e['late_min']} min")
    save("lateness", results)


def cmd_restore(_args: argparse.Namespace) -> None:
    start_stream("main", None, settle_s=0)
    print("stream: run main (barrier-events, /data/checkpoints/main)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("main")
    p.add_argument("--pace-ms", type=float, default=2)
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_main)
    p = sub.add_parser("control")
    p.add_argument("--pace-ms", type=float, default=0)
    p.set_defaults(func=cmd_control)
    p = sub.add_parser("dedup")
    p.add_argument("--pace-ms", type=float, default=2)
    p.set_defaults(func=cmd_dedup)
    p = sub.add_parser("restart")
    p.add_argument("--pace-ms", type=float, default=5)
    p.add_argument("--kill-after-s", type=float, default=0, help="pause between the first half and the kill")
    p.set_defaults(func=cmd_restart)
    for name, func in (("predict", cmd_predict), ("lateness", cmd_lateness)):
        p = sub.add_parser(name)
        p.add_argument("--watermarks", type=int, nargs="+", default=[2, 5])
        p.add_argument("--pace-ms", type=float, default=20)
        p.set_defaults(func=func)
    sub.add_parser("restore").set_defaults(func=cmd_restore)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
