"""Badge scans and presence.

POST /badges/scan       (scan_badges) {badge_number, area_id?, reader?}: a guard's badge read at a door or reader
GET  /presence          (any logged-in user) ?at=: who was present, and where, at that time (names only)
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import current_user, require
from app.models import Area, BadgeScan, Camera, GuardProfile, User
from app.schemas import BadgeScanIn, BadgeScanOut, PresenceOut, PresentGuard
from app.services import vault
from app.services.audit import record
from app.services.permissions import allowed_cameras

router = APIRouter(tags=["badges"])


@router.post("/badges/scan", response_model=BadgeScanOut, status_code=201)
def scan_badge(body: BadgeScanIn, caller: User = Depends(require("scan_badges"))) -> BadgeScanOut:
    with SessionLocal() as db:
        profile = db.scalar(select(GuardProfile).where(
            GuardProfile.badge_hash == vault.badge_hash(body.badge_number)))
        if profile is None:
            record(db, caller, "badge_unknown", channel="dashboard", detail={"reader": body.reader})  # no number logged
            db.commit()
            raise HTTPException(status_code=404, detail="unknown badge")
        if body.area_id is not None and db.get(Area, body.area_id) is None:
            raise HTTPException(status_code=422, detail=f"area {body.area_id} does not exist")
        guard = db.get(User, profile.user_id)
        at = datetime.now(timezone.utc)
        db.add(BadgeScan(at=at, user_id=guard.id, area_id=body.area_id, reader=body.reader or ""))
        record(db, caller, "badge_scan", target=f"user:{guard.id}", detail={"area_id": body.area_id})
        db.commit()
        return BadgeScanOut(ok=True, guard_name=guard.name, at=at, area_id=body.area_id)


@router.get("/presence", response_model=PresenceOut)
def presence(at: datetime | None = Query(None), caller: User = Depends(current_user)) -> PresenceOut:
    moment = at or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    window = timedelta(seconds=get_settings().presence_window_s)
    scope = allowed_cameras(caller)
    with SessionLocal() as db:
        scans = list(db.scalars(select(BadgeScan).where(
            BadgeScan.at >= moment - window, BadgeScan.at <= moment, BadgeScan.user_id.is_not(None))
            .order_by(BadgeScan.at.desc())))
        latest: dict[int, BadgeScan] = {}
        for scan in scans:
            latest.setdefault(scan.user_id, scan)  # the last scan per guard
        present = []
        for user_id, scan in latest.items():
            area = db.get(Area, scan.area_id) if scan.area_id else None
            if scope is not None and not _area_in_scope(db, scan.area_id, scope):
                continue  # only areas the caller's cameras watch
            guard = db.get(User, user_id)
            present.append(PresentGuard(user_id=user_id, name=guard.name if guard else "unknown",
                                        area_id=scan.area_id, area_name=area.name if area else None,
                                        last_scan_at=scan.at))
        return PresenceOut(at=moment, present=present)


def _area_in_scope(db, area_id: int | None, scope: set[str]) -> bool:
    if area_id is None:
        return False
    cams = db.scalars(select(Camera.id).where(Camera.area_id == area_id))
    return any(cam in scope for cam in cams)
