"""The Analyze page: one category's figures, and what to buy in it. Parameterized SQL only, every query filtered by the shop. No model call.

Counting rules are the ones /api/categories and /api/summary use: "items" counts every kept row, including ones with nothing on the
shelf; "at risk" means dead or slow with something on the shelf. Money is integer cents. Every bar length is worked out here, so the
page never divides money.
"""

import sqlite3
from datetime import date

from app.core import restock as rs
from app.core.classify import AT_RISK, STATUSES
from app.core.metrics import age_days
from app.core.thresholds import Thresholds
from app.storage.browse import AT_RISK_CASE, _why, category_label, known_categories
from app.storage.repo import latest_upload


class NoCategory(LookupError):
    """The shop has no stock in this category (or no stock list at all); the route answers 404, as for /api/products."""


def _check(conn: sqlite3.Connection, shop_id: int, category: str) -> date:
    if category not in known_categories(conn, shop_id):
        raise NoCategory(f"no category {category!r} in your stock")
    return date.fromisoformat(latest_upload(conn, shop_id)["as_of"])


def shares(values: list[int]) -> list[int]:
    """Whole percentages of the total that add up to exactly 100 (largest remainder), or all 0 when the total is 0."""
    total = sum(values)
    if total <= 0:
        return [0] * len(values)
    floors = [v * 100 // total for v in values]
    order = sorted(range(len(values)), key=lambda i: (-(values[i] * 100 % total), i))
    for i in order[:100 - sum(floors)]:
        floors[i] += 1
    return floors


def _pct(part: int, whole: int) -> int:
    return round(100 * part / whole) if whole > 0 else 0


def category_stats(conn: sqlite3.Connection, shop_id: int, category: str) -> dict:
    as_of = _check(conn, shop_id, category)
    t = conn.execute(
        f"SELECT COUNT(*) AS items, COALESCE(SUM(MAX(s.qty, 0)), 0) AS units, COALESCE(SUM(s.capital_cents), 0) AS cap,"
        f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN 1 ELSE 0 END), 0) AS at_risk_n,"
        f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN s.capital_cents ELSE 0 END), 0) AS at_risk_cap,"
        f" COALESCE(SUM(s.units_sold_30d), 0) AS sold_30d, COALESCE(SUM(s.units_sold_90d), 0) AS sold_90d"
        f" FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? AND p.category = ?",
        (*AT_RISK, *AT_RISK, shop_id, category)).fetchone()
    by_status = {r["status"]: r for r in conn.execute(
        "SELECT s.status AS status, COUNT(*) AS items, COALESCE(SUM(MAX(s.qty, 0)), 0) AS units, COALESCE(SUM(s.capital_cents), 0) AS cap"
        " FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? AND p.category = ? GROUP BY s.status", (shop_id, category))}
    present = [s for s in STATUSES if s in by_status]
    pcts = shares([by_status[s]["cap"] for s in present])
    brands = conn.execute(
        f"SELECT p.brand AS brand, COUNT(*) AS items, COALESCE(SUM(MAX(s.qty, 0)), 0) AS units, COALESCE(SUM(s.units_sold_90d), 0) AS sold_90d,"
        f" COALESCE(SUM(s.capital_cents), 0) AS cap, COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN s.capital_cents ELSE 0 END), 0) AS at_risk_cap"
        f" FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? AND p.category = ? GROUP BY p.brand"
        f" ORDER BY cap DESC, p.brand", (*AT_RISK, shop_id, category)).fetchall()
    biggest = max((b["cap"] for b in brands), default=0)
    return {
        "category": category, "label": category_label(category), "as_of": as_of.isoformat(),
        "items": t["items"], "units": t["units"], "stock_cap_cents": t["cap"], "at_risk_n": t["at_risk_n"], "at_risk_cap_cents": t["at_risk_cap"],
        "sold_30d": t["sold_30d"], "sold_90d": t["sold_90d"],
        "statuses": [{"status": s, "items": by_status[s]["items"], "units": by_status[s]["units"], "cap_cents": by_status[s]["cap"], "pct": p}
                     for s, p in zip(present, pcts)],
        "brands": [{"brand": b["brand"], "items": b["items"], "units": b["units"], "sold_90d": b["sold_90d"], "cap_cents": b["cap"],
                    "at_risk_cap_cents": b["at_risk_cap"], "bar_pct": _pct(b["cap"], biggest), "risk_bar_pct": _pct(b["at_risk_cap"], biggest)}
                   for b in brands],
    }


