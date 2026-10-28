"""The demo journey, end to end over the real HTTP API (no browser): upload -> dead stock -> daily brief -> alert ->
chat -> help -> plan -> accept -> capital at risk updates -> undo. The expected numbers come from an independent
recomputation of the CSV (tests/csv_truth.py), never from the app. Always runs; it is the gate for the browser test."""

import json

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.api.routes_demo import DEMO_FILE
from csv_truth import Truth
from test_watch import POISONS, FakeWeb, article, rss

ASK = "What should I clear before the launch?"
ALLOWED = {"discount", "bundle", "return", "hold"}


def sse(text: str) -> list[tuple[str, dict]]:
    out = []
    for frame in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in frame.split("\n") if ": " in line)
        out.append((lines["event"], json.loads(lines["data"])))
    return out


def dollars(cents: int) -> str:
    return f"${(cents + 50) // 100:,}"


@pytest.fixture
def truth(catalog_dir):
    return Truth(catalog_dir, master="upload_demo_200.csv")


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    monkeypatch.setenv("LOREG_DEMO", "1")
    return TestClient(create_app(tmp_path / "journey.sqlite", as_of="2026-10-26"))


def test_the_demo_journey_matches_the_csv_at_every_step(app_client, truth):
    c = app_client
    at_risk = truth.totals()["at_risk"]["cap_cents"]

    # 0. nothing yet: the API says so plainly
    assert c.get("/api/summary").status_code == 404

    # 1. upload (the demo file goes through the same path as a real upload)
    assert c.get("/api/demo").json() == {"enabled": True}
    up = c.post("/api/demo/load").json()
    assert up["kept"] == len(truth.items) and up["blocked"] == up["rows"] - up["kept"] == 3

    # 2. dead stock
    s = c.get("/api/summary").json()
    t = truth.totals()
    assert s["totals"]["dead"] == {"n": t["dead"]["n"], "cap_cents": t["dead"]["cap_cents"]}
    assert s["totals"]["slow"] == {"n": t["slow"]["n"], "cap_cents": t["slow"]["cap_cents"]}
    assert s["money_at_risk_cents"] == at_risk == s["at_risk_all_cents"]
    top = c.get("/api/inventory/top?limit=5").json()["items"]
    assert [(i["sku"], i["cap_cents"]) for i in top] == [(i["sku"], i["cap"]) for i in truth.top(5)]

    # 3. daily brief, from the same numbers
    brief = c.post("/api/daily/run").json()
    assert dollars(at_risk) in brief["headline"] and c.get("/api/daily").json()["id"] == brief["id"]

    # 4. alert first: the alert, the question, and no plan, no option, no discount
    group = c.get("/api/launch/exposed").json()["groups"][0]
    exposed = truth.launch_exposed()
    assert group["n"] == len(exposed) and group["cap_cents"] == sum(i["cap"] for i in exposed)
    assert group["help_requested"] is False and group["source"] == {"attached": False, "status": "no dated source attached"}
    assert c.get(f"/api/alerts/{group['alert_id']}").json()["recommendations"] == {}
    assert c.post(f"/api/alerts/{group['alert_id']}/plan").status_code == 409

    # 5. chat before help: status first, an alert card plus item cards, the question, and no plan card
    r = c.post("/api/ask", json={"question": ASK})
    events = sse(r.text)
    assert events[0][0] == "status" and events[-1][0] == "done" and "error" not in [e for e, _ in events]
    cards = [card for e, d in events if e == "cards" for card in d["cards"]]
    assert [x["type"] for x in cards] == ["alert", "item", "item", "item"]
    assert [x["sku"] for x in cards[1:]] == [i["sku"] for i in exposed[:3]]
    reply = "".join(d["text"] for e, d in events if e == "token")
    assert reply.endswith("Want me to plan what to do with them?")
    assert events[-1][1]["help_requested"] is False and events[-1][1]["money_at_risk_cents"] == at_risk

    # 6. help, then the plan: one allowed option per item, tied-up money equal to the CSV's
    aid = group["alert_id"]
    assert c.post(f"/api/alerts/{aid}/help").json()["help_requested"] is True
    plan = c.post(f"/api/alerts/{aid}/plan").json()["recommendations"]
    by_sku = {i["sku"]: i for i in truth.items}
    assert [p["sku"] for p in plan] == [i["sku"] for i in exposed[:3]]
    assert all(p["option"] in ALLOWED and p["tied_cents"] == by_sku[p["sku"]]["cap"] and p["status"] == "proposed" for p in plan)
    events = sse(c.post("/api/ask", json={"question": ASK}).text)
    assert [x["type"] for e, d in events if e == "cards" for x in d["cards"]] == ["plan", "plan", "plan"]

    # 7. accept: capital at risk drops by exactly that item's money, as the server reports it
    first, second = plan[0], plan[1]
    assert c.post(f"/api/actions/{first['id']}/accept").status_code == 200
    assert c.get("/api/summary").json()["money_at_risk_cents"] == at_risk - by_sku[first["sku"]]["cap"]

    # 8. decline changes nothing; undo restores the starting figure
    assert c.post(f"/api/actions/{second['id']}/decline").status_code == 200
    assert c.get("/api/summary").json()["money_at_risk_cents"] == at_risk - by_sku[first["sku"]]["cap"]
    assert c.post(f"/api/actions/{first['id']}/undo").status_code == 200
    assert c.get("/api/summary").json()["money_at_risk_cents"] == at_risk


def test_a_poisoned_article_changes_no_number_and_is_shown_as_data(tmp_path, monkeypatch, truth):
    monkeypatch.setenv("LOREG_DEMO", "1")
    poison = dict(POISONS)["ignore previous instructions"]
    web = FakeWeb(rss([article(title="SYNTHETIC <img src=x onerror=alert(1)> Samsung QN90D Neo QLED refresh", html=poison)]))
    c = TestClient(create_app(tmp_path / "p.sqlite", as_of="2026-10-26", http_factory=web.factory()))
    c.post("/api/demo/load")
    before = c.get("/api/summary").json()
    scan = c.post("/api/watch/scan").json()
    assert scan["network"] == "ok"
    item = c.get("/api/watch/items").json()["items"][0]
    assert "<" not in item["title"] and ">" not in item["title"]                       # markup is stripped from the title
    assert item["flagged"] is True and all(not e["attachable"] for e in item["events"])
    assert c.get("/api/summary").json() == before                                     # no number moved
    assert c.get("/api/launch/exposed").json()["groups"][0]["source"]["attached"] is False
