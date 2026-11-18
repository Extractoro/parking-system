"""DPC lab 5: the batch calculation of the panel (O5.5) on the control example of the kit and edge cases.

The first tests are pure (app/panel_batch.py, no stand); the last one compares /v1/panel with /v1/panel/batch on
the stand after the main run (docker compose up -d stream, events of normal.jsonl sent to barrier-events;
README, DPC lab 5) and is skipped when the stream has written nothing yet.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import panel_batch as panel

CONTROL = ROOT / "dpc" / "tools" / "generator" / "control"


def control_lines() -> list[str]:
    """The control JSONL of the kit with the key renamed gate_id -> garage_id (as events_c09.sh does)."""
    return [line.replace('"gate_id"', '"garage_id"') for line in (CONTROL / "events.jsonl").read_text().splitlines()]


def ev(event_id: str, at: str, garage: str = "mall", direction: str = "in") -> str:
    return json.dumps({"event_id": event_id, "garage_id": garage, "plate": "AA0000AA", "direction": direction,
                       "event_time": at})


def test_control_example_matches_expected():
    """15-minute windows of the kit: 08:14:59.999 is in the first window, 08:15:00.000 in the second."""
    expected = json.loads((CONTROL / "expected.json").read_text())
    got = panel.compute(control_lines(), window_minutes=15, watermark_minutes=5)["windows"]
    assert [(w["window_start"], w["garage_id"], w["window_end"], w["entries"], w["exits"]) for w in got] == sorted(
        (e["window_start"], e["gate_id"], e["window_end"], e["in"], e["out"]) for e in expected
    )


def test_window_boundaries_of_5_minutes():
    lines = [ev("a", "2026-11-16T07:04:59.999Z"), ev("b", "2026-11-16T07:05:00.000Z", direction="out"),
             ev("tick", "2026-11-16T08:00:00.000Z", garage="tick", direction="tick")]
    windows = panel.compute(lines, 5, 5)["windows"]
    assert [(w["window_start"], w["entries"], w["exits"]) for w in windows] == [
        ("2026-11-16T07:00:00.000Z", 1, 0), ("2026-11-16T07:05:00.000Z", 0, 1)]


def test_duplicates_are_counted_once_and_tick_is_not_counted():
    lines = [ev("a", "2026-11-16T07:01:00.000Z"), ev("a", "2026-11-16T07:01:00.000Z"),
             ev("tick", "2026-11-16T07:20:00.000Z", garage="tick", direction="tick")]
    result = panel.compute(lines, 5, 5)
    assert result["duplicates"] == 1 and result["ticks"] == 1 and result["events"] == 1
    assert [(w["garage_id"], w["entries"]) for w in result["windows"]] == [("mall", 1)]


def test_only_windows_before_the_watermark_are_final():
    """max event time 07:12 - watermark 5 min = 07:07: [07:00, 07:05) is final, [07:05, 07:10) is not."""
    lines = [ev("a", "2026-11-16T07:01:00.000Z"), ev("b", "2026-11-16T07:06:00.000Z"),
             ev("c", "2026-11-16T07:12:00.000Z")]
    result = panel.compute(lines, 5, 5)
    assert [w["window_start"] for w in result["windows"]] == ["2026-11-16T07:00:00.000Z"]
    assert result["not_final"] == 2


def test_late_event_in_file_is_counted_by_batch():
    """The batch calculation does not model lateness: a late delivery still counts in its event-time window."""
    lines = [ev("a", "2026-11-16T07:09:00.000Z"), ev("late", "2026-11-16T07:01:00.000Z"),
             ev("tick", "2026-11-16T07:30:00.000Z", garage="tick", direction="tick")]
    assert [w["window_start"] for w in panel.compute(lines, 5, 5)["windows"]] == [
        "2026-11-16T07:00:00.000Z", "2026-11-16T07:05:00.000Z"]


def test_stream_panel_equals_batch(api):
    stream = api.get("/v1/panel", params={"run": "main"})
    assert stream.status_code == 200
    if not stream.json()["windows"]:
        pytest.skip("the main run has written no windows yet")
    batch = api.get("/v1/panel/batch", params={"run": "main"})
    assert batch.status_code == 200
    keys = ("garage_id", "window_start", "window_end", "entries", "exits")
    assert [{k: w[k] for k in keys} for w in stream.json()["windows"]] == [
        {k: w[k] for k in keys} for w in batch.json()["windows"]]


def test_panel_requires_token(api):
    assert api.get("/v1/panel", headers={"Authorization": ""}).status_code == 401


def stream_sql(name: str) -> str:
    """A SQL constant of dpc/spark/jobs/stream.py (read without importing pyspark): the test runs the real text."""
    import ast

    tree = ast.parse((ROOT / "dpc" / "spark" / "jobs" / "stream.py").read_text(encoding="utf-8"))
    return next(n.value.value for n in tree.body if isinstance(n, ast.Assign) and n.targets[0].id == name)


def test_repeated_micro_batch_overwrites_the_same_window(db):
    """O5.4: foreachBatch of the same batch_id twice (a replay after a restart) leaves one row with the same values."""
    import uuid
    from datetime import UTC, datetime

    run = f"t-{uuid.uuid4().hex[:8]}"
    start, end = datetime(2026, 11, 16, 7, 0, tzinfo=UTC), datetime(2026, 11, 16, 7, 5, tzinfo=UTC)
    db.execute("INSERT INTO panel_run (run_id, topic, window_minutes, watermark_minutes) VALUES (%s, 't', 5, 5)",
               (run,))
    try:
        for _ in range(2):
            with db.transaction():
                db.execute(stream_sql("UPSERT"), (run, "mall", start, end, 232, 234, 4))
        found = db.execute("SELECT entries, exits, batch_id FROM panel_window WHERE run_id = %s", (run,)).fetchall()
        assert found == [{"entries": 232, "exits": 234, "batch_id": 4}]
    finally:
        db.execute("DELETE FROM panel_window WHERE run_id = %s", (run,))
        db.execute("DELETE FROM panel_run WHERE run_id = %s", (run,))
