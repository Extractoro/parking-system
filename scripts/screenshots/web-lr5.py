"""Client screenshots of the Web lab 5 report: fragments of the header with the notification list.

Needs the stand of lab 5 (docker compose up -d nginx publisher worker gateway-a gateway-b) and the client
(npm --prefix web/client run dev, :5173). Uses the installed Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright --with httpx --with pillow python scripts/screenshots/web-lr5.py

Writes docs/reports/img/web-lr5-*.png:
  two-windows   - two windows (two logins) of user1, each connected to its own gateway, with the same
                  notification «Spot confirmed» at the same position (the two lists side by side);
  reconnecting  - both gateways stopped: the header shows «Reconnecting…» while three bookings are made;
  recovered     - after docker compose start: «Live», the list has the three missed notifications.
Bookings are made through the API for user1 on center-1 a week ahead (new plates) and cancelled at the end.
"""

import os
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
from PIL import Image
from playwright.sync_api import BrowserContext, Page, expect, sync_playwright

CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
API = os.environ.get("WEB_BASE", "http://localhost:8080")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
ENV = {**os.environ, "MSYS_NO_PATHCONV": "1"}
RUN = uuid.uuid4().hex[:3].upper()
TOKEN = httpx.post(f"{API}/v1/auth/login", json={"login": "user1", "password": "user1-pass"}, timeout=10).json()[
    "access_token"]
DAY = (datetime.now(UTC) + timedelta(days=7)).replace(hour=6, minute=0, second=0, microsecond=0)
made: list[str] = []


def book(n: int) -> None:
    start = DAY + timedelta(hours=n)
    r = httpx.post(f"{API}/v1/bookings", timeout=10,
                   json={"level_id": "center-1", "from_hour": start.isoformat(), "hours": 1, "plate": f"AA{RUN}{n}KB"},
                   headers={"Authorization": f"Bearer {TOKEN}", "Idempotency-Key": f"shot-{uuid.uuid4()}"})
    r.raise_for_status()
    made.append(r.json()["id"])


def compose(*args: str) -> None:
    subprocess.run(["docker", "compose", *args], cwd=ROOT, env=ENV, check=True, capture_output=True)


def open_client(ctx: BrowserContext) -> Page:
    page = ctx.new_page()
    page.goto(f"{CLIENT}/?view=bookings")
    form = page.get_by_test_id("login-form")
    form.get_by_label("Login").fill("user1")
    form.get_by_label("Password").fill("user1-pass")
    form.get_by_role("button", name="Log in").click()
    expect(page.get_by_test_id("connection")).to_have_text("Live", timeout=20000)
    return page


def open_list(page: Page) -> None:
    if page.get_by_test_id("notification-list").count() == 0:
        page.get_by_role("button", name="Notifications", exact=False).click()
    expect(page.get_by_test_id("notification-list")).to_be_visible()


def shot(page: Page, path: Path) -> None:
    """The bell with the connection state and the open list: one rectangle around both."""
    page.mouse.move(0, 0)
    page.wait_for_timeout(300)
    box = page.get_by_test_id("notifications").bounding_box()
    panel = page.get_by_test_id("notification-list").bounding_box()
    x0, y0 = min(box["x"], panel["x"]) - 8, box["y"] - 8
    x1, y1 = max(box["x"] + box["width"], panel["x"] + panel["width"]) + 8, panel["y"] + panel["height"] + 8
    page.screenshot(path=path, clip={"x": x0, "y": y0, "width": x1 - x0, "height": y1 - y0})


def side_by_side(left: Path, right: Path, out: Path) -> None:
    a, b = Image.open(left), Image.open(right)
    gap = 24
    img = Image.new("RGB", (a.width + gap + b.width, max(a.height, b.height)), "white")
    img.paste(a, (0, 0))
    img.paste(b, (a.width + gap, 0))
    img.save(out)
    left.unlink()
    right.unlink()


def gateway_of(page: Page) -> str:
    return page.get_by_test_id("connection").get_attribute("title") or ""


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        view = {"viewport": {"width": 1100, "height": 760}, "device_scale_factor": 2}
        ctx_a, ctx_b = browser.new_context(**view), browser.new_context(**view)
        a = open_client(ctx_a)
        b = open_client(ctx_b)
        print("window 1 on", gateway_of(a), "| window 2 on", gateway_of(b), flush=True)

        book(0)
        for page in (a, b):
            expect(page.get_by_test_id("toasts")).to_contain_text(f"AA{RUN}0KB", timeout=10000)
            open_list(page)
            expect(page.get_by_test_id("notification-list")).to_contain_text(f"AA{RUN}0KB")
        shot(a, OUT / "l.png")
        shot(b, OUT / "r.png")
        side_by_side(OUT / "l.png", OUT / "r.png", OUT / "web-lr5-two-windows.png")
        ctx_b.close()

        compose("stop", "gateway-a", "gateway-b")
        try:
            expect(a.get_by_test_id("connection")).to_have_text("Reconnecting…", timeout=15000)
            for n in (1, 2, 3):
                book(n)
            a.wait_for_timeout(1500)
            open_list(a)
            shot(a, OUT / "web-lr5-reconnecting.png")
        finally:
            compose("start", "gateway-a", "gateway-b")
        expect(a.get_by_test_id("connection")).to_have_text("Live", timeout=90000)
        open_list(a)
        expect(a.get_by_test_id("notification-list")).to_contain_text(f"AA{RUN}3KB", timeout=10000)
        a.wait_for_timeout(500)
        shot(a, OUT / "web-lr5-recovered.png")
        browser.close()

    for booking_id in made:  # free the demo spots again
        httpx.post(f"{API}/v1/bookings/{booking_id}/cancel", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=10)
    print("written:", *sorted(f.name for f in OUT.glob("web-lr5-*.png")))


if __name__ == "__main__":
    main()
