"""Run Loreg Watch once: fetch a feed, store sanitized items, extract launch events, match them to catalog lines.

Everything fetched is untrusted data (untrusted=1). The Market agent only returns a validated event; matching to
catalog lines and attaching to alerts is done by code. A source, headline or date is never made up: if nothing
real matches, alerts keep saying that no dated source is attached.
"""

import json
import re
import sqlite3
from datetime import datetime, timezone

import httpx

from app.agents.market import Article, read_article, written_date_to_iso
from app.llm.client import LLMClient
from app.watch.attach import attach_events_to_alerts
from app.watch.feeds import SAMSUNG_FEED, FeedResult, canonical_url, content_hash, fetch_feed
from app.watch.matching import catalog_lines, match_lines
from app.watch.sanitize import INVISIBLE_FLAG, sanitize_html, sanitize_title

DEFAULT_SOURCES = [{"name": "Samsung Global Newsroom", "brand": "Samsung", "url": SAMSUNG_FEED, "kind": "rss"}]
MAX_ENTRIES = 100
MAX_EXTRACT_PER_SCAN = 10
_LAUNCH_CUE = re.compile(r"\b(launch(?:es|ed|ing)?|unveil(?:s|ed|ing)?|introduc(?:e|es|ed|ing)|announce(?:s|d)?|available|release(?:s|d)?|"
                         r"pre-?orders?|on sale|ships?|shipping)\b", re.IGNORECASE)
_ANY_DATE = re.compile(r"\b\d{4}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", re.IGNORECASE)


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_source(conn: sqlite3.Connection, shop_id: int, src: dict, ts: str):
    row = conn.execute("SELECT * FROM market_sources WHERE url = ?", (src["url"],)).fetchone()
    if row:
        return row
    conn.execute("INSERT INTO market_sources (shop_id, name, brand, url, kind, created_at) VALUES (?,?,?,?,?,?)",
                 (shop_id, src["name"], src["brand"], src["url"], src["kind"], ts))
    return conn.execute("SELECT * FROM market_sources WHERE url = ?", (src["url"],)).fetchone()


def _published(entry) -> str | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed:
        return datetime(*parsed[:6], tzinfo=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return None


def _entry_html(entry) -> str:
    content = entry.get("content")
    if content:
        return content[0].get("value", "")
    return entry.get("summary", "") or ""


def store_items(conn: sqlite3.Connection, source, entries: list, ts: str) -> dict:
    """Sanitize and store new entries. Duplicates (same canonical URL or same content) are ignored."""
    new = dup_url = dup_content = skipped = 0
    for e in entries[:MAX_ENTRIES]:
        link = (e.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            skipped += 1
            continue
        canon = canonical_url(link)
        if conn.execute("SELECT 1 FROM market_items WHERE canonical_url = ?", (canon,)).fetchone():
            dup_url += 1
            continue
        title = sanitize_title(e.get("title") or "")
        text, flags = sanitize_html(_entry_html(e))
        title_flags = sanitize_html(e.get("title") or "")[1]
        flags = list(dict.fromkeys(flags + title_flags))
        chash = content_hash(title, text)
        if conn.execute("SELECT 1 FROM market_items WHERE content_hash = ?", (chash,)).fetchone():
            dup_content += 1
            continue
        conn.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, content_hash,"
                     " injection_flags, untrusted, extracted) VALUES (?,?,?,?,?,?,?,?,?,1,0)",
                     (source["id"], link, canon, title or "(no title)", _published(e), ts, text, chash, json.dumps(flags)))
        new += 1
    return {"new": new, "duplicate_url": dup_url, "duplicate_content": dup_content, "skipped": skipped}


def extract_events(conn: sqlite3.Connection, source, llm: LLMClient, lines: list[dict], ts: str, limit: int = MAX_EXTRACT_PER_SCAN) -> dict:
    """Run the Market agent on stored items that look like launch news; match each event to catalog lines by code.

    The model calls happen first, with no database lock held; the results are then written in one short transaction.
    """
    items = conn.execute("SELECT * FROM market_items WHERE source_id = ? AND extracted = 0 ORDER BY published_at DESC, id DESC",
                         (source["id"],)).fetchall()
    skipped, outcomes = [], []
    for it in items:
        haystack = f"{it['title']} {it['text']}"
        cue = _LAUNCH_CUE.search(haystack) and (_ANY_DATE.search(haystack) or match_lines(source["brand"], haystack, lines))
        if not cue:
            skipped.append(it["id"])
        elif len(outcomes) < limit:  # the rest stay extracted = 0 and are read on a later scan
            result = read_article(llm, Article(source_id=str(it["id"]), outlet=source["name"], url=it["url"],
                                               published=it["published_at"] or "unknown", text=it["text"], title=it["title"]))
            outcomes.append((it, result))
    events = matched = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item_id in skipped:
            conn.execute("UPDATE market_items SET extracted = -1 WHERE id = ?", (item_id,))
        for it, result in outcomes:
            flagged = any(f != INVISIBLE_FLAG for f in json.loads(it["injection_flags"]))  # hard flags only; see sanitize.py
            for ev in result.output.events:
                brand = ev.brand or source["brand"]
                ids = match_lines(brand, f"{it['title']} {ev.product_family} {ev.quote}", lines)
                stated = bool(ev.date_stated and ev.launch_date)
                conn.execute("INSERT INTO market_events (item_id, brand, family, launch_date, launch_iso, date_stated, quote, matched_line_ids,"
                             " flagged, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (it["id"], brand, ev.product_family, ev.launch_date if stated else None,
                              written_date_to_iso(ev.launch_date) if stated else None, 1 if stated else 0, ev.quote, json.dumps(ids),
                              1 if flagged else 0, ts))
                events += 1
                matched += 1 if ids else 0
            conn.execute("UPDATE market_items SET extracted = 1 WHERE id = ?", (it["id"],))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"read": len(outcomes), "events": events, "matched_events": matched}


