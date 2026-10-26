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
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.config import PRODUCT_NAME, get_settings
from app.db import SessionLocal
from app.models import Camera, Incident, User
from app.services import alerts, camera_status, duty, incidents as incident_ops, summary as summaries
from app.services.assistant import ask
from app.services.audit import record
from app.services.calls import calls
from app.services.directory import contact_by_name, on_shift_contacts, on_shift_contacts_with_fallback
from app.services.escalation import TYPE_LABELS, escalation, local_time_text
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
    ("summary", r"\b(summary|summari[sz]e|recap)\b"),
    ("on_duty", r"\b(on|off) duty\b"),
    ("status_report", r"\bstatus( report)?\b"),
    ("show_recording", r"\b(show|open|play|pull up|find)\b.*\b(recordings?|videos?|footage|clips?)\b"),
    ("show_camera", r"\b(show|open|display) (me )?(the )?cam(era)?s?\s*(?P<camera>[\w-]+((\s*(,|and|&)\s*)+[\w-]+)*)"),
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
        ("show_camera", "Show one or more cameras on screen.",
         {"camera": {"type": "string", "description": "camera number(s) or name, e.g. '3, 4 and 12'"}}),
        ("summary", "Summary of what happened for a time window, optionally for some cameras.",
         {"hours": {"type": "number", "description": "the past N hours; omit for today"},
          "period": {"type": "string", "enum": ["today", "yesterday"]},
          "cameras": {"type": "array", "items": {"type": "integer"}, "description": "camera numbers"}}),
        ("on_duty", "Say which guards are on duty and which are off duty.", {}),
        ("show_recording", "Show the video recording of an incident (by number, by type like fire, or the latest) "
         "or the all-day recordings of a camera.",
         {"incident_id": {"type": "integer"}, "type": {"type": "string"},
          "camera": {"type": "string", "description": "camera number or name"}}),
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
        return CommandResult("unknown", "unknown", "Sorry, I didn't catch that.")
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
                             f"Calling the fire station.{_pretend()}" if call_id
                             else "Calls are off; please phone them directly.")

    if intent == "call_person":
        with SessionLocal() as db:
            contact = contact_by_name(db, args.get("name", text))
        if contact is None:
            return CommandResult(intent, "refused", "I couldn't find that person on the roster.")
        call_id = calls.place(kind="direct", to_name=contact.name, to_phone=contact.phone,
                              responder_id=contact.responder_id,
                              message=f"{user.name} in the control room needs you.")
        _audit(user, "call_person", f"responder:{contact.responder_id}")
        return CommandResult(intent, "done" if call_id else "refused",
                             f"Calling {contact.name}.{_pretend()}" if call_id else _calls_off())

    if intent == "call_zone":
        zone = (args.get("zone") or "").strip().replace(" ", "-")
        with SessionLocal() as db:
            contacts, fallback = on_shift_contacts_with_fallback(db, zone=zone)
        placed = [c.name for c in contacts if calls.place(
            kind="direct", to_name=c.name, to_phone=c.phone, responder_id=c.responder_id,
            message=f"The control room needs everyone in {zone.replace('-', ' ')} to respond.")]
        _audit(user, "call_zone", f"zone:{zone}")
        if not contacts:
            return CommandResult(intent, "refused", f"Nobody is on shift in {zone.replace('-', ' ')}.")
        note = " Nobody there is checked in on duty, so I used the shift roster." if fallback else ""
        return CommandResult(intent, "done",
                             f"Calling {len(placed)} people in {zone.replace('-', ' ')}.{note}{_pretend()}")

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
        wanted = str(args.get("camera", ""))
        parts = [w for w in re.split(r"\s*(?:,|\band\b|&)\s*", wanted) if w.strip()] or [wanted]
        if any(w.isdigit() for w in parts):
            parts = [w for w in parts if w.isdigit()]  # "cameras 3, 4 and 12 and show them": the numbers only
        found = [c for c in (_find_camera(w, user) for w in parts) if c is not None]
        found = list({c.id: c for c in found}.values())
        if not found:
            return CommandResult(intent, "refused", "I couldn't find that camera.")
        missing = len(parts) - len(found)
        answer = f"Showing {', '.join(c.name for c in found)}." + (
            f" {missing} of those I couldn't find." if missing > 0 else "")
        return CommandResult(intent, "done", answer,
                             data={"camera_id": found[0].id, "camera_ids": [c.id for c in found]})

    if intent == "summary":
        return _summary(args, user, text)

    if intent == "show_recording":
        return _show_recording(args, user, text)

    if intent == "on_duty":
        return CommandResult(intent, "done", _duty_report(user))

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


