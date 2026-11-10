"""The API process (app-a, app-b): `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Your work goes into new modules that you include below. Where each lab plugs in:
  TODO (O1.4, O1.5) routers of your topic: the main operation and the list with a cursor
  TODO (O2.1-O2.3)  invariant under concurrency, idempotency keys, fault("after_commit_before_response")
  O3.1-O3.3         login and logout (auth.py, users.py), owner in SQL (bookings.py), staff function
                    (catalog.py: close/open a level), CORS for CLIENT_ORIGIN (below)
  TODO (O4.1-O4.4)  long operation, outbox in the same transaction (publisher and handler are
                    separate processes: app/publisher.py, app/worker.py)
  TODO (O5.1)       POST /v1/ws-tickets; the gateway itself is app/gateway.py
  TODO (O6.3)       SIGTERM: lifecycle.start_draining(), finish started requests
The contract of all routes is your openapi.yaml (start from openapi.example.yaml).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import auth, bookings, cache, catalog, config, db, metrics, middleware, problems, service, telemetry
from .jsonlog import log


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.open_pool()

    log("app.started")
    yield

    await cache.close()
    await db.close_pool()

    log("app.stopped")


# The contract is openapi.yaml of your repository, so the generated schema routes are off.
app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
middleware.install(app)
problems.install(app)
app.include_router(service.router)
app.include_router(metrics.router)
telemetry.install(app)
app.include_router(auth.router)
app.include_router(catalog.router)
app.include_router(bookings.router)

# CORS (O3.3): the browser client (Vite, outside Compose) calls the API through nginx from exactly
# CLIENT_ORIGIN, never "*" and never the Origin echoed back. Any other origin gets no
# Access-Control-Allow-Origin, so the browser does not let that page read the answer, and its preflight
# (needed for Authorization / JSON) is refused with 400 before the request itself is sent.
# No credentials (cookies) are allowed: the login is a token in the Authorization header, which the
# browser never adds on its own, so a page of another site cannot send a request as the user (no CSRF).
# Added last, so it is the outermost middleware: preflight and error answers also get the CORS headers.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.CLIENT_ORIGIN],
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "If-Match", "X-Request-ID"],
    expose_headers=["Location", "ETag", "Retry-After", "X-Request-ID", "Idempotent-Replayed", "X-Instance"],
    max_age=600,
)
