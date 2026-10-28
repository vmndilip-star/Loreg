"""The demo alert always carries a source and a date, and is never unlabelled (only when LOREG_DEMO=1).

Two kinds of demo source, tried in this order:

1. A stored copy of one real article: data/demo/news_copy.json, written by scripts/capture_demo_news.py from the real feed. It keeps its true
   published and fetched dates and is labelled "Stored copy" everywhere. It is used only if it passes the same checks as a live item (allowed
   host, readable dates, a short verbatim excerpt) and really names a catalog line.
2. Otherwise a synthetic article, labelled "Demo article (synthetic)" on every card. Its address ends in .invalid, which can never be a real
   site, so it cannot be mistaken for a link. Its dates are the demo's own.

A real article that matches the alert always wins over either (see attach.py). Nothing here is shown without its label.
"""

import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.watch.allowlist import check_url
from app.watch.attach import attach_events_to_alerts, is_dated
from app.watch.common import NEWS, ensure_source, stamp
from app.watch.feeds import canonical_url
from app.watch.matching import catalog_lines, match_lines

SYNTHETIC_URL = "https://demo.invalid/loreg-synthetic-demo-article"
SYNTHETIC_TITLE = "Demo article (synthetic): a newer QN90D Neo QLED generation is announced"
SYNTHETIC_EXCERPT = ("Demo article (synthetic). This is not real news; it was written for Loreg's demo. "
                     "In this made-up example, a newer generation of the QN90D Neo QLED line is announced.")
# The synthetic article has its own source row. No real publisher's name appears on it anywhere.
SYNTHETIC_SOURCE = {"name": "Loreg demo (synthetic)", "brand": "Samsung", "url": "https://demo.invalid/", "kind": "demo"}   # "brand" is the product brand, for matching


def load_stored_copy(path, lines, news=NEWS) -> dict | None:
    """The stored copy of a real article, if the file exists and every field passes; otherwise None (and the synthetic one is used)."""
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        ok = (isinstance(d, dict) and not check_url(d["url"], news.allowed_hosts) and is_dated(d["published_at"])
              and datetime.fromisoformat(d["fetched_at"].replace("Z", "+00:00")) and 0 < len(d["excerpt"]) <= news.excerpt_chars
              and len(d["content_hash"]) == 64 and d["title"].strip() and d["brand"].strip())
        if not ok:
            return None
        if d.get("launch_date") and d["launch_date"] not in d["excerpt"]:
            return None                                       # a date is only used if the excerpt says it, word for word
        d["line_ids"] = match_lines(d["brand"], f"{d['title']} {d['excerpt']}", lines)
        return d if d["line_ids"] else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _unsourced_launch_alerts(conn: sqlite3.Connection, shop_id: int) -> int:
    return conn.execute("SELECT COUNT(*) AS n FROM alerts WHERE shop_id = ? AND kind = 'launch_family' AND source_item_id IS NULL", (shop_id,)).fetchone()["n"]


def _local_excerpts(path, limit: int) -> dict:
    """url -> excerpt from the git-ignored local file the capture script writes. Anything that is not a short string is ignored."""
    try:
        items = json.loads(path.read_text(encoding="utf-8"))["items"]
        return {d["url"]: d["excerpt"] for d in items if isinstance(d.get("excerpt"), str) and 0 < len(d["excerpt"]) <= limit}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {}


