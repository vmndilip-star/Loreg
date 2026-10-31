"""A signed-in client for the scripts. The app signs nobody in (demo mode included), so a script signs in the way the page does:
GET /api/session for a CSRF token, then sign up, then send the session's token on every POST.

Only for a database the script made itself (a throwaway one, or one it has just reset). The account is made up and its password is
random and never shown, so nobody signs in to it later.
"""

import secrets

EMAIL = "script@example.com"
PROFILE = {"org_name": "Script shop (synthetic)", "street": "1 Example Road", "city": "Exampleville", "state": "EX", "zip": "00000",
           "country": "US"}


def sign_in(client) -> dict:
    """Sign `client` (a TestClient or httpx.Client) up as the made-up script account. Returns the session answer."""
    token = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
    r = client.post("/api/signup", json={"email": EMAIL, "password": secrets.token_urlsafe(24), **PROFILE}, headers=token)
    if r.status_code != 201:
        raise SystemExit(f"Could not sign in: {r.status_code} {r.text[:200]}")
    s = r.json()
    client.headers["X-CSRF-Token"] = s["csrf_token"]
    return s
