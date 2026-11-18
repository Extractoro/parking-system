"""The `api` process: `uvicorn app.main:app --host 0.0.0.0 --port 8000`.

Where each lab plugs in (item numbers of the guide):
  DONE (O1.1-O1.5, D1.4) app/entries.py: POST/GET /v1/entries, GET /v1/garage/{id}, the bounded
                    queue, the worker pool, the garage lock, the time marks and the SIGTERM drain
  DONE (O2.1-O2.4)  app/compute.py, app/integrate.py: POST/GET /v1/compute, the process pool, the shared
                    sum modes none | lock | local (D2.5, D2.6 not chosen)
  DONE (O3.1-O3.4)  app/raytasks.py: the Ray task (this image also runs ray-head and ray-worker);
                    app/entries.py: the dispatcher (EXECUTION=ray, IN_FLIGHT_LIMIT), the receiver
                    POST /internal/effects with the effect table, fault("after_effect_before_ack")
                    in the task (D3.3, D3.4 not chosen)
  DONE (O4.2-O4.5, D4.3) app/reports.py: POST /v1/reports/main/refresh -> spark-runner (RUNNER_URL),
                    GET /v1/reports/main (pointer /data/reports/current), app/reference.py: GET
                    /v1/reports/main/reference without Spark (D4.1, D4.2, D4.4 not chosen)
  DONE (O5.1-O5.5)  app/panel.py: GET /v1/panel (panel_window of spark/jobs/stream.py), GET /v1/panel/batch
                    without Spark over the same JSONL (D5.1, D5.2 not chosen)
  TODO (O6.x)       GET /v1/coordinator, /v1/history; coordinators are separate processes
Protect your /v1 routers with `dependencies=[Depends(auth.require_token)]`.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import compute, config, db, entries, middleware, panel, problems, reports, service
from .jsonlog import log


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    await db.open_pool()
    # O1.2, O1.3: the bounded queue and exactly WORKERS workers; D1.4: SIGTERM drains the queue first
    await entries.start()
    compute.start()  # lab 2: gauges; jobs start on POST /v1/compute
    log("api.started")

    yield

    # D1.4: by now SIGTERM has drained the queue (api.drained); stop the idle workers, then the pool
    await entries.stop()
    await compute.stop()
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
# O1.2: outermost layer, a full queue is refused before routing (entries.Admission)
app.add_middleware(entries.Admission)
problems.install(app)
app.include_router(service.router)
app.include_router(entries.router)
app.include_router(entries.internal_router)  # the effect receiver of lab 3; nginx does not publish the api
app.include_router(compute.router)
app.include_router(reports.router)
app.include_router(panel.router)
