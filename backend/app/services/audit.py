"""Audit log helper: every action records who, what, when and from where (dashboard, phone, voice).

Call record() inside the same database transaction as the change itself, so the action and its
audit row are saved together or not at all.
"""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models import AuditLog, User


def record(db: DbSession, user: User | None, action: str, target: str | None = None,
           channel: str = "dashboard", detail: dict[str, Any] | None = None, user_name: str | None = None) -> None:
    db.add(AuditLog(
        at=datetime.now(timezone.utc),
        user_id=user.id if user else None,
        user_name=user.name if user else (user_name or "unknown"),
        action=action,
        target=target,
        channel=channel,
        detail=detail,
    ))
