"""Demo shortcut: loads the bundled synthetic stock file through the same path as a real upload.

Only available when LOREG_DEMO=1. Otherwise the route answers 404, as if it did not exist.
This route only supplies the file; parsing, data checks, quarantine and storage are the upload route's own code.
"""

import os
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.deps import get_conn, get_shop_id
from app.api.routes import store_upload

router = APIRouter(prefix="/api")

DEMO_FILE = Path(__file__).resolve().parents[2] / "data" / "demo" / "upload_demo_200.csv"


def demo_enabled() -> bool:
    return os.environ.get("LOREG_DEMO") == "1"


@router.get("/demo")
def demo_status():
    """Lets the page know whether to offer the demo button. 404 when the demo is off, like the load route; never loads anything."""
    if not demo_enabled():
        raise HTTPException(404, "Not Found")
    return {"enabled": True}


@router.post("/demo/load")
def demo_load(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    if not demo_enabled():
        raise HTTPException(404, "Not Found")
    return store_upload(request, conn, shop_id, DEMO_FILE.read_bytes(), DEMO_FILE.name)
