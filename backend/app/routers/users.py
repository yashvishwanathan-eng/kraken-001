"""Users, roles and camera delegation (Admin: needs the manage_users action).

The PIN is write-only: it is hashed on the way in and never returned.
"""

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db import SessionLocal
from app.deps import require, require_any
from app.models import Camera, User
from app.schemas import UserCreate, UserOut, UserUpdate
from app.services.audit import record
from app.services.permissions import ACTIONS, allowed_cameras, end_sessions, find_user_by_pin, hash_pin

router = APIRouter(prefix="/users", tags=["users"])


def to_out(user: User) -> UserOut:
    return UserOut(id=user.id, name=user.name, role=user.role, camera_ids=list(user.camera_ids),
                   actions=list(user.actions), active=user.active)


def _delegation_guard(actor: User, camera_ids: list[str] | None, actions: list[str] | None) -> None:
    """A user with manage_users may give anything. A delegating user (delegate_cameras only) may give only
    cameras they can see and actions they hold themselves. This is how a manager hands cameras down."""
    if "manage_users" in actor.actions:
        return
    scope = allowed_cameras(actor)
    if camera_ids is not None and scope is not None and not set(camera_ids) <= scope:
        raise HTTPException(status_code=403, detail="you can only give people cameras you can see yourself")
    if actions is not None and not set(actions) <= set(actor.actions):
        raise HTTPException(status_code=403, detail="you can only give people actions you hold yourself")


def _validate(db: DbSession, camera_ids: list[str] | None, actions: list[str] | None,
              pin: str | None, user_id: int | None) -> None:
    if camera_ids is not None:
        known = set(db.scalars(select(Camera.id)))
        unknown = sorted(set(camera_ids) - known)
        if unknown:
            raise HTTPException(status_code=422, detail=f"unknown camera ids: {', '.join(unknown)}")
    if actions is not None:
        unknown = sorted(set(actions) - ACTIONS)
        if unknown:
            raise HTTPException(status_code=422,
                                detail=f"unknown actions: {', '.join(unknown)}; allowed: {', '.join(sorted(ACTIONS))}")
    if pin is not None and find_user_by_pin(db, pin, exclude_user_id=user_id) is not None:
        raise HTTPException(status_code=409, detail="that PIN is already used by another user; pick another")


@router.get("", response_model=list[UserOut])
def list_users(_: User = Depends(require("manage_users"))) -> list[UserOut]:
    with SessionLocal() as db:
        return [to_out(u) for u in db.scalars(select(User).order_by(User.id))]


@router.post("", response_model=UserOut, status_code=201)
def create_user(body: UserCreate, admin: User = Depends(require_any("manage_users", "delegate_cameras"))) -> UserOut:
    _delegation_guard(admin, body.camera_ids, body.actions)
    with SessionLocal() as db:
        _validate(db, body.camera_ids, body.actions, body.pin, None)
        user = User(name=body.name, role=body.role, camera_ids=sorted(set(body.camera_ids)),
                    actions=sorted(set(body.actions)), active=body.active, pin_hash=hash_pin(body.pin))
        db.add(user)
        db.flush()
        record(db, admin, "user_create", target=f"user:{user.id}", detail={"name": user.name, "role": user.role})
        db.commit()
        return to_out(user)


@router.put("/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UserUpdate, admin: User = Depends(require_any("manage_users", "delegate_cameras"))) -> UserOut:
    _delegation_guard(admin, body.camera_ids, body.actions)
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"user {user_id} not found")
        _validate(db, body.camera_ids, body.actions, body.pin, user_id)
        if user_id == admin.id and (body.active is False or
                                    (body.actions is not None and "manage_users" not in body.actions)):
            raise HTTPException(status_code=400, detail="you cannot deactivate yourself or remove your own manage_users")
        changes = body.model_dump(exclude_none=True, exclude={"pin"})
        for field, value in changes.items():
            setattr(user, field, sorted(set(value)) if field in ("camera_ids", "actions") else value)
        if body.pin is not None:
            user.pin_hash = hash_pin(body.pin)
        if body.pin is not None or body.active is False:
            end_sessions(db, user.id)  # PIN reset or deactivation logs them out everywhere
        record(db, admin, "user_update", target=f"user:{user.id}",
               detail={**changes, **({"pin": "reset"} if body.pin is not None else {})})
        db.commit()
        return to_out(user)


@router.delete("/{user_id}", status_code=204)
def delete_user(user_id: int, admin: User = Depends(require("manage_users"))) -> Response:
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="you cannot delete your own account")
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"user {user_id} not found")
        record(db, admin, "user_delete", target=f"user:{user.id}", detail={"name": user.name})
        db.delete(user)
        db.commit()
    return Response(status_code=204)
