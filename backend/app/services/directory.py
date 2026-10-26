"""Who to contact: responders (phone, zone, shift) linked to users (role, cameras).

A responder row holds the phone number and shift. Its linked user holds the role label
("Head Guard", "Admin", "Manager") and the delegated cameras. The call ladder asks:
"the Head Guard for this camera, on shift now, lowest call_order first".

Duty (P23): Head Guards and Guards check in with a QR code. Off duty = not called. When nobody of the
needed role is on duty, the shift roster is used instead and the caller is told (fallback=True), so it
can log a warning and tell the Head Guard. Nothing is silently dropped.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import Responder, User
from app.services import duty
from app.services.permissions import can_see

log = logging.getLogger("safetyai.directory")


@dataclass(frozen=True)
class Contact:
    responder_id: int
    name: str
    phone: str
    zone: str
    role: str | None
    user_id: int | None
    on_shift: bool
    call_order: int
    on_duty: bool = True  # False: a Head Guard or Guard who has not checked in (or checked out)


def on_shift(now_local: time, start: time, end: time) -> bool:
    """Shift check that also works for night shifts crossing midnight (20:00-08:00)."""
    if start == end:
        return True  # a 24-hour shift
    if start < end:
        return start <= now_local < end
    return now_local >= start or now_local < end


def _now_local(now_utc: datetime | None) -> time:
    now_utc = now_utc or datetime.now(timezone.utc)
    return now_utc.astimezone(ZoneInfo(get_settings().timezone)).time()


def _contact(r: Responder, user: User | None, now_local: time, on_duty_ids: set[int] | None = None) -> Contact:
    checks_in = user is not None and duty.checks_in(user.role)
    on_duty = not checks_in or (on_duty_ids is not None and user.id in on_duty_ids)
    return Contact(r.id, r.name, r.phone, r.zone, user.role if user else None, user.id if user else None,
                   on_shift(now_local, r.shift_start, r.shift_end), r.call_order, on_duty)


def _on_duty_ids(db: DbSession, users: list[User | None], now_utc: datetime | None) -> set[int]:
    ids = [u.id for u in users if u is not None and duty.checks_in(u.role)]
    return duty.on_duty_ids(db, ids, now_utc) if ids else set()


def contacts_for_role(db: DbSession, role: str, camera_id: str | None = None, zone: str | None = None,
                      now_utc: datetime | None = None) -> list[Contact]:
    """Active responders whose user has this role (and can see the camera). On-shift first, then the
    ones for this zone, then call_order. Off-shift people stay at the end as a last resort."""
    now_local = _now_local(now_utc)
    rows = db.execute(
        select(Responder, User).join(User, Responder.user_id == User.id)
        .where(Responder.active.is_(True), User.active.is_(True), User.role == role)
    ).all()
    rows = [(r, u) for r, u in rows if camera_id is None or can_see(u, camera_id)]
    on_duty_ids = _on_duty_ids(db, [u for _, u in rows], now_utc)
    contacts = [_contact(r, u, now_local, on_duty_ids) for r, u in rows]
    return sorted(contacts, key=lambda c: (not c.on_duty, not c.on_shift, zone is not None and c.zone != zone,
                                           c.call_order))


def contacts_on_duty(db: DbSession, role: str, camera_id: str | None = None, zone: str | None = None,
                     now_utc: datetime | None = None) -> tuple[list[Contact], bool]:
    """(contacts to call, fallback). Only on-duty people, unless nobody of this role is on duty: then the
    whole shift roster for the role, with fallback=True so the caller warns the Head Guard."""
    contacts = contacts_for_role(db, role, camera_id, zone, now_utc)
    on = [c for c in contacts if c.on_duty]
    if on or not contacts:
        return on, False
    log.warning("nobody with role %s is on duty%s: using the shift roster", role,
                f" for camera {camera_id}" if camera_id else "")
    return contacts, True


def on_shift_contacts(db: DbSession, now_utc: datetime | None = None, zone: str | None = None) -> list[Contact]:
    """Everyone on the roster who is on shift now (optionally only one zone). Used by alert-all and broadcast."""
    return on_shift_contacts_with_fallback(db, now_utc, zone)[0]


def on_shift_contacts_with_fallback(db: DbSession, now_utc: datetime | None = None,
                                    zone: str | None = None) -> tuple[list[Contact], bool]:
    """On shift now, and for guards also on duty. If no guard at all is on duty, every on-shift guard is
    included instead (fallback=True)."""
    now_local = _now_local(now_utc)
    rows = db.execute(
        select(Responder, User).outerjoin(User, Responder.user_id == User.id)
        .where(Responder.active.is_(True)).order_by(Responder.call_order, Responder.id)
    ).all()
    rows = [(r, u) for r, u in rows if u is None or u.active]
    on_duty_ids = _on_duty_ids(db, [u for _, u in rows], now_utc)
    contacts = [_contact(r, u, now_local, on_duty_ids) for r, u in rows]
    contacts = [c for c in contacts if c.on_shift and (zone is None or c.zone == zone)]
    guards = [c for c in contacts if duty.checks_in(c.role)]
    if guards and not any(c.on_duty for c in guards):
        log.warning("no guard is on duty%s: using every on-shift guard", f" in {zone}" if zone else "")
        return contacts, True
    return [c for c in contacts if c.on_duty], False


def contact_by_name(db: DbSession, text: str) -> Contact | None:
    """Find a responder whose name appears in (or matches the start of) the text. Used by "call Ravi"."""
    now_local = _now_local(None)
    text = text.lower()
    rows = db.execute(select(Responder, User).outerjoin(User, Responder.user_id == User.id)
                      .where(Responder.active.is_(True))).all()
    for r, u in rows:
        first = r.name.lower().split()[0]
        if r.name.lower() in text or first in text.split():
            return _contact(r, u, now_local, _on_duty_ids(db, [u], None))
    return None
