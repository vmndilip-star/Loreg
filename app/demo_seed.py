"""The demo account: the organization BigDeal, with the bundled synthetic stock list. Database only.

The account is made by a script (scripts/reset_demo.py), never by the app: no route calls create_demo_account() (a test checks it). Its
password is stored hashed, like every other. Demo mode (LOREG_DEMO=1) signs nobody in; it only lets the sign-in page fill the demo
credential in, and lets this one account reset its own data.

PUBLIC DEMO CREDENTIAL. The defaults below are printed in the README and shown on the sign-in page in demo mode. They protect nothing:
never put real data in the demo account. LOREG_DEMO_EMAIL and LOREG_DEMO_PASSWORD override them. This file and the README are the only
places the default password is written (a test checks that too).
"""

import os
import re
import sqlite3
from datetime import datetime

from app.auth import sessions
from app.core.accounts import LIMITS, _email
from app.core.passwords import hash_password, verify_password
from app.demo import demo_enabled, production
from app.watch.common import stamp

DEFAULT_EMAIL = "demo@gmail.com"
DEFAULT_PASSWORD = "Loreg@1234"
PROFILE = {"org_name": "BigDeal", "street": "100 Example Street", "city": "Springfield", "state": "ST", "zip": "00000", "country": "US"}
# a made-up address, like the stock list

# The shop's own rows a reset deletes, children before parents (foreign keys are on). Fixed statements: nothing is built into them.
CLEAR = ("DELETE FROM recommendations WHERE shop_id = ?", "DELETE FROM alerts WHERE shop_id = ?",
         "DELETE FROM daily_briefs WHERE shop_id = ?", "DELETE FROM agent_calls WHERE shop_id = ?", "DELETE FROM dq_issues WHERE shop_id = ?",
         "DELETE FROM classifications WHERE shop_id = ?", "DELETE FROM inventory_snapshots WHERE shop_id = ?",
         "DELETE FROM sales_events WHERE shop_id = ?", "DELETE FROM skus WHERE shop_id = ?", "DELETE FROM products WHERE shop_id = ?",
         "DELETE FROM uploads WHERE shop_id = ?")
# The shop's rows a reset keeps, and why.
KEPT = {"sessions": "whoever is signed in stays signed in",
        "audit_log": "the record of what happened, the reset included",
        "launch_history": "append-only by design: its triggers refuse a delete",
        "launch_watch": "launch_history and notifications refer to it",
        "notifications": "the tracker's record, never deleted by the app (tests/test_launch_storage.py); marked unread again"}
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def credentials() -> tuple[str, str]:
    """(email, password) of the demo account: LOREG_DEMO_EMAIL and LOREG_DEMO_PASSWORD, else the public defaults."""
    email = _email(os.environ.get("LOREG_DEMO_EMAIL") or DEFAULT_EMAIL)
    password = os.environ.get("LOREG_DEMO_PASSWORD") or DEFAULT_PASSWORD
    if _CONTROL.search(password) or not 1 <= len(password) <= LIMITS["password"][1]:
        raise ValueError(f"LOREG_DEMO_PASSWORD must be 1 to {LIMITS['password'][1]} characters, with no control characters")
    return email, password


def is_demo_shop(conn: sqlite3.Connection, shop_id: int | None) -> bool:
    """True only in demo mode, for the shop whose email is the demo account's."""
    if shop_id is None or not demo_enabled():
        return False
    row = sessions.shop_by_email(conn, credentials()[0])
    return row is not None and row["id"] == shop_id


def create_demo_account(conn: sqlite3.Connection, now: datetime) -> int:
    """SCRIPT ONLY. Make the demo account and return its shop id; if it already exists with the demo password, return it unchanged.

    The password skips signup's 12-character minimum (the public demo password is shorter); the signup route keeps that rule for
    everyone. An account that has the demo email but another password is never touched: ValueError. Refused under LOREG_ENV=production.
    The caller holds the transaction."""
    if production():
        raise ValueError("LOREG_ENV=production: no demo account is made")
    email, password = credentials()
    row = sessions.shop_by_email(conn, email)
    if row is not None:
        if row["password_hash"] and verify_password(password, row["password_hash"]):
            return row["id"]
        raise ValueError(f"{email} is already registered with another password, so it is not the demo account. Nothing was changed.")
    return sessions.create_shop(conn, {"email": email, **PROFILE}, hash_password(password), now)


def clear_shop_data(conn: sqlite3.Connection, shop_id: int, now: datetime) -> dict:
    """Delete the shop's own rows (CLEAR), mark its notifications unread again (as they were when first shown), put back the demo
    profile, and record the reset in the audit log. Returns rows deleted per table. Only for the demo account (the caller checks). The
    caller holds the transaction."""
    deleted = {sql.split()[2]: conn.execute(sql, (shop_id,)).rowcount for sql in CLEAR}
    conn.execute("UPDATE notifications SET read_at = NULL WHERE shop_id = ?", (shop_id,))
    conn.execute("UPDATE shops SET org_name = ?, name = ?, street = ?, city = ?, state = ?, zip = ?, country = ? WHERE id = ?",
                 (PROFILE["org_name"], PROFILE["org_name"], PROFILE["street"], PROFILE["city"], PROFILE["state"], PROFILE["zip"],
                  PROFILE["country"], shop_id))
    conn.execute("INSERT INTO audit_log (shop_id, at, actor, action, entity, entity_id) VALUES (?,?,?,?,?,?)",
                 (shop_id, stamp(now), "owner", "demo_reset", "shop", str(shop_id)))
    return deleted
