"""Twilio webhooks: what the guard hears, and what happens when they press a key or speak.

  /twilio/voice   Twilio asks: "they picked up, what do I say?"   -> TwiML (<Say> + <Gather>)
  /twilio/gather  Twilio sends the keypress or the spoken words    -> act, then TwiML
  /twilio/status  Twilio reports ringing / answered / no-answer    -> update the call and timeline

Every request is checked against Twilio's signature (X-Twilio-Signature), so nobody else can fake a
keypress. Every handler is idempotent (Twilio may retry) and never answers with a 500: on any error
the guard hears a short apology instead of silence.

Keypad during an alert call: 1 going (acknowledge), 2 false alarm (high severity only), 3 supervisor,
9 repeat. Roll-call calls: 1 responding, 2 safe. Speech is matched to the same intents.
"""

import base64
import hashlib
import hmac
import logging
from xml.sax.saxutils import escape, quoteattr

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool

from app.config import PRODUCT_NAME, get_settings
from app.db import SessionLocal
from app.models import Call, User
from app.services import alerts, incidents as incident_ops
from app.services.calls import NOT_REACHED
from app.services.directory import contacts_for_role
from app.services.escalation import alert_text, escalation
from app.services.permissions import can, share_url
from app.ws import manager

router = APIRouter(prefix="/twilio", tags=["twilio"])
log = logging.getLogger("safetyai.twilio")

MAX_PROMPTS = 2

ALERT_DIGITS = {"1": "ack", "2": "false_alarm", "3": "alert_all", "4": "supervisor", "9": "repeat"}
ROLL_DIGITS = {"1": "responding", "2": "safe", "9": "repeat"}
ROLL_CALL_ACTION = {"responding": "roll_call_responded", "safe": "roll_call_safe"}  # contract timeline names
# Order matters: the first phrase found wins, so "alert everybody" is checked before a plain "yes".
ALERT_PHRASES = [
    ("alert_all", ("alert everybody", "alert everyone", "alert all", "everybody", "everyone")),
    ("false_alarm", ("false alarm", "false", "no problem", "nothing there", "all clear")),
    ("supervisor", ("supervisor", "manager", "connect me")),
    ("repeat", ("repeat", "again", "say that")),
    ("ack", ("going", "coming", "on my way", "acknowledge", "yes", "okay", "ok")),
]
ROLL_PHRASES = [
    ("safe", ("safe", "evacuated", "outside", "i'm out", "i am out")),
    ("responding", ("responding", "coming", "going", "on my way")),
    ("repeat", ("repeat", "again")),
]


# ---------- signature ----------

def expected_signature(url: str, params: dict[str, str], auth_token: str) -> str:
    """Twilio's scheme: HMAC-SHA1 over the full URL + every POST field (sorted by name, name+value), base64."""
    data = url + "".join(name + params[name] for name in sorted(params))
    return base64.b64encode(hmac.new(auth_token.encode(), data.encode(), hashlib.sha1).digest()).decode()


async def verified_form(request: Request) -> dict[str, str]:
    settings = get_settings()
    if not settings.twilio_auth_token or not settings.public_base_url:
        raise HTTPException(status_code=503, detail="Twilio is not configured (TWILIO_AUTH_TOKEN, PUBLIC_BASE_URL)")
    form = {k: str(v) for k, v in (await request.form()).items()}
    # Twilio signed the public (ngrok) address, not what the laptop sees, so rebuild it from PUBLIC_BASE_URL.
    url = settings.public_base_url.rstrip("/") + request.url.path
    if request.url.query:
        url += "?" + request.url.query
    given = request.headers.get("X-Twilio-Signature", "")
    if not hmac.compare_digest(given, expected_signature(url, form, settings.twilio_auth_token)):
        log.warning("rejected webhook with a bad Twilio signature: %s", request.url.path)
        raise HTTPException(status_code=403, detail="bad Twilio signature")
    return form


# ---------- TwiML helpers ----------

def twiml(*verbs: str) -> Response:
    return Response(content=f'<?xml version="1.0" encoding="UTF-8"?><Response>{"".join(verbs)}</Response>',
                    media_type="application/xml")


def say(text: str) -> str:
    return f"<Say language={quoteattr(get_settings().call_language)}>{escape(text)}</Say>"


def url(path: str) -> str:
    return get_settings().public_base_url.rstrip("/") + path


def gather(prompt: str, call_id: int, hints: str, step: str = "") -> str:
    action = url(f"/twilio/gather?call_id={call_id}" + (f"&step={step}" if step else ""))
    return (f'<Gather input="speech dtmf" numDigits="1" timeout="6" speechTimeout="auto" method="POST" '
            f'speechModel="phone_call" enhanced="true" '
            f'language={quoteattr(get_settings().call_language)} hints={quoteattr(hints)} action={quoteattr(action)}>'
            f"{say(prompt)}</Gather>")


