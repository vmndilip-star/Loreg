"""Every /api request passes here first. Two checks, with no switch to turn either off:

1. Sign-in. Every /api route except the few in PUBLIC needs a live session; otherwise 401 (an expired session included). The signed-in
   shop is put on the request (request.state.shop_id), and every route reads its shop from there and nowhere else.
2. CSRF. Every POST, PUT, PATCH or DELETE that gets past (1), the streamed /api/ask included, must send X-CSRF-Token. With a session it
   must equal that session's token; signed out (sign-in, signup and sign-out only) it must equal the loreg_csrf cookie that
   GET /api/session set. Otherwise 403.

The static page files are not under /api and are served to anyone.
"""

import hmac
import json

from starlette.requests import Request

from app.auth import sessions
from app.storage.db import connect

PUBLIC = frozenset({"/api/health", "/api/session", "/api/signup", "/api/login", "/api/logout"})
UNSAFE = frozenset({"POST", "PUT", "PATCH", "DELETE"})


async def _refuse(send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"cache-control", b"no-store")]})
    await send({"type": "http.response.body", "body": body})


class AuthMiddleware:
    def __init__(self, app, state):
        self.app, self.state = app, state                # state: the FastAPI app.state (database path, clock, auth config)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        request = Request(scope)
        cfg = self.state.auth
        conn = connect(self.state.db_path)
        try:
            session = sessions.lookup(conn, request.cookies.get(cfg.session_cookie), self.state.clock())
        finally:
            conn.close()
        if session is None and scope["path"] not in PUBLIC:
            return await _refuse(send, 401, "Sign in first.")
        if scope["method"] in UNSAFE:
            expected = session["csrf_token"] if session else request.cookies.get(cfg.csrf_cookie, "")
            given = request.headers.get("x-csrf-token", "")
            if not expected or not given or not hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8")):
                return await _refuse(send, 403, "This request is missing its security token. Reload the page and try again.")
        scope.setdefault("state", {})
        scope["state"]["shop_id"] = session["shop_id"] if session else None
        scope["state"]["session_id"] = session["id"] if session else None
        return await self.app(scope, receive, send)
