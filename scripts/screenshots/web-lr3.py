"""Client screenshots of the Web lab 3 report: fragments of pages, not whole screens.

Needs the stand (docker compose up -d nginx), the client (npm --prefix web/client run dev, :5173) and the
page of another site on :8082 (python -m http.server 8082 --bind 127.0.0.1 --directory web/tools/evil-origin).
Uses the installed Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright --with httpx --with tzdata python scripts/screenshots/web-lr3.py

Writes docs/reports/img/web-lr3-*.png:
  login        - the login form after a wrong password (401 invalid-credentials), then the header after login;
  foreign-404  - Access check as user1: a booking of user2 read by id -> 404 as Problem Details;
  close-403    - Access check as user1: "Close level" -> 403 as Problem Details;
  evil-blocked - the page of http://localhost:8082: both fetch attempts blocked by the browser, the form sent;
  evil-allowed - the same page opened on the client origin http://localhost:5173: both answers read.
The booking of user2 is created through the API on a day 20 days ahead.
"""

import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
from playwright.sync_api import Page, expect, sync_playwright

CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
API = os.environ.get("WEB_BASE", "http://localhost:8080")
EVIL = os.environ.get("EVIL_URL", "http://localhost:8082")
ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "reports" / "img"
TZ = ZoneInfo("Europe/Kyiv")


def login_api(login: str, password: str) -> dict:
    r = httpx.post(f"{API}/v1/auth/login", json={"login": login, "password": password}, timeout=10)
    r.raise_for_status()
    return r.json()


def booking_of_user2() -> str:
    s = login_api("user2", "user2-pass")
    start = (datetime.now(TZ) + timedelta(days=20)).replace(hour=11, minute=0, second=0, microsecond=0)
    r = httpx.post(f"{API}/v1/bookings", timeout=10,
                   json={"level_id": "mall-1", "from_hour": start.isoformat(), "hours": 2, "plate": "BC2222BB"},
                   headers={"Authorization": f"Bearer {s['access_token']}", "Idempotency-Key": f"shot-{uuid.uuid4()}"})
    r.raise_for_status()
    return r.json()["id"]


def login_ui(page: Page, login: str, password: str) -> None:
    form = page.get_by_test_id("login-form")
    form.get_by_label("Login").fill(login)
    form.get_by_label("Password").fill(password)
    form.get_by_role("button", name="Log in").click()


def login_shots(page: Page) -> None:
    page.goto(f"{CLIENT}/?view=bookings")
    page.evaluate("sessionStorage.clear()")
    page.reload()
    form = page.get_by_test_id("login-form")
    login_ui(page, "user1", "wrong-password")
    expect(form.get_by_text("Wrong login or password")).to_be_visible()
    form.get_by_label("Password").fill("user1-pass")
    form.screenshot(path=OUT / "web-lr3-login.png")
    form.get_by_role("button", name="Log in").click()
    expect(page.get_by_test_id("account")).to_be_visible()


def access_shots(page: Page, foreign_id: str) -> None:
    page.goto(f"{CLIENT}/?view=access")
    card = page.get_by_test_id("access-booking")
    card.get_by_label("Booking id").fill(foreign_id)
    card.get_by_role("button", name="Read").click()
    expect(card.get_by_test_id("problem-json")).to_be_visible()
    card.screenshot(path=OUT / "web-lr3-foreign-404.png")

    level = page.get_by_test_id("access-level")
    level.get_by_label("Level").select_option("mall-2")
    level.get_by_role("button", name="Close level").click()
    expect(level.get_by_test_id("problem-json")).to_be_visible()
    level.screenshot(path=OUT / "web-lr3-close-403.png")


def evil_shots(page: Page) -> None:
    html = (ROOT / "web" / "tools" / "evil-origin" / "index.html").read_text(encoding="utf-8")
    for name, open_page in (
        ("evil-blocked", lambda: page.goto(EVIL)),
        # the same page on the allowed origin: set_content keeps the URL (and so the origin) of the client
        ("evil-allowed", lambda: (page.goto(CLIENT), page.set_content(html))),
    ):
        open_page()
        page.get_by_role("button", name="2. Read the catalog (fetch)").click()
        expect(page.locator("#results li")).to_have_count(1)
        page.get_by_role("button", name="1. Create a booking (fetch)").click()
        expect(page.locator("#results li")).to_have_count(2)
        if name == "evil-blocked":
            page.get_by_role("button", name="3. HTML form POST").click()
            expect(page.locator("#results li")).to_have_count(3)
        top = page.locator("h1").bounding_box()
        bottom = page.locator("#results").bounding_box()
        page.screenshot(path=OUT / f"web-lr3-{name}.png",
                        clip={"x": top["x"] - 8, "y": top["y"] - 8, "width": bottom["width"] + 16,
                              "height": bottom["y"] + bottom["height"] - top["y"] + 12})


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    foreign = booking_of_user2()
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 900, "height": 900}, device_scale_factor=2)
        login_shots(page)
        access_shots(page, foreign)
        evil_shots(page)
        browser.close()
    for f in sorted(OUT.glob("web-lr3-*.png")):
        print(f.name, f.stat().st_size)


if __name__ == "__main__":
    main()
