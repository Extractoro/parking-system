"""Client screenshots of the Web lab 2 report: fragments of pages, not whole screens.

Needs the stand (docker compose up -d nginx) and the client (npm --prefix web/client run dev, :5173).
Uses the installed Microsoft Edge (channel "msedge"), so no browser download is needed:

    uv run --no-project --python 3.13 --with playwright --with httpx --with tzdata python scripts/screenshots/web-lr2.py

Writes docs/reports/img/web-lr2-*.png:
  retrying   - the booking form while it retries after a lost answer;
  unknown    - "We couldn't confirm your booking" after every attempt failed, after "Check my bookings";
  stale-412  - the plate change refused with 412 because the booking changed meanwhile;
  cancelled  - My bookings after a cancellation (the notice and the Past tab).
Lost answers are simulated in the browser (the POST is aborted), so the server state is not touched;
the bookings for the other pictures are created through the API for user u-2 on dates 3-6 days ahead.
"""

import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import Page, expect, sync_playwright

CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
API = os.environ.get("WEB_BASE", "http://localhost:8080")
USER = "u-2"
OUT = Path(__file__).resolve().parents[2] / "docs" / "reports" / "img"
TZ = ZoneInfo("Europe/Kyiv")
# plates unique per run: the cards are found by plate, and earlier runs leave their bookings
RUN = f"{uuid.uuid4().int % 10000:04d}"
STALE, NEW_PLATE, OTHER, CANCEL = f"SC{RUN}AA", f"SC{RUN}BB", f"SC{RUN}CC", f"CX{RUN}AA"


def hour_at(days: int, hour: int) -> str:
    d = (datetime.now(TZ) + timedelta(days=days)).replace(hour=hour, minute=0, second=0, microsecond=0)
    return d.isoformat()


def book(level: str, start: str, plate: str) -> dict:
    r = httpx.post(f"{API}/v1/bookings", timeout=10,
                   json={"level_id": level, "from_hour": start, "hours": 2, "plate": plate},
                   headers={"X-User-Id": USER, "Idempotency-Key": f"shot-{uuid.uuid4()}"})
    r.raise_for_status()
    return r.json()


def open_as_user(page: Page, query: dict) -> None:
    page.goto(CLIENT)
    page.evaluate(f"sessionStorage.setItem('userId', '{USER}')")
    page.goto(f"{CLIENT}/?{urlencode(query)}")


def booking_card(page: Page, plate: str):
    return page.locator("li.booking", has_text=plate)


def form_shots(page: Page) -> None:
    start = hour_at(3, 10)
    day = start[:10]
    # every POST of a booking gets no answer: the browser sees a network error, like after a crash
    page.route("**/v1/bookings", lambda route: route.abort() if route.request.method == "POST" else route.continue_())
    open_as_user(page, {"garage": "mall", "level": "mall-2", "day": day, "from": start, "hours": "2"})
    page.get_by_placeholder("e.g. AA 1234 BB…").fill("AT5050AA")
    page.get_by_role("button", name="Book").click()
    aside = page.locator("aside.col-side")
    expect(page.get_by_text("The connection is slow")).to_be_visible(timeout=15000)
    aside.screenshot(path=OUT / "web-lr2-retrying.png")

    expect(page.get_by_text("We couldn't confirm your booking")).to_be_visible(timeout=30000)
    page.get_by_role("button", name="Check my bookings").click()
    expect(page.get_by_text("isn't in your list")).to_be_visible(timeout=10000)
    aside.screenshot(path=OUT / "web-lr2-unknown.png")
    page.unroute("**/v1/bookings")


def stale_shot(page: Page) -> None:
    b = book("mall-1", hour_at(4, 9), STALE)
    open_as_user(page, {"view": "bookings"})
    card = booking_card(page, STALE)
    card.get_by_role("button", name="Change plate").click()
    card.locator("input").fill(NEW_PLATE)
    # meanwhile the same booking is changed elsewhere (another tab, another device): version 1 -> 2
    r = httpx.patch(f"{API}/v1/bookings/{b['id']}", json={"plate": OTHER}, timeout=10,
                    headers={"X-User-Id": USER, "If-Match": '"1"'})
    r.raise_for_status()
    card.get_by_role("button", name="Save plate").click()
    expect(card.get_by_text("The booking has changed")).to_be_visible()
    card.screenshot(path=OUT / "web-lr2-stale-412.png")


def cancelled_shot(page: Page) -> None:
    book("mall-2", hour_at(5, 14), CANCEL)
    open_as_user(page, {"view": "bookings"})
    card = booking_card(page, CANCEL)
    card.get_by_role("button", name="Cancel booking").click()
    card.get_by_role("button", name="Yes, cancel").click()
    expect(page.get_by_text("is cancelled")).to_be_visible()
    page.get_by_role("tab", name="Past").click()
    cancelled = booking_card(page, CANCEL)
    expect(cancelled.get_by_text("Cancelled")).to_be_visible()
    top = page.locator(".page-head").bounding_box()
    bottom = cancelled.bounding_box()
    page.screenshot(path=OUT / "web-lr2-cancelled.png",
                    clip={"x": top["x"], "y": top["y"], "width": top["width"],
                          "height": bottom["y"] + bottom["height"] - top["y"] + 4})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 1100, "height": 900}, device_scale_factor=2)
        form_shots(page)
        stale_shot(page)
        cancelled_shot(page)
        browser.close()
    for f in sorted(OUT.glob("web-lr2-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