def install_stored_items(conn: sqlite3.Connection, shop_id: int, path, now: datetime, news=NEWS, local_path=None) -> int:
    """Real items read in one earlier scan (data/demo/news_items.json), kept as labelled stored copies with their true dates so the Watch page
    shows real news even when robots.txt times out. They never attach to an alert and are never shown as fresh. Returns how many were added.

    The committed file holds titles, addresses, dates and hashes, and no article text. An excerpt is optional: an item
    without one renders fine. If the git-ignored local file exists (python scripts/capture_demo_news.py writes it), its excerpts fill in the rest."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        items = doc["items"]
    except (OSError, ValueError, KeyError, TypeError):
        return 0
    local = _local_excerpts(local_path, news.excerpt_chars) if local_path is not None else {}
    ts = stamp(now)
    source = ensure_source(conn, shop_id, dict(news.sources[0]), ts)
    added = 0
    for d in items if isinstance(items, list) else []:
        try:
            excerpt = d.get("excerpt") or local.get(d["url"], "")
            ok = (not check_url(d["url"], news.allowed_hosts) and is_dated(d["published_at"]) and datetime.fromisoformat(d["fetched_at"].replace("Z", "+00:00"))
                  and isinstance(excerpt, str) and len(excerpt) <= news.excerpt_chars and len(d["content_hash"]) == 64 and d["title"].strip())
        except (KeyError, ValueError, TypeError, AttributeError):
            ok = False
        if not ok or conn.execute("SELECT 1 FROM market_items WHERE canonical_url = ? OR content_hash = ?", (canonical_url(d["url"]), d["content_hash"])).fetchone():
            continue
        conn.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, excerpt, domain, origin, content_hash,"
                     " injection_flags, untrusted, extracted, body_cleared_at) VALUES (?,?,?,?,?,?,'',?,?,'stored_copy',?,'[]',1,-1,?)",
                     (source["id"], d["url"], canonical_url(d["url"]), d["title"][:300], d["published_at"], d["fetched_at"], excerpt, _host(d["url"]),
                      d["content_hash"], ts))
        added += 1
    return added


def ensure_demo_source(conn: sqlite3.Connection, shop_id: int, catalog, now: datetime, copy_path, items_path=None, news=NEWS, items_local_path=None) -> str:
    """Give every launch alert that has no source one that is labelled. Returns what was used: "none needed", "stored copy" or "synthetic"."""
    if items_path is not None:
        install_stored_items(conn, shop_id, items_path, now, news, items_local_path)
    if _unsourced_launch_alerts(conn, shop_id) == 0:
        return "none needed"
    lines = catalog_lines(catalog.variants)
    ts = stamp(now)
    real = load_stored_copy(copy_path, lines, news)
    source = ensure_source(conn, shop_id, dict(news.sources[0]) if real else dict(SYNTHETIC_SOURCE), ts)
    if real:
        kind, url, title, published, fetched, excerpt = "stored_copy", real["url"], real["title"], real["published_at"], real["fetched_at"], real["excerpt"]
        chash, brand, ids, launch = real["content_hash"], real["brand"], real["line_ids"], real.get("launch_date")
    else:
        from app.watch.feeds import content_hash
        kind, url, title, fetched, excerpt, brand, launch = "demo_synthetic", SYNTHETIC_URL, SYNTHETIC_TITLE, ts, SYNTHETIC_EXCERPT, "Samsung", None
        published = stamp(now - timedelta(days=2))
        chash, ids = content_hash(title, excerpt), match_lines("Samsung", f"{title} {excerpt}", lines)
    canon = canonical_url(url)
    item = conn.execute("SELECT id FROM market_items WHERE canonical_url = ?", (canon,)).fetchone()
    if item is None:
        item_id = conn.execute(
            "INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, excerpt, domain, origin, content_hash,"
            " injection_flags, untrusted, extracted, body_cleared_at) VALUES (?,?,?,?,?,?,'',?,?,?,?,'[]',1,1,?)",
            (source["id"], url, canon, title, published, fetched, excerpt, "demo.invalid" if kind == "demo_synthetic" else _host(url), kind, chash, ts)).lastrowid
    else:
        item_id = item["id"]
        if kind == "demo_synthetic":                          # the synthetic article is the demo's own: its dates follow the demo clock
            conn.execute("UPDATE market_items SET published_at = ?, fetched_at = ?, source_id = ? WHERE id = ?", (published, fetched, source["id"], item_id))
    if conn.execute("SELECT 1 FROM market_events WHERE item_id = ?", (item_id,)).fetchone() is None:
        conn.execute("INSERT INTO market_events (item_id, brand, family, launch_date, launch_iso, date_stated, quote, matched_line_ids, flagged, created_at)"
                     " VALUES (?,?,?,?,NULL,?,?,?,0,?)", (item_id, brand, "QN90D Neo QLED" if kind == "demo_synthetic" else title[:80], launch,
                                                           1 if launch else 0, excerpt[:news.excerpt_chars], json.dumps(ids), ts))
    attach_events_to_alerts(conn, shop_id)
    return "stored copy" if kind == "stored_copy" else "synthetic"


def _host(url: str) -> str:
    from urllib.parse import urlsplit
    return (urlsplit(url).hostname or "").rstrip(".")
