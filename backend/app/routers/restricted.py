"""Restricted areas in one place (P35): which cameras are restricted and whose face may enter each one.

  GET    /restricted                    every camera you can see, its restricted areas and the allowed people,
                                        plus everybody with how many face fingerprints they have
  POST   /restricted                    {camera_id}: make the whole camera restricted (one area covering the picture)
  PUT    /restricted/{zone_id}/people   {user_ids}: who may enter this area (a recognised face raises no alert)
  DELETE /restricted/{zone_id}          the area is no longer restricted

Restricted areas drawn in config/zones.yaml show up here too. Workers reload zones every 30 s, and face recognition
switches on for a camera as soon as it has a restricted area, so changes need no restart.
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select

from app.db import SessionLocal
from app.deps import require
from app.models import Camera, FaceSample, User, Zone
from app.schemas import (AllowedPeopleIn, RestrictCameraIn, RestrictedArea, RestrictedCamera, RestrictedOverview,
                         RestrictedPerson)
from app.services.audit import record
from app.services.permissions import can_see

router = APIRouter(prefix="/restricted", tags=["restricted"])

WHOLE = [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]]


def is_whole(polygon: list) -> bool:
    return [[float(x), float(y)] for x, y in polygon] == WHOLE


def _zone(db, user: User, zone_id: int) -> Zone:
    zone = db.get(Zone, zone_id)
    if zone is None or zone.kind != "restricted" or not can_see(user, zone.camera_id):
        raise HTTPException(status_code=404, detail=f"restricted area {zone_id} not found")
    return zone


def _area(zone: Zone) -> RestrictedArea:
    return RestrictedArea(zone_id=zone.id, name=zone.name, whole_camera=is_whole(zone.polygon),
                          allowed_user_ids=sorted(zone.allowed_user_ids or []))


@router.get("", response_model=RestrictedOverview)
def overview(user: User = Depends(require("manage_users"))) -> RestrictedOverview:
    with SessionLocal() as db:
        cameras = [c for c in db.scalars(select(Camera).order_by(Camera.number.nulls_last(), Camera.id))
                   if can_see(user, c.id)]
        zones = db.scalars(select(Zone).where(Zone.kind == "restricted").order_by(Zone.id)).all()
        counts = dict(db.execute(select(FaceSample.user_id, func.count()).group_by(FaceSample.user_id)).all())
        people = db.scalars(select(User).where(User.active.is_(True)).order_by(User.name)).all()
        return RestrictedOverview(
            cameras=[RestrictedCamera(id=c.id, name=c.name, number=c.number,
                                      areas=[_area(z) for z in zones if z.camera_id == c.id]) for c in cameras],
            people=[RestrictedPerson(id=p.id, name=p.name, role=p.role, face_samples=counts.get(p.id, 0))
                    for p in people])


@router.post("", response_model=RestrictedArea, status_code=201)
def restrict_camera(body: RestrictCameraIn, user: User = Depends(require("manage_zones"))) -> RestrictedArea:
    with SessionLocal() as db:
        camera = db.get(Camera, body.camera_id)
        if camera is None:
            raise HTTPException(status_code=422, detail=f"unknown camera id: {body.camera_id}")
        if not can_see(user, camera.id):
            raise HTTPException(status_code=403, detail=f"camera {camera.id} is not delegated to you")
        existing = next((z for z in camera.zones if z.kind == "restricted" and is_whole(z.polygon)), None)
        if existing is not None:  # pressing the button twice never makes two areas
            return _area(existing)
        zone = Zone(camera_id=camera.id, name=f"{camera.name} (restricted)", kind="restricted", polygon=WHOLE,
                    allowed_user_ids=[])
        db.add(zone)
        db.flush()
        record(db, user, "camera_restricted", target=f"zone:{zone.id}", detail={"camera": camera.id})
        db.commit()
        return _area(zone)


@router.put("/{zone_id}/people", response_model=RestrictedArea)
def set_people(zone_id: int, body: AllowedPeopleIn, admin: User = Depends(require("manage_users"))) -> RestrictedArea:
    with SessionLocal() as db:
        zone = _zone(db, admin, zone_id)
        wanted = set(body.user_ids)
        known = set(db.scalars(select(User.id).where(User.id.in_(wanted)))) if wanted else set()
        if wanted - known:
            raise HTTPException(status_code=422, detail=f"unknown people: {sorted(wanted - known)}")
        zone.allowed_user_ids = sorted(wanted)
        names = db.scalars(select(User.name).where(User.id.in_(wanted))).all() if wanted else []
        record(db, admin, "access_changed", target=f"zone:{zone.id}", detail={"area": zone.name, "allowed": sorted(names)})
        db.commit()
        return _area(zone)


@router.delete("/{zone_id}", status_code=204)
def unrestrict(zone_id: int, user: User = Depends(require("manage_zones"))) -> None:
    with SessionLocal() as db:
        zone = _zone(db, user, zone_id)
        record(db, user, "camera_unrestricted", target=f"zone:{zone.id}",
               detail={"camera": zone.camera_id, "area": zone.name})
        db.delete(zone)
        db.commit()
