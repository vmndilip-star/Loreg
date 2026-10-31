"""SQLite storage (WAL). Money is integer cents; dates are ISO text (YYYY-MM-DD). No ORM, parameterized SQL only.

`products` holds the catalog identity of everything in the shop (category, line, line_id, line_slug, year,
brand, model, variant and a normalized search_text) so browsing and search need no later migration.
`skus` holds the shop's current stock for each product. Every upload replaces the shop's current stock;
`inventory_snapshots` and `classifications` keep the history per upload.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "loreg.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS shops (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    email TEXT,                      -- lower case; unique (index made by the version 6 migration). NULL only for a shop made before accounts
    password_hash TEXT,              -- scrypt$N$r$p$salt$key (app/core/passwords.py); NULL: cannot sign in with a password (a shop from before accounts)
    org_name TEXT,                   -- the organization profile: plain text, trimmed, length-limited (app/core/accounts.py)
    street TEXT,
    city TEXT,
    state TEXT,
    zip TEXT,
    country TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    token_hash TEXT NOT NULL UNIQUE, -- SHA-256 of the session cookie's token; the token itself is never stored
    csrf_token TEXT NOT NULL,        -- must come back in X-CSRF-Token on every POST made with this session
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never reused, so a stale id cannot hit a new row
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    filename TEXT NOT NULL,
    uploaded_at TEXT NOT NULL,
    as_of TEXT NOT NULL,
    rule_version INTEGER NOT NULL,
    rows INTEGER NOT NULL,
    kept INTEGER NOT NULL,
    blocked INTEGER NOT NULL,
    look INTEGER NOT NULL,
    zero_qty INTEGER NOT NULL,
    unsorted INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS products (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    sku TEXT NOT NULL,
    category TEXT NOT NULL,
    line TEXT NOT NULL,            -- bare line name from data/catalog_lines.yaml; 'Unsorted' when no catalog match
    line_id TEXT NOT NULL,         -- unique key of the line; 'unsorted' when no catalog match
    line_slug TEXT NOT NULL,
    line_label TEXT NOT NULL,      -- display name built by code: "<line> (<year>)" only if the name repeats across years
    year INTEGER,                  -- release year of the line; NULL for Unsorted
    brand TEXT NOT NULL,
    model TEXT NOT NULL,
    variant TEXT NOT NULL,         -- size, storage, color or finish label
    search_text TEXT NOT NULL,     -- lowercased brand, model, line, variant, sku and category label
    launch_family INTEGER NOT NULL DEFAULT 0,
    matched INTEGER NOT NULL DEFAULT 1,
    UNIQUE (shop_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);
CREATE INDEX IF NOT EXISTS idx_products_line_slug ON products(line_slug);
CREATE INDEX IF NOT EXISTS idx_products_line_id ON products(line_id);

CREATE TABLE IF NOT EXISTS skus (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    product_id INTEGER NOT NULL REFERENCES products(id),
    sku TEXT NOT NULL,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    qty INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    sell_price_cents INTEGER NOT NULL,
    msrp_cents INTEGER,
    map_cents INTEGER,                      -- minimum advertised price, optional (blank in the upload = none)
    last_sale_date TEXT,
    received_date TEXT,
    units_sold_30d INTEGER NOT NULL,
    units_sold_90d INTEGER NOT NULL,
    is_seasonal INTEGER NOT NULL,
    supplier TEXT,
    return_window_end TEXT,
    status TEXT NOT NULL CHECK (status IN ('dead','slow','healthy','new','insufficient_data')),
    rule_version INTEGER NOT NULL,
    dsls INTEGER,
    capital_cents INTEGER NOT NULL,
    needs_look INTEGER NOT NULL DEFAULT 0,
    look_reason TEXT,
    UNIQUE (shop_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_skus_status ON skus(status);
CREATE INDEX IF NOT EXISTS idx_skus_product ON skus(product_id);

CREATE TABLE IF NOT EXISTS inventory_snapshots (
    id INTEGER PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    shop_id INTEGER REFERENCES shops(id),   -- the upload's shop (added in version 6; filled in for older rows)
    sku TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_cost_cents INTEGER NOT NULL,
    sell_price_cents INTEGER NOT NULL,
    status TEXT NOT NULL,
    capital_cents INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_snapshots_upload ON inventory_snapshots(upload_id);

CREATE TABLE IF NOT EXISTS classifications (
    id INTEGER PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    shop_id INTEGER REFERENCES shops(id),   -- the upload's shop (added in version 6; filled in for older rows)
    sku TEXT NOT NULL,
    status TEXT NOT NULL,
    rule_version INTEGER NOT NULL,
    dsls INTEGER,
    classified_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sales_events (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    sku TEXT NOT NULL,
    event_date TEXT NOT NULL,
    qty INTEGER NOT NULL,
    unit_price_cents INTEGER NOT NULL,
    event_type TEXT NOT NULL CHECK (event_type IN ('sale','return'))
);

CREATE TABLE IF NOT EXISTS dq_issues (
    id INTEGER PRIMARY KEY,
    upload_id INTEGER NOT NULL REFERENCES uploads(id),
    shop_id INTEGER REFERENCES shops(id),   -- the upload's shop (added in version 6; filled in for older rows)
    row_number INTEGER NOT NULL,
    sku TEXT,
    code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    quarantined INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dq_upload ON dq_issues(upload_id);

CREATE TABLE IF NOT EXISTS market_sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER REFERENCES shops(id),
    name TEXT NOT NULL,
    brand TEXT NOT NULL,
    url TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL,
    etag TEXT,                       -- for conditional GET
    last_modified TEXT,
    robots_allowed INTEGER,          -- cached robots.txt decision (NULL = never checked)
    robots_checked_at TEXT,
    last_fetch_at TEXT,
    last_status TEXT,
    last_ok_at TEXT,                 -- the last time the feed was reached and read (ok or not modified)
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES market_sources(id),
    url TEXT NOT NULL,
    canonical_url TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT NOT NULL,
    text TEXT NOT NULL,              -- the article body, held only until it has been read; then ''. Local database only; never committed.
    excerpt TEXT NOT NULL DEFAULT '',        -- at most 280 characters, an exact substring of the article. What is kept.
    domain TEXT,
    origin TEXT NOT NULL DEFAULT 'feed',     -- feed | stored_copy | demo_synthetic
    body_cleared_at TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    injection_flags TEXT NOT NULL DEFAULT '[]',
    untrusted INTEGER NOT NULL DEFAULT 1,
    extracted INTEGER NOT NULL DEFAULT 0   -- 0 not yet, 1 read by the Market agent, -1 skipped (no launch cue)
);

CREATE TABLE IF NOT EXISTS market_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id INTEGER NOT NULL REFERENCES market_items(id),
    brand TEXT NOT NULL,
    family TEXT NOT NULL,
    launch_date TEXT,                -- exactly as written in the article, only if it appears there word for word
    launch_iso TEXT,                 -- the same date as YYYY-MM-DD when it has a year
    date_stated INTEGER NOT NULL,
    quote TEXT,
    matched_line_ids TEXT NOT NULL DEFAULT '[]',
    flagged INTEGER NOT NULL DEFAULT 0,    -- came from an article with injection-like text: never attached automatically
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never reused, so a stale id cannot hit a new row
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    kind TEXT NOT NULL,
    title TEXT NOT NULL,
    brand TEXT NOT NULL,
    category TEXT NOT NULL,
    source_note TEXT NOT NULL,      -- says plainly when no dated source is attached
    n_items INTEGER NOT NULL,
    capital_cents INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    help_requested_at TEXT,         -- NULL until the owner asks for help; plans are refused until then
    source_item_id INTEGER,         -- the stored article behind this alert, if a real one matched
    source_name TEXT,
    source_url TEXT,
    source_title TEXT,
    source_published TEXT,
    launch_date TEXT,               -- as written in the article; NULL means announced, no date
    date_stated INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,   -- ids are never reused, so a stale id cannot hit a new row
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    alert_id INTEGER NOT NULL REFERENCES alerts(id),
    sku TEXT NOT NULL,
    action TEXT NOT NULL CHECK (action IN ('discount','bundle','return','hold')),
    discount_pct INTEGER NOT NULL,
    capital_cents INTEGER NOT NULL,
    margin_impact_cents INTEGER NOT NULL,
    reasons_json TEXT NOT NULL,
    citations_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed' CHECK (status IN ('proposed','accepted','rejected','expired')),
    created_at TEXT NOT NULL,
    decided_at TEXT,
    source TEXT NOT NULL DEFAULT 'rules',   -- rules | model | mock | fallback: who wrote the option and reasons
    UNIQUE (alert_id, sku)
);

CREATE TABLE IF NOT EXISTS daily_briefs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    generated_at TEXT NOT NULL,
    facts_hash TEXT NOT NULL,
    source TEXT NOT NULL,
    headline TEXT NOT NULL,
    bullets_json TEXT NOT NULL,
    citations_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER,
    at TEXT NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    entity TEXT NOT NULL,
    entity_id TEXT,
    detail_json TEXT
);
"""