RECORDING_TYPES = {"fire": "fire", "smoke": "smoke", "rider": "no_helmet", "without a helmet": "no_helmet",
                   "no helmet": "no_helmet", "fall": "fall", "fell": "fall", "bag": "unattended",
                   "restricted": "access", "intruder": "access", "entry": "access", "crowd": "crowding",
                   "helmet": "ppe", "vest": "ppe", "ppe": "ppe", "loiter": "loitering"}


def _show_recording(args: dict, user: User, text: str) -> CommandResult:
    """ "Show the recording of the fire / of incident 12 / of camera 2": the incident page (its clip and the
    full recording around it) or the camera's all-day recordings."""
    lowered = text.lower()
    number = args.get("incident_id") or (m.group(1) if (m := re.search(r"incident (?:number )?(\d+)", lowered))
                                         else None)
    camera_word = args.get("camera") or (m.group(1) if (m := re.search(r"camera (\w+)", lowered)) else None)
    if camera_word and not number:
        camera = _find_camera(str(camera_word), user)
        if camera is None:
            return CommandResult("show_recording", "refused", "I couldn't find that camera.")
        return CommandResult("show_recording", "done", f"Here are today's recordings of {camera.name}.",
                             data={"open_path": f"/recordings?camera={camera.id}", "camera_id": camera.id})
    kind = args.get("type") or next((t for word, t in RECORDING_TYPES.items() if word in lowered), None)
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        query = select(Incident).order_by(Incident.started_at.desc())
        if number:
            query = query.where(Incident.id == int(number))
        elif kind:
            query = query.where(Incident.type == RECORDING_TYPES.get(str(kind).lower(), str(kind).lower()))
        if scope is not None:
            query = query.where(Incident.camera_id.in_(scope))
        incident = db.scalars(query.limit(1)).first()
        if incident is None:
            return CommandResult("show_recording", "refused", "I couldn't find that incident.")
        camera_name = incident.camera.name if incident.camera else incident.camera_id
        when = local_time_text(incident.started_at, get_settings().timezone)
        label = TYPE_LABELS.get(incident.type, incident.type).lower()
        return CommandResult("show_recording", "done",
                             f"Here's the recording of incident {incident.id}, {label} on {camera_name} at {when}.",
                             data={"open_path": f"/incidents/{incident.id}", "incident_id": incident.id,
                                   "camera_id": incident.camera_id})


def _find_camera(text: str, user: User) -> Camera | None:
    with SessionLocal() as db:
        cameras = list(db.scalars(select(Camera).order_by(Camera.number.nulls_last(), Camera.id)))
    cameras = [c for c in cameras if can_see(user, c.id)]
    text = text.lower().strip()
    if text.isdigit():
        return next((c for c in cameras if c.number == int(text)), None)  # "show camera 6" = camera number 6
    return next((c for c in cameras if text and (text in c.id.lower() or text in c.name.lower())), None)


WORD_NUMBERS = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "eight": 8,
                "twelve": 12}


