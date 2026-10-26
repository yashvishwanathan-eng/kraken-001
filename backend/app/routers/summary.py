"""P20. GET /summary: today's (or a chosen window's) summary for the cameras the caller may see.

GET /summary?from=&to=&cameras=   (any logged-in user; defaults to today from local midnight IST until now)
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from app.db import SessionLocal
from app.deps import current_user
from app.models import User
from app.schemas import SummaryOut
from app.services.summary import build, today_window

router = APIRouter(prefix="/summary", tags=["summary"])


@router.get("", response_model=SummaryOut)
def get_summary(user: User = Depends(current_user),
                start: datetime | None = Query(None, alias="from"),
                end: datetime | None = Query(None, alias="to"),
                cameras: str | None = Query(None, description="comma-separated camera ids; default all yours")
                ) -> SummaryOut:
    default_start, default_end = today_window()
    start = _utc(start) or default_start
    end = _utc(end) or default_end
    if end <= start:
        raise HTTPException(status_code=422, detail="'to' must be after 'from'")
    with SessionLocal() as db:
        picked = [c.strip() for c in cameras.split(",") if c.strip()] if cameras else None
        return build(db, db.merge(user), start, end, picked)


def _utc(value: datetime | None) -> datetime | None:
    """A time without a zone is taken as UTC; None stays None."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
