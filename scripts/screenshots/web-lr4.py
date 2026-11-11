"""Client screenshots of the Web lab 4 report: fragments of pages, not whole screens.

Needs the stand of lab 4 (docker compose up -d nginx publisher worker relay external-sim api), Kafka UI
(docker compose --profile observability up -d kafka-ui, :8085) and the client
(npm --prefix web/client run dev, :5173). Uses the installed Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright --with httpx --with tzdata python scripts/screenshots/web-lr4.py

Writes docs/reports/img/web-lr4-*.png:
  opened  - My bookings as user1: a booking of the current hour, "Open barrier" pressed, the card with
            "Opened at ..." and the status Parked (entered, read from the entries of DPC);
  kafka   - Kafka UI: the messages of domain-events of that booking (booking.created, entry.requested,
            entry.opened, key = booking id);
  dpc     - the Barrier page of the DPC part: the entry request made by the relay (event = operation id)
            for the plate of the booking, state Done.
The booking is created through the API for the current hour (2 h) on center-1 with a new plate.
"""

import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
from playwright.sync_api import Page, expect, sync_playwright

CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
API = os.environ.get("WEB_BASE", "http://localhost:8080")
KAFKA_UI = os.environ.get("KAFKA_UI", "http://localhost:8085")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
RUN = uuid.uuid4().hex[:4].upper()
PLATE = f"KA{RUN}LR"


def booking_now() -> str:
    s = httpx.post(f"{API}/v1/auth/login", json={"login": "user1", "password": "user1-pass"}, timeout=10).json()
    start = datetime.now(UTC).replace(minute=0, second=0, microsecond=0)
    r = httpx.post(f"{API}/v1/bookings", timeout=10,
                   json={"level_id": "center-1", "from_hour": start.isoformat(), "hours": 2, "plate": PLATE},
                   headers={"Authorization": f"Bearer {s['access_token']}", "Idempotency-Key": f"shot-{uuid.uuid4()}"})
    r.raise_for_status()
    return r.json()["id"]


def login_ui(page: Page) -> None:
    form = page.get_by_test_id("login-form")
    form.get_by_label("Login").fill("user1")
    form.get_by_label("Password").fill("user1-pass")
    form.get_by_role("button", name="Log in").click()


def opened_shot(page: Page) -> None:
    page.goto(f"{CLIENT}/?view=bookings")
    page.evaluate("sessionStorage.clear()")
    page.reload()
    login_ui(page)
    card = page.locator("li.card.booking").filter(has_text=PLATE)
    expect(card).to_be_visible(timeout=15000)
    card.get_by_role("button", name="Open barrier").click()
    expect(card.get_by_text("Opening the barrier")).to_be_visible(timeout=5000)
    expect(card.get_by_text("Entry recorded")).to_be_visible(timeout=90000)
    expect(card.locator(".badge.entered")).to_have_text("Parked")
    card.screenshot(path=OUT / "web-lr4-opened.png")


def kafka_shot(page: Page, booking_id: str) -> None:
    """The newest messages first: the three events of the booking just opened are the top rows."""
    page.set_viewport_size({"width": 1700, "height": 900})
    page.goto(f"{KAFKA_UI}/ui/clusters/parking/all-topics/domain-events/messages"
              "?mode=LATEST&limit=20&keySerde=String&valueSerde=String")
    rows = page.locator("table tbody tr").filter(has_text=booking_id)
    expect(rows.first).to_be_visible(timeout=20000)
    page.wait_for_timeout(1000)
    n = rows.count()
    rows.first.locator("td").first.click()  # expand the newest one (entry.opened): the whole envelope
    page.wait_for_timeout(1000)
    head = page.locator("table thead").first.bounding_box()
    body = page.locator("table tbody tr")
    last = body.nth(n - 1).bounding_box()  # the expanded row and the next event of the booking
    page.screenshot(path=OUT / "web-lr4-kafka.png", clip={
        "x": head["x"], "y": head["y"], "width": head["width"],
        "height": last["y"] + last["height"] - head["y"] + 2})


def dpc_shot(page: Page) -> None:
    page.goto(f"{CLIENT}/?view=barrier")
    row = page.locator(".recent .entry-row").filter(has_text=PLATE).first
    expect(row).to_be_visible(timeout=20000)
    page.wait_for_timeout(1500)
    rows = page.locator(".recent .entry-row")
    first, last = rows.first.bounding_box(), rows.nth(min(2, rows.count() - 1)).bounding_box()
    page.screenshot(path=OUT / "web-lr4-dpc.png", clip={
        "x": first["x"] - 2, "y": first["y"] - 2, "width": first["width"] + 4,
        "height": last["y"] + last["height"] - first["y"] + 4})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    booking_id = booking_now()
    print("booking", booking_id, PLATE, flush=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        opened_shot(page)
        dpc_shot(page)
        kafka_shot(page, booking_id)
        browser.close()
    print("written:", *sorted(f.name for f in OUT.glob("web-lr4-*.png")))


if __name__ == "__main__":
    main()
