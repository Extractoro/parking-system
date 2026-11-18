"""DPC lab 4: the data sets (O4.1), the plan and the skew (O4.4), the executor loss (O4.5), the atomic pointer (D4.3).

Needs: docker compose up -d api spark-master spark-worker-1 spark-worker-2 spark-runner and both sets in the
data volume (docker compose run --rm generator; ... --out /data/c09/big --rows 10000000 --jobs 4).
From the repository root:

    uv run --no-project --with httpx --with pyarrow python experiments/dpc_lr4.py datasets | skew | executor | atomic

datasets  O4.1: rows, size on disk, seed, measured share of mall for /data/c09/small and /data/c09/big; the small set
          is generated a second time into /data/c09/small-repro and the files are compared by SHA-256.
skew      O4.4: report.py on the big set with --join shuffle, 3 runs of each variant: AQE off and AQE with the
          skew join split (thresholds SKEW below); the stage task metrics (_metrics.json of each run), the medians;
          the plan of the first run of each variant and of the default broadcast join -> results/dpc-lr4/plan-*.txt.
          Every run is compared with the reference of the api (GET /v1/reports/main/reference on the big set).
executor  O4.5: POST /v1/reports/main/refresh {dataset: big, join: shuffle}; when the tasks run, docker compose kill
          spark-worker-1; the job must end on spark-worker-2 and equal the reference. The event log of the run shows
          the lost executor and the resubmitted tasks. spark-worker-1 is started again at the end.
atomic    D4.3: a run with a missing facts path fails; the pointer /data/reports/current stays byte for byte the same
          and GET /v1/reports/main shows job failed + the previous published run; then a good run replaces it.
Outputs: results/dpc-lr4/<command>.json; the text goes to stdout (tee it to results/dpc-lr4/<command>.txt).
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dpc" / "api"))
from app.reference import compare

BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
OUT = Path("results/dpc-lr4")
BIG = ("/data/c09/big/history", "/data/c09/big/garages")
SMALL = ("/data/c09/small/history", "/data/c09/small/garages")
SKEW = ["--skew-threshold", "8m", "--skew-factor", "3", "--advisory", "4m", "--force-skew"]
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True, encoding="utf-8", env=ENV).stdout


def in_runner(code: str) -> str:
    """Python inside spark-runner: the data volume and the runner on localhost:8000."""
    return sh("docker", "compose", "exec", "-T", "spark-runner", "python", "-c", code)


def runner_submit(args: list[str]) -> str:
    body = json.dumps({"script": "report.py", "args": args})
    code = (
        "import json,urllib.request as u;"
        f"r=u.urlopen(u.Request('http://localhost:8000/jobs',{body!r}.encode(),{{'Content-Type':'application/json'}}));"
        "print(json.load(r)['run'])"
    )
    return in_runner(code).strip()


def runner_job(run: str) -> dict:
    return json.loads(in_runner(f"import urllib.request as u;print(u.urlopen('http://localhost:8000/jobs/{run}').read().decode())"))


def wait_runner(run: str, timeout: float = 900) -> dict:
    deadline = time.monotonic() + timeout
    while (job := runner_job(run))["state"] == "running":
        if time.monotonic() > deadline:
            raise TimeoutError(run)
        time.sleep(2)
    return job


def read_json(path: str) -> dict:
    return json.loads(in_runner(f"print(open({path!r}, encoding='utf-8').read())"))


def api() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=900, headers={"Authorization": f"Bearer {TOKEN}"})


def wait_idle(c: httpx.Client) -> dict:
    while (r := c.get("/v1/reports/main").raise_for_status().json())["job"]["state"] == "running":
        time.sleep(2)
    return r


def save(name: str, data: object) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f"{name}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------------------------------------------
def datasets(_: argparse.Namespace) -> None:
    code = r"""
