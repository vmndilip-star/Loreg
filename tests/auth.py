"""Signed-in clients for the tests. The app has no switch that turns sign-in or CSRF off, so the tests sign in the way the page does:
GET /api/session for a CSRF token, then sign up (or sign in, if that email exists), then send the session's token on every POST.

With LOREG_DEMO=1, GET /api/session signs the visitor in to the demo shop, so every client in a demo test sees the same demo data.
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
