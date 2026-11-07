"""DPC lab 2 experiments through POST /v1/compute of the running api (docker compose up -d api).

    uv run --no-project --python 3.13 --with httpx --with numpy python experiments/dpc_lr2_compute.py <what>

  accuracy  O2.2: abs error for n and 2n (n = 100 ... 12800), three methods, intervals [0;14] and [0;10],
            the a priori bounds of each method, ratios E(n)/E(2n)
  modes     O2.4: 5 runs of none / lock / local on 4 processes (+ none on 2 and 1 processes)
  bench     O2.3: p = 1, 2, 4, --runs (5) each, round-robin, block --block (default of the service);
            CSV workers,seconds for dpc/tools/bench_table.py and the timing of every run
  health    /health 10 per second on its own connection while p = 4 computes (--seconds 20)

Outputs go to results/dpc-lr2/. DPC_BASE (http://localhost:8000), API_TOKEN (dev-token).
"""

import argparse
import csv
import http.client
import json
import os
import statistics
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "dpc" / "api"))
from app import integrate  # noqa: E402  - the reference and the constants of the card

OUT = ROOT / "results" / "dpc-lr2"
BASE = os.environ.get("DPC_BASE", "http://localhost:8000")
TOKEN = os.environ.get("API_TOKEN", "dev-token")


def client() -> httpx.Client:
    return httpx.Client(base_url=BASE, timeout=60, headers={"Authorization": f"Bearer {TOKEN}"})


def compute(c: httpx.Client, **body) -> dict:
    for _ in range(600):
        r = c.post("/v1/compute", json=body)
        if r.status_code != 429:
            break
        time.sleep(float(r.headers.get("Retry-After", "1")))
    r.raise_for_status()
    loc = r.headers["Location"]
    while True:
        job = c.get(loc).json()
        if job["state"] != "running":
            if job["state"] != "done":
                raise RuntimeError(job)
            return job["result"]
        time.sleep(0.05)


# ------------------------------------------------------------------ O2.2


def f2_max(a: float, b: float, omax: float = integrate.OMAX) -> float:
    """max |O''| on [a; b]: O'' = C (12 t^2 - 12 T t + 2 T^2), a parabola: ends and the vertex T/2."""
    c, tt = integrate.coefficient(omax), integrate.T
    pts = [a, b] + ([tt / 2] if a <= tt / 2 <= b else [])
    return max(abs(c * (12 * t * t - 12 * tt * t + 2 * tt * tt)) for t in pts)


def bound(method: str, a: float, b: float, n: int) -> float:
    h, c = (b - a) / n, integrate.coefficient()
    if method == "midpoint":
        return (b - a) * h * h / 24 * f2_max(a, b)
    if method == "trapezoid":
        return (b - a) * h * h / 12 * f2_max(a, b)
    return (b - a) * h**4 / 180 * 24 * c  # O'''' = 24 C, a constant


def accuracy(_: argparse.Namespace) -> None:
    ns = [100 * 2**k for k in range(8)]
    rows = []
    with client() as c:
        for a, b in ((0.0, 14.0), (0.0, 10.0)):
            for method in integrate.METHODS:
                for n in ns:
                    r = compute(c, a=a, b=b, n=n, workers=1, method=method)
                    rows.append({"a": a, "b": b, "method": method, "n": n, "value": r["value"],
                                 "reference": r["reference"], "abs_error": r["abs_error"],
                                 "rel_error": r["rel_error"], "bound": bound(method, a, b, n)})
    (OUT / "accuracy.json").write_text(json.dumps(rows, indent=1))
    lines = ["| [a; b] | method | n | abs error E(n) | bound | E(n)/E(2n) |", "|---|---|---|---|---|---|"]
    for i, r in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) and rows[i + 1]["method"] == r["method"] and rows[i + 1]["b"] == r["b"] else None
        ratio = f"{r['abs_error'] / nxt['abs_error']:.2f}" if nxt and nxt["abs_error"] > 0 else "-"
        lines.append(f"| [{r['a']:g}; {r['b']:g}] | {r['method']} | {r['n']} | {r['abs_error']:.3e} | "
                     f"{r['bound']:.3e} | {ratio} |")
    eps = sys.float_info.epsilon
    ref = integrate.reference(0, 14)
    lines += ["", f"eps = {eps:.3e}; reference [0;14] = {ref!r}, [0;10] = {integrate.reference(0, 10)!r}",
              "rounding of the blocked sum, relative: eps * (log2(block) + n/block + 2) at n = 4e8, block = 1e6: "
              f"{eps * (20 + 400 + 2):.2e}; worst case of a plain sequential sum, n = 1e9: {eps * 1e9:.2e}; "
              f"shared modes, n/1000 sequential adds at n = 1e9: {eps * 1e6:.2e}"]
    text = "\n".join(lines)
    (OUT / "accuracy.md").write_text(text + "\n", encoding="utf-8")
    print(text)


