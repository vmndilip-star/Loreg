"""How far a discount may go and what stops the next step: a pure function, read when a card is built, never stored.

It only restates what `allowed_options` decided. Money is in cents; the page does no arithmetic on it."""

import csv
import io
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core import actions
from app.core.thresholds import load_thresholds
from conftest import OfflineModel

TH = load_thresholds()
AS_OF = date(2026, 10, 26)
COST = 100_000


def headroom(sell, cost=COST, map_price=None, dsls=30):
    opts = actions.allowed_options(qty=5, unit_cost=cost, sell_price=sell, dsls=dsls, units_sold_90d=2, category="tv", seasonal=False,
                                   return_window_end=None, as_of=AS_OF, th=TH, map_price=map_price)
    return opts, actions.discount_headroom(opts, sell_price=sell, unit_cost=cost, map_price=map_price, th=TH)


def test_a_discountable_item_can_use_the_whole_ladder_and_has_no_next_step():
    opts, h = headroom(125_000)
    assert (h.largest_pct, h.largest_price, h.next_pct, h.next_price, h.blocked_by, h.floor) == (20, 100_000, None, None, None, None)
    assert h.largest_pct == max(opts.discounts_pct)


def test_the_next_step_below_cost_is_blocked_by_cost():
    opts, h = headroom(110_000)                    # 5% -> 104,500 is fine, 10% -> 99,000 is below cost
    assert (h.largest_pct, h.largest_price, h.next_pct, h.next_price, h.blocked_by, h.floor) == (5, 104_500, 10, 99_000, "cost", COST)
    assert h.largest_pct == max(opts.discounts_pct)


def test_when_every_step_is_below_cost_there_is_no_largest_step():
    opts, h = headroom(102_000)                    # 5% -> 96,900 is below cost
    assert opts.discounts_pct == () and not opts.map_blocked
    assert (h.largest_pct, h.largest_price, h.next_pct, h.next_price, h.blocked_by, h.floor) == (None, None, 5, 96_900, "cost", COST)


def test_a_map_that_stops_a_later_step_is_named():
    opts, h = headroom(125_000, map_price=115_000)         # 5% -> 118,750 ok; 10% -> 112,500 is below MAP but above cost
    assert (h.largest_pct, h.largest_price, h.next_pct, h.next_price, h.blocked_by, h.floor) == (5, 118_750, 10, 112_500, "map", 115_000)


def test_a_map_that_blocks_every_step_is_named_with_the_first_step_and_its_price():
    opts, h = headroom(125_000, map_price=124_000)
    assert opts.map_blocked is True and opts.discounts_pct == ()
    assert (h.largest_pct, h.largest_price, h.next_pct, h.next_price, h.blocked_by, h.floor) == (None, None, 5, 118_750, "map", 124_000)


def test_a_blank_map_is_the_same_as_no_map_and_cost_is_the_only_floor():
    assert headroom(110_000, map_price=None)[1] == headroom(110_000)[1]
    assert headroom(110_000, map_price=0)[1] == headroom(110_000)[1]           # what a blank cell is stored as, if it ever were 0
    assert headroom(110_000)[1].blocked_by == "cost"


def test_a_map_below_cost_changes_nothing_because_cost_is_the_higher_floor():
    assert headroom(110_000, map_price=80_000)[1] == headroom(110_000)[1]


def test_when_both_floors_are_crossed_the_higher_one_is_named():
    _, h = headroom(125_000, map_price=124_000)            # 5% (118,750) is above cost and below MAP: only the MAP is crossed
    assert h.blocked_by == "map"
    _, h = headroom(102_000, map_price=101_000)            # 5% (96,900) is below both; the MAP (101,000) is the higher floor
    assert h.blocked_by == "map" and h.floor == 101_000
    _, h = headroom(102_000, map_price=99_000)             # 5% (96,900) is below both; cost is the higher floor
    assert h.blocked_by == "cost" and h.floor == COST


