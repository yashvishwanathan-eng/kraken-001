"""Incidents: list, detail, acknowledge, resolve, and the live WebSocket.

Every query is limited to the caller's delegated cameras. An incident on a camera you cannot see
answers 404, not 403, so its existence is not leaked.
"""

import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Incident, User
from app.schemas import IncidentOut, IncidentType, ResolveIn, Severity, Status
from app.services import incidents as incident_ops
from app.services.escalation import escalation
from app.services.permissions import allowed_cameras, can_see, user_for_token
from app.ws import manager

router = APIRouter(tags=["events"])
log = logging.getLogger("safetyai.events")

WS_UNAUTHORISED = 4401  # WebSocket close code: bad or missing token


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
        incident = incident_ops.load(db, incident_id)
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        return IncidentOut.from_model(incident)


def _ack(incident_id: int, user: User) -> tuple[IncidentOut, bool]:
    """Acknowledge from the dashboard. Repeating it changes nothing (idempotent)."""
    with SessionLocal() as db:
        incident = incident_ops.load(db, incident_id, lock=True)  # row lock: two clicks cannot both acknowledge
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        try:
            changed = incident_ops.acknowledge(db, incident, name=user.name, user=user, via="dashboard",
                                               channel="dashboard")
        except incident_ops.IncidentClosed as closed:
            raise HTTPException(status_code=409, detail=f"incident {incident_id} is already {closed}")
        db.commit()
        result = IncidentOut.from_model(incident)
    if changed:
        escalation.stop(incident_id)  # no more calls once someone has it
    return result, changed


@router.post("/events/{incident_id}/ack", response_model=IncidentOut)
async def ack_event(incident_id: int, user: User = Depends(require("ack"))) -> IncidentOut:
    incident, changed = await run_in_threadpool(_ack, incident_id, user)
    if changed:
        await manager.broadcast(incident.model_dump(mode="json"))
    return incident


def _resolve(incident_id: int, body: ResolveIn, user: User) -> IncidentOut:
    with SessionLocal() as db:
        incident = incident_ops.load(db, incident_id, lock=True)
        if incident is None or not can_see(user, incident.camera_id):
            raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
        try:
            incident_ops.close(db, incident, body.outcome, name=user.name, user=user, via="dashboard",
                               channel="dashboard", note=body.note)
        except incident_ops.IncidentClosed as closed:
            raise HTTPException(status_code=409, detail=f"incident {incident_id} is already {closed}")
        db.commit()
        result = IncidentOut.from_model(incident)
    escalation.stop(incident_id)
    return result


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
    # incidents: only delegated cameras; notices (alert_all, roll_call) carry no camera_id and go to everyone
    await manager.connect(websocket, lambda message: scope is None or "camera_id" not in message
                          or message["camera_id"] in scope, user_id=user.id)
    try:
        while True:
            await websocket.receive_text()  # the dashboard may send pings; we only listen to keep it open
    except WebSocketDisconnect:
        manager.disconnect(websocket)
        await manager.announce_speaker(user.id)  # the newest remaining dashboard takes over speaking
