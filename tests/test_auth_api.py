"""Accounts over HTTP: signup, sign-in, sign-out, sessions, CSRF, the limits and the demo shop. Offline, injected clock, made-up addresses."""

import hashlib
import sqlite3
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from auth import OWNER, PASSWORD, PROFILE, AuthedClient, sign_in
from test_launch_tracker import at
from test_news_provenance import Clock

WRONG = "Email or password is incorrect."


@pytest.fixture
def app(tmp_path):
    return create_app(tmp_path / "a.sqlite", as_of="2026-10-26", clock=Clock(at(2026, 10, 26)))


def fresh(app, **kw) -> tuple[TestClient, dict]:
    c = TestClient(app, **kw)
    return c, {"X-CSRF-Token": c.get("/api/session").json()["csrf_token"]}


def signup(c, token, email=OWNER, password=PASSWORD, **extra):
    return c.post("/api/signup", headers=token, json={"email": email, "password": password, **PROFILE, **extra})


def login(c, token, email=OWNER, password=PASSWORD):
    return c.post("/api/login", headers=token, json={"email": email, "password": password})


def rows(app, sql, *args):
    return sqlite3.connect(app.state.db_path).execute(sql, args).fetchall()


# ------------------------------------------------------------------------------ signup and the session cookie
def test_signup_signs_in_with_an_httponly_lax_cookie_and_stores_only_the_token_hash(app):
    c, token = fresh(app)
    r = signup(c, token)
    assert r.status_code == 201 and r.json()["signed_in"] and r.json()["profile"] == {"email": OWNER, **PROFILE}
    cookie = next(h for h in r.headers.get_list("set-cookie") if h.startswith("loreg_session="))
    value = cookie.split(";")[0].split("=", 1)[1]
    flags = cookie.lower()
    assert "httponly" in flags and "samesite=lax" in flags and "secure" not in flags and "max-age=604800" in flags and "path=/" in flags
    assert len(value) >= 43                                                              # 32 random bytes, base64
    stored = rows(app, "SELECT token_hash, csrf_token FROM sessions")
    assert stored == [(hashlib.sha256(value.encode()).hexdigest(), r.json()["csrf_token"])] and value not in str(stored)
    assert c.get("/api/profile").json()["org_name"] == PROFILE["org_name"]
    pw = rows(app, "SELECT password_hash FROM shops WHERE email = ?", OWNER)[0][0]
    assert pw.startswith("scrypt$16384$8$1$") and PASSWORD not in pw


def test_over_https_the_cookie_is_secure(app):
    c, token = fresh(app, base_url="https://testserver")
    r = signup(c, token)
    assert "secure" in next(h for h in r.headers.get_list("set-cookie") if h.startswith("loreg_session=")).lower()


def test_signup_input_is_checked_and_nothing_extra_is_accepted(app):
    c, token = fresh(app)
    assert signup(c, token, password="short").status_code == 422
    assert signup(c, token, org_name="<script>x</script>").status_code == 422
    r = signup(c, token, tax_id="12-3456789")
    assert r.status_code == 422 and "Extra inputs are not permitted" in r.text
    assert c.post("/api/signup", headers=token, content=b"not json", ).status_code == 422
    assert rows(app, "SELECT COUNT(*) FROM shops") == [(0,)]


def test_a_registered_email_is_409_whatever_its_case(app):
    c, token = fresh(app)
    assert signup(c, token).status_code == 201
    c2, token2 = fresh(app)
    r = signup(c2, token2, email=OWNER.upper())
    assert r.status_code == 409 and r.json()["detail"] == "That email is already registered. Sign in instead."


# ------------------------------------------------------------------------------ sign-in
def test_a_wrong_password_and_an_unknown_email_get_the_same_answer(app):
    c, token = fresh(app)
    signup(c, token)
    c2, token2 = fresh(app)
    a, b = login(c2, token2, password="wrong password here"), login(c2, token2, email="nobody@example.com")
    assert (a.status_code, a.json()) == (b.status_code, b.json()) == (401, {"detail": WRONG})
    r = login(c2, token2, email=" Owner@Example.com ")
    assert r.status_code == 200 and c2.get("/api/profile").json()["email"] == OWNER


def test_sign_in_backs_off_per_email_and_per_address_and_a_success_clears_the_email(app):
    c, token = fresh(app)
    signup(c, token)
    c2, token2 = fresh(app)
    for _ in range(5):
        assert login(c2, token2, password="wrong password here").status_code == 401
    r = login(c2, token2)                                                                  # even the right password waits now
    assert r.status_code == 429 and r.json()["detail"] == "Too many sign-in attempts. Try again later."
    app.state.clock.t += timedelta(seconds=31)
    assert login(c2, token2).status_code == 200
    app.state.clock.t += timedelta(minutes=15)                                            # the address's failures are forgotten too
    c3, token3 = fresh(app)
    for _ in range(5):                                                                     # an unknown email is throttled the same way
        assert login(c3, token3, email="nobody@example.com").status_code == 401
    assert login(c3, token3, email="nobody@example.com").status_code == 429
    c4, token4 = fresh(app)
    assert login(c4, token4).status_code == 429                                           # same address: it waits too, whatever the email


