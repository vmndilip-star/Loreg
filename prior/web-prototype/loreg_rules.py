"""Deterministic Loreg rules shared by the generator and the validator.

Pure functions, no I/O. This is the seed of app/core/: classification + data-quality checks.
Thresholds are tunable ASSUMPTIONS (derived from a ~48-day sector inventory benchmark), not facts.
"""
from __future__ import annotations
from datetime import date, datetime
import math

# category -> (slow_dsls, dead_dsls, slow_days_of_stock, new_item_grace_days)
TH = {
    "tv": (60, 120, 120, 30), "phone": (45, 90, 90, 21), "laptop": (60, 120, 120, 30),
    "tablet": (60, 120, 120, 30), "washer": (75, 150, 150, 45), "washer_dryer": (75, 150, 150, 45),
    "refrigerator": (75, 150, 150, 45), "accessory": (90, 180, 180, 30),
}
SEASONAL_MULT = 1.5  # applies to slow_dsls, dead_dsls, slow_dos (not grace)

DEPT = {
    "tv": "Displays", "phone": "Phones & Tablets", "tablet": "Phones & Tablets", "laptop": "Computing",
    "washer": "Large Appliances", "washer_dryer": "Large Appliances", "refrigerator": "Large Appliances",
    "accessory": "Accessories",
}


def classify(qty, received, last_sale, units_sold_90d, category, seasonal, as_of):
    """Return dead|slow|healthy|new|insufficient_data. qty<=0 is 'healthy' (excluded from risk)."""
    slow_d, dead_d, slow_dos, grace = TH[category]
    if seasonal:
        slow_d, dead_d, slow_dos = slow_d * SEASONAL_MULT, dead_d * SEASONAL_MULT, slow_dos * SEASONAL_MULT
    if qty <= 0:
        return "healthy"
    age = None if received is None else (as_of - received).days
    dsls = None if last_sale is None else (as_of - last_sale).days
    if dsls is None:
        if age is None:
            return "insufficient_data"
        if age < grace:
            return "new"
        if age >= dead_d:
            return "dead"
        return "insufficient_data"
    if age is not None and age < grace:
        return "new"
    if dsls >= dead_d:
        return "dead"
    v90 = (units_sold_90d or 0) / 90.0
    dos = qty / v90 if v90 > 0 else math.inf
    if dsls >= slow_d or dos > slow_dos:
        return "slow"
    return "healthy"


# ----------------------------------------------------------------------------- data-quality checks
CATEGORY_SYNONYMS = {
    "tv": "tv", "tvs": "tv", "television": "tv", "televisions": "tv",
    "phone": "phone", "phones": "phone", "smartphone": "phone", "mobile": "phone",
    "laptop": "laptop", "laptops": "laptop", "notebook": "laptop",
    "tablet": "tablet", "tablets": "tablet", "ipad": "tablet",
    "washer": "washer", "washing machine": "washer", "washing machines": "washer",
    "washer_dryer": "washer_dryer", "washer-dryer": "washer_dryer", "washer dryer": "washer_dryer", "combo": "washer_dryer",
    "refrigerator": "refrigerator", "fridge": "refrigerator", "refrigerators": "refrigerator",
    "accessory": "accessory", "accessories": "accessory",
}
MIN_OUTLIER_UNITS_30D = 30  # absolute floor: IQR fences alone misfire on sparse integer counts
REQUIRED = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price"]


def _money(v):
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if s == "":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int(v):
    if v is None:
        return None
    s = str(v).strip()
    if s == "":
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f == int(f) else None


def parse_date(v):
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return "BAD"


def dq_check(rows, as_of):
    """rows: list of dicts (raw CSV strings). Returns list of (row_num, code, severity). row_num is 1-based data row."""
    issues = []
    seen = {}
    # per-category IQR on units_sold_30d for outliers
    by_cat = {}
    for r in rows:
        c = CATEGORY_SYNONYMS.get(str(r.get("category", "")).strip().lower())
        u = _int(r.get("units_sold_30d"))
        if c and u is not None:
            by_cat.setdefault(c, []).append(u)
    fences = {}
    for c, vals in by_cat.items():
        vals = sorted(vals)
        q1, q3 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
        fences[c] = q3 + 3 * (q3 - q1)
    for i, r in enumerate(rows, start=1):
        sku = str(r.get("sku", "")).strip()
        for col in REQUIRED:
            if col in ("unit_cost",):
                continue
            if str(r.get(col, "")).strip() == "":
                issues.append((i, "MISSING_REQUIRED", "error"))
        cost = _money(r.get("unit_cost"))
        if cost is None or cost <= 0:
            issues.append((i, "MISSING_COST", "error"))
        qty = _int(r.get("qty_on_hand"))
        if qty is not None and qty < 0:
            issues.append((i, "NEGATIVE_QTY", "error"))
        if qty == 0:
            issues.append((i, "ZERO_QTY", "info"))
        key = sku.lower()
        if key:
            if key in seen:
                issues.append((i, "DUPLICATE_SKU", "error"))
            seen[key] = i
        cat = CATEGORY_SYNONYMS.get(str(r.get("category", "")).strip().lower())
        if str(r.get("category", "")).strip() and cat is None:
            issues.append((i, "BAD_CATEGORY", "warning"))
        ls, rc = parse_date(r.get("last_sale_date")), parse_date(r.get("received_date"))
        for d in (ls, rc):
            if isinstance(d, date) and d > as_of:
                issues.append((i, "FUTURE_DATE", "error"))
                break
        if isinstance(ls, date) and isinstance(rc, date) and ls < rc:
            issues.append((i, "SALE_BEFORE_RECEIVED", "warning"))
        sell = _money(r.get("sell_price"))
        if cost and sell is not None and sell < cost:
            issues.append((i, "PRICE_BELOW_COST", "warning"))
        msrp = _money(r.get("msrp"))
        if sell is not None and msrp and sell > 1.2 * msrp:
            issues.append((i, "MSRP_MISMATCH", "warning"))
        u30 = _int(r.get("units_sold_30d"))
        if cat and u30 is not None and cat in fences and u30 > max(fences[cat], MIN_OUTLIER_UNITS_30D):
            issues.append((i, "VELOCITY_OUTLIER", "warning"))
    return issues