# ------------------------------------------------------------------ O2.4


def modes(args: argparse.Namespace) -> None:
    plan = [(m, 4) for m in ("none", "lock", "local") for _ in range(5)] + [("none", 2)] * 3 + [("none", 1)] * 2
    rows = []
    with client() as c:
        for mode, workers in plan:
            r = compute(c, a=0, b=14, n=args.n, workers=workers, mode=mode)
            rows.append({"mode": mode, "workers": workers, "value": r["value"], "rel_error": r["rel_error"],
                         "seconds": r["seconds"], "compute_s": r["timing"]["compute_s"], "block": r["block"],
                         "lost_share": 1 - r["value"] / r["reference"]})
            print(f"{mode:5} p={workers} value={r['value']:.9f} rel={r['rel_error']:.2e} "
                  f"seconds={r['seconds']:.3f} compute={r['timing']['compute_s']:.3f}")
    (OUT / "modes.json").write_text(json.dumps(rows, indent=1))
    lines = [f"n = {args.n}, block = {rows[0]['block']} (n/block = {args.n // rows[0]['block']} shared updates), "
             f"reference = {integrate.reference(0, 14)!r}, tolerance 1e-9", "",
             "| mode | p | runs | value min - max | rel error max | within 1e-9 | compute s (median) |",
             "|---|---|---|---|---|---|---|"]
    for mode, workers in dict.fromkeys(plan):
        g = [r for r in rows if r["mode"] == mode and r["workers"] == workers]
        ok = sum(r["rel_error"] <= 1e-9 for r in g)
        lines.append(f"| {mode} | {workers} | {len(g)} | {min(r['value'] for r in g):.6f} - {max(r['value'] for r in g):.6f} "
                     f"| {max(r['rel_error'] for r in g):.2e} | {ok}/{len(g)} | {statistics.median(r['compute_s'] for r in g):.3f} |")
    text = "\n".join(lines)
    (OUT / "modes.md").write_text(text + "\n", encoding="utf-8")
    print(text)


# ------------------------------------------------------------------ O2.3


