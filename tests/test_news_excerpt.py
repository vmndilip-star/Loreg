"""S2: only a short, verbatim excerpt of an article is kept; the body is cleared once it has been read. Offline, SYNTHETIC articles."""

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.schemas.outputs import LaunchEvent, MarketExtraction
from app.storage.db import SCHEMA_VERSION, init_db
from app.watch.excerpt import make_excerpt
from app.watch.sanitize import sanitize_html
from test_watch import BASE, FakeModel, FakeWeb, article, rss

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
LONG = ("SYNTHETIC. " + "An opening sentence about the weather and nothing else. " * 3
        + "Samsung will launch the QN90D Neo QLED refresh on March 12, 2027 in the US " + "and keeps going with more words " * 40)


def db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def make(tmp_path, web, clock=None, llm=None, name="e.sqlite"):
    return TestClient(create_app(tmp_path / name, as_of="2026-10-26", http_factory=web.factory(), clock=clock or (lambda: T0), llm=llm))


# ------------------------------------------------------------------------------ the excerpt itself
@pytest.mark.parametrize("text", [LONG, BASE, "", "no cue here, just words " * 30, "Launch first. " + "x" * 400, "é" * 300 + " launch " + "ü" * 300, "a" * 1000,
                                  "Short.", " ".join(["word"] * 200) + " Samsung will launch a thing."])
@pytest.mark.parametrize("limit", [280, 100, 20])
def test_an_excerpt_is_a_short_exact_stretch_of_the_article(text, limit):
    e = make_excerpt(text, limit)
    assert len(e) <= limit and e in text and e == e.strip()


def test_the_excerpt_starts_at_the_sentence_with_the_launch_cue():
    e = make_excerpt(LONG, 280)
    assert e.startswith("Samsung will launch the QN90D Neo QLED refresh") and "opening sentence" not in e and "weather" not in e
    assert make_excerpt("Plain words only. Nothing else.", 280) == "Plain words only. Nothing else."


def test_the_excerpt_does_not_cut_a_word_in_half():
    e = make_excerpt("Samsung will launch " + "abcdefghij " * 40, 100)
    assert e.endswith("abcdefghij") and len(e) <= 100


# ------------------------------------------------------------------------------ what a scan keeps
def test_after_a_scan_no_body_is_kept_only_the_excerpt_hash_and_dates(tmp_path):
    items = [article(html=LONG, slug="a"), article(title="SYNTHETIC: unrelated", html="SYNTHETIC. A story about nothing in particular.", slug="b")]
    c = make(tmp_path, FakeWeb(rss(items)))
    assert c.post("/api/watch/scan").status_code == 200
    rows = db(tmp_path / "e.sqlite").execute("SELECT * FROM market_items ORDER BY id").fetchall()
    assert len(rows) == 2
    body = sanitize_html(f"<p>{LONG}</p>")[0]
    for r in rows:
        assert r["text"] == "" and r["body_cleared_at"] and r["extracted"] in (1, -1)          # read, or skipped as not launch news: either way cleared
        assert r["excerpt"] and len(r["excerpt"]) <= 280 and len(r["content_hash"]) == 64
        assert r["published_at"] and r["fetched_at"] == "2026-10-26T12:00:00Z" and r["domain"] == "news.samsung.com" and r["origin"] == "feed"
    first = next(r for r in rows if r["extracted"] == 1)
    assert first["excerpt"] in body and first["excerpt"].startswith("Samsung will launch")


def test_the_events_quote_is_kept_only_if_it_really_is_in_the_article(tmp_path):
    invented = MarketExtraction(events=[LaunchEvent(brand="Samsung", product_family="QN90D Neo QLED", date_stated=True, launch_date="March 12, 2027",
                                                    quote="Samsung promised a 90% discount to everyone")])
    c = make(tmp_path, FakeWeb(rss([article(html=LONG)])), llm=FakeModel(invented))
    c.post("/api/watch/scan")
    assert db(tmp_path / "e.sqlite").execute("SELECT quote FROM market_events").fetchone()["quote"] is None
    real = MarketExtraction(events=[LaunchEvent(brand="Samsung", product_family="QN90D Neo QLED", date_stated=True, launch_date="March 12, 2027",
                                                quote="Samsung will launch the QN90D Neo QLED refresh on March 12, 2027")])
    c2 = make(tmp_path, FakeWeb(rss([article(html=LONG)])), llm=FakeModel(real), name="e2.sqlite")
    c2.post("/api/watch/scan")
    assert db(tmp_path / "e2.sqlite").execute("SELECT quote FROM market_events").fetchone()["quote"].startswith("Samsung will launch")


def launch_items(n):
    return [article(title=f"SYNTHETIC launch {i}", html=f"SYNTHETIC. Samsung will launch product number {i} in 2027.", slug=f"n{i}") for i in range(n)]


def bodies_kept(tmp_path):
    return db(tmp_path / "e.sqlite").execute("SELECT COUNT(*) FROM market_items WHERE text != ''").fetchone()[0]


