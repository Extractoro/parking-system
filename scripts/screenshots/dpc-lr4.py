"""Fragments of the DPC lab 4 report: the Report page of the client, the join stage in the Spark UI before and
after AQE, the lost executor of O4.5.

Uses the installed Microsoft Edge (channel "msedge"); the Spark UI pictures come from the history server
(docker compose --profile extras up -d spark-history, :18080), which reads the event logs in /data/spark-events:

    uv run --no-project --python 3.13 --with playwright python scripts/screenshots/dpc-lr4.py report|stages|executor

report    the client (npm --prefix web/client run dev, :5173), page Report after a refresh of the small set: the table
          of the published report with the badge "Matches the reference". Writes docs/reports/img/dpc-lr4-report.png.
stages    the first run of each variant of results/dpc-lr4/skew.json: the "Summary Metrics" table (task duration,
          shuffle read: min, 25th percentile, median, 75th percentile, max) of the join stage.
          Writes docs/reports/img/dpc-lr4-stage-aqe-off.png and dpc-lr4-stage-aqe-skew.png.
executor  the application of results/dpc-lr4/executor.json: the executor table with the dead executor of
          spark-worker-1 and the failed tasks. Writes docs/reports/img/dpc-lr4-executor.png.
"""

import json
import os
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
RESULTS = ROOT / "results" / "dpc-lr4"
CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
HISTORY = os.environ.get("SPARK_HISTORY_URL", "http://localhost:18080")


def report(page: Page) -> None:
    page.set_viewport_size({"width": 1200, "height": 1000})
    page.goto(f"{CLIENT}/?view=report")
    badge = page.get_by_test_id("reference-match")
    expect(badge).to_have_text("Matches the reference", timeout=60000)
    page.mouse.move(5, 5)
    page.wait_for_timeout(800)
    page.locator(".columns").screenshot(path=OUT / "dpc-lr4-report.png")


def stages(page: Page) -> None:
    data = json.loads((RESULTS / "skew.json").read_text(encoding="utf-8"))
    page.set_viewport_size({"width": 1300, "height": 1200})
    for variant in ("aqe-off", "aqe-skew"):
        run = next(r for r in data["runs"] if r["variant"] == variant)
        app = run["app"]
        page.goto(f"{HISTORY}/history/{app}/stages/stage/?id={run['join']['stage']}&attempt={run['join']['attempt']}")
        table = page.locator("table").filter(has_text="Duration").filter(has_text="Median").first
        expect(table).to_be_visible(timeout=60000)
        page.wait_for_timeout(800)
        title = page.get_by_text("Summary Metrics for", exact=False).first
        box_t, box = title.bounding_box(), table.bounding_box()
        page.screenshot(path=OUT / f"dpc-lr4-stage-{variant}.png", clip={
            "x": box["x"] - 4, "y": box_t["y"] - 4, "width": box["width"] + 8,
            "height": box["y"] + box["height"] - box_t["y"] + 8})


def executor(page: Page) -> None:
    app = json.loads((RESULTS / "executor.json").read_text(encoding="utf-8"))["app"]
    page.set_viewport_size({"width": 1500, "height": 1100})
    page.goto(f"{HISTORY}/history/{app}/executors/")
    table = page.locator("#active-executors-table")
    expect(table.get_by_text("Dead").first).to_be_visible(timeout=60000)
    page.wait_for_timeout(800)
    table.screenshot(path=OUT / "dpc-lr4-executor.png")


def main() -> None:
    what = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(device_scale_factor=2)
        {"report": report, "stages": stages, "executor": executor}[what](page)
        browser.close()
    for f in sorted(OUT.glob("dpc-lr4-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
