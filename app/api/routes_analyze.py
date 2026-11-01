"""The Analyze page: one category's figures, its sales history and what to buy in it. All GET, all code and database: no model is
involved anywhere in this file. A category the shop does not stock is 404, as for /api/products."""

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.storage import analyze

router = APIRouter(prefix="/api")

CATEGORY = Path(..., max_length=40)


@router.get("/categories/{category}")
def category_stats(category: str = CATEGORY, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Items, units, money tied up and at risk, units sold in 30 and 90 days, the split by status and by brand."""
    try:
        return analyze.category_stats(conn, shop_id, category)
    except analyze.NoCategory as e:
        raise HTTPException(404, str(e)) from None


@router.get("/categories/{category}/restock")
def category_restock(request: Request, category: str = CATEGORY, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """What to buy: healthy items up to the target days of stock (an assumption in config/thresholds.yaml), what to wait on, and
    what to buy no more of. Arithmetic on the stock list's own figures, not a forecast."""
    try:
        return analyze.restock(conn, shop_id, category, request.app.state.th)
    except analyze.NoCategory as e:
        raise HTTPException(404, str(e)) from None


@router.get("/categories/{category}/sales")
def category_sales(category: str = CATEGORY, sku: str | None = Query(None, max_length=64), conn=Depends(get_conn),
                   shop_id: int = Depends(get_shop_id)):
    """The category's sales (or one item's, with sku) for the last day, 7, 30 and 365 days, by month for 12 months and by year, counted
    back from the stock list's date. Without an uploaded sales history: the stock list's own 30- and 90-day totals, labelled so."""
    try:
        return analyze.sales_history(conn, shop_id, category, sku)
    except (analyze.NoCategory, analyze.NoItem) as e:
        raise HTTPException(404, str(e)) from None
