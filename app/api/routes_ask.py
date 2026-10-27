"""Chat and daily brief routes. Replies stream as server-sent events: status, tool, token, cards, done, error.

The first status event is sent before any model call. Cards are built by code from stored rows, never by a model.
Recommender cards appear only after the owner has asked for help on the alert.
"""

import json
import time
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_shop_id
from app.api.deps_ai import get_llm
from app.ingest import now_text
from app.pipelines import Ctx, daily_brief, answer
from app.storage import repo, repo_ai
from app.storage.db import connect

router = APIRouter(prefix="/api")


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=500)
    sku: str | None = Field(default=None, max_length=40, description="optional: ask about one item; an unknown SKU is rejected")


def sse(event: dict) -> str:
    return f"event: {event['event']}\ndata: {json.dumps(event['data'], ensure_ascii=False)}\n\n"


def make_ctx(request: Request, conn, shop_id: int, llm) -> Ctx:
    s = request.app.state
    up = repo.latest_upload(conn, shop_id)
    as_of = date.fromisoformat(up["as_of"]) if up else (s.default_as_of or date.today())
    return Ctx(conn=conn, shop_id=shop_id, th=s.th, as_of=as_of, llm=llm, cache=s.cache, executor=s.executor, timeout_s=s.agent_timeout_s)


@router.post("/ask")
def ask(body: AskBody, request: Request, shop_id: int = Depends(get_shop_id), llm=Depends(get_llm)):
    """Ask Loreg a question. Returns text/event-stream."""
    state = request.app.state
    if body.sku:
        check = connect(state.db_path)
        try:
            if repo_ai.item_row(check, shop_id, body.sku.strip()) is None:
                raise HTTPException(404, f"no item with SKU {body.sku.strip()} in your stock")
        finally:
            check.close()

    def stream():
        conn = connect(state.db_path)   # its own connection: the response outlives the request's dependencies
        try:
            ctx = make_ctx(request, conn, shop_id, llm)
            for event in answer(ctx, body.question, body.sku.strip() if body.sku else None):
                yield sse(event)
        except Exception as e:  # noqa: BLE001 - the stream must end with an event, not a broken connection
            yield sse({"event": "error", "data": {"message": f"something went wrong ({type(e).__name__}); nothing was changed"}})
        finally:
            conn.close()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.get("/daily")
def get_daily(shop_id: int = Depends(get_shop_id), request: Request = None):
    conn = connect(request.app.state.db_path)
    try:
        brief = repo_ai.latest_brief(conn, shop_id)
    finally:
        conn.close()
    if brief is None:
        raise HTTPException(404, "no brief has been generated yet; POST /api/daily/run to make one")
    return brief


@router.post("/daily/run")
def run_daily(request: Request, shop_id: int = Depends(get_shop_id), llm=Depends(get_llm)):
    """Regenerate the brief from today's numbers (the Daily agent, with its deterministic fallback)."""
    conn = connect(request.app.state.db_path)
    try:
        ctx = make_ctx(request, conn, shop_id, llm)
        started = time.perf_counter()
        events = list(daily_brief(ctx, started))
        brief = repo_ai.latest_brief(conn, shop_id)
        if brief is None:
            raise HTTPException(409, "there is nothing to summarise yet: upload a stock list first")
        end = events[-1]["data"]
        return {**brief, "agents": end["agents"], "total_ms": end["total_ms"]}
    finally:
        conn.close()
