"""The `api` process: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Where each lab plugs in (item numbers of the guide):
  TODO (O1.1-O1.5, D1.4) the receiver route of your card (202), the bounded queue and
                    the worker pool started in `lifespan`, the shared resource and its lock, the
                    time log; metrics.gauge("queue_length", ...) for /metrics (O1.5)
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

from . import config, db, middleware, problems, service
from .jsonlog import log


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.open_pool()
    # TODO (O1.2, O1.3): create the bounded queue and start the worker pool here
    log("api.started")

    yield

    # TODO (D1.4): stop accepting, process what was accepted, then stop the workers
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
# app.include_router(your_module.router)
