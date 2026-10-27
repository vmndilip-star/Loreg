"""The numbers API. Every figure is computed by code from stored data; no route here calls a model.

Alert-first is enforced here and in the storage layer, not only in the page: a plan is refused with 409
until the owner has asked for help on that alert, and nothing can be accepted or declined before then.
"""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.ingest import MAX_BYTES, UploadError, commit_upload
from app.storage import repo

router = APIRouter(prefix="/api")


@router.get("/health")
def health():
    return {"ok": True}


@router.post("/upload")
async def upload(request: Request, as_of: date | None = Query(None, description="as-of date for the stock list (default: LOREG_AS_OF, else today)"),
                 conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Upload a stock list (multipart field `file`, or the raw CSV as the request body).

    Runs the data checks, quarantines rows with errors and commits the rest. Replaces the shop's current stock.
    """
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BYTES + 4096:
        raise HTTPException(413, "file is larger than 5 MB")
    if request.headers.get("content-type", "").startswith("multipart/form-data"):
        form = await request.form()
        f = form.get("file")
        if f is None or not hasattr(f, "read"):
            raise HTTPException(422, "send the CSV in a form field named file")
        raw, filename = await f.read(), f.filename or "upload.csv"
    else:
        raw, filename = await request.body(), request.headers.get("x-filename") or "upload.csv"
    if len(raw) > MAX_BYTES:
        raise HTTPException(413, "file is larger than 5 MB")
    state = request.app.state
    when = as_of or state.default_as_of or date.today()
    try:
        result = commit_upload(conn, shop_id=shop_id, filename=filename, raw=raw, as_of=when, th=state.th, catalog=state.catalog)
    except UploadError as e:
        raise HTTPException(422, str(e)) from None
    result["issues_url"] = f"/api/uploads/{result['upload_id']}/issues"
    return result


@router.get("/uploads/{upload_id}/issues")
def upload_issues(upload_id: int, conn=Depends(get_conn)):
    try:
        return repo.upload_issues(conn, upload_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None


@router.get("/summary")
def summary(conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    s = repo.summary(conn, shop_id)
    if s is None:
        raise HTTPException(404, "no stock list has been uploaded yet")
    return s


@router.get("/inventory/top")
def inventory_top(request: Request, limit: int = Query(8, ge=1, le=50), conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    return {"limit": limit, "items": repo.top_items(conn, shop_id, limit, request.app.state.th)}


@router.get("/launch/exposed")
def launch_exposed(conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Launch-family items that are dead or slow. No options here: plans come only after the owner asks for help."""
    return {"groups": repo.launch_exposed(conn, shop_id)}


@router.get("/needs-look")
def needs_look(limit: int = Query(100, ge=1, le=500), conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    return repo.needs_look(conn, shop_id, limit)


@router.get("/alerts/{alert_id}")
def alert(alert_id: int, conn=Depends(get_conn)):
    try:
        return repo.alert_state(conn, alert_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None


@router.post("/alerts/{alert_id}/help")
def alert_help(alert_id: int, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """The owner asks Loreg to help with this alert. Recorded once; nothing is planned or changed by this call."""
    try:
        return repo.request_help(conn, shop_id, alert_id)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None


@router.post("/alerts/{alert_id}/plan")
def alert_plan(request: Request, alert_id: int, limit: int = Query(3, ge=1, le=10), conn=Depends(get_conn),
               shop_id: int = Depends(get_shop_id)):
    """One proposed option per item. 409 unless help was recorded for this alert."""
    try:
        recs = repo.plan(conn, shop_id, alert_id, limit, request.app.state.th)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None
    except repo.Conflict as e:
        raise HTTPException(409, str(e)) from None
    return {"alert_id": alert_id, "recommendations": recs, **repo.money_at_risk(conn, shop_id)}


def _decide(rec_id: int, accept: bool, conn, shop_id: int):
    try:
        return repo.decide(conn, shop_id, rec_id, accept)
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None
    except repo.Conflict as e:
        raise HTTPException(409, str(e)) from None


@router.post("/actions/{rec_id}/accept")
def accept(rec_id: int, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    return _decide(rec_id, True, conn, shop_id)


@router.post("/actions/{rec_id}/decline")
def decline(rec_id: int, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    return _decide(rec_id, False, conn, shop_id)
