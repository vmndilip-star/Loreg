"""One shop never sees or changes another shop's data, through any route.

Shop A signs up, uploads its stock, reads news, asks for help, gets a plan, decides, writes a brief and gets notifications. Shop B signs up
with nothing. The test walks every route the app has (from its OpenAPI schema; a route missing from the table below fails the test):
signed out, every protected route is 401 and every POST without its CSRF token is 403; signed in as B, every route that takes an id is
404 for A's real ids, and every other route, the streamed chat included, answers without any of A's names, SKUs, ids or amounts.
Offline: a fake feed with one SYNTHETIC article, the mock model, an injected clock. Every address is made up."""

import json
import re
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.storage.db import TABLES, init_db
from auth import AuthedClient, sign_in
from test_launch_tracker import LAUNCH, at
from test_news_provenance import Clock
from test_watch import FakeWeb, rss

PROFILE_A = {"org_name": "Shop A Example", "street": "1 Alpha Example Road", "city": "Aville", "state": "AA", "zip": "11111", "country": "US"}
PROFILE_B = {"org_name": "Shop B Example", "street": "2 Beta Example Road", "city": "Bville", "state": "BB", "zip": "22222", "country": "US"}

PUBLIC = "public"
OWN = "own"          # works on the signed-in shop's own rows, takes no id
BY_ID = "by_id"      # takes the id of one shop's row: another shop's id is 404

ROUTES = {
    ("get", "/api/health"): PUBLIC, ("get", "/api/session"): PUBLIC, ("post", "/api/signup"): PUBLIC, ("post", "/api/login"): PUBLIC,
    ("post", "/api/logout"): PUBLIC,
    ("get", "/api/profile"): OWN, ("get", "/api/summary"): OWN, ("get", "/api/inventory/top"): OWN, ("get", "/api/launch/exposed"): OWN,
    ("get", "/api/needs-look"): OWN, ("get", "/api/categories"): OWN, ("get", "/api/products"): OWN, ("get", "/api/search"): OWN,
    ("get", "/api/daily"): OWN, ("post", "/api/daily/run"): OWN, ("get", "/api/launch/tracker"): OWN, ("get", "/api/notifications"): OWN,
    ("post", "/api/notifications/refresh"): OWN, ("post", "/api/notifications/read-all"): OWN, ("get", "/api/watch/items"): OWN,
    ("post", "/api/watch/scan"): OWN, ("post", "/api/upload"): OWN, ("get", "/api/demo"): OWN, ("post", "/api/demo/load"): OWN,
    ("post", "/api/ask"): OWN,
    ("get", "/api/alerts/{alert_id}"): BY_ID, ("post", "/api/alerts/{alert_id}/help"): BY_ID, ("post", "/api/alerts/{alert_id}/plan"): BY_ID,
    ("post", "/api/actions/{rec_id}/accept"): BY_ID, ("post", "/api/actions/{rec_id}/decline"): BY_ID, ("post", "/api/actions/{rec_id}/undo"): BY_ID,
    ("post", "/api/notifications/{notification_id}/read"): BY_ID, ("get", "/api/uploads/{upload_id}/issues"): BY_ID,
}


@pytest.fixture(scope="module")
def world(tmp_path_factory, catalog_dir):
    path = tmp_path_factory.mktemp("isolation") / "i.sqlite"
    web = FakeWeb(rss([LAUNCH]))
    app = create_app(path, as_of="2026-10-26", http_factory=web.factory(), clock=Clock(at(2026, 10, 26)))
    a = AuthedClient(app, email="a@example.com", profile=PROFILE_A)
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        upload = a.post("/api/upload", files={"file": ("a.csv", f, "text/csv")}).json()
    assert a.post("/api/watch/scan").json()["network"] == "ok"
    alert_id = a.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    a.post(f"/api/alerts/{alert_id}/help")
    recs = a.post(f"/api/alerts/{alert_id}/plan").json()["recommendations"]
    assert a.post(f"/api/actions/{recs[0]['id']}/accept").status_code == 200
    a.post("/api/daily/run")
    a.post("/api/notifications/refresh")
    notes = a.get("/api/notifications").json()["items"]
    assert notes, "shop A needs a notification for this test"
    skus = [i["sku"] for i in a.get("/api/inventory/top", params={"limit": 50}).json()["items"]]
    ids = {"alert_id": alert_id, "rec_id": recs[1]["id"], "notification_id": notes[0]["id"], "upload_id": upload["upload_id"]}
    b = AuthedClient(app, email="b@example.com", profile=PROFILE_B)
    return {"app": app, "a": a, "b": b, "ids": ids, "skus": skus, "accepted": recs[0]["id"], "path": path,
            "markers": ["a@example.com", *PROFILE_A.values()][:-1] + skus}


def snapshot(world) -> dict:
    """Everything shop A can read about itself, to show that nothing B does changes it."""
    a = world["a"]
    return {"summary": a.get("/api/summary").json(), "alert": a.get(f"/api/alerts/{world['ids']['alert_id']}").json(),
            "notes": a.get("/api/notifications").json(), "profile": a.get("/api/profile").json(), "daily": a.get("/api/daily").json()}


def fill(path: str, ids: dict) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(ids[m.group(1)]), path)


def call(client, method, path, ids, **kw):
    url = fill(path, ids)
    if path == "/api/ask":
        kw.setdefault("json", {"question": "What should I clear before the launch?"})
    if path in ("/api/signup", "/api/login"):
        kw.setdefault("json", {})
    return getattr(client, method)(url, **kw)


