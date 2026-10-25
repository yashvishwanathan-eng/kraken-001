"""P20. Sector summaries: what happened today on the cameras this person may see.

Scope: a user sees the cameras delegated to them (a Head Guard with 4 or 5 cameras gets those); a user with
all_cameras gets the overview of every camera. Login logs are included only for users with view_audit.
Violations are restricted-area entries (access incidents). The text is written from these counts only.
"""

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession, selectinload

from app.config import get_settings
from app.models import AuditLog, Camera, Incident, User
from app.services import camera_status
from app.services.badges import incident_badge_status
from app.services.permissions import allowed_cameras


def today_window(now: datetime | None = None) -> tuple[datetime, datetime]:
    """From local midnight (IST) to now, as UTC."""
    tz = ZoneInfo(get_settings().timezone)
    now = now or datetime.now(timezone.utc)
    local = now.astimezone(tz)
    return datetime.combine(local.date(), time(0, 0), tzinfo=tz).astimezone(timezone.utc), now


def build(db: DbSession, user: User, start: datetime, end: datetime) -> dict:
    tz = get_settings().timezone
    scope = allowed_cameras(user)
    cameras = list(db.scalars(select(Camera).order_by(Camera.id)))
    if scope is not None:
        cameras = [c for c in cameras if c.id in scope]
    camera_ids = [c.id for c in cameras]

    incidents = list(db.scalars(
        select(Incident).options(selectinload(Incident.timeline))
        .where(Incident.started_at >= start, Incident.started_at <= end, Incident.camera_id.in_(camera_ids))
        .order_by(Incident.started_at.desc())
    )) if camera_ids else []

    by_status: dict[str, int] = {}
    by_type: dict[str, int] = {}
    for inc in incidents:
        by_status[inc.status] = by_status.get(inc.status, 0) + 1
        by_type[inc.type] = by_type.get(inc.type, 0) + 1

    entries = [inc for inc in incidents if inc.type == "access"]
    open_entries = [inc for inc in entries if inc.status == "open"]

    camera_rows = []
    for cam in cameras:
        online, fps = camera_status.get(cam.id)
        camera_rows.append({
            "camera_id": cam.id, "name": cam.name, "online": online, "fps": round(fps, 1),
            "incidents": sum(1 for inc in incidents if inc.camera_id == cam.id),
            "open": sum(1 for inc in incidents if inc.camera_id == cam.id and inc.status == "open"),
        })

    logins: list[dict] = []
    if "view_audit" in user.actions or "all_cameras" in user.actions:
        rows = db.scalars(select(AuditLog).where(AuditLog.action == "login", AuditLog.at >= start,
                                                 AuditLog.at <= end).order_by(AuditLog.at.desc()).limit(200))
        logins = [{"at": r.at, "user_name": r.user_name, "channel": r.channel} for r in rows]

    result = {
        "from": start, "to": end,
        "sector": {"all_cameras": scope is None, "camera_ids": camera_ids},
        "incidents": {"total": len(incidents), "by_status": by_status, "by_type": by_type},
        "violations": {"restricted_entries": len(entries), "still_open": len(open_entries),
                       "without_badge": sum(1 for inc in entries if _badge(db, inc) == "not_scanned"),
                       "items": [_entry(db, inc) for inc in entries[:50]]},
        "cameras": camera_rows,
        "logins": logins,
    }
    result["text"] = _text(result, tz)
    return result


def _badge(db: DbSession, inc: Incident) -> str:
    return incident_badge_status(db, inc)


def _entry(db: DbSession, inc: Incident) -> dict:
    return {"incident_id": inc.id, "at": inc.started_at, "camera_id": inc.camera_id, "zone": inc.zone,
            "status": inc.status, "acknowledged_by": inc.acknowledged_by or None,
            "badge_status": _badge(db, inc)}


def _clock(value: datetime, tz: str) -> str:
    return value.astimezone(ZoneInfo(tz)).strftime("%H:%M")


def _text(r: dict, tz: str) -> str:
    cams = r["cameras"]
    offline = [c["name"] for c in cams if not c["online"]]
    scope = "all cameras" if r["sector"]["all_cameras"] else f"{len(cams)} of your cameras"
    parts = [f"Summary for {scope} from {_clock(r['from'], tz)} to {_clock(r['to'], tz)}."]
    v = r["violations"]
    if v["restricted_entries"] == 0:
        parts.append("No violations: no restricted-area entries.")
    elif v["without_badge"]:
        parts.append(f"{v['restricted_entries']} restricted-area entries, {v['without_badge']} with no badge scan, "
                     f"{v['still_open']} still open.")
    else:
        latest = v["items"][0]
        where = latest["zone"].replace("-", " ") if latest["zone"] else "an unknown zone"
        parts.append(f"{v['restricted_entries']} restricted-area entr{'y' if v['restricted_entries'] == 1 else 'ies'}, "
                     f"{v['still_open']} still open. Latest at {where} at {_clock(latest['at'], tz)}.")
    total = r["incidents"]["total"]
    if total:
        status = ", ".join(f"{n} {s.replace('_', ' ')}" for s, n in sorted(r["incidents"]["by_status"].items()))
        parts.append(f"{total} incident{'s' if total != 1 else ''} in total: {status}.")
    else:
        parts.append("No incidents.")
    if offline:
        parts.append(f"Offline: {', '.join(offline)}.")
    elif cams:
        parts.append("All cameras are online.")
    if r["logins"]:
        parts.append(f"{len(r['logins'])} login{'s' if len(r['logins']) != 1 else ''} today.")
    return " ".join(parts)
