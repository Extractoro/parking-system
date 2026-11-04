"""Coordinator (lab 6), services coordinator-a and coordinator-b: `python main.py`.

Stub: starts, checks that etcd answers and waits. Yours (O6.1-O6.3):
  1. a lease with TTL = LEASE_TTL_S;
  2. create the role key with this lease only if it does not exist (one transaction);
  3. renew the lease more often than the TTL; when it is lost, stop writing at once;
  4. the generation is the create_revision of the role key; register it on the resource (API_URL)
     and send it with every write - the resource checks it, not the coordinator;
  5. without the role: watch the key and try again when it disappears.
Pause test: docker compose pause coordinator-a; sleep 9; docker compose unpause coordinator-a
"""

import json
import os
import signal
import sys
import threading
import time
from datetime import UTC, datetime

from etcd import Etcd

INSTANCE = os.environ.get("INSTANCE", "coordinator")
ETCD_URL = os.environ.get("ETCD_URL", "http://localhost:2379")
LEASE_TTL_S = int(os.environ.get("LEASE_TTL_S", "5"))

stop = threading.Event()


def log(event: str, **fields: object) -> None:
    line = {
        "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "instance": INSTANCE,
        "event": event,
    }
    sys.stdout.write(json.dumps({**line, **fields}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    client = Etcd(ETCD_URL)

    while not stop.is_set():
        try:
            client.get("/health-probe")
            break
        except OSError:
            time.sleep(1)

    log("coordinator.started", lease_ttl_s=LEASE_TTL_S)
    log("todo", item="O6.1", what="take the role through an etcd lease")
    stop.wait()

    log("coordinator.stopped")


if __name__ == "__main__":
    main()
