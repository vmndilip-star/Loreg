"""Plain-language reasons written by code from computed numbers. Pure functions; no model.

These strings are what the screens show. A model may later rephrase them, but the numbers in them
always come from here.
"""

from datetime import date

from app.core.actions import OptionSet
from app.core.thresholds import Thresholds

SINGULAR = {"tv": "TV", "phone": "phone", "laptop": "laptop", "washer": "washer",
            "washer_dryer": "washer-dryer", "refrigerator": "fridge"}

DQ_TEXT = {
    "MISSING_REQUIRED": "{n} item(s) are missing a required value or have one that can't be read. Fill it in or leave them out.",
    "MISSING_COST": "{n} item(s) have no cost, so we can't tell how much money is tied up in them.",
    "NEGATIVE_QTY": "{n} item(s) show a negative quantity. Enter the real count.",
    "DUPLICATE_SKU": "{n} item(s) appear twice. Keep one row so they aren't counted twice.",
    "FUTURE_DATE": "{n} item(s) have a date in the future. Check the date.",
    "PRICE_BELOW_COST": "{n} item(s) sell for less than you paid.",
    "BAD_CATEGORY": "{n} item(s) have a category Loreg doesn't know.",
    "SALE_BEFORE_RECEIVED": "{n} item(s) show a sale before the stock arrived.",
    "VELOCITY_OUTLIER": "{n} item(s) sold unusually fast. Check the counts.",
    "MSRP_MISMATCH": "{n} item(s) are priced far above list price.",
    "ZERO_QTY": "{n} item(s) have nothing on the shelf. They are left out of the risk figures.",
}


def money(cents: int) -> str:
    return "${:,.0f}".format(round(cents / 100.0))


def why_flagged(status: str, dsls: int | None, slow_dsls: float) -> str:
    if dsls is not None and (status == "dead" or dsls >= slow_dsls):
        return f"No sale in {dsls} days"
    return "Too much stock for its sales pace"


def plan_reasons(*, option: str, discount_pct: int, qty: int, cost_cents: int, sell_cents: int, dsls: int | None,
                 dos: float | None, category: str, opts: OptionSet, return_end: date | None, th: Thresholds,
                 map_cents: int | None = None) -> list[str]:
    """Two short reasons for the proposed option, from the same numbers the rules used."""
    t = th.categories[category]
    noun = SINGULAR[category]
    if option == "discount" and not opts.below_cost:
        d = discount_pct / 100.0
        profit = round(qty * (sell_cents * (1 - d) - cost_cents))
        given = round(qty * sell_cents * d)
        return [f"You still make {money(profit)} across the {qty} {noun}s. The discount gives up about {money(given)}.",
                f"The {discount_pct}% step is the smallest one that keeps the price at or above your cost."]
    if option == "return":
        when = return_end.strftime("%b %d, %Y").replace(" 0", " ") if return_end else "a date on file"
        out = [f"The supplier return window is open until {when}.",
               f"Returning {qty} items frees about {money(qty * cost_cents)} in cash."]
        if opts.map_blocked and map_cents:
            out.append(f"A discount would take the price below the minimum advertised price of {money(map_cents)}, so none is offered.")
        return out
    if option == "discount":  # below cost, only after twice the dead limit
        d = discount_pct / 100.0
        loss = max(round(qty * (cost_cents - sell_cents * (1 - d))), 0)
        return [f"No sale in {dsls} days, past {th.below_cost_dead_multiple:g} times the dead limit for a {noun} "
                f"({th.below_cost_dead_multiple * t.dead_dsls:g}), so Loreg allows a price below cost.",
                f"This sells below your cost: about {money(loss)} lost, but about {money(round(qty * sell_cents * (1 - d)))} comes back in cash."]
    if option == "bundle":
        loss5 = max(round(qty * (cost_cents - sell_cents * 0.95)), 0)
        pace = (f"{round(dos)} days of stock at the current pace, against the {t.slow_days_of_stock:g} Loreg allows for a {noun}."
                if dos is not None else "No sales in the last 90 days.")
        if opts.map_blocked and map_cents:
            return [f"A discount would take the price below the minimum advertised price of {money(map_cents)}, so none is offered.", pace]
        return [f"A 5% discount would sell below your cost: about {money(loss5)} lost across the {qty}.", pace]
    return ["A discount would sell below your cost.",
            f"It is still selling: last sale {dsls} days ago." if dsls is not None else "It has no sale history yet."]
