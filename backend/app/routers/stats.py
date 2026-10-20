"""GET /stats: numbers for the Overview page, limited to the caller's cameras.

Window: `from`/`to` (ISO UTC), default the last 7 days.
- by_type: incidents per type in the window
- by_day: incidents per IST calendar day in the window (the dashboard shows IST)
- open: not yet closed (status open or acknowledged); resolved: closed (resolved or false alarm)
- avg_response_s: average seconds from detection to the first acknowledgement, over acknowledged incidents
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db import SessionLocal
from app.deps import current_user
from app.models import Incident, User
from app.schemas import DayCount, StatsOut
from app.services.permissions import allowed_cameras

router = APIRouter(tags=["stats"])

DEFAULT_WINDOW = timedelta(days=7)


@router.get("/stats", response_model=StatsOut)
def stats(
    from_: datetime | None = Query(None, alias="from", description="ISO UTC; default 7 days ago"),
    to: datetime | None = Query(None, description="ISO UTC; default now"),
    user: User = Depends(current_user),
) -> StatsOut:
    end = to or datetime.now(timezone.utc)
    start = from_ or end - DEFAULT_WINDOW
    query = (select(Incident).options(selectinload(Incident.timeline))
             .where(Incident.started_at >= start, Incident.started_at <= end))
    scope = allowed_cameras(user)
    if scope is not None:
        query = query.where(Incident.camera_id.in_(scope))
    local_tz = ZoneInfo(get_settings().timezone)
    with SessionLocal() as db:
        incidents = list(db.scalars(query))
        by_day = Counter(i.started_at.astimezone(local_tz).date().isoformat() for i in incidents)
        response_times = []
        for incident in incidents:
            first_ack = next((e.at for e in incident.timeline if e.action == "acknowledged"), None)
            if first_ack is not None:
                response_times.append((first_ack - incident.started_at).total_seconds())
        return StatsOut(
            by_type=dict(Counter(i.type for i in incidents)),
            by_day=[DayCount(date=day, count=count) for day, count in sorted(by_day.items())],
            open=sum(i.status in ("open", "acknowledged") for i in incidents),
            resolved=sum(i.status in ("resolved", "false_alarm") for i in incidents),
            avg_response_s=round(sum(response_times) / len(response_times), 1) if response_times else None,
        )
