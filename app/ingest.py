"""Upload pipeline: read a stock-list CSV, run the data checks, quarantine bad rows, commit the rest.

No model is involved. A row's line comes from matching brand and model against the catalog
(app.catalog); anything that does not match is "Unsorted" and is listed for the owner to look at.
Each upload replaces the shop's current stock; the history stays in inventory_snapshots and classifications.
"""

import csv
import io
import json
import sqlite3
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.catalog import (CATEGORY_LABEL, UNSORTED_LINE, UNSORTED_LINE_ID, Catalog, norm, search_text)
from app.core import metrics
from app.core.classify import AT_RISK, INSUFFICIENT, classify
from app.core.quality import SEVERITY, dq_check, normalize_category, parse_date, parse_int, split_by_severity
from app.core.thresholds import Thresholds
from app.watch.attach import NO_SOURCE_NOTE, attach_events_to_alerts

REQUIRED_COLUMNS = ["sku", "brand", "model", "category", "qty_on_hand", "unit_cost", "sell_price"]
MAX_BYTES = 5 * 1024 * 1024


class UploadError(ValueError):
    """The file cannot be read as a stock list at all."""


def now_text() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def to_cents(value) -> int | None:
    """"$1,299.00" -> 129900. Exact (decimal, not float). None if empty or unreadable."""
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return int((Decimal(s) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    except InvalidOperation:
        return None


def _as_date(raw):
    d = parse_date(raw)
    return d if isinstance(d, date) else None


def read_rows(raw: bytes) -> list[dict]:
    if len(raw) > MAX_BYTES:
        raise UploadError("file is larger than 5 MB")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise UploadError("file is not UTF-8 text") from None
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise UploadError("file is empty")
    missing = [c for c in REQUIRED_COLUMNS if c not in [f.strip() for f in reader.fieldnames]]
    if missing:
        raise UploadError(f"missing columns: {', '.join(missing)}")
    return [{(k or "").strip(): v for k, v in row.items()} for row in reader]


def commit_upload(conn: sqlite3.Connection, *, shop_id: int, filename: str, raw: bytes, as_of: date, th: Thresholds,
                  catalog: Catalog) -> dict:
    rows = read_rows(raw)
    if not rows:
        raise UploadError("file has a header but no rows")
    issues = dq_check(rows, as_of, th)
    err_rows, warn_rows, _info = split_by_severity(issues)
    codes_by_row: dict[int, set] = {}
    for i in issues:
        codes_by_row.setdefault(i.row, set()).add(i.code)

    ts = now_text()
    conn.execute("BEGIN IMMEDIATE")
    try:
        # a new upload replaces the shop's current stock (history stays in the snapshot tables)
        conn.execute("DELETE FROM recommendations WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM alerts WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM skus WHERE shop_id = ?", (shop_id,))
        conn.execute("DELETE FROM products WHERE shop_id = ?", (shop_id,))
        upload_id = conn.execute(
            "INSERT INTO uploads (shop_id, filename, uploaded_at, as_of, rule_version, rows, kept, blocked, look, zero_qty, unsorted)"
            " VALUES (?, ?, ?, ?, ?, 0, 0, 0, 0, 0, 0)",
            (shop_id, filename, ts, as_of.isoformat(), th.rule_version)).lastrowid

        unsorted_rows, kept, bad_date_rows, bad_map_rows = set(), 0, set(), set()
        for n, r in enumerate(rows, start=1):
            sku = str(r.get("sku") or "").strip()
            for code in sorted(codes_by_row.get(n, ())):
                conn.execute("INSERT INTO dq_issues (upload_id, row_number, sku, code, severity, quarantined) VALUES (?, ?, ?, ?, ?, ?)",
                             (upload_id, n, sku, code, SEVERITY[code], 1 if n in err_rows else 0))
            if n in err_rows:
                continue  # quarantined: never reaches the stock tables

            brand, model = str(r.get("brand") or "").strip(), str(r.get("model") or "").strip()
            hit = catalog.match(brand, model)
            category = normalize_category(r.get("category")) or (hit["category"] if hit else None)
            qty = parse_int(r.get("qty_on_hand"))
            cost, sell = to_cents(r.get("unit_cost")), to_cents(r.get("sell_price"))
            received, last_sale = _as_date(r.get("received_date")), _as_date(r.get("last_sale_date"))
            if parse_date(r.get("received_date")) == "BAD" or parse_date(r.get("last_sale_date")) == "BAD":
                bad_date_rows.add(n)
            rw = _as_date(r.get("return_window_end"))
            map_text = str(r.get("map_price") or "").strip()                 # optional column; blank means no minimum advertised price
            map_cents = to_cents(map_text) if map_text else None
            if map_text and (map_cents is None or map_cents <= 0):           # present but unreadable: ignored, and the row is flagged
                map_cents = None
                bad_map_rows.add(n)
            u30, u90 = parse_int(r.get("units_sold_30d")) or 0, parse_int(r.get("units_sold_90d")) or 0
            seasonal = 1 if str(r.get("is_seasonal") or "0").strip() in ("1", "true", "True", "yes") else 0

            if hit:
                prod = (hit["category"] if category is None else category, hit["line"], hit["line_id"], hit["line_slug"],
                        hit["line_label"], hit["year"], hit["variant"], int(hit["launch"]), 1)
            else:
                unsorted_rows.add(n)
                prod = (category or "unknown", UNSORTED_LINE, UNSORTED_LINE_ID, UNSORTED_LINE_ID, UNSORTED_LINE, None, model, 0, 0)
            cat, line, line_id, line_slug, line_label, year, variant, launch, matched = prod
            product_id = conn.execute(
                "INSERT INTO products (shop_id, sku, category, line, line_id, line_slug, line_label, year, brand, model, variant,"
                " search_text, launch_family, matched) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (shop_id, sku, cat, line, line_id, line_slug, line_label, year, brand, model, variant,
                 search_text(brand, model, line, variant, sku, cat), launch, matched)).lastrowid

            status = classify(qty, received, last_sale, u90, cat, bool(seasonal), as_of, th) if cat in th.categories else INSUFFICIENT
            dsls = metrics.days_since_last_sale(as_of, last_sale)
            capital = max(qty, 0) * cost
            reasons = [c for c in sorted(codes_by_row.get(n, ())) if SEVERITY[c] == "warning"]
            if n in unsorted_rows:
                reasons.append("UNSORTED_LINE")
            if n in bad_date_rows:
                reasons.append("UNREADABLE_DATE")
            if n in bad_map_rows:
                reasons.append("UNREADABLE_MAP")
            if cat not in th.categories:
                reasons.append("UNKNOWN_CATEGORY")
            conn.execute(
                "INSERT INTO skus (shop_id, product_id, sku, upload_id, qty, unit_cost_cents, sell_price_cents, msrp_cents, map_cents, last_sale_date,"
                " received_date, units_sold_30d, units_sold_90d, is_seasonal, supplier, return_window_end, status, rule_version, dsls,"
                " capital_cents, needs_look, look_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (shop_id, product_id, sku, upload_id, qty, cost, sell, to_cents(r.get("msrp")), map_cents,
                 last_sale.isoformat() if last_sale else None, received.isoformat() if received else None, u30, u90, seasonal,
                 (r.get("supplier") or "").strip() or None, rw.isoformat() if rw else None,
                 status, th.rule_version, dsls, capital, 1 if reasons else 0, ",".join(reasons) or None))
            conn.execute("INSERT INTO inventory_snapshots (upload_id, sku, qty, unit_cost_cents, sell_price_cents, status, capital_cents)"
                         " VALUES (?,?,?,?,?,?,?)", (upload_id, sku, qty, cost, sell, status, capital))
            conn.execute("INSERT INTO classifications (upload_id, sku, status, rule_version, dsls, classified_at) VALUES (?,?,?,?,?,?)",
                         (upload_id, sku, status, th.rule_version, dsls, ts))
            kept += 1

        look_rows = (warn_rows | unsorted_rows | bad_date_rows | bad_map_rows) - err_rows
        counts = dict(rows=len(rows), kept=kept, blocked=len(err_rows), look=len(look_rows),
                      zero_qty=len({i.row for i in issues if i.code == "ZERO_QTY" and i.row not in err_rows}),
                      unsorted=len(unsorted_rows))
        conn.execute("UPDATE uploads SET rows=?, kept=?, blocked=?, look=?, zero_qty=?, unsorted=? WHERE id=?",
                     (counts["rows"], counts["kept"], counts["blocked"], counts["look"], counts["zero_qty"], counts["unsorted"], upload_id))
        _refresh_launch_alerts(conn, shop_id, ts)
        conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id, detail_json) VALUES (?,?,?,?,?,?,?)",
                     (shop_id, ts, "owner", "upload", "upload", str(upload_id), json.dumps({"file": filename, **counts})))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"upload_id": upload_id, "as_of": as_of.isoformat(), **counts}