def summary_window(text: str, args: dict, now: datetime | None = None) -> tuple[datetime, datetime, str]:
    """(start, end, label) from "today", "yesterday", "the past hour", "last 3 hours", "last 30 minutes"."""
    now = now or datetime.now(timezone.utc)
    lowered = text.lower()
    hours = args.get("hours")
    if hours is None:
        if m := re.search(r"\b(?:past|last|previous)\s+(?:(\d+|an?|one|two|three|four|five|six|eight|twelve)\s+)?"
                          r"(hours?|hrs?|minutes?|mins?)\b", lowered):
            amount = m.group(1) or "1"
            amount = int(amount) if amount.isdigit() else WORD_NUMBERS[amount]
            hours = amount / 60 if m.group(2).startswith("min") else amount
    if hours is not None:
        hours = max(1 / 60, min(float(hours), 24 * 31))
        if hours < 1:
            label = f"the past {round(hours * 60)} minutes"
        else:
            label = "the past hour" if hours == 1 else f"the past {hours:g} hours"
        return now - timedelta(hours=hours), now, label
    today_start, _ = summaries.today_window(now)
    if args.get("period") == "yesterday" or "yesterday" in lowered:
        return today_start - timedelta(days=1), today_start, "yesterday"
    return today_start, now, "today"


def summary_cameras(text: str, args: dict) -> list[int]:
    """Camera numbers: "cameras 6, 7 and 8", "camera 3", or the LLM's list."""
    if args.get("cameras"):
        return [int(n) for n in args["cameras"] if str(n).isdigit()]
    m = re.search(r"\bcam(?:era)?s?\s+((?:number\s+)?\d+(?:(?:\s*(?:,|and|&)\s*)+\d+)*)", text.lower())
    return [int(n) for n in re.findall(r"\d+", m.group(1))] if m else []


def _summary(args: dict, user: User, text: str) -> CommandResult:
    start, end, label = summary_window(text, args)
    numbers = summary_cameras(text, args)
    with SessionLocal() as db:
        picked, missing = None, []
        if numbers:
            by_number = {c.number: c for c in db.scalars(select(Camera).where(Camera.number.in_(numbers)))
                         if can_see(user, c.id)}
            missing = [n for n in numbers if n not in by_number]
            picked = [by_number[n].id for n in numbers if n in by_number]
            if not picked:
                return CommandResult("summary", "refused", f"I couldn't find camera{'s' if len(numbers) > 1 else ''} "
                                                           f"{', '.join(map(str, numbers))} among your cameras.")
        result = summaries.build(db, db.merge(user), start, end, picked)
    cams = result["cameras"]
    scope = summaries.camera_list_text(cams) if picked else (
        "all cameras" if result["sector"]["all_cameras"] else f"{len(cams)} of your cameras")
    tz = get_settings().timezone
    answer = (f"Summary for {scope}, {label} ({summaries.clock(start, tz)} to {summaries.clock(end, tz)}). "
              + result["text"].split(". ", 1)[-1])
    if missing:
        answer += f" Camera{'s' if len(missing) > 1 else ''} {', '.join(map(str, missing))} not found."
    return CommandResult("summary", "done", answer, data={
        "from": start.isoformat(), "to": end.isoformat(), "camera_ids": result["sector"]["camera_ids"],
        "incident_ids": [i["incident_id"] for i in result["violations"]["items"]]})


def _duty_report(user: User) -> str:
    scope = allowed_cameras(user)
    with SessionLocal() as db:
        guards = [u for u in db.scalars(select(User).where(User.active.is_(True)).order_by(User.name))
                  if duty.checks_in(u.role) and (scope is None or set(u.camera_ids or []) & scope)]
        on_ids = duty.on_duty_ids(db, [g.id for g in guards]) if guards else set()
    on = [g.name for g in guards if g.id in on_ids]
    off = [g.name for g in guards if g.id not in on_ids]
    if not guards:
        return "There are no guards on the roster."
    answer = f"{len(on)} on duty" + (f": {', '.join(on)}" if on else "") + f". {len(off)} off duty" + (
        f": {', '.join(off)}" if off else "") + "."
    if not on:
        answer += " Alerts will go to the shift roster until someone checks in."
    return answer


def _pretend() -> str:
    """Say it plainly when nobody's phone will really ring."""
    return " This is only a pretend call: set CALLS_MODE to live to really call." if calls.mode == "simulate" else ""


def _calls_off() -> str:
    return "Calls are off: fill the Twilio keys and set CALLS_MODE to live."


def _audit(user: User, action: str, target: str, detail: dict | None = None) -> None:
    with SessionLocal() as db:
        record(db, user, action, target=target, channel="voice", detail=detail)
        db.commit()
