"""The demo journey in a real browser (system Chrome through Playwright), against a real server and database.

Skips with a reason when Playwright or Chrome is missing: this test must never block a commit. The API-level twin in
tests/test_journey.py is the gate. Expected numbers come from tests/csv_truth.py, never from the page or the app."""

import json
import re
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest

from app.api.routes_demo import DEMO_FILE
from app.core.thresholds import load_thresholds
from csv_truth import Truth
from auth import OWNER, PASSWORD, PROFILE, seed_demo, sign_in, sign_in_demo
from live_server import LiveServer
from test_watch import POISONS, FakeWeb, article, rss

playwright_sync = pytest.importorskip("playwright.sync_api", reason="Playwright is not installed (pip install playwright)")
from playwright.sync_api import expect, sync_playwright  # noqa: E402

expect.set_options(timeout=15_000)      # the whole suite runs on one machine; a busy moment must not fail a browser check

NOW = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)      # the pages are aged against this clock, so the checks do not drift with the real date
ASK = "What should I clear before the launch?"
POISON_TITLE = "SYNTHETIC <img src=x onerror=alert(1)> Samsung QN90D Neo QLED refresh"


def dollars(cents: int) -> str:
    return f"${(cents + 50) // 100:,}"


def api(server) -> httpx.Client:
    """A signed-in client for this server, made once per server: the same account as the pages (a test may set server.client first)."""
    if getattr(server, "client", None) is None:
        server.client = httpx.Client(base_url=server.url, timeout=30)
        sign_in(server.client)
    return server.client


def page_in(browser, server, **context_args):
    """A page in a new browser context that is already signed in, as the page itself would be after signing in."""
    context = browser.new_context(**context_args)
    s = context.request.get(server.url + "/api/session").json()
    if not s["signed_in"]:
        token = {"X-CSRF-Token": s["csrf_token"]}
        r = context.request.post(server.url + "/api/signup", data={"email": OWNER, "password": PASSWORD, **PROFILE}, headers=token)
        if r.status == 409:
            r = context.request.post(server.url + "/api/login", data={"email": OWNER, "password": PASSWORD}, headers=token)
        assert r.ok, r.text()
    return context.new_page()


