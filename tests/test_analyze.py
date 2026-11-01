"""The Analyze page's numbers: /api/categories/{category} and /api/categories/{category}/restock. Offline, no model.

The restock lists are checked against an independent recomputation from the CSV (tests/csv_truth.py classifies; the arithmetic is
written out again here), so a bug in app/core/restock.py cannot also hide in the check."""

import dataclasses
import math
from pathlib import Path

import pytest

from app.api.main import create_app
from app.core import restock as rs
from app.storage.analyze import shares
from auth import AuthedClient
from csv_truth import Truth, read_csv

pytestmark = pytest.mark.usefixtures("no_model")
AS_OF = "2026-10-26"
TARGET = 45


@pytest.fixture(scope="module")
def shop(catalog_dir, tmp_path_factory):
    app = create_app(tmp_path_factory.mktemp("analyze") / "a.sqlite", as_of=AS_OF)
    c = AuthedClient(app)
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("d.csv", f, "text/csv")}).status_code == 200
    truth = Truth(catalog_dir, AS_OF, master="upload_demo_200.csv")
    sold = {r["sku"]: int(r["units_sold_90d"] or 0) for r in read_csv(Path(catalog_dir) / "upload_demo_200.csv")}
    return app, c, truth, sold


# ---- the rule, as pure functions -----------------------------------------------------------------

@pytest.mark.parametrize("qty, sold_90d, target, units", [
    (1, 6, 45, 2),        # ceil(45 x 6 / 90) = 3, minus the 1 on the shelf
    (0, 6, 45, 3),        # out of stock: the whole target
    (3, 6, 45, 0),        # already exactly 45 days
    (9, 6, 45, 0),        # more than enough
    (0, 0, 45, 0),        # nothing sold in 90 days: no pace, nothing to buy
    (1, 1, 45, 0),        # ceil(0.5) = 1, and there is 1
    (0, 1, 45, 1),
    (2, 7, 45, 2),        # ceil(3.5) = 4
    (-2, 6, 45, 3),       # a negative shelf counts as empty
])
def test_units_to_buy_is_the_smallest_whole_number_that_reaches_the_target(qty, sold_90d, target, units):
    assert rs.units_to_buy(qty, sold_90d, target) == units
    if units:
        assert (max(qty, 0) + units) * 90 >= target * sold_90d > (max(qty, 0) + units - 1) * 90


def test_cover_days_rounds_down_and_has_no_pace_without_sales():
    assert rs.cover_days(1, 6) == 15 and rs.cover_days(2, 7) == 25 and rs.cover_days(0, 5) == 0 and rs.cover_days(4, 0) is None


@pytest.mark.parametrize("status, launch, decision", [
    ("dead", False, rs.DONT_BUY), ("slow", True, rs.DONT_BUY), ("new", False, rs.WAIT_NEW), ("insufficient_data", False, rs.NO_DATA),
    ("healthy", False, rs.BUY), ("healthy", True, rs.WAIT_LAUNCH),
])
def test_only_a_healthy_item_whose_line_is_not_being_replaced_is_bought(status, launch, decision):
    d = rs.decide(status, 1, 6, launch, TARGET)
    assert d.decision == decision
    assert d.units == (2 if decision in (rs.BUY, rs.WAIT_LAUNCH) else 0)
    assert rs.decide("healthy", 9, 6, launch, TARGET).decision == rs.ENOUGH


def test_every_figure_in_the_sentence_is_one_of_its_inputs():
    text = rs.why(rs.decide("healthy", 1, 6, False, TARGET), 1, 6, TARGET)
    assert text == "Sold 6 units in the last 90 days. The 1 unit on the shelf lasts about 15 days. Buying 2 brings the shelf to at least 45 days at that pace."
    assert rs.why(rs.decide("healthy", 0, 6, False, TARGET), 0, 6, TARGET).startswith("Sold 6 units in the last 90 days. Out of stock. Buying 3 ")
    assert "newer line may replace" in rs.why(rs.decide("healthy", 1, 6, True, TARGET), 1, 6, TARGET)
    assert rs.why(rs.decide("new", 4, 1, False, TARGET), 4, 1, TARGET, age_days=12) == "Received 12 days ago: too soon to judge its pace."
    assert "seasonal" in rs.why(rs.decide("healthy", 1, 6, False, TARGET), 1, 6, TARGET, seasonal=True)


@pytest.mark.parametrize("values", [[1, 1, 1], [6308164, 5100980, 8037233, 4145832], [5, 0], [0, 0], [1], [2, 2, 2, 2, 2, 2, 1]])
def test_shares_are_whole_percentages_that_add_up_to_100(values):
    out = shares(values)
    assert sum(out) == (100 if sum(values) else 0) and all(0 <= p <= 100 for p in out)
    assert all(abs(p - 100 * v / sum(values)) < 1 for p, v in zip(out, values)) if sum(values) else out == [0] * len(values)


# ---- the routes, on the demo stock list ----------------------------------------------------------

