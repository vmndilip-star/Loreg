"""Accounts: create one, sign in, sign out, and read the signed-in organization's profile.

Messages are fixed and plain. A wrong email and a wrong password get the same answer, after the same work (an unknown email is checked
against a dummy hash). Signup says when an email is already registered (409): account existence can be inferred there, a stated limit.
Signup and sign-in are rate limited in memory (config/auth.yaml); the limits reset when the app restarts.
"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.api.deps import get_conn, get_shop_id
from app.auth import sessions
from app.core.accounts import LoginBody, SignupBody
from app.core.passwords import DUMMY_HASH, hash_password, verify_password
from app.demo import demo_enabled

router = APIRouter(prefix="/api")

WRONG = "Email or password is incorrect."
SLOW_DOWN = "Too many sign-in attempts. Try again later."
TAKEN = "That email is already registered. Sign in instead."
TOO_MANY_SIGNUPS = "Too many accounts were created from this address. Try again later."


def _client(request: Request) -> str:
    return request.client.host if request.client else "unknown"           # no proxy headers are trusted (a stated limit)


def _cookie(response, request: Request, name: str, value: str, max_age: int | None) -> None:
    response.set_cookie(name, value, max_age=max_age, path="/", httponly=True, samesite="lax", secure=request.url.scheme == "https")


def _signed_in(request: Request, conn, shop_id: int, status: int = 200) -> JSONResponse:
    """Start a session for the shop: set the cookie and return the profile and the session's CSRF token. An earlier session on this
    browser is ended, so a session id set before sign-in is never carried over."""
    s, cfg = request.app.state, request.app.state.auth
    sessions.end(conn, request.cookies.get(cfg.session_cookie))
    token, csrf = sessions.create(conn, shop_id, s.clock(), cfg.session_hours)
    response = JSONResponse({"signed_in": True, "csrf_token": csrf, "profile": sessions.profile(conn, shop_id), "demo": demo_enabled()},
                            status_code=status)
    _cookie(response, request, cfg.session_cookie, token, int(cfg.session_hours * 3600))
    response.delete_cookie(cfg.csrf_cookie, path="/")
    return response


async def _body(request: Request, model):
    try:
        data = await request.json()
    except ValueError:
        raise RequestValidationError([{"type": "json_invalid", "loc": ("body",), "msg": "send a JSON object", "input": None}]) from None
    try:
        return model.model_validate(data)
    except ValidationError as e:
        raise RequestValidationError(jsonable_encoder(e.errors(include_url=False, include_context=False, include_input=False))) from None


@router.get("/session")
def session(request: Request, conn=Depends(get_conn)):
    """Who is signed in, and the CSRF token the page must send with every POST. Signed out, a token for signing in is issued in a
    cookie. Nobody is ever signed in here: demo mode included, a visitor signs in with an email and a password like everyone else."""
    s, cfg = request.app.state, request.app.state.auth
    live = sessions.lookup(conn, request.cookies.get(cfg.session_cookie), s.clock())
    if live:
        return {"signed_in": True, "csrf_token": live["csrf_token"], "profile": sessions.profile(conn, live["shop_id"]), "demo": demo_enabled()}
    csrf = request.cookies.get(cfg.csrf_cookie) or sessions.new_token()
    response = JSONResponse({"signed_in": False, "csrf_token": csrf, "profile": None, "demo": False})
    _cookie(response, request, cfg.csrf_cookie, csrf, None)
    return response


@router.post("/signup")
async def signup(request: Request, conn=Depends(get_conn)):
    if not request.app.state.signup_limit.allow(f"ip:{_client(request)}"):
        raise HTTPException(429, TOO_MANY_SIGNUPS)
    body: SignupBody = await _body(request, SignupBody)
    hashed = hash_password(body.password)
    conn.execute("BEGIN IMMEDIATE")
    try:
        shop_id = sessions.create_shop(conn, body.profile(), hashed, request.app.state.clock())
        response = _signed_in(request, conn, shop_id, status=201)
        conn.execute("COMMIT")
    except sqlite3.IntegrityError:
        conn.execute("ROLLBACK")
        raise HTTPException(409, TAKEN) from None
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return response


@router.post("/login")
async def login(request: Request, conn=Depends(get_conn)):
    body: LoginBody = await _body(request, LoginBody)
    backoff = request.app.state.login_backoff
    keys = (f"ip:{_client(request)}", f"email:{body.email}")
    if max(backoff.wait_s(k) for k in keys) > 0:
        raise HTTPException(429, SLOW_DOWN)
    shop = sessions.shop_by_email(conn, body.email)
    stored = shop["password_hash"] if shop is not None else None
    ok = verify_password(body.password, stored or DUMMY_HASH) and stored is not None     # the same scrypt work whether or not the email exists
    if not ok:
        for k in keys:
            backoff.fail(k)
        raise HTTPException(401, WRONG)
    backoff.clear(keys[1])
    conn.execute("BEGIN IMMEDIATE")
    try:
        response = _signed_in(request, conn, shop["id"])
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return response


@router.post("/logout")
def logout(request: Request, conn=Depends(get_conn)):
    """Ends this browser's session (the row is deleted) and clears the cookie. Signed out already: nothing to do."""
    cfg = request.app.state.auth
    sessions.end(conn, request.cookies.get(cfg.session_cookie))
    csrf = sessions.new_token()
    response = JSONResponse({"signed_in": False, "csrf_token": csrf})
    response.delete_cookie(cfg.session_cookie, path="/")
    _cookie(response, request, cfg.csrf_cookie, csrf, None)
    return response


@router.get("/profile")
def profile(conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """The signed-in organization's own profile, as plain text. Never another shop's."""
    return sessions.profile(conn, shop_id)