def signed_out_page(page):
    """After Sign out the page sets #signin and then reloads (app.js restartSignedOut): the address is #signin before the new page has
    loaded. Wait for the reloaded sign-in page, so the next navigation does not collide with the reload."""
    expect(page).to_have_url(re.compile(r"#signin$"))
    expect(page.get_by_role("heading", name="Sign in to Loreg")).to_be_visible()
    page.wait_for_load_state("load")


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

    with LiveServer(tmp_path / "browser.sqlite", http_factory=web.factory(), clock=lambda: NOW) as server:
        seed_demo(server.app)                                                       # what scripts/reset_demo.py makes
        server.client = httpx.Client(base_url=server.url, timeout=30)
        sign_in_demo(server.client)                                                 # the API checks below read the same account
        page = browser.new_context(viewport={"width": 1280, "height": 900}).new_page()
        problems, dialogs = [], []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.on("response", lambda r: problems.append(f"HTTP {r.status} {r.url}") if r.status >= 400 and not re.search(r"/api/(summary|daily)$", r.url) else None)
        money = lambda: page.locator("[data-testid=money-at-risk]").first  # noqa: E731

        # 0. signed out, the app opens on sign-in and shows no shop data; "Use demo account" fills the form in, then a welcome by name
        page.goto(server.url + "/")
        expect(page).to_have_url(re.compile(r"#signin$"))
        expect(page.locator("[data-testid=account-link]")).to_be_hidden()
        assert page.locator("[data-testid=money-at-risk]").count() == 0 and "BigDeal" not in page.content()
        page.locator("[data-testid=use-demo]").click()
        expect(page).to_have_url(re.compile(r"#signin$"))                         # filling the form in signs nobody in
        page.locator("[data-testid=signin-submit]").click()
        expect(page.locator("[data-testid=welcome-name]")).to_have_text("Welcome, BigDeal.")
        page.locator("[data-testid=welcome-continue]").click()

        # before any upload: Home says so and points to Upload
        expect(page).to_have_url(re.compile(r"#home$"))
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
        expect(page.locator("[data-testid=chip-at_risk]")).to_have_attribute("aria-pressed", "true")     # what is stuck comes first, not everything
        for n, item in enumerate(truth.top(25)):
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
        expect(alert.locator("[data-testid=source-label]")).to_have_text("Demo article (synthetic)")       # never unsourced, never unlabelled
        expect(alert.locator("[data-testid=source-dates]")).to_have_text("Published Oct 24, 2026Fetched Oct 26, 2026")
        pace = api(server).get("/api/launch/exposed").json()["groups"][0]["pace"]          # the server words it; the page only shows it
        assert pace["status"] == "upcoming" and pace["units"] == sum(i["qty"] for i in exposed) and pace["per_day"] == -(-pace["units"] // pace["days_until"])
        expect(alert.locator("[data-testid=pace]")).to_have_text(pace["text"])
        assert "not a sales forecast" in pace["text"] and "synthetic demo article" in pace["text"] and "November 20, 2026" in pace["text"]
        main_text = page.locator("main").inner_text()
        assert not re.search(r"discount|bundle|accept|decline", main_text, re.I), "an option or discount is visible before help was asked for"
        assert page.locator("[data-testid=plan-card]").count() == 0

        # 4. chat: steps, reply, an alert card and three item cards, and still no plan
        page.goto(server.url + "/#ask")
        page.fill("#q", ASK)
        page.press("#q", "Enter")
        expect(page.locator("[data-testid=alert-card]")).to_be_visible()
        expect(page.locator("[data-testid=reply]")).to_contain_text("Want me to plan what to do with them?")
        expect(page.locator("[data-testid=alert-card] [data-testid=pace]")).to_have_text(pace["text"])
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
        assert titles == ["Discount 5%", "Return", "Bundle"], titles      # three different outcomes
        assert "minimum advertised price of $822" in cards.nth(2).inner_text()

        # 5b. how far a discount may go, and what stops the next step: the figures are the API's cents, shown as dollars
        alert_id = api(server).get("/api/launch/exposed").json()["groups"][0]["alert_id"]
        api_plan = {r["sku"]: r for r in api(server).post(f"/api/alerts/{alert_id}/plan", params={"limit": 3}).json()["recommendations"]}
        dollars2 = lambda c: f"${c / 100:,.2f}"  # noqa: E731
        h = api_plan[exposed[0]["sku"]]["headroom"]
        note = cards.nth(0).locator("[data-testid=headroom]")
        expect(note).to_contain_text(f"Largest discount allowed: {h['largest_pct']}% ({dollars2(h['largest_price_cents'])} on the shelf).")
        expect(note).to_contain_text(f"Next step, {h['next_pct']}% ({dollars2(h['next_price_cents'])}), would go below cost ({dollars2(h['floor_cents'])}).")
        expect(cards.nth(1).locator("[data-testid=headroom]")).to_have_count(0)                  # a return card has none
        h = api_plan[exposed[2]["sku"]]["headroom"]
        note = cards.nth(2).locator("[data-testid=headroom]")
        expect(note).to_contain_text("No discount step is allowed.")
        expect(note).to_contain_text(f"Next step, 5% ({dollars2(h['next_price_cents'])}), would go below the minimum advertised price ({dollars2(h['floor_cents'])}).")

        # 6. accept the first option: capital at risk drops by exactly that item's money, and the server agrees
        first = exposed[0]
        cards.nth(0).locator("[data-testid=accept]").click()
        expect(money()).to_have_text(dollars(start - first["cap"]))
        server_now = api(server).get("/api/summary").json()["money_at_risk_cents"]
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
        everything = page.locator("[data-testid=news-item]")
        expect(everything).to_have_count(2)                                                  # the labelled demo article, and the poisoned one
        news = everything.filter(has_text="instruction-like")
        expect(news).to_have_count(1)
        expect(news).to_contain_text("Not used: contains instruction-like text")
        expect(news).to_contain_text("From the web")
        expect(everything.filter(has_text="Demo article (synthetic)")).to_have_count(1)
        assert page.locator("main img").count() == 0 and dialogs == []
        assert "<img" not in news.inner_html().lower()
        expect(page.locator("[data-testid=source-label]").first).to_have_text("Demo article (synthetic)")     # the poisoned article attached nothing
        expect(money()).to_have_text(dollars(start))

        assert problems == [], problems
        page.close()


STALE_DAYS = load_thresholds().stale_source_days          # watch.stale_after_days in config/thresholds.yaml (our 30-day assumption)


@pytest.mark.parametrize("days_over, banner", [(-1, False), (0, False), (1, True), (200, True)])
def test_the_stale_banner_shows_on_home_and_on_watch_exactly_when_the_source_is_older_than_the_limit(browser, tmp_path, monkeypatch, days_over, banner):
    """An attached real source, aged against the clock: at the limit it is still fresh, one day over it is stale, on both pages."""
    from datetime import timedelta
    monkeypatch.delenv("LOREG_DEMO", raising=False)             # a real article only becomes the alert's source outside demo mode
    age = STALE_DAYS + days_over
    now = datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc) + timedelta(days=age)
    web = FakeWeb(rss([article()]))                              # published 2026-10-26 08:00, a synthetic article that really matches the Samsung line
    with LiveServer(tmp_path / "stale.sqlite", http_factory=web.factory(), clock=lambda: now) as server:
        with open(DEMO_FILE, "rb") as f:
            assert api(server).post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
        assert api(server).post("/api/watch/scan").json()["matched_events"] == 1
        source = api(server).get("/api/launch/exposed").json()["groups"][0]["source"]
        assert source["attached"] and source["age_days"] == age and source["stale_after_days"] == STALE_DAYS
        assert source["freshness"] == ("stale" if banner else "fresh")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        page.goto(server.url + "/#home")
        home = page.locator("[data-testid=alert] .banner-warn")
        page.goto(server.url + "/#watch")
        watch = page.locator("[data-testid=stale-banner]")
        page.goto(server.url + "/#home")
        expect(page.locator("[data-testid=alert]")).to_contain_text("Published Oct 26, 2026")
        if banner:
            expect(home).to_contain_text(f"{age} days old (more than {STALE_DAYS}). Do not read it as fresh news.")
            page.goto(server.url + "/#watch")
            expect(watch).to_contain_text(f"{age} days old (more than {STALE_DAYS}). Do not read it as fresh news.")
        else:
            assert home.count() == 0
            page.goto(server.url + "/#watch")
            expect(page.get_by_label("Launch alert")).to_contain_text("Published Oct 26, 2026")
            assert watch.count() == 0
        page.close()


