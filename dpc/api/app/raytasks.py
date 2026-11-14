"""The Ray task of DPC lab 3 (O3.1, O3.4). Runs on ray-head / ray-worker, built from the same image.

process_entry(request) -> decision of the receiver
  1. plate recognition, the slow call of the card (100-500 ms);
  2. POST {RECEIVER_URL}/internal/effects with effect_key = event_id: the RECEIVER changes the garage,
     the task writes nothing to the database;
  3. fault("after_effect_before_ack"): the worker process dies after the effect was accepted but
     before the task is acknowledged; Ray runs the task again (max_retries) with the same event_id,
     the receiver answers with the stored decision.

The module is imported by the api (to submit) and by the Ray worker processes (to run): keep its
imports light, no FastAPI and no database here.
"""

import random
import time

import httpx
import ray

from . import config
from .faults import fault
from .jsonlog import log

RECEIVER_ATTEMPTS = 20  # a short outage of the api (restart) is waited for inside one attempt of the task
_client: httpx.Client | None = None  # one keep-alive client per Ray worker process


def _receiver() -> httpx.Client:
    """httpx.post() builds a new client (and its SSL context) on every call, ~80 ms; a worker process of Ray
    runs many tasks one after another, so it keeps one client with a kept-alive connection."""
    global _client
    if _client is None:
        _client = httpx.Client(base_url=config.RECEIVER_URL, timeout=30,
                               headers={"Authorization": f"Bearer {config.API_TOKEN}"})
    return _client


def _recognize(request: dict) -> str:
    """The simulator of plate recognition: 100-500 ms. Seeded by event_id, so every attempt of the same
    task waits the same time and recognizes the same plate."""
    rng = random.Random(f"{config.SEED}:{request['event_id']}")
    time.sleep(rng.uniform(config.RECOGNITION_MIN_MS, config.RECOGNITION_MAX_MS) / 1000)
    return request["plate"]


def _deliver(effect: dict) -> dict:
    """POST to the receiver until it answers. Connection errors and 5xx are repeated with the SAME key:
    a repeat of an accepted effect gets the stored decision. Any other answer is final."""
    delay = 0.2
    for attempt in range(1, RECEIVER_ATTEMPTS + 1):
        try:
            r = _receiver().post("/internal/effects", json=effect)
            if r.status_code < 500:
                r.raise_for_status()
                return r.json()
            error = f"HTTP {r.status_code}"
        except httpx.TransportError as exc:
            error = type(exc).__name__
        log("task.deliver_retry", event_id=effect["effect_key"], attempt=attempt, error=error)
        time.sleep(delay)
        delay = min(delay * 2, 2.0)
    raise RuntimeError(f"receiver unavailable after {RECEIVER_ATTEMPTS} attempts")


# max_retries: Ray repeats the task after a SYSTEM failure (the worker process died, the node was lost).
# retry_exceptions stays False: an exception of the task code is final (the request ends as failed).
# num_cpus 0.25: the task mostly waits (recognition, HTTP); 5 CPUs of the cluster give 20 slots >= IN_FLIGHT_LIMIT.
@ray.remote(max_retries=3, num_cpus=0.25)
def process_entry(request: dict) -> dict:
    started = time.monotonic()
    plate = _recognize(request)
    effect = {
        "effect_key": request["event_id"],  # the same key in every attempt of this task
        "request_id": request["request_id"],
        "garage_id": request["garage_id"],
        "plate": plate,
        "direction": request["direction"],
        "booking_id": request["booking_id"],
        "event_time": request["event_time"],
    }
    decision = _deliver(effect)
    fault("after_effect_before_ack")  # the effect is accepted, the task is not acknowledged yet
    log("task.done", event_id=request["event_id"], request_id=request["request_id"],
        decision=decision["decision"], repeated=decision["repeated"], ms=round((time.monotonic() - started) * 1000))
    return decision
