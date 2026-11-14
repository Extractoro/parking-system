"""Fragments of the DPC lab 3 report: Grafana during O3.2, the Ray Dashboard after O3.4, the garage card.

Uses the installed Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright python scripts/screenshots/dpc-lr3.py grafana|ray|garage

grafana  right after `experiments/dpc_lr3.py inflight` (profile observability: prometheus grafana): the top row
         of the dashboard "DPC project" (queue_length; in_flight with IN_FLIGHT_LIMIT) for the last --minutes (8).
         Writes docs/reports/img/dpc-lr3-grafana.png.
ray      right after `experiments/dpc_lr3.py fault`: the task table of the Ray Dashboard (:8265), the job of the
         api, the attempts of process_entry (the attempt 0 FAILED after fault.hit, the attempt 1 FINISHED).
         Writes docs/reports/img/dpc-lr3-ray.png.
garage   the client (npm --prefix web/client run dev, :5173): the garage card of dpc-race after the fault
         experiment (occupied = inside, invariant holds, execution Ray). Writes docs/reports/img/dpc-lr3-garage.png.
"""

import os
import sys
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000")
RAY = os.environ.get("RAY_URL", "http://localhost:8265")


def union(*boxes: dict) -> dict:
    x0 = min(b["x"] for b in boxes)
    y0 = min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0}


def grafana(page: Page, minutes: int) -> None:
    # GRAFANA_RANGE="from=<ms>&to=<ms>" takes a fixed interval (a later retake of the same run)
    span = os.environ.get("GRAFANA_RANGE", f"from=now-{minutes}m&to=now")
    page.set_viewport_size({"width": 900, "height": 800})  # narrow: the text of the panels stays readable in print
    page.goto(f"{GRAFANA}/d/dpc-project/dpc-project?orgId=1&{span}&kiosk&theme=light")
    boxes = []
    for title in ("Queue length", "In flight: requests in workers (local), unfinished Ray tasks (ray)"):
        el = page.get_by_text(title, exact=True).first
        el.wait_for(timeout=20000)
        boxes.append(el.locator("xpath=ancestor::section[1]").bounding_box())
    page.wait_for_timeout(4000)
    page.screenshot(path=OUT / "dpc-lr3-grafana.png", clip=union(*boxes))


def ray(page: Page, job: str) -> None:
    """The Task Table of the job (the Ray client connection of the api) after the fault experiment: the same
    Task ID twice, attempt 0 FAILED (the worker died at fault.hit) and attempt 1 FINISHED."""
    page.set_viewport_size({"width": 1500, "height": 1400})
    page.goto(f"{RAY}/#/jobs/{job}")
    page.get_by_text("Task Table", exact=True).first.click()
    table = page.locator("table").filter(has_text="process_entry").first
    expect(table.get_by_text("FAILED").first).to_be_visible(timeout=20000)
    page.wait_for_timeout(1000)
    rows = page.locator("tr").filter(has_text="process_entry")
    header = page.locator("tr").filter(has_text="Duration").first
    header.evaluate("e => e.scrollIntoView({block: 'start'})")  # the dashboard scrolls inside its own container
    page.mouse.move(5, 5)  # no hover tooltip over the ids
    page.wait_for_timeout(800)
    top = header.bounding_box()
    last = rows.last.bounding_box()
    cut = page.get_by_role("columnheader", name="Function or class name").first.bounding_box()
    x0 = top["x"]
    page.screenshot(path=OUT / "dpc-lr3-ray.png", clip={
        "x": x0, "y": top["y"] - 4, "width": cut["x"] - x0, "height": last["y"] + last["height"] - top["y"] + 8})


def garage(page: Page) -> None:
    page.set_viewport_size({"width": 1100, "height": 1000})
    page.goto(f"{CLIENT}/?view=barrier&garage=dpc-race")
    card = page.locator(".garage-state")
    expect(card.get_by_text("Invariant holds")).to_be_visible(timeout=15000)
    expect(card.get_by_text("Ray tasks + effect receiver")).to_be_visible()
    page.wait_for_timeout(1200)
    card.screenshot(path=OUT / "dpc-lr3-garage.png")


def main() -> None:
    what = sys.argv[1]
    minutes = int(sys.argv[2]) if len(sys.argv) > 2 and what == "grafana" else 8
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(device_scale_factor=2)
        {"grafana": lambda: grafana(page, minutes), "ray": lambda: ray(page, sys.argv[2] if len(sys.argv) > 2 else ""), "garage": lambda: garage(page)}[what]()
        browser.close()
    for f in sorted(OUT.glob("dpc-lr3-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
