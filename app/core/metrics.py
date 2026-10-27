"""Stock metrics. Pure functions: no I/O, no model, no wall clock (as_of is always passed in)."""

from datetime import date


def days_since(as_of: date, when: date | None) -> int | None:
    """Whole days from `when` to `as_of`; None if the date is unknown."""
    return None if when is None else (as_of - when).days


def days_since_last_sale(as_of: date, last_sale: date | None) -> int | None:
    return days_since(as_of, last_sale)


def age_days(as_of: date, received: date | None) -> int | None:
    return days_since(as_of, received)


def velocity_per_day(units_sold_90d: int | None) -> float:
    return (units_sold_90d or 0) / 90.0


def days_of_stock(qty: int, units_sold_90d: int | None) -> float | None:
    """qty divided by the 90-day sales rate. None means no sales in 90 days (infinite)."""
    v = velocity_per_day(units_sold_90d)
    return qty / v if v > 0 else None


def capital_at_risk(qty: int, unit_cost: float) -> float:
    """Money tied up on the shelf at what the owner paid. Never negative."""
    return max(qty, 0) * unit_cost


def margin_impact(qty: int, sell_price: float, discount: float) -> float:
    """Revenue given up by discounting `qty` units by `discount` (0.10 = 10%)."""
    return qty * sell_price * discount


def margin_after(qty: int, sell_price: float, unit_cost: float, discount: float) -> float:
    """Profit on `qty` units after the discount. Negative means selling below cost."""
    return qty * (sell_price * (1 - discount) - unit_cost)


def capital_released(qty: int, unit_cost: float) -> float:
    """Cash tied up that comes back when `qty` units leave the shelf (returned or sold at cost)."""
    return max(qty, 0) * unit_cost


def cash_back(qty: int, sell_price: float, discount: float) -> float:
    """Cash received from selling `qty` units at the discounted price."""
    return qty * sell_price * (1 - discount)


def launch_risk(days_to_launch: int | None, days_of_stock_value: float | None) -> bool:
    """A known launch date has not passed and current stock would not sell through before it."""
    if days_to_launch is None or days_to_launch < 0:
        return False
    return days_of_stock_value is None or days_of_stock_value > days_to_launch
