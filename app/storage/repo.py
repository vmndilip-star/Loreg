"""Queries and writes for the numbers API. Parameterized SQL only. No model call; every figure comes from stored cents."""

import json
import sqlite3
from datetime import date

from app.catalog import CATEGORY_LABEL
from app.core import actions, metrics
from app.core.classify import AT_RISK
from app.core.explain import DQ_TEXT, money, plan_reasons, why_flagged
from app.core.quality import CODES, SEVERITY
from app.core.thresholds import Thresholds
from app.ingest import now_text

RISK_MARKS = ",".join("?" for _ in AT_RISK)


class NotFound(LookupError):
    pass


class Conflict(RuntimeError):
    pass


def latest_upload(conn: sqlite3.Connection, shop_id: int):
    return conn.execute("SELECT * FROM uploads WHERE shop_id = ? ORDER BY id DESC LIMIT 1", (shop_id,)).fetchone()


def _name(row) -> str:
    return f"{row['brand']} {row['model']}".strip()


# ---------------------------------------------------------------------------------------- money at risk
def money_at_risk(conn: sqlite3.Connection, shop_id: int) -> dict:
    """At-risk capital, minus what the owner has accepted a plan for. Integer cents."""
    at_risk = conn.execute(
        f"SELECT COALESCE(SUM(capital_cents),0) AS c, COUNT(*) AS n FROM skus WHERE shop_id = ? AND qty > 0 AND status IN ({RISK_MARKS})",
        (shop_id, *AT_RISK)).fetchone()
    accepted = conn.execute(
        "SELECT COALESCE(SUM(capital_cents),0) AS c, COUNT(*) AS n FROM recommendations WHERE shop_id = ? AND status = 'accepted'",
        (shop_id,)).fetchone()
    return {"at_risk_cents": at_risk["c"], "at_risk_n": at_risk["n"], "accepted_cents": accepted["c"], "accepted_n": accepted["n"],
            "money_at_risk_cents": at_risk["c"] - accepted["c"]}


# ---------------------------------------------------------------------------------------- summary
def summary(conn: sqlite3.Connection, shop_id: int) -> dict | None:
    up = latest_upload(conn, shop_id)
    if up is None:
        return None
    by_status = {r["status"]: r for r in conn.execute(
        "SELECT status, COUNT(*) AS n, COALESCE(SUM(capital_cents),0) AS cap FROM skus WHERE shop_id = ? AND qty > 0 GROUP BY status", (shop_id,))}

    def blk(*names):
        return {"n": sum(by_status[s]["n"] for s in names if s in by_status), "cap_cents": sum(by_status[s]["cap"] for s in names if s in by_status)}

    total = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(capital_cents),0) AS cap, SUM(qty = 0) AS zero FROM skus WHERE shop_id = ?",
                         (shop_id,)).fetchone()
    cats = conn.execute(
        f"SELECT p.category AS key, COUNT(*) AS n, SUM(s.capital_cents) AS cap FROM skus s JOIN products p ON p.id = s.product_id"
        f" WHERE s.shop_id = ? AND s.qty > 0 AND s.status IN ({RISK_MARKS}) GROUP BY p.category ORDER BY cap DESC, p.category",
        (shop_id, *AT_RISK)).fetchall()
    top = cats[0]["cap"] if cats else 1
    mar = money_at_risk(conn, shop_id)
    nl = needs_look(conn, shop_id, limit=0)
    return {
        "as_of": up["as_of"], "upload_id": up["id"], "rule_version": up["rule_version"],
        "totals": {"skus": total["n"], "stock_at_cost_cents": total["cap"], "dead": blk("dead"), "slow": blk("slow"),
                   "at_risk": blk(*AT_RISK), "new": blk("new"), "insufficient": blk("insufficient_data"), "zero_qty": total["zero"] or 0},
        # money_at_risk_cents: dead or slow stock with no accepted plan yet. at_risk_all_cents: everything dead or slow.
        "money_at_risk_cents": mar["money_at_risk_cents"], "at_risk_all_cents": mar["at_risk_cents"],
        "accepted_cents": mar["accepted_cents"], "accepted_n": mar["accepted_n"],
        "by_category": [{"key": c["key"], "label": CATEGORY_LABEL.get(c["key"], c["key"]), "n": c["n"], "cap_cents": c["cap"],
                         "pct": round(100 * c["cap"] / top)} for c in cats],
        "needs_look": {"count": nl["count"], "unsorted": nl["unsorted"]},
    }


