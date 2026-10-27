"""Per-request wiring: a SQLite connection and the default shop. No model client lives here."""

from fastapi import Request

from app.ingest import now_text
from app.storage.db import connect, default_shop_id


def get_conn(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_shop_id(request: Request) -> int:
    conn = connect(request.app.state.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        sid = default_shop_id(conn, now_text())
        conn.execute("COMMIT")
        return sid
    finally:
        conn.close()
