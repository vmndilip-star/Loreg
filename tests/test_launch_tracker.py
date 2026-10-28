"""Launch tracker over stored news: watches, history, confirmation on "Check for news", synthetic labelling, nothing lost on reload.

Offline: the feed is a fake transport and every article is SYNTHETIC, written for these tests. The clock is injected."""

import sqlite3
from datetime import date, datetime, timezone

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.thresholds import load_thresholds
from app.schemas.outputs import LaunchEvent, MarketExtraction
from app.storage.db import connect
from app.watch import tracker
from test_news_provenance import Clock
from test_watch import POISONS, FakeWeb, rss

TH = load_thresholds()
HEAD = "SYNTHETIC TEST ARTICLE (written for Loreg's tests, not real news). "
LINE = "QN90D"


def at(y, m, d):
    return datetime(y, m, d, 12, tzinfo=timezone.utc)


def item(text, slug, pub, title="SYNTHETIC: Samsung QN90D Neo QLED news", host="news.samsung.com"):
    return {"title": title, "link": f"https://{host}/global/synthetic-{slug}", "html": f"<p>{HEAD}{text}</p>", "pub": pub}


LAUNCH = item("Samsung will launch the QN90D Neo QLED lineup refresh on November 20, 2026 in the US.", "a", "Tue, 20 Oct 2026 08:00:00 +0000")
MOVED = item("Samsung will launch the QN90D Neo QLED lineup refresh on December 4, 2026 in the US.", "b", "Sun, 25 Oct 2026 08:00:00 +0000")
ON_SALE = item("The Samsung QN90D Neo QLED lineup refresh is now available in the US.", "c", "Sat, 21 Nov 2026 08:00:00 +0000",
               title="SYNTHETIC: Samsung QN90D Neo QLED now available")