def top_items(conn: sqlite3.Connection, shop_id: int, limit: int, th: Thresholds) -> list[dict]:
    rows = conn.execute(
        f"SELECT s.sku, s.qty, s.dsls, s.status, s.capital_cents, p.brand, p.model, p.category FROM skus s JOIN products p ON p.id = s.product_id"
        f" WHERE s.shop_id = ? AND s.qty > 0 AND s.status IN ({RISK_MARKS}) ORDER BY s.capital_cents DESC, s.sku LIMIT ?",
        (shop_id, *AT_RISK, limit)).fetchall()
    out = []
    for r in rows:
        slow_d = th.categories[r["category"]].slow_dsls if r["category"] in th.categories else 0
        out.append({"sku": r["sku"], "name": _name(r), "category": r["category"], "category_label": CATEGORY_LABEL.get(r["category"], r["category"]),
                    "qty": r["qty"], "dsls": r["dsls"], "status": r["status"], "why": why_flagged(r["status"], r["dsls"], slow_d),
                    "cap_cents": r["capital_cents"]})
    return out


# ---------------------------------------------------------------------------------------- launch exposure and alerts
def _group_items(conn, brand: str, category: str, shop_id: int):
    return conn.execute(
        f"SELECT s.*, p.brand, p.model, p.category FROM skus s JOIN products p ON p.id = s.product_id WHERE s.shop_id = ? AND p.launch_family = 1"
        f" AND p.brand = ? AND p.category = ? AND s.qty > 0 AND s.status IN ({RISK_MARKS}) ORDER BY s.capital_cents DESC, s.sku",
        (shop_id, brand, category, *AT_RISK)).fetchall()


def _source(a) -> dict:
    """The real, dated source behind an alert, or a plain statement that there is none."""
    if a["source_item_id"] is None:
        return {"attached": False, "status": "no dated source attached"}
    return {"attached": True, "name": a["source_name"], "url": a["source_url"], "title": a["source_title"], "published": a["source_published"],
            "launch_date": a["launch_date"] if a["date_stated"] else None,
            "launch_date_status": "stated in the article, as written" if a["date_stated"] else "announced, no date"}


def launch_exposed(conn: sqlite3.Connection, shop_id: int) -> list[dict]:
    """Launch-family items that are dead or slow, per alert. Deliberately no options: plans come only after the owner asks."""
    out = []
    for a in conn.execute("SELECT * FROM alerts WHERE shop_id = ? AND kind = 'launch_family' ORDER BY capital_cents DESC, id", (shop_id,)):
        items = _group_items(conn, a["brand"], a["category"], shop_id)
        top3 = items[:3]
        out.append({
            "alert_id": a["id"], "label": f"{a['brand']} {CATEGORY_LABEL.get(a['category'], a['category'])}", "brand": a["brand"],
            "category": a["category"], "title": a["title"], "source_note": a["source_note"], "source": _source(a),
            "n": len(items), "cap_cents": sum(i["capital_cents"] for i in items),
            "top": [{"sku": i["sku"], "name": _name(i), "shelf": i["qty"], "cap_cents": i["capital_cents"]} for i in top3],
            "rest_n": len(items) - len(top3), "rest_cap_cents": sum(i["capital_cents"] for i in items[3:]),
            "help_requested": a["help_requested_at"] is not None})
    return out


def get_alert(conn: sqlite3.Connection, alert_id: int):
    a = conn.execute("SELECT * FROM alerts WHERE id = ?", (alert_id,)).fetchone()
    if a is None:
        raise NotFound(f"alert {alert_id} not found")
    return a


def alert_state(conn: sqlite3.Connection, alert_id: int) -> dict:
    a = get_alert(conn, alert_id)
    recs = conn.execute("SELECT status, COUNT(*) AS n FROM recommendations WHERE alert_id = ? GROUP BY status", (alert_id,)).fetchall()
    return {"alert_id": a["id"], "title": a["title"], "source_note": a["source_note"], "source": _source(a), "n_items": a["n_items"],
            "cap_cents": a["capital_cents"],
            "help_requested": a["help_requested_at"] is not None, "help_requested_at": a["help_requested_at"],
            "recommendations": {r["status"]: r["n"] for r in recs}}


def request_help(conn: sqlite3.Connection, shop_id: int, alert_id: int) -> dict:
    a = get_alert(conn, alert_id)
    ts = now_text()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if a["help_requested_at"] is None:  # idempotent: the first request is the one on record
            conn.execute("UPDATE alerts SET help_requested_at = ? WHERE id = ?", (ts, alert_id))
            conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id) VALUES (?,?,?,?,?,?)",
                         (shop_id, ts, "owner", "help_requested", "alert", str(alert_id)))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return alert_state(conn, alert_id)


