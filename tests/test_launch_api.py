"""Launch tracker and notification endpoints. GET routes write nothing; notifications come from app open and "Check for news", once each.
Offline, SYNTHETIC articles, injected clock."""

from test_launch_tracker import LAUNCH, MOVED, ON_SALE, Shop, at
from auth import AuthedClient


def counts(s):
    return {t: len(s.rows(t)) for t in ("launch_watch", "launch_history", "notifications")}


def test_get_routes_write_nothing_even_when_the_day_comes(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    before = counts(s)
    for day in (at(2026, 11, 20), at(2026, 12, 30)):
        s.clock.t = day
        body = s.c.get("/api/launch/tracker").json()
        assert body["today"] == day.date().isoformat() and body["grace_days"] == 7
        s.c.get("/api/notifications")
    assert counts(s) == before
    assert [w["state"] for w in body["watches"] if "QN90D" in w["line"]] == ["unverified"]


def test_signed_out_the_tracker_is_refused_and_a_new_shop_has_no_watches_and_no_notifications(tmp_path):
    import sqlite3
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    app = create_app(tmp_path / "e.sqlite", as_of="2026-10-26")
    assert TestClient(app).get("/api/launch/tracker").status_code == 401 and TestClient(app).get("/api/notifications").status_code == 401
    assert sqlite3.connect(tmp_path / "e.sqlite").execute("SELECT COUNT(*) FROM shops").fetchone()[0] == 0     # reading created nothing
    c = AuthedClient(app)
    assert c.get("/api/launch/tracker").json()["watches"] == [] and c.get("/api/notifications").json() == {"unread": 0, "items": []}


def test_app_open_and_check_for_news_add_each_notification_once(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    rep = s.scan(LAUNCH)
    assert rep["tracker"]["notifications"] >= 1
    first = s.c.get("/api/notifications").json()
    qn = [n for n in first["items"] if "QN90D" in n["text"]]
    assert [n["state"] for n in qn] == ["announced"] and qn[0]["text"].endswith("Launch date as written: November 20, 2026.")
    assert s.c.post("/api/notifications/refresh").json()["notifications"] == 0                    # app opened again, nothing new
    s.clock.t = at(2026, 11, 20)
    assert s.c.post("/api/notifications/refresh").json()["notifications"] >= 1                    # the day came: one "due"
    assert s.c.post("/api/notifications/refresh").json()["notifications"] == 0
    s.clock.t = at(2026, 11, 22)
    s.scan(LAUNCH, MOVED, ON_SALE)
    states = [n["state"] for n in s.c.get("/api/notifications").json()["items"] if "QN90D" in n["text"]]
    assert sorted(states) == sorted(["announced", "due", "date_changed"])                         # Dec 4 is ahead: no confirmation yet
    s.clock.t = at(2026, 12, 20)
    s.c.post("/api/notifications/refresh")
    after = {n["state"]: n["text"] for n in s.c.get("/api/notifications").json()["items"] if "QN90D" in n["text"]}
    assert "unverified" in after and after["unverified"].endswith("This does not mean it was delayed.")


def test_confirmed_notification_wording(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    s.clock.t = at(2026, 11, 22)
    s.scan(LAUNCH, ON_SALE)
    texts = [n["text"] for n in s.c.get("/api/notifications").json()["items"] if n["state"] == "launch_confirmed"]
    assert len(texts) == 1 and texts[0].endswith("launch confirmed by a source dated 2026-11-21.")


def test_read_and_unread(tmp_path, catalog_dir):
    s = Shop(tmp_path, catalog_dir)
    s.scan(LAUNCH)
    s.clock.t = at(2026, 11, 20)
    s.c.post("/api/notifications/refresh")
    body = s.c.get("/api/notifications").json()
    assert body["unread"] == len(body["items"]) >= 2 and not any(n["read"] for n in body["items"])
    one = body["items"][0]["id"]
    assert s.c.post(f"/api/notifications/{one}/read").json()["unread"] == body["unread"] - 1
    assert s.c.post(f"/api/notifications/{one}/read").json()["unread"] == body["unread"] - 1        # reading twice is the same
    assert s.c.post("/api/notifications/999999/read").status_code == 404
    assert s.c.post("/api/notifications/read-all").json() == {"unread": 0}
    after = s.c.get("/api/notifications").json()
    assert after["unread"] == 0 and all(n["read"] for n in after["items"])
    assert s.c.post("/api/notifications/refresh").json()["unread"] == 0                           # nothing came back as unread
