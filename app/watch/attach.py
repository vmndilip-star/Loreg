"""Attach a stored, dated market event to an alert. Database only: no model, no network.

An alert only ever shows a source that really matched one of its lines. Events taken from an article that
contained injection-like text are never attached automatically. If nothing matches, the alert keeps saying
that no dated source is attached.
"""

import json
import sqlite3

NO_SOURCE_NOTE = ("No dated news source is attached. These items are in the catalog's launch family (a newer generation is expected); "
                  "no launch date is claimed.")


def _note(name: str, published: str | None, launch: str | None, date_stated: bool) -> str:
    when = f"published {published[:10]}" if published else "publication date unknown"
    if date_stated and launch:
        return f"Source: {name}, {when}. Launch date: {launch} (as written in the article)."
    return f"Source: {name}, {when}. Announced, no launch date given in the article."


def attach_events_to_alerts(conn: sqlite3.Connection, shop_id: int) -> int:
    """Set or clear the source on each launch-family alert. Returns how many alerts now have a source."""
    attached = 0
    for a in conn.execute("SELECT * FROM alerts WHERE shop_id = ? AND kind = 'launch_family'", (shop_id,)).fetchall():
        line_ids = {r["line_id"] for r in conn.execute(
            "SELECT DISTINCT line_id FROM products WHERE shop_id = ? AND launch_family = 1 AND brand = ? AND category = ?",
            (shop_id, a["brand"], a["category"]))}
        chosen = None
        for ev in conn.execute(
                "SELECT e.*, i.title AS title, i.published_at AS published_at, i.url AS url, s.name AS source_name FROM market_events e"
                " JOIN market_items i ON i.id = e.item_id JOIN market_sources s ON s.id = i.source_id WHERE e.flagged = 0"
                " ORDER BY i.published_at DESC, e.id DESC"):
            if line_ids & set(json.loads(ev["matched_line_ids"])):
                chosen = ev
                break
        if chosen is None:
            conn.execute("UPDATE alerts SET source_item_id = NULL, source_name = NULL, source_url = NULL, source_title = NULL,"
                         " source_published = NULL, launch_date = NULL, date_stated = 0, source_note = ? WHERE id = ?", (NO_SOURCE_NOTE, a["id"]))
            continue
        stated = bool(chosen["date_stated"] and chosen["launch_date"])
        conn.execute("UPDATE alerts SET source_item_id = ?, source_name = ?, source_url = ?, source_title = ?, source_published = ?,"
                     " launch_date = ?, date_stated = ?, source_note = ? WHERE id = ?",
                     (chosen["item_id"], chosen["source_name"], chosen["url"], chosen["title"], chosen["published_at"],
                      chosen["launch_date"] if stated else None, 1 if stated else 0,
                      _note(chosen["source_name"], chosen["published_at"], chosen["launch_date"], stated), a["id"]))
        attached += 1
    return attached
