"""Small helpers shared by the scan and the demo source. No model, no network."""

import sqlite3
from datetime import datetime, timezone

from app.watch.allowlist import load_news_config

NEWS = load_news_config()                 # config/news_sources.yaml: the allowed hosts and the feeds on them


def stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ")


def ensure_source(conn: sqlite3.Connection, shop_id: int, src: dict, ts: str):
    row = conn.execute("SELECT * FROM market_sources WHERE url = ?", (src["url"],)).fetchone()
    if row:
        return row
    conn.execute("INSERT INTO market_sources (shop_id, name, brand, url, kind, created_at) VALUES (?,?,?,?,?,?)",
                 (shop_id, src["name"], src["brand"], src["url"], src["kind"], ts))
    return conn.execute("SELECT * FROM market_sources WHERE url = ?", (src["url"],)).fetchone()
