"""Fragments of the DPC lab 6 report: the Coordinator page of the client (npm --prefix web/client run dev, :5173).

Uses the installed Microsoft Edge (channel "msedge"); the stand runs api, etcd, coordinator-a, coordinator-b:

    uv run --no-project --python 3.13 --with playwright --with httpx python scripts/screenshots/dpc-lr6.py owner|stale|unfenced

owner     the role card (owner, generation, lease) and the current tariffs. Writes docs/reports/img/dpc-lr6-owner.png.
stale     makes coordinator-a the owner (restart of coordinator-b), pauses it for 9 s (docker compose pause/unpause, the
          scenario of the experiment), then the history of mall with the rejected write of the old owner (stale) and
          the check ok. Writes docs/reports/img/dpc-lr6-stale.png.
unfenced  after `uv run experiments/dpc_lr6.py unfenced --keep`: the history of dpc-race (writes of both managers
          accepted without the check) and the check ok = false. Writes docs/reports/img/dpc-lr6-unfenced.png and
          dpc-lr6-unfenced-check.png;
          clean dpc-race afterwards with `uv run experiments/dpc_lr6.py restore`.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
API = os.environ.get("DPC_BASE", "http://localhost:8000")
HEADERS = {"Authorization": f"Bearer {os.environ.get('API_TOKEN', 'dev-token')}"}


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, check=True, capture_output=True)


def owner() -> tuple[str | None, str | None]:
    body = httpx.get(f"{API}/v1/coordinator", headers=HEADERS, timeout=5).json()
    registered = {r["garage_id"]: r for r in body["registered"]}
    ok = body["owner"] and registered.get("mall", {}).get("generation") == body["generation"]
    return (body["owner"], body["generation"]) if ok else (None, None)


def wait_owner(timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        name, _ = owner()
        if name:
            return name
        time.sleep(0.2)
    raise TimeoutError("no owner")


def clip(page: Page, *locators) -> dict:
    boxes = [loc.bounding_box() for loc in locators]
    x0, y0 = min(b["x"] for b in boxes), min(b["y"] for b in boxes)
    x1 = max(b["x"] + b["width"] for b in boxes)
    y1 = max(b["y"] + b["height"] for b in boxes)
    return {"x": x0 - 4, "y": y0 - 4, "width": x1 - x0 + 8, "height": y1 - y0 + 8}


def history_top(page: Page, rows: int = 8) -> dict:
    """The history card down to its `rows`-th row: the newest operations, readable on a printed page."""
    section = page.locator(".col-main > section").nth(2).bounding_box()
    last = page.get_by_test_id("history").locator("tbody tr").nth(rows - 1).bounding_box()
    return {"x": section["x"] - 4, "y": section["y"] - 4, "width": section["width"] + 8,
            "height": last["y"] + last["height"] - section["y"] + 6}


def open_page(page: Page, scope: str = "") -> None:
    page.set_viewport_size({"width": 1180, "height": 1400})
    page.goto(f"{CLIENT}/?view=coordinator")
    expect(page.get_by_test_id("coordinator-generation")).not_to_have_text("–", timeout=30000)
    if scope:
        page.get_by_test_id("history-scope").select_option(scope)
    page.mouse.move(5, 5)


def shot_owner(page: Page) -> None:
    open_page(page)
    page.wait_for_timeout(1500)
    sections = page.locator(".col-main > section")
    page.screenshot(path=OUT / "dpc-lr6-owner.png", clip=clip(page, sections.nth(0), sections.nth(1)))


def shot_stale(page: Page) -> None:
    for _ in range(5):
        if wait_owner() == "coordinator-a":
            break
        compose("restart", "coordinator-b")
        time.sleep(1)
    compose("pause", "coordinator-a")
    time.sleep(9)
    compose("unpause", "coordinator-a")
    open_page(page, "mall")
    history = page.get_by_test_id("history")
    expect(history.get_by_text("rejected: stale generation")).to_be_visible(timeout=15000)
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "dpc-lr6-stale.png", clip=history_top(page))


def shot_unfenced(page: Page) -> None:
    open_page(page, "dpc-race")
    expect(page.get_by_test_id("history-check")).to_contain_text("ok = false", timeout=15000)
    page.wait_for_timeout(1200)
    page.screenshot(path=OUT / "dpc-lr6-unfenced.png", clip=history_top(page))
    page.locator(".col-side section").first.screenshot(path=OUT / "dpc-lr6-unfenced-check.png")


def main() -> None:
    what = sys.argv[1]
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(device_scale_factor=2)
        {"owner": shot_owner, "stale": shot_stale, "unfenced": shot_unfenced}[what](page)
        browser.close()
    for f in sorted(OUT.glob("dpc-lr6-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
