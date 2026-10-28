"""Browse and search the stock. All GET, all code and database: no model is involved anywhere in this file."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.storage import browse

router = APIRouter(prefix="/api")


def _unprocessable(e: browse.BadRequest) -> HTTPException:
    return HTTPException(422, str(e))


@router.get("/categories")
def categories(conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Per category: items, money tied up, dead, slow and at-risk counts and money. Biggest at-risk money first."""
    return {"categories": browse.categories(conn, shop_id)}


@router.get("/products")
def products(request: Request, category: str | None = Query(None, max_length=40), status: str = Query("all", max_length=20), q: str | None = Query(None, max_length=200),
             sort: str = Query(browse.DEFAULT_SORT, max_length=20), page: int = Query(1, ge=1, le=100_000),
             page_size: int = Query(browse.DEFAULT_PAGE_SIZE, ge=1, le=browse.MAX_PAGE_SIZE), conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """One page of stock, with totals for the current filter. status: all|dead|slow|healthy|new. sort: tied_up|name|qty|days_since_sold."""
    if category and category not in browse.known_categories(conn, shop_id):
        raise HTTPException(404, f"no category {category!r} in your stock")
    try:
        return browse.products(conn, shop_id, request.app.state.th, category=category, status=status, q=q, sort=sort, page=page, page_size=page_size)
    except browse.BadRequest as e:
        raise _unprocessable(e) from None


@router.get("/search")
def search(request: Request, q: str = Query("", max_length=400), conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Up to 5 product lines and 20 items. Words are ANDed, case does not matter, and % _ ' " ; are plain text."""
    try:
        return browse.search(conn, shop_id, request.app.state.th, q)
    except browse.BadRequest as e:
        raise _unprocessable(e) from None
