"""P24. The activity log: who used what. Admin and Manager only (view_audit); a Head Guard cannot see it.

GET /activity ?from&to&user (default the last 24 hours)
  users:         per person: actions, AI questions, AI commands, camera views and which cameras
  ai:            every question and command given to the assistant, with its answer
  camera_views:  who opened which camera feed, and when

It is read from the audit log, which records every action including the admin's own: the assistant
writes assistant_query and assistant_command rows, the stream proxy writes camera_view rows.
The assistant does not learn from use; this is a record of what it was asked and what it said.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from app.db import SessionLocal
from app.deps import require
from app.models import AuditLog, User
from app.schemas import ActivityAi, ActivityOut, ActivityUser, ActivityView

router = APIRouter(tags=["activity"])

AI_ACTIONS = {"assistant_query": "question", "assistant_command": "command"}


@router.get("/activity", response_model=ActivityOut)
def activity(start: datetime | None = Query(None, alias="from"), end: datetime | None = Query(None, alias="to"),
             user: str | None = Query(None, description="user name"),
             _: User = Depends(require("view_audit"))) -> ActivityOut:
    end = end or datetime.now(timezone.utc)
    start = start or end - timedelta(hours=24)
    if end <= start:
        raise HTTPException(status_code=422, detail="'to' must be after 'from'")
    query = select(AuditLog).where(AuditLog.at >= start, AuditLog.at <= end)
    if user is not None:
        query = query.where(AuditLog.user_name == user)
    with SessionLocal() as db:
        rows = list(db.scalars(query.order_by(AuditLog.at.desc(), AuditLog.id.desc()).limit(5000)))
        roles = {u.id: u.role for u in db.scalars(select(User))}

    people: dict[str, dict] = {}
    ai, views = [], []
    for row in rows:
        person = people.setdefault(row.user_name, {
            "user_id": row.user_id, "user": row.user_name, "role": roles.get(row.user_id), "actions": 0,
            "ai_questions": 0, "ai_commands": 0, "camera_views": 0, "cameras_viewed": set(), "last_at": row.at})
        detail = row.detail or {}
        if row.action in AI_ACTIONS:
            kind = AI_ACTIONS[row.action]
            person["ai_questions" if kind == "question" else "ai_commands"] += 1
            if len(ai) < 200:
                ai.append(ActivityAi(at=row.at, user=row.user_name, channel=row.channel, kind=kind,
                                     text=str(detail.get("text", "")), answer=str(detail.get("answer", "")),
                                     intent=detail.get("intent"), status=detail.get("status"),
                                     source=detail.get("source")))
        elif row.action == "camera_view":
            camera_id = (row.target or "").removeprefix("camera:")
            person["camera_views"] += 1
            person["cameras_viewed"].add(camera_id)
            if len(views) < 200:
                views.append(ActivityView(at=row.at, user=row.user_name, camera_id=camera_id))
        else:
            person["actions"] += 1
    users = [ActivityUser(**{**p, "cameras_viewed": sorted(p["cameras_viewed"])}) for p in people.values()]
    return ActivityOut(from_=start, to=end, users=users, ai=ai, camera_views=views)
