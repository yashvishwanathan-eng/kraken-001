"""Conversations with Friday (P31): every phone call she made and every talk on the laptop, as chats.

  GET /conversations?channel=phone|laptop&person=Ravi%20Kumar&limit=50   newest first
  GET /conversations/people                          per person: calls answered / declined / not answered
  GET /conversations/{id}                            every line, with the call recording and voice clips

Admin and Manager only (view_audit), like the Logs page. Audio is served from /media like clips.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.db import SessionLocal
from app.deps import require
from app.models import Conversation, User
from app.schemas import ConversationOut, ConversationSummary, ConversationTurnOut, PersonCalls

router = APIRouter(prefix="/conversations", tags=["conversations"])


def _media(path: str | None) -> str | None:
    return f"/media/{path}" if path else None


def _summary(conv: Conversation) -> dict:
    last = conv.turns[-1].text if conv.turns else ""
    return dict(id=conv.id, channel=conv.channel, person=conv.person, incident_id=conv.incident_id,
                started_at=conv.started_at, last_at=conv.last_at, turns=len(conv.turns), last_text=last[:160],
                recording_url=_media(conv.recording_path), outcome=conv.outcome, duration_s=conv.duration_s)


@router.get("", response_model=list[ConversationSummary])
def list_conversations(channel: str | None = Query(None, pattern="^(phone|laptop)$"),
                       person: str | None = Query(None, max_length=120),
                       limit: int = Query(50, ge=1, le=500),
                       _: User = Depends(require("view_audit"))) -> list[ConversationSummary]:
    with SessionLocal() as db:
        query = select(Conversation).options(selectinload(Conversation.turns)).order_by(Conversation.last_at.desc())
        if channel:
            query = query.where(Conversation.channel == channel)
        if person:
            query = query.where(Conversation.person == person)
        return [ConversationSummary(**_summary(c)) for c in db.scalars(query.limit(limit))]


@router.get("/people", response_model=list[PersonCalls])
def people(_: User = Depends(require("view_audit"))) -> list[PersonCalls]:
    """Everyone Friday has talked with, by name: how many calls they answered, declined or missed."""
    with SessionLocal() as db:
        rows = db.scalars(select(Conversation)).all()
    by: dict[str, dict] = {}
    for c in rows:
        p = by.setdefault(c.person, {"person": c.person, "calls": 0, "answered": 0, "declined": 0, "no_answer": 0,
                                     "laptop_talks": 0, "last_at": c.last_at})
        p["last_at"] = max(p["last_at"], c.last_at)
        if c.channel == "laptop":
            p["laptop_talks"] += 1
            continue
        p["calls"] += 1
        if c.outcome in ("answered", "declined", "no_answer"):
            p[c.outcome] += 1
    return [PersonCalls(**p) for p in sorted(by.values(), key=lambda p: p["person"].lower())]


@router.get("/{conversation_id}", response_model=ConversationOut)
def get_conversation(conversation_id: int, _: User = Depends(require("view_audit"))) -> ConversationOut:
    with SessionLocal() as db:
        conv = db.get(Conversation, conversation_id, options=[selectinload(Conversation.turns)])
        if conv is None:
            raise HTTPException(status_code=404, detail="conversation not found")
        lines = [ConversationTurnOut(at=t.at, speaker=t.speaker, text=t.text, action=t.action,
                                     audio_url=_media(t.audio_path)) for t in conv.turns]
        return ConversationOut(**_summary(conv), lines=lines)
