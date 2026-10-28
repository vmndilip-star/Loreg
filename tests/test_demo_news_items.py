"""Stored copies of real items (data/demo/news_items.json): they let the demo's Watch page show real news when robots.txt times out.
They are always labelled "Stored copy" with their true dates, never attach to an alert, and are never shown as fresh. Offline."""

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import routes
from app.api.main import create_app
from app.demo import NEWS_ITEMS
from app.watch.allowlist import check_url, load_news_config
from test_watch import FakeWeb, article, rss

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
ALLOWED = load_news_config().allowed_hosts
H = "a" * 64


def item(n, **kw):
    base = {"url": f"https://news.samsung.com/global/synthetic-stored-{n}", "title": f"SYNTHETIC stored item {n}", "published_at": f"2026-10-2{n}T06:00:00Z",
            "fetched_at": "2026-10-27T09:30:00Z", "excerpt": f"SYNTHETIC. Excerpt number {n}.", "content_hash": f"{n}" * 64, "domain": "news.samsung.com"}
    return {**base, **kw}


def write(tmp_path, items):
    path = tmp_path / "items.json"
    path.write_text(json.dumps({"brand": "Samsung", "items": items}), encoding="utf-8")
    return path


def demo_app(tmp_path, monkeypatch, items_path, web=None, name="d.sqlite"):
    monkeypatch.setenv("LOREG_DEMO", "1")
    monkeypatch.setattr(routes, "NEWS_ITEMS", items_path)
    c = TestClient(create_app(tmp_path / name, as_of="2026-10-26", http_factory=(web or FakeWeb(b"")).factory(), clock=lambda: NOW))
    assert c.post("/api/demo/load").status_code == 200
    return c


# ------------------------------------------------------------------------------ the file that ships with the repo
def test_the_shipped_file_holds_real_items_with_true_dates_and_no_article_text():
    doc = json.loads(NEWS_ITEMS.read_text(encoding="utf-8"))
    items = doc["items"]
    assert 1 <= len(items) <= 10 and doc["captured_by"] == "scripts/capture_demo_news.py"
    today = datetime.now(timezone.utc).date()
    for d in items:
        assert check_url(d["url"], ALLOWED) == "" and d["url"].startswith("https://news.samsung.com/global/")
        assert date.fromisoformat(d["published_at"][:10]) <= date.fromisoformat(d["fetched_at"][:10]) <= today          # published, then fetched; nothing in the future
        assert re.fullmatch(r"[0-9a-f]{64}", d["content_hash"]) and d["title"].strip()
        assert set(d) == {"url", "title", "published_at", "fetched_at", "content_hash", "domain"}               # no article text of any kind (copyright)
    assert NEWS_ITEMS.stat().st_size < 20_000
    assert len({d["content_hash"] for d in items}) == len(items) and len({d["url"] for d in items}) == len(items)


# ------------------------------------------------------------------------------ what the demo does with them
def test_in_demo_mode_the_items_appear_as_labelled_stored_copies_with_their_true_dates(tmp_path, monkeypatch):
    c = demo_app(tmp_path, monkeypatch, write(tmp_path, [item(1), item(2), item(3)]))
    items = {i["title"]: i for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "stored_copy"}
    assert len(items) == 3
    for n in (1, 2, 3):
        i = items[f"SYNTHETIC stored item {n}"]
        assert i["label"] == "Stored copy" and i["fetched_at"] == "2026-10-27T09:30:00Z" and i["undated"] is False and i["events"] == []
        assert i["fetched_at"] != "2026-10-28T09:00:00Z"                       # the clock's "now" was not written over the true fetch date
    con = sqlite3.connect(tmp_path / "d.sqlite")
    assert con.execute("SELECT MAX(LENGTH(text)) FROM market_items").fetchone()[0] == 0           # no body anywhere


def test_they_never_become_the_alert_and_the_alert_stays_the_synthetic_demo_article(tmp_path, monkeypatch):
    c = demo_app(tmp_path, monkeypatch, write(tmp_path, [item(1, title="SYNTHETIC QN90D Neo QLED refresh announced", excerpt="SYNTHETIC. QN90D Neo QLED refresh.")]))
    src = c.get("/api/launch/exposed").json()["groups"][0]["source"]
    assert src["origin"] == "demo_synthetic" and src["label"] == "Demo article (synthetic)"


