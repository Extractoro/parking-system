"""Watch the owner key of a singleton role in etcd (lab 6): who owns it, its revision, and how
long the role had no owner. Helps to measure the role transition time of O6.1.
Standard library only.

Run from the project root:

    python tools/etcd_watch.py /roles/assigner
    python tools/etcd_watch.py /roles/assigner --etcd http://localhost:2379 --every 0.2

Prints a line on every change seen by polling (a gap shorter than --every may be missed):

    12:00:03.412  owner coordinator-a  create_revision 17  lease 694d8...  ttl 5
    12:00:11.950  NO OWNER
    12:00:14.108  owner coordinator-b  create_revision 23  lease 694da...  ttl 5  (no owner for 2.16 s)

Uses the JSON gateway of etcd v3 (POST /v3/kv/range, /v3/lease/timetolive), where keys and
values are base64.
"""

import argparse
import base64
import contextlib
import json
import math
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime

RETRY_UNREACHABLE_S = 1.0


@dataclass(frozen=True)
class Owner:
    name: str
    revision: int
    lease: str
    ttl: int | None


def post(etcd: str, path: str, body: dict) -> dict:
    data = json.dumps(body).encode()
    request = urllib.request.Request(
        f"{etcd}{path}", data=data, method="POST", headers={"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(request, timeout=2) as response:
        return json.load(response)


def read_owner(etcd: str, key: str) -> Owner | None:
    """The current owner of the key, or None if nobody holds the role."""
    encoded_key = base64.b64encode(key.encode()).decode()
    kvs = post(etcd, "/v3/kv/range", {"key": encoded_key}).get("kvs", [])
    if not kvs:
        return None

    kv = kvs[0]
    lease = kv.get("lease", "0")
    ttl = post(etcd, "/v3/lease/timetolive", {"ID": lease}).get("TTL") if lease != "0" else None
    return Owner(base64.b64decode(kv["value"]).decode(), int(kv["create_revision"]), lease, ttl)


def stamp() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def describe(owner: Owner, absent_for_s: float | None) -> str:
    gap = "" if absent_for_s is None else f"  (no owner for {absent_for_s:.2f} s)"
    ttl = "-" if owner.ttl is None else owner.ttl
    return f"owner {owner.name}  create_revision {owner.revision}  lease {owner.lease}  ttl {ttl}{gap}"


def watch(etcd: str, key: str, every_s: float) -> None:
    seen = False
    last: Owner | None = None
    absent_since: float | None = None

    while True:
        try:
            owner = read_owner(etcd, key)
        except OSError as error:
            print(f"{stamp()}  UNKNOWN: etcd unreachable: {error}")
            seen, absent_since = False, None
            time.sleep(RETRY_UNREACHABLE_S)
            continue

        if not (seen and _same_holder(owner, last)):
            if owner is None:
                absent_since = time.monotonic()
                print(f"{stamp()}  NO OWNER")
            else:
                absent_for = None if absent_since is None else time.monotonic() - absent_since
                print(f"{stamp()}  {describe(owner, absent_for)}")
                absent_since = None
            seen, last = True, owner

        time.sleep(every_s)


def _same_holder(current: Owner | None, previous: Owner | None) -> bool:
    """The same holder of the same generation (TTL changes on every poll and is ignored)."""
    if current is None or previous is None:
        return current is previous
    return (current.name, current.revision) == (previous.name, previous.revision)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("key")
    parser.add_argument("--etcd", default="http://localhost:2379")
    parser.add_argument("--every", type=float, default=0.2, help="poll period, seconds")
    args = parser.parse_args()

    if not math.isfinite(args.every) or args.every <= 0:
        parser.error("--every must be a positive number of seconds")

    with contextlib.suppress(KeyboardInterrupt):
        watch(args.etcd, args.key, args.every)


if __name__ == "__main__":
    main()
