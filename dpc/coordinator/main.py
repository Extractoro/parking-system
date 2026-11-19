"""Coordinator of DPC lab 6, services coordinator-a and coordinator-b: the dynamic tariff manager of C09.

One process at a time holds the role ROLE_KEY (/roles/tariff-manager) in etcd (O6.1):
  1. lease with TTL = LEASE_TTL_S (5 s), renewed every RENEW_EVERY_S (1.5 s) by a separate thread;
  2. the role is taken by one transaction: IF create_revision(ROLE_KEY) == 0 THEN PUT ROLE_KEY=INSTANCE lease=<id>;
  3. the generation is the create_revision of the key (a revision of etcd, not the time or a random number); the owner
     registers it on the resource for each of its garages (POST /v1/tariff/generations; the protected record is the
     tariff of a garage) and sends it with every write (PUT /v1/tariff/{id}).
     The resource checks it in one transaction with the write (O6.2): this process never decides that its write is
     safe, a write of a paused old owner reaches the resource and is refused there (O6.3);
  4. without the role: watch the key from the revision of the failed transaction and try again when it is deleted;
  5. the lease is lost (keepalive answers TTL 0, or no successful renewal for the whole TTL) or the resource refused a
     write -> stop writing at once (role.lost) and go back to 4. SIGTERM revokes the lease: the role moves at once.

The work of the owner (C09 card): every TARIFF_EVERY_S (3 s) the tariff of each garage of TARIFF_GARAGES is
base * (1 + occupied / spots) from GET /v1/garage/{id}, rounded to 100 kopecks: ROUNDING=up (coordinator-a) or down
(coordinator-b), so two managers without protection set visibly different tariffs.
WRITE_GAP_MS (default 0): a pause between "tariff.prepared" and the write - the check-then-act gap of the pause
experiment of O6.3 (the script pauses the container inside it).

Log: JSON lines role.acquired, role.lost, generation.registered, tariff.write (accepted true/false, reason).
Standard library only.
"""

import json
import os
import signal
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

from etcd import Etcd, b64

INSTANCE = os.environ.get("INSTANCE", "coordinator")
ETCD_URL = os.environ.get("ETCD_URL", "http://localhost:2379")
API_URL = os.environ.get("API_URL", "http://localhost:8000").rstrip("/")
API_TOKEN = os.environ.get("API_TOKEN", "dev-token")
ROLE_KEY = os.environ.get("ROLE_KEY", "/roles/tariff-manager")
LEASE_TTL_S = int(os.environ.get("LEASE_TTL_S", "5"))
RENEW_EVERY_S = float(os.environ.get("RENEW_EVERY_S", "1.5"))
TARIFF_EVERY_S = float(os.environ.get("TARIFF_EVERY_S", "3"))
TARIFF_GARAGES = [g for g in os.environ.get("TARIFF_GARAGES", "mall,center").split(",") if g]
TARIFF_BASE = {
    k: int(v)
    for k, v in (p.split("=") for p in os.environ.get("TARIFF_BASE", "mall=3000,center=4000,dpc-race=2750").split(","))
}
ROUNDING = os.environ.get("ROUNDING") or ("up" if INSTANCE.endswith("-a") else "down")
WRITE_GAP_MS = int(os.environ.get("WRITE_GAP_MS", "0"))

stop = threading.Event()


