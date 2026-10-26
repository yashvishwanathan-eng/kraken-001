"""Every person is on the call list: their phone number lives on a responder row linked to their user.

Adding or inviting a person creates that row; editing their phone, zone or shift updates it. An inactive
person (deactivated, or an invitation not accepted yet) is never called: the call ladder skips inactive users.
"""

from datetime import time

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Responder, User


def _time(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


def for_user(db: DbSession, user_id: int) -> Responder | None:
    return db.scalar(select(Responder).where(Responder.user_id == user_id).order_by(Responder.id).limit(1))


def upsert(db: DbSession, user: User, phone: str | None = None, zone: str | None = None,
           shift_start: str | None = None, shift_end: str | None = None) -> Responder | None:
    """Create or update the user's call-list row. Without a phone and without a row, nothing is created."""
    row = for_user(db, user.id)
    if row is None:
        if phone is None:
            return None
        row = Responder(user_id=user.id, name=user.name, phone=phone, zone=zone or "general",
                        shift_start=_time(shift_start or "00:00"), shift_end=_time(shift_end or "00:00"),
                        call_order=1, active=True)
        db.add(row)
    else:
        row.name = user.name
        if phone is not None:
            row.phone = phone
        if zone is not None:
            row.zone = zone
        if shift_start is not None:
            row.shift_start = _time(shift_start)
        if shift_end is not None:
            row.shift_end = _time(shift_end)
    return row


def fields(row: Responder | None) -> dict:
    """The phone fields shown with a user."""
    if row is None:
        return {"phone": None, "zone": None, "shift_start": None, "shift_end": None}
    return {"phone": row.phone, "zone": row.zone, "shift_start": row.shift_start.strftime("%H:%M"),
            "shift_end": row.shift_end.strftime("%H:%M")}
