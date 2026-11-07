"""Fragment of the DPC lab 2 report: the Compute page of the client after "Compare 1/2/4".

Needs the api (docker compose up -d api) and the client (npm --prefix web/client run dev, :5173).
Uses the installed Microsoft Edge (channel "msedge"):

    uv run --no-project --python 3.13 --with playwright python scripts/screenshots/dpc-lr2.py

Writes docs/reports/img/dpc-lr2-compute.png: the form (n = 2*10^8, midpoint, local sums), the result of
the last run (value, reference, error, time, processes) and the table of the three runs on 1, 2 and 4
processes with S_p and E_p.
"""

import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[2]
CLIENT = os.environ.get("CLIENT_URL", "http://localhost:5173")
OUT = ROOT / "docs" / "reports" / "img"


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge")
        page = browser.new_page(viewport={"width": 860, "height": 1200}, device_scale_factor=2)
        page.goto(f"{CLIENT}/?view=compute")
        page.get_by_label("n, subintervals").fill("200000000")
        page.get_by_role("button", name="Compare 1/2/4").click()
        expect(page.locator(".compute-table caption")).to_be_visible(timeout=60_000)
        expect(page.locator(".col-main .compute-table tbody tr")).to_have_count(3, timeout=60_000)
        page.wait_for_timeout(300)
        page.locator(".col-main").screenshot(path=OUT / "dpc-lr2-compute.png")
        browser.close()
    print("written", OUT / "dpc-lr2-compute.png")


if __name__ == "__main__":
    main()