def _rec_json(rec, item) -> dict:
    reasons = json.loads(rec["reasons_json"])
    d = rec["discount_pct"]
    label = {"discount": f"Discount {d}%", "bundle": "Bundle", "return": "Return", "hold": "Hold"}[rec["action"]]
    dos = metrics.days_of_stock(item["qty"], item["units_sold_90d"])
    chips = [f"SKU {rec['sku']}", "Days of stock " + (str(round(dos)) if dos is not None else "no sales in 90 days")]
    return {"id": rec["id"], "alert_id": rec["alert_id"], "sku": rec["sku"], "name": _name(item), "shelf": item["qty"],
            "tied_cents": rec["capital_cents"], "option": rec["action"], "option_label": label, "discount_pct": d,
            "margin_impact_cents": rec["margin_impact_cents"], "reasons": reasons, "chips": chips,
            "citations": json.loads(rec["citations_json"]), "status": rec["status"], "decided_at": rec["decided_at"]}


def plan(conn: sqlite3.Connection, shop_id: int, alert_id: int, limit: int, th: Thresholds) -> list[dict]:
    """One proposed option per item for the top `limit` items of the alert. Refused (Conflict) until the owner asked for help."""
    a = get_alert(conn, alert_id)
    if a["help_requested_at"] is None:
        raise Conflict("Loreg only plans after you ask for help on this alert")
    as_of = date.fromisoformat(latest_upload(conn, shop_id)["as_of"])
    items = _group_items(conn, a["brand"], a["category"], shop_id)[:limit]
    ts = now_text()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for it in items:
            rw = date.fromisoformat(it["return_window_end"]) if it["return_window_end"] else None
            opts = actions.allowed_options(qty=it["qty"], unit_cost=it["unit_cost_cents"], sell_price=it["sell_price_cents"], dsls=it["dsls"],
                                           units_sold_90d=it["units_sold_90d"], category=it["category"], seasonal=bool(it["is_seasonal"]),
                                           return_window_end=rw, as_of=as_of, th=th)
            option, pct = actions.default_option(opts)
            dos = metrics.days_of_stock(it["qty"], it["units_sold_90d"])
            reasons = plan_reasons(option=option, discount_pct=pct, qty=it["qty"], cost_cents=it["unit_cost_cents"], sell_cents=it["sell_price_cents"],
                                   dsls=it["dsls"], dos=dos, category=it["category"], opts=opts, return_end=rw, th=th)
            facts = [{"id": f"{it['sku']}:capital", "label": "Money tied up", "value": money(it["capital_cents"])},
                     {"id": f"{it['sku']}:dsls", "label": "Days since last sale", "value": "none" if it["dsls"] is None else str(it["dsls"])},
                     {"id": f"{it['sku']}:status", "label": "Status", "value": it["status"]}]
            impact = round(it["qty"] * it["sell_price_cents"] * pct / 100) if option == "discount" else 0
            conn.execute(
                "INSERT OR IGNORE INTO recommendations (shop_id, alert_id, sku, action, discount_pct, capital_cents, margin_impact_cents,"
                " reasons_json, citations_json, status, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (shop_id, alert_id, it["sku"], option, pct, it["capital_cents"], impact, json.dumps(reasons), json.dumps(facts), "proposed", ts))
        conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id, detail_json) VALUES (?,?,?,?,?,?,?)",
                     (shop_id, ts, "system", "plan_proposed", "alert", str(alert_id), json.dumps({"items": len(items)})))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    by_sku = {it["sku"]: it for it in items}
    recs = conn.execute("SELECT * FROM recommendations WHERE alert_id = ? ORDER BY capital_cents DESC, sku", (alert_id,)).fetchall()
    return [_rec_json(r, by_sku[r["sku"]]) for r in recs if r["sku"] in by_sku]