def test_when_robots_txt_times_out_the_watch_list_still_shows_the_real_items_and_none_is_called_fresh(tmp_path, monkeypatch):
    from app.watch import feeds
    monkeypatch.setattr(feeds, "_sleep", lambda s: None)
    c = demo_app(tmp_path, monkeypatch, write(tmp_path, [item(1), item(2)]), web=FakeWeb(rss([article()]), robots_status=503))
    rep = c.post("/api/watch/scan").json()
    assert rep["sources"][0]["status"] == "robots_unavailable" and rep["cached"] is True and rep["network"] == "unavailable"
    assert "Showing stored copies" in rep["message"]
    shown = [i for i in rep["items"] if i["origin"] != "demo_synthetic"]
    assert len(shown) == 2 and all(i["label"] == "Stored copy" and i["fetched_at"] == "2026-10-27T09:30:00Z" for i in shown)


def test_loading_again_adds_nothing_and_without_the_demo_switch_nothing_is_installed(tmp_path, monkeypatch):
    path = write(tmp_path, [item(1), item(2)])
    c = demo_app(tmp_path, monkeypatch, path)
    c.post("/api/demo/load")
    assert sum(1 for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "stored_copy") == 2
    monkeypatch.delenv("LOREG_DEMO")
    plain = TestClient(create_app(tmp_path / "p.sqlite", as_of="2026-10-26", http_factory=FakeWeb(b"").factory(), clock=lambda: NOW))
    with open(Path(routes.__file__).resolve().parents[2] / "data" / "demo" / "upload_demo_200.csv", "rb") as f:
        plain.post("/api/upload", files={"file": ("d.csv", f, "text/csv")})
    assert plain.get("/api/watch/items").json()["items"] == []


@pytest.mark.parametrize("bad", [
    {"url": "https://news.samsung.com.evil.test/x"}, {"url": "http://news.samsung.com/global/x"}, {"published_at": None}, {"published_at": "soon"},
    {"excerpt": "x" * 281}, {"excerpt": ""}, {"content_hash": "short"}, {"fetched_at": "never"}, {"title": "  "}, {"url": None},
])
def test_an_item_that_fails_any_check_is_skipped_and_the_others_still_load(tmp_path, monkeypatch, bad):
    c = demo_app(tmp_path, monkeypatch, write(tmp_path, [item(1, **bad), item(2)]))
    assert [i["title"] for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "stored_copy"] == ["SYNTHETIC stored item 2"]


def test_a_missing_or_broken_file_changes_nothing(tmp_path, monkeypatch):
    for content in (None, "{not json", json.dumps({"items": "nope"}), json.dumps([1, 2])):
        path = tmp_path / "x.json"
        if content is None:
            path.unlink(missing_ok=True)
        else:
            path.write_text(content, encoding="utf-8")
        c = demo_app(tmp_path, monkeypatch, path, name=f"d{abs(hash(str(content)))}.sqlite")
        assert [i for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "stored_copy"] == []


def test_an_item_read_live_later_becomes_a_real_fetched_item_with_the_new_date(tmp_path, monkeypatch):
    live = article(slug="stored-1", title="SYNTHETIC stored item 1")
    c = demo_app(tmp_path, monkeypatch, write(tmp_path, [item(1, url="https://news.samsung.com/global/synthetic-stored-1")]), web=FakeWeb(rss([live])))
    before = next(i for i in c.get("/api/watch/items").json()["items"] if i["origin"] == "stored_copy")
    assert before["fetched_at"] == "2026-10-27T09:30:00Z"
    rep = c.post("/api/watch/scan").json()
    assert rep["sources"][0]["refreshed"] == 1
    after = [i for i in c.get("/api/watch/items").json()["items"] if i["url"].endswith("synthetic-stored-1")]
    assert len(after) == 1 and after[0]["origin"] == "feed" and after[0]["label"] == "" and after[0]["fetched_at"] == "2026-10-28T09:00:00Z"
    con = sqlite3.connect(tmp_path / "d.sqlite")
    assert con.execute("SELECT MAX(LENGTH(text)) FROM market_items WHERE origin = 'feed' AND extracted != 0").fetchone()[0] in (0, None)   # read, so the body went
