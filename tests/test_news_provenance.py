"""S3: every source an alert shows says where and when it came from; old news is aged against today (an injectable clock); undated articles never
attach; a copy that could not be refreshed is labelled with its true dates; the demo alert is always sourced and always labelled.
Offline. Every article here is SYNTHETIC."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.main import create_app
from app.watch.demo_news import SYNTHETIC_URL
from test_watch import FakeWeb, article, rss
from auth import AuthedClient

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
DAY = timedelta(days=1)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def build(tmp_path, catalog_dir, web, clock=None, name="p.sqlite"):
    c = AuthedClient(create_app(tmp_path / name, as_of="2026-10-26", http_factory=web.factory(), clock=clock or Clock()))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    return c


def source(c):
    return c.get("/api/launch/exposed").json()["groups"][0]["source"]


def complete(src):
    """The provenance every attached source must carry."""
    assert src["attached"] is True
    assert src["url"].startswith("https://") and src["domain"] and src["title"]
    assert date.fromisoformat(src["published"][:10]) and datetime.fromisoformat(src["fetched"].replace("Z", "+00:00"))
    assert src["excerpt"] and len(src["excerpt"]) <= 280
    assert src["origin"] in ("feed", "stored_copy", "demo_synthetic") and "freshness" in src and src["age_days"] is not None


# ------------------------------------------------------------------------------ provenance
def test_a_live_source_carries_url_domain_published_fetched_checked_and_an_excerpt(tmp_path, catalog_dir):
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article()])))
    c.post("/api/watch/scan")
    s = source(c)
    complete(s)
    assert (s["domain"], s["published"], s["fetched"], s["checked"], s["origin"], s["cached"], s["label"]) == \
           ("news.samsung.com", "2026-10-26T08:00:00Z", "2026-10-26T12:00:00Z", "2026-10-26T12:00:00Z", "feed", False, "")
    alert = c.get(f"/api/alerts/{c.get('/api/launch/exposed').json()['groups'][0]['alert_id']}").json()
    assert alert["source"]["fetched"] == s["fetched"]                          # the same everywhere: card, alert, chat


def test_the_chat_alert_card_and_reply_show_the_fetched_date(tmp_path, catalog_dir):
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article()])))
    c.post("/api/watch/scan")
    body = c.post("/api/ask", json={"question": "What should I clear before the launch?"}).text
    reply = "".join(json.loads(f.split("data: ", 1)[1])["text"] for f in body.split("\n\n") if f.startswith("event: token"))
    assert "published 2026-10-26, fetched 2026-10-26" in reply
    card = next(json.loads(f.split("data: ", 1)[1])["cards"][0] for f in body.split("\n\n") if f.startswith("event: cards"))
    assert card["type"] == "alert" and card["source"]["fetched"] == "2026-10-26T12:00:00Z" and card["source"]["url"].startswith("https://news.samsung.com/")


# ------------------------------------------------------------------------------ undated never attach
def test_an_article_without_a_publication_date_never_becomes_a_source(tmp_path, catalog_dir):
    feed = rss([article()]).replace(b"<pubDate>Mon, 26 Oct 2026 08:00:00 +0000</pubDate>", b"")
    c = build(tmp_path, catalog_dir, FakeWeb(feed))
    rep = c.post("/api/watch/scan").json()
    assert rep["matched_events"] == 1                                          # the article was read and does name the line ...
    assert source(c) == {"attached": False, "status": "no dated source attached"}   # ... but without a date it cannot be a source
    item = c.get("/api/watch/items").json()["items"][0]
    assert item["undated"] is True and item["published_at"] is None


def test_an_unreadable_date_is_the_same_as_no_date_and_a_dated_article_still_attaches(tmp_path, catalog_dir):
    feed = rss([article(slug="bad", pub="sometime soon"), article(slug="good", title="SYNTHETIC: Samsung QN90D Neo QLED refresh, dated")])
    c = build(tmp_path, catalog_dir, FakeWeb(feed))
    c.post("/api/watch/scan")
    s = source(c)
    complete(s)
    assert s["url"].endswith("synthetic-good")
    assert {i["url"].rsplit("-", 1)[1]: i["undated"] for i in c.get("/api/watch/items").json()["items"]} == {"bad": True, "good": False}


# ------------------------------------------------------------------------------ freshness is measured against today
def test_news_is_aged_against_the_clock_with_the_stock_list_date_as_a_second_figure(tmp_path, catalog_dir):
    clock = Clock()
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article()])), clock)
    c.post("/api/watch/scan")
    for days, freshness in ((0, "fresh"), (30, "fresh"), (31, "stale"), (90, "stale")):
        clock.t = T0 + days * DAY
        s = source(c)
        assert (s["freshness"], s["age_days"], s["stale_after_days"]) == (freshness, days, 30)
        assert s["age_at_stock_date_days"] == 0                                # published on the stock list's date, whatever today is
    assert c.get("/api/summary").json()["as_of"] == "2026-10-26"


def test_a_stock_list_dated_in_the_past_does_not_make_old_news_look_fresh(tmp_path, catalog_dir):
    clock = Clock(T0 + 40 * DAY)                                               # today is 40 days after the article and the stock list
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article()])), clock)
    c.post("/api/watch/scan")
    s = source(c)
    assert s["freshness"] == "stale" and s["age_days"] == 40 and s["age_at_stock_date_days"] == 0
    body = c.post("/api/ask", json={"question": "What should I clear before the launch?"}).text
    reply = "".join(json.loads(f.split("data: ", 1)[1])["text"] for f in body.split("\n\n") if f.startswith("event: token"))
    assert "this source is 40 days old, so it is not fresh news" in reply


# ------------------------------------------------------------------------------ stored copies are labelled with their true dates
def test_when_the_feed_cannot_be_reached_the_stored_copy_says_so_with_its_true_dates(tmp_path, catalog_dir):
    clock, web = Clock(), FakeWeb(rss([article()]))
    c = build(tmp_path, catalog_dir, web, clock)
    c.post("/api/watch/scan")
    assert source(c)["cached"] is False
    web.down, clock.t = True, T0 + 3 * DAY
    rep = c.post("/api/watch/scan").json()
    assert rep["cached"] is True and rep["network"] == "unavailable" and "the newest was fetched on 2026-10-26" in rep["message"]
    s = source(c)
    complete(s)
    assert s["cached"] is True and s["label"] == "Stored copy"
    assert s["fetched"] == "2026-10-26T12:00:00Z" and s["published"] == "2026-10-26T08:00:00Z"        # the true dates, not today's
    assert s["checked"] == "2026-10-26T12:00:00Z"                                                      # the last time the feed was really read
    assert s["cache_note"] == ("Stored copy. Published 2026-10-26, fetched 2026-10-26. "
                               "Could not check for newer news on 2026-10-29 (the site could not be reached).")
    web.down, clock.t = False, T0 + 4 * DAY
    c.post("/api/watch/scan")
    s = source(c)
    assert s["cached"] is False and s["label"] == "" and s["checked"] == "2026-10-30T12:00:00Z" and s["fetched"] == "2026-10-26T12:00:00Z"


def test_robots_unavailable_is_also_a_stored_copy_with_the_reason_in_words(tmp_path, catalog_dir, monkeypatch):
    from app.watch import feeds
    monkeypatch.setattr(feeds, "_sleep", lambda s: None)
    clock, web = Clock(), FakeWeb(rss([article()]))
    c = build(tmp_path, catalog_dir, web, clock)
    c.post("/api/watch/scan")
    con = sqlite3.connect(tmp_path / "p.sqlite")
    con.execute("UPDATE market_sources SET robots_allowed = NULL, robots_checked_at = NULL")     # no cached success: nothing to fall back on
    con.commit()
    con.close()
    web.robots_status, clock.t = 503, T0 + DAY
    c.post("/api/watch/scan")
    assert "the site's robots.txt could not be read" in source(c)["cache_note"]


def test_an_unchanged_feed_is_checked_not_cached(tmp_path, catalog_dir):
    clock, web = Clock(), FakeWeb(rss([article()]))
    c = build(tmp_path, catalog_dir, web, clock)
    c.post("/api/watch/scan")
    clock.t = T0 + 2 * DAY
    rep = c.post("/api/watch/scan").json()
    assert rep["sources"][0]["status"] == "not_modified" and rep["cached"] is False
    s = source(c)
    assert s["cached"] is False and s["checked"] == "2026-10-28T12:00:00Z" and s["fetched"] == "2026-10-26T12:00:00Z"


# ------------------------------------------------------------------------------ the demo alert
@pytest.fixture
def demo(monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")


@pytest.mark.parametrize("path", ["demo-button", "real-upload"])
def test_in_demo_mode_the_alert_always_carries_a_labelled_source_whichever_way_the_stock_arrived(tmp_path, catalog_dir, demo, path):
    clock = Clock()
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=FakeWeb(b"").factory(), clock=clock))
    if path == "demo-button":
        assert c.post("/api/demo/load").status_code == 200
    else:
        with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
            assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    groups = c.get("/api/launch/exposed").json()["groups"]
    assert groups
    for g in groups:
        s = g["source"]
        complete(s)
        assert s["origin"] == "demo_synthetic" and s["label"] == "Demo article (synthetic)" and s["url"] == SYNTHETIC_URL and s["domain"] == "demo.invalid"
        assert s["published"] == "2026-10-24T12:00:00Z" and s["fetched"] == "2026-10-26T12:00:00Z" and s["freshness"] == "fresh"
        assert s["excerpt"].startswith("Demo article (synthetic).") and s["cached"] is False
        assert (s["launch_date"], s["launch_iso"]) == ("November 20, 2026", "2026-11-20")        # made up: 25 days after the stock list's date
    again = c.post("/api/demo/load").json()                                   # loading again adds nothing new and keeps one labelled item
    assert again["kept"] > 0
    con = sqlite3.connect(tmp_path / "d.sqlite")
    assert con.execute("SELECT COUNT(*) FROM market_items").fetchone()[0] == 1 and con.execute("SELECT COUNT(*) FROM market_events").fetchone()[0] == 1
    reply = "".join(json.loads(f.split("data: ", 1)[1])["text"] for f in
                    c.post("/api/ask", json={"question": "What should I clear before the launch?"}).text.split("\n\n") if f.startswith("event: token"))
    assert "Demo article (synthetic)" in reply and "published 2026-10-24, fetched 2026-10-26" in reply


def test_without_the_demo_switch_an_alert_is_honestly_unsourced_and_nothing_synthetic_exists(tmp_path, catalog_dir):
    c = build(tmp_path, catalog_dir, FakeWeb(b""))
    assert source(c) == {"attached": False, "status": "no dated source attached"}
    assert sqlite3.connect(tmp_path / "p.sqlite").execute("SELECT COUNT(*) FROM market_items").fetchone()[0] == 0


def test_with_the_demo_switch_a_scan_never_replaces_the_synthetic_alert_but_real_items_reach_the_list(tmp_path, catalog_dir, demo):
    web = FakeWeb(rss([article()]))                                            # a real-looking article that matches the alert's line
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=web.factory(), clock=Clock()))
    c.post("/api/demo/load")
    before = source(c)
    assert before["origin"] == "demo_synthetic"
    for n in range(2):                                                         # a scan, then another (the feed is unchanged the second time)
        rep = c.post("/api/watch/scan").json()
        assert rep["matched_events"] == (1 if n == 0 else 0)                   # the article was read and does match the line ...
        assert source(c) == before                                             # ... and the demo alert did not move at all
    c.post("/api/demo/load")                                                   # nor does loading the stock list again
    assert source(c) == before
    items = {i["origin"]: i for i in c.get("/api/watch/items").json()["items"]}
    assert set(items) == {"feed", "demo_synthetic"}                            # the real item is in the news list, labelled as what it is
    assert items["feed"]["label"] == "" and items["demo_synthetic"]["label"] == "Demo article (synthetic)"
    assert items["feed"]["events"][0]["matched_line_ids"]                      # and it really matches: only the pin kept it off the alert


def test_without_the_demo_switch_the_newest_real_article_replaces_the_alerts_source(tmp_path, catalog_dir, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")                                      # a database that was loaded in demo mode ...
    web = FakeWeb(rss([article()]))
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=web.factory(), clock=Clock()))
    c.post("/api/demo/load")
    assert source(c)["origin"] == "demo_synthetic"
    monkeypatch.delenv("LOREG_DEMO")                                           # ... and then used normally
    c.post("/api/watch/scan")
    s = source(c)
    assert s["origin"] == "feed" and s["label"] == "" and s["url"].startswith("https://news.samsung.com/")
    assert {i["origin"]: i["label"] for i in c.get("/api/watch/items").json()["items"]} == {"feed": "", "demo_synthetic": "Demo article (synthetic)"}


# ------------------------------------------------------------------------------ the stored copy of a real article
COPY = {"brand": "Samsung", "url": "https://news.samsung.com/global/synthetic-stored-copy", "title": "SYNTHETIC test copy: QN90D Neo QLED refresh announced",
        "published_at": "2026-10-22T06:00:00Z", "fetched_at": "2026-10-25T09:30:00Z", "excerpt": "SYNTHETIC. Samsung announces the QN90D Neo QLED refresh.",
        "content_hash": "a" * 64, "launch_date": None}


def with_copy(tmp_path, monkeypatch, doc):
    path = tmp_path / "news_copy.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(routes, "NEWS_COPY", path)


def demo_app(tmp_path):
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=FakeWeb(b"").factory(), clock=Clock()))
    c.post("/api/demo/load")
    return c


def test_a_valid_stored_copy_is_used_with_its_true_dates_and_labelled(tmp_path, catalog_dir, demo, monkeypatch):
    with_copy(tmp_path, monkeypatch, COPY)
    s = source(demo_app(tmp_path))
    complete(s)
    assert (s["origin"], s["label"], s["cached"], s["published"], s["fetched"], s["url"]) == \
           ("stored_copy", "Stored copy", True, "2026-10-22T06:00:00Z", "2026-10-25T09:30:00Z", COPY["url"])
    assert s["cache_note"] == "Stored copy of an article fetched on 2026-10-25. It was not fetched again here."


@pytest.mark.parametrize("change", [
    {"url": "https://news.samsung.com.evil.test/x"}, {"url": "http://news.samsung.com/global/x"}, {"published_at": None}, {"published_at": "yesterday"},
    {"excerpt": "x" * 281}, {"excerpt": ""}, {"content_hash": "short"}, {"fetched_at": "not a date"},
    {"title": "SYNTHETIC test copy about nothing", "excerpt": "SYNTHETIC. A story about nothing in particular."},      # names no catalog line
    {"launch_date": "March 12, 2027"},                                                                                  # a date the excerpt does not state
])
def test_a_stored_copy_that_fails_any_check_is_ignored_and_the_labelled_synthetic_one_is_used(tmp_path, catalog_dir, demo, monkeypatch, change):
    with_copy(tmp_path, monkeypatch, {**COPY, **change})
    s = source(demo_app(tmp_path))
    assert s["origin"] == "demo_synthetic" and s["label"] == "Demo article (synthetic)"


def test_a_missing_or_broken_copy_file_falls_back_to_the_synthetic_article(tmp_path, catalog_dir, demo, monkeypatch):
    monkeypatch.setattr(routes, "NEWS_COPY", tmp_path / "does-not-exist.json")
    assert source(demo_app(tmp_path))["origin"] == "demo_synthetic"
    (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(routes, "NEWS_COPY", tmp_path / "broken.json")
    assert source(demo_app(tmp_path / "x" if (tmp_path / "x").mkdir() is None else tmp_path))["origin"] == "demo_synthetic"


# ------------------------------------------------------------------------------ a date question about the synthetic demo article
def reply_to(c, question):
    body = c.post("/api/ask", json={"question": question}).text
    return "".join(json.loads(f.split("data: ", 1)[1])["text"] for f in body.split("\n\n") if f.startswith("event: token"))


def test_a_date_question_on_the_synthetic_article_is_answered_from_stored_news_and_says_the_source_is_synthetic(tmp_path, catalog_dir, demo):
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=FakeWeb(b"").factory(), clock=Clock()))
    c.post("/api/demo/load")
    reply = reply_to(c, "When will Samsung launch the new QN90D?")
    assert reply.startswith("From your stored news only: This source is a synthetic demo article, not real news.")
    assert "Demo article (synthetic)" in reply and "published 2026-10-24, fetched 2026-10-26" in reply
    assert "Launch date: November 20, 2026 (as written in the article)." in reply and "I have no other source for launch dates." in reply
    assert "cannot say when" not in reply and "Not enough data" not in reply
    assert "2027" not in reply and "2028" not in reply and "March" not in reply                              # the only date is the one the article states


def test_a_price_question_on_the_synthetic_article_is_not_enough_data_and_still_says_it_is_synthetic(tmp_path, catalog_dir, demo):
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=FakeWeb(b"").factory(), clock=Clock()))
    c.post("/api/demo/load")
    reply = reply_to(c, "What will Samsung charge for the new QN90D?")
    assert reply.startswith("Not enough data: I don't have a price for that.") and "synthetic demo article, not real news" in reply


def test_a_date_question_on_a_real_article_with_no_launch_date_says_so_and_does_not_call_it_synthetic(tmp_path, catalog_dir):
    no_date = "SYNTHETIC. Samsung announced the QN90D Neo QLED refresh will launch this spring."
    c = build(tmp_path, catalog_dir, FakeWeb(rss([article(html=no_date)])))
    c.post("/api/watch/scan")
    reply = reply_to(c, "When will Samsung launch the new QN90D?")
    assert reply.startswith("From your stored news only: ") and "synthetic demo article" not in reply
    assert "That article gives no launch date, so I cannot say when." in reply


# ------------------------------------------------------------------------------ no real publisher name on a synthetic item
def test_nothing_about_the_synthetic_article_names_a_real_publisher_anywhere_it_is_shown(tmp_path, catalog_dir, demo):
    c = AuthedClient(create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=FakeWeb(rss([article()])).factory(), clock=Clock()))
    c.post("/api/demo/load")
    c.post("/api/watch/scan")                                                   # a scan puts a real-looking item next to it; they must not be mixed up
    group = c.get("/api/launch/exposed").json()["groups"][0]
    alert = c.get(f"/api/alerts/{group['alert_id']}").json()
    listed = next(i for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "demo_synthetic")
    scan_item = next(i for i in c.post("/api/watch/scan").json()["items"] if i["origin"] == "demo_synthetic")
    sources = [group["source"], alert["source"]]
    for src in sources:
        assert (src["name"], src["label"], src["domain"], src["url"]) == ("Loreg demo", "Demo article (synthetic)", "demo.invalid", SYNTHETIC_URL)
    for item in (listed, scan_item):
        assert (item["source"], item["label"], item["domain"]) == ("Loreg demo", "Demo article (synthetic)", "demo.invalid")
    without_events = lambda item: {k: v for k, v in item.items() if k != "events"}      # an event's "brand" is the PRODUCT brand used for matching  # noqa: E731
    shown = json.dumps([sources, without_events(listed), without_events(scan_item), group["source_note"], alert["source_note"]])
    for publisher in ("Samsung", "samsung", "Newsroom", "news.samsung.com"):
        assert publisher not in shown, publisher                               # not in a name, address, title, excerpt, label or note
    reply = reply_to(c, "What should I clear before the launch?")
    cited = reply[reply.index("Source:"):reply.index("published")]
    assert cited == "Source: Loreg demo (Demo article (synthetic)), "
    con = sqlite3.connect(tmp_path / "d.sqlite")
    row = con.execute("SELECT s.name, s.url, i.domain FROM market_items i JOIN market_sources s ON s.id = i.source_id WHERE i.origin = 'demo_synthetic'").fetchone()
    assert row == ("Loreg demo", "https://demo.invalid/", "demo.invalid")      # in the database too, not only in the labels
    real = con.execute("SELECT s.name FROM market_items i JOIN market_sources s ON s.id = i.source_id WHERE i.origin = 'feed'").fetchone()
    assert real == ("Samsung Global Newsroom",)                                  # the real item keeps its real publisher
