"""Stock-health classifier: dead, slow, healthy, new or insufficient_data. Pure function.

Rules (all thresholds come from config/thresholds.yaml, never from this file):
  qty <= 0                                   healthy   (nothing on the shelf, excluded from risk)
  never sold:  received date unknown         insufficient_data
               received < grace days ago     new
               received >= dead threshold    dead
               otherwise                     insufficient_data
  sold:        received < grace days ago     new
               days since last sale >= dead  dead
               >= slow, or days of stock > slow limit   slow
               otherwise                     healthy
"""

from datetime import date

from app.core.metrics import age_days, days_of_stock, days_since_last_sale
from app.core.thresholds import Thresholds

DEAD, SLOW, HEALTHY, NEW, INSUFFICIENT = "dead", "slow", "healthy", "new", "insufficient_data"
STATUSES = (DEAD, SLOW, HEALTHY, NEW, INSUFFICIENT)
AT_RISK = (DEAD, SLOW)


def classify(qty: int, received: date | None, last_sale: date | None, units_sold_90d: int | None,
             category: str, seasonal: bool, as_of: date, th: Thresholds) -> str:
    t = th.for_category(category, bool(seasonal))
    if qty <= 0:
        return HEALTHY
    age = age_days(as_of, received)
    dsls = days_since_last_sale(as_of, last_sale)
    if dsls is None:
        if age is None:
            return INSUFFICIENT
        if age < t.new_grace_days:
            return NEW
        if age >= t.dead_dsls:
            return DEAD
        return INSUFFICIENT
    if age is not None and age < t.new_grace_days:
        return NEW
    if dsls >= t.dead_dsls:
        return DEAD
    dos = days_of_stock(qty, units_sold_90d)  # None means no sales in 90 days: infinite days of stock
    if dsls >= t.slow_dsls or dos is None or dos > t.slow_days_of_stock:
        return SLOW
    return HEALTHY