def _refresh_launch_alerts(conn: sqlite3.Connection, shop_id: int, ts: str) -> None:
    """One alert per brand and category whose launch-family stock is dead or slow.

    There is no dated news source behind these yet, and the alert says so: the launch-family flag comes
    from the catalog, not from an article.
    """
    placeholders = ",".join("?" for _ in AT_RISK)
    groups = conn.execute(
        f"SELECT p.brand AS brand, p.category AS category, COUNT(*) AS n, SUM(s.capital_cents) AS cap FROM skus s"
        f" JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? AND p.launch_family = 1 AND s.qty > 0"
        f" AND s.status IN ({placeholders}) GROUP BY p.brand, p.category ORDER BY cap DESC, p.brand",
        (shop_id, *AT_RISK)).fetchall()
    for g in groups:
        conn.execute(
            "INSERT INTO alerts (shop_id, kind, title, brand, category, source_note, n_items, capital_cents, created_at)"
            " VALUES (?,?,?,?,?,?,?,?,?)",
            (shop_id, "launch_family", f"{g['brand']} {CATEGORY_LABEL.get(g['category'], g['category'])} may be affected by a newer line",
             g["brand"], g["category"], NO_SOURCE_NOTE, g["n"], g["cap"], ts))
    attach_events_to_alerts(conn, shop_id)  # a stored, dated event may already match; otherwise the note above stays