def test_signup_is_limited_per_address(app):
    for n in range(5):
        c, token = fresh(app)
        assert signup(c, token, email=f"shop{n}@example.com").status_code == 201
    c, token = fresh(app)
    r = signup(c, token, email="shop6@example.com")
    assert r.status_code == 429 and rows(app, "SELECT COUNT(*) FROM shops") == [(5,)]
    app.state.clock.t += timedelta(minutes=15)
    assert signup(c, token, email="shop6@example.com").status_code == 201


def test_a_new_sign_in_replaces_the_session_on_that_browser(app):
    c = AuthedClient(app)
    first = c.cookies.get("loreg_session")
    sign_in_again = c.post("/api/login", json={"email": OWNER, "password": PASSWORD})
    assert sign_in_again.status_code == 200 and c.cookies.get("loreg_session") != first
    assert rows(app, "SELECT COUNT(*) FROM sessions") == [(1,)]
    old = TestClient(app, cookies={"loreg_session": first})
    assert old.get("/api/profile").status_code == 401


# ------------------------------------------------------------------------------ sign-out and expiry
def test_sign_out_deletes_the_session_and_the_old_cookie_no_longer_works(app):
    c = AuthedClient(app)
    cookie = c.cookies.get("loreg_session")
    r = c.post("/api/logout")
    assert r.status_code == 200 and r.json()["signed_in"] is False and rows(app, "SELECT COUNT(*) FROM sessions") == [(0,)]
    assert TestClient(app, cookies={"loreg_session": cookie}).get("/api/summary").status_code == 401


def test_a_session_expires_after_the_configured_hours(app):
    c, token = fresh(app)
    signup(c, token)
    app.state.clock.t += timedelta(hours=167, minutes=59)
    assert c.get("/api/profile").status_code == 200
    app.state.clock.t += timedelta(minutes=2)
    assert c.get("/api/profile").status_code == 401 and c.get("/api/session").json()["signed_in"] is False


# ------------------------------------------------------------------------------ CSRF
def test_every_post_needs_the_session_s_own_token(app):
    c = AuthedClient(app)
    good = c.headers["X-CSRF-Token"]
    other = AuthedClient(app, email="other@example.com").headers["X-CSRF-Token"]
    for header in (None, "", "wrong", other):
        c.headers["X-CSRF-Token"] = header or ""
        if header is None:
            del c.headers["X-CSRF-Token"]
        assert c.post("/api/notifications/refresh").status_code == 403
        assert c.post("/api/ask", json={"question": "hello"}).status_code == 403             # the streamed chat too
    c.headers["X-CSRF-Token"] = good
    assert c.post("/api/notifications/refresh").status_code == 200 and c.get("/api/summary").status_code in (200, 404)


def test_sign_in_and_signup_need_the_signed_out_token_from_the_session_route(app):
    c = TestClient(app)
    assert c.post("/api/login", json={"email": OWNER, "password": PASSWORD}).status_code == 403
    c.get("/api/session")
    assert c.post("/api/login", json={"email": OWNER, "password": PASSWORD}, headers={"X-CSRF-Token": "guess"}).status_code == 403


# ------------------------------------------------------------------------------ the demo shop
def test_with_the_demo_switch_a_visitor_is_signed_in_to_the_demo_shop_which_has_no_password(app, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    c = TestClient(app)
    s = c.get("/api/session").json()
    assert s["signed_in"] and s["demo"] and s["profile"] == {"email": "demo@example.com", "org_name": "Demo shop (synthetic)",
                                                             "street": "100 Example Street", "city": "Springfield", "state": "ST",
                                                             "zip": "00000", "country": "US"}
    assert rows(app, "SELECT password_hash FROM shops WHERE email = 'demo@example.com'") == [(None,)]
    second = TestClient(app)                                                             # a second visitor: the same demo shop
    assert second.get("/api/session").json()["profile"]["email"] == "demo@example.com"
    assert rows(app, "SELECT COUNT(*) FROM shops") == [(1,)]
    monkeypatch.delenv("LOREG_DEMO")
    c3, token = fresh(app)
    assert login(c3, token, email="demo@example.com", password="anything at all").status_code == 401        # no password signs in to it


def test_a_shop_made_before_accounts_is_taken_over_as_the_demo_shop(tmp_path, monkeypatch):
    path = tmp_path / "legacy.sqlite"
    create_app(path)
    con = sqlite3.connect(path)
    con.execute("INSERT INTO shops (name, created_at) VALUES ('Sample shop', 'x')")
    con.commit()
    monkeypatch.setenv("LOREG_DEMO", "1")
    s = sign_in(TestClient(create_app(path)))
    assert s["profile"]["email"] == "demo@example.com" and rows(create_app(path), "SELECT id FROM shops") == [(1,)]
