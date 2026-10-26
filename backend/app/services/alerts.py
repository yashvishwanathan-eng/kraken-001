"""Alert everybody and roll call.

alert_all():
  1. creates a roll call (linked to an incident if there is one)
  2. WhatsApps every on-shift guard in parallel (about 2 s for all)
  3. queues a roll-call call to each of them (1 new call per second; Twilio's default rate)
  4. returns a notice that every dashboard shows as the red banner
Each guard then presses 1 (responding) or 2 (safe/evacuated); roll_call_summary() counts them.
"""

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import PRODUCT_NAME
from app.db import SessionLocal
from app.models import Call, RollCall, User
from app.schemas import iso_utc
from app.services import incidents as incident_ops
from app.services.audit import record
from app.services.calls import NOT_REACHED, calls
from app.services.directory import on_shift_contacts
from app.services.whatsapp import whatsapp

log = logging.getLogger("safetyai.alerts")

VIA = {"dashboard": "dashboard", "phone": "keypad", "voice": "voice"}


class UnknownIncident(Exception):
    pass


def alert_all(message: str, *, by_name: str, user: User | None, channel: str,
              incident_id: int | None = None, via: str | None = None) -> dict:
    """Returns the alert_all notice (also the shape pushed on /ws/events)."""
    with SessionLocal() as db:
        incident = None
        if incident_id is not None:
            incident = incident_ops.load(db, incident_id)
            if incident is None:
                raise UnknownIncident(incident_id)
        roll_call = RollCall(incident_id=incident_id, message=message, started_by=by_name, channel=channel,
                             started_at=datetime.now(timezone.utc))
        db.add(roll_call)
        db.flush()
        contacts = on_shift_contacts(db)
        if incident is not None:
            # via="" means nobody pressed or said anything (the system raised it): no via on the timeline
            incident_ops.add_timeline(incident, "alert_all", by=by_name,
                                      via=VIA.get(channel) if via is None else (via or None))
        record(db, user, "alert_all", target=f"roll_call:{roll_call.id}", channel=channel, user_name=by_name,
               detail={"message": message, "recipients": len(contacts)})
        db.commit()
        roll_call_id, started_at = roll_call.id, roll_call.started_at
        incident_message = incident_ops.to_message(incident) if incident is not None else None

    sender = "Nobody answered the alert calls." if by_name == PRODUCT_NAME else f"From {by_name}."
    problem = message if message.startswith(PRODUCT_NAME) else f"{PRODUCT_NAME} ALERT: {message}"
    text = f"{problem} {sender} You will get a call: press 1 if responding, 2 if safe."
    sent = whatsapp.send_many([(c.phone, text, None) for c in contacts])
    queued = 0
    for c in contacts:
        if calls.place(kind="roll_call", to_name=c.name, to_phone=c.phone, responder_id=c.responder_id,
                       incident_id=incident_id, roll_call_id=roll_call_id, message=message) is not None:
            queued += 1
    log.info("roll_call=%d alert_all by %s: %d on shift, %d WhatsApp sent, %d calls queued",
             roll_call_id, by_name, len(contacts), sent, queued)
    return {
        "kind": "alert_all", "roll_call_id": roll_call_id, "incident_id": incident_id, "message": message,
        "by": by_name, "at": iso_utc(started_at), "recipients": len(contacts), "whatsapp_sent": sent,
        "calls_queued": queued, "incident": incident_message,
    }


def _guard_status(call: Call) -> str:
    if call.response in ("responding", "safe"):
        return call.response
    if call.status in NOT_REACHED or call.status == "completed":
        return "not_reached"  # did not answer, or hung up without pressing 1 or 2
    return "pending"


def roll_call_summary(db: DbSession, roll_call_id: int) -> dict | None:
    roll_call = db.get(RollCall, roll_call_id)
    if roll_call is None:
        return None
    rows = list(db.scalars(select(Call).where(Call.roll_call_id == roll_call_id).order_by(Call.id)))
    guards = [{"id": c.responder_id or c.id, "name": c.to_name, "status": _guard_status(c)} for c in rows]
    count = {status: sum(g["status"] == status for g in guards) for status in ("responding", "safe", "not_reached")}
    return {"roll_call_id": roll_call.id, "incident_id": roll_call.incident_id, "message": roll_call.message,
            "total": len(guards), **count, "guards": guards}


def latest_roll_call_id(db: DbSession, incident_id: int | None = None) -> int | None:
    query = select(RollCall.id).order_by(RollCall.id.desc()).limit(1)
    if incident_id is not None:
        query = query.where(RollCall.incident_id == incident_id)
    return db.scalar(query)


def roll_call_notice(roll_call_id: int) -> dict | None:
    with SessionLocal() as db:
        summary = roll_call_summary(db, roll_call_id)
    return {"kind": "roll_call", **summary} if summary else None
