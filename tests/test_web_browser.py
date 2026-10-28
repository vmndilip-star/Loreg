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

expect.set_options(timeout=15_000)      # the whole suite runs on one machine; a busy moment must not fail a browser check

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

        # 2. dead stock: Stock shows the independent total and the table, sorted by money tied up
        page.goto(server.url + "/#stock")
        expect(money()).to_have_text(dollars(start))
        rows = page.locator("[data-testid=products] tbody tr")
        expect(rows).to_have_count(25)
        for n, item in enumerate(sorted(truth.items, key=lambda i: (-i["cap"], i["sku"]))[:25]):
            assert rows.nth(n).get_attribute("data-sku") == item["sku"] and dollars(item["cap"]) in rows.nth(n).inner_text()
        page.locator("[data-testid=chip-dead]").click()
        dead = truth.by_status("dead")
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"{len(dead)} items")
        for n, item in enumerate(sorted(dead, key=lambda i: (-i["cap"], i["sku"]))[:5]):
            assert rows.nth(n).get_attribute("data-sku") == item["sku"] and dollars(item["cap"]) in rows.nth(n).inner_text()

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
        titles = [cards.nth(n).locator(".num-lg").inner_text() for n in range(3)]
        assert titles == ["Discount 5%", "Return", "Hold"], titles      # three different outcomes (the offline writer holds where the rules would bundle)
        assert "Minimum advertised price: $822" in cards.nth(2).inner_text()

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


def test_browse_and_search_in_chrome_show_the_numbers_the_api_returns(browser, catalog_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    truth = Truth(catalog_dir, master="upload_demo_200.csv")
    with LiveServer(tmp_path / "browse.sqlite") as server:
        httpx.post(server.url + "/api/demo/load")
        page = browser.new_page(viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        rows = page.locator("[data-testid=products] tbody tr")
        api = lambda **p: httpx.get(server.url + "/api/products", params=p).json()  # noqa: E731

        # a category link filters the table below, and the address says so
        page.goto(server.url + "/#stock")
        page.locator("[data-testid=category-link][data-category=tv]").click()
        tvs = sorted((i for i in truth.items if i["category"] == "tv"), key=lambda i: (-i["cap"], i["sku"]))
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"TVs: {len(tvs)} items")
        assert "category=tv" in page.url
        expect(rows).to_have_count(25)
        assert [rows.nth(n).get_attribute("data-sku") for n in range(25)] == [i["sku"] for i in tvs[:25]]

        # status chips carry the counts, and filtering changes the table and the money on screen
        slow = [i for i in tvs if i["status"] == "slow"]
        expect(page.locator("[data-testid=chip-slow]")).to_have_text(f"Slow ({len(slow)})")
        page.locator("[data-testid=chip-slow]").click()
        expect(rows).to_have_count(len(slow))
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"{dollars(sum(i['cap'] for i in slow))} at risk here")
        body = api(category="tv", status="slow")
        for n, r in enumerate(body["rows"]):
            assert rows.nth(n).get_attribute("data-sku") == r["sku"] and dollars(r["tied_up_cents"]) in rows.nth(n).inner_text()

        # pagination: page 2 shows the next 25, and Back returns to page 1
        page.locator("[data-testid=chip-all]").click()
        page.get_by_role("link", name="Page 2").click()
        expect(page.get_by_text(f"Page 2 of {(len(tvs) + 24) // 25}")).to_be_visible()
        assert [rows.nth(n).get_attribute("data-sku") for n in range(rows.count())] == [i["sku"] for i in tvs[25:50]]
        page.go_back()
        expect(page.get_by_text(f"Page 1 of {(len(tvs) + 24) // 25}")).to_be_visible()

        # sorting
        page.locator("[data-testid=sort]").select_option("name")
        names = [rows.nth(n).locator("td").first.locator(".fw-semibold").inner_text() for n in range(rows.count())]
        assert names == sorted(names, key=str.lower)

        # search: "/" focuses the box, results are grouped, arrows and Enter open one, and the table equals the API
        page.locator("body").click(position={"x": 4, "y": 4})
        page.keyboard.press("/")
        expect(page.locator("#siteSearch")).to_be_focused()
        page.keyboard.type("qn85d", delay=15)
        expect(page.locator("#searchList").get_by_role("option").first).to_be_visible()
        assert page.locator(".search-group").all_inner_texts() == ["LINES", "ITEMS"]
        want = [i for i in truth.items if "qn85d" in i["name"].lower() or "qn85d" in i["sku"].lower()]
        page.keyboard.press("ArrowDown")
        page.keyboard.press("Enter")
        expect(rows.first).to_be_visible()
        shown = {rows.nth(n).get_attribute("data-sku"): rows.nth(n).inner_text() for n in range(rows.count())}
        assert set(shown) == {i["sku"] for i in want}
        assert all(dollars(i["cap"]) in shown[i["sku"]] for i in want)
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"{dollars(sum(i['cap'] for i in want if i['status'] in ('dead', 'slow')))} at risk here")

        # Escape closes the list; a hopeless search shows the empty state and a clear way out
        page.locator("#siteSearch").fill("")
        page.locator("#siteSearch").type("zzzz qqqq", delay=10)
        expect(page.locator("#searchList").get_by_role("option")).to_have_count(0)
        expect(page.locator("#searchList")).to_contain_text("Nothing matches. Try fewer words.")
        page.keyboard.press("Escape")                                    # closes the list (Chrome also clears a search box on Escape)
        expect(page.locator("#searchList")).to_be_hidden()
        page.locator("#siteSearch").fill("zzzz qqqq")
        page.keyboard.press("Enter")                                     # Enter with no option chosen opens the full results table
        expect(page.locator("[data-testid=empty]")).to_contain_text("Nothing matches. Try fewer words.")
        page.get_by_role("button", name="Clear the filter").click()
        expect(rows).to_have_count(25)

        # an error from the server is shown as an error, with a way to try again
        page.route("**/api/products*", lambda route: route.abort())
        page.locator("[data-testid=chip-dead]").click()
        expect(page.get_by_text("The list could not be loaded")).to_be_visible()
        page.unroute("**/api/products*")

        # an unknown category is a clean "not found", not a crash
        page.goto(server.url + "/#stock?category=gadgets")
        expect(page.locator("[data-testid=not-found]")).to_be_visible()
        assert problems == [], problems
        page.close()
