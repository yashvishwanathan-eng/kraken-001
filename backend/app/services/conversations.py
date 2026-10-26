"""The history of every talk with Friday (P31), kept apart from Friday's page and the audit log.

  phone   one conversation per call: what Friday said, what the person said, and (when Twilio sends it)
          the whole call's audio, downloaded to media/calls/call_<id>.mp3 so it stays on the laptop
  laptop  the orders one user gives Friday (wake word, Space, typed), grouped into one conversation while
          they keep talking (a new one after SESSION_GAP of quiet); their voice clip is saved with each order

Dashboards only get a small {"kind": "conversation", "conversation_id"} nudge to refresh the Conversations
page; the words themselves are read from GET /conversations by people allowed to see them (view_audit).
"""

import logging
import threading
import uuid
from datetime import datetime, timedelta, timezone

import httpx
from sqlalchemy import select

from app.config import get_settings, media_root
from app.db import SessionLocal
from app.models import Call, Conversation, ConversationTurn, User
from app.ws import manager

log = logging.getLogger("safetyai.conversations")

SESSION_GAP = timedelta(minutes=10)
TEXT_LIMIT = 1000
AUDIO_EXT = {"audio/wav": "wav", "audio/x-wav": "wav", "audio/webm": "webm", "video/webm": "webm",
             "audio/ogg": "ogg", "audio/mpeg": "mp3", "audio/mp4": "m4a"}


def _nudge(conversation_id: int) -> None:
    manager.publish_from_thread({"kind": "conversation", "conversation_id": conversation_id})


def _add(db, conv: Conversation, speaker: str, text: str, action: str | None = None,
         audio_path: str | None = None) -> None:
    now = datetime.now(timezone.utc)
    db.add(ConversationTurn(conversation_id=conv.id, at=now, speaker=speaker, text=(text or "")[:TEXT_LIMIT],
                            action=action, audio_path=audio_path))
    conv.last_at = now


# ---------- phone calls ----------

def _for_call(db, call_id: int) -> Conversation | None:
    conv = db.scalar(select(Conversation).where(Conversation.call_id == call_id))
    if conv is None:
        call = db.get(Call, call_id)
        if call is None:
            return None
        user_id = call.responder.user_id if call.responder is not None else None
        conv = Conversation(channel="phone", person=call.to_name, user_id=user_id, call_id=call.id,
                            incident_id=call.incident_id)
        db.add(conv)
        db.flush()
    return conv


def call_turn(call_id: int, speaker: str, text: str, action: str | None = None) -> None:
    """Friday or the person said something on this call."""
    if not text:
        return
    try:
        with SessionLocal() as db:
            conv = _for_call(db, call_id)
            if conv is None:
                return
            _add(db, conv, speaker, text, action)
            db.commit()
            conversation_id = conv.id
        _nudge(conversation_id)
    except Exception:  # the history must never break a live call
        log.exception("call=%d could not save a conversation turn", call_id)


OUTCOMES = {"in-progress": "answered", "completed": "answered", "busy": "declined", "no-answer": "no_answer",
            "failed": "failed", "canceled": "cancelled"}


def call_outcome(call_id: int, twilio_status: str, duration_s: int | None) -> None:
    """The call ended: answered, declined (rejected or busy), not answered, failed. A call nobody picked up still
    gets its line in the history, so the log shows who declined and who never answered."""
    outcome = OUTCOMES.get(twilio_status)
    if outcome is None:
        return
    try:
        with SessionLocal() as db:
            conv = _for_call(db, call_id)
            if conv is None:
                return
            if conv.outcome == "answered" and outcome != "answered":
                return  # a late duplicate status never overwrites a real answer
            conv.outcome = outcome
            conv.duration_s = duration_s
            if outcome != "answered" and not conv.turns:
                text = {"declined": "Declined the call.", "no_answer": "Did not answer.",
                        "failed": "The call could not be connected.", "cancelled": "Call cancelled."}[outcome]
                _add(db, conv, "person", text, outcome)
            conv.last_at = datetime.now(timezone.utc)
            db.commit()
            conversation_id = conv.id
        _nudge(conversation_id)
    except Exception:
        log.exception("call=%d could not save the call outcome", call_id)


def save_call_recording(call_id: int, recording_url: str) -> None:
    """Twilio finished recording the call: download it (in the background) and attach it to the conversation."""
    threading.Thread(target=_download, args=(call_id, recording_url), name=f"rec-{call_id}", daemon=True).start()


def _download(call_id: int, recording_url: str) -> None:
    s = get_settings()
    folder = media_root() / "calls"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"call_{call_id}.mp3"
    try:
        response = httpx.get(f"{recording_url}.mp3", auth=(s.twilio_account_sid, s.twilio_auth_token), timeout=30,
                             follow_redirects=True)
        response.raise_for_status()
        (folder / name).write_bytes(response.content)
        with SessionLocal() as db:
            conv = _for_call(db, call_id)
            if conv is not None:
                conv.recording_path = f"calls/{name}"
                db.commit()
                conversation_id = conv.id
            else:
                return
        log.info("call=%d recording saved (%d KB)", call_id, len(response.content) // 1024)
        _nudge(conversation_id)
    except Exception as exc:
        log.warning("call=%d recording could not be downloaded: %s", call_id, exc)


# ---------- laptop ----------

def save_voice(data: bytes, content_type: str) -> str:
    """Keep the user's spoken order. Returns the path under media/ (voice/<id>.<ext>)."""
    folder = media_root() / "voice"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{datetime.now():%Y-%m-%d_%H-%M-%S}_{uuid.uuid4().hex[:6]}.{AUDIO_EXT.get(content_type, 'wav')}"
    (folder / name).write_bytes(data)
    return f"voice/{name}"


def laptop_turn(user: User, said: str, answer: str, intent: str | None = None, audio_path: str | None = None,
                via: str = "typed") -> None:
    """One order to Friday on the laptop and her answer."""
    try:
        with SessionLocal() as db:
            since = datetime.now(timezone.utc) - SESSION_GAP
            conv = db.scalar(select(Conversation).where(Conversation.channel == "laptop",
                                                        Conversation.user_id == user.id,
                                                        Conversation.last_at >= since)
                             .order_by(Conversation.last_at.desc()).limit(1))
            if conv is None:
                conv = Conversation(channel="laptop", person=user.name, user_id=user.id)
                db.add(conv)
                db.flush()
            _add(db, conv, "person", said or "(nothing heard)", via, audio_path)
            _add(db, conv, "friday", answer, intent)
            db.commit()
            conversation_id = conv.id
        _nudge(conversation_id)
    except Exception:
        log.exception("could not save the laptop conversation for %s", user.name)
