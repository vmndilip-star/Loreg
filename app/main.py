"""ASGI entry point:  uvicorn app.main:app --reload   (env or a local .env: LOREG_DB, LOREG_AS_OF, LOREG_DEMO, LLM_BACKEND)."""

from app.api.main import create_app, load_env

load_env()                      # before the app is created, because it reads LOREG_AS_OF and LOREG_DEMO then
app = create_app()
