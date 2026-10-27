import logging
import time
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from app.db import engine
from app.schemas import CameraHealth, HealthResponse
from app.services import camera_registry, camera_status, llm
from app.services.calls import calls

router = APIRouter()
log = logging.getLogger("safetyai.health")


def _check_db() -> Literal["ok", "error"]:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        log.warning("db check failed", exc_info=True)
        return "error"


@router.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    cameras = [CameraHealth(id=cam.id, online=online, fps=fps)
               for cam in camera_registry.all_cameras() for online, fps in [camera_status.get(cam.id)]]
    twilio = "ok" if calls.client.configured() else "not_configured"
    error = calls.last_error
    recent = error[1] if error is not None and time.monotonic() - error[0] < 600 else None
    return HealthResponse(cameras=cameras, db=_check_db(), twilio=twilio, llm=llm.status(), calls_mode=calls.mode,
                          calls_error=recent)
