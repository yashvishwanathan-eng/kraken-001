"""Who to contact: responders (phone, zone, shift) linked to users (role, cameras).

A responder row holds the phone number and shift. Its linked user holds the role label
("Head Guard", "Admin", "Manager") and the delegated cameras. The call ladder asks:
"the Head Guard for this camera, on shift now, lowest call_order first".
"""

from dataclasses import dataclass
from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import Responder, User
from app.services.permissions import can_see


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


def _contact(r: Responder, user: User | None, now_local: time) -> Contact:
    return Contact(r.id, r.name, r.phone, r.zone, user.role if user else None, user.id if user else None,
                   on_shift(now_local, r.shift_start, r.shift_end), r.call_order)


def contacts_for_role(db: DbSession, role: str, camera_id: str | None = None, zone: str | None = None,
                      now_utc: datetime | None = None) -> list[Contact]:
    """Active responders whose user has this role (and can see the camera). On-shift first, then the
    ones for this zone, then call_order. Off-shift people stay at the end as a last resort."""
    now_local = _now_local(now_utc)
    rows = db.execute(
        select(Responder, User).join(User, Responder.user_id == User.id)
        .where(Responder.active.is_(True), User.active.is_(True), User.role == role)
    ).all()
    contacts = [_contact(r, u, now_local) for r, u in rows if camera_id is None or can_see(u, camera_id)]
    return sorted(contacts, key=lambda c: (not c.on_shift, zone is not None and c.zone != zone, c.call_order))


def on_shift_contacts(db: DbSession, now_utc: datetime | None = None, zone: str | None = None) -> list[Contact]:
    """Everyone on the roster who is on shift now (optionally only one zone). Used by alert-all and broadcast."""
    now_local = _now_local(now_utc)
    rows = db.execute(
        select(Responder, User).outerjoin(User, Responder.user_id == User.id)
        .where(Responder.active.is_(True)).order_by(Responder.call_order, Responder.id)
    ).all()
    contacts = [_contact(r, u, now_local) for r, u in rows if u is None or u.active]
    return [c for c in contacts if c.on_shift and (zone is None or c.zone == zone)]


def contact_by_name(db: DbSession, text: str) -> Contact | None:
    """Find a responder whose name appears in (or matches the start of) the text. Used by "call Ravi"."""
    now_local = _now_local(None)
    text = text.lower()
    rows = db.execute(select(Responder, User).outerjoin(User, Responder.user_id == User.id)
                      .where(Responder.active.is_(True))).all()
    for r, u in rows:
        first = r.name.lower().split()[0]
        if r.name.lower() in text or first in text.split():
            return _contact(r, u, now_local)
    return None
