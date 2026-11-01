"""Demo routes: the demo credential for the sign-in page, loading the bundled synthetic stock list and sales history, and resetting
the demo account.

Only available when LOREG_DEMO=1. Otherwise every route here answers 404, as if it did not exist.
Loading and resetting only supply the files; parsing, data checks, quarantine and storage are the upload routes' own code.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app import demo_seed
from app.api.deps import get_conn, get_shop_id
from app.api.routes import store_sales, store_upload
from app.demo import DEMO_FILE, DEMO_SALES_FILE, demo_enabled  # noqa: F401  (DEMO_FILE is re-exported for the tests)

router = APIRouter(prefix="/api")

NOT_THE_DEMO = "Only the demo account can be reset."


def _demo_on() -> None:
    if not demo_enabled():
        raise HTTPException(404, "Not Found")


@router.get("/demo")
def demo_credential():
    """The demo account's email and password, for the sign-in page's "Use demo account" button (it fills the form in and signs nobody
    in). Answered signed out, because the sign-in page asks. It is the public demo credential (app/demo_seed.py), never a secret."""
    _demo_on()
    email, password = demo_seed.credentials()
    return {"email": email, "password": password}


def _load(request: Request, conn, shop_id: int) -> dict:
    """The demo stock list, then its synthetic sales history (made from that stock list, so the two agree)."""
    stock = store_upload(request, conn, shop_id, DEMO_FILE.read_bytes(), DEMO_FILE.name)
    return {**stock, "sales": store_sales(request, conn, shop_id, DEMO_SALES_FILE.read_bytes(), DEMO_SALES_FILE.name, synthetic=True)}


@router.post("/demo/load")
def demo_load(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    _demo_on()
    return _load(request, conn, shop_id)


@router.post("/demo/reset")
def demo_reset(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Put the demo account back as the seed script left it: its own rows deleted (the launch tracker's record is kept, its notifications
    unread again), then the demo stock list and sales history loaded through the upload paths. Refused (403) for every account but the demo account.
    Two steps, not one transaction: if the load failed, the account would be left empty, and resetting again fixes it."""
    _demo_on()
    if not demo_seed.is_demo_shop(conn, shop_id):
        raise HTTPException(403, NOT_THE_DEMO)
    conn.execute("BEGIN IMMEDIATE")
    try:
        deleted = demo_seed.clear_shop_data(conn, shop_id, request.app.state.clock())
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    request.app.state.cache.drop_shop(shop_id)
    return {"reset": True, "deleted": deleted, **_load(request, conn, shop_id)}
