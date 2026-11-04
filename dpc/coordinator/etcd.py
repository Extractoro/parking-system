"""Thin client of the etcd v3 JSON gateway (standard library only): lease, keepalive, txn, range, watch.

    client = Etcd("http://etcd:2379")
    lease = client.grant(5)
    client.keepalive(lease)
    client.get("/roles/assigner")      # {"value", "create_revision", "mod_revision", "lease"} or None

A transaction - here a compare-and-swap of a config value, changed only if nobody changed it
since we read it (keys and values in base64):

    current = client.get("/config/limit")
    reply = client.txn(
        compare=[{"key": b64("/config/limit"), "target": "MOD", "mod_revision": current["mod_revision"],
                  "result": "EQUAL"}],
        success=[{"request_put": {"key": b64("/config/limit"), "value": b64("20")}}],
        failure=[{"request_range": {"key": b64("/config/limit")}}],
    )
    reply["succeeded"]   # False - someone was first; the failure branch returned the current value

Other targets of compare: "CREATE" (create_revision, 0 - the key does not exist), "VALUE", "LEASE".
Taking the role, the generation and fencing on the resource are yours (O6.1-O6.3).
"""

import base64
import json
import urllib.request
from collections.abc import Iterator
from typing import Any


def b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def _text(data: str) -> str:
    return base64.b64decode(data).decode()


class Etcd:
    def __init__(self, url: str, timeout_s: float = 2.0) -> None:
        self.url = url.rstrip("/")
        self.timeout_s = timeout_s

    def call(self, path: str, body: dict[str, Any], timeout_s: float | None = None) -> dict[str, Any]:
        request = urllib.request.Request(
            self.url + path, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=timeout_s or self.timeout_s) as reply:
            return json.loads(reply.read() or b"{}")

    def grant(self, ttl_s: int) -> str:
        return self.call("/v3/lease/grant", {"TTL": ttl_s})["ID"]

    def keepalive(self, lease: str) -> int:
        """Returns the remaining TTL; 0 means the lease has already expired."""
        result = self.call("/v3/lease/keepalive", {"ID": lease}).get("result", {})
        return int(result.get("TTL", 0))

    def revoke(self, lease: str) -> None:
        self.call("/v3/lease/revoke", {"ID": lease})

    def txn(self, compare: list[dict], success: list[dict], failure: list[dict] | None = None) -> dict[str, Any]:
        """One atomic transaction of the etcd API: keys and values inside are base64 (use b64())."""
        return self.call("/v3/kv/txn", {"compare": compare, "success": success, "failure": failure or []})

    def put(self, key: str, value: str, lease: str | None = None) -> int:
        body = {"key": b64(key), "value": b64(value), **({"lease": lease} if lease else {})}
        return int(self.call("/v3/kv/put", body)["header"]["revision"])

    def get(self, key: str) -> dict[str, Any] | None:
        kvs = self.call("/v3/kv/range", {"key": b64(key)}).get("kvs", [])
        if not kvs:
            return None

        kv = kvs[0]
        return {
            "value": _text(kv.get("value", "")),
            "create_revision": int(kv["create_revision"]),
            "mod_revision": int(kv["mod_revision"]),
            "lease": kv.get("lease"),
        }

    def delete(self, key: str) -> None:
        self.call("/v3/kv/deleterange", {"key": b64(key)})

    def watch(self, key: str, timeout_s: float = 3600) -> Iterator[dict[str, Any]]:
        """Yields events of the key: {"type": "PUT" | "DELETE", "value": ..., "create_revision": ...}."""
        request = urllib.request.Request(
            self.url + "/v3/watch",
            data=json.dumps({"create_request": {"key": b64(key)}}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(request, timeout=timeout_s) as stream:
            for line in stream:
                for event in json.loads(line).get("result", {}).get("events", []):
                    kv = event["kv"]
                    yield {
                        "type": event.get("type", "PUT"),
                        "value": _text(kv.get("value", "")),
                        "create_revision": int(kv.get("create_revision", 0)),
                    }
