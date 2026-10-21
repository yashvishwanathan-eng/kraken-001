"""Alert everybody and the roll-call board."""

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.concurrency import run_in_threadpool

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import User
from app.schemas import AlertAllIn, AlertAllOut, RollCallOut
from app.services import alerts
from app.ws import manager

router = APIRouter(tags=["alerts"])


@router.post("/alerts/all", response_model=AlertAllOut)
async def alert_everybody(body: AlertAllIn, user: User = Depends(require("alert_all"))) -> AlertAllOut:
    try:
        notice = await run_in_threadpool(lambda: alerts.alert_all(
            body.message, by_name=user.name, user=user, channel="dashboard", incident_id=body.incident_id))
    except alerts.UnknownIncident:
        raise HTTPException(status_code=404, detail=f"incident {body.incident_id} not found")
    incident = notice.pop("incident")
    await manager.broadcast(notice)  # red banner on every dashboard
    if incident is not None:
        await manager.broadcast(incident)
    return AlertAllOut(**notice)


@router.get("/roll-call", response_model=RollCallOut)
def roll_call(incident_id: int | None = Query(None, description="omit for the most recent roll call"),
              _: User = Depends(current_user)) -> RollCallOut:
    with SessionLocal() as db:
        roll_call_id = alerts.latest_roll_call_id(db, incident_id)
        summary = alerts.roll_call_summary(db, roll_call_id) if roll_call_id else None
    if summary is None:
        raise HTTPException(status_code=404, detail="no roll call yet" + (f" for incident {incident_id}"
                                                                         if incident_id else ""))
    return RollCallOut(**summary)
