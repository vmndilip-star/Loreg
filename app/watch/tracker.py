"""Launch tracker: the catalog lines that stored news says a launch is coming for, the dates it gave, and whether a later source confirms it.

Code over what is already stored (market_events, market_items): no model, no second date parser, no web access. New news only arrives
through "Check for news" (app/watch/scan.py and its allowlisted fetcher), so that is the only way a launch can become confirmed.

- A watch derives from market_events that are not flagged, come from a dated item, and name a line the shop stocks (matched by code).
- An article that gives several different dates for one line, or no full date, records no date for it.
- The synthetic demo article has a watch of its own. A real article never dates or confirms it, and it never dates a real one.
- `tracker()` only reads. `sync()` writes, and only adds: new watches, new history rows and new notifications.
"""

import json
import sqlite3
from datetime import date

from app.core.launch_state import (ANNOUNCED, DATE_APPROXIMATE, DATE_CHANGED, DUE, LAUNCH_CONFIRMED, UNVERIFIED, StatedDate, confirms_launch,
                                   launch_state)
from app.watch.allowlist import check_url
from app.watch.attach import is_dated
from app.watch.common import NEWS
from app.watch.matching import match_lines
from app.watch.sanitize import INVISIBLE_FLAG

SYNTHETIC_LABEL = "Demo article (synthetic)"

# Notifications are short and carry no day count, so they stay true after the day they were written. Fixed templates.
NOTIFICATION = {
    "announced_no_date": "Loreg is following {line}. The stored news gives no exact launch day.",
    ANNOUNCED: "Loreg is following {line}. Launch date as written: {date}.",
    DATE_APPROXIMATE: "Loreg is following {line}. Launch date as written: {date}, with no year, so it is never treated as due.",
    DATE_CHANGED: "{line}: the launch date changed to {date}.",
    DUE: "{line}: the stated launch day, {date}, has come. No stored news says it launched.",
    UNVERIFIED: "{line}: no stored news confirms the launch stated for {date}. This does not mean it was delayed.",
    LAUNCH_CONFIRMED: "{line}: launch confirmed by a source dated {confirmed}.",
}
SYNTHETIC_PREFIX = "From the synthetic demo article. "


def _hard_flags(raw: str | None) -> list[str]:
    return [f for f in json.loads(raw or "[]") if f != INVISIBLE_FLAG]


def _shop_lines(conn: sqlite3.Connection, shop_id: int) -> dict:
    return {r["line_id"]: dict(r) for r in conn.execute(
        "SELECT line_id, MIN(brand) AS brand, MIN(line) AS line, MIN(line_label) AS label, MIN(category) AS category FROM products"
        " WHERE shop_id = ? AND line_id != 'unsorted' GROUP BY line_id", (shop_id,))}


def _observations(conn: sqlite3.Connection, lines: dict, news) -> dict:
    """(line_id, synthetic) -> {item_id: what that item says about the line}, oldest item first."""
    groups: dict = {}
    for r in conn.execute(
            "SELECT e.item_id, e.launch_date, e.launch_iso, e.date_stated, e.matched_line_ids, i.url, i.title, i.published_at, i.fetched_at,"
            " i.origin, i.domain, i.injection_flags, s.name AS source_name FROM market_events e JOIN market_items i ON i.id = e.item_id"
            " JOIN market_sources s ON s.id = i.source_id WHERE e.flagged = 0 AND i.origin IN ('feed', 'demo_synthetic')"
            " ORDER BY i.published_at, i.id, e.id"):
        synthetic = r["origin"] == "demo_synthetic"
        if not is_dated(r["published_at"]) or _hard_flags(r["injection_flags"]) or (not synthetic and check_url(r["url"], news.allowed_hosts)):
            continue
        for line_id in json.loads(r["matched_line_ids"]):
            if line_id not in lines:
                continue
            obs = groups.setdefault((line_id, synthetic), {}).setdefault(r["item_id"], {
                "url": r["url"], "title": r["title"], "published": r["published_at"], "fetched": r["fetched_at"], "domain": r["domain"],
                "origin": r["origin"], "name": r["source_name"], "dates": {}})
            if r["date_stated"] and r["launch_date"]:
                obs["dates"][r["launch_date"]] = r["launch_iso"]
    return groups


