"""DPC lab 4 (O4.2, O4.3): the published Spark report equals the independent reference, compared by keys.

From the repository root, with the Spark services and the api running:

    uv run --no-project --with httpx --with pyarrow python experiments/dpc_lr4_compare.py [--refresh]
        [--dataset small|big] [--join auto|shuffle] [--json results/dpc-lr4/compare-small.json]

--refresh starts POST /v1/reports/main/refresh first; in any case the script waits until job.state is no
longer running, then reads GET /v1/reports/main and GET /v1/reports/main/reference and compares the rows
by garage_id: counts and sums exactly (same value and integer type), avg_minutes within 1e-9.
The comparison is app/reference.py compare() of the api (the same function as the tests).
Exit code 1 if the rows differ or the job failed.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "dpc" / "api"))
from app.reference import compare  # noqa: E402

BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")
KEYS = ["garage_id"]
TOLERANCE = 1e-9


def wait_job(c: httpx.Client, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        report = c.get("/v1/reports/main").raise_for_status().json()
        if report["job"]["state"] != "running":
            return report
        if time.monotonic() > deadline:
            raise TimeoutError("the report job is still running")
        time.sleep(1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--dataset", choices=["small", "big"])
    parser.add_argument("--join", choices=["auto", "shuffle"], default="auto")
    parser.add_argument("--timeout", type=float, default=900)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()

    with httpx.Client(base_url=BASE, timeout=600, headers={"Authorization": f"Bearer {TOKEN}"}) as c:
        if args.refresh:
            body = {"join": args.join} | ({"dataset": args.dataset} if args.dataset else {})
            r = c.post("/v1/reports/main/refresh", json=body)
            print(f"POST /v1/reports/main/refresh {json.dumps(body)} -> {r.status_code} {r.text}")
            r.raise_for_status()
        started = time.monotonic()
        report = wait_job(c, args.timeout)
        waited = round(time.monotonic() - started, 1)
        t = time.monotonic()
        ref = c.get("/v1/reports/main/reference").raise_for_status().json()
        ref_http_s = round(time.monotonic() - t, 3)

    job, published = report["job"], report["published"]
    print(f"job {job['run']} state={job['state']} seconds={job.get('seconds')} (waited {waited} s)")
    print(f"published run {published and published['run']} facts={published and published['facts']}")
    print(f"reference facts={ref['facts']} computed in {ref['seconds']} s (HTTP {ref_http_s} s)")
    print(f"{'garage_id':<15} {'sessions':>9} {'total_minutes':>14} {'avg_minutes':>20} {'revenue_minor':>15}  ref")
    refs = {r["garage_id"]: r for r in ref["rows"]}
    for row in report["rows"]:
        other = refs.get(row["garage_id"], {})
        same = not compare([row], [other], KEYS, TOLERANCE) if other else False
        print(
            f"{row['garage_id']:<15} {row['sessions']:>9} {row['total_minutes']:>14} {row['avg_minutes']:>20.12f} "
            f"{row['revenue_minor']:>15}  {'=' if same else 'DIFF'}"
        )
    problems = compare(report["rows"], ref["rows"], KEYS, TOLERANCE)
    if published and published["facts"] != ref["facts"]:
        problems.append(f"different sets: report {published['facts']}, reference {ref['facts']}")
    total = sum(r["sessions"] for r in report["rows"])
    mall = next((r["sessions"] for r in report["rows"] if r["garage_id"] == "mall"), 0)
    print(f"rows {len(report['rows'])} / reference {len(ref['rows'])}; sessions {total}, mall share {mall / max(total, 1):.4f}")
    verdict = "MATCH" if not problems and job["state"] in ("done", "none") else "MISMATCH"
    print(f"{verdict}: {len(problems)} differences" + "".join(f"\n  {p}" for p in problems))

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({"job": job, "published": published, "report": report["rows"], "reference": ref,
                        "problems": problems, "verdict": verdict}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    sys.exit(0 if verdict == "MATCH" else 1)


if __name__ == "__main__":
    main()
