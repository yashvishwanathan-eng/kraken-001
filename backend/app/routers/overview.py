"""P24. The main dashboard in one call, and data usage.

GET /overview   (any logged-in user; their cameras only) camera count, what is running, problems now,
                past problems (7 days), guards on and off duty, and one sentence to show or read aloud
GET /usage      (view_audit) disk used by snapshots, clips and logs, database size and rows, per-day counts
"""

import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import func, select, text

from app.config import BACKEND_DIR, get_settings, media_root
from app.db import SessionLocal
from app.deps import current_user, require
from app.models import AuditLog, Base, Camera, Incident, User
from app.schemas import DayCount, OverviewCamera, OverviewOut, OverviewProblem, UsageFiles, UsageOut
from app.services import camera_status, duty
from app.services.escalation import TYPE_LABELS
from app.services.permissions import allowed_cameras
from app.services.workers import workers

router = APIRouter(tags=["overview"])

SEVERITY_ORDER = {"critical": 0, "high": 1, "low": 2}


def _days(times: list[datetime], days: int) -> list[DayCount]:
    """Counts per IST day for the last `days` days, including days with zero."""
    tz = ZoneInfo(get_settings().timezone)
    counts = Counter(t.astimezone(tz).date().isoformat() for t in times)
    today = datetime.now(tz).date()
    return [DayCount(date=(today - timedelta(days=n)).isoformat(),
                     count=counts.get((today - timedelta(days=n)).isoformat(), 0)) for n in range(days - 1, -1, -1)]


@router.get("/overview", response_model=OverviewOut)
def overview(user: User = Depends(current_user)) -> OverviewOut:
    scope = allowed_cameras(user)
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        cameras = [c for c in db.scalars(select(Camera).order_by(Camera.number.nulls_last(), Camera.id))
                   if scope is None or c.id in scope]
        ids = [c.id for c in cameras]
        rows = []
        for cam in cameras:
            online, fps = camera_status.get(cam.id)
            rows.append(OverviewCamera(id=cam.id, number=cam.number, name=cam.name, online=online, fps=fps,
                                       enabled=cam.enabled, worker_running=online or workers.status(cam.id)["running"]))
        open_incidents = list(db.scalars(
            select(Incident).where(Incident.status.in_(("open", "acknowledged")), Incident.camera_id.in_(ids))
            .order_by(Incident.started_at.desc()).limit(200))) if ids else []
        week = list(db.scalars(select(Incident.started_at).where(
            Incident.camera_id.in_(ids), Incident.started_at >= now - timedelta(days=7)))) if ids else []
        names = {c.id: c.name for c in cameras}

        guards = [u for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.name))
                  if duty.checks_in(u.role) and (scope is None or set(u.camera_ids or []) & scope)]
        on_ids = duty.on_duty_ids(db, [g.id for g in guards]) if guards else set()

    problems = [OverviewProblem(kind="incident", severity=i.severity, incident_id=i.id, camera_id=i.camera_id,
                                text=f"{TYPE_LABELS.get(i.type, i.type)} at {names.get(i.camera_id, i.camera_id)} "
                                     f"({i.status})")
                for i in sorted(open_incidents, key=lambda i: SEVERITY_ORDER.get(i.severity, 3))[:20]]
    problems += [OverviewProblem(kind="camera_offline", severity="high", camera_id=c.id,
                                 text=f"Camera {c.number or c.id} {c.name} is offline")
                 for c in rows if c.enabled and not c.online]
    if guards and not on_ids:
        problems.append(OverviewProblem(kind="no_guard_on_duty", severity="high",
                                        text="No guard is checked in on duty: alerts go to the shift roster"))
    by_severity = dict(Counter(i.severity for i in open_incidents))
    online = sum(1 for c in rows if c.online)
    on_names = [g.name for g in guards if g.id in on_ids]
    off_names = [g.name for g in guards if g.id not in on_ids]
    sentence = (f"{online} of {len(rows)} cameras online. "
                f"{len(open_incidents)} open incident{'s' if len(open_incidents) != 1 else ''}. "
                f"{len(on_names)} guard{'s' if len(on_names) != 1 else ''} on duty, {len(off_names)} off duty.")
    return OverviewOut(
        at=now, cameras_total=len(rows), cameras_online=online, cameras_offline=len(rows) - online,
        workers_running=sum(1 for c in rows if c.worker_running), cameras=rows,
        open_incidents=len(open_incidents), open_by_severity=by_severity, problems_now=problems,
        past_problems=_days(week, 7), past_total_7d=len(week), guards_on_duty=on_names, guards_off_duty=off_names,
        ok=not problems,
        text=sentence + (" No problems." if not problems else f" {len(problems)} problems need a look."),
    )


def _files(paths: list[Path]) -> UsageFiles:
    return UsageFiles(count=len(paths), bytes=sum(p.stat().st_size for p in paths))


@router.get("/usage", response_model=UsageOut)
def usage(_: User = Depends(require("view_audit"))) -> UsageOut:
    media = media_root()
    files = [p for p in media.rglob("*") if p.is_file()]
    snapshots = [p for p in files if p.suffix.lower() in (".jpg", ".jpeg", ".png")]
    clips = [p for p in files if p.suffix.lower() in (".mp4", ".webm", ".avi")]
    other = [p for p in files if p not in snapshots and p not in clips]
    log_dir = BACKEND_DIR / get_settings().worker_log_dir
    logs = [p for p in log_dir.glob("*.log")] if log_dir.exists() else []
    disk = shutil.disk_usage(media)
    since = datetime.now(timezone.utc) - timedelta(days=30)
    with SessionLocal() as db:
        try:
            db_bytes = db.scalar(text("SELECT pg_database_size(current_database())"))
        except Exception:
            db.rollback()
            db_bytes = None
        rows = {t.name: db.scalar(select(func.count()).select_from(t)) or 0 for t in Base.metadata.sorted_tables}
        incidents = list(db.scalars(select(Incident.started_at).where(Incident.started_at >= since)))
        ai = list(db.scalars(select(AuditLog.at).where(AuditLog.at >= since,
                                                       AuditLog.action.in_(("assistant_query", "assistant_command")))))
    return UsageOut(media_total_bytes=sum(p.stat().st_size for p in files), snapshots=_files(snapshots),
                    clips=_files(clips), other_media=_files(other), worker_logs=_files(logs),
                    disk_free_bytes=disk.free, disk_total_bytes=disk.total, database_bytes=db_bytes, rows=rows,
                    incidents_by_day=_days(incidents, 30), ai_queries_by_day=_days(ai, 30))