# Launch tracker (schema version 5). A watch follows one catalog line. Its dates live only in launch_history, which can be added to
# but never updated or deleted (the triggers refuse both). Its state is worked out when it is read and is never stored.
LAUNCH_SCHEMA = """
CREATE TABLE IF NOT EXISTS launch_watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    line_id TEXT NOT NULL,                  -- the catalog line the news named, matched by code (app/watch/matching.py)
    synthetic INTEGER NOT NULL DEFAULT 0,   -- 1: follows only the synthetic demo article; real news never dates or confirms it
    created_at TEXT NOT NULL,
    UNIQUE (shop_id, line_id, synthetic)
);

CREATE TABLE IF NOT EXISTS launch_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    watch_id INTEGER NOT NULL REFERENCES launch_watch(id),
    shop_id INTEGER REFERENCES shops(id),   -- the watch's shop (added in version 6; filled in for older rows)
    old_date TEXT,                          -- as written; NULL for the first date
    old_iso TEXT,
    new_date TEXT NOT NULL,                 -- as written in the source
    new_iso TEXT,                           -- YYYY-MM-DD only for an exact day with a year
    source_url TEXT NOT NULL,
    published_at TEXT,
    fetched_at TEXT,
    recorded_at TEXT NOT NULL,
    UNIQUE (watch_id, source_url, new_date) -- one source and one date are recorded once, however often the news is checked
);
CREATE TRIGGER IF NOT EXISTS launch_history_no_update BEFORE UPDATE ON launch_history
BEGIN SELECT RAISE(ABORT, 'launch_history is append-only'); END;
CREATE TRIGGER IF NOT EXISTS launch_history_no_delete BEFORE DELETE ON launch_history
BEGIN SELECT RAISE(ABORT, 'launch_history is append-only'); END;

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    watch_id INTEGER NOT NULL REFERENCES launch_watch(id),
    state TEXT NOT NULL,
    date_key TEXT NOT NULL,                 -- the date the state is about, as written ('' when there is none)
    text TEXT NOT NULL,                     -- from a fixed template
    created_at TEXT NOT NULL,
    read_at TEXT,                           -- NULL: unread
    UNIQUE (watch_id, state, date_key)
);
"""
SCHEMA += LAUNCH_SCHEMA

