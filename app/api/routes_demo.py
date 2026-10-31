"""Demo routes: the demo credential for the sign-in page, loading the bundled synthetic stock file, and resetting the demo account.

Only available when LOREG_DEMO=1. Otherwise every route here answers 404, as if it did not exist.
Loading and resetting only supply the file; parsing, data checks, quarantine and storage are the upload route's own code.
"""

from fastapi import APIRouter, Depends, HTTPException, Request

from app import demo_seed
from app.api.deps import get_conn, get_shop_id
from app.api.routes import store_upload
from app.demo import DEMO_FILE, demo_enabled  # noqa: F401  (DEMO_FILE is re-exported for the tests)

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


@router.post("/demo/load")
def demo_load(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    _demo_on()
    return store_upload(request, conn, shop_id, DEMO_FILE.read_bytes(), DEMO_FILE.name)


@router.post("/demo/reset")
def demo_reset(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Put the demo account back as the seed script left it: its own rows deleted, then the demo stock list loaded through the upload
    path. Refused (403) for every account but the demo account. Two steps, not one transaction: if the load failed, the account would
    be left empty, and resetting again fixes it."""
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
    return {"reset": True, "deleted": deleted, **store_upload(request, conn, shop_id, DEMO_FILE.read_bytes(), DEMO_FILE.name)}
