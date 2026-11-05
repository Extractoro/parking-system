"""The `api` process: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Where each lab plugs in (item numbers of the guide):
  DONE (O1.1-O1.5, D1.4) app/entries.py: POST/GET /v1/entries, GET /v1/garage/{id}, the bounded
                    queue, the worker pool, the garage lock, the time marks and the SIGTERM drain
  TODO (O2.1-O2.4, D2.5, D2.6)  POST /v1/compute and its state, the parallel computation, cancel
  TODO (O3.1-O3.4, D3.3, D3.4)  Ray tasks (this image also runs ray-head and ray-worker), the effect
                    table and fault("after_effect_before_ack")
  TODO (O4.2)       POST /v1/reports/main/refresh calls the given Spark runner (RUNNER_URL)
  TODO (O5.x)       GET /v1/panel, /v1/panel/batch: the windows written by your stream job
  TODO (O6.x)       GET /v1/coordinator, /v1/history; coordinators are separate processes
Protect your /v1 routers with `dependencies=[Depends(auth.require_token)]`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import config, db, entries, middleware, problems, service
from .jsonlog import log


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.open_pool()
    # O1.2, O1.3: the bounded queue and exactly WORKERS workers; D1.4: SIGTERM drains the queue first
    await entries.start()
    log("api.started")

    yield

    # D1.4: by now SIGTERM has drained the queue (api.drained); stop the idle workers, then the pool
    await entries.stop()
    await db.close_pool()
    log("api.stopped")


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
middleware.install(app)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[config.CLIENT_ORIGIN],
    allow_methods=["*"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
    expose_headers=["Location", "Retry-After", "X-Request-ID"],
)
problems.install(app)
app.include_router(service.router)
app.include_router(entries.router)