def test_browse_and_search_in_chrome_show_the_numbers_the_api_returns(browser, catalog_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    truth = Truth(catalog_dir, master="upload_demo_200.csv")
    with LiveServer(tmp_path / "browse.sqlite") as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        rows = page.locator("[data-testid=products] tbody tr")
        products = lambda **p: api(server).get("/api/products", params=p).json()  # noqa: E731

        # a category link filters the table below, and the address says so
        page.goto(server.url + "/#stock")
        page.locator("[data-testid=category-link][data-category=tv]").click()
        tvs = sorted((i for i in truth.items if i["category"] == "tv"), key=lambda i: (-i["cap"], i["sku"]))
        risky = [i for i in tvs if i["status"] in ("dead", "slow") and i["qty"] > 0]
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"TVs: {len(risky)} items")      # at risk by default
        assert "category=tv" in page.url and "status=" not in page.url
        expect(rows).to_have_count(min(25, len(risky)))
        assert [rows.nth(n).get_attribute("data-sku") for n in range(min(25, len(risky)))] == [i["sku"] for i in risky[:25]]

        # status chips carry the counts, and filtering changes the table and the money on screen
        slow = [i for i in tvs if i["status"] == "slow"]
        expect(page.locator("[data-testid=chip-slow]")).to_have_text(f"Slow ({len(slow)})")
        page.locator("[data-testid=chip-slow]").click()
        expect(rows).to_have_count(len(slow))
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"{dollars(sum(i['cap'] for i in slow))} at risk here")
        body = products(category="tv", status="slow")
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
        expect(page.locator("[data-testid=filter-summary]")).to_contain_text(f"{len(want)} item")     # wait for the new table, not the old one
        expect(rows).to_have_count(len(want))
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


def feed_with_an_undated_story():
    return rss([article()]).replace(b"</channel>", (b"<item><title>SYNTHETIC undated story</title><link>https://news.samsung.com/global/synthetic-undated</link>"
                                                     b"<guid>u</guid><description>short</description><content:encoded><![CDATA[<p>SYNTHETIC. Samsung will launch a thing in 2027.</p>]]></content:encoded></item></channel>"))


