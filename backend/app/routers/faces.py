"""Faces and restricted access (P34), on a person:

  GET    /users/{id}/face     how many face fingerprints are enrolled, and which restricted zones they may enter
  POST   /users/{id}/face     enroll: photos (multipart "photos", 1 to 10) + consent=true; keeps only fingerprints
  DELETE /users/{id}/face     delete all their face data (their right; also done when the person is deleted)
  PUT    /users/{id}/access   {zone_ids}: the restricted zones they may enter without an alert

Admin only (manage_users). Enrollment needs the person's consent: faces are biometric data.
"""

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from sqlalchemy import delete, func, select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import require
from app.models import FaceSample, User, Zone
from app.schemas import AccessIn, FaceEnrollOut, FaceStatusOut
from app.services import faces
from app.services.audit import record

router = APIRouter(prefix="/users", tags=["faces"])
PHOTO_LIMIT = 5 * 1024 * 1024


def _status(db, user_id: int) -> FaceStatusOut:
    samples = db.scalar(select(func.count()).select_from(FaceSample).where(FaceSample.user_id == user_id)) or 0
    zones = db.scalars(select(Zone).where(Zone.kind == "restricted").order_by(Zone.id)).all()
    allowed = [z.id for z in zones if user_id in (z.allowed_user_ids or [])]
    return FaceStatusOut(user_id=user_id, samples=samples, zone_ids=allowed)


def _person(db, user_id: int) -> User:
    person = db.get(User, user_id)
    if person is None:
        raise HTTPException(status_code=404, detail="person not found")
    return person


@router.get("/{user_id}/face", response_model=FaceStatusOut)
def face_status(user_id: int, _: User = Depends(require("manage_users"))) -> FaceStatusOut:
    with SessionLocal() as db:
        _person(db, user_id)
        return _status(db, user_id)


@router.post("/{user_id}/face", response_model=FaceEnrollOut)
async def enroll(user_id: int, photos: list[UploadFile] = File(...), consent: bool = Form(False),
                 admin: User = Depends(require("manage_users"))) -> FaceEnrollOut:
    if not consent:
        raise HTTPException(status_code=422, detail="the person must agree to face enrollment (consent=true)")
    if not 1 <= len(photos) <= get_settings().face_max_photos:
        raise HTTPException(status_code=422, detail=f"send 1 to {get_settings().face_max_photos} photos")
    datas = []
    for photo in photos:
        data = await photo.read(PHOTO_LIMIT + 1)
        if not data or len(data) > PHOTO_LIMIT:
            raise HTTPException(status_code=422, detail="each photo must be between 1 byte and 5 MB")
        datas.append(data)

    def work() -> FaceEnrollOut:
        engine = faces.engine()
        vectors, rejected = [], []
        for n, data in enumerate(datas, 1):
            try:
                vectors.append(engine.enroll(faces.decode(data)))
            except faces.FaceError as exc:
                rejected.append(f"photo {n}: {exc}")
        with SessionLocal() as db:
            person = _person(db, user_id)
            for vec in vectors:
                db.add(FaceSample(user_id=user_id, embedding=faces.to_bytes(vec)))
            record(db, admin, "face_enrolled", target=f"user:{user_id}",
                   detail={"person": person.name, "added": len(vectors), "rejected": len(rejected), "consent": True})
            db.commit()
            status = _status(db, user_id)
        return FaceEnrollOut(**status.model_dump(), added=len(vectors), rejected=rejected)

    result = await run_in_threadpool(work)
    if not result.added:
        raise HTTPException(status_code=422, detail="; ".join(result.rejected) or "no usable face")
    return result


@router.delete("/{user_id}/face", response_model=FaceStatusOut)
def delete_face(user_id: int, admin: User = Depends(require("manage_users"))) -> FaceStatusOut:
    with SessionLocal() as db:
        person = _person(db, user_id)
        removed = db.execute(delete(FaceSample).where(FaceSample.user_id == user_id)).rowcount
        record(db, admin, "face_deleted", target=f"user:{user_id}", detail={"person": person.name, "removed": removed})
        db.commit()
        return _status(db, user_id)


@router.put("/{user_id}/access", response_model=FaceStatusOut)
def set_access(user_id: int, body: AccessIn, admin: User = Depends(require("manage_users"))) -> FaceStatusOut:
    with SessionLocal() as db:
        person = _person(db, user_id)
        wanted = set(body.zone_ids)
        zones = db.scalars(select(Zone).where(Zone.kind == "restricted")).all()
        unknown = wanted - {z.id for z in zones}
        if unknown:
            raise HTTPException(status_code=422, detail=f"not restricted zones: {sorted(unknown)}")
        for zone in zones:
            allowed = set(zone.allowed_user_ids or [])
            allowed = allowed | {user_id} if zone.id in wanted else allowed - {user_id}
            zone.allowed_user_ids = sorted(allowed)
        record(db, admin, "access_changed", target=f"user:{user_id}",
               detail={"person": person.name, "zones": [z.name for z in zones if z.id in wanted]})
        db.commit()
        return _status(db, user_id)
