"""Loreg Watch routes: fetch real dated news on demand and list what is stored."""

from fastapi import APIRouter, Depends, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.api.deps_ai import get_http, get_llm
from app.watch import scan as watch

router = APIRouter(prefix="/api/watch")


@router.post("/scan")
def scan_now(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id), llm=Depends(get_llm), http=Depends(get_http)):
    """Fetch the configured feed once (robots.txt, conditional GET, 10 s timeout, 2 MB cap), store sanitized items,
    read launch events and match them to catalog lines. If the network is down the stored items are returned and the
    response says so. Nothing is invented: an alert only gets a source that really matched."""
    report = watch.scan(conn, shop_id, http=http, llm=llm, catalog=request.app.state.catalog)
    return {**report, "items": watch.list_items(conn, 20)}


@router.get("/items")
def items(limit: int = Query(50, ge=1, le=200), conn=Depends(get_conn)):
    """Stored items (title, URL, dates, flags, extracted events). Article text is never returned."""
    return {"items": watch.list_items(conn, limit)}