def test_the_demo_alert_stays_pinned_while_real_items_reach_the_news_list(browser, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    web = FakeWeb(feed_with_an_undated_story())
    with LiveServer(tmp_path / "pin.sqlite", http_factory=web.factory(), clock=lambda: NOW) as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        src = page.locator("[data-testid=source]").first

        # the labelled synthetic demo source: label, dates, excerpt, and no link to a made-up address
        page.goto(server.url + "/#home")
        expect(src.locator("[data-testid=source-label]")).to_have_text("Demo article (synthetic)")
        expect(src.locator("[data-testid=source-dates]")).to_have_text("Published Oct 24, 2026Fetched Oct 26, 2026")
        expect(src.locator("[data-testid=excerpt]")).to_contain_text("This is not real news")
        assert src.locator("a").count() == 0
        page.goto(server.url + "/#ask")
        page.fill("#q", ASK)
        page.press("#q", "Enter")
        card = page.locator("[data-testid=alert-card]")
        expect(card.locator("[data-testid=source-label]")).to_have_text("Demo article (synthetic)")
        expect(card.locator("[data-testid=source-dates]")).to_contain_text("Fetched Oct 26, 2026")

        # a scan adds the real items to the list; the demo alert does not move
        page.goto(server.url + "/#watch")
        page.locator("[data-testid=scan]").click()
        alert = page.get_by_label("Launch alert")
        expect(page.locator("[data-testid=news-item]")).to_have_count(3)                       # the real article, the undated story, the demo article
        expect(alert.locator("[data-testid=source-label]")).to_have_text("Demo article (synthetic)")
        expect(alert.locator("[data-testid=source-dates]")).to_have_text("Published Oct 24, 2026Fetched Oct 26, 2026")
        assert alert.locator("a").count() == 0
        undated_item = page.locator("[data-testid=news-item]").filter(has_text="undated story")
        expect(undated_item).to_contain_text("No publication date")
        expect(undated_item).to_contain_text("Not used: an article without a publication date is never a source")
        assert problems == [], problems
        page.close()


def test_a_real_fetched_source_its_stored_copy_and_its_age_in_chrome(browser, catalog_dir, tmp_path, monkeypatch):
    from datetime import timedelta
    monkeypatch.delenv("LOREG_DEMO", raising=False)
    now = {"t": NOW}
    web = FakeWeb(rss([article()]))
    with LiveServer(tmp_path / "prov.sqlite", http_factory=web.factory(), clock=lambda: now["t"]) as server:
        with open(DEMO_FILE, "rb") as f:
            assert api(server).post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.goto(server.url + "/#watch")
        alert = page.get_by_label("Launch alert")
        expect(alert).to_contain_text("No dated news source is attached")                      # honest: nothing real has matched yet

        # a real fetched article: the three dates, the excerpt, and a link that opens safely
        page.locator("[data-testid=scan]").click()
        expect(alert.locator("[data-testid=source-label]")).to_have_count(0)
        expect(alert.locator("[data-testid=source-dates]")).to_have_text("Published Oct 26, 2026Fetched Oct 26, 2026Last checked Oct 26, 2026")
        expect(alert.locator("[data-testid=excerpt]")).to_contain_text("QN90D Neo QLED")
        link = alert.locator("a[href^='https://news.samsung.com/']")
        expect(link).to_have_count(1)
        assert link.get_attribute("rel") == "noopener noreferrer" and link.get_attribute("target") == "_blank"

        # the feed cannot be reached: the alert becomes a labelled stored copy with its true dates
        web.down, now["t"] = True, NOW + timedelta(days=3)
        page.locator("[data-testid=scan]").click()
        expect(page.locator("[data-testid=cache-note]")).to_contain_text("Stored copy. Published 2026-10-26, fetched 2026-10-26. Could not check for newer news on 2026-10-29")
        expect(alert.locator("[data-testid=source-label]")).to_have_text("Stored copy")
        expect(alert.locator("[data-testid=source-dates]")).to_contain_text("Fetched Oct 26, 2026")                 # not today's date
        expect(page.get_by_text("Showing stored copies; the newest was fetched on 2026-10-26")).to_be_visible()
        assert problems == [], problems
        page.close()


@pytest.mark.parametrize("width", [320, 360, 390, 576, 640, 768, 1024, 1280])
def test_the_header_search_dropdown_stays_inside_the_screen_with_no_sideways_scroll(browser, tmp_path, monkeypatch, width):
    monkeypatch.setenv("LOREG_DEMO", "1")
    with LiveServer(tmp_path / "w.sqlite", clock=lambda: NOW) as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": width, "height": 800})
        page.goto(server.url + "/#stock")
        expect(page.locator("[data-testid=products]")).to_be_visible()
        page.locator("#siteSearch").click()
        page.keyboard.type("qn85d", delay=15)
        listbox = page.locator("#searchList")
        expect(listbox.get_by_role("option").first).to_be_visible()
        box = listbox.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width + 0.5, box                      # the list is fully on screen
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), "the page scrolls sideways"
        for option in listbox.get_by_role("option").all():                                            # and so is every result inside it
            ob = option.bounding_box()
            assert ob["x"] >= 0 and ob["x"] + ob["width"] <= width + 0.5, ob
        page.keyboard.press("Escape")
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        page.close()


def test_stored_copies_without_an_excerpt_render_with_label_and_dates_and_no_empty_quote(browser, tmp_path, monkeypatch):
    from app.api import routes
    path = tmp_path / "items.json"
    base = {"fetched_at": "2026-10-27T09:30:00Z", "content_hash": "b" * 64, "domain": "news.samsung.com"}
    path.write_text(json.dumps({"items": [
        {**base, "url": "https://news.samsung.com/global/synthetic-x1", "title": "SYNTHETIC stored without excerpt", "published_at": "2026-10-26T06:00:00Z"},
        {**base, "url": "https://news.samsung.com/global/synthetic-x2", "title": "SYNTHETIC stored with excerpt", "published_at": "2026-10-25T06:00:00Z",
         "content_hash": "c" * 64, "excerpt": "SYNTHETIC. A short quotation."}]}), encoding="utf-8")
    monkeypatch.setenv("LOREG_DEMO", "1")
    monkeypatch.setattr(routes, "NEWS_ITEMS", path)
    with LiveServer(tmp_path / "x.sqlite", clock=lambda: NOW) as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        page.goto(server.url + "/#watch")
        bare = page.locator("[data-testid=news-item]").filter(has_text="stored without excerpt")
        quoted = page.locator("[data-testid=news-item]").filter(has_text="stored with excerpt")
        for item in (bare, quoted):
            expect(item).to_contain_text("Stored copy")
            expect(item).to_contain_text("Fetched Oct 27, 2026")
        expect(bare).to_contain_text("Published Oct 26, 2026")
        assert bare.locator("blockquote").count() == 0                          # nothing empty is drawn
        expect(quoted.locator("blockquote")).to_have_text("SYNTHETIC. A short quotation.")
        page.close()


