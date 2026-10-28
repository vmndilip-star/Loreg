"""One source of truth for plans: the chat and the Watch page show the same action per item, and nothing the rules forbid is ever stored.

Layer 1 is the Recommender's own check; layer 2 is plan_alert, which asks the rules again before storing an answer. Each is tested alone."""

import json
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import pipelines
from app.agents import recommender
from app.agents.base import AgentResult
from app.api.main import create_app
from app.core import actions
from app.core.thresholds import load_thresholds
from app.facts import item_facts
from app.schemas.outputs import Recommendation
from app.storage import repo, repo_ai
from app.storage.db import connect, default_shop_id
from conftest import OfflineModel
from auth import AuthedClient

AS_OF = "2026-10-26"
ASK = "What should I clear before the launch?"
MAP_SKU = "TV-SAM-0032"          # the demo's MAP-blocked item
TH = load_thresholds()


def make(tmp_path, llm, name="p.sqlite"):
    return AuthedClient(create_app(tmp_path / name, as_of=AS_OF, llm=llm))


def load(client, catalog_dir):
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert client.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    group = client.get("/api/launch/exposed").json()["groups"][0]
    client.post(f"/api/alerts/{group['alert_id']}/help")
    return group["alert_id"]


def chat_plan(client):
    body = client.post("/api/ask", json={"question": ASK}).text
    cards = []
    for frame in body.strip().split("\n\n"):
        lines = dict(x.split(": ", 1) for x in frame.split("\n") if ": " in x)
        if lines["event"] == "cards":
            cards += json.loads(lines["data"])["cards"]
    return {c["sku"]: c for c in cards if c["type"] == "plan"}


def watch_plan(client, alert_id, limit=3):
    return {r["sku"]: r for r in client.post(f"/api/alerts/{alert_id}/plan", params={"limit": limit}).json()["recommendations"]}


def same(a, b):
    assert a.keys() == b.keys() and a
    for sku in a:
        assert (a[sku]["option"], a[sku]["discount_pct"], a[sku]["reasons"], a[sku]["source"], a[sku]["id"]) == \
               (b[sku]["option"], b[sku]["discount_pct"], b[sku]["reasons"], b[sku]["source"], b[sku]["id"]), sku


class HoldModel:
    """A model that always answers 'hold' (always allowed), with the offline answer's reasons and citations."""
    is_mock = False

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        out = mock_output()
        return out.model_copy(update={"option": "hold", "discount_pct": 0}) if isinstance(out, Recommendation) else out


class GreedyModel:
    """A model that proposes a 10% discount for every item, whatever the rules say."""
    is_mock = False

    def complete_json(self, *, system, user, schema, mock_output=None, max_completion_tokens=0):
        out = mock_output()
        return out.model_copy(update={"option": "discount", "discount_pct": 10}) if isinstance(out, Recommendation) else out


# ------------------------------------------------------------------------------ one plan, two views
@pytest.mark.parametrize("llm", [OfflineModel, HoldModel], ids=["offline", "model-answers-hold"])
@pytest.mark.parametrize("first", ["chat", "watch"])
def test_the_chat_and_the_watch_page_show_the_same_action_for_every_item(catalog_dir, tmp_path, llm, first):
    c = make(tmp_path, llm())
    aid = load(c, catalog_dir)
    if first == "chat":
        chat = chat_plan(c)
        watch = watch_plan(c, aid)
    else:
        watch = watch_plan(c, aid)
        chat = chat_plan(c)
    same(chat, watch)
    assert len(chat) == 3
    again = watch_plan(c, aid)                              # and asking again changes nothing
    same(again, watch)
    if llm is HoldModel:
        assert {r["option"] for r in watch.values()} == {"hold"} and {r["source"] for r in watch.values()} == {"model"}


def test_every_demo_item_agrees_not_only_the_top_three(catalog_dir, tmp_path):
    c = make(tmp_path, OfflineModel())
    aid = load(c, catalog_dir)
    everything = watch_plan(c, aid, limit=10)
    assert len(everything) == 7                              # all seven dead or slow launch-family TVs in the demo
    same({k: v for k, v in everything.items() if k in chat_plan(c)}, chat_plan(c))
    con = connect(tmp_path / "p.sqlite")
    sid = default_shop_id(con, "x")
    for sku, rec in everything.items():                      # offline, the stored action is the rules' own first choice
        opts = repo.item_options(repo_ai.item_row(con, sid, sku), date.fromisoformat(AS_OF), TH)
        assert (rec["option"], rec["discount_pct"]) == (lambda o, p: (o, p))(*actions.default_option(opts)), sku
    con.close()


