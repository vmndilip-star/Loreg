"""Fail closed: the page files hold no shop's data, no /api answer may be kept by a cache, the session cookie's Secure flag can be
forced (LOREG_COOKIE_SECURE), and LOREG_ENV=production refuses demo mode and turns the API docs off. Offline, made-up addresses."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import WEB_DIR, create_app
from auth import PROFILE, AuthedClient

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def plain_env(monkeypatch):
    for key in ("LOREG_ENV", "LOREG_DEMO", "LOREG_COOKIE_SECURE"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def shop(tmp_path):
    """A shop with its stock loaded, and the strings that would give its data away."""
    app = create_app(tmp_path / "f.sqlite", as_of="2026-10-26")
    c = AuthedClient(app)
    with open(ROOT / "data" / "demo" / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    summary = c.get("/api/summary").json()
    skus = [i["sku"] for i in c.get("/api/inventory/top", params={"limit": 5}).json()["items"]]
    dollars = f"{summary['money_at_risk_cents'] / 100:,.2f}"
    return app, c, [PROFILE["org_name"], PROFILE["street"], dollars, *skus]


def cookie(r, name="loreg_session") -> str:
    return next(h for h in r.headers.get_list("set-cookie") if h.startswith(name + "=")).lower()


def signup_through_a_proxy(c, email):
    """Sign up as a browser behind a proxy that ends https would: the browser keeps a Secure cookie and sends it on, and the proxy hands
    it to the app over plain http. A client talking plain http itself never sends a Secure cookie back (and is refused: fail closed)."""
    s = c.get("/api/session")
    token = s.json()["csrf_token"]
    c.cookies.set("loreg_csrf", token)
    r = c.post("/api/signup", json={"email": email, "password": "a long test password", **PROFILE}, headers={"X-CSRF-Token": token})
    assert r.status_code == 201, r.text
    return s, r


def test_no_page_file_holds_any_shop_data_signed_out_or_in(shop):
    app, signed_in, markers = shop
    anon = TestClient(app)
    files = ["/", *("/" + p.relative_to(WEB_DIR).as_posix() for p in WEB_DIR.rglob("*") if p.is_file())]
    assert len(files) > 10
    for path in files:
        a, b = anon.get(path), signed_in.get(path)
        assert a.status_code == 200 and a.content == b.content, path                 # the same bytes for everyone
        assert not [m for m in markers if m in a.text], (path, [m for m in markers if m in a.text])


@pytest.mark.parametrize("path", ["/api/session", "/api/summary", "/api/profile", "/api/products", "/api/notifications", "/api/quality",
                                  "/api/health"])
def test_every_api_answer_is_marked_no_store_signed_in_and_signed_out(shop, path):
    app, signed_in, _ = shop
    for c in (signed_in, TestClient(app)):
        r = c.get(path)
        assert r.headers["cache-control"] == "no-store", (path, r.status_code, r.headers.get("cache-control"))


def test_the_chat_stream_keeps_its_own_cache_header(shop):
    _, c, _ = shop
    assert c.post("/api/ask", json={"question": "hi"}).headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("setting, https, secure", [(None, False, False), (None, True, True), ("1", False, True), ("0", True, False)])
def test_the_session_cookie_is_secure_over_https_or_when_forced(tmp_path, monkeypatch, setting, https, secure):
    if setting is not None:
        monkeypatch.setenv("LOREG_COOKIE_SECURE", setting)
    app = create_app(tmp_path / "c.sqlite")
    c = TestClient(app, base_url="https://testserver" if https else "http://testserver")
    signed_out, r = signup_through_a_proxy(c, "c@example.com")
    assert ("; secure" in cookie(signed_out, "loreg_csrf")) is secure and ("; secure" in cookie(r)) is secure
    if https or not secure:                                                          # over http a Secure cookie is never sent back
        out = c.post("/api/logout", headers={"X-CSRF-Token": r.json()["csrf_token"]})
        assert out.status_code == 200 and ("; secure" in cookie(out)) is secure      # the clearing cookie matches


def test_a_secure_cookie_over_plain_http_signs_nobody_in(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_COOKIE_SECURE", "1")
    c = TestClient(create_app(tmp_path / "h.sqlite"))
    token = {"X-CSRF-Token": c.get("/api/session").json()["csrf_token"]}
    r = c.post("/api/signup", json={"email": "h@example.com", "password": "a long test password", **PROFILE}, headers=token)
    assert r.status_code == 403                                                      # the CSRF cookie never came back over http


def test_a_bad_cookie_setting_stops_the_app_at_start(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_COOKIE_SECURE", "yes")
    with pytest.raises(ValueError, match="LOREG_COOKIE_SECURE"):
        create_app(tmp_path / "bad.sqlite")


def test_production_refuses_demo_mode_and_turns_the_api_docs_off(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_ENV", "production")
    monkeypatch.setenv("LOREG_DEMO", "1")
    with pytest.raises(RuntimeError, match="production refuses LOREG_DEMO=1"):
        create_app(tmp_path / "p.sqlite")
    monkeypatch.delenv("LOREG_DEMO")
    app = create_app(tmp_path / "p.sqlite")
    anon = TestClient(app)
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon.get(path).status_code == 404, path
    assert anon.get("/api/demo").status_code == 404
    c = AuthedClient(app, base_url="https://testserver")                            # signing up and in still work (over https)
    assert c.get("/api/profile").json()["org_name"] == PROFILE["org_name"]


def test_under_production_the_cookie_is_secure_even_over_plain_http_unless_set_to_0(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_ENV", "production")
    for setting, secure in ((None, True), ("0", False)):
        if setting is None:
            monkeypatch.delenv("LOREG_COOKIE_SECURE", raising=False)
        else:
            monkeypatch.setenv("LOREG_COOKIE_SECURE", setting)
        _, r = signup_through_a_proxy(TestClient(create_app(tmp_path / f"s{setting}.sqlite")), "p@example.com")
        assert ("; secure" in cookie(r)) is secure
