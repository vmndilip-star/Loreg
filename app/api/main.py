"""FastAPI app factory for the numbers API (no model calls)."""

import os
from datetime import date
from pathlib import Path

from fastapi import FastAPI

from app.api.routes import router
from app.catalog import load_catalog
from app.core.thresholds import load_thresholds
from app.storage.db import DEFAULT_DB_PATH, init_db


def create_app(db_path=None, as_of: str | date | None = None, thresholds_path=None, catalog_path=None) -> FastAPI:
    app = FastAPI(title="Loreg numbers API", version="0.1.0")
    path = Path(db_path or os.environ.get("LOREG_DB") or DEFAULT_DB_PATH)
    init_db(path)
    app.state.db_path = path
    app.state.th = load_thresholds(thresholds_path)
    app.state.catalog = load_catalog(catalog_path)
    default = as_of or os.environ.get("LOREG_AS_OF")
    app.state.default_as_of = date.fromisoformat(default) if isinstance(default, str) and default else (default or None)
    app.include_router(router)
    return app
