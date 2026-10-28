"""Build the Facts agents may cite, from stored rows. Pure code: every number here comes from app.core, never from a model.

A fact id looks like "TV-SAM-0042:capital_tied". The agents get a list of Facts and may only state numbers that appear in them;
app.agents.base checks that after every model answer.
"""

from datetime import date

from app.core import actions, metrics
from app.core.explain import money
from app.core.thresholds import Thresholds
from app.schemas.facts import Fact

STATUS_TEXT = {"dead": "dead stock", "slow": "slow stock", "healthy": "healthy", "new": "new stock", "insufficient_data": "not enough sales history"}


def item_facts(row, th: Thresholds, as_of: date) -> list[Fact]:
    """Facts about one stocked item. `row` is a skus row joined with its product (sqlite3.Row or dict)."""
    map_cents = row["map_cents"] if "map_cents" in row.keys() else None      # older rows and tests have no minimum advertised price
    sku, cat = row["sku"], row["category"]
    cost, sell, qty, dsls = row["unit_cost_cents"], row["sell_price_cents"], row["qty"], row["dsls"]
    rw = date.fromisoformat(row["return_window_end"]) if row["return_window_end"] else None
    dos = metrics.days_of_stock(qty, row["units_sold_90d"])
    opts = actions.allowed_options(qty=qty, unit_cost=cost, sell_price=sell, dsls=dsls, units_sold_90d=row["units_sold_90d"], category=cat,
                                   seasonal=bool(row["is_seasonal"]), return_window_end=rw, as_of=as_of, th=th,
                                   map_price=map_cents)                # cents, like cost and price

    def f(key, label, value, number=None):
        return Fact(id=f"{sku}:{key}", sku=sku, key=key, label=label, value=value, number=number, as_of=as_of.isoformat())

    facts = [
        f("qty", "On the shelf", f"{qty}", float(qty)),
        f("capital_tied", "Money tied up", money(row["capital_cents"]), row["capital_cents"] / 100.0),
        f("days_since_last_sale", "Days since last sale", "never sold" if dsls is None else f"{dsls} days", None if dsls is None else float(dsls)),
        f("days_of_stock", "Days of stock at the current pace", "no sales in 90 days" if dos is None else f"{round(dos)} days", dos),
        f("status", "Stock status", STATUS_TEXT.get(row["status"], row["status"])),
        f("status_dead", "Dead stock", "yes" if row["status"] == "dead" else "no", 1.0 if row["status"] == "dead" else 0.0),
        f("return_window_open", "Supplier return window", f"open until {rw.isoformat()}" if opts.return_allowed else "closed or none",
          1.0 if opts.return_allowed else 0.0),
        f("max_discount_pct", "Highest discount that stays within the rules", f"{max(opts.discounts_pct)}%" if opts.discounts_pct else "none",
          float(max(opts.discounts_pct)) if opts.discounts_pct else 0.0),
        f("bundle_allowed", "Bundling allowed", "yes" if opts.bundle_allowed else "no", 1.0 if opts.bundle_allowed else 0.0),
    ]
    if map_cents is not None:                         # only items that have a minimum advertised price carry this fact
        facts.append(f("map_price", "Minimum advertised price", money(map_cents), map_cents / 100.0))
        facts.append(f("discount_blocked_by_map", "A discount is blocked by that price", "yes" if opts.map_blocked else "no",
                       1.0 if opts.map_blocked else 0.0))
    return facts


def summary_facts(summary: dict, launch_groups: list[dict]) -> list[Fact]:
    """Shop-level facts for the daily brief."""
    t = summary["totals"]

    def f(key, label, value, number):
        return Fact(id=f"shop:{key}", key=key, label=label, value=value, number=number, as_of=summary["as_of"])

    facts = [
        f("capital_at_risk_total", "Money at risk, no plan yet", money(summary["money_at_risk_cents"]), summary["money_at_risk_cents"] / 100.0),
        f("dead_capital", "Money tied up in dead stock", f"{money(t['dead']['cap_cents'])} in {t['dead']['n']} items", t["dead"]["cap_cents"] / 100.0),
        f("slow_capital", "Money tied up in slow stock", f"{money(t['slow']['cap_cents'])} in {t['slow']['n']} items", t["slow"]["cap_cents"] / 100.0),
        f("launch_exposed_count", "Launch-family items that are dead or slow", str(sum(g["n"] for g in launch_groups)),
          float(sum(g["n"] for g in launch_groups))),
    ]
    for g in launch_groups:
        src = g["source"]
        facts.append(Fact(id=f"alert:{g['alert_id']}", kind="source", key="launch_alert", label=f"{g['label']} alert",
                          value=(f"{src['name']}, published {str(src['published'])[:10]}; launch date: {src['launch_date'] or 'announced, no date'}"
                                 if src["attached"] else "no dated source attached"),
                          url=src.get("url", "") or "", as_of=summary["as_of"]))
    return facts
