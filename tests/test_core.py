"""Golden tests for app/core: every expected value here is worked out by hand, not by the code under test."""

from datetime import date, timedelta

import pytest

from app.core import actions, classify as C, metrics, quality
from app.core.thresholds import load_thresholds

AS_OF = date(2026, 10, 26)
TH = load_thresholds()


def d(days_ago):
    return AS_OF - timedelta(days=days_ago)


def status(qty=3, received=200, last=None, u90=0, cat="tv", seasonal=False):
    return C.classify(qty, None if received is None else d(received), None if last is None else d(last),
                      u90, cat, seasonal, AS_OF, TH)


# ---- thresholds come from the config file ------------------------------------------

def test_thresholds_loaded_from_config():
    tv = TH.categories["tv"]
    assert (tv.slow_dsls, tv.dead_dsls, tv.slow_days_of_stock, tv.new_grace_days) == (60, 120, 120, 30)
    assert TH.categories["phone"].dead_dsls == 90
    assert TH.categories["refrigerator"].new_grace_days == 45
    assert set(TH.categories) == {"tv", "phone", "laptop", "washer", "washer_dryer", "refrigerator"}


def test_seasonal_multiplies_all_but_grace():
    s = TH.for_category("tv", seasonal=True)
    assert (s.slow_dsls, s.dead_dsls, s.slow_days_of_stock, s.new_grace_days) == (90, 180, 180, 30)


# ---- classification ------------------------------------------------------------------

@pytest.mark.parametrize("kwargs, expected", [
    (dict(qty=0, last=1, u90=5), "healthy"),                      # nothing on the shelf
    (dict(received=None, last=None), "insufficient_data"),        # never sold, no received date
    (dict(received=25, last=None), "new"),                        # inside the 30-day grace
    (dict(received=70, last=None), "insufficient_data"),          # never sold, mid age
    (dict(received=120, last=None), "dead"),                      # never sold, as old as the dead limit
    (dict(received=119, last=None), "insufficient_data"),         # one day short
    (dict(last=59, u90=1, qty=1), "healthy"),                     # one day short of slow
    (dict(last=60, u90=1, qty=1), "slow"),                        # exactly at slow
    (dict(last=119, u90=0, qty=1), "slow"),                       # one day short of dead
    (dict(last=120, u90=0, qty=1), "dead"),                       # exactly at dead
    (dict(last=5, u90=6, qty=10), "slow"),                        # 10 / (6/90) = 150 days of stock > 120
    (dict(last=5, u90=6, qty=8), "healthy"),                      # 8 / (6/90) = 120, not above 120
    (dict(last=5, u90=0, qty=1), "slow"),                         # recent sale but no 90-day sales: infinite stock
    (dict(received=10, last=3, u90=2, qty=4), "new"),             # received inside the grace window
    (dict(last=89, u90=1, qty=1, seasonal=True), "healthy"),      # seasonal slow limit is 90
    (dict(last=90, u90=0, qty=1, seasonal=True), "slow"),
    (dict(last=179, u90=0, qty=1, seasonal=True), "slow"),
    (dict(last=180, u90=0, qty=1, seasonal=True), "dead"),
    (dict(cat="phone", last=44, u90=1, qty=1), "healthy"),        # phone slow limit is 45
    (dict(cat="phone", last=45, u90=1, qty=1), "slow"),
    (dict(cat="phone", last=90, u90=0, qty=1), "dead"),           # phone dead limit is 90
    (dict(cat="washer", received=44, last=None), "new"),          # appliance grace is 45
    (dict(cat="washer", last=149, u90=0, qty=1), "slow"),
    (dict(cat="refrigerator", last=150, u90=0, qty=1), "dead"),
])
def test_classification(kwargs, expected):
    assert status(**kwargs) == expected


# ---- metrics ---------------------------------------------------------------------------

def test_metrics_hand_computed():
    assert metrics.days_since_last_sale(AS_OF, d(241)) == 241
    assert metrics.days_since_last_sale(AS_OF, None) is None
    assert metrics.days_of_stock(10, 6) == pytest.approx(150.0)
    assert metrics.days_of_stock(10, 0) is None
    assert metrics.capital_at_risk(4, 1192.75) == pytest.approx(4771.0)
    assert metrics.capital_at_risk(-2, 100.0) == 0.0
    assert metrics.margin_impact(7, 1000.0, 0.05) == pytest.approx(350.0)
    assert metrics.margin_after(7, 1000.0, 900.0, 0.05) == pytest.approx(350.0)   # 7 * (950 - 900)
    assert metrics.margin_after(7, 1000.0, 980.0, 0.05) == pytest.approx(-210.0)  # sells below cost
    assert metrics.capital_released(3, 500.0) == 1500.0
    assert metrics.cash_back(3, 500.0, 0.10) == pytest.approx(1350.0)


def test_launch_risk():
    assert metrics.launch_risk(30, 90.0) is True       # 90 days of stock, launch in 30
    assert metrics.launch_risk(30, 20.0) is False
    assert metrics.launch_risk(-1, 90.0) is False      # launch already passed
    assert metrics.launch_risk(None, 90.0) is False    # no date, no risk claim
    assert metrics.launch_risk(30, None) is True       # no sales at all


# ---- data quality ----------------------------------------------------------------------

