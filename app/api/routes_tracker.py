"""Launch tracker and notification routes. The GET routes only read. Notifications are added when the app opens (POST /notifications/refresh)
and on "Check for news" (POST /watch/scan); each (watch, state, date) is notified once."""

from datetime import timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.watch import tracker
from app.watch.common import stamp

router = APIRouter(prefix="/api")


def _today(request: Request):
    return request.app.state.clock().astimezone(timezone.utc).date()      # a calendar day in UTC; see config/thresholds.yaml


@router.get("/launch/tracker")
def launch_tracker(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Every tracked line with its state today, worked out now from stored news and its history. Nothing is written."""
    today, th = _today(request), request.app.state.th
    return {"today": today.isoformat(), "grace_days": th.launch_grace_days, "watches": tracker.read(conn, shop_id, today, th)}


@router.get("/notifications")
def list_notifications(limit: int = Query(50, ge=1, le=200), conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """The newest notifications and how many are unread. Nothing is written."""
    return tracker.notifications(conn, shop_id, limit)


@router.post("/notifications/refresh")
def refresh(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    """Called when the app opens: adds new watches, history rows and notifications, each at most once. Returns what was added."""
    now = request.app.state.clock()
    conn.execute("BEGIN IMMEDIATE")
    try:
        added = tracker.sync(conn, shop_id, now.astimezone(timezone.utc).date(), request.app.state.th, stamp(now))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return {**added, "unread": tracker.notifications(conn, shop_id, 1)["unread"]}


@router.post("/notifications/read-all")
def read_all(request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    conn.execute("UPDATE notifications SET read_at = ? WHERE shop_id = ? AND read_at IS NULL", (stamp(request.app.state.clock()), shop_id))
    return {"unread": 0}


@router.post("/notifications/{notification_id}/read")
def read_one(notification_id: int, request: Request, conn=Depends(get_conn), shop_id: int = Depends(get_shop_id)):
    if conn.execute("SELECT 1 FROM notifications WHERE id = ? AND shop_id = ?", (notification_id, shop_id)).fetchone() is None:
        raise HTTPException(404, f"no notification {notification_id}")
    conn.execute("UPDATE notifications SET read_at = COALESCE(read_at, ?) WHERE id = ? AND shop_id = ?",
                 (stamp(request.app.state.clock()), notification_id, shop_id))
    return {"id": notification_id, "unread": tracker.notifications(conn, shop_id, 1)["unread"]}
