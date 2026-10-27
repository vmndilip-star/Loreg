"""ASGI entry point:  uvicorn app.main:app --reload   (env: LOREG_DB, LOREG_AS_OF, LLM_BACKEND)."""

from app.api.main import create_app

app = create_app()
