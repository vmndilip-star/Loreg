"""The demo account (BigDeal): made by a script with a hashed password, offered to the sign-in page only in demo mode, and the only
account that can reset its own data. Offline, mock model, injected clock, temporary databases. Every other address is made up."""

import os
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import demo_seed
from app.api.main import create_app
from app.api.routes_demo import NOT_THE_DEMO
from app.core.accounts import LIMITS
from app.storage.db import TABLES
from auth import PROFILE, AuthedClient, seed_demo, sign_in_demo
from test_isolation import GLOBAL_TABLES
from test_launch_tracker import at
from test_news_provenance import Clock

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def demo_app(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    for key in ("LOREG_DEMO_EMAIL", "LOREG_DEMO_PASSWORD", "LOREG_ENV"):
        monkeypatch.delenv(key, raising=False)
    return create_app(tmp_path / "d.sqlite", as_of="2026-10-26", clock=Clock(at(2026, 10, 26)))


seed = seed_demo


def demo_client(app) -> TestClient:
    c = TestClient(app)
    sign_in_demo(c)
    return c


def rows(app, sql, *args):
    return sqlite3.connect(app.state.db_path).execute(sql, args).fetchall()


# ------------------------------------------------------------------------------ the seed
def test_the_seed_makes_one_hashed_bigdeal_account_and_a_second_run_changes_nothing(demo_app):
    first = seed(demo_app)
    before = rows(demo_app, "SELECT * FROM shops")
    assert seed(demo_app) == first and rows(demo_app, "SELECT * FROM shops") == before
    [(email, stored, org)] = rows(demo_app, "SELECT email, password_hash, org_name FROM shops")
    assert (email, org) == (demo_seed.DEFAULT_EMAIL, "BigDeal")
    assert stored.startswith("scrypt$") and demo_seed.DEFAULT_PASSWORD not in stored


def test_the_short_demo_password_is_the_scripts_exception_and_signup_still_refuses_it(demo_app):
    assert len(demo_seed.DEFAULT_PASSWORD) < LIMITS["password"][0]                  # shorter than signup allows, on purpose
    anon = TestClient(demo_app)
    token = {"X-CSRF-Token": anon.get("/api/session").json()["csrf_token"]}
    r = anon.post("/api/signup", json={"email": "short@example.com", "password": demo_seed.DEFAULT_PASSWORD, **PROFILE}, headers=token)
    assert r.status_code == 422 and rows(demo_app, "SELECT COUNT(*) FROM shops") == [(0,)]
    seed(demo_app)
    c = demo_client(demo_app)
    assert c.get("/api/profile").json()["org_name"] == "BigDeal"


def test_an_account_already_using_the_demo_email_is_never_taken_over(demo_app):
    AuthedClient(demo_app, email=demo_seed.DEFAULT_EMAIL)                          # someone signed up with that address first
    before = rows(demo_app, "SELECT * FROM shops")
    with pytest.raises(ValueError, match="not the demo account"):
        seed(demo_app)
    assert rows(demo_app, "SELECT * FROM shops") == before
    c = TestClient(demo_app)
    token = {"X-CSRF-Token": c.get("/api/session").json()["csrf_token"]}
    assert c.post("/api/login", json=c.get("/api/demo").json(), headers=token).status_code == 401


def test_the_credential_can_be_set_in_the_environment(demo_app, monkeypatch, tmp_path):
    monkeypatch.setenv("LOREG_DEMO_EMAIL", "  Shop.Demo@Example.com ")
    monkeypatch.setenv("LOREG_DEMO_PASSWORD", "another demo password")
    seed(demo_app)
    assert TestClient(demo_app).get("/api/demo").json() == {"email": "shop.demo@example.com", "password": "another demo password"}
    assert demo_client(demo_app).get("/api/profile").json()["email"] == "shop.demo@example.com"
    monkeypatch.setenv("LOREG_DEMO_EMAIL", "not an email")
    with pytest.raises(ValueError):
        create_app(tmp_path / "bad.sqlite")                                         # a bad setting stops the app at start


def test_no_demo_account_is_made_in_production(demo_app, monkeypatch, tmp_path):
    monkeypatch.setenv("LOREG_ENV", "production")
    with pytest.raises(ValueError, match="production"):
        seed(demo_app)
    assert rows(demo_app, "SELECT COUNT(*) FROM shops") == [(0,)]
    db = tmp_path / "prod.sqlite"
    db.write_bytes(b"not touched")
    env = {**os.environ, "LOREG_ENV": "production", "LOREG_DB": str(db), "LOREG_LOG_LEVEL": "WARNING"}
    r = subprocess.run([sys.executable, str(ROOT / "scripts" / "reset_demo.py"), "--yes"], capture_output=True, text=True, env=env, cwd=ROOT,
                       timeout=120)
    assert r.returncode == 2 and "production" in r.stdout and db.read_bytes() == b"not touched"


def test_only_the_seed_script_makes_the_demo_account():
    callers = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for folder in ("app", "scripts") for p in (ROOT / folder).rglob("*.py")
                     if "create_demo_account(" in p.read_text(encoding="utf-8"))
    assert callers == ["app/demo_seed.py", "scripts/_session.py"]                   # the definition, and the scripts' sign-in helper


