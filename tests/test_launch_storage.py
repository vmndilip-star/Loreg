"""Schema version 5: the launch tracker's tables. History is append-only and survives a restart and a migration. Offline, no web."""

import re
import sqlite3
from pathlib import Path

import pytest

from app.storage.db import SCHEMA_VERSION, TABLES, init_db

REPO = Path(__file__).resolve().parent.parent


def db(path):
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    return con


def seed_watch(con):
    con.execute("INSERT INTO shops (name, created_at) VALUES ('s', 'x')")
    con.execute("INSERT INTO launch_watch (shop_id, line_id, synthetic, created_at) VALUES (1, 'samsung-qn90d-2024', 0, 'x')")
    con.execute("INSERT INTO launch_history (watch_id, new_date, new_iso, source_url, published_at, fetched_at, recorded_at)"
                " VALUES (1, 'November 20, 2026', '2026-11-20', 'https://news.samsung.com/global/synthetic-a', '2026-10-20T08:00:00Z', '2026-10-21T08:00:00Z', 'x')")
    con.commit()


def history(con):
    return [tuple(r) for r in con.execute("SELECT * FROM launch_history ORDER BY id")]


def test_a_new_database_is_at_version_5_with_the_tracker_tables(tmp_path):
    init_db(tmp_path / "n.sqlite")
    con = db(tmp_path / "n.sqlite")
    assert SCHEMA_VERSION == 5 and con.execute("PRAGMA user_version").fetchone()[0] == 5
    names = {r["name"] for r in con.execute("SELECT name FROM sqlite_master")}
    assert {"launch_watch", "launch_history", "notifications", "launch_history_no_update", "launch_history_no_delete"} <= names
    assert {"launch_watch", "launch_history", "notifications"} <= set(TABLES)


def test_history_can_be_added_to_but_never_changed_or_deleted(tmp_path):
    init_db(tmp_path / "h.sqlite")
    con = db(tmp_path / "h.sqlite")
    seed_watch(con)
    before = history(con)
    for sql in ("UPDATE launch_history SET new_date = 'December 1, 2026'", "UPDATE launch_history SET recorded_at = 'y' WHERE id = 1",
                "DELETE FROM launch_history", "DELETE FROM launch_history WHERE id = 1"):
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute(sql)
    assert history(con) == before


def test_the_same_source_and_date_are_recorded_once(tmp_path):
    init_db(tmp_path / "u.sqlite")
    con = db(tmp_path / "u.sqlite")
    seed_watch(con)
    again = ("INSERT OR IGNORE INTO launch_history (watch_id, new_date, new_iso, source_url, recorded_at)"
             " VALUES (1, 'November 20, 2026', '2026-11-20', 'https://news.samsung.com/global/synthetic-a', 'later')")
    assert con.execute(again).rowcount == 0 and len(history(con)) == 1
    with pytest.raises(sqlite3.IntegrityError):
        con.execute(again.replace("OR IGNORE ", ""))


def test_one_notification_per_watch_state_and_date(tmp_path):
    init_db(tmp_path / "n.sqlite")
    con = db(tmp_path / "n.sqlite")
    seed_watch(con)
    sql = ("INSERT OR IGNORE INTO notifications (shop_id, watch_id, state, date_key, text, created_at) VALUES (1, 1, ?, ?, 't', 'x')")
    added = [con.execute(sql, args).rowcount for args in (("due", "November 20, 2026"), ("due", "November 20, 2026"), ("due", "December 1, 2026"),
                                                          ("announced", "November 20, 2026"))]
    assert added == [1, 0, 1, 1]


def test_a_restart_keeps_the_history(tmp_path):
    path = tmp_path / "r.sqlite"
    init_db(path)
    con = db(path)
    seed_watch(con)
    before = history(con)
    con.close()
    init_db(path)
    init_db(path)
    assert history(db(path)) == before


def test_a_version_4_database_is_migrated_in_place_and_keeps_its_rows(tmp_path):
    path = tmp_path / "v4.sqlite"
    init_db(path)
    con = db(path)
    for t in ("launch_history_no_update", "launch_history_no_delete"):
        con.execute(f"DROP TRIGGER {t}")
    for t in ("notifications", "launch_history", "launch_watch"):
        con.execute(f"DROP TABLE {t}")
    con.execute("INSERT INTO shops (name, created_at) VALUES ('s', 'x')")
    con.execute("INSERT INTO market_sources (shop_id, name, brand, url, kind, created_at) VALUES (1, 'n', 'Samsung', 'https://news.samsung.com/global/feed', 'rss', 'x')")
    con.execute("INSERT INTO market_items (source_id, url, canonical_url, title, published_at, fetched_at, text, content_hash)"
                " VALUES (1, 'https://news.samsung.com/global/synthetic-1', 'https://news.samsung.com/global/synthetic-1', 'SYNTHETIC', '2026-10-19T08:00:00Z',"
                " '2026-10-20T08:00:00Z', '', 'h')")
    con.execute("PRAGMA user_version = 4")
    con.commit()
    items = [tuple(r) for r in con.execute("SELECT * FROM market_items")]
    con.close()
    init_db(path)
    con = db(path)
    assert con.execute("PRAGMA user_version").fetchone()[0] == 5
    assert [tuple(r) for r in con.execute("SELECT * FROM market_items")] == items
    seed = con.execute("INSERT INTO launch_watch (shop_id, line_id, synthetic, created_at) VALUES (1, 'l', 0, 'x')")
    con.execute("INSERT INTO launch_history (watch_id, new_date, source_url, recorded_at) VALUES (?, 'March 12, 2027', 'u', 'x')", (seed.lastrowid,))
    con.commit()
    with pytest.raises(sqlite3.DatabaseError, match="append-only"):
        con.execute("DELETE FROM launch_history")
    before = history(con)
    con.close()
    init_db(path)                                                       # running the migration again changes nothing
    assert history(db(path)) == before


def test_no_code_deletes_or_drops_tracker_rows():
    """Nothing in the app removes history: a reset means a new database file, which init_db creates from scratch."""
    pattern = re.compile(r"(DELETE\s+FROM|DROP\s+TABLE[^\n]*?)\s*(launch_history|launch_watch|notifications)\b", re.IGNORECASE)
    hits = [f"{p.relative_to(REPO)}" for p in (REPO / "app").rglob("*.py") if pattern.search(p.read_text(encoding="utf-8"))]
    assert hits == []