def test_articles_beyond_the_per_scan_limit_keep_their_body_only_until_a_later_scan_reads_them(tmp_path):
    c = make(tmp_path, FakeWeb(rss(launch_items(25))))
    kept = []
    for _ in range(3):
        c.post("/api/watch/scan")
        kept.append(bodies_kept(tmp_path))
    assert kept == [15, 5, 0]                                  # ten are read per scan; each body goes as soon as it has been read


def test_an_unread_body_is_cleared_after_the_keep_days_but_the_excerpt_stays(tmp_path):
    now = {"t": T0}
    web = FakeWeb(rss(launch_items(25)))
    c = make(tmp_path, web, clock=lambda: now["t"])
    c.post("/api/watch/scan")
    assert bodies_kept(tmp_path) == 15
    web.down = True                                            # the feed cannot be reached on the next check
    now["t"] = T0 + timedelta(days=8)
    c.post("/api/watch/scan")
    assert bodies_kept(tmp_path) == 0
    unread = db(tmp_path / "e.sqlite").execute("SELECT * FROM market_items WHERE extracted = -2").fetchall()
    assert len(unread) == 15 and all(r["excerpt"] and r["body_cleared_at"] == "2026-11-03T12:00:00Z" for r in unread)


def test_the_database_never_holds_more_than_an_excerpt_per_read_article(tmp_path):
    c = make(tmp_path, FakeWeb(rss([article(html=LONG + " filler" * 2000, slug="big")])))
    c.post("/api/watch/scan")
    con = db(tmp_path / "e.sqlite")
    assert con.execute("SELECT MAX(LENGTH(text)) FROM market_items").fetchone()[0] == 0
    assert con.execute("SELECT MAX(LENGTH(excerpt)) FROM market_items").fetchone()[0] <= 280
    items = c.get("/api/watch/items").json()["items"]
    assert items[0]["excerpt"] and "text" not in items[0] and items[0]["domain"] == "news.samsung.com"


# ------------------------------------------------------------------------------ migration
def v3_database(path, bodies):
    init_db(path)
    con = sqlite3.connect(path)
    for col in ("excerpt", "domain", "origin", "body_cleared_at"):
        con.execute(f"ALTER TABLE market_items DROP COLUMN {col}")
    con.execute("ALTER TABLE market_sources DROP COLUMN last_ok_at")
    con.execute("PRAGMA user_version = 3")
    con.execute("INSERT INTO shops (name, created_at) VALUES ('s', 'x')")
    con.execute("INSERT INTO market_sources (shop_id, name, brand, url, kind, last_fetch_at, last_status, created_at)"
                " VALUES (1, 'Samsung Global Newsroom', 'Samsung', 'https://news.samsung.com/global/feed', 'rss', '2026-10-20T08:00:00Z', 'ok', 'x')")
    for n, (body, extracted) in enumerate(bodies):
        url = f"https://news.samsung.com/global/synthetic-{n}"
        con.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, content_hash, extracted)"
                    " VALUES (1, ?, ?, ?, '2026-10-19T08:00:00Z', '2026-10-20T08:00:00Z', ?, ?, ?)", (url, url, f"SYNTHETIC {n}", body, f"h{n:063d}", extracted))
    con.commit()
    con.close()


def test_a_version_3_database_keeps_an_excerpt_and_loses_the_bodies(tmp_path):
    path = tmp_path / "old.sqlite"
    v3_database(path, [(LONG * 10, 1), (LONG, 0), ("short body about nothing", -1)])
    init_db(path)
    con = db(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 4
    rows = con.execute("SELECT * FROM market_items ORDER BY id").fetchall()
    assert all(r["text"] == "" and r["body_cleared_at"] and r["domain"] == "news.samsung.com" and r["origin"] == "feed" for r in rows)
    assert rows[0]["excerpt"] in LONG * 10 and len(rows[0]["excerpt"]) <= 280 and rows[0]["excerpt"].startswith("Samsung will launch")
    assert [r["extracted"] for r in rows] == [1, -2, -1]            # the unread one can no longer be read, and says so
    assert rows[2]["excerpt"] == "short body about nothing"
    assert con.execute("SELECT last_ok_at FROM market_sources").fetchone()[0] == "2026-10-20T08:00:00Z"
    init_db(path)                                                   # a second run changes nothing
    assert [r["excerpt"] for r in con.execute("SELECT excerpt FROM market_items ORDER BY id")] == [r["excerpt"] for r in rows]


def test_an_empty_database_starts_at_version_4_with_the_new_columns(tmp_path):
    init_db(tmp_path / "n.sqlite")
    con = db(tmp_path / "n.sqlite")
    cols = {r["name"] for r in con.execute("PRAGMA table_info(market_items)")}
    assert con.execute("PRAGMA user_version").fetchone()[0] == 4 and {"excerpt", "domain", "origin", "body_cleared_at"} <= cols
