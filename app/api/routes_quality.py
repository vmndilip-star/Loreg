"""Which backend answers, and how often its answers were replaced by the rules. Read-only; never builds or calls a model client."""

import os

from fastapi import APIRouter, Depends, Request

from app.api.deps import get_conn, get_shop_id
from app.storage import repo

router = APIRouter(prefix="/api")

BACKEND_LABEL = {"mock": "mock (offline test writer, no model)", "meta": "meta", "openai_compat": "openai_compat"}


def backend_name(request: Request) -> str:
    """The backend the app will use: the built client's, else LLM_BACKEND (the client is built on first use, never here)."""
    llm = request.app.state.llm
    if llm is not None:
        return getattr(llm, "backend", None) or ("mock" if getattr(llm, "is_mock", True) else "unknown")
    return (os.environ.get("LLM_BACKEND") or "mock").strip().lower()


@router.get("/quality")
def quality(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """The backend, and over this shop's recent agent calls: how many used the model's answer (model_ok), needed one retry (retried_ok),
    fell back to the rules (fallback) or came from the offline test writer (mock). fallback_rate counts only calls a real model was asked."""
    name = backend_name(request)
    return {"backend": name, "backend_label": BACKEND_LABEL.get(name, name), "is_mock": name == "mock", **repo.agent_quality(conn, shop_id)}
