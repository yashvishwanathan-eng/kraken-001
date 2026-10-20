"""Cameras the caller may see, with live status, zones and a signed stream URL."""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.deps import current_user
from app.models import Camera, User, Zone
from app.schemas import CameraOut, StreamUrlResponse, ZoneOut
from app.services import camera_status
from app.services.permissions import allowed_cameras, can_see, stream_url

router = APIRouter(prefix="/cameras", tags=["cameras"])


def zone_out(zone: Zone) -> ZoneOut:
    return ZoneOut(id=zone.id, camera_id=zone.camera_id, name=zone.name, kind=zone.kind,
                   polygon=[tuple(p) for p in zone.polygon], max_people=zone.max_people)


@router.get("", response_model=list[CameraOut])
def list_cameras(user: User = Depends(current_user)) -> list[CameraOut]:
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        query = select(Camera).options(selectinload(Camera.zones)).order_by(Camera.id)
        if scope is not None:
            query = query.where(Camera.id.in_(scope))
        result = []
        for camera in db.scalars(query):
            online, fps = camera_status.get(camera.id)
            result.append(CameraOut(
                id=camera.id, name=camera.name, stream_url=stream_url(user.id, camera.id), online=online, fps=fps,
                zones=[zone_out(z) for z in sorted(camera.zones, key=lambda z: z.id)],
            ))
        return result


@router.get("/{camera_id}/stream-url", response_model=StreamUrlResponse)
def refresh_stream_url(camera_id: str, user: User = Depends(current_user)) -> StreamUrlResponse:
    with SessionLocal() as db:
        if db.get(Camera, camera_id) is None:
            raise HTTPException(status_code=404, detail=f"camera {camera_id} not found")
    if not can_see(user, camera_id):
        raise HTTPException(status_code=403, detail=f"camera {camera_id} is not delegated to you")
    return StreamUrlResponse(stream_url=stream_url(user.id, camera_id))
