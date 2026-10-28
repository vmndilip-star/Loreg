"""S1: the news domain allowlist, https only, redirect checks and size limits. Offline: a fake transport plays every server,
and every test also checks which hosts were (not) requested. All articles are SYNTHETIC."""

import httpx
import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.watch import feeds
from app.watch.allowlist import check_url, load_news_config
from test_watch import FakeWeb, article, rss

NEWS = load_news_config()
ALLOWED = NEWS.allowed_hosts
FEED = "https://news.samsung.com/global/feed"
GOOD = rss([article()])


class Web:
    """A fake internet: routes[(host, path)] = a Response, or a function of the request. Remembers every request."""

    def __init__(self, routes):
        self.routes, self.requests = routes, []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get((request.url.host, request.url.path))
        if route is None:
            return httpx.Response(404)
        return route(request) if callable(route) else route

    def client(self):
        return httpx.Client(transport=httpx.MockTransport(self.handler))

    def hosts(self):
        return {r.url.host for r in self.requests}

    def paths(self):
        return [(r.url.host, r.url.path) for r in self.requests]


def ok_feed():
    return httpx.Response(200, content=GOOD, headers={"content-type": "text/xml"})


ROBOTS = httpx.Response(200, text="User-agent: *\nAllow: /\n")


def fetch(web, url=FEED, **kw):
    return feeds.fetch_feed(web.client(), url, allowed=ALLOWED, **kw)


# ------------------------------------------------------------------------------ the address rule
@pytest.mark.parametrize("url", [FEED, "https://NEWS.SAMSUNG.COM/global/feed", "https://news.samsung.com./x", "https://news.samsung.com:443/x"])
def test_these_addresses_are_allowed(url):
    assert check_url(url, ALLOWED) == ""


@pytest.mark.parametrize("url", [
    "http://news.samsung.com/global/feed",                       # not https
    "https://news.samsung.com.evil.test/global/feed",             # a look-alike that starts the same
    "https://evilnews.samsung.com/x", "https://samsung.com/x", "https://www.news.samsung.com/x", "https://global.news.samsung.com/x",
    "https://evil-news.samsung.com.attacker.test/x",
    "https://news.samsung.com@evil.test/x", "https://user:pw@news.samsung.com/x",   # a user name hides the real host
    "https://news.samsung.com:8443/x", "https://127.0.0.1/x", "https://[::1]/x", "ftp://news.samsung.com/x", "file:///etc/passwd",
    "//news.samsung.com/x", "", "not a url", "https://", "https://news.samsung.com\\@evil.test/",
    "https://news.samsung.com%2eevil.test/x", "https://xn--news-samsung-x0a.com/x",
])
def test_these_addresses_are_refused(url):
    assert check_url(url, ALLOWED) != ""


def test_every_configured_feed_is_on_an_allowed_host_and_a_bad_config_is_refused(tmp_path):
    assert NEWS.sources and all(check_url(s["url"], ALLOWED) == "" for s in NEWS.sources)
    assert ALLOWED == frozenset({"news.samsung.com"})                   # only the Samsung newsroom, as decided
    bad = tmp_path / "bad.yaml"
    bad.write_text("allowed_hosts: [news.samsung.com]\nsources:\n  - {name: X, brand: X, url: 'https://news.samsung.com.evil.test/feed', kind: rss}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="not allowed"):
        load_news_config(bad)


# ------------------------------------------------------------------------------ redirects
def test_a_redirect_to_a_look_alike_host_is_refused_and_never_requested():
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS,
               ("news.samsung.com", "/global/feed"): httpx.Response(302, headers={"location": "https://news.samsung.com.evil.test/global/feed"}),
               ("news.samsung.com.evil.test", "/global/feed"): ok_feed()})
    r = fetch(web)
    assert r.status == "blocked_redirect" and "news.samsung.com.evil.test" in r.message and not r.entries
    assert web.hosts() == {"news.samsung.com"}                           # the look-alike was never contacted


@pytest.mark.parametrize("location", ["http://news.samsung.com/global/feed", "https://evil.test/feed", "https://user@news.samsung.com/x",
                                      "https://news.samsung.com:8443/x", "//evil.test/feed", "https://127.0.0.1/feed"])
def test_a_redirect_to_http_or_another_host_is_refused_unrequested(location):
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS,
               ("news.samsung.com", "/global/feed"): httpx.Response(301, headers={"location": location}),
               ("evil.test", "/feed"): ok_feed(), ("127.0.0.1", "/feed"): ok_feed()})
    r = fetch(web)
    assert r.status == "blocked_redirect"
    assert web.hosts() == {"news.samsung.com"} and ("news.samsung.com", "/global/feed") in web.paths()
    assert len([p for p in web.paths() if p == ("news.samsung.com", "/global/feed")]) == 1


