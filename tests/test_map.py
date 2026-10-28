"""Minimum advertised price (MAP): an optional upload column, one rule, plain-language reasons.

Assumption (ours, not from any source document): a discount step is allowed only while the new price stays at or above
BOTH the unit cost and the MAP. A blank MAP means no minimum. Old files and old databases keep working."""

import csv
import io
import sqlite3
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.agents import recommender
from app.api.main import create_app
from app.core import actions
from app.core.thresholds import load_thresholds
from app.facts import item_facts
from app.storage.db import SCHEMA_VERSION, init_db

TH = load_thresholds()
AS_OF = date(2026, 10, 26)


def options(sell, cost=100_000, map_price=None, dsls=30, window=None):
    return actions.allowed_options(qty=5, unit_cost=cost, sell_price=sell, dsls=dsls, units_sold_90d=2, category="tv", seasonal=False,
                                   return_window_end=window, as_of=AS_OF, th=TH, map_price=map_price)


def test_without_a_map_nothing_changes():
    assert options(125_000).discounts_pct == (5, 10, 15, 20) and not options(125_000).map_blocked
    assert options(125_000, map_price=None) == options(125_000)


def test_a_map_removes_the_steps_that_would_go_below_it():
    o = options(125_000, map_price=115_000)                  # 5% -> 118,750 ok; 10% -> 112,500 below MAP
    assert o.discounts_pct == (5,) and not o.map_blocked


def test_a_map_that_blocks_every_step_says_so_and_keeps_the_other_options():
    o = options(125_000, map_price=124_000, dsls=100)        # even 5% (118,750) is below MAP
    assert o.discounts_pct == () and o.map_blocked is True
    assert "minimum advertised price" in o.notes[0]
    assert o.bundle_allowed and "hold" in o.options() and "discount" not in o.options()


def test_a_map_below_cost_changes_nothing_because_cost_is_the_higher_floor():
    assert options(125_000, map_price=80_000) == options(125_000)


def test_cost_blocked_items_are_not_reported_as_map_blocked():
    o = options(102_000, map_price=101_000)                  # every step is below cost anyway
    assert o.discounts_pct == () and o.map_blocked is False and "below cost" in o.notes[0]


def test_the_dead_stock_below_cost_exception_never_goes_below_the_map():
    dead = 2 * TH.categories["tv"].dead_dsls                 # long enough for the below-cost exception
    free = options(102_000, dsls=dead)
    assert free.below_cost and free.discounts_pct
    held = options(102_000, dsls=dead, map_price=101_000)    # 5% -> 96,900 is below MAP: nothing is allowed
    assert held.discounts_pct == () and held.map_blocked is True


def test_a_discount_below_the_map_is_never_in_the_recommenders_allowed_set():
    row = dict(sku="TV-SAM-0032", category="tv", unit_cost_cents=66_450, sell_price_cents=83_062, qty=6, dsls=83, units_sold_90d=1,
               is_seasonal=0, status="slow", capital_cents=398_700, return_window_end=None, map_cents=82_231)
    facts = item_facts(row, TH, AS_OF)
    by_key = {f.key: f for f in facts}
    assert by_key["map_price"].value == "$822" and by_key["discount_blocked_by_map"].number == 1.0
    assert by_key["max_discount_pct"].value == "none"
    assert "discount" not in recommender.allowed_options(facts) and recommender.allowed_discounts(facts) == []
    assert "map_price" not in {f.key for f in item_facts({**row, "map_cents": None}, TH, AS_OF)}      # no MAP, no extra facts


# ------------------------------------------------------------------------------ upload and database
def stock_csv(map_values):
    header = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price", "last_sale_date", "received_date",
              "units_sold_30d", "units_sold_90d", "msrp", "is_seasonal", "supplier", "return_window_end"]
    with_map = map_values is not None
    out = io.StringIO()
    w = csv.writer(out, lineterminator="\n")
    w.writerow(header + (["map_price"] if with_map else []))
    for n, m in enumerate(map_values or ["x"], start=1):
        row = [f"TV-SAM-90{n:02d}", "Samsung", "QN90D Neo QLED 43 inch", "tv", 6, "664.50", "830.62", "2026-08-04", "2025-12-01",
               0, 1, "999.99", 0, "Samsung Direct", ""]
        w.writerow(row + ([m] if with_map else []))
    return out.getvalue().encode()


