"""Data-quality checks for an uploaded stock list. Pure functions over rows of raw strings.

Eleven codes. "error" rows are blocked (quarantined), "warning" rows are kept but flagged for a
look, "info" rows are kept. Row numbers are 1-based data rows (the header is not counted).
"""

from dataclasses import dataclass
from datetime import date, datetime

from app.core.thresholds import Thresholds

REQUIRED = ["sku", "brand", "model", "category", "qty_on_hand", "sell_price"]  # unit_cost has its own code
CODES = ("MISSING_REQUIRED", "MISSING_COST", "NEGATIVE_QTY", "DUPLICATE_SKU", "FUTURE_DATE", "PRICE_BELOW_COST",
         "BAD_CATEGORY", "SALE_BEFORE_RECEIVED", "VELOCITY_OUTLIER", "MSRP_MISMATCH", "ZERO_QTY")
SEVERITY = {"MISSING_REQUIRED": "error", "MISSING_COST": "error", "NEGATIVE_QTY": "error", "DUPLICATE_SKU": "error",
            "FUTURE_DATE": "error", "PRICE_BELOW_COST": "warning", "BAD_CATEGORY": "warning",
            "SALE_BEFORE_RECEIVED": "warning", "VELOCITY_OUTLIER": "warning", "MSRP_MISMATCH": "warning",
            "ZERO_QTY": "info"}

CATEGORY_SYNONYMS = {
    "tv": "tv", "tvs": "tv", "television": "tv", "televisions": "tv",
    "phone": "phone", "phones": "phone", "smartphone": "phone", "smartphones": "phone", "mobile": "phone",
    "laptop": "laptop", "laptops": "laptop", "notebook": "laptop", "notebooks": "laptop",
    "washer": "washer", "washers": "washer", "washing machine": "washer", "washing machines": "washer",
    "washer_dryer": "washer_dryer", "washer-dryer": "washer_dryer", "washer dryer": "washer_dryer",
    "washer/dryer": "washer_dryer", "combo": "washer_dryer",
    "refrigerator": "refrigerator", "refrigerators": "refrigerator", "fridge": "refrigerator", "fridges": "refrigerator",
}


@dataclass(frozen=True)
class Issue:
    row: int
    code: str
    severity: str


def parse_money(v) -> float | None:
    """"$1,299.00" -> 1299.0; empty or unreadable -> None."""
    if v is None:
        return None
    s = str(v).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_int(v) -> int | None:
    """"5" and "5.0" -> 5; "5.5", empty or unreadable -> None."""
    if v is None:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        f = float(s)
    except ValueError:
        return None
    return int(f) if f == int(f) else None


def parse_date(v) -> date | None | str:
    """ISO or MM/DD/YYYY. Returns None if empty and the string "BAD" if present but unreadable."""
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


def normalize_category(v) -> str | None:
    return CATEGORY_SYNONYMS.get(str(v or "").strip().lower())


def _velocity_fences(rows: list[dict], th: Thresholds) -> dict:
    by_cat: dict = {}
    for r in rows:
        c, u = normalize_category(r.get("category")), parse_int(r.get("units_sold_30d"))
        if c and u is not None:
            by_cat.setdefault(c, []).append(u)
    fences = {}
    for c, vals in by_cat.items():
        vals = sorted(vals)
        q1, q3 = vals[len(vals) // 4], vals[(3 * len(vals)) // 4]
        fences[c] = q3 + th.velocity_outlier_iqr_multiplier * (q3 - q1)
    return fences


def dq_check(rows: list[dict], as_of: date, th: Thresholds) -> list[Issue]:
    issues: list[Issue] = []
    seen: set = set()
    fences = _velocity_fences(rows, th)

    def add(i: int, code: str):
        issues.append(Issue(i, code, SEVERITY[code]))

    for i, r in enumerate(rows, start=1):
        for col in REQUIRED:
            v = r.get(col)
            if v is None or str(v).strip() == "":   # a quantity of 0 is a value, not a missing one
                add(i, "MISSING_REQUIRED")
            elif (col == "qty_on_hand" and parse_int(v) is None) or (col == "sell_price" and parse_money(v) is None):
                add(i, "MISSING_REQUIRED")          # present but not a readable number
        cost = parse_money(r.get("unit_cost"))
        if cost is None or cost <= 0:
            add(i, "MISSING_COST")
        qty = parse_int(r.get("qty_on_hand"))
        if qty is not None and qty < 0:
            add(i, "NEGATIVE_QTY")
        if qty == 0:
            add(i, "ZERO_QTY")
        key = str(r.get("sku", "") or "").strip().lower()
        if key:
            if key in seen:
                add(i, "DUPLICATE_SKU")
            seen.add(key)
        cat_text = str(r.get("category", "") or "").strip()
        cat = normalize_category(cat_text)
        if cat_text and cat is None:
            add(i, "BAD_CATEGORY")
        last_sale, received = parse_date(r.get("last_sale_date")), parse_date(r.get("received_date"))
        if any(isinstance(d, date) and d > as_of for d in (last_sale, received)):
            add(i, "FUTURE_DATE")
        if isinstance(last_sale, date) and isinstance(received, date) and last_sale < received:
            add(i, "SALE_BEFORE_RECEIVED")
        sell = parse_money(r.get("sell_price"))
        if cost and sell is not None and sell < cost:
            add(i, "PRICE_BELOW_COST")
        msrp = parse_money(r.get("msrp"))
        if sell is not None and msrp and sell > th.msrp_mismatch_factor * msrp:
            add(i, "MSRP_MISMATCH")
        u30 = parse_int(r.get("units_sold_30d"))
        if cat and u30 is not None and cat in fences and u30 > max(fences[cat], th.velocity_outlier_floor):
            add(i, "VELOCITY_OUTLIER")
    return issues


def split_by_severity(issues: list[Issue]) -> tuple[set, set, set]:
    """(rows with an error, rows with only warnings, rows with only info)."""
    err = {i.row for i in issues if i.severity == "error"}
    warn = {i.row for i in issues if i.severity == "warning"} - err
    info = {i.row for i in issues if i.severity == "info"} - err - warn
    return err, warn, info
