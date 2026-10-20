import logging
from typing import Literal

from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings, load_cameras
from app.db import engine
from app.schemas import CameraHealth, HealthResponse
from app.services import camera_status

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
    settings = get_settings()
    cameras = [CameraHealth(id=cam.id, online=online, fps=fps)
               for cam in load_cameras() for online, fps in [camera_status.get(cam.id)]]
    twilio = "not_configured" if not (settings.twilio_account_sid and settings.twilio_auth_token) else "not_checked"
    return HealthResponse(cameras=cameras, db=_check_db(), twilio=twilio, llm="not_checked")
