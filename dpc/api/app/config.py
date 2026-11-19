"""Settings from environment variables: one image runs api, ray-head, ray-worker and coordinators."""

import os


def _int(name: str, default: int) -> int:
    return int(os.environ.get(name, default))


def _float(name: str, default: float) -> float:
    return float(os.environ.get(name, default))


INSTANCE = os.environ.get("INSTANCE", "api")

DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://app:app@localhost:5432/app")
POOL_MIN = _int("POOL_MIN", 1)
POOL_SIZE = _int("POOL_SIZE", 10)
POOL_TIMEOUT_S = _float("POOL_TIMEOUT_S", 2.0)
STATEMENT_TIMEOUT_MS = _int("STATEMENT_TIMEOUT_MS", 5000)

API_TOKEN = os.environ.get("API_TOKEN", "dev-token")
CLIENT_ORIGIN = os.environ.get("CLIENT_ORIGIN", "http://localhost:8083")

RAY_ADDRESS = os.environ.get("RAY_ADDRESS", "ray://localhost:10001")
RUNNER_URL = os.environ.get("RUNNER_URL", "http://localhost:8000")
KAFKA_BOOTSTRAP = os.environ.get("KAFKA_BOOTSTRAP", "localhost:9095")
EVENTS_TOPIC = os.environ.get("EVENTS_TOPIC", "events")
ETCD_URL = os.environ.get("ETCD_URL", "http://localhost:2379")

# experiment modes: what each value changes is yours to write
QUEUE_CAPACITY = _int("QUEUE_CAPACITY", 50)
WORKERS = _int("WORKERS", 4)
# locked | unsafe (O1.4); "unprotected" of the kit is the same as unsafe. Unsafe touches ONLY UNSAFE_GARAGE.
RESOURCE_MODE = os.environ.get("RESOURCE_MODE", "locked")
UNSAFE_GARAGE = os.environ.get("UNSAFE_GARAGE", "dpc-race")
UNSAFE_GAP_MS = _int("UNSAFE_GAP_MS", 50)  # read -> write gap of the unsafe mode
# the slow call of the card: plate recognition 100-500 ms, a seeded generator (O1.3)
RECOGNITION_MIN_MS = _int("RECOGNITION_MIN_MS", 100)
RECOGNITION_MAX_MS = _int("RECOGNITION_MAX_MS", 500)
SEED = _int("SEED", 1)
# test levels of Web that are not part of the garage capacity (decision P1)
EXCLUDED_LEVELS = tuple(x for x in os.environ.get("EXCLUDED_LEVELS", "race-test").split(",") if x)
DRAIN_RETRY_AFTER_S = _int("DRAIN_RETRY_AFTER_S", 5)
# lab 3: local = the worker pool of lab 1 applies the effect itself; ray = Ray tasks, the receiver applies it
EXECUTION = os.environ.get("EXECUTION", "ray")
IN_FLIGHT_LIMIT = _int("IN_FLIGHT_LIMIT", 8)  # O3.2: unfinished Ray tasks at most
RECEIVER_DELAY_MS = _int("RECEIVER_DELAY_MS", 0)  # O3.2 experiment: a slow receiver
# where a Ray task delivers its effect (POST /internal/effects of the api, not published by nginx)
RECEIVER_URL = os.environ.get("RECEIVER_URL", "http://api:8000")
# lab 6: the role of the tariff manager in etcd; FENCING=off skips the generation check for UNSAFE_GARAGE only (O6.3)
FENCING = os.environ.get("FENCING", "on") == "on"
ROLE_KEY = os.environ.get("ROLE_KEY", "/roles/tariff-manager")
LEASE_TTL_S = _int("LEASE_TTL_S", 5)
# lab 2: one computation at a time; relative tolerance of the result (O2.2); niceness of the pool processes
COMPUTE_TOLERANCE = _float("COMPUTE_TOLERANCE", 1e-9)
COMPUTE_RETRY_AFTER_S = _int("COMPUTE_RETRY_AFTER_S", 2)
COMPUTE_KEEP = _int("COMPUTE_KEEP", 100)
COMPUTE_NICE = _int("COMPUTE_NICE", 10)

# lab 4: the batch report. Data sets of the generator: {REPORT_DATA_DIR}/{small|big}/{history,garages};
# runs in {REPORTS_DIR}/runs/{run}, the pointer of the published run {REPORTS_DIR}/current
REPORT_DATA_DIR = os.environ.get("REPORT_DATA_DIR", "/data/c09")
REPORT_DATASET = os.environ.get("REPORT_DATASET", "small")
REPORTS_DIR = os.environ.get("REPORTS_DIR", "/data/reports")
REPORT_SCRIPT = os.environ.get("REPORT_SCRIPT", "report.py")
RUNNER_TIMEOUT_S = _float("RUNNER_TIMEOUT_S", 5.0)

# lab 5: the batch calculation of /v1/panel/batch when the run has no row in panel_run (decision P34)
PANEL_BATCH_FILE = os.environ.get("PANEL_BATCH_FILE", "/data/events/normal.jsonl")
PANEL_WINDOW_MINUTES = _int("WINDOW_MINUTES", 5)
PANEL_WATERMARK_MINUTES = _int("WATERMARK_MINUTES", 5)

PROBLEM_BASE = os.environ.get("PROBLEM_BASE", "https://example.edu/problems/")

FAULTS = os.environ.get("FAULTS", "off") == "on"
FAULT_DIR = os.environ.get("FAULT_DIR", "/faults")
