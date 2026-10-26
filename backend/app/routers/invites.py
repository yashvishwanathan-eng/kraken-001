"""P19. Invitations: an admin invites someone by email; they open the link or scan the QR code and set their PIN.

POST /invites                    (manage_users) create the account (inactive) and the invitation, email the link
GET  /invites                    (manage_users) list invitations and their status
POST /invites/{id}/revoke        (manage_users) cancel an invitation that is still pending
GET  /invites/link/{token}       (public) who the invitation is for; 404 if unknown, used, expired or revoked
POST /invites/link/{token}/accept (public) body {pin}: sets the PIN, activates the account, returns a login

The token is 32 random bytes. Only its SHA-256 is stored. A link works once and for 48 hours.
"""

import base64
import hashlib
import io
import secrets
from datetime import datetime, timedelta, timezone

import qrcode
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.db import SessionLocal
from app.deps import require
from app.models import Invitation, User
from app.schemas import InviteAccept, InviteCreate, InviteCreated, InviteOut, InvitePreview, LoginResponse, UserBrief
from app.services.audit import record
from app.services.email_sender import emailer
from app.services import roles
from app.services.permissions import create_session, find_user_by_pin, hash_pin, staff_id
from app.routers.users import _validate

router = APIRouter(prefix="/invites", tags=["invites"])


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _status(inv: Invitation, now: datetime) -> str:
    if inv.revoked:
        return "revoked"
    if inv.used_at is not None:
        return "accepted"
    if inv.expires_at <= now:
        return "expired"
    return "pending"


def _as_utc(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _qr_png(text: str) -> str:
    buffer = io.BytesIO()
    qrcode.make(text, box_size=8, border=2).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _invite_out(inv: Invitation, user: User, creator: str) -> InviteOut:
    return InviteOut(invite_id=inv.id, user_id=user.id, staff_id=staff_id(user.id), name=user.name, email=inv.email,
                     role=user.role,
                     status=_status(inv, datetime.now(timezone.utc)), expires_at=_as_utc(inv.expires_at),
                     created_by=creator)


def _find_valid(db: DbSession, token: str) -> tuple[Invitation, User]:
    inv = db.scalar(select(Invitation).where(Invitation.token_hash == _hash(token)))
    user = db.get(User, inv.user_id) if inv else None
    if inv is None or user is None or _status(inv, datetime.now(timezone.utc)) != "pending":
        raise HTTPException(status_code=404, detail="this invitation link is not valid any more")
    return inv, user


@router.post("", response_model=InviteCreated, status_code=201)
def create_invite(body: InviteCreate, admin: User = Depends(require("manage_users"))) -> InviteCreated:
    settings = get_settings()
    with SessionLocal() as db:
        camera_ids, actions = roles.defaults(db, body.role, body.camera_ids, body.actions)
        _validate(db, camera_ids, actions, None, None)
        if db.scalar(select(Invitation).where(Invitation.email == body.email, Invitation.used_at.is_(None),
                                              Invitation.revoked.is_(False))):
            raise HTTPException(status_code=409, detail="a pending invitation already exists for this email")
        # inactive account: cannot log in until the invitation is accepted. The PIN hash is a random placeholder.
        user = User(name=body.name, role=body.role, camera_ids=camera_ids, actions=actions, active=False,
                    pin_hash=hash_pin(secrets.token_urlsafe(24)))
        db.add(user)
        db.flush()
        token = secrets.token_urlsafe(32)
        inv = Invitation(token_hash=_hash(token), user_id=user.id, email=body.email, created_by=admin.name,
                         expires_at=datetime.now(timezone.utc) + timedelta(hours=settings.invite_ttl_h))
        db.add(inv)
        db.flush()
        link = f"{settings.dashboard_url.rstrip('/')}/invite/{token}"
        status = emailer.send(
            body.email,
            "You are invited to RingGuard",
            f"Hello {body.name},\n\n{admin.name} invited you to RingGuard as {body.role}.\n"
            f"Your staff ID is {staff_id(user.id)}. Keep it: it is on your duty pass and in every log.\n"
            f"Open this link on your laptop or phone to set your PIN (valid for {int(settings.invite_ttl_h)} hours):\n"
            f"{link}\n\nIf you did not expect this, you can ignore this email.",
        )
        record(db, admin, "invite_create", target=f"user:{user.id}",
               detail={"email": body.email, "role": body.role, "email_status": status})
        db.commit()
        out = _invite_out(inv, user, admin.name)
        return InviteCreated(**out.model_dump(), link=link, qr_png=_qr_png(link), email_status=status)


@router.get("", response_model=list[InviteOut])
def list_invites(_: User = Depends(require("manage_users"))) -> list[InviteOut]:
    with SessionLocal() as db:
        rows = db.execute(select(Invitation, User).join(User, User.id == Invitation.user_id)
                          .order_by(Invitation.id.desc()).limit(200)).all()
        return [_invite_out(inv, user, inv.created_by) for inv, user in rows]


@router.post("/{invite_id}/revoke", response_model=InviteOut)
def revoke_invite(invite_id: int, admin: User = Depends(require("manage_users"))) -> InviteOut:
    with SessionLocal() as db:
        inv = db.get(Invitation, invite_id)
        if inv is None:
            raise HTTPException(status_code=404, detail=f"invitation {invite_id} not found")
        if _status(inv, datetime.now(timezone.utc)) != "pending":
            raise HTTPException(status_code=409, detail="only a pending invitation can be revoked")
        inv.revoked = True
        user = db.get(User, inv.user_id)
        record(db, admin, "invite_revoke", target=f"user:{user.id}")
        db.commit()
        return _invite_out(inv, user, inv.created_by)


@router.get("/link/{token}", response_model=InvitePreview)
def preview_invite(token: str) -> InvitePreview:
    with SessionLocal() as db:
        inv, user = _find_valid(db, token)
        return InvitePreview(name=user.name, email=inv.email, role=user.role, expires_at=_as_utc(inv.expires_at))


@router.post("/link/{token}/accept", response_model=LoginResponse)
def accept_invite(token: str, body: InviteAccept) -> LoginResponse:
    with SessionLocal() as db:
        inv, user = _find_valid(db, token)
        if find_user_by_pin(db, body.pin, exclude_user_id=user.id) is not None:
            raise HTTPException(status_code=409, detail="that PIN is already used by another user; pick another")
        user.pin_hash = hash_pin(body.pin)
        user.active = True
        inv.used_at = datetime.now(timezone.utc)
        session_token = create_session(db, user)
        record(db, user, "invite_accepted", target=f"user:{user.id}")
        db.commit()
        return LoginResponse(token=session_token, user=UserBrief(id=user.id, name=user.name, role=user.role))