def redirect_to_voice(call_id: int, attempt: int) -> str:
    return f'<Redirect method="POST">{escape(url(f"/twilio/voice?call_id={call_id}&attempt={attempt}"))}</Redirect>'


SORRY = twiml(say("Sorry, something went wrong. Please check the dashboard."), "<Hangup/>")


# ---------- what the guard hears ----------

def _linked_user(db, call: Call) -> User | None:
    return call.responder.user if call.responder is not None and call.responder.user_id else None


def _voice(call_id: int, attempt: int) -> Response:
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return twiml("<Hangup/>")
        if call.kind == "broadcast":
            audio = share_url(call.audio_file) if call.audio_file else None
            return twiml(f"<Play>{escape(audio)}</Play>" if audio else say("Broadcast audio is missing."), "<Hangup/>")
        if call.kind == "direct":
            return twiml(say(call.message or f"{PRODUCT_NAME} control room calling. Please call back."), "<Hangup/>")
        if call.kind == "roll_call":
            problem = call.message or ""
            if not problem.startswith(PRODUCT_NAME):
                problem = f"{PRODUCT_NAME} alert. {problem}"
            prompt = f"{problem} Press 1 if you are responding. Press 2 if you are safe."
            return twiml(gather(prompt, call_id, "responding, safe, repeat"),
                         redirect_to_voice(call_id, attempt + 1) if attempt < MAX_PROMPTS else say("Goodbye."))
        incident = incident_ops.load(db, call.incident_id) if call.incident_id else None
        if incident is None:
            return twiml(say("This alert is no longer active."), "<Hangup/>")
        if incident.status != "open":
            by = f" by {incident.acknowledged_by}" if incident.acknowledged_by else ""
            return twiml(say(f"Incident {incident.id} was already {incident.status.replace('_', ' ')}{by}. Thank you."),
                         "<Hangup/>")
        options = " What should we do? Press 1 or say going, if you are handling it."
        if incident.severity == "high":
            options += " Press 2 if it is a false alarm."
        user = _linked_user(db, call)
        if user is not None and can(user, "alert_all"):
            options += " Press 3 or say alert everybody, to alert all guards."
        options += " Press 4 to talk to the manager. Press 9 to repeat."
        prompt = alert_text(incident, get_settings().timezone) + options
        return twiml(gather(prompt, call_id, "going, false alarm, supervisor, repeat, alert everybody"),
                     redirect_to_voice(call_id, attempt + 1) if attempt < MAX_PROMPTS else say("Goodbye."))


@router.post("/voice")
async def voice(request: Request, call_id: int, attempt: int = 1) -> Response:
    await verified_form(request)
    try:
        return await run_in_threadpool(_voice, call_id, attempt)
    except Exception:
        log.exception("call=%d voice webhook failed", call_id)
        return SORRY


# ---------- understanding the reply ----------

def match_phrase(text: str, phrases: list[tuple[str, tuple[str, ...]]]) -> str | None:
    lowered = f" {text.lower()} "
    for intent, words in phrases:
        if any(f" {w} " in lowered or (" " in w and w in lowered) for w in words):
            return intent
    return None


def understand(kind: str, digits: str, speech: str) -> tuple[str | None, str]:
    """(intent, via). Keypad first; then keyword match on speech; then a short LLM prompt as a fallback."""
    if digits:
        table = ROLL_DIGITS if kind == "roll_call" else ALERT_DIGITS
        return table.get(digits[:1]), "keypad"
    if not speech:
        return None, "voice"
    phrases = ROLL_PHRASES if kind == "roll_call" else ALERT_PHRASES
    intent = match_phrase(speech, phrases)
    if intent is None:
        from app.services.llm import classify_intent  # only needed for unclear speech
        intent = classify_intent(speech, [name for name, _ in phrases])
    return intent, "voice"


def _publish(message: dict | None) -> None:
    if message is not None:
        manager.publish_from_thread(message)