def _item(r) -> dict:
    return {"sku": r["sku"], "name": f"{r['brand']} {r['model']}".strip(), "variant": r["variant"], "qty": r["qty"], "sold_90d": r["units_sold_90d"]}


def restock(conn: sqlite3.Connection, shop_id: int, category: str, th: Thresholds) -> dict:
    """Three lists for one category: buy (with units and what they cost at the shelf's own unit cost), wait, and buy no more."""
    as_of = _check(conn, shop_id, category)
    target = th.restock_target_days
    rows = conn.execute(
        "SELECT s.sku, s.qty, s.units_sold_90d, s.status, s.dsls, s.capital_cents, s.unit_cost_cents, s.is_seasonal, s.received_date,"
        " p.brand, p.model, p.variant, p.category, p.launch_family FROM skus s JOIN products p ON p.id = s.product_id"
        " WHERE s.shop_id = ? AND p.category = ? ORDER BY s.sku", (shop_id, category)).fetchall()
    buy, wait, dont, enough, no_data = [], [], [], 0, 0
    for r in rows:
        d = rs.decide(r["status"], r["qty"], r["units_sold_90d"], bool(r["launch_family"]), target)
        age = age_days(as_of, date.fromisoformat(r["received_date"])) if r["received_date"] else None
        text = rs.why(d, r["qty"], r["units_sold_90d"], target, bool(r["is_seasonal"]), age)
        if d.decision == rs.BUY:
            buy.append({**_item(r), "cover_days": d.cover_days, "buy_units": d.units, "unit_cost_cents": r["unit_cost_cents"],
                        "buy_cost_cents": d.units * r["unit_cost_cents"], "why": text})
        elif d.decision in (rs.WAIT_LAUNCH, rs.WAIT_NEW):
            wait.append({**_item(r), "cover_days": d.cover_days, "reason": "launch" if d.decision == rs.WAIT_LAUNCH else "new",
                         "would_buy_units": d.units, "why": text})
        elif d.decision == rs.DONT_BUY:
            dont.append({**_item(r), "status": r["status"], "tied_up_cents": r["capital_cents"],
                         "why": _why(r["status"], r["qty"], r["dsls"], r["category"], th)})
        elif d.decision == rs.ENOUGH:
            enough += 1
        else:
            no_data += 1
    buy.sort(key=lambda i: (i["cover_days"], i["sku"]))
    wait.sort(key=lambda i: (i["reason"], i["sku"]))
    dont.sort(key=lambda i: (-i["tied_up_cents"], i["sku"]))
    return {
        "category": category, "label": category_label(category), "as_of": as_of.isoformat(), "target_days": target, "pace_days": rs.PACE_DAYS,
        "buy": buy, "buy_units": sum(i["buy_units"] for i in buy), "buy_cost_cents": sum(i["buy_cost_cents"] for i in buy),
        "wait": wait, "dont_buy": dont, "dont_buy_cap_cents": sum(i["tied_up_cents"] for i in dont), "enough_n": enough, "no_data_n": no_data,
        "note": (f"Each healthy item is stocked up to {target} days at its own pace over the last {rs.PACE_DAYS} days, at what you paid per unit. "
                 f"{target} days is an assumption (config/thresholds.yaml), and the pace is the past, not a forecast."),
    }
