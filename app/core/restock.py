"""What to buy: stock each item up to a target number of days at its own selling pace over the last 90 days. Pure functions: no I/O,
no model, no wall clock.

It is arithmetic on the stock list's own figures (on the shelf, units sold in 90 days), NOT a sales forecast: it assumes the next days
sell like the last 90 did. The target (restock.target_days in config/thresholds.yaml) is an assumption chosen by us.
The 90-day pace is the one the slow rule already uses (app.core.metrics.days_of_stock), so "what to buy" and "what is slow" agree.
"""

from dataclasses import dataclass

from app.core.classify import AT_RISK, INSUFFICIENT, NEW

PACE_DAYS = 90
BUY, ENOUGH, WAIT_LAUNCH, WAIT_NEW, DONT_BUY, NO_DATA = "buy", "enough", "wait_launch", "wait_new", "dont_buy", "no_data"


@dataclass(frozen=True)
class Restock:
    decision: str            # buy | enough | wait_launch | wait_new | dont_buy | no_data
    units: int               # units to buy (buy), or that would be bought without the reason to wait (wait_launch); else 0
    cover_days: int | None   # whole days the shelf lasts at the 90-day pace; None when nothing sold in 90 days


def cover_days(qty: int, units_sold_90d: int) -> int | None:
    """Whole days the units on the shelf last at the 90-day pace, rounded down. None when nothing sold in 90 days (no pace)."""
    if units_sold_90d <= 0:
        return None
    return max(qty, 0) * PACE_DAYS // units_sold_90d


def units_to_buy(qty: int, units_sold_90d: int, target_days: int) -> int:
    """The fewest whole units that bring the shelf to at least `target_days` of stock at the 90-day pace. Integers only:
    ceil(target_days x units_sold_90d / 90) - on the shelf, never below 0."""
    need = -(-target_days * max(units_sold_90d, 0) // PACE_DAYS)
    return max(0, need - max(qty, 0))


def decide(status: str, qty: int, units_sold_90d: int, launch_family: bool, target_days: int) -> Restock:
    """Dead or slow: buy no more. New: too soon to judge its pace. Healthy: buy up to the target, unless the catalog marks the item's
    line as one a newer line may replace (then wait). An item already at the target needs nothing."""
    cover = cover_days(qty, units_sold_90d)
    if status in AT_RISK:
        return Restock(DONT_BUY, 0, cover)
    if status == NEW:
        return Restock(WAIT_NEW, 0, cover)
    if status == INSUFFICIENT:
        return Restock(NO_DATA, 0, cover)
    n = units_to_buy(qty, units_sold_90d, target_days)
    if n == 0:
        return Restock(ENOUGH, 0, cover)
    return Restock(WAIT_LAUNCH if launch_family else BUY, n, cover)


def _units(n: int) -> str:
    return f"{n} unit" + ("" if n == 1 else "s")


def why(r: Restock, qty: int, units_sold_90d: int, target_days: int, seasonal: bool = False, age_days: int | None = None) -> str:
    """The sentence the page shows. Every figure in it is one of the arguments."""
    season = " It is seasonal: the last 90 days may not show the coming season." if seasonal else ""
    pace = f"Sold {_units(units_sold_90d)} in the last 90 days."
    if r.decision == BUY:
        shelf = "Out of stock." if qty <= 0 else f"The {_units(qty)} on the shelf last{'s' if qty == 1 else ''} about {r.cover_days} days."
        return f"{pace} {shelf} Buying {r.units} brings the shelf to at least {target_days} days at that pace.{season}"
    if r.decision == WAIT_LAUNCH:
        return (f"{pace} Loreg's catalog marks its line as one a newer line may replace, so wait before buying more. "
                f"At its pace, {r.units} more would bring the shelf to {target_days} days.{season}")
    if r.decision == WAIT_NEW:
        return (f"Received {age_days} days ago: too soon to judge its pace." if age_days is not None
                else "Received recently: too soon to judge its pace.")
    if r.decision == ENOUGH:
        return f"{pace} The shelf already covers {target_days} days or more."
    if r.decision == NO_DATA:
        return "Not enough sales data to judge."
    return "Dead or slow: buy no more until it sells."
