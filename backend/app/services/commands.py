"""Laptop assistant commands (push-to-talk).

Three paths, fastest first:
  1. fast path: about 12 known phrases matched by patterns. No model, instant.
  2. smart path: Groq function calling picks one of the same commands for anything else.
  3. knowledge path: SOP questions are answered from our documents (services/knowledge.py).

Big actions (call everyone, evacuate, call the fire station) are never done straight away: the
reply is status "needs_confirmation" with a confirm_token, and only a "yes" on /assistant/confirm
(same user, within 60 s) runs them. Calling one person or one zone is not gated.
The fire station is only ever called because a human ordered it here; nothing calls it automatically.
"""

import logging
import re
import secrets
import threading
import time
from dataclasses import dataclass, field

from sqlalchemy import func, select

from app.config import PRODUCT_NAME, get_settings
from app.db import SessionLocal
from app.models import Camera, Incident, User
from app.services import alerts, camera_status, incidents as incident_ops
from app.services.assistant import ask
from app.services.audit import record
from app.services.calls import calls
from app.services.directory import contact_by_name, on_shift_contacts
from app.services.escalation import escalation
from app.services.knowledge import knowledge
from app.services.llm import choose_tool
from app.services.permissions import allowed_cameras, can, can_see
from app.services.whatsapp import whatsapp

log = logging.getLogger("safetyai.commands")

BIG_ACTIONS = {"alert_all", "evacuate", "call_fire_station"}
EVACUATE_MESSAGE = "Evacuate now. Leave by the nearest exit and go to the assembly point."
ALERT_ALL_MESSAGE = "Emergency. All guards respond now."

# Which action a user needs for each command (permissions are data: see services/permissions.py).
NEEDS = {
    "alert_all": "alert_all", "evacuate": "alert_all", "call_fire_station": "alert_all", "call_person": "alert_all",
    "call_zone": "alert_all", "broadcast_message": "broadcast", "ack": "ack", "resolve": "resolve",
    "stop_calls": "alert_all",
}

# Fast path: (intent, pattern). First match wins, so specific phrases come before general ones.
PATTERNS: list[tuple[str, re.Pattern]] = [(intent, re.compile(p)) for intent, p in [
    ("call_fire_station", r"\bcall (the )?fire (station|brigade|service|department)\b"),
    ("evacuate", r"\bevacuat"),
    ("alert_all", r"\b(call|alert) (everybody|everyone|all( the)? guards|all)\b"),
    ("stop_calls", r"\bstop (all )?(the )?calls?\b"),
    ("not_responded", r"\bwho (has ?n.?t|has not|did ?n.?t|didn't) (responded|answered|replied)\b"),
    ("status_report", r"\bstatus( report)?\b"),
    ("show_camera", r"\bshow (me )?(the )?camera\s*(?P<camera>[\w-]+)"),
    ("ack", r"\b(ack|acknowledge)\b"),
    ("resolve", r"\b(resolve|close)\b"),
    ("call_zone", r"\bcall (the )?zone (?P<zone>[\w -]+)"),
    ("broadcast_message", r"\b(broadcast|tell everyone|message everyone)\b[:,]?\s*(?P<text>.+)"),
    ("query", r"\b(what happened|any incidents?|anything happen|last hour|show incidents)\b"),
    ("call_person", r"\bcall (?P<name>[a-z][a-z ]+)$"),
]]
QUESTION_WORDS = re.compile(r"\b(what|how|where|who|when|which|procedure|sop|number|contact)\b")

TOOLS = [
    {"type": "function", "function": {"name": name, "description": desc, "parameters": {
        "type": "object", "properties": props, "required": []}}}
    for name, desc, props in [
        ("alert_all", "Alert every on-shift guard (WhatsApp + calls).", {}),
        ("evacuate", "Order an evacuation of the site.", {}),
        ("call_fire_station", "Call the fire station (only if the user clearly orders it).", {}),
        ("call_person", "Call one guard by name.", {"name": {"type": "string"}}),
        ("call_zone", "Call the on-shift guards of one zone.", {"zone": {"type": "string"}}),
        ("broadcast_message", "Send a text message to every on-shift guard.", {"text": {"type": "string"}}),
        ("status_report", "Summarise open incidents and camera status.", {}),
        ("show_camera", "Show one camera on screen.", {"camera": {"type": "string"}}),
        ("query", "Answer a question about past incidents.", {"question": {"type": "string"}}),
        ("ack", "Acknowledge an incident.", {"incident_id": {"type": "integer"}}),
        ("resolve", "Resolve an incident.", {"incident_id": {"type": "integer"}}),
        ("stop_calls", "Stop all ongoing alert calls.", {}),
        ("not_responded", "List guards who have not responded to the roll call.", {}),
        ("knowledge", "Answer a question about safety procedures or contacts from our documents.",
         {"question": {"type": "string"}}),
    ]
]