def list_items(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Stored items without their article text."""
    out = []
    for it in conn.execute("SELECT i.*, s.name AS source_name FROM market_items i JOIN market_sources s ON s.id = i.source_id"
                           " ORDER BY i.published_at DESC, i.id DESC LIMIT ?", (limit,)):
        evs = conn.execute("SELECT brand, family, launch_date, launch_iso, date_stated, quote, matched_line_ids, flagged FROM market_events WHERE item_id = ?",
                           (it["id"],)).fetchall()
        flags = json.loads(it["injection_flags"])
        out.append({"id": it["id"], "source": it["source_name"], "title": it["title"], "url": it["url"], "published_at": it["published_at"],
                    "fetched_at": it["fetched_at"], "untrusted": bool(it["untrusted"]), "injection_flags": flags, "flagged": bool(flags),
                    "extracted": it["extracted"],
                    "events": [{"brand": e["brand"], "family": e["family"],
                                "launch_date": e["launch_date"] if e["date_stated"] else None,
                                "launch_date_status": "stated in the article" if e["date_stated"] else "announced, no date",
                                "launch_iso": e["launch_iso"], "quote": e["quote"], "matched_line_ids": json.loads(e["matched_line_ids"]),
                                "attachable": not e["flagged"]} for e in evs]})
    return out


def scan(conn: sqlite3.Connection, shop_id: int, *, http: httpx.Client, llm: LLMClient, catalog, sources: list[dict] | None = None) -> dict:
    ts = _now()
    lines = catalog_lines(catalog.variants)
    report = {"scanned_at": ts, "sources": [], "new_items": 0, "events": 0, "matched_events": 0, "network": "ok", "message": ""}
    for src in sources or DEFAULT_SOURCES:
        conn.execute("BEGIN IMMEDIATE")
        try:
            source = ensure_source(conn, shop_id, src, ts)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        res: FeedResult = fetch_feed(http, source["url"], etag=source["etag"], last_modified=source["last_modified"],
                                     robots_allowed=None if source["robots_allowed"] is None else bool(source["robots_allowed"]),
                                     robots_checked_at=source["robots_checked_at"])
        entry = {"source": source["name"], "url": source["url"], "status": res.status, "message": res.message}
        conn.execute("BEGIN IMMEDIATE")
        try:
            sets = ["last_fetch_at = ?", "last_status = ?"]
            vals: list = [ts, res.status]
            if res.robots_checked:
                sets += ["robots_allowed = ?", "robots_checked_at = ?"]
                vals += [None if res.robots_allowed is None else int(res.robots_allowed), ts]
            if res.status == "ok":
                sets += ["etag = ?", "last_modified = ?"]
                vals += [res.etag, res.last_modified]
            conn.execute(f"UPDATE market_sources SET {', '.join(sets)} WHERE id = ?", (*vals, source["id"]))
            if res.status == "ok":
                entry.update(store_items(conn, source, res.entries, ts))
                report["new_items"] += entry["new"]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if res.status in ("ok", "not_modified"):
            entry.update(extract_events(conn, source, llm, lines, ts))
            report["events"] += entry["events"]
            report["matched_events"] += entry["matched_events"]
        if res.status in ("network_error", "robots_unavailable"):
            report["network"] = "unavailable"
        report["sources"].append(entry)
    conn.execute("BEGIN IMMEDIATE")
    try:
        report["alerts_with_a_source"] = attach_events_to_alerts(conn, shop_id)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    bad = [s for s in report["sources"] if s["status"] not in ("ok", "not_modified")]
    report["message"] = ("; ".join(f"{s['source']}: {s['message']}" for s in bad) + ". Showing the items already stored.") if bad else "scan complete"
    report["items_stored"] = conn.execute("SELECT COUNT(*) AS n FROM market_items").fetchone()["n"]
    return report