def test_a_restart_does_not_change_a_stored_plan_even_if_the_model_would_now_answer_differently(catalog_dir, tmp_path):
    c = make(tmp_path, HoldModel())
    aid = load(c, catalog_dir)
    before = watch_plan(c, aid)
    restarted = make(tmp_path, GreedyModel())                # same database, empty cache, a model with other ideas
    after = watch_plan(restarted, aid)
    same(before, after)
    same(before, chat_plan(restarted))


def test_a_decided_plan_is_never_rewritten(catalog_dir, tmp_path):
    c = make(tmp_path, OfflineModel())
    aid = load(c, catalog_dir)
    plan = watch_plan(c, aid)
    rec = next(iter(plan.values()))
    c.post(f"/api/actions/{rec['id']}/accept")
    c.app.state.llm = HoldModel()
    c.app.state.cache = pipelines.ResultCache()
    now = watch_plan(c, aid)
    assert now[rec["sku"]]["status"] == "accepted" and now[rec["sku"]]["option"] == rec["option"]


# ------------------------------------------------------------------------------ the rules have the last word
def test_a_model_that_discounts_the_map_blocked_item_is_overruled(catalog_dir, tmp_path):
    c = make(tmp_path, GreedyModel())
    aid = load(c, catalog_dir)
    plan = watch_plan(c, aid)
    assert plan[MAP_SKU]["option"] != "discount"                                    # blocked by the minimum advertised price
    assert plan[MAP_SKU]["source"] == "fallback" and "minimum advertised price" in plan[MAP_SKU]["reasons"][0]
    for sku, rec in plan.items():                                                   # nothing stored breaks any rule
        con = connect(tmp_path / "p.sqlite")
        opts = repo.item_options(repo_ai.item_row(con, default_shop_id(con, "x"), sku), date.fromisoformat(AS_OF), TH)
        con.close()
        assert rec["option"] in opts.options() and (rec["option"] != "discount" or rec["discount_pct"] in opts.discounts_pct)
    same(plan, chat_plan(c))


def test_layer_two_stops_a_bad_answer_even_if_the_agents_own_checks_are_bypassed(catalog_dir, tmp_path, monkeypatch):
    """The Recommender's checks are skipped here on purpose: this is the second, independent check in plan_alert."""
    def lawless(client, sku, facts, *, help_requested):
        return AgentResult(Recommendation(sku=sku, option="discount", discount_pct=10, reasons=["Cut the price."], citations=[]), "model")
    monkeypatch.setattr(recommender, "recommend", lawless)
    c = make(tmp_path, OfflineModel())
    aid = load(c, catalog_dir)
    plan = watch_plan(c, aid)
    assert plan[MAP_SKU]["option"] == "bundle" and plan[MAP_SKU]["discount_pct"] == 0 and plan[MAP_SKU]["source"] == "fallback"
    assert plan["TV-SAM-0060"]["option"] == "return"                                # cost-blocked, window open
    con = sqlite3.connect(tmp_path / "p.sqlite")
    refused = {json.loads(r[0])["sku"]: json.loads(r[0]) for r in con.execute("SELECT detail_json FROM audit_log WHERE action = 'agent_answer_rejected'")}
    con.close()
    assert MAP_SKU in refused and "not allowed" in refused[MAP_SKU]["why"] and refused[MAP_SKU]["option"] == "discount"
    assert plan["TV-SAM-0040"]["option"] == "discount"                              # a discount the rules do allow is accepted
    same(plan, chat_plan(c))


# ------------------------------------------------------------------------------ the fallback is the rules
def test_the_recommenders_fallback_is_the_rules_default_for_every_at_risk_item(catalog_dir, tmp_path):
    c = AuthedClient(create_app(tmp_path / "all.sqlite", as_of=AS_OF))
    with open(catalog_dir / "skus_master.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("m.csv", f, "text/csv")}).status_code == 200
    con = connect(tmp_path / "all.sqlite")
    rows = con.execute("SELECT s.*, p.brand, p.model, p.category, p.line_label, p.launch_family, p.variant FROM skus s JOIN products p ON p.id = s.product_id"
                       " WHERE s.qty > 0 AND s.status IN ('dead','slow')").fetchall()
    assert len(rows) > 500
    as_of = date.fromisoformat(AS_OF)
    seen = set()
    for r in rows:
        opts = repo.item_options(r, as_of, TH)
        want = actions.default_option(opts)
        got = recommender._fallback(r["sku"], item_facts(r, TH, as_of))
        assert (got.option, got.discount_pct) == want, r["sku"]
        seen.add(want[0])
    con.close()
    assert {"discount", "bundle"} <= seen