def test_the_category_figures_agree_with_the_category_list_and_the_csv(shop):
    _, c, truth, sold = shop
    listed = {k["key"]: k for k in c.get("/api/categories").json()["categories"]}
    for key, row in listed.items():
        s = c.get(f"/api/categories/{key}").json()
        mine = [i for i in truth.items if i["category"] == key]
        assert (s["items"], s["at_risk_n"], s["at_risk_cap_cents"], s["stock_cap_cents"]) == (row["items"], row["at_risk_n"], row["at_risk_cap_cents"],
                                                                                         row["stock_cap_cents"])
        assert s["items"] == len(mine) and s["units"] == sum(max(i["qty"], 0) for i in mine) and s["sold_90d"] == sum(sold[i["sku"]] for i in mine)
        assert sum(x["items"] for x in s["statuses"]) == s["items"] and sum(x["cap_cents"] for x in s["statuses"]) == s["stock_cap_cents"]
        assert sum(x["pct"] for x in s["statuses"]) == (100 if s["stock_cap_cents"] else 0)
        assert sum(b["cap_cents"] for b in s["brands"]) == s["stock_cap_cents"] and sum(b["at_risk_cap_cents"] for b in s["brands"]) == s["at_risk_cap_cents"]
        assert s["brands"][0]["bar_pct"] == 100 and all(b["risk_bar_pct"] <= b["bar_pct"] for b in s["brands"])


def test_what_to_buy_matches_an_independent_recomputation_from_the_csv(shop):
    _, c, truth, sold = shop
    for key in {i["category"] for i in truth.items}:
        r = c.get(f"/api/categories/{key}/restock").json()
        mine = {i["sku"]: i for i in truth.items if i["category"] == key}
        expect_buy, expect_wait_launch = {}, set()
        for sku, i in mine.items():
            if i["status"] != "healthy":
                continue
            need = max(0, math.ceil(TARGET * sold[sku] / 90) - max(i["qty"], 0))
            if need and i["launch"]:
                expect_wait_launch.add(sku)
            elif need:
                expect_buy[sku] = need
        assert {b["sku"]: b["buy_units"] for b in r["buy"]} == expect_buy, key
        assert {w["sku"] for w in r["wait"] if w["reason"] == "launch"} == expect_wait_launch, key
        assert {w["sku"] for w in r["wait"] if w["reason"] == "new"} == {s for s, i in mine.items() if i["status"] == "new"}
        assert {d["sku"] for d in r["dont_buy"]} == {i["sku"] for i in truth.by_status("dead", "slow") if i["category"] == key}
        assert len(r["buy"]) + len(r["wait"]) + len(r["dont_buy"]) + r["enough_n"] + r["no_data_n"] == len(mine)
        for b in r["buy"]:
            assert b["buy_cost_cents"] == b["buy_units"] * mine[b["sku"]]["cost"] and b["unit_cost_cents"] == mine[b["sku"]]["cost"]
            assert (max(b["qty"], 0) + b["buy_units"]) * 90 >= TARGET * b["sold_90d"]
        assert r["buy_units"] == sum(b["buy_units"] for b in r["buy"]) and r["buy_cost_cents"] == sum(b["buy_cost_cents"] for b in r["buy"])
        assert r["dont_buy_cap_cents"] == c.get(f"/api/categories/{key}").json()["at_risk_cap_cents"]
        assert [b["cover_days"] for b in r["buy"]] == sorted(b["cover_days"] for b in r["buy"])           # what runs out first comes first


def test_the_demo_tvs_wait_on_the_line_a_newer_one_may_replace_and_buy_more_of_what_sells(shop):
    r = shop[1].get("/api/categories/tv/restock").json()
    assert r["target_days"] == TARGET and r["pace_days"] == 90 and "assumption" in r["note"] and "not a forecast" in r["note"]
    assert r["buy"] and not [b for b in r["buy"] if b["sku"].startswith("TV-SAM") and "launch" in b["why"]]
    assert [w for w in r["wait"] if w["reason"] == "launch"]
    assert r["dont_buy"][0]["tied_up_cents"] == max(d["tied_up_cents"] for d in r["dont_buy"])


def test_the_target_comes_from_the_config(shop):
    app, c, _, _ = shop
    before = c.get("/api/categories/tv/restock").json()
    th = app.state.th
    app.state.th = dataclasses.replace(th, restock_target_days=90)
    try:
        after = c.get("/api/categories/tv/restock").json()
    finally:
        app.state.th = th
    assert after["target_days"] == 90 and after["buy_units"] > before["buy_units"] and "90 days is an assumption" in after["note"]


def test_a_category_the_shop_does_not_stock_is_404_and_so_is_a_shop_with_no_stock_list(shop, tmp_path):
    c = shop[1]
    for path in ("/api/categories/refrigerator", "/api/categories/refrigerator/restock", "/api/categories/nope", "/api/categories/%27%20OR%201=1"):
        r = c.get(path)
        assert r.status_code == 404 and r.json()["detail"].startswith("no category"), path
    assert c.get("/api/categories/" + "x" * 41).status_code == 422
    empty = AuthedClient(create_app(tmp_path / "e.sqlite", as_of=AS_OF))
    assert empty.get("/api/categories/tv").status_code == 404 and empty.get("/api/categories/tv/restock").status_code == 404
