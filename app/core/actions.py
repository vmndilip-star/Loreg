"""Allowed options for one at-risk item. Pure functions; code decides what is allowed, a model may only pick.

Rules (from the Loreg base context, section 4.2):
  - Discount ladder (config): a step is allowed while sell_price * (1 - step) >= unit_cost.
  - If the item has a minimum advertised price (MAP), a step is also allowed only while the price stays at or above it.
  - Selling below cost is never offered, except after `below_cost_dead_multiple` times the dead limit
    with no sale, and then only up to `below_cost_max_pct`.
  - Return is allowed only while the supplier return window is open.
  - Bundle is allowed for stock that is dead, slow or has no sales in 90 days.
  - Hold is always allowed.
The default (what a rule-based fallback proposes) tries: smallest profitable discount, return,
below-cost discount, bundle, hold.
"""

from dataclasses import dataclass, field, replace
from datetime import date

from app.core.metrics import days_of_stock
from app.core.thresholds import Thresholds


@dataclass(frozen=True)
class OptionSet:
    discounts_pct: tuple = ()          # allowed discount steps, smallest first
    below_cost: bool = False           # True when the discounts listed sell below cost
    return_allowed: bool = False
    bundle_allowed: bool = False
    hold_allowed: bool = True
    map_blocked: bool = False         # a discount would have been allowed, but every step goes below the minimum advertised price
    notes: tuple = field(default_factory=tuple)

    def options(self) -> list:
        out = []
        if self.return_allowed:
            out.append("return")
        if self.discounts_pct:
            out.append("discount")
        if self.bundle_allowed:
            out.append("bundle")
        if self.hold_allowed:
            out.append("hold")
        return out


def allowed_options(*, qty: int, unit_cost: float, sell_price: float, dsls: int | None, units_sold_90d: int,
                    category: str, seasonal: bool, return_window_end: date | None, as_of: date,
                    th: Thresholds, map_price: float | None = None) -> OptionSet:
    t = th.for_category(category, seasonal)
    floor = max(unit_cost, map_price or 0.0)                   # the price may not go below cost or below MAP
    steps = tuple(p for p in th.discount_ladder_pct if sell_price * (1 - p / 100.0) >= floor)
    below_cost = False
    notes = []
    if not steps:
        if dsls is not None and dsls >= th.below_cost_dead_multiple * t.dead_dsls:
            steps = tuple(p for p in th.discount_ladder_pct if p <= th.below_cost_max_pct
                          and (not map_price or sell_price * (1 - p / 100.0) >= map_price))   # past MAP is never allowed
            below_cost = bool(steps)
            if steps:
                notes.append("below cost allowed: no sale for at least "
                             f"{th.below_cost_dead_multiple:g} times the dead limit")
        if not steps:
            notes.append("every discount step would sell below cost")
    dos = days_of_stock(qty, units_sold_90d)
    bundle = dos is None or dos > t.slow_days_of_stock or (dsls is not None and dsls >= t.slow_dsls)
    opts = OptionSet(
        discounts_pct=steps, below_cost=below_cost,
        return_allowed=return_window_end is not None and return_window_end >= as_of,
        bundle_allowed=bundle, hold_allowed=True, notes=tuple(notes),
    )
    if map_price and not steps:                                # would any step have been allowed without the MAP?
        free = allowed_options(qty=qty, unit_cost=unit_cost, sell_price=sell_price, dsls=dsls, units_sold_90d=units_sold_90d, category=category,
                               seasonal=seasonal, return_window_end=return_window_end, as_of=as_of, th=th)
        if free.discounts_pct:
            opts = replace(opts, map_blocked=True, notes=("every discount step would go below the minimum advertised price",))
    return opts


def default_option(opts: OptionSet) -> tuple:
    """(option, discount_pct) a rule-based fallback proposes, in the prototype's order."""
    if opts.discounts_pct and not opts.below_cost:
        return "discount", opts.discounts_pct[0]
    if opts.return_allowed:
        return "return", 0
    if opts.discounts_pct and opts.below_cost:
        return "discount", opts.discounts_pct[-1]
    if opts.bundle_allowed:
        return "bundle", 0
    return "hold", 0