def _gather(call_id: int, digits: str, speech: str, step: str) -> Response:
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return twiml("<Hangup/>")
        intent, via = understand(call.kind, digits, speech)
        user = _linked_user(db, call)
        name = call.to_name
        log.info("call=%d heard intent=%s via=%s digits=%r speech=%r", call_id, intent, via, digits, speech[:80])

        if step == "confirm_all":
            if digits[:1] == "1" or intent == "ack":
                incident_id = call.incident_id
                current = incident_ops.load(db, incident_id) if incident_id else None
                problem = alert_text(current, get_settings().timezone) if current else "Emergency: please respond now."
                db.close()
                notice = alerts.alert_all(problem, by_name=name, user=user, channel="phone",
                                          incident_id=incident_id, via=via)
                _publish(notice)
                _publish(notice.get("incident"))
                return twiml(say(f"Alerting {notice['recipients']} guards now. Thank you."), "<Hangup/>")
            return twiml(say("Cancelled. Nobody else was alerted."), redirect_to_voice(call_id, 1))

        if intent == "repeat" or intent is None:
            return twiml(redirect_to_voice(call_id, 1))

        if call.kind == "roll_call":
            if call.response != intent:  # repeated keypress: no duplicate timeline entry
                call.response = intent
                if call.incident_id:
                    incident = incident_ops.load(db, call.incident_id)
                    incident_ops.add_timeline(incident, ROLL_CALL_ACTION[intent], by=name, via=via)
                    db.commit()
                    _publish(incident_ops.to_message(incident))
                db.commit()
                _publish(alerts.roll_call_notice(call.roll_call_id))
            word = "responding" if intent == "responding" else "safe"
            return twiml(say(f"Thank you. You are marked {word}."), "<Hangup/>")

        incident = incident_ops.load(db, call.incident_id, lock=True) if call.incident_id else None
        if incident is None:
            return twiml(say("This alert is no longer active."), "<Hangup/>")

        if intent == "alert_all":
            if user is None or not can(user, "alert_all"):
                return twiml(say("Only the head guard or admin can alert everybody."), redirect_to_voice(call_id, 1))
            return twiml(gather("Press 1 to confirm alerting every guard on shift. Press any other key to cancel.",
                                call_id, "yes, confirm, cancel", step="confirm_all"))

        if intent == "supervisor":
            supervisors = contacts_for_role(db, get_settings().role_manager, incident.camera_id, incident.zone)
            if not supervisors:
                return twiml(say("No supervisor number is on the roster."), redirect_to_voice(call_id, 1))
            target = supervisors[0]
            incident_ops.add_timeline(incident, "escalated", to=target.name)
            db.commit()
            _publish(incident_ops.to_message(incident))
            return twiml(say(f"Connecting you to {target.name}."),
                         f"<Dial callerId={quoteattr(get_settings().twilio_from_number)}>{escape(target.phone)}</Dial>")

        if intent == "ack":
            try:
                changed = incident_ops.acknowledge(db, incident, name=name, user=user, via=via, channel="phone")
            except incident_ops.IncidentClosed:
                return twiml(say(f"Incident {incident.id} is already closed. Thank you."), "<Hangup/>")
            if changed:
                call.response = "ack"
                db.commit()
                _publish(incident_ops.to_message(incident))
                escalation.stop(incident.id, except_call_id=call_id)
                return twiml(say(f"Thank you {name.split()[0]}. Acknowledged. Stay safe."), "<Hangup/>")
            return twiml(say(f"Already acknowledged by {incident.acknowledged_by}. Thank you."), "<Hangup/>")

        if intent == "false_alarm":
            if incident.severity != "high":
                reason = ("Critical alerts can only be acknowledged by phone; closing needs the dashboard."
                          if incident.severity == "critical" else "Please close this one from the dashboard.")
                return twiml(say(reason), redirect_to_voice(call_id, 1))
            try:
                incident_ops.close(db, incident, "false_alarm", name=name, user=user, via=via, channel="phone")
            except incident_ops.IncidentClosed:
                return twiml(say(f"Incident {incident.id} is already closed. Thank you."), "<Hangup/>")
            call.response = "false_alarm"
            db.commit()
            _publish(incident_ops.to_message(incident))
            escalation.stop(incident.id, except_call_id=call_id)
            return twiml(say(f"Marked as false alarm by {name}. Thank you."), "<Hangup/>")

        return twiml(redirect_to_voice(call_id, 1))


@router.post("/gather")
async def gather_webhook(request: Request, call_id: int, step: str = "") -> Response:
    form = await verified_form(request)
    try:
        return await run_in_threadpool(_gather, call_id, form.get("Digits", ""), form.get("SpeechResult", ""), step)
    except Exception:
        log.exception("call=%d gather webhook failed", call_id)
        return SORRY


def _status(call_id: int, status: str, call_sid: str) -> None:
    with SessionLocal() as db:
        call = db.get(Call, call_id)
        if call is None:
            return
        if call_sid and call.call_sid is None:
            call.call_sid = call_sid
        previous, call.status = call.status, status
        db.commit()
        if status in NOT_REACHED and previous not in NOT_REACHED:
            if call.kind == "alert" and call.incident_id and call.response is None:
                incident = incident_ops.load(db, call.incident_id)
                incident_ops.add_timeline(incident, "call_no_answer", to=call.to_name)
                db.commit()
                _publish(incident_ops.to_message(incident))
        if call.kind == "roll_call" and call.roll_call_id and status in NOT_REACHED | {"completed"}:
            _publish(alerts.roll_call_notice(call.roll_call_id))
    log.info("call=%d status=%s", call_id, status)


@router.post("/status")
async def status_webhook(request: Request, call_id: int) -> Response:
    form = await verified_form(request)
    try:
        await run_in_threadpool(_status, call_id, form.get("CallStatus", ""), form.get("CallSid", ""))
    except Exception:
        log.exception("call=%d status webhook failed", call_id)
    return twiml()

