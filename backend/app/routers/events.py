"""Incidents: list, detail, acknowledge, resolve, and the live WebSocket.

Every query is limited to the caller's delegated cameras. An incident on a camera you cannot see
answers 404, not 403, so its existence is not leaked.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Incident, TimelineEvent, User
from app.schemas import IncidentOut, IncidentType, ResolveIn, Severity, Status
from app.services.audit import record
from app.services.permissions import allowed_cameras, can_see, user_for_token
from app.ws import manager

router = APIRouter(tags=["events"])
log = logging.getLogger("safetyai.events")

CLOSED = {"resolved", "false_alarm"}
WS_UNAUTHORISED = 4401  # WebSocket close code: bad or missing token


def _load(db: DbSession, incident_id: int, lock: bool = False) -> Incident | None:
    return db.get(Incident, incident_id, with_for_update=lock,
                  options=[selectinload(Incident.timeline), selectinload(Incident.camera)])


@router.get("/events", response_model=list[IncidentOut])
def list_events(
    type: IncidentType | None = None,
    camera: str | None = None,
    severity: Severity | None = None,
    status: Status | None = None,
    from_: datetime | None = Query(None, alias="from", description="ISO UTC, e.g. 2026-10-24T00:00:00Z"),
    to: datetime | None = Query(None, description="ISO UTC"),
    limit: int = Query(100, ge=1, le=500),
    user: User = Depends(current_user),
) -> list[IncidentOut]:
    query = select(Incident).options(selectinload(Incident.timeline), selectinload(Incident.camera))
    scope = allowed_cameras(user)
    if scope is not None:
        query = query.where(Incident.camera_id.in_(scope))
    for column, value in ((Incident.type, type), (Incident.camera_id, camera),
                          (Incident.severity, severity), (Incident.status, status)):
        if value is not None:
            query = query.where(column == value)
    if from_ is not None:
        query = query.where(Incident.started_at >= from_)
    if to is not None:
        query = query.where(Incident.started_at <= to)
    query = query.order_by(Incident.started_at.desc(), Incident.id.desc()).limit(limit)
    with SessionLocal() as db:
        return [IncidentOut.from_model(i) for i in db.scalars(query)]


@router.get("/events/{incident_id}", response_model=IncidentOut)
def get_event(incident_id: int, user: User = Depends(current_user)) -> IncidentOut:
    with SessionLocal() as db:
        incident = _load(db, incident_id)
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        return IncidentOut.from_model(incident)


def _ack(incident_id: int, user: User) -> tuple[IncidentOut, bool]:
    """Acknowledge from the dashboard. Repeating it changes nothing (idempotent)."""
    with SessionLocal() as db:
        incident = _load(db, incident_id, lock=True)  # row lock: two clicks cannot both acknowledge
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        if incident.status == "acknowledged":
            return IncidentOut.from_model(incident), False
        if incident.status in CLOSED:
            raise HTTPException(status_code=409, detail=f"incident {incident_id} is already {incident.status}")
        now = datetime.now(timezone.utc)
        incident.status = "acknowledged"
        incident.acknowledged_by, incident.acknowledged_by_user_id = user.name, user.id
        # append to the loaded list (not db.add) so the response below includes the new entry
        incident.timeline.append(TimelineEvent(at=now, action="acknowledged", actor=user.name, via="dashboard"))
        record(db, user, "ack", target=f"incident:{incident.id}")
        db.commit()
        # Stopping the call ladder on acknowledgement is added with escalation (22 Oct).
        return IncidentOut.from_model(incident), True


@router.post("/events/{incident_id}/ack", response_model=IncidentOut)
async def ack_event(incident_id: int, user: User = Depends(require("ack"))) -> IncidentOut:
    incident, changed = await run_in_threadpool(_ack, incident_id, user)
    if changed:
        await manager.broadcast(incident.model_dump(mode="json"))
    return incident


def _resolve(incident_id: int, body: ResolveIn, user: User) -> IncidentOut:
    with SessionLocal() as db:
        incident = _load(db, incident_id, lock=True)
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        if incident.status in CLOSED:
            raise HTTPException(status_code=409, detail=f"incident {incident_id} is already {incident.status}")
        incident.status = body.outcome
        incident.timeline.append(TimelineEvent(at=datetime.now(timezone.utc), action=body.outcome,
                                               actor=user.name, via="dashboard"))
        record(db, user, body.outcome, target=f"incident:{incident.id}",
               detail={"note": body.note} if body.note else None)
        db.commit()
        return IncidentOut.from_model(incident)


@router.post("/events/{incident_id}/resolve", response_model=IncidentOut)
async def resolve_event(incident_id: int, body: ResolveIn, user: User = Depends(require("resolve"))) -> IncidentOut:
    incident = await run_in_threadpool(_resolve, incident_id, body, user)
    await manager.broadcast(incident.model_dump(mode="json"))
    return incident


def _ws_user(token: str) -> User | None:
    with SessionLocal() as db:
        user = user_for_token(db, token)
        if user is not None:
            db.expunge(user)
        return user


@router.websocket("/ws/events")
async def ws_events(websocket: WebSocket, token: str = "") -> None:
    """Browsers cannot set headers on a WebSocket, so the login token comes as ?token=..."""
    user = await run_in_threadpool(_ws_user, token)
    if user is None:
        await websocket.accept()
        await websocket.close(code=WS_UNAUTHORISED, reason="log in first")
        return
    scope = allowed_cameras(user)
    await manager.connect(websocket, lambda message: scope is None or message.get("camera_id") in scope)
    try:
        while True:
            await websocket.receive_text()  # the dashboard may send pings; we only listen to keep it open
    except WebSocketDisconnect:
        manager.disconnect(websocket)
