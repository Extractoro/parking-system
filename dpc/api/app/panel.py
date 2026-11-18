"""DPC lab 5: the panel of the streaming job (O5.2, O5.4) and the same windows without Spark (O5.5).

  GET /v1/panel?run=main        -> {run, windows}: finalized windows written by the streaming job (panel_window)
  GET /v1/panel/batch?run=main  -> {run, file, watermark, windows, ...}: app/panel_batch.py over the JSONL file
                                   the run was fed from (panel_run.events_file, else PANEL_BATCH_FILE), with
                                   the window and the watermark of that run
Windows are sorted by (window_start, garage_id); keys of the comparison: garage_id, window_start.
"""

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, Query

from . import auth, config, db
from .panel_batch import compute_file, iso
from .problems import Problem

router = APIRouter(prefix="/v1/panel", dependencies=[Depends(auth.require_token)])

RUN = Query("main", min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")


async def _run(run: str) -> dict[str, Any] | None:
    async with db.connection() as conn:
        cur = await conn.execute("SELECT * FROM panel_run WHERE run_id = %s", (run,))
        return await cur.fetchone()


@router.get("")
async def get_panel(run: str = RUN) -> dict[str, Any]:
    async with db.connection() as conn:
        cur = await conn.execute(
            """SELECT garage_id, window_start, window_end, entries, exits, batch_id, written_at
                 FROM panel_window WHERE run_id = %s ORDER BY window_start, garage_id""",
            (run,),
        )
        rows = await cur.fetchall()
    return {
        "run": run,
        "windows": [
            {
                "garage_id": r["garage_id"],
                "window_start": iso(r["window_start"]),
                "window_end": iso(r["window_end"]),
                "entries": r["entries"],
                "exits": r["exits"],
                "batch_id": str(r["batch_id"]),
                "written_at": iso(r["written_at"]),
            }
            for r in rows
        ],
    }


@router.get("/batch")
async def get_panel_batch(run: str = RUN) -> dict[str, Any]:
    meta = await _run(run)
    path = (meta and meta["events_file"]) or config.PANEL_BATCH_FILE
    window = meta["window_minutes"] if meta else config.PANEL_WINDOW_MINUTES
    watermark = meta["watermark_minutes"] if meta else config.PANEL_WATERMARK_MINUTES
    try:
        result = await asyncio.to_thread(compute_file, path, window, watermark)
    except FileNotFoundError:
        raise Problem(404, "no-events-file", "No event file for the batch calculation",
                      f"{path} not found; generate the sets (README, DPC lab 5)") from None
    return {"run": run, "file": path, "window_minutes": window, "watermark_minutes": watermark, **result}
