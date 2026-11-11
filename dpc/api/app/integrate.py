"""Parallel numerical integral of the C09 card (DPC lab 2).

O(t) = 16*Omax/T^4 * t^2 (T - t)^2 is the occupancy of a garage (cars) at hour t of a working day
[0; T], T = 14. Its integral over [a; b] is car-hours; car-hours times the tariff is the revenue.
The reference is the antiderivative F(b) - F(a), F(t) = C (T^2 t^3/3 - T t^4/2 + t^5/5), C = 16*Omax/T^4,
computed in exact rational arithmetic.

Scheme (O2.1): the n subintervals [0, n) are split into `workers` contiguous index ranges without gaps
or overlaps (`split`). Each process computes the weighted sum of its nodes in numpy blocks; node i of
the trapezoid / Simpson rule belongs to the range that contains i, the last node n to the last range,
so the end weights are counted exactly once. Every part starts after a barrier of `workers` parties,
so each part runs in its own process (PC-12). The sequential part: the split, the start of the pool,
pickling of one small Task per part and the sum of `workers` numbers in the main process.

The modes of the sum (O2.4): `local` - each part returns its own sum; `none` - every part adds the sum
of each small block to one shared double without a lock (`total.value += s`: read, add, write, so
concurrent updates get lost); `lock` - the same under the lock of the shared value.

Pure functions and the part body here; the process pool and timing in `run`. No random numbers: the
method is deterministic, the result does not depend on a seed or on the process number.
"""

import multiprocessing as mp
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from fractions import Fraction
from typing import Any

import numpy as np

T = 14.0  # hours of the working day
OMAX = 81.0  # peak occupancy, cars: spots of the garage mall in the demo data (decision P16)
BLOCK = 1_000_000  # nodes per numpy block of the local mode
SHARED_BLOCK = 1_000  # nodes per shared update of the none / lock modes: many small updates
METHODS = ("midpoint", "trapezoid", "simpson")
MODES = ("none", "lock", "local")
ORDERS = ("forward", "reverse")


def coefficient(omax: float = OMAX) -> float:
    return 16.0 * omax / T**4


def occupancy(t: float, omax: float = OMAX) -> float:
    """O(t), cars at hour t."""
    return coefficient(omax) * (t * (T - t)) ** 2


def reference(a: float, b: float, omax: float = OMAX) -> float:
    """F(b) - F(a) in exact rational arithmetic, rounded once to float."""
    tt, c = Fraction(14), Fraction(16) * Fraction(omax) / Fraction(14) ** 4

    def antiderivative(t: Fraction) -> Fraction:
        return c * (tt**2 * t**3 / 3 - tt * t**4 / 2 + t**5 / 5)

    return float(antiderivative(Fraction(b)) - antiderivative(Fraction(a)))


def split(n: int, parts: int) -> list[tuple[int, int]]:
    """[0, n) -> `parts` contiguous ranges [lo, hi): no gaps, no overlaps, sizes differ by at most 1."""
    q, r = divmod(n, parts)
    ranges, lo = [], 0
    for k in range(parts):
        hi = lo + q + (1 if k < r else 0)
        ranges.append((lo, hi))
        lo = hi
    return ranges


@dataclass(frozen=True)
class Task:
    """Everything a part needs: a few numbers, pickled once per part (no arrays are sent)."""

    a: float
    b: float
    n: int
    method: str
    lo: int
    hi: int
    block: int
    order: str
    mode: str
    last: bool  # the last range also owns node n (empty ranges of n < workers must not add it again)


def _g(t: float) -> float:
    """(t (T - t))^2: O(t) without the constant factor C (it is applied once at the end)."""
    return (t * (T - t)) ** 2


def block_sum(task: Task, s: int, e: int) -> float:
    """Weighted sum of g over the nodes s..e-1 of the method, without the factors C and h."""
    h = (task.b - task.a) / task.n
    x = np.arange(s, e, dtype=np.float64)
    if task.method == "midpoint":
        x += 0.5
    x *= h
    x += task.a
    y = np.subtract(T, x)
    y *= x
    y *= y
    if task.method == "simpson":  # weights 2 (even node), 4 (odd node); the ends are fixed in part_sum
        return 2.0 * float(y.sum()) + 2.0 * float(y[(s + 1) % 2 :: 2].sum())
    return float(y.sum())


def end_terms(task: Task) -> float:
    """Corrections of the end nodes: node 0 has weight 1/2 (trapezoid) or 1 (Simpson) instead of 1 / 2,
    node n is in no range [lo, hi) and is added by the last part only."""
    if task.method == "midpoint":
        return 0.0
    extra = 0.0
    if task.lo == 0 and task.hi > 0:
        extra -= _g(task.a) / 2 if task.method == "trapezoid" else _g(task.a)
    if task.last:
        extra += _g(task.b) / 2 if task.method == "trapezoid" else _g(task.b)
    return extra


# the shared double of the none / lock modes and the start barrier of the parts; set in each pool
# process by _init (inherited, not pickled)
_total: Any = None
_barrier: Any = None
BARRIER_TIMEOUT_S = 120.0  # a lost process must not hang the parts forever: BrokenBarrierError fails the job


def _init(total: Any, nice: int, barrier: Any = None) -> None:
    global _total, _barrier
    _total = total
    _barrier = barrier
    if nice:
        os.nice(nice)  # the event loop of api keeps priority over the computation (/health stays fast)


