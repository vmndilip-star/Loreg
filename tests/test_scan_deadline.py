"""One news check has an overall deadline: no model call starts after it and a slow one is not waited for. Articles not read in time
stay unread and are read on the next check. Offline: SYNTHETIC articles, a fake feed and a fake, slow model."""

import dataclasses
import sqlite3
import time

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.storage.db import connect
from app.watch import scan as watch
from app.watch.common import NEWS
from app.watch.allowlist import load_news_config
from test_watch import BASE, FakeWeb, article, rss
from auth import AuthedClient

N = 5


class SlowModel:
    """Answers like the mock backend, after `delay` seconds."""
    is_mock = True

    def __init__(self, delay):
        self.delay, self.calls = delay, 0

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        self.calls += 1
        time.sleep(self.delay)
        return mock_output()


def setup(tmp_path, catalog_dir):
    web = FakeWeb(rss([article(slug=f"d{i}", html=f"{BASE} Story {i}.") for i in range(N)]))
    app = create_app(tmp_path / "d.sqlite", as_of="2026-10-26", http_factory=web.factory())
    c = AuthedClient(app)
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("upload_demo_200.csv", f, "text/csv")}).status_code == 200
    return app, web


def unread(tmp_path):
    return sqlite3.connect(tmp_path / "d.sqlite").execute("SELECT COUNT(*) FROM market_items WHERE extracted = 0").fetchone()[0]


def test_the_deadline_comes_from_the_config_file():
    assert load_news_config().scan_deadline_s == 60 == NEWS.scan_deadline_s


def test_a_slow_model_stops_the_check_at_the_deadline_and_the_rest_waits_for_the_next_check(tmp_path, catalog_dir):
    app, web = setup(tmp_path, catalog_dir)
    model = SlowModel(0.4)
    news = dataclasses.replace(NEWS, scan_deadline_s=1.0)
    conn = connect(tmp_path / "d.sqlite")
    started = time.monotonic()
    rep = watch.scan(conn, 1, http=web.factory()(), llm=model, catalog=app.state.catalog, news=news)
    took = time.monotonic() - started
    assert took < 1.0 + 0.5, f"the check took {took:.2f} s"                      # the deadline, plus a margin for the fetch and the writes
    read = rep["sources"][0]["read"]
    assert 1 <= read < N and rep["left_for_next_check"] == N - read
    assert unread(tmp_path) == N - read and f"{N - read} articles will be read on the next check" in rep["message"]
    time.sleep(0.5)                                                             # the call that was cut off finishes in its thread, unused
    again = watch.scan(conn, 1, http=web.factory()(), llm=SlowModel(0.0), catalog=app.state.catalog, news=NEWS)
    assert again["left_for_next_check"] == 0 and again["message"] == "scan complete" and unread(tmp_path) == 0
    conn.close()


def test_with_time_to_spare_everything_is_read_and_nothing_is_left(tmp_path, catalog_dir):
    app, web = setup(tmp_path, catalog_dir)
    conn = connect(tmp_path / "d.sqlite")
    rep = watch.scan(conn, 1, http=web.factory()(), llm=SlowModel(0.0), catalog=app.state.catalog)
    assert rep["sources"][0]["read"] == N and rep["left_for_next_check"] == 0 and rep["message"] == "scan complete"
    conn.close()
