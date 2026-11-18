"""A script client of the notification feed (Web lab 5): the same algorithm as the browser client
(web/client/src/notifications.ts), used by the experiments of experiments/web_lr5.py.

One session of the client:
  1. POST /v1/ws-tickets -> ticket; connect {ws}/v1/ws?ticket=... with Origin; the first frame is hello;
     from now on every live frame goes into a buffer (a queue).
  2. (experiment only) pause `pause_s` between the subscription and head: the "window" of O5.3.
  3. GET /v1/me/notifications/head -> head.
  4. GET /v1/me/notifications?after=last_applied page by page until head; apply each.
  5. Live frames from the buffer, then as they come: position <= last_applied -> skip (already applied),
     position > last_applied + 1 -> read the history up to it first (a gap), then apply.
On a close: a pause that grows (0.5, 1, 2, 4 ... s, capped) and a new session with a new ticket from
last_applied. A close with 1008 after the connection was open (logout) ends the client.
"""

import asyncio
import contextlib
import json
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

ORIGIN = "http://localhost:5173"
CLOCK: float | None = None  # the zero of the log times; the script sets it


class FeedClient:
    def __init__(self, name: str, api: str, token: str, ws_urls: list[str], *, origin: str = ORIGIN,
                 pause_s: float = 0.0, drop_share: float = 0.0, seed: int = 1, quiet: bool = False,
                 on_subscribed: Callable[[], Awaitable[None]] | None = None, t0: float | None = None) -> None:
        self.name, self.api, self.token, self.ws_urls, self.origin = name, api, token, ws_urls, origin
        self.pause_s, self.drop_share, self.rng, self.quiet = pause_s, drop_share, random.Random(seed), quiet
        self.on_subscribed = on_subscribed
        self.last_applied = 0
        self.applied: list[dict] = []  # in the order of application
        self.times_applied: Counter[str] = Counter()  # notification id -> how many times applied
        self.skipped = 0  # live frames already applied (duplicates of the history)
        self.via: Counter[str] = Counter()  # how the applied ones came: live / history
        self.sessions = 0
        self.gateways: list[str] = []
        self.close_codes: list[int | None] = []
        self.revoked = asyncio.Event()  # closed with 1008 after it was open
        self.online = asyncio.Event()
        self.t0 = t0 if t0 is not None else (CLOCK if CLOCK is not None else time.monotonic())

    def log(self, event: str, **fields) -> None:
        if not self.quiet:
            extra = " ".join(f"{k}={v}" for k, v in fields.items())
            print(f"{time.monotonic() - self.t0:7.2f}s [{self.name}] {event} {extra}".rstrip(), flush=True)

    # --- HTTP -----------------------------------------------------------------------------------------
    async def _get(self, http: httpx.AsyncClient, path: str, **params) -> dict:
        r = await http.get(self.api + path, params=params, headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()

    async def ticket(self, http: httpx.AsyncClient) -> str:
        r = await http.post(self.api + "/v1/ws-tickets", headers={"Authorization": f"Bearer {self.token}"})
        r.raise_for_status()
        return r.json()["ticket"]

    # --- applying ---------------------------------------------------------------------------------------
    def _apply(self, item: dict, via: str) -> None:
        if item["position"] <= self.last_applied:
            self.skipped += 1
            self.log("skip", position=item["position"], via=via, reason="already applied")
            return
        assert item["position"] == self.last_applied + 1, (item["position"], self.last_applied)
        self.last_applied = item["position"]
        self.applied.append(item)
        self.times_applied[item["id"]] += 1
        self.via[via] += 1
        self.log("apply", position=item["position"], via=via, type=item["type"], id=item["id"][:8])

    async def catch_up(self, http: httpx.AsyncClient, target: int) -> None:
        """History after last_applied until target (head or the position of a gap)."""
        while self.last_applied < target:
            page = await self._get(http, "/v1/me/notifications", after=self.last_applied, limit=50)
            if not page["items"]:
                break
            for item in page["items"]:
                self._apply(item, "history")

    # --- one session --------------------------------------------------------------------------------------
    async def session(self, http: httpx.AsyncClient, url: str) -> None:
        ticket = await self.ticket(http)
        async with connect(f"{url}/v1/ws?ticket={ticket}", origin=self.origin, open_timeout=10) as ws:
            hello = json.loads(await ws.recv())
            self.sessions += 1
            self.gateways.append(hello.get("gateway"))
            self.log("connected", url=url, gateway=hello.get("gateway"), last_applied=self.last_applied)
            buffer: asyncio.Queue = asyncio.Queue()

            async def pump() -> None:
                try:
                    async for raw in ws:
                        frame = json.loads(raw)
                        buffer.put_nowait(frame)
                        self.log("received", position=frame.get("position"))
                except ConnectionClosed:
                    pass
                buffer.put_nowait(None)

            reader = asyncio.create_task(pump())

            async def drop_later() -> None:  # a random break of the connection while it is in use
                await asyncio.sleep(self.rng.uniform(0.2, 0.8))
                self.log("drop", reason="random disconnect by the client (timer)")
                await ws.close()

            dropper = asyncio.create_task(drop_later()) if self.drop_share else None
            try:
                if self.on_subscribed is not None:
                    await self.on_subscribed()
                if self.pause_s:
                    self.log("pause", seconds=self.pause_s)
                    await asyncio.sleep(self.pause_s)
                head = (await self._get(http, "/v1/me/notifications/head"))["position"]
                self.log("head", position=head, buffered=buffer.qsize())
                await self.catch_up(http, head)
                self.online.set()
                while (frame := await buffer.get()) is not None:
                    if frame["position"] > self.last_applied + 1:
                        self.log("gap", position=frame["position"], last_applied=self.last_applied)
                        await self.catch_up(http, frame["position"])
                    self._apply(frame, "live")
                    if self.drop_share and self.rng.random() < self.drop_share:
                        self.log("drop", reason="random disconnect by the client")
                        await ws.close()
            finally:
                self.online.clear()
                reader.cancel()
                if dropper is not None:
                    dropper.cancel()
                code = ws.close_code
                self.close_codes.append(code)
                self.log("closed", code=code)
                if code == 1008:
                    self.revoked.set()

    async def run(self, stop: asyncio.Event) -> None:
        attempt = 0
        async with httpx.AsyncClient(timeout=10) as http:
            while not stop.is_set() and not self.revoked.is_set():
                url = self.ws_urls[min(self.sessions, len(self.ws_urls) - 1)]
                session = asyncio.create_task(self.session(http, url))
                stopping = asyncio.create_task(stop.wait())
                await asyncio.wait({session, stopping}, return_when=asyncio.FIRST_COMPLETED)
                stopping.cancel()
                if not session.done():  # the experiment is over: leave the session
                    session.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await session
                    break
                try:
                    session.result()
                    attempt = 0
                except (OSError, InvalidStatus, ConnectionClosed, httpx.HTTPError, TimeoutError) as exc:
                    attempt += 1
                    self.log("connect_failed", url=url, error=type(exc).__name__, attempt=attempt)
                if self.revoked.is_set() or stop.is_set():
                    break
                pause = min(0.5 * 2 ** attempt, 8.0) * (0.75 + self.rng.random() / 2)
                self.log("reconnect_in", seconds=round(pause, 2))
                try:
                    await asyncio.wait_for(stop.wait(), timeout=pause)
                except TimeoutError:
                    pass
