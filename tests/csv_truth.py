"""Independent recomputation of every number the API serves, from the CSV files only.

Deliberately imports nothing from `app`: plain csv, yaml and arithmetic, written separately from app/core so that a
bug in the app's rules cannot also hide in the check. Money is exact: Decimal to integer cents.
"""

import csv
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
CATEGORY_LABEL = {"tv": "TVs", "phone": "Phones", "laptop": "Laptops", "washer": "Washers",
                  "washer_dryer": "Washer-dryers", "refrigerator": "Refrigerators"}


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cents(text):
    return int((Decimal(text.replace("$", "").replace(",", "")) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def day(text):
    return date.fromisoformat(text) if text else None


def classify(row, as_of, cfg):
    """dead / slow / healthy / new / insufficient_data, from the written rules (see config/thresholds.yaml)."""
    t = cfg["categories"][row["category"]]
    k = cfg["seasonal_multiplier"] if row["is_seasonal"] == "1" else 1
    slow_d, dead_d, slow_dos, grace = t["slow_dsls"] * k, t["dead_dsls"] * k, t["slow_days_of_stock"] * k, t["new_grace_days"]
    qty = int(row["qty_on_hand"])
    if qty <= 0:
        return "healthy"
    received, last = day(row["received_date"]), day(row["last_sale_date"])
    age = None if received is None else (as_of - received).days
    since = None if last is None else (as_of - last).days
    if since is None:
        if age is None:
            return "insufficient_data"
        if age < grace:
            return "new"
        return "dead" if age >= dead_d else "insufficient_data"
    if age is not None and age < grace:
        return "new"
    if since >= dead_d:
        return "dead"
    u90 = int(row["units_sold_90d"] or 0)
    too_much = u90 == 0 or qty * 90 / u90 > slow_dos
    return "slow" if (since >= slow_d or too_much) else "healthy"


class Truth:
    def __init__(self, folder, as_of="2026-10-26", master="skus_master.csv"):
        self.as_of = date.fromisoformat(as_of)
        self.cfg = yaml.safe_load((REPO / "config" / "thresholds.yaml").read_text(encoding="utf-8"))
        launch = {r["sku"]: r["launch_family"] == "1" for r in read_csv(Path(folder) / "ground_truth.csv")}
        self.items = []
        seen = set()
        for r in read_csv(Path(folder) / master):
            if r["sku"].strip().lower() in seen or not r["unit_cost"] or int(float(r["qty_on_hand"])) < 0:
                continue  # rows the data checks quarantine
            seen.add(r["sku"].strip().lower())
            qty, cost, sell = int(float(r["qty_on_hand"])), cents(r["unit_cost"]), cents(r["sell_price"])
            last = day(r["last_sale_date"])
            self.items.append(dict(
                sku=r["sku"], name=f"{r['brand']} {r['model']}", category=r["category"], qty=qty, cost=cost, sell=sell,
                dsls=None if last is None else (self.as_of - last).days, status=classify(r, self.as_of, self.cfg),
                cap=qty * cost, launch=launch.get(r["sku"], False)))

    def by_status(self, *statuses):
        return [i for i in self.items if i["status"] in statuses and i["qty"] > 0]

    def totals(self):
        def blk(*s):
            sel = self.by_status(*s)
            return {"n": len(sel), "cap_cents": sum(i["cap"] for i in sel)}
        return {"skus": len(self.items), "stock_at_cost_cents": sum(i["cap"] for i in self.items), "dead": blk("dead"), "slow": blk("slow"),
                "at_risk": blk("dead", "slow"), "new": blk("new"), "insufficient": blk("insufficient_data"),
                "zero_qty": sum(1 for i in self.items if i["qty"] == 0)}

    def risk_by_category(self):
        agg = {}
        for i in self.by_status("dead", "slow"):
            a = agg.setdefault(i["category"], [0, 0])
            a[0] += 1
            a[1] += i["cap"]
        return sorted(((k, v[0], v[1]) for k, v in agg.items()), key=lambda x: (-x[2], x[0]))

    def top(self, n):
        return sorted(self.by_status("dead", "slow"), key=lambda i: (-i["cap"], i["sku"]))[:n]

    def launch_exposed(self):
        return sorted((i for i in self.by_status("dead", "slow") if i["launch"]), key=lambda i: (-i["cap"], i["sku"]))
