"""FastAPI app factory. The numbers routes never call a model; the watch, ask and daily routes use agents."""

import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.api.routes import router
from app.api.routes_browse import router as browse_router
from app.api.routes_demo import router as demo_router
from app.api.routes_plan import router as plan_router
from app.api.routes_ask import router as ask_router
from app.api.routes_watch import router as watch_router
from app.api.routes_tracker import router as tracker_router
from app.api.routes_auth import router as auth_router
from app.api.routes_quality import router as quality_router
from app.api.routes_mapping import router as mapping_router
from app.auth.config import cookie_secure, load_auth_config
from app.auth.middleware import AuthMiddleware
from app.core.throttle import Backoff, Window
from app.catalog import load_catalog
from app.core.thresholds import load_thresholds
from app import demo_seed, obs
from app.demo import demo_enabled, production
from app.pipelines import AGENT_TIMEOUT_S, REQUEST_TIMEOUT_S, ResultCache
from app.storage.db import DEFAULT_DB_PATH, init_db

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path=ENV_FILE) -> None:
    """Read a local .env into the environment (values already set in the environment win). Without this, LOREG_AS_OF and LOREG_DEMO in .env
    would be read too late: the app reads them when it is created. The file is never committed and its values are never printed."""
    from dotenv import load_dotenv
    load_dotenv(path)

mimetypes.add_type("text/javascript", ".js")   # Windows registries sometimes say text/plain, which browsers refuse for module scripts


class WebFiles(StaticFiles):
    """The frontend, served from the same origin as the API. no-cache so a reload always shows the latest files."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(db_path=None, as_of: str | date | None = None, thresholds_path=None, catalog_path=None, llm=None, http_factory=None,
               agent_timeout_s: float = AGENT_TIMEOUT_S, web_dir=WEB_DIR, clock=None, auth_path=None, request_timeout_s: float | None = None) -> FastAPI:
    """`clock`: a function returning the current time (timezone-aware). Tests inject one; news freshness and fetch dates use it.
    `llm`: a model client (default: built from LLM_BACKEND on first use). `http_factory`: returns an httpx.Client (tests inject a fake)."""
    if production() and demo_enabled():
        raise RuntimeError("LOREG_ENV=production refuses LOREG_DEMO=1: demo mode offers a public sign-in. Unset one of them.")
    docs = {} if not production() else {"docs_url": None, "redoc_url": None, "openapi_url": None}     # no API map on a public host
    app = FastAPI(title="Loreg API", version="0.2.0", **docs)
    path = Path(db_path or os.environ.get("LOREG_DB") or DEFAULT_DB_PATH)
    init_db(path)
    app.state.db_path = path
    app.state.th = load_thresholds(thresholds_path)
    app.state.catalog = load_catalog(catalog_path)
    app.state.llm = llm
    app.state.http_factory = http_factory
    app.state.clock = clock or (lambda: datetime.now(timezone.utc))
    app.state.cache = ResultCache()                       # agent results by hash of their facts
    app.state.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent")
    app.state.agent_timeout_s = agent_timeout_s
    app.state.request_timeout_s = float(request_timeout_s or os.environ.get("LOREG_REQUEST_TIMEOUT_S") or REQUEST_TIMEOUT_S)
    obs.setup()
    default = as_of or os.environ.get("LOREG_AS_OF")
    app.state.default_as_of = date.fromisoformat(default) if isinstance(default, str) and default else (default or None)
    if demo_enabled():
        demo_seed.credentials()                           # a bad LOREG_DEMO_EMAIL or _PASSWORD stops the app here, not at a sign-in
    cfg = app.state.auth = load_auth_config(auth_path)
    app.state.cookie_secure = cookie_secure(os.environ.get("LOREG_COOKIE_SECURE"), production())
    seconds = lambda: app.state.clock().timestamp()       # noqa: E731  (the limits follow the same injectable clock)
    app.state.signup_limit = Window(cfg.signup_max_per_ip, cfg.signup_window_s, seconds)
    app.state.login_backoff = Backoff(cfg.login_free_failures, cfg.login_base_wait_s, cfg.login_max_wait_s, cfg.login_window_s, seconds)
    app.add_middleware(AuthMiddleware, state=app.state)   # sign-in and CSRF on every /api route; there is no switch to turn it off
    app.add_middleware(obs.RequestLog)                    # added last, so it runs first: every request gets an id and one log line
    app.include_router(auth_router)
    app.include_router(router)
    app.include_router(demo_router)
    app.include_router(browse_router)
    app.include_router(plan_router)
    app.include_router(watch_router)
    app.include_router(tracker_router)
    app.include_router(ask_router)
    app.include_router(quality_router)
    app.include_router(mapping_router)
    if web_dir and Path(web_dir).is_dir():
        app.mount("/", WebFiles(directory=str(web_dir), html=True), name="web")   # last, so /api routes win
    return app