@dataclass
class CommandResult:
    intent: str
    status: str  # done, needs_confirmation, unknown, refused
    answer: str
    confirm_token: str | None = None
    data: dict = field(default_factory=dict)
    notices: list[dict] = field(default_factory=list)  # pushed to dashboards by the router


_pending: dict[str, tuple[int, str, dict, float]] = {}  # token -> (user id, intent, args, expires)
_pending_lock = threading.Lock()


def detect(text: str) -> tuple[str, dict, str]:
    """Returns (intent, args, path) with path fast, smart, knowledge or none."""
    lowered = re.sub(r"\s+", " ", text.lower()).strip(" .!?")
    for intent, pattern in PATTERNS:
        if m := pattern.search(lowered):
            args = {k: v.strip() for k, v in m.groupdict().items() if v}
            if number := re.search(r"\b(\d+)\b", lowered):
                args.setdefault("incident_id", int(number.group(1)))
            return intent, args, "fast"
    picked = choose_tool([{"role": "system", "content": "Pick the one function that does what the control-room "
                           "operator asked. Only pick call_fire_station if they clearly order it."},
                          {"role": "user", "content": text}], TOOLS)
    if picked is not None:
        return picked[0], picked[1], "smart"
    if QUESTION_WORDS.search(lowered):
        return "knowledge", {"question": text}, "knowledge"
    return "unknown", {}, "none"


def handle(text: str, user: User) -> CommandResult:
    t0 = time.perf_counter()
    intent, args, path = detect(text)
    log.info("step=command intent=%s path=%s user=%s", intent, path, user.name)
    if intent == "unknown":
        return CommandResult("unknown", "unknown", "Sorry, I didn't catch that. Try 'status report' or "
                                                   "'what happened in the last hour'.")
    needed = NEEDS.get(intent)
    if needed and not can(user, needed):
        return CommandResult(intent, "refused", "Your role is not allowed to do that.")
    if intent in BIG_ACTIONS:
        token = secrets.token_urlsafe(16)
        ttl = get_settings().assistant_confirm_ttl_s
        with _pending_lock:
            _pending[token] = (user.id, intent, args, time.monotonic() + ttl)
        question = {"alert_all": "This will message and call every guard on shift.",
                    "evacuate": "This will order an evacuation: every guard on shift is messaged and called.",
                    "call_fire_station": "This will call the fire station."}[intent]
        return CommandResult(intent, "needs_confirmation", f"{question} Say yes to confirm.", token)
    result = execute(intent, args, user, text)
    log.info("step=command_done intent=%s status=%s ms=%.0f", intent, result.status, (time.perf_counter() - t0) * 1000)
    return result


def confirm(token: str, confirmed: bool, user: User) -> CommandResult | None:
    """None if the token is unknown, expired, or belongs to someone else."""
    with _pending_lock:
        pending = _pending.pop(token, None)
        for t in [t for t, (_, _, _, exp) in _pending.items() if exp < time.monotonic()]:
            _pending.pop(t, None)
    if pending is None:
        return None
    user_id, intent, args, expires = pending
    if user_id != user.id or expires < time.monotonic():
        return None
    if not confirmed:
        return CommandResult(intent, "done", "Cancelled. Nothing was done.")
    return execute(intent, args, user, "")


