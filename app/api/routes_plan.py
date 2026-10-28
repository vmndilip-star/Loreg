"""The plan for an alert. This route may ask the Recommender, so it lives with the other model-using routes.

It is the same function the chat uses (app.pipelines.plan_alert), so the Watch page and the chat always show the same action per item.
"""

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.deps import get_conn, get_shop_id
from app.api.deps_ai import get_llm
from app.api.routes_ask import make_ctx
from app.pipelines import plan_alert
from app.storage import repo

router = APIRouter(prefix="/api")


@router.post("/alerts/{alert_id}/plan")
def alert_plan(request: Request, alert_id: int, limit: int = Query(3, ge=1, le=10), conn=Depends(get_conn),
               shop_id: int = Depends(get_shop_id), llm=Depends(get_llm)):
    """One proposed option per item, the same ones the chat shows. 409 unless help was recorded for this alert."""
    try:
        repo.get_alert(conn, alert_id)
        plans = _run(plan_alert(make_ctx(request, conn, shop_id, llm), alert_id, limit))
    except repo.NotFound as e:
        raise HTTPException(404, str(e)) from None
    except repo.Conflict as e:
        raise HTTPException(409, str(e)) from None
    return {"alert_id": alert_id, "recommendations": plans, **repo.money_at_risk(conn, shop_id)}


def _run(gen):
    """Run a generator that yields events and returns a value; the events are of no use here."""
    try:
        while True:
            next(gen)
    except StopIteration as done:
        return done.value