def test_a_redirect_within_the_allowed_host_is_followed_up_to_the_limit():
    def hop(n):
        return httpx.Response(302, headers={"location": f"/global/feed{n}"})
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): hop(1), ("news.samsung.com", "/global/feed1"): hop(2),
               ("news.samsung.com", "/global/feed2"): hop(3), ("news.samsung.com", "/global/feed3"): ok_feed()})
    assert fetch(web).status == "ok"                                     # three hops are fine
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): hop(1), ("news.samsung.com", "/global/feed1"): hop(2),
               ("news.samsung.com", "/global/feed2"): hop(3), ("news.samsung.com", "/global/feed3"): hop(4), ("news.samsung.com", "/global/feed4"): ok_feed()})
    r = fetch(web)
    assert r.status == "blocked_redirect" and "redirects" in r.message and ("news.samsung.com", "/global/feed4") not in web.paths()


def test_a_redirect_loop_and_a_redirect_without_a_destination_end_cleanly():
    loop = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): httpx.Response(302, headers={"location": "/global/feed"})})
    assert fetch(loop).status == "blocked_redirect"
    nowhere = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): httpx.Response(302)})
    assert fetch(nowhere).status == "network_error"


def test_a_robots_txt_that_redirects_off_the_list_means_do_not_fetch():
    web = Web({("news.samsung.com", "/robots.txt"): httpx.Response(302, headers={"location": "https://evil.test/robots.txt"}),
               ("evil.test", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): ok_feed()})
    r = fetch(web)
    assert r.status == "robots_unavailable" and "refused" in r.message
    assert web.hosts() == {"news.samsung.com"} and ("news.samsung.com", "/global/feed") not in web.paths()


@pytest.mark.parametrize("url", ["http://news.samsung.com/global/feed", "https://news.samsung.com.evil.test/global/feed", "https://evil.test/feed"])
def test_a_feed_address_off_the_list_is_not_requested_at_all(url):
    web = Web({})
    r = fetch(web, url)
    assert r.status == "blocked_host" and web.requests == []              # not even robots.txt


# ------------------------------------------------------------------------------ size
def test_a_feed_larger_than_the_limit_is_refused_by_header_and_by_counting():
    big = httpx.Response(200, content=b"x" * 50, headers={"content-length": "3000000"})
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): big})
    assert fetch(web).status == "too_large"
    body = GOOD + b" " * 5000
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS, ("news.samsung.com", "/global/feed"): httpx.Response(200, content=body)})
    assert fetch(web, max_bytes=len(GOOD) + 100).status == "too_large"       # no header: counted as it arrives
    assert fetch(web, max_bytes=len(body) + 10).status == "ok"


# ------------------------------------------------------------------------------ what is stored
def test_entries_whose_links_are_off_the_list_are_dropped_and_counted(tmp_path, catalog_dir):
    items = [article(slug="ok1"),
             {**article(slug="x"), "link": "https://evil.test/global/synthetic-x"},
             {**article(slug="y"), "link": "http://news.samsung.com/global/synthetic-y"},
             {**article(slug="z"), "link": "https://news.samsung.com.evil.test/global/synthetic-z"}]
    web = FakeWeb(rss(items))
    client = TestClient(create_app(tmp_path / "a.sqlite", as_of="2026-10-26", http_factory=web.factory()))
    rep = client.post("/api/watch/scan").json()
    assert rep["sources"][0]["new"] == 1 and rep["sources"][0]["offsite_links"] == 3
    assert [i["url"] for i in client.get("/api/watch/items").json()["items"]] == ["https://news.samsung.com/global/synthetic-ok1"]


def test_a_source_that_is_not_on_the_list_is_never_fetched_even_if_passed_in(tmp_path):
    import sqlite3
    from app.llm.client import LLMClient
    from app.catalog import load_catalog
    from app.storage.db import connect, init_db
    from app.watch import scan as watch
    init_db(tmp_path / "s.sqlite")
    conn = connect(tmp_path / "s.sqlite")
    web = Web({})
    rep = watch.scan(conn, 1, http=web.client(), llm=LLMClient("mock"), catalog=load_catalog(),
                     sources=[{"name": "Evil", "brand": "Samsung", "url": "https://news.samsung.com.evil.test/feed", "kind": "rss"}])
    assert rep["sources"][0]["status"] == "blocked_host" and web.requests == []
    assert conn.execute("SELECT COUNT(*) FROM market_sources").fetchone()[0] == 0
    conn.close()


def test_the_scan_route_reports_a_refused_redirect_in_plain_words(tmp_path):
    web = Web({("news.samsung.com", "/robots.txt"): ROBOTS,
               ("news.samsung.com", "/global/feed"): httpx.Response(302, headers={"location": "https://news.samsung.com.evil.test/global/feed"})})
    client = TestClient(create_app(tmp_path / "r.sqlite", as_of="2026-10-26", http_factory=web.client))
    rep = client.post("/api/watch/scan").json()
    assert rep["sources"][0]["status"] == "blocked_redirect" and "was refused" in rep["message"] and "Showing the items already stored" in rep["message"]
    assert web.hosts() == {"news.samsung.com"}
