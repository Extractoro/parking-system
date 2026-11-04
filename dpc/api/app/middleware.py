"""Request id, one JSON log line per request and the request metrics.

/health and /metrics are not counted in the latencies: under load they would hide the
latency of the real requests (they are still logged).
"""

import re
import time
import uuid

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import RequestResponseEndpoint

from . import config, metrics, problems
from .jsonlog import log

_SANE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_SERVICE = {"/health", "/metrics", "/metrics/prom"}


async def observe(request: Request, call_next: RequestResponseEndpoint) -> Response:
    incoming = request.headers.get("x-request-id", "")
    request_id = incoming if _SANE_ID.match(incoming) else str(uuid.uuid4())
    request.state.request_id = request_id

    started = time.perf_counter()
    status = 500
    metrics.request_started()
    try:
        try:
            response = await call_next(request)
        except Exception as exc:  # noqa: BLE001 - the answer is built here, so it gets the headers below
            response = problems.crash(request, exc)
        status = response.status_code
    finally:
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = getattr(request.scope.get("route"), "path", "unmatched")
        metrics.request_finished(status, elapsed_ms, count_latency=route not in _SERVICE)
        log(
            "http.request",
            request_id=request_id,
            method=request.method,
            route=route,
            status=status,
            duration_ms=round(elapsed_ms, 1),
        )

    response.headers["X-Request-ID"] = request_id
    response.headers["X-Instance"] = config.INSTANCE
    return response


def install(app: FastAPI) -> None:
    app.middleware("http")(observe)
