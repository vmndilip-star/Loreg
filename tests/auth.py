"""Signed-in clients for the tests. The app has no switch that turns sign-in or CSRF off, so the tests sign in the way the page does:
GET /api/session for a CSRF token, then sign up (or sign in, if that email exists), then send the session's token on every POST.

Demo mode (LOREG_DEMO=1) signs nobody in, so a client in a demo test signs in the same way.
Every address and name here is made up (example.com is reserved for examples)."""

from fastapi.testclient import TestClient

OWNER = "owner@example.com"
PASSWORD = "test password, not a real one"
PROFILE = {"org_name": "Example Electronics", "street": "100 Example Street", "city": "Springfield", "state": "ST", "zip": "00000", "country": "US"}


def sign_in(client, email: str = OWNER, password: str = PASSWORD, profile: dict | None = None) -> dict:
    """Signs `client` (a TestClient or an httpx.Client) in and makes it send the CSRF token from then on. Returns the session answer."""
    s = client.get("/api/session").json()
    if not s["signed_in"]:
        token = {"X-CSRF-Token": s["csrf_token"]}
        r = client.post("/api/signup", json={"email": email, "password": password, **(profile or PROFILE)}, headers=token)
        if r.status_code == 409:                                   # this database already has the account: sign in to it
            r = client.post("/api/login", json={"email": email, "password": password}, headers=token)
        assert r.status_code in (200, 201), r.text
        s = r.json()
    client.headers["X-CSRF-Token"] = s["csrf_token"]
    return s


class AuthedClient(TestClient):
    """A TestClient that is signed in from the start (as OWNER unless `email` says otherwise). Tests that move the injected clock
    weeks ahead outlive the 7-day session; then, like a person, the client signs in again (same account) and repeats the request."""

    def __init__(self, app, *args, email: str = OWNER, profile: dict | None = None, **kwargs):
        super().__init__(app, *args, **kwargs)
        self.email, self.profile = email, profile
        self.session = sign_in(self, email, profile=profile)

    def request(self, method, url, *args, **kwargs):
        r = super().request(method, url, *args, **kwargs)
        if r.status_code == 401 and r.json().get("detail") == "Sign in first." and hasattr(self, "email"):
            self.session = sign_in(self, self.email, profile=self.profile)
            r = super().request(method, url, *args, **kwargs)
        return r


def seed_demo(app) -> int:
    """Make the demo account (BigDeal) in `app`'s database, as scripts/reset_demo.py does. Returns its shop id."""
    from app import demo_seed
    from app.storage.db import connect
    conn = connect(app.state.db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        shop = demo_seed.create_demo_account(conn, app.state.clock())
        conn.execute("COMMIT")
        return shop
    finally:
        conn.close()


def sign_in_demo(client) -> dict:
    """Sign `client` in to the demo account the way the page does after "Use demo account" and "Sign in": the credential from
    GET /api/demo (demo mode only), then POST /api/login. Returns the session answer."""
    token = {"X-CSRF-Token": client.get("/api/session").json()["csrf_token"]}
    cred = client.get("/api/demo")
    assert cred.status_code == 200, "the demo credential is offered only with LOREG_DEMO=1"
    r = client.post("/api/login", json=cred.json(), headers=token)
    assert r.status_code == 200, r.text
    s = r.json()
    client.headers["X-CSRF-Token"] = s["csrf_token"]
    return s
