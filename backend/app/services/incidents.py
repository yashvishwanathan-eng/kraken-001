"""Incident actions shared by the dashboard, the phone and the laptop assistant.

Acknowledge and close always do the same four things, whoever triggers them:
  1. change the incident (inside a row lock, so two at once cannot both win)
  2. add a timeline entry
  3. write an audit row (who, what, channel)
  4. stop the call ladder, and push the update to dashboards (the caller pushes after commit)
"""

from datetime import datetime, timezone

from sqlalchemy.orm import Session as DbSession
from sqlalchemy.orm import selectinload

from app.models import Incident, TimelineEvent, User
from app.schemas import IncidentOut
from app.services.audit import record

CLOSED = {"resolved", "false_alarm"}


class IncidentClosed(Exception):
    """The incident is already resolved or marked false alarm."""


def load(db: DbSession, incident_id: int, lock: bool = False) -> Incident | None:
    return db.get(Incident, incident_id, with_for_update=lock,
                  options=[selectinload(Incident.timeline), selectinload(Incident.camera)])


def add_timeline(incident: Incident, action: str, *, to: str | None = None, by: str | None = None,
                 via: str | None = None) -> None:
    # append to the loaded list (not db.add) so a response built right after includes it
    incident.timeline.append(TimelineEvent(at=datetime.now(timezone.utc), action=action, target=to, actor=by, via=via))


def acknowledge(db: DbSession, incident: Incident, *, name: str, user: User | None, via: str, channel: str) -> bool:
    """Returns True if this call acknowledged it, False if it was already acknowledged (idempotent)."""
    if incident.status == "acknowledged":
        return False
    if incident.status in CLOSED:
        raise IncidentClosed(incident.status)
    incident.status = "acknowledged"
    incident.acknowledged_by = name
    incident.acknowledged_by_user_id = user.id if user else None
    add_timeline(incident, "acknowledged", by=name, via=via)
    record(db, user, "ack", target=f"incident:{incident.id}", channel=channel, user_name=name)
    return True


def close(db: DbSession, incident: Incident, outcome: str, *, name: str, user: User | None, via: str,
          channel: str, note: str | None = None) -> None:
    """outcome: resolved or false_alarm."""
    if incident.status in CLOSED:
        raise IncidentClosed(incident.status)
    incident.status = outcome
    add_timeline(incident, outcome, by=name, via=via)
    record(db, user, outcome, target=f"incident:{incident.id}", channel=channel, user_name=name,
           detail={"note": note} if note else None)


def to_message(incident: Incident) -> dict:
    return IncidentOut.from_model(incident).model_dump(mode="json")