def test_the_default_demo_password_is_written_only_in_the_seed_module_and_the_readme():
    files = [p for folder in ("app", "web", "scripts", "tests", "docs", "config", "data/demo", "evals") for p in (ROOT / folder).rglob("*")
             if p.is_file() and "__pycache__" not in p.parts and p.suffix in {".py", ".js", ".mjs", ".html", ".md", ".yaml", ".json", ".csv", ".txt"}]
    files += [p for p in ROOT.iterdir() if p.is_file() and p.name != ".env"]       # .env is local and never committed
    found = sorted(str(p.relative_to(ROOT)).replace("\\", "/") for p in files
                   if demo_seed.DEFAULT_PASSWORD in p.read_text(encoding="utf-8", errors="ignore"))
    assert found == ["README.md", "app/demo_seed.py"]


# ------------------------------------------------------------------------------ the credential route and the session
def test_the_demo_route_is_404_with_demo_mode_off_and_only_the_credential_with_it_on(demo_app, monkeypatch):
    seed(demo_app)
    anon = TestClient(demo_app)
    r = anon.get("/api/demo")
    assert r.json() == {"email": demo_seed.DEFAULT_EMAIL, "password": demo_seed.DEFAULT_PASSWORD} and "set-cookie" not in r.headers
    assert anon.get("/api/session").json()["signed_in"] is False and anon.get("/api/summary").status_code == 401   # it signs nobody in
    monkeypatch.delenv("LOREG_DEMO")
    assert anon.get("/api/demo").status_code == 404
    assert AuthedClient(demo_app, email="someone@example.com").get("/api/demo").status_code == 404


def test_the_session_marks_only_the_demo_account_as_demo(demo_app, monkeypatch):
    seed(demo_app)
    demo, other = demo_client(demo_app), AuthedClient(demo_app, email="other@example.com")
    assert demo.get("/api/session").json()["demo"] is True and other.get("/api/session").json()["demo"] is False
    monkeypatch.delenv("LOREG_DEMO")
    assert demo.get("/api/session").json()["demo"] is False


# ------------------------------------------------------------------------------ reset
def state(c) -> dict:
    """Everything the demo account's screens show, without ids (they are never reused) or times."""
    s = c.get("/api/summary").json()
    s.pop("upload_id")
    groups = [{k: v for k, v in g.items() if k != "alert_id"} for g in c.get("/api/launch/exposed").json()["groups"]]
    c.post("/api/notifications/refresh")                                             # what opening the app does
    notes = c.get("/api/notifications").json()
    return {"summary": s, "groups": groups, "daily": c.get("/api/daily").status_code, "unread": notes["unread"],
            "notes": sorted(n["text"] for n in notes["items"]), "agent_calls": c.get("/api/quality").json()["calls"]}


def test_reset_puts_the_demo_account_back_as_the_seed_left_it(demo_app):
    seed(demo_app)
    c = demo_client(demo_app)
    assert c.post("/api/demo/load").status_code == 200
    seeded = state(c)
    alert_id = c.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    c.post(f"/api/alerts/{alert_id}/help")
    recs = c.post(f"/api/alerts/{alert_id}/plan").json()["recommendations"]
    assert c.post(f"/api/actions/{recs[0]['id']}/accept").status_code == 200
    c.post("/api/daily/run")
    c.post("/api/notifications/read-all")
    assert state(c) != seeded
    history = rows(demo_app, "SELECT COUNT(*) FROM launch_history")

    r = c.post("/api/demo/reset")
    assert r.status_code == 200 and r.json()["reset"] is True and r.json()["deleted"]["recommendations"] == len(recs)
    assert state(c) == seeded
    assert c.get(f"/api/alerts/{alert_id}").status_code == 404                      # the old alert is gone, not reused
    new = c.get("/api/launch/exposed").json()["groups"][0]
    assert new["help_requested"] is False and c.get(f"/api/alerts/{new['alert_id']}").json()["recommendations"] == {}
    assert c.get("/api/profile").json()["org_name"] == "BigDeal" and c.get("/api/session").json()["signed_in"] is True
    assert rows(demo_app, "SELECT COUNT(*) FROM launch_history") == history                # append-only: kept
    assert rows(demo_app, "SELECT action FROM audit_log WHERE action = 'demo_reset'") == [("demo_reset",)]


def test_reset_is_refused_for_every_other_account_and_404_with_demo_mode_off(demo_app, monkeypatch):
    seed(demo_app)
    demo, other = demo_client(demo_app), AuthedClient(demo_app, email="other@example.com")
    demo.post("/api/demo/load")
    other.post("/api/demo/load")
    other.post("/api/daily/run")
    mine = {"summary": other.get("/api/summary").json(), "daily": other.get("/api/daily").json()}
    r = other.post("/api/demo/reset")
    assert r.status_code == 403 and r.json()["detail"] == NOT_THE_DEMO
    assert demo.post("/api/demo/reset").status_code == 200
    assert {"summary": other.get("/api/summary").json(), "daily": other.get("/api/daily").json()} == mine   # another shop is never touched
    assert TestClient(demo_app).post("/api/demo/reset").status_code == 401
    monkeypatch.delenv("LOREG_DEMO")
    assert demo.post("/api/demo/reset").status_code == 404


def test_a_reset_clears_every_table_of_the_shop_or_says_why_it_keeps_it():
    assert all(re.fullmatch(r"DELETE FROM \w+ WHERE shop_id = \?", sql) for sql in demo_seed.CLEAR)
    cleared = {sql.split()[2] for sql in demo_seed.CLEAR}
    assert not cleared & set(demo_seed.KEPT)
    assert cleared | set(demo_seed.KEPT) == set(TABLES) - set(GLOBAL_TABLES)
