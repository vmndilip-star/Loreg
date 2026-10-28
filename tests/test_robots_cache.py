"""The robots.txt cache. A successful answer is trusted for 24 hours and keeps its true checked-on date. When robots.txt cannot be read,
the last successful answer is used (and says so, with its date); with no successful answer on record the scan fails closed and the feed is
not requested. A cached "disallowed" is never overridden. There is no other way round robots.txt. Offline."""

import sqlite3
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.watch import feeds
from test_watch import FakeWeb, article, rss
from auth import AuthedClient

T0 = datetime(2026, 10, 26, 12, 0, tzinfo=timezone.utc)
HOUR = timedelta(hours=1)


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    monkeypatch.setattr(feeds, "_sleep", lambda s: None)


def make(tmp_path, web, clock):
    return AuthedClient(create_app(tmp_path / "r.sqlite", as_of="2026-10-26", http_factory=web.factory(), clock=clock))


def robots_fetches(web):
    return sum(1 for r in web.requests if r.url.path == "/robots.txt")


def feed_fetches(web):
    return sum(1 for r in web.requests if r.url.path == "/global/feed")


def stored_robots(tmp_path):
    con = sqlite3.connect(tmp_path / "r.sqlite")
    row = con.execute("SELECT robots_allowed, robots_checked_at FROM market_sources").fetchone()
    con.close()
    return row


def scan(c):
    rep = c.post("/api/watch/scan").json()
    return rep, rep["sources"][0]


# ------------------------------------------------------------------------------ a cached success is a hit for 24 hours
def test_a_successful_robots_answer_is_cached_for_24_hours_and_keeps_its_true_date(tmp_path):
    web, clock = FakeWeb(rss([article()])), Clock()
    c = make(tmp_path, web, clock)
    _, first = scan(c)
    assert first["status"] == "ok" and robots_fetches(web) == 1
    assert first["robots"] == {"checked_on": "2026-10-26", "from_cache": False} and "robots.txt read on 2026-10-26" in first["message"]
    assert stored_robots(tmp_path) == (1, "2026-10-26T12:00:00Z")
    for hours in (1, 12, 23):
        clock.t = T0 + hours * HOUR
        web.etag = f'"w{hours}"'                                  # the feed itself has changed, so it is fetched again ...
        _, again = scan(c)
        assert again["status"] == "ok"
        assert robots_fetches(web) == 1                           # ... but robots.txt is not asked again
        assert again["robots"] == {"checked_on": "2026-10-26", "from_cache": True}     # and the date shown is the day it was really read
        assert "robots.txt cached, checked on 2026-10-26" in again["message"]
    assert stored_robots(tmp_path) == (1, "2026-10-26T12:00:00Z")      # a hit never rewrites the date


def test_a_cached_disallow_is_a_hit_too_and_blocks_without_asking_again(tmp_path):
    web, clock = FakeWeb(rss([article()]), robots="User-agent: *\nDisallow: /global/\n"), Clock()
    c = make(tmp_path, web, clock)
    assert scan(c)[1]["status"] == "blocked_by_robots" and robots_fetches(web) == 1
    clock.t = T0 + 5 * HOUR
    rep, entry = scan(c)
    assert entry["status"] == "blocked_by_robots" and robots_fetches(web) == 1 and feed_fetches(web) == 0
    assert entry["robots"] == {"checked_on": "2026-10-26", "from_cache": True}


# ------------------------------------------------------------------------------ expiry
def test_after_24_hours_robots_txt_is_read_again_and_the_new_date_is_stored(tmp_path):
    web, clock = FakeWeb(rss([article()])), Clock()
    c = make(tmp_path, web, clock)
    scan(c)
    clock.t = T0 + 25 * HOUR
    web.etag = '"v2"'
    _, entry = scan(c)
    assert robots_fetches(web) == 2 and entry["robots"] == {"checked_on": "2026-10-27", "from_cache": False}
    assert stored_robots(tmp_path) == (1, "2026-10-27T13:00:00Z")