def test_the_item_with_no_sales_and_no_received_date_shows_not_enough_data_in_chrome(browser, catalog_dir, tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    sku = [i["sku"] for i in Truth(catalog_dir, master="upload_demo_200.csv").items if i["status"] == "insufficient_data"]
    assert len(sku) == 1
    with LiveServer(tmp_path / "nd.sqlite") as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        page.goto(server.url + f"/#stock?status=all&q={sku[0]}")
        row = page.locator(f"[data-testid=products] tbody tr[data-sku={sku[0]}]")
        expect(row).to_have_count(1)
        expect(row).to_contain_text("Not enough data")
        expect(row).to_contain_text("never")                                    # never sold
        assert "Dead" not in row.inner_text() and "Slow" not in row.inner_text()
        page.close()


def test_first_screen_polish_in_chrome(browser, tmp_path, monkeypatch):
    """The label is said once, the news list names the demo (not a publisher), excerpts that stop partway say so, and the dead and slow
    amounts are on the first screen above the fixed dock at the sizes a screen recording is made at."""
    monkeypatch.setenv("LOREG_DEMO", "1")
    with LiveServer(tmp_path / "polish.sqlite", http_factory=FakeWeb(b"").factory(), clock=lambda: NOW) as server:
        api(server).post("/api/demo/load")
        problems = []
        for width, height in ((1280, 720), (1366, 768), (1920, 1080)):
            page = page_in(browser, server, viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
            page.goto(server.url + "/#home")
            for name in ("Dead items", "Slow items"):
                tile = page.get_by_label(name)
                expect(tile).to_contain_text("tied up")
                tied, dock = tile.locator(".soft").bounding_box(), page.locator("nav.dock").bounding_box()
                assert tied["y"] + tied["height"] <= dock["y"], f"{name}: the amount is under the dock at {width}x{height}"
                assert tied["y"] + tied["height"] <= height, f"{name}: the amount is below the first screen at {width}x{height}"
            src = page.locator("[data-testid=source]").first
            label = src.locator("[data-testid=source-label]")
            expect(label).to_have_text("Demo article (synthetic)")
            title = src.locator("xpath=./div[1]")
            expect(title).to_have_text("Demo article (synthetic)A newer QN90D Neo QLED generation is announced")      # chip, then a title that does not repeat it
            page.close()

        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        page.goto(server.url + "/#watch")
        synthetic = page.locator("[data-testid=news-item]").filter(has_text="newer QN90D")
        expect(synthetic.locator(".soft.small").first).to_have_text(re.compile(r"^Loreg demo \(demo\.invalid\)\. Published"))
        head = synthetic.locator("xpath=./div[position() <= 2]").all_inner_texts()         # title and source line; the event line below names the product brand
        assert "Samsung" not in " ".join(head) and "Newsroom" not in " ".join(head), head
        for quote in page.locator("[data-testid=news-item] blockquote").all():
            text = quote.inner_text().strip()
            assert not re.match(r"^[a-z]", text.removeprefix("… ")), text           # nothing starts on a lower-case fragment without the mark
            assert text.startswith("… ") or text[0].isupper() or text[0].isdigit() or text[0] in "“\"▲(", text
        page.close()
        assert problems == [], problems


def test_the_synthetic_title_and_source_name_follow_the_demo_when_it_is_loaded_again(tmp_path, catalog_dir, monkeypatch):
    """A database made by an earlier version (publisher-like name, label repeated in the title) is brought up to date on the next load."""
    import sqlite3
    from test_pace import demo_app
    monkeypatch.setenv("LOREG_DEMO", "1")
    c = demo_app(tmp_path)
    c.post("/api/demo/load")
    con = sqlite3.connect(tmp_path / "p.sqlite")
    con.execute("UPDATE market_sources SET name = 'Samsung Global Newsroom' WHERE url = 'https://demo.invalid/'")
    con.execute("UPDATE market_items SET title = 'Demo article (synthetic): a newer QN90D Neo QLED generation is announced' WHERE origin = 'demo_synthetic'")
    con.commit()
    con.close()
    c2 = demo_app(tmp_path)
    c2.post("/api/demo/load")
    item = next(i for i in c2.get("/api/watch/items").json()["items"] if i["origin"] == "demo_synthetic")
    assert (item["source"], item["title"], item["label"]) == ("Loreg demo", "A newer QN90D Neo QLED generation is announced", "Demo article (synthetic)")
    assert not item["title"].startswith(item["label"])


def test_the_launch_tracker_and_its_notification_count_in_chrome_at_360_and_1280(browser, tmp_path, monkeypatch):
    """Tracker rows as text, the synthetic one labelled, the confirmed wording, history, the header count equal to the API's, no sideways scroll."""
    from test_launch_tracker import LAUNCH, ON_SALE, at
    monkeypatch.setenv("LOREG_DEMO", "1")
    web = FakeWeb(rss([LAUNCH, ON_SALE]))
    with LiveServer(tmp_path / "tracker.sqlite", http_factory=web.factory(), clock=lambda: at(2026, 11, 22)) as server:
        api(server).post("/api/demo/load")
        problems = []
        for width, height in ((360, 800), (1280, 900)):
            page = page_in(browser, server, viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
            page.goto(server.url + "/#watch")
            if width == 360:
                page.locator("[data-testid=scan]").click()                   # "Check for news": the real article is read and confirms its launch
            rows = page.locator("[data-testid=tracker-row]")
            expect(rows).to_have_count(2)
            synthetic = rows.filter(has=page.locator("[data-testid=tracker-label]"))
            expect(synthetic.locator("[data-testid=tracker-label]")).to_have_text("Demo article (synthetic)")
            expect(synthetic.locator("[data-testid=tracker-state]")).to_have_text("Due")
            expect(synthetic.locator("[data-testid=tracker-text]")).to_contain_text("the date alone does not mean it did")
            real = rows.filter(has=page.locator("[data-testid=tracker-confirmation]"))
            expect(real.locator("[data-testid=tracker-state]")).to_have_text("Launch confirmed")
            expect(real.locator("[data-testid=tracker-text]")).to_have_text("Launch confirmed by a source dated 2026-11-21.")
            real.locator("summary").click()
            expect(real.locator("[data-testid=history-row]")).to_have_count(1)
            expect(real.locator("[data-testid=history-row]")).to_contain_text("First date: November 20, 2026")
            unread = api(server).get("/api/notifications").json()["unread"]
            assert unread >= 2
            expect(page.locator("[data-testid=unread-count]")).to_have_text(f"{unread} new")
            assert page.evaluate("document.documentElement.scrollWidth") <= width, f"sideways scroll at {width}px"
            page.close()
        page = page_in(browser, server, viewport={"width": 360, "height": 800})
        page.goto(server.url + "/#watch")
        page.locator("[data-testid=note-read]").first.click()
        expect(page.locator("[data-testid=unread-count]")).to_have_text(f"{unread - 1} new")
        page.locator("[data-testid=notes-read-all]").click()
        expect(page.locator("[data-testid=unread-count]")).to_be_hidden()
        assert api(server).get("/api/notifications").json()["unread"] == 0
        page.close()
        assert problems == [], problems


def test_signup_account_sign_out_and_a_wrong_password_in_chrome(browser, tmp_path):
    """Signed out, every screen leads to sign-in. Create an account, see the profile as text, sign out, then a wrong password and an
    unknown email get the same message, and the right one signs in. No sideways scroll at 360 px."""
    with LiveServer(tmp_path / "accounts.sqlite") as server:
        problems = []
        page = browser.new_context(viewport={"width": 360, "height": 800}).new_page()
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.goto(server.url + "/#home")
        expect(page).to_have_url(re.compile(r"#signin$"))
        expect(page.get_by_role("heading", name="Sign in to Loreg")).to_be_visible()
        expect(page.locator("[data-testid=account-link]")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth") <= 360
        form = page.locator("[data-testid=signup]")
        for name, value in {"email": "chrome@example.com", "password": "a long test password", "org_name": "Chrome <Example> Shop",
                            "street": "100 Example Street", "city": "Springfield", "state": "ST", "zip": "00000", "country": "US"}.items():
            form.locator(f"[name={name}]").fill(value)
        form.locator("[data-testid=signup-submit]").click()
        expect(page.locator("[data-testid=signup-note]")).to_contain_text("Organization name must be plain text")      # < and > refused
        form.locator("[name=org_name]").fill("Chrome Example Shop")
        form.locator("[data-testid=signup-submit]").click()
        expect(page).to_have_url(re.compile(r"#welcome$"))
        expect(page.locator("[data-testid=welcome-name]")).to_have_text("Welcome, Chrome Example Shop.")
        page.locator("[data-testid=welcome-continue]").click()
        expect(page).to_have_url(re.compile(r"#home$"))
        expect(page.locator("[data-testid=account-link]")).to_be_visible()
        assert page.evaluate("document.documentElement.scrollWidth") <= 360
        page.locator("[data-testid=account-link]").click()
        expect(page.locator("[data-testid=profile] [data-field=org_name]")).to_have_text("Chrome Example Shop")
        expect(page.locator("[data-testid=profile] [data-field=zip]")).to_have_text("00000")
        page.locator("[data-testid=signout]").click()
        signed_out_page(page)
        expect(page.locator("[data-testid=account-link]")).to_be_hidden()
        page.goto(server.url + "/#watch")
        expect(page).to_have_url(re.compile(r"#signin$"))
        sign = page.locator("[data-testid=signin]")
        for email, password in (("chrome@example.com", "not the password"), ("nobody@example.com", "a long test password")):
            sign.locator("[name=email]").fill(email)
            sign.locator("[name=password]").fill(password)
            sign.locator("[data-testid=signin-submit]").click()
            expect(page.locator("[data-testid=signin-note]")).to_have_text("Email or password is incorrect.")
        sign.locator("[name=email]").fill("chrome@example.com")
        sign.locator("[name=password]").fill("a long test password")
        sign.locator("[data-testid=signin-submit]").click()
        expect(page).to_have_url(re.compile(r"#welcome$"))
        expect(page.locator("[data-testid=welcome-name]")).to_have_text("Welcome, Chrome Example Shop.")
        assert problems == [], problems


def test_the_welcome_screen_names_the_organization_as_text_on_every_sign_in_and_not_on_a_reload(browser, tmp_path):
    """The name is the signed-in shop's, from the session. Signup refuses < and >, so markup is written straight into the database (as an
    old or hand-edited row could hold it): the page must show it as text and run nothing."""
    name = "Tom & \"Jerry's\" <img src=x onerror=alert(1)>"
    with LiveServer(tmp_path / "welcome.sqlite") as server:
        sign_in(httpx.Client(base_url=server.url, timeout=30), email="welcome@example.com")
        con = sqlite3.connect(server.app.state.db_path)
        con.execute("UPDATE shops SET org_name = ? WHERE email = 'welcome@example.com'", (name,))
        con.commit()
        con.close()
        problems, dialogs = [], []
        page = browser.new_context(viewport={"width": 360, "height": 800}).new_page()
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        for _ in range(2):                                                           # every sign-in, not only the first
            page.goto(server.url + "/#signin")
            sign = page.locator("[data-testid=signin]")
            sign.locator("[name=email]").fill("welcome@example.com")
            sign.locator("[name=password]").fill(PASSWORD)
            sign.locator("[data-testid=signin-submit]").click()
            expect(page).to_have_url(re.compile(r"#welcome$"))
            expect(page.locator("[data-testid=welcome-name]")).to_have_text(f"Welcome, {name}.")
            expect(page.locator("[data-testid=welcome]")).to_contain_text("I'm ready to provide a smart assistant for your data.")
            assert page.locator("main img").count() == 0
            page.locator("[data-testid=welcome-continue]").click()
            expect(page).to_have_url(re.compile(r"#home$"))
            page.reload()                                                            # a reload with a live session goes on to Home
            expect(page).to_have_url(re.compile(r"#home$"))
            expect(page.locator("[data-testid=welcome]")).to_have_count(0)
            page.goto(server.url + "/#account")
            page.locator("[data-testid=signout]").click()
            signed_out_page(page)
        assert problems == [] and dialogs == [], (problems, dialogs)


def test_signing_out_or_losing_the_session_leaves_nothing_of_the_shop_on_the_page(browser, catalog_dir, tmp_path):
    """Sign out from Account, and separately have the session end on the server while the page is open: either way the page is
    loaded again on #signin, and nothing of the shop is left in it (header date, search results, numbers), nor after Back."""
    with LiveServer(tmp_path / "out.sqlite") as server:
        with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
            assert api(server).post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
        sku = api(server).get("/api/inventory/top", params={"limit": 1}).json()["items"][0]["sku"]
        markers = [sku, PROFILE["org_name"], "Stock as of"]
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        nav_type = lambda: page.evaluate("performance.getEntriesByType('navigation')[0].type")  # noqa: E731

        def shop_on_screen():
            page.goto(server.url + "/#stock")
            expect(page.locator("[data-testid=money-at-risk]").first).to_be_visible()
            expect(page.locator("#asOf")).to_contain_text("Stock as of")
            page.locator("#siteSearch").fill(sku)
            expect(page.locator("#searchList")).to_contain_text(sku)

        def nothing_left():
            expect(page).to_have_url(re.compile(r"#signin$"))
            expect(page.get_by_role("heading", name="Sign in to Loreg")).to_be_visible()
            assert nav_type() == "reload"                                            # a fresh page, not the old one with a new view
            for hidden in ("#siteSearchForm", "#dock", "[data-testid=account-link]", "[data-testid=backend-badge]", "[data-testid=unread-count]"):
                expect(page.locator(hidden)).to_be_hidden()
            assert page.locator("#asOf").inner_text() == "" and page.locator("#searchList").inner_text() == ""
            html = page.content()
            assert not [m for m in markers if m in html], [m for m in markers if m in html]

        shop_on_screen()
        page.goto(server.url + "/#account")
        page.locator("[data-testid=signout]").click()
        nothing_left()
        page.go_back()                                                               # Back does not bring the shop's screen back
        expect(page).to_have_url(re.compile(r"#signin$"))
        assert page.locator("[data-testid=money-at-risk]").count() == 0

        sign = page.locator("[data-testid=signin]")
        sign.locator("[name=email]").fill(OWNER)
        sign.locator("[name=password]").fill(PASSWORD)
        sign.locator("[data-testid=signin-submit]").click()
        page.locator("[data-testid=welcome-continue]").click()
        shop_on_screen()
        con = sqlite3.connect(server.app.state.db_path)                              # the session ends on the server (as if it expired)
        con.execute("DELETE FROM sessions")
        con.commit()
        con.close()
        page.goto(server.url + "/#home")                                             # the next request is refused: 401
        nothing_left()
        assert problems == [], problems


def test_analyze_a_category_then_the_report_the_sales_history_and_what_to_buy_in_chrome(browser, catalog_dir, tmp_path, monkeypatch):
    """Pick TVs, see their figures, then each of the three choices. The expected numbers come from the CSV (csv_truth classifies; the
    restock arithmetic is written out here again). Back returns to the previous choice, a chart value shows on keyboard focus, and
    nothing scrolls sideways at 360 px."""
    import csv
    import math
    monkeypatch.setenv("LOREG_DEMO", "1")
    truth = Truth(catalog_dir, master="upload_demo_200.csv")
    with open(DEMO_FILE, newline="", encoding="utf-8-sig") as f:
        sold = {r["sku"]: (int(r["units_sold_30d"] or 0), int(r["units_sold_90d"] or 0)) for r in csv.DictReader(f)}
    tvs = [i for i in truth.items if i["category"] == "tv"]
    buy = {}
    for i in tvs:
        need = max(0, math.ceil(45 * sold[i["sku"]][1] / 90) - max(i["qty"], 0))
        if i["status"] == "healthy" and not i["launch"] and need:
            buy[i["sku"]] = (need, need * i["cost"])
    dead = [i for i in truth.by_status("dead") if i["category"] == "tv"]
    with LiveServer(tmp_path / "analyze.sqlite", clock=lambda: NOW) as server:
        api(server).post("/api/demo/load")
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        problems = []
        page.on("pageerror", lambda e: problems.append(f"page error: {e}"))
        page.goto(server.url + "/#home")
        page.locator("nav.dock a[data-nav=analyze]").click()
        page.locator("[data-testid=analyze-category][data-category=tv]").click()
        expect(page.locator("[data-testid=analyze-category][data-category=tv]")).to_have_attribute("aria-pressed", "true")
        stats = page.locator("[data-testid=cat-stats]")
        expect(stats).to_contain_text(f"Items{len(tvs)}")
        expect(page.locator("[data-testid=status-bar] .legend")).to_contain_text(f"Dead{len(dead)} items · {dollars(sum(i['cap'] for i in dead))}")
        assert page.locator("[data-testid=brand-bars] .hbar-row").count() == 8

        page.locator("[data-testid=show-buy]").click()
        expect(page).to_have_url(re.compile(r"#analyze\?category=tv&show=buy$"))
        units, cost = sum(n for n, _ in buy.values()), sum(c for _, c in buy.values())
        expect(page.locator("[data-testid=buy-total]")).to_have_text(f"Buy {units} units of {len(buy)} items, about {dollars(cost)}")
        expect(page.locator("[data-testid=buy-note]")).to_contain_text("45 days is an assumption")
        rows = page.locator("[data-testid=buy-table] tbody tr")
        assert rows.count() == len(buy) and len([r for r in rows.all() if r.is_visible()]) == 10
        page.locator("[data-testid=buy-table-more]").click()
        expect(rows.nth(len(buy) - 1)).to_be_visible()
        assert {r.get_attribute("data-sku") for r in rows.all()} == set(buy)
        assert page.locator("[data-testid=wait-table] tr[data-reason=launch]").count() >= 1

        page.locator("[data-testid=show-sales]").click()
        history = page.locator("[data-testid=sales-history]")
        expect(history.locator("[data-testid=synthetic]")).to_have_text("Synthetic sales history")
        expect(history.locator("[data-window='30d']")).to_contain_text(f"{sum(sold[i['sku']][0] for i in tvs)} units")
        assert history.locator("[data-testid=monthly-chart] .col-slot").count() == 12
        assert history.locator("[data-testid=history-mismatch]").count() == 0          # the synthetic history agrees with the stock list
        last = history.locator("[data-testid=monthly-chart] [role=img]").last
        last.focus()
        expect(history.locator("[data-testid=monthly-chart] .chart-tip")).to_contain_text("Oct 2026:")
        page.locator("[data-testid=sales-item]").select_option("TV-SON-0419")
        expect(page).to_have_url(re.compile(r"show=sales&sku=TV-SON-0419$"))
        expect(page.locator("[data-testid=sales-history] [data-window='30d']")).to_contain_text(f"{sold['TV-SON-0419'][0]} units")

        page.go_back()
        expect(page).to_have_url(re.compile(r"show=sales$"))
        page.go_back()
        expect(page.locator("[data-testid=what-to-buy]")).to_be_visible()

        page.locator("[data-testid=show-report]").click()
        expect(page.locator("[data-testid=stock-report] tbody tr")).to_have_count(25)
        assert problems == [], problems
        page.close()

        narrow = page_in(browser, server, viewport={"width": 360, "height": 800})
        for show, ready in (("report", "stock-report"), ("sales", "monthly-chart"), ("buy", "what-to-buy")):
            narrow.goto(f"{server.url}/#analyze?category=tv&show={show}")
            expect(narrow.locator(f"[data-testid={ready}]")).to_be_visible()
            assert narrow.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth"), f"{show} scrolls sideways"
        narrow.close()


def test_analyze_shows_names_from_the_stock_list_as_text_and_says_when_there_is_no_sales_history(browser, tmp_path):
    header = "sku,brand,model,category,qty_on_hand,unit_cost,sell_price,last_sale_date,received_date,units_sold_30d,units_sold_90d"
    rows = ["TV-X-1,<b id=bold>Bold</b>,<img src=x onerror=alert(1)> 55 inch,tv,1,100.00,150.00,2026-10-20,2026-01-01,2,6"]
    with LiveServer(tmp_path / "markup.sqlite", clock=lambda: NOW) as server:
        assert api(server).post("/api/upload", files={"file": ("m.csv", "\n".join([header, *rows]).encode(), "text/csv")}).status_code == 200
        page = page_in(browser, server, viewport={"width": 1280, "height": 900})
        dialogs = []
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(server.url + "/#analyze?category=tv&show=buy")
        expect(page.locator("[data-testid=brand-bars]")).to_contain_text("<b id=bold>Bold</b>")
        expect(page.locator("[data-testid=buy-table]")).to_contain_text("<img src=x onerror=alert(1)> 55 inch")
        assert page.locator("#bold").count() == 0 and page.locator("main img").count() == 0 and dialogs == []
        page.locator("[data-testid=show-sales]").click()
        expect(page.locator("[data-testid=no-history]")).to_contain_text("No sales history has been uploaded")
        expect(page.locator("[data-testid=stock-list-bars]")).to_contain_text("2 units")
        page.close()
