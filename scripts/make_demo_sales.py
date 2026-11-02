"""Write the demo's SYNTHETIC sales history, data/demo/sales_history_demo.csv, from the demo stock list. Every sale in it is made up.

It agrees with the stock list it is made from, for every item the stock list keeps:
  - the sales in the last 30 days and the last 90 days (counted back from the stock date, 2026-10-26) add up to the list's
    units_sold_30d and units_sold_90d;
  - the last sale is on the list's last_sale_date, and an item the list says never sold has no sale;
  - nothing is dated after the stock date, or before 2024-11-01 (or June 1 of the line's release year, if later).
Before the last 90 days, an item sells at its own 90-day pace (one that sold nothing in 90 days: one unit about every 20 days, until its
last sale), with more in November and December. A fixed seed: running it again writes the same file.

Usage: python scripts/make_demo_sales.py [--out data/demo/sales_history_demo.csv]
"""

import argparse
import csv
import math
import random
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.catalog import load_catalog  # noqa: E402
from app.demo import DEMO_FILE  # noqa: E402

AS_OF = date(2026, 10, 26)
START = date(2024, 11, 1)
SEED = 2026
OUT = ROOT / "data" / "demo" / "sales_history_demo.csv"
SEASON = {11: 1.6, 12: 1.4, 1: 0.8, 2: 0.8}      # assumption: holiday months sell more, the months after sell less; the rest 1.0
DEAD_PACE = 1 / 20                                 # units a day before the last sale, for an item that sold nothing in 90 days


def _day(text: str) -> date | None:
    return date.fromisoformat(text) if text else None


def _poisson(rng: random.Random, lam: float) -> int:
    """Knuth's method: fine for the small daily rates here."""
    limit, k, p = math.exp(-lam), 0, 1.0
    while True:
        p *= rng.random()
        if p <= limit:
            return k
        k += 1


def _spread(rng: random.Random, units: int, first: date, last: date, sales: dict) -> None:
    """Put `units` single sales on days drawn evenly from first..last (both included)."""
    span = (last - first).days
    for _ in range(units):
        d = first + timedelta(days=rng.randint(0, span))
        sales[d] = sales.get(d, 0) + 1


def item_sales(sku: str, u30: int, u90: int, last: date | None, start: date) -> dict:
    """{date: units} for one item, meeting every rule in the module docstring."""
    rng = random.Random(f"{SEED}:{sku}")
    sales: dict = {}
    if last is None:
        return sales
    a_first, b_first, b_last = AS_OF - timedelta(days=29), AS_OF - timedelta(days=89), AS_OF - timedelta(days=30)
    sales[last] = 1                                                  # the list's last sale, whichever window it falls in
    if last >= a_first:                                              # sold in the last 30 days
        _spread(rng, u30 - 1, a_first, last, sales)
        _spread(rng, u90 - u30, b_first, b_last, sales)
    elif last >= b_first:                                            # last sold 30 to 89 days ago
        _spread(rng, u90 - 1, b_first, last, sales)
    before_last = (last if last < b_first else b_first) - timedelta(days=1)   # the history before the 90 days ends before the last sale
    start = min(start, last)
    pace = u90 / 90 if u90 else DEAD_PACE
    d = start
    while d <= before_last:
        n = _poisson(rng, pace * SEASON.get(d.month, 1.0))
        if n:
            sales[d] = sales.get(d, 0) + n
        d += timedelta(days=1)
    return sales


def build(stock_path: Path = DEMO_FILE) -> list[list[str]]:
    catalog = load_catalog()
    seen, out = set(), []
    with open(stock_path, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            sku = r["sku"].strip()
            if sku.lower() in seen or not r["unit_cost"].strip() or int(float(r["qty_on_hand"])) < 0:
                continue                                             # the rows the stock upload blocks: no history for them either
            seen.add(sku.lower())
            hit = catalog.match(r["brand"], r["model"])
            start = max(START, date(hit["year"], 6, 1)) if hit and hit["year"] else START
            price = r["sell_price"].strip()
            sales = item_sales(sku, int(r["units_sold_30d"] or 0), int(r["units_sold_90d"] or 0), _day(r["last_sale_date"]), start)
            out.extend([sku, d.isoformat(), str(n), price, "sale"] for d, n in sorted(sales.items()))
    out.sort(key=lambda row: (row[1], row[0]))
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--out", type=Path, default=OUT)
    args = p.parse_args()
    rows = build()
    with open(args.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["sku", "date", "qty", "unit_price", "type"])
        w.writerows(rows)
    print(f"Wrote {len(rows)} rows ({sum(int(r[2]) for r in rows)} units, {rows[0][1]} to {rows[-1][1]}) to {args.out}. SYNTHETIC.")


if __name__ == "__main__":
    main()