def log(event: str, **fields: object) -> None:
    line = {
        "time": datetime.now(UTC).isoformat(timespec="milliseconds"),
        "instance": INSTANCE,
        "event": event,
    }
    sys.stdout.write(json.dumps({**line, **fields}, ensure_ascii=False) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- the resource (DPC api)


class Refused(Exception):
    """The resource answered 409: the generation is not (or no longer) the registered one."""

    def __init__(self, body: dict[str, Any]) -> None:
        super().__init__(body.get("title", "refused"))
        self.body = body


def api(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(
        API_URL + path, method=method, data=None if body is None else json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_TOKEN}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as reply:
            return json.loads(reply.read() or b"{}")
    except urllib.error.HTTPError as error:
        if error.code == 409:
            raise Refused(json.loads(error.read() or b"{}")) from None
        raise


def price(garage: str, occupied: int, spots: int) -> int:
    """base * (1 + occupied / spots) in kopecks, rounded to 100 up or down, in integers."""
    numerator, step = TARIFF_BASE.get(garage, 3000) * (spots + occupied), spots * 100
    hundreds = -(-numerator // step) if ROUNDING == "up" else numerator // step
    return max(hundreds, 1) * 100


# ---------------------------------------------------------------- the lease


class Lease:
    """Renews the lease in a thread; `lost` is set when etcd says it expired or nothing renewed it for the TTL."""

    def __init__(self, client: Etcd, lease: str) -> None:
        self.client, self.id = client, lease
        self.lost = threading.Event()
        self.reason = ""
        self.renewed_at = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="keepalive", daemon=True)

    def start(self) -> "Lease":
        self.thread.start()
        return self

    def lose(self, reason: str) -> None:
        if not self.lost.is_set():
            self.reason = reason
            self.lost.set()

    def _run(self) -> None:
        while not self.lost.is_set() and not stop.wait(RENEW_EVERY_S):
            try:
                ttl = self.client.keepalive(self.id)
            except OSError as error:
                if time.monotonic() - self.renewed_at >= LEASE_TTL_S:  # may have expired already
                    self.lose(f"keepalive-failed:{type(error).__name__}")
                continue
            if ttl <= 0:
                self.lose("lease-expired")
            else:
                self.renewed_at = time.monotonic()


# ---------------------------------------------------------------- holding the role


def acquire(client: Etcd) -> tuple[Lease, int] | int:
    """One attempt: (lease, generation) when this process took the role, else the etcd revision to watch from."""
    lease = client.grant(LEASE_TTL_S)
    reply = client.txn(
        compare=[{"key": b64(ROLE_KEY), "target": "CREATE", "create_revision": "0", "result": "EQUAL"}],
        success=[{"request_put": {"key": b64(ROLE_KEY), "value": b64(INSTANCE), "lease": lease}}],
        failure=[{"request_range": {"key": b64(ROLE_KEY)}}],
    )
    if not reply.get("succeeded"):
        client.revoke(lease)
        return int(reply["header"]["revision"])
    held = client.get(ROLE_KEY)
    if held is None or held["value"] != INSTANCE or str(held["lease"]) != str(lease):
        client.revoke(lease)  # gone already (expired at once): try again
        return int(reply["header"]["revision"])
    return Lease(client, lease).start(), held["create_revision"]


def register(lease: Lease, generation: int) -> list[str]:
    """Register the generation for every garage on the resource; retried while the lease lives (the api may be
    restarting). Returns the garages to manage (a garage without a tariff, 404, is skipped)."""
    garages = []
    for garage in TARIFF_GARAGES:
        while not lease.lost.is_set() and not stop.is_set():
            try:
                api("POST", "/v1/tariff/generations", {"garage_id": garage, "owner": INSTANCE,
                                                       "generation": generation})
                log("generation.registered", garage_id=garage, generation=str(generation))
                garages.append(garage)
                break
            except Refused as refused:
                lease.lose(f"registration-refused:{garage}:{refused.body.get('current_generation')}")
                return []
            except urllib.error.HTTPError as error:
                log("api.error", during="register", garage_id=garage, status=error.code)
                break
            except OSError as error:
                log("api.error", during="register", garage_id=garage, error=type(error).__name__)
                stop.wait(0.5)
    return [] if lease.lost.is_set() else garages


def write_tariffs(lease: Lease, generation: int, garages: list[str]) -> None:
    """The work of the owner until the role is lost: never writes after `lost` is seen, but a write that was
    already prepared goes out - the resource, not this process, refuses it."""
    while not lease.lost.is_set() and not stop.is_set():
        for garage in garages:
            if lease.lost.is_set() or stop.is_set():
                return
            try:
                state = api("GET", f"/v1/garage/{garage}")
                minor = price(garage, state["occupied"], state["spots"])
                log("tariff.prepared", garage_id=garage, minor_per_hour=minor, generation=str(generation),
                    occupied=state["occupied"], spots=state["spots"])
                if WRITE_GAP_MS:
                    time.sleep(WRITE_GAP_MS / 1000)  # the gap between the check and the write (O6.3)
                api("PUT", f"/v1/tariff/{garage}", {"minor_per_hour": minor, "owner": INSTANCE,
                                                    "generation": generation})
                log("tariff.write", garage_id=garage, minor_per_hour=minor, generation=str(generation),
                    accepted=True)
            except Refused as refused:
                reason = refused.body.get("reason", "refused")
                log("tariff.write", garage_id=garage, minor_per_hour=minor, generation=str(generation),
                    accepted=False, reason=reason, registered=refused.body.get("current_generation"))
                lease.lose(f"write-rejected:{reason}")
                return
            except (OSError, KeyError, ValueError) as error:
                log("api.error", during="write", garage_id=garage, error=type(error).__name__)
        stop.wait(TARIFF_EVERY_S)


def hold(client: Etcd, lease: Lease, generation: int) -> None:
    log("role.acquired", role=ROLE_KEY, generation=str(generation), lease=lease.id, lease_ttl_s=LEASE_TTL_S)
    garages = register(lease, generation)
    if garages:
        write_tariffs(lease, generation, garages)
    elif not lease.lost.is_set() and not stop.is_set():
        lease.lose("no-garages")
    if stop.is_set() and not lease.lost.is_set():
        lease.lose("stopping")
    log("role.lost", generation=str(generation), reason=lease.reason)
    try:
        client.revoke(lease.id)  # if it is still alive: the next owner need not wait for the TTL
        log("role.released", generation=str(generation))
    except OSError:
        pass
    if lease.reason == "no-garages":
        stop.wait(LEASE_TTL_S)  # nothing to manage: do not spin on the role


def wait_for_release(client: Etcd, revision: int) -> None:
    """Block until the role key is deleted after `revision` (or a timeout: then the caller simply tries again)."""
    try:
        for event in client.watch(ROLE_KEY, timeout_s=LEASE_TTL_S * 2, start_revision=revision + 1):
            if event["type"] == "DELETE" or stop.is_set():
                return
    except OSError:
        return


def main() -> None:
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    client = Etcd(ETCD_URL)
    log("coordinator.started", role=ROLE_KEY, lease_ttl_s=LEASE_TTL_S, renew_every_s=RENEW_EVERY_S,
        garages=TARIFF_GARAGES, rounding=ROUNDING, write_gap_ms=WRITE_GAP_MS)

    waiting = False
    while not stop.is_set():
        try:
            got = acquire(client)
        except OSError as error:
            log("etcd.error", error=type(error).__name__)
            stop.wait(1)
            continue
        if isinstance(got, int):
            if not waiting:  # once per wait, not on every timeout of the watch
                log("role.waiting", role=ROLE_KEY, revision=got)
                waiting = True
            wait_for_release(client, got)
        else:
            waiting = False
            hold(client, *got)

    log("coordinator.stopped")


if __name__ == "__main__":
    main()