def test_the_table_covers_every_route_the_app_has(world):
    paths = world["app"].openapi()["paths"]
    have = {(m, p) for p, ops in paths.items() for m in ops}
    assert have == set(ROUTES), {"not in the table": sorted(have - set(ROUTES)), "gone from the app": sorted(set(ROUTES) - have)}


def test_signed_out_every_protected_route_is_401_and_every_public_post_needs_its_token(world):
    anon = TestClient(world["app"])
    token = anon.get("/api/session").json()["csrf_token"]
    for (method, path), kind in ROUTES.items():
        if kind == PUBLIC:
            continue
        assert call(anon, method, path, world["ids"]).status_code == 401, (method, path)
        if method == "post":                                                            # a signed-out CSRF token does not get one in either
            assert call(anon, method, path, world["ids"], headers={"X-CSRF-Token": token}).status_code == 401, path
    for path in ("/api/signup", "/api/login", "/api/logout"):
        assert TestClient(world["app"]).post(path, json={}).status_code == 403, path    # no token: refused before anything is read


def test_a_signed_in_post_without_or_with_a_wrong_csrf_token_is_403(world):
    b = world["b"]
    for header in ({}, {"X-CSRF-Token": ""}, {"X-CSRF-Token": "not-the-token"}, {"X-CSRF-Token": world["a"].headers["X-CSRF-Token"]}):
        for (method, path), kind in ROUTES.items():
            if method == "post" and kind != PUBLIC:
                r = TestClient(world["app"], cookies=b.cookies).post(fill(path, world["ids"]), headers=header, json={"question": "hi"})
                assert r.status_code == 403, (path, header, r.status_code)


def test_shop_b_gets_404_for_every_one_of_shop_a_s_ids_and_changes_nothing(world):
    before = snapshot(world)
    for (method, path), kind in ROUTES.items():
        if kind == BY_ID:
            r = call(world["b"], method, path, world["ids"])
            assert r.status_code == 404, (method, path, r.status_code, r.text)
            assert not any(m in r.text for m in world["markers"]), (path, r.text)
    assert world["b"].post(f"/api/actions/{world['accepted']}/undo").status_code == 404
    assert snapshot(world) == before


def test_every_other_route_answers_shop_b_without_any_of_shop_a_s_data(world):
    before = snapshot(world)
    b, sku = world["b"], world["skus"][0]
    seen = []
    for (method, path), kind in ROUTES.items():
        if kind != OWN or path in ("/api/upload", "/api/demo/load"):
            continue
        r = call(b, method, path, world["ids"])
        assert r.status_code in (200, 404, 409, 422), (method, path, r.status_code, r.text)
        seen.append((path, r.text))
    seen.append(("search", b.get("/api/search", params={"q": sku}).text))
    seen.append(("products", b.get("/api/products", params={"q": sku}).text))
    asked = b.post("/api/ask", json={"question": f"Why is {sku} stuck?"}).text
    assert f"I can't find an item with SKU {sku} in your stock." in asked                 # B's own words echoed back, nothing of A's
    seen.append(("ask sku", asked.replace(sku, "the SKU B typed")))
    assert b.post("/api/ask", json={"question": "anything", "sku": sku}).status_code == 404
    for path, text in seen:
        hits = [m for m in world["markers"] if m in text]
        assert not hits, (path, hits)
    assert b.get("/api/summary").status_code == 404 and b.get("/api/launch/tracker").json()["watches"] == []
    assert b.get("/api/notifications").json() == {"unread": 0, "items": []}
    assert snapshot(world) == before                                                    # B's scan, brief and read-all left A as it was


def test_each_shop_reads_only_its_own_profile(world):
    a, b = world["a"].get("/api/profile").json(), world["b"].get("/api/profile").json()
    assert a == {"email": "a@example.com", **PROFILE_A} and b == {"email": "b@example.com", **PROFILE_B}
    assert "password" not in json.dumps(a) and "scrypt" not in json.dumps(a)


def test_the_streamed_chat_for_shop_b_names_none_of_shop_a_s_items(world):
    body = world["b"].post("/api/ask", json={"question": "What should I clear before the launch?"}).text
    assert body.startswith("event: status") and not any(m in body for m in world["markers"])


GLOBAL_TABLES = {"shops": "the accounts themselves", "market_sources": "shared news", "market_items": "shared news", "market_events": "shared news"}


def test_every_table_that_holds_one_shop_s_rows_has_a_shop_id(tmp_path):
    init_db(tmp_path / "t.sqlite")
    con = sqlite3.connect(tmp_path / "t.sqlite")
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}
    assert names == set(TABLES)
    missing = [t for t in names - set(GLOBAL_TABLES) if "shop_id" not in {r[1] for r in con.execute(f"PRAGMA table_info({t})")}]
    assert missing == []


def test_a_second_signup_with_the_same_email_in_any_case_is_409_and_signs_nobody_in(world):
    c = TestClient(world["app"])
    token = c.get("/api/session").json()["csrf_token"]
    r = c.post("/api/signup", headers={"X-CSRF-Token": token},
               json={"email": "A@Example.com", "password": "another long password", **PROFILE_B})
    assert r.status_code == 409 and r.json()["detail"] == "That email is already registered. Sign in instead."
    assert c.get("/api/profile").status_code == 401
    assert sign_in(TestClient(world["app"]), "a@example.com", "test password, not a real one")["profile"]["org_name"] == "Shop A Example"
