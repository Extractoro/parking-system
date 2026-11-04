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
RESOURCE_MODE = os.environ.get("RESOURCE_MODE", "locked")  # locked | unprotected (O1.4)
EXECUTION = os.environ.get("EXECUTION", "local")  # local | ray (lab 3)
IN_FLIGHT_LIMIT = _int("IN_FLIGHT_LIMIT", 8)  # O3.2
RECEIVER_DELAY_MS = _int("RECEIVER_DELAY_MS", 0)  # D3.4
FENCING = os.environ.get("FENCING", "on") == "on"  # O6.3

PROBLEM_BASE = os.environ.get("PROBLEM_BASE", "https://example.edu/problems/")

FAULTS = os.environ.get("FAULTS", "off") == "on"
FAULT_DIR = os.environ.get("FAULT_DIR", "/faults")