def row(**over):
    base = dict(sku="TV-1", brand="LG", model="x", category="tv", qty_on_hand="3", unit_cost="500", sell_price="600",
                last_sale_date="2026-10-01", received_date="2026-08-01", units_sold_30d="4", msrp="700")
    base.update(over)
    return base


def codes(rows):
    return sorted((i.row, i.code) for i in quality.dq_check(rows, AS_OF, TH))


def test_a_clean_row_raises_nothing():
    assert codes([row()]) == []


@pytest.mark.parametrize("over, code", [
    (dict(brand=""), "MISSING_REQUIRED"),
    (dict(unit_cost=""), "MISSING_COST"),
    (dict(unit_cost="0"), "MISSING_COST"),
    (dict(qty_on_hand="-2"), "NEGATIVE_QTY"),
    (dict(last_sale_date="2026-10-30"), "FUTURE_DATE"),
    (dict(sell_price="400"), "PRICE_BELOW_COST"),
    (dict(category="televison"), "BAD_CATEGORY"),
    (dict(received_date="2026-10-15", last_sale_date="2026-10-01"), "SALE_BEFORE_RECEIVED"),
    (dict(sell_price="850"), "MSRP_MISMATCH"),            # 850 > 1.2 * 700
    (dict(qty_on_hand="0"), "ZERO_QTY"),
])
def test_each_code_fires(over, code):
    assert (1, code) in codes([row(**over)])


def test_duplicate_sku_is_case_and_space_insensitive():
    assert (2, "DUPLICATE_SKU") in codes([row(), row(sku=" tv-1 ")])


def velocity_rows(last):
    return [row(sku=f"S{i}", units_sold_30d=str(v)) for i, v in enumerate([1, 1, 1, 1, 1, 1, 1, 1, last])]


def test_velocity_outlier_fires_above_the_floor():
    assert (9, "VELOCITY_OUTLIER") in codes(velocity_rows(31))


def test_velocity_floor_is_absolute():
    # 25 is far above the IQR fence (1 + 3 * 0 = 1) but below the floor of 30, so it is not flagged
    assert not any(code == "VELOCITY_OUTLIER" for _, code in codes(velocity_rows(25)))
    assert not any(code == "VELOCITY_OUTLIER" for _, code in codes(velocity_rows(30)))   # the floor itself is allowed


def test_severities():
    assert quality.SEVERITY["DUPLICATE_SKU"] == "error" and quality.SEVERITY["MSRP_MISMATCH"] == "warning"
    assert quality.SEVERITY["ZERO_QTY"] == "info" and len(quality.CODES) == 11


def test_formats_that_must_parse():
    assert quality.parse_date("03/15/2026") == date(2026, 3, 15)
    assert quality.parse_date("2026-03-15") == date(2026, 3, 15)
    assert quality.parse_date("2026-13-45") == "BAD" and quality.parse_date("") is None
    assert quality.parse_money("$1,299.00") == 1299.0 and quality.parse_money("abc") is None
    assert quality.parse_int("5.0") == 5 and quality.parse_int("5.5") is None
    assert quality.normalize_category("Television") == "tv" and quality.normalize_category("fridge") == "refrigerator"
    ok = [row(last_sale_date="10/01/2026", unit_cost="$500.00", category="Television", qty_on_hand="3.0")]
    assert codes(ok) == []


# ---- allowed options -----------------------------------------------------------------------

def opts(**over):
    base = dict(qty=4, unit_cost=1000.0, sell_price=1200.0, dsls=70, units_sold_90d=1, category="tv", seasonal=False,
                return_window_end=None, as_of=AS_OF, th=TH)
    base.update(over)
    return actions.allowed_options(**base)


def test_discount_steps_stay_above_cost():
    o = opts(sell_price=1200.0, unit_cost=1000.0)          # 1200 * 0.85 = 1020 >= 1000, 1200 * 0.80 = 960 < 1000
    assert o.discounts_pct == (5, 10, 15) and not o.below_cost
    assert opts(sell_price=1040.0).discounts_pct == ()     # 1040 * 0.95 = 988 < 1000: no step
    assert opts(sell_price=1053.0).discounts_pct == (5,)   # 1053 * 0.95 = 1000.35 >= 1000: 5% only


def test_no_below_cost_discount_for_ordinary_dead_stock():
    o = opts(sell_price=1010.0, dsls=130)                   # dead (>=120) but not yet 2 x 120
    assert o.discounts_pct == () and "discount" not in o.options()


def test_below_cost_only_after_twice_the_dead_limit():
    o = opts(sell_price=1010.0, dsls=240)
    assert o.discounts_pct == (5, 10) and o.below_cost
    assert actions.default_option(o) == ("discount", 10)


def test_return_only_while_the_window_is_open():
    assert opts(return_window_end=AS_OF).return_allowed
    assert not opts(return_window_end=AS_OF - timedelta(days=1)).return_allowed
    assert not opts(return_window_end=None).return_allowed


def test_default_option_order():
    assert actions.default_option(opts()) == ("discount", 5)
    closed = opts(sell_price=1010.0, return_window_end=AS_OF + timedelta(days=5))
    assert actions.default_option(closed) == ("return", 0)
    assert actions.default_option(opts(sell_price=1010.0)) == ("bundle", 0)             # slow, no discount, no return
    assert actions.default_option(opts(sell_price=1010.0, dsls=5, units_sold_90d=30, qty=1)) == ("hold", 0)


def test_hold_is_always_allowed():
    assert "hold" in opts(sell_price=1010.0).options()
