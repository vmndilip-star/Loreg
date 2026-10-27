"""Database helpers for the agent pipelines. Reads and writes only; nothing here calls a model."""

import json
import re
import sqlite3

from app.ingest import now_text

_CITATION_MARKS = re.compile(r"\s*\[[A-Za-z0-9:_\-.]+\]")


def known_skus(conn: sqlite3.Connection, shop_id: int) -> set[str]:
    return {r["sku"] for r in conn.execute("SELECT sku FROM skus WHERE shop_id = ?", (shop_id,))}


def item_row(conn: sqlite3.Connection, shop_id: int, sku: str):
    """One stocked item with its product, or None."""
    return conn.execute(
        "SELECT s.*, p.brand, p.model, p.category, p.line, p.line_label, p.launch_family FROM skus s JOIN products p ON p.id = s.product_id"
        " WHERE s.shop_id = ? AND s.sku = ?", (shop_id, sku)).fetchone()


def apply_agent_recommendation(conn: sqlite3.Connection, shop_id: int, alert_id: int, sku: str, option: str, discount_pct: int,
                               reasons: list[str], source: str) -> bool:
    """Replace the option and reasons on a plan that is still only proposed. A decided plan is never rewritten."""
    row = conn.execute("SELECT r.id, s.qty, s.sell_price_cents FROM recommendations r JOIN skus s ON s.sku = r.sku AND s.shop_id = r.shop_id"
                       " WHERE r.alert_id = ? AND r.sku = ? AND r.status = 'proposed'", (alert_id, sku)).fetchone()
    if row is None:
        return False
    clean = [_CITATION_MARKS.sub("", r).strip() for r in reasons if r.strip()]
    impact = round(row["qty"] * row["sell_price_cents"] * discount_pct / 100) if option == "discount" else 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE recommendations SET action = ?, discount_pct = ?, margin_impact_cents = ?, reasons_json = ?, source = ? WHERE id = ?",
                     (option, discount_pct, impact, json.dumps(clean), source, row["id"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return True


def save_brief(conn: sqlite3.Connection, shop_id: int, facts_hash: str, source: str, headline: str, bullets: list[str], citations: list[str]) -> int:
    clean = [_CITATION_MARKS.sub("", b).strip() for b in bullets]
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute("INSERT INTO daily_briefs (shop_id, generated_at, facts_hash, source, headline, bullets_json, citations_json)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (shop_id, now_text(), facts_hash, source, _CITATION_MARKS.sub("", headline).strip(), json.dumps(clean), json.dumps(citations)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return cur.lastrowid


def latest_brief(conn: sqlite3.Connection, shop_id: int) -> dict | None:
    r = conn.execute("SELECT * FROM daily_briefs WHERE shop_id = ? ORDER BY id DESC LIMIT 1", (shop_id,)).fetchone()
    if r is None:
        return None
    return {"id": r["id"], "generated_at": r["generated_at"], "source": r["source"], "headline": r["headline"],
            "bullets": json.loads(r["bullets_json"]), "citations": json.loads(r["citations_json"]), "facts_hash": r["facts_hash"]}
