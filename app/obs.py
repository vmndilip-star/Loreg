"""Structured logs: one JSON line per request and per agent call, on the "loreg" logger (stderr).

A request gets an id (also sent back as X-Request-ID); agent-call lines carry it, with the backend, the agent, the outcome
(model_ok | retried_ok | fallback | mock) and the latency. Never logged: prompts, model answers, article text, cookies, tokens,
passwords, keys or query strings. LOREG_LOG_LEVEL sets the level (default INFO; WARNING silences these lines).
"""

import contextvars
import json
import logging
import os
import sys
import time
import uuid

log = logging.getLogger("loreg")
request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar("loreg_request_id", default=None)


def setup() -> None:
    if not log.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        log.addHandler(handler)
        log.propagate = False
    log.setLevel((os.environ.get("LOREG_LOG_LEVEL") or "INFO").upper())


def event(kind: str, **fields) -> None:
    """One JSON line. Callers pass only names, numbers and fixed codes."""
    if log.isEnabledFor(logging.INFO):
        log.info(json.dumps({"ts": round(time.time(), 3), "kind": kind, "request_id": request_id.get(), **fields}, default=str))


class RequestLog:
    """ASGI middleware: gives each HTTP request an id, sends it back as X-Request-ID and logs method, path (no query string), status
    and latency when the response has been sent (a streamed answer included)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        rid = uuid.uuid4().hex[:12]
        token = request_id.set(rid)
        started, status = time.perf_counter(), 500

        async def send_with_id(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message.setdefault("headers", [])
                message["headers"] = list(message["headers"]) + [(b"x-request-id", rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            if scope.get("path", "").startswith("/api/"):
                event("request", method=scope.get("method"), path=scope.get("path"), status=status,
                      ms=round(1000 * (time.perf_counter() - started)))
            request_id.reset(token)
