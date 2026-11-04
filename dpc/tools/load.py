"""Load generator for labs 1 and 3: N requests with C parallel senders to any endpoint.

Prints answers by status and the client-side latency p50/p95/p99. The service measures its
own latency for O1.5; this tool only creates the load and shows what a client saw.
Standard library only.

Run from the project root:

    python tools/load.py http://localhost:8000/v1/returns tools/body.example.json --requests 400 --concurrency 60
    python tools/load.py http://localhost:8000/health --method GET --requests 200 --concurrency 20

The body template is JSON; placeholders in its strings are replaced per request:
    {uuid}           a new random id
    {i}              the number of the request (0, 1, ...)
    {choice:a|b|c}   one of the values (seeded: the same run gives the same choices)
Every request gets its own Idempotency-Key unless --same-key is given.
"""

import argparse
import http.client
import json
import random
import re
import statistics
import time
import urllib.error
import urllib.request
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PLACEHOLDER = re.compile(r"\{(uuid|i|choice:[^}]+)\}")
NO_ANSWER = 0


@dataclass(frozen=True)
class Answer:
    status: int  # HTTP status, or NO_ANSWER: refused, timed out or broken off
    latency_ms: float


def fill(template: Any, index: int, rnd: random.Random) -> Any:
    """Replace placeholders in every string of the template."""
    if isinstance(template, dict):
        return {key: fill(value, index, rnd) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(value, index, rnd) for value in template]
    if not isinstance(template, str):
        return template

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "uuid":
            return uuid.uuid4().hex[:12]
        if name == "i":
            return str(index)
        return rnd.choice(name.removeprefix("choice:").split("|"))

    return PLACEHOLDER.sub(replace, template)


def make_bodies(template_path: Path | None, count: int, seed: int) -> list[Any]:
    """All bodies are built before the run, so building them does not distort latency."""
    if template_path is None:
        return [None] * count

    template = json.loads(template_path.read_text(encoding="utf-8"))
    rnd = random.Random(seed)
    return [fill(template, index, rnd) for index in range(count)]


def send(url: str, method: str, body: Any, headers: dict[str, str], timeout: float) -> Answer:
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(url, data=data, method=method, headers=headers)

    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            response.read()
            status = response.status
    except urllib.error.HTTPError as error:
        status = error.code
    except (OSError, http.client.HTTPException):  # refused, timed out, or the answer broke off while read
        status = NO_ANSWER

    return Answer(status, (time.perf_counter() - started) * 1000)


def run(args: argparse.Namespace, bodies: list[Any]) -> tuple[list[Answer], float]:
    """Send all requests; returns the answers and the wall time of the run in seconds."""
    shared_key = uuid.uuid4().hex

    def one(body: Any) -> Answer:
        headers = {
            "Authorization": f"Bearer {args.token}",
            "Content-Type": "application/json",
            "Idempotency-Key": shared_key if args.same_key else uuid.uuid4().hex,
        }
        return send(args.url, args.method, body, headers, args.timeout)

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        answers = list(pool.map(one, bodies))

    return answers, time.perf_counter() - started


def percentile(values: list[float], share: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))]


def report(answers: list[Answer], concurrency: int, elapsed: float) -> None:
    statuses = Counter(answer.status for answer in answers)
    latencies = [answer.latency_ms for answer in answers]

    print(f"{len(answers)} requests, {concurrency} parallel, {elapsed:.2f} s, {len(answers) / elapsed:.1f} req/s")
    print("statuses:", ", ".join(f"{code or 'no answer'} x {n}" for code, n in sorted(statuses.items())))
    print(
        f"client latency, ms: p50 {statistics.median(latencies):.0f}, p95 {percentile(latencies, 0.95):.0f}, "
        f"p99 {percentile(latencies, 0.99):.0f}, max {max(latencies):.0f}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("url")
    parser.add_argument("body", nargs="?", type=Path, help="JSON body template (omit for GET)")
    parser.add_argument("--method", default="POST")
    parser.add_argument("--requests", type=int, default=400)
    parser.add_argument("--concurrency", type=int, default=40)
    parser.add_argument("--token", default="dev-token")
    parser.add_argument("--same-key", action="store_true", help="one Idempotency-Key for all requests")
    parser.add_argument("--timeout", type=float, default=30, help="seconds per request")
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()

    if args.requests < 1 or args.concurrency < 1 or args.timeout <= 0:
        parser.error("requests, concurrency and timeout must be positive")

    bodies = make_bodies(args.body, args.requests, args.seed)
    answers, elapsed = run(args, bodies)
    report(answers, args.concurrency, elapsed)


if __name__ == "__main__":
    main()