@pytest.fixture
def client(tmp_path):
    return TestClient(create_app(tmp_path / "m.sqlite", as_of="2026-10-26")), tmp_path / "m.sqlite"


def stored(path):
    con = sqlite3.connect(path)
    rows = {r[0]: r[1:] for r in con.execute("SELECT sku, map_cents, needs_look, look_reason FROM skus")}
    con.close()
    return rows


def upload(c, raw):
    r = c.post("/api/upload", files={"file": ("s.csv", raw, "text/csv")})
    assert r.status_code == 200, r.text
    return r.json()


def test_map_price_is_read_stored_in_cents_and_blank_means_none(client):
    c, path = client
    upload(c, stock_csv(["822.31", "$1,049.99", ""]))
    assert [v[0] for v in stored(path).values()] == [82_231, 104_999, None]


def test_an_upload_without_the_map_column_still_works(client):
    c, path = client
    assert upload(c, stock_csv(None))["kept"] == 1 and list(stored(path).values())[0][0] is None


@pytest.mark.parametrize("bad", ["abc", "-5", "0", "n/a"])
def test_an_unreadable_map_is_ignored_and_the_row_is_flagged_not_blocked(client, bad):
    c, path = client
    result = upload(c, stock_csv([bad]))
    (map_cents, needs_look, reason), = stored(path).values()
    assert result["kept"] == 1 and result["blocked"] == 0 and map_cents is None
    assert needs_look == 1 and "UNREADABLE_MAP" in reason and result["look"] == 1


def test_a_version_2_database_is_migrated_and_keeps_its_rows(tmp_path):
    path = tmp_path / "old.sqlite"
    c = TestClient(create_app(path, as_of="2026-10-26"))
    upload(c, stock_csv(["822.31"]))
    con = sqlite3.connect(path)
    con.execute("ALTER TABLE skus DROP COLUMN map_cents")       # what a version 2 database looked like
    con.execute("PRAGMA user_version = 2")
    con.commit()
    con.close()
    init_db(path)
    con = sqlite3.connect(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION == 3
    assert con.execute("SELECT sku, map_cents, qty FROM skus").fetchall() == [("TV-SAM-9001", None, 6)]   # data kept, MAP unknown
    con.close()
    init_db(path)                                               # running it again changes nothing


def test_an_empty_database_starts_at_the_current_version(tmp_path):
    init_db(tmp_path / "new.sqlite")
    con = sqlite3.connect(tmp_path / "new.sqlite")
    assert con.execute("PRAGMA user_version").fetchone()[0] == 3
    assert "map_cents" in [r[1] for r in con.execute("PRAGMA table_info(skus)")]
    con.close()


def test_the_plan_for_a_map_blocked_item_says_why_in_plain_words(client):
    c, _ = client
    raw = stock_csv(["822.31"]).decode().replace("2026-08-04", "2026-06-01")      # slow enough to be at risk, launch-family line
    upload(c, raw.encode())
    group = c.get("/api/launch/exposed").json()["groups"]
    if not group:
        pytest.skip("the fixture row is not launch-family at risk in this catalog")
    c.post(f"/api/alerts/{group[0]['alert_id']}/help")
    plan = c.post(f"/api/alerts/{group[0]['alert_id']}/plan").json()["recommendations"][0]
    assert plan["option"] != "discount" and "minimum advertised price of $822" in plan["reasons"][0]


def test_the_fallback_recommendation_says_why_no_discount_is_offered():
    row = dict(sku="TV-SAM-0032", category="tv", unit_cost_cents=66_450, sell_price_cents=83_062, qty=6, dsls=83, units_sold_90d=1,
               is_seasonal=0, status="slow", capital_cents=398_700, return_window_end=None, map_cents=82_231)
    rec = recommender._fallback("TV-SAM-0032", item_facts(row, TH, AS_OF))
    assert rec.option == "hold" and rec.reasons[0] == "Minimum advertised price: $822" and "blocked" in rec.reasons[1]
