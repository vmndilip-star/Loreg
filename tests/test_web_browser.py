"""The demo journey in a real browser (system Chrome through Playwright), against a real server and database.

Skips with a reason when Playwright or Chrome is missing: this test must never block a commit. The API-level twin in
tests/test_journey.py is the gate. Expected numbers come from tests/csv_truth.py, never from the page or the app."""

import json
import re

import httpx
import pytest

from app.api.routes_demo import DEMO_FILE
from csv_truth import Truth
from live_server import LiveServer
from test_watch import POISONS, FakeWeb, article, rss

playwright_sync = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (pip install playwright)")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

ASK = "What should I clear before the launch?"
POISON_TITLE = "SYNTHETIC <img src=x onerror=alert(1)> Samsung QN90D Neo QLED refresh"


def dollars(cents: int) -> str:
    return f"${(cents + 50) // 100:,}"


@pytest.fixture
def browser():
    with sync_playwright() as p:
        try:
            b = p.chromium.launch(channel="chrome", headless=True)
        except Exception as e:  # noqa: BLE001 - no Chrome here: skip, never fail
            pytest.skip(f"Chrome could not be started through Playwright: {str(e).splitlines()[0]}")
        yield b
        b.close()


def test_the_demo_journey_in_chrome(browser, catalog_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    truth = Truth(catalog_dir, master="upload_demo_200.csv")
    totals, exposed = truth.totals(), truth.launch_exposed()
    start = totals["at_risk"]["cap_cents"]
    by_sku = {i["sku"]: i for i in truth.items}
    web = FakeWeb(rss([article(title=POISON_TITLE, html=dict(POISONS)["ignore previous instructions"])]))

    with LiveServer(tmp_path / "browser.sqlite", http_factory=web.factory()) as server:
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        problems, dialogs = [], []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and not re.search(r"/api/(summary|daily)$", r.url) else None)
        money = lambda: page.locator("[data-testid=money-at-risk]").first  # noqa: E731

        # 0. before any upload: Home says so and points to Upload
        page.goto(server.url + "/#home")
        expect(page.get_by_text("No stock list yet")).to_be_visible()

        # 1. upload the real file through the file chooser (the primary path); the demo button is also offered
        page.goto(server.url + "/#upload")
        expect(page.get_by_role("button", name="Use the demo stock list")).to_be_visible()
        page.set_input_files("input[type=file]", str(DEMO_FILE))
        expect(page.get_by_text("rows OK")).to_be_visible()
        assert page.locator("section[aria-label='Rows OK'] .num").inner_text() == f"{len(truth.items):,}"
        assert page.locator("section[aria-label='Blocked'] .num").inner_text() == "3"

        # 2. dead stock: Stock shows the independent top items and the independent total
        page.goto(server.url + "/#stock")
        expect(money()).to_have_text(dollars(start))
        rows = page.locator("[data-testid=top-items] tbody tr")
        expect(rows).to_have_count(10)
        for n, item in enumerate(truth.top(10)):
            row = rows.nth(n)
            assert row.get_attribute("data-sku") == item["sku"]
            assert dollars(item["cap"]) in row.inner_text()

        # 3. Home: dead and slow counts, the daily brief, and the alert before any option
        page.goto(server.url + "/#home")
        expect(page.get_by_label("Dead items")).to_contain_text(f"{totals['dead']['n']} dead")
        expect(page.get_by_label("Slow items")).to_contain_text(f"{totals['slow']['n']} slow")
        page.get_by_role("button", name="Write today’s summary").click()
        expect(page.locator("[data-testid=brief-headline]")).to_contain_text(dollars(start))
        alert = page.locator("[data-testid=alert]")
        expect(alert).to_contain_text(f"{len(exposed)} of your Samsung TVs are dead or slow, with {dollars(sum(i['cap'] for i in exposed))} tied up.")
        expect(alert).to_contain_text("Want me to plan what to do with them?")
        expect(alert).to_contain_text("No dated news source is attached")
        main_text = page.locator("main").inner_text()
        assert not re.search(r"discount|bundle|accept|decline", main_text, re.I), "an option or discount is visible before help was asked for"
        assert page.locator("[data-testid=plan-card]").count() == 0

        # 4. chat: steps, reply, an alert card and three item cards, and still no plan
        page.goto(server.url + "/#ask")
        page.fill("#q", ASK)
        page.press("#q", "Enter")
        expect(page.locator("[data-testid=alert-card]")).to_be_visible()
        expect(page.locator("[data-testid=reply]")).to_contain_text("Want me to plan what to do with them?")
        expect(page.locator("[data-testid=steps] li").first).to_have_text("Understood your question")
        items = page.locator("[data-testid=item-card]")
        expect(items).to_have_count(3)
        assert [items.nth(n).get_attribute("data-sku") for n in range(3)] == [i["sku"] for i in exposed[:3]]
        assert page.locator("[data-testid=plan-card]").count() == 0
        expect(money()).to_have_text(dollars(start))

        # 5. help, then the plan appears in the chat: one card per item, money matching the CSV
        page.locator("[data-testid=plan-yes]").click()
        cards = page.locator("[data-testid=plan-card]")
        expect(cards).to_have_count(3)
        for n, item in enumerate(exposed[:3]):
            assert cards.nth(n).get_attribute("data-sku") == item["sku"]
            assert dollars(item["cap"]) in cards.nth(n).inner_text()

        # 6. accept the first option: capital at risk drops by exactly that item's money, and the server agrees
        first = exposed[0]
        cards.nth(0).locator("[data-testid=accept]").click()
        expect(money()).to_have_text(dollars(start - first["cap"]))
        server_now = httpx.get(server.url + "/api/summary").json()["money_at_risk_cents"]
        assert server_now == start - first["cap"] and money().inner_text() == dollars(server_now)

        # 7. decline another: no change; undo the first: back to the start
        cards.nth(1).locator("[data-testid=decline]").click()
        expect(cards.nth(1).locator("[data-testid=decision]")).to_have_text("Declined")
        expect(money()).to_have_text(dollars(start - first["cap"]))
        cards.nth(0).locator("[data-testid=undo]").click()
        expect(money()).to_have_text(dollars(start))

        # 8. a poisoned article: shown as text, flagged, never run, changes no number and attaches nothing
        page.goto(server.url + "/#watch")
        expect(money()).to_have_text(dollars(start))
        page.locator("[data-testid=scan]").click()
        news = page.locator("[data-testid=news-item]")
        expect(news).to_have_count(1)
        expect(news).to_contain_text("Not used: contains instruction-like text")
        expect(news).to_contain_text("From the web")
        assert page.locator("main img").count() == 0 and dialogs == []
        assert "<img" not in news.inner_html().lower()
        expect(page.get_by_text("No dated news source is attached")).to_be_visible()
        expect(money()).to_have_text(dollars(start))

        assert problems == [], problems
        page.close()


@pytest.mark.parametrize("pub, banner", [("Sun, 01 Mar 2026 08:00:00 +0000", True), ("Mon, 26 Oct 2026 08:00:00 +0000", False)])
def test_an_old_source_gets_a_stale_banner_and_a_fresh_one_does_not(browser, tmp_path, monkeypatch, pub, banner):
    monkeypatch.setenv("LOREG_DEMO", "1")
    web = FakeWeb(rss([article(pub=pub)]))                       # a synthetic article that really matches the Samsung line
    with LiveServer(tmp_path / "stale.sqlite", http_factory=web.factory()) as server:
        httpx.post(server.url + "/api/demo/load")
        scan = httpx.post(server.url + "/api/watch/scan").json()
        assert scan["matched_events"] == 1
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        page.goto(server.url + "/#watch")
        expect(page.get_by_label("Launch alert")).to_contain_text("published Mar 1, 2026" if banner else "published Oct 26, 2026")
        if banner:
            expect(page.locator("[data-testid=stale-banner]")).to_contain_text("239 days old")
            expect(page.locator("[data-testid=stale-banner]")).to_contain_text("Do not read it as fresh news")
        else:
            assert page.locator("[data-testid=stale-banner]").count() == 0
        page.close()