def part_sum(task: Task) -> tuple[float, float, float, int]:
    """Body of one part: (local sum or 0.0, start, end, pid). Times are time.monotonic(), comparable
    between processes of one Linux host.

    PC-12: a part first waits on the barrier of `workers` parties. A process that holds a part blocks
    there until every other part has been taken, so it cannot take a second part: each of the `workers`
    parts runs in its own process (processes == workers). The wait is before `started`, so compute_s
    and parts_s do not include it."""
    if _barrier is not None:
        _barrier.wait(BARRIER_TIMEOUT_S)
    started = time.monotonic()
    starts = list(range(task.lo, task.hi, task.block))
    if task.order == "reverse":
        starts.reverse()
    local = 0.0
    for s in starts:
        v = block_sum(task, s, min(s + task.block, task.hi))
        if task.mode == "local":
            local += v
        elif task.mode == "lock":
            with _total.get_lock():
                _total.value += v
        else:  # none: read-modify-write without synchronization, updates of other processes get lost
            _total.value += v
    extra = end_terms(task)
    if extra:
        if task.mode == "local":
            local += extra
        elif task.mode == "lock":
            with _total.get_lock():
                _total.value += extra
        else:
            _total.value += extra
    return local, started, time.monotonic(), os.getpid()


def sequential(a: float, b: float, n: int, method: str, omax: float = OMAX) -> float:
    """Plain Python loop over the nodes with explicit weights: the check of the parallel split (small n)."""
    h = (b - a) / n
    if method == "midpoint":
        s = sum(_g(a + (i + 0.5) * h) for i in range(n))
        return coefficient(omax) * h * s
    if method == "trapezoid":
        s = _g(a) / 2 + sum(_g(a + i * h) for i in range(1, n)) + _g(b) / 2
        return coefficient(omax) * h * s
    s = _g(a) + sum((4 if i % 2 else 2) * _g(a + i * h) for i in range(1, n)) + _g(b)
    return coefficient(omax) * h / 3 * s


def context() -> Any:
    """forkserver (the default of Python 3.14 on Linux): children are forked from a clean server that has
    numpy and this module preloaded, not from the api process with its threads and sockets."""
    ctx = mp.get_context("forkserver")
    ctx.set_forkserver_preload(["app.integrate"])
    return ctx


def run(
    a: float,
    b: float,
    n: int,
    workers: int,
    method: str = "midpoint",
    order: str = "forward",
    mode: str = "local",
    block: int | None = None,
    omax: float = OMAX,
    nice: int = 0,
    on_part: Callable[[], None] | None = None,
    on_pool: Callable[[Any], None] | None = None,
) -> dict[str, Any]:
    """Blocking: a new pool of `workers` processes per call (its start is part of the measured time).
    Called from a thread of api (run_in_executor), never on the event loop."""
    block = block or (BLOCK if mode == "local" else SHARED_BLOCK)
    ctx = context()
    t0 = time.monotonic()
    total = None
    if mode == "none":
        total = ctx.Value("d", 0.0, lock=False)
    elif mode == "lock":
        total = ctx.Value("d", 0.0)
    ranges = split(n, workers)
    tasks = [Task(a, b, n, method, lo, hi, block, order, mode, k == workers - 1) for k, (lo, hi) in enumerate(ranges)]

    barrier = ctx.Barrier(workers) if workers > 1 else None  # PC-12: one part per process
    pool = ctx.Pool(workers, initializer=_init, initargs=(total, nice, barrier))
    t_pool = time.monotonic()
    if on_pool:
        on_pool(pool)
    try:
        callback = (lambda _: on_part()) if on_part else None
        pending = [pool.apply_async(part_sum, (t,), callback=callback) for t in tasks]
        parts = [p.get() for p in pending]
        sums = [p[0] for p in parts]
        if order == "reverse":
            sums.reverse()
        raw = sum(sums) if mode == "local" else total.value
        h = (b - a) / n
        value = coefficient(omax) * (h / 3 if method == "simpson" else h) * raw
        t_done = time.monotonic()
    finally:
        pool.terminate()
        pool.join()
    t_closed = time.monotonic()

    ref = reference(a, b, omax)
    first_start, last_start = min(p[1] for p in parts), max(p[1] for p in parts)
    last_end = max(p[2] for p in parts)
    return {
        "value": value,
        "reference": ref,
        "abs_error": abs(value - ref),
        "rel_error": abs(value - ref) / abs(ref) if ref else abs(value - ref),
        "seconds": t_done - t0,
        "pool_start_seconds": t_pool - t0,
        "workers": workers,
        "method": method,
        "n": n,
        "mode": mode,
        "order": order,
        "block": block,
        "omax": omax,
        "timing": {
            # the composition of `seconds`: pool start + dispatch + compute + combine
            "pool_start_s": t_pool - t0,
            "dispatch_s": max(last_start - t_pool, 0.0),  # pickling the tasks, the first task of each process
            "compute_s": max(p[2] - p[1] for p in parts),  # the longest part
            "parts_s": [round(p[2] - p[1], 6) for p in parts],
            "first_part_start_s": first_start - t0,
            "combine_s": max(t_done - last_end, 0.0),  # results back to the main process and their sum
            "shutdown_s": t_closed - t_done,  # pool teardown, not in `seconds`
            "processes": len({p[3] for p in parts}),
        },
    }