def _target_incident(db, args: dict, user: User) -> Incident | None:
    incident_id = args.get("incident_id")
    if incident_id is not None:
        incident = incident_ops.load(db, int(incident_id), lock=True)
        return incident if incident is not None and can_see(user, incident.camera_id) else None
    query = select(Incident.id).where(Incident.status == "open").order_by(Incident.started_at.desc()).limit(1)
    scope = allowed_cameras(user)
    if scope is not None:
        query = query.where(Incident.camera_id.in_(scope))
    latest = db.scalar(query)
    return incident_ops.load(db, latest, lock=True) if latest else None


def execute(intent: str, args: dict, user: User, text: str) -> CommandResult:
    s = get_settings()
    if intent in ("alert_all", "evacuate"):
        message = EVACUATE_MESSAGE if intent == "evacuate" else ALERT_ALL_MESSAGE
        notice = alerts.alert_all(message, by_name=user.name, user=user, channel="voice", incident_id=None)
        notice.pop("incident", None)
        return CommandResult(intent, "done", f"Done. Alerting {notice['recipients']} guards on shift.",
                             data={"roll_call_id": notice["roll_call_id"]}, notices=[notice])

    if intent == "call_fire_station":
        if not s.fire_station_number:
            return CommandResult(intent, "refused", "The fire station number is not set (FIRE_STATION_NUMBER).")
        place = s.site_address or "our site"
        call_id = calls.place(kind="direct", to_name="Fire station", to_phone=s.fire_station_number,
                              message=f"This is an emergency call from {PRODUCT_NAME} at {place}. "
                                      "A fire has been reported. Please send help.")
        _audit(user, "call_fire_station", "fire_station")
        return CommandResult(intent, "done" if call_id else "refused",
                             "Calling the fire station." if call_id else "Calls are off; please phone them directly.")

    if intent == "call_person":
        with SessionLocal() as db:
            contact = contact_by_name(db, args.get("name", text))
        if contact is None:
            return CommandResult(intent, "refused", "I couldn't find that person on the roster.")
        call_id = calls.place(kind="direct", to_name=contact.name, to_phone=contact.phone,
                              responder_id=contact.responder_id,
                              message=f"{PRODUCT_NAME} control room. {user.name} needs you. Please come to the "
                                      "control room or call back.")
        _audit(user, "call_person", f"responder:{contact.responder_id}")
        return CommandResult(intent, "done" if call_id else "refused",
                             f"Calling {contact.name}." if call_id else "Calls are off right now.")

    if intent == "call_zone":
        zone = (args.get("zone") or "").strip().replace(" ", "-")
        with SessionLocal() as db:
            contacts = on_shift_contacts(db, zone=zone)
        placed = [c.name for c in contacts if calls.place(
            kind="direct", to_name=c.name, to_phone=c.phone, responder_id=c.responder_id,
            message=f"{PRODUCT_NAME} control room. Everyone in {zone.replace('-', ' ')}, please respond.")]
        _audit(user, "call_zone", f"zone:{zone}")
        if not contacts:
            return CommandResult(intent, "refused", f"Nobody is on shift in {zone.replace('-', ' ')}.")
        return CommandResult(intent, "done", f"Calling {len(placed)} people in {zone.replace('-', ' ')}.")

    if intent == "broadcast_message":
        message = args.get("text") or text
        with SessionLocal() as db:
            contacts = on_shift_contacts(db)
        sent = whatsapp.send_many([(c.phone, f"{PRODUCT_NAME} message from {user.name}: {message}", None)
                                   for c in contacts])
        _audit(user, "broadcast_message", "on_shift", {"message": message})
        return CommandResult(intent, "done", f"Message sent to {sent} of {len(contacts)} guards on shift.")

    if intent == "status_report":
        return CommandResult(intent, "done", _status_report(user))

    if intent == "show_camera":
        camera = _find_camera(args.get("camera", ""), user)
        if camera is None:
            return CommandResult(intent, "refused", "I couldn't find that camera.")
        return CommandResult(intent, "done", f"Showing {camera.name}.", data={"camera_id": camera.id})

    if intent == "query":
        with SessionLocal() as db:
            answer, rows, source, _ = ask(db, args.get("question") or text or "what happened in the last hour", user)
        return CommandResult(intent, "done", answer, data={"incident_ids": [r.id for r in rows], "source": source})

    if intent in ("ack", "resolve"):
        return _ack_or_resolve(intent, args, user)

    if intent == "stop_calls":
        cancelled = calls.cancel_pending()
        hung = calls.hang_up_active() if calls.mode == "live" else 0
        for job in escalation.scheduler.get_jobs():
            if job.id.startswith("ladder:"):
                job.remove()
        _audit(user, "stop_calls", "all")
        return CommandResult(intent, "done", f"Stopped all calls: {cancelled} queued cancelled, {hung} hung up.")

    if intent == "not_responded":
        with SessionLocal() as db:
            roll_call_id = alerts.latest_roll_call_id(db)
            summary = alerts.roll_call_summary(db, roll_call_id) if roll_call_id else None
        if summary is None:
            return CommandResult(intent, "done", "There is no roll call running.")
        missing = [g["name"] for g in summary["guards"] if g["status"] in ("pending", "not_reached")]
        answer = (f"{len(missing)} of {summary['total']} have not responded: {', '.join(missing)}."
                  if missing else f"All {summary['total']} have responded.")
        return CommandResult(intent, "done", answer, data={"roll_call_id": summary["roll_call_id"]})

    if intent == "knowledge":
        answer, sources, how = knowledge.answer(args.get("question") or text)
        return CommandResult(intent, "done", answer, data={"sources": sources, "source": how})

    return CommandResult("unknown", "unknown", "Sorry, I can't do that yet.")


