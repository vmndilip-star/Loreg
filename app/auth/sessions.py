"""Sessions and shops for sign-in. Database only.

A session token is 256 random bits, given to the browser once in an HttpOnly cookie; only its SHA-256 is stored, so a copy of the
database cannot be used to sign in. Each session also has a CSRF token that every POST made with it must send back.
"""

import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone

from app.watch.common import stamp

PROFILE_FIELDS = ("email", "org_name", "street", "city", "state", "zip", "country")


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)                                        # 32 bytes = 256 bits


def create(conn: sqlite3.Connection, shop_id: int, now: datetime, hours: float) -> tuple[str, str]:
    """A new session for the shop. Returns (session token, CSRF token); only the session token's hash is stored."""
    token, csrf = new_token(), new_token()
    conn.execute("DELETE FROM sessions WHERE expires_at <= ?", (stamp(now),))            # expired sessions are of no use to anyone
    conn.execute("INSERT INTO sessions (shop_id, token_hash, csrf_token, created_at, expires_at) VALUES (?,?,?,?,?)",
                 (shop_id, token_hash(token), csrf, stamp(now), stamp(now + timedelta(hours=hours))))
    return token, csrf


def lookup(conn: sqlite3.Connection, token: str | None, now: datetime):
    """The live session for this cookie value, or None (no cookie, unknown token, or expired)."""
    if not token or len(token) > 200:
        return None
    return conn.execute("SELECT * FROM sessions WHERE token_hash = ? AND expires_at > ?", (token_hash(token), stamp(now))).fetchone()


def end(conn: sqlite3.Connection, token: str | None) -> None:
    if token:
        conn.execute("DELETE FROM sessions WHERE token_hash = ?", (token_hash(token),))


def profile(conn: sqlite3.Connection, shop_id: int) -> dict:
    row = conn.execute(f"SELECT {', '.join(PROFILE_FIELDS)} FROM shops WHERE id = ?", (shop_id,)).fetchone()
    return {k: row[k] for k in PROFILE_FIELDS} if row else {}


def shop_by_email(conn: sqlite3.Connection, email: str):
    return conn.execute("SELECT * FROM shops WHERE lower(email) = lower(?)", (email,)).fetchone()


def create_shop(conn: sqlite3.Connection, fields: dict, password_hash: str | None, now: datetime) -> int:
    """Raises sqlite3.IntegrityError if the email is already registered (the unique index decides, so two signups cannot race)."""
    return conn.execute(
        "INSERT INTO shops (name, created_at, email, password_hash, org_name, street, city, state, zip, country) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (fields["org_name"], stamp(now), fields["email"], password_hash, fields["org_name"], fields["street"], fields["city"], fields["state"],
         fields["zip"], fields["country"])).lastrowid


def utc(now: datetime) -> datetime:
    return now.astimezone(timezone.utc)