def decide(conn: sqlite3.Connection, shop_id: int, rec_id: int, accept: bool) -> dict:
    """Save the owner's decision. Needs a recorded help request. Returns the recomputed money at risk."""
    rec = conn.execute("SELECT r.*, a.help_requested_at AS help_at FROM recommendations r JOIN alerts a ON a.id = r.alert_id WHERE r.id = ?",
                       (rec_id,)).fetchone()
    if rec is None:
        raise NotFound(f"recommendation {rec_id} not found")
    if rec["help_at"] is None:
        raise Conflict("nothing can be accepted or declined before you have asked for help")
    target = "accepted" if accept else "rejected"
    if rec["status"] != target:
        if rec["status"] != "proposed":
            raise Conflict(f"this plan was already {rec['status']}")
        ts = now_text()
        conn.execute("BEGIN IMMEDIATE")
        try:
            conn.execute("UPDATE recommendations SET status = ?, decided_at = ? WHERE id = ?", (target, ts, rec_id))
            conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id, detail_json) VALUES (?,?,?,?,?,?,?)",
                         (shop_id, ts, "owner", "accepted" if accept else "declined", "recommendation", str(rec_id),
                          json.dumps({"sku": rec["sku"], "capital_cents": rec["capital_cents"]})))
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    return {"recommendation_id": rec_id, "sku": rec["sku"], "status": target, "capital_cents": rec["capital_cents"], **money_at_risk(conn, shop_id)}


def undo(conn: sqlite3.Connection, shop_id: int, rec_id: int) -> dict:
    """Take back an accepted or declined decision: the plan goes back to 'proposed'. Money at risk is recomputed."""
    rec = conn.execute("SELECT * FROM recommendations WHERE id = ?", (rec_id,)).fetchone()
    if rec is None:
        raise NotFound(f"recommendation {rec_id} not found")
    if rec["status"] not in ("accepted", "rejected"):
        raise Conflict("there is no decision to undo on this plan")
    ts = now_text()
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("UPDATE recommendations SET status = 'proposed', decided_at = NULL WHERE id = ?", (rec_id,))
        conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id, detail_json) VALUES (?,?,?,?,?,?,?)",
                     (shop_id, ts, "owner", "undone", "recommendation", str(rec_id),
                      json.dumps({"sku": rec["sku"], "was": "accepted" if rec["status"] == "accepted" else "declined",
                                  "capital_cents": rec["capital_cents"]})))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"recommendation_id": rec_id, "sku": rec["sku"], "status": "proposed", "capital_cents": rec["capital_cents"], **money_at_risk(conn, shop_id)}


# ---------------------------------------------------------------------------------------- uploads and needs-a-look
def upload_issues(conn: sqlite3.Connection, upload_id: int, row_limit: int = 200) -> dict:
    up = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if up is None:
        raise NotFound(f"upload {upload_id} not found")
    counts = conn.execute("SELECT code, COUNT(*) AS n FROM dq_issues WHERE upload_id = ? GROUP BY code", (upload_id,)).fetchall()
    by_code = {c["code"]: c["n"] for c in counts}
    issues = [{"code": c, "n": by_code[c], "level": SEVERITY[c], "text": DQ_TEXT[c].format(n=by_code[c])} for c in CODES if c in by_code]
    rows = conn.execute("SELECT row_number, sku, code, severity, quarantined FROM dq_issues WHERE upload_id = ? ORDER BY row_number, code LIMIT ?",
                        (upload_id, row_limit)).fetchall()
    return {"upload": {"id": up["id"], "file": up["filename"], "as_of": up["as_of"], "rows": up["rows"], "ok": up["rows"] - up["blocked"] - up["look"],
                       "look": up["look"], "blocked": up["blocked"], "kept": up["kept"], "zero_qty": up["zero_qty"], "unsorted": up["unsorted"]},
            "issues": issues,
            "rows": [{"row": r["row_number"], "sku": r["sku"], "code": r["code"], "severity": r["severity"], "quarantined": bool(r["quarantined"])}
                     for r in rows]}


def needs_look(conn: sqlite3.Connection, shop_id: int, limit: int = 100) -> dict:
    """Kept rows that need the owner's eye: warnings, rows with no catalog line (Unsorted), unreadable dates or categories."""
    count = conn.execute("SELECT COUNT(*) AS n FROM skus WHERE shop_id = ? AND needs_look = 1", (shop_id,)).fetchone()["n"]
    unsorted = conn.execute("SELECT COUNT(*) AS n FROM products WHERE shop_id = ? AND matched = 0", (shop_id,)).fetchone()["n"]
    items = []
    if limit:
        for r in conn.execute(
                "SELECT s.sku, s.look_reason, s.status, p.brand, p.model, p.line FROM skus s JOIN products p ON p.id = s.product_id"
                " WHERE s.shop_id = ? AND s.needs_look = 1 ORDER BY p.matched, s.sku LIMIT ?", (shop_id, limit)):
            items.append({"sku": r["sku"], "name": _name(r), "line": r["line"], "status": r["status"], "reasons": (r["look_reason"] or "").split(",")})
    return {"count": count, "unsorted": unsorted, "items": items}
