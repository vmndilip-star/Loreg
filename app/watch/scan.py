"""Run Loreg Watch once: fetch a feed, store sanitized items, extract launch events, match them to catalog lines.

Everything fetched is untrusted data (untrusted=1). The Market agent only returns a validated event; matching to
catalog lines and attaching to alerts is done by code. A source, headline or date is never made up: if nothing
real matches, alerts keep saying that no dated source is attached.
"""

import json
import re
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import httpx

from app.agents.market import Article, read_article, written_date_to_iso
from app.llm.client import LLMClient
from app.watch.allowlist import check_url
from app.watch.common import NEWS, ensure_source, stamp
from app.watch.attach import attach_events_to_alerts, is_dated
from app.watch.excerpt import LAUNCH_CUE as _LAUNCH_CUE, make_excerpt
from app.watch.feeds import FeedResult, canonical_url, content_hash, fetch_feed
from app.watch.matching import catalog_lines, match_lines
from app.watch.sanitize import INVISIBLE_FLAG, sanitize_html, sanitize_title

DEFAULT_SOURCES = [dict(s) for s in NEWS.sources]
MAX_ENTRIES = 100
MAX_EXTRACT_PER_SCAN = 10
_READERS = ThreadPoolExecutor(max_workers=2, thread_name_prefix="market")   # model calls run here so a scan can stop waiting for one
_ANY_DATE = re.compile(r"\b\d{4}\b|\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\b", re.IGNORECASE)


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


def store_items(conn: sqlite3.Connection, source, entries: list, ts: str, allowed=None, news=None) -> dict:
    """Sanitize and store new entries. Duplicates (same canonical URL or same content) are ignored."""
    news = news or NEWS
    allowed = allowed if allowed is not None else news.allowed_hosts
    new = dup_url = dup_content = skipped = offsite = refreshed = 0
    for e in entries[:MAX_ENTRIES]:
        link = (e.get("link") or "").strip()
        if not link.startswith(("http://", "https://")):
            skipped += 1
            continue
        if check_url(link, allowed):                      # a link to another site, or not https: never stored
            offsite += 1
            continue
        canon = canonical_url(link)
        existing = conn.execute("SELECT id, origin FROM market_items WHERE canonical_url = ?", (canon,)).fetchone()
        if existing and existing["origin"] == "stored_copy":          # read live now: it is a real, freshly fetched item from here on
            title = sanitize_title(e.get("title") or "")
            text, flags = sanitize_html(_entry_html(e))
            conn.execute("UPDATE market_items SET origin = 'feed', fetched_at = ?, text = ?, excerpt = ?, content_hash = ?, injection_flags = ?, extracted = 0,"
                         " body_cleared_at = NULL WHERE id = ?", (ts, text, make_excerpt(text, news.excerpt_chars), content_hash(title, text),
                                                                  json.dumps(list(dict.fromkeys(flags + sanitize_html(e.get('title') or '')[1]))), existing["id"]))
            refreshed += 1
            continue
        if existing:
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
        conn.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, excerpt, domain, origin,"
                     " content_hash, injection_flags, untrusted, extracted) VALUES (?,?,?,?,?,?,?,?,?,'feed',?,?,1,0)",
                     (source["id"], link, canon, title or "(no title)", _published(e), ts, text, make_excerpt(text, news.excerpt_chars),
                      (urlsplit(link).hostname or "").rstrip("."), chash, json.dumps(flags)))
        new += 1
    return {"new": new, "duplicate_url": dup_url, "duplicate_content": dup_content, "skipped": skipped, "offsite_links": offsite, "refreshed": refreshed}


def _verbatim(quote: str | None, body: str, limit: int) -> str | None:
    """A quote is kept only if it really is a stretch of the article, and only up to `limit` characters."""
    q = (quote or "").strip()
    return q[:limit] if q and q in body else None


def clear_old_bodies(conn: sqlite3.Connection, now: datetime, keep_days: int) -> int:
    """An article body nobody has read within `keep_days` is cleared anyway; the excerpt, hash and dates stay."""
    cutoff, ts = stamp(now - timedelta(days=keep_days)), stamp(now)
    cur = conn.execute("UPDATE market_items SET text = '', body_cleared_at = ?, extracted = CASE WHEN extracted = 0 THEN -2 ELSE extracted END"
                       " WHERE text != '' AND fetched_at < ?", (ts, cutoff))
    return cur.rowcount


