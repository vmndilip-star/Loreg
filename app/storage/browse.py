"""Browse and search the shop's stock. Parameterized SQL only: every value is a bound parameter, and the only text spliced into a
query is a fixed fragment chosen from a whitelist in this file (never anything the caller typed). No model call.

Counting rules (the same as /api/summary): "items" counts every kept row, including ones with nothing on the shelf;
"at risk" means dead or slow with something on the shelf. Money is integer cents.
"""

import sqlite3

from app.catalog import CATEGORY_LABEL, norm
from app.core.classify import AT_RISK
from app.core.explain import why_flagged
from app.core.thresholds import Thresholds

STATUS_FILTERS = ("all", "dead", "slow", "healthy", "new")
# Each sort is a fixed fragment; the key the caller sends only selects one of these. Every sort ends with the SKU so pages never overlap.
SORTS = {
    "tied_up": "s.capital_cents DESC, s.sku",
    "name": "LOWER(p.brand || ' ' || p.model) ASC, s.sku",
    "qty": "s.qty DESC, s.sku",
    "days_since_sold": "(s.dsls IS NULL) DESC, s.dsls DESC, s.sku",       # never sold counts as the longest
}
DEFAULT_SORT, DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE = "tied_up", 25, 100
MIN_QUERY, MAX_QUERY = 2, 100
UNSORTED_KEY = "unknown"
RISK = ",".join("?" for _ in AT_RISK)
AT_RISK_CASE = f"(s.qty > 0 AND s.status IN ({RISK}))"


class BadRequest(ValueError):
    """The caller sent something outside the allowed values; the route turns this into a 422."""


def category_label(key: str) -> str:
    return CATEGORY_LABEL.get(key, "Unsorted")


def clean_query(q: str | None, *, required: bool = False) -> list[str]:
    """Trim, check length, split into lowercase tokens. A % or _ or quote is just text: it is escaped when matched."""
    text = norm(q)
    if not text:
        if required:
            raise BadRequest(f"type at least {MIN_QUERY} characters")
        return []
    if len(text) < MIN_QUERY:
        raise BadRequest(f"type at least {MIN_QUERY} characters")
    if len(text) > MAX_QUERY:
        raise BadRequest(f"search text is limited to {MAX_QUERY} characters")
    return text.split(" ")


def _escape(text: str) -> str:
    """Make text safe to put in a LIKE pattern: backslash, % and _ become plain characters (the query names the backslash as its escape)."""
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like(token: str) -> str:
    return "%" + _escape(token) + "%"


def _prefix_like(text: str) -> str:
    return _escape(text) + "%"


def _why(status: str, qty: int, dsls: int | None, category: str, th: Thresholds) -> str:
    if qty <= 0:
        return "Nothing on the shelf"
    if status in AT_RISK:
        slow_d = th.categories[category].slow_dsls if category in th.categories else 0
        return why_flagged(status, dsls, slow_d)
    return {"new": "Received recently", "healthy": "Selling at a normal pace"}.get(status, "Not enough sales history")


def _row(r, th: Thresholds) -> dict:
    return {"sku": r["sku"], "name": f"{r['brand']} {r['model']}".strip(), "variant": r["variant"], "category": r["category"],
            "category_label": category_label(r["category"]), "line": r["line_label"], "line_slug": r["line_slug"], "qty": r["qty"],
            "days_since_sold": r["dsls"], "status": r["status"], "why": _why(r["status"], r["qty"], r["dsls"], r["category"], th),
            "tied_up_cents": r["capital_cents"], "sell_price_cents": r["sell_price_cents"]}


ROW_COLUMNS = ("s.sku, s.qty, s.dsls, s.status, s.capital_cents, s.sell_price_cents, "
               "p.brand, p.model, p.category, p.variant, p.line_label, p.line_slug")


# ------------------------------------------------------------------------------------------------ categories
def categories(conn: sqlite3.Connection, shop_id: int) -> list[dict]:
    rows = conn.execute(
        f"SELECT p.category AS key, COUNT(*) AS items, COALESCE(SUM(s.capital_cents), 0) AS stock_cap,"
        f" COALESCE(SUM(CASE WHEN s.qty > 0 AND s.status = ? THEN 1 ELSE 0 END), 0) AS dead_n,"
        f" COALESCE(SUM(CASE WHEN s.qty > 0 AND s.status = ? THEN 1 ELSE 0 END), 0) AS slow_n,"
        f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN 1 ELSE 0 END), 0) AS at_risk_n,"
        f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN s.capital_cents ELSE 0 END), 0) AS at_risk_cap"
        f" FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? GROUP BY p.category"
        f" ORDER BY at_risk_cap DESC, key",
        ("dead", "slow", *AT_RISK, *AT_RISK, shop_id)).fetchall()
    biggest = max((r["at_risk_cap"] for r in rows), default=0)
    return [{"key": r["key"], "label": category_label(r["key"]), "items": r["items"], "stock_cap_cents": r["stock_cap"],
             "dead_n": r["dead_n"], "slow_n": r["slow_n"], "at_risk_n": r["at_risk_n"], "at_risk_cap_cents": r["at_risk_cap"],
             "bar_pct": round(100 * r["at_risk_cap"] / biggest) if biggest else 0} for r in rows]       # bar length, worked out here so the page never divides money