import json, hashlib, os, pyarrow.dataset as ds, pyarrow.compute as pc
def size(p): return sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(p) for f in fs)
out = {}
for name in ("small", "big", "small-repro"):
    root = f"/data/c09/{name}"
    if not os.path.isdir(root): continue
    facts = ds.dataset(f"{root}/history", format="parquet")
    rows = facts.count_rows()
    mall = facts.count_rows(filter=pc.field("garage_id") == "mall")
    files = sorted(os.listdir(f"{root}/history"))
    digest = hashlib.sha256()
    for f in files + ["../garages/garages.parquet"]:
        digest.update(open(f"{root}/history/{f}", "rb").read())
    out[name] = {"rows": rows, "mall": mall, "mall_share": mall / rows, "facts_bytes": size(f"{root}/history"),
                 "dim_bytes": size(f"{root}/garages"), "dim_rows": ds.dataset(f"{root}/garages").count_rows(),
                 "files": len(files), "sha256": digest.hexdigest()}
print(json.dumps(out))
"""
    print("$ docker compose run --rm generator --out /data/c09/small-repro   # the same command as the small set")
    t = time.monotonic()
    print(sh("docker", "compose", "run", "--rm", "generator", "--out", "/data/c09/small-repro").strip())
    print(f"generated in {time.monotonic() - t:.1f} s")
    stats = json.loads(in_runner(code))
    commands = {
        "small": "docker compose run --rm generator",
        "big": "docker compose run --rm generator --out /data/c09/big --rows 10000000 --jobs 4",
        "small-repro": "docker compose run --rm generator --out /data/c09/small-repro",
    }
    print(f"\n{'set':<12} {'rows':>10} {'facts, MB':>10} {'dim':>4} {'files':>5} {'seed':>5} {'mall':>9} {'share':>7}  sha256")
    for name, s in stats.items():
        print(f"{name:<12} {s['rows']:>10} {s['facts_bytes'] / 2**20:>10.1f} {s['dim_rows']:>4} {s['files']:>5} {909:>5} "
              f"{s['mall']:>9} {s['mall_share']:>7.4f}  {s['sha256'][:16]}")
        s["command"], s["seed"] = commands[name], 909
    same = stats["small"]["sha256"] == stats["small-repro"]["sha256"]
    print(f"\nsmall == small-repro (SHA-256 of all Parquet files): {same}")
    save("datasets", {"sets": stats, "reproduced": same})


# ---------------------------------------------------------------------------------------------------------------
def join_stage(metrics: dict) -> dict:
    """The stage that reads the shuffled facts (the most shuffle-read bytes): the sort-merge join."""
    return max(metrics["stages"], key=lambda s: s["shuffle_read_bytes_total"])


def scan_stage(metrics: dict) -> dict:
    """The longest stage without shuffle read: reading Parquet and writing the shuffle of the facts."""
    return max((s for s in metrics["stages"] if not s["shuffle_read_bytes_total"]), key=lambda s: s["task_ms_max"])


def skew(args: argparse.Namespace) -> None:
    variants = {"aqe-off": ["--aqe", "off"], "aqe-skew": ["--aqe", "on", *SKEW]}
    runs: list[dict] = []
    for i in range(args.repeat):
        for variant, extra in variants.items():  # alternate the variants: a slow minute hits both
            run = runner_submit(["--facts", BIG[0], "--dim", BIG[1], "--out", "/data/reports/runs/{run}", "--join",
                                 "shuffle", "--metrics", *extra])
            job = wait_runner(run)
            if job["state"] != "done":
                raise SystemExit(f"{variant} run {run} failed:\n" + "\n".join(job["log"][-20:]))
            folder = f"/data/reports/runs/{run}"
            metrics, rows = read_json(f"{folder}/_metrics.json"), read_json(f"{folder}/rows.json")
            js, ss = join_stage(metrics), scan_stage(metrics)
            entry = {"variant": variant, "repeat": i + 1, "run": run, "app": metrics["app"], "seconds": metrics["seconds"],
                     "job_s": job["seconds"],
                     "join": js, "scan": ss, "rows": rows["rows"]}
            runs.append(entry)
            print(f"{variant:<9} #{i + 1} {run}: collect {metrics['seconds']:.1f} s; join stage {js['stage']}: "
                  f"{js['tasks']} tasks ({js['tasks_with_shuffle_read']} non-empty), max {js['task_ms_max']} ms, "
                  f"median non-empty {js['task_ms_median_nonempty']:.0f} ms, shuffle read max "
                  f"{js['shuffle_read_bytes_max'] / 2**20:.1f} MB / median {js['shuffle_read_bytes_median_nonempty'] / 2**20:.1f} MB",
                  flush=True)
            if i == 0:
                plan = in_runner(f"print(open('{folder}/_plan.txt').read())")
                (OUT / f"plan-shuffle-{variant}.txt").write_text(plan, encoding="utf-8")

    # the default broadcast join on the same set, once, for the plan only
    run = runner_submit(["--facts", BIG[0], "--dim", BIG[1], "--out", "/data/reports/runs/{run}", "--metrics"])
    job = wait_runner(run)
    (OUT / "plan-auto-broadcast.txt").write_text(in_runner(f"print(open('/data/reports/runs/{run}/_plan.txt').read())"),
                                                 encoding="utf-8")
    auto = read_json(f"/data/reports/runs/{run}/_metrics.json")
    print(f"auto      #1 {run}: collect {auto['seconds']:.1f} s (broadcast join, plan only); published = this run")

    with api() as c:
        ref = c.get("/v1/reports/main/reference").raise_for_status().json()
    assert ref["facts"] == BIG[0], ref["facts"]
    print(f"\nreference of {ref['facts']}: {ref['seconds']} s without Spark")
    for r in runs:
        r["problems"] = compare(r.pop("rows"), ref["rows"], ["garage_id"], 1e-9)

    print(f"\n{'variant':<9} {'collect s':>9} {'join max ms':>11} {'join med ms':>11} {'max/med':>7} {'join stage s':>12} "
          f"{'tasks':>5} {'shuffle max MB':>14} {'= ref':>6}   (medians of {args.repeat} runs)")
    summary = {}
    for variant in variants:
        rs = [r for r in runs if r["variant"] == variant]
        med = lambda f: statistics.median(f(r) for r in rs)
        row = {
            "collect_s": med(lambda r: r["seconds"]),
            "join_task_max_ms": med(lambda r: r["join"]["task_ms_max"]),
            "join_task_median_ms": med(lambda r: r["join"]["task_ms_median_nonempty"]),
            "join_tasks": med(lambda r: r["join"]["tasks"]),
            "join_shuffle_max_mb": med(lambda r: r["join"]["shuffle_read_bytes_max"] / 2**20),
            "scan_task_max_ms": med(lambda r: r["scan"]["task_ms_max"]),
            "all_equal_reference": all(not r["problems"] for r in rs),
        }
        row["join_stage_s"] = med(lambda r: stage_seconds(r["join"]))
        row["ratio"] = row["join_task_max_ms"] / row["join_task_median_ms"]
        summary[variant] = row
        print(f"{variant:<9} {row['collect_s']:>9.1f} {row['join_task_max_ms']:>11.0f} {row['join_task_median_ms']:>11.0f} "
              f"{row['ratio']:>7.1f} {row['join_stage_s']:>12.1f} {row['join_tasks']:>5.0f} {row['join_shuffle_max_mb']:>14.1f} "
              f"{'yes' if row['all_equal_reference'] else 'NO':>6}")
    save("skew", {"thresholds": SKEW, "runs": runs, "summary": summary, "auto_run": run})


def stage_seconds(stage: dict) -> float:
    """Wall time of a stage from the UI timestamps (2026-11-18T09:20:51.123GMT)."""
    from datetime import datetime

    def parse(s: str) -> float:
        return datetime.strptime(s.replace("GMT", "+0000"), "%Y-%m-%dT%H:%M:%S.%f%z").timestamp()

    return parse(stage["completed"]) - parse(stage["submitted"])


# ---------------------------------------------------------------------------------------------------------------
EVENTS = r"""
import json, glob, os, sys
from compression import zstd
app = sys.argv[1]
# Spark 4 writes rolling event logs: /data/spark-events/eventlog_v2_<app>/events_<n>_<app>.zstd
parts = sorted(glob.glob(f'/data/spark-events/*{app}*/events_*'), key=lambda p: int(os.path.basename(p).split('_')[1]))
path = os.path.dirname(parts[0])
lines = (line for part in parts for line in (zstd.open(part, 'rt', encoding='utf-8') if part.endswith('.zstd') else open(part, encoding='utf-8')))
added, removed, failed, stages = [], [], [], []
for line in lines:
    e = json.loads(line)
    k = e['Event']
    if k == 'SparkListenerExecutorAdded':
        added.append({'id': e['Executor ID'], 'host': e['Executor Info']['Host'], 'time': e['Timestamp']})
    elif k == 'SparkListenerExecutorRemoved':
        removed.append({'id': e['Executor ID'], 'reason': e['Removed Reason'][:160], 'time': e['Timestamp']})
    elif k == 'SparkListenerTaskEnd' and e['Task End Reason']['Reason'] != 'Success':
        r = e['Task End Reason']
        failed.append({'stage': e['Stage ID'], 'attempt': e['Stage Attempt ID'], 'reason': r['Reason'],
                       'executor': e['Task Info']['Executor ID'], 'detail': str(r.get('Loss Reason') or r.get('Message') or '')[:120]})
    elif k == 'SparkListenerStageCompleted':
        s = e['Stage Info']
        stages.append({'stage': s['Stage ID'], 'attempt': s['Stage Attempt ID'], 'tasks': s['Number of Tasks'],
                       'failure': (s.get('Failure Reason') or '')[:120]})