def extract_events(conn: sqlite3.Connection, source, llm: LLMClient, lines: list[dict], ts: str, limit: int = MAX_EXTRACT_PER_SCAN, news=None,
                   deadline: float | None = None) -> dict:
    """Run the Market agent on stored items that look like launch news; match each event to catalog lines by code.
    Once an item has been read (or skipped) its body is cleared; the excerpt, hash and dates stay.

    The model calls happen first, with no database lock held; the results are then written in one short transaction.
    `deadline` (time.monotonic()): no model call starts after it, and a call still running at it is no longer waited for (its thread
    finishes on its own; the answer is dropped). Articles not read in time keep extracted = 0 and are read on the next check.
    """
    news = news or NEWS
    items = conn.execute("SELECT * FROM market_items WHERE source_id = ? AND extracted = 0 AND text != '' ORDER BY published_at DESC, id DESC",
                         (source["id"],)).fetchall()
    skipped, outcomes, left = [], [], 0
    for it in items:
        haystack = f"{it['title']} {it['text']}"
        cue = _LAUNCH_CUE.search(haystack) and (_ANY_DATE.search(haystack) or match_lines(source["brand"], haystack, lines))
        if not cue:
            skipped.append(it["id"])
        elif len(outcomes) < limit:  # the rest stay extracted = 0 and are read on a later scan
            article = Article(source_id=str(it["id"]), outlet=source["name"], url=it["url"], published=it["published_at"] or "unknown",
                              text=it["text"], title=it["title"])
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                left += 1
                continue
            try:
                result = read_article(llm, article) if remaining is None else _READERS.submit(read_article, llm, article).result(timeout=remaining)
            except FutureTimeout:
                left += 1
                continue
            outcomes.append((it, result))
        else:
            left += 1
    events = matched = 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        for item_id in skipped:
            conn.execute("UPDATE market_items SET extracted = -1, text = '', body_cleared_at = ? WHERE id = ?", (ts, item_id))
        for it, result in outcomes:
            flagged = any(f != INVISIBLE_FLAG for f in json.loads(it["injection_flags"]))  # hard flags only; see sanitize.py
            for ev in result.output.events:
                brand = ev.brand or source["brand"]
                ids = match_lines(brand, f"{it['title']} {ev.product_family} {ev.quote}", lines)
                stated = bool(ev.date_stated and ev.launch_date)
                conn.execute("INSERT INTO market_events (item_id, brand, family, launch_date, launch_iso, date_stated, quote, matched_line_ids,"
                             " flagged, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                             (it["id"], brand, ev.product_family, ev.launch_date if stated else None,
                              written_date_to_iso(ev.launch_date) if stated else None, 1 if stated else 0, _verbatim(ev.quote, it["text"], news.excerpt_chars), json.dumps(ids),
                              1 if flagged else 0, ts))
                events += 1
                matched += 1 if ids else 0
            conn.execute("UPDATE market_items SET extracted = 1, text = '', body_cleared_at = ? WHERE id = ?", (ts, it["id"]))   # read: the body goes
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {"read": len(outcomes), "events": events, "matched_events": matched, "left_for_next_check": left}


def list_items(conn: sqlite3.Connection, limit: int = 50) -> list[dict]:
    """Stored items without their article text."""
    out = []
    for it in conn.execute("SELECT i.*, s.name AS source_name FROM market_items i JOIN market_sources s ON s.id = i.source_id"
                           " ORDER BY i.published_at DESC, i.id DESC LIMIT ?", (limit,)):
        evs = conn.execute("SELECT brand, family, launch_date, launch_iso, date_stated, quote, matched_line_ids, flagged FROM market_events WHERE item_id = ?",
                           (it["id"],)).fetchall()
        flags = json.loads(it["injection_flags"])
        out.append({"id": it["id"], "source": it["source_name"], "title": it["title"], "url": it["url"], "published_at": it["published_at"],
                    "fetched_at": it["fetched_at"], "excerpt": it["excerpt"], "domain": it["domain"], "origin": it["origin"],
                    "label": "Demo article (synthetic)" if it["origin"] == "demo_synthetic" else ("Stored copy" if it["origin"] == "stored_copy" else ""),
                    "undated": not is_dated(it["published_at"]), "untrusted": bool(it["untrusted"]), "injection_flags": flags, "flagged": bool(flags),
                    "extracted": it["extracted"],
                    "events": [{"brand": e["brand"], "family": e["family"],
                                "launch_date": e["launch_date"] if e["date_stated"] else None,
                                "launch_date_status": "stated in the article" if e["date_stated"] else "announced, no date",
                                "launch_iso": e["launch_iso"], "quote": e["quote"], "matched_line_ids": json.loads(e["matched_line_ids"]),
                                "attachable": not e["flagged"]} for e in evs]})
    return out