class Shop:
    def __init__(self, tmp_path, catalog_dir, clock=None, llm=None, demo=False, name="t.sqlite"):
        self.path, self.web, self.clock = tmp_path / name, FakeWeb(b""), clock or Clock(at(2026, 10, 26))
        self.c = TestClient(create_app(self.path, as_of="2026-10-26", http_factory=self.web.factory(), clock=self.clock, llm=llm))
        self.v = 0
        if demo:
            assert self.c.post("/api/demo/load").status_code == 200
        else:
            self.upload(catalog_dir)

    def upload(self, catalog_dir):
        with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
            assert self.c.post("/api/upload", files={"file": ("upload_demo_200.csv", f, "text/csv")}).status_code == 200

    def scan(self, *items):
        self.v += 1
        self.web.feed, self.web.etag = rss(list(items)), f'"v{self.v}"'
        rep = self.c.post("/api/watch/scan").json()
        assert rep["network"] == "ok", rep
        return rep

    def conn(self):
        return connect(self.path)

    def watches(self, synthetic=False):
        con = self.conn()
        try:
            return [w for w in tracker.read(con, 1, self.clock().date(), TH) if LINE in w["line"] and w["synthetic"] == synthetic]
        finally:
            con.close()

    def watch(self, synthetic=False):
        found = self.watches(synthetic)
        assert len(found) == 1, found
        return found[0]

    def rows(self, table):
        con = self.conn()
        try:
            return [dict(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY id")]
        finally:
            con.close()


# ------------------------------------------------------------------------------ dates and states
def test_a_future_exact_date_is_announced_and_recorded_once(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    rep = s.scan(LAUNCH)
    w = s.watch()
    assert (w["state"], w["date"], w["date_iso"], w["days_until"]) == ("announced", "November 20, 2026", "2026-11-20", 25)
    assert w["synthetic"] is False and w["label"] == "" and w["source"]["url"] == LAUNCH["link"] and w["source"]["published"] == "2026-10-20T08:00:00Z"
    assert [(h["old_date"], h["new_date"], h["source_url"]) for h in w["history"]] == [(None, "November 20, 2026", LAUNCH["link"])]
    assert rep["tracker"]["watches"] >= 1 and rep["tracker"]["history"] >= 1


def test_due_on_the_day_then_unverified_after_the_grace_period_never_launched_or_delayed(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    for day, state in ((at(2026, 11, 19), "announced"), (at(2026, 11, 20), "due"), (at(2026, 11, 27), "due"), (at(2026, 11, 28), "unverified"),
                       (at(2027, 6, 1), "unverified")):
        s.clock.t = day
        w = s.watch()
        assert w["state"] == state and w["confirmed_on"] is None, (day, w["state"])
    assert "This does not mean it was delayed." in w["text"]


def test_a_later_article_with_another_date_is_a_date_change_recorded_in_history_and_checking_again_adds_nothing(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    s.scan(LAUNCH, MOVED)
    w = s.watch()
    assert (w["state"], w["date"]) == ("date_changed", "December 4, 2026") and "Before: November 20, 2026." in w["text"]
    assert [(h["old_date"], h["new_date"], h["source_url"]) for h in w["history"]] == [
        (None, "November 20, 2026", LAUNCH["link"]), ("November 20, 2026", "December 4, 2026", MOVED["link"])]
    history, notes = s.rows("launch_history"), s.rows("notifications")
    s.scan(LAUNCH, MOVED)
    s.scan(LAUNCH, MOVED)
    assert s.rows("launch_history") == history and s.rows("notifications") == notes


def test_reading_the_tracker_writes_nothing(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    con = s.conn()
    before = con.total_changes
    s.clock.t = at(2026, 11, 30)
    tracker.read(con, 1, s.clock().date(), TH)
    assert con.total_changes == before
    con.close()


def test_a_month_only_date_is_never_due(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(item("Samsung will launch the QN90D Neo QLED lineup refresh in March 2027 in the US.", "m", "Tue, 20 Oct 2026 08:00:00 +0000"))
    for day in (at(2026, 10, 26), at(2027, 3, 1), at(2027, 3, 31), at(2029, 1, 1)):
        s.clock.t = day
        w = s.watch()
        assert (w["state"], w["date"], w["days_until"]) == ("announced", None, None)       # no exact day is recorded, so nothing can come due
    assert s.watch()["history"] == []


def test_a_date_without_a_year_is_approximate_and_never_due(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(item("Samsung will launch the QN90D Neo QLED lineup refresh on November 20 in the US.", "n", "Tue, 20 Oct 2026 08:00:00 +0000"))
    for day in (at(2026, 11, 20), at(2026, 12, 31), at(2028, 11, 21)):
        s.clock.t = day
        w = s.watch()
        assert (w["state"], w["date"], w["date_iso"]) == ("date_approximate", "November 20", None)


class TwoDates:
    """A model stand-in that reports two different dates for the same line in one article."""
    is_mock = True

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        if schema is not MarketExtraction:
            return mock_output()
        q1 = "Samsung will launch the QN90D Neo QLED lineup refresh on November 20, 2026 in the US."
        q2 = "A second report says the QN90D Neo QLED arrives on December 4, 2026."
        return MarketExtraction(events=[LaunchEvent(brand="Samsung", product_family="QN90D Neo QLED", date_stated=True, launch_date="November 20, 2026", quote=q1),
                                        LaunchEvent(brand="Samsung", product_family="QN90D Neo QLED", date_stated=True, launch_date="December 4, 2026", quote=q2)])


def test_several_dates_for_one_line_in_one_article_record_no_date(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir, llm=TwoDates())
    s.scan(item("Samsung will launch the QN90D Neo QLED lineup refresh on November 20, 2026 in the US. "
                "A second report says the QN90D Neo QLED arrives on December 4, 2026.", "two", "Tue, 20 Oct 2026 08:00:00 +0000"))
    con = s.conn()
    assert {r["launch_date"] for r in con.execute("SELECT launch_date FROM market_events")} == {"November 20, 2026", "December 4, 2026"}
    con.close()
    w = s.watch()
    assert (w["state"], w["date"], w["history"]) == ("announced", None, [])
    s.clock.t = at(2026, 12, 30)
    assert s.watch()["state"] == "announced"


# ------------------------------------------------------------------------------ confirmation
def test_a_later_source_that_names_the_line_and_says_now_available_confirms_the_launch(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    s.clock.t = at(2026, 11, 22)
    assert s.watch()["state"] == "due"
    s.scan(LAUNCH, ON_SALE)
    w = s.watch()
    assert (w["state"], w["confirmed_on"]) == ("launch_confirmed", "2026-11-21")
    assert w["text"] == "Launch confirmed by a source dated 2026-11-21." and w["confirmation"]["url"] == ON_SALE["link"]


def test_a_confirmation_from_a_source_older_than_the_stated_day_is_rejected(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    early = dict(ON_SALE, pub="Thu, 19 Nov 2026 08:00:00 +0000")
    s.clock.t = at(2026, 11, 22)
    s.scan(LAUNCH, early)
    w = s.watch()
    assert (w["state"], w["confirmed_on"], w["confirmation"]) == ("due", None, None)


def test_future_or_delay_wording_after_the_day_does_not_confirm(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.clock.t = at(2026, 11, 30)
    later = [item(text, f"w{n}", "Sat, 21 Nov 2026 08:00:00 +0000", title="SYNTHETIC: Samsung QN90D Neo QLED update") for n, text in enumerate([
        "The Samsung QN90D Neo QLED lineup refresh will be available soon in the US.",
        "The Samsung QN90D Neo QLED lineup refresh is delayed; the current models are now available.",
        "The Samsung QN90D Neo QLED lineup refresh has been postponed, so older sets are on sale now.",
        "The Samsung QN90D Neo QLED lineup refresh was pushed back; older sets are now available.",
    ])]
    s.scan(LAUNCH, *later)
    assert s.watch()["state"] == "unverified"


def test_a_source_that_does_not_name_the_line_does_not_confirm(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.clock.t = at(2026, 11, 22)
    other = item("The Samsung Bespoke AI washer is now available in the US.", "o", "Sat, 21 Nov 2026 08:00:00 +0000", title="SYNTHETIC: a washer")
    s.scan(LAUNCH, other)
    assert s.watch()["state"] == "due"


def test_a_poisoned_article_creates_and_changes_nothing(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    poison = dict(POISONS)["bracket system tag"]
    s.scan(item(f"Samsung will launch the QN90D Neo QLED lineup refresh on November 20, 2026 in the US. {poison}", "p", "Tue, 20 Oct 2026 08:00:00 +0000"))
    assert s.watches() == [] and s.rows("launch_history") == [] and s.rows("notifications") == []
    s.scan(LAUNCH)
    s.clock.t = at(2026, 11, 22)
    s.scan(LAUNCH, item(f"The Samsung QN90D Neo QLED lineup refresh is now available. {dict(POISONS)['ignore previous instructions']}", "q",
                        "Sat, 21 Nov 2026 08:00:00 +0000"))
    assert s.watch()["state"] == "due"                                       # a poisoned article never confirms either


def test_a_look_alike_host_is_never_stored_and_never_confirms(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.clock.t = at(2026, 11, 22)
    rep = s.scan(LAUNCH, dict(ON_SALE, link="https://news.samsung.com.evil.test/global/synthetic-c"),
                 dict(ON_SALE, link="https://evilnews.samsung.com/global/synthetic-d"))
    assert sum(src.get("offsite_links", 0) for src in rep["sources"]) == 2
    assert s.watch()["state"] == "due"
    con = s.conn()                                                           # even if one got into the database, it would not count
    con.execute("UPDATE market_items SET url = 'https://news.samsung.com.evil.test/x' WHERE url = ?", (LAUNCH["link"],))
    con.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, excerpt, domain, origin, content_hash)"
                " VALUES (1, 'https://news.samsung.com.evil.test/y', 'y', 'SYNTHETIC: Samsung QN90D Neo QLED now available', '2026-11-21T08:00:00Z',"
                " '2026-11-21T09:00:00Z', '', 'The Samsung QN90D Neo QLED is now available.', 'news.samsung.com.evil.test', 'feed', 'hash-y')")
    assert s.watches() == []                                                # its date came from a look-alike address, so it is not tracked
    con.close()


# ------------------------------------------------------------------------------ the synthetic demo article
def test_the_synthetic_watch_is_labelled_and_a_real_article_never_dates_or_confirms_it(tmp_path, catalog_dir, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    s = Shop(tmp_path, catalog_dir, demo=True)
    s.scan()                                                                 # an empty feed: the synthetic watch is recorded
    w = s.watch(synthetic=True)
    assert (w["label"], w["source"]["origin"], w["date"]) == ("Demo article (synthetic)", "demo_synthetic", "November 20, 2026")
    assert w["history"] and all(h["source_url"].startswith("https://demo.invalid/") for h in w["history"])
    s.clock.t = at(2026, 11, 22)
    s.scan(dict(LAUNCH, link="https://news.samsung.com/global/synthetic-real-a"), dict(MOVED, link="https://news.samsung.com/global/synthetic-real-b"), ON_SALE)
    w = s.watch(synthetic=True)
    assert (w["state"], w["date"], w["confirmed_on"], w["confirmation"]) == ("due", "November 20, 2026", None, None)
    assert all(h["source_url"].startswith("https://demo.invalid/") for h in w["history"])
    real = s.watch(synthetic=False)
    assert real["label"] == "" and real["date"] == "December 4, 2026" and real["state"] == "date_changed"
    notes = s.rows("notifications")
    synthetic_ids = {r["id"] for r in s.rows("launch_watch") if r["synthetic"]}
    assert all(n["text"].startswith("From the synthetic demo article. ") for n in notes if n["watch_id"] in synthetic_ids)
    assert not any(n["text"].startswith("From the synthetic") for n in notes if n["watch_id"] not in synthetic_ids)


# ------------------------------------------------------------------------------ nothing is lost
def test_demo_reload_upload_and_restart_keep_every_history_row(tmp_path, catalog_dir, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    s = Shop(tmp_path, catalog_dir, demo=True)
    s.scan(LAUNCH)
    s.scan(LAUNCH, MOVED)
    history, watches = s.rows("launch_history"), s.rows("launch_watch")
    assert len(history) >= 3
    assert s.c.post("/api/demo/load").status_code == 200                    # demo reload
    s.upload(catalog_dir)                                                    # a new stock list
    s2 = Shop(tmp_path, catalog_dir, demo=True)                              # a restart on the same database file
    assert s2.rows("launch_history") == history and s2.rows("launch_watch") == watches
    s2.scan(LAUNCH, MOVED)
    assert s2.rows("launch_history") == history


def test_a_reset_is_a_new_database_file_and_starts_empty(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    assert s.rows("launch_history")
    fresh = Shop(tmp_path, catalog_dir, name="fresh.sqlite")
    assert fresh.rows("launch_history") == [] and fresh.rows("launch_watch") == []
    assert s.rows("launch_history")                                          # the old file still has its history
    assert sqlite3.connect(s.path).execute("SELECT COUNT(*) FROM launch_history").fetchone()[0] == 1
    assert date.fromisoformat(s.rows("launch_history")[0]["new_iso"]) == date(2026, 11, 20)
