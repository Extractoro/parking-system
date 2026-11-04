"""Service routes: GET /health, GET /metrics and GET /metrics/prom (open, without the token)."""

from fastapi import APIRouter
from fastapi.responses import PlainTextResponse

from . import config, metrics

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness without the database: item O1.2 measures its p95 under full load."""
    return {"status": "ok", "instance": config.INSTANCE}


@router.get("/metrics")
async def get_metrics() -> dict[str, object]:
    return metrics.snapshot()


@router.get("/metrics/prom")
async def get_metrics_prometheus() -> PlainTextResponse:
    return PlainTextResponse(metrics.prometheus(), media_type="text/plain; version=0.0.4")