def scan(conn: sqlite3.Connection, shop_id: int, *, http: httpx.Client, llm: LLMClient, catalog, sources: list[dict] | None = None,
         now: datetime | None = None, news=NEWS) -> dict:
    deadline = time.monotonic() + news.scan_deadline_s                # the whole check, fetch and reading included
    ts = stamp(now)
    lines = catalog_lines(catalog.variants)
    conn.execute("BEGIN IMMEDIATE")
    clear_old_bodies(conn, now or datetime.now(timezone.utc), news.body_keep_days)
    conn.execute("COMMIT")
    report = {"scanned_at": ts, "sources": [], "new_items": 0, "events": 0, "matched_events": 0, "left_for_next_check": 0, "network": "ok", "message": ""}
    for src in sources or DEFAULT_SOURCES:
        refused = check_url(src["url"], news.allowed_hosts)
        if refused:                                       # never configured, never fetched
            report["sources"].append({"source": src.get("name", "?"), "url": src["url"], "status": "blocked_host", "message": f"not fetched: {refused}"})
            continue
        conn.execute("BEGIN IMMEDIATE")
        try:
            source = ensure_source(conn, shop_id, src, ts)
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        res: FeedResult = fetch_feed(http, source["url"], etag=source["etag"], last_modified=source["last_modified"],
                                     robots_allowed=None if source["robots_allowed"] is None else bool(source["robots_allowed"]),
                                     robots_checked_at=source["robots_checked_at"], now=now, allowed=news.allowed_hosts,
                                     max_bytes=news.max_feed_bytes, max_redirects=news.max_redirects)
        entry = {"source": source["name"], "url": source["url"], "status": res.status, "message": res.message,
                 "robots": {"checked_on": res.robots_checked_on, "from_cache": res.robots_from_cache}}
        conn.execute("BEGIN IMMEDIATE")
        try:
            sets = ["last_fetch_at = ?", "last_status = ?"]
            vals: list = [ts, res.status]
            if res.robots_checked:
                sets += ["robots_allowed = ?", "robots_checked_at = ?"]
                vals += [None if res.robots_allowed is None else int(res.robots_allowed), ts]
            if res.status in ("ok", "not_modified"):
                sets += ["last_ok_at = ?"]
                vals += [ts]
            if res.status == "ok":
                sets += ["etag = ?", "last_modified = ?"]
                vals += [res.etag, res.last_modified]
            conn.execute(f"UPDATE market_sources SET {', '.join(sets)} WHERE id = ?", (*vals, source["id"]))
            if res.status == "ok":
                entry.update(store_items(conn, source, res.entries, ts, news.allowed_hosts, news))
                report["new_items"] += entry["new"]
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        if res.status in ("ok", "not_modified"):
            entry.update(extract_events(conn, source, llm, lines, ts, news=news, deadline=deadline))
            report["events"] += entry["events"]
            report["left_for_next_check"] += entry["left_for_next_check"]
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
    newest = conn.execute("SELECT MAX(fetched_at) AS t FROM market_items WHERE origin IN ('feed', 'stored_copy')").fetchone()["t"]
    report["cached"] = bool(bad)
    report["checked_at"] = ts
    report["message"] = ("; ".join(f"{s['source']}: {s['message']}" for s in bad)
                         + (f". Showing stored copies; the newest was fetched on {newest[:10]}." if newest else ". Nothing is stored yet.")) if bad else "scan complete"
    if report["left_for_next_check"]:
        report["message"] += f"; {report['left_for_next_check']} articles will be read on the next check"
    report["items_stored"] = conn.execute("SELECT COUNT(*) AS n FROM market_items").fetchone()["n"]
    return report
