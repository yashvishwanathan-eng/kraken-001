"""GET /audit-log: who did what, when, and from where. Needs the view_audit action (Admin, Manager)."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import require
from app.models import AuditLog, User
from app.schemas import AuditRow

router = APIRouter(tags=["audit"])


@router.get("/audit-log", response_model=list[AuditRow])
def audit_log(
    user: str | None = Query(None, description="user name"),
    action: str | None = None,
    channel: Literal["dashboard", "phone", "voice"] | None = None,
    from_: datetime | None = Query(None, alias="from", description="ISO UTC"),
    to: datetime | None = Query(None, description="ISO UTC"),
    limit: int = Query(200, ge=1, le=1000),
    _: User = Depends(require("view_audit")),
) -> list[AuditRow]:
    query = select(AuditLog)
    for column, value in ((AuditLog.user_name, user), (AuditLog.action, action), (AuditLog.channel, channel)):
        if value is not None:
            query = query.where(column == value)
    if from_ is not None:
        query = query.where(AuditLog.at >= from_)
    if to is not None:
        query = query.where(AuditLog.at <= to)
    query = query.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(limit)
    with SessionLocal() as db:
        return [AuditRow(at=row.at, user=row.user_name, action=row.action, target=row.target, channel=row.channel)
                for row in db.scalars(query)]