# Version 7: one row per agent call, for the fallback-rate metric. A new table only; nothing else changes.
METRICS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    shop_id INTEGER NOT NULL REFERENCES shops(id),
    run_id TEXT NOT NULL,              -- one request: a chat answer, a brief, a plan or a news check
    at TEXT NOT NULL,
    pipeline TEXT NOT NULL,            -- launch_clear | sku_question | daily_brief | plan | watch_scan | none
    agent TEXT NOT NULL,               -- router | inventory | recommender | daily | market
    backend TEXT NOT NULL,             -- mock | meta | openai_compat
    outcome TEXT NOT NULL,             -- model_ok | retried_ok | fallback | mock
    ms INTEGER NOT NULL,
    cached INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_agent_calls_shop ON agent_calls(shop_id, id);
"""
SCHEMA += METRICS_SCHEMA

TABLES = ("shops", "products", "skus", "uploads", "inventory_snapshots", "sales_events", "dq_issues", "classifications",
          "market_sources", "market_items", "market_events", "alerts", "recommendations", "audit_log", "daily_briefs",
          "launch_watch", "launch_history", "notifications", "sessions", "agent_calls")
SCHEMA_VERSION = 7


def connect(path=None) -> sqlite3.Connection:
    p = Path(path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10, check_same_thread=False, isolation_level=None)  # we manage transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    return any(r["name"] == column for r in conn.execute(f"PRAGMA table_info({table})"))


def migrate(conn: sqlite3.Connection) -> None:
    """Bring a database made by an earlier version up to SCHEMA_VERSION. Version 2 adds Loreg Watch and the brief; version 3 adds skus.map_cents; version 4 keeps only an excerpt of each article;
    version 5 adds the launch tracker (its tables only: no existing row is changed); version 6 adds accounts, sessions and a shop_id on every
    table that holds one shop's rows; version 7 adds agent_calls (a new table only)."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version >= SCHEMA_VERSION:
        return
    if version < 2:
        if not _has_column(conn, "market_items", "canonical_url"):
            for t in ("market_events", "market_items", "market_sources"):  # version 1 had these empty and unused
                conn.execute(f"DROP TABLE IF EXISTS {t}")
        conn.executescript(SCHEMA)
        for col, decl in (("source_item_id", "INTEGER"), ("source_name", "TEXT"), ("source_url", "TEXT"), ("source_title", "TEXT"),
                          ("source_published", "TEXT"), ("launch_date", "TEXT"), ("date_stated", "INTEGER NOT NULL DEFAULT 0")):
            if not _has_column(conn, "alerts", col):
                conn.execute(f"ALTER TABLE alerts ADD COLUMN {col} {decl}")
        if not _has_column(conn, "recommendations", "source"):
            conn.execute("ALTER TABLE recommendations ADD COLUMN source TEXT NOT NULL DEFAULT 'rules'")
    if version < 3 and not _has_column(conn, "skus", "map_cents"):
        conn.execute("ALTER TABLE skus ADD COLUMN map_cents INTEGER")
    if version < 4:
        _migrate_to_4(conn)
    if version < 5:
        conn.executescript(LAUNCH_SCHEMA)
    if version < 6:
        _migrate_to_6(conn)
    if version < 7:
        conn.executescript(METRICS_SCHEMA)
    conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")