def test_the_dead_stock_exception_still_names_cost_for_the_step_beyond_it():
    dead = 2 * TH.categories["tv"].dead_dsls
    opts, h = headroom(102_000, dsls=dead)                 # below cost is allowed, but only up to the configured depth
    assert opts.below_cost and h.largest_pct == max(opts.discounts_pct) == TH.below_cost_max_pct
    assert h.next_pct is not None and h.blocked_by == "cost"


# ------------------------------------------------------------------------------ on the cards the API sends
def load(tmp_path, catalog_dir, demo_file=None):
    client = TestClient(create_app(tmp_path / "h.sqlite", as_of="2026-10-26", llm=OfflineModel()))
    raw = demo_file if demo_file is not None else (catalog_dir / "upload_demo_200.csv").read_bytes()
    assert client.post("/api/upload", files={"file": ("d.csv", raw, "text/csv")}).status_code == 200
    group = client.get("/api/launch/exposed").json()["groups"][0]
    client.post(f"/api/alerts/{group['alert_id']}/help")
    r = client.post(f"/api/alerts/{group['alert_id']}/plan", params={"limit": 3})
    return {x["sku"]: x for x in r.json()["recommendations"]}


def test_discount_and_map_blocked_cards_carry_the_headroom_and_other_cards_do_not(tmp_path, catalog_dir):
    cards = load(tmp_path, catalog_dir)
    by_option = {c["option"]: c for c in cards.values()}
    assert set(by_option) == {"discount", "return", "bundle"}
    disc = by_option["discount"]["headroom"]
    assert all(isinstance(disc[k], int) for k in ("largest_pct", "largest_price_cents", "next_pct", "next_price_cents", "floor_cents"))
    assert disc["largest_pct"] >= by_option["discount"]["discount_pct"] and disc["blocked_by"] in ("cost", "map")
    assert by_option["return"]["headroom"] is None                         # no discount, and not blocked by a MAP
    blocked = by_option["bundle"]["headroom"]                              # the MAP-blocked item
    assert blocked["map_blocked"] is True and blocked["largest_pct"] is None and blocked["largest_price_cents"] is None
    assert blocked["blocked_by"] == "map" and blocked["next_pct"] == 5
    assert blocked["floor_cents"] > blocked["next_price_cents"] > 0        # the 5% price is under the MAP, both in cents


def test_nothing_about_the_headroom_is_stored(tmp_path, catalog_dir):
    import sqlite3
    load(tmp_path, catalog_dir)
    con = sqlite3.connect(tmp_path / "h.sqlite")
    columns = {r[1] for r in con.execute("PRAGMA table_info(recommendations)")}
    con.close()
    assert not {c for c in columns if "headroom" in c or "largest" in c or "next_" in c or "block" in c}


def test_an_unreadable_map_is_ignored_so_the_card_shows_cost_only_and_no_map_block(tmp_path, catalog_dir):
    raw = (catalog_dir / "upload_demo_200.csv").read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(raw)))
    ix = rows[0].index("map_price")
    planted = [r for r in rows[1:] if r[ix]]
    assert len(planted) == 1
    planted[0][ix] = "abc"                                                 # cannot be read, so it is treated as no MAP
    out = io.StringIO()
    csv.writer(out, lineterminator="\n").writerows(rows)
    cards = load(tmp_path, catalog_dir, out.getvalue().encode())
    card = cards[planted[0][0]]
    assert card["option"] == "discount"                                    # with no MAP the rules offer a discount again
    h = card["headroom"]
    assert h["map_blocked"] is False and h["blocked_by"] != "map"


def test_a_blocked_price_is_never_shown_equal_to_the_floor_it_goes_below():
    """The demo's first item: 20% off 133,111 is 106,488.8, which is below its cost of 106,489. The shown price is rounded down to the cent."""
    opts, h = headroom(133_111, cost=106_489)
    assert (h.largest_pct, h.next_pct, h.blocked_by) == (15, 20, "cost")
    assert h.next_price == 106_488 < h.floor == 106_489
