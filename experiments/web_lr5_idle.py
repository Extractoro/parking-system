"""Web lab 5: a quiet connection through nginx (proxy_read_timeout 60 s) stays open for 75 s.

    uv run --no-project --python 3.13 --with httpx --with "psycopg[binary]" --with websockets \
        python experiments/web_lr5_idle.py | tee results/web-lr5/idle.txt

uvicorn (websockets) sends a ping every 20 s, the client answers with pong: nginx sees traffic both ways.
"""

import asyncio
import json
import os
import sys
import time

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

sys.path.insert(0, os.path.dirname(__file__))
import web_lr5 as w  # noqa: E402


async def main() -> None:
    user = w.new_user()
    token = w.login(user)
    async with httpx.AsyncClient() as http:
        t = await w.ticket(http, token)
    t0 = time.monotonic()
    async with connect(f"{w.WS_NGINX}/v1/ws?ticket={t}", origin=w.ORIGIN) as ws:
        print("connected through nginx:", json.loads(await ws.recv()), flush=True)
        try:
            await asyncio.wait_for(ws.recv(), timeout=75)
            print("unexpected frame")
        except TimeoutError:
            pong = await ws.ping()
            await asyncio.wait_for(pong, 5)
            print(f"after {time.monotonic() - t0:.1f} s without notifications: open, ping answered", flush=True)
        except ConnectionClosed as exc:
            print(f"closed after {time.monotonic() - t0:.1f} s: {exc}", flush=True)


asyncio.run(main())
