"""The fallback-rate metric: every agent call is stored with its outcome and latency, /api/quality reports it with the backend, and the
offline test writer is never counted or shown as a model. Offline: the mock backend, or small fake models."""

import json
import sqlite3
from pathlib import Path

import pytest

from app.api.main import create_app
from app.schemas.outputs import InventoryExplanation
from auth import AuthedClient
from conftest import OfflineModel

ASK = "What should I clear before the launch?"
REPO = Path(__file__).resolve().parent.parent


class Model:
    """A non-mock model. mode: "good" answers with the deterministic output; "bad" gives every inventory answer a made-up number;
    "bad_once" does so only on the first inventory call of each item, so the retry passes."""

    is_mock = False
    backend = "meta"

    def __init__(self, mode="good"):
        self.mode, self.seen = mode, set()

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        out = mock_output()
        if schema is InventoryExplanation and self.mode != "good":
            first = (out.sku not in self.seen)
            self.seen.add(out.sku)
            if self.mode == "bad" or first:
                return InventoryExplanation(sku=out.sku, summary="No sale in 99999 days.", citations=out.citations)
        return out


def shop(catalog_dir, tmp_path, llm):
    c = AuthedClient(create_app(db_path=tmp_path / "t.sqlite", as_of="2026-10-26", llm=llm))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    return c


def events(client, question=ASK):
    r = client.post("/api/ask?stream=false", json={"question": question})
    return r.json()["events"]


def rows(client):
    con = sqlite3.connect(client.app.state.db_path)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute("SELECT * FROM agent_calls ORDER BY id")]
    finally:
        con.close()


def test_every_agent_call_of_a_run_is_stored_once_with_its_outcome(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, OfflineModel())
    done = events(c)[-1]["data"]
    stored = rows(c)
    assert [(r["agent"], r["outcome"]) for r in stored] == [(a["agent"], a["outcome"]) for a in done["agents"]]
    assert len({r["run_id"] for r in stored}) == 1 and {r["pipeline"] for r in stored} == {"launch_clear"}
    assert {r["outcome"] for r in stored} == {"mock"} and {r["backend"] for r in stored} == {"mock"}


def test_quality_on_mock_reports_no_fallback_rate_and_says_mock(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, OfflineModel())
    events(c)
    q = c.get("/api/quality").json()
    assert q["backend"] == "mock" and q["is_mock"] and "no model" in q["backend_label"]
    assert q["mock"] == q["calls"] > 0 and q["fallback_rate"] is None and q["model_ok"] == 0


def test_a_model_whose_answers_fail_is_counted_as_fallback(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, Model("bad"))
    events(c)
    q = c.get("/api/quality").json()
    assert q["backend"] == "meta" and not q["is_mock"]
    assert q["per_agent"]["inventory"]["fallback"] == q["per_agent"]["inventory"]["calls"] > 0
    assert q["per_agent"]["inventory"]["fallback_rate"] == 1.0 and q["per_agent"]["router"]["model_ok"] == 1


def test_a_second_answer_that_passes_is_counted_as_retried_ok(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, Model("bad_once"))
    events(c)
    inv = c.get("/api/quality").json()["per_agent"]["inventory"]
    assert inv["retried_ok"] == inv["calls"] > 0 and inv["fallback"] == 0 and inv["fallback_rate"] == 0.0


def test_plan_items_count_the_rules_based_options(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, Model("good"))
    a = c.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    c.post(f"/api/alerts/{a}/help")
    recs = c.post(f"/api/alerts/{a}/plan").json()["recommendations"]
    q = c.get("/api/quality").json()
    assert q["plan_items"]["items"] == len(recs) and q["plan_items"]["rules_based"] == sum(r["source"] in ("rules", "fallback") for r in recs)
    assert q["per_agent"]["recommender"]["calls"] == len(recs)


def test_quality_is_per_shop(catalog_dir, tmp_path):
    c = shop(catalog_dir, tmp_path, OfflineModel())
    events(c)
    other = AuthedClient(c.app, email="second@example.com")
    assert other.get("/api/quality").json()["calls"] == 0


def test_the_page_shows_the_backend_and_never_shows_mock_as_a_model():
    app_js = (REPO / "web" / "js" / "app.js").read_text(encoding="utf-8")
    assert "Backend: ${q.backend_label}" in app_js and "rules-based fallback" in app_js and "q.is_mock ? 'chip-amber'" in app_js
    assert 'data-testid="backend-badge"' in (REPO / "web" / "index.html").read_text(encoding="utf-8")


def test_the_eval_script_runs_on_mock(tmp_path):
    import os
    import subprocess
    import sys
    out = tmp_path / "eval.json"
    env = {**os.environ, "LLM_BACKEND": "mock"}
    env.pop("META_API_KEY", None)
    r = subprocess.run([sys.executable, str(REPO / "scripts" / "eval_fallback_rate.py"), "--backend", "mock", "--items", "2", "--json", str(out)],
                       capture_output=True, text=True, env=env, cwd=REPO, timeout=300)
    assert r.returncode == 0, r.stdout + r.stderr
    summary = json.loads(out.read_text(encoding="utf-8"))["summary"]
    assert summary["ALL"]["calls"] > 0 and summary["ALL"]["fallback_rate"] is None and set(summary) >= {"router", "inventory", "recommender", "daily"}


def test_a_news_check_records_its_market_agent_calls(catalog_dir, tmp_path):
    from test_watch import FakeWeb, article, make_client, rss
    c = make_client(tmp_path, FakeWeb(rss([article(slug="q1")])))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")})
    report = c.post("/api/watch/scan").json()
    stored = [r for r in rows(c) if r["pipeline"] == "watch_scan"]
    assert stored and {r["agent"] for r in stored} == {"market"} and report["agent_outcomes"] == [r["outcome"] for r in stored]
