"""POST /demo/incident (run_demo): a demo alarm raised from the dashboard.

It is a real incident: stored, sent to every dashboard, and the real call ladder starts (calls and WhatsApp
follow CALLS_MODE / WHATSAPP_MODE, so `simulate` rings nobody). Used on stage and to test the phones.
The audit log records who raised it ("demo_incident"), so a demo is never mistaken for a real alarm.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import require
from app.models import Camera, Incident, TimelineEvent, User, Zone
from app.schemas import DemoIncidentIn, IncidentOut
from app.services import incidents as incident_ops
from app.services.audit import record
from app.services.escalation import escalation
from app.services.permissions import allowed_cameras, can_see
from app.ws import manager
from worker.rules import is_night, parse_hhmm

router = APIRouter(prefix="/demo", tags=["demo"])

SEVERITY = {"fire": "critical", "smoke": "high", "no_helmet": "low", "fall": "critical", "access": "high", "unattended": "high", "crowding": "high",
            "ppe": "low", "loitering": "low"}
ZONE_KIND = {"access": "restricted", "ppe": "ppe"}  # which zone of the camera fits the type


def _create(body: DemoIncidentIn, user: User) -> dict:
    s = get_settings()
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        if body.camera_id is not None:
            camera = db.get(Camera, body.camera_id)
            if camera is None or not can_see(user, camera.id):
                raise HTTPException(status_code=404, detail=f"camera {body.camera_id} not found")
        else:
            scope = allowed_cameras(user)
            query = select(Camera).order_by(Camera.number.nulls_last(), Camera.id)
            camera = next((c for c in db.scalars(query) if scope is None or c.id in scope), None)
            if camera is None:
                raise HTTPException(status_code=409, detail="you have no camera to raise a demo alarm on")
        severity = SEVERITY[body.type]
        if body.type == "access" and is_night(now, parse_hhmm(s.night_start), parse_hhmm(s.night_end), s.timezone):
            severity = "critical"  # same rule as the real detector
        zones = list(db.scalars(select(Zone).where(Zone.camera_id == camera.id).order_by(Zone.id)))
        zone = next((z for z in zones if z.kind == ZONE_KIND.get(body.type)), zones[0] if zones else None)
        incident = Incident(type=body.type, severity=severity, status="open", camera_id=camera.id,
                            zone=zone.name if zone else None, started_at=now, confidence=1.0)
        db.add(incident)
        db.flush()
        db.add(TimelineEvent(incident_id=incident.id, at=now, action="detected"))
        record(db, user, "demo_incident", target=f"incident:{incident.id}",
               detail={"type": body.type, "camera_id": camera.id})
        db.commit()
        return incident_ops.to_message(incident_ops.load(db, incident.id))


@router.post("/incident", response_model=IncidentOut, status_code=201)
async def demo_incident(body: DemoIncidentIn, user: User = Depends(require("run_demo"))) -> dict:
    message = await run_in_threadpool(_create, body, user)
    await manager.broadcast(message)  # red alert on every dashboard, exactly like a camera detection
    await run_in_threadpool(escalation.start, message["id"])
    return message
