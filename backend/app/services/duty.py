"""P23. On duty / off duty, from QR check-ins.

Three ways to check in or out, all ending in one DutyEvent row:
  - pass_scan: the guard shows their personal QR pass (on their phone) and the control room scans it
  - post_qr:   the guard scans the QR code printed at their post with their phone (logged in with their PIN)
  - dashboard: a supervisor sets it by hand
A scan toggles: on duty -> off duty, off duty -> on duty.

On duty = the latest event is "in" and is less than DUTY_MAX_H old (a forgotten check-out expires).
Only the roles in DUTY_ROLES (Head Guard, Guard) check in. Admins and managers are always callable.

The call ladder calls on-duty guards. If nobody of the needed role is on duty, it falls back to the shift
roster, logs a warning and tells the Head Guard, so an alert is never silently dropped.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import DutyEvent, User

PASS = "duty-pass"  # signed value = user id
POST = "duty-post"  # signed value = area id


def duty_roles() -> set[str]:
    return {r.strip() for r in get_settings().duty_roles.split(",") if r.strip()}


def checks_in(role: str | None) -> bool:
    return role in duty_roles()


def latest_events(db: DbSession, user_ids: list[int] | None = None) -> dict[int, DutyEvent]:
    """The newest duty event per user."""
    newest = select(DutyEvent.user_id, func.max(DutyEvent.id).label("id")).group_by(DutyEvent.user_id)
    if user_ids is not None:
        newest = newest.where(DutyEvent.user_id.in_(user_ids))
    sub = newest.subquery()
    rows = db.scalars(select(DutyEvent).join(sub, DutyEvent.id == sub.c.id))
    return {row.user_id: row for row in rows}


def is_on(event: DutyEvent | None, now: datetime | None = None) -> bool:
    if event is None or event.action != "in":
        return False
    now = now or datetime.now(timezone.utc)
    at = event.at if event.at.tzinfo else event.at.replace(tzinfo=timezone.utc)
    return now - at <= timedelta(hours=get_settings().duty_max_h)


def on_duty_ids(db: DbSession, user_ids: list[int] | None = None, now: datetime | None = None) -> set[int]:
    return {uid for uid, ev in latest_events(db, user_ids).items() if is_on(ev, now)}


def set_duty(db: DbSession, user: User, action: str, method: str, by_name: str,
             area_id: int | None = None) -> DutyEvent:
    """action: in, out or toggle. Returns the new event (not committed)."""
    if action == "toggle":
        action = "out" if is_on(latest_events(db, [user.id]).get(user.id)) else "in"
    event = DutyEvent(at=datetime.now(timezone.utc), user_id=user.id, action=action, method=method,
                      by_name=by_name, area_id=area_id)
    db.add(event)
    db.flush()
    return event