def known_categories(conn: sqlite3.Connection, shop_id: int) -> set[str]:
    return {r["category"] for r in conn.execute("SELECT DISTINCT p.category FROM products p WHERE p.shop_id = ?", (shop_id,))}


# ------------------------------------------------------------------------------------------------ products
def _where(shop_id: int, category: str | None, status: str | None, tokens: list[str]) -> tuple[str, list]:
    parts, params = ["s.shop_id = ?"], [shop_id]
    if category:
        parts.append("p.category = ?")
        params.append(category)
    if status and status != "all":
        parts.append("s.status = ?")
        params.append(status)
    for t in tokens:
        parts.append("p.search_text LIKE ? ESCAPE '\\'")
        params.append(_like(t))
    return " AND ".join(parts), params


def products(conn: sqlite3.Connection, shop_id: int, th: Thresholds, *, category: str | None = None, status: str = "all", q: str | None = None,
             sort: str = DEFAULT_SORT, page: int = 1, page_size: int = DEFAULT_PAGE_SIZE) -> dict:
    if status not in STATUS_FILTERS:
        raise BadRequest(f"status must be one of {', '.join(STATUS_FILTERS)}")
    if sort not in SORTS:
        raise BadRequest(f"sort must be one of {', '.join(SORTS)}")
    if page < 1 or not 1 <= page_size <= MAX_PAGE_SIZE:
        raise BadRequest(f"page must be 1 or more and page_size between 1 and {MAX_PAGE_SIZE}")
    tokens = clean_query(q)
    where, params = _where(shop_id, category, status, tokens)
    base = "FROM skus s JOIN products p ON p.id = s.product_id"
    total = conn.execute(
        f"SELECT COUNT(*) AS items, COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN 1 ELSE 0 END), 0) AS at_risk_n,"
        f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN s.capital_cents ELSE 0 END), 0) AS at_risk_cap {base} WHERE {where}",
        (*AT_RISK, *AT_RISK, *params)).fetchone()
    # how many rows each status chip would show with the same category and search (the status filter itself left out)
    free_where, free_params = _where(shop_id, category, None, tokens)
    counts = {r["status"]: r["n"] for r in conn.execute(
        f"SELECT s.status AS status, COUNT(*) AS n {base} WHERE {free_where} GROUP BY s.status", free_params)}
    status_counts = {"all": sum(counts.values()), **{k: counts.get(k, 0) for k in STATUS_FILTERS if k != "all"}}
    rows = conn.execute(
        f"SELECT {ROW_COLUMNS} {base} WHERE {where} ORDER BY {SORTS[sort]} LIMIT ? OFFSET ?",
        (*params, page_size, (page - 1) * page_size)).fetchall()
    return {"rows": [_row(r, th) for r in rows],
            "totals": {"items": total["items"], "at_risk_n": total["at_risk_n"], "at_risk_cap_cents": total["at_risk_cap"], "status_counts": status_counts},
            "pagination": {"page": page, "page_size": page_size, "total": total["items"]},
            "filter": {"category": category, "status": status, "q": " ".join(tokens), "sort": sort}}


# ------------------------------------------------------------------------------------------------ search
def search(conn: sqlite3.Connection, shop_id: int, th: Thresholds, q: str | None) -> dict:
    tokens = clean_query(q, required=True)
    text = " ".join(tokens)
    where, params = _where(shop_id, None, None, tokens)
    items = conn.execute(
        f"SELECT {ROW_COLUMNS} FROM skus s JOIN products p ON p.id = s.product_id WHERE {where}"
        f" ORDER BY CASE WHEN LOWER(s.sku) = ? THEN 0 WHEN LOWER(p.brand || ' ' || p.model) LIKE ? ESCAPE '\\' THEN 1 ELSE 2 END,"
        f" s.capital_cents DESC, s.sku LIMIT 20",
        (*params, text, _prefix_like(text))).fetchall()
    lines = []
    for r in conn.execute(
            f"SELECT p.line_id, p.line_slug, p.line_label, p.brand, p.category, COUNT(*) AS items,"
            f" COALESCE(SUM(s.capital_cents), 0) AS stock_cap,"
            f" COALESCE(SUM(CASE WHEN {AT_RISK_CASE} THEN s.capital_cents ELSE 0 END), 0) AS at_risk_cap"
            f" FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? GROUP BY p.line_id, p.line_slug, p.line_label, p.brand, p.category",
            (*AT_RISK, shop_id)):
        haystack = norm(f"{r['brand']} {r['line_label']} {category_label(r['category'])}")
        if all(t in haystack for t in tokens):
            rank = 0 if norm(f"{r['brand']} {r['line_label']}").startswith(text) or norm(r["line_label"]).startswith(text) else 1
            lines.append((rank, -r["at_risk_cap"], r["line_label"], r))
    lines.sort(key=lambda x: x[:3])
    return {"query": text,
            "lines": [{"line": r["line_label"], "line_slug": r["line_slug"], "brand": r["brand"], "category": r["category"],
                       "category_label": category_label(r["category"]), "items": r["items"], "stock_cap_cents": r["stock_cap"],
                       "at_risk_cap_cents": r["at_risk_cap"]} for _, _, _, r in lines[:5]],
            "items": [_row(r, th) for r in items]}
