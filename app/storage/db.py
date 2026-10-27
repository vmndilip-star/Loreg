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
    created_at TEXT NOT NULL
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
    row_number INTEGER NOT NULL,
    sku TEXT,
    code TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('error','warning','info')),
    quarantined INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_dq_upload ON dq_issues(upload_id);

CREATE TABLE IF NOT EXISTS market_sources (
    id INTEGER PRIMARY KEY,
    shop_id INTEGER REFERENCES shops(id),
    name TEXT NOT NULL,
    url TEXT NOT NULL,
    kind TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES market_sources(id),
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    published TEXT,
    fetched_at TEXT NOT NULL,
    text_hash TEXT NOT NULL,
    injection_flags TEXT
);

CREATE TABLE IF NOT EXISTS market_events (
    id INTEGER PRIMARY KEY,
    item_id INTEGER NOT NULL REFERENCES market_items(id),
    brand TEXT NOT NULL,
    family TEXT NOT NULL,
    launch_date TEXT,
    date_stated INTEGER NOT NULL,
    quote TEXT,
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
    help_requested_at TEXT          -- NULL until the owner asks for help; plans are refused until then
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
    UNIQUE (alert_id, sku)
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

TABLES = ("shops", "products", "skus", "uploads", "inventory_snapshots", "sales_events", "dq_issues", "classifications",
          "market_sources", "market_items", "market_events", "alerts", "recommendations", "audit_log")


def connect(path=None) -> sqlite3.Connection:
    p = Path(path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=10, check_same_thread=False, isolation_level=None)  # we manage transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def init_db(path=None) -> None:
    conn = connect(path)
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def default_shop_id(conn: sqlite3.Connection, now: str) -> int:
    row = conn.execute("SELECT id FROM shops ORDER BY id LIMIT 1").fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO shops (name, created_at) VALUES (?, ?)", ("Sample shop", now))
    return cur.lastrowid
