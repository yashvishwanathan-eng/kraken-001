"""P23. Duty check-in with QR codes. On duty = called by the alert ladder; off duty = not called.

GET  /duty                 the board: who is on duty and who is off (people who share a camera with the caller)
GET  /duty/events          check-in history (view_audit or manage_guards) ?from&to&user_id
GET  /duty/pass/{user_id}  a guard's personal QR pass, to show on their phone (themselves, or manage_guards)
GET  /duty/post-qr         ?area_id: a QR code to print at a post; guards scan it with their phone (manage_guards)
POST /duty/scan            {pass_token, action?, area_id?}: the control room scans a guard's pass (scan_badges)
POST /duty/check-in        {post_token, action?}: a logged-in guard scanned the post QR on their phone
POST /duty/set             {user_id, action}: set by hand from the dashboard (manage_guards)

Every change is in the audit log and goes to every dashboard as {"kind":"duty", ...}.
"""

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.config import get_settings
from app.db import SessionLocal
from app.deps import current_user, require, require_any
from app.models import Area, DutyEvent, GuardProfile, User
from app.routers.invites import _qr_png
from app.schemas import (
    DutyBoardOut, DutyCheckIn, DutyEventOut, DutyPassOut, DutyPerson, DutyPostQrOut, DutyScanIn, DutySetIn,
)
from app.services import duty
from app.services.audit import record
from app.services.permissions import allowed_cameras, can, check_value, sign_value, staff_id
from app.ws import manager

router = APIRouter(prefix="/duty", tags=["duty"])


def _event_out(event: DutyEvent, name: str) -> DutyEventOut:
    return DutyEventOut(user_id=event.user_id, name=name, action=event.action, method=event.method, at=event.at,
                        area_id=event.area_id, by=event.by_name)


def _change(db, guard: User, action: str, method: str, by: User, area_id: int | None) -> DutyEventOut:
    if not guard.active:
        raise HTTPException(status_code=409, detail=f"{guard.name}'s account is not active")
    if area_id is not None and db.get(Area, area_id) is None:
        raise HTTPException(status_code=422, detail=f"area {area_id} does not exist")
    event = duty.set_duty(db, guard, action, method, by.name, area_id)
    record(db, by, f"duty_{event.action}", target=f"user:{guard.id}", detail={"method": method, "area_id": area_id})
    db.commit()
    out = _event_out(event, guard.name)
    manager.publish_from_thread({"kind": "duty", **out.model_dump(mode="json")})
    return out


@router.get("", response_model=DutyBoardOut)
def board(caller: User = Depends(current_user)) -> DutyBoardOut:
    scope = allowed_cameras(caller)
    with SessionLocal() as db:
        guards = [u for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.name))
                  if duty.checks_in(u.role) and (scope is None or set(u.camera_ids or []) & scope)]
        latest = duty.latest_events(db, [g.id for g in guards])
        profiles = {p.user_id: p for p in db.scalars(select(GuardProfile).where(
            GuardProfile.user_id.in_([g.id for g in guards])))} if guards else {}
        on, off = [], []
        for guard in guards:
            event, profile = latest.get(guard.id), profiles.get(guard.id)
            person = DutyPerson(user_id=guard.id, staff_id=staff_id(guard.id), name=guard.name, role=guard.role,
                                on_duty=duty.is_on(event), since=event.at if event else None,
                                method=event.method if event else None, area_id=event.area_id if event else None,
                                post=profile.post if profile else None,
                                equipment=profile.equipment if profile else None)
            (on if person.on_duty else off).append(person)
        return DutyBoardOut(on_duty=on, off_duty=off)


@router.get("/events", response_model=list[DutyEventOut])
def events(start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
           user_id: int | None = None, limit: int = Query(200, ge=1, le=1000),
           _: User = Depends(require_any("view_audit", "manage_guards"))) -> list[DutyEventOut]:
    query = select(DutyEvent, User.name).join(User, User.id == DutyEvent.user_id)
    if start is not None:
        query = query.where(DutyEvent.at >= start)
    if end is not None:
        query = query.where(DutyEvent.at <= end)
    if user_id is not None:
        query = query.where(DutyEvent.user_id == user_id)
    with SessionLocal() as db:
        rows = db.execute(query.order_by(DutyEvent.id.desc()).limit(limit)).all()
        return [_event_out(ev, name) for ev, name in rows]


@router.get("/pass/{user_id}", response_model=DutyPassOut)
def duty_pass(user_id: int, caller: User = Depends(current_user)) -> DutyPassOut:
    if caller.id != user_id and not can(caller, "manage_guards"):
        raise HTTPException(status_code=403, detail="you can only open your own duty pass")
    with SessionLocal() as db:
        guard = db.get(User, user_id)
        if guard is None:
            raise HTTPException(status_code=404, detail=f"user {user_id} not found")
        token = sign_value(duty.PASS, guard.id)
        return DutyPassOut(user_id=guard.id, staff_id=staff_id(guard.id), name=guard.name, pass_token=token,
                           qr_png=_qr_png(token))


@router.get("/post-qr", response_model=DutyPostQrOut)
def post_qr(area_id: int, _: User = Depends(require_any("manage_guards", "manage_sites"))) -> DutyPostQrOut:
    with SessionLocal() as db:
        area = db.get(Area, area_id)
        if area is None:
            raise HTTPException(status_code=404, detail=f"area {area_id} not found")
        token = sign_value(duty.POST, area.id)
        link = f"{get_settings().dashboard_url.rstrip('/')}/check-in?post={token}"
        return DutyPostQrOut(area_id=area.id, area_name=area.name, link=link, post_token=token, qr_png=_qr_png(link))


@router.post("/scan", response_model=DutyEventOut, status_code=201)
def scan_pass(body: DutyScanIn, caller: User = Depends(require("scan_badges"))) -> DutyEventOut:
    user_id = check_value(duty.PASS, body.pass_token)
    if user_id is None:
        raise HTTPException(status_code=404, detail="this is not a valid duty pass")
    with SessionLocal() as db:
        guard = db.get(User, user_id)
        if guard is None:
            raise HTTPException(status_code=404, detail="this duty pass belongs to a deleted user")
        return _change(db, guard, body.action, "pass_scan", caller, body.area_id)


@router.post("/check-in", response_model=DutyEventOut, status_code=201)
def check_in(body: DutyCheckIn, caller: User = Depends(current_user)) -> DutyEventOut:
    area_id = check_value(duty.POST, body.post_token)
    if area_id is None:
        raise HTTPException(status_code=404, detail="this is not a valid post QR code")
    with SessionLocal() as db:
        return _change(db, db.merge(caller), body.action, "post_qr", caller, area_id)


@router.post("/set", response_model=DutyEventOut, status_code=201)
def set_by_hand(body: DutySetIn, caller: User = Depends(require("manage_guards"))) -> DutyEventOut:
    with SessionLocal() as db:
        guard = db.get(User, body.user_id)
        if guard is None:
            raise HTTPException(status_code=404, detail=f"user {body.user_id} not found")
        return _change(db, guard, body.action, "dashboard", caller, None)
