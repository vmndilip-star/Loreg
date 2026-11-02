"""Accepting a plan moves an item's capital from "at risk" to "action in progress"; nothing is called freed. Offline.

The pure split lives in app/core/exposure.py; the API carries it on every decision, drops the shop's cached agent answers and marks a
brief written before the decision as stale.
"""

import sqlite3
from pathlib import Path

import pytest

from app.api.main import create_app
from app.core.exposure import Exposure, compute_exposure
from auth import AuthedClient
from conftest import OfflineModel

AS_OF = "2026-10-26"
ITEMS = [("A", 1000), ("B", 250), ("C", 4)]


# ---- the pure function -------------------------------------------------------------

def test_nothing_accepted_means_everything_is_at_risk():
    assert compute_exposure(ITEMS, []) == Exposure(1254, 3, 0, 0)


def test_accepting_moves_exactly_that_items_capital():
    e = compute_exposure(ITEMS, ["B"])
    assert (e.at_risk_cents, e.at_risk_n, e.action_in_progress_cents, e.action_in_progress_n) == (1004, 2, 250, 1)


@pytest.mark.parametrize("accepted", [[], ["A"], ["A", "C"], ["A", "B", "C"], ["B", "B"]])
def test_the_total_never_changes(accepted):
    e = compute_exposure(ITEMS, accepted)
    assert e.total_cents == 1254 and e.total_n == 3


def test_empty_input():
    e = compute_exposure([], ["A"])
    assert e == Exposure(0, 0, 0, 0) and e.total_cents == 0


def test_an_accepted_sku_that_is_no_longer_dead_or_slow_counts_nowhere():
    assert compute_exposure(ITEMS, ["Z"]) == compute_exposure(ITEMS, [])


def test_as_dict_carries_both_buckets_and_the_total():
    assert compute_exposure(ITEMS, ["A"]).as_dict() == {"at_risk_cents": 254, "at_risk_n": 2, "action_in_progress_cents": 1000,
                                                       "action_in_progress_n": 1, "total_cents": 1254, "total_n": 3}


# ---- through the API -----------------------------------------------------------------

@pytest.fixture
def shop(catalog_dir, tmp_path):
    app = create_app(db_path=tmp_path / "t.sqlite", as_of=AS_OF, llm=OfflineModel())
    client = AuthedClient(app)
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert client.post("/api/upload", files={"file": ("upload_demo_200.csv", f, "text/csv")}).status_code == 200
    a = client.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    client.post(f"/api/alerts/{a}/help")
    recs = client.post(f"/api/alerts/{a}/plan").json()["recommendations"]
    return client, recs, tmp_path / "t.sqlite"


def shop_id_of(db: Path, email: str = "owner@example.com") -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute("SELECT id FROM shops WHERE email = ?", (email,)).fetchone()[0]


def test_accept_moves_the_capital_between_buckets_and_keeps_the_total(shop):
    client, recs, _ = shop
    before = client.get("/api/summary").json()["exposure"]
    r = client.post(f"/api/actions/{recs[0]['id']}/accept").json()
    after = r["exposure"]
    assert after["at_risk_cents"] == before["at_risk_cents"] - recs[0]["tied_cents"]
    assert after["action_in_progress_cents"] == before["action_in_progress_cents"] + recs[0]["tied_cents"]
    assert after["total_cents"] == before["total_cents"]
    assert client.get("/api/summary").json()["exposure"] == after                      # the screens read the same figures


def test_double_accept_is_idempotent_and_undo_restores_the_buckets(shop):
    client, recs, _ = shop
    before = client.get("/api/summary").json()["exposure"]
    once = client.post(f"/api/actions/{recs[0]['id']}/accept").json()["exposure"]
    assert client.post(f"/api/actions/{recs[0]['id']}/accept").json()["exposure"] == once
    assert client.post(f"/api/actions/{recs[0]['id']}/undo").json()["exposure"] == before


def test_declining_leaves_both_buckets_alone(shop):
    client, recs, _ = shop
    before = client.get("/api/summary").json()["exposure"]
    assert client.post(f"/api/actions/{recs[0]['id']}/decline").json()["exposure"] == before


def test_unknown_or_another_shops_plan_is_404_and_changes_nothing(shop):
    client, recs, _ = shop
    before = client.get("/api/summary").json()["exposure"]
    assert client.post("/api/actions/999999/accept").status_code == 404
    other = AuthedClient(client.app, email="second@example.com")
    assert other.post(f"/api/actions/{recs[0]['id']}/accept").status_code == 404    # 404, not 403: another shop's ids are not revealed
    assert client.get("/api/summary").json()["exposure"] == before


def test_accept_drops_the_shops_cached_agent_answers(shop):
    client, recs, db = shop
    cache, me = client.app.state.cache, shop_id_of(db)
    assert client.post("/api/daily/run").status_code == 200
    assert cache.count_shop(me) > 0
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    assert cache.count_shop(me) == 0


def test_another_shops_decision_leaves_this_shops_cache_alone(shop, catalog_dir):
    client, _, db = shop
    other = AuthedClient(client.app, email="second@example.com")
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        other.post("/api/upload", files={"file": ("upload_demo_200.csv", f, "text/csv")})
    a = other.get("/api/launch/exposed").json()["groups"][0]["alert_id"]
    other.post(f"/api/alerts/{a}/help")
    their = other.post(f"/api/alerts/{a}/plan").json()["recommendations"]
    client.post("/api/daily/run")
    mine = client.app.state.cache.count_shop(shop_id_of(db))
    other.post(f"/api/actions/{their[0]['id']}/accept")
    assert client.app.state.cache.count_shop(shop_id_of(db)) == mine > 0


def test_a_brief_written_before_a_decision_is_marked_stale_until_the_numbers_match_again(shop):
    client, recs, _ = shop
    assert client.post("/api/daily/run").json()["stale"] is False
    assert client.get("/api/daily").json()["stale"] is False
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    assert client.get("/api/daily").json()["stale"] is True
    client.post(f"/api/actions/{recs[0]['id']}/undo")
    assert client.get("/api/daily").json()["stale"] is False
    client.post(f"/api/actions/{recs[0]['id']}/accept")
    fresh = client.post("/api/daily/run").json()
    assert fresh["stale"] is False and client.get("/api/daily").json()["stale"] is False


def test_the_page_never_says_accepted_money_is_freed():
    text = (Path(__file__).resolve().parent.parent / "web" / "js" / "views" / "watch.js").read_text(encoding="utf-8")
    assert "no longer at risk" not in text and "action in progress" in text