SHOP_COLUMNS = ("email", "password_hash", "org_name", "street", "city", "state", "zip", "country")
_HISTORY_TRIGGERS = ("CREATE TRIGGER IF NOT EXISTS launch_history_no_update BEFORE UPDATE ON launch_history"
                     " BEGIN SELECT RAISE(ABORT, 'launch_history is append-only'); END",
                     "CREATE TRIGGER IF NOT EXISTS launch_history_no_delete BEFORE DELETE ON launch_history"
                     " BEGIN SELECT RAISE(ABORT, 'launch_history is append-only'); END")


def _migrate_to_6(conn: sqlite3.Connection) -> None:
    """Accounts: profile and password columns on shops, a unique email, a sessions table, and shop_id on the four tables that held one
    shop's rows only through a parent (filled in from that parent). No row is added or removed and no other value changes.

    launch_history is append-only. Its triggers are dropped for the one UPDATE that fills in shop_id and created again in the same
    transaction; no date, address or other column is touched."""
    for col in SHOP_COLUMNS:
        if not _has_column(conn, "shops", col):
            conn.execute(f"ALTER TABLE shops ADD COLUMN {col} TEXT")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS shops_email_unique ON shops (lower(email)) WHERE email IS NOT NULL")
    conn.execute("BEGIN IMMEDIATE")
    try:
        for table in ("inventory_snapshots", "classifications", "dq_issues"):
            if not _has_column(conn, table, "shop_id"):
                conn.execute(f"ALTER TABLE {table} ADD COLUMN shop_id INTEGER REFERENCES shops(id)")
            conn.execute(f"UPDATE {table} SET shop_id = (SELECT u.shop_id FROM uploads u WHERE u.id = {table}.upload_id) WHERE shop_id IS NULL")
        if not _has_column(conn, "launch_history", "shop_id"):
            conn.execute("ALTER TABLE launch_history ADD COLUMN shop_id INTEGER REFERENCES shops(id)")
        if conn.execute("SELECT 1 FROM launch_history WHERE shop_id IS NULL LIMIT 1").fetchone():
            conn.execute("DROP TRIGGER IF EXISTS launch_history_no_update")
            conn.execute("UPDATE launch_history SET shop_id = (SELECT w.shop_id FROM launch_watch w WHERE w.id = launch_history.watch_id)"
                         " WHERE shop_id IS NULL")
        for ddl in _HISTORY_TRIGGERS:
            conn.execute(ddl)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _migrate_to_4(conn: sqlite3.Connection) -> None:
    """Keep an excerpt of each stored article and clear the body. An article not yet read can no longer be read: it is marked so."""
    from datetime import datetime, timezone
    from urllib.parse import urlsplit

    from app.watch.excerpt import make_excerpt

    for table, col, decl in (("market_sources", "last_ok_at", "TEXT"), ("market_items", "excerpt", "TEXT NOT NULL DEFAULT ''"),
                             ("market_items", "domain", "TEXT"), ("market_items", "origin", "TEXT NOT NULL DEFAULT 'feed'"),
                             ("market_items", "body_cleared_at", "TEXT")):
        if not _has_column(conn, table, col):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for it in conn.execute("SELECT id, url, text, extracted FROM market_items").fetchall():
        host = (urlsplit(it["url"]).hostname or "").rstrip(".") or None
        conn.execute("UPDATE market_items SET domain = ? WHERE id = ?", (host, it["id"]))
        if it["text"]:
            conn.execute("UPDATE market_items SET excerpt = ?, text = '', body_cleared_at = ?, extracted = CASE WHEN extracted = 0 THEN -2 ELSE extracted END"
                         " WHERE id = ?", (make_excerpt(it["text"]), now, it["id"]))
    conn.execute("UPDATE market_sources SET last_ok_at = last_fetch_at WHERE last_status IN ('ok', 'not_modified') AND last_ok_at IS NULL")


def init_db(path=None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
        migrate(conn)
    finally:
        conn.close()


def default_shop_id(conn: sqlite3.Connection, now: str) -> int:
    row = conn.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO shops (name, created_at) VALUES (?, ?)", ("Sample shop", now))
    return cur.lastrowid