def test_the_rules_changing_after_expiry_are_followed(tmp_path):
    web, clock = FakeWeb(rss([article()])), Clock()
    c = make(tmp_path, web, clock)
    assert scan(c)[1]["status"] == "ok"
    web.robots = "User-agent: *\nDisallow: /global/\n"
    clock.t = T0 + 30 * HOUR
    web.etag = '"v2"'
    before = feed_fetches(web)
    assert scan(c)[1]["status"] == "blocked_by_robots" and feed_fetches(web) == before
    assert stored_robots(tmp_path)[0] == 0


@pytest.mark.parametrize("age", [timedelta(hours=25), timedelta(days=3), timedelta(days=45), timedelta(days=400)])
def test_an_expired_success_is_used_when_robots_txt_cannot_be_read_and_it_says_which_day_it_is_from(tmp_path, age):
    web, clock = FakeWeb(rss([article()])), Clock()
    c = make(tmp_path, web, clock)
    scan(c)
    web.robots_status, web.etag = 503, '"v2"'
    clock.t = T0 + age
    _, entry = scan(c)
    assert entry["status"] == "ok" and feed_fetches(web) == 2
    assert entry["robots"] == {"checked_on": "2026-10-26", "from_cache": True}
    assert "robots.txt was unreachable" in entry["message"] and "used the copy checked on 2026-10-26" in entry["message"]
    assert robots_fetches(web) == 1 + 3                           # it did try (three tries) before falling back
    assert stored_robots(tmp_path) == (1, "2026-10-26T12:00:00Z")  # the failure does not overwrite the good record


def test_an_unreachable_robots_txt_never_overrides_a_cached_disallow(tmp_path):
    web, clock = FakeWeb(rss([article()]), robots="User-agent: *\nDisallow: /global/\n"), Clock()
    c = make(tmp_path, web, clock)
    scan(c)
    web.robots_status = 503
    clock.t = T0 + 3 * 24 * HOUR
    _, entry = scan(c)
    assert entry["status"] == "robots_unavailable" and feed_fetches(web) == 0


# ------------------------------------------------------------------------------ nothing cached
def test_with_no_cached_success_and_a_failure_the_scan_fails_closed(tmp_path):
    web, clock = FakeWeb(rss([article()]), robots_status=503), Clock()
    c = make(tmp_path, web, clock)
    rep, entry = scan(c)
    assert entry["status"] == "robots_unavailable" and feed_fetches(web) == 0 and robots_fetches(web) == 3
    assert rep["network"] == "unavailable" and rep["new_items"] == 0 and stored_robots(tmp_path) == (None, None)
    web.robots_status, clock.t = 200, T0 + HOUR                    # as soon as robots.txt answers, the scan goes ahead
    assert scan(c)[1]["status"] == "ok"


@pytest.mark.parametrize("failure", ["status_503", "timeout", "connection"])
def test_every_kind_of_failure_with_nothing_cached_fails_closed(tmp_path, failure):
    web = FakeWeb(rss([article()]))
    if failure == "status_503":
        web.robots_status = 503
    elif failure == "timeout":
        original = web.handler

        def handler(request: httpx.Request):
            if request.url.path == "/robots.txt":
                raise httpx.ReadTimeout("simulated")
            return original(request)
        web.handler = handler
    else:
        web.down = True
    _, entry = scan(make(tmp_path, web, Clock()))
    assert entry["status"] == "robots_unavailable" and feed_fetches(web) == 0


def test_a_404_for_robots_txt_means_no_rules_and_is_a_cacheable_success(tmp_path):
    web, clock = FakeWeb(rss([article()]), robots_status=404), Clock()
    c = make(tmp_path, web, clock)
    assert scan(c)[1]["status"] == "ok" and stored_robots(tmp_path) == (1, "2026-10-26T12:00:00Z")
    clock.t = T0 + 2 * HOUR
    web.etag = '"v2"'
    scan(c)
    assert robots_fetches(web) == 1
