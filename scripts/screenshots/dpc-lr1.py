"""Fragments of the DPC lab 1 report: the Barrier page of the client and a Grafana panel.

Needs the stand (docker compose up -d nginx api; docker compose --profile observability up -d
prometheus grafana) and the client (npm --prefix web/client run dev, :5173). Uses the installed
Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright --with httpx --with aiohttp --with tzdata python scripts/screenshots/dpc-lr1.py

Writes docs/reports/img/dpc-lr1-*.png:
  rejected - the Barrier form after an exit of a car that is not inside (rejected, reason) and the
             list of the last requests of the garage;
  garage   - the garage state card of center (a booking of the current hour made through Web is
             counted as reserved);
  grafana  - the panel Queue length of the dashboard "DPC project" during a
             30-second overload (60 clients, the same load as experiment O1.2, without restarting api).
"""

import asyncio
import os
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "experiments"))
from dpc_lr1_overload import load  # noqa: E402

CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
WEB = os.environ.get("WEB_BASE", "http://localhost:8080")
DPC = os.environ.get("DPC_BASE", "http://localhost:8000")
GRAFANA = os.environ.get("GRAFANA_URL", "http://localhost:3000")
H = {"Authorization": f"Bearer {os.environ.get('API_TOKEN', 'dev-token')}"}
OUT = ROOT / "docs" / "reports" / "img"
RUN = f"{uuid.uuid4().int % 10000:04d}"


def now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def entry(plate: str, direction: str = "in") -> None:
    r = httpx.post(f"{DPC}/v1/entries", headers=H, timeout=10, json={
        "event_id": str(uuid.uuid4()), "garage_id": "center", "plate": plate,
        "direction": direction, "event_time": now_iso()})
    r.raise_for_status()


def prepare() -> None:
    """Two cars inside center and a booking of the current hour on center-1 (made through Web)."""
    hour = datetime.now(ZoneInfo("Europe/Kyiv")).replace(minute=0, second=0, microsecond=0).isoformat()
    httpx.post(f"{WEB}/v1/bookings", timeout=10,
               json={"level_id": "center-1", "from_hour": hour, "hours": 2, "plate": f"BK{RUN}CE"},
               headers={"X-User-Id": "u-2", "Idempotency-Key": str(uuid.uuid4())}).raise_for_status()
    for p in (f"AA{RUN}KC", f"AI{RUN}BH"):
        entry(p)
    time.sleep(2)


def barrier_shots(page: Page) -> None:
    page.goto(f"{CLIENT}/?view=barrier")
    page.get_by_placeholder("e.g. AA 1234 BB…").fill(f"KA {RUN} KO")
    page.get_by_role("radio", name="Exit").click()
    page.get_by_role("button", name="Send event").click()
    expect(page.get_by_text("cannot exit").first).to_be_visible(timeout=15000)
    page.wait_for_timeout(1500)  # the list of the last requests is polled once a second
    main = page.locator(".col-main")
    form = page.locator(".barrier-form").bounding_box()
    rows = page.locator(".recent .entry-row")
    last = rows.nth(min(3, rows.count() - 1)).bounding_box()
    page.screenshot(path=OUT / "dpc-lr1-rejected.png", clip={
        "x": form["x"] - 2, "y": form["y"] - 2, "width": form["width"] + 4,
        "height": last["y"] + last["height"] - form["y"] + 6})
    assert main.count() == 1
    card = page.locator(".garage-state")
    expect(card.get_by_text("Invariant holds")).to_be_visible()
    card.screenshot(path=OUT / "dpc-lr1-garage.png")


def grafana_shot(page: Page) -> None:
    print("overload 30 s, 60 clients ...", flush=True)
    res: list = []  # Playwright's sync API owns the loop of this thread: the load runs in its own thread
    t = threading.Thread(target=lambda: res.append(asyncio.run(load(60, 30))))
    t.start()
    t.join()
    statuses, _, accepted, wall = res[0]
    print(f"  {dict(statuses)} in {wall:.1f} s", flush=True)
    time.sleep(12)  # the last scrapes of Prometheus
    page.set_viewport_size({"width": 1000, "height": 900})
    page.goto(f"{GRAFANA}/d/dpc-project/dpc-project?orgId=1&from=now-3m&to=now&kiosk")
    panel = page.locator("[data-viz-panel-key='panel-1'], [data-panelid='1']").first
    try:
        panel.wait_for(timeout=15000)
        box1 = panel.bounding_box()
    except Exception:  # noqa: BLE001 - another Grafana version: take the top row by its title
        box1 = page.get_by_text("Queue length", exact=True).first.locator("xpath=ancestor::section[1]").bounding_box()
    page.wait_for_timeout(4000)
    page.screenshot(path=OUT / "dpc-lr1-grafana.png", clip={
        "x": box1["x"], "y": box1["y"], "width": box1["width"],
        "height": box1["height"]})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    prepare()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1100, "height": 1000}, device_scale_factor=2)
        barrier_shots(page)
        grafana_shot(page)
        browser.close()
    for f in sorted(OUT.glob("dpc-lr1-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
