"""Worker-to-API calls. Protected by the shared X-Internal-Key header, not by user tokens."""

import hmac
import logging
import time

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.models import Incident
from app.schemas import IncidentOut, OkResponse
from app.ws import manager

router = APIRouter(prefix="/internal", tags=["internal"])
log = logging.getLogger("safetyai.internal")


def require_internal_key(x_internal_key: str = Header(default="")) -> None:
    expected = get_settings().internal_key
    if not expected or not hmac.compare_digest(x_internal_key, expected):
        raise HTTPException(status_code=401, detail="missing or wrong X-Internal-Key")


def load_incident(incident_id: int) -> IncidentOut | None:
    with SessionLocal() as db:
        incident = db.get(Incident, incident_id, options=[selectinload(Incident.timeline), selectinload(Incident.camera)])
        return IncidentOut.from_model(incident) if incident else None


@router.post("/incidents/{incident_id}/created", response_model=OkResponse,
             dependencies=[Depends(require_internal_key)])
async def incident_created(incident_id: int) -> OkResponse:
    t0 = time.perf_counter()
    incident = await run_in_threadpool(load_incident, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail=f"incident {incident_id} not found")
    sent = await manager.broadcast(incident.model_dump(mode="json"))
    # Escalation (calls, WhatsApp) starts here once services/escalation.py is built (22 Oct).
    log.info("incident=%d step=broadcast clients=%d ms=%.1f", incident_id, sent, (time.perf_counter() - t0) * 1000)
    return OkResponse(ok=True)