def _one_date(obs: dict) -> StatedDate | None:
    """The date an item gives for a line, only if it gives exactly one. Several different dates: none (it is unclear which holds)."""
    return StatedDate(*next(iter(obs["dates"].items()))) if len(obs["dates"]) == 1 else None


def _confirmation(conn: sqlite3.Connection, line: dict, current: StatedDate | None, th, news) -> dict | None:
    """The first real, allowlisted, unflagged item published on or after the stated day whose own stored words name the line and say
    it is on sale (confirm phrases, no blocking phrase). None for a date that is not an exact day."""
    if current is None or not current.exact:
        return None
    for it in conn.execute("SELECT i.*, s.name AS source_name, s.brand AS source_brand FROM market_items i JOIN market_sources s ON s.id = i.source_id"
                           " WHERE i.origin = 'feed' AND i.published_at IS NOT NULL ORDER BY i.published_at, i.id"):
        if not is_dated(it["published_at"]) or it["published_at"][:10] < current.iso:
            continue
        if _hard_flags(it["injection_flags"]) or check_url(it["url"], news.allowed_hosts):
            continue
        quotes = [q["quote"] or "" for q in conn.execute("SELECT quote FROM market_events WHERE item_id = ? AND flagged = 0", (it["id"],))]
        words = " ".join([it["title"] or "", it["excerpt"] or "", *quotes])
        if match_lines(it["source_brand"], words, [line]) and confirms_launch(words, th.launch_confirm_phrases, th.launch_block_phrases):
            return {"name": it["source_name"], "title": it["title"], "url": it["url"], "published": it["published_at"], "fetched": it["fetched_at"]}
    return None


def _watch_id(conn: sqlite3.Connection, shop_id: int, line_id: str, synthetic: bool) -> int | None:
    row = conn.execute("SELECT id FROM launch_watch WHERE shop_id = ? AND line_id = ? AND synthetic = ?", (shop_id, line_id, int(synthetic))).fetchone()
    return row["id"] if row else None


def _history(conn: sqlite3.Connection, watch_id: int | None) -> list[dict]:
    if watch_id is None:
        return []
    return [{"old_date": h["old_date"], "new_date": h["new_date"], "new_iso": h["new_iso"], "source_url": h["source_url"],
             "published": h["published_at"], "fetched": h["fetched_at"], "recorded": h["recorded_at"]}
            for h in conn.execute("SELECT * FROM launch_history WHERE watch_id = ? ORDER BY id", (watch_id,))]


def tracker(conn: sqlite3.Connection, shop_id: int, today: date, th, news=NEWS) -> list[dict]:
    """Every tracked line with its state on `today`. Reads only."""
    lines = _shop_lines(conn, shop_id)
    out = []
    for (line_id, synthetic), items in _observations(conn, lines, news).items():
        line = lines[line_id]
        watch_id = _watch_id(conn, shop_id, line_id, synthetic)
        history = _history(conn, watch_id)
        dated = [(o, _one_date(o)) for o in items.values() if _one_date(o)]
        source, current = dated[-1] if dated else (list(items.values())[-1], None)
        earlier = [d.text for _, d in dated[:-1]] + [h["new_date"] for h in history]
        confirmed = None if synthetic else _confirmation(conn, line, current, th, news)
        s = launch_state(current, earlier, date.fromisoformat(confirmed["published"][:10]) if confirmed else None, today, th.launch_grace_days)
        out.append({"watch_id": watch_id, "line_id": line_id, "line": line["label"], "brand": line["brand"], "category": line["category"],
                    "synthetic": synthetic, "label": SYNTHETIC_LABEL if synthetic else "",
                    "state": s.state, "text": s.text, "date": s.date, "date_iso": s.date_iso, "days_until": s.days_until, "confirmed_on": s.confirmed_on,
                    "source": {k: source[k] for k in ("name", "title", "url", "published", "fetched", "domain", "origin")},
                    "confirmation": confirmed, "history": history, "dated_sources": [(o, d) for o, d in dated]})
    out.sort(key=lambda w: (w["line"].lower(), w["synthetic"]))
    return out