def _ack_or_resolve(intent: str, args: dict, user: User) -> CommandResult:
    with SessionLocal() as db:
        incident = _target_incident(db, args, user)
        if incident is None:
            return CommandResult(intent, "refused", "I couldn't find an open incident to " +
                                 ("acknowledge." if intent == "ack" else "resolve."))
        try:
            if intent == "ack":
                changed = incident_ops.acknowledge(db, incident, name=user.name, user=user, via="voice",
                                                   channel="voice")
                answer = (f"Incident {incident.id} acknowledged." if changed
                          else f"Incident {incident.id} was already acknowledged by {incident.acknowledged_by}.")
            else:
                if incident.severity == "critical":
                    return CommandResult(intent, "refused", f"Incident {incident.id} is critical: close it from "
                                                            "the dashboard.")
                incident_ops.close(db, incident, "resolved", name=user.name, user=user, via="voice", channel="voice")
                answer = f"Incident {incident.id} resolved."
        except incident_ops.IncidentClosed as closed:
            return CommandResult(intent, "refused", f"Incident {incident.id} is already {closed}.")
        db.commit()
        message = incident_ops.to_message(incident)
        incident_id = incident.id
    escalation.stop(incident_id)
    return CommandResult(intent, "done", answer, data={"incident_id": incident_id}, notices=[message])


def _status_report(user: User) -> str:
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        query = select(Incident.severity, func.count(Incident.id)).where(
            Incident.status.in_(("open", "acknowledged"))).group_by(Incident.severity)
        cameras_query = select(Camera).order_by(Camera.id)
        if scope is not None:
            query = query.where(Incident.camera_id.in_(scope))
            cameras_query = cameras_query.where(Camera.id.in_(scope))
        by_severity = dict(db.execute(query).all())
        cameras = list(db.scalars(cameras_query))
    online = [c.name for c in cameras if camera_status.get(c.id)[0]]
    open_total = sum(by_severity.values())
    detail = ", ".join(f"{by_severity[s]} {s}" for s in ("critical", "high", "low") if by_severity.get(s))
    return (f"{open_total} open incident{'s' if open_total != 1 else ''}" + (f" ({detail})" if detail else "") +
            f". {len(online)} of {len(cameras)} cameras online.")


def _find_camera(text: str, user: User) -> Camera | None:
    with SessionLocal() as db:
        cameras = list(db.scalars(select(Camera).order_by(Camera.id)))
    cameras = [c for c in cameras if can_see(user, c.id)]
    text = text.lower().strip()
    if text.isdigit() and 1 <= int(text) <= len(cameras):
        return cameras[int(text) - 1]  # "show camera 2" = the second camera in the list
    return next((c for c in cameras if text and (text in c.id.lower() or text in c.name.lower())), None)


def _audit(user: User, action: str, target: str, detail: dict | None = None) -> None:
    with SessionLocal() as db:
        record(db, user, action, target=target, channel="voice", detail=detail)
        db.commit()
