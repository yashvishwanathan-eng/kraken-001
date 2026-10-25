"""Was this restricted entry covered by a badge scan? Used by the alert text and the sector summary.

A camera watches an area (camera.area_id). A scan at that area within BADGE_WINDOW_S of the entry covers it.
Result: scanned, not_scanned (the area has no scan nearby) or unknown (the camera is not placed in an area yet).
"""

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import BadgeScan, Camera, Incident


def entry_badge_status(db: DbSession, camera_id: str, at) -> str:
    camera = db.get(Camera, camera_id)
    if camera is None or camera.area_id is None:
        return "unknown"
    window = timedelta(seconds=get_settings().badge_window_s)
    scan = db.scalar(select(BadgeScan.id).where(
        BadgeScan.area_id == camera.area_id, BadgeScan.at >= at - window, BadgeScan.at <= at + window))
    return "scanned" if scan else "not_scanned"


def incident_badge_status(db: DbSession, incident: Incident) -> str:
    return entry_badge_status(db, incident.camera_id, incident.started_at)
