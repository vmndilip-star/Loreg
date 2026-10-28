"""Per-request wiring: a SQLite connection and the signed-in shop. No model client lives here."""

from fastapi import HTTPException, Request

from app.storage.db import connect


def get_conn(request: Request):
    conn = connect(request.app.state.db_path)
    try:
        yield conn
    finally:
        conn.close()


def get_shop_id(request: Request) -> int:
    """The signed-in shop, as the auth middleware found it from the session cookie (app/auth/middleware.py). Every route that reads or
    writes one shop's rows takes its shop from here, never from the request body or the address."""
    shop_id = getattr(request.state, "shop_id", None)
    if shop_id is None:
        raise HTTPException(401, "Sign in first.")
    return shop_id
