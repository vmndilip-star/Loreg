"""FastAPI app factory. The numbers routes never call a model; the watch, ask and daily routes use agents."""

import mimetypes
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from fastapi import FastAPI
from starlette.staticfiles import StaticFiles

from app.api.routes import router
from app.api.routes_browse import router as browse_router
from app.api.routes_demo import router as demo_router
from app.api.routes_ask import router as ask_router
from app.api.routes_watch import router as watch_router
from app.catalog import load_catalog
from app.core.thresholds import load_thresholds
from app.pipelines import AGENT_TIMEOUT_S, ResultCache
from app.storage.db import DEFAULT_DB_PATH, init_db

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
mimetypes.add_type("text/javascript", ".js")   # Windows registries sometimes say text/plain, which browsers refuse for module scripts


class WebFiles(StaticFiles):
    """The frontend, served from the same origin as the API. no-cache so a reload always shows the latest files."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


def create_app(db_path=None, as_of: str | date | None = None, thresholds_path=None, catalog_path=None, llm=None, http_factory=None,
               agent_timeout_s: float = AGENT_TIMEOUT_S, web_dir=WEB_DIR) -> FastAPI:
    """`llm`: a model client (default: built from LLM_BACKEND on first use). `http_factory`: returns an httpx.Client (tests inject a fake)."""
    app = FastAPI(title="Loreg API", version="0.2.0")
    path = Path(db_path or os.environ.get("LOREG_DB") or DEFAULT_DB_PATH)
    init_db(path)
    app.state.db_path = path
    app.state.th = load_thresholds(thresholds_path)
    app.state.catalog = load_catalog(catalog_path)
    app.state.llm = llm
    app.state.http_factory = http_factory
    app.state.cache = ResultCache()                       # agent results by hash of their facts
    app.state.executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="agent")
    app.state.agent_timeout_s = agent_timeout_s
    default = as_of or os.environ.get("LOREG_AS_OF")
    app.state.default_as_of = date.fromisoformat(default) if isinstance(default, str) and default else (default or None)
    app.include_router(router)
    app.include_router(demo_router)
    app.include_router(browse_router)
    app.include_router(watch_router)
    app.include_router(ask_router)
    if web_dir and Path(web_dir).is_dir():
        app.mount("/", WebFiles(directory=str(web_dir), html=True), name="web")   # last, so /api routes win
    return app