print(json.dumps({'log': os.path.basename(path), 'added': added, 'removed': removed, 'failed_tasks': failed, 'stages': stages}))
"""


def executor(args: argparse.Namespace) -> None:
    with api() as c:
        wait_idle(c)
        before = c.get("/v1/reports/main").json()["published"]
        r = c.post("/v1/reports/main/refresh", json={"dataset": "big", "join": "shuffle"})
        print(f"POST /v1/reports/main/refresh {{dataset: big, join: shuffle}} -> {r.status_code} {r.text}")
        run = r.raise_for_status().json()["job"]["run"]
        t0 = time.monotonic()
        # wait until both executors run tasks of the job (the driver UI of the runner, port 4040)
        while True:
            try:
                apps = httpx.get("http://localhost:4040/api/v1/applications", timeout=2).json()
                execs = httpx.get(f"http://localhost:4040/api/v1/applications/{apps[0]['id']}/executors", timeout=2).json()
                active = [e for e in execs if e["id"] != "driver" and e["activeTasks"] > 0]
                if len(active) >= 2:
                    break
            except (httpx.HTTPError, IndexError, KeyError, ValueError):
                pass
            time.sleep(0.5)
        app = apps[0]["id"]
        print(f"t+{time.monotonic() - t0:.1f} s: application {app}, executors with tasks: "
              + ", ".join(f"{e['id']}@{e['hostPort']} ({e['activeTasks']} tasks)" for e in active))
        time.sleep(args.delay)
        print(f"t+{time.monotonic() - t0:.1f} s: $ docker compose kill spark-worker-1")
        print(sh("docker", "compose", "kill", "spark-worker-1").strip() or "killed")
        killed_at = time.monotonic() - t0
        report = wait_idle(c)
        finished_at = time.monotonic() - t0
        print(f"t+{finished_at:.1f} s: job {report['job']['run']} state={report['job']['state']} "
              f"seconds={report['job']['seconds']}; published {report['published']['run']}")
        ref = c.get("/v1/reports/main/reference").json()
    problems = compare(report["rows"], ref["rows"], ["garage_id"], 1e-9)
    events = json.loads(sh("docker", "compose", "exec", "-T", "spark-runner", "python", "-c", EVENTS, app))
    print(f"\nevent log {events['log']}:")
    for e in events["added"]:
        print(f"  executor added   {e['id']} on {e['host']}")
    for e in events["removed"]:
        print(f"  executor removed {e['id']}: {e['reason']}")
    reasons: dict[str, int] = {}
    for t in events["failed_tasks"]:
        reasons[f"stage {t['stage']}.{t['attempt']} {t['reason']} on executor {t['executor']}"] = reasons.get(
            f"stage {t['stage']}.{t['attempt']} {t['reason']} on executor {t['executor']}", 0) + 1
    for k, v in reasons.items():
        print(f"  failed tasks: {v} x {k}")
    for s in events["stages"]:
        if s["attempt"] > 0 or s["failure"]:
            print(f"  stage {s['stage']} attempt {s['attempt']} ({s['tasks']} tasks) {s['failure']}")
    print(f"\nreference of {ref['facts']}: {len(problems)} differences -> {'MATCH' if not problems else 'MISMATCH'}")
    print("$ docker compose up -d spark-worker-1")
    print(sh("docker", "compose", "up", "-d", "spark-worker-1").strip() or "started")
    save("executor", {"run": run, "app": app, "before": before, "killed_at_s": killed_at, "finished_at_s": finished_at,
                      "job": report["job"], "published": report["published"], "events": events, "problems": problems})


# ---------------------------------------------------------------------------------------------------------------
def atomic(_: argparse.Namespace) -> None:
    pointer = lambda: in_runner("print(open('/data/reports/current', 'rb').read().hex())").strip()
    inode = lambda: in_runner("import os;print(os.stat('/data/reports/current').st_ino)").strip()
    with api() as c:
        wait_idle(c)
        before, ino = pointer(), inode()
        print(f"current before: {bytes.fromhex(before).decode()}\ninode {ino}")
        bad = runner_submit(["--facts", "/data/c09/missing/history", "--dim", SMALL[1], "--out", "/data/reports/runs/{run}"])
        job = wait_runner(bad)
        tail = [line for line in job["log"] if "PATH_NOT_FOUND" in line or "AnalysisException" in line][:2]
        print(f"\nfailed run {bad}: state={job['state']} exit_code={job['exit_code']}\n  " + "\n  ".join(tail))
        folder = in_runner(f"import os;print(os.path.exists('/data/reports/runs/{bad}'))").strip()
        after = pointer()
        report = c.get("/v1/reports/main").json()
        print(f"folder of the failed run exists: {folder}")
        print(f"current unchanged (bytes): {after == before}; inode {inode()}")
        print(f"GET /v1/reports/main: job {report['job']['run']} {report['job']['state']}, published {report['published']['run']}")
        r = c.post("/v1/reports/main/refresh", json={"dataset": "small"})
        good = r.raise_for_status().json()["job"]["run"]
        report = wait_idle(c)
        changed = pointer()
        print(f"\ngood run {good}: state={report['job']['state']}, published {report['published']['run']}, "
              f"current changed: {changed != before}; inode {inode()} (a new file renamed over the old one)")
        leftovers = in_runner("import os;print([f for f in os.listdir('/data/reports') if f.endswith('.tmp')])").strip()
        print(f"temporary files left in /data/reports: {leftovers}")
    save("atomic", {"failed_run": bad, "failed_state": job["state"], "pointer_unchanged": after == before,
                    "failed_folder_exists": folder, "good_run": good, "published": report["published"],
                    "pointer_changed": changed != before})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("datasets")
    p = sub.add_parser("skew")
    p.add_argument("--repeat", type=int, default=3)
    p = sub.add_parser("executor")
    p.add_argument("--delay", type=float, default=3.0, help="seconds after both executors have tasks")
    sub.add_parser("atomic")
    args = parser.parse_args()
    {"datasets": datasets, "skew": skew, "executor": executor, "atomic": atomic}[args.command](args)


if __name__ == "__main__":
    main()
