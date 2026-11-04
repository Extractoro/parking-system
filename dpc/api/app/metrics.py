"""Request metrics for GET /metrics: latency percentiles, requests in flight, answers by status.

The middleware records every request. Values that only your code knows - the queue length,
busy workers, rejected requests - are added with `gauge("queue_length", lambda: queue.qsize())`
(item O1.5 asks for the queue length and the number of refusals).
"""

import statistics
import threading
import time
from collections import Counter, deque
from collections.abc import Callable

WINDOW = 2000  # the last N latencies: percentiles follow the current load, not the whole run

_lock = threading.Lock()
_latencies_ms: deque[float] = deque(maxlen=WINDOW)
_statuses: Counter[int] = Counter()
_in_flight = 0
_gauges: dict[str, Callable[[], float | int]] = {}
_started = time.time()


def gauge(name: str, read: Callable[[], float | int]) -> None:
    """Register a value read at every GET /metrics, e.g. the length of your queue."""
    _gauges[name] = read


def request_started() -> None:
    global _in_flight
    with _lock:
        _in_flight += 1


def request_finished(status: int, duration_ms: float, *, count_latency: bool = True) -> None:
    global _in_flight
    with _lock:
        _in_flight -= 1
        _statuses[status] += 1
        if count_latency:
            _latencies_ms.append(duration_ms)


def _percentile(values: list[float], q: int) -> float | None:
    if not values:
        return None
    if len(values) == 1:
        return round(values[0], 1)
    return round(statistics.quantiles(values, n=100, method="inclusive")[q - 1], 1)


def snapshot() -> dict[str, object]:
    with _lock:
        values = list(_latencies_ms)
        statuses = {str(code): count for code, count in sorted(_statuses.items())}
        in_flight = _in_flight

    return {
        "uptime_s": round(time.time() - _started, 1),
        "latency_ms": {
            "p50": _percentile(values, 50),
            "p95": _percentile(values, 95),
            "p99": _percentile(values, 99),
        },
        "in_flight": in_flight,
        "responses": statuses,
        **{name: read() for name, read in _gauges.items()},
    }


def prometheus() -> str:
    """The same numbers in the Prometheus text format (GET /metrics/prom, read by Prometheus)."""
    snap = snapshot()
    lines = [f"uptime_seconds {snap['uptime_s']}", f"in_flight {snap['in_flight']}"]

    for q, value in snap["latency_ms"].items():
        if value is not None:
            lines.append(f'latency_ms{{quantile="{q}"}} {value}')
    for status, count in snap["responses"].items():
        lines.append(f'responses_total{{status="{status}"}} {count}')
    for name in _gauges:
        value = snap.get(name)
        if isinstance(value, int | float):
            lines.append(f"{name} {value}")

    return "\n".join(lines) + "\n"
