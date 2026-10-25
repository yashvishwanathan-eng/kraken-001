"""Guards: post, equipment and personal details.

GET /guards                     (any logged-in user) names and posts of the guards the caller may see
GET /guards/{user_id}/profile   (view_guard_details, Admin only) the full profile, personal details decrypted
PUT /guards/{user_id}/profile   (manage_guards) replaces the whole profile; personal details encrypted

Personal details (badge number, badge ID, security number, emergency contact) are never returned by the
list or by any other user's view. Audit entries record which fields changed, never their values.
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.db import SessionLocal
from app.deps import current_user, require
from app.models import GuardProfile, Site, User
from app.schemas import GuardListItem, GuardProfileIn, GuardProfileOut, PersonalDetails
from app.services import vault
from app.services.audit import record
from app.services.permissions import allowed_cameras

router = APIRouter(prefix="/guards", tags=["guards"])
PROFILE_FIELDS = ("position", "site_id", "post", "equipment", "has_phone")


def _site_name(db, site_id: int | None) -> str | None:
    site = db.get(Site, site_id) if site_id else None
    return site.name if site else None


def _public_profile(db, user: User, profile: GuardProfile | None) -> GuardProfileOut:
    return GuardProfileOut(
        user_id=user.id, name=user.name, role=user.role, camera_ids=sorted(user.camera_ids or []),
        position=profile.position if profile else None,
        site_id=profile.site_id if profile else None,
        site_name=_site_name(db, profile.site_id) if profile else None,
        post=profile.post if profile else None,
        equipment=profile.equipment if profile else None,
        has_phone=profile.has_phone if profile else False,
        linked_login=bool(user.active),
        has_personal_details=bool(profile and profile.personal),
    )


@router.get("", response_model=list[GuardListItem])
def list_guards(caller: User = Depends(current_user)) -> list[GuardListItem]:
    scope = allowed_cameras(caller)
    with SessionLocal() as db:
        out = []
        for user in db.scalars(select(User).order_by(User.name)):
            if scope is not None and not (set(user.camera_ids or []) & scope):
                continue  # only people who share a camera with the caller
            profile = db.get(GuardProfile, user.id)
            out.append(GuardListItem(user_id=user.id, name=user.name, role=user.role,
                                     position=profile.position if profile else None,
                                     post=profile.post if profile else None))
        return out


@router.get("/{user_id}/profile", response_model=GuardProfileOut)
def get_profile(user_id: int, _: User = Depends(require("view_guard_details"))) -> GuardProfileOut:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"user {user_id} not found")
        profile = db.get(GuardProfile, user_id)
        out = _public_profile(db, user, profile)
        out.personal = PersonalDetails(**vault.decrypt_personal(profile.personal if profile else None))
        return out


@router.put("/{user_id}/profile", response_model=GuardProfileOut)
def save_profile(user_id: int, body: GuardProfileIn, admin: User = Depends(require("manage_guards"))) -> GuardProfileOut:
    with SessionLocal() as db:
        user = db.get(User, user_id)
        if user is None:
            raise HTTPException(status_code=404, detail=f"user {user_id} not found")
        if body.site_id is not None and db.get(Site, body.site_id) is None:
            raise HTTPException(status_code=422, detail=f"site {body.site_id} does not exist")
        profile = db.get(GuardProfile, user_id) or GuardProfile(user_id=user_id)
        before = {field: getattr(profile, field) for field in PROFILE_FIELDS}
        old_personal = vault.decrypt_personal(profile.personal) if profile.personal else None

        new_values = {field: getattr(body, field) for field in PROFILE_FIELDS}
        for field, value in new_values.items():
            setattr(profile, field, value)
        new_personal = body.personal.model_dump() if body.personal else {f: "" for f in vault.PERSONAL_FIELDS}
        profile.personal = vault.encrypt_personal(new_personal)  # replaces the previous details entirely
        profile.badge_hash = vault.badge_hash(new_personal["badge_number"]) if new_personal["badge_number"] else None
        profile.updated_at = datetime.now(timezone.utc)
        profile.updated_by = admin.name
        db.merge(profile)

        changed = [f for f in PROFILE_FIELDS if before[f] != new_values[f]]
        if old_personal != new_personal:
            changed.append("personal")
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="that badge number is already used by another guard")
        record(db, admin, "guard_profile_update", target=f"user:{user_id}", detail={"changed": changed})
        db.commit()
        return _public_profile(db, user, profile)
