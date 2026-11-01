"""Sales history upload: dated sales (and returns) per SKU, so the Analyze page can show a day, a week, a month and a year. No model.

Columns: sku, date, qty, unit_price, and optionally type (sale or return; blank means sale). One row is one or more units of one SKU
on one day; several rows for the same SKU and day are added up. Each upload replaces the shop's sales history.

The history is read against the stock list's date: a row dated after it is blocked, because the stock list and the sales history must
describe the same moment (upload a newer stock list first). A SKU that is not in the current stock list is kept; it shows once the
item is in a stock list. A file in which no row can be used is refused, and the history already stored stays as it was.
"""

import json
import re
import sqlite3
from datetime import date

from app.core.quality import parse_date
from app.ingest import UploadError, now_text, read_table, to_cents

REQUIRED = ["sku", "date", "qty", "unit_price"]
TYPES = ("sale", "return")
MAX_ROWS = 250_000
MAX_QTY = 100_000
SHOWN_ISSUES = 20
_WHOLE = re.compile(r"\d{1,6}")


class SalesError(ValueError):
    """The file cannot be used as a sales history; nothing was stored."""


class NoStockList(SalesError):
    """There is no stock list yet to read the sales history against."""


def _problems(r: dict, as_of: date) -> tuple[list[str], tuple | None]:
    sku = str(r.get("sku") or "").strip()
    out = []
    if not sku or len(sku) > 64:
        out.append("The SKU is missing or longer than 64 characters.")
    d = parse_date(r.get("date"))
    if not isinstance(d, date):
        out.append("The date is missing or unreadable (use YYYY-MM-DD).")
    elif d > as_of:
        out.append(f"Dated after the stock list's date ({as_of.isoformat()}). Upload a newer stock list first.")
    q = str(r.get("qty") or "").strip()
    qty = int(q) if _WHOLE.fullmatch(q) else 0
    if not 1 <= qty <= MAX_QTY:
        out.append(f"The quantity must be a whole number from 1 to {MAX_QTY:,}.")
    price = to_cents(r.get("unit_price"))
    if price is None or price < 0:
        out.append("The unit price is missing, unreadable or negative.")
    kind = str(r.get("type") or "").strip().lower() or "sale"
    if kind not in TYPES:
        out.append("The type must be sale or return.")
    return out, None if out else (sku, d.isoformat(), qty, price, kind)


def commit_sales(conn: sqlite3.Connection, *, shop_id: int, raw: bytes, filename: str, synthetic: bool = False) -> dict:
    """Check every row, then replace the shop's sales history with the rows that can be used. Raises SalesError when nothing can be."""
    up = conn.execute("SELECT as_of FROM uploads WHERE shop_id = ? ORDER BY id DESC LIMIT 1", (shop_id,)).fetchone()
    if up is None:
        raise NoStockList("Upload a stock list first: the sales history is read against its date.")
    as_of = date.fromisoformat(up["as_of"])
    try:
        headers, rows = read_table(raw)
    except UploadError as e:
        raise SalesError(str(e)) from None
    missing = [c for c in REQUIRED if c not in headers]
    if missing:
        raise SalesError(f"missing columns: {', '.join(missing)} (a sales history needs sku, date, qty and unit_price; type is optional)")
    if not rows:
        raise SalesError("file has a header but no rows")
    if len(rows) > MAX_ROWS:
        raise SalesError(f"a sales history is limited to {MAX_ROWS:,} rows; this file has {len(rows):,}")
    good, issues = [], []
    for n, r in enumerate(rows, start=1):
        problems, row = _problems(r, as_of)
        if problems:
            issues.append({"row": n, "sku": str(r.get("sku") or "").strip()[:64], "problems": problems})
        else:
            good.append(row)
    if not good:
        shown = "; ".join(f"row {i['row']}: {i['problems'][0]}" for i in issues[:3])
        raise SalesError(f"none of the {len(rows):,} rows can be used, so nothing was stored ({shown})")
    stocked = {r["sku"] for r in conn.execute("SELECT sku FROM products WHERE shop_id = ?", (shop_id,))}
    skus = {g[0] for g in good}
    counts = {"rows": len(rows), "kept": len(good), "blocked": len(issues), "skus": len(skus), "not_in_stock_list": len(skus - stocked),
              "first_date": min(g[1] for g in good), "last_date": max(g[1] for g in good), "as_of": as_of.isoformat(), "synthetic": synthetic}
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM sales_events WHERE shop_id = ?", (shop_id,))
        conn.executemany("INSERT INTO sales_events (shop_id, sku, event_date, qty, unit_price_cents, event_type) VALUES (?, ?, ?, ?, ?, ?)",
                         [(shop_id, *g) for g in good])
        conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id, detail_json) VALUES (?,?,?,?,?,?,?)",
                     (shop_id, now_text(), "owner", "sales_upload", "sales_history", filename[:200], json.dumps({"file": filename[:200], **counts})))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {**counts, "file": filename[:200], "issues": issues[:SHOWN_ISSUES]}


def latest_sales_upload(conn: sqlite3.Connection, shop_id: int) -> dict | None:
    """What the stored history came from: the file name, its dates and whether it is the synthetic demo history."""
    row = conn.execute("SELECT detail_json FROM audit_log WHERE shop_id = ? AND action = 'sales_upload' ORDER BY id DESC LIMIT 1",
                       (shop_id,)).fetchone()
    return json.loads(row["detail_json"]) if row else None
