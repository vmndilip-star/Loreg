"""Signed-in clients for the scripts. The app signs nobody in (demo mode included), so a script signs in the way the page does:
GET /api/session for a CSRF token, then sign in (or up), then send the session's token on every POST.

Only for a database the script made itself (a throwaway one, or one it has just reset).
"""

import secrets

EMAIL = "script@example.com"
PROFILE = {"org_name": "Script shop (synthetic)", "street": "1 Example Road", "city": "Exampleville", "state": "EX", "zip": "00000",
           "country": "US"}


def _token(client) -> dict:
    return {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}


def _signed_in(client, r, expect: int) -> dict:
    if r.status_code != expect:
        raise SystemExit(f"Could not sign in: {r.status_code} {r.text[:200]}")
    s = r.json()
    client.headers["X-CSRF-Token"] = s["csrf_token"]
    return s


def sign_in(client) -> dict:
    """Sign `client` (a TestClient or httpx.Client) up as a made-up account whose random password is never shown."""
    r = client.post("/api/signup", json={"email": EMAIL, "password": secrets.token_urlsafe(24), **PROFILE}, headers=_token(client))
    return _signed_in(client, r, 201)


def sign_in_demo(client, app) -> dict:
    """Make the demo account (BigDeal) in `app`'s database if it is not there yet, then sign `client` in to it with the demo credential,
    as the sign-in page's "Use demo account" button would. Needs LOREG_DEMO=1. Returns the session answer."""
    from app import demo_seed
    from app.storage.db import connect
    conn = connect(app.state.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        try:
            demo_seed.create_demo_account(conn, app.state.clock())
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise
    finally:
        conn.close()
    cred = client.get("/api/demo")
    if cred.status_code != 200:
        raise SystemExit(f"The demo credential is not offered ({cred.status_code}): is LOREG_DEMO=1 set?")
    return _signed_in(client, client.post("/api/login", json=cred.json(), headers=_token(client)), 200)
