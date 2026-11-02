"""Schema version 6: accounts, sessions and a shop_id on every table that holds one shop's rows. A version 5 database is migrated in place:
no row is added or removed, the launch history keeps every date and stays append-only."""

import sqlite3

import pytest

from app.storage.db import SCHEMA_VERSION, SHOP_COLUMNS, init_db
from auth import AuthedClient

CHILD_TABLES = ("inventory_snapshots", "classifications", "dq_issues", "launch_history")


def db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def cols(con, table):
    return {r["name"] for r in con.execute(f"PRAGMA table_info({table})")}


def test_a_new_database_is_at_version_6_with_accounts_and_sessions(tmp_path):
    init_db(tmp_path / "n.sqlite")
    con = db(tmp_path / "n.sqlite")
    assert SCHEMA_VERSION >= 6 and con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION       # 7 added agent_calls only
    assert set(SHOP_COLUMNS) <= cols(con, "shops")
    assert {"token_hash", "csrf_token", "expires_at", "shop_id"} <= cols(con, "sessions") and "token" not in cols(con, "sessions")
    assert all("shop_id" in cols(con, t) for t in CHILD_TABLES)


def test_an_email_is_unique_whatever_its_case_and_shops_without_one_are_allowed(tmp_path):
    init_db(tmp_path / "u.sqlite")
    con = db(tmp_path / "u.sqlite")
    con.execute("INSERT INTO shops (name, created_at, email) VALUES ('a', 'x', 'owner@example.com')")
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("INSERT INTO shops (name, created_at, email) VALUES ('b', 'x', 'Owner@Example.com')")
    con.execute("INSERT INTO shops (name, created_at) VALUES ('legacy 1', 'x')")
    con.execute("INSERT INTO shops (name, created_at) VALUES ('legacy 2', 'x')")


def version_5_database(path):
    """A database as version 5 left it: no account columns, no sessions, no shop_id on the child tables, with rows in all of them."""
    init_db(path)
    con = db(path)
    con.execute("DROP INDEX shops_email_unique")
    con.execute("DROP TABLE sessions")
    for c in SHOP_COLUMNS:
        con.execute(f"ALTER TABLE shops DROP COLUMN {c}")
    for t in CHILD_TABLES:
        con.execute(f"ALTER TABLE {t} DROP COLUMN shop_id")
    for shop in (1, 2):
        con.execute("INSERT INTO shops (id, name, created_at) VALUES (?, ?, 'x')", (shop, f"shop {shop}"))
        up = con.execute("INSERT INTO uploads (shop_id, filename, uploaded_at, as_of, rule_version, rows, kept, blocked, look, zero_qty, unsorted)"
                         " VALUES (?, 'f.csv', 'x', '2026-10-26', 1, 1, 1, 0, 0, 0, 0)", (shop,)).lastrowid
        con.execute("INSERT INTO inventory_snapshots (upload_id, sku, qty, unit_cost_cents, sell_price_cents, status, capital_cents)"
                    " VALUES (?, ?, 1, 100, 200, 'dead', 100)", (up, f"S{shop}"))
        con.execute("INSERT INTO classifications (upload_id, sku, status, rule_version, dsls, classified_at) VALUES (?, ?, 'dead', 1, 200, 'x')",
                    (up, f"S{shop}"))
        con.execute("INSERT INTO dq_issues (upload_id, row_number, sku, code, severity, quarantined) VALUES (?, 2, ?, 'NEG_QTY', 'error', 1)",
                    (up, f"S{shop}"))
        w = con.execute("INSERT INTO launch_watch (shop_id, line_id, synthetic, created_at) VALUES (?, 'l', 0, 'x')", (shop,)).lastrowid
        con.execute("INSERT INTO launch_history (watch_id, new_date, new_iso, source_url, published_at, fetched_at, recorded_at)"
                    " VALUES (?, 'November 20, 2026', '2026-11-20', 'https://news.samsung.com/global/a', 'p', 'f', 'r')", (w,))
        con.execute("INSERT INTO launch_history (watch_id, old_date, old_iso, new_date, new_iso, source_url, recorded_at)"
                    " VALUES (?, 'November 20, 2026', '2026-11-20', 'December 4, 2026', '2026-12-04', 'https://news.samsung.com/global/b', 'r2')", (w,))
    con.execute("PRAGMA user_version = 5")
    con.commit()
    con.close()


def rows_without_shop(con, table):
    return [tuple(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY id")]


def test_a_version_5_database_is_migrated_in_place_and_keeps_every_row_and_date(tmp_path):
    path = tmp_path / "v5.sqlite"
    version_5_database(path)
    con = db(path)
    before = {t: rows_without_shop(con, t) for t in (*CHILD_TABLES, "shops", "uploads", "launch_watch")}
    con.close()
    init_db(path)
    con = db(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    for t in CHILD_TABLES:
        assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE shop_id IS NULL").fetchone()[0] == 0, t
        got = [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY id")]
        names = [r["name"] for r in con.execute(f"PRAGMA table_info({t})")]
        without = [tuple(v for n, v in zip(names, row) if n != "shop_id") for row in got]
        assert without == before[t], t                                                  # nothing but shop_id was filled in
    assert [r[0] for r in con.execute("SELECT shop_id FROM dq_issues ORDER BY id")] == [1, 2]
    assert [r[0] for r in con.execute("SELECT shop_id FROM launch_history ORDER BY id")] == [1, 1, 2, 2]
    assert [r["new_date"] for r in con.execute("SELECT new_date FROM launch_history ORDER BY id")] == ["November 20, 2026", "December 4, 2026"] * 2
    for sql in ("UPDATE launch_history SET new_date = 'x'", "DELETE FROM launch_history"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)
    assert set(SHOP_COLUMNS) <= cols(con, "shops") and con.execute("SELECT COUNT(*) FROM shops WHERE email IS NULL").fetchone()[0] == 2
    migrated = {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY id")] for t in CHILD_TABLES}
    con.close()
    init_db(path)                                                                       # running it again changes nothing
    con = db(path)
    assert {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY id")] for t in CHILD_TABLES} == migrated


def test_new_rows_carry_their_shop_id(tmp_path, catalog_dir):
    from fastapi.testclient import TestClient
    from app.api.main import create_app
    c = AuthedClient(create_app(tmp_path / "r.sqlite", as_of="2026-10-26"))
    with open(catalog_dir / "upload_demo_200.csv", "rb") as f:
        assert c.post("/api/upload", files={"file": ("upload_demo_200.csv", f, "text/csv")}).status_code == 200
    con = db(tmp_path / "r.sqlite")
    for t in ("inventory_snapshots", "classifications", "dq_issues"):
        assert con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] > 0 or t == "dq_issues"
        assert con.execute(f"SELECT COUNT(*) FROM {t} WHERE shop_id IS NULL").fetchone()[0] == 0, t


def test_version_7_adds_only_the_agent_calls_table(tmp_path):
    from app.storage.db import connect
    path = tmp_path / "v6.sqlite"
    init_db(path)
    con = connect(path)
    con.execute("DROP TABLE agent_calls")
    con.execute("PRAGMA user_version = 6")
    before = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    init_db(path)
    con = connect(path)
    after = {r["name"] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert after - before == {"agent_calls"} and con.execute("PRAGMA user_version").fetchone()[0] == 7
    con.close()
