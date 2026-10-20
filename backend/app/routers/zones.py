"""Zone polygons per camera. Workers reload zones every 30 s, so edits take effect without a restart."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Camera, User, Zone
from app.routers.cameras import zone_out
from app.schemas import ZoneIn, ZoneOut
from app.services.audit import record
from app.services.permissions import allowed_cameras, can_see

router = APIRouter(prefix="/zones", tags=["zones"])


def _check_camera(db, user: User, camera_id: str) -> None:
    if db.get(Camera, camera_id) is None:
        raise HTTPException(status_code=422, detail=f"unknown camera id: {camera_id}")
    if not can_see(user, camera_id):
        raise HTTPException(status_code=403, detail=f"camera {camera_id} is not delegated to you")


@router.get("", response_model=list[ZoneOut])
def list_zones(camera: str | None = Query(None, description="only this camera"),
               user: User = Depends(current_user)) -> list[ZoneOut]:
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        query = select(Zone).order_by(Zone.camera_id, Zone.id)
        if scope is not None:
            query = query.where(Zone.camera_id.in_(scope))
        if camera is not None:
            query = query.where(Zone.camera_id == camera)
        return [zone_out(z) for z in db.scalars(query)]


@router.post("", response_model=ZoneOut, status_code=201)
def create_zone(body: ZoneIn, user: User = Depends(require("manage_zones"))) -> ZoneOut:
    with SessionLocal() as db:
        _check_camera(db, user, body.camera_id)
        zone = Zone(camera_id=body.camera_id, name=body.name, kind=body.kind,
                    polygon=[list(p) for p in body.polygon], max_people=body.max_people)
        db.add(zone)
        db.flush()
        record(db, user, "zone_create", target=f"zone:{zone.id}", detail={"camera": zone.camera_id, "name": zone.name})
        db.commit()
        return zone_out(zone)


@router.put("/{zone_id}", response_model=ZoneOut)
def update_zone(zone_id: int, body: ZoneIn, user: User = Depends(require("manage_zones"))) -> ZoneOut:
    with SessionLocal() as db:
        zone = db.get(Zone, zone_id)
        if zone is None or not can_see(user, zone.camera_id):
            raise HTTPException(status_code=404, detail=f"zone {zone_id} not found")
        _check_camera(db, user, body.camera_id)
        zone.camera_id, zone.name, zone.kind = body.camera_id, body.name, body.kind
        zone.polygon, zone.max_people = [list(p) for p in body.polygon], body.max_people
        record(db, user, "zone_update", target=f"zone:{zone.id}", detail={"camera": zone.camera_id, "name": zone.name})
        db.commit()
        return zone_out(zone)