def bench(args: argparse.Namespace) -> None:
    label = args.label
    body = {"a": 0, "b": 14, "n": args.n, "method": "midpoint"}
    if args.block:
        body["block"] = args.block
    rows = []
    with client() as c:
        compute(c, **body, workers=4)  # warm-up: the forkserver starts once (not measured)
        for run in range(args.runs):
            for workers in (1, 2, 4):
                r = compute(c, **body, workers=workers)
                t = r["timing"]
                rows.append({"run": run, "workers": workers, "seconds": r["seconds"], "rel_error": r["rel_error"],
                             "block": r["block"], **{k: t[k] for k in ("pool_start_s", "dispatch_s", "compute_s",
                                                                        "combine_s", "shutdown_s", "processes")},
                             "parts_s": t["parts_s"]})
                print(f"{label} run {run} p={workers} seconds={r['seconds']:.3f} compute={t['compute_s']:.3f} "
                      f"rel={r['rel_error']:.1e}")
    with (OUT / f"bench-{label}.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["workers", "seconds"])
        w.writerows([r["workers"], f"{r['seconds']:.4f}"] for r in rows)
    (OUT / f"bench-{label}.json").write_text(json.dumps(rows, indent=1))
    lines = [f"{label}: n = {args.n}, block = {rows[0]['block']}, medians of {args.runs} runs, seconds", "",
             "| p | pool start | process start + dispatch | compute (longest part) | combine | total | shutdown (not in total) |",
             "|---|---|---|---|---|---|---|"]
    for workers in (1, 2, 4):
        g = [r for r in rows if r["workers"] == workers]
        m = {k: statistics.median(r[k] for r in g) for k in ("pool_start_s", "dispatch_s", "compute_s", "combine_s",
                                                              "seconds", "shutdown_s")}
        lines.append(f"| {workers} | {m['pool_start_s']:.3f} | {m['dispatch_s']:.3f} | {m['compute_s']:.3f} | "
                     f"{m['combine_s']:.4f} | {m['seconds']:.3f} | {m['shutdown_s']:.3f} |")
    text = "\n".join(lines)
    (OUT / f"timing-{label}.md").write_text(text + "\n", encoding="utf-8")
    print(text)


# ------------------------------------------------------------------ /health under the computation


def health(args: argparse.Namespace) -> None:
    u = urlparse(BASE)
    samples: list[tuple[str, float]] = []
    phase = ["idle"]
    stop = threading.Event()

    def probe() -> None:
        conn = http.client.HTTPConnection(u.hostname, u.port, timeout=10)
        while not stop.is_set():
            t0 = time.perf_counter()
            conn.request("GET", "/health")
            resp = conn.getresponse()
            resp.read()
            samples.append((phase[0], (time.perf_counter() - t0) * 1000))
            time.sleep(max(0.0, 0.1 - (time.perf_counter() - t0)))

    th = threading.Thread(target=probe, daemon=True)
    th.start()
    time.sleep(5)
    phase[0] = "compute p=4"
    jobs = 0
    deadline = time.monotonic() + args.seconds
    with client() as c:
        while time.monotonic() < deadline:
            compute(c, a=0, b=14, n=args.n, workers=4)
            jobs += 1
    stop.set()
    th.join()

    def q(v: list[float], p: float) -> float:
        v = sorted(v)
        return v[min(len(v) - 1, int(round(p / 100 * (len(v) - 1))))]

    lines = [f"/health 10/s on one connection; compute p=4, n={args.n}, {jobs} jobs back to back", "",
             "| phase | samples | p50 ms | p95 ms | p99 ms | max ms |", "|---|---|---|---|---|---|"]
    for name in ("idle", "compute p=4"):
        v = [ms for ph, ms in samples if ph == name]
        lines.append(f"| {name} | {len(v)} | {q(v, 50):.1f} | {q(v, 95):.1f} | {q(v, 99):.1f} | {max(v):.1f} |")
    text = "\n".join(lines)
    (OUT / "health.md").write_text(text + "\n", encoding="utf-8")
    print(text)


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="what", required=True)
    sub.add_parser("accuracy").set_defaults(fn=accuracy)
    m = sub.add_parser("modes")
    m.add_argument("--n", type=int, default=20_000_000)
    m.set_defaults(fn=modes)
    b = sub.add_parser("bench")
    b.add_argument("--n", type=int, default=400_000_000)
    b.add_argument("--runs", type=int, default=5)
    b.add_argument("--block", type=int, default=None)
    b.add_argument("--label", default="base")
    b.set_defaults(fn=bench)
    h = sub.add_parser("health")
    h.add_argument("--n", type=int, default=400_000_000)
    h.add_argument("--seconds", type=float, default=20)
    h.set_defaults(fn=health)
    args = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    args.fn(args)


if __name__ == "__main__":
    main()