def _public(w: dict) -> dict:
    return {k: v for k, v in w.items() if k != "dated_sources"}


def read(conn: sqlite3.Connection, shop_id: int, today: date, th, news=NEWS) -> list[dict]:
    return [_public(w) for w in tracker(conn, shop_id, today, th, news)]


def notification_text(w: dict) -> str:
    key = "announced_no_date" if w["state"] == ANNOUNCED and not w["date"] else w["state"]
    text = NOTIFICATION[key].format(line=w["line"], date=w["date"], confirmed=w["confirmed_on"])
    return SYNTHETIC_PREFIX + text if w["synthetic"] else text


def sync(conn: sqlite3.Connection, shop_id: int, today: date, th, ts: str, news=NEWS) -> dict:
    """Add what is new: a watch for each newly tracked line, a history row for each new (source, date), and a notification for each new
    (watch, state, date). Never updates or deletes a history row. Running it twice adds nothing. The caller holds the transaction."""
    added = {"watches": 0, "history": 0, "notifications": 0}
    for w in tracker(conn, shop_id, today, th, news):
        if w["watch_id"] is None:
            conn.execute("INSERT OR IGNORE INTO launch_watch (shop_id, line_id, synthetic, created_at) VALUES (?,?,?,?)",
                         (shop_id, w["line_id"], int(w["synthetic"]), ts))
            added["watches"] += 1
        watch_id = _watch_id(conn, shop_id, w["line_id"], w["synthetic"])
        last = conn.execute("SELECT new_date, new_iso FROM launch_history WHERE watch_id = ? ORDER BY id DESC LIMIT 1", (watch_id,)).fetchone()
        prev = (last["new_date"], last["new_iso"]) if last else (None, None)
        for obs, d in w["dated_sources"]:
            if d.text == prev[0]:
                continue
            added["history"] += conn.execute(
                "INSERT OR IGNORE INTO launch_history (watch_id, shop_id, old_date, old_iso, new_date, new_iso, source_url, published_at, fetched_at,"
                " recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (watch_id, shop_id, prev[0], prev[1], d.text, d.iso, obs["url"], obs["published"], obs["fetched"], ts)).rowcount
            prev = (d.text, d.iso)
    for w in tracker(conn, shop_id, today, th, news):                 # again, so the states see the history just added
        added["notifications"] += conn.execute(
            "INSERT OR IGNORE INTO notifications (shop_id, watch_id, state, date_key, text, created_at) VALUES (?,?,?,?,?,?)",
            (shop_id, w["watch_id"], w["state"], w["confirmed_on"] or w["date"] or "", notification_text(w), ts)).rowcount
    return added


def notifications(conn: sqlite3.Connection, shop_id: int, limit: int = 50) -> dict:
    unread = conn.execute("SELECT COUNT(*) AS n FROM notifications WHERE shop_id = ? AND read_at IS NULL", (shop_id,)).fetchone()["n"]
    items = [{"id": n["id"], "watch_id": n["watch_id"], "state": n["state"], "date": n["date_key"] or None, "text": n["text"],
              "created_at": n["created_at"], "read": n["read_at"] is not None, "synthetic": bool(n["synthetic"]),
              "label": SYNTHETIC_LABEL if n["synthetic"] else ""}
             for n in conn.execute("SELECT n.*, w.synthetic FROM notifications n JOIN launch_watch w ON w.id = n.watch_id WHERE n.shop_id = ?"
                                   " ORDER BY n.id DESC LIMIT ?", (shop_id, limit))]
    return {"unread": unread, "items": items}
